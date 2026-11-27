#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_waves.py — MCP 回包 → 去重过滤 → 小波切分（2026-11-26 my10c 效率修正）

治两个实测浪费（my10c 三批对照，见 20261126_my10b/timing_analysis.md 追加段）：
  1) 全灭波：洗衣袋种子 26 候选一波进 B0，0 过闸，白烧 ~7min。
     → 按品类分组、每组切 ≤N 品小波（默认7）；品类死亡损失封顶在一波。
     → 波文件按"历史转化高的品类在前"排序，driver 序贯跑、凑满 target 自动停，
       劣质品类排在后面根本不进 B0。
  2) agent 手搓组池：MCP 回包→读→去重→过滤→写 CSV 每轮 ~2min 空转 ×6 轮。
     → 本脚本一次吃全部 mcp_write 落盘文件，去重/过滤/切波/写盘全脚本化。

用法：
  python3 scripts/make_waves.py --in erp_data/linkfox/mcp/xxx.json [...] \
      --run-dir erp_data/_run/20261126_my10d --per-wave 7 \
      --kw-order apron-seed,laundry-seed          # 可选：品类波序
  不传 --kw-order 时按各品类过闸历史率排序（读 erp_data/word_pool_feedback.csv，
  无记录则保持输入顺序）。

输出：<run-dir>/waves_01.csv ...（driver --waves 直接吃），并打印组池账。
退出码：0 有波 / 2 过滤后为空（如实报错，不猜）。
"""
import argparse
import csv
import glob
import json
import os
import re
import sys

REDLINE_KW = ["刀", "剪", "玻璃", "液体", "电池", "磁吸"]  # levelName 命中即剔（利器/禁运向）


def load_used():
    """历史已铺/已发 + 本 run 已进过池的 offer_id。"""
    used = set()
    for f in glob.glob("erp_data/**/tiktok_logs*.csv", recursive=True):
        try:
            for r in csv.DictReader(open(f, encoding="utf-8-sig")):
                oid = (r.get("货源ID") or "").strip()
                if oid:
                    used.add(oid)
        except Exception:
            pass
    for f in glob.glob("erp_data/_run/*/waves*.csv"):
        try:
            for r in csv.DictReader(open(f, encoding="utf-8-sig")):
                used.add(r["offer_id"])
        except Exception:
            pass
    return used


def classify_kw(p):
    # 2026-11-27 修复（np1 批实证）：旧版 hook/storage 只匹配 levelName，
    # 百炼回包无 levelName → 挂钩/海绵/垃圾袋/防尘罩全部打成 other，
    # 波序/品类账全失真。统一 levelName OR title 双匹配。
    lvl = p.get("levelName") or ""
    t = p.get("title") or ""
    src = lvl + t
    if "围裙" in src or "罩衣" in src or "apron" in t.lower():
        return "apron"
    if "洗衣袋" in src or "护洗" in src:
        return "laundry"
    if "挂钩" in src or "粘钩" in src or "排钩" in src or "衣钩" in src or "挂架" in src:
        return "hook"
    if "海绵" in src or "百洁布" in src or "魔力擦" in src:
        return "sponge"
    if "垃圾袋" in src or "塑料袋" in src and "垃" in src:
        return "garbage"
    if "防尘" in src or "遮尘" in src:
        return "dustcover"
    if "置物架" in src or "收纳" in src:
        return "storage"
    return "other"


def kw_hist_rate():
    """word_pool_feedback.csv → ({kw: 过闸率}, {kw: 累计sent})。

    注意：表里 kw 有历史脏值（apron-seed2/imgseed 等命名漂移），统一取
    第一个连字符/下划线前的词根聚合；'?' 行（driver 旧版未回填 kw）跳过。
    """
    agg = {}
    fb = "erp_data/word_pool_feedback.csv"
    if not os.path.exists(fb):
        return {}, {}
    for r in csv.DictReader(open(fb, encoding="utf-8")):
        kw = (r.get("kw") or "").split("-")[0].split("_")[0]
        if not kw or kw == "?":
            continue
        try:
            s, p = int(r.get("sent") or 0), int(r.get("passed") or 0)
        except ValueError:
            continue
        a = agg.setdefault(kw, [0, 0])
        a[0] += s
        a[1] += p
    rates = {k: (v[1] / v[0] if v[0] else 0) for k, v in agg.items()}
    return rates, {k: v[0] for k, v in agg.items()}


# P0-3 黑名单口径（my10d 分析）：单波样本不足以判死品类（供给面会变），
# 需累计 sent ≥ SENT_MIN 且过闸率 < RATE_MAX 才拒收；--allow-dead 放行。
# 表可信守卫：若全表（剔除'?'行）passed 恒为 0，说明 kw 回填缺口未修复、
# 过闸数全记在 '?' 名下——此时任何品类都"看起来是死的"，黑名单必须整体
# 停用，否则会把 apron（实际转化 33%+）误杀。
SENT_MIN = 15
RATE_MAX = 0.10


def dead_kws(rates, sents, allow=False):
    if allow:
        return {}
    if not rates or sum(sents.values()) == 0:
        return {}
    fb = "erp_data/word_pool_feedback.csv"
    total_passed = 0
    try:
        for r in csv.DictReader(open(fb, encoding="utf-8")):
            kw = (r.get("kw") or "").split("-")[0].split("_")[0]
            if kw and kw != "?":
                total_passed += int(r.get("passed") or 0)
    except Exception:
        return {}
    if total_passed == 0:      # 表不可信（kw 回填修复前的历史数据）
        return {}
    return {k: r for k, r in rates.items()
            if sents.get(k, 0) >= SENT_MIN and r < RATE_MAX}


# ---- P0-2：linkfox 原始回包（MCP proxy_call 落盘）→ 瘦 products ----

def _coerce_fields(p):
    """imageSearch / dld productSearch 原始条目字段并一：
    两接口都有 offerId/title/consignPrice/quantityBegin/imageUrl；
    dld 独有 levelName/salesOrderCount，imageSearch 独有 productUrl。
    缺 levelName 时红线过滤退到 title 命中（宽松口径，宁漏不误杀——
    真正的准入杀闸在 precheck B①/C⑦）。"""
    q = dict(p)
    q["imageUrl"] = q.get("imageUrl") or ""
    if not q.get("imageUrl"):
        return None
    if not q.get("levelName"):
        q["levelName"] = ""
    return q


def iter_products(fn):
    """吃两类文件：mcp_write 瘦表（顶层 products）或 linkfox 原始回包
    （嵌套 data.products / data.list / products 多路径探测）。"""
    d = json.load(open(fn, encoding="utf-8"))
    if isinstance(d, list):
        return d, "list"
    for path in ("products",):
        if isinstance(d.get(path), list):
            return d[path], "mcp"
    data = d.get("data")
    if isinstance(data, dict):
        for path in ("products", "list", "items"):
            if isinstance(data.get(path), list):
                return data[path], "raw"
    if isinstance(data, list):
        return data, "raw"
    raise SystemExit("[make_waves] ✗ %s 结构无法识别（无 products/list 数组）——"
                     "如是新接口回包，先落盘再看字段，勿猜" % fn)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="ins", nargs="*", default=[],
                    help="mcp_write --kind search 落盘的 JSON（可多个）")
    ap.add_argument("--raw", nargs="*", default=[],
                    help="linkfox MCP proxy_call 原始回包 JSON（imageSearch/"
                         "dld productSearch，P0-2：免手搓 TSV，直接吃）")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--per-wave", type=int, default=7)
    ap.add_argument("--price-min", type=float, default=2.0)
    ap.add_argument("--price-max", type=float, default=10.0)
    ap.add_argument("--kw-order", default="", help="逗号分隔品类波序（靠前先进 B0）")
    ap.add_argument("--allow-dead", action="store_true",
                    help="放行黑名单品类（默认历史 sent≥%d 且过闸率<%d 的品类拒收）"
                         % (SENT_MIN, RATE_MAX))
    a = ap.parse_args()
    files = [(f, "thin") for f in a.ins] + [(f, "raw") for f in a.raw]
    if not files:
        ap.error("需 --in 或 --raw 至少一个输入")

    used = load_used()
    rates, sents = kw_hist_rate()
    dead = dead_kws(rates, sents, allow=a.allow_dead)
    by_kw = {}
    stats = {"in": 0, "dup": 0, "moq": 0, "price": 0, "redline": 0,
             "dead": 0, "nofield": 0, "out": 0}
    for fn, kind in files:
        prods, shape = iter_products(fn)
        for p in prods:
            if kind == "raw" or shape == "raw":
                p = _coerce_fields(p)
                if p is None:
                    stats["nofield"] += 1
                    continue
            stats["in"] += 1
            oid = str(p["offerId"])
            if oid in used:
                stats["dup"] += 1
                continue
            qb = int(p.get("quantityBegin") or 1)
            cp = float(p.get("consignPrice") or 0)
            if qb > 1:
                stats["moq"] += 1
                continue
            if cp < a.price_min or cp > a.price_max:
                stats["price"] += 1
                continue
            lvl = p.get("levelName") or ""
            # 红线：levelName 命中即剔；imageSearch 无 levelName → title 兜底
            # （实测「刀架」型标题不含 levelName 时会漏进池，必须补）
            red_src = lvl if lvl else (p.get("title") or "")
            if any(k in red_src for k in REDLINE_KW):
                stats["redline"] += 1
                continue
            kw = classify_kw(p)
            if kw in dead:
                stats["dead"] += 1
                continue
            by_kw.setdefault(kw, []).append(dict(
                offer_id=oid, consign_price=cp, image_url=p["imageUrl"],
                title=p["title"], kw=kw, ship_city="", qty_begin=qb, price_low=cp))
            stats["out"] += 1
            used.add(oid)

    if dead:
        print("[make_waves] ⚠ 黑名单拒收品类: %s（历史 sent/率：%s；--allow-dead 放行）"
              % (",".join(dead), {k: "%d/%.0f%%" % (sents.get(k, 0), dead[k] * 100)}))
    if not by_kw:
        print("[make_waves] ✗ 过滤后为空 %s" % stats)
        return 2

    # 波序：显式 --kw-order > 历史过闸率降序 > 输入序
    if a.kw_order:
        order = [k.strip() for k in a.kw_order.split(",") if k.strip()]
        order += [k for k in by_kw if k not in order]
    else:
        order = sorted(by_kw, key=lambda k: -(rates.get(k, 0.5)))
    cols = ["offer_id", "consign_price", "image_url", "title", "kw",
            "ship_city", "qty_begin", "price_low"]
    os.makedirs(a.run_dir, exist_ok=True)
    waves = []
    for kw in order:
        items = by_kw.get(kw) or []
        for i in range(0, len(items), a.per_wave):
            waves.append((kw, items[i:i + a.per_wave]))
    paths = []
    for n, (kw, rows) in enumerate(waves, 1):
        path = "%s/waves_%02d.csv" % (a.run_dir, n)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        paths.append(path)
        print("  波%02d [%s] %d 品 → %s" % (n, kw, len(rows), path))
    print("[make_waves] 输入%d → 剔(重复%d/MOQ%d/价格%d/红线%d/黑名单%d/无图%d) → 出%d品 %d波 | 波序=%s"
          % (stats["in"], stats["dup"], stats["moq"], stats["price"],
             stats["redline"], stats["dead"], stats["nofield"],
             stats["out"], len(waves), ",".join(k for k, _ in waves)))
    print("[make_waves] driver 命令：--waves %s" % ",".join(paths))
    return 0


if __name__ == "__main__":
    sys.exit(main())
