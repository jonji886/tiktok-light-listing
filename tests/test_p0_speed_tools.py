# -*- coding: utf-8 -*-
"""P0 提效工具回归测试（2026-11-20，针线盒 53 分复盘的固化）。

覆盖：
- category_template：match 词根命中 / render 占位未填拒绝 / 描述质量闸（≥500/≥2段）/
  category_chain 祖先链反查 + AVAILABLE 校验（真实 case_category_tree.json）
- hero_main_images：针线盒**反事实验证**——方款 PASS(6)、椭圆/圆款 FAIL(0)，
  即"若选品期跑过本工具就不会中途换款 8 分钟"
- linkfox_oauth 价格裁决：详情页错值(1.0) vs OAuth(5.9) 差>30% → 覆盖+留痕；
  差≤30% 不动；缺失仍走"缺才填"
- ttl_edit build_body：真实 detail JSON + mock _post，验证类目/SKU精简/定价/域名拒绝
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import category_template as ct  # noqa: E402
import hero_main_images as hmi  # noqa: E402
import linkfox_oauth as lo  # noqa: E402


class TestCategoryChain(unittest.TestCase):
    def test_storage_box_chain(self):
        ids, names, perm = ct.category_chain("600621")
        self.assertEqual(ids, "600001,851848,600621")
        self.assertIn("收纳盒", names)
        self.assertIn("AVAILABLE", perm)

    def test_apron_chain(self):
        ids, names, perm = ct.category_chain("600427")
        self.assertEqual(ids, "600001,852232,600427")
        self.assertIn("AVAILABLE", perm)

    def test_nonexistent(self):
        with self.assertRaises(RuntimeError):
            ct.category_chain("999999999")


class TestMatchRender(unittest.TestCase):
    def test_match_sewing_box_hits_storage(self):
        tpls = ct.load_templates()
        self.assertIn("收纳盒", tpls)
        self.assertEqual(tpls["收纳盒"]["erp_category_id"], "600621")

    def test_render_rejects_unfilled(self):
        tpls = ct.load_templates()
        s, left = ct.render_desc(tpls["收纳盒"], {"product_name": "Kotak"})
        self.assertIn("feature1", left)  # 未填占位必须暴露，禁止带 {x} 上线

    def test_render_full_passes_quality_gate(self):
        tpls = ct.load_templates()
        fields = {"product_name": "Kotak Jahit Magnetik", "feature1": "Susunan magnetik",
                  "feature2": "16 gelung benang", "dims": "12 x 12 x 6 cm",
                  "color_list": "Kuning, Biru, Pink", "scene": "jahitan"}
        s, left = ct.render_desc(tpls["收纳盒"], fields)
        self.assertEqual(left, [])
        self.assertGreaterEqual(ct.plain_len(s), 500)
        self.assertGreaterEqual(len([p for p in s.split("\n\n") if p.strip()]), 2)


class TestHeroModelSupply(unittest.TestCase):
    """反事实验证：针线盒真实数据，方款应 PASS、椭圆/圆款应 FAIL（= 今天的正确决策）。"""

    def setUp(self):
        self.sku = os.path.join(ROOT, "erp_data/linkfox/mcp/sku_972899576020.json")
        self.review = os.path.join(ROOT, "erp_data/image_gate_针线盒/review.json")
        if not (os.path.exists(self.sku) and os.path.exists(self.review)):
            self.skipTest("针线盒真实数据不在本机")

    def _run(self, argv):
        old = sys.argv
        sys.argv = [sys.argv[0]] + argv
        try:
            return hmi.main()
        finally:
            sys.argv = old

    def test_square_pass_others_fail(self):
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = self._run(["--sku-json", self.sku, "--review", self.review,
                            "--model-regex", "方款|圆款|磁吸针线盒"])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("方款」SKU3 可用主图供给=6", out)
        self.assertIn("FAIL", out)
        self.assertIn("建议款型=「方款」", out)


class TestPriceAdjudication(unittest.TestCase):
    SKUS = [{"price": "5.9", "amountOnSale": "100", "attributes": [], "skuImageUrl": ""},
            {"price": "7.9", "amountOnSale": "200", "attributes": [], "skuImageUrl": ""}]

    def test_detail_misread_overridden(self):
        it = {"cost_cny": 1.0, "max_sku_cost_cny": 1.0}   # 详情页 promotionPrice 误读
        lo.apply_one(it, self.SKUS)
        self.assertEqual(it["cost_cny"], 5.9)
        self.assertEqual(it["max_sku_cost_cny"], 7.9)
        self.assertIn("差>30%", it["price_adjudication"])

    def test_close_values_untouched(self):
        it = {"cost_cny": 5.7, "max_sku_cost_cny": 7.7}   # 差 <30%：促销微降属正常
        lo.apply_one(it, self.SKUS)
        self.assertEqual(it["cost_cny"], 5.7)
        self.assertNotIn("price_adjudication", it)

    def test_missing_still_fills(self):
        it = {"cost_cny": None, "max_sku_cost_cny": None}
        lo.apply_one(it, self.SKUS)
        self.assertEqual(it["cost_cny"], 5.9)
        self.assertNotIn("price_adjudication", it)


class TestTtlEditBuildBody(unittest.TestCase):
    """真实 detail（方款定稿态）+ mock _post：一次 edit 到位的组装纪律。"""

    def setUp(self):
        import ttl_edit
        self.te = ttl_edit
        f = os.path.join(ROOT, "erp_data/ttl_detail_针线盒4/ttl_detail_1573354985664102400.json")
        if not os.path.exists(f):
            self.skipTest("针线盒 detail 落盘不在本机")
        self.detail = json.load(open(f, encoding="utf-8"))

    def _args(self, **over):
        import argparse
        ns = argparse.Namespace(ttl_id="1573354985664102400", template="收纳盒",
                                category_id="", site="MY", scheme="shelf", pieces=1,
                                source_prices="", keep_prefix="", drop_zero=False,
                                stock="1000", weight_g="", dims="", mains_file="",
                                desc_file="", desc_fields=[], save=False)
        for k, v in over.items():
            setattr(ns, k, v)
        return ns

    def test_template_builds_full_body(self):
        body, m, probs = self.te.build_body(self.detail, self._args(
            keep_prefix="00011546",
            desc_fields=["product_name=Kotak Jahit", "feature1=Susunan magnetik",
                         "feature2=16 benang", "dims=12 x 12 x 6 cm",
                         "color_list=Kuning, Biru, Pink", "scene=jahitan"]))
        self.assertEqual(probs, [])
        self.assertEqual(body["toCategoryId"], "600621")
        self.assertEqual(len(m["skus"]), 3)
        self.assertEqual(m["package_weight"]["value"], "0.3")
        self.assertIn("<p>", m["description"])

    def test_bad_main_host_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("https://kdzs-jxc-vpc.oss-cn-zhangjiakou.aliyuncs.com/x.jpg\n")
            path = f.name
        _, _, probs = self.te.build_body(self.detail, self._args(mains_file=path))
        os.unlink(path)
        self.assertTrue(any("域名违规" in p for p in probs))

    def test_unfilled_placeholder_rejected(self):
        _, _, probs = self.te.build_body(self.detail, self._args(
            desc_fields=["product_name=Kotak"]))
        self.assertTrue(any("占位未填" in p for p in probs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
