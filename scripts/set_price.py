#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
set_price.py — 用 ERP 官方「批量设置售价」接口改价（不清空商品数据）

接口（2026-11-11 从 chunk77 CrossFastUploadTiktokCollection 反编译确认）：
  POST /item/crossBorder/platformCollect/batchOps
  {
    "idList": ["<ttl_id>"], "platform": "tiktok", "opsType": 2,
    "priceType": 2,                       # 1=固定价 2=公式
    "priceFormula": {
      "type": 1,                          # 1=基于当前售价 2=基于货源价
      "multiplicationColumn": 2.8,       # 倍数(UI 百分比/100)
      "additionColumn": 39.26,           # 加价，单位=商品币种
      "reduceColumn": 0,
      "decimalPlacesType": 0,
      "tailAmount": null
    }
  }
  公式: 新售价 = 基准价 × multiplicationColumn + additionColumn - reduceColumn

注意：/item/crossBorder/edit 会把 tikTokItemModel 清空（实测 2026-11-11 变
      MISSING_ITEM_DATA），禁止用它改价；本接口是 UI 官方批量改价入口，安全。

用法：
  python3 set_price.py --id <ttl_id> --rate 2.8 --add 39.26 --dry-run
  python3 set_price.py --id <ttl_id> --rate 2.8 --add 39.26 --save
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from erp_client import _post

BATCH_OPS = "/item/crossBorder/platformCollect/batchOps"
DETAIL = "/item/crossBorder/platformCollect/getDetail"


def snapshot(args):
    d = _post(DETAIL, {"id": args.id}, args)
    ttm = (d.get("reqInfo") or {}).get("tikTokItemModel") or {}
    v2 = ttm.get("productCreateRequestV2") or {}
    prices = [(s.get("price") or {}).get("amount") for s in (v2.get("skus") or [])]
    return {
        "prices": prices,
        "description_len": len(v2.get("description") or ""),
        "images": len(v2.get("main_images") or []),
        "skus": len(v2.get("skus") or []),
        "category": d.get("toCategoryNameTree"),
        "weight": v2.get("package_weight"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--rate", type=float, default=2.8)
    ap.add_argument("--add", type=float, default=39.26)
    ap.add_argument("--base", type=int, default=1, choices=[1, 2], help="1=当前售价 2=货源价")
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    before = snapshot(args)
    print("[set_price] 改价前:", json.dumps(before, ensure_ascii=False))
    if not before["skus"]:
        sys.exit("[set_price] 商品数据为空（itemModel 已被清空），请先删除并重新认领")

    formula = {
        "type": args.base,
        "multiplicationColumn": args.rate,
        "additionColumn": args.add,
        "reduceColumn": 0,
        "decimalPlacesType": 0,
        "tailAmount": None,
    }
    body = {
        "idList": [args.id],
        "platform": "tiktok",
        "opsType": 2,
        "priceType": 2,
        "priceFormula": formula,
        "price": None,
    }
    expect = [round(float(p) * args.rate + args.add, 2) for p in before["prices"]]
    print(f"[set_price] 公式: 现价 × {args.rate} + {args.add} → 预期 {expect}")
    if not args.save:
        print("[set_price] dry-run，未提交。确认后加 --save")
        print("  body:", json.dumps(body, ensure_ascii=False))
        return

    r = _post(BATCH_OPS, body, args)
    print(f"[set_price] 提交结果: {json.dumps(r, ensure_ascii=False)[:400]}")
    after = snapshot(args)
    print("[set_price] 改价后:", json.dumps(after, ensure_ascii=False))
    ok = (after["skus"] == before["skus"] and
          after["description_len"] == before["description_len"] and
          after["images"] == before["images"])
    print(f"[set_price] 数据完整性: {'OK（描述/主图/SKU 未丢失）' if ok else '⚠ 数据被清空，需删重建'}")
    if after["prices"] != before["prices"]:
        print("[set_price] 价格已变更 ✓")
    else:
        print("[set_price] ⚠ 价格未变化，检查返回")


if __name__ == "__main__":
    main()
