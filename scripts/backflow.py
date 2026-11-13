#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据回流闭环（P1-7）——让「放大赢家」真的有数据输入

诊断结论：150 款 × 3 个时点的人工回填大概率烂尾，`backflow.csv` 当初根本不存在，
也没有任何提醒。结果就是 T+20 该 kill 的没 kill、该放大的没放大。

本脚本把回流变成「自动排期 + 到期提醒 + 规则判定」：
  sync   从 tiktok_list.csv(状态=1) + tiktok_logs.csv(success) 生成/更新回流行
  due    列出今天到期与逾期未填的检查点（退出码 1 = 有逾期，供定时任务判断）
  mark   单条回填某时点数据（不想开 Excel 时用）
  judge  按 selection.yaml → cadence 的阈值算出 kill / 培养 / 放大，并写回
  stats  汇总各判定分布与赢家清单

时点口径（selection.yaml → cadence.data_backflow_day = [2,7,20]）：
  T+2   入池检查：曝光 < exposure_not_indexed_threshold(100) = 未入池 → 换主图重收录
  T+7   曝光/点击/加购：有点击无加购 → 换主图或调价；零曝光 → 等 T+20
  T+20  成交/退款/延迟发货：零成交 → kill；有成交 → 进放大池

纪律：只读写本地 CSV，不碰 ERP、不猜数据。没回填就是空，绝不填 0 冒充「零曝光」。
"""

import argparse
import csv
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SELECTION_YAML = os.path.join(ROOT, "config", "selection.yaml")
BACKFLOW = os.path.join(ROOT, "erp_data", "backflow.csv")
LIST_CSV = os.path.join(ROOT, "erp_data", "tiktok_list.csv")
LOGS_CSV = os.path.join(ROOT, "erp_data", "tiktok_logs.csv")

HEADER = [
    "ttl_id", "offer_id", "title", "region", "shop", "publish_date",
    "due_t2", "due_t7", "due_t20",
    "t2_exposure", "t2_note",
    "t7_exposure", "t7_clicks", "t7_add_cart",
    "t20_orders", "t20_gmv", "t20_refund_rate", "t20_late_ship_rate",
    "verdict", "action", "note", "updated_at",
]

DEFAULTS = {
    "index_check_hours": 48,
    "index_exposure_min": 100,
    "optimize_day": 7,
    "kill_day": 20,
    "backflow_days": [2, 7, 20],
    "late_ship_rate_warn": 0.10,
}


# ────────────────────────── 配置读取 ──────────────────────────
def parse_scalar(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        return [parse_scalar(x) for x in inner.split(",")] if inner else []
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def load_config():
    cfg = dict(DEFAULTS)
    if not os.path.exists(SELECTION_YAML):
        return cfg
    stack, found = [], {}
    with open(SELECTION_YAML, encoding="utf-8") as f:
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
            val = val.split("#")[0].strip()
            while stack and stack[-1][0] >= indent:
                stack.pop()
            path = [k for _, k in stack] + [key]
            if val == "":
                stack.append((indent, key))
            else:
                found[".".join(path)] = parse_scalar(val)
    mapping = {
        "index_check_hours": "cadence.listing_index_check_hours",
        "index_exposure_min": "cadence.exposure_not_indexed_threshold",
        "optimize_day": "cadence.optimize_day",
        "kill_day": "cadence.kill_day",
        "backflow_days": "cadence.data_backflow_day",
        "late_ship_rate_warn": "cadence.late_ship_rate_warn",
    }
    for k, path in mapping.items():
        if path in found:
            cfg[k] = found[path]
    return cfg


# ────────────────────────── CSV 读写 ──────────────────────────
def read_rows(path):
    if not os.path.exists(path) or os.path.getsize(path) < 2:
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_rows(rows, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in HEADER})


def date_add(iso, days):
    t = time.strptime(iso, "%Y-%m-%d")
    return time.strftime("%Y-%m-%d", time.localtime(
        time.mktime(t) + days * 86400))


def days_between(a, b):
    return int((time.mktime(time.strptime(b, "%Y-%m-%d")) -
                time.mktime(time.strptime(a, "%Y-%m-%d")))) // 86400


def today_iso():
    return time.strftime("%Y-%m-%d")


# ────────────────────────── sync ──────────────────────────
def cmd_sync(args):
    cfg = load_config()
    days = cfg["backflow_days"]
    if not isinstance(days, list) or len(days) < 3:
        days = [2, 7, 20]

    published = [r for r in read_rows(LIST_CSV) if str(r.get("状态", "")).strip() == "1"]
    if not published:
        print("[sync] %s 里没有状态=1 的已发布记录；先跑 erp_client.py ttl-list" % LIST_CSV)
        return 2

    log_date = {}
    for r in read_rows(LOGS_CSV):
        oid = str(r.get("货源ID") or "").strip()
        if oid and str(r.get("状态") or "").lower() == "success":
            d = (r.get("发布时间") or "")[:10]
            if d and (oid not in log_date or d > log_date[oid]):
                log_date[oid] = d

    existing = {r["ttl_id"]: r for r in read_rows(BACKFLOW)} if os.path.exists(BACKFLOW) else {}
    added, updated = 0, 0

    for p in published:
        ttl_id = str(p.get("ttl_id") or "").strip()
        if not ttl_id:
            continue
        if args.region and str(p.get("站点") or "").strip() != args.region:
            continue
        oid = str(p.get("货源ID") or "").strip()
        pub = log_date.get(oid)
        if not pub:
            if not args.allow_no_date:
                print("[sync] 跳过 %s（%s）：日志里没有 success 时间，无法排期。"
                      "确认实际发布时间可手工补 publish_date，或加 --allow-no-date 以今天计"
                      % (ttl_id, str(p.get("标题") or "")[:20]))
                continue
            pub = today_iso()

        row = existing.get(ttl_id) or {}
        if row:
            updated += 1
        else:
            added += 1
            row = {"ttl_id": ttl_id}
        row.update({
            "offer_id": oid,
            "title": (p.get("标题") or "")[:80],
            "region": p.get("站点") or "",
            "shop": p.get("店铺") or "",
            "publish_date": row.get("publish_date") or pub,
        })
        base = row["publish_date"]
        row["due_t2"] = date_add(base, int(days[0]))
        row["due_t7"] = date_add(base, int(days[1]))
        row["due_t20"] = date_add(base, int(days[2]))
        for k in HEADER:
            row.setdefault(k, "")
        row["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        existing[ttl_id] = row

    rows = list(existing.values())
    write_rows(rows, BACKFLOW)
    print("[sync] %s：新增 %d / 更新 %d / 总计 %d" % (BACKFLOW, added, updated, len(rows)))
    print("[sync] 时点口径：T+2=%s 天、T+7=%s 天、T+20=%s 天（selection.yaml → cadence）"
          % (days[0], days[1], days[2]))
    print("[sync] 下一步：python3 scripts/backflow.py due")
    return 0


# ────────────────────────── due ──────────────────────────
def stage_status(row, today, cfg):
    """返回 [(stage, due_date, overdue_days, missing_fields)]"""
    out = []
    checks = [
        ("T+2", row.get("due_t2"), ["t2_exposure"], 3),
        ("T+7", row.get("due_t7"), ["t7_exposure", "t7_clicks"], 7),
        ("T+20", row.get("due_t20"),
         ["t20_orders", "t20_gmv", "t20_refund_rate", "t20_late_ship_rate"], 20),
    ]
    for name, due, fields, grace in checks:
        if not due:
            continue
        missing = [f for f in fields if str(row.get(f, "")).strip() == ""]
        if not missing:
            continue
        od = days_between(due, today)
        if od < 0:
            continue
        out.append((name, due, od, missing, od > grace))
    return out


def cmd_due(args):
    cfg = load_config()
    rows = read_rows(BACKFLOW)
    if not rows:
        print("[due] %s 不存在或为空，先跑 python3 scripts/backflow.py sync" % BACKFLOW)
        return 2
    today = args.today or today_iso()

    pending, overdue = [], []
    for r in rows:
        for name, due, od, missing, is_overdue in stage_status(r, today, cfg):
            item = {"ttl_id": r.get("ttl_id"), "title": (r.get("title") or "")[:28],
                    "region": r.get("region"), "stage": name, "due": due,
                    "overdue_days": od, "missing": missing,
                    "overdue": is_overdue}
            (overdue if is_overdue else pending).append(item)

    if args.json:
        print(json.dumps({"today": today, "due": pending, "overdue": overdue},
                         ensure_ascii=False, indent=2))
    else:
        print("=== 回流到期检查（今天 %s）===" % today)
        if not pending and not overdue:
            print("无到期项。")
        for tag, items in (("逾期未填（超宽限期）", overdue), ("已到期未填（宽限期内）", pending)):
            if not items:
                continue
            print("\n【%s】%d 项" % (tag, len(items)))
            print("  %-6s %-3s %-8s %-28s %s" % ("stage", "逾期", "due", "标题", "缺字段"))
            for it in sorted(items, key=lambda x: (x["due"], x["ttl_id"])):
                print("  %-6s %3d天 %-8s %-28s %s"
                      % (it["stage"], it["overdue_days"], it["due"],
                         it["title"], ",".join(it["missing"])))
        if overdue:
            print("\n提示：T+2 逾期 = 错过入池判定窗口（曝光<%s 就该换主图重收录）；"
                  "T+20 逾期 = 该 kill 的没 kill，会持续占配额" % cfg["index_exposure_min"])

    return 1 if overdue else 0


# ────────────────────────── mark ──────────────────────────
def cmd_mark(args):
    rows = read_rows(BACKFLOW)
    if not rows:
        print("[mark] backflow.csv 为空，先 sync")
        return 2
    target = [r for r in rows if str(r.get("ttl_id")) == str(args.ttl_id)]
    if not target:
        print("[mark] 找不到 ttl_id=%s" % args.ttl_id)
        return 2
    row = target[0]
    changed = 0
    stage = args.stage.lower()
    fields = {
        "t2": ["t2_exposure", "t2_note"],
        "t7": ["t7_exposure", "t7_clicks", "t7_add_cart"],
        "t20": ["t20_orders", "t20_gmv", "t20_refund_rate", "t20_late_ship_rate"],
    }.get(stage)
    if not fields:
        print("[mark] --stage 只能是 t2 / t7 / t20")
        return 2
    for key in fields:
        val = getattr(args, key, None)
        if val is not None:
            row[key] = val
            changed += 1
    if not changed:
        print("[mark] 没有提供任何字段。示例：--stage t2 --t2-exposure 120")
        return 2
    row["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    write_rows(rows, BACKFLOW)
    print("[mark] %s 写入 %d 个字段（stage=%s）" % (args.ttl_id, changed, stage))
    print("[mark] 下一步：python3 scripts/backflow.py judge")
    return 0


# ────────────────────────── judge ──────────────────────────
def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def judge_row(row, cfg):
    thr = float(cfg["index_exposure_min"])
    late_warn = float(cfg["late_ship_rate_warn"])
    verdict, action = row.get("verdict", ""), row.get("action", "")

    t2e = num(row.get("t2_exposure"))
    if t2e is not None:
        if t2e < thr:
            verdict = "T2_未入池"
            action = "上架满 48h 后换主图强制重收录（48h 内不要动标题/主图）"
        else:
            verdict = "T2_已入池"
            action = "等 T+7 曝光/点击"

    e7, c7, a7 = (num(row.get("t7_exposure")), num(row.get("t7_clicks")),
                  num(row.get("t7_add_cart")))
    if e7 is not None or c7 is not None:
        if (e7 or 0) == 0:
            verdict = "T7_零曝光"
            action = "仍无曝光 → 等 T+20 判 kill；同时补报商品机会关联词"
        elif (c7 or 0) == 0:
            verdict = "T7_有曝光无点击"
            action = "换主图/改标题（首图与标题决定点击）"
        elif (a7 if a7 is not None else 0) == 0:
            verdict = "T7_有点击无加购"
            action = "调价或换主图（加购率低指向价格/信任度）"
        else:
            verdict = "T7_正常"
            action = "培养，等 T+20 成交判定"

    orders = num(row.get("t20_orders"))
    late = num(row.get("t20_late_ship_rate"))
    if orders is not None:
        if orders <= 0:
            verdict = "KILL"
            action = "下架换品（T+20 零成交），释放铺货配额"
        else:
            if late is not None and late > late_warn:
                verdict = "链路预警"
                action = "延迟发货率 %.0f%% > %.0f%%，先换货代或转小批量备货，别靠换品掩盖"
                action = action % (late * 100, late_warn * 100)
            else:
                verdict = "AMPLIFY"
                action = "进放大池：追加同款变体/同店可搭配品，复用已验证的主图与描述模板"
    return verdict, action


def cmd_judge(args):
    cfg = load_config()
    rows = read_rows(BACKFLOW)
    if not rows:
        print("[judge] backflow.csv 为空，先 sync")
        return 2
    changed = 0
    for r in rows:
        v, a = judge_row(r, cfg)
        if v != r.get("verdict") or a != r.get("action"):
            r["verdict"], r["action"] = v, a
            r["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            changed += 1
    if args.dry_run:
        for r in rows:
            if r.get("verdict"):
                print("%-18s %-10s %-24s %s" % (r.get("ttl_id"), r.get("verdict"),
                                                (r.get("title") or "")[:24],
                                                r.get("action")))
        print("[judge] dry-run：%d 条会更新（未写盘）" % changed)
        return 0
    write_rows(rows, BACKFLOW)
    print("[judge] 更新 %d 条判定 → %s" % (changed, BACKFLOW))
    return 0


# ────────────────────────── stats ──────────────────────────
def cmd_stats(args):
    rows = read_rows(BACKFLOW)
    if not rows:
        print("[stats] backflow.csv 为空，先 sync")
        return 2
    buckets, winners, killers = {}, [], []
    for r in rows:
        v = r.get("verdict") or "未判定"
        buckets[v] = buckets.get(v, 0) + 1
        if v == "AMPLIFY":
            winners.append(r)
        elif v == "KILL":
            killers.append(r)
    print("=== 回流判定分布（共 %d 款）===" % len(rows))
    for k, n in sorted(buckets.items(), key=lambda x: -x[1]):
        print("  %-14s %d" % (k, n))
    if winners:
        print("\n【放大池】%d 款" % len(winners))
        for r in winners:
            print("  %-18s %-28s 单量%s 成交额%s" % (
                r.get("ttl_id"), (r.get("title") or "")[:28],
                r.get("t20_orders"), r.get("t20_gmv")))
    if killers:
        print("\n【待下架】%d 款（释放配额后换新品）" % len(killers))
        for r in killers[:20]:
            print("  %-18s %s" % (r.get("ttl_id"), (r.get("title") or "")[:30]))
    return 0


# ────────────────────────── main ──────────────────────────
def main():
    ap = argparse.ArgumentParser(description="数据回流闭环（排期/提醒/判定）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sync", help="从已发布记录生成回流排期")
    s.add_argument("--region", default="", help="只同步某站点（TH/VN）")
    s.add_argument("--allow-no-date", action="store_true",
                   help="日志无 success 时间时以今天为发布日（会算错时点，慎用）")

    d = sub.add_parser("due", help="列出到期与逾期未填项")
    d.add_argument("--today", default="", help="模拟日期 YYYY-MM-DD（默认今天）")
    d.add_argument("--json", action="store_true")

    m = sub.add_parser("mark", help="回填某时点数据")
    m.add_argument("--ttl-id", required=True)
    m.add_argument("--stage", required=True, choices=["t2", "t7", "t20"])
    m.add_argument("--t2-exposure", dest="t2_exposure", default=None)
    m.add_argument("--t2-note", dest="t2_note", default=None)
    m.add_argument("--t7-exposure", dest="t7_exposure", default=None)
    m.add_argument("--t7-clicks", dest="t7_clicks", default=None)
    m.add_argument("--t7-add-cart", dest="t7_add_cart", default=None)
    m.add_argument("--t20-orders", dest="t20_orders", default=None)
    m.add_argument("--t20-gmv", dest="t20_gmv", default=None)
    m.add_argument("--t20-refund-rate", dest="t20_refund_rate", default=None)
    m.add_argument("--t20-late-ship-rate", dest="t20_late_ship_rate", default=None)

    j = sub.add_parser("judge", help="按规则算 kill/培养/放大")
    j.add_argument("--dry-run", action="store_true")

    sub.add_parser("stats", help="判定分布与赢家清单")

    args = ap.parse_args()
    return {
        "sync": cmd_sync, "due": cmd_due, "mark": cmd_mark,
        "judge": cmd_judge, "stats": cmd_stats,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
