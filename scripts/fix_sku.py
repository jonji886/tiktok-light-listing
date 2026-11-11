#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_sku.py — 用官方 edit body 结构修 SKU 规格名与库存（发布前置校验修复）

背景（2026-11-11 实测）：
  TikTok 发布失败返回：
    [12052153] 产品可变选项「立体打印自透气【50*70*30cm】」内容词 (自)
    [12052055] SKU inventory quantity ... must be between 0 and 999999

官方 edit body（chunk77 buildTKRequest 逆向，关键！）：
  {
    "id": "<ttl_id>", "platform": "tiktok", "sellerId": "<toSellerId>",
    "currency": "THB", "toCategoryId": ..., "toCategoryIdTree": ...,
    "toCategoryNameTree": ..., "title": "<title>",
    "itemModel": {                       # ← 整个 reqInfo 结构，不是内层 ttm！
       "shopeeItemModel": null, "temuItemModel": null,
       "tikTokItemModel": { ...原 ttm, "productCreateRequestV2": <改后的 v2> }
    }
  }
  且**不能**传顶层 "reqInfo"（旧版 prep_publish.py 的错误做法，会把数据清空成
  MISSING_ITEM_DATA）。

用法：
  python3 fix_sku.py --id <ttl_id> --stock 1000 --dry-run
  python3 fix_sku.py --id <ttl_id> --stock 1000 --save
"""
import argparse, copy, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from erp_client import _post

DETAIL = "/item/crossBorder/platformCollect/getDetail"
EDIT = "/item/crossBorder/edit"

# 纯尺寸规格名（泰文「ขนาด」=尺寸），去掉所有描述性词，避开违禁词命中
SAFE_NAMES = {
    "50*70*30": "ขนาด 50x70x30 ซม.",
    "80*100*30": "ขนาด 80x100x30 ซม.",
    "35*50": "ขนาด 35x50 ซม.",
}


def safe_name(origin):
    for key, val in SAFE_NAMES.items():
        if key in str(origin):
            return val
    return f"ขนาด {str(origin)[-14:]}"


def snapshot(args):
    d = _post(DETAIL, {"id": args.id}, args)
    ttm = ((d.get("reqInfo") or {}).get("tikTokItemModel") or {})
    v2 = ttm.get("productCreateRequestV2") or {}
    skus = v2.get("skus") or []
    return d, ttm, v2, skus, {
        "description_len": len(v2.get("description") or ""),
        "images": len(v2.get("main_images") or []),
        "skus": len(skus),
        "prices": [(s.get("price") or {}).get("amount") for s in skus],
        "stocks": [((s.get("inventory") or [{}])[0]).get("quantity") for s in skus],
        "spec_names": [((s.get("sales_attributes") or [{}])[0]).get("value_name") for s in skus],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--stock", type=int, default=1000)
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    d, ttm, v2, skus, before = snapshot(args)
    print("[fix_sku] 修改前:", json.dumps(before, ensure_ascii=False))
    if not skus:
        sys.exit("[fix_sku] itemModel 为空，先删除并重新认领")

    modified = copy.deepcopy(v2)
    for i, sku in enumerate(modified.get("skus") or []):
        inv = sku.get("inventory") or []
        if inv:
            inv[0]["quantity"] = args.stock
        else:
            sku["inventory"] = [{"quantity": args.stock}]
        attrs = sku.get("sales_attributes") or []
        if attrs:
            old = attrs[0].get("value_name", "")
            attrs[0]["value_name"] = safe_name(old)
            print(f"  SKU{i+1} 规格名: {old[:38]}  →  {attrs[0]['value_name']}")
    print(f"[fix_sku] 库存统一改为 {args.stock}（平台上限 999999）")

    req_info = d.get("reqInfo") or {}
    body = {
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
            "tikTokItemModel": {**ttm, "productCreateRequestV2": modified},
        },
    }
    if not body["sellerId"] or not body["toCategoryId"]:
        sys.exit("[fix_sku] 关键字段缺失，中止")

    if not args.save:
        print("[fix_sku] dry-run 未提交。确认后加 --save")
        print("  body 顶层:", {k: str(v)[:60] for k, v in body.items() if k != "itemModel"})
        print("  itemModel keys:", list(body["itemModel"].keys()))
        return

    r = _post(EDIT, body, args)
    print(f"[fix_sku] 保存结果: {json.dumps(r, ensure_ascii=False)[:250]}")
    _, _, _, _, after = snapshot(args)
    print("[fix_sku] 修改后:", json.dumps(after, ensure_ascii=False))
    ok = (after["skus"] == before["skus"] and
          after["description_len"] == before["description_len"] and
          after["images"] == before["images"] and
          after["prices"] == before["prices"])
    print(f"[fix_sku] 数据完整性: {'OK' if ok else '⚠ 被清空，需删重建'}")
    changed = (after["stocks"] != before["stocks"] or
               after["spec_names"] != before["spec_names"])
    print(f"[fix_sku] SKU 修改生效: {'✓' if changed else '⚠ 未变化'}")


if __name__ == "__main__":
    main()
