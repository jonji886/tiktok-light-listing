#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地 Vision OCR 回填 image_gate 主图证据（Aidge 欠费时的诚实降级）。

背景（2026-11-25）：阿里云 Aidge 按量付费账户欠费（Service suspended due to
overdue payment），image_fix.py probe 全线失败，导致主图 L3_keep 因缺
evidence.rec_text 被 image_gate apply 拦截。

本脚本用 img_grade.py 的本地 macOS Vision OCR（零成本、真实识别）回填
evidence.rec_text，并在 evidence.evidence_source 明确标注来源=local_vision。
不伪造、不跳过闸门：识别到中文的图照样会被 image_gate 的 CJK 检查拦截。

用法：python3 scripts/evidence_local.py --review <review.json> [--only L3_keep]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import img_grade  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", required=True)
    ap.add_argument("--only", default="L3_keep")
    a = ap.parse_args()
    d = json.load(open(a.review, encoding="utf-8"))
    only = {s.strip() for s in a.only.split(",")} if a.only else None
    todo = [im for im in d["images"]
            if (only is None or im.get("verdict") in only)
            and "main" in (im.get("channels") or [])]
    print(f"[ev-local] 待回填 {len(todo)}/{len(d['images'])} 张主图（本地 Vision OCR）")
    tmp = Path("/tmp/evidence_local")
    if not tmp.exists():
        tmp.mkdir(parents=True, exist_ok=True)
    for n, im in enumerate(todo, 1):
        p = tmp / f"{n}.jpg"
        if not img_grade.dl(im["url"], p):
            print(f"  [{n}] 下载失败 {im['url'][-50:]}")
            continue
        try:
            ocr = img_grade.ocr(p)
            items = [x for x in ocr.get("items", []) if x.get("conf", 0) >= 0.25]
            texts = [x["text"] for x in items]
        except Exception as e:
            print(f"  [{n}] OCR 失败 {str(e)[:60]}")
            continue
        im["evidence"] = {
            "rec_text": texts,
            "evidence_source": "local_vision_ocr",
            "reason": "Aidge 欠费降级；本地 Vision OCR 真实识别结果",
        }
        im["seen_text"] = " / ".join(texts)
        cjk = [t for t in texts if img_grade.CJK.search(t)]
        print(f"  [{n}] {im.get('id')}: {len(texts)} 段文字, CJK={len(cjk)}"
              + (f" ⚠{cjk[:3]}" if cjk else " ✓"))
    json.dump(d, open(a.review, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("[ev-local] 已写回", a.review)


if __name__ == "__main__":
    main()
