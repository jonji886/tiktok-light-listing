#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""罗盘导出 → backflow 批量回填的离线回归（P0-1）。

不依赖 openpyxl（解析层用二维行数组测）、不联网、不读真实 erp_data。
覆盖：数值清洗、双层表头解析、时点字段映射、缺失值不写、原值保护、
站点过滤、ID 闭环映射、渠道汇总表报错。
"""
import csv
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import backflow_import as bi  # noqa: E402


def compass_rows():
    """与真实罗盘商品明细同构：标题/空行/渠道分组/表头/数据（「全部」段在前）。"""
    header = ["商品名", "商品 ID", "GMV", "订单数", "商品成交件数",
              "商品曝光次数", "商品点击量", "商品点击率", "加购次数", "加购率",
              "CTOR（SKU 订单）"]
    group = ["", "", "全部", "全部", "全部", "全部", "全部", "全部", "全部", "全部", "全部"]
    data = [
        ["Product A", "1738635000000000001", "RM0.00", "0", "0", "1", "0",
         "0.00%", "0", "0.00%", "0.00%"],
        ["Product B", "1738670000000000002", "RM12.50", "2", "3", "40", "10",
         "25.00%", "2", "20.00%", "20.00%"],
        ["Product C", "1738670000000000000", "RM0.00", "0", "0", "-", "-",
         "-", "-", "-", "-"],
    ]
    return [["数据分析日期: 19/11/2026~25/11/2026"], [], group, header] + data


class TestParseNumber(unittest.TestCase):
    def test_real_zero_is_zero_not_missing(self):
        self.assertEqual(bi.parse_number("RM0.00"), 0.0)
        self.assertEqual(bi.parse_number("0"), 0.0)

    def test_dash_and_empty_are_missing(self):
        self.assertIsNone(bi.parse_number("-"))
        self.assertIsNone(bi.parse_number("--"))
        self.assertIsNone(bi.parse_number(""))
        self.assertIsNone(bi.parse_number(None))

    def test_strip_currency_and_thousands(self):
        self.assertEqual(bi.parse_number("RM1,234.50"), 1234.50)
        self.assertEqual(bi.parse_number("50%"), 50.0)


class TestParseCompass(unittest.TestCase):
    def test_records_extracted_from_all_channel_block(self):
        records = bi._rows_to_records(compass_rows())
        self.assertEqual(len(records), 3)
        first = records[0]
        self.assertEqual(first["num_iid"], "1738635000000000001")
        self.assertEqual(first["exposure"], 1.0)
        self.assertEqual(first["clicks"], 0.0)
        self.assertEqual(first["add_cart"], 0.0)
        self.assertEqual(first["orders"], 0.0)
        self.assertEqual(first["gmv"], 0.0)

        second = records[1]
        self.assertEqual(second["exposure"], 40.0)
        self.assertEqual(second["clicks"], 10.0)
        self.assertEqual(second["add_cart"], 2.0)
        self.assertEqual(second["orders"], 2.0)
        self.assertEqual(second["gmv"], 12.50)

    def test_missing_dash_stays_none(self):
        rec = next(r for r in bi._rows_to_records(compass_rows())
                   if r["num_iid"] == "1738670000000000000")
        self.assertIsNone(rec["exposure"])
        self.assertIsNone(rec["clicks"])
        self.assertIsNone(rec["add_cart"])

    def test_summary_table_without_product_id_raises(self):
        rows = [["渠道"], ["联盟", "RM0.00", "0"], ["商家商品卡", "RM0.00", "6"]]
        with self.assertRaises(ValueError):
            bi._rows_to_records(rows)


class TestIdMap(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp), exist_ok=True)
        with open(os.path.join(self.tmp, "tiktok_list.csv"), "w", encoding="utf-8-sig",
                  newline="") as f:
            w = csv.writer(f)
            w.writerow(["ttl_id", "货源ID", "标题", "站点"])
            w.writerow(["111", "offer-A", "围裙", "MY"])
            w.writerow(["222", "offer-B", "海绵", "MY"])
        snap = {"data": {"list": [
            {"numIid": "1738635000000000001", "outerId": "offer-A",
             "itemAlias": "x", "platform": "tiktok"},
            {"numIid": "1738670000000000002", "outerId": "offer-B",
             "itemAlias": "y", "platform": "tiktok"},
            {"numIid": "1738670000000000000", "outerId": "offer-Ghost",
             "itemAlias": "z", "platform": "tiktok"},
            {"numIid": "58158770", "outerId": "offer-Shopee",
             "itemAlias": "shopee 草稿", "platform": "shopees"},
        ]}}
        with open(os.path.join(self.tmp, "site_list_发布后.json"), "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False)

    def test_closed_loop_mapping(self):
        num_to_ttl, num_to_offer, offer_to_ttl = bi.build_id_map(self.tmp)
        self.assertEqual(num_to_ttl["1738635000000000001"], "111")
        self.assertEqual(num_to_ttl["1738670000000000002"], "222")
        self.assertNotIn("1738670000000000000", num_to_ttl)  # offer-Ghost 本地无货源
        self.assertNotIn("58158770", num_to_offer)  # shopee 草稿不进映射
        self.assertEqual(offer_to_ttl["offer-A"], "111")


class TestApplyImport(unittest.TestCase):
    def _rows(self):
        return [
            {"ttl_id": "111", "region": "MY", "title": "围裙",
             "t7_exposure": "", "t7_clicks": "", "t7_add_cart": "",
             "t20_orders": "", "t20_gmv": "", "note": ""},
            {"ttl_id": "222", "region": "MY", "title": "海绵",
             "t7_exposure": "500", "t7_clicks": "", "t7_add_cart": "",
             "t20_orders": "", "t20_gmv": "", "note": ""},
            {"ttl_id": "333", "region": "TH", "title": "泰品",
             "t7_exposure": "", "t7_clicks": "", "t7_add_cart": "",
             "t20_orders": "", "t20_gmv": "", "note": ""},
        ]

    def _records(self):
        return [
            {"num_iid": "777", "title": "围裙", "exposure": 40.0, "clicks": 10.0,
             "add_cart": 2.0, "orders": 2.0, "gmv": 12.5},
            {"num_iid": "888", "title": "海绵", "exposure": 40.0, "clicks": 10.0,
             "add_cart": 2.0, "orders": 0.0, "gmv": 0.0},
            {"num_iid": "000", "title": "找不到", "exposure": 5.0, "clicks": 0.0,
             "add_cart": 0.0, "orders": 0.0, "gmv": 0.0},
        ]

    def test_t7_writes_exposure_clicks_addcart(self):
        rows = self._rows()
        result = bi.apply_import(rows, self._records(), {"777": "111", "888": "222"},
                                 "t7", region="MY")
        # 111 全字段写入；222 已有曝光值被保护，仅补齐点击/加购
        self.assertEqual(len(result["updated"]), 2)
        r111 = next(r for r in rows if r["ttl_id"] == "111")
        self.assertEqual(r111["t7_exposure"], "40")
        self.assertEqual(r111["t7_clicks"], "10")
        self.assertEqual(r111["t7_add_cart"], "2")
        self.assertIn("import:compass-t7", r111["note"])
        r222 = next(r for r in rows if r["ttl_id"] == "222")
        self.assertEqual(r222["t7_exposure"], "500")  # 原值保留
        self.assertEqual(r222["t7_clicks"], "10")
        self.assertEqual(r222["t7_add_cart"], "2")
        self.assertEqual(len(result["unmatched"]), 1)

    def test_overwrite_replaces_existing_value(self):
        rows = self._rows()
        bi.apply_import(rows, self._records(), {"888": "222"}, "t7",
                        overwrite=True, region="MY")
        r222 = next(r for r in rows if r["ttl_id"] == "222")
        self.assertEqual(r222["t7_exposure"], "40")

    def test_region_filter_excludes_other_shop(self):
        records = [{"num_iid": "777", "title": "x", "exposure": 1.0, "clicks": 0.0,
                    "add_cart": 0.0, "orders": 0.0, "gmv": 0.0}]
        rows = [{"ttl_id": "333", "region": "TH", "title": "泰品",
                 "t7_exposure": "", "t7_clicks": "", "t7_add_cart": ""}]
        result = bi.apply_import(rows, records, {"777": "333"}, "t7", region="MY")
        self.assertEqual(result["updated"], [])
        self.assertEqual(len(result["unmatched"]), 1)

    def test_t20_orders_and_gmv_format(self):
        rows = [{"ttl_id": "111", "region": "MY", "title": "围裙",
                 "t20_orders": "", "t20_gmv": "", "note": ""}]
        records = [{"num_iid": "777", "title": "围裙", "exposure": 40.0,
                    "clicks": 10.0, "add_cart": 2.0, "orders": 3.0, "gmv": 12.5}]
        bi.apply_import(rows, records, {"777": "111"}, "t20")
        r111 = rows[0]
        self.assertEqual(r111["t20_orders"], "3")
        self.assertEqual(r111["t20_gmv"], "12.50")

    def test_missing_value_is_not_written_as_zero(self):
        rows = [{"ttl_id": "111", "region": "MY", "title": "围裙",
                 "t7_exposure": "", "t7_clicks": "", "t7_add_cart": ""}]
        records = [{"num_iid": "777", "title": "围裙", "exposure": None,
                    "clicks": None, "add_cart": None, "orders": None, "gmv": None}]
        result = bi.apply_import(rows, records, {"777": "111"}, "t7")
        self.assertEqual(result["updated"], [])
        r111 = rows[0]
        self.assertEqual(r111["t7_exposure"], "")
        self.assertEqual(r111["t7_clicks"], "")


if __name__ == "__main__":
    unittest.main()
