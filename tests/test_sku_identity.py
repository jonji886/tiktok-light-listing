# -*- coding: utf-8 -*-
"""vn20_editor 的 SKU 身份校验：同价 SKU 不能再靠价格断言「验证通过」。"""
import copy
import unittest

import helpers  # noqa: F401  (挂 sys.path)
import vn20_editor as ed


def want(sku_id="", spec=None, cost=6.5):
    w = {"source_sku_id": sku_id, "price_vnd": 100000, "stock": 10, "cost_cny": cost}
    if spec:
        w["source_spec_attrs"] = spec
    return w


def erp_sku(seller_sku="", value_name="", source_price="6.5"):
    return {"seller_sku": seller_sku,
            "price": {"source_price": source_price},
            "sales_attributes": [{"name": "สี", "value_name": value_name}]}


class TestSpecIdentity(unittest.TestCase):
    def test_returns_none_when_erp_already_translated(self):
        """ERP 已把规格翻成泰文 → 无法反查原文，必须返回 None（未知），不能算通过。"""
        self.assertIsNone(ed.spec_identity_matches(want(spec="深灰>30x40"),
                                                  erp_sku(value_name="Xám đậm")))

    def test_compares_when_both_chinese(self):
        self.assertTrue(ed.spec_identity_matches(want(spec="深灰>30x40"), erp_sku(value_name="深灰>30x40")))
        self.assertFalse(ed.spec_identity_matches(want(spec="深灰>30x40"), erp_sku(value_name="藏青>30x40")))

    def test_missing_side_returns_none(self):
        self.assertIsNone(ed.spec_identity_matches(want(), erp_sku(value_name="深灰>30x40")))


class TestPositionalFallback(unittest.TestCase):
    def test_sku_id_match_allows(self):
        self.assertIsNone(ed.positional_fallback_allowed(
            want(sku_id="6151668175276"), erp_sku(seller_sku="6151668175276"), 1, dup_price=True))

    def test_spec_match_allows_without_sku_id(self):
        self.assertIsNone(ed.positional_fallback_allowed(
            want(spec="深灰>30x40"), erp_sku(seller_sku="", value_name="深灰>30x40"), 1, dup_price=True))

    def test_unprovable_identity_with_duplicate_prices_is_refused(self):
        """核心回归：59/70 个 SKU 同价，索引错位时旧价格断言形同虚设。"""
        why = ed.positional_fallback_allowed(
            want(sku_id="6151668175276", spec="粉色>30x40"),
            erp_sku(seller_sku="SHOP-001", value_name="Xám đậm"),
            3, dup_price=True)
        self.assertIsNotNone(why)
        self.assertIn("身份无法证明", why)

    def test_unprovable_identity_with_unique_prices_allowed(self):
        """同品价格互不相同 → 价格断言本身有区分力，允许位置回退。"""
        self.assertIsNone(ed.positional_fallback_allowed(
            want(sku_id="6151668175276"), erp_sku(seller_sku="SHOP-001", value_name="x"),
            3, dup_price=False))


class TestEditorStillLoads(unittest.TestCase):
    def test_payload_has_identity_fields(self):
        import json
        import os
        path = os.path.join(helpers.RUN_VN20, "listing_payloads_vn20_public.json")
        if not os.path.exists(path):
            self.skipTest("无 payload")
        with open(path, encoding="utf-8") as f:
            items = json.load(f)["items"]
        for p in items:
            for s in p["skus"]:
                self.assertTrue(s.get("source_sku_id"), "缺 source_sku_id，skuId 映射不可用")
                self.assertTrue(s.get("source_spec_attrs"), "缺 source_spec_attrs，兜底校验不可用")

    def test_dup_price_is_common_so_guard_matters(self):
        """记录事实：同价 SKU 占多数，这也是守卫存在的理由。"""
        import json
        import os
        path = os.path.join(helpers.RUN_VN20, "listing_payloads_vn20_public.json")
        if not os.path.exists(path):
            self.skipTest("无 payload")
        with open(path, encoding="utf-8") as f:
            items = json.load(f)["items"]
        dup_products = 0
        for p in items:
            costs = [round(float(s["cost_cny"]), 4) for s in p["skus"]]
            if len(set(costs)) != len(costs):
                dup_products += 1
        self.assertGreater(dup_products, 0)


if __name__ == "__main__":
    unittest.main()
