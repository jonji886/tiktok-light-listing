#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图片供给预测闸（clean_main_images_after_fix，2026-11-19 P0-3）回归测试。

背景：发布硬闸 min_publish_main_images=5（prepublish_guard）落地后，货源在选品期
就要预测「image_fix 修复 + SKU/详情图降级复用」能凑几张干净主图。围裙实测：
5 张主图 4 张被删、救回 3 张 + 1 张 SKU 图复用 = 4，恰好演示了不前置预测的代价。

只测供给列的三态行为（与既有 select_gate 测试同风格，不联网）。
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
        offer_id="900000000001", region="TH", cost_cny="4.0", max_sku_cost_cny="4.0",
        clean_main_images="4", desc_rebuildable="true", stock="500", min_order_qty="1",
        ship_transit_days="1", seller_48h_proof="true", free_shipping="true",
        free_shipping_proof="1件起包邮，客服确认", weight_g="300", dims_cm="20x15x5",
        redline_check="pass", pieces="1",
    )
    row.update(over)
    return row


class TestSupplyGate(unittest.TestCase):
    def setUp(self):
        self.thr = select_gate.load_thresholds()
        self.price = load_config()

    def judge(self, row):
        out = select_gate.judge(row, 0, self.thr, self.price, {}, False, None, {})
        return out[1], out[2]  # issues, warns

    def img_codes(self, issues, warns):
        hit = [i.split(":")[0] for i in issues if "IMG_SUPPLY" in i]
        miss = [w.split(":")[0] for w in warns if "IMG_SUPPLY" in w]
        return hit, miss

    def test_threshold_loaded_from_yaml(self):
        self.assertEqual(self.thr["clean_images_after_fix_min"], 5)

    def test_missing_column_warns_not_blocks(self):
        issues, warns = self.judge(base_row())
        hit, miss = self.img_codes(issues, warns)
        self.assertEqual(hit, [])
        self.assertIn("IMG_SUPPLY_UNPREDICTED", miss)

    def test_under_5_blocks(self):
        issues, _ = self.judge(base_row(clean_main_images_after_fix="4"))
        hit, _ = self.img_codes(issues, [])
        self.assertIn("IMG_SUPPLY_LT5", hit)

    def test_equal_5_passes(self):
        issues, _ = self.judge(base_row(clean_main_images_after_fix="5"))
        hit, _ = self.img_codes(issues, [])
        self.assertEqual(hit, [])

    def test_over_5_passes(self):
        issues, _ = self.judge(base_row(clean_main_images_after_fix="7"))
        hit, _ = self.img_codes(issues, [])
        self.assertEqual(hit, [])

    def test_present_but_empty_is_missing_field(self):
        issues, _ = self.judge(base_row(clean_main_images_after_fix=""))
        self.assertTrue(any(i.startswith("MISSING_FIELD") and "after_fix" in i
                            for i in issues))

    def test_template_header_contains_supply_col(self):
        self.assertIn("clean_main_images_after_fix", select_gate.TEMPLATE_HEADER)
        # 渐进策略：不进 REQUIRED_COLS（旧清单不被炸）
        self.assertNotIn("clean_main_images_after_fix", select_gate.REQUIRED_COLS)


if __name__ == "__main__":
    unittest.main()
