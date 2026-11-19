#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一条命令跑完全部离线测试：python3 tests/run.py

不需要 pytest，不需要网络，不需要 ERP token。缺 PIL 时相关用例自动跳过。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main():
    suite = unittest.defaultTestLoader.discover(start_dir=HERE, pattern="test_*.py",
                                                top_level_dir=HERE)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
