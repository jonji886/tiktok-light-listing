#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""assemble_guard.py — 参数装配前置校验（P1 三件套）。

把 batch13 踩过的三个「发布后才知道」的坑前移到装配阶段：
  1. SKU 规格值 > 50 字符 → 平台截断导致 Warna 属性重复 → [fail]。
     batch13 的 734033984225 因此整品重发一次（~10 分钟）。
  2. markup 默认算出破价格带 → dry-run 才知道，逐品试错。
     这里用定价公式**反解**可行 markup 区间，一次给出。
  3. 重复 seller_sku → dict 互相覆盖成 1 条，必须走 sku_map_seq + sku_code_seq。
     prep_one.py 曾漏写 sku_code_seq.json（已修），这里再校验一次存在性。

用法：
  # 检查一个品（detail 文件 + 词表 + 参数）
  python3 scripts/assemble_guard.py --detail <ttl_detail.json> \
      --sku-map <sku_map.json> --site MY --weight-g 300 --dims 25x20x4
  # 反解 markup（给定最低/最高采购价）
  python3 scripts/assemble_guard.py --suggest-markup --site MY \
      --src-min 2.78 --src-max 25.4 --weight-g 300 --dims 25x20x4
  # 批量检查已装配的品
  python3 scripts/assemble_guard.py --batch erp_data/_run/20261125_p10/edit/args_*.json
"""
import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "config"))
import pricing_calc as PC  # noqa: E402

MAX_SKU_VALUE = 40          # 平台截断阈值 50，留 10 字符安全边距
CJK = re.compile(r"[\u4e00-\u9fff]")


def _load_yaml(site):
    """用 pricing_calc 自带的纯标准库 yaml 解析器（环境无 pyyaml）。"""
    d = PC.load_config()
    sc = PC.merged(d, site)       # defaults + 站点覆盖
    sc["site"] = site             # compute() 需要 site 键
    return sc


def suggest_markup(cfg, src_min, src_max, weight_g, dims, scheme="shelf"):
    """反解 markup 区间：使最低价进下限、最高价不破上限。返回 (lo, hi, current, ok)。"""
    def price_at(m, src):
        r = PC.compute(cfg, src, scheme=scheme, pieces=1, weight_g=weight_g, dims=dims, margin=None)
        # compute 用 cfg.schemes[scheme].markup；这里临时替换得到目标 markup 的价格
        return r
    base_markup = cfg["schemes"][scheme]["markup"]
    # price_local 与 markup 成正比（其余项在 landed 里固定）
    def local(m, src):
        r = PC.compute(cfg, src, scheme=scheme, pieces=1, weight_g=weight_g, dims=dims)
        return r["price_local"] / base_markup * m

    band = cfg["price_band_usd"]
    # 用 order_usd 同比例（usd = local / rate_to_local / ... 线性）
    def usd(m, src):
        r = PC.compute(cfg, src, scheme=scheme, pieces=1, weight_g=weight_g, dims=dims)
        return r["order_usd"] / base_markup * m

    lo = band["min"] / usd(base_markup, src_min) * base_markup if usd(base_markup, src_min) > 0 else base_markup
    hi = band["max"] / usd(base_markup, src_max) * base_markup if usd(base_markup, src_max) > 0 else base_markup
    lo = round(max(1.01, lo), 3)
    hi = round(hi, 3)
    return lo, hi, base_markup, lo <= base_markup <= hi


def check_detail(detail, sku_map, site, weight_g, dims, markup=None):
    """返回 (problems, suggestions)。markup 覆盖定价表默认值（装配时实际用的值）。"""
    problems, sug = [], []
    d = json.load(open(detail, encoding="utf-8"))
    r = d["reqInfo"]["tikTokItemModel"]["productCreateRequestV2"]
    skus = r.get("skus") or []
    smap = json.load(open(sku_map, encoding="utf-8")) if sku_map and os.path.exists(sku_map) else {}
    # 有 sku_map_seq（按 order 对齐）优先用它：重复 seller_sku 时 dict 会塌成 1 条
    seq_dir0 = os.path.dirname(sku_map) if sku_map else ""
    seqp = os.path.join(seq_dir0, "sku_map_seq.json") if seq_dir0 else ""
    smap_seq = json.load(open(seqp, encoding="utf-8")) if seqp and os.path.exists(seqp) else None

    # ① SKU 值长度 + 唯一性
    # ⚠ 必须校验**映射后**的值：真正发给平台的是 sku_map 里的短值，
    #   拿 detail 原文长度判会误杀已用词表压缩过的品（2026-11-25 本批实测）。
    vals, raw_long = [], []
    by_dim = {}
    for idx, s in enumerate(skus):
        ssku = str(s.get("seller_sku") or "")
        mapped = None
        if smap_seq and idx < len(smap_seq):
            mapped = smap_seq[idx]
        elif ssku in smap:
            mapped = smap[ssku]
        for j, at in enumerate(s.get("sales_attributes") or []):
            raw = at.get("value_name") or ""
            v = raw
            if isinstance(mapped, list) and j < len(mapped) and mapped[j]:
                v = str(mapped[j])
            vals.append(v)
            by_dim.setdefault(j, []).append(v)
            if len(raw) > MAX_SKU_VALUE and len(v) <= MAX_SKU_VALUE:
                raw_long.append(raw)
    longs = [v for v in set(vals) if len(v) > MAX_SKU_VALUE]
    if longs:
        problems.append("SKU 值超 %d 字符 %d 个（平台截断→属性重复 fail）: %s"
                        % (MAX_SKU_VALUE, len(longs), longs[0][:60]))
    cjk = [v for v in set(vals) if CJK.search(v)]
    if cjk:
        problems.append("SKU 值含中文 %d 个（发布必拒 [12052250]）: %s" % (len(cjk), cjk[0][:40]))
    # 同维度内映射后撞车 → 平台判属性重复（不同维度重复是正常的，如统一尺码）
    from collections import Counter as _C
    for j, vs in by_dim.items():
        dupv = [k for k, n in _C(vs).items() if n > 1]
        if dupv:
            problems.append("映射后第 %d 维 SKU 值重复「%s」×%d（平台判属性重复）"
                            % (j + 1, dupv[0][:40], _C(vs)[dupv[0]]))
            break

    # ② 重复 seller_sku
    codes = [str(s.get("seller_sku") or "") for s in skus]
    from collections import Counter
    cnt = Counter(codes)
    dup = {k: v for k, v in cnt.items() if v > 1}
    cjk_code = [c for c in codes if CJK.search(c)]
    if dup:
        problems.append("重复 seller_sku %d 组（%s）→ 必须 --sku-map-seq/--sku-code-seq"
                        % (len(dup), list(dup.items())[:3]))
        seq_dir = os.path.dirname(sku_map) if sku_map else ""
        for f in ("sku_code_seq.json", "sku_map_seq.json"):
            if seq_dir and not os.path.exists(os.path.join(seq_dir, f)):
                problems.append("  缺少 %s（prep_one 曾漏写，现已修）" % f)
    if cjk_code:
        problems.append("seller_sku 含中文 %d 个 → 必须 --sku-code 映射" % len(cjk_code))

    # ③ markup 反解（用有货 SKU 的 source_price）
    srcs = []
    for s in skus:
        inv = sum(int(i.get("quantity") or 0) for i in (s.get("inventory") or []))
        if inv <= 0:
            continue
        src = (s.get("price") or {}).get("source_price")
        if src:
            try:
                srcs.append(float(src))
            except (TypeError, ValueError):
                pass
    if srcs:
        cfg = _load_yaml(site)
        lo, hi, cur, ok = suggest_markup(cfg, min(srcs), max(srcs), weight_g, dims)
        if markup:
            try:
                cur = float(markup)
                ok = lo <= cur <= hi
            except ValueError:
                pass
        if not ok:
            problems.append("markup %.3f 出带：采购 ¥%.2f-%.2f 的可行区间 [%.3f, %.3f]"
                            % (cur, min(srcs), max(srcs), lo, hi))
        sug.append(dict(src_min=round(min(srcs), 2), src_max=round(max(srcs), 2),
                        markup_lo=lo, markup_hi=hi, markup_current=cur, in_band=ok))
    return problems, sug


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail")
    ap.add_argument("--sku-map")
    ap.add_argument("--site", default="MY")
    ap.add_argument("--weight-g", type=float, default=200)
    ap.add_argument("--dims", default="25x20x4")
    ap.add_argument("--suggest-markup", action="store_true")
    ap.add_argument("--src-min", type=float)
    ap.add_argument("--src-max", type=float)
    ap.add_argument("--markup", type=float, default=None, help="装配时实际 markup（覆盖 yaml 默认值）")
    ap.add_argument("--batch", nargs="*")
    args = ap.parse_args()

    if args.suggest_markup:
        if args.src_min is None or args.src_max is None:
            sys.exit("[guard] --suggest-markup 需 --src-min/--src-max")
        cfg = _load_yaml(args.site)
        lo, hi, cur, ok = suggest_markup(cfg, args.src_min, args.src_max,
                                         args.weight_g, tuple(float(x) for x in args.dims.split("x")))
        # recommend 取「刚好进带的最小 markup」+ 2% 边距，而不是区间中点：
        # 中点会把价格顶到带宽中位，丧失价格竞争力（batch13 实测低价品应贴下限定价）。
        rec = round(lo * 1.02, 3) if lo <= hi else None
        print(json.dumps(dict(site=args.site, src_min=args.src_min, src_max=args.src_max,
                              markup_lo=lo, markup_hi=hi, current=cur,
                              in_band=ok, recommend=rec),
                         ensure_ascii=False, indent=1))
        return 0 if lo <= hi else 1

    if args.detail:
        problems, sug = check_detail(args.detail, args.sku_map, args.site,
                                     args.weight_g, tuple(float(x) for x in args.dims.split("x")),
                                     markup=args.markup)
        print("[guard] %s: %d 个问题" % (Path(args.detail).name, len(problems)))
        for p in problems:
            print("  ✗", p)
        if sug:
            print("  定价建议:", json.dumps(sug, ensure_ascii=False))
        return 1 if problems else 0

    if args.batch:
        bad = 0
        for f in args.batch:
            a = json.load(open(f, encoding="utf-8"))
            oid = Path(f).stem.replace("args_", "")
            df = glob.glob("erp_data/_run/pipeline/%s/detail.json/*" % oid)
            if not df:
                print("  ? %s 无 detail" % oid)
                continue
            smap = a.get("sku_map") or a.get("sku_map_seq")
            problems, sug = check_detail(df[0], smap, args.site,
                                         float(a.get("weight_g") or 200),
                                         tuple(float(x) for x in (a.get("dims") or "25x20x4").split("x")))
            if problems:
                bad += 1
                print("  ✗ %s: %s" % (oid, problems[0][:90]))
        print("[guard] 批量检查 %d 品，%d 品有问题" % (len(args.batch), bad))
        return 1 if bad else 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
