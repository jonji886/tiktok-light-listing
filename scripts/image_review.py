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


def _local_evidence_batch(imgs, base):
    """Aidge 不可用时的本地 Vision OCR 回退，返回 {img_id: ev}。

    B3（2026-11-25 实损）：Aidge 欠费当天，probe 全线返回 error →
    classify 全部落到 "review" → review.json 留 pending → apply 报错 →
    agent 手工跑 evidence_local 回填，白花 202s 排查。现在内置回退。

    局限（不掩饰）：noobj_wm（非文字水印）依赖 Aidge 目标检测，本地拿不到，
    故一律置 False。回退判为 L3_keep 的图仍可能带非文字水印，evidence 里
    显式标 evidence_source=local_vision_ocr 供人工复核。
    """
    import img_grade as G
    paths = {}
    for im in imgs:
        lf = im.get("local_file") or ""
        p = os.path.join(base, lf)
        if lf and os.path.exists(p):
            paths[im["id"]] = p
            continue
        dst = os.path.join(base, str(im.get("id") or "x") + ".jpg")
        if G.dl(im["url"], dst):
            paths[im["id"]] = dst
    if not paths:
        return {}
    ocr_map = {}
    try:
        ocr_map = G.ocr_batch(list(paths.values()))
    except Exception as e:
        print("[review] 批量本地 OCR 异常（%s）→ 逐张回退" % str(e)[:50])
    out = {}
    for iid, fp in paths.items():
        od = ocr_map.get(str(fp))
        if od is None:
            try:
                od = G.ocr(fp)
            except Exception:
                od = {}
        texts = [x["text"] for x in (od.get("items") or []) if x.get("conf", 0) >= 0.25]
        out[iid] = {"rec_text": texts, "evidence_source": "local_vision_ocr",
                    "noobj_wm": False, "noobj_char": None,
                    "reason": "Aidge 不可用，本地 Vision OCR 真实识别（非文字水印不可检）"}
    return out


def classify(ev, keep_english):
    """探针证据 → (verdict, why)。verdict ∈ L3_keep / review / drop?  (review=待目视)"""
    if ev.get("error"):
        return "review", "探针失败: " + ev["error"][:60]
    # B3：标注证据来源，让「本地 OCR 判的」和「Aidge 判的」在 note 里可区分
    src = ev.get("evidence_source") or ""
    tag = "（本地OCR，非文字水印未检）" if src == "local_vision_ocr" else ""
    texts = ev.get("rec_text") or []
    blob = " / ".join(texts)
    if not texts and not ev.get("noobj_char") and not ev.get("noobj_wm"):
        return "L3_keep", "探针未见文字/水印" + tag
    if CJK.search(blob) or CJK.search(str(ev.get("noobj_char") or "")):
        return "review", "含中文: " + blob[:80] + tag
    if ev.get("noobj_wm"):
        return "review", "水印/牛皮癣(非文字): " + blob[:60]
    if keep_english:
        return "L3_keep", "纯英文/当地语装饰字: " + blob[:60] + tag
    return "review", "有文字(未开--keep-english): " + blob[:60] + tag


def can_reuse(im):
    """A（2026-11-26）：判断这张图能否跳过 Aidge probe、沿用已有判定。

    只有同时满足才复用（保守，先保 678 硬闸不破）：
      ① 已有 evidence.rec_text（C 段 auto_verdict 或 judge_cache 回填的本地 OCR 证据）
      ② verdict 已确定（非 pending）：L0_drop 要删的图不需要 probe；L3_keep 要放行
      ③ 不是「main 通道 + L3_keep」——这是 678 事故点，必须跑 Aidge probe 补检
         非文字水印（Aidge 的 noobj_wm 是本地 OCR 检不到的独有能力）
    sku/desc 通道维持宽松（与 image_gate.main_gate_violations 只强制 main 一致）：
    有本地证据就放行，避免为详情图白花 probe 费。
    """
    ev = im.get("evidence") or {}
    if "rec_text" not in ev:
        return False
    v = im.get("verdict")
    if v not in ("L0_drop", "L3_keep"):
        return False
    if v == "L3_keep" and "main" in (im.get("channels") or []):
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", required=True)
    ap.add_argument("--keep-english", action="store_true", default=True,
                    help="纯英文装饰字判 L3_keep（默认开；关掉则全部有字图进目视）")
    ap.add_argument("--only", default="", help="限定通道 main,sku,desc（默认全部）")
    ap.add_argument("--max-sheet", type=int, default=12, help="存疑图超过则分批")
    ap.add_argument("--workers", type=int, default=4, help="Aidge QPS=5，勿超 4")
    ap.add_argument("--reuse-cache", dest="reuse_cache", action="store_true", default=True,
                    help="已有合法证据的图跳过 Aidge probe（默认开；省 ¥0.02/张）")
    ap.add_argument("--force-probe", dest="reuse_cache", action="store_false",
                    help="强制所有图都跑 Aidge probe（回退旧行为）")
    a = ap.parse_args()

    p = os.path.abspath(a.review)
    if not os.path.exists(p):
        print("[err] review.json 不存在（先跑 image_gate.py scan）", file=sys.stderr)
        return 2
    d = json.load(open(p, encoding="utf-8"))
    imgs = d.get("images") or []
    only = {s.strip() for s in a.only.split(",")} if a.only else None
    in_scope = [i for i in imgs if only is None
                or any(c in only for c in (i.get("channels") or []))]
    reused = {i["id"] for i in in_scope if a.reuse_cache and can_reuse(i)}
    todo = [i for i in in_scope if i["id"] not in reused]
    print("[review] probe %d/%d 张，复用证据 %d 张，成本≈¥%.2f（原 ¥%.2f）"
          % (len(todo), len(imgs), len(reused), len(todo) * 0.02, len(in_scope) * 0.02))

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
    stats = {"L3_keep": 0, "review": 0, "reused": len(reused)}

    # A：复用图沿用 C 段判级（不重新 classify——C 段已按同口径判过，
    # 重判反而可能因 classify 的 keep_english 规则改变结论）。只补来源标记。
    for im in in_scope:
        if im["id"] in reused:
            ev = im.get("evidence") or {}
            im["seen_text"] = " / ".join(ev.get("rec_text") or [])
            im["auto_verdict"] = "reused"
            im["auto_why"] = "复用 C 段判定（%s），跳过 Aidge probe" % (
                ev.get("evidence_source") or "cache")
            if im.get("verdict") == "L3_keep":
                stats["L3_keep"] += 1

    # B3：Aidge 探针失败 → 自动本地 Vision OCR 回退，不留 pending
    failed = [im for im in todo if im["id"] in results and (results[im["id"]] or {}).get("error")]
    if failed:
        err0 = str((results[failed[0]["id"]] or {}).get("error"))[:70]
        print("[review] Aidge 探针失败 %d/%d 张（示例: %s）" % (len(failed), len(todo), err0))
        print("[review] → 自动切本地 Vision OCR 回退（evidence_source=local_vision_ocr）")
        local = _local_evidence_batch(failed, os.path.dirname(p))
        for im in failed:
            if im["id"] in local:
                results[im["id"]] = local[im["id"]]
        print("[review] 本地回退成功 %d/%d 张" % (len(local), len(failed)))

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
