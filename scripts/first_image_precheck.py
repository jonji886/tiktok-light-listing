#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""first_image_precheck.py — 1688 候选首图水印/中文预检（批量拼图给多模态判，2026-11-19 沉淀）。

为什么：第五批实测淘汰 6/10 全因货源图片质量（中文横幅/水印/品牌标）。
发布用图必须 cbu01 原链（TikTok 会拉），修图救不了发布 → 检索后、认领前先把关。

用法：
  python3 scripts/first_image_precheck.py --search-json aibuy_data/opportunity/kw_xxx.json [--n 20]
  → 下载各 offerId 的 imageUrl，裁右下+底部水印区，拼一张 sheet 并打印清单；
  → 多模态看 sheet 后回填：CLEAN=[offerId,...]（首图无中文无水印的），只对 CLEAN 做 collect。
纯标准库+PIL（缺 PIL 时降级只下载不拼图）。
"""
import argparse, json, os, sys, urllib.request
try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

def fetch(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = urllib.request.urlopen(req, timeout=25).read()
    open(dest, "wb").write(data)
    return data

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--search-json", required=True, help="aibuy search 落盘 JSON")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--out-dir", default="erp_data/precheck")
    a = ap.parse_args()
    rows = json.load(open(a.search_json, encoding="utf-8"))[:a.n]
    os.makedirs(a.out_dir, exist_ok=True)
    got = []
    for r in rows:
        oid, u = str(r.get("offerId")), r.get("imageUrl") or ""
        if not u.startswith("http"):
            continue
        try:
            fp = os.path.join(a.out_dir, "%s.jpg" % oid)
            fetch(u, fp)
            got.append((oid, fp, r))
            print("ok", oid, r.get("price"), r.get("monthSold"), (r.get("title") or "")[:24])
        except Exception as e:
            print("FAIL", oid, str(e)[:60])
    if HAS_PIL and got:
        cols, th = 4, 260
        rows_n = (len(got) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * 268, rows_n * (th + 22)), "white")
        dr = ImageDraw.Draw(sheet)
        for i, (oid, fp, r) in enumerate(got):
            im = Image.open(fp).convert("RGB")
            nw = min(int(im.width * th / im.height), 260)
            x, y = (i % cols) * 268, (i // cols) * (th + 22)
            sheet.paste(im.resize((nw, th)), (x + 4, y + 20))
            dr.text((x + 4, y + 4), oid, fill="black")
        out = os.path.join(a.out_dir, "first_images_sheet.jpg")
        sheet.save(out, quality=78)
        print("[sheet]", out)
    print("[next] 多模态判 sheet：首图含中文横幅/水印/品牌标的 offerId 全部剔除，只对干净品 collect")

if __name__ == "__main__":
    main()
