#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""image_fix.image_dims 纯函数测试（2026-11-19 主图尺寸账代码化的回归保险）。

教训背景：垃圾袋 desc_08（790×1200 竖版）内容极好但不可升主图、魔力擦 desc_25
（265×320）分辨率不达 600 下限——这类账靠目视必漏，机器兜底前先保证机器自己算对。
"""
import os
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import image_fix  # noqa: E402


def make_png(w, h):
    """最小合法 PNG：签名 + IHDR(w,h) + IEND。"""
    ihdr = b"IHDR" + struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    import zlib
    chunks = [b"\x89PNG\r\n\x1a\n"]
    chunks.append(struct.pack(">I", len(ihdr) - 4) + ihdr
                  + struct.pack(">I", zlib.crc32(ihdr) & 0xFFFFFFFF))
    iend = b"IEND"
    chunks.append(struct.pack(">I", 0) + iend
                  + struct.pack(">I", zlib.crc32(iend) & 0xFFFFFFFF))
    return b"".join(chunks)


def make_jpeg(w, h):
    """最小 JPEG：SOI + 填充段 + SOF0(w,h) + EOI。"""
    # SOF0: FF C0 | len(2,=8+3*ncomp) | precision | height | width | ncomp | comp(3)
    sof = (b"\xff\xc0" + struct.pack(">H", 11)
           + struct.pack(">BHHB", 8, h, w, 1) + b"\x01\x22\x00")
    pad = b"\xff\xfe" + struct.pack(">H", 6) + b"XXXX"  # 普通 marker 段（长度含自身2字节）
    return b"\xff\xd8" + pad + sof + b"\xff\xd9"


class TestImageDims(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def dims(self, blob):
        p = os.path.join(self.tmp, "x.img")
        with open(p, "wb") as f:
            f.write(blob)
        return image_fix.image_dims(p)

    def test_png_square(self):
        self.assertEqual(self.dims(make_png(800, 800)), (800, 800))

    def test_png_portrait(self):
        self.assertEqual(self.dims(make_png(790, 1200)), (790, 1200))

    def test_jpeg_with_prefix_segments(self):
        self.assertEqual(self.dims(make_jpeg(960, 960)), (960, 960))

    def test_garbage_returns_none_not_raise(self):
        # 非图像字节：返回 None，不抛异常（verify_public_images 同款教训）
        self.assertIsNone(self.dims(b"not an image at all"))

    def test_small_png_still_parses(self):
        # 魔力擦 desc_25 类：265×320 要能解析出来才能被 <600 规则拦下
        self.assertEqual(self.dims(make_png(265, 320)), (265, 320))


if __name__ == "__main__":
    unittest.main()
