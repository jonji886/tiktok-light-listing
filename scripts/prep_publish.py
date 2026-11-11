#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prep_publish.py — 铺货前置：按 SKU 梯度改价 + 补包裹重量 + 回读验证

链路位置：ttl-claim（认领）→ 【本脚本】→ ttl-publish（发布）

实测依据（2026-11-11）：
  - ⚠ 本脚本早期版本把 itemModel 写成内层 ttm 且额外传顶层 reqInfo，
    会把商品数据清空成 MISSING_ITEM_DATA。已按官方 buildTKRequest 修正：
    itemModel 必须是「整个 reqInfo 结构」，且不能传顶层 reqInfo。
    改价请用 set_price.py（batchOps opsType:2），改SKU请用 fix_sku.py。
  - description 为纯图片 HTML 不阻塞发布（历史成功商品同为纯文字长度 0）；
    两条发布失败的记录是 description HTML 长度 = 0。
  - package_weight 留空也不阻塞（由 publishConfig 统一设置），但补齐可清 MISSING_WEIGHT 标签。
  - 库存沿用 1688 原值会触发平台 12052055（上限 999999），发布前须用 fix_sku.py 收敛。

用法：
  python3 prep_publish.py --id <ttl_id> --sku-prices 129,169,199 --weight 0.3 --save
  python3 prep_publish.py --id <ttl_id> --show
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from erp_client import _post

DETAIL = "/item/crossBorder/platformCollect/getDetail"
EDIT = "/item/crossBorder/edit"


def field(d, key, default=None):
    """顶层优先，data 嵌套兜底"""
    v = d.get(key)
    if v is None:
        v = (d.get("data") or {}).get(key)
    return default if v is None else v


def build_edit_body(d, ttm, req_info, args):
    """官方 chunk77 buildTKRequest 结构：itemModel = 整个 reqInfo，不传顶层 reqInfo"""
    return {
        "id": str(d.get("id") or args.id),
        "platform": "tiktok",
        "sellerId": str(d.get("toSellerId") or ""),
        "currency": d.get("currency") or "THB",
        "toCategoryId": d.get("toCategoryId"),
        "toCategoryIdTree": d.get("toCategoryIdTree"),
        "toCategoryNameTree": d.get("toCategoryNameTree"),
        "title": d.get("title"),
        "itemModel": {
            **req_info,
            "tikTokItemModel": {**ttm, "productCreateRequestV2": ttm.get("productCreateRequestV2") or {}},
        },
    }


def summary(d, tag):
    ttm = (d.get("reqInfo") or {}).get("tikTokItemModel") or {}
    v2 = ttm.get("productCreateRequestV2") or {}
    print(f"--- {tag} ---")
    print(f"  id={field(d,'id')}  店铺={field(d,'toSellerNick')}  币种={field(d,'currency')}")
    print(f"  类目={field(d,'toCategoryNameTree')} (id={field(d,'toCategoryId')})")
    desc = v2.get("description") or ""
    print(f"  描述 HTML={len(desc)} 字符 / 图 {desc.count('<img')} 张 | 主图 {len(v2.get('main_images') or [])} 张")
    print(f"  包裹重量 = {v2.get('package_weight')}")
    for i, s in enumerate(v2.get("skus") or []):
        p = s.get("price") or {}
        attr = (s.get("sales_attributes") or [{}])[0].get("value_name", "")
        print(f"  SKU{i+1}: {p.get('amount')} {p.get('currency')} (sale={p.get('sale_price')}, 成本¥{p.get('source_price')}) | {attr[:36]}")
    return v2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--sku-prices", default="", help="按 SKU 顺序的 THB 价格，逗号分隔")
    ap.add_argument("--weight", type=float, default=0.0, help="包裹重量 KG")
    ap.add_argument("--save", action="store_true", help="真正提交保存（写操作）")
    ap.add_argument("--show", action="store_true", help="只查看不修改")
    args = ap.parse_args()

    d = _post(DETAIL, {"id": args.id}, args)
    if args.show:
        summary(d, "当前状态")
        return

    req_info = d.get("reqInfo") or (d.get("data") or {}).get("reqInfo") or {}
    ttm = req_info.get("tikTokItemModel") or {}
    if not ttm:
        sys.exit("[prep] getDetail 无 tikTokItemModel，无法编辑（可能被之前的保存清空）")
    v2 = ttm.get("productCreateRequestV2") or {}
    skus = v2.get("skus") or []
    if not skus:
        sys.exit("[prep] 无 SKU，中止")

    if args.sku_prices:
        prices = [x.strip() for x in args.sku_prices.split(",") if x.strip()]
        if len(prices) != len(skus):
            sys.exit(f"[prep] 价格数 {len(prices)} != SKU 数 {len(skus)}，中止")
        currency = v2.get("currency") or field(d, "currency") or "THB"
        for sku, price in zip(skus, prices):
            sku.setdefault("price", {})
            sku["price"]["amount"] = price
            sku["price"]["sale_price"] = price
            sku["price"]["currency"] = currency
        print(f"[prep] 价格改为: {', '.join(prices)} {currency}")

    if args.weight > 0:
        v2["package_weight"] = {"value": str(args.weight), "unit": "KILOGRAM"}
        print(f"[prep] 包裹重量改为: {args.weight} KG")

    body = build_edit_body(d, ttm, req_info, args)
    miss = [k for k in ("sellerId", "toCategoryId", "currency", "title") if not body.get(k)]
    if miss:
        sys.exit(f"[prep] edit body 关键字段为空 {miss}，中止（避免写坏记录）")

    if not args.save:
        print("[prep] dry-run：未提交。确认无误后加 --save")
        print("  body 关键字段:", {k: str(body[k])[:60] for k in
              ("id", "title", "sellerId", "toCategoryId", "currency")})
        return

    r = _post(EDIT, body, args)
    print(f"[prep] 保存已提交: {json.dumps(r, ensure_ascii=False)[:200]}")
    print("[prep] 回读验证：")
    d2 = _post(DETAIL, {"id": args.id}, args)
    summary(d2, "保存后")


if __name__ == "__main__":
    main()
