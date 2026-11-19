# -*- coding: utf-8 -*-
"""build_listing_payloads 的迁移回归：读 CSV 的构建必须复现已批准 payload 的文案与规格。

做法：把 run 目录复制到临时目录再跑，绝不覆盖已批准的 listing_payloads_vn20*.json。
图片集来自 final20_images/manifest.json（外部输入，会随人工增减而变），故只比对
文案 / 类目 / SKU 结构 / 价格 / 库存，不比 images。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import shutil  # noqa: F401  (tearDown 清临时目录)

import helpers  # noqa: F401  (挂 sys.path)

RUN = helpers.RUN_VN20
TEXT_FIELDS = ("title_vn", "description_vn", "description_html", "category_id",
               "source_sku_total", "order_usd")
SKU_FIELDS = ("source_sku_id", "source_sku_index", "sales_attributes",
              "price_vnd", "stock", "cost_cny")


def load(path):
    with open(path, encoding="utf-8") as f:
        return {x["offer_id"]: x for x in json.load(f)["items"]}


class TestRebuildReproducesApprovedText(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        need = [os.path.join(RUN, n) for n in ("enriched20.json", "final20_images/manifest.json",
                                               "listing_payloads_vn20.json")]
        need.append(os.path.join(RUN, "html"))
        missing = [p for p in need if not os.path.exists(p)]
        if missing:
            raise unittest.SkipTest("缺少重建输入：%s" % missing)

        cls.approved = load(os.path.join(RUN, "listing_payloads_vn20.json"))
        cls.tmp = tempfile.mkdtemp()
        # VN20_OUT_DIR 把输出重定向到临时目录 → 不会覆盖已批准的 payload
        env = dict(os.environ, VN20_OUT_DIR=cls.tmp)
        cls.proc = subprocess.run(
            [sys.executable, os.path.join(RUN, "build_listing_payloads.py")],
            capture_output=True, text=True, timeout=300, env=env)
        out = os.path.join(cls.tmp, "listing_payloads_vn20.json")
        cls.rebuilt = load(out) if (cls.proc.returncode == 0 and os.path.exists(out)) else {}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_build_succeeds(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr[-1500:])

    def test_same_offers(self):
        self.assertEqual(set(self.approved), set(self.rebuilt))

    def test_text_and_pricing_identical(self):
        diff = []
        for oid, old in self.approved.items():
            new = self.rebuilt[oid]
            for k in TEXT_FIELDS:
                if old.get(k) != new.get(k):
                    diff.append((oid, k, old.get(k), new.get(k)))
            if len(old["skus"]) != len(new["skus"]):
                diff.append((oid, "sku 数量", len(old["skus"]), len(new["skus"])))
                continue
            for a, b in zip(old["skus"], new["skus"]):
                for k in SKU_FIELDS:
                    if a.get(k) != b.get(k):
                        diff.append((oid, k, a.get(k), b.get(k)))
        self.assertEqual(diff, [], "读 CSV 重建后文案/规格与批准版不一致")

    def test_internal_field_present_but_not_in_visible_text(self):
        for oid, p in self.rebuilt.items():
            for s in p["skus"]:
                self.assertTrue(s.get("source_spec_attrs"), oid)
            visible = json.dumps([{k: v for k, v in s.items() if k != "source_spec_attrs"}
                                  for s in p["skus"]], ensure_ascii=False)
            self.assertNotIn(">", visible, "中文源规格漏进买家可见字段")


if __name__ == "__main__":
    unittest.main()
