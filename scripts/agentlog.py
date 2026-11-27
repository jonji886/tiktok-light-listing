#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agentlog.py — 任务级结构化日志 + 敏感信息脱敏 + 异常分类 + 有限重试（2026-11-27 工程化）

要解决的问题（此前实测痛点）：
  ① 全部脚本只有 print 到 stdout，进程被杀后只能靠 timing 账本 + state 文件拼现场；
  ② 错误分类散落在各脚本的字符串匹配里（"For input string"、code=500…），口径不一；
  ③ 超时=结果未知，但旧日志不区分「请求失败」与「不知道成没成」。

设计约束（与 timing.py 同一哲学，零依赖、append-only）：
  - 每条事件独立 append 一行 JSONL → 多进程并发安全，不加锁、不读改写；
  - 落盘 erp_data/_run/trace/<task_id>.jsonl（_run 已 gitignore，日志不入库）；
  - task_id 三处取：env TT_TASK_ID → erp_data/_run/.current_task → "adhoc_<pid>"。
    子进程天然继承 env，所以 publish_one 设好 task_id 后，它 subprocess 调起的
    erp_client/set_price/image_gate 全部落在同一条轨迹里；
  - 日志绝不能反过来把主流程搞挂：一切写入 try/except 静默；
  - 任何字符串出日志前必须过 redact()（JWT/Cookie/Token/AccessKey/手机号/邮箱）。

异常分类（与 ERP 实际返回码对应，见 references/erp-pitfalls.md）：
  authentication_error  703/token 失效        → 终止 + 人工刷新登录态
  authorization_error   HTTP 403              → 终止 + 人工核权限
  rate_limit_error      707/HTTP 429          → 有限重试（长等待）
  api_timeout           socket 超时           → 读操作可重试；写操作=unknown，先回查
  network_error         连接失败/域名解析      → 同上
  external_service_error HTTP 5xx             → 读可重试；写=unknown，先回查
  business_rule_error   ERP success=false 业务码(如 500/For input string、[1205xxxx]) → 终止，修数据
  validation_error      本地闸门/参数校验失败   → 终止，修数据（不重试是纪律）
  data_format_error     响应非 JSON/缺关键字段  → 终止 + 人工核接口契约
  unknown_error         兜底                  → 终止 + 人工
注意：HTTP 超时 ≠ 服务端未执行。写操作超时后禁止直接重试，必须先回查业务状态
（与 SKILL.md「超时先回查不盲目重试，重试过→重复认领 20 条」同源）。
"""
import datetime
import json
import os
import random
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# TT_TRACE_DIR 覆盖落盘目录（测试隔离用；默认仍进运行数据区，已 gitignore）。
# 动态解析：任何模块先 import 也不会固化目录（曾因 import 顺序导致测试写进真实轨迹区）
_DEFAULT_TRACE_DIR = os.path.join(ROOT, "erp_data", "_run", "trace")


def trace_dir():
    return os.environ.get("TT_TRACE_DIR") or _DEFAULT_TRACE_DIR
CURRENT_TASK = os.path.join(ROOT, "erp_data", "_run", ".current_task")

_seq = [0]

# ─────────────────────────── task_id ───────────────────────────

def new_task_id(prefix="t"):
    """生成新 task_id（时间戳+随机后缀，同一商品任务全阶段复用同一个）。"""
    return "%s%s_%04x" % (prefix, datetime.datetime.now().strftime("%Y%m%d%H%M%S"),
                          random.randrange(0xFFFF))


def set_current_task(tid):
    """设为当前 task_id：写入 env（子进程继承）+ .current_task 兜底文件。"""
    os.environ["TT_TASK_ID"] = tid
    try:
        os.makedirs(os.path.dirname(CURRENT_TASK), exist_ok=True)
        open(CURRENT_TASK, "w", encoding="utf-8").write(tid)
    except Exception:
        pass
    return tid


def task_id():
    tid = os.environ.get("TT_TASK_ID", "").strip()
    if tid:
        return tid
    try:
        tid = open(CURRENT_TASK, encoding="utf-8").read().strip()
        if tid:
            return tid
    except Exception:
        pass
    return "adhoc_%d" % os.getpid()


def trace_path(tid=None):
    return os.path.join(trace_dir(), "%s.jsonl" % (tid or task_id()))


# ─────────────────────────── 敏感信息脱敏 ───────────────────────────

# 运行期注册的精确密钥（如当前 JWT 原文）——命中即整体替换
_secrets = set()

_PATTERNS = [
    # JWT（三段 base64url）
    (re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"), "<JWT:masked>"),
    # qnquerystring / Authorization / Cookie 头的值
    (re.compile(r"(?i)(qnquerystring|authorization|proxy-authorization|cookie)\s*[:=]\s*[^\s,;\"'}]+"),
     r"\1=<masked>"),
    # 阿里云 AccessKey / DASHSCOPE key
    (re.compile(r"\bLTAI[A-Za-z0-9]{12,}\b"), "<ACCESSKEY:masked>"),
    (re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"), "<APIKEY:masked>"),
    # token=xxx / "token": "xxx" 形式的字段
    (re.compile(r"(?i)([\"']?token[\"']?\s*[:=]\s*)[\"']?[A-Za-z0-9._\-]{8,}[\"']?"), r"\1<masked>"),
    (re.compile(r"(?i)(secret|password|passwd)([\"']?\s*[:=]\s*)[\"']?[^\s,;\"'}]{6,}"), r"\1\2<masked>"),
    # 中国大陆手机号（11 位整段，不误伤 12 位 offerId）
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "<PHONE>"),
    # 邮箱
    (re.compile(r"(?<![\w.])[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "<EMAIL>"),
]


def set_secret(s):
    """登记一个精确密钥串（如刚加载的 JWT），此后所有日志输出中出现的原文一律替换。"""
    s = (s or "").strip()
    if len(s) >= 16:
        _secrets.add(s)


def redact(value):
    """递归脱敏：str 逐个模式替换；dict/list 递归。日志写入前必经此门。"""
    if isinstance(value, str):
        for sec in _secrets:
            if sec and sec in value:
                value = value.replace(sec, "<secret-hidden>")
        for pat, repl in _PATTERNS:
            value = pat.sub(repl, value)
        return value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


# ─────────────────────────── 异常分类 ───────────────────────────

ERR_TYPES = ("validation_error", "authentication_error", "authorization_error",
             "rate_limit_error", "api_timeout", "network_error",
             "external_service_error", "business_rule_error",
             "data_format_error", "unknown_error")


def classify(exc=None, code=None, message="", http_status=None, is_write=False):
    """把异常/错误码/报文归入统一类型，并给出恢复策略。

    返回 dict: error_type, retryable, retry_after_verify, refresh_auth,
               fix_data, human_intervention, terminal
    """
    msg = str(message or "")
    if exc is not None:
        msg = msg or str(exc)
        name = type(exc).__name__
        if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower():
            return _mk("api_timeout", is_write)
        if "URLError" in name or "ConnectionReset" in name or "getaddrinfo" in str(exc).lower():
            return _mk("network_error", is_write)
        status = getattr(exc, "code", None)  # urllib.error.HTTPError
        if status:
            http_status = status
    if code is None and http_status is None:
        m = re.search(r"code\s*[=:]\s*(\d{3})", msg)
        if m:
            code = int(m.group(1))
    if http_status in (401,) or code == 703 or re.search(r"token", msg, re.I) and code == 703:
        r = _mk("authentication_error")
        r["refresh_auth"] = True
        r["human_intervention"] = True
        return r
    if http_status == 403:
        r = _mk("authorization_error")
        r["human_intervention"] = True
        return r
    if code == 707 or http_status == 429 or "限流" in msg:
        r = _mk("rate_limit_error")
        r["retryable"] = True
        return r
    if http_status and 500 <= int(http_status) < 600:
        return _mk("external_service_error", is_write)
    if code == 500 or "For input string" in msg or re.search(r"\[1205\d{4}\]", msg):
        r = _mk("business_rule_error")
        r["fix_data"] = True
        return r
    if "非JSON" in msg or "not JSON" in msg.lower() or "解析" in msg and "失败" in msg:
        r = _mk("data_format_error")
        r["human_intervention"] = True
        return r
    if "闸门" in msg or "校验" in msg or "禁止" in msg or "退出码 1" in msg:
        r = _mk("validation_error")
        r["fix_data"] = True
        return r
    return {"error_type": "unknown_error", "retryable": False, "retry_after_verify": False,
            "refresh_auth": False, "fix_data": False, "human_intervention": True,
            "terminal": True}


def _mk(t, is_write=False):
    unknownish = t in ("api_timeout", "network_error", "external_service_error")
    if unknownish and is_write:
        # 写操作超时/断连/5xx：服务端可能已执行 —— 禁止直接重试，先回查
        return {"error_type": t, "retryable": False, "retry_after_verify": True,
                "refresh_auth": False, "fix_data": False, "human_intervention": True,
                "terminal": False}
    if unknownish:
        return {"error_type": t, "retryable": True, "retry_after_verify": False,
                "refresh_auth": False, "fix_data": False, "human_intervention": False,
                "terminal": False}
    return {"error_type": t, "retryable": False, "retry_after_verify": False,
            "refresh_auth": False, "fix_data": False, "human_intervention": False,
            "terminal": True}


# ─────────────────────────── 事件写入 ───────────────────────────

def event(action, stage="", tool="", status="success", product_id=None, attempt=1,
          error=None, duration_ms=None, retryable=None, human_intervention=None,
          task=None, **meta):
    """写一条事件。签名宽松，失败静默——计时/日志不能反过来搞挂主流程。

    status: start | success | failed | unknown | hold | info
    """
    try:
        _seq[0] += 1
        tid = task or task_id()
        if tid.startswith("adhoc_"):
            # 无人显式指定时把自己固化为当前任务，保证同任务后续阶段同一 id
            set_current_task(tid)
        line = {
            "timestamp": datetime.datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "task_id": tid,
            "event_id": "%s-%d-%d" % (tid, os.getpid(), _seq[0]),
            "product_id": product_id,
            "stage": stage,
            "tool": tool or os.path.basename(sys.argv[0] or ""),
            "action": action,
            "attempt": attempt,
            "status": status,
            "duration_ms": round(duration_ms, 1) if duration_ms is not None else None,
        }
        if error:
            line.update({k: error.get(k) for k in
                         ("error_code", "error_type", "retryable",
                          "human_intervention") if error.get(k) is not None})
        if retryable is not None:
            line["retryable"] = retryable
        if human_intervention is not None:
            line["human_intervention"] = human_intervention
        if meta:
            line["meta"] = meta
        line = redact(line)
        os.makedirs(trace_dir(), exist_ok=True)
        with open(trace_path(tid), "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        return line
    except Exception:
        return None


def read_trace(tid):
    """按 task_id 读全部事件（时间序）。返回 list[dict]，坏行跳过。"""
    out = []
    try:
        with open(trace_path(tid), encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(json.loads(ln))
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return out


# ─────────────────────────── 有限重试 ───────────────────────────

class UnknownResult(Exception):
    """写操作超时/断连/5xx：结果未知，须回查后再决定，禁止盲目重试。"""


def retry_call(fn, classify_fn, is_write=False, max_attempts=3, base_delay=1.0,
               max_delay=15.0, sleep=time.sleep, rng=random.random,
               on_event=None):
    """对 fn() 做指数退避 + 抖动的有限重试。

    classify_fn(exc) 返回 classify() 同构 dict。策略：
      - retryable=True → 退避重试（rate_limit 用 base*4 起步）；
      - retry_after_verify=True 且 is_write → 抛 UnknownResult（绝不重发写请求）；
      - 其余（terminal/校验失败/鉴权失败）→ 原样抛出，不重试。
    永不重试超过 max_attempts，单步等待不超过 max_delay。
    """
    last = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as e:
            last = e
            info = classify_fn(e)
            if on_event:
                on_event(attempt, info, e)
            if info.get("error_type") in ("api_timeout", "network_error",
                                          "external_service_error") and is_write:
                raise UnknownResult(str(e)) from e
            if not info.get("retryable"):
                raise
            if attempt >= max_attempts:
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            if info.get("error_type") == "rate_limit_error":
                delay = min(max_delay, max(delay, base_delay * 4))
            sleep(delay * (0.5 + rng()))  # 抖动：避免同批商品同步重试形成尖峰
    raise last  # 理论上到不了


# ─────────────────────────── 查询 CLI ───────────────────────────

def _iter_traces():
    if not os.path.isdir(trace_dir()):
        return
    for fn in sorted(os.listdir(trace_dir())):
        if fn.endswith(".jsonl"):
            yield fn[:-6], os.path.join(trace_dir(), fn)


def cmd_trace(a):
    rows = read_trace(a.task_id)
    if not rows:
        print("[trace] task %s 无记录" % a.task_id)
        return 1
    for r in rows:
        if a.status and r.get("status") != a.status:
            continue
        print(json.dumps(r, ensure_ascii=False))
    return 0


def cmd_tasks(a):
    """列最近任务：末行状态即任务当前结论。"""
    items = []
    for tid, path in _iter_traces():
        try:
            lines = [ln for ln in open(path, encoding="utf-8") if ln.strip()]
        except Exception:
            continue
        if not lines:
            continue
        last = json.loads(lines[-1])
        items.append((os.path.getmtime(path), tid, last.get("status"),
                      last.get("stage"), len(lines)))
    items.sort(reverse=True)
    for ts, tid, status, stage, n in items[: (a.limit or 15)]:
        print("%-24s %-16s stage=%-12s events=%d" % (tid, status, stage, n))
    return 0


def cmd_failures(a):
    rows = []
    for tid, path in _iter_traces():
        for ln in open(path, encoding="utf-8"):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r.get("status") in ("failed", "unknown"):
                rows.append(r)
    rows.sort(key=lambda r: r.get("timestamp", ""))
    for r in rows[-(a.limit or 20):]:
        print(json.dumps(r, ensure_ascii=False))
    return 0


def cmd_stats(a):
    agg = {}
    dur = {}
    for tid, path in _iter_traces():
        if a.task_id and tid != a.task_id:
            continue
        for ln in open(path, encoding="utf-8"):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            key = r.get(a.by or "error_type") or "-"
            agg[key] = agg.get(key, 0) + 1
            if r.get("duration_ms") is not None:
                dur.setdefault(key, []).append(r["duration_ms"])
    for k in sorted(agg):
        d = dur.get(k) or []
        extra = ""
        if d:
            d.sort()
            extra = "  ms: n=%d p50=%.0f p95=%.0f max=%.0f" % (
                len(d), d[len(d) // 2], d[min(len(d) - 1, int(len(d) * 0.95))], d[-1])
        print("%-28s %4d%s" % (k, agg[k], extra))
    return 0


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="任务轨迹查询（trace JSONL）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("trace"); p.add_argument("--task-id", required=True)
    p.add_argument("--status", default="")
    p.set_defaults(fn=cmd_trace)
    p = sub.add_parser("tasks"); p.add_argument("--limit", type=int, default=15)
    p.set_defaults(fn=cmd_tasks)
    p = sub.add_parser("failures"); p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_failures)
    p = sub.add_parser("stats"); p.add_argument("--by", default="error_type",
                                                choices=["error_type", "stage", "tool", "status"])
    p.add_argument("--task-id", default="")
    p.set_defaults(fn=cmd_stats)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
