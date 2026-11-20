#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""opportunity_intake.py — TikTok 商家后台「商品机会中心」信号判读闸门（本地只读，不联网）。

为什么需要（2026-11-19 MY 站实测结论）：
  商品机会「基于市场趋势的热门商品」tab 的卡片把三类坑打包呈现：
    1. 品牌跟卖陷阱（WATSONS 磨脚石 / Winstar 垃圾袋）——平台请你去卖品牌货，
       撞 selection.yaml redlines.brand_ip；
    2. 低价消耗品陷阱（纸尿裤处理袋参考价 RM0.55 起）——外部平台销量高，
       但价格带压不进一件代发成本模型；
    3. 数据口径陷阱——「3.2k+ 销量」是外部平台（shp_top_products）销量，
       不是 TikTok 站内需求；机会详情 市场GMV/商家总数 显示 "--" 即平台无站内数据。
  人工逐个点开判断慢且不稳定，本脚本把判读规则代码化：浏览器抄录卡片字段 →
  脚本输出 reject / direction_only / candidate 三档判定。

定位（与 select_gate 同一哲学）：
  纯标准库、本地只读、不联网、不看图。它**不替代** FastMoss 复核与 1688 供给侧
  验证——candidate 也只是「值得进 1688 找货」，最终采集仍必须过 select_gate.py。
  数据口径与判读规则的权威说明在 config/opportunity_intake.yaml 头部注释。

输入 CSV 列（模板：python3 scripts/opportunity_intake.py --init-template）：
  title        卡片标题（必填）
  sales        卡片销量近似值，如 "3.2k+" / "2.6k+"（必填）
  price_ref    机会详情参考价，如 "RM0.55 - RM4.55"（可空=只 WARN）
  category     机会详情类目，如 "Bekalan Rumah > Bekalan Penjagaan Rumah > Beg Sampah"（可空）
  source_site  MY / TH / VN（必填，价格带按该站 pricing.yaml 判）
  tiktok_metrics  机会详情四项站内指标（市场GMV/库存可用性/可用商品数/商家总数）
                  是否有数：yes / no / unknown（可空=unknown；no → 降级 direction_only）

用法：
  python3 scripts/opportunity_intake.py --init-template
  python3 scripts/opportunity_intake.py --csv erp_data/opportunity_my.csv
  python3 scripts/opportunity_intake.py --csv ... --json
  python3 scripts/opportunity_intake.py --title "WATSONS Pumice Stone 1S" --sales "3.2k+" \
      --price-ref "RM4.90 - RM8.90" --site MY          # 单品快查

退出码：0 正常（判定结果本身不是阻断，candidate≠可采集）；2 输入文件/参数问题
"""
import argparse
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from pricing_calc import load_config as load_pricing  # noqa: E402

ROOT = os.path.dirname(HERE)
CONFIG = os.path.join(ROOT, "config", "opportunity_intake.yaml")
PRICING = os.path.join(ROOT, "config", "pricing.yaml")
DEFAULT_CSV = os.path.join(ROOT, "erp_data", "opportunity_signals.csv")

REQUIRED_COLS = ["title", "sales", "source_site"]
# image_url / search_keyword 两列本脚本不消费，为下游 opportunity_to_sourcing.py 预留（同一份 CSV 走全路线）
TEMPLATE_HEADER = ["title", "sales", "price_ref", "category",
                   "source_site", "tiktok_metrics", "image_url", "search_keyword", "note"]

# ── 最小 YAML 子集解析（块状 dict + 标量 + 标量列表，免 PyYAML）──
def _strip_comment(val):
    if val[:1] in ('"', "'"):
        q = val[0]
        end = val.find(q, 1)
        return val[1:end] if end > 0 else val[1:]
    idx = val.find(" #")
    return val[:idx].strip() if idx >= 0 else val


def _cast(val):
    val = val.strip()
    if val == "":
        return None
    if len(val) > 1 and val[0] == val[-1] and val[0] in ('"', "'"):
        return val[1:-1]
    if val.startswith("[") and val.endswith("]"):
        return [_cast(x) for x in val[1:-1].split(",") if x.strip()]
    low = val.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        return val


def load_yaml(path):
    """递归下降解析（块状 dict + 标量 + 标量列表，免 PyYAML）。
    支持形态：`key: value`、`key:` 后接更深缩进的 dict 或 `- item` 列表。
    不支持：列表项内嵌 dict——本配置没有该形态；将来出现则换 PyYAML 并同步 dependency_contract。"""
    lines = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            lines.append((indent, raw.strip()))

    def parse_block(i, indent):
        """从 lines[i] 起解析同缩进块，返回 (obj, next_i)。首行决定 dict/list。"""
        is_list = lines[i][1].startswith("- ")
        obj = [] if is_list else {}
        while i < len(lines) and lines[i][0] >= indent:
            ind, body = lines[i]
            if ind > indent:            # 更深缩进却没被上层消费 = 结构异常，跳过防御
                i += 1
                continue
            if is_list:
                if not body.startswith("- "):
                    break
                obj.append(_cast(_strip_comment(body[2:])))
                i += 1
                continue
            if body.startswith("- "):
                break
            if ":" not in body:
                i += 1
                continue
            key, _, val = body.partition(":")
            key = key.strip().strip("\"'")
            val = _strip_comment(val)
            if val.strip() == "":
                if i + 1 < len(lines) and lines[i + 1][0] > ind:
                    sub, i = parse_block(i + 1, lines[i + 1][0])
                    obj[key] = sub
                else:
                    obj[key] = None
                    i += 1
            else:
                obj[key] = _cast(val)
                i += 1
        return obj, i

    data, _ = parse_block(0, 0)
    return data if isinstance(data, dict) else {}

# ── 数值解析 ──
def parse_sales(v):
    """'3.2k+' / '2.6k+' / '1200' -> 数值下界；解析失败返回 None。"""
    s = str(v or "").strip().lower().replace(",", "").replace("+", "")
    m = re.match(r"^(\d+(?:\.\d+)?)\s*k$", s)
    if m:
        return float(m.group(1)) * 1000
    try:
        return float(s)
    except ValueError:
        return None


def parse_price_range(v):
    """'RM0.55 - RM4.55' / 'RM4.90-RM8.90' -> (cur_prefix, lo, hi)；失败 (None,None,None)。"""
    s = str(v or "").strip()
    if not s:
        return None, None, None
    nums = re.findall(r"(\d+(?:\.\d+)?)", s)
    cur = None
    m = re.match(r"\s*([A-Za-z₫฿$]{1,3})\s*\d", s)
    if m:
        cur = m.group(1)
    vals = [float(x) for x in nums]
    if not vals:
        return cur, None, None
    return cur, min(vals), max(vals)


# ── 判定 ──
def brand_hit(title, cfg):
    t = str(title or "").lower()
    for b in cfg.get("known_brands") or []:
        if re.search(r"(?<![a-z])%s(?![a-z])" % re.escape(str(b).lower()), t):
            return b
    return None


def brand_suspect(title, cfg):
    """启发式（2026-11-19 收紧）：只认「全大写英文词」= 疑似品牌。
    依据 = skill 既有原则「标题含大写英文词 = 已被品牌方运营」（WATSONS 型）。
    Title-case 词（Multi-Pocket/Filem 等）在营销标题里逐词大写是常态，判品牌误报率过高，
    不判——已知 title-case 品牌（Winstar）靠 known_brands 名单兜。
    代价：名单外的 title-case 新品牌会漏 → 由人工目视主图 + 1688 找货阶段兜底。"""
    h = cfg.get("brand_heuristic") or {}
    min_len = int(h.get("min_len") or 4)
    generic_upper = {str(x).lower() for x in (h.get("generic_upper") or [])}
    generic = {str(x).lower() for x in (cfg.get("generic_terms") or [])}
    words = re.findall(r"[A-Za-z][A-Za-z'’\-]*", str(title or ""))
    return [w for w in words
            if len(w) >= min_len and w.isupper()
            and w.lower() not in generic_upper and w.lower() not in generic]


def category_ok(category, cfg):
    if not str(category or "").strip():
        return None                      # 未提供 → WARN
    c = str(category).lower()
    hints = [str(x).lower() for x in (cfg.get("category_home_hints") or [])]
    return any(h in c for h in hints)


def price_band_check(price_ref, site, pricing_cfg, cfg):
    cur, lo, hi = parse_price_range(price_ref)
    if lo is None:
        return "WARN", "参考价缺失/无法解析，不判价格带"
    prefix_map = (cfg.get("price_check") or {}).get("currency_prefix") or {}
    site_of_cur = prefix_map.get(cur)
    if site_of_cur and site_of_cur != site:
        return "WARN", "参考价币种 %s 与 source_site=%s 不符" % (cur, site)
    merged = pricing_cfg[site] if site in pricing_cfg else None
    if not merged:
        return "WARN", "pricing.yaml 无 %s 档" % site
    rate = float(merged.get("rate_to_local") or 0) * float(merged.get("cny_per_usd") or 0)
    if rate <= 0:
        return "WARN", "%s 汇率缺失" % site
    band = (merged.get("price_band_usd") or {})
    bmin, bmax = float(band.get("min") or 0), float(band.get("max") or 9e9)
    lo_usd, hi_usd = lo / rate, hi / rate
    if hi_usd < bmin:
        return "REJECT", ("参考价上限 $%.2f < 带下限 $%.2f（挂价压不进带内=无利可图）"
                          % (hi_usd, bmin))
    if lo_usd > bmax:
        return "REJECT", ("参考价下限 $%.2f > 带上限 $%.2f（超主流客单）"
                          % (lo_usd, bmax))
    return "OK", "参考价 $%.2f–$%.2f 与带 $%.1f–$%.1f 有交集" % (lo_usd, hi_usd, bmin, bmax)


def food_hit(title, cfg):
    """食品/保健红线词根（独立词匹配）。"""
    t = " " + str(title or "").lower() + " "
    for w in (cfg.get("food_terms") or []):
        if " " + str(w).lower() + " " in t:
            return str(w)
    return None


def liquid_chemical_hit(title, cfg):
    """液体/膏体/化工红线（selection.yaml redlines.liquid_chemical 的代码化，2026-11-19）。
    词根按「独立词」匹配；注意排除词：washing machine/cloth 不是液体品。"""
    t = " " + str(title or "").lower() + " "
    # 排除语境：washing 后紧跟 machine/cloth/towel/liquid 之外的"设备/织物"词 → 不算液体品
    for ex in (cfg.get("liquid_exclusions") or []):
        t = t.replace(" " + str(ex).lower() + " ", " _EX_ ")
    for w in (cfg.get("liquid_terms") or []):
        w = " " + str(w).lower() + " "
        if w in t:
            return str(w).strip()
    return None


def judge(row, cfg, pricing_cfg):
    title = str(row.get("title") or "").strip()
    site = str(row.get("source_site") or "").strip().upper()
    reasons = []
    verdict = "candidate"

    b = brand_hit(title, cfg)
    if b:
        return "reject", "BRAND_IP: 标题含已知品牌「%s」（机会列表跟卖品牌货=红线，不商量）" % b

    fh = food_hit(title, cfg)
    if fh:
        return "reject", "FOOD_REDLINE: 标题含食品词根「%s」（selection.yaml 红线 food_health_medical）" % fh

    lc = liquid_chemical_hit(title, cfg)
    if lc:
        return "reject", "LIQUID_CHEMICAL: 标题含液体/化工词根「%s」（selection.yaml 红线：物流受限+合规风险）" % lc

    sus = brand_suspect(title, cfg)
    if sus:
        reasons.append("brand_suspect:%s（疑似品牌词，转人工复核）" % ",".join(sus))
        verdict = "direction_only"

    sales = parse_sales(row.get("sales"))
    dmin = float(((cfg.get("demand_filter") or {}).get("opportunity_sales_min")) or 0)
    if sales is None:
        reasons.append("sales_unparsed")
        verdict = "direction_only"
    elif sales < dmin:
        return "reject", "LOW_SALES: 销量 %s < 下限 %g" % (row.get("sales"), dmin)

    tm = str(row.get("tiktok_metrics") or "unknown").strip().lower()
    if tm == "no":
        reasons.append("DATA_GAP: 机会详情站内四项指标全空(--)=无 TikTok 侧需求证据，"
                       "外部平台销量不能当站内需求，必须回 FastMoss 复核")
        verdict = "direction_only" if verdict == "candidate" else verdict
    elif tm not in ("yes", "unknown"):
        reasons.append("tiktok_metrics 值非法(%s)按 unknown 处理" % tm)

    cat = category_ok(row.get("category"), cfg)
    if cat is False:
        return "reject", "CROSS_CATEGORY: 类目「%s」不在家居树词根内" % row.get("category")
    if cat is None:
        reasons.append("category_missing（未抄类目，跨类目风险未排除）")

    if site in pricing_cfg:
        st, msg = price_band_check(row.get("price_ref"), site, pricing_cfg, cfg)
        reasons.append("price_band:%s" % msg)
        if st == "REJECT":
            return "reject", "; ".join(reasons)
        if st == "WARN":
            verdict = "direction_only" if verdict == "candidate" else verdict
    else:
        reasons.append("site_unknown:%s（无 pricing 档，价格带未判）" % site)
        verdict = "direction_only" if verdict == "candidate" else verdict

    return verdict, "; ".join(reasons) if reasons else "全项通过（仍需 1688 供给验证 + select_gate）"


def main():
    ap = argparse.ArgumentParser(description="商品机会中心信号判读闸门")
    ap.add_argument("--csv", default="", help="抄录 CSV（模板见 --init-template）")
    ap.add_argument("--init-template", action="store_true",
                    help="生成 erp_data/opportunity_signals.csv 模板")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--title", help="单品快查：标题")
    ap.add_argument("--sales", help="单品快查：销量，如 3.2k+")
    ap.add_argument("--price-ref", dest="price_ref", help="单品快查：参考价区间")
    ap.add_argument("--category", help="单品快查：机会详情类目")
    ap.add_argument("--site", help="单品快查：站点 MY/TH/VN")
    ap.add_argument("--tiktok-metrics", dest="tiktok_metrics",
                    choices=["yes", "no", "unknown"], help="单品快查：站内四项指标是否有数")
    a = ap.parse_args()

    if a.init_template:
        os.makedirs(os.path.dirname(DEFAULT_CSV), exist_ok=True)
        if os.path.exists(DEFAULT_CSV):
            print("[tpl] 已存在，不覆盖：%s" % DEFAULT_CSV)
            return 0
        with open(DEFAULT_CSV, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(TEMPLATE_HEADER)
            w.writerow(["示例：WATSONS Pumice Stone 1S", "3.2k+", "RM4.90 - RM8.90",
                        "Bekalan Rumah > ...", "MY", "no", "", "",
                        "浏览器抄录：卡片标题/销量 + 详情弹窗参考价/类目/四项指标是否有数；"
                        "image_url=卡片主图右键复制、search_keyword=中文品类词（给 opportunity_to_sourcing 用）"])
        print("[tpl] 模板：%s" % DEFAULT_CSV)
        return 0

    cfg = load_yaml(CONFIG)
    pricing_cfg = load_pricing(PRICING)

    rows = []
    if a.title:
        rows.append({"title": a.title, "sales": a.sales or "",
                     "price_ref": a.price_ref or "", "category": a.category or "",
                     "source_site": (a.site or "").upper(),
                     "tiktok_metrics": a.tiktok_metrics or "unknown"})
    elif a.csv:
        if not os.path.exists(a.csv):
            print("[err] CSV 不存在：%s" % a.csv, file=sys.stderr)
            return 2
        with open(a.csv, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            print("[err] CSV 无数据行", file=sys.stderr)
            return 2
    else:
        ap.error("需要 --csv 或 --title（或 --init-template）")

    out = []
    counts = {"candidate": 0, "direction_only": 0, "reject": 0}
    for r in rows:
        missing = [c for c in REQUIRED_COLS if not str(r.get(c) or "").strip()]
        if missing:
            out.append({"title": r.get("title") or "?", "verdict": "reject",
                        "reasons": "MISSING_FIELD: 缺 %s（卡片字段必须抄全）" % ",".join(missing)})
            counts["reject"] += 1
            continue
        v, why = judge(r, cfg, pricing_cfg)
        counts[v] += 1
        out.append({"title": r.get("title"), "site": r.get("source_site"),
                    "verdict": v, "reasons": why})

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        for o in out:
            print("[%s] %s\n      %s" % (o["verdict"].upper(), o["title"], o["reasons"]))
        print("\n[汇总] candidate=%d direction_only=%d reject=%d"
              % (counts["candidate"], counts["direction_only"], counts["reject"]))
        print("[提醒] candidate ≠ 可采集：还要 1688 找货 + select_gate.py 退出码 0；"
              "direction_only 只进「品类方向池」，禁止直接铺同款。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
