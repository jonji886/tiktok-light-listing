#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定价计算与价格带校验（纯标准库；参数唯一来源 = config/pricing.yaml）

用法：
  # 算售价：采购价 8 元、150g、单件、货架场
  python3 scripts/pricing_calc.py --cost 8 --weight 150 --scheme shelf --pieces 1

  # 带抛重（长宽高，单位 cm；与实重取大）
  python3 scripts/pricing_calc.py --cost 8 --weight 200 --dims 25x20x10

  # 各站点/打法/重量的采购价上限矩阵。改费率或汇率后必跑
  python3 scripts/pricing_calc.py --limits

  # 降净利冲量测算（新店破零期）
  python3 scripts/pricing_calc.py --limits --margin 0.12

  # 一致性自检：yaml 里的 markup 与费率是否吻合、是否有"无解"组合
  python3 scripts/pricing_calc.py --check

退出码：0 正常/全部在带内；2 有超带组合；1 自检发现不一致
"""
import argparse
import json
import os
import sys

CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "config", "pricing.yaml")

WEIGHT_COLS = (100, 150, 250, 400)


# ── 最小 YAML 子集解析（块状 dict + 标量，免 PyYAML 依赖）──
def _strip_comment(val):
    if val[:1] in ('"', "'"):
        q = val[0]
        end = val.find(q, 1)
        return val[1:end] if end > 0 else val[1:]
    idx = val.find(" #")
    return val[:idx] if idx >= 0 else val


def _cast(val):
    val = val.strip()
    if val == "":
        return None
    if len(val) > 1 and val[0] == val[-1] and val[0] in ('"', "'"):
        return val[1:-1]
    low = val.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        return val


def load_config(path=CONFIG):
    root = {}
    stack = [(-1, root)]
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            indent = len(line) - len(line.lstrip(" "))
            body = line.strip()
            if ":" not in body:
                continue
            key, _, val = body.partition(":")
            val = _strip_comment(val)
            while stack and stack[-1][0] >= indent:
                stack.pop()
            parent = stack[-1][1]
            if val.strip() == "":
                node = {}
                parent[key.strip()] = node
                stack.append((indent, node))
            else:
                parent[key.strip()] = _cast(val)
    return root


def merged(cfg, site):
    node = dict(cfg.get("defaults", {}))
    node.update(cfg[site])
    return node


# ── 重量 ──
def parse_dims(s):
    """'25x20x10' -> (25,20,10)"""
    parts = s.lower().replace("*", "x").split("x")
    if len(parts) != 3:
        raise ValueError("--dims 需形如 25x20x10")
    return tuple(float(p) for p in parts)


def chargeable_weight(cfg, weight_g=None, dims=None):
    """计费重量 = max(实重, 抛重)。两者都缺时用 default_weight_g。"""
    actual = weight_g if weight_g else None
    vol = None
    if dims:
        l, w, h = dims
        vol = l * w * h / cfg.get("volumetric_divisor", 6000) * 1000
    if actual is None and vol is None:
        actual = cfg.get("default_weight_g", 150)
    return max([x for x in (actual, vol) if x is not None])


# ── 核心公式 ──
def domestic_cost(cfg, pieces):
    """一个包裹只付一次国内快递，多件只加续重"""
    return cfg["domestic_shipping"] + (pieces - 1) * cfg["domestic_shipping_per_extra_unit"]


def hidden_shipping(cfg, pieces, weight_g):
    """跨境段物流藏价：按计费重量线性，件数线性累加"""
    rate = cfg.get("shipping_cny_per_gram")
    if rate is None:                       # 兼容旧版固定值配置
        return (cfg.get("shipping_base_cny", 0)
                + (pieces - 1) * cfg.get("shipping_per_extra_unit_cny", 0))
    raw = rate * weight_g * pieces
    return max(raw, cfg.get("shipping_min_cny", 0) * pieces)


def fixed_cost(cfg, pieces, weight_g):
    """订单级固定成本：国内快递 + 货代 + 包材 + 物流藏价 + 平台按单固定费"""
    return (domestic_cost(cfg, pieces)
            + cfg["freight_forwarder_fee"]
            + cfg["packaging_material"]
            + hidden_shipping(cfg, pieces, weight_g)
            + (cfg.get("order_fixed_fee_cny") or 0.0))


def landed_cost(cfg, cost, pieces, weight_g):
    return cost * pieces + fixed_cost(cfg, pieces, weight_g)


def fee_rate(cfg, scheme="shelf"):
    """总扣点率：佣金 + 交易手续费 + 增长服务费 + 退款预留 + 代扣税 + 达人佣金

    2026-11-12 起支持两个豁免开关（新商扶持窗口，窗口一过必须关掉，否则定价失准）：
      transaction_fee_waived  → VN 前 90 天免 6% 交易手续费
      growth_fee_waived       → TH 前 21 天免 8.03% 电商增长服务费
    """
    sc = cfg["schemes"][scheme]
    tx = 0.0 if cfg.get("transaction_fee_waived") else (cfg.get("transaction_fee") or 0)
    eg = 0.0 if cfg.get("growth_fee_waived") else (cfg.get("ecommerce_growth_fee") or 0)
    return (cfg.get("platform_commission", 0)
            + tx
            + eg
            + cfg.get("refund_reserve", 0)
            + (cfg.get("tax_withhold") or 0)
            + (sc.get("affiliate_commission") or 0))



def markup_for(cfg, scheme, margin):
    """由目标净利倒推加价系数（覆盖 yaml 里的 markup，用于冲量期测算）"""
    denom = 1 - fee_rate(cfg, scheme) - margin
    if denom <= 0:
        return None
    return 1 / denom


def compute(cfg, cost, scheme="shelf", pieces=1, weight_g=None, dims=None, margin=None):
    w = chargeable_weight(cfg, weight_g, dims)
    landed = landed_cost(cfg, cost, pieces, w)
    markup = markup_for(cfg, scheme, margin) if margin is not None \
        else cfg["schemes"][scheme]["markup"]
    usa = landed * markup / cfg["cny_per_usd"]
    band = cfg["price_band_usd"]
    unit_usd = usa / pieces
    buyer_mult = 1 + (cfg.get("buyer_tax_rate") or 0.0)
    return {
        "site": cfg["site"], "scheme": scheme, "pieces": pieces,
        "cost_cny": cost, "weight_g": round(w, 0),
        "landed_cny": round(landed, 2),
        "fixed_cny": round(fixed_cost(cfg, pieces, w), 2),
        "hidden_cny": round(hidden_shipping(cfg, pieces, w), 2),
        "markup": round(markup, 3),
        "price_local": round(landed * cfg["rate_to_local"] * markup, 2),
        "currency": cfg["currency"],
        "order_usd": round(usa, 2), "unit_usd": round(unit_usd, 2),
        # 买家实付（含进口税/VAT）——价格带与竞品比价必须用这个口径，
        # 因为买家看到的是它，不是你的挂价（TH 17.7% / VN 10%）
        "buyer_paid_usd": round(usa * buyer_mult, 2),
        "fee_rate": round(fee_rate(cfg, scheme), 4),
        # 价格带判定用「客单价（订单总额）」，不是「单件价」：
        # 组合装的订单额 $7.02 完全在带内，但单件等效 $3.51 会被旧口径判"超带"而误杀
        # （这是 2026-11-12 修掉的逻辑 bug：旧版 unit_usd >= band.min 让所有 2 件套永远 False）
        "band": [band["min"], band["max"]],
        "in_band": usa >= band["min"] and usa <= band["max"],
        "single_unit_ok": unit_usd >= band["min"],   # 单件价是否也过下限（仅单品有意义）
    }



def max_cost(cfg, scheme, pieces=1, weight_g=None, margin=None):
    """价格带上限反推：最高可承受的采购价（元/件）"""
    w = chargeable_weight(cfg, weight_g)
    markup = markup_for(cfg, scheme, margin) if margin is not None \
        else cfg["schemes"][scheme]["markup"]
    max_landed = cfg["price_band_usd"]["max"] * cfg["cny_per_usd"] / markup
    return (max_landed - fixed_cost(cfg, pieces, w)) / pieces


def min_price_usd(cfg, cost, scheme="shelf", pieces=1, weight_g=None, margin=None):
    """给定采购价，反推最低可售单价（USD）——判断是否掉到价格带下限以下"""
    w = chargeable_weight(cfg, weight_g)
    markup = markup_for(cfg, scheme, margin) if margin is not None \
        else cfg["schemes"][scheme]["markup"]
    return landed_cost(cfg, cost, pieces, w) * markup / cfg["cny_per_usd"] / pieces


def max_cost_at_price(cfg, price_usd, scheme="shelf", pieces=1, weight_g=None, margin=None):
    """按指定售价反推可承受采购价（元/件）；用于看价格带下限处还剩多少空间"""
    w = chargeable_weight(cfg, weight_g)
    markup = markup_for(cfg, scheme, margin) if margin is not None \
        else cfg["schemes"][scheme]["markup"]
    max_landed = price_usd * cfg["cny_per_usd"] / markup
    return (max_landed - fixed_cost(cfg, pieces, w)) / pieces


def sites_of(cfg_all, only=None):
    out = []
    for s in ("TH", "VN"):
        if only and s != only:
            continue
        c = merged(cfg_all, s)
        c["site"] = s
        out.append(c)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cost", type=float, help="采购价(元/件)")
    ap.add_argument("--site", choices=["TH", "VN"])
    ap.add_argument("--scheme", default="shelf", choices=["shelf", "creator"])
    ap.add_argument("--pieces", type=int, default=1,
                    help="1=单件（一件代发只准填 1）；2=2件套，仅定价模型测算，禁止据此铺货（见 SKILL 4.4）")
    ap.add_argument("--weight", type=float, help="单件实重(g)")
    ap.add_argument("--dims", help="包装长宽高 cm，如 25x20x10；抛重与实重取大")
    ap.add_argument("--margin", type=float, help="覆盖目标净利率，如 0.12（冲量期测算）")
    ap.add_argument("--limits", action="store_true", help="打印采购价上限矩阵")
    ap.add_argument("--check", action="store_true", help="校验配置自洽")
    ap.add_argument("--all-schemes", action="store_true",
                    help="包含 enabled:false 的打法（默认只算启用的）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    cfg_all = load_config()
    targets = sites_of(cfg_all, a.site)

    if a.check:
        bad = 0
        for c in targets:
            for name, sc in c["schemes"].items():
                expect = markup_for(c, name, sc["target_margin"])
                if expect is None:
                    bad += 1
                    print(f"!! {c['site']}/{name}: 扣点+目标净利 ≥ 100%，无解")
                    continue
                diff = abs(expect - sc["markup"])
                if diff >= 0.02:
                    bad += 1
                print(f"{'OK ' if diff < 0.02 else '!! '}{c['site']}/{name}: "
                      f"yaml={sc['markup']:.3f} 计算={expect:.3f} "
                      f"(扣点合计 {fee_rate(c, name) * 100:.2f}%)")
            for name, sc in c["schemes"].items():
                if not a.all_schemes and not sc.get("enabled", True):
                    continue          # 停用档不计入失败项，避免"策略已关掉却报错"
                for p in (1, 2):
                    for w in WEIGHT_COLS:
                        mc = max_cost(c, name, p, w)
                        if mc <= 0:
                            bad += 1
                            print(f"!! {c['site']}/{name}/{p}件/{w}g: 采购价上限 {mc:.1f}元 → 无解")
            # 价格带下限是否可覆盖最低固定成本
            mc_top = max_cost(c, "shelf", 1, WEIGHT_COLS[-1])
            print(f"   {c['site']} 参考：400g 单件采购价上限 {mc_top:.1f} 元")
        print("\n" + ("配置自洽" if bad == 0 else f"{bad} 处需修正"))
        return 0 if bad == 0 else 1

    if a.limits:
        if a.json:
            rows = []
            for c in targets:
                for name, sc in c["schemes"].items():
                    if not a.all_schemes and not sc.get("enabled", True):
                        continue
                    for p in (1, 2):
                        for w in WEIGHT_COLS:
                            rows.append({"site": c["site"], "scheme": name, "pieces": p,
                                         "weight_g": w,
                                         "markup": round(sc["markup"], 2),
                                         "max_cost_cny": round(max_cost(c, name, p, w, a.margin), 1),
                                         "feasible": max_cost(c, name, p, w, a.margin) > 0})
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0

        print("⚠️  「2件」列仅为定价模型测算：一件代发认领后 SKU 是单件规格、代发只下 1 件，\n"
              "    按 2 件成本定价 = 收 2 件钱发 1 件（2026-11-12 第二批实测纠正，见 SKILL 4.4）。\n"
              "    --pieces 一律填 1。\n")
        for c in targets:
            for name, sc in c["schemes"].items():
                if not a.all_schemes and not sc.get("enabled", True):
                    continue
                mk = markup_for(c, name, a.margin) if a.margin is not None else sc["markup"]
                head = f"[{c['site']}] {name} · markup {mk:.2f} · 扣点合计 {fee_rate(c, name) * 100:.2f}%"
                if a.margin is not None:
                    head += f" · 目标净利 {a.margin * 100:.0f}%"
                print(head)
                print(f"  {'件数':<6}" + "".join(f"{w}g".rjust(9) for w in WEIGHT_COLS))
                for p in (1, 2):
                    cells = "".join(
                        (f"{max_cost(c, name, p, w, a.margin):>9.1f}" if max_cost(c, name, p, w, a.margin) > 0
                         else f"{'无解':>9}") for w in WEIGHT_COLS)
                    print(f"  {p}件".ljust(8) + cells)
                print()

                print()

            # 价格带下限的余量：按下限价卖，还能承受多高的采购价
            lo = c["price_band_usd"]["min"]
            cells = []
            for w in WEIGHT_COLS:
                mc = max_cost_at_price(c, lo, "shelf", 1, w, a.margin)
                cells.append(f"{w}g {mc:>5.1f}元" if mc > 0 else f"{w}g {'无解':>5}")
            print(f"  按价格带下限 ${lo:.2f} 卖：可承受采购价 " + " / ".join(cells))
            print()
        return 0

    if a.cost is None:
        ap.error("需要 --cost，或用 --limits / --check")

    dims = parse_dims(a.dims) if a.dims else None
    out = []
    for c in targets:
        if not c["schemes"][a.scheme].get("enabled", True):
            sys.stderr.write(f"[warn] {c['site']} 的 {a.scheme} 档在 pricing.yaml 里是 "
                             f"enabled:false（当前策略不开达人佣金），结果仅供参考\n")
        out.append(compute(c, a.cost, a.scheme, a.pieces, a.weight, dims, a.margin))
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for r in out:
            tag = "✓ 带内" if r["in_band"] else "✗ 超带 → 换品/改打法，不要硬调 markup"
            print(f"[{r['site']}] {r['scheme']} · {r['pieces']}件 · 采购 {r['cost_cny']:.1f} 元/件"
                  f" · 计费重 {r['weight_g']:.0f}g")
            print(f"  到岸 {r['landed_cny']:.1f} 元（固定 {r['fixed_cny']:.1f}，其中物流藏价 {r['hidden_cny']:.1f}）"
                  f" × {r['markup']:.2f} = {r['price_local']:.0f} {r['currency']}")
            # 买家实付 = 挂价 ×（1+进口税/VAT），比价必须用这个数，否则高估自己的价格竞争力
            buyer_note = (f"（买家实付 ${r['buyer_paid_usd']:.2f}，含税）"
                          if r["buyer_paid_usd"] > r["order_usd"] + 0.005 else "")
            print(f"  单件折 ${r['unit_usd']:.2f}｜客单价 ${r['order_usd']:.2f}{buyer_note}"
                  f"｜价格带 ${r['band'][0]}-{r['band'][1]}  {tag}")
            if r["pieces"] > 1 and not r["single_unit_ok"]:
                print(f"  注：单件等效 ${r['unit_usd']:.2f} 低于带下限，但按客单价判定为带内"
                      f"（组合装按订单总额比价，不按单件）")

    return 0 if all(r["in_band"] for r in out) else 2


if __name__ == "__main__":
    sys.exit(main())
