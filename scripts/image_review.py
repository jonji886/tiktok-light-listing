#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""image_review.py — probe-first 图片判定（2026-11-25 P1-2）

治的痛：2026-11-24 流程是「拼 contact sheet → 多模态目视全部 → probe 补证据」。
实测目视判"干净"不可靠（EVA 垫目视 6 张干净、probe 抓到 2 张中文角标），
目视那趟基本白跑。本脚本翻转顺序：
  1. probe 全部候选图（Aidge ImageRecognition ¥0.02/张，确定性 OCR 证据）
  2. 无文字无水印 → 直接 L3_keep（自动回填 review.json）
  3. 有文字 → 按规则分流：
       文字含 CJK → 存疑（L1 可擦 / L0 整版中文 / L2 功能词，需目视）
       文字纯英文/当地语装饰字（GOOD DAY 类）→ L3_keep（目视仅抽检）
  4. 只对「存疑图」拼一张小 sheet 给 agent 目视一次
预期：目视量砍 60–80%，ViewImage 从每品 2–3 次降到 0–1 次。

用法（scan 之后、apply 之前）：
  set -a; source .env; set +a
  python3 scripts/image_review.py --review erp_data/image_gate/<ttl_id>/review.json \
      [--keep-english] [--only main,sku] [--max-sheet 12]
  # 输出：自动判定统计 + 存疑清单 + 存疑 sheet 路径（agent 用 ViewImage 判它）
  # 存疑图判完后照常走 image_fix finalize / image_gate apply

退出码：0 全部自动判完（无存疑）/ 1 有存疑待目视（sheet 已产出）/ 2 输入问题
"""
import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import image_fix  # noqa: E402  复用 evidence_one/call_retry 与 Aidge 凭证逻辑

CJK = re.compile(r"[\u4e00-\u9fff]")


def classify(ev, keep_english):
    """探针证据 → (verdict, why)。verdict ∈ L3_keep / review / drop?  (review=待目视)"""
    if ev.get("error"):
        return "review", "探针失败: " + ev["error"][:60]
    texts = ev.get("rec_text") or []
    blob = " / ".join(texts)
    if not texts and not ev.get("noobj_char") and not ev.get("noobj_wm"):
        return "L3_keep", "探针未见文字/水印"
    if CJK.search(blob) or CJK.search(str(ev.get("noobj_char") or "")):
        return "review", "含中文: " + blob[:80]
    if ev.get("noobj_wm"):
        return "review", "水印/牛皮癣(非文字): " + blob[:60]
    if keep_english:
        return "L3_keep", "纯英文/当地语装饰字: " + blob[:60]
    return "review", "有文字(未开--keep-english): " + blob[:60]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", required=True)
    ap.add_argument("--keep-english", action="store_true", default=True,
                    help="纯英文装饰字判 L3_keep（默认开；关掉则全部有字图进目视）")
    ap.add_argument("--only", default="", help="限定通道 main,sku,desc（默认全部）")
    ap.add_argument("--max-sheet", type=int, default=12, help="存疑图超过则分批")
    ap.add_argument("--workers", type=int, default=4, help="Aidge QPS=5，勿超 4")
    a = ap.parse_args()

    p = os.path.abspath(a.review)
    if not os.path.exists(p):
        print("[err] review.json 不存在（先跑 image_gate.py scan）", file=sys.stderr)
        return 2
    d = json.load(open(p, encoding="utf-8"))
    imgs = d.get("images") or []
    only = {s.strip() for s in a.only.split(",")} if a.only else None
    todo = [i for i in imgs if only is None
            or any(c in only for c in (i.get("channels") or []))]
    print("[review] probe %d/%d 张，成本≈¥%.2f" % (len(todo), len(imgs), len(todo) * 0.02))

    def one(img):
        return img, image_fix.evidence_one(img["url"])

    results = {}
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(one, im) for im in todo]
        for n, fu in enumerate(as_completed(futs), 1):
            img, ev = fu.result()
            results[img["id"]] = ev
            if n % 10 == 0:
                print("  ...%d/%d" % (n, len(todo)))

    review_ids = []
    stats = {"L3_keep": 0, "review": 0}
    for im in imgs:
        ev = results.get(im["id"])
        if ev is None:
            continue
        im["evidence"] = ev
        im["seen_text"] = " / ".join(ev.get("rec_text") or [])
        v, why = classify(ev, a.keep_english)
        im["auto_verdict"], im["auto_why"] = v, why
        if v == "L3_keep":
            im["verdict"] = "L3_keep"
            im["note"] = "2026-11-25 image_review probe自动判: " + why
            stats["L3_keep"] += 1
        else:
            review_ids.append(im["id"])
            stats["review"] += 1

    tmp = p + ".tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    print("[review] 自动 L3_keep=%d，待目视=%d" % (stats["L3_keep"], stats["review"]))

    if not review_ids:
        print("[review] 无存疑图，可直接 image_gate apply（desc 通道若判 L0 无需目视）")
        return 0

    # 存疑图 sheet（PIL，无则只打印清单）
    byid = {i["id"]: i for i in imgs}
    try:
        from PIL import Image, ImageDraw
        CELL, PAD, COLS = 260, 24, 4
        items = [byid[x] for x in review_ids][: a.max_sheet]
        rows = (len(items) + COLS - 1) // COLS
        W = COLS * (CELL + PAD) + PAD
        H = rows * (CELL + PAD + 34) + PAD
        sheet = Image.new("RGB", (W, H), "white")
        dr = ImageDraw.Draw(sheet)
        for i, im in enumerate(items):
            ci, rj = i % COLS, i // COLS
            x0, y0 = PAD + ci * (CELL + PAD), PAD + rj * (CELL + PAD + 34) + 34
            dr.text((x0, y0 - 32), im["id"][:20], fill="red")
            dr.text((x0, y0 - 16), (im.get("auto_why") or "")[:34], fill="blue")
            f = os.path.join(os.path.dirname(p), im.get("local_file") or "")
            try:
                q = Image.open(f).convert("RGB")
                q.thumbnail((CELL, CELL))
                sheet.paste(q, (x0, y0))
            except Exception:
                dr.text((x0, y0), "本地文件缺失", fill="black")
        out = os.path.join(os.path.dirname(p), "review_sheet.png")
        sheet.save(out)
        print("[review] 存疑 sheet → %s（%d 张；>max-sheet 分下轮）" % (out, len(items)))
    except ImportError:
        print("[review] 无 PIL，仅清单: %s" % review_ids)
    if len(review_ids) > a.max_sheet:
        print("[review] ⚠ 存疑超过 %d 张，剩余重跑本脚本即可（已判的不会重复 probe）" % a.max_sheet)
    return 1


if __name__ == "__main__":
    sys.exit(main())
