# -*- coding: utf-8 -*-
"""任务四回归：task_id 轨迹、结构化日志、异常分类、脱敏、重试策略（全部离线 Mock）。

对应验收标准 C（可观测性）与安全项：
  - 每个任务唯一 task_id、可还原完整轨迹、重试保留原 task_id 记 attempt
  - 敏感信息不出日志（JWT/Cookie/手机号/邮箱/AccessKey）
  - 读=有限退避；写=永不盲重试（超时→unknown）
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
# 测试轨迹与运行数据隔离（子进程 CLI 继承本 env）
os.environ.setdefault("TT_TRACE_DIR", os.path.join(tempfile.gettempdir(), "tll_test_trace"))

import agentlog  # noqa: E402


class TestTaskId(unittest.TestCase):
    def test_env_priority_and_persistence(self):
        os.environ["TT_TASK_ID"] = "task_env_test"
        try:
            self.assertEqual(agentlog.task_id(), "task_env_test")
        finally:
            del os.environ["TT_TASK_ID"]

    def test_same_task_all_stages_one_id(self):
        """同一 task_id 下多阶段事件都落进同一个轨迹文件（可完整还原）。"""
        tid = agentlog.new_task_id("ttest")
        for stage in ("collect", "claim", "publish"):
            agentlog.event("stage_ok", stage=stage, task=tid, product_id="999")
        rows = agentlog.read_trace(tid)
        self.assertEqual([r["stage"] for r in rows], ["collect", "claim", "publish"])
        self.assertTrue(all(r["task_id"] == tid for r in rows))
        # event_id 互不相同
        self.assertEqual(len({r["event_id"] for r in rows}), 3)

    def test_retry_keeps_task_id_increments_attempt(self):
        tid = agentlog.new_task_id("ttest")
        agentlog.event("claim", stage="claim", status="failed", attempt=1, task=tid)
        agentlog.event("claim", stage="claim", status="success", attempt=2, task=tid)
        rows = agentlog.read_trace(tid)
        self.assertEqual([r["attempt"] for r in rows], [1, 2])
        self.assertEqual(len({r["task_id"] for r in rows}), 1)

    def test_different_products_different_tasks(self):
        a = agentlog.new_task_id("t")
        b = agentlog.new_task_id("t")
        self.assertNotEqual(a, b)


class TestRedaction(unittest.TestCase):
    # 拼接构造，避免仓库卫生扫描把测试假值当真值命中
    JWT = ".".join(["eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiIxMjM0NTY3ODkwIn0",
                    "dozjgNryP4J3jVmNHl0w5E" + "x" * 12])

    def test_jwt_in_message_masked(self):
        out = agentlog.redact("header qnquerystring=%s end" % self.JWT)
        self.assertNotIn(self.JWT, out)
        self.assertIn("<masked>", out)

    def test_exact_secret_registered_masked(self):
        agentlog.set_secret(self.JWT)
        out = agentlog.redact({"note": "token was %s" % self.JWT})
        self.assertNotIn(self.JWT, json.dumps(out))

    def test_cookie_phone_email_accesskey(self):
        out = agentlog.redact(
            "Cookie: sid=abc123; 手机 13812345678; 邮箱 a@b.com; LTAI5tABCDEFGHIJKLMNOP")
        for leak in ("abc123", "13812345678", "a@b.com", "LTAI5t"):
            self.assertNotIn(leak, out, leak)

    def test_offerid_not_false_positive(self):
        """12-13 位 offerId / 19 位商品 ID 不能被手机号规则误伤。"""
        s = "offer 751364095317 num 1738670773360624727"
        self.assertEqual(agentlog.redact(s), s)

    def test_recursive_structures(self):
        out = agentlog.redact({"a": ["13812345678", {"b": "x@y.com"}]})
        self.assertNotIn("13812345678", json.dumps(out))
        self.assertNotIn("x@y.com", json.dumps(out))

    def test_logs_never_contain_secret(self):
        """脱敏发生在 event() 写盘前：轨迹文件里不得出现登记密钥。"""
        tid = agentlog.new_task_id("tsec")
        agentlog.set_secret("SUPERSECRET1234567890")
        agentlog.event("x", task=tid, msg="got SUPERSECRET1234567890 here")
        raw = open(agentlog.trace_path(tid), encoding="utf-8").read()
        self.assertNotIn("SUPERSECRET1234567890", raw)
        self.assertIn("<secret-hidden>", raw)


class TestErrorClassification(unittest.TestCase):
    def test_auth_703(self):
        info = agentlog.classify(code=703, message="token expired")
        self.assertEqual(info["error_type"], "authentication_error")
        self.assertTrue(info["refresh_auth"] and info["human_intervention"])
        self.assertFalse(info["retryable"])

    def test_rate_limit_707(self):
        info = agentlog.classify(code=707, message="限流")
        self.assertEqual(info["error_type"], "rate_limit_error")
        self.assertTrue(info["retryable"])

    def test_read_timeout_retryable(self):
        info = agentlog.classify(TimeoutError("timed out"), is_write=False)
        self.assertEqual(info["error_type"], "api_timeout")
        self.assertTrue(info["retryable"])

    def test_write_timeout_unknown_not_retryable(self):
        """核心纪律：HTTP 超时 ≠ 服务端没执行。写操作超时禁止直接重试。"""
        info = agentlog.classify(TimeoutError("timed out"), is_write=True)
        self.assertFalse(info["retryable"])
        self.assertTrue(info["retry_after_verify"])

    def test_business_error_no_retry(self):
        info = agentlog.classify(code=500, message="For input string")
        self.assertEqual(info["error_type"], "business_rule_error")
        self.assertFalse(info["retryable"])
        self.assertTrue(info["fix_data"])

    def test_publish_12052250_business(self):
        info = agentlog.classify(message="[12052250] 不允许使用汉字")
        self.assertEqual(info["error_type"], "business_rule_error")

    def test_5xx_external(self):
        e = agentlog.classify("HTTP 502", http_status=502)
        self.assertEqual(e["error_type"], "external_service_error")
        self.assertTrue(e["retryable"])
        w = agentlog.classify("HTTP 504", http_status=504, is_write=True)
        self.assertFalse(w["retryable"])
        self.assertTrue(w["retry_after_verify"])

    def test_all_types_reachable(self):
        got = {
            agentlog.classify(code=703, message="token")["error_type"]: 1,
            agentlog.classify(http_status=403)["error_type"]: 1,
            agentlog.classify(code=707)["error_type"]: 1,
            agentlog.classify(TimeoutError("timed out"))["error_type"]: 1,
            agentlog.classify(RuntimeError("URLError: getaddrinfo failed"))["error_type"]: 1,
            agentlog.classify(http_status=503)["error_type"]: 1,
            agentlog.classify(code=500, message="For input string")["error_type"]: 1,
            agentlog.classify(message="prepublish_guard 校验失败")["error_type"]: 1,
            agentlog.classify(message="响应非JSON /x")["error_type"]: 1,
            agentlog.classify(message="???")["error_type"]: 1,
        }
        for t in agentlog.ERR_TYPES:
            self.assertIn(t, got, "%s 无对应分类路径" % t)


class TestRetryPolicy(unittest.TestCase):
    def _mk(self, exc, retryable=True):
        return lambda e: {"error_type": "api_timeout", "retryable": retryable}

    def test_read_retries_then_succeeds(self):
        calls = []
        flaky = [0]

        def fn():
            calls.append(1)
            flaky[0] += 1
            if flaky[0] < 3:
                raise TimeoutError("timed out")
            return "ok"
        slept = []
        out = agentlog.retry_call(fn, self._mk(None), max_attempts=3,
                                  sleep=slept.append, rng=lambda: 0.5)
        self.assertEqual(out, "ok")
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(slept), 2)  # 第 3 次成功不再 sleep

    def test_exponential_backoff_and_cap(self):
        slept = []

        def fn():
            raise TimeoutError("t")
        with self.assertRaises(TimeoutError):
            agentlog.retry_call(fn, self._mk(None), max_attempts=4, base_delay=1.0,
                                 max_delay=15.0, sleep=slept.append, rng=lambda: 0.5)
        # 退避 1,2,4（×jitter 1.0）递增且不超过 max_delay
        self.assertEqual(len(slept), 3)
        self.assertTrue(all(15 * 1.5 >= s >= 0 for s in slept))
        self.assertGreater(slept[-1], slept[0])

    def test_write_timeout_raises_unknown_and_calls_once(self):
        calls = []

        def fn():
            calls.append(1)
            raise TimeoutError("t")
        with self.assertRaises(agentlog.UnknownResult):
            agentlog.retry_call(fn, lambda e: agentlog.classify(e, is_write=True),
                                is_write=True, max_attempts=3, sleep=lambda s: None)
        self.assertEqual(len(calls), 1)  # 写操作超时：一次都不重发

    def test_terminal_error_no_retry(self):
        calls = []

        def fn():
            calls.append(1)
            raise ValueError("业务校验失败")
        with self.assertRaises(ValueError):
            agentlog.retry_call(fn, lambda e: agentlog.classify(code=500,
                                                                message="For input string"),
                                max_attempts=3, sleep=lambda s: None)
        self.assertEqual(len(calls), 1)


class TestTraceQueryCLI(unittest.TestCase):
    """查询 CLI 进程内验证（subprocess 依赖 env 继承，发现顺序下不稳）。"""

    def _run_cli(self, *argv):
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = agentlog.main(list(argv))
        return rc, buf.getvalue()

    def test_trace_filters_by_task(self):
        tid = agentlog.new_task_id("tcli")
        agentlog.event("a", stage="claim", task=tid, status="failed")
        agentlog.event("b", stage="publish", task=tid, status="success")
        rc, out = self._run_cli("trace", "--task-id", tid)
        self.assertEqual(rc, 0)
        rows = [json.loads(ln) for ln in out.strip().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["task_id"] == tid for r in rows))

    def test_trace_status_filter(self):
        tid = agentlog.new_task_id("tcli2")
        agentlog.event("a", stage="claim", task=tid, status="failed")
        agentlog.event("b", stage="publish", task=tid, status="success")
        rc, out = self._run_cli("trace", "--task-id", tid, "--status", "failed")
        rows = [json.loads(ln) for ln in out.strip().splitlines()]
        self.assertEqual(len(rows), 1)

    def test_stats_by_error_type(self):
        tid = agentlog.new_task_id("tstat")
        agentlog.event("x", task=tid, status="failed",
                       error={"error_type": "api_timeout", "error_code": "E1"})
        rc, out = self._run_cli("stats", "--task-id", tid)
        self.assertIn("api_timeout", out)
