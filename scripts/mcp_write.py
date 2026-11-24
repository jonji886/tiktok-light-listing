#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mcp_write.py — MCP 取数结果瘦表落盘器（2026-11-25 P0-2）

治的痛：2026-11-24 批次里 Agent 把 MCP 返回的**完整 JSON 重新抄写**进文件
（24 次内联 python + 15 次 Write，~15% 全程耗时），且转录是造假高危点
（漏字段/抄错位=静默数据污染）。本脚本把转录量压到最小：Agent 只贴
**一行一个商品的 TSV 瘦表**（只含 read/apply 真正消费的字段），脚本生成
消费侧完全兼容的 JSON 落盘文件。

两种格式（与消费侧一一对应，字段名即 TSV 表头）：
  --kind search  → opportunity_to_sourcing.py --linkfox read 吃的
                   {errcode, products:[{offerId,title,price,consignPrice,
                    salesQuantity,quantityBegin,company,imageUrl,asinUrl,
                    sendGoodsAddressText,levelName,deliveryTime,salesOrderCount}]}
  --kind sku     → linkfox_oauth.py apply 吃的
                   {errcode, offerId, skuList:[{price,amountOnSale,
                    attributes:[{attributeName,value}],skuImageUrl,cargoNumber}]}
                   TSV 每行=一个SKU；attr 列用 `颜色=黑色|尺寸=45x100` 竖线分隔多属性。

真实性纪律：
  - TSV 内容必须来自**本轮 MCP 实际返回**，禁止凭记忆补写；缺列留空，脚本不猜。
  - 落盘文件带 `ingest` 溯源块（copied_at、来源任务、行数），read/apply 不受影响
    （它们只校验 errcode/products/skuList）。

用法：
  python3 scripts/mcp_write.py --kind search --save-to erp_data/linkfox/mcp/kw_xxx.json \
      --task "kw_洗衣机罩" --tsv -   # 从 stdin 读，或 --tsv <file>
  python3 scripts/mcp_write.py --kind sku --offer-id 728915971932 \
      --save-to erp_data/linkfox/mcp/sku_728915971932.json --tsv /tmp/skus.tsv

退出码：0 成功 / 2 表头或数据不合法（打印行号与原因，不写盘）
"""
import argparse
import csv
import io
import json
import os
import sys
import datetime

SEARCH_COLS = ["offerId", "title", "price", "consignPrice", "salesQuantity",
               "quantityBegin", "company", "imageUrl", "asinUrl",
               "sendGoodsAddressText", "levelName", "deliveryTime", "salesOrderCount"]
SKU_COLS = ["price", "amountOnSale", "attr", "skuImageUrl", "cargoNumber"]


def num_or_str(v):
    v = (v or "").strip()
    if v == "":
        return ""
    try:
        f = float(v)
        return int(f) if f == int(f) and "." not in v else f
    except ValueError:
        return v


def build_search(rows):
    products = []
    for i, r in enumerate(rows, 1):
        missing = [c for c in ("offerId", "title") if not (r.get(c) or "").strip()]
        if missing:
            sys.exit("[mcp_write] search 第%d行缺必填列 %s（禁止空offerId落盘）" % (i, missing))
        oid = r["offerId"].strip()
        if not oid.isdigit():
            sys.exit("[mcp_write] search 第%d行 offerId 非数字: %r" % (i, oid))
        p = {}
        for c in SEARCH_COLS:
            if c in r and str(r[c]).strip() != "":
                p[c] = num_or_str(r[c])
        products.append(p)
    return {"errcode": 200, "errmsg": "ok", "total": len(products), "products": products}


def build_sku(rows, offer_id):
    skus = []
    for i, r in enumerate(rows, 1):
        attrs = []
        for part in (r.get("attr") or "").split("|"):
            if not part.strip():
                continue
            name, _, val = part.partition("=")
            if not val:
                sys.exit("[mcp_write] sku 第%d行 attr 格式应为 名=值[|名=值]: %r" % (i, part))
            attrs.append({"attributeName": name.strip(), "value": val.strip()})
        if not attrs:
            sys.exit("[mcp_write] sku 第%d行 attr 为空（apply 依赖 attributeName 判汉字指纹）" % i)
        s = {"attributes": attrs}
        for c in ("price", "amountOnSale", "skuImageUrl", "cargoNumber"):
            v = str(r.get(c) or "").strip()
            if v:
                s[c] = num_or_str(v)
        skus.append(s)
    return {"errcode": 200, "errmsg": "ok", "offerId": str(offer_id), "skuList": skus}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["search", "sku"], required=True)
    ap.add_argument("--save-to", required=True)
    ap.add_argument("--tsv", default="-", help="TSV 文件路径或 - 读 stdin")
    ap.add_argument("--task", default="", help="溯源：任务名/关键词")
    ap.add_argument("--offer-id", default="", help="kind=sku 必填")
    ap.add_argument("--note", default="", help="溯源备注（如换词重试原因）")
    a = ap.parse_args()

    src = sys.stdin if a.tsv == "-" else open(a.tsv, encoding="utf-8")
    with src as f:
        text = f.read()
    rdr = csv.DictReader(io.StringIO(text), delimiter="\t")
    if not rdr.fieldnames:
        sys.exit("[mcp_write] TSV 无表头")
    want = SEARCH_COLS if a.kind == "search" else SKU_COLS
    unknown = [c for c in rdr.fieldnames if c and c not in want]
    if unknown:
        sys.exit("[mcp_write] 未知列 %s（合法列: %s）" % (unknown, want))
    rows = list(rdr)
    if not rows:
        sys.exit("[mcp_write] TSV 无数据行")

    if a.kind == "search":
        data = build_search(rows)
    else:
        if not a.offer_id.isdigit():
            sys.exit("[mcp_write] kind=sku 需要 --offer-id（数字）")
        data = build_sku(rows, a.offer_id)

    data["ingest"] = {"copied_at": datetime.datetime.now().isoformat(timespec="seconds"),
                      "task": a.task, "note": a.note, "rows": len(rows),
                      "tool": "mcp_write.py (agent 贴 MCP 原返回瘦表)"}
    os.makedirs(os.path.dirname(a.save_to), exist_ok=True)
    with open(a.save_to, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("[mcp_write] %s kind=%s 行数=%d → %s" % ("ok", a.kind, len(rows), a.save_to))
    return 0


if __name__ == "__main__":
    sys.exit(main())
