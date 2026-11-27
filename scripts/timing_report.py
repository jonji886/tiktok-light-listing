#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""timing_report.py — 把 timing.jsonl 汇总成「每个模块耗时」表

口径（重要，别混）：
  span_total   = 首条记录起点 → 末条记录终点（整批墙钟，不含批次外时间）
  script_busy  = 所有记录区间的**并集**（嵌套不重复计；并发取并集，不虚增）
  agent_gap    = span_total - script_busy（agent 思考/转录/等人工 —— 之前复盘里占 60% 的那块）
  self         = 该模块自身耗时（已扣掉子模块）

用法：
  python3 scripts/timing_report.py --run 20261125_p5
  python3 scripts/timing_report.py --run 20261125_p5 --json
"""
import argparse
import json
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER_DIR = os.path.join(ROOT, "erp_data", "_run", "timing")


def attribute_parents(rows):
    """把「父模块名未入账」的记录按时间区间归因到最小的包含者。

    场景：precheck 里 `os.environ['TT_PARENT']='precheck'` 传给子进程 publish_one，
    但账本里只有 `precheck.B0_...` 这种细分模块，没有 `precheck` → 子记录会浮到顶层
    被重复计数。这里按「谁的时间区间完整包含我，且区间最小」重挂父节点。
    """
    spans = [(r["t0"], r["t1"], r["module"]) for r in rows if r["dur"] > 0]
    names = set(r["module"] for r in rows)
    for r in rows:
        p = r.get("parent") or ""
        if not p or p in names or p == r["module"]:
            continue
        best = None
        for s, e, m in spans:
            if m == r["module"]:
                continue
            if s <= r["t0"] and e >= r["t1"]:
                if best is None or (e - s) < best[0]:
                    best = (e - s, m)
        if best:
            r["parent"] = best[1]
        else:
            r["parent"] = ""
    return rows


def load(run):
    p = os.path.join(LEDGER_DIR, "%s.jsonl" % run)
    if not os.path.exists(p):
        sys.exit("[timing] 无账本 %s" % p)
    rows = []
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows


def union_len(intervals):
    if not intervals:
        return 0.0
    iv = sorted(intervals)
    total, cs, ce = 0.0, iv[0][0], iv[0][1]
    for s, e in iv[1:]:
        if s > ce:
            total += ce - cs
            cs, ce = s, e
        elif e > ce:
            ce = e
    return total + (ce - cs)


def fmt(s):
    if s < 90:
        return "%.1fs" % s
    m = s / 60.0
    if m < 90:
        return "%.1fmin" % m
    return "%.2fh" % (m / 60.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args()
    run = a.run or (open(os.path.join(ROOT, "erp_data", "_run", ".current_run"),
                         encoding="utf-8").read().strip()
                    if os.path.exists(os.path.join(ROOT, "erp_data", "_run", ".current_run")) else "")
    rows = attribute_parents(load(run))

    t_first = min(r["t0"] for r in rows)
    t_last = max(r["t1"] for r in rows)
    span_total = t_last - t_first
    script_busy = union_len([(r["t0"], r["t1"]) for r in rows if r["dur"] > 0])

    # 按模块聚合：sum / count / self
    agg = defaultdict(lambda: dict(n=0, sum=0.0, self=0.0, max=0.0, ok=0, fail=0))
    parent_of = {}
    for r in rows:
        m = r["module"]
        a_ = agg[m]
        a_["n"] += 1
        a_["sum"] += r["dur"]
        a_["max"] = max(a_["max"], r["dur"])
        if r.get("meta", {}).get("ok") is False:
            a_["fail"] += 1
        else:
            a_["ok"] += 1
        parent_of.setdefault(m, r.get("parent") or "")
    for r in rows:
        p = r.get("parent") or ""
        if p and p in agg:
            agg[p]["self"] -= r["dur"]          # 父 self = sum - 子 sum
    for m in agg:
        agg[m]["self"] += agg[m]["sum"]

    # 分母口径：只以「顶层模块」为 100%（parent 不在 agg 里的）
    tops = [m for m in agg if (parent_of.get(m) or "") not in agg]

    out = dict(run=run, records=len(rows),
               span_total=round(span_total, 1), script_busy=round(script_busy, 1),
               agent_gap=round(span_total - script_busy, 1),
               t_start=__import__("time").strftime("%H:%M:%S", __import__("time").localtime(t_first)),
               t_end=__import__("time").strftime("%H:%M:%S", __import__("time").localtime(t_last)),
               modules={m: dict(n=v["n"], sum=round(v["sum"], 1), itself=round(v["self"], 1),
                                max=round(v["max"], 1), parent=parent_of.get(m, ""))
                        for m, v in agg.items()},
               top_modules=sorted(tops, key=lambda m: -agg[m]["sum"]))
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0

    print("=" * 84)
    print("批次 %s  记录 %d 条  %s → %s" % (run, len(rows), out["t_start"], out["t_end"]))
    print("墙钟 %s | 脚本实占 %s (%.0f%%) | agent空转 %s (%.0f%%)"
          % (fmt(span_total), fmt(script_busy), 100 * script_busy / max(span_total, .001),
             fmt(out["agent_gap"]), 100 * out["agent_gap"] / max(span_total, .001)))
    print("=" * 84)
    print("%-38s %7s %10s %10s %8s" % ("模块（顶层，含子模块）", "次数", "累计", "占比", "单次均值"))
    print("-" * 84)
    top_sum = max(script_busy, 0.001)
    for m in out["top_modules"][:a.top]:
        v = agg[m]
        print("%-38s %7d %10s %9.1f%% %8s"
              % (m[:38], v["n"], fmt(v["sum"]), 100 * v["sum"] / top_sum, fmt(v["sum"] / v["n"])))
    print("-" * 84)
    print("子模块明细（self=扣掉子模块后的净耗时）")
    print("%-44s %7s %10s %10s" % ("模块", "次数", "累计", "self"))
    for m in sorted(agg, key=lambda x: -agg[x]["sum"]):
        if (parent_of.get(m) or "") in agg:
            v = agg[m]
            print("%-44s %7d %10s %10s" % (m[:44], v["n"], fmt(v["sum"]), fmt(v["self"])))
    # 按商品透视：每品各阶段累计（meta.offer_id 由 pipeline/precheck 写入）
    by_prod = defaultdict(lambda: defaultdict(float))
    prod_total = defaultdict(float)
    for r in rows:
        oid = str((r.get("meta") or {}).get("offer_id") or "")
        if not oid:
            continue
        key = r["module"].split(".")[-1] if r["module"].startswith("pipeline.") else r["module"]
        if r["module"].startswith("pipeline.") or r["module"].startswith("precheck.") \
                or r["module"] == "assemble":
            by_prod[oid][key] += r["dur"]
            prod_total[oid] += r["dur"]
    if by_prod:
        stages = ["precheck.A_static_gate", "B0_collect_claim_detail", "B1_structure_gate",
                  "C1_scan", "C2_ocr_verdict", "edit_dry", "edit_save", "scan", "review_img",
                  "apply", "guards", "publish", "wait", "verify4"]
        stages = [s for s in stages if any(s in d for d in by_prod.values())]
        print("-" * 84)
        print("按商品透视（秒；pipeline.* 与 precheck.* 均归到同名阶段）")
        print("%-14s %8s " % ("offer_id", "合计") + " ".join("%s" % s[:11].rjust(11) for s in stages))
        for oid in sorted(by_prod, key=lambda x: -prod_total[x]):
            cells = " ".join(("%.0f" % by_prod[oid].get(s, 0)).rjust(11) for s in stages)
            print("%-14s %8.0f " % (oid, prod_total[oid]) + cells)

    marks = [r for r in rows if r["module"].startswith("MARK::")]
    if marks:
        print("-" * 84)
        print("人工/agent 打点：")
        prev = None
        for r in marks:
            gap = ("(+%s)" % fmt(r["t0"] - prev)) if prev else ""
            print("  %-28s %s %s %s" % (r["module"][6:], out["t_start"], gap,
                                        json.dumps(r.get("meta") or {}, ensure_ascii=False)))
            prev = r["t0"]
    print("=" * 84)
    return 0


if __name__ == "__main__":
    sys.exit(main())
