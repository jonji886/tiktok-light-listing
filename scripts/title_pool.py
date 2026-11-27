#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""title_pool.py — 标题模板池（A6，2026-11-25）

背景：本批 5 个马来语标题全部现写，逐字想很慢。从已成功 ACTIVATE 的 15 品
      沉淀「品类词根 + 特征 + 场景」模板，给关键词即出候选，人工只做选择/微调。

模板只含已验证标题里出现过的词——不机翻、不编造新词。

用法：
  python3 scripts/title_pool.py --category apron --features "tali,arnab,bunga" [--wrap women]
  python3 scripts/title_pool.py --list              # 列出品类与可用特征词
"""
import argparse
import itertools
import json
import re
import sys

# 从 15 个已 ACTIVATE 标题沉淀；每个词根都来自真实标题
POOL = {
    "apron": {
        "root": ["Apron Kanvas", "Apron Kanvas Tali", "Apron Kanvas Lengan Panjang"],
        "feature": {
            "comel": "Comel", "arnab": "Tali Arnab", "bunga": "Bunga",
            "matahari": "Bunga Matahari", "grid": "Grid", "puteri": "Puteri",
            "sulaman": "Sulaman", "kalisminyak": "Kalis Minyak",
            "tahanminyak": "Tahan Minyak", "kalisair": "Kalis Air",
            "bernafas": "Bernafas", "zip": "Poket Zip", "poket": "Poket",
            "renda": "Poket Renda", "tuala": "Poket Tuala",
        },
        "scene": ["untuk Dapur Rumah Wanita", "untuk Dapur Kafe Rumah",
                  "untuk Dapur Katering", "Memasak Serba Guna"],
    },
    "laundry_bag": {
        "root": ["Beg Basuh Jaring Halus", "Beg Basuh Pakaian Jaring Halus"],
        "feature": {
            "bra": "Bra", "ritsleting": "Ritsleting Dua Baris",
            "stokin": "Stokin", "berasingan": "Pembalut Berasingan",
            "warna": "Biru Kelabu",
        },
        "scene": ["untuk Mesin Basuh Rumah", "untuk Pakaian Dalam Mesin Basuh"],
    },
    "hook": {
        "root": ["Cangkuk Dinding"],
        "feature": {
            "keluli": "Keluli Tahan Karat", "tanpapaku": "Tanpa Paku",
            "pelekat": "Pelekat Kuat",
        },
        "scene": ["untuk Dapur Bilik Mandi"],
    },
}


def build(category, features, wrap):
    p = POOL.get(category)
    if not p:
        sys.exit("[pool] 未知品类 %s，可选：%s" % (category, ", ".join(POOL)))
    feats = []
    for f in features:
        if f not in p["feature"]:
            print("[pool] ⚠ 特征「%s」不在词表，跳过（--list 查看可用词）" % f, file=sys.stderr)
        else:
            feats.append(p["feature"][f])
    # 词根 + 特征（最多3个，避免标题过长） + 场景，笛卡尔出候选
    out, seen = [], set()
    for root, n in itertools.product(p["root"], range(1, min(3, len(feats)) + 1)):
        for combo in itertools.combinations(feats, n):
            for sc in p["scene"][:2]:
                sc2 = sc
                if wrap and wrap.lower() in ("wanita", "women"):
                    # 场景里已含 Wanita（untuk Dapur Rumah Wanita）→ 去掉避免重复
                    sc2 = re.sub(r"\s+Wanita$", "", sc)
                parts = [root] + list(combo) + ([wrap] if wrap else []) + [sc2]
                title = " ".join(dict.fromkeys(parts))   # 去重保序
                title = re.sub(r"\s+", " ", title).strip()
                if 25 <= len(title) <= 90 and title not in seen:
                    seen.add(title)
                    out.append(title)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--category", default="apron")
    ap.add_argument("--features", default="", help="逗号分隔的特征词 key")
    ap.add_argument("--wrap", default="", help="追加人群词，如 women / lelaki wanita")
    ap.add_argument("--limit", type=int, default=8)
    a = ap.parse_args()

    if a.list:
        for cat, p in POOL.items():
            print("%s: 词根%s 特征%s 场景%s" %
                  (cat, p["root"], list(p["feature"]), p["scene"]))
        return 0
    feats = [x.strip() for x in a.features.split(",") if x.strip()]
    titles = build(a.category, feats, a.wrap.strip())
    print(json.dumps(dict(category=a.category, count=len(titles),
                          titles=titles[:a.limit]), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
