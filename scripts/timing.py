#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""timing.py — 全链路耗时账本（2026-11-25 P0：把「慢在哪」变成可度量的事实）

设计要点（为什么这么简单）：
  ① 每条记录独立 append 一行 JSONL，不加锁、不读改写 —— 多进程/多脚本并发安全；
  ② 嵌套用 `parent` 字段表达父子关系，不做聚合，聚合交给 timing_report.py；
  ③ run_id 三处取：env TT_RUN → erp_data/_run/.current_run → 日期。子进程天然继承 env，
     所以 precheck 调 publish_one 时，账本自动同一批、且 parent 关系不丢；
  ④ 记录的是「脚本真实执行区间」，不是 agent 思考时间。agent 空转 = 相邻记录之间
     的时间缝隙（timing_report 会单独算成 agent_gap）。

用法（脚本内）：
    import timing
    with timing.span("precheck.C_scan_ocr", parent="precheck", offer_id=oid):
        ...

用法（CLI，给 agent 打人工环节的时间点）：
    python3 scripts/timing.py init 20261125_p5        # 开一批
    python3 scripts/timing.py mark "CP1人工确认"       # 打一个点（零时长）
    python3 scripts/timing.py report --run 20261125_p5 # 出表
"""
import argparse
import contextlib
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER_DIR = os.path.join(ROOT, "erp_data", "_run", "timing")
CURRENT = os.path.join(ROOT, "erp_data", "_run", ".current_run")


def run_id():
    rid = os.environ.get("TT_RUN", "").strip()
    if rid:
        return rid
    try:
        rid = open(CURRENT, encoding="utf-8").read().strip()
    except Exception:
        rid = ""
    return rid or time.strftime("%Y%m%d")


def ledger_path(rid=None):
    return os.path.join(LEDGER_DIR, "%s.jsonl" % (rid or run_id()))


def rec(module, t0, t1, meta=None, parent=None):
    """写一条。失败静默（计时不能反过来把主流程搞挂）。"""
    try:
        os.makedirs(LEDGER_DIR, exist_ok=True)
        line = dict(
            run=run_id(), module=module, parent=parent or os.environ.get("TT_PARENT") or "",
            t0=round(t0, 3), t1=round(t1, 3), dur=round(t1 - t0, 3),
            pid=os.getpid(), script=os.path.basename(sys.argv[0] or ""),
            meta=meta or {},
        )
        with open(ledger_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        pass


@contextlib.contextmanager
def span(module, parent=None, **meta):
    """计时一个代码块；异常也记（meta.ok=False），不吞异常。"""
    t0 = time.time()
    ok = True
    try:
        yield
    except BaseException:
        ok = False
        raise
    finally:
        m = dict(meta)
        m["ok"] = ok
        rec(module, t0, time.time(), m, parent)


def stage(module, parent=None, **meta):
    """装饰器版：@timing.stage("xxx")"""
    def deco(fn):
        def wrap(*a, **kw):
            with span(module, parent, **meta):
                return fn(*a, **kw)
        return wrap
    return deco


def mark(name, **meta):
    """打点：零时长记录，用于标注人工/agent 环节边界。"""
    now = time.time()
    rec("MARK::" + name, now, now, meta)
    return now


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init")
    i.add_argument("name")
    m = sub.add_parser("mark")
    m.add_argument("name")
    m.add_argument("--meta", default="")
    r = sub.add_parser("report")
    r.add_argument("--run", default="")
    r.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.cmd == "init":
        os.makedirs(os.path.dirname(CURRENT), exist_ok=True)
        open(CURRENT, "w", encoding="utf-8").write(a.name)
        p = ledger_path(a.name)
        os.makedirs(LEDGER_DIR, exist_ok=True)
        open(p, "a", encoding="utf-8").close()
        print("[timing] run=%s → %s" % (a.name, p))
        return 0
    if a.cmd == "mark":
        meta = {}
        for kv in (a.meta or "").split(","):
            if "=" in kv:
                k, v = kv.split("=", 1)
                meta[k.strip()] = v.strip()
        mark(a.name, **meta)
        print("[timing] mark %s %.3f" % (a.name, time.time()))
        return 0
    if a.cmd == "report":
        sys.argv = ["timing_report.py", "--run", a.run] + (["--json"] if a.json else [])
        import timing_report
        return timing_report.main()
    return 2


if __name__ == "__main__":
    sys.exit(main())
