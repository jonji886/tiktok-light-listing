#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""选品取证自动化（P1-6）——把「人工抄录」变成「机器抄录 + 人工只留判断」

背景（诊断结论）：
  历史落盘候选只有 14 条，而目标是 150 款。根因不是图片编辑慢，是选品段的
  「L1 后台抄录 / 主图目视 / 48h 书面确认 / 货代覆盖」全是人工节点。
  其中抄录与主图预筛是可自动化的，只有货代与供应商确认必须人工。

三个子命令：
  pool    批量关键词池拉取（FastMoss 免 token）→ 去重 → 按达人出单率/价格带预筛
  image   按 offerId 批量抓 1688 主图 → 自动抄录成本/库存/起订量/重量/发货城市 → 拼 contact sheet
  seed    把抓取结果写成 select_gate 候选骨架（证据列留空，等人工/多模态回填）

设计纪律（与 image_gate / select_gate 一致）：
  - 只读、不写 ERP、不动采集箱；不替人做判断
  - 抓不到的字段一律留空并在 reason 里写明「拿不到」，**不用推测值补位**
  - 页面上写「承诺48小时发货」不等于供应商书面确认 → seller_48h_proof 仍留空
  - 生成的候选 CSV 必须过 scripts/select_gate.py 才能进 Step 1

用法：
  # 1) 关键词池：18 个当地语言长尾词批量拉，命中量比销量总榜高一个数量级
  python3 scripts/selection_intake.py pool --region VN --keywords-file erp_data/keywords_vn.txt \
      --usd-min 3.5 --usd-max 8.5 --max-creator 100 --min-video 10 \
      --out erp_data/selection_pool.csv

  # 2) 主图 + 自动抄录（offerId 可来自遨虾 search 结果 CSV 或手抄）
  python3 scripts/selection_intake.py image --ids-file erp_data/offers.txt --region VN \
      --out erp_data/selection_intake
  python3 scripts/selection_intake.py image --from-csv erp_data/aibuy_search.csv --region VN

  # 3) 生成候选骨架（证据列留空，闸门会逐条告诉你要补什么）
  python3 scripts/selection_intake.py seed --batch erp_data/selection_intake/prefill.json \
      --out erp_data/selection_candidates.csv --append

退出码：0 全部成功 / 1 部分失败（详见 stdout 的 failed 清单） / 2 输入或参数问题
"""

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SELECTION_YAML = os.path.join(ROOT, "config", "selection.yaml")
DEFAULT_OUT = os.path.join(ROOT, "erp_data", "selection_intake")
CAND_CSV = os.path.join(ROOT, "erp_data", "selection_candidates.csv")
SWIFT_SHEET = os.path.join(HERE, "make_contact_sheet.swift")
COOKIE_FILE = os.path.join(HERE, "ali_cookie.txt")   # 浏览器 Cookie，已被 .gitignore 忽略

DETAIL_URL = "https://detail.1688.com/offer/{oid}.html"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

sys.path.insert(0, HERE)


# ────────────────────────── 极简 YAML 标量读取 ──────────────────────────
def parse_scalar(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "~"):
        return None
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        return [parse_scalar(x) for x in inner.split(",")]
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def load_yaml_scalars(path):
    """返回 {'a.b.c': value}。只处理键值行，忽略注释与列表项，不引入 PyYAML。"""
    out = {}
    if not os.path.exists(path):
        return out
    stack = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            indent = len(line) - len(line.lstrip())
            content = line.strip()
            if content.startswith("- ") or ":" not in content:
                continue
            key, _, val = content.partition(":")
            key = key.strip()
            if not key or " " in key:
                continue
            val = val.split("#")[0].strip()
            while stack and stack[-1][0] >= indent:
                stack.pop()
            path_parts = [k for _, k in stack] + [key]
            if val == "":
                stack.append((indent, key))
            else:
                out[".".join(path_parts)] = parse_scalar(val)
    return out


def thresholds():
    s = load_yaml_scalars(SELECTION_YAML)
    return {
        "ideal": s.get("content.creator_order_rate.shelf_only_ideal", 0.05),
        "shelf_max": s.get("content.creator_order_rate.shelf_friendly_max", 0.30),
        "creator_min": s.get("content.creator_order_rate.creator_driven_min", 0.80),
    }


# ────────────────────────── HTTP ──────────────────────────
# 2026-11-12 实测：python urllib 的 TLS 指纹会被 1688 baxia 风控识别，返回
# 「_____tmd_____/punish」跳转页（HTTP 200 但正文 2.4KB）；同 URL 用 curl 拿到
# 85KB 正常详情页。所以优先 curl，urllib 只作兜底，并显式识别风控页。
CURL = shutil.which("curl")
PUNISH_MARKS = (b"_____tmd_____", b"x5secdata", b"punish")


def is_punish(body):
    return any(m in body for m in PUNISH_MARKS)


def http_get(url, timeout=20, retries=2, referer="https://detail.1688.com/", cookie=None):
    last = None
    for attempt in range(retries + 1):
        try:
            if CURL:
                cmd = [CURL, "-sS", "-L", "--compressed", "--max-time", str(timeout),
                       "-A", UA, "-H", "Accept-Language: zh-CN,zh;q=0.9"]
                if referer:
                    cmd += ["-H", "Referer: " + referer]
                if cookie:
                    cmd += ["-H", "Cookie: " + cookie]
                cmd.append(url)
                proc = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
                if proc.returncode != 0:
                    raise RuntimeError("curl rc=%d %s" % (
                        proc.returncode, (proc.stderr or b"")[:120]))
                body = proc.stdout
            else:
                req = urllib.request.Request(url, headers={
                    "User-Agent": UA,
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    **({"Cookie": cookie} if cookie else {}),
                })
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = resp.read()
            if is_punish(body):
                raise RuntimeError("被 1688 风控拦截（punish 页）")
            return body
        except Exception as e:  # noqa: BLE001 - 需要吞掉所有网络异常做重试
            last = e
            if attempt < retries:
                time.sleep(2.0 + attempt * 2)
    raise RuntimeError(str(last))


def grab_json(text, key):
    """在 HTML 里找 '"key":{...}' 或 '"key":[...]'，做引号感知的括号配对后 json.loads。

    不用正则：1688 页面里同名键出现多次，正则容易截断。
    """
    needle = '"%s"' % key
    start = text.find(needle)
    while start >= 0:
        pos = start + len(needle)
        while pos < len(text) and text[pos] in " \t\r\n:":
            pos += 1
        if pos < len(text) and text[pos] in "[{":
            open_ch = text[pos]
            close_ch = "]" if open_ch == "[" else "}"
            depth, in_str, esc = 0, False, False
            for i in range(pos, len(text)):
                ch = text[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == open_ch:
                    depth += 1
                elif ch == close_ch:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[pos:i + 1])
                        except Exception:  # noqa: BLE001 - 页面里常有非法片段，继续找下一个
                            pass
                        break
        start = text.find(needle, start + 1)
    return None


def grab_str(text, key):
    m = re.search(r'"%s"\s*:\s*("(?:[^"\\]|\\.)*")' % key, text)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:  # noqa: BLE001
        return None


def to_float(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def city_from_location(loc):
    if not loc:
        return None
    loc = str(loc).strip()
    for sep in ("省", "自治区"):
        if sep in loc:
            tail = loc.split(sep, 1)[1].strip()
            for unit in ("市", "县", "区"):
                idx = tail.find(unit)
                if idx >= 0:
                    return tail[: idx + 1]
            return tail or None
    idx = loc.find("市")
    return loc[: idx + 1] if idx >= 0 else loc


# ────────────────────────── 1688 单品抓取 ──────────────────────────
def fetch_offer(oid, cookie=None, timeout=20):
    """抓 1688 详情页并解析出闸门需要的字段。拿不到的字段返回 None。"""
    url = DETAIL_URL.format(oid=oid)
    raw = http_get(url, timeout=timeout, cookie=cookie)
    text = raw.decode("utf-8", errors="ignore")

    if "offerImgList" not in text and "subject" not in text:
        # 风控页 / 下架 / 变版：明确报错，不猜
        raise RuntimeError("页面无商品字段（被风控、已下架或页面改版）")

    images = grab_json(text, "offerImgList") or []
    images = [u for u in images if isinstance(u, str)]
    if not images:
        img_list = grab_json(text, "imageList") or []
        for it in img_list:
            if isinstance(it, dict):
                u = it.get("fullPathImageURI") or it.get("imageURI")
                if u:
                    if u.startswith("img/") or u.startswith("/"):
                        u = "https://cbu01.alicdn.com/" + u.lstrip("/")
                    images.append(u)

    order = grab_json(text, "orderParam") or {}
    trade = grab_json(text, "tradeModel") or {}
    sku_map = grab_json(text, "skuInfoMap") or {}
    freight = grab_json(text, "freightInfo") or {}
    base = grab_json(text, "offerBaseInfo") or {}

    title = grab_str(text, "subject") or ""
    location = None
    m = re.search(r'"location"\s*:\s*"([^"]+)"', text)
    if m:
        location = m.group(1)
    labels = []
    m = re.search(r'"labels"\s*:\s*\[([^\]]*)\]', text)
    if m:
        labels = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))

    # 价格：优先 sku 明细，退到区间价，再退到 tradeModel
    sku_prices, sku_stock = [], 0
    if isinstance(sku_map, dict):
        for spec in sku_map.values():
            if not isinstance(spec, dict):
                continue
            p = to_float(spec.get("priceAmount"))
            if p is not None:
                sku_prices.append(p)
            s = to_float(spec.get("canBookCount"))
            if s is not None:
                sku_stock += s

    range_prices = []
    sku_param = order.get("skuParam") or {}
    for rp in (sku_param.get("skuRangePrices") or []):
        p = to_float(rp.get("price"))
        if p is not None:
            range_prices.append(p)

    all_prices = sku_prices or range_prices or [
        p for p in (to_float(trade.get("minPrice")), to_float(trade.get("maxPrice")))
        if p is not None
    ]
    if all_prices:
        cost = min(all_prices)          # 1 件价（代发买 1 件）
        max_sku = max(all_prices)       # 最高 SKU 价（价格带按它判）
    else:
        cost = max_sku = None

    stock = None
    if sku_stock:
        stock = int(sku_stock)
    elif to_float(order.get("canBookedAmount")) is not None:
        stock = int(to_float(order.get("canBookedAmount")))

    unit_weight = to_float(freight.get("unitWeight"))
    weight_g = int(round(unit_weight * 1000)) if unit_weight else None

    return {
        "offer_id": str(oid),
        "offer_url": url,
        "title": title,
        "seller": base.get("sellerLoginId"),
        "cat_id_1688": base.get("catId") or trade.get("topCategoryId"),
        "cost_cny": cost,
        "max_sku_cost_cny": max_sku,
        "price_points": len(all_prices),
        "stock": stock,
        "min_order_qty": int(to_float(order.get("beginNum"), 1)),
        "weight_g": weight_g,
        "ship_city": city_from_location(location),
        "location": location,
        "labels": labels,
        "support_dropship": "一件代发" in labels,
        "logistics_text": freight.get("logisticsText"),
        "page_says_48h": "48小时" in (freight.get("logisticsText") or ""),
        "marketing_free_postage": freight.get("marketingFreePostage"),
        "free_end_amount": freight.get("freeEndAmount"),
        "delivery_limit_days": freight.get("deliveryLimit"),
        "images": images,
    }


def download_images(item, out_dir, timeout=20):
    """把主图下到 <out>/<offer_id>/main_01.jpg …，返回本地路径列表。"""
    target = os.path.join(out_dir, item["offer_id"])
    os.makedirs(target, exist_ok=True)
    paths = []
    for i, url in enumerate(item["images"], 1):
        ext = ".jpg"
        low = url.lower()
        for cand in (".png", ".webp", ".jpeg"):
            if cand in low:
                ext = cand
                break
        fp = os.path.join(target, "main_%02d%s" % (i, ext))
        if os.path.exists(fp) and os.path.getsize(fp) > 1024:
            paths.append(fp)
            continue
        try:
            data = http_get(url, timeout=timeout, retries=1)
            if len(data) < 1024:
                continue
            with open(fp, "wb") as f:
                f.write(data)
            paths.append(fp)
        except Exception:  # noqa: BLE001 - 单图失败不影响整品
            continue
    return paths


def build_sheets(prefill_path, items, out_dir):
    """为每个品生成一张 contact sheet（供多模态整图分类，替代逐张目视）。"""
    if not os.path.exists(SWIFT_SHEET):
        return {"error": "make_contact_sheet.swift 不存在，跳过拼图"}
    manifest = {}
    for it in items:
        locals_ = it.get("local_images") or []
        if not locals_:
            continue
        sheet = os.path.join(out_dir, "sheet_%s.jpg" % it["offer_id"])
        manifest[sheet] = [
            {"label": "%s_%02d" % (it["offer_id"], i), "path": p}
            for i, p in enumerate(locals_, 1)
        ]
    if not manifest:
        return {"error": "无可拼图"}
    mf_path = os.path.join(out_dir, "manifest.json")
    with open(mf_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    try:
        proc = subprocess.run(
            ["swift", SWIFT_SHEET, mf_path],
            capture_output=True, text=True, timeout=180,
        )
        if proc.returncode != 0:
            return {"error": (proc.stderr or proc.stdout or "").strip()[:200]}
        return {"sheets": len(manifest), "manifest": mf_path}
    except Exception as e:  # noqa: BLE001 - swift 缺失/超时都不该中断主流程
        return {"error": str(e)[:200]}


# ────────────────────────── 子命令：pool ──────────────────────────
POOL_COLS = [
    "keyword", "product_id", "title", "site", "price_local", "usd_equiv",
    "day7_units", "day28_units", "videos", "creators",
    "creator_order_rate", "tier", "content_proof", "creator_comp",
    "category", "shop", "fm_url",
]


def read_keywords(args):
    words = []
    if args.keywords:
        words.extend([w.strip() for w in args.keywords.split(",") if w.strip()])
    if args.keywords_file:
        if not os.path.exists(args.keywords_file):
            sys.exit("[intake] 关键词文件不存在：%s" % args.keywords_file)
        with open(args.keywords_file, encoding="utf-8") as f:
            for line in f:
                line = line.split("#")[0].strip()
                if line:
                    words.append(line)
    return words


def cmd_pool(args):
    import kdzs_fastmoss as fm

    words = read_keywords(args)
    if not words:
        sys.exit("[intake] 需要 --keywords 或 --keywords-file（当地语言长尾词，一次 15–30 个）")

    thr = thresholds()
    seen, kept, stats = set(), [], []
    for w in words:
        try:
            rows, _total = fm.search(
                region=args.region, usd_min=args.usd_min, usd_max=args.usd_max,
                order=args.order, pagesize=args.pagesize, max_pages=args.max_pages,
                min_video=args.min_video, max_creator=args.max_creator, keyword=w,
            )
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001 - 单关键词失败不中断整池
            stats.append((w, 0, 0, "ERR:%s" % str(e)[:60]))
            continue

        raw_n = len(rows)
        new_n = 0
        for r in rows:
            e = fm.enrich(r)
            pid = e.get("productId")
            key = pid or (e.get("title"), e.get("shop"))
            if key in seen:
                continue
            seen.add(key)
            new_n += 1
            if not pid:
                continue
            rate = e.get("creator_order_rate")
            usd = e.get("usd_equiv") or 0
            # 纯达人驱动：不开佣金拿不到量，直接剔除
            if rate is not None and rate >= thr["creator_min"]:
                continue
            # 价格带外的先剔（本店价格带见 pricing.yaml）
            if args.usd_min is not None and usd < args.usd_min:
                continue
            if args.usd_max is not None and usd > args.usd_max:
                continue
            if rate is None or rate <= thr["ideal"]:
                tier = "ideal"      # 几乎全靠自然搜索，不开佣金最该抢
            elif rate <= thr["shelf_max"]:
                tier = "shelf"
            else:
                tier = "mixed"
            kept.append({
                "keyword": w,
                "product_id": pid,
                "title": e.get("title"),
                "site": e.get("site"),
                "price_local": e.get("price_local"),
                "usd_equiv": e.get("usd_equiv"),
                "day7_units": e.get("day7_units"),
                "day28_units": e.get("day28_units"),
                "videos": e.get("videos"),
                "creators": e.get("creators"),
                "creator_order_rate": ("%.2f%%" % (rate * 100)) if rate is not None else "",
                "tier": tier,
                "content_proof": e.get("content_proof"),
                "creator_comp": e.get("creator_comp"),
                "category": e.get("category"),
                "shop": e.get("shop"),
                "fm_url": e.get("fm_url"),
            })
        stats.append((w, raw_n, new_n, len([k for k in kept if k["keyword"] == w])))

    if not kept:
        print("[pool] 全部候选被过滤（达人驱动/价格带外），换关键词或放宽价格带")
        return 2

    out = args.out or os.path.join(ROOT, "erp_data", "selection_pool.csv")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=POOL_COLS)
        w.writeheader()
        w.writerows(kept)

    print("[pool] 关键词 %d 个 → 去重 %d 条 → 入池 %d 条（已剔除达人驱动与带外）"
          % (len(words), len(seen), len(kept)))
    print("%-24s %6s %6s %6s" % ("keyword", "raw", "new", "kept"))
    for w_, raw_n, new_n, k_n in stats:
        print("%-24s %6d %6d %6s" % (w_[:24], raw_n, new_n, k_n))
    tiers = {}
    for k in kept:
        tiers[k["tier"]] = tiers.get(k["tier"], 0) + 1
    print("[pool] 分档：%s" % ", ".join("%s=%d" % (k, v) for k, v in sorted(tiers.items())))
    print("[pool] 落盘 %s" % out)
    print("[pool] 下一步：拿 Tier=ideal/shelf 的标题做 1688 找货（aibuy search 或图搜），"
          "拿到 offerId 后跑 `selection_intake.py image`")
    return 0


# ────────────────────────── 子命令：image ──────────────────────────
def read_offers(args):
    """返回 [{offer_id, title, cost_cny, min_order_qty, image, seller, source}]。

    遨虾 search 的 CSV 自带 offerId + 主图 + 起批价 + 起订量，是当前最稳的取数源
    （1688 详情页对无 Cookie 的直连有 IP 级风控，见文件头说明）。
    """
    recs = []

    def add(oid, **kw):
        oid = str(oid).strip()
        if oid.isdigit():
            recs.append(dict({"offer_id": oid}, **kw))

    if args.ids:
        for x in args.ids.split(","):
            add(x, source="ids")
    if args.ids_file:
        with open(args.ids_file, encoding="utf-8") as f:
            for line in f:
                line = line.split("#")[0].strip()
                if line:
                    add(line.split(",")[0], source="ids")
    if args.from_csv:
        with open(args.from_csv, encoding="utf-8-sig") as f:
            rd = csv.DictReader(f)
            cols = rd.fieldnames or []
            ocol = next((c for c in ("offerId", "offer_id", "offerid", "货源ID", "1688_id")
                         if c in cols), None)
            if not ocol:
                sys.exit("[intake] CSV 里找不到 offerId 列，实际列名：%s" % cols)
            for row in rd:
                add(
                    row.get(ocol),
                    title=(row.get("标题") or row.get("title") or "").strip() or None,
                    cost_cny=to_float(row.get("起批价") or row.get("cost_cny")),
                    min_order_qty=to_float(row.get("起订量") or row.get("min_order_qty")),
                    image=(row.get("图片") or row.get("image") or "").strip() or None,
                    seller=(row.get("供应商") or row.get("seller") or "").strip() or None,
                    source="aibuy",
                )

    seen, out = set(), []
    for r in recs:
        if r["offer_id"] in seen:
            continue
        seen.add(r["offer_id"])
        out.append(r)
    return out


def merge_offer(rec, detail):
    """详情页数据优先；遨虾字段只补详情页拿不到的。不猜。"""
    item = dict(detail) if detail else {}
    item.setdefault("offer_id", rec["offer_id"])
    item["offer_url"] = DETAIL_URL.format(oid=rec["offer_id"])
    if not item.get("title"):
        item["title"] = rec.get("title") or ""
    if item.get("cost_cny") is None:
        item["cost_cny"] = rec.get("cost_cny")
    if item.get("max_sku_cost_cny") is None:
        item["max_sku_cost_cny"] = rec.get("cost_cny")
    if item.get("min_order_qty") is None:
        item["min_order_qty"] = int(rec.get("min_order_qty") or 1)
    if not item.get("images"):
        item["images"] = [rec["image"]] if rec.get("image") else []
    item["seller"] = item.get("seller") or rec.get("seller")
    for k in ("stock", "weight_g", "ship_city", "location", "labels",
              "logistics_text", "page_says_48h", "support_dropship",
              "marketing_free_postage", "free_end_amount", "cat_id_1688"):
        item.setdefault(k, None if k not in ("labels",) else [])
    item["data_source"] = "detail" if detail else (
        "aibuy" if (rec.get("source") == "aibuy" and rec.get("image")) else "ids_only")
    return item


def cmd_image(args):
    recs = read_offers(args)
    if not recs:
        sys.exit("[intake] 需要 --ids / --ids-file / --from-csv 提供 1688 offerId")
    out_dir = args.out or DEFAULT_OUT
    os.makedirs(out_dir, exist_ok=True)

    cookie = args.cookie
    if not cookie and os.path.exists(COOKIE_FILE):
        with open(COOKIE_FILE, encoding="utf-8") as f:
            cookie = f.read().strip() or None

    records, failed, detail_ok = [], [], 0
    for n, rec in enumerate(recs, 1):
        detail = None
        err = None
        if not args.no_detail:
            try:
                detail = fetch_offer(rec["offer_id"], cookie=cookie, timeout=args.timeout)
                detail_ok += 1
            except Exception as e:  # noqa: BLE001 - 详情失败要降级而不是整批失败
                err = str(e)[:80]
        item = merge_offer(rec, detail)
        if not item["images"]:
            failed.append((rec["offer_id"], err or "无可用图片来源"))
            print("[image] %d/%d %s FAIL %s" % (n, len(recs), rec["offer_id"],
                                                (err or "无图")[:60]))
            continue
        if not args.no_download:
            item["local_images"] = download_images(item, out_dir)
        else:
            item["local_images"] = []
        records.append(item)
        print("[image] %d/%d %s OK[%s] 图%d 成本%s 最高SKU%s 库存%s MOQ%s 重%s 城市%s"
              % (n, len(recs), rec["offer_id"], item["data_source"],
                 len(item.get("local_images") or []), item["cost_cny"],
                 item["max_sku_cost_cny"], item["stock"], item["min_order_qty"],
                 item["weight_g"], item["ship_city"]))
        time.sleep(args.sleep)

    prefill = os.path.join(out_dir, "prefill.json")
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "region": args.region,
        "source": {
            "detail": "detail.1688.com 页面解析（offerImgList/orderParam/tradeModel/"
                      "skuInfoMap/freightInfo），需可访问或提供 Cookie",
            "aibuy": "遨虾 search CSV（offerId/主图/起批价/起订量），无风控但只有 1 张图",
        },
        "detail_success": detail_ok,
        "items": records,
        "failed": [{"offer_id": o, "error": e} for o, e in failed],
    }
    with open(prefill, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("[image] 抄录结果 %s（详情页成功 %d / 总 %d；失败 %d）"
          % (prefill, detail_ok, len(records) + len(failed), len(failed)))

    if detail_ok == 0:
        print("[image] ⚠ 全部走降级源：1688 详情页不可访问（IP 级风控）。"
              "要拿全 5 张主图/库存/重量，把浏览器里 detail.1688.com 的 Cookie "
              "存到 scripts/ali_cookie.txt 或传 --cookie；否则 clean_main_images "
              "只能靠遨虾单图 + 人工补证")

    if not args.no_sheet and records:
        res = build_sheets(prefill, records, out_dir)
        if "error" in res:
            print("[image] contact sheet 跳过：%s" % res["error"])
        else:
            print("[image] 已拼 %d 张 contact sheet（多模态整图判定用，替代逐张目视）"
                  % res["sheets"])

    print("[image] 下一步：多模态看 sheet 回填 clean_main_images / desc_rebuildable，"
          "再跑 `selection_intake.py seed`")
    return 1 if failed else 0


# ────────────────────────── 子命令：seed ──────────────────────────
def cmd_seed(args):
    import select_gate

    with open(args.batch, encoding="utf-8") as f:
        payload = json.load(f)
    items = payload.get("items") or []
    if not items:
        sys.exit("[intake] batch 里没有成功抄录的品，先跑 image")

    out = args.out or CAND_CSV
    exists = os.path.exists(out) and os.path.getsize(out) > 2
    header = list(select_gate.TEMPLATE_HEADER)

    rows = []
    for it in items:
        notes = []
        if it.get("page_says_48h"):
            notes.append("页面标注48h(非书面确认)")
        if it.get("support_dropship"):
            notes.append("标签含一件代发")
        if it.get("free_end_amount") not in (None, -1):
            notes.append("满%s件包邮" % it["free_end_amount"])
        proof_bits = []
        if it.get("labels"):
            proof_bits.append("labels=%s" % ",".join(it["labels"]))
        for label, key in (("marketingFreePostage", "marketing_free_postage"),
                           ("freeEndAmount", "free_end_amount"),
                           ("logisticsText", "logistics_text")):
            if it.get(key) is not None:
                proof_bits.append("%s=%s" % (label, it[key]))
        proof = "；".join(proof_bits)
        if it.get("data_source") != "detail":
            proof = (proof + "；" if proof else "") + "遨虾源无包邮/时效字段，需人工核"
        rows.append({
            "offer_id": it["offer_id"],
            "offer_url": it.get("offer_url"),
            "title_zh": (it.get("title") or "")[:100],
            "region": args.region or payload.get("region"),
            "category_id": "",                       # 认领后由 ERP 匹配，人工复核
            "cost_cny": "" if it.get("cost_cny") is None else "%.2f" % it["cost_cny"],
            "max_sku_cost_cny": "" if it.get("max_sku_cost_cny") is None else "%.2f" % it["max_sku_cost_cny"],
            "pieces": 1,
            "clean_main_images": "",                 # 多模态目视回填（闸门硬要 ≥3）
            "desc_rebuildable": "",
            "stock": "" if it.get("stock") is None else it["stock"],
            "min_order_qty": it.get("min_order_qty", 1),
            "ship_transit_days": "",                 # 需查货代，人工
            "seller_48h_proof": "",                  # 必须供应商书面确认，页面标注不算
            "free_shipping": "",                     # 真包邮需人工判（满件包邮是假包邮）
            "free_shipping_proof": proof,
            "weight_g": "" if it.get("weight_g") is None else it["weight_g"],
            "dims_cm": "",                           # 详情参数表里读，或量实物
            "redline_check": "",                     # 人工过红线
            "ship_city": it.get("ship_city") or "",
            "forwarder_city": "",
            "reason": "auto抄录[%s]成本/库存/MOQ/重量/发货城市；待人工：干净主图数、详情可重建、"
                      "48h书面确认、真包邮、货代天数、红线"
                      % it.get("data_source", "?"),
        })
        if it.get("data_source") == "aibuy":
            rows[-1]["reason"] += "；仅遨虾单图，主图证据不足3张需补"
        if notes:
            rows[-1]["reason"] += "；" + "；".join(notes)

    write_header = (not exists) or args.overwrite
    mode = "w" if write_header else "a"
    with open(out, mode, encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        if write_header:
            w.writeheader()
        w.writerows(rows)

    print("[seed] 写入 %d 行 → %s（%s）" % (len(rows), out, "覆盖" if write_header else "追加"))
    print("[seed] 已自动填：cost_cny / max_sku_cost_cny / stock / min_order_qty / weight_g / ship_city / pieces")
    print("[seed] 待人工或多模态填：clean_main_images / desc_rebuildable / seller_48h_proof / "
          "free_shipping / ship_transit_days / redline_check / dims_cm / forwarder_city")
    print("[seed] 下一步：python3 scripts/select_gate.py --csv %s" % out)
    return 0


# ────────────────────────── main ──────────────────────────
def main():
    ap = argparse.ArgumentParser(description="选品取证自动化（关键词池 / 主图抄录 / 候选骨架）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("pool", help="批量关键词池拉取并预筛")
    p1.add_argument("--region", required=True, help="站点：TH/VN/...")
    p1.add_argument("--keywords", default="", help="逗号分隔关键词（当地语言）")
    p1.add_argument("--keywords-file", default="", help="每行一个关键词，# 起注释")
    p1.add_argument("--usd-min", type=float, default=None)
    p1.add_argument("--usd-max", type=float, default=None)
    p1.add_argument("--order", default="day7_units_sold")
    p1.add_argument("--pagesize", type=int, default=100)
    p1.add_argument("--max-pages", type=int, default=2)
    p1.add_argument("--min-video", type=int, default=0)
    p1.add_argument("--max-creator", type=int, default=None)
    p1.add_argument("--out", default="")

    p2 = sub.add_parser("image", help="批量抓主图并抄录闸门字段")
    p2.add_argument("--ids", default="", help="逗号分隔 1688 offerId")
    p2.add_argument("--ids-file", default="", help="每行一个 offerId")
    p2.add_argument("--from-csv", default="", help="从 CSV 的 offer_id 列读（如遨虾 search 结果）")
    p2.add_argument("--region", default="VN")
    p2.add_argument("--out", default="")
    p2.add_argument("--cookie", default="", help="1688 Cookie 串（或存 scripts/ali_cookie.txt）")
    p2.add_argument("--no-detail", action="store_true",
                    help="跳过详情页，只用遨虾 CSV 的字段（无风控但只有 1 张图）")
    p2.add_argument("--timeout", type=int, default=20)
    p2.add_argument("--no-download", action="store_true", help="只抄录字段不下图")
    p2.add_argument("--no-sheet", action="store_true", help="不拼 contact sheet")
    p2.add_argument("--sleep", type=float, default=1.2, help="每品间隔秒数（防 1688 风控）")

    p3 = sub.add_parser("seed", help="生成 select_gate 候选骨架")
    p3.add_argument("--batch", required=True, help="image 产出的 prefill.json")
    p3.add_argument("--out", default="")
    p3.add_argument("--region", default="")
    p3.add_argument("--append", action="store_true", default=True,
                    help="追加到现有 CSV（默认）；--overwrite 才整表重写")
    p3.add_argument("--overwrite", action="store_true")

    args = ap.parse_args()
    if args.cmd == "pool":
        return cmd_pool(args)
    if args.cmd == "image":
        return cmd_image(args)
    if args.cmd == "seed":
        return cmd_seed(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
