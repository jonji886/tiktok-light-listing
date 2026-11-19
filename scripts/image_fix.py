#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""image_fix.py — L1/L2 图片修复管道（Aidge 专用 API → ERP 图床 → uri_map 回填）

背景（2026-11-19 A/B 实测定型，见 references/erp-pitfalls.md 同日条目）：
  修图走阿里云 Aidge（零依赖 V3 签名，复用 aidge_http.py）：
    - ImageRemove 元素模式 NonObjectRemoveElements=[1,2,3,4]：自动消除非主体的
      文字/水印/logo/牛皮癣，mask 外像素不动（实测四宫格 4 标签全净）
    - ImageRemove Mask 模式：复检发现残留时按 img.fix_boxes 定向补擦
    - ImageTranslationPro：功能标注 中→当地语言（保版式回填）
    - ImageMatting WHITE_BACKGROUND：首图白底（TikTok 硬要求）
  不用 wan2.7-image 修图：整图重绘破坏商品保真（实测织纹/色调/分辨率全变），
  命中 TikTok「真实反映收到商品」合规线。

流程位置（image_gate.py 判定与 apply 之间的中间件）：
  scan → 目视判定 → probe(补证据) → 修正判定 → fix(修复+转存) → 目视复检 →
  finalize(校验) → image_gate apply → verify → publish

纪律：
  - 修好的图立即下载 → erp_upload 转存 ERP OSS（Aidge 结果链约 30 天过期，不可直接进 uri_map）
  - 复检五条款：①翻译/文字完整正确 ②无中文残留 ③无幻觉字 ④无URL残留 ⑤商品本体未被改动
  - 复检不过：verdict 改 L0_drop 重新 finalize；宁缺毋滥，不无限返修（补刀最多 1 轮）
  - 本脚本只写 review.json 与 gate 目录下的修复图，不碰 ERP 商品数据
用法：
  set -a; source .env; set +a
  python3 scripts/image_fix.py probe    --review <review.json> [--only L0_drop,pending]
  python3 scripts/image_fix.py fix      --review <review.json> [--dst ms] [--matting-first]
  python3 scripts/image_fix.py finalize --review <review.json>
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import aidge_http  # noqa: E402
import erp_upload  # noqa: E402

# 刊例价（元/张，2026-11-19 官方计费页；仅成本估算，非对账）
PRICE = {"recognize": 0.02, "remove": 0.025, "translate": 0.06, "matting": 0.002}
SLEEP = 0.25  # Aidge 默认 QPS=5


def load_review(path):
    p = Path(path).resolve()
    return p, json.loads(p.read_text(encoding="utf-8"))


def save_review(p, d):
    bak = p.with_name(p.name + ".bak")
    if not bak.exists():  # 只留最早一份，可反复回滚到 scan 后原始判定
        bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def call_retry(action, params=None, body=None, tries=2):
    """网络/限流重试 1 次；业务错误码不重试。返回 (status, dict)。"""
    last = (0, None)
    for _ in range(tries):
        st, r = aidge_http.call(action, params, body)
        if isinstance(r, dict) and (r.get("Success") is True
                                    or str(r.get("Code", "")).lower() == "success"):
            return st, r
        last = (st, r)
        if isinstance(r, dict) and r.get("Code") not in (
                "Throttling", "InternalError", "DownstreamUnavailable"):
            break
        time.sleep(1.5)
    return last


def evidence_one(url):
    """元素识别探针（¥0.02）：产出 seen_text 证据，治「空判 L0」。"""
    st, r = call_retry("ImageRecognition", {
        "ImageUrl": url, "NonObjectDetectElements": "[1,2,3]",
        "ObjectDetectElements": "[1,2,3]", "ReturnCharacter": "1"})
    d = (r or {}).get("Data") if isinstance(r, dict) else None
    if not d:
        return {"error": str(r)[:200]}
    return {"rec_text": d.get("RecText") or [],
            "noobj_char": d.get("NoobjCharacter"),
            "obj_char": d.get("ObjCharacter"),
            "noobj_wm": bool(d.get("NoobjWatermark")) or bool(d.get("NoobjNpx"))}


def suggest_verdict(ev):
    if ev.get("error"):
        return "pending", "探针失败，需人工"
    if ev["rec_text"] or ev["noobj_char"] or ev["noobj_wm"]:
        return "L1_inpaint", "非主体文字/水印可擦: " + " / ".join(ev["rec_text"][:6])
    return "L3_keep", "探针未见文字/水印（拼贴/图标仍须目视）"


def cmd_probe(args):
    p, d = load_review(args.review)
    only = {s.strip() for s in args.only.split(",")} if args.only else None
    todo = [i for i in d["images"] if only is None or i.get("verdict") in only]
    print(f"[probe] {len(todo)}/{len(d['images'])} 张，成本≈¥{len(todo)*PRICE['recognize']:.2f}")
    for n, img in enumerate(todo, 1):
        ev = evidence_one(img["url"])
        img["evidence"] = ev
        if not ev.get("error"):
            img["seen_text"] = " / ".join(ev["rec_text"])  # 证据落进原字段
        img["probe_suggest"], img["probe_why"] = suggest_verdict(ev)
        if n % 10 == 0:
            print(f"  ...{n}/{len(todo)}")
        time.sleep(SLEEP)
    save_review(p, d)
    conflicts = [i for i in d["images"]
                 if i.get("verdict") == "L0_drop"
                 and i.get("probe_suggest") in ("L1_inpaint", "L3_keep")
                 and len(i.get("evidence", {}).get("rec_text", [])) <= 4]
    print(f"[probe] 完成。⚠ 疑似过杀（判 L0 但探针只见少量可擦文字/无文字）{len(conflicts)} 张，"
          f"目视复核后可改 L1_inpaint/L2_localize/L3_keep：")
    for c in conflicts[:30]:
        print("   ", c["id"], c["url"][-42:], "|", c.get("probe_why", "")[:60])


def fetch_to(url, dest):
    """Aidge 临时链 → 本地。按魔数定扩展名（ERP PIC 只收 jpg/png）。"""
    urllib.request.urlretrieve(url, dest)
    with open(dest, "rb") as f:
        head = f.read(4)
    if head[:2] == b"\xff\xd8":
        real = dest.with_suffix(".jpg")
    elif head[:8] == b"\x89PNG\r\n\x1a\n":
        real = dest.with_suffix(".png")
    else:
        raise RuntimeError(f"结果非 jpg/png: {dest} head={head!r}")
    if real != dest:
        os.replace(dest, real)
    return real


def fix_one(img, dst_lang, gate_dir, umap):
    """单图修复+转存。返回新 ERP URL；失败返回 None（写 img['fix_error']）。
    起点优先取 uri_map 里的上一轮结果（补刀场景：在原图已擦字的基础上继续擦图形），
    没有才从 1688 原图开始。"""
    cur = umap.get(img["url"], img["url"])
    v = img["verdict"]
    if v == "L1_inpaint":
        boxes = img.get("fix_boxes")
        # 实测（2026-11-19 元数据）：Mask 要 RLE 编码（配编辑器），矩形坐标走 Position
        # 优先级 UserImage>UserText>Position>Mask>元素模式；Position 格式 [x1,y1,x2,y2]
        if boxes:
            pos = boxes[0] if isinstance(boxes[0], list) else boxes
            params = {"ImageUrl": cur, "Position": json.dumps(pos, separators=(",", ":"))}
        else:
            params = {"ImageUrl": cur, "NonObjectRemoveElements": "[1,2,3,4]"}
        st, r = call_retry("ImageRemove", params)
        d = (r or {}).get("Data") if isinstance(r, dict) else None
        if not d or not d.get("ImageUrl"):
            img["fix_error"] = str(r)[:200]
            return None
        cur = d["ImageUrl"]
    if v == "L2_localize":
        body = {"ImageUrl": cur, "SourceLanguage": "auto", "TargetLanguage": dst_lang}
        st, r = call_retry("ImageTranslationPro", None, body)
        if isinstance(r, dict) and r.get("Code") == "InvalidParameter":
            body["TargetLanguage"] = "en"
            img["note"] = (img.get("note") or "") + f" [目标语言{dst_lang}无效→回退en]"
            st, r = call_retry("ImageTranslationPro", None, body)
        d = (r or {}).get("Data") if isinstance(r, dict) else None
        rl = (d or {}).get("ResultList") or []
        if not rl or not rl[0].get("FileUrl"):
            img["fix_error"] = str(r)[:200]
            return None
        cur = rl[0]["FileUrl"]
    stem = re.sub(r"[^a-z0-9]", "", img.get("id", "img").lower()) or "img"
    dest = gate_dir / f"fixed_{stem}"
    local = fetch_to(cur, dest)
    erp_url, sha = erp_upload.upload(str(local))
    img["fixed_local"] = local.name
    img["recheck"] = "pending"
    return erp_url


def cmd_fix(args):
    p, d = load_review(args.review)
    gate_dir = p.parent
    todo = [i for i in d["images"]
            if i.get("verdict") in ("L1_inpaint", "L2_localize")
            and i.get("recheck") != "ok"]
    umap = d.setdefault("uri_map", {})
    cost = 0.0
    print(f"[fix] 待修 {len(todo)} 张（目标语言 {args.dst}）")
    for n, img in enumerate(todo, 1):
        url = fix_one(img, args.dst, gate_dir, umap)
        cost += PRICE["remove"] if img["verdict"] == "L1_inpaint" else PRICE["translate"]
        if url:
            umap[img["url"]] = url
            print(f"  [{n}/{len(todo)}] {img['id']} -> {url}")
        else:
            print(f"  [{n}/{len(todo)}] {img['id']} FAIL: {img.get('fix_error','')[:120]}")
        time.sleep(SLEEP)
    if args.matting_first:
        first = next((i for i in d["images"] if i.get("id") == "main_01"), None)
        if first:
            src = umap.get(first["url"], first["url"])
            st, r = call_retry("ImageMatting", {"ImageUrl": src,
                                                "BackGroundType": "WHITE_BACKGROUND"})
            dd = (r or {}).get("Data") if isinstance(r, dict) else None
            if dd and dd.get("ImageUrl"):
                local = fetch_to(dd["ImageUrl"], gate_dir / "fixed_first_white")
                erp_url, sha = erp_upload.upload(str(local))
                umap[first["url"]] = erp_url
                first["fixed_local"] = local.name
                first["recheck"] = "pending"
                cost += PRICE["matting"]
                print(f"  [首图白底] main_01 -> {erp_url}")
            else:
                print(f"  [首图白底] FAIL: {str(r)[:150]}")
    save_review(p, d)
    print(f"[fix] 完成。调用成本≈¥{cost:.3f}（免费额度内则 0）")
    print("[fix] ⚠ 复检五条款（目视 fixed_*）：①文字完整正确 ②无中文残留 ③无幻觉字 "
          "④无URL残留 ⑤商品本体未被改动。过→recheck改\"ok\"；不过→补刀(fix_boxes 加进该图重跑 fix，"
          "限1轮)或 verdict 改 L0_drop。")


def cmd_finalize(args):
    p, d = load_review(args.review)
    umap = d.get("uri_map") or {}
    bad = [i for i in d["images"]
           if i.get("verdict") in ("L1_inpaint", "L2_localize")
           and i["url"] not in umap and i.get("recheck") != "ok"]
    pend = [i for i in d["images"] if i.get("recheck") == "pending"]
    print(f"[finalize] L1/L2 缺 URL: {len(bad)} | 待复检: {len(pend)}")
    for i in bad:
        print("   缺:", i["id"], i["url"][-40:])
    for i in pend:
        print("   待复检:", i.get("fixed_local", "(无)"))
    if bad or pend:
        sys.exit(1)
    print("[finalize] 全部就绪 → 可跑 image_gate.py apply")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("probe"); s.add_argument("--review", required=True)
    s.add_argument("--only", default="", help="逗号分隔 verdict 过滤，默认全部")
    s = sub.add_parser("fix"); s.add_argument("--review", required=True)
    s.add_argument("--dst", default="ms", help="L2 目标语言（MY=ms/TH=th/VN=vi）")
    s.add_argument("--matting-first", action="store_true")
    s = sub.add_parser("finalize"); s.add_argument("--review", required=True)
    args = ap.parse_args()
    {"probe": cmd_probe, "fix": cmd_fix, "finalize": cmd_finalize}[args.cmd](args)


if __name__ == "__main__":
    main()
