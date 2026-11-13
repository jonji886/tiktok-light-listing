#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
遨虾(aibuy) 只读选品客户端（纯标准库，免登录、免付费 key，仅做 mtop token 握手）。
用途（shopee-light-listing Step1/2）：
  1) 批量拉 Shopee/TikTok/Amazon 榜单候选（热销/飙升/新品），导出 JSON/CSV，供 AI 按规则打分选最优；
  2) 对 AI 选定的榜单品，用关键词 / 榜单图在1688检索现货 offer 候选，供 AI 自动匹配。

合规与边界（务必遵守）：
- 个人只读、低频（内置 ≥0.6s 间隔），不批量转售数据；接口为未公开内部 mtop，无 SLA，前端改版会断。
- 榜单(opprank)只有：标题、下游价、1688同款数、1688同款【中位价】、图、类目；
  无 Shopee 销量/GMV/评分/收藏/关键词趋势；每榜封顶约500条，需先选类目。
- 检索(keywordSearch/imageSearch)给1688现货候选：offerId/标准offer链接/起批价/月销/评分；
  月销是1688供给侧热度，不是 Shopee 销量；结果混入无关品，禁止盲取第一条，由 AI 按同类/月销/评分规则自动选最优。
- 红线（品牌/液体/带电/食品/保健/医疗/宗教/易碎/活体）由 AI 自动剔除，拿不准跳过不发，脚本不判定。

用法：
  python3 aibuy_rank_client.py sites
  python3 aibuy_rank_client.py tabs --platform Shopee_ZX --site MY
  python3 aibuy_rank_client.py categories --platform Shopee_ZX --site SG --tab overseas_hot_selling_list_v1
  python3 aibuy_rank_client.py categories --platform Shopee_ZX --site SG --tab overseas_hot_selling_list_v1 --level 2 --parent <一级ID>
  python3 aibuy_rank_client.py items --platform Shopee_ZX --site MY --tab overseas_soaring_list \
      --cate-id <类目ID> --cate-level 1 --pages 3 --out ./aibuy_data
  python3 aibuy_rank_client.py search --keyword "adhesive hook" --pages 2 --out ./aibuy_data
  python3 aibuy_rank_client.py search-image --image-url <oppImageUrl> --pages 1 --out ./aibuy_data
"""
import argparse, csv, hashlib, json, os, sys, time, urllib.request, urllib.parse, http.cookiejar

APPKEY = "12574478"
HOST = "https://h5api.m.1688.com/h5/"
OPP = "mtop.com.alibaba.cbu.crossborder.lp.opprank."
SRCH = "mtop.com.alibaba.cbu.crossBorder.lp."
COMMON = {"bizType": "ERP", "customerId": "kdzhushou",
          "language": "zh", "currency": "CNY", "version": "v2"}

_cj = http.cookiejar.CookieJar()
_op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cj))
_op.addheaders = [
    ("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/155 Safari/537.36"),
    ("Origin", "https://aibuy.1688.com"),
    ("Referer", "https://aibuy.1688.com/"),
    ("Content-Type", "application/x-www-form-urlencoded"),
]


def _token():
    for c in _cj:
        if c.name == "_m_h5_tk":
            return c.value.split("_")[0]
    return ""


def call(full_api, extra, retry=True):
    data = dict(COMMON)
    data.update(extra)
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    t = str(int(time.time() * 1000))
    sign = hashlib.md5(f"{_token()}&{t}&{APPKEY}&{raw}".encode()).hexdigest()
    qs = urllib.parse.urlencode({
        "jsv": "2.7.5", "appKey": APPKEY, "t": t, "sign": sign,
        "type": "originaljson", "v": "1.0", "timeout": "20000",
        "ecode": "0", "dataType": "json", "api": full_api,
    })
    body = urllib.parse.urlencode({"data": raw}).encode()
    try:
        r = _op.open(HOST + full_api + "/1.0/?" + qs, data=body, timeout=15)
        out = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        out = json.loads(e.read().decode())
    if retry and isinstance(out.get("ret"), list) and "TOKEN" in out["ret"][0]:
        time.sleep(0.3)
        return call(full_api, extra, False)
    return out


def result(api, extra):
    r = call(OPP + api, extra)
    d = r.get("data") or {}
    if d.get("retCode") not in (None, "S0000"):
        print(f"[warn] {api}: {d.get('retMsg') or r.get('ret')}", file=sys.stderr)
    return d.get("result") or []


# ---------- Step1 榜单 ----------
def cmd_sites(_):
    for pf in ["Shopee_ZX", "TikTok_XS", "Amazon_XS"]:
        rows = result("countrylist", {"platformCode": pf})
        print(f"\n{pf}")
        for x in rows:
            print(f"  {x['siteCode']:6} {x['siteName']}")
        time.sleep(0.6)


def cmd_tabs(a):
    for x in result("tablist", {"platformCode": a.platform, "siteCode": a.site}):
        print(f"{x['tabCode']:38} {x['tabName']}")


def cmd_categories(a):
    # level=1 根类目；level=2/3 用 --parent 传上一层 cateId；节点仅含 cateId/cateName
    for x in result("categorylist", {"platformCode": a.platform, "siteCode": a.site,
                    "tabCode": a.tab, "cateId": a.parent or "", "cateLevel": a.level}):
        print(f"{x['cateId']}  {x['cateName']}")


ITEM_FIELDS = [
    ("ranking", "排名"), ("tabName", "榜单"), ("siteCode", "站点"),
    ("oppTitle", "标题"), ("oppCurrency", "币种"), ("oppPrice", "下游标价"),
    ("oppRmbPrice", "下游人民币价"), ("matchItmCnt", "1688同款数"),
    ("matchItmMedPrice", "1688同款中位价"), ("cateLvl1Name", "一级类目"),
    ("cateLvl2Name", "二级类目"), ("cateLvl3Name", "三级类目"),
    ("oppImageUrl", "图片"), ("oppProductUrl", "下游链接"), ("rankId", "rankId"),
]


def _dump(base, rows, fields):
    os.makedirs(os.path.dirname(base) or ".", exist_ok=True)
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([zh for _, zh in fields])
        for row in rows:
            w.writerow([row.get(k, "") for k, _ in fields])
    print(f"saved {len(rows)} rows -> {base}.json / .csv")


def cmd_items(a):
    all_rows = []
    for page in range(1, a.pages + 1):
        r = call(OPP + "getitemlist", {"platformCode": a.platform, "siteCode": a.site,
                 "tabCode": a.tab, "cateId": a.cate_id, "cateLevel": a.cate_level,
                 "pageNo": page, "pageSize": 50})
        d = r.get("data") or {}
        rows = d.get("model") or []
        if not rows:
            break
        all_rows.extend(rows)
        print(f"page {page}: +{len(rows)} (total={d.get('total')})")
        time.sleep(0.8)
    _dump(os.path.join(a.out, f"{a.platform}_{a.site}_{a.tab}"), all_rows, ITEM_FIELDS)


# ---------- Step2 1688 检索候选 ----------
OFFER_FIELDS = [
    ("offerId", "offerId"), ("title", "标题"), ("price", "起批价"),
    ("monthSold", "1688月销"), ("compositeScore", "评分"),
    ("beginQuantity", "起订量"), ("companyName", "供应商"),
    ("sourceFactory", "源头工厂"), ("link", "标准offer链接"), ("imageUrl", "图片"),
]


def _std_link(href):
    if not href:
        return ""
    oid = href.split("/offer/")[-1].split("?")[0]
    if oid.endswith(".html"):
        oid = oid[:-5]
    return f"https://detail.1688.com/offer/{oid}.html" if oid else ""


def _search_call(api_name, sp, page):
    sp = dict(sp)
    sp["beginPage"] = page
    sp["pageSize"] = 20
    r = call(SRCH + api_name, {"searchParam": json.dumps(sp, ensure_ascii=False)})
    d = r.get("data") or {}
    if d.get("retCode") not in (None, "S0000"):
        print(f"[warn] {api_name}: {d.get('retMsg') or r.get('ret')}", file=sys.stderr)
    res = d.get("result") or {}
    rows = res.get("data") or []
    for x in rows:
        x["link"] = _std_link(x.get("link"))
    return rows, res.get("totalRecords"), res.get("totalPage")


def cmd_search(a):
    all_rows = []
    for page in range(1, a.pages + 1):
        rows, total, tp = _search_call("keywordSearch", {"keyword": a.keyword}, page)
        if not rows:
            break
        all_rows.extend(rows)
        print(f"page {page}: +{len(rows)} (totalRecords={total}, totalPage={tp})")
        time.sleep(0.8)
    _dump(os.path.join(a.out, f"kw_{a.keyword}"), all_rows, OFFER_FIELDS)


def cmd_search_image(a):
    all_rows = []
    for page in range(1, a.pages + 1):
        rows, total, tp = _search_call("imageSearch", {"imageAddress": a.image_url}, page)
        if not rows:
            break
        all_rows.extend(rows)
        print(f"page {page}: +{len(rows)} (totalRecords={total}, totalPage={tp})")
        time.sleep(0.8)
    _dump(os.path.join(a.out, "img_search"), all_rows, OFFER_FIELDS)


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sites")
    t = sub.add_parser("tabs"); t.add_argument("--platform", required=True); t.add_argument("--site", required=True)
    c = sub.add_parser("categories")
    c.add_argument("--platform", required=True); c.add_argument("--site", required=True); c.add_argument("--tab", required=True)
    c.add_argument("--level", type=int, default=1, choices=[1, 2, 3])
    c.add_argument("--parent", default="", help="上一层 cateId；level=2/3 必填")
    i = sub.add_parser("items")
    i.add_argument("--platform", required=True); i.add_argument("--site", required=True)
    i.add_argument("--tab", required=True); i.add_argument("--cate-id", required=True)
    i.add_argument("--cate-level", type=int, default=1, choices=[1, 2, 3], help="cateId 所在层级，必须与类目一致，错层返回0条")
    i.add_argument("--pages", type=int, default=3); i.add_argument("--out", default="./aibuy_data")
    k = sub.add_parser("search")
    k.add_argument("--keyword", required=True); k.add_argument("--pages", type=int, default=2)
    k.add_argument("--out", default="./aibuy_data")
    g = sub.add_parser("search-image")
    g.add_argument("--image-url", required=True); g.add_argument("--pages", type=int, default=1)
    g.add_argument("--out", default="./aibuy_data")
    a = p.parse_args()
    {
        "sites": cmd_sites, "tabs": cmd_tabs, "categories": cmd_categories,
        "items": cmd_items, "search": cmd_search, "search-image": cmd_search_image,
    }[a.cmd](a)


if __name__ == "__main__":
    main()
