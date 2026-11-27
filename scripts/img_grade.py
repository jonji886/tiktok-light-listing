#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图集四级打标（图片语言策略 P0 / Step2 前置共用）。

四级口径（与 图片语言策略MVP修改方案.md 一致）：
  A 白底/纯净底 + 无文字      —— 理想主图
  B 有背景无文字              —— 可作副图，主图勉强
  C 含英文/数字等文字（无中文）—— 需人工判断是否促销字
  D 含中文营销文字            —— 主图=硬闸FAIL；描述图=删/裁/擦

实现：本地 macOS Vision OCR + 白度统计（零 API 成本，链路已实测）。
     OCR 对艺术字/小字可能漏检 → D 判定偏低是安全方向的偏差，漏掉的由
     VL 抽检层补（--vl 预留）。B2B 行话词（定制/批发/一件代发…）单列标记。

用法:
  # 从 published_items_full.json 直接打标主图（默认）
  python3 img_grade.py main --out ./erp_data
  # 指定货源ID子集
  python3 img_grade.py main --ids 123,456 --out ./erp_data
  # 任意图片URL清单（每行一个，可带 <id>\\t<url>）
  python3 img_grade.py urls --file list.txt --out ./erp_data

输出: erp_data/img_grade_main.csv/.json （增量安全：已打标货源ID默认跳过，--redo 重打）
退出码: 0 正常; 2 有下载/OCR失败条目（见 fail 列）
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OCR_SWIFT = Path(__file__).resolve().parent / "ocr_vision.swift"
CJK = re.compile(r"[\u4e00-\u9fff]")
LATIN = re.compile(r"[A-Za-z]")
B2B_WORDS = ["定制", "批发", "源头", "厂家", "工厂", "代发", "现货", "量大",
             "加盟", "贴标", "一件代发", "起批", "拿货", "供货", "OEM", "ODM", "MOQ"]
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://detail.1688.com/"}

try:
    from PIL import Image
except ImportError:
    sys.exit("[grade] 需要 Pillow：pip3 install pillow")


def dl(url, path):
    if path.exists() and path.stat().st_size > 2000:
        return True
    try:
        req = urllib.request.Request(url, headers=UA)
        path.write_bytes(urllib.request.urlopen(req, timeout=30).read())
        return True
    except Exception as e:
        return False


def ocr(path):
    r = subprocess.run(["swift", str(OCR_SWIFT), str(path)],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:150])
    return json.loads(r.stdout)


OCR_BATCH_SWIFT = Path(__file__).resolve().parent / "ocr_vision_batch.swift"


def ocr_batch(paths, max_per_call=40):
    """批量 OCR：一个 swift 进程处理多张图，摊薄冷启动（实测 5x 加速）。

    返回 {str(path): ocr_dict}。批量版对中文更敏感（request 复用），判级方向安全。
    失败项回退单图 ocr()，保证不因批量故障丢失判级。
    """
    out = {}
    paths = [str(p) for p in paths]
    for start in range(0, len(paths), max_per_call):
        chunk = paths[start:start + max_per_call]
        try:
            r = subprocess.run(["swift", str(OCR_BATCH_SWIFT)] + chunk,
                               capture_output=True, text=True, timeout=180)
            if r.returncode == 0:
                for item in json.loads(r.stdout):
                    if item.get("ok"):
                        out[item["file"]] = item
        except Exception:
            pass
        # 回退：批量未返回的图逐个单图 OCR
        for p in chunk:
            if p not in out:
                try:
                    out[p] = ocr(p)
                except Exception:
                    pass
    return out


def whiteness(im):
    px = list(im.getdata())
    white = sum(1 for R, G, B in px if R > 230 and G > 230 and B > 230) / max(1, len(px))

    def band(box):
        d = list(im.crop(box).getdata())
        return sum(1 for r, g, b in d if r > 225 and g > 225 and b > 225) / max(1, len(d))
    n = 160
    edge = (band((0, 0, n, 13)) + band((0, n - 13, n, n)) +
            band((0, 0, 13, n)) + band((n - 13, 0, n, n))) / 4
    return round(white, 3), round(edge, 3)


def grade_from_ocr(d, path):
    """从已完成的 OCR 结果 + 图片路径算判级。批量/串行共用，保证判级逻辑零漂移。"""
    W, H = d["width"], d["height"]
    items = [x for x in d.get("items", []) if x.get("conf", 0) >= 0.25]
    text = " ".join(x["text"] for x in items)
    area_pct = sum(x["w"] * x["h"] for x in items) / max(1, W * H) * 100
    cjk_n = len(CJK.findall(text))
    has_latin = bool(LATIN.search(text))
    b2b = [w for w in B2B_WORDS if w in text]

    im = Image.open(path).convert("RGB").resize((160, 160))
    white, edge = whiteness(im)

    if cjk_n > 0:
        g = "D"                    # 含中文营销文字
    elif has_latin or area_pct > 1.5:
        g = "C"                    # 有英文/大段文字（含尺寸标注，需人工判是否促销）
    elif edge >= 0.55:
        g = "A"                    # 白底无字
    else:
        g = "B"                    # 有背景无字
    return dict(grade=g, cjk=cjk_n, text_pct=round(area_pct, 1),
                white=white, edge=edge, b2b=",".join(b2b),
                text=text[:80], size="%dx%d" % (W, H))


def grade_one(path):
    """返回 dict: grade, cjk, text_pct, white, edge, b2b, text样本"""
    return grade_from_ocr(ocr(path), path)


def load_existing(outdir):
    p = Path(outdir) / "img_grade_main.json"
    if p.exists():
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return {}


def write_out(outdir, res):
    rows = list(res.values())
    jp = Path(outdir) / "img_grade_main.json"
    cp = Path(outdir) / "img_grade_main.csv"
    json.dump(res, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if rows:
        with open(cp, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    return cp


def cmd_main(args):
    src = Path(args.items_json)
    items = json.load(open(src, encoding="utf-8"))
    if isinstance(items, dict):
        items = items.get("data") or list(items.values())[0]
    by_sid = {}
    for it in items:
        sid = str(it.get("platNumIid") or "")
        pic = it.get("picUrl") or ""
        if sid and pic.startswith("http"):
            by_sid[sid] = {"pic": pic, "title": (it.get("title") or "")[:40]}
    if args.ids:
        want = set(x.strip() for x in args.ids.split(",") if x.strip())
        by_sid = {k: v for k, v in by_sid.items() if k in want}
    res = load_existing(args.out)
    todo = [(k, v) for k, v in by_sid.items() if args.redo or k not in res or res[k].get("fail")]
    print("[grade] 目标 %d，待打 %d（已有 %d）" % (len(by_sid), len(todo), len(by_sid) - len(todo)))
    imgdir = Path(args.out) / "grade_imgs"
    imgdir.mkdir(parents=True, exist_ok=True)
    fails = 0
    for i, (sid, v) in enumerate(todo, 1):
        p = imgdir / ("%s_main0.jpg" % sid)
        rec = {"source_id": sid, "pic": v["pic"], "title": v["title"]}
        if not dl(v["pic"], p):
            rec.update(grade="", fail="download")
            fails += 1
        else:
            try:
                g = grade_one(p)
                rec.update(g)
                rec["fail"] = ""
            except Exception as e:
                rec.update(grade="", fail="ocr:%s" % str(e)[:60])
                fails += 1
        res[sid] = rec
        if i % 10 == 0 or i == len(todo):
            write_out(args.out, res)
            print("[%d/%d] sid=%s grade=%s cjk=%d text=%.1f%% edge=%.2f" % (
                i, len(todo), sid, rec.get("grade"), rec.get("cjk", 0),
                rec.get("text_pct", 0), rec.get("edge", 0)), flush=True)
        time.sleep(0.2)
    cp = write_out(args.out, res)
    dist = {}
    for r in res.values():
        dist[r.get("grade") or "FAIL"] = dist.get(r.get("grade") or "FAIL", 0) + 1
    print("[done] 分布:", dist, "→", cp)
    return 2 if fails else 0


def cmd_urls(args):
    res = load_existing(args.out)
    pairs = []
    for line in open(args.file, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        sid, url = (parts[0], parts[1]) if len(parts) > 1 else (str(len(pairs) + 1), parts[0])
        pairs.append((sid, url))
    imgdir = Path(args.out) / "grade_imgs"
    imgdir.mkdir(parents=True, exist_ok=True)
    fails = 0
    for i, (sid, url) in enumerate(pairs, 1):
        p = imgdir / ("%s_u.jpg" % sid)
        rec = {"source_id": sid, "pic": url}
        if not dl(url, p):
            rec.update(grade="", fail="download")
            fails += 1
        else:
            try:
                rec.update(grade_one(p))
                rec["fail"] = ""
            except Exception as e:
                rec.update(grade="", fail="ocr:%s" % str(e)[:60])
                fails += 1
        res[sid] = rec
        print("[%d/%d] %s grade=%s" % (i, len(pairs), sid, rec.get("grade")), flush=True)
        time.sleep(0.2)
    write_out(args.out, res)
    # URL 索引账本（select_gate 按 imageUrl 查）
    url_idx = {}
    for r in res.values():
        if r.get("pic") and r.get("grade"):
            url_idx[r["pic"]] = {"grade": r["grade"], "cjk": r.get("cjk", 0)}
    up = Path(args.out) / "img_grade_url.json"
    if up.exists():
        try:
            url_idx = {**json.load(open(up, encoding="utf-8")), **url_idx}
        except Exception:
            pass
    json.dump(url_idx, open(up, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("[done] URL 账本 →", up, len(url_idx), "条")
    return 2 if fails else 0


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    m = sp.add_parser("main")
    m.add_argument("--items-json", default=str(ROOT / "erp_data" / "published_items_full.json"))
    m.add_argument("--ids")
    m.add_argument("--out", default=str(ROOT / "erp_data"))
    m.add_argument("--redo", action="store_true")
    m.set_defaults(fn=cmd_main)
    u = sp.add_parser("urls")
    u.add_argument("--file", required=True)
    u.add_argument("--out", default=str(ROOT / "erp_data"))
    u.set_defaults(fn=cmd_urls)
    args = ap.parse_args()
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
