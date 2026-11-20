#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""opportunity_to_sourcing.py — 商品机会卡片 → 1688 相似款候选 的编排胶水
（2026-11-19 新增；2026-11-20 检索主通道切到 LinkFox，遨虾降级为 fallback）。

链路位置（用户确认的选品路线）：
  浏览器抄录机会卡片 → opportunity_intake.py 判读 [本脚本内部复用其 judge()]
  → 对 candidate / direction_only 行检索 1688 offer，**双通道**：
     · linkfox（默认，主通道）：Read 模式——Agent 先经 MCP proxy_call
       （alibaba1688/imageSearch / dld/productSearch，WorkBuddy 注入凭证、无本地 token）
       把响应**原样落盘**到 erp_data/linkfox/mcp/，本脚本只读文件，不发网络；
       plan 模式——只出取数任务清单，由 Agent 按清单取数后改 --linkfox read 重跑。
     · aibuy（fallback，免积分）：原 mtop 内部接口子进程路径（逆向、无 SLA）。
  → 落 erp_data/opportunity_sourcing.csv
  → selection_intake.py image --from-csv erp_data/opportunity_sourcing.csv
  → select_gate.py 硬闸 → 正常铺货链路。

输入 CSV = opportunity_intake 的模板（--init-template）再加两列：
  image_url       机会卡片主图 URL（右键复制图片地址；有则走图搜）
  search_keyword  1688 检索词（**中文品类词**优先，如「围裙 厨房 多口袋」；有则走词搜）
  两列都缺的行 → NEED_INPUT，不猜。

通道纪律（2026-11-20 切换决定，依据当日实测）：
- LinkFox 走 WorkBuddy MCP 连接器，凭证平台注入，不存在 mtop token 过期问题；
  返回**第三方快照（THIRD_PARTY_ONLY）**，且 imageSearch 带 sendGoodsAddressText（发货地），
  词搜带 company——发货城市证据链 aibuy 给不全（companyCity 实为企业注册地）。
- **linkfox 结果不自动取 topN**：以图搜图混入无关品（同 aibuy 纪律），且排序字段口径
  与 mtop 不同（monthSold vs salesQuantity）——默认保留检索原序（LinkFox 按月销倒序返回），
  --rank-by sales 才按销量降序重排。同款判定始终归人工/多模态。
- 输出行带 `data_source`（linkfox|aibuy），**两源数字不混用同一评分维度**；
  失败不跨通道自动补位——如实标 SEARCH_FAIL + next_step 提示 --channel aibuy 重试，
  防止静默混源。

边界与纪律（延续 2026-11-19 版）：
- 本脚本只做「判读 + 读检索结果/发检索请求 + 汇总」，**不做同款判定**。
- direction_only 行同样出候选，但输出里保留 verdict 列，后续闸门不得因"官方机会品"跳闸。
- aibuy 网络调用全部经 scripts/aibuy_rank_client.py 子进程（复用其 mtop 握手与 ≥0.6s 限速）；
  LinkFox 严禁脚本直连网关（硬约束：只能经 WorkBuddy MCP proxy_call，见 linkfox_sourcing.py 头注）。
- --dry-run 只列计划不发网络。

用法：
  python3 scripts/opportunity_to_sourcing.py --dry-run --csv erp_data/opportunity_signals.csv
  # ① LinkFox 主通道（两段式）：
  python3 scripts/opportunity_to_sourcing.py --linkfox plan   # 出取数任务清单
  #   → Agent 按清单调 MCP proxy_call，响应原样写 erp_data/linkfox/mcp/<task_id>.json
  python3 scripts/opportunity_to_sourcing.py --linkfox read    # 读落盘 → 候选 CSV
  # ② 遨虾 fallback（免积分，token 可能过期）：
  python3 scripts/opportunity_to_sourcing.py --channel aibuy
退出码：0 正常；2 输入问题；3 全部检索失败（linkfox read 下=落盘缺失；aibuy 下=通道疑似被风控）
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from opportunity_intake import load_yaml, judge, CONFIG as INTAKE_CONFIG  # noqa: E402
from pricing_calc import load_config as load_pricing                      # noqa: E402
from selection_intake import city_from_location                           # noqa: E402

ROOT = os.path.dirname(HERE)
AIBUY = os.path.join(HERE, "aibuy_rank_client.py")
DEFAULT_CSV = os.path.join(ROOT, "erp_data", "opportunity_signals.csv")
DEFAULT_OUT = os.path.join(ROOT, "erp_data", "opportunity_sourcing.csv")
AIBUY_OUT_DIR = os.path.join(ROOT, "aibuy_data", "opportunity")
LINKFOX_MCP_DIR = os.path.join(ROOT, "erp_data", "linkfox", "mcp")
LINKFOX_TASKS = os.path.join(ROOT, "erp_data", "linkfox", "sourcing_tasks.json")

OUT_COLS = ["opportunity_title", "verdict", "search_mode", "rank",
            "offer_id", "offer_title", "start_price_cny", "month_sold",
            "begin_qty", "source_factory", "company", "offer_url", "image_url",
            "ship_city_raw", "consign_price_cny",
            "late_ship_compensate", "pickup_24h_rate", "pay_ord_30d", "rebuy_30d",
            "data_source", "source_site", "next_step"]

IMG_ROUTE = "alibaba1688/imageSearch"
KW_ROUTE = "dld/productSearch"


def norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def fnum(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return -1.0


def slug(s, limit=40):
    return re.sub(r"[^\w\u4e00-\u9fff-]+", "_", s)[:limit].strip("_") or "x"


# ────────────────────────── LinkFox：任务清单 / 读落盘 ──────────────────────────
def build_tasks(rows_decided):
    """rows_decided: [(row, verdict)]，返回 linkfox 取数任务清单（不含任何网络调用）。"""
    tasks = []
    for r, v in rows_decided:
        if v == "reject":
            continue
        site = str(r.get("source_site") or "").strip().upper()
        img = str(r.get("image_url") or "").strip()
        kw = str(r.get("search_keyword") or "").strip()
        if not img and not kw:
            continue
        if img:
            tasks.append({"task_id": "image_" + slug(os.path.basename(img.split("?")[0])),
                          "mode": "image", "toolName": IMG_ROUTE,
                          "arguments": {"imageUrl": img, "page": 1, "pageSize": 20},
                          "save_to": os.path.join(LINKFOX_MCP_DIR, "image_%s.json" % slug(os.path.basename(img.split("?")[0]))),
                          "opportunity_title": r.get("title"), "verdict": v, "source_site": site})
        if kw:
            tasks.append({"task_id": "kw_" + slug(kw),
                          "mode": "keyword", "toolName": KW_ROUTE,
                          "arguments": {"keyWord": kw, "pageIndex": 1, "pageSize": 20,
                                        "sortField": "orderCount30d", "sortType": "desc"},
                          "save_to": os.path.join(LINKFOX_MCP_DIR, "kw_%s.json" % slug(kw)),
                          "opportunity_title": r.get("title"), "verdict": v, "source_site": site})
    return tasks


def load_linkfox_payload(path):
    """读 MCP 落盘 JSON（校验 errcode），返回 products[]；错误时抛 ValueError(原因)。"""
    if not os.path.exists(path):
        raise ValueError("落盘不存在")
    with open(path, encoding="utf-8") as f:
        try:
            j = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError("JSON 解析失败: %s" % e)
    if str(j.get("errcode")) not in ("200", "0"):
        raise ValueError("errcode=%s errmsg=%s" % (j.get("errcode"), j.get("errmsg")))
    products = j.get("products")
    if not isinstance(products, list):
        raise ValueError("无 products[]，顶层键=%s" % list(j.keys())[:10])
    return products


def bailian_to_rows(items):
    """百炼 find_product 原始 items → 统一行结构。履约标签单独带出（select_gate 机验档吃）。

    monthSold 列在 bailian 行 = pay_ord_cnt_30d（30 天成交单数），口径与 linkfox(salesQuantity)/
    aibuy(monthSold) 不同 → 禁跨通道混排（同文件头纪律）。
    揽收率 0.0/空 → 保留原值，消费侧按 UNKNOWN 处理（实测与无数据不可区分，不得判 FAIL）。
    """
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        svc = it.get("serviceTags") or {}
        old = it.get("oldReputationTags") or {}
        rate = svc.get("lgt_3m_24h_avg")
        out.append({
            "offerId": str(it.get("itemId") or ""),
            "title": norm(it.get("title")),
            "price": it.get("currentPrice") if it.get("currentPrice") is not None else "",
            "monthSold": old.get("pay_ord_cnt_30d") or "",
            "beginQuantity": it.get("quantityBegin") if it.get("quantityBegin") is not None else "",
            "sourceFactory": "",
            "companyName": norm(it.get("company")),
            "link": norm(it.get("detailUrl")),
            "imageUrl": norm(it.get("imageUrl")),
            "shipCityRaw": "",   # bailian 无发货地 → 空，城市靠 linkfox/详情页补
            "consignPrice": "",
            "lateShipCompensate": svc.get("is_late_ship_compensate") or "",
            "pickup24h": "" if rate in (None, "") else rate,
            "payOrd30d": old.get("pay_ord_cnt_30d") or "",
            "rebuy30d": old.get("rebuy_cnt_30d") or "",
        })
    return [r for r in out if r["offerId"].isdigit()]


def linkfox_to_rows(products):
    """LinkFox products[] → 本脚本统一行结构（保留原序，不排序、不截断，人工判同款）。

    字段映射（2026-11-20 实测 imageSearch/dld 返回）：
      offerId / productCode → offer_id；price → start_price_cny；
      salesQuantity → month_sold（dataType 为周期口径，≠ aibuy monthSold）；
      quantityBegin → begin_qty；company → company；
      productUrl|asinUrl → offer_url；sendGoodsAddressText → ship_city_raw（原样，归一化归 selection_intake）。
    缺失一律留空，不猜、不填 0。
    """
    out = []
    for p in products:
        if not isinstance(p, dict):
            continue
        out.append({
            "offerId": str(p.get("offerId") or p.get("productCode") or ""),
            "title": norm(p.get("title")),
            "price": p.get("price") if p.get("price") is not None else "",
            "monthSold": p.get("salesQuantity") if p.get("salesQuantity") is not None else "",
            "beginQuantity": p.get("quantityBegin") if p.get("quantityBegin") is not None else "",
            "sourceFactory": p.get("sellerIdentities") or "",   # 超级工厂/实力商家… 是身份串，非布尔
            "companyName": norm(p.get("company")),
            "link": norm(p.get("productUrl") or p.get("asinUrl")),
            "imageUrl": norm(p.get("imageUrl")),
            "shipCityRaw": norm(p.get("sendGoodsAddressText")),
            "consignPrice": p.get("consignPrice") if p.get("consignPrice") is not None else "",
        })
    return out


# ────────────────────────── aibuy fallback（原路径） ──────────────────────────
def run_aibuy(args, timeout=90):
    """调 aibuy_rank_client.py 子命令，返回 (ok, rows, note)。rows 读其落盘 JSON。"""
    cmd = [sys.executable, AIBUY] + args
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, [], "timeout"
    if p.returncode != 0:
        return False, [], (p.stderr or p.stdout or "")[-200:].strip()
    # 找最后一个 saved 行定位产物
    saved = [l for l in p.stdout.splitlines() if l.startswith("saved ")]
    if not saved:
        return False, [], "no saved file: " + p.stdout[-150:]
    base = saved[-1].split("->")[-1].split(".json")[0].strip()
    path = base + ".json"
    if not os.path.exists(path):
        return False, [], "missing " + path
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    return True, rows, ""


def dedup_sort(rows, top):
    """aibuy 专用：offerId 去重 + monthSold 降序预排 topN（原 2026-11-19 行为）。"""
    best = {}
    for r in rows:
        oid = str(r.get("offerId") or "").strip()
        if not oid:
            continue
        if oid not in best or fnum(r.get("monthSold")) > fnum(best[oid].get("monthSold")):
            best[oid] = r
    out = sorted(best.values(), key=lambda r: fnum(r.get("monthSold")), reverse=True)
    return out[:top]


def dedup_keep_order(rows, top):
    """linkfox 专用：offerId 去重（保留首次=检索最高排名行），**严格保留检索原序**。

    LinkFox 返回本身已按排序字段倒序（imageSearch 默认月销、dld 传 orderCount30d）——
    重排会换口径（salesQuantity 件数 ≠ orderCount30d 笔数），默认不碰。
    """
    seen, out = set(), []
    for r in rows:
        oid = str(r.get("offerId") or "").strip()
        if not oid or oid in seen:
            continue
        seen.add(oid)
        out.append(r)
    return out[:top]


def rank_rows(rows, top, rank_by):
    if rank_by == "source":
        return dedup_keep_order(rows, top)
    return dedup_sort(rows, top)


# ────────────────────────── 主流程 ──────────────────────────
def decide_rows(a):
    """读输入 CSV → [(原始行, verdict, why, site, modes)]；reject/NEED_INPUT 也带出。"""
    with open(a.csv, encoding="utf-8-sig", newline="") as f:
        raw = list(csv.DictReader(f))
    cfg = load_yaml(INTAKE_CONFIG)
    pricing = load_pricing()
    decided = []
    for r in raw:
        site = (a.site or str(r.get("source_site") or "")).strip().upper()
        v, why = judge(r, cfg, pricing)
        modes = []
        if str(r.get("image_url") or "").strip():
            modes.append("image")
        if str(r.get("search_keyword") or "").strip():
            modes.append("keyword")
        decided.append({"row": r, "verdict": v, "why": why, "site": site, "modes": modes})
    return raw, decided


def base_of(d):
    return {"opportunity_title": d["row"].get("title"), "verdict": d["verdict"],
            "source_site": d["site"], "reasons": d["why"]}


def emit(out_rows, a):
    if not out_rows:
        return
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=OUT_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(out_rows)
    print("[out] %d 行候选 → %s" % (len(out_rows), a.out))
    print("[next] 逐行执行 next_step 列命令（自动抄 1688 成本/库存/主图/发货城市），"
          "回填判断后进 select_gate.py 硬闸。**机会品不享受任何跳闸特权。**")


def cand_row(base, d_modes, i, p, site, channel):
    """统一候选行构造（三通道共用，缺的字段留空不猜）。"""
    return dict(base, search_mode="+".join(d_modes), rank=i,
                offer_id=p.get("offerId", ""),
                offer_title=p.get("title", ""),
                start_price_cny=p.get("price", ""),
                month_sold=p.get("monthSold", ""),
                begin_qty=p.get("beginQuantity", ""),
                source_factory=p.get("sourceFactory", ""),
                company=p.get("companyName", ""),
                offer_url=p.get("link", ""),
                image_url=p.get("imageUrl", ""),
                ship_city_raw=p.get("shipCityRaw", ""),
                consign_price_cny=p.get("consignPrice", ""),
                late_ship_compensate=p.get("lateShipCompensate", ""),
                pickup_24h_rate=p.get("pickup24h", ""),
                pay_ord_30d=p.get("payOrd30d", ""),
                rebuy_30d=p.get("rebuy30d", ""),
                data_source=channel,
                next_step=next_step_of(p, site))


def next_step_of(p, site):
    ns = "selection_intake.py image --ids %s --region %s" % (p.get("offerId"), site)
    if not norm(p.get("shipCityRaw")) and not norm(p.get("companyCity")):
        ns += "（发货地缺→浏览器详情页补 ship_city）"
    return ns


def main():
    ap = argparse.ArgumentParser(description="机会卡片 → 1688 候选 编排（LinkFox 主通道 / aibuy fallback）")
    ap.add_argument("--csv", default=DEFAULT_CSV, help="机会卡片抄录 CSV（intake 模板 + image_url/search_keyword）")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--top", type=int, default=8, help="每张卡片保留的 offer 数（默认 8）")
    ap.add_argument("--site", default="", help="覆盖行内 source_site（一般不填）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划（判读+将发的检索），不发网络、不写盘")
    ap.add_argument("--channel", choices=["bailian", "linkfox", "aibuy"], default="linkfox",
                    help="检索通道：linkfox=MCP 落盘（默认，需 --linkfox read 前置取数，带发货城市）；"
                         "bailian=百炼 1688 官方 MCP（脚本直连、¥0.058/次、带晚发必赔/揽收率官方履约标签，"
                         "无城市/多图）；aibuy=mtop 内部接口（免积分 fallback，token 会过期）")
    ap.add_argument("--linkfox", choices=["plan", "read"], default="plan",
                    help="linkfox 模式：plan=生成 MCP 取数任务清单（默认）；read=读 erp_data/linkfox/mcp/ 落盘产候选")
    ap.add_argument("--rank-by", choices=["source", "sales"], default="source",
                    help="linkfox 排序：source=保留检索原序（默认，LinkFox 返回已按月销倒序）；"
                         "sales=按 salesQuantity 降序重排（仅同通道内比较）")
    a = ap.parse_args()

    if not os.path.exists(a.csv):
        print("[err] 找不到 %s（先跑 opportunity_intake.py --init-template 并抄录）" % a.csv,
              file=sys.stderr)
        return 2
    raw, decided = decide_rows(a)
    out_rows, plan = [], []
    search_attempts = search_ok = 0

    # ── dry-run：只出计划 ──
    if a.dry_run:
        for d in decided:
            r, v = d["row"], d["verdict"]
            if v == "reject":
                plan.append("%s → REJECT（%s）跳过检索" % (r.get("title"), d["why"].split(";")[0]))
            elif not d["modes"]:
                plan.append("%s → %s NEED_INPUT：缺 image_url 与 search_keyword，转人工补" % (r.get("title"), v))
            else:
                kw = str(r.get("search_keyword") or "").strip()
                if a.channel == "bailian":
                    plan.append("%s → %s 计划检索[bailian 官方MCP直连 ¥0.058/次]: %s%s → 取 top%d"
                                % (r.get("title"), v, "+".join(d["modes"]),
                                   " kw=%r" % kw if kw else "", a.top))
                elif a.channel == "linkfox":
                    src = "linkfox:%s" + ("(需先 --linkfox plan → MCP 取数 → read)" if a.linkfox == "plan" else "(读落盘)")
                    plan.append("%s → %s 计划检索: %s%s → %s" % (
                        r.get("title"), v, "+".join(d["modes"]),
                        " kw=%r" % kw if kw else "", src))
                else:
                    plan.append("%s → %s 计划检索[aibuy fallback]: %s%s → 取 top%d" % (
                        r.get("title"), v, "+".join(d["modes"]),
                        " kw=%r" % kw if kw else "", a.top))
        print("\n".join("[plan] " + l for l in plan))
        return 0

    # ── linkfox plan：发判读后的取数任务清单，Agent 按清单走 MCP ──
    if a.channel == "linkfox" and a.linkfox == "plan":
        tasks = build_tasks([(d["row"], d["verdict"]) for d in decided])
        for d in decided:
            if d["verdict"] == "reject":
                plan.append("%s → REJECT（%s）跳过" % (d["row"].get("title"), d["why"].split(";")[0]))
            elif not d["modes"]:
                plan.append("%s → %s NEED_INPUT：缺 image_url 与 search_keyword" % (d["row"].get("title"), d["verdict"]))
                out_rows.append(dict(base_of(d), search_mode="none", rank="", offer_id="",
                                     next_step="NEED_INPUT：浏览器补抄卡片图URL或填中文品类词"))
        os.makedirs(os.path.dirname(LINKFOX_TASKS), exist_ok=True)
        with open(LINKFOX_TASKS, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)
        print("\n".join("[plan] " + l for l in plan))
        print("[tasks] %d 个取数任务 → %s" % (len(tasks), LINKFOX_TASKS))
        print("[tasks] Agent 侧执行：逐任务调 LinkFox MCP proxy_call(toolName, arguments)，"
              "响应**原样**写入 save_to 路径（顶层含 errcode/products）；全部落盘后重跑 --linkfox read")
        emit(out_rows, a)
        return 0

    # ── linkfox read：读 MCP 落盘 → 候选 ──
    if a.channel == "linkfox":
        tasks = {}
        if os.path.exists(LINKFOX_TASKS):
            with open(LINKFOX_TASKS, encoding="utf-8") as f:
                for t in json.load(f):
                    tasks[(t["mode"], t["opportunity_title"])] = t
        failed_cards = 0
        for d in decided:
            r, v = d["row"], d["verdict"]
            if v == "reject" or not d["modes"]:
                if not d["modes"] and v != "reject":
                    out_rows.append(dict(base_of(d), search_mode="+".join(d["modes"]) or "none",
                                         rank="", offer_id="",
                                         next_step="NEED_INPUT：浏览器补抄卡片图URL或填中文品类词"))
                continue
            merged, notes = [], []
            for mode in d["modes"]:
                search_attempts += 1
                t = tasks.get((mode, r.get("title")))
                path = t["save_to"] if t else None
                if not path:
                    notes.append("%s_no_task:任务清单里没有这行（重跑 --linkfox plan）" % mode)
                    continue
                try:
                    products = load_linkfox_payload(path)
                except ValueError as e:
                    notes.append("%s_read_fail:%s(%s)" % (mode, os.path.basename(path), e))
                    continue
                search_ok += 1
                merged += linkfox_to_rows(products)
            picks = rank_rows(merged, a.top, a.rank_by) if merged else []
            if not picks:
                failed_cards += 1
                plan.append("%s → %s SEARCH_FAIL（linkfox 0 条：%s）" % (r.get("title"), v, ";".join(notes) or "空结果"))
                out_rows.append(dict(base_of(d), search_mode="+".join(d["modes"]), rank="", offer_id="",
                                     next_step="SEARCH_FAIL(linkfox)：" + (";".join(notes) or "结果为空")
                                     + "；可 --channel aibuy 重试一次（免积分，token 会过期），再失败转人工"))
                continue
            for i, p in enumerate(picks, 1):
                out_rows.append(cand_row(base_of(d), d["modes"], i, p, d["site"], "linkfox"))
            plan.append("%s → %s linkfox 检索 %d 条、去重取 top%d（排序=%s）%s"
                        % (r.get("title"), v, len(merged), len(picks), a.rank_by,
                           ("WARN:" + ";".join(notes)) if notes else ""))
        print("\n".join("[done] " + l for l in plan))
        emit(out_rows, a)
        if search_attempts and not search_ok:
            print("[warn] 全部落盘不可读——先确认已按 sourcing_tasks.json 完成 MCP 取数落盘；"
                  "仍不通再 --channel aibuy 走 fallback", file=sys.stderr)
            return 3
        return 0

    # ── bailian：百炼 1688 官方 MCP，脚本直连（凭证在 skill .env） ──
    if a.channel == "bailian":
        import bailian_sourcing as bs
        try:
            key = bs.load_key()
        except SystemExit as e:
            print("[err] bailian 凭证问题: %s" % e, file=sys.stderr)
            return 2
        for d in decided:
            r, v = d["row"], d["verdict"]
            if v == "reject":
                plan.append("%s → REJECT（%s）跳过检索" % (r.get("title"), d["why"].split(";")[0]))
                continue
            if not d["modes"]:
                plan.append("%s → %s NEED_INPUT：缺 image_url 与 search_keyword，转人工补" % (r.get("title"), v))
                out_rows.append(dict(base_of(d), search_mode="none", rank="", offer_id="",
                                     next_step="NEED_INPUT：浏览器补抄卡片图URL或填中文品类词"))
                continue
            img = str(r.get("image_url") or "").strip()
            kw = str(r.get("search_keyword") or "").strip()
            merged, notes = [], []
            # 同款优先：有图先图搜（scoreLevel=high 机器预筛相关性），词搜补充
            if "image" in d["modes"]:
                search_attempts += 1
                try:
                    items = bs.find_product(key, {"imageUrl": img, "pageSize": a.top,
                                                   "scoreLevel": "high"})
                    got = bailian_to_rows(items)
                    if got:
                        search_ok += 1
                        merged += got
                    else:
                        notes.append("image_search_empty")
                except SystemExit as e:   # bailian_sourcing 对 401/404/业务错误 sys.exit
                    notes.append("image_search_fail:%s" % str(e)[:120])
            if "keyword" in d["modes"]:
                search_attempts += 1
                try:
                    items = bs.find_product(key, {"query": kw, "pageSize": a.top})
                    got = bailian_to_rows(items)
                    if got:
                        search_ok += 1
                        merged += got
                    else:
                        notes.append("kw_search_empty")
                except SystemExit as e:
                    notes.append("kw_search_fail:%s" % str(e)[:120])
            picks = dedup_keep_order(merged, a.top)
            if not picks:
                plan.append("%s → %s SEARCH_FAIL（bailian 0 条：%s）" % (r.get("title"), v, ";".join(notes) or "空结果"))
                out_rows.append(dict(base_of(d), search_mode="+".join(d["modes"]), rank="", offer_id="",
                                     next_step="SEARCH_FAIL(bailian)：" + (";".join(notes) or "结果为空")
                                     + "；可 --channel linkfox 重试（带城市证据），再失败转人工"))
                continue
            for i, p in enumerate(picks, 1):
                out_rows.append(cand_row(base_of(d), d["modes"], i, p, d["site"], "bailian"))
            plan.append("%s → %s [bailian 官方MCP] 检索 %d 条、去重取 top%d %s"
                        % (r.get("title"), v, len(merged), len(picks),
                           ("WARN:" + ";".join(notes)) if notes else ""))
        print("\n".join("[done] " + l for l in plan))
        emit(out_rows, a)
        if search_attempts and not search_ok:
            print("[warn] 全部 bailian 调用失败——看上面 fail 原文（401=凭证/404=开通中/额度尽=计费），勿重试风暴",
                  file=sys.stderr)
            return 3
        return 0

    # ── aibuy fallback：原 mtop 子进程路径 ──
    for d in decided:
        r, v = d["row"], d["verdict"]
        if v == "reject":
            plan.append("%s → REJECT（%s）跳过检索" % (r.get("title"), d["why"].split(";")[0]))
            continue
        if not d["modes"]:
            plan.append("%s → %s NEED_INPUT：缺 image_url 与 search_keyword，转人工补" % (r.get("title"), v))
            out_rows.append(dict(base_of(d), search_mode="none", rank="", offer_id="",
                                 next_step="NEED_INPUT：浏览器补抄卡片图URL或填中文品类词"))
            continue
        img = str(r.get("image_url") or "").strip()
        kw = str(r.get("search_keyword") or "").strip()
        merged, notes = [], []
        if "image" in d["modes"]:
            search_attempts += 1
            ok, got, note = run_aibuy(["search-image", "--image-url", img,
                                       "--pages", "1", "--out", AIBUY_OUT_DIR])
            if ok:
                search_ok += 1
                for g in got:
                    g.setdefault("shipCityRaw", g.get("companyCity", ""))
                merged += got
            else:
                notes.append("image_search_fail:%s" % note)
        if "keyword" in d["modes"]:
            search_attempts += 1
            ok, got, note = run_aibuy(["search", "--keyword", kw,
                                       "--pages", "1", "--out", AIBUY_OUT_DIR])
            if ok:
                search_ok += 1
                for g in got:
                    g.setdefault("shipCityRaw", g.get("companyCity", ""))
                merged += got
            else:
                notes.append("kw_search_fail:%s" % note)
        picks = dedup_sort(merged, a.top)
        if not picks:
            plan.append("%s → %s SEARCH_FAIL（0 条可用：%s）" % (r.get("title"), v, ";".join(notes) or "空结果"))
            out_rows.append(dict(base_of(d), search_mode="+".join(d["modes"]), rank="", offer_id="",
                                 next_step="SEARCH_FAIL(aibuy)：" + (";".join(notes) or "结果为空，换词/换图重试一次，再失败转人工")))
            continue
        for i, p in enumerate(picks, 1):
            out_rows.append(cand_row(base_of(d), d["modes"], i, p, d["site"], "aibuy"))
        plan.append("%s → %s [aibuy fallback] 检索 %d 条、去重取 top%d %s"
                    % (r.get("title"), v, len(merged), len(picks),
                       ("WARN:" + ";".join(notes)) if notes else ""))
    print("\n".join("[done] " + l for l in plan))
    emit(out_rows, a)
    if search_attempts and not search_ok:
        print("[warn] 全部检索失败——aibuy mtop 可能被风控，看上面 fail 原文，勿重试风暴",
              file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
