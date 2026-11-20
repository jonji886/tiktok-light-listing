#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""48h 书面证据拦截开关（seller_ships_48h_proof）回归测试。

2026-11-19 用户指示暂时关闭拦截：off 模式下缺证据必须降级为 WARN（48H_PROOF_SKIPPED），
不得静默消失；改回 platform_compensation_or_chat 后阻断行为必须原样恢复。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import select_gate  # noqa: E402
from pricing_calc import load_config  # noqa: E402


def base_row(**over):
    row = dict(
        offer_id="900000000009", region="MY", cost_cny="6.0", max_sku_cost_cny="6.0",
        clean_main_images="5", desc_rebuildable="true", clean_main_images_after_fix="5",
        stock="500", min_order_qty="1", ship_transit_days="1",
        seller_48h_proof="false", seller_48h_proof_text="",
        free_shipping="false", free_shipping_proof="", domestic_shipping_cny="3.5",
        weight_g="95", dims_cm="23x16x1", redline_check="pass", pieces="1",
        market_median_price_usd="3.4", market_price_source="fastmoss:test",
        market_price_n="40", market_price_sampled_at="2026-11-19",
    )
    row.update(over)
    return row


class TestProof48hSwitch(unittest.TestCase):
    def setUp(self):
        self.price = load_config()

    def judge(self, row, mode):
        thr = dict(select_gate.load_thresholds())
        thr["seller_ships_48h_proof"] = mode
        out = select_gate.judge(row, 0, thr, self.price, {}, False, None, {})
        return out[1], out[2]  # issues, warns

    def test_off_mode_downgrades_to_warn(self):
        issues, warns = self.judge(base_row(), "off")
        self.assertFalse([i for i in issues if "48H" in i],
                         "off 模式不得有 48h 阻断项")
        self.assertIn("48H_PROOF_SKIPPED(拦截已关闭)", warns[0].split(":")[0]
                      if ":" in warns[0] else warns[0])

    def test_off_mode_silent_when_proof_true(self):
        issues, warns = self.judge(base_row(seller_48h_proof="true"), "off")
        self.assertFalse([i for i in issues if "48H" in i])
        self.assertFalse([w for w in warns if "48H" in w])

    def test_compensation_mode_restores_block(self):
        issues, _ = self.judge(base_row(), "platform_compensation_or_chat")
        self.assertIn("NO_48H_PROOF", issues[0].split(":")[0])

    def test_compensation_mode_requires_text(self):
        issues, _ = self.judge(base_row(seller_48h_proof="true"),
                               "platform_compensation_or_chat")
        self.assertIn("NO_48H_PROOF_TEXT", issues[0].split(":")[0])

    def test_compensation_mode_passes_with_clause(self):
        issues, _ = self.judge(base_row(seller_48h_proof="true",
                                        seller_48h_proof_text="1688晚发必赔服务条款已截图"),
                               "platform_compensation_or_chat")
        self.assertFalse([i for i in issues if "48H" in i])

    def test_yaml_currently_off(self):
        # 2026-11-20 13:18 用户指示：暂不启用强拦截（机验档能力保留但不启用）
        self.assertEqual(select_gate.load_thresholds()["seller_ships_48h_proof"], "off")


class TestMachineProof48h(unittest.TestCase):
    """machine_or_chat 档（2026-11-20）：百炼官方标签机验优先，人工书面兜底。"""

    def setUp(self):
        self.price = load_config()

    def judge(self, row):
        thr = dict(select_gate.load_thresholds())
        thr["seller_ships_48h_proof"] = "machine_or_chat"
        out = select_gate.judge(row, 0, thr, self.price, {}, False, None, {})
        return out[1], out[2]

    def test_late_ship_compensate_y_passes_without_manual(self):
        issues, warns = self.judge(base_row(late_ship_compensate="Y"))
        self.assertFalse([i for i in issues if "48H" in i])
        self.assertFalse([w for w in warns if "48H" in w])

    def test_high_pickup_rate_soft_pass_with_warn(self):
        issues, warns = self.judge(base_row(late_ship_compensate="N", pickup_24h_rate="0.96"))
        self.assertFalse([i for i in issues if "48H" in i])
        self.assertTrue([w for w in warns if "48H_MACHINE_SOFT" in w])

    def test_zero_pickup_rate_is_unknown_not_fail(self):
        # 实测：30天零成交新品揽收率=0.0 与无数据不可区分 → 不得据此判 FAIL/放行
        issues, _ = self.judge(base_row(late_ship_compensate="N", pickup_24h_rate="0.0"))
        self.assertTrue([i for i in issues if "NO_48H_PROOF" in i])

    def test_no_machine_data_falls_back_to_manual(self):
        issues, _ = self.judge(base_row())  # 无 bailian 列值
        self.assertIn("NO_48H_PROOF", issues[0].split(":")[0])

    def test_manual_proof_still_works_in_machine_mode(self):
        issues, _ = self.judge(base_row(seller_48h_proof="true",
                                        seller_48h_proof_text="客服书面确认48h发货"))
        self.assertFalse([i for i in issues if "48H" in i])


if __name__ == "__main__":
    unittest.main(verbosity=2)
