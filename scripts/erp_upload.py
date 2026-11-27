#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
erp_upload.py — ERP 官方图片上传通道（getUploadPolicy + OSS 表单直传），纯 curl/标准库可通。

2026-11-19 破解旧结论「getUploadPolicy curl 直调 500，只有浏览器里能通」：
  根因不是鉴权、不是浏览器专属，而是 **body 参数错了**。
  后端唯一必填参数是 `fileUploadTypeEnum`，枚举值 "PIC" / "VIDEO"
  （证据：前端 app.7cc506d6→d55ef933.js 内 OSSStore: `ItemFileGetUploadPolicyApi({fileUploadTypeEnum:a})`，
   a 来自 getOSSData("PIC") / getOSSData("VIDEO")；传 {} 或不符的字段 → 后端 NPE → 统一包成 errorCode 500）。

流程（与前端 Upload 组件完全一致）：
  1) POST /item/file/getUploadPolicy  {"fileUploadTypeEnum":"PIC"}
     → {dir, host, ossAccessKeyId, policy, signature, expire}
  2) POST <host>  multipart: key=<dir>+<文件名>, OSSAccessKeyId, policy, Signature, file
     → 204（默认）或 200（带 success_action_status=200）；body 为空。
  3) 最终公网 URL = host + "/" + key（kdzs-jxc-vpc 域名实测公网可直接 GET，hash 与本地一致）。

策略缓存：dir/签名按天有效（expire 为剩余秒数），本脚本进程内缓存即可，无需持久化。

用法：
  python3 erp_upload.py <file1> [file2 ...]          # 上传，打印 本地路径 -> 公网URL
  python3 erp_upload.py --verify <url> <local_file>  # 下载 URL 与本地文件比 SHA-256
约束（来自 policy conditions 与前端校验）：
  - PIC：仅 image/jpeg|png，单文件 < 200MB（policy content-length-range 0–209715200）
  - key 必须以 dir 开头（policy starts-with 条件），文件名建议 时间戳_随机.扩展名，避免覆盖
"""
import argparse, base64, hashlib, json, os, random, sys, time
import urllib.request, urllib.error

_DEFAULT_BASE = "https://erp.kuaidizs.cn"
_env_base = (os.environ.get("ERP_BASE_URL") or "").strip()
BASE = _env_base if _env_base and "example.com" not in _env_base and not _env_base.startswith("<") else _DEFAULT_BASE
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "erp_token.txt")
_policy_cache = {}


def _token():
    tok = os.environ.get("ERP_TOKEN") or open(TOKEN_FILE, encoding="utf-8").read().strip()
    return tok


def _req(url, data=None, headers=None, method=None, timeout=60):
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data is not None else "GET"))
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def get_policy(kind="PIC"):
    """取上传凭证。kind: PIC|VIDEO"""
    hit = _policy_cache.get(kind)
    if hit and time.time() < hit[1]:
        return hit[0]
    body = json.dumps({"fileUploadTypeEnum": kind}).encode()
    st, raw = _req(BASE + "/item/file/getUploadPolicy", body, {
        "Content-Type": "application/json;charset=UTF-8",
        "appType": "3",
        "qnquerystring": _token(),
    })
    j = json.loads(raw)
    if st != 200 or not j.get("success"):
        sys.exit(f"[upload] getUploadPolicy 失败 HTTP={st} resp={raw[:200]}（token 过期先跑 erp_token_grab.py --save）")
    p = j["data"]
    _policy_cache[kind] = (p, time.time() + max(60, int(p.get("expire", 300)) - 30))
    return p


def _multipart(fields, file_field, filename, content, content_type):
    boundary = "----kdzsUpload%s%04d" % (int(time.time()), random.randint(0, 9999))
    parts = []
    for k, v in fields.items():
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (boundary, k, v)).encode())
    parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
                  "Content-Type: %s\r\n\r\n" % (boundary, file_field, filename, content_type)).encode())
    parts.append(content + ("\r\n--%s--\r\n" % boundary).encode())
    return boundary, b"".join(parts)


def upload(path, kind="PIC", check_platform=True):
    ext = os.path.splitext(path)[1].lower()
    ctype = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext.lstrip("."))
    if check_platform and kind == "PIC" and ctype is None:
        sys.exit(f"[upload] PIC 仅支持 jpg/jpeg/png（平台校验，发布前置同理），跳过: {path}")
    content = open(path, "rb").read()
    p = get_policy(kind)
    fname = "%d_%s%s" % (int(time.time() * 1000), os.urandom(4).hex(), ext or ".png")
    key = p["dir"] + fname
    fields = {
        "key": key,
        "OSSAccessKeyId": p["ossAccessKeyId"],
        "policy": p["policy"],
        "Signature": p["signature"],
    }
    boundary, body = _multipart(fields, "file", fname, content, ctype or "application/octet-stream")
    st, raw = _req(p["host"], body, {"Content-Type": "multipart/form-data; boundary=%s" % boundary})
    if st not in (200, 204):
        sys.exit(f"[upload] OSS 直传失败 HTTP={st}: {raw[:300].decode('utf-8','replace')}")
    url = "%s/%s" % (p["host"], key)
    # 回读核验：公网可访问 + SHA-256 一致（与 verify_public_images 同一纪律）
    st2, got = _req(url)
    if st2 != 200:
        sys.exit(f"[upload] 回读失败 HTTP={st2} url={url}")
    if hashlib.sha256(got).hexdigest() != hashlib.sha256(content).hexdigest():
        sys.exit(f"[upload] 回读 hash 不一致 url={url}")
    return url, hashlib.sha256(content).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", help="要上传的本地图片")
    ap.add_argument("--kind", default="PIC", choices=["PIC", "VIDEO"])
    ap.add_argument("--no-platform-check", action="store_true", help="跳过 jpg/png 限制（非发布用途）")
    ap.add_argument("--verify", nargs=2, metavar=("URL", "LOCAL"), help="下载 URL 与本地文件比哈希")
    args = ap.parse_args()
    if args.verify:
        st, got = _req(args.verify[0])
        local = open(args.verify[1], "rb").read()
        ok = st == 200 and hashlib.sha256(got).hexdigest() == hashlib.sha256(local).hexdigest()
        print("MATCH" if ok else "MISMATCH", "HTTP", st)
        sys.exit(0 if ok else 1)
    if not args.files:
        ap.print_help()
        sys.exit(2)
    out = []
    for f in args.files:
        url, sha = upload(f, args.kind, check_platform=not args.no_platform_check)
        print(f"{f} -> {url}  (sha256={sha[:12]})")
        out.append({"local": f, "url": url, "sha256": sha})
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
