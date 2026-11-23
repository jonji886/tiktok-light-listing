# -*- coding: utf-8 -*-
"""report_ready_check 回归测试（P2 提报前置检查，2026-11-23）。

钉死四条：① "laris Amazon"（11-20 弹窗实测被拦原型）→ PLATFORM_WORD；
② 汉字残留标题 → CJK_RESIDUE；③ 连写变体（plasticsampah）与正常马来语标题不误伤；
④ 词表加载忽略注释行。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import ROOT, SCRIPTS  # noqa: F401,E402

sys.path.insert(0, SCRIPTS)
import report_ready_check as rrc  # noqa: E402


class TestWords(unittest.TestCase):
    def test_load_ignores_comments(self):
        banned = rrc.load_banned()
        self.assertIn("amazon", banned)
        self.assertTrue(all(not w.startswith("#") for w in banned))

    def test_platform_word_laris_amazon(self):
        v = rrc.check_title("Apron separuh panjang laris Amazon, kedai kraf", rrc.load_banned())
        self.assertIn("PLATFORM_WORD:amazon", v)

    def test_concatenated_variant(self):
        v = rrc.check_title("larisamazon murah apron", rrc.load_banned())
        self.assertIn("PLATFORM_WORD:amazon", v)

    def test_cjk_residue(self):
        v = rrc.check_title("磁吸针线盒 Kotak jahit", rrc.load_banned())
        self.assertTrue(any(x.startswith("CJK_RESIDUE") for x in v))

    def test_clean_titles_no_hit(self):
        b = rrc.load_banned()
        self.assertEqual(rrc.check_title(
            "Apron leher boleh dicetak tiga dalam satu, kalis air", b), [])
        self.assertEqual(rrc.check_title(
            "drawstring store plastik sampah murah", b), [])
        self.assertEqual(rrc.check_title(
            "Beg plastik 3kg tebal tahan lasak", b), [])


if __name__ == "__main__":
    unittest.main()
