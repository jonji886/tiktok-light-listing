#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""precheck.py — 采集前预检 + 全图集判级 + 自动生成 main.txt

把 2026-11-25 一天踩的坑全部前置成闸门：

  A 段（采集前，无需联网）
    ① 价格：pool.csv 里 consign_price < 2 → 弃（收纳袋/挂钩/耗材类结构性过不了）
    ② 主图指纹：主图 URL 已在 erp_data/used_main_images.json → 弃（防重复铺货）

  B 段（采集后，读 detail）
    ③ 唯一 seller_sku：全重复 → 提示必须用 --sku-map-seq（防 dict 覆盖）
    ④ 库存：无任何 SKU 库存 >0 → 弃
    ⑤ 价格：source_price < 2 → 弃
    ⑥ 体积/重量：超 2400cm³ / 400g → 弃，并用 config/weight_by_category 兜底缺失值

  C 段（全图集判级 —— 今天最大的单点突破）
    ⑦ scan 三通道（main+desc+sku）→ 本地 OCR 判级 → 统计「整册干净数」
    ⑧ 整册干净 <5 → 弃；≥5 → 按 main>sku>desc 优先级、content_sha256 去重挑 5 张
       → 写 <pipeline>/edit/mains.txt（可直接接 publish_one next）

用法：
  python3 scripts/precheck.py --ids 123,456,789 --site MY
  python3 scripts/precheck.py --pool erp_data/pool_1125.csv --site MY --max 5
  python3 scripts/precheck.py --ids ... --no-collect     # 只跑 A 段（有 pool 价时）
  python3 scripts/precheck.py --ids ... --offline        # 跳过判级（只看结构闸）

输出：
  erp_data/_run/precheck/<YYYYmmdd_HHMM>.json   每品结论 + 弃品原因
  erp_data/used_main_images.json                主图指纹库（自动维护）

退出码：0 全部可发 / 1 有弃品 / 2 参数错误
"""
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "config"))

import weight_by_category as WT      # noqa: E402
import word_pool as WP               # noqa: E402
import category_map as CM            # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "scripts"))
import timing                        # noqa: E402  耗时账本（scripts/timing.py）
from ms_terms import check_sku_selectable  # noqa: E402  D3：与 batch_listing+ttl_edit 同口径

USED_DB = "erp_data/used_main_images.json"
MIN_CLEAN_TOTAL = 5      # 整册干净图下限
MIN_SRC_PRICE = 2.0      # 来源价下限（¥）
BATCH_SIZE = 40          # C 段批量 OCR 每组图数；swift 冷启动摊薄，实测 5x 加速


# ────────────────────────── 基础工具 ──────────────────────────
def run(cmd, timeout=420):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "[timeout] " + " ".join(cmd)


def load_json(p, default=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def save_json(p, obj):
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def st_path(oid):
    return "erp_data/_run/pipeline/%s.json" % oid


# ────────────────────────── 主图指纹库 ──────────────────────────
def load_used():
    return load_json(USED_DB, {})


def add_used(used, urls, oid):
    for u in urls:
        if u:
            used[u] = oid


# ────────────────────────── A 段：采集前 ──────────────────────────
def precheck_pool(row, used):
    """pool 行 → (ok, reasons)。row 需含 offer_id，可选 consign_price/image_url。"""
    reasons = []
    try:
        price = float(row.get("consign_price") or 0)
    except (TypeError, ValueError):
        price = 0.0
    if price and price < MIN_SRC_PRICE:
        reasons.append("A① 来源价 ¥%.2f < ¥%.0f" % (price, MIN_SRC_PRICE))
    img = (row.get("image_url") or "").strip()
    if img and img in used:
        reasons.append("A② 主图已用过（offerId=%s），重复铺货风险" % used[img])
    return (not reasons), reasons


# ────────────────── A 段增强（P1-4，2026-11-26）──────────────────
# 候选带 ship_city / qty_begin / price_low 列时，认领前就砍掉注定出局的品：
#   - 发货市不在已验证转仓白名单 → 跨省时效大概率>1天（本批 800089478561 菏泽
#     一路烧到 select_gate 才死，全程 ~6min）
#   - qty_begin>1 → MOQ 红线（本批 734033984225 起批2，B 段才杀）
#   - price_low<¥2 → SKU 最低价出带（本批 B⑤ 主要死因；图搜 consign 与 SKU
#     最低价可差 3 倍——回填 price_low 取真实价区间下限，别取 consign）
# 无列 = 行为与改前完全一致（向后兼容）。
_FWD_WHITELIST = {  # 供应商注册城市 → 绑定货代仓（见 references 货代段）
    "金华": "义乌市", "义乌": "义乌市", "温州": "义乌市", "杭州": "义乌市",
    "宁波": "义乌市", "台州": "义乌市", "苏州": "义乌市", "上海": "义乌市",
    "石家庄": "义乌市", "廊坊": "义乌市", "沧州": "义乌市", "晋州": "义乌市",
    "潮州": "东莞市", "汕头": "东莞市", "揭阳": "东莞市", "广州": "东莞市",
    "深圳": "东莞市", "东莞": "东莞市", "佛山": "东莞市",
}


def precheck_enriched(row):
    """A 段第二层：pool 行带补充列时做发货地/MOQ/价区间预判。返回 (ok, reasons)。"""
    reasons = []
    sc = (row.get("ship_city") or "").strip()
    if sc:
        # 兼容「金华」（MY10 纯城市名）与「浙江省金华市」（vn20 全称）：
        # 只要包含任一白名单城市即视为已验证转仓，不再依赖 rstrip("市") 的精确等值。
        if not any(city in sc for city in _FWD_WHITELIST):
            reasons.append("A③ 发货地「%s」不在已验证转仓白名单，跨省时效大概率>1天"
                           "——先补 ≤1 天到仓证据，否则换货源" % sc)
    try:
        qb = int(float(row.get("qty_begin") or 0))
    except (TypeError, ValueError):
        qb = 0
    if qb > 1:
        reasons.append("A④ 起批量 %d>1（一件代发红线，B 段必杀）" % qb)
    try:
        plow = float(row.get("price_low") or 0)
    except (TypeError, ValueError):
        plow = 0.0
    if plow and plow < MIN_SRC_PRICE:
        reasons.append("A⑤ SKU最低价 ¥%.2f < ¥%.0f（B⑤ 大概率出带）" % (plow, MIN_SRC_PRICE))
    return (not reasons), reasons


# ────────────────────────── B 段：采集 + 结构闸 ──────────────────────────
def ensure_detail(oid, site):
    """复用 publish_one 状态机跑到 detail 完成。返回 (st, out_text)。"""
    sp = st_path(oid)
    if not os.path.exists(sp):
        run(["python3", "scripts/publish_one.py", "start", "--offer-id", oid, "--site", site])
    st = load_json(sp)
    guard = 0
    while guard < 4:
        guard += 1
        st = load_json(sp)
        stage = st.get("stage")
        if stage in ("edit_dry", "edit_save", "review_img", "apply", "guards",
                     "publish", "wait", "verify4", "done"):
            break
        rc, out = run(["python3", "scripts/publish_one.py", "next", "--offer-id", oid])
        st = load_json(sp)
        if st.get("stage") == stage and rc != 0:
            return st, out          # 卡住，原样返回
    return load_json(sp), ""


def batch_funnel(oids, site, jobs=3):
    """--b2-batch：波内 B0 由「逐品状态机」改「一次批量接口」。
    实测单品 B0=26.8s 里接口调用只占 ~8s，其余全是逐品轮询；批量路径：
      批量collect(500/次) → 批量claim(接口支持多numIids) → 一轮ttl-list匹配全部
      → 并行补 next 到 detail。
    语义与 ensure_detail 完全一致（同样落到 pipeline state），失败品由调用方
    用 ensure_detail 逐品补跑兜底。返回 {oid: (ok, note)}。"""
    out_map = {o: (False, "未进入漏斗") for o in oids}
    # 1) 批量 collect（无 state 的品先建最小 state；已在售防呆由 A② 主图去重
    #    与 B 段结构闸兜底，不走 publish_one start 的 site_goods 全表拉取）
    fresh = [o for o in oids if not os.path.exists(st_path(o))]
    if fresh:
        run(["python3", "scripts/erp_client.py", "collect", "--ids", ",".join(fresh),
             "--out", "erp_data/_run/batch"], timeout=600)
    for o in fresh:
        save_json(st_path(o), {"offer_id": o, "site": site, "stage": "collect",
                               "approved": [], "edit": {},
                               "started": time.strftime("%Y-%m-%dT%H:%M:%S")})
    # 2) 批量 claim（业务拒绝=配置类错误，整批快败，不轮询）
    rc, out = run(["python3", "scripts/erp_client.py", "ttl-claim", "--ids",
                   ",".join(oids), "--region", site], timeout=600)
    low = (out or "").lower()
    if "for input string" in low or "未配置" in out:
        for o in oids:
            out_map[o] = (False, "claim 业务拒绝（配置/参数非法），修 .env 后重跑")
        return out_map
    # 3) 认领异步：轮询一次 ttl-list 匹配全部（一次调用判全批，代替每品 86s 回查）
    import csv as _csv
    backoff, t0, found, i = (5, 8, 12, 15, 20, 25, 30), time.time(), {}, 0
    lp = "erp_data/_run/batch/tiktok_list.csv"
    while time.time() - t0 < 150 and len(found) < len(oids):
        time.sleep(backoff[min(i, len(backoff) - 1)])
        i += 1
        run(["python3", "scripts/erp_client.py", "ttl-list", "--region", site,
             "--size", "300", "--out", "erp_data/_run/batch"], timeout=300)
        if not os.path.exists(lp):
            continue
        want = set(oids) - set(found)
        for r in _csv.DictReader(open(lp, encoding="utf-8-sig")):
            iid = (r.get("货源ID") or "").strip()
            if iid in want:
                found[iid] = r["ttl_id"]
    for o in oids:
        st = load_json(st_path(o))
        st["site"] = site
        if o in found:
            # 批量 claim 已完成，直接把 state 放在 detail 阶段（ttl_id 已有）。
            # 若停在 claim，第4步 next 会重跑 stage_claim → 每品重复认领一次
            # （2026-11-26 实测：6 品白烧 6 次 ttl-claim×6s，批量路径形同虚设）。
            st["stage"], st["ttl_id"] = "detail", found[o]
            st.setdefault("done_stages", [])
            if "claim" not in st["done_stages"]:
                st["done_stages"].append("claim")
            out_map[o] = (True, "claimed")
        else:
            st["stage"] = "claim"
            out_map[o] = (False, "claim 150s 未见（调用方 ensure_detail 复核）")
        save_json(st_path(o), st)
    # 4) 并行补 detail（state 已停在 detail+ttl_id：next 直接跑 ttl-detail→edit_dry，
    #    不再重跑 claim；实测应从 6 次单品 claim 降到 0）
    from concurrent.futures import ThreadPoolExecutor

    def _det(o):
        # state 已是 detail+ttl_id：next 跑 ttl-detail→edit_dry（不重跑 claim）
        for _ in range(3):
            st = load_json(st_path(o))
            stage = st.get("stage")
            if stage in ("edit_dry", "edit_save", "review_img", "apply",
                         "guards", "publish", "wait", "verify4", "done"):
                return o, True, "detail ok"
            if stage not in ("collect", "claim", "detail"):
                return o, False, "stage 异常: %s" % stage
            rc, out = run(["python3", "scripts/publish_one.py", "next", "--offer-id", o])
            st2 = load_json(st_path(o))
            if st2.get("stage") == stage and rc != 0:
                return o, False, "next(%s) 失败: %s" % (stage, (out or "").strip()[-100:])
        st = load_json(st_path(o))
        ok = st.get("stage") in ("edit_dry", "detail", "edit_save", "done")
        return o, ok, st.get("stage")

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        for o, ok, note in ex.map(_det, [o for o in oids if out_map[o][0]]):
            out_map[o] = (ok, note)
    return out_map


def check_structure(st, used):
    """读 detail 做 ③④⑤⑥ 闸。返回 (ok, reasons, info)。"""
    reasons, info = [], {}
    df = st.get("detail_file") or ""
    if not df or not os.path.exists(df):
        return False, ["B0 detail 未生成"], info
    d = load_json(df)
    try:
        v2 = d["reqInfo"]["tikTokItemModel"]["productCreateRequestV2"]
    except Exception:
        return False, ["B0 detail 结构异常"], info

    skus = v2.get("skus") or []
    if not skus:
        return False, ["B0 无 SKU"], info
    info["category_path"] = d.get("toCategoryNameTree", "") or ""

    # ③ 唯一 seller_sku
    sids = [str(s.get("seller_sku") or "") for s in skus]
    uniq = len(set(sids))
    info["sku_total"] = len(skus)
    info["sku_unique"] = uniq
    if uniq < len(skus):
        # 不是弃品理由：用 --sku-map-seq/--sku-code-seq 按原始顺序对齐即可解决
        info.setdefault("warnings", []).append(
            "B③ %d 个 SKU 只有 %d 个唯一 seller_sku → 提交时须 --sku-map-seq/--sku-code-seq"
            % (len(skus), uniq))

    # ④ 库存
    inv = []
    for s in skus:
        inv.append(sum(int(i.get("quantity") or 0) for i in (s.get("inventory") or [])))
    live = sum(1 for x in inv if x > 0)
    info["sku_in_stock"] = live
    if live == 0:
        reasons.append("B④ 全部 SKU 零库存")

    # ⑦ SKU 可编辑性预检（20261126_my10b D3）：edit 链有三层结构死亡——
    # 属性值不可查表译出 / 选中款与 drop 等值冲突（重复 seller_sku）/ 选中款零库存。
    # 口径与 batch_listing.pick_skus + ttl_edit 同源（import 自 ms_terms），
    # 否则过闸品在 edit 二次死，白烧一轮 B0+人工回合
    # （实证：833710035247「可译10/10、有货、在带」却死于 drop 等值砍）。
    res = check_sku_selectable(skus)
    if not res["ok"]:
        reasons.append("B⑦ %s" % res["reason"])

    # ⑤ 来源价
    srcs = []
    for s in skus:
        try:
            srcs.append(float((s.get("price") or {}).get("source_price") or 0))
        except (TypeError, ValueError):
            pass
    srcs = [x for x in srcs if x > 0]
    info["src_min"] = min(srcs) if srcs else 0
    info["src_max"] = max(srcs) if srcs else 0
    if srcs and min(srcs) < MIN_SRC_PRICE:
        reasons.append("B⑤ 最低来源价 ¥%.2f < ¥%.0f" % (min(srcs), MIN_SRC_PRICE))

    # ⑥ 体积/重量（detail 缺则用模板兜底，只告警不阻断）
    pkg = v2.get("package_weight") or {}
    dims = v2.get("package_dimensions") or {}
    w = 0
    try:
        w = float(pkg.get("value") or 0) * (1000 if str(pkg.get("unit", "")).upper().startswith("KILO") else 1)
    except (TypeError, ValueError):
        w = 0
    dm = "25x20x4"
    if dims:
        try:
            dm = "%gx%gx%g" % (float(dims.get("length")), float(dims.get("width")),
                               float(dims.get("height")))
        except (TypeError, ValueError):
            pass
    if w <= 0:
        t = WT.lookup(st.get("offer_id"), v2.get("title", ""), info["category_path"])
        w, dm = t["weight_g"], t["dims"]
        info["weight_source"] = t["source"]
    else:
        info["weight_source"] = "detail"
    ok_w, why = WT.check(w, dm)
    info["weight_g"], info["dims"], info["volume"] = w, dm, WT.volume(dm)
    if not ok_w:
        reasons.append("B⑥ " + "；".join(why))

    # 建议类目（省掉每品手查类目树）
    cid, cname, kw = CM.suggest(v2.get("title", ""), info["category_path"])
    if cid:
        okc, msg = CM.check_available(cid)
        info["suggest_category"] = dict(id=cid, name=cname, matched=kw,
                                        available=okc, msg=msg)

    # A② 主图指纹（detail 拿到实际 main 后复查；排除自己）
    oid = str(st.get("offer_id") or "")
    mains = [m.get("uri") for m in (v2.get("main_images") or []) if m.get("uri")]
    hit = [u for u in mains if u in used and str(used[u]) != oid]
    if hit:
        reasons.append("A② 主图已用过（offerId=%s）" % used[hit[0]])
    info["main_count"] = len(mains)
    return (not reasons), reasons, info


# ────────────────────────── C 段：全图集判级 ──────────────────────────
def grade_all(st):
    """scan + auto_verdict，返回 (review_dict, reasons)。"""
    df = st.get("detail_file")
    ttl = str(st.get("ttl_id") or "")
    if not df or not ttl:
        return None, ["C0 缺 detail/ttl_id"]
    with timing.span("precheck.C1_scan", parent="precheck", offer_id=str(st.get("offer_id") or "")):
        rc, out = run(["python3", "scripts/image_gate.py", "scan", "--detail", df], timeout=600)
    if rc != 0:
        return None, ["C0 scan 失败: " + out[-160:]]
    rev = "erp_data/image_gate/%s/review.json" % ttl
    if not os.path.exists(rev):
        return None, ["C0 review.json 未生成"]
    # P1-3 抽样早停（2026-11-26）：先判 main>sku>desc 前 8 张，干净 <5 → 预杀，
    # 省一次全册 OCR（实测 ~38s/品）。本批 C⑦ 淘汰 16 品里 15 品整册干净 ≤4，
    # 抽样信号区分度足够；抽样图已下载，全册模式直接吃缓存不重复下载。
    # 关闭：env PRE_SAMPLE=0。
    if os.environ.get("PRE_SAMPLE", "1") != "0":
        with timing.span("precheck.C2a_sample", parent="precheck",
                         offer_id=str(st.get("offer_id") or "")):
            rc_s, out_s = run(["python3", "scripts/auto_verdict.py", "--review", rev,
                               "--sample", "8", "--need", str(MIN_CLEAN_TOTAL)], timeout=420)
        if rc_s == 5:
            return None, ["C⑦ 抽样预杀（前8张干净<%d，全册大概率不过）" % MIN_CLEAN_TOTAL]
        if rc_s == 2:
            return None, ["C0 抽样下载失败>50%（网络/CDN问题），本波稍后重跑"]
    # C 通道分级（2026-11-26）：C 段需求是「凑够 5 张干净主图」，不是判完全册。
    # sample 判过的前 8 张已进 OCR 缓存（B），stop-after 命中免重跑；
    # 按 main>sku>desc 判到凑够 5 张即停，剩余标 unjudged（Phase3 scan 会重建覆盖）。
    # 实测 fast OCR 中文召回 0/6 → 仍用 accurate，只减张数不减精度。
    # 关闭：env PRE_STOP_AFTER=0 回退全册判。
    if os.environ.get("PRE_STOP_AFTER", "1") != "0":
        with timing.span("precheck.C2_ocr_verdict", parent="precheck",
                         offer_id=str(st.get("offer_id") or ""), imgs=len((load_json(rev).get("images") or []))):
            rc, out = run(["python3", "scripts/auto_verdict.py", "--review", rev,
                           "--stop-after", str(MIN_CLEAN_TOTAL),
                           "--stop-max", os.environ.get("PRE_STOP_MAX", "20"),
                           "--batch-size", str(BATCH_SIZE)], timeout=900)
    else:
        with timing.span("precheck.C2_ocr_verdict", parent="precheck",
                         offer_id=str(st.get("offer_id") or ""), imgs=len((load_json(rev).get("images") or []))):
            rc, out = run(["python3", "scripts/auto_verdict.py", "--review", rev,
                           "--batch-size", str(BATCH_SIZE)], timeout=900)
    # auto_verdict 在存在 pending 图时返回非 0（提示需人工），但 review 已写回——
    # 以文件内容为准，不能因退出码把「判级完成但有存疑图」误判成「判级失败」。
    rev_d = load_json(rev)
    if not rev_d.get("images"):
        return None, ["C0 判级未产出 review: " + out[-160:]]
    return rev_d, []


def pick_mains(review, n=5):
    """main > sku > desc，按 content_sha256 去重，返回 (urls, detail)。"""
    imgs = review.get("images") or []
    bucket = {"main": [], "sku": [], "desc": []}
    seen = set()
    for im in imgs:
        if im.get("verdict") != "L3_keep":
            continue
        sha = im.get("content_sha256") or ""
        if sha and sha in seen:
            continue
        ch = (im.get("channels") or ["desc"])[0]
        ch = ch if ch in bucket else "desc"
        bucket[ch].append(im["url"])
        if sha:
            seen.add(sha)
    pick = []
    for ch in ("main", "sku", "desc"):
        for u in bucket[ch]:
            if len(pick) < n:
                pick.append(u)
    return pick, {k: len(v) for k, v in bucket.items()}


# ────────────────────────── 主流程 ──────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="", help="逗号分隔 offerId")
    ap.add_argument("--pool", default="", help="CSV：offer_id,consign_price,image_url,title")
    ap.add_argument("--site", default="MY")
    ap.add_argument("--max", type=int, default=0, help="最多处理几个品")
    ap.add_argument("--no-collect", action="store_true", help="只跑 A 段，不采集")
    ap.add_argument("--offline", action="store_true", help="跳过 C 段判级")
    ap.add_argument("--batch-size", type=int, default=40, help="C 段批量 OCR 每组图数")
    ap.add_argument("--target", type=int, default=0,
                    help="A3 序贯筛选：凑够 N 个通过品就停（0=不限，默认）")
    ap.add_argument("--wave", type=int, default=0,
                    help="A3 每轮处理多少候选（默认 target×2；0 且无 target 时=全部）")
    ap.add_argument("--c-jobs", dest="c_jobs", type=int, default=1,
                    help="A4 C 段并行路数（默认1=串行；实测 3 路 1.5x，4 核别再高）")
    ap.add_argument("--b2-batch", dest="b2_batch", action="store_true",
                    help="B0 批量漏斗：整波一次 collect+claim+轮询+并行 detail（替代逐品状态机）")
    args = ap.parse_args()
    global BATCH_SIZE
    BATCH_SIZE = max(1, args.batch_size)

    rows = []
    if args.pool:
        import csv
        with open(args.pool, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                oid = str(r.get("offer_id") or r.get("offerId") or "").strip()
                if oid:
                    rows.append(dict(offer_id=oid,
                                     consign_price=r.get("consign_price") or r.get("consignPrice") or "",
                                     image_url=r.get("image_url") or r.get("imageUrl") or "",
                                     title=r.get("title") or "",
                                     prefilter_verdict=r.get("prefilter_verdict") or "",
                                     # P1-4 增强列（缺列=空串，A 段预判自动跳过）
                                     ship_city=r.get("ship_city") or "",
                                     qty_begin=r.get("qty_begin") or "",
                                     price_low=r.get("price_low") or ""))
    for x in (args.ids or "").split(","):
        x = x.strip()
        if x and not any(r["offer_id"] == x for r in rows):
            rows.append(dict(offer_id=x, consign_price="", image_url="", title=""))
    if not rows:
        sys.exit("[pre] 需 --ids 或 --pool")
    # A2（2026-11-25）：prefilter 首图判级信号排序 —— keep 优先、flag 排队尾。
    # 不硬杀 flag（实测 1036443288103 首图有字仍成功在售），只调检测顺序；
    # 配合 --target 序贯早停，让「整册干净图不足」品少占认领配额。
    order = {"keep": 0, "no_image": 1, "flag": 2}
    ranked = sorted(rows, key=lambda r: order.get(r.get("prefilter_verdict") or "", 1))
    if ranked != rows:
        moved = [(r["offer_id"], r.get("prefilter_verdict")) for r in rows
                 if r.get("prefilter_verdict") == "flag"]
        print("[pre] A2 排序：flag 品移到队尾 → %s" % (moved[:8] or "无"))
    rows = ranked
    if args.max:
        rows = rows[:args.max]

    used = load_used()
    report = dict(batch=time.strftime("%Y%m%d_%H%M"), site=args.site,
                  total=len(rows), ok=[], dropped=[], started=time.time())
    # 子进程（publish_one/ttl_edit/image_gate）继承 parent，账本里自动挂到 precheck 下
    os.environ["TT_PARENT"] = "precheck"

    # ── A3/A4：分轮序贯筛选 + C 段并行（2026-11-25 落地）─────────────────
    # 原实现：一次跑完全部候选、全串行。本轮实测 16 候选 → 9 过闸 → 只用 5 个，
    # 7 个淘汰品白花 4.9 min、3 个白检过闸品再 2.1 min（合计占筛选段 61%）。
    # 改法：按 --wave 分批，每批 A+B 串行（认领是写操作，不并发）、C 段按 --c-jobs
    # 并行（实测品间并行 1.5x，非线性——Vision 已占满 4 核）。达标即停。
    # 不传 --target 时 wave=全部、c_jobs=1，行为与改前完全一致（向后兼容）。
    target = max(0, args.target)
    wave = max(1, args.wave or (target * 2 if target else len(rows)))
    c_jobs = max(1, args.c_jobs)

    def c_stage(item):
        """C 段：scan + 判级 + 挑图。返回 (oid, st, info, review, whyC)。"""
        oid2, st2, info2 = item
        review2, whyC2 = grade_all(st2)
        return oid2, st2, info2, review2, whyC2

    ip_flagged = []     # B1：需人工复核 SKU 图的品
    idx = 0
    while idx < len(rows):
        if target and len(report["ok"]) >= target:
            print("\n[pre] A3 序贯早停：已达标 %d/%d，剩 %d 个候选留待下批"
                  % (len(report["ok"]), target, len(rows) - idx))
            report.setdefault("skipped_by_target", []).extend(
                [r["offer_id"] for r in rows[idx:]])
            break
        chunk = rows[idx: idx + wave]
        idx += len(chunk)
        cand = []          # 通过 A+B 段、待 C 段

        # ── A 段：整波先筛（--b2-batch 需要知道全批才能批量 collect/claim）──
        passA = []
        for j, row in enumerate(chunk, 1):
            oid = row["offer_id"]
            print("\n[%d/%d] %s" % (idx - len(chunk) + j, len(rows), oid))

            # A 段
            with timing.span("precheck.A_static_gate", parent="precheck", offer_id=oid):
                okA, whyA = precheck_pool(row, used)
                # P1-4：基础闸通过后，pool 行带 ship_city/qty_begin/price_low 列时
                # 追加发货地/MOQ/价区间预判（无列=行为与改前一致）
                if okA and (row.get("ship_city") or row.get("qty_begin") or row.get("price_low")):
                    okA, whyA = precheck_enriched(row)
            if not okA:
                print("  ✗ A 段: " + "；".join(whyA))
                report["dropped"].append(dict(offer_id=oid, stage="A", reasons=whyA))
                continue
            if args.no_collect:
                print("  ✓ A 段通过（未采集）")
                report["ok"].append(dict(offer_id=oid, stage="A_only",
                                         enriched=bool(row.get("ship_city") or row.get("qty_begin"))))
                continue
            passA.append((row, oid))

        # ── B0 批量漏斗（--b2-batch）：整波一次 collect+claim+ttl-list ──
        states = {}
        if args.b2_batch and passA:
            _oids = [o for _, o in passA]
            print("\n[pre] B0 批量漏斗：%d 品（collect×1 + claim×1 + 轮询合一）" % len(_oids))
            with timing.span("precheck.B0_batch_funnel", parent="precheck"):
                fmap = batch_funnel(_oids, args.site, jobs=max(1, args.c_jobs))
            for o, (ok, note) in fmap.items():
                if ok:
                    states[o] = load_json(st_path(o))
                else:
                    print("  [%s] 漏斗未成（%s）→ 单品状态机复核" % (o, note))
                    with timing.span("precheck.B0_collect_claim_detail",
                                     parent="precheck", offer_id=o):
                        st2, _o = ensure_detail(o, args.site)
                    states[o] = st2

        for row, oid in passA:
            st = states.get(oid)
            if st is None:
                # B 段（默认逐品路径，行为与改前一致）
                with timing.span("precheck.B0_collect_claim_detail", parent="precheck", offer_id=oid):
                    st, out = ensure_detail(oid, args.site)
            if st.get("stage") not in ("edit_dry", "edit_save", "review_img", "apply",
                                       "guards", "publish", "wait", "verify4", "done"):
                print("  ✗ 采集/认领未完成（stage=%s）" % st.get("stage"))
                report["dropped"].append(dict(offer_id=oid, stage="B",
                                              reasons=["采集未完成 stage=%s" % st.get("stage")]))
                continue
            with timing.span("precheck.B1_structure_gate", parent="precheck", offer_id=oid):
                okB, whyB, info = check_structure(st, used)
            if not okB:
                print("  ✗ B 段: " + "；".join(whyB))
                report["dropped"].append(dict(offer_id=oid, stage="B", reasons=whyB, info=info))
                continue
            print("  ✓ B 段: SKU %d(唯一%d, 有货%d) 源价 ¥%.1f-%.1f %sg %s(%.0fcm³)"
                  % (info["sku_total"], info["sku_unique"], info["sku_in_stock"],
                     info["src_min"], info["src_max"], info["weight_g"], info["dims"],
                     info["volume"]))
            sc = info.get("suggest_category")
            if sc:
                print("     建议类目 %s %s（匹配「%s」%s）"
                      % (sc["id"], sc["name"], sc["matched"],
                         "✓AVAILABLE" if sc["available"] else "⚠" + str(sc["msg"])))
            else:
                print("     ⚠ 未匹配到类目，需手填 --category-id")

            # B1（2026-11-25 641912316438 实损）：IP 形象检查必须是**必然步骤**，
            # 不能靠人工偶然看图。词表宽进，命中只代表「装配前必须人看一眼 SKU 图」。
            try:
                import ip_risk as IPR
                ipr = IPR.scan(oid)
                info["ip_risk"] = ipr.get("hits") or []
                if info["ip_risk"]:
                    print("     ⚠ IP 风险词：%s → 装配前必须看 SKU 图确认"
                          % ",".join(info["ip_risk"]))
                    ip_flagged.append(oid)
            except Exception as e:
                print("     (IP 扫描跳过: %s)" % str(e)[:40])

            if args.offline:
                report["ok"].append(dict(offer_id=oid, stage="B_only", info=info))
                continue
            cand.append((oid, st, info))

        # ── C 段：并行判图（A4）──
        if not cand:
            continue
        if c_jobs > 1 and len(cand) > 1:
            print("\n[pre] C 段并行判图：%d 个品 / %d 路" % (len(cand), min(c_jobs, len(cand))))
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(c_jobs, len(cand))) as ex:
                cres = list(ex.map(c_stage, cand))
        else:
            cres = [c_stage(c) for c in cand]

        for oid, st, info, review, whyC in cres:
            print("\n[%s] C 段结果" % oid)
            if whyC:
                print("  ✗ C 段: " + "；".join(whyC))
                report["dropped"].append(dict(offer_id=oid, stage="C", reasons=whyC, info=info))
                continue
            with timing.span("precheck.C3_pick_mains", parent="precheck", offer_id=oid):
                keep = [x for x in (review.get("images") or []) if x.get("verdict") == "L3_keep"]
                pick, dist = pick_mains(review, 5)
            info["clean_total"] = len(keep)
            info["clean_by_channel"] = dist
            if len(pick) < 5:
                why = ["C⑦ 整册干净图仅 %d 张（需 ≥%d）" % (len(pick), MIN_CLEAN_TOTAL)]
                print("  ✗ " + why[0])
                report["dropped"].append(dict(offer_id=oid, stage="C", reasons=why, info=info))
                continue

            # 写 mains.txt
            edit_dir = "erp_data/_run/pipeline/%s/edit" % oid
            os.makedirs(edit_dir, exist_ok=True)
            mp = os.path.join(edit_dir, "mains.txt")
            open(mp, "w", encoding="utf-8").write("\n".join(pick) + "\n")
            add_used(used, pick, oid)
            print("  ✓ C 段: 整册干净 %d 张 %s → mains.txt 已生成（5 张）"
                  % (len(keep), dist))
            report["ok"].append(dict(offer_id=oid, stage="C", info=info, mains_file=mp,
                                     clean_total=len(keep)))

    # 类目配比软告警（P2-7）
    from collections import Counter
    cats = Counter()
    for it in report["ok"]:
        cp = (it.get("info") or {}).get("category_path") or ""
        for k in WP.CATEGORY_QUOTA:
            if k in cp:
                cats[k] += 1
    n_ok = max(1, len(report["ok"]))
    for k, quota in WP.CATEGORY_QUOTA.items():
        if cats.get(k, 0) / n_ok > quota:
            report.setdefault("warnings", []).append(
                "%s 占比 %.0f%% 超配额 %.0f%%（类目集中风险）"
                % (k, 100.0 * cats[k] / n_ok, 100 * quota))

    if ip_flagged:
        report["ip_risk_offers"] = ip_flagged
        print("\n" + "!" * 60)
        print("B1 IP 风险复核：%d 品需人工看 SKU 图 → %s" % (len(ip_flagged), ",".join(ip_flagged)))
        try:
            import ip_risk as IPR
            sh = IPR.build_sheet([IPR.scan(o) for o in ip_flagged])
            if sh:
                print("  拼图 → %s（ViewImage 看一眼 SKU 图上有没有卡通 IP 形象）" % sh)
                report["ip_risk_sheet"] = sh
            else:
                print("  拼图失败，请逐品看图")
        except Exception as e:
            print("  拼图异常: %s" % str(e)[:60])
        print("!" * 60)

    save_json(USED_DB, used)
    elapsed = time.time() - report["started"]
    report["elapsed_s"] = round(elapsed, 1)
    outp = "erp_data/_run/precheck/%s.json" % report["batch"]
    save_json(outp, report)

    print("\n" + "=" * 60)
    print("可发 %d 个 / 弃 %d 个 / 耗时 %.0fs" % (len(report["ok"]), len(report["dropped"]), elapsed))
    for w in report.get("warnings", []):
        print("⚠ " + w)
    print("报告 → " + outp)
    if report["ok"]:
        print("\n下一步（每品）：publish_one set --offer-id <id> --mains-file <mains> ... → next")
    return 0 if not report["dropped"] else 1


if __name__ == "__main__":
    sys.exit(main())
