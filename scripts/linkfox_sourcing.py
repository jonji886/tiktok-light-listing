#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LinkFox 选品取数加工器（2026-11-18 新增；2026-11-20 范围收窄）

定位：**纯本地加工器，不联网**。LinkFox 硬约束——所有网关调用只能经 WorkBuddy 的
MCP proxy_call（凭证由 WorkBuddy 注入），禁止脚本用 curl/urllib/requests 直连。
所以本脚本吃的是「Agent 通过 MCP 取回后落盘的 JSON」，做的是人不想手算的预筛/归并。

⛔ 范围收窄（2026-11-20）：TikTok 需求数据统一走 ERP 透传（scripts/kdzs_fastmoss.py），
   LinkFox 的 fastmoss/echotik 直连已停用。本脚本只保留 1688 找货两个子命令：
  1. screen      找货预筛：1688 供应链硬门槛（代发/48h/起订量1/体验分）从人工抄改为机器筛
  2. same-source 同款换发货地：以图搜图结果聚同款、标记不同供应商（发货地字段多为空 → 不臆断城市）
  spec-terms（EchoTik 规格名）代码留档但运行时硬停，详见 cmd_spec_terms。

数据来源与真实性纪律：
  - 输入必须是 MCP 原样落盘的 JSON dld/productSearch / alibaba1688/imageSearch
    响应（顶层含 products[] 或 data[]，errcode:200）。
  - 数据均为第三方快照（THIRD_PARTY_ONLY），不是 1688/TikTok 官方后台；字段缺失=UNKNOWN，
    **绝不推测为 0 或满足条件**。发货地 sendGoodsAddressText 实测多为空，脚本一律留空标注。
  - 只筛候选、不写 ERP、不认定货源；产出必须仍过 select_gate.py 才算可采集。

用法（先由 Agent 用 MCP 取数落盘，再跑本脚本）：
  # screen：找货预筛
  python3 scripts/linkfox_sourcing.py screen \
      --in erp_data/linkfox/dld_真空压缩袋.json --out erp_data/linkfox/screened.csv
  # same-source：以图搜图聚同款、跨供应商
  python3 scripts/linkfox_sourcing.py same-source \
      --in erp_data/linkfox/image_1069717571224.json --out erp_data/linkfox/same_source.csv
  # spec-terms：⛔ 2026-11-20 停用，调用直接退出 2（代码留档）

MCP 取数参数备忘（Agent 侧执行，不写进脚本）：
  dld/productSearch      {"keyWord":"真空压缩袋","pageIndex":1,"pageSize":20}
  alibaba1688/imageSearch{"imageUrl":"<主图URL>","page":1,"pageSize":20}
退出码：0 成功 / 1 部分跳过（详见 stdout）/ 2 输入或参数问题
"""
import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_DIR = os.path.join(ROOT, "erp_data", "linkfox")

# 供应链硬门槛（与 config/selection.yaml supplier_gates 一致，勿在此放宽）
GATES = {
    "require_dropship": True,     # quantityBegin==1 视为无起订量；isOnePsale==true 视为支持代发
    "max_delivery_hours": 48,     # deliveryTime 是小时字符串，如 "48"；空 = UNKNOWN 不通过也不剔除
    "min_composite_service": 4.0, # 综合服务体验分下限（imageSearch 才有；dld 无此字段 → UNKNOWN）
    "min_offer_experience": 3.5,  # 商品体验分下限
}


def load_payload(path):
    """读 MCP 落盘 JSON，返回 (products, meta)。校验 errcode，不猜字段。"""
    if not os.path.exists(path):
        sys.exit("[linkfox] 输入不存在: %s" % path)
    with open(path, encoding="utf-8") as f:
        try:
            j = json.load(f)
        except json.JSONDecodeError as e:
            sys.exit("[linkfox] JSON 解析失败 %s: %s" % (path, e))
    # MCP 有时会包一层 structured content，取第一层 errcode
    if "errcode" in j and str(j.get("errcode")) not in ("200", "0"):
        sys.exit("[linkfox] 下游错误 errcode=%s errmsg=%s" % (j.get("errcode"), j.get("errmsg")))
    products = j.get("products")
    if products is None:
        d = j.get("data")
        if isinstance(d, list):
            products = [x for x in d if isinstance(x, dict) and ("products" not in x)]
        elif isinstance(d, dict):
            products = d.get("products") or d.get("list") or []
    if not isinstance(products, list):
        sys.exit("[linkfox] 未找到 products[]/data[]，顶层键=%s" % list(j.keys())[:12])
    return products, j


def as_num(v, default=None):
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return default


def norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def write_csv(path, rows, fields):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


# ────────────────────────── screen：找货预筛 ──────────────────────────
SCREEN_FIELDS = [
    "offerId", "title", "company", "consignPrice", "price", "deliveryTime",
    "quantityBegin", "isOnePsale", "compositeServiceScore", "offerExperienceScore",
    "salesQuantity", "repurchaseRate", "levelName", "asinUrl", "imageUrl",
    "ship_city", "screen_pass", "screen_reason", "source", "source_tool",
]


def cmd_screen(args):
    products, meta = load_payload(args.in_file)
    out, skipped = [], 0
    for p in products:
        reason = []
        qty = as_num(p.get("quantityBegin"))
        dts = norm(p.get("deliveryTime"))
        one = p.get("isOnePsale")
        comp = as_num(p.get("compositeServiceScore"))
        offer = as_num(p.get("offerExperienceScore"))

        # 起订量：必须 ==1（"一件代发"但满 N 件才发是隐形门槛）
        if qty is None:
            reason.append("起订量UNKNOWN")
        elif qty > 1:
            reason.append("起订量>%d" % int(qty))
        # 48h：只有明确 ≤48 才算通过；空=UNKNOWN（不臆断为达标）
        dts_h = as_num(dts)
        if dts_h is None:
            reason.append("发货时效UNKNOWN")
        elif dts_h > GATES["max_delivery_hours"]:
            reason.append("发货>%dh" % GATES["max_delivery_hours"])
        # 一件代发：imageSearch 有 isOnePsale；dld 无此字段 → UNKNOWN
        if one is False or (isinstance(one, str) and one.strip().lower() in ("false", "0", "no")):
            reason.append("不支持一件代发")
        # 体验分：imageSearch 有；dld 无 → UNKNOWN（不据此剔除）
        if comp is not None and comp < GATES["min_composite_service"]:
            reason.append("综合服务分<%.1f" % GATES["min_composite_service"])
        if offer is not None and offer < GATES["min_offer_experience"]:
            reason.append("商品体验分<%.1f" % GATES["min_offer_experience"])

        hard_fail = any(("UNKNOWN" not in r) for r in reason)
        ship = norm(p.get("sendGoodsAddressText"))  # 实测多为空，空就留空，不猜城市
        if not ship:
            reason.append("发货地UNKNOWN(需人工核)")
        out.append({
            "offerId": p.get("offerId") or p.get("productCode") or "",
            "title": norm(p.get("title")),
            "company": norm(p.get("company")),
            "consignPrice": p.get("consignPrice") if p.get("consignPrice") is not None else "",
            "price": p.get("price") if p.get("price") is not None else "",
            "deliveryTime": dts,
            "quantityBegin": "" if qty is None else int(qty),
            "isOnePsale": "" if one is None else one,
            "compositeServiceScore": "" if comp is None else comp,
            "offerExperienceScore": "" if offer is None else offer,
            "salesQuantity": p.get("salesQuantity") if p.get("salesQuantity") is not None else "",
            "repurchaseRate": norm(p.get("repurchaseRate")),
            "levelName": norm(p.get("levelName")),
            "asinUrl": norm(p.get("asinUrl") or p.get("productUrl")),
            "imageUrl": norm(p.get("imageUrl")),
            "ship_city": ship,
            "screen_pass": "否" if hard_fail else ("待核" if reason else "是"),
            "screen_reason": "；".join(reason),
            "source": norm(p.get("sourceType")),
            "source_tool": norm(p.get("sourceTool")),
        })
        if hard_fail:
            skipped += 1
    write_csv(args.out_file, out, SCREEN_FIELDS)
    print("[screen] 输入 %d 条 → 输出 %d 条；硬门槛剔除 %d 条（理由见 screen_reason）"
          % (len(products), len(out), skipped))
    print("[screen] 提醒：数据为第三方快照(THIRD_PARTY_ONLY)；发货地/书面48h证明仍需人工核实，"
          "产出须过 select_gate.py 才算可采集。")
    return 0


# ────────────────────────── same-source：同款跨供应商 ──────────────────────────
SAME_FIELDS = [
    "group", "offerId", "title", "company", "shopId", "consignPrice", "price",
    "salesQuantity", "isOnePsale", "ship_city", "productUrl", "imageUrl",
]


def cmd_same_source(args):
    products, meta = load_payload(args.in_file)

    # imageSearch 实测**不含 company/shopId/sendGoodsAddressText** → 需要 dld/productSearch
    # 按 offerId 补齐公司/发货地。补不到就标 UNKNOWN，绝不臆断。
    enrich = {}
    if getattr(args, "enrich", None):
        dld_products, _ = load_payload(args.enrich)
        for p in dld_products:
            oid = str(p.get("offerId") or p.get("productCode") or "")
            if oid:
                enrich[oid] = p

    # 同款判据：标题归一化的前 6 个词块作为粗聚类键（以图搜图结果天然是相似款）
    # 不臆断"完全同款"——只给聚类候选，人工目视确认。
    def key_of(p):
        t = norm(p.get("title"))
        t = re.sub(r"[【】\[\]（）()·\-_—+,，。/\\|]", " ", t)
        return " ".join(t.split()[:6]) or "(无标题)"

    groups = defaultdict(list)
    for p in products:
        groups[key_of(p)].append(p)

    rows, multi, unknown_supplier = [], 0, 0
    for gi, (k, items) in enumerate(sorted(groups.items(), key=lambda x: -len(x[1])), 1):
        suppliers = set()
        for p in items:
            oid = str(p.get("offerId") or p.get("productCode") or "")
            src = enrich.get(oid, p)  # 优先用 dld 补齐的字段
            company = norm(src.get("company"))
            ship = norm(src.get("sendGoodsAddressText"))
            if company:
                suppliers.add(company)
            else:
                unknown_supplier += 1
            rows.append({
                "group": "G%02d" % gi,
                "offerId": oid,
                "title": norm(p.get("title")),
                "company": company or "UNKNOWN",
                "shopId": norm(src.get("shopId")) or "UNKNOWN",
                "consignPrice": p.get("consignPrice") if p.get("consignPrice") is not None else "",
                "price": p.get("price") if p.get("price") is not None else "",
                "salesQuantity": p.get("salesQuantity") if p.get("salesQuantity") is not None else "",
                "isOnePsale": p.get("isOnePsale") if p.get("isOnePsale") is not None else "",
                "ship_city": ship,  # 空则留空，不推断城市
                "productUrl": norm(p.get("productUrl") or p.get("asinUrl")),
                "imageUrl": norm(p.get("imageUrl")),
            })
        if len(suppliers) > 1:
            multi += 1
    write_csv(args.out_file, rows, SAME_FIELDS)
    print("[same-source] 输入 %d 条 → %d 个同款簇，其中 %d 簇含 ≥2 家供应商（按已知公司名）"
          % (len(products), len(groups), multi))
    if unknown_supplier:
        print("[same-source] ⚠️ %d 条公司名 UNKNOWN：imageSearch 不返回 company，且未提供 --enrich "
              "或 dld 未覆盖该 offerId；请用 dld/productSearch 按 offerId 补齐。" % unknown_supplier)
    print("[same-source] ⚠️ 发货地字段(sendGoodsAddressText)实测多为空 → 本脚本**不推断城市**；"
          "换发货地仍需人工去 1688 页面核对城市 + 算到货代仓快递天数。")
    return 0


# ────────────────────────── spec-terms：已停用（代码留档） ──────────────────────────
SPEC_FIELDS = ["site", "kind", "prop_name", "prop_value", "product_id", "product_name", "source"]


def cmd_spec_terms(args):
    # 2026-11-20：TikTok 数据统一走 ERP 透传，EchoTik 快照钩子切断。
    # 不删除实现是为了留档可追溯；但任何调用都硬停，防止误用混源数据。
    sys.stderr.write(
        "[spec-terms] ⛔ 已于 2026-11-20 停用：TikTok 需求数据统一走 ERP 透传"
        "（scripts/kdzs_fastmoss.py）。EchoTik 为第三方快照且与 ERP 口径不一致"
        "（同款销量两源差近一倍），禁止作为评分/词表输入。\n"
    )
    return 2
    products, meta = load_payload(args.in_file)  # noqa: F821 - 留档死代码，不可达
    rows = []
    seen = set()
    for p in products:
        pid = str(p.get("productId") or "")
        pname = norm(p.get("productName"))
        props = p.get("salePropsInfo") or []
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except json.JSONDecodeError:
                props = []
        for prop in props:
            if not isinstance(prop, dict):
                continue
            pprop = norm(prop.get("propName"))
            # 属性名（如 Kích thước / Quy cách）—— 当地语术语候选
            key_a = (args.site.upper(), "attr", pprop)
            if pprop and key_a not in seen:
                seen.add(key_a)
                rows.append({
                    "site": args.site.upper(), "kind": "attr", "prop_name": pprop,
                    "prop_value": "", "product_id": pid, "product_name": pname[:80],
                    "source": "echotik/salePropsInfo",
                })
            # 规格值（如 -Bơm tay / Kích thước lớn）—— 当地语用词候选
            for v in prop.get("salePropValues") or []:
                val = norm(v.get("propValue")) if isinstance(v, dict) else norm(v)
                if not val:
                    continue
                key_v = (args.site.upper(), "spec", pprop, val)
                if key_v in seen:
                    continue
                seen.add(key_v)
                rows.append({
                    "site": args.site.upper(), "kind": "spec", "prop_name": pprop,
                    "prop_value": val, "product_id": pid, "product_name": pname[:80],
                    "source": "echotik/salePropsInfo",
                })
    write_csv(args.out_file, rows, SPEC_FIELDS)
    n_attr = sum(1 for r in rows if r["kind"] == "attr")
    print("[spec-terms] 输入 %d 商品 → 去重后 %d 条（属性名 %d / 规格值 %d）"
          % (len(products), len(rows), n_attr, len(rows) - n_attr))
    print("[spec-terms] ⚠️ 这些是**当地语原词**，无中文对应，只作译名表的人工用词参考，"
          "不得当作 src(中文)→tgt 直接入库。请人工核对后才增量 config/i18n/%s.csv"
          % ("th" if args.site.upper() == "TH" else "vi"))
    return 0


def main():
    ap = argparse.ArgumentParser(description="LinkFox MCP 取数结果加工器（纯本地，不联网）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("screen", help="1688 供应链硬门槛预筛")
    p1.add_argument("--in", dest="in_file", required=True, help="dld/productSearch 或 imageSearch 的 MCP 落盘 JSON")
    p1.add_argument("--out", dest="out_file", default=os.path.join(DEFAULT_DIR, "screened.csv"))
    p1.set_defaults(func=cmd_screen)

    p2 = sub.add_parser("same-source", help="以图搜图结果聚同款、标记跨供应商")
    p2.add_argument("--in", dest="in_file", required=True, help="alibaba1688/imageSearch 的 MCP 落盘 JSON")
    p2.add_argument("--enrich", dest="enrich", default="",
                    help="可选：dld/productSearch 落盘 JSON，按 offerId 补公司/发货地")
    p2.add_argument("--out", dest="out_file", default=os.path.join(DEFAULT_DIR, "same_source.csv"))
    p2.set_defaults(func=cmd_same_source)

    p3 = sub.add_parser("spec-terms", help="⛔ 2026-11-20 停用（EchoTik 规格名，TikTok 数据走 ERP 透传）")
    p3.add_argument("--in", dest="in_file", required=True, help="echotik/batchProductDetail 的 MCP 落盘 JSON")
    p3.add_argument("--site", choices=["MY", "TH", "VN"], required=True)
    p3.add_argument("--out", dest="out_file", default=os.path.join(DEFAULT_DIR, "spec_terms.csv"))
    p3.set_defaults(func=cmd_spec_terms)

    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
