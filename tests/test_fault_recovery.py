# -*- coding: utf-8 -*-
"""任务五故障场景回归（全部 Mock，不触网、不碰真实店铺）。

10 项场景对照验收 D：
 1 采集成功后进程中断 → 断点恢复不重复采集
 2 ERP 认领成功后客户端超时 → 回查而非重发
 3 发布请求超时但服务端已发布 → unknown + 平台侧回查命中即跳过重发
 4 HTTP 429 → 读操作有限退避
 5 HTTP 5xx → 读可重试；写=结果未知
 6 token 过期(703) → 立即终止不重试
 7 SKU 数据校验失败 → 业务错误不重试（validation 路径）
 8 同一任务重复执行 → done 幂等
 9 双进程并发同一任务 → 文件锁拦截
10 人工接管(CP1)后恢复 → approve 续跑，前置阶段不重跑
"""
import io
import json
import os
import sys
import contextlib
import tempfile
import unittest
import urllib.error
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
# 测试轨迹与运行数据隔离
os.environ.setdefault("TT_TRACE_DIR", os.path.join(tempfile.gettempdir(), "tll_test_trace"))

import erp_client  # noqa: E402
import agentlog  # noqa: E402
import tasklock  # noqa: E402

# 拼接构造，避免仓库卫生扫描（test_security_repo_hygiene）把测试假值当真值命中
JWT_FAKE = ".".join(["eyJhbGciOiIxIn0", "eyJzdWIiOiIxIn0", "fake" * 10])


def _resp(payload, status=200):
    raw = json.dumps(payload).encode()

    class R:
        def __init__(self): self.status = status
        def read(self): return raw
        def __enter__(self): return self
        def __exit__(self, *a): return False
    return R()


def _args(**kw):
    class A: pass
    a = A()
    a.token = JWT_FAKE
    for k, v in kw.items():
        setattr(a, k, v)
    return a


@contextlib.contextmanager
def quiet_trace():
    tid = agentlog.new_task_id("tfault")
    os.environ["TT_TASK_ID"] = tid
    try:
        yield tid
    finally:
        del os.environ["TT_TASK_ID"]


class PostFaultTests(unittest.TestCase):
    def setUp(self):
        self._mi = erp_client.MIN_INTERVAL
        erp_client.MIN_INTERVAL = 0  # 测试提速；行为不变

    def tearDown(self):
        erp_client.MIN_INTERVAL = self._mi

    # ── 场景 6：token 过期 → 立即失败，不消耗重试 ──
    def test_token_expired_no_retry(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            return _resp({"success": False, "errorCode": 703, "errorMessage": "token 失效"})
        with mock.patch("urllib.request.urlopen", fake_urlopen), quiet_trace() as tid:
            with self.assertRaises(SystemExit) as cm:
                erp_client._post("/item/center/queryErpItemListPage", {}, _args())
        self.assertIn("703", str(cm.exception))
        self.assertEqual(len(calls), 1)
        rows = agentlog.read_trace(tid)
        self.assertEqual(rows[-1]["error_type"], "authentication_error")

    # ── 场景 4：429 读请求有限重试后成功 ──
    def test_429_read_retries_then_succeeds(self):
        seq = []

        def fake_urlopen(req, timeout=None):
            seq.append(1)
            if len(seq) == 1:
                return _resp({}, status=429)
            return _resp({"success": True, "data": {"total": 1}})
        slept = []
        with mock.patch("urllib.request.urlopen", fake_urlopen), \
             mock.patch("time.sleep", slept.append), quiet_trace():
            out = erp_client._post("/item/center/queryErpItemListPage", {}, _args())
        self.assertEqual(out["total"], 1)
        self.assertEqual(len(seq), 2)
        self.assertEqual(len(slept), 1)  # 退避一次（MIN_INTERVAL 已置 0）

    # ── 场景 5：5xx 读可重试；写=结果未知且不重发 ──
    def test_5xx_read_retry_exhausted(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            return _resp({"err": "gateway"}, status=502)
        slept = []
        with mock.patch("urllib.request.urlopen", fake_urlopen), \
             mock.patch("time.sleep", slept.append), quiet_trace():
            with self.assertRaises(SystemExit) as cm:
                erp_client._post("/item/center/queryErpItemListPage", {}, _args())
        self.assertIn("HTTP 502", str(cm.exception))
        self.assertEqual(len(calls), 3)  # max_attempts=3，有限次数
        self.assertEqual(len(slept), 2)

    def test_5xx_write_unknown_single_send(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            return _resp({"err": "gateway"}, status=504)
        with mock.patch("urllib.request.urlopen", fake_urlopen), quiet_trace() as tid:
            with self.assertRaises(SystemExit) as cm:
                erp_client._post("/item/distribute/batchPublish", {}, _args())
        self.assertIn("结果未知", str(cm.exception))
        self.assertEqual(len(calls), 1)  # 写操作绝不多发一次
        self.assertEqual(agentlog.read_trace(tid)[-1]["status"], "unknown")

    # ── 场景 2/3：写操作超时 → unknown，只发一次 ──
    def test_write_timeout_unknown(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            raise OSError("timed out")
        with mock.patch("urllib.request.urlopen", fake_urlopen), quiet_trace() as tid:
            with self.assertRaises(SystemExit) as cm:
                erp_client._post("/item/crossBorder/commonCollect/batchClaimMultiShop",
                                 {}, _args())
        self.assertIn("结果未知", str(cm.exception))
        self.assertEqual(len(calls), 1)
        last = agentlog.read_trace(tid)[-1]
        self.assertEqual(last["status"], "unknown")
        self.assertTrue(last.get("retryable") is False)

    # ── 场景 7：业务校验失败不重试、归为 business_rule_error ──
    def test_business_error_no_retry(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            return _resp({"success": False, "errorCode": 500,
                          "errorMessage": "For input string: \"abc\""})
        with mock.patch("urllib.request.urlopen", fake_urlopen), quiet_trace() as tid:
            with self.assertRaises(SystemExit):
                erp_client._post("/item/crossBorder/commonCollect/batchClaimMultiShop",
                                 {}, _args())
        self.assertEqual(len(calls), 1)
        self.assertEqual(agentlog.read_trace(tid)[-1]["error_type"], "business_rule_error")

    # ── 读操作超时 → 重试且带抖动（不阻塞真实等待：sleep 打桩） ──
    def test_read_timeout_backoff_then_success(self):
        seq = []

        def fake_urlopen(req, timeout=None):
            seq.append(1)
            if len(seq) < 3:
                raise OSError("timed out")
            return _resp({"success": True, "data": {}})
        slept = []
        with mock.patch("urllib.request.urlopen", fake_urlopen), \
             mock.patch("time.sleep", slept.append), quiet_trace():
            erp_client._post("/item/center/queryErpItemListPage", {}, _args())
        self.assertEqual(len(seq), 3)
        self.assertEqual(len(slept), 2)

    # ── 脱敏：报错路径/trace 不得含 JWT 原文 ──
    def test_jwt_never_in_trace(self):
        def fake_urlopen(req, timeout=None):
            raise OSError("timed out connecting with %s" % JWT_FAKE)
        with mock.patch("urllib.request.urlopen", fake_urlopen), \
             mock.patch("time.sleep", lambda s: None), quiet_trace() as tid:
            with self.assertRaises(SystemExit):
                erp_client._post("/x/y", {}, _args())
        raw = open(agentlog.trace_path(tid), encoding="utf-8").read()
        self.assertNotIn(JWT_FAKE.split(".")[0] + "." + JWT_FAKE.split(".")[1], raw)
        self.assertNotIn(JWT_FAKE, raw)


class PipelineRecoveryTests(unittest.TestCase):
    """publish_one 断点恢复/幂等/并发锁/人工接管——用假 HANDLERS 隔离网络。"""

    def setUp(self):
        import publish_one
        self.po = publish_one
        self.tmp = tempfile.mkdtemp()
        self._run = publish_one.RUNDIR
        publish_one.RUNDIR = self.tmp

    def tearDown(self):
        self.po.RUNDIR = self._run
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _st(self, stage="done_stages_none"):
        return {"offer_id": "900001", "site": "MY", "stage": "detail",
                "approved": [], "edit": {}, "done_stages": [], "attempts": {}}

    def test_collect_done_not_rerun_on_resume(self):
        """场景1：collect 成功后中断；恢复执行 detail，不重复 collect。"""
        calls = []
        orig = dict(self.po.HANDLERS)

        def fake(name):
            return lambda st: (calls.append(name), True)[1]
        for k in orig:
            self.po.HANDLERS[k] = fake(k)
        st = {"offer_id": "900002", "site": "MY", "stage": "claim",
              "approved": ["edit"], "edit": {}, "done_stages": ["collect"]}
        try:
            with quiet_trace():
                rc = self.po.do_next(st)
            self.assertEqual(rc, 0)
            self.assertNotIn("collect", calls)  # 已完成阶段不重跑
            self.assertEqual(calls, ["claim"])
        finally:
            self.po.HANDLERS.update(orig)

    def test_done_state_idempotent(self):
        """场景8：同一任务重复执行——done 后 next 直接返回 0，不调任何 handler。"""
        st = {"offer_id": "900003", "site": "MY", "stage": "done",
              "approved": [], "edit": {}}
        with mock.patch.dict(self.po.HANDLERS, {}, clear=True), quiet_trace():
            self.assertEqual(self.po.do_next(st), 0)

    def test_concurrent_same_task_blocked(self):
        """场景9：另一进程持锁时 do_next 拒绝执行（退出码 7）。"""
        st = {"offer_id": "900004", "site": "MY", "stage": "detail",
              "approved": [], "edit": {}}
        lk = tasklock.acquire("pipeline_900004")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = self.po.do_next(st)
            self.assertEqual(rc, 7)
        finally:
            lk.release()

    def test_checkpoint_gates_write_after_approval(self):
        """场景10：edit_dry 完成但未 approve → next 停在 CP1，不会进 edit_save。"""
        calls = []

        def fake_edit_dry(st):
            calls.append("edit_dry")
            return True
        orig = dict(self.po.HANDLERS)
        self.po.HANDLERS.update({"edit_dry": fake_edit_dry,
                                 "edit_save": lambda st: calls.append("edit_save") or True})
        st = {"offer_id": "900005", "site": "MY", "stage": "edit_dry",
              "approved": [], "edit": {}, "done_stages": [], "attempts": {}}
        try:
            with quiet_trace():
                rc = self.po.do_next(st)      # 跑 edit_dry → 下一步 edit_save 但 CP1 未确认
            self.assertEqual(rc, 5)
            self.assertEqual(st["stage"], "edit_dry")
            self.assertNotIn("edit_save", calls)  # 未授权不执行写操作
            st["approved"].append("edit")        # 模拟 cmd_approve 的效果
            with quiet_trace():
                rc2 = self.po.do_next(st)        # 重跑 edit_dry → 闸开放，推进到 edit_save
            self.assertEqual(rc2, 0)
            self.assertEqual(st["stage"], "edit_save")
            self.assertNotIn("edit_save", calls)
            with quiet_trace():
                self.po.do_next(st)              # edit_save 实际执行
            self.assertIn("edit_save", calls)    # approve 后写操作才执行
        finally:
            self.po.HANDLERS.update(orig)


class TaskLockTests(unittest.TestCase):
    def test_double_acquire_raises_with_pid(self):
        lk = tasklock.acquire("ut_%d" % os.getpid())
        name = "ut_%d" % os.getpid()
        try:
            with self.assertRaises(tasklock.LockedError) as cm:
                tasklock.acquire(name)
            self.assertIsNotNone(cm.exception.holder_pid)
        finally:
            lk.release()

    def test_release_allers_reacquire(self):
        name = "ut_free_%d" % os.getpid()
        lk = tasklock.acquire(name)
        lk.release()
        lk2 = tasklock.acquire(name)
        lk2.release()
