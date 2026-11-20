#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""linkfox_oauth.py — 1688 OAuth sku 接口取数的本地加工器（2026-11-20 新增）。

定位：**纯本地加工器，不联网**（LinkFox 硬约束——网关调用只能经 WorkBuddy MCP proxy_call，
凭证由 WorkBuddy 注入）。吃「Agent 经 MCP `alibaba1688/sku` 取回后原样落盘的 JSON」，
把 SKU 级官方数据回填进 selection_intake 的 prefill.json，让详情页抓取降级为
「只补重量/主图/发货地」的单点操作，减少 baxia 风控触发。

数据性质：offerId → sku 回包 = 用户本人 1688 账号 OAuth 授权后的**官方接口数据**，
不是第三方快照（区别于 dld/imageSearch 的 THIRD_PARTY_ONLY）。price/amountOnSale 为实时值。

两个子命令（两段式，中间夹 Agent 的 MCP 取数）：
  scan  读 prefill.json → 列出需要 sku 补数的 offer（缺成本/最高SKU价/库存/MOQ 的行）
        → 出 erp_data/linkfox/oauth_tasks.json（toolName=alibaba1688/sku, save_to 定死）
  apply 读 erp_data/linkfox/mcp/sku_<oid>.json → 回填进 prefill.json（原文件就地更新，
        同时写 prefill.oauth_backup.json 防呆）

回填规则（缺才填、填必带来源、绝不覆盖详情页已有值）：
  min(price)      → cost_cny（仅当原值缺失）
  max(price)      → max_sku_cost_cny（仅当原值缺失）+ max_sku_source="oauth_sku"
  sum(amountOnSale) → stock（仅当原值缺失或 < 0）
  SKU 规格值正则  → dims_parsed（"长25宽18高14cm" 形态；解析不到留空，不猜）
  规格图去重      → sku_images[]（是规格图不是主图组，不混入 images，判断仍归多模态）
  规格维度/组合数 → sku_spec_count / sku_spec_names
重量：sku 接口**没有**独立重量字段（2026-11-20 实测确认）→ 不填、reason 里保留"需详情页/客服"。

用法：
  python3 scripts/linkfox_oauth.py scan --batch erp_data/selection_intake/prefill.json
  # → Agent 按 oauth_tasks.json 逐任务调 MCP proxy_call(alibaba1688/sku)，
  #   响应原样写 save_to；先决检查 authorizedStores 有 status=ACTIVE 且 expired!=true
  python3 scripts/linkfox_oauth.py apply --batch erp_data/selection_intake/prefill.json
退出码：0 成功 / 1 部分 offer 未落盘（已如实报告） / 2 输入问题
"""
import argparse
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_BATCH = os.path.join(ROOT, "erp_data", "selection_intake", "prefill.json")
TASKS_FILE = os.path.join(ROOT, "erp_data", "linkfox", "oauth_tasks.json")
MCP_DIR = os.path.join(ROOT, "erp_data", "linkfox", "mcp")

DIM_RE = re.compile(r"长\s*(\d+(?:\.\d+)?)\s*[宽×xX]\s*(\d+(?:\.\d+)?)\s*[高×xX]\s*(\d+(?:\.\d+)?)\s*cm")


def fnum(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def sku_payload_path(oid):
    return os.path.join(MCP_DIR, "sku_%s.json" % oid)


def load_sku(oid):
    """读落盘 sku JSON；返回 products 级 dict；问题抛 (原因) 由调用方如实记录。"""
    p = sku_payload_path(oid)
    if not os.path.exists(p):
        raise ValueError("落盘不存在")
    with open(p, encoding="utf-8") as f:
        try:
            j = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError("JSON 解析失败: %s" % e)
    if str(j.get("errcode")) not in ("200", "0"):
        raise ValueError("errcode=%s errmsg=%s" % (j.get("errcode"), j.get("errmsg")))
    if str(j.get("offerId")) != str(oid):
        raise ValueError("落盘 offerId=%s 与任务 %s 不符（禁止错配回填）" % (j.get("offerId"), oid))
    skus = j.get("skuList")
    if not isinstance(skus, list) or not skus:
        raise ValueError("skuList 为空")
    return skus


def need_sku(it):
    """该 item 是否需要 sku 补数：四个价格/库存字段任一缺失即需要。"""
    return any(it.get(k) in (None, "", []) for k in
               ("cost_cny", "max_sku_cost_cny", "stock", "min_order_qty"))


def cmd_scan(args):
    with open(args.batch, encoding="utf-8") as f:
        payload = json.load(f)
    items = payload.get("items") or []
    todo = []
    for it in items:
        oid = str(it.get("offer_id") or "")
        if not oid.isdigit():
            continue
        if args.force or need_sku(it):
            todo.append({"task_id": "sku_" + oid, "offerId": oid,
                         "toolName": "alibaba1688/sku",
                         "arguments": {"offerId": oid},
                         "save_to": sku_payload_path(oid)})
    os.makedirs(os.path.dirname(TASKS_FILE), exist_ok=True)
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(todo, f, ensure_ascii=False, indent=2)
    print("[scan] %d/%d 个 offer 需 sku 补数 → %s" % (len(todo), len(items), TASKS_FILE))
    print("[scan] Agent 侧前置：MCP 调 alibaba1688/authorizedStores，确认存在 "
          "status=ACTIVE 且 expired!=true 的账号（无则重新走 authorizeUrl，需带 accountName）")
    print("[scan] 逐任务调 proxy_call(toolName='alibaba1688/sku', arguments={\"offerId\":...})，"
          "响应原样写 save_to；全部落盘后跑 apply")
    return 0


def apply_one(it, skus):
    """把一个 sku 回包的派生值回填进 item（缺才填）。返回变更字段名列表。"""
    changed = []
    prices = [fnum(s.get("price")) for s in skus]
    prices = [p for p in prices if p is not None]
    stocks = [fnum(s.get("amountOnSale")) for s in skus]
    stocks = [v for v in stocks if v is not None]

    if prices and it.get("cost_cny") in (None, ""):
        it["cost_cny"] = min(prices); changed.append("cost_cny")
    if prices and it.get("max_sku_cost_cny") in (None, ""):
        it["max_sku_cost_cny"] = max(prices); changed.append("max_sku_cost_cny")
    # 价格三源裁决（2026-11-20 P2 固化，针线盒实测教训：详情页把 promotionPrice 误读成 1.0，
    # 与 OAuth 官方 5.9-7.9 差 6 倍，"缺才填"救不了"填了错值"）。规则：
    # OAuth 官方实时价恒优先——详情页/检索通道值与 OAuth 差 >30% 即视为误读，覆盖并留痕。
    # 依据：OAuth=用户账号官方接口回包（VERIFIED 级）；详情页正则解析历史误读率已有两例
    # （promotionPrice、区间价首档）。ERP source_price 11-19 起与 OAuth 恒一致，为旁证。
    if prices:
        old_cost, old_max = fnum(it.get("cost_cny")), fnum(it.get("max_sku_cost_cny"))
        o_min, o_max = min(prices), max(prices)
        for key, old, new in (("cost_cny", old_cost, o_min),
                              ("max_sku_cost_cny", old_max, o_max)):
            if old and old > 0 and abs(new - old) / max(old, new) > 0.30:
                it[key] = new
                it["price_adjudication"] = (
                    "%s 详情页/通道值 %g 与 OAuth 官方 %g 差>30%% → 取 OAuth（VERIFIED 级优先，"
                    "2026-11-20 规则）" % (key, old, new))
                changed.append(key + "(裁决覆盖)")
    if stocks and it.get("stock") in (None, ""):
        it["stock"] = int(sum(stocks)); changed.append("stock")
    # min_order_qty 不填——sku 回包没有起订量字段，缺就缺（select_gate 会报 MISSING），
    # 值应由检索通道 CSV（quantityBegin/begin_qty）带来；此处禁止猜 1。

    # 规格维度（颜色/尺寸属性名与组合数、规格图）——给多模态判定与 SKU 修名用，不覆盖主图组
    attr_names, imgs = [], []
    dims = None
    for s in skus:
        for a in (s.get("attributes") or []):
            nm = str(a.get("attributeName") or "").strip()
            if nm and nm not in attr_names:
                attr_names.append(nm)
            v = str(a.get("value") or "")
            m = DIM_RE.search(v)
            if m and not dims:
                dims = "%s×%s×%s" % (m.group(1), m.group(2), m.group(3))
        u = str(s.get("skuImageUrl") or "").strip()
        if u and u not in imgs:
            imgs.append(u)
    if dims and not it.get("dims_parsed"):
        it["dims_parsed"] = dims; changed.append("dims_parsed")
    if imgs:
        it["sku_images"] = imgs; changed.append("sku_images[%d]" % len(imgs))
    if attr_names:
        it["sku_spec_names"] = attr_names; changed.append("sku_spec_names")
    it["sku_spec_count"] = len(skus)
    if prices:
        it["max_sku_source"] = "oauth_sku"
    return changed


def cmd_apply(args):
    with open(args.batch, encoding="utf-8") as f:
        payload = json.load(f)
    items = payload.get("items") or []
    if not items:
        print("[apply] prefill 无 items，跳过", file=sys.stderr)
        return 2

    backup = args.batch.replace(".json", ".oauth_backup.json")
    if not os.path.exists(backup):
        shutil.copy2(args.batch, backup)

    ok, miss = 0, []
    for it in items:
        oid = str(it.get("offer_id") or "")
        if not oid.isdigit():
            continue
        try:
            skus = load_sku(oid)
        except ValueError as e:
            miss.append((oid, str(e)))
            continue
        changed = apply_one(it, skus)
        ok += 1
        print("[apply] %s ← sku 官方数据（回填: %s）" % (oid, ",".join(changed) or "无(已有值不覆盖)"))

    # source 字典补 oauth 说明，供 seed reason 消费
    payload.setdefault("source", {})["oauth_sku"] = (
        "alibaba1688/sku（用户 1688 账号 OAuth 官方接口，SKU 实时价/库存/规格，"
        "VERIFIED 级非快照；无重量/发货地字段）")
    with open(args.batch, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("[apply] %d 个 offer 已回填 → %s（备份 %s）" % (ok, args.batch, os.path.basename(backup)))
    if miss:
        print("[apply] ⚠ %d 个未落盘/不可读：%s" % (len(miss), "; ".join("%s:%s" % m for m in miss[:5])),
              file=sys.stderr)
        print("[apply] 按纪律不自动补位——按 oauth_tasks.json 补取后重跑 apply", file=sys.stderr)
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser(description="1688 OAuth sku 取数加工器（纯本地）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("scan", help="列出需 sku 补数的 offer，出 MCP 任务清单")
    p1.add_argument("--batch", default=DEFAULT_BATCH)
    p1.add_argument("--force", action="store_true", help="全部 offer 都出任务（不只缺字段的）")
    p2 = sub.add_parser("apply", help="读落盘 sku JSON 回填 prefill")
    p2.add_argument("--batch", default=DEFAULT_BATCH)
    a = ap.parse_args()
    return cmd_scan(a) if a.cmd == "scan" else cmd_apply(a)


if __name__ == "__main__":
    sys.exit(main())
