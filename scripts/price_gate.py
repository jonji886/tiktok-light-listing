#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布前价格/库存闸门（读 ttl-list 落盘 CSV，逐条判能不能发布）

为什么需要：ERP 认领时会用内置线性公式自动铺价（实测 THB = 采购价¥ × 7.574 + 25.24），
该公式不区分重量与类目费率，低价品必破价格带 → 实测采集箱里 26.48~55.67 THB（$0.79~1.65）
的商品全部为亏损价，且已有一个 29.13 THB 的牙刷架在售，每单净亏 ¥10+。
SKILL 旧版只有"必须用 set_price 重算"的文字规则，没有任何强制闸门 —— 本脚本补上。

用法：
  # 先拉列表（在 skill 根目录跑，落盘 erp_data/tiktok_list.csv）
  python3 scripts/erp_client.py ttl-list --region TH

  # 判全部（不区分站点，按每行的"站点"列取对应币种与价格带）
  python3 scripts/price_gate.py

  # 只看某个站 / 只看已发布 / 只看未发布
  python3 scripts/price_gate.py --site VN
  python3 scripts/price_gate.py --status 1
  python3 scripts/price_gate.py --status 0

  # 给某采购价，反推该挂多少本币（含价格带上下限）
  python3 scripts/price_gate.py --hint --cost 5 --weight 150 --site VN --pieces 1

退出码：0 全部可发布；1 存在阻断项（禁止 ttl-publish）
"""
import argparse
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from pricing_calc import load_config, merged, compute, max_cost, bundle_violation  # noqa: E402

ROOT = os.path.dirname(HERE)
DEFAULT_CSV = os.path.join(ROOT, "erp_data", "tiktok_list.csv")

# 平台硬上限：SKU inventory 超过 999999 发布必败（错误码 12052055）
STOCK_HARD_MAX = 999999
# 平台要求的最小可售库存
STOCK_MIN = 5


def parse_price_range(s):
    """'129.00~199.00' / '~' / '' -> (low, high) or (None, None)"""
    if not s:
        return None, None
    s = str(s).replace(",", "").strip()
    if "~" not in s:
        try:
            v = float(s)
            return v, v
        except ValueError:
            return None, None
    a, _, b = s.partition("~")
    try:
        low = float(a)
    except ValueError:
        low = None
    try:
        high = float(b)
    except ValueError:
        high = None
    return low, high


def fnum(v, default=0.0):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def judge(row, cfg_all):
    site = (row.get("站点") or "").strip()
    if site not in ("TH", "VN"):
        return {"verdict": "SKIP", "reason": f"未知站点 {site or '(空)'}", "site": site}
    cfg = merged(cfg_all, site)
    cfg["site"] = site
    cny_per_usd = cfg["cny_per_usd"]
    rate = cfg["rate_to_local"]
    band = cfg["price_band_usd"]
    band_lo_local = band["min"] * cny_per_usd * rate
    band_hi_local = band["max"] * cny_per_usd * rate

    low, high = parse_price_range(row.get("售价区间"))
    stock = fnum(row.get("库存"), -1)
    tags = (row.get("异常标签") or "").strip()

    issues = []
    if low is None:
        issues.append("NO_PRICE: 售价区间为空（未定价，发布必失败）")
    else:
        usd = low / rate * 1.0            # 本币 -> CNY -> USD
        cny = low / rate
        usd = cny / cny_per_usd
        if usd < band["min"] - 0.005:
            issues.append(f"PRICE_LOW: 低档 ${usd:.2f} 破带下限 ${band['min']:.2f}（亏本价）")
        if high is not None and (high / rate / cny_per_usd) > band["max"] + 0.005:
            issues.append(f"PRICE_HIGH: 高档 ${high / rate / cny_per_usd:.2f} 破带上限 ${band['max']:.2f}")

    if stock > STOCK_HARD_MAX:
        issues.append(f"STOCK_OVER: 库存 {stock:,.0f} 超平台上限 {STOCK_HARD_MAX:,}（错误码 12052055）")
    elif 0 <= stock < STOCK_MIN:
        issues.append(f"STOCK_LOW: 库存 {stock:,.0f} 低于可售下限 {STOCK_MIN}")

    if "CATEGORY_NOT_MATCH" in tags:
        issues.append("CATEGORY: 类目未匹配，需显式写类目三件套")
    if "MISSING_ITEM_DATA" in tags:
        issues.append("DATA_BROKEN: tikTokItemModel 为空（body 结构写坏 → 删除重认领）")
    if "MISSING_WEIGHT" in tags:
        issues.append("WEIGHT: 缺重量，发布配置会用默认 1kg 虚报体积重（可用 prep_publish 补）")

    hard = [i for i in issues if not i.startswith("WEIGHT")]
    return {
        "site": site,
        "verdict": "BLOCK" if hard else ("WARN" if issues else "OK"),
        "issues": issues,
        "low": low, "high": high, "stock": stock,
        "band_lo_local": band_lo_local, "band_hi_local": band_hi_local,
        "currency": cfg["currency"],
    }


def main():
    ap = argparse.ArgumentParser(description="发布前价格/库存闸门")
    ap.add_argument("--csv", default=DEFAULT_CSV, help="ttl-list 落盘的 CSV 路径")
    ap.add_argument("--site", choices=["TH", "VN"], help="只判某个站")
    ap.add_argument("--status", choices=["0", "1"], help="0=未发布 1=已发布（只看哪一种）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--hint", action="store_true", help="反推建议挂价（配 --cost/--site/--pieces）")
    ap.add_argument("--cost", type=float, help="采购价(元/件)，--hint 用")
    ap.add_argument("--weight", type=float, default=150, help="实重 g，--hint 用")
    ap.add_argument("--pieces", type=int, default=1, help="1=单件（默认，唯一允许铺货的取值）")
    ap.add_argument("--allow-bundle", action="store_true",
                    help="实测确认捆绑发货后才允许 pieces>1（与 pricing_calc 同一硬闸）")
    ap.add_argument("--dims", help="包装尺寸 如 20x15x8，--hint 用")
    a = ap.parse_args()

    cfg_all = load_config()

    if a.hint:
        if a.cost is None:
            ap.error("--hint 需要 --cost")
        bv = bundle_violation(a.pieces, a.allow_bundle)
        if bv:
            print(f"[block] {bv}", file=sys.stderr)
            return 3
        sites = [a.site] if a.site else ["TH", "VN"]
        for s in sites:
            cfg = merged(cfg_all, s)
            cfg["site"] = s
            dims = None
            if a.dims:
                p = a.dims.lower().replace("*", "x").split("x")
                if len(p) == 3:
                    dims = tuple(float(x) for x in p)
            r = compute(cfg, a.cost, "shelf", a.pieces, a.weight, dims)
            print(f"[{s}] 采购 ¥{a.cost}/件 × {a.pieces} 件 · 计费重 {r['weight_g']:.0f}g · 净利目标 "
                  f"{cfg['schemes']['shelf']['target_margin']*100:.0f}%")
            print(f"  到岸 ¥{r['landed_cny']}（固定 ¥{r['fixed_cny']}）→ 建议挂价 "
                  f"{r['price_local']:,.0f} {r['currency']}（$ {r['order_usd']:.2f} 客单）")
            lo = cfg['price_band_usd']['min'] * cfg['cny_per_usd'] * cfg['rate_to_local']
            hi = cfg['price_band_usd']['max'] * cfg['cny_per_usd'] * cfg['rate_to_local']
            print(f"  本币价格带：{lo:,.0f} – {hi:,.0f} {r['currency']}"
                  f"（$ {cfg['price_band_usd']['min']}–{cfg['price_band_usd']['max']}）"
                  f"  {'✓ 在带内' if r['in_band'] else '✗ 超带 → 换品，不要硬调 markup'}")
            print(f"  买家实付约 $ {r['buyer_paid_usd']:.2f}（含进口税 "
                  f"{(cfg.get('buyer_tax_rate') or 0)*100:.1f}%，比价用这个数）")
            mc = max_cost(cfg, "shelf", a.pieces, a.weight)
            print(f"  该打法该重量下的采购价上限：¥{mc:.1f}/件")
            print()
        return 0

    if not os.path.exists(a.csv):
        sys.exit(f"[gate] 找不到 {a.csv}\n"
                 f"        先跑：cd {ROOT} && python3 scripts/erp_client.py ttl-list --region TH")

    rows = list(csv.DictReader(open(a.csv, encoding="utf-8-sig")))
    if a.site:
        rows = [r for r in rows if (r.get("站点") or "").strip() == a.site]
    if a.status:
        rows = [r for r in rows if str(r.get("状态", "")).strip() == a.status]

    results = []
    for r in rows:
        j = judge(r, cfg_all)
        j.update({
            "ttl_id": r.get("ttl_id"), "title": (r.get("标题") or "")[:40],
            "status": r.get("状态"), "price_range": r.get("售价区间"),
            "tags": (r.get("异常标签") or "").strip(),
        })
        results.append(j)

    if a.json:
        import json
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        blocks = [x for x in results if x["verdict"] == "BLOCK"]
        warns = [x for x in results if x["verdict"] == "WARN"]
        print(f"[gate] 共 {len(results)} 条 | 阻断 {len(blocks)} | 仅告警 {len(warns)} "
              f"| 通过 {len([x for x in results if x['verdict'] == 'OK'])}\n")
        for x in results:
            mark = {"BLOCK": "✗", "WARN": "!", "OK": "✓", "SKIP": "-"}[x["verdict"]]
            print(f"{mark} [{x['site']}] {x['ttl_id']} 状态={x['status']} 价={x['price_range']} "
                  f"带={x.get('band_lo_local', 0):,.0f}–{x.get('band_hi_local', 0):,.0f} "
                  f"{x['title']}")
            for i in x["issues"]:
                print(f"    · {i}")
        print()
        if blocks:
            print(f"[gate] ❌ 阻断 {len(blocks)} 条 —— 禁止对这些商品 ttl-publish。")
            print("       修价：python3 scripts/price_gate.py --hint --cost <采购价> --site <TH|VN> "
                  "--pieces 1 得到目标价，再 set_price.py 提交")
            print("       修库存/类目：python3 scripts/fix_sku.py --id <ttl_id> --stock 1000 ...")
        else:
            print("[gate] ✅ 无阻断项，可进入 ttl-publish")
    return 1 if any(x["verdict"] == "BLOCK" for x in results) else 0


if __name__ == "__main__":
    sys.exit(main())
