#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""judge_cache.py — 图片判定共享缓存（2026-11-26 A/B 提效）。

背景（实测 VERIFIED）：
  同一批图在流水线里被判 2–3 遍：
    · prefilter（选品期）本地 Vision OCR 判首图
    · precheck C 段 auto_verdict 本地 Vision OCR 判全册（17–27 张 / 21–25s）
    · driver Phase3 image_review Aidge probe 再判全册（¥0.02/张）
  三处判的是同一批 URL / 同一份字节，却互不复用。

设计（第一性原理）：
  判定的本质是「这张图的字节里有没有中文/水印」——这是**图片内容**的属性，
  与它在哪一步被判、id 是 main_01 还是 desc_07 无关。故缓存 key = content_sha256
  （与 image_gate.scan 已算好的字段一致），URL 兜底（下载失败无 sha 时）。

安全约束（不可破）：
  1. 678 硬闸：main 通道 L3_keep 必须有 evidence.rec_text 且无 CJK。
     本地 Vision OCR 的 rec_text 已被 image_review._local_evidence_batch 接受为合法证据，
     复用时保留 evidence_source 标记供人工追溯。
  2. Aidge probe 独有能力：非文字水印（noobj_wm）。本地 OCR 检不到 →
     复用规则里 main+L3_keep **仍跑 Aidge**，其余通道复用（详见 image_review）。
  3. 过期：detail 被 ttl_edit 改写后图集可能变化 → 缓存条带 source_detail_sha，
     merge 时按当前 review 的 images 集合比对，只回填当前仍存在的 sha。

磁盘格式 erp_data/judge_cache.json:
  { "<sha256>": {verdict, seen_text, note, evidence, grade, cjk,
                  source, source_detail, ts} }

用法（脚本内）：
  import judge_cache as JC
  JC.save_from_review(review_path, detail_path=None)   # auto_verdict / prefilter 判完调用
  JC.merge_into_review(review_path, detail_path=None)  # image_gate.scan 后调用
CLI:
  python3 judge_cache.py stats                          # 看缓存规模/分布
  python3 judge_cache.py clear --older-than 7           # 清 7 天前条目
"""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "erp_data" / "judge_cache.json"
VALID = {"L0_drop", "L1_inpaint", "L2_localize", "L3_keep", "pending"}


def _load():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(d):
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(CACHE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CACHE)


def key_of(im):
    """缓存 key：content_sha256 优先，URL 兜底（下载失败无 sha）。"""
    sha = (im.get("content_sha256") or "").strip()
    if sha and len(sha) >= 16:
        return "sha:" + sha
    url = (im.get("url") or "").strip()
    return "url:" + hashlib.sha1(url.encode()).hexdigest() if url else ""


def _detail_sha(detail_path):
    if not detail_path or not os.path.exists(detail_path):
        return ""
    return hashlib.sha256(Path(detail_path).read_bytes()).hexdigest()[:16]


def save_from_review(review_path, detail_path=None, source="auto_verdict"):
    """把 review.json 里已判的图写入缓存。只写有效 verdict，不覆盖更新的同 key。

    返回新增/更新条数。
    """
    try:
        with open(review_path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return 0
    cache = _load()
    dsha = _detail_sha(detail_path)
    n = 0
    for im in d.get("images") or []:
        v = im.get("verdict")
        if v not in VALID or v == "pending":
            continue
        k = key_of(im)
        if not k:
            continue
        ev = im.get("evidence")
        rec = {
            "verdict": v,
            "seen_text": im.get("seen_text") or "",
            "note": im.get("note") or "",
            "evidence": ev if isinstance(ev, dict) else {},
            "grade": im.get("grade") or "",
            "cjk": im.get("cjk") or 0,
            "source": ev.get("evidence_source") if isinstance(ev, dict) else source,
            "source_detail": dsha,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        # 已有同 key：Aidge 证据（source=aidge）优先于本地 OCR，不被降级覆盖
        old = cache.get(k) or {}
        old_src = old.get("source") or ""
        if old_src == "aidge" and rec["source"] != "aidge":
            continue
        if old.get("verdict") == v and old.get("seen_text") == rec["seen_text"] \
                and old.get("evidence") == rec["evidence"]:
            continue
        cache[k] = rec
        n += 1
    if n:
        _save(cache)
    return n


def merge_into_review(review_path, detail_path=None, only=None):
    """scan 重建 review.json 后调用：按 key 回填缓存里的判定。

    only: 可选 set，限定只回填这些 id（如只回填 main 通道），None=全部。
    返回 {回填数, 跳过数, 总数}。绝不覆盖已有非 pending 判定（尊重本轮新判）。
    """
    try:
        with open(review_path, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {"merged": 0, "skipped": 0, "total": 0}
    cache = _load()
    if not cache:
        return {"merged": 0, "skipped": 0, "total": 0}
    merged = skipped = total = 0
    for im in d.get("images") or []:
        total += 1
        if im.get("verdict") not in (None, "", "pending"):
            skipped += 1
            continue
        if only is not None and im.get("id") not in only:
            skipped += 1
            continue
        k = key_of(im)
        rec = cache.get(k) if k else None
        if not rec or rec.get("verdict") not in VALID or rec["verdict"] == "pending":
            skipped += 1
            continue
        for f in ("verdict", "seen_text", "note", "evidence", "grade", "cjk"):
            if f in rec:
                im[f] = rec[f]
        im["cache_hit"] = rec.get("source") or "cache"
        merged += 1
    if merged:
        tmp = review_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, review_path)
    return {"merged": merged, "skipped": skipped, "total": total}


def stats():
    c = _load()
    from collections import Counter
    v = Counter(x.get("verdict") for x in c.values())
    s = Counter(x.get("source") for x in c.values())
    print("缓存条目:", len(c))
    print("判定分布:", dict(v))
    print("来源分布:", dict(s))
    return c


# ── OCR 原始结果缓存（B 三判合一：prefilter 判过的首图，C 段不再重复跑 OCR）──
OCR_CACHE = ROOT / "erp_data" / "ocr_cache.json"


def _load_ocr():
    try:
        with open(OCR_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_ocr(path, ocr_dict, sha=""):
    """存一份原始 OCR 结果（items/width/height），key 用内容 sha 或路径 sha。

    prefilter 判首图、auto_verdict 判全册都调它；后者命中即跳过 swift OCR（单张 ~1s）。
    """
    if not ocr_dict or not ocr_dict.get("ok", True):
        return False
    key = ""
    if sha:
        key = "sha:" + sha
    elif path:
        try:
            key = "sha:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except Exception:
            key = "path:" + hashlib.sha1(str(path).encode()).hexdigest()
    if not key:
        return False
    c = _load_ocr()
    if key in c:
        return False
    c[key] = {"d": ocr_dict, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    OCR_CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(OCR_CACHE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False)
    os.replace(tmp, OCR_CACHE)
    return True


def get_ocr(path, sha=""):
    """取缓存的 OCR 结果；未命中返回 None。path 与 sha 都试（兼容 prefilter/C段不同路径）。"""
    c = _load_ocr()
    keys = []
    if sha:
        keys.append("sha:" + sha)
    if path:
        try:
            keys.append("sha:" + hashlib.sha256(Path(path).read_bytes()).hexdigest())
        except Exception:
            keys.append("path:" + hashlib.sha1(str(path).encode()).hexdigest())
    for k in keys:
        if k in c:
            return c[k].get("d")
    return None


def ocr_stats():
    c = _load_ocr()
    print("OCR 缓存条目:", len(c))
    return len(c)


def clear_older_than(days):
    c = _load()
    cutoff = time.time() - days * 86400
    keep = {}
    for k, v in c.items():
        try:
            t = time.mktime(time.strptime(v["ts"], "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            t = 0
        if t >= cutoff:
            keep[k] = v
    n = len(c) - len(keep)
    if n:
        _save(keep)
    return n


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("stats")
    sp.add_parser("ocr-stats")
    cl = sp.add_parser("clear")
    cl.add_argument("--older-than", type=int, default=7)
    a = ap.parse_args()
    if a.cmd == "stats":
        stats()
    elif a.cmd == "ocr-stats":
        ocr_stats()
    elif a.cmd == "clear":
        print("清除判定缓存", clear_older_than(a.older_than), "条")
        n = 0
        try:
            c = _load_ocr()
            n = len(c)
            if n:
                os.replace(OCR_CACHE, str(OCR_CACHE) + ".bak")
        except Exception:
            pass
        print("清除 OCR 缓存", n, "条（已备份 .bak）")


if __name__ == "__main__":
    main()
