#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ip_risk.py — IP 风险强制人工复核（B1，2026-11-25 641912316438 实损）

治的痛：641912316438 的 SKU 规格图是 Hello Kitty 形象，OCR 读成 `slelo slitluy`，
品牌词表（当时只有中文）拦不住，一路走过 precheck → 装配 → edit_dry，
直到 review_img 阶段被我**偶然**看图才发现 → 整品报废，白花装配+编辑干跑。
「靠偶然发现的检查」等于没有检查。

本脚本把 IP 检查变成**必然发生**的一步，且只看真的需要看的东西：
  ① 扫标题 + 全部 SKU 属性值 + review.json 的 OCR 文本，命中形象暗示词 → 进队列
  ② 对队列品，把**去重后的 SKU 规格图**拼成一张 sheet（SKU 图是 IP 重灾区：
     本轮 6419 的 Hello Kitty 全在 SKU 图上，主图反而是干净白底）
  ③ agent 一次 ViewImage 看完，判定 IP 与否，结果写回 verdict 文件

词表是**宽进**的（宁可多看几眼）：命中不代表侵权，只代表"必须人看一眼"。

用法：
  python3 scripts/ip_risk.py --offers 641912316438,739330069435 [--scan-only] [--max-per 12]
  python3 scripts/ip_risk.py --auto-list --pool erp_data/_run/xxx_clean.csv  # 批量扫已预检的品

输出：
  erp_data/_run/ip_risk/sheet_<ts>.png   去重 SKU 图拼图（每个品一行）
  erp_data/_run/ip_risk/report_<ts>.json 命中词 + 图位置标注

退出码：0 无命中 / 1 有命中需人工复核 / 2 输入问题
"""
import argparse
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import img_grade as G  # noqa: E402

# 形象暗示词：宽进，只用来"筛出要人看的品"，不作硬杀依据
SHAPE_RE = None
try:
    import re
    SHAPE_RE = re.compile(
        r"卡通形象|卡通人物|动漫角色|公仔|玩偶|毛绒玩具|手办|正版|授权|联名|同款|"
        r"猫脸|猫头|大脸|立体脸|立体.{0,2}脸|兔头|熊头|猪头|鸭头|脸型|大头贴|"
        r"muka\s*besar|muka\s*kucing|watak\s*kartun|"
        r"3d\s*face|cartoon\s*character|plush\s*toy|figure",
        re.I)
except Exception:
    pass


def detail_of(oid):
    """取该品最新 detail.json。返回 (v2, path) 或 (None, '')。"""
    fs = sorted(glob.glob(os.path.join(ROOT, "erp_data/_run/pipeline/%s/detail.json/*" % oid)))
    if not fs:
        return None, ""
    p = fs[-1]
    try:
        d = json.load(open(p, encoding="utf-8"))
        return d["reqInfo"]["tikTokItemModel"]["productCreateRequestV2"], p
    except Exception:
        return None, p


def scan(oid, review_path=""):
    """返回 {offer_id, title, hits:[...], sku_values:[...], sku_imgs:[uri...]}。"""
    v2, dp = detail_of(oid)
    out = {"offer_id": oid, "detail": dp, "title": "", "hits": [],
           "sku_values": [], "sku_imgs": [], "risk": False}
    if not v2:
        out["error"] = "无 detail"
        return out
    out["title"] = v2.get("title") or ""
    texts = [out["title"]]
    seen_img = []
    for s in (v2.get("skus") or []):
        for a in (s.get("sales_attributes") or []):
            v = (a.get("value_name") or "").strip()
            if v and v not in out["sku_values"]:
                out["sku_values"].append(v)
                texts.append(v)
            u = ((a.get("sku_img") or {}) or {}).get("uri")
            if u and u not in seen_img:
                seen_img.append(u)
    out["sku_imgs"] = seen_img
    # OCR 文本（review.json 里已有，不重复跑 OCR）
    if review_path and os.path.exists(review_path):
        try:
            rj = json.load(open(review_path, encoding="utf-8"))
            for im in (rj.get("images") or []):
                t = im.get("seen_text") or ""
                if t:
                    texts.append(t)
        except Exception:
            pass
    if SHAPE_RE:
        for t in texts:
            for m in SHAPE_RE.finditer(t or ""):
                w = m.group(0)
                if w not in out["hits"]:
                    out["hits"].append(w)
    out["risk"] = bool(out["hits"])
    return out


def build_sheet(items, max_per=12, outdir=None):
    """每个品一行：去重 SKU 图 + 左侧标注。返回 png 路径或 None。"""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    CELL, PAD, TXT = 200, 14, 26
    rows = []
    tmp = "/tmp/ip_risk_imgs"
    os.makedirs(tmp, exist_ok=True)
    for it in items:
        uris = it["sku_imgs"][:max_per]
        paths = []
        for k, u in enumerate(uris):
            dst = os.path.join(tmp, "%s_%02d.jpg" % (it["offer_id"], k))
            if os.path.exists(dst) and os.path.getsize(dst) > 3000:
                paths.append(dst)
            elif G.dl(u, dst):
                paths.append(dst)
        rows.append((it, paths))
    if not any(p for _, p in rows):
        return None
    cols = max(len(p) for _, p in rows) or 1
    W = 210 + cols * (CELL + PAD) + PAD
    H = PAD + len(rows) * (CELL + PAD + TXT)
    sheet = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(sheet)
    for r, (it, paths) in enumerate(rows):
        y0 = PAD + r * (CELL + PAD + TXT)
        dr.text((10, y0 + 4), it["offer_id"], fill="red")
        dr.text((10, y0 + 20), ("命中:" + ",".join(it["hits"]))[:26], fill="blue")
        for c, p in enumerate(paths):
            x0 = 210 + c * (CELL + PAD)
            try:
                q = Image.open(p).convert("RGB")
                q.thumbnail((CELL, CELL))
                sheet.paste(q, (x0, y0 + TXT))
                dr.text((x0, y0 + 2), "sku%d" % c, fill="gray")
            except Exception:
                dr.text((x0, y0 + TXT), "缺失", fill="black")
    outdir = outdir or os.path.join(ROOT, "erp_data/_run/ip_risk")
    os.makedirs(outdir, exist_ok=True)
    p = os.path.join(outdir, "sheet_%s.png" % time.strftime("%Y%m%d_%H%M%S"))
    sheet.save(p)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offers", default="", help="逗号分隔 offer_id")
    ap.add_argument("--pool", default="", help="候选 CSV（取 offer_id 列）")
    ap.add_argument("--scan-only", action="store_true", help="只扫不拼图")
    ap.add_argument("--max-per", dest="max_per", type=int, default=12, help="每品最多拼几张 SKU 图")
    a = ap.parse_args()

    ids = [x.strip() for x in a.offers.split(",") if x.strip()]
    if a.pool:
        import csv
        for r in csv.DictReader(open(os.path.join(ROOT, a.pool), encoding="utf-8-sig")):
            o = str(r.get("offer_id") or r.get("offerId") or "").strip()
            if o and o not in ids:
                ids.append(o)
    if not ids:
        print("[ip] 需 --offers 或 --pool", file=sys.stderr)
        return 2

    results = []
    for oid in ids:
        rvp = ""
        sp = os.path.join(ROOT, "erp_data/_run/pipeline/%s.json" % oid)
        if os.path.exists(sp):
            try:
                st = json.load(open(sp, encoding="utf-8"))
                if st.get("ttl_id"):
                    cand = os.path.join(ROOT, "erp_data/image_gate/%s/review.json" % st["ttl_id"])
                    if os.path.exists(cand):
                        rvp = cand
            except Exception:
                pass
        results.append(scan(oid, rvp))

    risky = [r for r in results if r.get("risk")]
    print("[ip] 扫描 %d 品，命中 %d 品需人工复核" % (len(results), len(risky)))
    for r in risky:
        print("  ⚠ %-14s 命中 %s" % (r["offer_id"], ",".join(r["hits"])))
        print("     SKU 值样例: %s" % " | ".join(r["sku_values"][:4]))
    clean = [r for r in results if not r.get("risk") and not r.get("error")]
    if clean:
        print("  ✓ 无命中 %d 品: %s" % (len(clean), ",".join(x["offer_id"] for x in clean)))

    sheet = None
    if risky and not a.scan_only:
        sheet = build_sheet(risky, a.max_per)
        if sheet:
            print("[ip] 复核拼图 → %s（每行一个品，看 SKU 图上是否有卡通 IP 形象）" % sheet)
        else:
            print("[ip] 拼图失败（无 SKU 图或 PIL 缺失），请逐品看图")

    rp = os.path.join(ROOT, "erp_data/_run/ip_risk/report_%s.json" % time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(os.path.dirname(rp), exist_ok=True)
    json.dump({"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "sheet": sheet,
               "results": results}, open(rp, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("[ip] 报告 → %s" % rp)
    return 1 if risky else 0


if __name__ == "__main__":
    sys.exit(main())
