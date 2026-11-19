# -*- coding: utf-8 -*-
"""CLI 层回归：错误子命令必须已移除；规格名不得按序号编造。

这两项是 2026-11-13 审查的「止血」项，一旦被谁"顺手加回来"，测试立刻红。
"""
import argparse
import contextlib
import io
import unittest

import helpers  # noqa: F401  (挂 sys.path)
import apply_listing_fix as fix
import erp_client


def parse_erp(argv):
    """跑 erp_client 的 parser，返回 (SystemExit, 合并后的输出)。"""
    out = io.StringIO()
    raised = None
    with contextlib.redirect_stderr(out), contextlib.redirect_stdout(out):
        try:
            import sys
            old = sys.argv
            sys.argv = ["erp_client.py"] + argv
            try:
                erp_client.main()
            finally:
                sys.argv = old
        except SystemExit as e:
            raised = e
    return raised, out.getvalue()


class TestBrokenSubcommandRemoved(unittest.TestCase):
    def test_ttl_save_is_gone(self):
        raised, err = parse_erp(["ttl-save", "--id", "123"])
        self.assertIsNotNone(raised, "ttl-save 仍可用 —— 它会把商品写成 MISSING_ITEM_DATA")
        self.assertEqual(raised.code, 2)
        self.assertIn("invalid choice", err)

    def test_no_cmd_ttl_save_symbol(self):
        self.assertFalse(hasattr(erp_client, "cmd_ttl_save"),
                         "错误的 ttl-save 实现又被加回来了")

    def test_other_subcommands_intact(self):
        raised, err = parse_erp(["--help"])
        self.assertIn("ttl-detail", err)
        self.assertIn("ttl-publish", err)
        self.assertNotIn("ttl-save", err)


class TestNoFabricatedSpec(unittest.TestCase):
    def test_legacy_colour_template_is_gone(self):
        self.assertFalse(hasattr(fix, "TH_SPEC"),
                         "按 SKU 序号贴色的 TH_SPEC 又被加回来了，这是造假")

    def test_already_localized_kept_as_is(self):
        sku = {"sales_attributes": [{"name": "สี", "value_name": "สี ชมพู"}]}
        self.assertIsNone(fix.localize_sku_attrs(sku))
        self.assertEqual(sku["sales_attributes"][0]["value_name"], "สี ชมพู")

    def test_chinese_value_without_mapping_is_skipped_not_invented(self):
        sku = {"sales_attributes": [{"name": "颜色", "value_name": "深灰大条纹>30*40*60"}]}
        why = fix.localize_sku_attrs(sku)
        self.assertIsNotNone(why)
        self.assertIn("无泰语译名", why)
        # 关键：不能改动原值去"凑"一个合规名字
        self.assertEqual(sku["sales_attributes"][0]["value_name"], "深灰大条纹>30*40*60")

    def test_attr_name_uses_table(self):
        sku = {"sales_attributes": [{"name": "颜色", "value_name": "สี ฟ้า"}]}
        self.assertIsNone(fix.localize_sku_attrs(sku))
        self.assertEqual(sku["sales_attributes"][0]["name"], "สี")

    def test_no_attrs_skipped(self):
        self.assertIn("无规格属性", fix.localize_sku_attrs({"sales_attributes": []}))


if __name__ == "__main__":
    unittest.main()
