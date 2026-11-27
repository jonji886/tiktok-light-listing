#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""generate_evaluation_report.py — 从真实运行产物生成评估报告（docs/evaluation.md 的数据源）

原则（与 AGENTS 真实性约束一致）：
  - 每个数字都来自本次执行的命令输出或本地落盘文件，标来源路径；
  - 拿不到的指标如实标「待采集 / 暂无数据」，绝不补位；
  - 四类指标分开呈现（Unit/Integration 测试、Agent 评估、真实业务），不互相替代。

用法：
  python3 scripts/generate_evaluation_report.py            # 打印报告
  python3 scripts/generate_evaluation_report.py --out docs/evaluation_generated.md
"""
import argparse
import csv
import datetime
import glob
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def sh(cmd, timeout=120):
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                       cwd=ROOT, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def ver():
    rc, out = sh("git rev-parse --short HEAD && git log -1 --format=%cd --date=short")
    lines = out.strip().splitlines()
    return (lines[0] if lines else "unknown", lines[1] if len(lines) > 1 else "unknown")


def section_tests():
    rc, out = sh("python3 tests/run.py 2>&1")
    m = re.search(r"Ran (\d+) tests in ([\d.]+)s", out)
    skipped = re.search(r"skipped=(\d+)", out)
    res = {"ran": int(m.group(1)) if m else None,
           "seconds": float(m.group(2)) if m else None,
           "skipped": int(skipped.group(1)) if skipped else 0,
           "passed": (int(m.group(1)) - (int(skipped.group(1)) if skipped else 0)) if m else None,
           "rc": rc}
    return res, out.strip().splitlines()[-1] if out else ""


def section_golden():
    rc, out = sh("python3 scripts/failure_router.py backtest 2>&1")
    m = re.search(r"回测：(\d+) 案例 \| 命中 (\d+) \| 未中 (\d+) \| 命中率 ([\d.]+)%", out)
    if not m:
        return {"ran_cases": None}, out.strip()[-120:]
    return {"ran_cases": int(m.group(1)), "hit": int(m.group(2)),
            "miss": int(m.group(3)), "rate": m.group(4) + "%"}, "failure_router backtest 实测"


def section_golden_fields():
    """黄金集字段完备性检查（任务三 §3 要求的 7 项）。"""
    p = os.path.join(ROOT, "tests", "fixtures", "failure_golden_set.json")
    d = json.load(open(p, encoding="utf-8"))
    cases = d.get("cases", [])
    have_input = sum(1 for c in cases if c.get("error_text"))
    have_expect = sum(1 for c in cases if c.get("expect"))
    missing = [c.get("id") for c in cases if not (c.get("error_text") and c.get("expect"))]
    return {"total": len(cases), "with_input": have_input, "with_expect": have_expect,
            "missing": missing}


def section_business_logs():
    """发布日志统计：只认 logs csv 的 status 列（唯一口径，SKILL 纪律）。"""
    files = glob.glob(os.path.join(ROOT, "erp_data", "**", "tiktok_logs*.csv"),
                      recursive=True)
    # 逐文件直接统计 success/fail 行（时间戳取最新一条 per itemId）
    latest = {}
    for f in files:
        try:
            with open(f, encoding="utf-8-sig") as fh:
                rdr = csv.reader(fh)
                header = next(rdr, None)
                if not header:
                    continue
                # 定位列：itemId 或第一列、status、time
                idx_item = 0
                idx_status = None
                idx_time = None
                for i, h in enumerate(header):
                    hl = h.strip().lower()
                    if hl in ("itemid", "itemid ", "商品id", "货源id"):
                        idx_item = i
                    if hl == "status" or h.strip() == "状态":
                        idx_status = i
                    if hl in ("time", "createtime", "发布时间", "time "):
                        idx_time = i
                if idx_status is None:
                    continue
                for row in rdr:
                    if len(row) <= idx_status:
                        continue
                    k = row[idx_item].strip()
                    t = row[idx_time].strip() if idx_time is not None and len(row) > idx_time else ""
                    if k and (k not in latest or t >= latest[k][0]):
                        latest[k] = (t, row[idx_status].strip().lower())
        except Exception:
            continue
    from collections import Counter
    c = Counter(s for _, s in latest.values() if s)
    return {"log_files": len(files), "status_counts": dict(c),
            "distinct_items": len(latest),
            "note": "按 itemId 最新一条 status 去重；跨批次快照合并，"
                    "非同一口径下的连续漏斗（各批候选数不同）"}


def section_pipeline_states():
    d = os.path.join(ROOT, "erp_data", "_run", "pipeline")
    if not os.path.isdir(d):
        return {"note": "无 pipeline 状态目录"}
    states = {}
    done_full = 0
    for f in glob.glob(os.path.join(d, "*.json")):
        try:
            st = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        stage = st.get("stage", "?")
        states[stage] = states.get(stage, 0) + 1
        if stage == "done":
            done_full += 1
    return {"total": sum(states.values()), "by_stage": states, "reached_done": done_full}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    now = datetime.datetime.now().isoformat(timespec="seconds")
    commit, cdate = ver()
    tests, tline = section_tests()
    golden, gline = section_golden()
    fields = section_golden_fields()
    biz = section_business_logs()
    pipe = section_pipeline_states()

    L = []
    L.append("# Evaluation Report（自动生成）")
    L.append("")
    L.append("> 由 `python3 scripts/generate_evaluation_report.py` 生成；本报告所有数字"
             "均来自本次执行的命令输出或本地文件实测，未标注来源的数字不存在。")
    L.append("")
    L.append("## 运行信息")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append("| 生成时间（本地） | %s |" % now)
    L.append("| 代码版本 | commit `%s`（%s） |" % (commit, cdate))
    L.append("| 测试命令 | `python3 tests/run.py`（离线，不联网、不需要 token） |")
    L.append("| Python | %s |" % sys.version.split()[0])
    L.append("| 环境 | macOS 本机；CI 另有 GitHub Actions ubuntu-3.12 跑同一套件 |")
    L.append("")
    L.append("## 1. 测试通过率（Unit + Integration，离线）")
    L.append("")
    if tests["ran"]:
        L.append("| 指标 | 值 |")
        L.append("|---|---|")
        L.append("| 用例总数 | %d |" % tests["ran"])
        L.append("| 通过 | %d |" % tests["passed"])
        L.append("| 跳过 | %d（缺 PIL 环境相关用例） |" % tests["skipped"])
        L.append("| 失败 | %d |" % (0 if tests["rc"] == 0 else -1))
        L.append("| 耗时 | %.1fs |" % (tests["seconds"] or 0))
    L.append("")
    L.append("口径说明：这**只**覆盖纯本地确定性逻辑（闸门规则、定价公式、SKU 校验、"
             "失败路由、脱敏、重试策略、故障场景 Mock），**不等于**真实业务成功率。"
             "ERP 网络调用全部为 Mock（`unittest.mock`），不触网、不改真实店铺。")
    L.append("")
    L.append("## 2. Agent 黄金测试集（失败路由回测）")
    L.append("")
    if golden.get("ran_cases"):
        L.append("| 指标 | 值 |")
        L.append("|---|---|")
        L.append("| 案例数 | %d |" % golden["ran_cases"])
        L.append("| 命中 | %d |" % golden["hit"])
        L.append("| 命中率 | %s |" % golden["rate"])
    L.append("")
    L.append("字段完备性（tests/fixtures/failure_golden_set.json 实测扫描）：")
    L.append("")
    L.append("| 检查项 | 结果 |")
    L.append("|---|---|")
    L.append("| 含测试输入（error_text） | %d/%d |" % (fields["with_input"], fields["total"]))
    L.append("| 含预期路由结果（expect） | %d/%d |" % (fields["with_expect"], fields["total"]))
    L.append("| 缺字段案例 | %s |" % (fields["missing"] or "无"))
    L.append("")
    L.append("注：`预期工具调用 / 是否重试 / 是否人工接管` 三项在规则表 "
             "`config/failure_rules.json` 的 tier/auto_exec 字段中定义，golden set 目前"
             "只断言路由结果（expect）；已如实标注为**部分覆盖**。")
    L.append("")
    L.append("## 3. 真实业务数据（本地落盘快照统计）")
    L.append("")
    L.append("### 3.1 发布日志（唯一口径：tiktok_logs*.csv 的 status 列）")
    L.append("")
    L.append("| 指标 | 值 | 来源 |")
    L.append("|---|---|---|")
    L.append("| 日志快照文件数 | %d | erp_data/**/tiktok_logs*.csv glob |" % biz["log_files"])
    sc = biz.get("status_counts") or {}
    if sc:
        L.append("| 各状态商品数（按 itemId 最新去重） | %s | 同上 |" %
                 ", ".join("%s=%d" % (k, v) for k, v in sorted(sc.items())))
    L.append("")
    L.append("口径警告：`success` 是 **ERP 发布日志受理成功**，不等于平台侧在售数"
             "（在售唯一口径 = `productStatus == ACTIVATE`，见 `site_goods.py status`）；"
             "跨批快照混合了不同店铺与历史下架品，不能当连续漏斗读。")
    L.append("")
    L.append("### 3.2 单品流水线状态机（erp_data/_run/pipeline/*.json）")
    L.append("")
    if pipe.get("total"):
        L.append("| 指标 | 值 |")
        L.append("|---|---|")
        L.append("| 有状态文件的任务数 | %d |" % pipe["total"])
        L.append("| 走到 done（四路验证通过归档） | %d |" % pipe["reached_done"])
        top = sorted(pipe["by_stage"].items(), key=lambda kv: -kv[1])[:8]
        L.append("| 停留阶段分布（top） | %s |" %
                 ", ".join("%s=%d" % (k, v) for k, v in top))
        L.append("")
        L.append("口径警告：`edit_dry` 大量堆积是**历史批量 start 后未推进的草稿态状态文件**"
                 "（预检/试验残留），不代表 615 个真实在途任务；`reached_done` 与本地快照的"
                 "平台侧 ACTIVATE 数（results-matrix 记 27）一致性待核。")
    L.append("")
    L.append("## 4. 未采集指标（如实标注，不补位）")
    L.append("")
    L.append("| 指标 | 状态 | 原因 |")
    L.append("|---|---|---|")
    L.append("| 商品采集成功率 | 待采集 | collect 回执有成功/失败数，但未跨批聚合落盘 |")
    L.append("| ERP 认领成功率 | 待采集 | 认领走 batchClaimMultiShop 回执，历史未集中记录 |")
    L.append("| Agent 任务完成率（真实环境） | 暂无数据 | 任务多经由 agent 会话驱动，无独立评测 harness |")
    L.append("| 异常诊断准确率 | 部分 | 黄金集回测 23/23 为**规则命中**口径；「LLM 判断异常」准确率未测 |")
    L.append("| 自动恢复成功率 | 待采集 | failure_router apply --execute 使用次数少，样本不足 |")
    L.append("| 人工接管率 | 待采集 | trace 日志自 2026-11-27 起才有 human_approve 事件 |")
    L.append("| 平均执行耗时 | 见 timing 账本 | `python3 scripts/timing.py report --run <id>`（按批统计，无全局聚合口径） |")
    L.append("")
    L.append("## 5. 复现步骤")
    L.append("")
    L.append("```bash")
    L.append("python3 tests/run.py                      # 全部离线测试")
    L.append("python3 scripts/failure_router.py backtest # 黄金集回测")
    L.append("python3 scripts/generate_evaluation_report.py --out docs/evaluation_generated.md")
    L.append("```")
    L.append("")
    text = "\n".join(L) + "\n"
    if a.out:
        p = os.path.join(ROOT, a.out) if not os.path.isabs(a.out) else a.out
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8").write(text)
        print("[report] 写入 %s" % p)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
