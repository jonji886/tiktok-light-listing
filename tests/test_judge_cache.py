# -*- coding: utf-8 -*-
"""判定缓存 + 复用规则回归测试（2026-11-26 A/B 提效）。

钉死两条安全线：
1. can_reuse：main+L3_keep 必须重跑 Aidge probe（678 非文字水印风险）；
   L0_drop 与 sku/desc 的 L3_keep 才可复用。
2. judge_cache：按 content_sha256 索引，Aidge 证据不被本地 OCR 覆盖，
   merge 只回填 pending，不覆盖本轮新判。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import image_review as IR  # noqa: E402
import judge_cache as JC  # noqa: E402


def _im(iid, verdict, ch, ev=None, sha="a" * 32, url="http://x/1.jpg"):
    d = {"id": iid, "verdict": verdict, "channels": ch,
         "content_sha256": sha, "url": url}
    if ev is not None:
        d["evidence"] = ev
    return d


class TestCanReuse(unittest.TestCase):
    EV = {"rec_text": [], "evidence_source": "local_vision_ocr"}

    def test_main_l3_must_probe(self):
        """678 事故点：main 通道 L3_keep 即使有本地证据也必须重跑 Aidge（非文字水印）"""
        self.assertFalse(IR.can_reuse(_im("main_01", "L3_keep", ["main"], self.EV)))

    def test_sku_l3_reuses(self):
        self.assertTrue(IR.can_reuse(_im("sku_03", "L3_keep", ["sku"], self.EV)))

    def test_desc_l3_reuses(self):
        self.assertTrue(IR.can_reuse(_im("desc_05", "L3_keep", ["desc"], self.EV)))

    def test_l0_reuses_even_main(self):
        """L0_drop 要删的图不需要 probe 证据，main 通道也可复用"""
        self.assertTrue(IR.can_reuse(_im("main_02", "L0_drop", ["main"], self.EV)))

    def test_no_evidence_no_reuse(self):
        self.assertFalse(IR.can_reuse(_im("sku_01", "L3_keep", ["sku"])))

    def test_pending_no_reuse(self):
        self.assertFalse(IR.can_reuse(_im("sku_01", "pending", ["sku"], self.EV)))

    def test_l1_no_reuse(self):
        """L1_inpaint 需擦字，仍走 probe 确认文字区域"""
        self.assertFalse(IR.can_reuse(_im("sku_01", "L1_inpaint", ["sku"], self.EV)))


class TestJudgeCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old = JC.CACHE
        JC.CACHE = Path(self.tmp) / "cache.json"

    def tearDown(self):
        JC.CACHE = self.old

    def _review(self, imgs):
        p = os.path.join(self.tmp, "review.json")
        json.dump({"images": imgs}, open(p, "w", encoding="utf-8"))
        return p

    def test_key_prefers_sha(self):
        im = _im("main_01", "L3_keep", ["main"], sha="b" * 32, url="http://x/a.jpg")
        self.assertEqual(JC.key_of(im), "sha:" + "b" * 32)

    def test_key_falls_back_to_url(self):
        im = _im("main_01", "L3_keep", ["main"], sha="", url="http://x/a.jpg")
        self.assertTrue(JC.key_of(im).startswith("url:"))

    def test_save_and_merge(self):
        im = _im("main_01", "L3_keep", ["main"], {"rec_text": []}, sha="c" * 32)
        rp = self._review([im])
        self.assertEqual(JC.save_from_review(rp), 1)
        # 模拟 Phase3 scan 重建：新 review 只有 pending
        fresh = _im("main_03", "pending", ["main"], sha="c" * 32)
        rp2 = self._review([fresh])
        m = JC.merge_into_review(rp2)
        self.assertEqual(m["merged"], 1)
        got = json.load(open(rp2))["images"][0]
        self.assertEqual(got["verdict"], "L3_keep")
        self.assertIn("rec_text", got["evidence"])

    def test_merge_does_not_overwrite_new_verdict(self):
        im = _im("main_01", "L3_keep", ["main"], {"rec_text": []}, sha="d" * 32)
        rp = self._review([im])
        JC.save_from_review(rp)
        fresh = _im("main_03", "L0_drop", ["main"], sha="d" * 32)  # 本轮新判
        rp2 = self._review([fresh])
        m = JC.merge_into_review(rp2)
        self.assertEqual(m["merged"], 0)
        self.assertEqual(json.load(open(rp2))["images"][0]["verdict"], "L0_drop")

    def test_aidge_evidence_not_downgraded(self):
        sha = "e" * 32
        aid = _im("main_01", "L3_keep", ["main"],
                  {"rec_text": [], "evidence_source": "aidge", "noobj_wm": True}, sha=sha)
        rp = self._review([aid])
        JC.save_from_review(rp)
        local = _im("main_02", "L3_keep", ["main"],
                    {"rec_text": [], "evidence_source": "local_vision_ocr"}, sha=sha)
        rp2 = self._review([local])
        JC.save_from_review(rp2)
        c = JC._load()
        self.assertEqual(c["sha:" + sha]["evidence"]["evidence_source"], "aidge")

    def test_ocr_cache_shared_between_stages(self):
        """B：prefilter 存的 OCR 结果，C 段（auto_verdict）按 sha 命中免跑 swift"""
        self.old_ocr = JC.OCR_CACHE
        JC.OCR_CACHE = Path(self.tmp) / "ocr.json"
        try:
            p = os.path.join(self.tmp, "first.jpg")
            Path(p).write_bytes(b"fake-image-bytes-12345")
            od = {"ok": True, "width": 800, "height": 800,
                  "items": [{"text": "abc", "conf": 0.8}], "file": p}
            self.assertTrue(JC.save_ocr(p, od, sha="f" * 64))
            hit = JC.get_ocr("/different/path.jpg", sha="f" * 64)
            self.assertEqual(hit["width"], 800)
            self.assertEqual(hit["items"][0]["text"], "abc")
            self.assertIsNone(JC.get_ocr("/x", sha="0" * 64))  # 未命中
        finally:
            JC.OCR_CACHE = self.old_ocr


if __name__ == "__main__":
    unittest.main(verbosity=2)
