#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""failure_router 离线测试：规则路由正确性 + golden set 回测 + 未路由降级。"""
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import failure_router as fr  # noqa: E402

GOLDEN = os.path.join(HERE, "fixtures", "failure_golden_set.json")


class TestFailureRouter(unittest.TestCase):
    def setUp(self):
        self.rules = fr.load_rules()
        self.by_id = {r["id"]: r for r in self.rules}

    def test_rules_table_shape(self):
        for r in self.rules:
            self.assertTrue(r.get("id") and r.get("name") and r.get("tier"))
            self.assertIn(r["tier"], ("auto", "manual"))
            self.assertTrue(r.get("codes") or r.get("patterns"),
                            "规则 %s 缺少 codes 与 patterns" % r["id"])
            for fx in r.get("fix", []):
                self.assertTrue(fx.strip())

    def test_code_route_12052250(self):
        r = fr.classify("xxx 商品前置校验未通过: [12052250] 不允许使用汉字。修改产品属性的属性值名称: 悬挂.", self.rules)
        self.assertTrue(r["routed"])
        self.assertIn("cjk_attributes", [m["id"] for m in r["matched"]])

    def test_composite_codes_route_all(self):
        text = ("[12052018] 必须填写包裹长度；[12052019] 必须填写包裹宽度；"
                "[12052020] 必须填写包裹高度；[12052585] 您的保证金余额不足。")
        got = {m["id"] for m in fr.classify(text, self.rules)["matched"]}
        self.assertIn("package_dims_required", got)
        self.assertIn("deposit_insufficient", got)

    def test_fullwidth_bracket_normalization(self):
        r = fr.classify("【12052522】变体缺少主图", self.rules)
        self.assertIn("variant_missing_image", [m["id"] for m in r["matched"]])

    def test_pattern_route_main_image(self):
        r = fr.classify("主图上传失败：TikTok商店 上传图片失败: 参数无效", self.rules)
        self.assertIn("main_image_upload_failed", [m["id"] for m in r["matched"]])
        self.assertEqual(r["codes"], [])

    def test_unknown_text_falls_back_to_tier2(self):
        r = fr.classify("完全没见过的报错内容xyz", self.rules)
        self.assertFalse(r["routed"])
        self.assertEqual(r["matched"], [])

    def test_golden_set_backtest_100pct(self):
        with open(GOLDEN, encoding="utf-8") as f:
            cases = json.load(f)["cases"]
        miss = []
        for c in cases:
            got = {m["id"] for m in fr.classify(c["error_text"], self.rules)["matched"]}
            if not set(c["expect"]) <= got:
                miss.append((c["id"], c["expect"], sorted(got)))
        self.assertEqual(miss, [], "golden set 未全命中: %s" % miss)

    def test_cli_backtest_exit0_and_output(self):
        with patch.object(sys, "argv", ["failure_router.py", "backtest"]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = fr.main()
        self.assertEqual(rc, 0)
        self.assertIn("命中率", buf.getvalue())

    def test_cli_scan_exit_code_contract(self):
        with patch.object(sys, "argv", ["failure_router.py", "scan", "--csv", GOLDEN]):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = fr.main()
        # golden set 行不是标准日志 CSV（无 状态 列），fail 行 0 -> 无未路由 -> exit 0
        self.assertEqual(rc, 0)
        self.assertIn("fail 行 0", buf.getvalue())

    def test_scan_flags_unrouted(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8-sig") as f:
            f.write("货源ID,状态,失败原因\n999,fail,神秘未知错误abc\n")
            tmp = f.name
        try:
            with patch.object(sys, "argv", ["failure_router.py", "scan", "--csv", tmp]):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = fr.main()
            self.assertEqual(rc, 1)
            self.assertIn("MISS", buf.getvalue())
        finally:
            os.unlink(tmp)


def _mk_root(tmp_root, offer, log_rows, pipeline=None):
    """搭一个最小离线 root：logs CSV + pipeline 状态。"""
    os.makedirs(os.path.join(tmp_root, "erp_data", "logs_test"), exist_ok=True)
    csv_path = os.path.join(tmp_root, "erp_data", "logs_test", "tiktok_logs.csv")
    with open(csv_path, "w", encoding="utf-8-sig") as f:
        f.write("货源ID,状态,标题,发布时间,店铺,失败原因\n")
        f.write(log_rows)
    if pipeline is not None:
        pdir = os.path.join(tmp_root, "erp_data", "_run", "pipeline")
        os.makedirs(pdir, exist_ok=True)
        with open(os.path.join(pdir, offer + ".json"), "w", encoding="utf-8") as f:
            json.dump(pipeline, f, ensure_ascii=False)
    return csv_path


class TestFailureRouterApply(unittest.TestCase):
    STOCK_FAIL = ("[12052055] The SKU inventory quantity for the warehouse 'x' "
                  "must be between 0 and 999999, inclusive.")

    def setUp(self):
        import tempfile
        self.root = tempfile.mkdtemp(prefix="fr_apply_")
        self.mocked = None

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _run(self, argv, mock_rc=0, mock_side_effect=None):
        calls = []
        def fake_run(argv2, cwd=None, capture_output=None, text=None, timeout=None):
            calls.append((list(argv2), cwd))
            if mock_side_effect:
                return mock_side_effect(argv2)
            m = unittest.mock.Mock()
            m.returncode = mock_rc
            m.stdout = "ok"
            m.stderr = ""
            return m
        with patch.object(sys, "argv", ["failure_router.py"] + argv):
            buf = io.StringIO()
            with redirect_stdout(buf):
                with patch.object(fr.subprocess, "run", side_effect=fake_run):
                    rc = fr.main()
        return rc, buf.getvalue(), calls

    def test_dry_run_default_no_subprocess(self):
        _mk_root(self.root, "111", "111,fail,t,%s,shop,%s\n" % ("2026-11-25 10:00", self.STOCK_FAIL),
                 pipeline={"offer_id": "111", "site": "MY", "ttl_id": "T1",
                           "edit": {"weight_g": "70", "dims": "25x18x2"}})
        argv = ["apply", "--ids", "111", "--root", self.root]
        rc, out, calls = self._run(argv)
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [], "dry-run 默认不得调用任何子进程")
        self.assertIn("[EXEC] stock_overflow", out)
        self.assertIn("fix_sku.py --id T1 --stock 1000", out)

    def test_execute_runs_dry_then_write(self):
        _mk_root(self.root, "111", "111,fail,t,%s,shop,%s\n" % ("2026-11-25 10:00", self.STOCK_FAIL),
                 pipeline={"offer_id": "111", "site": "MY", "ttl_id": "T1"})
        argv = ["apply", "--ids", "111", "--root", self.root, "--execute"]
        rc, out, calls = self._run(argv)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("--save", " ".join(calls[0][0]), "第一步必须是 dry-run（无 --save）")
        self.assertIn("--save", " ".join(calls[1][0]), "第二步才是真写")
        self.assertTrue(os.path.exists(os.path.join(
            self.root, "erp_data", "_run", "failure_router_apply")), "journal 目录未落盘")

    def test_execute_stops_on_first_failure(self):
        _mk_root(self.root, "111", "111,fail,t,%s,shop,%s\n" % ("2026-11-25 10:00", self.STOCK_FAIL),
                 pipeline={"offer_id": "111", "site": "MY", "ttl_id": "T1"})
        argv = ["apply", "--ids", "111", "--root", self.root, "--execute"]
        rc, out, calls = self._run(argv, mock_rc=1)
        self.assertEqual(rc, 1)
        self.assertEqual(len(calls), 1, "dry 步失败后不得继续真写步")

    def test_manual_tier_never_executed(self):
        _mk_root(self.root, "222", "222,fail,t,%s,shop,[12052585] 您的存款金额不足。\n" % "2026-11-25 10:00",
                 pipeline={"offer_id": "222", "site": "MY", "ttl_id": "T2"})
        argv = ["apply", "--ids", "222", "--root", self.root, "--execute"]
        rc, out, calls = self._run(argv)
        self.assertEqual(calls, [], "tier=manual 规则禁止任何子进程执行")
        self.assertIn("[MANUAL] deposit_insufficient", out)

    def test_missing_ttl_id_degrades_to_plan(self):
        _mk_root(self.root, "333", "333,fail,t,%s,shop,%s\n" % ("2026-11-25 10:00", self.STOCK_FAIL),
                 pipeline={"offer_id": "333", "site": "MY"})
        argv = ["apply", "--ids", "333", "--root", self.root, "--execute"]
        rc, out, calls = self._run(argv)
        self.assertEqual(calls, [])
        self.assertIn("[PLAN] stock_overflow", out)
        self.assertIn("缺参数 ttl_id", out)

    def test_on_sale_check_skips_republish(self):
        fail = "重发时 filterAlreadyPublish 静默跳过已提交过的 ttl_id，计数不动"
        _mk_root(self.root, "444", "444,fail,t,%s,shop,%s\n" % ("2026-11-25 10:00", fail),
                 pipeline={"offer_id": "444", "site": "MY", "ttl_id": "T4",
                           "edit": {"weight_g": "70", "dims": "25x18x2"}})
        def se(argv2):
            if "site_goods.py" in " ".join(argv2):
                out_path = argv2[argv2.index("--out") + 1]
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump({"on_sale": True, "product_status": "ACTIVATE"}, f)
                m = unittest.mock.Mock()
                m.returncode = 0
                m.stdout = m.stderr = ""
                return m
            raise AssertionError("已在售时不得再调用发布命令")
        argv = ["apply", "--ids", "444", "--root", self.root, "--execute"]
        rc, out, calls = self._run(argv, mock_side_effect=se)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1, "只应调用一次 site_goods status")
        self.assertIn("已在售", out)

    def test_initial_status_skips_republish(self):
        fail = "重发时 filterAlreadyPublish 静默跳过已提交过的 ttl_id"
        _mk_root(self.root, "555", "555,fail,t,%s,shop,%s\n" % ("2026-11-25 10:00", fail),
                 pipeline={"offer_id": "555", "site": "MY", "ttl_id": "T5",
                           "edit": {"weight_g": "70", "dims": "25x18x2"}})
        def se(argv2):
            out_path = argv2[argv2.index("--out") + 1]
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump({"on_sale": False, "product_status": "INITIAL"}, f)
            m = unittest.mock.Mock()
            m.returncode = 3
            m.stdout = m.stderr = ""
            return m
        argv = ["apply", "--ids", "555", "--root", self.root, "--execute"]
        rc, out, calls = self._run(argv, mock_side_effect=se)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1)
        self.assertIn("INITIAL", out)

    def test_execute_cap_10_ids(self):
        ids = ",".join(str(i) for i in range(11))
        argv = ["apply", "--ids", ids, "--root", self.root, "--execute"]
        rc, out, calls = self._run(argv)
        self.assertEqual(rc, 2)
        self.assertEqual(calls, [])

    def test_latest_fail_reason_picks_newest(self):
        rows = ("111,fail,t1,2026-11-24 09:00,shop,旧错误 [12052055]\n"
                "111,fail,t2,2026-11-25 10:00,shop,新错误 [12052585]\n")
        _mk_root(self.root, "111", rows)
        got = fr.latest_fail_reason(self.root, "111")
        self.assertIn("12052585", got)
        self.assertNotIn("12052055", got)


if __name__ == "__main__":
    unittest.main()
