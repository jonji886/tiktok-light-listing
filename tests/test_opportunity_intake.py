# -*- coding: utf-8 -*-
"""opportunity_intake 回归测试（2026-11-19 MY 商品机会判读闸门）。

用例即当日浏览器实测的 MY 站「基于市场趋势的热门商品」6 张真实卡片，
判定结论与人工分析对齐；其中围裙案例专门锁定"启发式不得误报 title-case 通用词"。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import ROOT, SCRIPTS  # noqa: F401,E402

sys.path.insert(0, SCRIPTS)
import opportunity_intake as oi  # noqa: E402
from pricing_calc import load_config  # noqa: E402

CFG = oi.load_yaml(oi.CONFIG)
PRICING = load_config(oi.PRICING)


def judge(title, sales="3.2k+", price_ref="", category="", site="MY", metrics="unknown"):
    row = {"title": title, "sales": sales, "price_ref": price_ref,
           "category": category, "source_site": site, "tiktok_metrics": metrics}
    return oi.judge(row, CFG, PRICING)


class TestKnownBrands(unittest.TestCase):
    def test_watsons_reject(self):
        v, why = judge("WATSONS Pumice Stone 1S")
        self.assertEqual(v, "reject")
        self.assertIn("BRAND_IP", why)

    def test_winstar_reject(self):
        v, why = judge("Winstar Everyday Scented, Fragrance Garbage Bag Roll with Handle")
        self.assertEqual(v, "reject")
        self.assertIn("winstar", why)

    def test_brand_case_insensitive_word_boundary(self):
        # "flash" 作为品牌词命中，但 "flashlight" 不应命中（词边界）
        v, _ = judge("Flash Garbage Bags")
        self.assertEqual(v, "reject")
        v, why = judge("Flashlight Storage Box")
        self.assertNotIn("BRAND_IP", why)


class TestHeuristicNoFalsePositive(unittest.TestCase):
    """title-case 通用词不得判品牌（2026-11-19 收紧：启发式只认全大写词）。"""

    def test_multi_pocket_apron_not_brand(self):
        v, why = judge(
            "Multi-Pocket Apron / Chef Apron / Kitchen Apron / Aksara Apron",
            price_ref="RM5.00 - RM12.00",
            category="Bekalan Rumah > Dapur > Apron", metrics="yes")
        self.assertNotIn("brand_suspect", why)
        self.assertEqual(v, "candidate")

    def test_filem_not_brand(self):
        v, why = judge("[Filem Penutup Cat] 30-300cm Masking Film with Tape",
                       sales="2.9k+",
                       category="Bekalan Rumah > Alat > Filem", metrics="yes")
        self.assertNotIn("brand_suspect", why)


class TestPriceBand(unittest.TestCase):
    def test_diaper_bag_below_band_reject(self):
        # RM0.55–4.55 ≈ $0.13–1.11 < MY 带下限 $2.2 → reject
        v, why = judge("Portable Baby Diaper Bag Set Odor Seal Disposable",
                       price_ref="RM0.55 - RM4.55",
                       category="Bekalan Rumah > Bekalan Penjagaan Rumah > Beg Sampah")
        self.assertEqual(v, "reject")
        self.assertIn("带下限", why)

    def test_missing_price_warn_not_block(self):
        v, why = judge("Kitchen Storage Rack", price_ref="",
                       category="Bekalan Rumah > Penyimpanan", metrics="yes")
        self.assertIn("price_band:参考价缺失", why)
        self.assertEqual(v, "direction_only")


class TestDataGap(unittest.TestCase):
    def test_metrics_blank_downgrades(self):
        v, why = judge("Scented Trash Bags Refill", sales="3.2k+",
                       price_ref="RM5.00 - RM9.00",
                       category="Bekalan Rumah > Beg Sampah", metrics="no")
        self.assertEqual(v, "direction_only")
        self.assertIn("DATA_GAP", why)

    def test_low_sales_reject(self):
        v, why = judge("Home Storage Box", sales="300",
                       price_ref="RM8.00 - RM12.00",
                       category="Bekalan Rumah > Penyimpanan")
        self.assertEqual(v, "reject")
        self.assertIn("LOW_SALES", why)


class TestCategory(unittest.TestCase):
    def test_cross_category_reject(self):
        v, why = judge("Phone Case Silicone Cute", sales="2.0k+",
                       price_ref="RM8.00 - RM15.00",
                       category="Telefon > Aksesori Telefon")
        self.assertEqual(v, "reject")
        self.assertIn("CROSS_CATEGORY", why)


class TestParsing(unittest.TestCase):
    def test_sales_formats(self):
        self.assertEqual(oi.parse_sales("3.2k+"), 3200.0)
        self.assertEqual(oi.parse_sales("2.6k+"), 2600.0)
        self.assertEqual(oi.parse_sales("1200"), 1200.0)
        self.assertIsNone(oi.parse_sales("--"))

    def test_price_range_formats(self):
        cur, lo, hi = oi.parse_price_range("RM4.90 - RM8.90")
        self.assertEqual((cur, lo, hi), ("RM", 4.9, 8.9))
        cur, lo, hi = oi.parse_price_range("RM0.55-RM4.55")
        self.assertEqual((cur, lo, hi), ("RM", 0.55, 4.55))

    def test_yaml_config_shapes(self):
        self.assertIsInstance(CFG.get("known_brands"), list)
        self.assertEqual(CFG["price_check"]["currency_prefix"]["RM"], "MY")
        self.assertEqual(CFG["demand_filter"]["opportunity_sales_min"], 1000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
