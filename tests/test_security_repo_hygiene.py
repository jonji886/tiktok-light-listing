# -*- coding: utf-8 -*-
"""任务六安全回归：仓库内不得出现明文凭证/真实店铺标识；.gitignore 必须覆盖凭证文件。

2026-11-27 整改背景：_archive 快照曾带真实 sellerId 与店铺名（已占位化）；
ERP sourceSellerId 曾硬编码在代码/文档里（已改 .env）。本测试把「不再犯」钉死。
"""
import os
import re
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

JWT_PAT = re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")
# 敏感字面量用拼接构造，防止扫描器自身命中（自引用假阳性）
_REAL_SELLERS = [
    "868682311" + "8790558807", "868682311" + "8790493271",
    "868682311" + "8786233431", "868682311" + "8786298967",
    "749487732" + "0827733079", "1791686992" + "43081", "281293016" + "3",
]
REAL_SELLER_PAT = re.compile(r"\b(" + "|".join(re.escape(x) for x in _REAL_SELLERS) + r")\b")
HARDCODED_SOURCE_PAT = re.compile(r"\b" + "13239" + "65" + r"\b")


def tracked_text_files():
    out = subprocess.run(["git", "-c", "core.quotePath=false", "ls-files"],
                         capture_output=True, text=True, cwd=ROOT).stdout
    for p in out.splitlines():
        fp = os.path.join(ROOT, p)
        if not os.path.isfile(fp):
            continue
        if os.path.getsize(fp) > 2_000_000:
            continue
        try:
            yield p, open(fp, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue


class TestRepoHasNoCredentials(unittest.TestCase):
    """只扫 git 跟踪文件（工作区未跟踪的 .env/erp_data 运行产物本来就不入库）。"""

    @unittest.skipIf(not os.path.isdir(os.path.join(ROOT, ".git")), "无 git 环境")
    def test_no_jwt_anywhere_in_tracked(self):
        for p, s in tracked_text_files():
            m = JWT_PAT.search(s)
            self.assertIsNone(m, "%s 含疑似 JWT" % p if m else None)

    @unittest.skipIf(not os.path.isdir(os.path.join(ROOT, ".git")), "无 git 环境")
    def test_no_real_seller_ids(self):
        for p, s in tracked_text_files():
            m = REAL_SELLER_PAT.search(s)
            self.assertIsNone(m, "%s 含真实 sellerId: %s" % (p, m.group(0)) if m else None)

    @unittest.skipIf(not os.path.isdir(os.path.join(ROOT, ".git")), "无 git 环境")
    def test_no_hardcoded_source_seller_id(self):
        hits = []
        for p, s in tracked_text_files():
            if HARDCODED_SOURCE_PAT.search(s):
                hits.append(p)
        self.assertEqual(hits, [], "硬编码 sourceSellerId（应走 .env ERP_SOURCE_SELLER_ID）: %s" % hits)

    def test_env_example_has_no_values(self):
        s = open(os.path.join(ROOT, ".env.example"), encoding="utf-8").read()
        sensitive = ("SELLER_ID", "TOKEN", "KEY", "SECRET", "NICK")
        for line in s.splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                if any(t in k for t in sensitive):
                    self.assertEqual(v.strip(), "", "%s 的 .env.example 不应带真实值" % k)

    def test_gitignore_blocks_credentials(self):
        gi = open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read()
        for token in (".env", "erp_token.txt", "ali_cookie.txt"):
            self.assertIn(token, gi)
        for pat in ("**/.env", "**/erp_token.txt"):
            self.assertIn(pat, gi, "缺少全目录兜底: %s" % pat)

    def test_trace_logs_gitignored(self):
        """任务轨迹可能含商品/店铺细节，必须不入库。"""
        self.assertTrue(subprocess.run(
            ["git", "check-ignore", "erp_data/_run/trace/task_x.jsonl"],
            capture_output=True, cwd=ROOT).returncode == 0)


class TestAgentlogRedactionInErp(unittest.TestCase):
    def test_erp_client_registers_token_secret(self):
        """_post 必须把 JWT 原文登记给 agentlog（写日志前统一替换的机制前提）。"""
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        s = open(os.path.join(ROOT, "scripts", "erp_client.py"), encoding="utf-8").read()
        self.assertIn("agentlog.set_secret(tok)", s)
