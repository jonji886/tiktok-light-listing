#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""site_goods.py — ERP「商品 → TikTok → 站点商品」（已发布/在售商品）客户端。

⚠️ 与 erp_client.py 的「采集箱」是两套东西：
  采集箱 = 发布前草稿（ttl_id）；本脚本 = 平台侧在售商品（numIid = TikTok 商品 ID）。
接口契约见 references/tiktok-api.md 末章，踩坑见 references/erp-pitfalls.md 第十二章。

只读命令（安全）：
  python3 scripts/site_goods.py list --platform tiktok --limit 10
  python3 scripts/site_goods.py detail --num-iid <id> --out erp_data/site_detail_<id>.json
  python3 scripts/site_goods.py log --num-iid <id>
  python3 scripts/site_goods.py status --offer <1688货源ID>     # 真正的「是否在售」判定

⚠️ 三个 ID 别混（2026-11-25 踩过）：
  - offer_id    1688 货源 ID（646441528349）—— logs 的 itemId / 站点商品的 outerId
  - itemCenterNumIid  ERP 内部商品中心 ID（1574...）—— **不能**喂 queryPlatformItemDetail
  - numIid       TikTok 商品 ID（1738...）—— 只有它能查详情；来源是 logs 的 platformItemId
  判在售只能看 productStatus：ACTIVATE/在售 = 真在售；INITIAL = 已创建待审核（不算上架）。

写命令（默认 dry-run，必须显式 --save）：
  python3 scripts/site_goods.py update-desc --num-iid <id> --text-file desc_th.txt
  python3 scripts/site_goods.py update-desc --num-iid <id> --text-file desc_th.txt --save

  --text-file 是当地语言纯文本，用空行分段（→ 逐段包 <p>）。
  ⚠️ 保存 body 必须删 replicatedProducts，否则平台报 Missing SKU details；
  ⚠️ ERP 详情接口有数分钟缓存：--save 后立刻回读常仍是旧值（实测数分钟后才显示新描述），
     别过早判失败；平台侧是否同步仍需回 TikTok 后台人工复核。

未实现上/下架：/item/item/itemBatchUpDownShelf 的 ops 取值未实测，禁止猜测调用。
"""
import argparse
import copy
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

BASE = "https://erp.kuaidizs.cn"
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "erp_token.txt")
DEFAULT_SELLER = os.environ.get("TH_SELLER_ID", "<TH_SELLER_ID>")  # 存量 TH 店；从 .env 读，仓库不存真实值
HEADERS = {
    "Content-Type": "application/json;charset=UTF-8",
    "appType": "3",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}


def post(path, body):
    if not os.path.exists(TOKEN_FILE):
        sys.exit("[site] 缺 token 文件：先登录 ERP 并用 erp_token_grab.py 落盘")
    tok = open(TOKEN_FILE, encoding="utf-8").read().strip()
    req = urllib.request.Request(BASE + path, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 method="POST")
    for k, v in HEADERS.items():
        req.add_header(k, v)
    req.add_header("qnquerystring", tok)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except Exception:
        sys.exit(f"[site] 响应非 JSON {path}: {raw[:200]}")


def desc_stats(desc):
    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", desc or "")).strip()
    paras = len(re.findall(r"<(?:p|div|li|h[1-6])\b[^>]*>\s*[^<\s]", desc or "", re.I))
    return len(plain), paras


def get_detail(num_iid, seller):
    r = post("/item/item/queryPlatformItemDetail",
             {"sellerId": seller, "platform": "tiktok", "numIid": str(num_iid)})
    if r.get("success") is False:
        sys.exit(f"[site] 详情失败: {r.get('errorMessage')}")
    d = (r.get("data") or {}).get("tiktokDetailInfo")
    if not d:
        sys.exit("[site] 详情无 tiktokDetailInfo（商品可能已下架或 numIid 错）")
    return r, d


def cmd_list(a):
    # 翻页聚合：单页 100 条不够（全平台混排 398+），只拉第 1 页会漏在售品、
    # 导致 backflow_import 的 numIid→货源ID 映射缺条目。循环到取完或空页。
    all_rows, page, total = [], 1, 0
    while True:
        r = post("/item/sysItem/queryPlatformItemListWithPage",
                 {"pageNo": page, "pageSize": a.page_size})
        data = r.get("data") or {}
        rows = data.get("list") or []
        if not rows:
            break
        all_rows += rows
        total = int(data.get("total") or 0)
        if len(all_rows) >= total or page >= 20:
            break
        page += 1
    rows = all_rows
    if a.platform:
        rows = [x for x in rows if str(x.get("platform")) == a.platform]
    print(f"[site] total(全平台)={total} 聚合={len(all_rows)} 筛后={len(rows)}")
    for x in rows[:a.limit]:
        print(f"  {x.get('numIid')} | {x.get('outerId')} | {x.get('sellerNick')} | "
              f"{str(x.get('itemAlias'))[:46]}")
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        payload = {"data": {"list": all_rows, "total": total}, "success": True}
        open(a.out, "w", encoding="utf-8").write(json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"[site] 原始响应（含翻页聚合） → {a.out}")


def cmd_detail(a):
    r, d = get_detail(a.num_iid, a.seller)
    n, p = desc_stats(d.get("description"))
    skus = d.get("skus") or []
    price = (skus[0].get("price") or {}).get("salePrice") if skus else "?"
    print(f"[site] {a.num_iid} | {d.get('productStatus')}/{d.get('productStatusName')} "
          f"| 标题 {str(d.get('title'))[:46]}")
    print(f"       description 纯文本 {n} 字符 / {p} 段 | SKU {len(skus)} 个 | 首价 {price} "
          f"| 类目 {d.get('categoryId')} | 多国家副本 {len(d.get('replicatedProducts') or [])}")
    out = a.out or f"erp_data/site_detail_{a.num_iid}.json"
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    open(out, "w", encoding="utf-8").write(json.dumps(r, ensure_ascii=False, indent=2))
    print(f"[site] 已落盘备份 → {out}")


def cmd_log(a):
    r = post("/item/getPlatItemOperateLog",
             {"pageNo": 1, "pageSize": 20, "numIid": str(a.num_iid),
              "viewType": 1, "logOperateTypeList": [8]})
    rows = (r.get("data") or {}).get("list") or []
    print(f"[site] 编辑日志 {len(rows)} 条")
    for x in rows:
        print(f"  {x.get('gmtCreated')} type={x.get('logOperateType')} "
              f"enable={x.get('enableStatus')} by={x.get('operateName')} content={x.get('content')}")


def cmd_update_desc(a):
    if not os.path.exists(a.text_file):
        sys.exit(f"[site] 文件不存在: {a.text_file}")
    text = open(a.text_file, encoding="utf-8").read().strip()
    if not text:
        sys.exit("[site] 描述文件为空")
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    import html
    new_desc = "".join(f"<p>{html.escape(p)}</p>" for p in parts)
    # 保留原 description 里的详情图标签（若存在），不要因为只改文本就删图
    _, d = get_detail(a.num_iid, a.seller)
    imgs = re.findall(r"<img[^>]*>", d.get("description") or "")
    if imgs:
        new_desc += "\n" + "\n".join(imgs)
    n, p = desc_stats(new_desc)
    print(f"[site] 新描述 纯文本 {n} 字符 / {p} 段 / 保留详情图 {len(imgs)} 张")
    if n < 500 or p < 2:
        print("[site] ⚠ 未达 config/image_gate.yaml 的描述质量线（≥500 字符 / ≥2 段）")

    body_d = copy.deepcopy(d)
    body_d.pop("replicatedProducts", None)          # 坑 1：否则 Missing SKU details
    body_d.pop("multiCountrySkuSalesConfig", None)
    body_d["description"] = new_desc
    body = {"sellerId": a.seller, "platform": "tiktok",
            "numIid": str(a.num_iid), "tiktokDetailInfo": body_d}
    if a.out:
        open(a.out, "w", encoding="utf-8").write(json.dumps(body, ensure_ascii=False, indent=2))
        print(f"[site] body → {a.out}")
    if not a.save:
        print("[site] dry-run：未提交。确认后加 --save")
        return
    res = post("/item/item/updatePlatformItem", body)
    print(f"[site] 保存响应: {json.dumps(res, ensure_ascii=False)[:400]}")
    time.sleep(2)
    _, after = get_detail(a.num_iid, a.seller)
    n2, p2 = desc_stats(after.get("description"))
    print(f"[site] 回读: 纯文本 {n2} 字符 / {p2} 段 | 状态 {after.get('productStatus')}")
    if n2 < 500 or p2 < 2:
        print("[site] ⚠ 立即回读未体现新描述：实测 ERP 详情有数分钟缓存延迟"
              "（#1 提交后数分钟回读到 1011 字符 / 4 段）。请稍后重跑 detail 复核；"
              "平台侧是否同步仍需回 TikTok 后台确认，不要在此刻下结论")


def cmd_update_images(a):
    """在售品主图替换（2026-11-19 新增，围裙 1→5 场景）。
    --urls 逗号分隔的公网直链（须 prepublish 同一纪律：非 aka 短链、已回读核验）。
    实测路径：ERP updatePlatformItem 换 mainImages[].uri 外链 → 平台重转存。
    已知缓存：ERP 详情数分钟才见新值（与 update-desc 同源教训），勿即时判败。"""
    urls = [u.strip() for u in a.urls.split(",") if u.strip()]
    if not urls:
        sys.exit("[site] --urls 为空")
    for u in urls:
        if not u.startswith("https://"):
            sys.exit(f"[site] 主图必须 https 直链: {u}")
        if "aka.doubaocdn.com" in u:
            sys.exit(f"[site] aka 短链平台取图必败（pitfalls 13.4）: {u}")
    _, d = get_detail(a.num_iid, a.seller)   # d 即 tiktokDetailInfo 本体（update-desc 同源）
    old = d.get("mainImages") or []
    proto = dict(old[0]) if old else {}
    new_imgs = []
    for u in urls:
        m = dict(proto)
        m["uri"] = u
        for k in ("thumbUrls", "imagesId", "id"):
            m.pop(k, None)  # 平台旧图私有字段清掉，重转存由平台侧做
        new_imgs.append(m)
    print(f"[site] 主图 {len(old)} → {len(new_imgs)} 张")
    body_d = copy.deepcopy(d)
    body_d.pop("replicatedProducts", None)          # 同 update-desc：否则 Missing SKU details
    body_d.pop("multiCountrySkuSalesConfig", None)
    body_d["mainImages"] = new_imgs
    body = {"sellerId": a.seller, "platform": "tiktok",
            "numIid": str(a.num_iid), "tiktokDetailInfo": body_d}
    if a.out:
        open(a.out, "w", encoding="utf-8").write(json.dumps(body, ensure_ascii=False, indent=2))
        print(f"[site] body → {a.out}")
    if not a.save:
        print("[site] dry-run：未提交。确认后加 --save")
        return
    res = post("/item/item/updatePlatformItem", body)
    print(f"[site] 保存响应: {json.dumps(res, ensure_ascii=False)[:400]}")
    time.sleep(3)
    _, after = get_detail(a.num_iid, a.seller)
    n2 = len(after.get("mainImages") or [])
    print(f"[site] 立即回读 mainImages={n2} 张")
    if n2 != len(new_imgs):
        print("[site] ⚠ ERP 详情缓存数分钟（update-desc 同款教训）：稍后重跑 detail 复核；"
              "平台侧以 TikTok 后台为准，不要在此刻下结论")


def logs_for_offer(offer_id, days=31):
    """从铺货日志里取该 1688 货源的所有发布记录（含 TikTok 商品 ID）。

    ⚠️ post() 返回的是**整个响应**（含 data/success），不是 data 本身。
    """
    import datetime
    end = datetime.datetime.now()
    start = end - datetime.timedelta(days=days)
    r = post("/item/distribute/queryLogs", {
        "pageNo": 1, "pageSize": 200,
        "toPlatformList": ["tiktok"], "fromPlatformList": ["1688"],
        "startTime": start.strftime("%Y-%m-%d 00:00:00"),
        "endTime": end.strftime("%Y-%m-%d %H:%M:%S")})
    rows = (r.get("data") or {}).get("list") or []
    return [x for x in rows if str(x.get("itemId")) == str(offer_id)]


def cmd_status(a):
    """判「平台侧是否在售」——logs → platformItemId → productStatus。

    退出码：0 在售(ACTIVATE) / 3 已创建但未激活(INITIAL/PENDING 等) /
            4 发布失败或无记录 / 0 且 --json 时由调用方读 JSON 判定。
    """
    hits = logs_for_offer(a.offer)
    out = {"offer_id": a.offer, "log_status": None, "tiktok_id": None,
           "product_status": None, "product_status_name": None,
           "on_sale": False, "error": "", "log_upt": None}
    if not hits:
        out["error"] = "logs 无该货源记录（未发布或超过 31 天）"
        _emit_status(a, out)
        return 4
    latest = max(hits, key=lambda x: str(x.get("uptTime") or x.get("createTime") or ""))
    ext = latest.get("extInfo") or {}
    out["log_status"] = latest.get("status")
    out["log_upt"] = latest.get("uptTime") or latest.get("createTime")
    out["tiktok_id"] = latest.get("platformItemId") or ext.get("TIKTOK_PRODUCT_ID") or None
    out["error"] = latest.get("errorMessage") or ""
    if out["log_status"] != "success":
        _emit_status(a, out)
        return 4
    if not out["tiktok_id"]:
        out["error"] = "发布成功但无 platformItemId"
        _emit_status(a, out)
        return 4
    seller = a.seller or str(latest.get("shopId") or DEFAULT_SELLER)
    try:
        _, d = get_detail(out["tiktok_id"], seller)
    except SystemExit as e:
        out["error"] = str(e)
        _emit_status(a, out)
        return 4
    out["product_status"] = d.get("productStatus")
    out["product_status_name"] = d.get("productStatusName")
    out["on_sale"] = d.get("productStatus") == "ACTIVATE"
    _emit_status(a, out)
    return 0 if out["on_sale"] else 3


def _emit_status(a, out):
    line = ("[status] offer=%s log_status=%s tiktok_id=%s product_status=%s(%s) ON_SALE=%s"
            % (out["offer_id"], out["log_status"], out["tiktok_id"],
               out["product_status"], out["product_status_name"],
               "yes" if out["on_sale"] else "no"))
    print(line)
    if out["error"]:
        print("[status] 备注: %s" % str(out["error"])[:300])
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        open(a.out, "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=1))
        print("[status] → %s" % a.out)


def main():
    ap = argparse.ArgumentParser(description="TikTok 站点商品（在售）客户端")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="站点商品列表（只读，全平台混排）")
    p.add_argument("--platform", default="tiktok")
    p.add_argument("--page-size", type=int, default=100)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--out")

    p = sub.add_parser("detail", help="编辑详情（只读，落盘备份）")
    p.add_argument("--num-iid", required=True)
    p.add_argument("--seller", default=DEFAULT_SELLER)
    p.add_argument("--out")

    p = sub.add_parser("log", help="编辑操作日志（只读）")
    p.add_argument("--num-iid", required=True)

    p = sub.add_parser("update-desc", help="改描述，默认 dry-run，--save 才写")
    p.add_argument("--num-iid", required=True)
    p.add_argument("--text-file", required=True, help="当地语言纯文本，空行分段")
    p.add_argument("--seller", default=DEFAULT_SELLER)
    p.add_argument("--out", help="把 body 落盘")
    p.add_argument("--save", action="store_true")

    p = sub.add_parser("update-images", help="换在售品主图，默认 dry-run，--save 才写（2026-11-19）")
    p.add_argument("--num-iid", required=True)
    p.add_argument("--urls", required=True, help="逗号分隔 https 公网直链（禁 aka 短链，先回读核验）")
    p.add_argument("--seller", default=DEFAULT_SELLER)
    p.add_argument("--out", help="把 body 落盘")
    p.add_argument("--save", action="store_true")

    p = sub.add_parser("status", help="判平台侧是否在售（logs→platformItemId→productStatus）")
    p.add_argument("--offer", required=True, help="1688 货源 ID")
    p.add_argument("--seller", default=None, help="缺省取日志里的 shopId")
    p.add_argument("--out", help="把判定结果落盘 JSON")

    a = ap.parse_args()
    if a.cmd == "status":
        sys.exit(cmd_status(a))
    {"list": cmd_list, "detail": cmd_detail, "log": cmd_log,
     "update-desc": cmd_update_desc, "update-images": cmd_update_images}[a.cmd](a)


if __name__ == "__main__":
    main()
