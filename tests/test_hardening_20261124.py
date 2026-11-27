#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2026-11-24 MY5 批固化的回归测试：select_gate 三新硬闸、linkfox_oauth 指纹与 ≤¥1 强制补数、
ttl_edit 四新参数 + PAYLOAD_CJK 终检、batch_review 判定回填。"""
import argparse
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))


class TestSelectGateThreeGates(unittest.TestCase):
    """三列硬闸（跨类目 / 属性名汉字 / 指纹缺失）。"""

    def setUp(self):
        import select_gate
        self.sg = select_gate
        self.thr = select_gate.load_thresholds()
        self.price_cfg = None
        try:
            import pricing_calc as pc
            self.price_cfg = pc.load_config()
        except Exception:
            self.price_cfg = {}
        self.home = select_gate.load_home_category_ids()

    def _row(self, **over):
        base = dict(offer_id="1", region="MY", cost_cny="3", max_sku_cost_cny="6",
                    pieces="1", clean_main_images="6", desc_rebuildable="true",
                    clean_main_images_after_fix="6", stock="10000", min_order_qty="1",
                    ship_transit_days="1", seller_48h_proof="true",
                    seller_48h_proof_text="晚发必赔", free_shipping="true",
                    free_shipping_proof="一件包邮", weight_g="170", dims_cm="25x20x4",
                    redline_check="pass", ship_city="金华市", forwarder_city="金华市",
                    category_id="700665", sku_attr_names_ascii="true", seller_sku_ascii="true")
        base.update(over)
        return base

    def _codes(self, row):
        out = self.sg.judge(row, 0, self.thr, self.price_cfg, self.home)
        return ";".join(out[1])

    @unittest.skipIf(not os.path.exists(os.path.join(ROOT, "erp_data", "case_category_tree.json")),
                     "类目树落盘不在本机")
    def test_cross_category_blocked(self):
        # 600204 桌布和桌旗在「家纺布艺」独立根下，不在家居树 → 必拦
        self.assertIn("CATEGORY_NOT_HOME", self._codes(self._row(category_id="600204")))

    def test_new_row_missing_category_blocked(self):
        # 新清单（含指纹列）缺 category_id → 阻断，不再只是 WARN
        self.assertIn("CATEGORY_ID_MISSING", self._codes(self._row(category_id="")))

    def test_fingerprint_missing_blocked(self):
        self.assertIn("CJK_FINGERPRINT_MISSING",
                      self._codes(self._row(sku_attr_names_ascii="")))

    def test_attr_name_cjk_blocked(self):
        self.assertIn("SKU_ATTR_NAME_CJK",
                      self._codes(self._row(sku_attr_names_ascii="false")))

    def test_seller_sku_cjk_blocked(self):
        self.assertIn("SELLER_SKU_CJK",
                      self._codes(self._row(seller_sku_ascii="false")))

    def test_clean_row_not_blocked_by_three_gates(self):
        codes = self._codes(self._row())
        for k in ("CATEGORY_NOT_HOME", "CATEGORY_ID_MISSING", "CJK_FINGERPRINT_MISSING",
                  "SKU_ATTR_NAME_CJK", "SELLER_SKU_CJK"):
            self.assertNotIn(k, codes)

    def test_old_row_back_compat_warn_only(self):
        # 旧清单（无三列）：category 空 → WARN 而非阻断（向后兼容纪律）
        row = self._row()
        row.pop("category_id"); row.pop("sku_attr_names_ascii"); row.pop("seller_sku_ascii")
        out = self.sg.judge(row, 0, self.thr, self.price_cfg, self.home)
        self.assertNotIn("CATEGORY_ID_MISSING", ";".join(out[1]))


class TestLinkfoOauthFingerprint(unittest.TestCase):
    def setUp(self):
        import linkfox_oauth
        self.lo = linkfox_oauth

    def _sku(self, **over):
        base = {"price": "3.7", "amountOnSale": "1000",
                "attributes": [{"attributeName": "颜色", "value": "米色"}]}
        base.update(over)
        return base

    def test_need_sku_cjk_price_gt1_false(self):
        it = {"cost_cny": 3.7, "max_sku_cost_cny": 3.7, "stock": 100, "min_order_qty": 1}
        self.assertFalse(self.lo.need_sku(it))

    def test_need_sku_forces_when_price_le_1(self):
        # 详情页 promotionPrice 误读成 1.0 → 强制走 OAuth（本批 5 品 + 针线盒的教训）
        it = {"cost_cny": 1.0, "max_sku_cost_cny": 1.0, "stock": 100, "min_order_qty": 1}
        self.assertTrue(self.lo.need_sku(it))

    def test_cjk_fingerprints_and_ascii_code_map(self):
        it = {"cost_cny": 8.0, "max_sku_cost_cny": 9.0, "stock": 100}
        skus = [self._sku(cargoNumber="立体罩90*30*50", skuId="5584593636550")]
        changed = self.lo.apply_one(it, skus)
        self.assertTrue(any("sku" in c for c in changed))
        self.assertFalse(it["sku_attr_names_ascii"])     # 颜色=汉字
        self.assertFalse(it["seller_sku_ascii"])          # 货号含汉字
        self.assertTrue(it["sku_values_with_cjk"] >= 1)
        cmap = it["sku_ascii_code_map"]
        self.assertIn("立体罩90*30*50", cmap)
        newcode = cmap["立体罩90*30*50"]
        self.assertRegex(newcode, r"^[0-9A-Za-z_]+$")     # 纯 ASCII

    def test_ascii_cargo_gets_no_code_map(self):
        it = {"cost_cny": 3.7, "max_sku_cost_cny": 3.7, "stock": 100}
        skus = [{"price": "3.7", "amountOnSale": "100",
                 "attributes": [{"attributeName": "Color", "value": "Beige"}],
                 "cargoNumber": "A147-01", "skuId": "1"}]
        self.lo.apply_one(it, skus)
        self.assertTrue(it["sku_attr_names_ascii"])
        self.assertTrue(it["seller_sku_ascii"])
        self.assertNotIn("sku_ascii_code_map", it)


class TestTtlEditNewParams(unittest.TestCase):
    def _detail(self):
        sku = {"seller_sku": "立体罩90*30*50",
               "price": {"source_price": "8.0", "currency": "MYR"},
               "inventory": [{"quantity": 1000}],
               "sales_attributes": [{"name": "颜色", "value_name": "立体防尘罩60*30*90cm"}]}
        sku2 = {"seller_sku": "fc005-2",
                "price": {"source_price": "9.0", "currency": "MYR"},
                "inventory": [{"quantity": 1000}],
                "sales_attributes": [{"name": "颜色", "value_name": "立体防尘罩60*50*90cm"}]}
        return {"id": "1", "toSellerId": "x", "currency": "MYR", "title": "T",
                "toCategoryIdTree": "600001,852232,700665",
                "toCategoryNameTree": "家居用品,家庭护理用品,防尘罩",
                "reqInfo": {"tikTokItemModel": {
                    "productCreateRequestV2": {
                        "skus": [sku, sku2], "category_id": "700665",
                        "main_images": [{"uri": "https://cbu01.alicdn.com/a.jpg"}],
                        "product_attributes": [], "description": "<p>x</p>"}}}}

    def _args(self, **over):
        ns = argparse.Namespace(ttl_id="1", template="", category_id="700665", site="MY",
                                scheme="shelf", pieces=1, source_prices="", keep_prefix="",
                                drop_zero=False, stock="1000", weight_g="300", dims="30x25x5",
                                mains_file="", desc_file="", desc_fields=[], save=False)
        for k, v in over.items():
            setattr(ns, k, v)
        return ns

    def test_all_four_params(self):
        import ttl_edit
        with tempfile.TemporaryDirectory() as d:
            smap = os.path.join(d, "m.json")
            code = os.path.join(d, "c.json")
            json.dump({"立体罩90*30*50": ["Size 60x30x90cm"], "fc005-2": ["Size 60x50x90cm"]},
                      open(smap, "w"))
            json.dump({"立体罩90*30*50": "fc005-1"}, open(code, "w"))
            body, m, probs = ttl_edit.build_body(
                self._detail(),
                self._args(category_id="700665", title="Beg Penutup Debu Gantung Storage Baju",
                           sku_map=smap, sku_code=code,
                           attr_names="颜色=Warna"))
        self.assertEqual([p for p in probs if "出带" not in p], [], probs)
        if m:
            s0 = m["skus"][0]
            self.assertEqual(s0["seller_sku"], "fc005-1")
            self.assertEqual(s0["sales_attributes"][0]["name"], "Warna")
            self.assertEqual(s0["sales_attributes"][0]["value_name"], "Size 60x30x90cm")

    def test_payload_cjk_final_check_blocks(self):
        import ttl_edit
        _, _, probs = ttl_edit.build_body(
            self._detail(), self._args(category_id="700665"))
        self.assertTrue(any("PAYLOAD_CJK" in p for p in probs), probs)

    def test_title_with_cjk_rejected(self):
        import ttl_edit
        _, _, probs = ttl_edit.build_body(
            self._detail(), self._args(category_id="700665", title="中文标题防尘罩"))
        self.assertTrue(any("TITLE_CJK" in p for p in probs), probs)

    def test_drop_sku_removes_named_variant(self):
        # --drop-sku 定点剔除指名变体，保留其余；不需要整品放弃（batch13 教训 #6）。
        import ttl_edit
        _, m, _ = ttl_edit.build_body(
            self._detail(),
            self._args(category_id="700665", drop_sku="fc005-2"))
        self.assertIsNotNone(m)
        kept = [s["seller_sku"] for s in m["skus"]]
        self.assertEqual(kept, ["立体罩90*30*50"])

    def test_drop_sku_keeps_all_when_no_match(self):
        import ttl_edit
        _, m, _ = ttl_edit.build_body(
            self._detail(),
            self._args(category_id="700665", drop_sku="not-exist"))
        self.assertIsNotNone(m)
        self.assertEqual(len(m["skus"]), 2)


class TestBatchReview(unittest.TestCase):
    def test_fill_and_inherit(self):
        import batch_review
        rv = {"images": [
            {"id": "main_01", "url": "u1", "channels": ["main"], "verdict": "pending"},
            {"id": "main_02", "url": "u2", "channels": ["main", "sku"], "verdict": "pending"},
            {"id": "sku_09", "url": "u2", "channels": ["sku"], "verdict": "pending"},
            {"id": "main_03", "url": "u3", "channels": ["main"], "verdict": "pending"}]}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "review.json")
            json.dump(rv, open(p, "w"))
            sys.argv = ["x", "--review", p, "--offer", "1", "--clean", "1,2", "--dirty", "3"]
            self.assertEqual(batch_review.main(), 0)
            out = json.load(open(p))
            v = {i["id"]: i["verdict"] for i in out["images"]}
        self.assertEqual(v["main_01"], "L3_keep")
        self.assertEqual(v["main_02"], "L3_keep")
        self.assertEqual(v["sku_09"], "L3_keep")   # 同 URL 继承
        self.assertEqual(v["main_03"], "L0_drop")


if __name__ == "__main__":
    unittest.main(verbosity=2)
