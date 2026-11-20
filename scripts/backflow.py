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
  traffic    回填发布后 24h 免费流量三件套（新品折扣/销售加速任务/商品机会关联词）
  traffic-due 列出上架满 24h 仍未完成三件套的款（退出码 1 = 逾期）

时点口径（selection.yaml → cadence.data_backflow_day = [2,7,20]）：
  T+2   入池检查：曝光 < exposure_not_indexed_threshold(100) = 未入池 → 换主图重收录
  T+7   曝光×加购率四象限：低曝光+高转化 = 搜索不匹配 → 改标题补词，**不是 kill**；
        高曝光+低点击 = 换主图；高点击+低加购 = 调价（官方「商品分析四象限」落地）
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

# 入池体检单独落一张表：它每条要记「哪个号搜的、搜的什么词、搜没搜到」，
# 与 backflow.csv 的数值回填是两件事，混在一起会互相覆盖。
INDEX_CSV = os.path.join(ROOT, "erp_data", "index_check.csv")
INDEX_HEADER = [
    "ttl_id", "region", "shop", "title", "publish_date", "checked_at",
    "account_type", "keywords", "found", "exposure_7d", "verdict", "action", "note",
]

# 发布后免费流量三件套单独落表：它记录的不是"数据结果"而是"我做了什么动作"，
# 与数值回流/入池体检是三类事实，混进 backflow.csv 会互相覆盖。
TRAFFIC_CSV = os.path.join(ROOT, "erp_data", "traffic_actions.csv")
TRAFFIC_HEADER = [
    "ttl_id", "publish_date", "new_user_discount", "discount_set_at",
    "accelerator_task", "accelerator_done_at",
    "opportunity_apply", "opportunity_apply_at", "note",
]
TRAFFIC_LABELS = {
    "new_user_discount": "新品折扣/早鸟价",
    "accelerator_task": "销售加速中心任务",
    "opportunity_apply": "商品机会关联词提报",
}


DEFAULTS = {
    "index_check_hours": 48,
    "index_exposure_min": 100,
    "optimize_day": 7,
    "kill_day": 20,
    "backflow_days": [2, 7, 20],
    "late_ship_rate_warn": 0.10,
    "quadrant_high_addcart_rate": 0.10,
    "traffic_action_hours": 24,
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
        "quadrant_high_addcart_rate": "cadence.quadrant_high_addcart_rate",
        "traffic_action_hours": "cadence.traffic_action_hours",
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


def read_index():
    if not os.path.exists(INDEX_CSV) or os.path.getsize(INDEX_CSV) < 2:
        return []
    with open(INDEX_CSV, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_index(rows):
    os.makedirs(os.path.dirname(INDEX_CSV) or ".", exist_ok=True)
    with open(INDEX_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=INDEX_HEADER, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in INDEX_HEADER})


def read_traffic():
    if not os.path.exists(TRAFFIC_CSV) or os.path.getsize(TRAFFIC_CSV) < 2:
        return []
    with open(TRAFFIC_CSV, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_traffic(rows):
    os.makedirs(os.path.dirname(TRAFFIC_CSV) or ".", exist_ok=True)
    with open(TRAFFIC_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TRAFFIC_HEADER, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in TRAFFIC_HEADER})


def traffic_missing(row):
    """返回未完成的三件套中文名列表。必须同时有 true 和动作日期才算做过——
    只勾 true 不记日期 = 自证不清，按未完成处理（与 skill 其它闸门口径一致）。"""
    missing = []
    for flag, date_key in (("new_user_discount", "discount_set_at"),
                           ("accelerator_task", "accelerator_done_at"),
                           ("opportunity_apply", "opportunity_apply_at")):
        if fbool(row.get(flag)) is not True or not str(row.get(date_key) or "").strip():
            missing.append(TRAFFIC_LABELS[flag])
    return missing


def traffic_gaps_for(row, traffic_rec, cfg, today):
    """judge 用：有回填记录就按记录判；无记录 = 没证据（上架满 24h 才算该做没做）。"""
    if traffic_rec is not None:
        return traffic_missing(traffic_rec)
    pub = str(row.get("publish_date") or "").strip()
    hours = int(cfg.get("traffic_action_hours", 24))
    if pub and days_between(pub, today) * 24 >= hours:
        return list(TRAFFIC_LABELS.values())
    return []


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


# ────────────────────────── 入池体检（P0-2）──────────────────────────
# 为什么单独立一张表：曝光 <100 只说明"没量"，不区分"未收录"还是"收录了搜不到"。
# 唯一能区分两者的办法是**陌生账号搜核心短词**（自己的号有浏览记录、会命中自己）。
# 两个信号交叉判：搜得到 + 有曝光 = 入池；搜不到 + 无曝光 = 未入池；
# 搜不到但曝光 ≥ 阈值 = 收录了但标题词与热搜词不匹配（改标题，不是换品）。
def judge_index(found, exposure, cfg):
    thr = float(cfg["index_exposure_min"])
    f = str(found or "").strip().lower()
    e = None
    try:
        e = float(exposure) if str(exposure).strip() != "" else None
    except (TypeError, ValueError):
        e = None

    if f in ("yes", "y", "1", "true", "是", "搜到"):
        if e is not None and e < thr:
            return ("POOLED_LOW_EXPOSURE",
                    "已收录但曝光 %g < %g：标题词太窄或权重低 → 补热搜词根、加视频，别换品" % (e, thr))
        return ("POOLED", "已入池：等 T+7 看点击，别在 48h 内改标题/主图")
    if f in ("no", "n", "0", "false", "否", "没搜到"):
        if e is not None and e >= thr:
            return ("PARTIAL_INDEX",
                    "有曝光但核心词搜不到：标题词与热搜词不匹配 → 只改标题，不要换主图触发重收录")
        return ("NOT_POOLED", "未入池：立即换主图（尺寸/构图/去同质化）强制重收录，48h 后再验")
    return ("UNKNOWN", "搜到/没搜到未记录，入池状态不可判 → 补 --found yes|no")


def cmd_index(args):
    cfg = load_config()
    bf = {str(r.get("ttl_id")): r for r in read_rows(BACKFLOW)}
    src = bf.get(str(args.ttl_id), {})
    if not src:
        print("[index] backflow.csv 里没有 ttl_id=%s：先 sync，或确认该款已发布" % args.ttl_id)
        return 2

    verdict, action = judge_index(args.found, args.exposure_7d, cfg)
    rows = read_index()
    row = next((r for r in rows if str(r.get("ttl_id")) == str(args.ttl_id)), None)
    if row is None:
        row = {"ttl_id": str(args.ttl_id)}
        rows.append(row)
    row.update({
        "region": src.get("region", ""),
        "shop": src.get("shop", ""),
        "title": (src.get("title") or "")[:80],
        "publish_date": src.get("publish_date", ""),
        "checked_at": args.checked_at or time.strftime("%Y-%m-%d %H:%M:%S"),
        "account_type": args.account or "fresh",
        "keywords": args.keyword or "",
        "found": args.found or "",
        "exposure_7d": "" if args.exposure_7d is None else args.exposure_7d,
        "verdict": verdict,
        "action": action,
        "note": args.note or "",
    })
    write_index(rows)
    print("[index] %s → %s" % (args.ttl_id, verdict))
    print("[index] 动作：%s" % action)
    return 0


def cmd_index_due(args):
    """上架满 N 小时还没做入池体检的款 —— 这是「到期强制触发」的入口。"""
    cfg = load_config()
    hours = int(cfg["index_check_hours"])
    rows = read_rows(BACKFLOW)
    if not rows:
        print("[index-due] backflow.csv 为空，先 python3 scripts/backflow.py sync")
        return 2
    today = args.today or today_iso()
    checked = {str(r.get("ttl_id")) for r in read_index() if str(r.get("verdict") or "")}

    pending, overdue, no_date = [], [], []
    for r in rows:
        tid = str(r.get("ttl_id") or "")
        pub = str(r.get("publish_date") or "").strip()
        if not pub:
            no_date.append(r)
            continue
        age_days = days_between(pub, today)
        if age_days * 24 < hours:
            continue
        if tid in checked:
            continue
        item = {"ttl_id": tid, "region": r.get("region"), "title": (r.get("title") or "")[:28],
                "publish_date": pub, "age_days": age_days,
                "due_date": date_add(pub, (hours + 23) // 24)}
        # 超过体检时点 1 天仍未做 = 逾期
        (overdue if age_days * 24 >= hours + 24 else pending).append(item)

    if args.json:
        print(json.dumps({"today": today, "pending": pending, "overdue": overdue,
                          "no_publish_date": [r.get("ttl_id") for r in no_date]},
                         ensure_ascii=False, indent=2))
    else:
        print("=== 入池体检到期（今天 %s，体检时点=上架后 %dh）===" % (today, hours))
        if not pending and not overdue:
            print("无待检项。")
        for tag, items in (("逾期未检", overdue), ("刚到期待检", pending)):
            if not items:
                continue
            print("\n【%s】%d 项" % (tag, len(items)))
            print("  %-19s %-3s %-10s %-6s %s" % ("ttl_id", "站", "上架日", "已挂", "标题"))
            for it in sorted(items, key=lambda x: x["publish_date"]):
                print("  %-19s %-3s %-10s %2d天  %s"
                      % (it["ttl_id"], it["region"] or "-", it["publish_date"],
                         it["age_days"], it["title"]))
        if no_date:
            print("\n! %d 款缺 publish_date，无法排期（backflow sync --allow-no-date 或手工补）"
                  % len(no_date))
        if pending or overdue:
            print("\n体检方法（两条都要记）：")
            print("  1) 陌生账号（未登录过店铺、无浏览记录）搜 2-3 个核心短词，看能否搜到本款")
            print("  2) 后台 商品分析 → 近 7 天曝光")
            print("  回填：python3 scripts/backflow.py index --ttl-id <id> --found yes|no "
                  "--keyword '<词>' --exposure-7d <数>")
        if overdue:
            print("\n❌ 逾期 = 错过入池判定窗口（曝光<%s 就该换主图重收录）" % cfg["index_exposure_min"])
    return 1 if overdue else 0


def cmd_index_stats(args):
    rows = read_index()
    if not rows:
        print("[index-stats] 还没有体检记录（%s）" % INDEX_CSV)
        return 2
    buckets = {}
    for r in rows:
        v = r.get("verdict") or "未判定"
        buckets[v] = buckets.get(v, 0) + 1
    total = len(rows)
    pooled = buckets.get("POOLED", 0) + buckets.get("POOLED_LOW_EXPOSURE", 0) + \
        buckets.get("PARTIAL_INDEX", 0)
    print("=== 入池体检分布（共 %d 款）===" % total)
    for k, n in sorted(buckets.items(), key=lambda x: -x[1]):
        print("  %-22s %d" % (k, n))
    print("\n入池率 %.1f%%（含「收录但曝光低 / 词不匹配」，真正可判死的未入池 %d 款）"
          % (100.0 * pooled / total if total else 0, buckets.get("NOT_POOLED", 0)))
    if buckets.get("NOT_POOLED"):
        print("\n【待换主图重收录】")
        for r in rows:
            if r.get("verdict") == "NOT_POOLED":
                print("  %-19s %-3s %s" % (r.get("ttl_id"), r.get("region") or "-",
                                           (r.get("title") or "")[:34]))
    return 0


# ────────────────────────── 发布后流量动作（P0-4）──────────────────────────
# 官方：销售加速工具 > 营销工具(早鸟价) > 广告工具 —— 这三项是「新店免费流量」，
# 不是可选项。skill 旧版只写在 strategy §8.3，发布流程/backflow 都没有检查项。
def cmd_traffic(args):
    bf = {str(r.get("ttl_id")): r for r in read_rows(BACKFLOW)}
    if not bf:
        print("[traffic] backflow.csv 为空，先 sync")
        return 2
    tid = str(args.ttl_id)
    if tid not in bf:
        print("[traffic] backflow.csv 里没有 ttl_id=%s" % tid)
        return 2
    rows = read_traffic()
    row = next((r for r in rows if str(r.get("ttl_id")) == tid), None)
    if row is None:
        row = {"ttl_id": tid, "publish_date": bf[tid].get("publish_date", "")}
        rows.append(row)
    fields = ("new_user_discount", "discount_set_at", "accelerator_task",
              "accelerator_done_at", "opportunity_apply", "opportunity_apply_at", "note")
    changed = 0
    for key in fields:
        val = getattr(args, key, None)
        if val is not None:
            row[key] = str(val)
            changed += 1
    if not changed:
        print("[traffic] 未提供任何字段。示例：\n"
              "  python3 scripts/backflow.py traffic --ttl-id <id> "
              "--new-user-discount true --discount-set-at 2026-11-18 "
              "--accelerator-task true --accelerator-done-at 2026-11-18 "
              "--opportunity-apply true --opportunity-apply-at 2026-11-18")
        return 2
    write_traffic(rows)
    gaps = traffic_missing(row)
    print("[traffic] %s 写入 %d 字段；未完成：%s" % (tid, changed, "/".join(gaps) or "无"))
    print("[traffic] 下一步：python3 scripts/backflow.py judge  # 缺项会写进 action")
    return 0


def cmd_traffic_due(args):
    cfg = load_config()
    hours = int(cfg["traffic_action_hours"])
    bf = read_rows(BACKFLOW)
    if not bf:
        print("[traffic-due] backflow.csv 为空，先 sync")
        return 2
    done = {str(r.get("ttl_id")): r for r in read_traffic()}
    today = args.today or today_iso()
    pending, no_date = [], []
    for r in bf:
        tid = str(r.get("ttl_id") or "")
        pub = str(r.get("publish_date") or "").strip()
        if not pub:
            no_date.append(tid)
            continue
        age_days = days_between(pub, today)
        if age_days * 24 < hours:
            continue
        gaps = traffic_missing(done.get(tid, {}))
        if gaps:
            pending.append({"ttl_id": tid, "title": (r.get("title") or "")[:28],
                            "region": r.get("region"), "publish_date": pub,
                            "age_days": age_days, "missing": gaps})

    if args.json:
        print(json.dumps({"today": today, "hours": hours, "pending": pending,
                          "no_publish_date": no_date}, ensure_ascii=False, indent=2))
    else:
        print("=== 发布后流量动作到期（今天 %s，时点=上架后 %dh）===" % (today, hours))
        if not pending:
            print("无待办项。")
        else:
            print("【未完成三件套】%d 项" % len(pending))
            print("  %-19s %-3s %-10s %-6s %s" % ("ttl_id", "站", "上架日", "已挂", "缺项"))
            for it in sorted(pending, key=lambda x: x["publish_date"]):
                print("  %-19s %-3s %-10s %2d天  %s"
                      % (it["ttl_id"], it["region"] or "-", it["publish_date"],
                         it["age_days"], "/".join(it["missing"])))
            print("\n官方优先级：销售加速任务 > 新品折扣(早鸟价) > 广告工具。"
                  "回填：python3 scripts/backflow.py traffic --ttl-id <id> ...")
        if no_date:
            print("\n! %d 款缺 publish_date，无法排期" % len(no_date))
    return 1 if pending else 0


# ────────────────────────── judge ──────────────────────────
def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fbool(v):
    s = str(v or "").strip().lower()
    if s in ("true", "1", "yes", "y", "是"):
        return True
    if s in ("false", "0", "no", "n", "否"):
        return False
    return None


def judge_row(row, cfg, index_verdict="", traffic_gaps=None):
    thr = float(cfg["index_exposure_min"])
    late_warn = float(cfg["late_ship_rate_warn"])
    high_cvr = float(cfg.get("quadrant_high_addcart_rate", 0.10))
    verdict, action = row.get("verdict", ""), row.get("action", "")

    t2e = num(row.get("t2_exposure"))
    if t2e is not None:
        if t2e < thr:
            verdict = "T2_未入池"
            action = "上架满 48h 后换主图强制重收录（48h 内不要动标题/主图）"
        else:
            verdict = "T2_已入池"
            action = "等 T+7 曝光/点击"

    # 入池体检优先于纯曝光阈值：曝光低有两种病，药不同
    if index_verdict == "NOT_POOLED":
        verdict = "T2_未入池"
        action = "换主图强制重收录（陌生账号搜不到 + 无曝光），48h 后再验"
    elif index_verdict == "PARTIAL_INDEX":
        verdict = "T2_收录但词不匹配"
        action = "只改标题补热搜词根，不要动主图（会触发重收录）"
    elif index_verdict in ("POOLED", "POOLED_LOW_EXPOSURE"):
        verdict = "T2_已入池"
        action = "等 T+7 曝光/点击"

    # ── T+7 四象限（官方「商品分析四象限」落地）──
    # 官方原文：低曝光高转化 = 搜索不匹配，回到「商品可被搜索」阶段优化，不是换品/kill。
    # T+7 还没有成交数据，用「加购率 = add_cart/clicks」代理转化率；这是经验阈值，非官方数字。
    e7, c7, a7 = (num(row.get("t7_exposure")), num(row.get("t7_clicks")),
                  num(row.get("t7_add_cart")))
    if e7 is not None or c7 is not None:
        e, c = (e7 or 0), (c7 or 0)
        a = a7 if a7 is not None else 0
        cvr = (a / c) if c > 0 else 0.0
        if e < thr and c > 0 and cvr >= high_cvr:
            # 低曝光 + 高转化：旧逻辑会误判 T7_零曝光/有曝光无点击 → 等 kill
            verdict = "T7_低曝光高转化"
            action = ("别 kill：搜索不匹配 → 只改标题补热搜词根 + 提报商品机会关联词，"
                      "不要动主图（会触发重收录）")
        elif e == 0:
            verdict = "T7_零曝光"
            action = "仍无曝光且无转化信号 → 等 T+20 判 kill；同时补报商品机会关联词"
        elif c == 0:
            verdict = "T7_有曝光无点击"
            action = "高曝光低点击：换主图/改标题（首图与标题决定点击）"
        elif a == 0:
            verdict = "T7_有点击无加购"
            action = "高曝光低转化：调价或换主图（加购率低指向价格/信任度）"
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

    if traffic_gaps:
        action += ("；⚠ 发布后流量动作未完成：" + "/".join(traffic_gaps) +
                   "（官方免费流量优先级：销售加速 > 早鸟价 > 广告），先补齐再优化其它")
    return verdict, action


def cmd_judge(args):
    cfg = load_config()
    rows = read_rows(BACKFLOW)
    if not rows:
        print("[judge] backflow.csv 为空，先 sync")
        return 2
    idx = {str(r.get("ttl_id")): str(r.get("verdict") or "") for r in read_index()}
    traffic = {str(r.get("ttl_id")): r for r in read_traffic()}
    today = today_iso()
    changed = 0
    for r in rows:
        tid = str(r.get("ttl_id") or "")
        gaps = traffic_gaps_for(r, traffic.get(tid), cfg, today)
        v, a = judge_row(r, cfg, idx.get(tid, ""), gaps)
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
    s.add_argument("--region", default="", help="只同步某站点（MY/TH/VN）")
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

    ix = sub.add_parser("index", help="回填单款入池体检结果（陌生账号搜词 + 7 天曝光）")
    ix.add_argument("--ttl-id", required=True)
    ix.add_argument("--found", default="", choices=["yes", "no", "unknown"],
                    help="陌生账号搜核心短词能否搜到本款")
    ix.add_argument("--keyword", default="", help="实际搜索用的 2-3 个核心短词")
    ix.add_argument("--exposure-7d", dest="exposure_7d", default=None,
                    help="后台近 7 天曝光数")
    ix.add_argument("--account", default="fresh", choices=["fresh", "own"],
                    help="搜索用的账号类型（own 会因浏览记录失真，只作参考）")
    ix.add_argument("--checked-at", default="")
    ix.add_argument("--note", default="")

    ix_due = sub.add_parser("index-due", help="列出上架满 48h 仍未做入池体检的款（退出码 1 = 逾期）")
    ix_due.add_argument("--today", default="")
    ix_due.add_argument("--json", action="store_true")

    sub.add_parser("index-stats", help="入池率与待换主图清单")

    t = sub.add_parser("traffic", help="回填发布后 24h 免费流量三件套")
    t.add_argument("--ttl-id", required=True)
    t.add_argument("--new-user-discount", choices=["true", "false"], default=None)
    t.add_argument("--discount-set-at", default=None, help="设置日期 YYYY-MM-DD")
    t.add_argument("--accelerator-task", choices=["true", "false"], default=None)
    t.add_argument("--accelerator-done-at", default=None)
    t.add_argument("--opportunity-apply", choices=["true", "false"], default=None)
    t.add_argument("--opportunity-apply-at", default=None)
    t.add_argument("--note", default=None)

    td = sub.add_parser("traffic-due", help="列出上架满 24h 仍未完成三件套的款（退出码 1 = 有逾期）")
    td.add_argument("--today", default="")
    td.add_argument("--json", action="store_true")

    args = ap.parse_args()
    return {
        "sync": cmd_sync, "due": cmd_due, "mark": cmd_mark,
        "judge": cmd_judge, "stats": cmd_stats,
        "index": cmd_index, "index-due": cmd_index_due, "index-stats": cmd_index_stats,
        "traffic": cmd_traffic, "traffic-due": cmd_traffic_due,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
