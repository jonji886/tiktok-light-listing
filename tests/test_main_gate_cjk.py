#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主图判定硬闸（678 教训）回归测试。

背景：2026-11-19 MY 批把带中文横幅「灰色三件套/蓝色三件套」的 main_02/03 目视误填 L3_keep，
上架后详情页中文被用户抓到。修复：apply 前机器强制——主图 L3_keep 必须有 probe 证据且
rec_text 无中文；换链 URL 不得落在 TikTok 拉不到的域。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

from image_gate import main_gate_violations  # noqa: E402

CBU = "https://cbu01.alicdn.com/img/x1.jpg"
CBU2 = "https://cbu01.alicdn.com/img/x2.jpg"
DASH = "https://dashscope-7c2c.oss-cn-shanghai.aliyuncs.com/a.jpg"
VPC = "https://kdzs-jxc-vpc.oss-cn-zhangjiakou.aliyuncs.com/b.jpg"


def img(iid, url, verdict="L3_keep", evidence=None, channels=("main",)):
    d = {"id": iid, "url": url, "verdict": verdict, "channels": list(channels)}
    if evidence is not None:
        d["evidence"] = evidence
    return d


class TestMainGate(unittest.TestCase):
    def test_no_probe_evidence_blocks(self):
        """678 失误模式：没跑探针就标 L3_keep → 拦截"""
        images = [img("main_01", CBU)]  # 无 evidence 键
        viol = main_gate_violations(images, {CBU}, {})
        self.assertEqual(len(viol), 1)
        self.assertIn("无 probe 证据", viol[0])

    def test_cjk_rec_text_blocks(self):
        """探针见中文（678 main_02/03 实测文本）→ 拦截"""
        images = [img("main_02", CBU, evidence={"rec_text": ["灰色三件套", "马桶刷*1"]})]
        viol = main_gate_violations(images, {CBU}, {})
        self.assertIn("灰色三件套", viol[0])

    def test_english_decor_passes(self):
        """main_01 型英文装饰字（TAKE INTO ACCOUNT…）→ 放行不误伤"""
        images = [img("main_01", CBU, evidence={"rec_text": ["TAKE INTO ACCOUNT THE"]})]
        self.assertEqual(main_gate_violations(images, {CBU}, {}), [])

    def test_clean_probe_passes(self):
        images = [img("main_04", CBU, evidence={"rec_text": []})]
        self.assertEqual(main_gate_violations(images, {CBU}, {}), [])

    def test_desc_channel_not_enforced(self):
        """desc 通道维持宽松：无证据也不拦"""
        u = "https://cbu01.alicdn.com/img/d.jpg"
        images = [img("desc_06", u, evidence=None, channels=("desc",))]
        self.assertEqual(main_gate_violations(images, {u}, {}), [])

    def test_bad_host_uri_map_blocks(self):
        """换链到 dashscope（713 实测 TikTok 拉不到）→ 拦截"""
        images = [img("main_01", CBU, evidence={"rec_text": []})]
        viol = main_gate_violations(images, {CBU}, {CBU: DASH})
        self.assertIn("拉不到的域", viol[0])
        viol2 = main_gate_violations(images, {CBU}, {CBU: VPC})
        self.assertIn("拉不到的域", viol2[0])

    def test_good_host_swap_passes(self):
        images = [img("main_01", CBU, evidence={"rec_text": []})]
        self.assertEqual(main_gate_violations(images, {CBU}, {CBU: CBU2}), [])

    def test_l1_inpaint_not_cjk_checked(self):
        """L1_inpaint 本来就允许带字（待擦），只查域名"""
        images = [img("main_02", CBU, verdict="L1_inpaint",
                      evidence={"rec_text": ["灰色三件套"]})]
        self.assertEqual(main_gate_violations(images, {CBU}, {}), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
