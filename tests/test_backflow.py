#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T+7 四象限与发布后流量动作的回归测试（2026-11-18 P0）。

核心回归：旧 judge_row 是串行单指标，低曝光高转化会被判「等 T+20 kill」，
而官方口径明确这类品应改标题补词，不是换品/kill。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import backflow  # noqa: E402


class TestQuadrant(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(backflow.DEFAULTS)

    def test_low_exposure_high_cvr_is_rescued_not_killed(self):
        """低曝光 + 加购率 20% → 搜索不匹配，只改标题，禁止 kill。"""
        v, a = backflow.judge_row(
            {"t7_exposure": 40, "t7_clicks": 10, "t7_add_cart": 2}, self.cfg)
        self.assertEqual(v, "T7_低曝光高转化")
        self.assertIn("别 kill", a)
        self.assertIn("改标题", a)
        self.assertNotIn("kill；", a)

    def test_zero_exposure_no_signal_still_waits_for_kill(self):
        v, a = backflow.judge_row(
            {"t7_exposure": 0, "t7_clicks": 0, "t7_add_cart": 0}, self.cfg)
        self.assertEqual(v, "T7_零曝光")
        self.assertIn("T+20", a)

    def test_high_exposure_no_click_switches_image(self):
        v, a = backflow.judge_row(
            {"t7_exposure": 500, "t7_clicks": 0, "t7_add_cart": 0}, self.cfg)
        self.assertEqual(v, "T7_有曝光无点击")
        self.assertIn("换主图", a)

    def test_high_exposure_no_cart_adjusts_price(self):
        v, a = backflow.judge_row(
            {"t7_exposure": 500, "t7_clicks": 20, "t7_add_cart": 0}, self.cfg)
        self.assertEqual(v, "T7_有点击无加购")
        self.assertIn("调价", a)

    def test_low_exposure_low_cvr_does_not_enter_rescue_bucket(self):
        """同样是低曝光，加购率为 0 时不能享受「改标题」保护。"""
        v, _ = backflow.judge_row(
            {"t7_exposure": 50, "t7_clicks": 10, "t7_add_cart": 0}, self.cfg)
        self.assertNotEqual(v, "T7_低曝光高转化")

    def test_t20_zero_orders_still_kills_even_if_rescued_at_t7(self):
        """四象限只改 T+7 动作，不推翻 T+20 零成交 kill 规则。"""
        v, _ = backflow.judge_row(
            {"t7_exposure": 40, "t7_clicks": 10, "t7_add_cart": 2,
             "t20_orders": 0}, self.cfg)
        self.assertEqual(v, "KILL")


class TestTrafficActions(unittest.TestCase):
    def test_missing_all_three(self):
        self.assertEqual(len(backflow.traffic_missing({})), 3)

    def test_flag_without_date_is_not_evidence(self):
        """只勾 true 不记日期 = 自证不清，按未完成处理。"""
        row = {"new_user_discount": "true", "accelerator_task": "true",
               "opportunity_apply": "true"}
        self.assertEqual(len(backflow.traffic_missing(row)), 3)

    def test_complete_three_have_no_gaps(self):
        row = {"new_user_discount": "true", "discount_set_at": "2026-11-18",
               "accelerator_task": "true", "accelerator_done_at": "2026-11-18",
               "opportunity_apply": "true", "opportunity_apply_at": "2026-11-18"}
        self.assertEqual(backflow.traffic_missing(row), [])

    def test_gaps_are_appended_to_judge_action(self):
        _, a = backflow.judge_row(
            {"t7_exposure": 500, "t7_clicks": 20, "t7_add_cart": 5},
            dict(backflow.DEFAULTS), "", ["新品折扣/早鸟价"])
        self.assertIn("发布后流量动作未完成", a)
        self.assertIn("新品折扣/早鸟价", a)

    def test_no_gaps_does_not_pollute_action(self):
        _, a = backflow.judge_row(
            {"t7_exposure": 500, "t7_clicks": 20, "t7_add_cart": 5},
            dict(backflow.DEFAULTS), "", [])
        self.assertNotIn("发布后流量动作未完成", a)

    def test_no_traffic_record_after_24h_counts_as_missing(self):
        """traffic_actions.csv 整张表不存在时，老款不能静默通过。"""
        cfg = dict(backflow.DEFAULTS)
        gaps = backflow.traffic_gaps_for(
            {"publish_date": "2026-10-27"}, None, cfg, "2026-11-18")
        self.assertEqual(len(gaps), 3)

    def test_fresh_publish_is_not_nagged(self):
        gaps = backflow.traffic_gaps_for(
            {"publish_date": "2026-11-18"}, None,
            dict(backflow.DEFAULTS), "2026-11-18")
        self.assertEqual(gaps, [])

    def test_existing_record_takes_precedence(self):
        gaps = backflow.traffic_gaps_for(
            {"publish_date": "2026-10-27"},
            {"new_user_discount": "true", "discount_set_at": "2026-11-18",
             "accelerator_task": "true", "accelerator_done_at": "2026-11-18",
             "opportunity_apply": "true", "opportunity_apply_at": "2026-11-18"},
            dict(backflow.DEFAULTS), "2026-11-18")
        self.assertEqual(gaps, [])


if __name__ == "__main__":
    unittest.main()
