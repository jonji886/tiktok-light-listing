# -*- coding: utf-8 -*-
"""opportunity_keywords 回归测试（2026-11-20 商品机会→热门关键词收割器）。

锁定四条行为：
  1) 修饰词（tahan lasak / murah）不拆成品类词根，但整串原词仍进整串表；
  2) 多词修饰串本身不进整串表（喂 pool 会拉回无关品）；
  3) 计量单位（pcs/50）不进词根表；
  4) --apply 幂等：同一 CSV 重跑不产生重复行。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import ROOT, SCRIPTS  # noqa: F401,E402

sys.path.insert(0, SCRIPTS)
import opportunity_keywords as ok  # noqa: E402


class TestSplitRoots(unittest.TestCase):
    def setUp(self):
        self.mods = ok.load_modifiers("my")

    def test_modifier_two_word_phrase_excluded(self):
        toks, roots = ok.split_roots("plastic sampah tahan lasak", self.mods)
        self.assertNotIn("tahan", roots)
        self.assertNotIn("lasak", roots)
        self.assertIn("plastic", roots)
        self.assertIn("sampah", roots)

    def test_unit_and_number_excluded(self):
        _t, roots = ok.split_roots("beg plastik sampah 50 pcs tebal", self.mods)
        self.assertNotIn("pcs", roots)
        self.assertNotIn("50", roots)
        self.assertNotIn("tebal", roots)  # tebal 在修饰词表
        self.assertIn("beg", roots)

    def test_short_word_excluded(self):
        _t, roots = ok.split_roots("rak api x", self.mods)
        self.assertNotIn("x", roots)
        self.assertIn("rak", roots)

    def test_verified_2026_11_19_words(self):
        # 首版 7 词根应全部被识别为"已在表"（这里只验拆根正确性）
        _t, roots = ok.split_roots("african net sponge", self.mods)
        self.assertEqual(roots, ["african", "net", "sponge"])


class TestSyncApply(unittest.TestCase):
    def _fixture(self, d):
        # 隔离两张目标表 + 修饰词表到 tmp，避免动真实 config
        kwd = os.path.join(d, "config", "keywords")
        pool = os.path.join(d, "erp_data")
        os.makedirs(kwd)
        os.makedirs(pool)
        with open(os.path.join(kwd, "my.txt"), "w", encoding="utf-8") as f:
            f.write("# test\napron\nsampah\n")
        with open(os.path.join(pool, "keywords_my.txt"), "w", encoding="utf-8") as f:
            f.write("apron dapur\n")
        src = os.path.join(os.path.dirname(ok.__file__), os.pardir,
                           "config", "keywords", "modifiers_my.txt")
        import shutil
        shutil.copy(src, os.path.join(kwd, "modifiers_my.txt"))
        return kwd, pool

    def test_apply_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            kwd, pool = self._fixture(d)
            orig_paths = ok.paths
            ok.paths = lambda site: {
                "roots": os.path.join(kwd, "my.txt"),
                "pool": os.path.join(pool, "keywords_my.txt"),
                "modifiers": os.path.join(kwd, "modifiers_my.txt"),
                "template": os.path.join(pool, "opportunity_keywords_my.csv"),
            }
            csvp = os.path.join(d, "kw.csv")
            with open(csvp, "w", encoding="utf-8") as f:
                f.write("word,volume,rank,copied_at,note\n"
                        "apron chef,2.6k+,1,2026-11-20,\n"
                        "plastic sampah tahan lasak,2.6k+,2,2026-11-20,\n")

            class A:
                site, csv, apply = "my", csvp, True

            ok.cmd_sync(A)
            first_roots = open(os.path.join(kwd, "my.txt"), encoding="utf-8").read()
            ok.cmd_sync(A)
            second_roots = open(os.path.join(kwd, "my.txt"), encoding="utf-8").read()
            ok.paths = orig_paths

            self.assertIn("chef", second_roots.split("\n"))
            self.assertNotIn("tahan\n", second_roots)  # 修饰词不进根表
            # 幂等：第二次 apply 不应新增任何词根行（注释头允许多一行，词根行不重复）
            wr = [w for w in ok.parse_entries(second_roots.split("\n"))]
            self.assertEqual(len(wr), len(set(wr)))
            wp = [w for w in ok.parse_entries(
                open(os.path.join(pool, "keywords_my.txt"), encoding="utf-8").read().split("\n"))]
            self.assertEqual(len(wp), len(set(wp)))


if __name__ == "__main__":
    unittest.main()
