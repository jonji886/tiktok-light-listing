# -*- coding: utf-8 -*-
"""i18n 译名表测试：迁移等价性 + 未命中不猜。

两个不变量：
  A) CSV 能精确复现已批准的 payload（重建 payload 的输出不变）
  B) 与迁移前的 dict 字面量在「去掉所有空白」后完全一致（只允许修断词，不允许丢内容）
"""
import json
import os
import re
import unittest

import helpers  # noqa: F401  (挂 sys.path)
import i18n

ROOT = helpers.ROOT
RUN = helpers.RUN_VN20
nows = lambda s: re.sub(r"\s+", "", str(s or ""))


def load_payload(name="listing_payloads_vn20_public.json"):
    path = os.path.join(RUN, name)
    if not os.path.exists(path):
        raise unittest.SkipTest("缺少 payload：%s" % path)
    with open(path, encoding="utf-8") as f:
        return json.load(f)["items"]


class TestTableShape(unittest.TestCase):
    def setUp(self):
        i18n.clear_cache()

    def test_site_maps_to_language_file(self):
        self.assertTrue(i18n.table_path("VN").endswith("vi.csv"))
        self.assertTrue(i18n.table_path("TH").endswith("th.csv"))

    def test_both_tables_exist(self):
        for site in ("VN", "TH"):
            self.assertTrue(os.path.exists(i18n.table_path(site)), i18n.table_path(site))

    def test_kinds_are_known(self):
        for site in ("VN", "TH"):
            for row in i18n.rows(site):
                self.assertIn(row["kind"], i18n.KINDS, row)

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            i18n.get("VN", "description", key="1")


class TestLookupDiscipline(unittest.TestCase):
    def setUp(self):
        i18n.clear_cache()

    def test_miss_returns_none_not_guess(self):
        self.assertIsNone(i18n.get("VN", "spec", src="这条规格表里没有"))
        self.assertIsNone(i18n.get("TH", "spec", src="颜色"))  # TH 表只有 attr 类
        self.assertIsNone(i18n.get("VN", "title", key="000000000000"))

    def test_require_raises_with_actionable_message(self):
        with self.assertRaises(KeyError) as ctx:
            i18n.require("VN", "spec", src="没有的词")
        self.assertIn("vi.csv", str(ctx.exception))

    def test_src_normalization_only_collapses_whitespace(self):
        hit = i18n.get("VN", "spec", src="  深灰大条纹>30*40*60  ")
        self.assertIsNotNone(hit)
        self.assertEqual(hit, i18n.get("VN", "spec", src="深灰大条纹>30*40*60"))

    def test_attr_kind_present_for_th(self):
        self.assertEqual(i18n.get("TH", "attr", src="颜色"), "สี")

    def test_missing_bulk(self):
        needs = [("title", "850179548207", None), ("title", "999999999999", None)]
        gone = i18n.missing("VN", needs)
        self.assertEqual([g[1] for g in gone], ["999999999999"])


class TestMarketTermsDiscipline(unittest.TestCase):
    """市场用词参考表（EchoTik 当地语原词）的隔离纪律（2026-11-18）。

    不变量：市场词是 THIRD_PARTY_ONLY 的当地语原词，绝不能混进主译名表当中文 src。
    """
    def setUp(self):
        i18n.clear_cache()

    def test_market_table_is_separate_file(self):
        self.assertTrue(i18n._market_path("VN").endswith("_market_terms_vi.csv"))
        self.assertTrue(i18n._market_path("TH").endswith("_market_terms_th.csv"))
        self.assertNotEqual(i18n._market_path("TH"), i18n.table_path("TH"))

    def test_market_terms_do_not_pollute_main_table(self):
        # 市场表的当地语词不能在主表按 src 命中（主表 key 是中文）
        for r in i18n.market_rows("TH"):
            self.assertIsNone(i18n.get("TH", "attr", src=r["local_term"]),
                              "市场词漏进主表: %s" % r["local_term"])

    def test_market_lookup_hit_and_miss(self):
        hit = i18n.market_lookup("TH", "สี")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["evidence"], "VERIFIED_BY_TABLE")  # 主表反证
        self.assertEqual(hit["cn_hint"], "颜色")
        self.assertIsNone(i18n.market_lookup("TH", "不存在的词"))

    def test_unverified_hint_is_blank_not_guessed(self):
        # 没有证据的条目 cn_hint 必须为空——禁止机翻填中文
        rows = [r for r in i18n.market_rows("VN")
                if r["evidence"] == "INFERRED_UNVERIFIED"]
        self.assertTrue(rows)
        for r in rows:
            self.assertEqual(r["cn_hint"], "", "无证据条目不得填中文: %s" % r)

    def test_verified_only_filters_correctly(self):
        all_rows = i18n.market_rows("TH")
        verified = i18n.market_rows("TH", verified_only=True)
        self.assertLess(len(verified), len(all_rows))
        self.assertTrue(all(r["evidence"] == "VERIFIED_BY_TABLE" for r in verified))


class TestMigrationEquivalence(unittest.TestCase):
    """A) CSV 必须能精确重建已批准的 payload。"""

    def setUp(self):
        i18n.clear_cache()

    def test_reproduces_approved_payload(self):
        bad = []
        for p in load_payload():
            oid = p["offer_id"]
            if i18n.get("VN", "title", key=oid) != p["title_vn"]:
                bad.append(("title", oid))
            if i18n.get("VN", "desc", key=oid) != p["description_vn"]:
                bad.append(("desc", oid))
            for s in p["skus"]:
                src = s["source_spec_attrs"]
                want = s["sales_attributes"][0]["value_name"]
                if i18n.get("VN", "spec", src=src) != want:
                    bad.append(("spec", oid, src))
        self.assertEqual(bad, [], "CSV 与已批准 payload 不一致，重建会改变已发布文案")

    def test_payload_has_source_spec_attrs(self):
        for p in load_payload():
            for s in p["skus"]:
                self.assertTrue(s.get("source_spec_attrs"),
                                "%s 的 SKU 缺 source_spec_attrs，无法做身份校验" % p["offer_id"])


class TestNoContentLost(unittest.TestCase):
    """B) 与旧 dict 字面量相比，只允许「断词修正」这类空白级差异。"""

    def setUp(self):
        i18n.clear_cache()
        with open(os.path.join(helpers.FIXTURES, "vi_legacy.json"), encoding="utf-8") as f:
            self.legacy = json.load(f)

    def test_titles_and_descriptions(self):
        diff = []
        for oid, v in self.legacy["meta"].items():
            if nows(i18n.get("VN", "title", key=oid)) != nows(v["title_vn"]):
                diff.append(("title", oid))
            if nows(i18n.get("VN", "desc", key=oid)) != nows(v["description_vn"]):
                diff.append(("desc", oid))
        self.assertEqual(diff, [])

    def test_all_spec_sources_still_translate(self):
        missing = []
        for oid, mp in self.legacy["specs"].items():
            for src, tgt in mp.items():
                got = i18n.get("VN", "spec", src=src)
                if got is None:
                    missing.append((oid, src))
                elif nows(got) != nows(tgt):
                    missing.append((oid, src, "译文变了"))
        self.assertEqual(missing, [])

    def test_known_typo_fixes_are_the_only_deviations(self):
        """记录的偏差：旧字面量里 13 处越南语断词（thờ i / ngườ i），payload 已修正。"""
        fixed = 0
        for oid, v in self.legacy["meta"].items():
            if i18n.get("VN", "desc", key=oid) != v["description_vn"]:
                fixed += 1
        self.assertEqual(fixed, 13, "描述改动数量与已知断词修正数不符，需复核")


if __name__ == "__main__":
    unittest.main()
