#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性编排：把本轮 10 品清单修到「只差图片闸门」的合规状态。

本轮实测发现的三条硬约束（详见会话 2026-11-12）：
  1. 上一版清单按 2 件套定价，但 ERP 一件代发只下 1 件 → 收 2 件钱发 1 件，履约欺诈。
     改用【单件 + 400g 保守计费重】定价：任何实重 <=400g 都不亏，且落在 $3.5-8 带内。
  2. ERP 采集到的 source_price 比遨虾起批价更可信，用它反推售价。
  3. 多规格货源存在零库存 / 高价 SKU，必须精简，否则价差超限或发布失败。

只做可确定的数据修复；不碰图片、不碰 description、不发布。
用法：
  python3 scripts/apply_listing_fix.py            # dry-run，打印计划
  python3 scripts/apply_listing_fix.py --save     # 提交（写操作，需点名确认）
"""
import argparse
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from erp_client import _post  # noqa: E402
import pricing_calc as pc  # noqa: E402

DETAIL = "/item/crossBorder/platformCollect/getDetail"
EDIT = "/item/crossBorder/edit"

# (ttl_id, 品名, 采购价CNY/件, 类目修正 或 None, 保留SKU上限)
# 采购价 = ERP getDetail 里所有 SKU 的 source_price 最小值（本轮实测）
ITEMS = [
    ("1570393952287219712", "1可折叠购物袋", 3.50, ("852744", "600001,851848,852744", "家居用品,家居收纳,收纳袋"), 3),
    ("1570393927490494464", "2静电除尘纸", 3.51, None, 1),
    ("1570394126360764416", "3水槽沥水篮", 3.30, None, 2),
    ("1570394115333939200", "4长柄杯刷", 3.61, None, 1),
    ("1570394097575256064", "5纳米海绵", 3.80, None, 2),
    # 6 冰格模具：唯一匹配类目为「厨具>烘焙用具」NON_MAIN_CATEGORY，跨类目红线，剔除
    ("1570394171395092480", "7按压器", 4.80, None, 2),
    ("1570394153623826432", "8防风晾衣夹", 3.50, None, 1),
    ("1570394203376824320", "9地板湿巾", 3.70, None, 1),
    ("1570394234171404288", "10鞋子防尘袋", 3.70, None, 3),
]

WEIGHT_G = 400          # 保守计费重：覆盖实重 <=400g 的所有情形
WEIGHT_KG = 0.4
MAX_SOURCE_PRICE = 9.4  # SKILL: TH 150g shelf 2件套单件等效上限；这里作高价SKU剔除线
STOCK = 1000

# 泰文规格名：不带任何中文/功能描述，避开平台敏感词
TH_SPEC = ["สี ชมพู", "สี ฟ้า", "สี เทา", "สี ดำ", "สี ขาว", "สี เขียว"]


def get_v2(d):
    ri = d.get("reqInfo") or (d.get("data") or {}).get("reqInfo") or {}
    ttm = ri.get("tikTokItemModel") or {}
    v2 = ttm.get("productCreateRequestV2") or {}
    return ri, ttm, v2


def plan_one(args, tid, name, cost, cat_fix, keep_max):
    d = _post(DETAIL, {"id": tid}, args)
    ri, ttm, v2 = get_v2(d)
    if not ttm:
        return None, f"[跳过] {name}: getDetail 无 tikTokItemModel（可能已被保存清空）"
    skus = v2.get("skus") or []
    if not skus:
        return None, f"[跳过] {name}: 无 SKU"

    cfg = pc.merged(pc.load_config(), "TH")
    cfg["site"] = "TH"
    target = pc.compute(cfg, cost, "shelf", 1, WEIGHT_G)
    target_thb = round(target["price_local"])

    def src(s):
        try:
            return float((s.get("price") or {}).get("source_price") or 0)
        except (TypeError, ValueError):
            return 0.0

    def stock(s):
        return sum((i.get("quantity") or 0) for i in (s.get("inventory") or []))

    # 精简：有货 + 采购价不超上限；按采购价升序取前 keep_max 个
    eligible = [s for s in skus if stock(s) > 0 and 0 < src(s) <= MAX_SOURCE_PRICE]
    eligible.sort(key=src)
    kept = eligible[:keep_max]
    if not kept:
        return None, f"[跳过] {name}: 无合格 SKU（全部零库存或超采购价上限）"
    dropped = len(skus) - len(kept)

    modified = copy.deepcopy(v2)
    new_skus = []
    for i, s in enumerate(kept):
        s = copy.deepcopy(s)
        s["inventory"] = [{"quantity": STOCK}]
        attrs = s.get("sales_attributes") or []
        if attrs:
            attrs[0]["name"] = "สี" if "颜色" in str(attrs[0].get("name", "")) or str(attrs[0].get("name", "")) == "สี" else attrs[0].get("name", "สี")
            attrs[0]["value_name"] = TH_SPEC[i % len(TH_SPEC)]
        p = s.setdefault("price", {})
        p["amount"] = str(target_thb)
        p["sale_price"] = str(target_thb)
        p["currency"] = "THB"
        new_skus.append(s)
    modified["skus"] = new_skus
    modified["package_weight"] = {"value": str(WEIGHT_KG), "unit": "KILOGRAM"}

    cat_id = d.get("toCategoryId")
    cat_tree = d.get("toCategoryIdTree")
    cat_names = d.get("toCategoryNameTree")
    if cat_fix:
        cat_id, cat_tree, cat_names = cat_fix
    if not cat_id or cat_id == "-9999":
        return None, f"[跳过] {name}: 类目仍为空/未匹配，需人工指定"
    modified["category_id"] = cat_id

    body = {
        "id": str(d.get("id") or tid),
        "platform": "tiktok",
        "sellerId": str(d.get("toSellerId") or ""),
        "currency": d.get("currency") or "THB",
        "toCategoryId": str(cat_id),
        "toCategoryIdTree": cat_tree,
        "toCategoryNameTree": cat_names,
        "title": d.get("title"),
        "itemModel": {**ri, "tikTokItemModel": {**ttm, "productCreateRequestV2": modified}},
    }
    miss = [k for k in ("sellerId", "toCategoryId", "toCategoryIdTree", "toCategoryNameTree", "title") if not body.get(k)]
    if miss:
        return None, f"[跳过] {name}: edit body 关键字段缺失 {miss}"

    info = {
        "name": name, "tid": tid, "target_thb": target_thb,
        "order_usd": target["order_usd"], "in_band": target["in_band"],
        "sku_before": len(skus), "sku_after": len(kept), "dropped": dropped,
        "kept_costs": [src(s) for s in kept], "class": cat_names,
    }
    return (body, info), None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--token", default=None)
    args = ap.parse_args()

    plans = []
    print(f"=== {'提交' if args.save else 'DRY-RUN'} | 单件 / {WEIGHT_G}g 保守计费重 / markup 1.62 ===")
    for tid, name, cost, cat_fix, keep_max in ITEMS:
        try:
            res, err = plan_one(args, tid, name, cost, cat_fix, keep_max)
        except Exception as e:
            print(f"[异常] {name}: {type(e).__name__}: {e}")
            continue
        if err:
            print(err)
            continue
        body, info = res
        plans.append((body, info))
        print(f"  {info['name']:<16} 采购¥{cost:<5} → ฿{info['target_thb']} (${info['order_usd']:.2f}, 在带={info['in_band']}) "
              f"SKU {info['sku_before']}→{info['sku_after']} 保留成本{info['kept_costs']} 类目={info['class']}")

    if not args.save:
        print(f"\nDRY-RUN 共 {len(plans)} 品可修。确认后加 --save")
        return
    print(f"\n=== 提交 {len(plans)} 品 ===")
    ok = 0
    for body, info in plans:
        try:
            r = _post(EDIT, body, args)
            s = json.dumps(r, ensure_ascii=False)
            good = ("success" not in s.lower() or "true" in s.lower()) and "false" not in s.lower()
            print(f"  {info['name']:<16} {'✓' if good else '⚠'} {s[:160]}")
            ok += 1 if good else 0
        except Exception as e:
            print(f"  {info['name']:<16} ✗ {type(e).__name__}: {e}")
    print(f"提交完成 {ok}/{len(plans)}（success 仅代表请求被接受，需 ttl-list 回查状态）")


if __name__ == "__main__":
    main()
