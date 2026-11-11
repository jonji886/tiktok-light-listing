#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
快递助手ERP跨境版 · TikTok 选品数据源（免登录）

数据来源：<your-erp-host> 内部接口，后端直连 FastMoss（见前端模块 2174）。
关键结论（2026-11-11 实测）：
  - /item/fastmoss/category/list  和  /item/fastmoss/product/search  无需 token，可直接调用
  - 采集进采集箱 / 铺货 需要 qnquerystring（见 scripts/erp_client.py），本脚本不涉及

用法：
  export ERP_BASE_URL=https://your-erp-host.example.com
  python3 kdzs_fastmoss.py --region TH --usd-min 2 --usd-max 8 --top 40
  python3 kdzs_fastmoss.py --region VN --order creator_count --out vn.csv
  python3 kdzs_fastmoss.py --categories          # 打印 FastMoss 类目树

字段说明（来自接口返回）：
  floorPrice        最低价（本地货币数值）
  day7UnitsSold     近7天销量
  day28UnitsSold    近28天销量
  day90UnitsSold    近90天销量
  videoCount        带货视频数      <- 判断内容路径是否已被验证
  creatorCount      带货达人数      <- 判断达人竞争激烈程度
  creatorOrderRate  达人出单率
  isCrossBorder     1=跨境店商品
  offShelves        1=已下架
  ctime             上架时间
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.request
import urllib.error

BASE = "https://<your-erp-host>"
OTHER_BASE = "https://<your-erp-host>"  # 同一后端，可互换

# 本地货币 -> USD 汇率（单位：1 USD = N 本地货币）
# 数据源：快递助手跨境版 /item/exchangeRate/getList（2026-11-11 20:15 更新）
# 重新校准：1 USD = 6.67111 CNY = 33.65 THB = 25,657 VND
# 可用 `--live-fx` 从 ERP 实时拉取覆盖本表
FX = {
    "US": 1.0, "GB": 0.79, "TH": 33.65, "VN": 25657, "ID": 18129,
    "MY": 4.068, "PH": 63.15, "SG": 1.282, "MX": 18.0, "BR": 5.575,
    "JP": 152.0, "TW": 32.83, "DE": 0.92, "FR": 0.92, "IT": 0.92, "ES": 0.92,
    "AT": 0.92, "BE": 0.92, "NL": 0.92,
}

ORDER_FIELDS = {
    "day7_units_sold", "day7_gmv", "commission_rate",
    "total_units_sold", "total_gmv", "creator_count",
}


def post(path, body, timeout=30):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "Mozilla/5.0")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError as e:
        sys.exit(f"[kdzs] 网络错误 {path}: {e}")
    if not j.get("success"):
        sys.exit(f"[kdzs] 接口错误 {path}: code={j.get('errorCode')} msg={j.get('errorMessage')}")
    return j.get("data")


def to_int(v, default=0):
    try:
        return int(float(str(v).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def to_float(v, default=0.0):
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return default


def fetch_categories():
    d = post("/item/fastmoss/category/list", {"lang": "ZH_CN"})
    return d or []


def search(region, usd_min=None, usd_max=None, order="day7_units_sold",
           pagesize=100, max_pages=5, cross_border_only=True,
           min_video=0, max_creator=None, keyword=None):
    fx = FX.get(region)
    if fx is None:
        sys.exit(f"[kdzs] 未配置汇率: {region}，请先在 FX 表中补充")

    flt = {"region": region}
    if cross_border_only:
        flt["shopType"] = 2          # 1=本土店 2=跨境店
    if usd_min is not None or usd_max is not None:
        lo = int((usd_min or 0) * fx)
        hi = int((usd_max or 9999) * fx)
        flt["priceRange"] = {"min": lo, "max": hi}
    if min_video:
        pass                          # 接口无视频数下限过滤，取回后本地筛
    if max_creator is not None:
        flt["creatorCountRange"] = {"max": int(max_creator)}

    if order not in ORDER_FIELDS:
        sys.exit(f"[kdzs] 排序字段不合法，仅支持: {sorted(ORDER_FIELDS)}")

    rows, total = [], None
    for page in range(1, max_pages + 1):
        body = {
            "keywords": (keyword or "").strip() or None,
            "filter": flt,
            "orderby": [{"field": order, "order": "desc"}],
            "page": page,
            "pagesize": pagesize,
        }
        d = post("/item/fastmoss/product/search", body) or {}
        batch = d.get("list") or []
        total = d.get("total")
        rows.extend(batch)
        if len(batch) < pagesize:
            break
        time.sleep(0.6)               # 温和限速

    if min_video:
        rows = [r for r in rows if to_int(r.get("videoCount")) >= min_video]

    return rows, total


def enrich(r):
    """派生指标：给新人判断'能不能做'用，规则简单可解释。"""
    videos = to_int(r.get("videoCount"))
    creators = to_int(r.get("creatorCount"))
    d7 = to_int(r.get("day7UnitsSold"))
    rate = to_float(r.get("creatorOrderRate"))

    # 内容验证：有视频说明这个品已经被短视频跑通过
    content = "已验证" if videos >= 50 else ("初步" if videos >= 10 else "无/弱")
    # 达人竞争：达人数越多，新卖家抢达人越难
    if creators == 0:
        comp = "无达人"
    elif creators < 50:
        comp = "低"
    elif creators < 500:
        comp = "中"
    else:
        comp = "高"

    return {
        "productId": r.get("productId"),
        "title": (r.get("title") or "")[:80],
        "site": r.get("region"),
        "price_local": r.get("floorPrice"),
        "price_raw": r.get("price"),
        "usd_equiv": round(to_float(r.get("floorPrice")) / FX.get(r.get("region"), 1), 2),
        "day7_units": d7,
        "day28_units": to_int(r.get("day28UnitsSold")),
        "day90_units": to_int(r.get("day90UnitsSold")),
        "total_units": to_int(r.get("totalUnitsSold")),
        "videos": videos,
        "creators": creators,
        "creator_order_rate": r.get("creatorOrderRate"),
        "rating": r.get("productRating"),
        "skus": to_int(r.get("skuCount")),
        "commission": r.get("commissionRate"),
        "cross_border": r.get("isCrossBorder"),
        "off_shelves": r.get("offShelves"),
        "listed_at": r.get("ctime"),
        "content_proof": content,
        "creator_comp": comp,
        "category": " > ".join(
            [(r.get("category") or {}).get(k, {}).get("name", "")
             for k in ("l1", "l2", "l3")] 
        ).strip(" >"),
        "shop": (r.get("shop") or {}).get("name"),
        "fm_url": r.get("fastmossUrl"),
    }


def main():
    ap = argparse.ArgumentParser(description="快递助手ERP · TikTok 选品数据源（免登录）")
    ap.add_argument("--region", default="TH", help="站点：TH/VN/ID/MY/PH/US...")
    ap.add_argument("--usd-min", type=float, default=None, help="价格下限（美元）")
    ap.add_argument("--usd-max", type=float, default=None, help="价格上限（美元）")
    ap.add_argument("--order", default="day7_units_sold", help=f"排序：{sorted(ORDER_FIELDS)}")
    ap.add_argument("--pagesize", type=int, default=100)
    ap.add_argument("--max-pages", type=int, default=5)
    ap.add_argument("--top", type=int, default=0, help="只输出前 N 条")
    ap.add_argument("--keyword", default="", help="关键词（需当地语言/英文）")
    ap.add_argument("--min-video", type=int, default=0, help="带货视频数下限")
    ap.add_argument("--max-creator", type=int, default=None, help="带货达人数上限")
    ap.add_argument("--include-local", action="store_true", help="包含本土店（默认仅跨境店）")
    ap.add_argument("--out", default="", help="CSV 输出路径")
    ap.add_argument("--categories", action="store_true", help="打印类目树")
    ap.add_argument("--json", action="store_true", help="输出原始 JSON")
    args = ap.parse_args()

    if args.categories:
        cats = fetch_categories()
        for c in cats:
            print(f"{c.get('cCode'):>10}  {c.get('cName')}  (子类 {len(c.get('sub') or [])})")
        return

    rows, total = search(
        region=args.region,
        usd_min=args.usd_min,
        usd_max=args.usd_max,
        order=args.order,
        pagesize=args.pagesize,
        max_pages=args.max_pages,
        cross_border_only=not args.include_local,
        min_video=args.min_video,
        max_creator=args.max_creator,
        keyword=args.keyword,
    )
    data = [enrich(r) for r in rows]
    if args.top:
        data = data[:args.top]

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    if not data:
        print("[kdzs] 无结果。可能原因：价格带过窄 / min-video 过高 / 该类目跨境品少")
        return

    hdr = ["productId", "title", "site", "price_local", "usd_equiv", "day7_units",
           "day28_units", "videos", "creators", "creator_comp", "content_proof",
           "rating", "commission", "cross_border", "listed_at", "category", "fm_url"]
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=hdr, extrasaction="ignore")
            w.writeheader()
            w.writerows(data)
        print(f"[kdzs] 已写入 {args.out}（{len(data)} 条）")
    else:
        print(f"{'7日销量':>8} {'视频':>7} {'达人':>7} {'$约':>6} {'竞争':<4} {'内容':<5} 标题")
        for d in data:
            print(f"{d['day7_units']:>8} {d['videos']:>7} {d['creators']:>7} "
                  f"{d['usd_equiv']:>6} {d['creator_comp']:<4} {d['content_proof']:<5} "
                  f"{d['title'][:44]}")

    print(f"\n[汇总] {args.region} 跨境店 订单排序={args.order} "
          f"接口报告总数={total} 实际取回={len(rows)} 输出={len(data)}")


if __name__ == "__main__":
    main()
