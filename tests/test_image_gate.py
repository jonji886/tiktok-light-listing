# -*- coding: utf-8 -*-
"""image_gate 测试：三通道扫描 / review 过期 / 覆盖率硬闸 / SKU 图处置。

全部离线：不下载、不写 ERP。涉及 sys.exit 的路径用 assertRaises 断言退出码语义。
"""
import argparse
import json
import os
import shutil
import tempfile
import unittest

import helpers  # noqa: F401  (挂 sys.path)
import image_gate as g

M1 = "https://img.example.com/main1.jpg"
M2 = "https://img.example.com/main2.jpg"
D1 = "https://img.example.com/desc1.jpg"
S1 = "https://img.example.com/sku1.jpg"
S2 = "https://img.example.com/sku2.png"


class TestChannels(unittest.TestCase):
    def test_sku_image_urls(self):
        d = helpers.detail_v2(M1, [D1], [S1, S2])
        v2 = g.get_v2(d, strict=False)[2]
        self.assertEqual(g.sku_image_urls(v2), [S1, S2])
        self.assertEqual(g.main_image_urls(v2), [M1])
        self.assertEqual(g.desc_image_urls(v2["description"]), [D1])

    def test_all_urls_dedup_and_order(self):
        d = helpers.detail_v2([M1, D1], [D1], [S1])
        v2 = g.get_v2(d, strict=False)[2]
        # D1 同时是主图和详情图，只能出现一次
        self.assertEqual(g.all_image_urls(v2), [M1, D1, D1, S1][:1] + [D1, S1])

    def test_sku_image_missing_or_empty(self):
        v2 = {"skus": [{"sales_attributes": [{"name": "x", "value_name": "y"}]},
                       {"sales_attributes": [{"sku_img": {}}]},
                       {"sales_attributes": [{"sku_img": {"uri": "  "}}]},
                       {"sales_attributes": None},
                       "not-a-dict"]}
        self.assertEqual(g.sku_image_urls(v2), [])

    def test_image_ext_from_url(self):
        self.assertEqual(g.image_ext("https://a/b.png"), ".png")
        self.assertEqual(g.image_ext("https://a/b.webp?x=1"), ".webp")
        self.assertEqual(g.image_ext("https://a/b.jpeg"), ".jpg")
        self.assertEqual(g.image_ext("https://a/b"), ".jpg")


class TestCoverage(unittest.TestCase):
    def test_full_coverage(self):
        d = helpers.detail_v2([M1], [D1], [S1])
        cov = g.review_coverage(d, helpers.review([M1, D1, S1]))
        self.assertEqual(cov["missing"], [])
        self.assertEqual(cov["pending"], [])
        self.assertEqual(cov["stale"], [])

    def test_sku_image_not_judged_is_missing(self):
        """三通道都要覆盖：只判定主图 + 详情图时，SKU 图必须报 missing。"""
        d = helpers.detail_v2([M1], [D1], [S1])
        cov = g.review_coverage(d, helpers.review([M1, D1]))
        self.assertEqual(cov["missing"], [S1])

    def test_stale_detected(self):
        d = helpers.detail_v2([M1], [], [])
        cov = g.review_coverage(d, helpers.review(["https://old/1.jpg",
                                                   "https://old/2.jpg",
                                                   "https://old/3.jpg"]))
        self.assertEqual(len(cov["stale"]), 3)
        self.assertEqual(cov["missing"], [M1])

    def test_uri_map_value_counts_as_known(self):
        """apply 之后图已换成新 URL，原判定仍然有效，不能误报 missing。"""
        d = helpers.detail_v2(["https://cdn/new/1.jpg"], [], [])
        rev = helpers.review([M1], uri_map={M1: "https://cdn/new/1.jpg"})
        cov = g.review_coverage(d, rev)
        self.assertEqual(cov["missing"], [])
        self.assertEqual(cov["stale"], [M1])

    def test_pending_detected(self):
        d = helpers.detail_v2([M1], [], [])
        cov = g.review_coverage(d, helpers.review([M1], verdict="pending"))
        self.assertEqual(cov["pending"], [M1])


class TestStaleThreshold(unittest.TestCase):
    def test_small_stale_is_tolerated(self):
        self.assertFalse(g.stale_too_much([], 10))
        self.assertFalse(g.stale_too_much(["a"], 10))      # 1/10 = 10% < 20%
        self.assertFalse(g.stale_too_much(["a", "b"], 50))  # 4% < 20%

    def test_ratio_or_count_triggers(self):
        self.assertTrue(g.stale_too_much(["a", "b", "c"], 100))   # 3 条即触发
        self.assertTrue(g.stale_too_much(["a", "b"], 5))          # 40% > 20%


class TestReviewGate(unittest.TestCase):
    def test_no_review_blocks_by_default(self):
        d = helpers.detail_v2([M1], [], [])
        issues = g.review_gate_issues(d, None, require_review=True)
        self.assertEqual([i[1] for i in issues], ["NO_REVIEW"])
        self.assertEqual(issues[0][0], "FAIL")

    def test_no_review_can_be_explicitly_allowed(self):
        d = helpers.detail_v2([M1], [], [])
        issues = g.review_gate_issues(d, None, require_review=False)
        self.assertEqual(issues[0][0], "WARN")

    def test_missing_and_pending_block(self):
        d = helpers.detail_v2([M1], [D1], [])
        rev = helpers.review([M1], verdict="pending")
        codes = {i[1] for i in g.review_gate_issues(d, rev)}
        self.assertIn("REVIEW_MISSING_URL", codes)
        self.assertIn("REVIEW_PENDING", codes)

    def test_complete_review_passes(self):
        d = helpers.detail_v2([M1], [D1], [S1])
        issues = g.review_gate_issues(d, helpers.review([M1, D1, S1]))
        self.assertEqual(issues, [])


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_detail(self, d, name="detail.json"):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        return p

    def write_review(self, rev, name="review.json"):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(rev, f, ensure_ascii=False)
        return p


class TestApply(_Tmp):
    def apply_args(self, detail, review, extra=()):
        return argparse.Namespace(detail=detail, review=review, out=self.tmp,
                                  desc_text="", assume_pending="abort",
                                  save=False, ttl_id="", config=None)

    def test_apply_drops_and_remaps_sku_images(self):
        d = helpers.detail_v2([M1], [D1], [S1, S2])
        detail = self.write_detail(d)
        rev = helpers.review([M1, S1, S2],
                             verdict="L3_keep",
                             uri_map={M1: "https://cdn/new/main1.jpg",
                                      S1: "https://cdn/new/sku1.jpg"},
                             localized_text="Đoạn mô tả thứ nhất.\n\nĐoạn mô tả thứ hai.")
        # D1 进详情图要判定；S2 判整图删（验证「置空」分支）
        rev["images"].append({"url": D1, "verdict": "L0_drop"})
        rev["images"].append({"url": S2, "verdict": "L0_drop"})
        rev["images"] = [x for x in rev["images"] if not (x["url"] == S2 and x["verdict"] == "L3_keep")]
        review_path = self.write_review(rev)
        rc = g.cmd_apply(self.apply_args(detail, review_path), g.load_config())
        self.assertEqual(rc, 0)
        clean = json.load(open(os.path.join(self.tmp, "cleaned_detail.json"), encoding="utf-8"))
        v2 = clean["reqInfo"]["tikTokItemModel"]["productCreateRequestV2"]
        self.assertEqual([x["uri"] for x in v2["main_images"]], ["https://cdn/new/main1.jpg"])
        self.assertNotIn(D1, v2["description"])
        attrs = [s["sales_attributes"][0] for s in v2["skus"]]
        self.assertEqual(attrs[0]["sku_img"]["uri"], "https://cdn/new/sku1.jpg")  # 换 URL
        # B2（2026-11-25 671250031819 实损）：L0 删图不能置空——平台报 [12052522]
        # 「变体缺主图」整品发布 fail。有干净主图时必须轮换补位。
        self.assertEqual(attrs[1]["sku_img"]["uri"], "https://cdn/new/main1.jpg")  # 主图补位
        report = json.load(open(os.path.join(self.tmp, "report.json"), encoding="utf-8"))
        self.assertEqual(report["sku_images_dropped"], 0)
        self.assertEqual(report["sku_images_remapped"], 1)
        self.assertEqual(report["sku_images_filled_by_main"], 1)
        self.assertEqual(report["after"]["sku_images"], 2)

    def test_apply_blocks_on_stale_review(self):
        d = helpers.detail_v2([M1], [], [S1])
        detail = self.write_detail(d)
        rev = helpers.review(["https://old/1.jpg", "https://old/2.jpg", "https://old/3.jpg"])
        with self.assertRaises(SystemExit) as ctx:
            g.cmd_apply(self.apply_args(detail, self.write_review(rev)), g.load_config())
        self.assertIn("判定表", str(ctx.exception))

    def test_apply_blocks_when_current_url_unjudged(self):
        d = helpers.detail_v2([M1], [], [S1])
        detail = self.write_detail(d)
        rev = helpers.review([M1])          # 漏了 S1
        with self.assertRaises(SystemExit) as ctx:
            g.cmd_apply(self.apply_args(detail, self.write_review(rev)), g.load_config())
        self.assertIn("清单未覆盖", str(ctx.exception))

    def test_apply_rejects_cjk_description(self):
        d = helpers.detail_v2([M1], [], [])
        detail = self.write_detail(d)
        rev = helpers.review([M1], localized_text="这是一段中文描述")
        with self.assertRaises(SystemExit) as ctx:
            g.cmd_apply(self.apply_args(detail, self.write_review(rev)), g.load_config())
        self.assertIn("中文", str(ctx.exception))


class TestInspect(unittest.TestCase):
    def test_sku_images_in_stats(self):
        d = helpers.detail_v2([M1], [D1], [S1, S2])
        _, stats = g.inspect_detail(d, g.load_config())
        self.assertEqual(stats["sku_images"], 2)

    def test_dropped_sku_image_leak_is_fail(self):
        d = helpers.detail_v2([M1], [], [S1])
        rev = helpers.review([M1], verdict="L3_keep")
        rev["images"].append({"url": S1, "verdict": "L0_drop"})
        issues, stats = g.inspect_detail(d, g.load_config(), rev)
        self.assertEqual(stats["dropped_url_leaked"], 1)
        self.assertIn("DROPPED_IMAGE_LEAK", [i[1] for i in issues])


if __name__ == "__main__":
    unittest.main()
