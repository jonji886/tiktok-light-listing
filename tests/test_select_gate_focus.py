#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""官方 2026 ✅ 重点类目信号的回归测试（2026-11-18 P0-2）。

核心约束：✅ 是「优先方向」不是准入白名单 —— 未命中只能 WARN，绝不能阻断 collect。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import select_gate  # noqa: E402


class TestOfficialFocus(unittest.TestCase):
    def setUp(self):
        self.cfg = select_gate.load_official_focus()

    def test_config_loads_with_source_and_time_warning(self):
        self.assertIsNotNone(self.cfg)
        self.assertGreaterEqual(len(self.cfg["categories"]), 18)
        self.assertIn("time_warning", self.cfg["_source"])
        self.assertIn("policy", self.cfg["_source"])

    def test_hit_on_home_organizer(self):
        status, hits = select_gate.match_official_focus("家居收纳用品 塑料", self.cfg)
        self.assertEqual(status, "hit")
        self.assertIn("家居收纳用品", hits)

    def test_hit_on_festive_category(self):
        status, hits = select_gate.match_official_focus("节日派对装饰", self.cfg)
        self.assertEqual(status, "hit")
        self.assertTrue(any("节日" in h for h in hits))

    def test_miss_does_not_claim_hit(self):
        status, hits = select_gate.match_official_focus("完全不相关的商品", self.cfg)
        self.assertEqual(status, "miss")
        self.assertEqual(hits, [])

    def test_missing_config_is_unknown_not_miss(self):
        status, hits = select_gate.match_official_focus("家居收纳", None)
        self.assertEqual(status, "unknown")
        self.assertEqual(hits, [])

    def test_category_name_map_builds_from_tree(self):
        names = select_gate.build_category_name_map()
        self.assertIn("600001", names)
        self.assertTrue(names["600001"])

    def test_miss_only_warns_never_blocks(self):
        row = {"region": "VN", "title_zh": "不相关商品", "category_id": "600001"}
        ident, issues, warns, _, focus = select_gate.judge(
            row, 0, select_gate.load_thresholds(), {}, None, False,
            self.cfg, select_gate.build_category_name_map())
        self.assertEqual(focus["status"], "miss")
        self.assertTrue(any("OFFICIAL_FOCUS_MISS" in w for w in warns))
        # 未命中类目本身不能成为阻断理由（其它字段缺失的 issues 与本测试无关）
        self.assertFalse(any("OFFICIAL_FOCUS" in i for i in issues))

    def test_hit_has_no_miss_warning(self):
        row = {"region": "VN", "title_zh": "家居收纳盒", "category_id": "600001"}
        _, _, warns, _, focus = select_gate.judge(
            row, 0, select_gate.load_thresholds(), {}, None, False,
            self.cfg, select_gate.build_category_name_map())
        self.assertEqual(focus["status"], "hit")
        self.assertFalse(any("OFFICIAL_FOCUS_MISS" in w for w in warns))


if __name__ == "__main__":
    unittest.main()
