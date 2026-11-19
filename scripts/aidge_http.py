#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""aidge_http.py — 阿里云 Aidge 纯标准库 HTTP 客户端（ACS3-HMAC-SHA256 V3 签名）。

2026-11-19 决策：不装官方 SDK（代理绕路+Py3.14 无 cryptography 轮子，安装反复失败），
POP 网关 V3 签名用 hashlib/hmac/urllib 即可完整实现。零第三方依赖，
与 erp_client.py / erp_upload.py 的纪律一致。

凭证：环境变量 ALIBABA_CLOUD_ACCESS_KEY_ID / ALIBABA_CLOUD_ACCESS_KEY_SECRET（.env）。
网络：建议直连国内 endpoint（NO_PROXY），代理绕路仅增加延迟。

用法：
  set -a; source .env; set +a
  python3 scripts/aidge_http.py probe    # 连通性探针：最小调用看网关应答
  python3 scripts/aidge_http.py matting <公网图URL> [--bg FFFFFF]
  python3 scripts/aidge_http.py inpaint <公网图URL> --boxes '[[0,0,800,120]]'
  python3 scripts/aidge_http.py translate <公网图URL> [--src auto] [--dst en]
说明：Action/参数名以本次真实响应为准（probe 故意不带参数发请求，让网关自己
吐出期望的参数校验错误，是最快的协议勘探方式）。
"""
import argparse
import hashlib
import hmac
import json
import os
import sys
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

ENDPOINT = os.environ.get("AIDGE_ENDPOINT", "aidge.cn-beijing.aliyuncs.com")
API_VERSION = os.environ.get("AIDGE_API_VERSION", "2026-04-28")


def _pct(s):
    """RFC3986 百分号编码（V3 签名要求，unreserved 仅 A-Za-z0-9-_.~）。"""
    return urllib.parse.quote(str(s), safe="-_.~")


def urlencode_pairs(d):
    return "&".join("%s=%s" % (_pct(k), _pct(v)) for k, v in d.items())


def _ak_sk():
    ak = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID")
    sk = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
    if not ak or not sk:
        sys.exit("[aidge] 缺 ALIBABA_CLOUD_ACCESS_KEY_ID/SECRET（先 source .env）")
    return ak, sk


def call(action, params=None, body=None):
    """一次 POP RPC 调用（POST + query 参数；body 可选 JSON）。
    返回 (http_status, 解析后的响应 dict 或原始文本)。
    params 走 query string（元数据里 matting/remove/recognition 均为 in=query）；
    form 类参数（ImageTranslationPro 为 in=formData）放 body 的 x-www-form-urlencoded。"""
    ak, sk = _ak_sk()
    method = "POST"  # 元数据：Aidge 各 API methods 均为 post
    query = {"Action": action, "Version": API_VERSION}
    for k, v in (params or {}).items():
        query[k] = v if isinstance(v, str) else json.dumps(v, separators=(",", ":"))
    canonical_qs = "&".join("%s=%s" % (_pct(k), _pct(query[k]))
                            for k in sorted(query))
    if body is not None and not isinstance(body, bytes):
        body = urlencode_pairs(body).encode() if isinstance(body, dict) \
            else json.dumps(body).encode()
    payload = body or b""
    hashed_payload = hashlib.sha256(payload).hexdigest()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    headers = {
        "host": ENDPOINT,
        "x-acs-action": action,
        "x-acs-version": API_VERSION,
        "x-acs-date": now,
        "x-acs-signature-nonce": uuid.uuid4().hex,
        "x-acs-content-sha256": hashed_payload,
    }
    if body is not None:
        headers["content-type"] = "application/x-www-form-urlencoded"
    signed_keys = sorted(headers)
    canonical_headers = "".join("%s:%s\n" % (k, headers[k].strip()) for k in signed_keys)
    signed_headers = ";".join(signed_keys)

    canonical_request = "\n".join([method, "/", canonical_qs,
                                   canonical_headers, signed_headers, hashed_payload])
    string_to_sign = "ACS3-HMAC-SHA256\n" + hashlib.sha256(
        canonical_request.encode()).hexdigest()
    signature = hmac.new(sk.encode(), string_to_sign.encode(),
                         hashlib.sha256).hexdigest()
    headers["authorization"] = ("ACS3-HMAC-SHA256 Credential=%s,SignedHeaders=%s,"
                                "Signature=%s" % (ak, signed_headers, signature))

    url = "https://%s/?%s" % (ENDPOINT, canonical_qs)
    req = urllib.request.Request(url, data=payload or None, method=method)
    for k, v in headers.items():
        if k != "host":
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    try:
        return status, json.loads(raw)
    except Exception:
        return status, raw.decode("utf-8", "replace")[:800]


def show(name, status, resp):
    print("===== %s ===== HTTP %s" % (name, status))
    print(json.dumps(resp, ensure_ascii=False, indent=1)[:2000]
          if isinstance(resp, dict) else str(resp)[:2000])
    print()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("probe", help="故意空参数调用 SmartMatting，看网关协议反馈")

    p = sub.add_parser("matting"); p.add_argument("url"); p.add_argument("--bg", default="FFFFFF")
    p = sub.add_parser("inpaint"); p.add_argument("url"); p.add_argument("--boxes", required=True)
    p = sub.add_parser("translate"); p.add_argument("url")
    p.add_argument("--src", default="auto"); p.add_argument("--dst", default="en")
    sub.add_parser("recognize").add_argument("url")

    args = ap.parse_args()
    if args.cmd == "probe":
        # 故意不带 ImageUrl，让网关回参数错误 → 证明鉴权通过 + Action 名正确
        st, r = call("ImageMatting")
        show("probe ImageMatting（空参数）", st, r)
    elif args.cmd == "matting":
        # in=query；实测枚举 WHITE_BACKGROUND|TRANSPARENT（文档给的 COLOR 会报 InvalidParameter）
        st, r = call("ImageMatting",
                     {"ImageUrl": args.url, "BackGroundType": "WHITE_BACKGROUND"})
        show("ImageMatting 智能抠图", st, r)
    elif args.cmd == "inpaint":
        # Action 实为 ImageRemove；指定坐标矩形消除走 Mask（in=query）
        st, r = call("ImageRemove",
                     {"ImageUrl": args.url, "Mask": args.boxes})
        show("ImageRemove 智能消除", st, r)
    elif args.cmd == "translate":
        # ImageTranslationPro 参数 in=formData → 走 body
        st, r = call("ImageTranslationPro", None,
                     {"ImageUrl": args.url, "SourceLanguage": args.src,
                      "TargetLanguage": args.dst})
        show("ImageTranslationPro 图片翻译Pro", st, r)
    elif args.cmd == "recognize":
        # 智能元素识别（前置探针）：ObjectDetectElements/NonObjectDetectElements in=query
        st, r = call("ImageRecognition",
                     {"ImageUrl": args.url, "NonObjectDetectElements": "[1,2,3]",
                      "ReturnCharacter": "1"})
        show("ImageRecognition 元素识别", st, r)


if __name__ == "__main__":
    main()
