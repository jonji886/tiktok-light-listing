# -*- coding: utf-8 -*-
"""B0 批量漏斗回归测试（2026-11-26：6 品实测 opt_b2batch_6c/6d 复盘的固化）。

覆盖两个实测暴露的 bug：
1. A③ 发货地白名单：pool 带「浙江省金华市」全称时，原 rstrip("市") 得到
   「浙江省金华」匹配不上白名单「金华」→ 全批误杀。修为「包含任一白名单城市」。
2. batch_funnel 认领成功后 state 必须停在 detail（不是 claim），否则第4步
   publish_one next 会重跑 stage_claim → 每品重复认领，采集箱脏数据翻倍。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import precheck  # noqa: E402


class TestShipCityWhitelist(unittest.TestCase):
    def test_full_province_name_passes(self):
        for city in ("浙江省金华市", "广东省潮州市", "江苏省苏州市", "上海市"):
            ok, reasons = precheck.precheck_enriched(
                dict(ship_city=city, qty_begin="1", price_low="5"))
            self.assertTrue(ok, "%s 应命中白名单，实际 %s" % (city, reasons))

    def test_plain_city_passes(self):
        ok, reasons = precheck.precheck_enriched(
            dict(ship_city="金华", qty_begin="1", price_low="5"))
        self.assertTrue(ok, reasons)

    def test_blacklist_city_blocked(self):
        ok, reasons = precheck.precheck_enriched(
            dict(ship_city="山东省菏泽市", qty_begin="1", price_low="5"))
        self.assertFalse(ok)
        self.assertTrue(any("A③" in r for r in reasons))

    def test_empty_skips(self):
        ok, reasons = precheck.precheck_enriched(
            dict(ship_city="", qty_begin="1", price_low="5"))
        self.assertTrue(ok, reasons)


class TestBatchFunnelClaimDedup(unittest.TestCase):
    """静态断言：认领成功分支把 stage 设为 detail 并记 claim 到 done_stages。
    不 mock 网络，只校验源码结构——行为层由 6 品 e2e（opt_b2batch_6d）覆盖。"""

    def test_success_branch_stages_at_detail(self):
        src = open(os.path.join(ROOT, "scripts", "precheck.py"), encoding="utf-8").read()
        # 定位 found 分支（批量认领命中）
        marker = 'if o in found:'
        pos = src.index(marker)
        chunk = src[pos:pos + 600]
        self.assertIn('st["stage"], st["ttl_id"] = "detail", found[o]', chunk,
                      "认领成功后 state 必须停在 detail，避免 next 重跑 claim")
        self.assertIn("done_stages", chunk)
        self.assertIn('append("claim")', chunk)

    def test_argparse_b2_batch_defined(self):
        src = open(os.path.join(ROOT, "scripts", "precheck.py"), encoding="utf-8").read()
        self.assertIn('ap.add_argument("--b2-batch"', src)

    def test_driver_propagates_b2_batch(self):
        src = open(os.path.join(ROOT, "scripts", "batch_listing.py"), encoding="utf-8").read()
        self.assertIn('ap.add_argument("--b2-batch"', src)
        self.assertIn('pc_cmd.append("--b2-batch")', src)

    def test_driver_auto_loads_env(self):
        src = open(os.path.join(ROOT, "scripts", "batch_listing.py"), encoding="utf-8").read()
        self.assertIn("def _load_env()", src)
        self.assertIn("_load_env()", src)


class TestChannelGrading(unittest.TestCase):
    """C 通道分级：fast 模式中文召回 0/6（实测）——代码必须保持 accurate。"""

    def test_fast_not_used_as_replacement(self):
        src = open(os.path.join(ROOT, "scripts", "ocr_vision_batch.swift"),
                   encoding="utf-8").read()
        # 默认 recognitionLevel = accurate；--fast 只是实验室开关，不得在 pipeline 调用
        self.assertIn("fast ? .fast : .accurate", src)

    def test_precheck_uses_stop_after(self):
        src = open(os.path.join(ROOT, "scripts", "precheck.py"), encoding="utf-8").read()
        self.assertIn('"--stop-after"', src)
        self.assertIn("PRE_STOP_AFTER", src)

    def test_stop_after_marks_unjudged(self):
        src = open(os.path.join(ROOT, "scripts", "auto_verdict.py"), encoding="utf-8").read()
        self.assertIn("def stop_after_mode", src)
        self.assertIn('"unjudged"', src)

    def test_stop_after_budget_guard(self):
        """A/B 实测：脏品（49图仅3张干净）无闸时 stop-after 比全册判慢45%。
        预算闸 max_judge 必须存在且可由 PRE_STOP_MAX 覆盖，防回归。"""
        av = open(os.path.join(ROOT, "scripts", "auto_verdict.py"), encoding="utf-8").read()
        self.assertIn("max_judge=20", av)
        self.assertIn("judged >= max_judge and clean < need", av)
        self.assertIn('"--stop-max"', av)
        pc = open(os.path.join(ROOT, "scripts", "precheck.py"), encoding="utf-8").read()
        self.assertIn("PRE_STOP_MAX", pc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
