# -*- coding: utf-8 -*-
"""公网图回读校验：哈希反查 + RMS 容差判定（缺 PIL 时行为必须可解释）。"""
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import helpers  # noqa: F401  (挂 sys.path)
import verify_public_images as v


class TestJudge(unittest.TestCase):
    def test_exact_wins(self):
        self.assertTrue(v.judge_match(True, None, 3.0)[0])
        self.assertTrue(v.judge_match(True, 99.0, 3.0)[0])   # 精确命中即便 RMS=None 也过

    def test_rms_within_threshold_passes(self):
        ok, why = v.judge_match(False, 1.2, 3.0)
        self.assertTrue(ok, why)

    def test_rms_over_threshold_fails(self):
        ok, why = v.judge_match(False, 9.9, 3.0)
        self.assertFalse(ok)
        self.assertIn("内容已变", why)

    def test_no_pil_does_not_fake_pass(self):
        ok, why = v.judge_match(False, None, 3.0)
        self.assertFalse(ok)
        self.assertIn("无法计算像素差", why)


class TestHashIndex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, name, data):
        p = os.path.join(self.tmp, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_index_maps_content_to_path(self):
        a = self.write("01.jpg", b"AAA")
        b = self.write("02.jpg", b"BBB")
        idx = v.build_hash_index([a, b, os.path.join(self.tmp, "missing.jpg")])
        self.assertEqual(idx[hashlib.sha256(b"AAA").hexdigest()], a)
        self.assertEqual(idx[hashlib.sha256(b"BBB").hexdigest()], b)
        self.assertEqual(len(idx), 2)

    def test_local_files_dedup(self):
        item = {"local_images": ["/x/1.jpg", "/x/1.jpg", "/x/2.jpg"]}
        self.assertEqual(v.local_files(item, {}, "o"), ["/x/1.jpg", "/x/2.jpg"])

    def test_local_files_manifest_first(self):
        manifest = {"o": {"images": [{"file": "/m/1.jpg"}, {"nope": 1}, {"file": "/m/2.jpg"}]}}
        self.assertEqual(v.local_files({}, manifest, "o"), ["/m/1.jpg", "/m/2.jpg"])


class TestEndToEnd(unittest.TestCase):
    """用假 download 跑完整流程：错配必须能定位到具体是哪一张。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.files = []
        self.blobs = {}
        for i, name in enumerate(("01.jpg", "02.jpg")):
            data = ("IMG%d" % i).encode()
            p = os.path.join(self.tmp, name)
            with open(p, "wb") as f:
                f.write(data)
            self.files.append(p)
            self.blobs["https://cdn/%s" % name] = data

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_main(self, payload_items, remote_map):
        payload = os.path.join(self.tmp, "payload.json")
        out = os.path.join(self.tmp, "out.json")
        with open(payload, "w", encoding="utf-8") as f:
            json.dump({"items": payload_items}, f, ensure_ascii=False)
        with mock.patch.object(v, "download", side_effect=lambda url, t=20: remote_map[url]), \
             mock.patch("sys.argv", ["x", "--payload", payload, "--out", out]):
            rc = v.main()
        with open(out, encoding="utf-8") as f:
            return rc, json.load(f)

    def test_swapped_order_is_detected_and_located(self):
        # 远端第 1 张其实是本地第 2 张 → 内容命中，但位置错配
        items = [{"offer_id": "o", "images": ["https://cdn/01.jpg", "https://cdn/02.jpg"],
                  "local_images": self.files}]
        remote = {"https://cdn/01.jpg": b"IMG1", "https://cdn/02.jpg": b"IMG0"}  # 顺序反了
        rc, recs = self.run_main(items, remote)
        self.assertEqual(rc, 0, "内容都能对上，不该判失败")
        self.assertTrue(all(r["matched_by"] == "sha256" for r in recs))
        self.assertTrue(recs[0].get("position_mismatch"))
        self.assertIn("02.jpg", recs[0]["position_mismatch"])

    def test_unknown_content_fails_without_pil(self):
        items = [{"offer_id": "o", "images": ["https://cdn/x.jpg"], "local_images": self.files[:1]}]
        rc, recs = self.run_main(items, {"https://cdn/x.jpg": b"DIFFERENT"})
        if v.HAVE_PIL:
            self.skipTest("本机有 PIL，走 RMS 分支")
        self.assertEqual(rc, 1)
        self.assertFalse(recs[0]["match"])

    def test_download_error_fails(self):
        items = [{"offer_id": "o", "images": ["https://cdn/dead.jpg"], "local_images": []}]

        def boom(url, timeout=20):
            raise RuntimeError("404")
        payload = os.path.join(self.tmp, "p.json")
        out = os.path.join(self.tmp, "o.json")
        with open(payload, "w", encoding="utf-8") as f:
            json.dump({"items": items}, f)
        with mock.patch.object(v, "download", side_effect=boom), \
             mock.patch("sys.argv", ["x", "--payload", payload, "--out", out]):
            self.assertEqual(v.main(), 1)


if __name__ == "__main__":
    unittest.main()
