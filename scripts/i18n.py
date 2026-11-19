#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""i18n.py — 商品文案 / 规格名的译名表（唯一来源 config/i18n/<site>.csv）

为什么需要（2026-11-13 架构审查）：越南语标题、≥500 字描述、70 条规格译名原先
全部是 build_listing_payloads.py 里的 dict 字面量 —— 加一个新商品就要改代码、
就要人工重写一段描述；写漏直接整批 ValueError。译名本质是**数据**，不是代码。

设计纪律（与 image_gate / select_gate 一致）：
  - **只查表，不猜**。查不到返回 None，由调用方转人工或阻断；
    绝不用占位、序号模板、机器直译补位（IndexError→套色模板 就是这么来的）。
  - **表是纯文本**：加新品 = 往 CSV 加一行，不改代码，可 code review、可 diff。
  - 同一进程内只读一次；命中结果进内存缓存（不做磁盘缓存：查表是本地 IO，
    没有网络成本，引入缓存文件只会带来"缓存与表不一致"的新故障面）。

CSV 列（config/i18n/<site>.csv）：
  kind   title / desc / spec / attr
  key    kind=title|desc 时为 offer_id；kind=spec|attr 时留空
  src    原文，用于留痕与核对（title 放中文标题，spec/attr 放中文规格串，desc 留空）
  tgt    译文

查询键：
  title / desc  → 按 (kind, offer_id)：标题与描述是逐品创作，复用无意义
  spec / attr   → 按 (kind, 归一化原文)：同一规格串跨商品复用，改了原文自然失配

用法：
  python3 scripts/i18n.py --site VN --list spec          # 列出某类译名
  python3 scripts/i18n.py --site VN --get spec "深灰大条纹>30*40*60"
  python3 scripts/i18n.py --site VN --missing-check      # 反查候选清单缺哪些译名
  python3 scripts/i18n.py --site TH --market-list        # 列市场用词（竞品当地语原词）
  python3 scripts/i18n.py --site TH --market-lookup สี   # 查一个当地语词（只参考，非译文）

退出码：0 命中 / 1 未命中 / 2 参数或文件问题
"""
import argparse
import csv
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
I18N_DIR = os.path.join(ROOT, "config", "i18n")

KINDS = ("title", "desc", "spec", "attr")
_COLS = ("kind", "key", "src", "tgt")
_CACHE = {}          # site -> {(kind, key): tgt} / {(kind, src): tgt}

# 市场用词参考表（2026-11-18 新增，EchoTik 竞品当地语规格名）
# **与主译名表物理隔离**：主表是 中文src→当地语tgt（可自动用于 listing），
# 市场表是 当地语原词（THIRD_PARTY_ONLY，中文提示多为 INFERRED），只能人工参考。
# 二者混用会把竞品原文当译文塞进 src 列 = 造假，故分文件 + 独立查询入口。
MARKET_COLS = ("site", "kind", "local_term", "prop_name", "cn_hint",
               "evidence", "product_id", "source")
_ACCEPTED_EVIDENCE = {"VERIFIED_BY_TABLE"}   # 只有主表反证的中文提示可当"可信"

# 站点码 ≠ 语言码：VN 站的商品语言是越南语(vi)，TH 站是泰语(th)。
# 文件按语言码命名，站点到语言的映射只此一处，避免 vn.csv/vi.csv 这种二义。
LANGS = {"TH": "th", "VN": "vi"}


def normalize(s):
    """规整原文：去首尾空白 + 压缩内部空白。只统一空白，不做任何字符替换。"""
    return re.sub(r"\s+", " ", str(s or "")).strip()


def lang_of(site):
    site = str(site or "").strip().upper()
    return LANGS.get(site, site.lower())


def table_path(site):
    return os.path.join(I18N_DIR, "%s.csv" % lang_of(site))


def _load(site):
    """返回 {(kind,key):tgt} 与 {(kind,src):tgt} 两张索引；文件缺失返回空表。"""
    site = str(site or "").upper()
    if site in _CACHE:
        return _CACHE[site]
    by_key, by_src = {}, {}
    path = table_path(site)
    if os.path.exists(path):
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                kind = (row.get("kind") or "").strip().lower()
                if kind not in KINDS:
                    continue
                tgt = row.get("tgt") or ""
                if not tgt.strip():
                    continue
                key = (row.get("key") or "").strip()
                src = normalize(row.get("src"))
                if key:
                    by_key.setdefault((kind, key), tgt)
                if src:
                    by_src.setdefault((kind, src), tgt)
    _CACHE[site] = (by_key, by_src)
    return _CACHE[site]


def clear_cache():
    _CACHE.clear()


def get(site, kind, key=None, src=None):
    """查译名。命中返回字符串，未命中返回 None（**不抛异常、不猜**）。

    title/desc 优先按 key 查，spec/attr 按 src 查；两类都允许回落到另一种键，
    这样同一份表既能按 offer_id 记文案，也能按原文记词条。
    """
    kind = str(kind or "").strip().lower()
    if kind not in KINDS:
        raise ValueError("未知 kind=%r，只能是 %s" % (kind, list(KINDS)))
    by_key, by_src = _load(site)
    if key is not None and str(key).strip():
        hit = by_key.get((kind, str(key).strip()))
        if hit is not None:
            return hit
    nsrc = normalize(src)
    if nsrc:
        return by_src.get((kind, nsrc))
    return None


def require(site, kind, key=None, src=None):
    """get() 的严格版：未命中抛 KeyError（调用方决定怎么报错）。"""
    hit = get(site, kind, key=key, src=src)
    if hit is None:
        raise KeyError("缺译名 site=%s kind=%s key=%r src=%r（补 %s）"
                       % (site, kind, key, (src or "")[:60], table_path(site)))
    return hit


def missing(site, needs):
    """批量缺项检查。needs = [(kind, key, src), ...] → 返回未命中的子列表。"""
    return [n for n in needs if get(site, n[0], key=n[1], src=n[2]) is None]


def rows(site, kind=None):
    """原样列出表内行（供 CLI / 测试）。"""
    path = table_path(site)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if kind and (row.get("kind") or "").strip().lower() != kind:
                continue
            out.append({c: (row.get(c) or "") for c in _COLS})
    return out


def _market_path(site):
    return os.path.join(I18N_DIR, "_market_terms_%s.csv" % lang_of(site))


def market_rows(site, kind=None, verified_only=False):
    """读市场用词参考表（EchoTik 当地语原词）。

    verified_only=True 时只返回中文提示已被主译名表反证的条目；
    默认返回全部但 **cn_hint 为空的就是没证据，调用方不得当译文使用**。
    """
    path = _market_path(site)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            k = (row.get("kind") or "").strip().lower()
            if kind and k != kind:
                continue
            if verified_only and (row.get("evidence") or "").strip() not in _ACCEPTED_EVIDENCE:
                continue
            out.append({c: (row.get(c) or "") for c in MARKET_COLS})
    return out


def market_lookup(site, local_term):
    """按当地语原词查市场表，命中返回整行 dict，未命中 None（不猜）。"""
    term = normalize(local_term)
    for r in market_rows(site):
        if normalize(r["local_term"]) == term:
            return r
    return None


def main():
    ap = argparse.ArgumentParser(description="译名表查询（只读）")
    ap.add_argument("--site", required=True, help="TH / VN")
    ap.add_argument("--list", dest="list_kind", choices=list(KINDS), help="列出某类译名")
    ap.add_argument("--get", dest="get_kind", choices=list(KINDS), help="按原文查一条")
    ap.add_argument("--key", default="", help="配合 --get：按 offer_id 查 title/desc")
    ap.add_argument("--missing-check", action="store_true",
                    help="按候选清单的 offer_id 反查缺哪些 title/desc")
    ap.add_argument("--csv", default="", help="--missing-check 用的候选清单（默认 selection_candidates）")
    ap.add_argument("--market-list", action="store_true",
                    help="列市场用词参考表（竞品当地语原词，非译文）")
    ap.add_argument("--market-lookup", default="", metavar="TERM",
                    help="按当地语原词查市场用词表")
    ap.add_argument("--verified-only", action="store_true",
                    help="配合 --market-list：只列中文提示被主表反证的条目")
    a = ap.parse_args()

    if not os.path.exists(table_path(a.site)):
        sys.stderr.write("[i18n] 译名表不存在: %s\n" % table_path(a.site))
        return 2

    if a.market_list or a.market_lookup:
        if a.market_lookup:
            hit = market_lookup(a.site, a.market_lookup)
            if not hit:
                print("[i18n] 市场表未命中：%s / %r" % (a.site.upper(), a.market_lookup))
                return 1
            print("local_term : %s\nprop_name  : %s\ncn_hint    : %s\nevidence   : %s\nproduct_id : %s"
                  % (hit["local_term"], hit["prop_name"], hit["cn_hint"] or "(空=无证据)",
                     hit["evidence"], hit["product_id"]))
            return 0
        rs = market_rows(a.site, verified_only=a.verified_only)
        print("[i18n] %s 市场用词参考表：%d 条（THIRD_PARTY_ONLY，cn_hint 为空=无中文证据）"
              % (a.site.upper(), len(rs)))
        for r in rs:
            print("  %-22s prop=%-12s cn=%-8s [%s]"
                  % (r["local_term"][:22], r["prop_name"][:12],
                     (r["cn_hint"] or "-")[:8], r["evidence"]))
        return 0

    if a.list_kind:
        rs = rows(a.site, a.list_kind)
        print("[i18n] %s / %s：%d 条" % (a.site.upper(), a.list_kind, len(rs)))
        for r in rs:
            label = r["key"] or r["src"]
            print("  %-28s → %s" % (label[:28], r["tgt"][:70].replace("\n", "⏎")))
        return 0

    if a.get_kind:
        hit = get(a.site, a.get_kind, key=a.key or None, src=a.get_kind in ("spec", "attr") and a.key or None)
        if a.get_kind in ("spec", "attr") and not a.key:
            sys.stderr.write("[i18n] --get spec|attr 需把原文放在 --key 里\n")
            return 2
        if hit is None:
            print("[i18n] 未命中：%s / %s = %r" % (a.site.upper(), a.get_kind, a.key))
            return 1
        print(hit)
        return 0

    if a.missing_check:
        cand = a.csv or os.path.join(ROOT, "erp_data", "selection_candidates.csv")
        if not os.path.exists(cand):
            sys.stderr.write("[i18n] 找不到候选清单 %s\n" % cand)
            return 2
        needs = []
        with open(cand, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                oid = (row.get("offer_id") or "").strip()
                if not oid:
                    continue
                needs.append(("title", oid, None))
                needs.append(("desc", oid, None))
        gone = missing(a.site, needs)
        print("[i18n] %s：检查 %d 条，缺 %d 条" % (a.site.upper(), len(needs), len(gone)))
        for kind, key, _ in gone:
            print("  ✗ %s %s" % (kind, key))
        return 1 if gone else 0

    ap.error("需要 --list / --get / --missing-check 之一")


if __name__ == "__main__":
    sys.exit(main())
