#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""上架负载本地审计：把 VN20 批次已踩过的坑固化成阻断规则。

覆盖：站点/货源标识、图片数量、越南语文本、SKU 单件化、库存/价格、
源 SKU 序号映射、重量与尺寸提醒。本脚本只读本地 JSON，不写 ERP；
本地通过不等于真实 ERP 详情已通过，认领后仍须重跑 prepublish_guard。

退出码：0 全部通过；1 有阻断项；2 输入文件问题。
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CJK = re.compile(r"[一-鿿]")
FORBIDDEN_SKU = ("配胶水", "免钉胶", "胶水", "两件装", "四件装", "2件套", "4件套")
VISIBLE_FIELDS = {"title_vn", "description_vn", "description_html", "name", "value_name"}
MAX_STOCK = 999_999


def as_items(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"]
    if isinstance(data, dict) and all(isinstance(v, dict) for v in data.values()):
        return list(data.values())
    return []


def plain_text(html_or_text):
    return re.sub(r"<[^>]+>", " ", str(html_or_text or ""))


def paragraphs(text):
    blocks = re.split(r"\n\s*\n|\n", str(text or ""))
    return [b.strip() for b in blocks if b.strip()]


def visible_cjk(obj, path="", hits=None):
    if hits is None:
        hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in VISIBLE_FIELDS or k in ("sales_attributes",):
                visible_cjk(v, f"{path}.{k}", hits)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            visible_cjk(v, f"{path}[{i}]", hits)
    elif isinstance(obj, str) and CJK.search(obj):
        hits.append((path, obj[:80]))
    return hits


def num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser(description="本地 listing payload 审计")
    ap.add_argument("--payload", required=True, help="payload JSON 路径")
    ap.add_argument("--region", default="VN")
    ap.add_argument("--min-images", type=int, default=3)
    ap.add_argument("--min-desc-chars", type=int, default=500)
    ap.add_argument("--min-paragraphs", type=int, default=2)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(a.payload):
        sys.stderr.write(f"[payload] 找不到 {a.payload}\n")
        return 2

    data = json.load(open(a.payload, encoding="utf-8"))
    items = as_items(data)
    if not items:
        sys.stderr.write("[payload] 未识别到商品列表\n")
        return 2

    results = []
    for item in items:
        offer = str(item.get("offer_id") or "").strip()
        issues, warns = [], []

        if not offer:
            issues.append("OFFER_MISSING: offer_id 为空")
        region = str(item.get("region") or "").strip().upper()
        if region != a.region.upper():
            issues.append(f"REGION_MISMATCH: region={region!r}，目标站点={a.region.upper()}")

        images = item.get("images") or []
        if len(images) < a.min_images:
            issues.append(f"IMAGE_TOO_FEW: 公网图 {len(images)} 张 < {a.min_images} 张；除主图外必须有其他展示图")
        if item.get("image_count") is not None and int(item["image_count"]) != len(images):
            issues.append(f"IMAGE_COUNT_MISMATCH: image_count={item['image_count']}，实际 {len(images)}")
        for u in images:
            if not str(u).startswith(("http://", "https://")):
                issues.append(f"IMAGE_URL_INVALID: {u!r}")

        title = str(item.get("title_vn") or "")
        desc = str(item.get("description_vn") or "")
        blocks = paragraphs(desc)
        plain = plain_text(desc)
        if len(plain) < a.min_desc_chars:
            issues.append(f"DESC_TOO_SHORT: 纯文本 {len(plain)} 字符 < {a.min_desc_chars}")
        if len(blocks) < a.min_paragraphs:
            issues.append(f"DESC_TOO_FEW_PARAGRAPHS: {len(blocks)} 段 < {a.min_paragraphs}")
        if not title.strip():
            issues.append("TITLE_MISSING: title_vn 为空")

        for path, text in visible_cjk(item):
            issues.append(f"VISIBLE_CJK: {path}={text}")

        skus = item.get("skus") or []
        if not skus:
            issues.append("SKU_EMPTY: 无保留 SKU")
        indexes = []
        for i, sku in enumerate(skus):
            stock = num(sku.get("stock"))
            price = num(sku.get("price_vnd")) or num(sku.get("price_usd"))
            cost = num(sku.get("cost_cny"))
            if stock is None or stock < 0 or stock > MAX_STOCK:
                issues.append(f"SKU[{i}] STOCK_INVALID: stock={sku.get('stock')}")
            if price is None or price <= 0:
                issues.append(f"SKU[{i}] PRICE_INVALID: price={sku.get('price_vnd')}")
            if cost is None or cost <= 0:
                issues.append(f"SKU[{i}] COST_INVALID: cost={sku.get('cost_cny')}")
            idx = sku.get("source_sku_index")
            if idx is None:
                issues.append(f"SKU[{i}] SOURCE_INDEX_MISSING")
            else:
                indexes.append(idx)
            attr_values = " ".join(str(x.get("value_name", "")) for x in sku.get("sales_attributes", []))
            for bad in FORBIDDEN_SKU:
                if bad in attr_values:
                    issues.append(f"SKU[{i}] FORBIDDEN_SPEC: {bad}")

        total = item.get("source_sku_total")
        if total is not None:
            for idx in indexes:
                if idx < 1 or idx > int(total):
                    issues.append(f"SOURCE_INDEX_OUT_OF_RANGE: {idx} 不在 1..{total}")
        if len(indexes) != len(set(indexes)):
            issues.append("SOURCE_INDEX_DUPLICATED: 同品 source_sku_index 重复")

        weight = item.get("package_weight") or {}
        w = num(weight.get("value")) if isinstance(weight, dict) else None
        if w is None or w <= 0:
            issues.append("WEIGHT_INVALID: 重量为空或 <=0")
        dims = item.get("package_dimensions") or {}
        if not dims:
            warns.append("DIM_MISSING: 包装尺寸为空；能从 1688 核实就补，不要沿用旧批次占位尺寸")

        results.append({"offer_id": offer, "issues": issues, "warns": warns})

    blocked = [r for r in results if r["issues"]]
    warned = [r for r in results if r["warns"] and not r["issues"]]

    if a.json:
        print(json.dumps({"total": len(items), "blocked": len(blocked),
                          "results": results}, ensure_ascii=False, indent=2))
    else:
        print("=== listing_payload_audit（本地只读负载审计）===")
        print(f"[payload] 共 {len(items)} 条 | 阻断 {len(blocked)} | 仅提醒 {len(warned)}\n")
        for r in results:
            mark = "✗" if r["issues"] else ("!" if r["warns"] else "✓")
            print(f"{mark} {r['offer_id']}")
            for x in r["issues"]:
                print(f"   ✗ {x}")
            for x in r["warns"]:
                print(f"   ! {x}")
        print()
        if blocked:
            print("[payload] ❌ 存在阻断项；修复本地负载后再进入 ERP 认领/编辑。")
        else:
            print("[payload] ✅ 本地规则通过；ERP 真实详情上仍须重跑图片/价格/发布前闸门。")

    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
