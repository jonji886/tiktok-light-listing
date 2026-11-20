#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bailian_sourcing.py — 百炼「1688智能选品」官方 MCP 客户端（2026-11-20 新增）。

数据源定位（VERIFIED 2026-11-20）：
  提供方 = 阿里巴巴(中国)网络技术有限公司（1688 官方，非第三方快照）；
  端点 = 百炼 One Key MCP（Streamable HTTP，Bearer DASHSCOPE_API_KEY）；
  计费 = ¥0.058/次固定单价 + 免费体验包 1000 credits（≈172 次）。
  与 LinkFox 的关键差别：**凭证在 skill .env，脚本可直连**（LinkFox 硬约束只能经 WorkBuddy MCP）。

工具只有 1 个 find_product：query 文本搜 / imageUrl 图搜同款 /（ID 同款路径未验证，勿用 query 传 ID——
实测 query=商品ID 会走文本召回返回无关品，score≈0.23）。

返回的官方决策标签（本 skill 闸门要吃的）：
  serviceTags.is_late_ship_compensate  晚发必赔 Y/N（平台级赔付承诺 = 48h 证据链的机验档）
  serviceTags.lgt_3m_24h_avg           24h 揽收率（⚠ 0.0 与"无数据"不可区分 → 一律按 UNKNOWN 处理，
                                       不得判 FAIL，实测 30 天零成交新品必为 0.0）
  serviceTags.is_has_inventory         有库存 Y/N
  oldReputationTags.pay_ord_cnt_30d    30 天成交单数（month_sold 列的 bailian 口径，≠ linkfox/aibuy）
  oldReputationTags.rebuy_cnt_30d      30 天复购
  merchantReputationTags.repeated_rate 店铺复购率 / shop_year_count 开店年数
  storeAmount / quantityBegin / currentPrice / detailUrl / imageUrl(单图)
  没有：发货地城市、重量尺寸、多图（→ 城市仍靠 linkfox/详情页，图仍靠详情页）。

纪律：
- 低频（内置 ≥1.2s 间隔），失败如实报（401=凭证失效 / 404"开通中"=等部署 / 业务错误透传），不重试风暴。
- 输出 CSV 的 month_sold 列在 data_source=bailian 行含义是 pay_ord_cnt_30d，**禁止与 linkfox/aibuy 混排**。

用法：
  python3 scripts/bailian_sourcing.py search --keyword "围裙 厨房 多口袋" [--pages-ignored] [--top 20] --out erp_data/bailian
  python3 scripts/bailian_sourcing.py search-image --image-url <URL> [--score-level high] --out erp_data/bailian
  python3 scripts/bailian_sourcing.py probe        # 连通性自检（1 次最小调用）
退出码：0 成功 / 1 业务失败 / 2 参数或凭证问题
"""
import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ENDPOINT = ("https://llm-vetju8rgd6dmsi4o.cn-beijing.maas.aliyuncs.com"
            "/api/v1/mcps/market-cmgjmcp00075018/mcp")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENV_FILE = os.path.join(ROOT, ".env")
MIN_INTERVAL = 1.2
_last_call = [0.0]

CSV_COLS = ["offerId", "标题", "现价", "30天成交单数", "30天复购", "起批量", "库存",
            "晚发必赔", "24h揽收率", "有库存", "店铺复购率", "开店年数", "供应商",
            "严选指数", "相关性分", "召回源", "标准offer链接", "图片"]


def load_key():
    if not os.path.exists(ENV_FILE):
        sys.exit("[bailian] 缺 %s（DASHSCOPE_API_KEY）" % ENV_FILE)
    for raw in open(ENV_FILE, encoding="utf-8"):
        m = re.match(r"^DASHSCOPE_API_KEY=(\S+)\s*$", raw)
        if m:
            return m.group(1)
    sys.exit("[bailian] .env 里没有 DASHSCOPE_API_KEY")


def _rpc(payload, key, timeout=60):
    gap = time.time() - _last_call[0]
    if gap < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - gap)
    _last_call[0] = time.time()
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 "Authorization": "Bearer " + key},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        if e.code == 401:
            sys.exit("[bailian] 401 凭证失效：检查 .env DASHSCOPE_API_KEY")
        if e.code == 404 and "开通中" in detail:
            sys.exit("[bailian] 服务开通中/被停用，稍后重试（勿循环打）")
        sys.exit("[bailian] HTTP %d: %s" % (e.code, detail))
    # streamable http 可能回 SSE：取最后一个 data: 行
    datas = re.findall(r"^data: (.*)$", body, re.M)
    if datas:
        body = datas[-1]
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        sys.exit("[bailian] 响应非 JSON: %s" % body[:200])


def find_product(key, arguments):
    j = _rpc({"jsonrpc": "2.0", "id": int(time.time()), "method": "tools/call",
              "params": {"name": "find_product", "arguments": arguments}}, key)
    if "error" in j:
        sys.exit("[bailian] rpc error: %s" % json.dumps(j["error"], ensure_ascii=False)[:200])
    r = j.get("result") or {}
    if r.get("isError"):
        sys.exit("[bailian] tool error: %s" % str(r)[:300])
    txt = (r.get("content") or [{}])[0].get("text", "[]")
    try:
        items = json.loads(txt)
    except json.JSONDecodeError:
        sys.exit("[bailian] 工具返回非 JSON 列表: %s" % txt[:200])
    if not isinstance(items, list):
        sys.exit("[bailian] 返回结构异常(非 list): %s" % str(items)[:200])
    return items


def to_rows(items):
    rows = []
    for it in items:
        if not isinstance(it, dict):
            continue
        svc = it.get("serviceTags") or {}
        old = it.get("oldReputationTags") or {}
        mer = it.get("merchantReputationTags") or {}
        rate = svc.get("lgt_3m_24h_avg")
        rows.append({
            "offerId": str(it.get("itemId") or ""),
            "标题": (it.get("title") or "").strip(),
            "现价": it.get("currentPrice") if it.get("currentPrice") is not None else "",
            "30天成交单数": old.get("pay_ord_cnt_30d") or "",
            "30天复购": old.get("rebuy_cnt_30d") or "",
            "起批量": it.get("quantityBegin") if it.get("quantityBegin") is not None else "",
            "库存": it.get("storeAmount") if it.get("storeAmount") is not None else "",
            "晚发必赔": svc.get("is_late_ship_compensate") or "",
            # 0.0 保留原值但消费侧按 UNKNOWN 处理（与无数据不可区分，实测结论）
            "24h揽收率": "" if rate in (None, "") else rate,
            "有库存": svc.get("is_has_inventory") or "",
            "店铺复购率": mer.get("repeated_rate") or "",
            "开店年数": mer.get("shop_year_count") or "",
            "供应商": (it.get("company") or "").strip(),
            "严选指数": it.get("yxIndex") if it.get("yxIndex") is not None else "",
            "相关性分": it.get("score") if it.get("score") is not None else "",
            "召回源": it.get("recallSource") or "",
            "标准offer链接": it.get("detailUrl") or "",
            "图片": it.get("imageUrl") or "",
        })
    return [r for r in rows if r["offerId"].isdigit()]


def save(items, out_dir, prefix, tag):
    os.makedirs(out_dir, exist_ok=True)
    rows = to_rows(items)
    jpath = os.path.join(out_dir, "%s_%s.json" % (prefix, tag))
    cpath = os.path.join(out_dir, "%s_%s.csv" % (prefix, tag))
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    with open(cpath, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print("saved %s -> %s / %s" % (tag, jpath, cpath))
    return rows


def main():
    ap = argparse.ArgumentParser(description="百炼 1688 官方选品 MCP 客户端")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("search", help="中文关键词文本搜")
    p1.add_argument("--keyword", required=True)
    p1.add_argument("--sort", default="", choices=["", "price_asc", "price_desc", "sold_desc", "yx_desc"])
    p1.add_argument("--top", type=int, default=20)
    p1.add_argument("--out", default=os.path.join(ROOT, "erp_data", "bailian"))
    p2 = sub.add_parser("search-image", help="图片URL找同款（scoreLevel=high 预筛相关性）")
    p2.add_argument("--image-url", required=True)
    p2.add_argument("--score-level", default="high", choices=["high", "medium", "low", ""])
    p2.add_argument("--sort", default="")
    p2.add_argument("--top", type=int, default=20)
    p2.add_argument("--out", default=os.path.join(ROOT, "erp_data", "bailian"))
    p3 = sub.add_parser("probe", help="连通性自检（1 次最小调用）")
    a = ap.parse_args()
    key = load_key()

    if a.cmd == "probe":
        items = find_product(key, {"query": "收纳盒", "pageSize": 1})
        print("[probe] OK，返回 %d 条；晚发必赔字段样例=%s"
              % (len(items), (items[0].get("serviceTags") or {}).get("is_late_ship_compensate") if items else "-"))
        return 0

    if a.cmd == "search":
        args = {"query": a.keyword, "pageSize": a.top}
        if a.sort:
            args["sortType"] = a.sort
        tag = "kw_" + re.sub(r"[^\w\u4e00-\u9fff-]+", "_", a.keyword)[:40]
    else:
        args = {"imageUrl": a.image_url, "pageSize": a.top}
        if a.score_level:
            args["scoreLevel"] = a.score_level
        if a.sort:
            args["sortType"] = a.sort
        tag = "img_" + re.sub(r"[^\w]+", "_", os.path.basename(a.image_url.split("?")[0]))[:40]

    items = find_product(key, args)
    rows = save(items, a.out, "bailian" if a.cmd == "search" else "bailian_img", tag)
    n_comp = sum(1 for r in rows if r["晚发必赔"] == "Y")
    print("[bailian] %d 条（晚发必赔=Y %d 条；揽收率0.0/空 %d 条按 UNKNOWN 用，勿判 FAIL）"
          % (len(rows), n_comp, sum(1 for r in rows if str(r["24h揽收率"]) in ("", "0.0", "0"))))
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
