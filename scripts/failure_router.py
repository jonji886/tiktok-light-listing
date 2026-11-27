#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""失败路由器：把铺货失败原因原文路由到已验证的规则（诊断 + 修复命令）。

三层架构（2026-11-25 P0-2）：
  Tier 1 = 本脚本：错误码/文本模式精确路由（确定性，零幻觉，退出码即契约）
  Tier 2 = 路由不到的失败 -> 转 LLM 读全量案例库诊断（调用方自行编排，
           本脚本输出 case 模板供回填）
  Tier 3 = 案例库规模超限后才引入 embedding 检索（当前 20 类去重失败，
           全量可路由，不启用）

子命令：
  classify  --text "..." [--json]        单条失败原因路由
  scan      [--csv a.csv b.csv] [--json] 扫日志 CSV（默认 glob erp_data/**/tiktok_logs.csv）
  backtest  [--set tests/fixtures/failure_golden_set.json] [--json]   历史案例回测
  apply     --ids <offerId,...> [--site MY] [--execute] [--json]      修复计划 / 受控执行

apply 安全边界（2026-11-25）：
  - 仅对 --ids 点名清单执行（写操作纪律），单次上限 10 个货源
  - 默认 dry-run 只出计划；--execute 只执行规则表里带 auto_exec 的规则
    （当前仅 stock_overflow=fix_sku 收库存、silent_skip=先查在售再 --force 重发），
    tier=manual（保证金/建仓）与无 auto_exec 的规则一律只出计划不执行
  - 每条写命令先跑 dry-run 变体，成功才跑真命令；任一步失败立即停止该货源
  - 重发前先跑 site_goods status：已 ACTIVATE 跳过（防双链接）、INITIAL 跳过（等审核）
  - ttl_id/weight/dims 从 erp_data/_run/pipeline/<offer>.json 解析，缺参数降级为计划
  - 全程 journal 落盘 erp_data/_run/failure_router_apply/<ts>.jsonl
  - 脚本不自判修复成功：执行后必须人工跑四路验证

退出码：0 = 全部路由到已知规则；1 = 存在未路由失败（转 LLM/人工）；2 = 用法/IO 错误。
纯标准库。规则表 config/failure_rules.json，规则只收实测案例，不收推测。
"""
import argparse
import csv
import glob
import json
import os
import re
import shlex
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_PATH = os.path.join(HERE, "config", "failure_rules.json")
GOLDEN_DEFAULT = os.path.join(HERE, "tests", "fixtures", "failure_golden_set.json")

# 全角/变体括号归一化（ERP 报错原文两种括号都出现过）
_TRANS = str.maketrans({
    "［": "[", "］": "]", "【": "[", "】": "]",
    "（": "(", "）": ")", "：": ":", "；": ";",
})
CODE_RE = re.compile(r"1205\d{4}")
MAX_APPLY_IDS = 10


def load_rules(path=None):
    path = path or RULES_PATH
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["rules"]


def classify(text, rules=None):
    """路由一条失败原因原文。返回 {"matched": [rule...], "codes": [...], "routed": bool}。

    一条报错常含多个错误码（如 [12052018]+[12052585]），返回全部命中规则，
    修复时逐条处置；顺序 = 规则表顺序（非报错原文顺序）。
    """
    if rules is None:
        rules = load_rules()
    norm = (text or "").translate(_TRANS)
    codes_found = sorted(set(CODE_RE.findall(norm)))
    matched = []
    for rule in rules:
        hit = False
        for c in rule.get("codes", []):
            if c in codes_found:
                hit = True
                break
        if not hit:
            for p in rule.get("patterns", []):
                if p and p in norm:
                    hit = True
                    break
        if hit:
            matched.append(rule)
    return {
        "routed": bool(matched),
        "codes": codes_found,
        "matched": [
            {
                "id": r["id"],
                "name": r["name"],
                "tier": r["tier"],
                "diagnosis": r["diagnosis"],
                "fix": r["fix"],
                "evidence": r["evidence"],
            }
            for r in matched
        ],
    }


def unmatched_case_template(text):
    """Tier 2 转交模板：LLM 诊断 + 复盘回填 golden set / 规则表。"""
    return {
        "tier": "llm_fallback",
        "error_text": text,
        "hint": "路由不到已知规则。用 LLM 读 references/erp-pitfalls.md + replay-log.md 全量案例诊断；"
                "确诊后回填 config/failure_rules.json（新规则）与本 golden set（新案例），"
                "不得凭猜测给修复命令。",
    }


def iter_fail_rows(csv_paths):
    for path in csv_paths:
        try:
            with open(path, encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    status = (row.get("状态") or row.get("status") or "").strip()
                    reason = (row.get("失败原因") or row.get("errorMessage") or "").strip()
                    pub = (row.get("发布时间") or row.get("publishTime") or "").strip()
                    if status == "fail" and reason:
                        yield path, (row.get("货源ID") or row.get("itemId") or "").strip(), reason, pub
        except OSError as e:
            print("WARN: 读取失败 %s: %s" % (path, e), file=sys.stderr)


def latest_fail_reason(root, offer_id):
    """取该货源最新一条 fail 原因（按 发布时间 列，缺时间则文件顺序兜底）。"""
    root = root or HERE
    paths = sorted(glob.glob(os.path.join(root, "erp_data", "**", "tiktok_logs.csv"),
                             recursive=True))
    best = None
    for _path, iid, reason, pub in iter_fail_rows(paths):
        if iid != offer_id:
            continue
        if best is None or (pub or "") >= (best[0] or ""):
            best = (pub, reason)
    return best[1] if best else None


def resolve_context(root, offer_id, site=None):
    """从流水线状态解析修复参数。缺什么就缺什么，不猜不补位。"""
    ctx = {"offer_id": offer_id, "site": site, "ttl_id": None,
           "weight_kg": None, "dims": None}
    p = os.path.join(root or HERE, "erp_data", "_run", "pipeline", offer_id + ".json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {}
        ctx["ttl_id"] = st.get("ttl_id")
        ctx["site"] = ctx["site"] or st.get("site")
        edit = st.get("edit") or {}
        wg = edit.get("weight_g")
        if wg:
            try:
                ctx["weight_kg"] = round(float(wg) / 1000.0, 3)
            except ValueError:
                pass
        if edit.get("dims"):
            ctx["dims"] = edit["dims"]
    return ctx


def build_actions(matched_rules, ctx):
    """规则 -> action。kind: exec（可受控执行）/ plan（只出命令）/ manual（永不执行）。"""
    actions = []
    for rule in matched_rules:
        if rule.get("tier") == "manual":
            actions.append({"rule_id": rule["id"], "kind": "manual",
                            "reason": "账号/后台配置类问题，脚本禁止执行",
                            "steps": list(rule.get("fix", []))})
            continue
        ae = rule.get("auto_exec")
        if not ae:
            actions.append({"rule_id": rule["id"], "kind": "plan",
                            "reason": "修复需商品数据/多模态判定/人工确认，路由器只出命令不执行",
                            "steps": list(rule.get("fix", []))})
            continue
        missing = [k for k in ae.get("requires", []) if not ctx.get(k)]
        if missing:
            actions.append({"rule_id": rule["id"], "kind": "plan",
                            "reason": "缺参数 %s（pipeline 状态未落盘），降级为计划" % ",".join(missing),
                            "steps": list(rule.get("fix", []))})
            continue
        actions.append({
            "rule_id": rule["id"], "kind": "exec",
            "precheck": ae.get("precheck"),
            "steps": [{"cmd": s["cmd"].format(**ctx), "mode": s["mode"]}
                      for s in ae.get("steps", [])],
        })
    return actions


def _run_cmd(cmd, root, timeout=180):
    argv = shlex.split(cmd)
    if argv and argv[0] == "python3":
        argv[0] = sys.executable
    try:
        p = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, "命令执行异常: %s" % e


def _on_sale_check(root, offer_id, journal_dir):
    """跑 site_goods.py status：返回 (verdict, detail)。verdict ∈ on_sale/initial/fail/no_record/error。"""
    out_path = os.path.join(journal_dir, "status_%s.json" % offer_id)
    cmd = "python3 scripts/site_goods.py status --offer %s --out %s" % (offer_id, shlex.quote(out_path))
    rc, output = _run_cmd(cmd, root)
    detail = {"cmd": cmd, "rc": rc, "output_tail": output.strip()[-400:]}
    if os.path.exists(out_path):
        try:
            with open(out_path, encoding="utf-8") as f:
                detail["status"] = json.load(f)
        except (OSError, ValueError):
            pass
    st = detail.get("status") or {}
    if st.get("on_sale"):
        return "on_sale", detail
    if rc == 3:
        return "initial", detail
    if rc == 4:
        return "no_record", detail
    return ("error", detail) if rc != 0 else ("not_on_sale", detail)


def cmd_apply(args):
    root = args.root or HERE
    ids = [x.strip() for x in (args.ids or "").split(",") if x.strip()]
    if not ids:
        print("用法错误: --ids 必填（写操作仅对点名清单执行）", file=sys.stderr)
        return 2
    if args.execute and len(ids) > MAX_APPLY_IDS:
        print("拒绝执行: 单次 --execute 上限 %d 个货源（小批次纪律），当前 %d 个" % (MAX_APPLY_IDS, len(ids)),
              file=sys.stderr)
        return 2
    rules = load_rules()
    if args.rules:
        keep = {x.strip() for x in args.rules.split(",") if x.strip()}
        rules = [r for r in rules if r["id"] in keep]
    offers, unrouted = [], []
    for oid in ids:
        reason = latest_fail_reason(root, oid)
        if not reason:
            offers.append({"offer_id": oid, "status": "no_fail_record",
                           "note": "日志中无该货源 fail 记录，不执行任何操作"})
            continue
        r = classify(reason, rules)
        if not r["routed"]:
            unrouted.append(oid)
        ctx = resolve_context(root, oid, args.site)
        by_id = {x["id"]: x for x in rules}
        matched_full = [by_id[m["id"]] for m in r["matched"]]
        offers.append({"offer_id": oid, "status": "routed" if r["routed"] else "unrouted",
                       "error_text": reason, "codes": r["codes"],
                       "context": ctx, "actions": build_actions(matched_full, ctx)})

    if not args.execute:
        if args.json:
            print(json.dumps({"mode": "dry-run", "offers": offers}, ensure_ascii=False, indent=1))
        else:
            for o in offers:
                print("== %s (%s) %s" % (o["offer_id"], o.get("status"), (o.get("error_text") or o.get("note", ""))[:80]))
                for a in o.get("actions", []):
                    print("  [%s] %s — %s" % (a["kind"].upper(), a["rule_id"], a.get("reason", "")))
                    for s in a.get("steps", []):
                        print("      %s" % (s if isinstance(s, str) else s["cmd"]))
            if unrouted:
                print("\n未路由 %d 个货源 -> Tier 2（LLM 诊断 + 回填规则表）" % len(unrouted))
            print("\n(dry-run 默认；确认计划后加 --execute 执行 [EXEC] 项)")
        return (1 if unrouted else 0)

    # ---- 受控执行 ----
    journal_dir = os.path.join(root, "erp_data", "_run", "failure_router_apply")
    os.makedirs(journal_dir, exist_ok=True)
    journal_path = os.path.join(journal_dir, time.strftime("%Y%m%dT%H%M%S") + ".jsonl")

    def jour(event):
        with open(journal_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(event, ts=time.strftime("%Y-%m-%dT%H:%M:%S")),
                               ensure_ascii=False) + "\n")

    jour({"event": "apply_start", "ids": ids, "site": args.site})
    failed = False
    for o in offers:
        if o.get("status") == "no_fail_record":
            continue
        oid = o["offer_id"]
        for a in o["actions"]:
            if a["kind"] != "exec":
                jour({"event": "skip", "offer_id": oid, "rule_id": a["rule_id"], "kind": a["kind"]})
                if not args.json:
                    print("[%s] %s — %s" % (a["kind"].upper(), a["rule_id"], a.get("reason", "")))
                continue
            if a.get("precheck") == "on_sale_check":
                verdict, detail = _on_sale_check(root, oid, journal_dir)
                jour({"event": "precheck", "offer_id": oid, "rule_id": a["rule_id"],
                      "verdict": verdict, "detail": detail})
                if verdict == "on_sale":
                    a["skipped"] = "已在售（ACTIVATE），跳过防双链接自我竞争"
                elif verdict == "initial":
                    a["skipped"] = "INITIAL 已创建待平台审核，跳过等待激活"
                if a.get("skipped"):
                    if not args.json:
                        print("[SKIP] %s — %s" % (a["rule_id"], a["skipped"]))
                    continue
            for s in a["steps"]:
                rc, output = _run_cmd(s["cmd"], root)
                jour({"event": "step", "offer_id": oid, "rule_id": a["rule_id"],
                      "mode": s["mode"], "cmd": s["cmd"], "rc": rc,
                      "output_tail": output.strip()[-500:]})
                if args.json:
                    print(json.dumps({"offer_id": oid, "rule_id": a["rule_id"],
                                      "mode": s["mode"], "cmd": s["cmd"], "rc": rc},
                                     ensure_ascii=False))
                else:
                    print("[%s rc=%d] %s" % (s["mode"], rc, s["cmd"]))
                    if rc != 0:
                        print("  输出尾部: %s" % output.strip()[-300:])
                if rc != 0:
                    a["failed_at"] = s["cmd"]
                    failed = True
                    jour({"event": "abort_offer", "offer_id": oid,
                          "rule_id": a["rule_id"], "at": s["cmd"]})
                    break
            else:
                jour({"event": "action_done", "offer_id": oid, "rule_id": a["rule_id"]})
        # 单个 offer 内任一 action 失败不影响其他 offer（继续小批次），但总退出码为 1

    if args.json:
        print(json.dumps({"mode": "execute", "journal": journal_path, "offers": offers,
                          "failed": failed}, ensure_ascii=False, indent=1))
    else:
        print("journal → %s" % os.path.relpath(journal_path, root))
        print("⚠ 修复命令执行完 ≠ 修复成功：必须人工跑四路验证（logs/ttl-list/quota/后台在售数）")
    return (1 if (failed or unrouted) else 0)


def cmd_scan(args):
    if args.csv:
        paths = args.csv
    else:
        root = os.path.join(HERE, "erp_data")
        paths = sorted(glob.glob(os.path.join(root, "**", "tiktok_logs.csv"), recursive=True))
    rules = load_rules()
    results, unrouted = [], []
    total = 0
    for path, item_id, reason, _pub in iter_fail_rows(paths):
        total += 1
        r = classify(reason, rules)
        entry = {"source": os.path.relpath(path, HERE), "item_id": item_id,
                 "error_text": reason, "routed": r["routed"],
                 "codes": r["codes"], "matched": [m["id"] for m in r["matched"]]}
        results.append(entry)
        if not r["routed"]:
            unrouted.append(entry)
    unique = {}
    for e in results:
        key = e["error_text"]
        if key not in unique:
            unique[key] = {"error_text": key, "count": 0, "routed": e["routed"],
                           "codes": e["codes"], "matched": e["matched"]}
        unique[key]["count"] += 1
    summary = {
        "csv_files": len(paths),
        "fail_rows": total,
        "unique_reasons": len(unique),
        "routed_rows": sum(1 for e in results if e["routed"]),
        "routed_rate": round(sum(1 for e in results if e["routed"]) / total, 4) if total else None,
        "unrouted": [unmatched_case_template(e["error_text"]) for e in unrouted],
        "by_reason": sorted(unique.values(), key=lambda x: -x["count"]),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    else:
        print("日志文件 %d 个 | fail 行 %d | 去重原因 %d | 已路由 %d (%.1f%%)" % (
            len(paths), total, len(unique), summary["routed_rows"],
            (summary["routed_rate"] or 0) * 100))
        for u in sorted(unique.values(), key=lambda x: -x["count"]):
            mark = "OK  " if u["routed"] else "MISS"
            print("[%s] x%-3d %s" % (mark, u["count"], u["error_text"][:90]))
            if u["matched"]:
                print("       -> %s" % ", ".join(u["matched"]))
        if unrouted:
            print("\n未路由失败 %d 条 -> Tier 2（LLM 诊断 + 回填规则表）" % len(unrouted))
    return 0 if not unrouted else 1


def cmd_backtest(args):
    rules = load_rules()
    with open(args.set, encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    hits, misses = [], []
    for case in cases:
        r = classify(case["error_text"], rules)
        got = {m["id"] for m in r["matched"]}
        expect = set(case["expect"])
        ok = expect <= got
        (hits if ok else misses).append({
            "id": case["id"], "expect": sorted(expect), "got": sorted(got), "ok": ok})
    rate = len(hits) / len(cases) if cases else 0.0
    out = {"cases": len(cases), "hits": len(hits), "misses": len(misses),
           "hit_rate": round(rate, 4), "detail": misses or "all_hit"}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print("回测：%d 案例 | 命中 %d | 未中 %d | 命中率 %.1f%%" % (
            len(cases), len(hits), len(misses), rate * 100))
        for m in misses:
            print("  MISS %s expect=%s got=%s" % (m["id"], m["expect"], m["got"]))
    return 0 if not misses else 1


def cmd_classify(args):
    rules = load_rules()
    r = classify(args.text, rules)
    if r["routed"]:
        out = r
    else:
        out = {"routed": False, "codes": r["codes"], "matched": [],
               "fallback": unmatched_case_template(args.text)}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        if not r["routed"]:
            print("MISS -> Tier 2（LLM 诊断）：", args.text[:120])
        for m in r["matched"]:
            print("[%s] %s (%s)" % (m["tier"], m["name"], m["id"]))
            print("  诊断:", m["diagnosis"])
            for fx in m["fix"]:
                print("  修复:", fx)
            print("  证据:", m["evidence"])
    return 0 if r["routed"] else 1


def main():
    ap = argparse.ArgumentParser(description="铺货失败原因路由器（Tier 1 确定性规则）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("classify", help="单条失败原因路由")
    p1.add_argument("--text", required=True)
    p1.add_argument("--json", action="store_true")
    p2 = sub.add_parser("scan", help="扫日志 CSV 全部 fail 行")
    p2.add_argument("--csv", nargs="*", help="指定 CSV（默认 glob erp_data/**/tiktok_logs.csv）")
    p2.add_argument("--json", action="store_true")
    p3 = sub.add_parser("backtest", help="历史失败案例回测")
    p3.add_argument("--set", default=GOLDEN_DEFAULT)
    p3.add_argument("--json", action="store_true")
    p4 = sub.add_parser("apply", help="修复计划（默认 dry-run）/ 受控执行（--execute）")
    p4.add_argument("--ids", required=True, help="1688 货源 ID 逗号分隔（写操作仅对点名清单执行）")
    p4.add_argument("--site", default=None, help="MY/TH/VN，缺省取 pipeline 状态")
    p4.add_argument("--rules", default=None, help="只考虑这些规则 id（调试用）")
    p4.add_argument("--root", default=None, help=argparse.SUPPRESS)
    p4.add_argument("--execute", action="store_true", help="真执行（缺省 dry-run 只出计划）")
    p4.add_argument("--json", action="store_true")
    args = ap.parse_args()
    try:
        return {"classify": cmd_classify, "scan": cmd_scan, "backtest": cmd_backtest,
                "apply": cmd_apply}[args.cmd](args)
    except FileNotFoundError as e:
        print("IO 错误: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
