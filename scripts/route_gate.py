#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""collect 前的货代路线复核：只认回填到候选 CSV 的路线证据，不猜测快递天数。

规则来自 2026-11-13 VN20 批次教训：
- 服务商卡片写“多仓联动”不等于当前账号已绑定该仓；以“我的货代”已绑定行地址为准。
- 同城/同省只是先验，不能替代发货地→实际货代仓的 ≤1 天到仓证据。
- ship_city 为空时，公司名/籍贯不能当成已核实发货城市。

退出码：0 路线证据齐全；1 有阻断项；2 输入文件问题。
"""
import argparse
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CSV = os.path.join(ROOT, "erp_data", "selection_candidates.csv")
WAREHOUSE_PROVINCE = {
    "义乌市": "浙江省", "金华市": "浙江省",
    "东莞市": "广东省", "广州市": "广东省",
    "泉州市": "福建省",
}
PROVINCE_KEYS = ("省", "市", "自治区", "壮族", "回族", "维吾尔")


def province_of(city: str) -> str:
    city = (city or "").strip()
    for key in PROVINCE_KEYS:
        idx = city.find(key)
        if idx >= 0:
            return city[: idx + len(key)]
    return ""


def num(value):
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser(description="货代路线硬闸（本地只读；不联网、不补写天数）")
    ap.add_argument("--csv", default=DEFAULT_CSV)
    ap.add_argument("--warehouses", default="", help="已绑定货代仓城市，逗号分隔；如 义乌市,东莞市")
    ap.add_argument("--max-transit-days", type=float, default=1.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(a.csv):
        sys.stderr.write(f"[route] 找不到候选清单 {a.csv}\n")
        return 2

    bound = {x.strip() for x in a.warehouses.split(",") if x.strip()}
    rows = list(csv.DictReader(open(a.csv, encoding="utf-8-sig")))
    results = []

    for row in rows:
        offer = (row.get("offer_id") or "").strip()
        issues, warns = [], []
        ship = (row.get("ship_city") or "").strip()
        warehouse = (row.get("forwarder_city") or "").strip()
        transit = num(row.get("ship_transit_days"))

        if not offer:
            issues.append("OFFER_MISSING: offer_id 为空")
        if not ship:
            issues.append("SHIP_CITY_MISSING: 发货城市为空；公司名不能替代已核实发货地")
        if not warehouse:
            issues.append("WAREHOUSE_MISSING: forwarder_city 为空")
        elif bound and warehouse not in bound:
            issues.append(f"WAREHOUSE_NOT_BOUND: {warehouse} 不在已绑定货代仓列表 {sorted(bound)}")
        if transit is None:
            issues.append("TRANSIT_MISSING: ship_transit_days 为空；不得用同城/同省推测补位")
        elif transit > a.max_transit_days:
            issues.append(f"TRANSIT_OVER: 到仓 {transit:g} 天 > {a.max_transit_days:g} 天")

        sp, wp = province_of(ship), WAREHOUSE_PROVINCE.get(warehouse, "")
        if ship and warehouse and sp and wp and sp != wp:
            warns.append(f"跨省线路：{ship} → {warehouse}；必须有 ≤1 天到仓证据，否则换发货地")

        results.append({"offer_id": offer, "issues": issues, "warns": warns})

    blocked = [r for r in results if r["issues"]]
    warned = [r for r in results if r["warns"] and not r["issues"]]

    if a.json:
        print(json.dumps({"bound_warehouses": sorted(bound), "total": len(rows),
                          "blocked": len(blocked), "results": results},
                         ensure_ascii=False, indent=2))
    else:
        print("=== route_gate（发货地→已绑定货代仓路线复核）===")
        print(f"[route] 共 {len(rows)} 条 | 阻断 {len(blocked)} | 仅提醒 {len(warned)}\n")
        for r in results:
            mark = "✗" if r["issues"] else ("!" if r["warns"] else "✓")
            print(f"{mark} {r['offer_id']}")
            for x in r["issues"]:
                print(f"   ✗ {x}")
            for x in r["warns"]:
                print(f"   ! {x}")
        print()
        if blocked:
            print("[route] ❌ 存在路线阻断项，禁止 collect；先补已绑定仓与 ≤1 天到仓证据。")
        else:
            print("[route] ✅ 路线字段齐全，仍需与 select_gate 结果一起作为 collect 前置条件。")

    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
