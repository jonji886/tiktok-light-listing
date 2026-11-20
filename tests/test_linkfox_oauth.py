# -*- coding: utf-8 -*-
"""linkfox_oauth 离线回归测试（2026-11-20）：scan 任务清单 / apply 回填纪律。

不发网络：sku 落盘 JSON 用临时 fixture mock MCP 产物（数据形态取自当日真实回包）。
锁的行为：缺才填、不覆盖详情页已有值、offerId 错配拒绝、MOQ 禁止猜、dims 正则解析、
规格图不与主图组混用。
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import linkfox_oauth as lo  # noqa: E402

SKU_OK = {"errcode": 200, "errmsg": "ok", "offerId": "111", "skuList": [
    {"specId": "a", "skuId": "s1", "price": "7.9", "amountOnSale": "4483",
     "skuImageUrl": "https://cbu01.alicdn.com/a.jpg", "cargoNumber": "0001-1",
     "attributes": [{"attributeId": "3216", "attributeName": "颜色",
                     "value": "奶油黄【方款】内含配件", "valueTrans": "Cream yellow"}]},
    {"specId": "b", "skuId": "s2", "price": "5.9", "amountOnSale": "9920",
     "skuImageUrl": "https://cbu01.alicdn.com/b.jpg", "cargoNumber": "0001-2",
     "attributes": [{"attributeId": "3216", "attributeName": "颜色",
                     "value": "浅蓝色【圆款】内含配件"}]},
]}
SKU_DIMS = {"errcode": 200, "offerId": "222", "skuList": [
    {"specId": "c", "price": "2.98", "amountOnSale": "95575",
     "skuImageUrl": "https://cbu01.alicdn.com/c.jpg",
     "attributes": [{"attributeName": "颜色", "value": "天空蓝【破损包赔】"},
                    {"attributeName": "尺寸", "value": "10#【长25宽18高14cm】无轮"}]},
    {"specId": "d", "price": "4.8", "amountOnSale": "99198",
     "attributes": [{"attributeName": "尺寸", "value": "30#【长30宽21高17cm】无轮"}]},
]}


def write_payload(d, oid, content):
    p = os.path.join(d, "mcp", "sku_%s.json" % oid)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(content, f)
    return p


class TestApplyOne(unittest.TestCase):
    def test_fill_missing_only(self):
        it = {"offer_id": "111", "cost_cny": None, "max_sku_cost_cny": None,
              "stock": None}
        changed = lo.apply_one(it, SKU_OK["skuList"])
        self.assertEqual(it["cost_cny"], 5.9)          # min(price)
        self.assertEqual(it["max_sku_cost_cny"], 7.9)  # max(price)
        self.assertEqual(it["stock"], 14403)           # sum(amountOnSale)
        self.assertNotIn("min_order_qty", it)          # MOQ 不猜（sku 回包无此字段）
        self.assertEqual(it["max_sku_source"], "oauth_sku")
        self.assertEqual(len(it["sku_images"]), 2)

    def test_never_overwrite_detail_values(self):
        it = {"offer_id": "111", "cost_cny": 6.8, "max_sku_cost_cny": 6.8, "stock": 200}
        lo.apply_one(it, SKU_OK["skuList"])
        self.assertEqual((it["cost_cny"], it["max_sku_cost_cny"], it["stock"]),
                         (6.8, 6.8, 200))

    def test_dims_parsed_from_spec_value(self):
        it = {"offer_id": "222"}
        lo.apply_one(it, SKU_DIMS["skuList"])
        self.assertEqual(it["dims_parsed"], "25×18×14")
        self.assertIn("尺寸", it["sku_spec_names"])

    def test_no_dims_no_guess(self):
        it = {"offer_id": "111"}
        lo.apply_one(it, SKU_OK["skuList"])
        self.assertNotIn("dims_parsed", it)


class TestScanApplyCLI(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.batch = os.path.join(self.d, "prefill.json")
        payload = {"items": [
            {"offer_id": "111", "cost_cny": None, "max_sku_cost_cny": None,
             "stock": None, "min_order_qty": None},
            {"offer_id": "222", "cost_cny": None, "max_sku_cost_cny": None,
             "stock": None, "min_order_qty": 1},
            {"offer_id": "333", "cost_cny": None, "max_sku_cost_cny": None,
             "stock": None, "min_order_qty": None},  # 不落盘 → miss
        ]}
        with open(self.batch, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        write_payload(self.d, "111", SKU_OK)
        write_payload(self.d, "222", SKU_DIMS)
        self._orig = (lo.MCP_DIR, lo.TASKS_FILE)
        lo.MCP_DIR = os.path.join(self.d, "mcp")
        lo.TASKS_FILE = os.path.join(self.d, "oauth_tasks.json")

    def tearDown(self):
        lo.MCP_DIR, lo.TASKS_FILE = self._orig

    def _argv(self, *a):
        old = sys.argv
        sys.argv = [sys.argv[0]] + list(a)
        try:
            return lo.main()
        finally:
            sys.argv = old

    def test_scan_lists_all_missing(self):
        rc = self._argv("scan", "--batch", self.batch)
        self.assertEqual(rc, 0)
        tasks = json.load(open(lo.TASKS_FILE))
        self.assertEqual({t["offerId"] for t in tasks}, {"111", "222", "333"})
        self.assertEqual(tasks[0]["toolName"], "alibaba1688/sku")

    def test_apply_backfills_and_reports_miss(self):
        rc = self._argv("apply", "--batch", self.batch)
        self.assertEqual(rc, 1)  # 333 未落盘 → 1，不崩不猜
        payload = json.load(open(self.batch))
        by = {i["offer_id"]: i for i in payload["items"]}
        self.assertEqual(by["111"]["cost_cny"], 5.9)
        self.assertEqual(by["222"]["dims_parsed"], "25×18×14")
        self.assertIn("oauth_sku", payload["source"])
        self.assertTrue(os.path.exists(self.batch.replace(".json", ".oauth_backup.json")))

    def test_offerid_mismatch_rejected(self):
        write_payload(self.d, "111", {"errcode": 200, "offerId": "999",
                                      "skuList": [{"price": "1"}]})
        with self.assertRaises(ValueError) as cm:
            lo.load_sku("111")
        self.assertIn("不符", str(cm.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
