#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
erp_client.py — 快递助手ERP跨境版 TikTok Shop 铺货接口客户端

链路：1688链接采集 → 公共采集箱 → 认领到TikTok站点店铺 → TikTok采集箱编辑（标题翻译/图片处理/定价）→ 发布。

认证（与 shopee-light-listing 相同）：
  - 请求头 qnquerystring: <JWT> + appType:3
  - token 获取：浏览器登录态内 bu.js 导出 sessionStorage.qnquerystring

TikTok 店铺 sellerId（实测 2026-10-27，有效授权）：
  TH = 7494877320827733079  (JingShanShop-TH)
  VN = 8686823118786298967  (JingShanShop-VN)

用法：
  python3 erp_client.py token --check
  python3 erp_client.py collect --ids 989551711745          # 1688链接采集（复用）
  python3 erp_client.py items [--page N] [--size N]        # 公共采集箱列表
  python3 erp_client.py ttl-list [--region TH] [--status 0]  # TikTok采集箱列表
  python3 erp_client.py ttl-detail --id 1564704833189261312  # 编辑详情
  python3 erp_client.py ttl-claim --ids <numIid> --seller 7494877320827733079  # 认领(写)
  python3 erp_client.py ttl-translate --id <id> --lang th  # 标题一键翻译
  python3 erp_client.py ttl-publish --ids <ttl采集箱id>      # 发布(写)
  python3 erp_client.py logs --months 1                     # 发布记录
  python3 erp_client.py shops --platform tiktok             # TikTok店铺列表
"""
import argparse, csv, json, os, sys, time
import urllib.request, urllib.error

BASE = "https://erp.kuaidizs.cn"
HEADERS = {
    "Content-Type": "application/json;charset=UTF-8",
    "appType": "3",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}
MIN_INTERVAL = 1.0
_last_ts = 0.0
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "erp_token.txt")

# TikTok 站点配置（sellerId 实测 2026-10-27）
TT_SHOPS = {
    "TH": {"sellerId": "7494877320827733079", "nick": "JingShanShop-TH", "lang": "th", "currency": "THB"},
    "VN": {"sellerId": "8686823118786298967", "nick": "JingShanShop-VN", "lang": "vi", "currency": "VND"},
}


def _load_token(args):
    tok = getattr(args, "token", None) or os.environ.get("ERP_TOKEN") or ""
    if not tok and os.path.exists(TOKEN_FILE):
        tok = open(TOKEN_FILE, "r", encoding="utf-8").read().strip()
    if not tok:
        sys.exit("[erp] 未找到 token：先登录 ERP 后用 bu.js 导出，或 token --set <token>")
    return tok


def _post(path, body, args):
    global _last_ts
    now = time.time()
    if now - _last_ts < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - (now - _last_ts))
    _last_ts = time.time()
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    for k, v in HEADERS.items():
        req.add_header(k, v)
    req.add_header("qnquerystring", _load_token(args))
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
    except Exception as e:
        sys.exit(f"[erp] 网络错误 {path}: {e}")
    try:
        j = json.loads(raw)
    except Exception:
        sys.exit(f"[erp] 响应非JSON {path}: {raw[:200]}")
    if not j.get("success"):
        code = j.get("errorCode")
        msg = j.get("errorMessage", "")
        if code == 703 or "token" in str(msg):
            sys.exit(f"[erp] token失效(703)：重新登录ERP并刷新 erp_token.txt")
        if code == 707:
            sys.exit(f"[erp] 限流(707)：{msg}，等10s再试")
        sys.exit(f"[erp] 接口错误 {path}: code={code} msg={msg}")
    return j.get("data") or {}


def _write_out(args, name, rows, default_dir="erp_data"):
    out_dir = getattr(args, "out", None) or default_dir
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, name + ".csv")
    json_path = os.path.join(out_dir, name + ".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    if rows:
        cols = []
        for r in rows:
            for k in r.keys():
                if k not in cols:
                    cols.append(k)
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in cols})
    return csv_path, json_path


# ── token ──────────────────────────────────────────────
def cmd_token(args):
    if args.action == "set":
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(args.token.strip())
        print(f"[erp] token saved: {TOKEN_FILE}")
    elif args.action == "check":
        d = _post("/index/platformShop/getPlatformShops", {"pageNo":1,"pageSize":5,"platform":"tiktok"}, args)
        print(f"[erp] token OK, tiktok shops total={d.get('total')}")


# ── collect（复用旧逻辑）──────────────────────────────
def cmd_collect(args):
    ids = [i.strip() for i in (args.ids or "").split(",") if i.strip()]
    if args.ids_file:
        for line in open(args.ids_file, encoding="utf-8"):
            line = line.strip()
            if not line: continue
            m = line.split(",")[0].strip()
            if "detail.1688.com/offer/" in line:
                m = line.split("detail.1688.com/offer/")[1].split(".")[0].split("/")[0].strip()
            ids.append(m)
    ids = list(dict.fromkeys(ids))
    rows = []
    for i in range(0, len(ids), 500):
        chunk = ids[i:i+500]
        d = _post("/item/center/batchPluginCollectItem", {"itemIdList": chunk}, args)
        rows.append({"批次": i//500+1, "提交": len(chunk),
                     "成功": d.get("collectSuccessNum",0), "失败": d.get("collectFailNum",0)})
    cp, jp = _write_out(args, "collect_result", rows)
    print(f"[erp] collect: {len(ids)} offers → {cp}")


# ── items（公共采集箱）───────────────────────────────
def cmd_items(args):
    body = {"pageNo": args.page, "pageSize": args.size}
    if args.ids:
        body["platNumIidList"] = [i.strip() for i in args.ids.split(",") if i.strip()]
    d = _post("/item/center/queryErpItemListPage", body, args)
    rows = []
    for it in d.get("list") or []:
        sl = it.get("itemSourceLinks") or []
        rows.append({
            "货源ID": it.get("platNumIid",""), "ERP内部ID": it.get("numIid",""),
            "标题": it.get("title",""), "认领平台": ",".join(it.get("claimPlatformList") or []),
            "已认领": it.get("claimStatus",""), "价格区间": f"{it.get('priceLow','')}~{it.get('priceTop','')}",
            "1688链接": sl[0].get("sourceLink","") if sl else "",
        })
    cp, jp = _write_out(args, "shared_goods", rows)
    print(f"[erp] 公共采集箱: total={d.get('total')} → {cp}")


# ── ttl-list（TikTok采集箱列表）──────────────────────
def cmd_ttl_list(args):
    body = {"pageNo": args.page, "pageSize": args.size}
    if args.region:
        body["toRegion"] = args.region
    if args.status is not None:
        body["status"] = args.status
    d = _post("/item/crossBorder/platformCollect/queryListPage", body, args)
    rows = []
    for it in d.get("list") or []:
        rows.append({
            "ttl_id": it.get("id",""),           # 编辑/发布用这个
            "itemCenterNumIid": it.get("itemCenterNumIid",""),
            "货源ID": it.get("numIid",""),
            "标题": it.get("title","")[:60],
            "站点": it.get("toRegion",""),
            "店铺": it.get("toSellerNick",""),
            "类目": it.get("toCategoryNameTree",""),
            "币种": it.get("currency",""),
            "售价区间": f"{it.get('priceLow','')}~{it.get('priceTop','')}",
            "库存": it.get("stock",""),
            "状态": it.get("status",""),
            "异常标签": ",".join(it.get("serviceTagList") or []),
            "图片": it.get("picUrl",""),
        })
    cp, jp = _write_out(args, "tiktok_list", rows)
    print(f"[erp] TikTok采集箱: total={d.get('total')} → {cp}")


# ── ttl-detail（编辑详情）────────────────────────────
def cmd_ttl_detail(args):
    d = _post("/item/crossBorder/platformCollect/getDetail", {"id": args.id}, args)
    out_dir = getattr(args, "out", None) or "erp_data"
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, f"ttl_detail_{args.id}.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    ttm = (d.get("reqInfo") or {}).get("tikTokItemModel") or {}
    v2 = ttm.get("productCreateRequestV2") or {}
    print(f"[erp] 详情已存: {p}")
    print(f"  标题: {v2.get('title','')[:80]}")
    print(f"  类目ID: {v2.get('category_id','')}  类目树: {d.get('toCategoryNameTree','')}")
    imgs = v2.get("main_images",[])
    print(f"  主图: {len(imgs)} 张")
    skus = v2.get("skus",[])
    print(f"  SKU: {len(skus)} 个, 首SKU价: {skus[0].get('price',{}).get('amount','') if skus else '?'} {v2.get('currency','')}")


# ── ttl-save（保存编辑，写操作）──────────────────────
def cmd_ttl_save(args):
    """保存编辑。接口 POST /item/crossBorder/edit
    正确body（2026-10-28破解）：顶层字段(id/itemModel=整个外层tikTokItemModel/platform/reqInfo/title/类目三件套/店铺/币种/来源/status)
    用法：python3 erp_client.py ttl-save --id <ttl_id> [--price 190] [--weight 0.5] [--category 853128 --cat-tree "600001,851976,853128" --cat-name "家居用品,家居收纳,挂钩"]
    注意：保存后 getDetail 不再返回 itemModel（异常清空=保存生效）。仅对"有异常标签"的商品用（发布需itemModel可读）。
    ⚠⚠ 2026-11-11 实测：本命令的 body 结构是错的 —— itemModel 应传「整个 reqInfo 结构」
       （含 shopeeItemModel/temuItemModel/tikTokItemModel），且不能传顶层 reqInfo；
       否则会把商品数据清空成 MISSING_ITEM_DATA，发布必失败。
       正确实现见 fix_sku.py；改价请用 set_price.py（batchOps opsType:2）。
       保留本命令仅作历史记录，**不要直接使用**。
    """
    # 1) 拉详情（注意：reqInfo/tikTokItemModel 在响应顶层，不在 data 里，2026-10-28 实测）
    d = _post("/item/crossBorder/platformCollect/getDetail", {"id": args.id}, args)
    data = d.get("data") or {}
    req_info = d.get("reqInfo") or {}
    ttm = req_info.get("tikTokItemModel") or {}
    v2 = ttm.get("productCreateRequestV2") or {}
    if not ttm:
        sys.exit(f"[erp] 该商品 getDetail 无 itemModel（仅'带异常标签'商品才有；接口保存/完善后会清空）。本命令仅对'有异常标签'商品可用")
    # 2) 改价格（可选）
    if args.price:
        for sku in v2.get("skus", []):
            sku.setdefault("price", {})["amount"] = str(args.price)
            sku["price"]["currency"] = v2.get("currency", "THB")
        print(f"  价格已改为: {args.price} {v2.get('currency','')}")
    # 3) 改重量（可选）
    if args.weight:
        v2["package_weight"] = {"value": str(args.weight), "unit": "KILOGRAM"}
        print(f"  重量已改为: {args.weight} KG")
    # 3.5) 改主图（可选，按顺序替换 main_images[].uri，2026-10-29 图片语言测试新增）
    if args.images:
        urls = [u.strip() for u in args.images.split(",") if u.strip()]
        imgs = v2.get("main_images") or []
        if len(urls) != len(imgs):
            sys.exit(f"[erp] --images 数量 {len(urls)} 与主图数 {len(imgs)} 不一致，中止保存")
        for img, u in zip(imgs, urls):
            img["uri"] = u
        print(f"  主图已替换: {len(urls)} 张 → {urls[0][:40]}...")
    # 4) 改类目（可选）
    if args.category:
        v2["category_id"] = int(args.category)
        data["toCategoryId"] = args.category
        if args.cat_tree: data["toCategoryIdTree"] = args.cat_tree
        if args.cat_name: data["toCategoryNameTree"] = args.cat_name
        print(f"  类目已改为: {args.cat_name or args.category}")
    # 5) 构造edit body（正确结构：顶层字段 + itemModel=整个外层tikTokItemModel；顶层字段取自响应 d 顶层，2026-10-28 实测）
    # 注意：类目/店铺/币种/来源字段在 data 里（不在 d 顶层），2026-10-29 修复
    body = {
        "id": data.get("id") or d.get("id") or args.id,
        "itemModel": ttm,
        "platform": "tiktok",
        "reqInfo": req_info,
        "title": data.get("title") or d.get("title") or v2.get("title"),
        "toSellerId": data.get("toSellerId"),
        "toSellerNick": data.get("toSellerNick"),
        "toCategoryId": data.get("toCategoryId"),
        "toCategoryIdTree": data.get("toCategoryIdTree"),
        "toCategoryNameTree": data.get("toCategoryNameTree"),
        "currency": data.get("currency"),
        "fromItemId": data.get("fromItemId"),
        "fromPlatform": data.get("fromPlatform"),
        "status": data.get("status"),
    }
    # 6) 提交
    result = _post("/item/crossBorder/edit", body, args)
    print(f"[erp] 保存提交 id={args.id}: success={result.get('success', True)}")
    print(f"  判定生效：刷新 ttl-list 看异常标签清空 + 类目生效")


# ── ttl-claim（认领到TikTok店铺，写操作）─────────────
def cmd_ttl_claim(args):
    """把公共采集箱商品认领到指定TikTok站点店铺。
    接口 POST /item/crossBorder/commonCollect/batchClaimMultiShop
    body 已破解（2026-10-29 CDP document_start 注入抓真实请求）：
      {"numIids":["<ERP内部ID>"],"targetShops":[{"targetPlatform":"tiktok",
       "sellerId":"...","sellerNick":"JingShanShop-TH"}]}
    注意：numIids 是 ERP内部ID(numIid)，不是1688 offerId。
    """
    shop = TT_SHOPS.get(args.region.upper())
    if not shop:
        sys.exit(f"[erp] 未知站点 {args.region}，支持: {list(TT_SHOPS.keys())}")
    seller_id = args.seller or shop["sellerId"]
    # 从公共采集箱查 numIid（传入的可能是 offerId，映射到 ERP内部ID）
    ids = [i.strip() for i in args.ids.split(",") if i.strip()]
    d = _post("/item/center/queryErpItemListPage",
              {"pageNo":1,"pageSize":100,"platNumIidList": ids}, args)
    num_ids = [it["numIid"] for it in d.get("list",[]) if it.get("numIid")]
    if not num_ids:
        sys.exit(f"[erp] 货源ID {ids} 不在公共采集箱，先 collect")
    body = {
        "numIids": [str(n) for n in num_ids],
        "targetShops": [{
            "targetPlatform": "tiktok",
            "sellerId": str(seller_id),
            "sellerNick": shop["nick"],
        }],
    }
    result = _post("/item/crossBorder/commonCollect/batchClaimMultiShop", body, args)
    print(f"[erp] 认领 {len(num_ids)} 个商品 → {shop['nick']}({args.region}): {json.dumps(result, ensure_ascii=False)[:300]}")


# ── ttl-translate（标题一键翻译）─────────────────────
def cmd_ttl_translate(args):
    """调 /item/crossBorder/translate 翻译标题。
    注意：具体 body 参数待抓包确认，先留接口骨架。
    """
    lang_map = {"th": "泰语", "vi": "越南语"}
    print(f"[erp] TODO: 翻译 id={args.id} → {lang_map.get(args.lang, args.lang)}")
    print(f"  接口: POST /item/crossBorder/translate (body待确认)")


# ── ttl-publish（发布，写操作）──────────────────────
def cmd_ttl_publish(args):
    """发布 TikTok 采集箱商品（接口已破解 2026-10-29）。
    接口 POST /item/distribute/batchPublish（正确body已抓真实请求）：
      {"publishConfig":{...}, "distribItemVos":[{itemId/sourcePlatform/targetPlatform/
        sourceSellerId/targetSellerId/targetSellerNick/categoryIds/categoryNames}]}
    注意：
      - itemId 是 TikTok采集箱记录 id（ttl_id，即 getDetail 的 id），不是 itemCenterNumIid（2026-10-29 实测对比：ttl_id→受理，center ID→"未找到该平台采集记录"）
      - sourceSellerId 必须填（实测=1323965），缺则后端报错
      - categoryIds/categoryNames 是字符串（可为空），不是数组
      - 商品缺类目→"类目不能为空"；缺描述→"商品描述不能为空"（商品自身数据问题，需先在编辑弹窗/接口完善）
    """
    ids = [i.strip() for i in args.ids.split(",") if i.strip()]
    if not ids:
        sys.exit("[erp] ttl-publish 需要 --ids（TikTok采集箱记录id清单）")
    try:
        args.dims = [float(x) for x in str(args.dims).lower().split("x")]
        assert len(args.dims) == 3
    except Exception:
        sys.exit("[erp] --dims 格式应为 长x宽x高（cm），如 30x20x5")
    # 查列表拿 itemCenterNumIid / toSellerId / toSellerNick
    body = {"pageNo":1,"pageSize":100}
    d = _post("/item/crossBorder/platformCollect/queryListPage", body, args)
    id_map = {}
    for it in d.get("list",[]):
        id_map[str(it.get("id",""))] = it
    vos = []
    for tid in ids:
        it = id_map.get(tid)
        if not it:
            print(f"[erp][警告] ttl_id={tid} 不在采集箱列表，跳过")
            continue
        vos.append({
            "itemId": str(it.get("id","")),   # 采集箱记录id（ttl_id）——不是 itemCenterNumIid！
            "sourcePlatform": "kdzs_erp",
            "targetPlatform": "tiktok",
            "sourceSellerId": str(it.get("sourceSellerId","") or "1323965"),
            "targetSellerId": str(it.get("toSellerId","")),
            "targetSellerNick": it.get("toSellerNick",""),
            # 官方前端(chunk77)传的是类目树；旧版传空串，2026-11-11 修正
            "categoryIds": it.get("toCategoryIdTree","") or "",
            "categoryNames": it.get("toCategoryNameTree","") or "",
        })
    if not vos:
        sys.exit("[erp] 无可发布商品")
    publish_config = {
        "weight": args.weight, "weightSet": 1,
        "packageLength": args.dims[0], "packageWidth": args.dims[1],
        "packageHeight": args.dims[2], "packageSet": 1,
        "filterAlreadyPublish": True, "forbiddenWordDetectMode": 0,
        "priceLimitEnable": False, "priceLimitMultiple": "1",
        "useTiktokMultiCountryConfig": False,
        "tiktokOverflowConfig": {
            "detailEnabled": True, "titleEnabled": True, "titleLimit": 255, "titleTruncateType": "END",
            "skuNameEnabled": True, "skuNameLimit": 20, "skuNameTruncateType": "END",
            "skuValueEnabled": True, "skuValueLimit": 50, "skuValueTruncateType": "END",
            "skuTotalEnabled": False,
        },
    }
    total = len(vos)
    if args.dry_run:
        print(f"[erp] dry-run 未提交。publishConfig={json.dumps(publish_config, ensure_ascii=False)}")
        print(f"[erp] distribItemVos={json.dumps(vos, ensure_ascii=False)}")
        return
    for x in range(0, total, 4):
        chunk = vos[x:x+4]
        body2 = {"publishConfig": publish_config, "distribItemVos": chunk}
        r = _post("/item/distribute/batchPublish", body2, args)
        print(f"[erp] 发布批次 {x//4+1}: {len(chunk)} 个 → success={r.get('success',True)} msg={r.get('errorMessage','')}")
    print(f"[erp] 发布提交完成: {total} 个商品进入队列，稍后用 logs --months 1 查状态")


# ── logs（发布记录）─────────────────────────────────
def cmd_logs(args):
    months = max(1, args.months)
    all_rows = []
    for m in range(months):
        end_ts = time.time() - m*30*86400
        start_ts = end_ts - 30*86400
        start = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start_ts))
        end = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(end_ts))
        body = {"pageNo":1,"pageSize":100,"startTime":start,"endTime":end,
                "fromPlatformList":["1688"],"toPlatformList":["tiktok"]}
        d = _post("/item/distribute/queryLogs", body, args)
        for it in d.get("list",[]):
            all_rows.append({
                "货源ID": it.get("itemId",""), "状态": it.get("status",""),
                "标题": it.get("title","") or it.get("fromTitle",""),
                "发布时间": it.get("createTime",""), "店铺": it.get("shopName",""),
                "失败原因": it.get("errorMessage",""),
            })
    cp, jp = _write_out(args, "tiktok_logs", all_rows)
    cnt = {}
    for r in all_rows:
        cnt[r["状态"]] = cnt.get(r["状态"],0)+1
    print(f"[erp] TikTok发布记录: {len(all_rows)} 条, 状态={json.dumps(cnt,ensure_ascii=False)} → {cp}")


# ── shops ────────────────────────────────────────────
def cmd_shops(args):
    d = _post("/index/platformShop/getPlatformShops",
              {"pageNo":1,"pageSize":100,"platform":"tiktok"}, args)
    rows = []
    for s in d.get("list",[]):
        ext = s.get("extendInfo") or {}
        ok = s.get("status")==1 and s.get("tokenStatus")==1
        rows.append({"店铺名":s.get("sellerNick",""),"sellerId":s.get("sellerId",""),
                      "站点":ext.get("region",""),"授权":"有效" if ok else "异常"})
    cp, jp = _write_out(args, "tiktok_shops", rows)
    print(f"[erp] TikTok店铺: {len(rows)} → {cp}")


# ── quota（铺货配额，只读；2026-11-12 实测可读）──────
def cmd_quota(args):
    """铺货配额查询（发布后四路验证的第三路，只读）。

    body 实测可为空 {}；返回 businessModule=CROSS_ITEM_LIMIT + details[]，
    details 按 sellerId 分店铺给 totalNum/consumeNum/remainingNum 与月度重置窗口。
    """
    d = _post("/trade/crossItemDistrib/quotaQuery", {}, args)
    rows = []
    for it in d.get("details", []):
        rows.append({
            "平台": it.get("platform", ""),
            "sellerId": it.get("sellerId", ""),
            "站点": _quota_region(it.get("sellerId", "")),
            "总量": it.get("totalNum", ""),
            "已用": it.get("consumeNum", ""),
            "剩余": it.get("remainingNum", ""),
            "本周期起": it.get("lastResetTime", ""),
            "下周期起": it.get("nextResetTime", ""),
        })
    cp, jp = _write_out(args, "quota", rows)
    if args.region:
        rows = [r for r in rows if r["站点"] == args.region.upper()]
    for r in (rows or []):
        if r["平台"] == "tiktok":
            print("[erp] 配额 %s(%s): 已用 %s / 总量 %s（剩余 %s，下次重置 %s）"
                  % (r["站点"] or r["sellerId"], r["sellerId"], r["已用"], r["总量"],
                     r["剩余"], r["下周期起"]))
    print(f"[erp] 配额明细 {len(rows)} 条 → {cp}")


def _quota_region(seller_id):
    for k, v in TT_SHOPS.items():
        if v["sellerId"] == seller_id:
            return k
    return ""


# ── ttl-del（删除采集箱记录，写操作）───────────────
def cmd_ttl_del(args):
    """删除 TikTok 采集箱记录（接口已破解 2026-10-29）。
    接口 POST /item/crossBorder/platformCollect/batchOps：
      {"idList":["<ttl_id>"],"platform":"tiktok","opsType":3}
    opsType: 3=删除（其他值未验证）。
    注意：删除后重新认领可能受公共采集箱认领标记残留影响。
    """
    ids = [i.strip() for i in args.ids.split(",") if i.strip()]
    if not ids:
        sys.exit("[erp] ttl-del 需要 --ids（TikTok采集箱记录id清单，即ttl_id）")
    body = {"idList": ids, "platform": "tiktok", "opsType": 3}
    r = _post("/item/crossBorder/platformCollect/batchOps", body, args)
    print(f"[erp] 删除 {len(ids)} 条采集箱记录: {json.dumps(r, ensure_ascii=False)[:200]}")
    print(f"  验证：ttl-list 核对记录消失")


def main():
    p = argparse.ArgumentParser(description="快递助手ERP TikTok铺货客户端")
    sub = p.add_subparsers(dest="cmd", required=True)

    pt = sub.add_parser("token"); pt.add_argument("action", choices=["set","check"]); pt.add_argument("--token", default="")
    pc = sub.add_parser("collect"); pc.add_argument("--ids", default=""); pc.add_argument("--ids-file", default=""); pc.add_argument("--out", default="")
    pi = sub.add_parser("items"); pi.add_argument("--page", type=int, default=1); pi.add_argument("--size", type=int, default=50); pi.add_argument("--ids", default=""); pi.add_argument("--out", default="")
    pl = sub.add_parser("ttl-list"); pl.add_argument("--page", type=int, default=1); pl.add_argument("--size", type=int, default=50); pl.add_argument("--region", default=""); pl.add_argument("--status", type=int, default=None); pl.add_argument("--out", default="")
    pd = sub.add_parser("ttl-detail"); pd.add_argument("--id", required=True); pd.add_argument("--out", default="")
    psv = sub.add_parser("ttl-save"); psv.add_argument("--id", required=True); psv.add_argument("--price", type=float, default=0); psv.add_argument("--weight", type=float, default=0); psv.add_argument("--images", default=""); psv.add_argument("--category", default=""); psv.add_argument("--cat-tree", default=""); psv.add_argument("--cat-name", default=""); psv.add_argument("--out", default="")
    pcl = sub.add_parser("ttl-claim"); pcl.add_argument("--ids", required=True); pcl.add_argument("--region", default="TH"); pcl.add_argument("--seller", default=""); pcl.add_argument("--out", default="")
    ptr = sub.add_parser("ttl-translate"); ptr.add_argument("--id", required=True); ptr.add_argument("--lang", default="th")
    pp = sub.add_parser("ttl-publish"); pp.add_argument("--ids", required=True)
    pp.add_argument("--weight", type=float, default=1.0, help="包裹重量 KG（默认1，轻小件建议按实重填）")
    pp.add_argument("--dims", default="10x10x10", help="包裹尺寸 cm，格式 长x宽x高")
    pp.add_argument("--dry-run", action="store_true", help="只打印发布参数，不提交")
    pp.add_argument("--out", default="")
    pdl = sub.add_parser("ttl-del"); pdl.add_argument("--ids", required=True); pdl.add_argument("--out", default="")
    plg = sub.add_parser("logs"); plg.add_argument("--months", type=int, default=1); plg.add_argument("--out", default="")
    ps = sub.add_parser("shops"); ps.add_argument("--platform", default="tiktok"); ps.add_argument("--out", default="")
    pq = sub.add_parser("quota"); pq.add_argument("--region", default="", help="TH/VN，留空列全部"); pq.add_argument("--out", default="")

    for sp in (pi,pl,plg,ps,pc,pcl,pp,pdl,pq):
        sp.add_argument("--token", default="")
    args = p.parse_args()

    {"token":cmd_token,"collect":cmd_collect,"items":cmd_items,
     "ttl-list":cmd_ttl_list,"ttl-detail":cmd_ttl_detail,"ttl-save":cmd_ttl_save,"ttl-claim":cmd_ttl_claim,
     "ttl-translate":cmd_ttl_translate,"ttl-publish":cmd_ttl_publish,"ttl-del":cmd_ttl_del,
     "logs":cmd_logs,"shops":cmd_shops,"quota":cmd_quota}.get(args.cmd, lambda a: None)(args)


if __name__ == "__main__":
    main()
