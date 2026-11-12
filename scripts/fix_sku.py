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
    d2, _, _, _, after = snapshot(args)
    print("[fix_sku] 修改后:", json.dumps(after, ensure_ascii=False))

    # 回读校验用「语义完整」而不是「长度相等」：图片闸门重建 description / 整图删后，
    # 描述长度与主图数本来就会变。旧断言把「不变」当正确，会把正常的重建误报为「被清空」，
    # 且长度相等既证明不了语言正确，也证明不了没被清空。见 SKILL.md Step 4.3 / 4.5。
    readable = bool(d2.get("reqInfo") or (d2.get("data") or {}).get("reqInfo"))
    ok = (after["skus"] == before["skus"] and after["prices"] == before["prices"])
    if not readable:
        print("[fix_sku] ⚠ 回读无 reqInfo（getDetail 只服务草稿态，保存后通道会关闭）——"
              "不能据此判定「被清空」，以发布日志 + 店铺后台为准")
    elif after["description_len"] == 0 or after["images"] == 0:
        ok = False
        print("[fix_sku] ⚠ 描述或主图为空，数据可能被写坏，需删除重认领")
    print(f"[fix_sku] 数据完整性: {'OK' if ok else '⚠ 有异常'}"
          "（判据：SKU 数/价格未缩水 + 描述与主图非空）")

    try:
        from image_gate import inspect_detail
        issues, stats = inspect_detail(d2)
        if not stats.get("readable"):
            print("[fix_sku] 图片闸门: ⚠ 回读不到 itemModel，无法据此宣称图片已验收")
        else:
            fails = [x for x in issues if x[0] == "FAIL"]
            for _, code, msg in fails:
                print(f"[fix_sku] 图片闸门 ✗ {code}: {msg}")
            print(f"[fix_sku] 图片闸门: {'✓ 通过' if not fails else '✗ 未通过 → 发布前先跑 image_gate.py apply / verify'}")
    except ImportError:
        print("[fix_sku] 图片闸门: ⚠ 缺 image_gate.py，跳过语义校验")

    changed = (after["stocks"] != before["stocks"] or
               after["spec_names"] != before["spec_names"])
    print(f"[fix_sku] SKU 修改生效: {'✓' if changed else '⚠ 未变化'}")


if __name__ == "__main__":
    main()
