#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""report_ready_check.py — 提报前置检查（P2，2026-11-23）。

为什么存在（2026-11-20 实测，VERIFIED）：商品机会「绑定现有商品」弹窗逐品校验标题违禁词，
**任一**被勾品命中（首品标题含 "Amazon"）→ 提交按钮禁用、整单发不出去。
当时是进 GUI 后靠试错撞出来的，来回 6 次截图才定位原因。本脚本把检查前移到进 GUI 之前：
一条命令输出「可直接提报 / 先改标题再提报」两张清单 + 弹窗里该勾哪些品的名字。

检查口径（与提报弹窗同源的两类硬拦）：
  1) 竞品平台词：config/banned_platform_words.txt（amazon/shopee/lazada…），
     匹配 = 标题去非字母后小写子串（抓 laris Amazon / plasticsampah / larisamazon 连写变体）；
  2) 汉字残留：标题含 CJK → TikTok 侧违规，提报/发布都会拦（prepublish_guard 同源纪律）。
商标词/品牌词不在本脚本范围（弹窗拦的是违禁词类，品牌跟卖走 opportunity_intake 红线）。

数据源：erp_data/ttl_list_<site>.csv（backflow.py sync 维护的在售清单）或
       erp_data/tiktok_list.csv（erp_client.py ttl-list 全站点清单，--all-sites）。

用法：
  python3 scripts/report_ready_check.py                    # MY 在售清单（默认）
  python3 scripts/report_ready_check.py --sites my,th      # 多站点在售 csv
  python3 scripts/report_ready_check.py --list erp_data/tiktok_list.csv --region MY
  python3 scripts/report_ready_check.py --json             # 机读（给 Agent 编排）
退出码：0 存在可提报品 / 1 全部被拦（先修标题再进 GUI）/ 2 清单缺失。
纪律：**纯只读，零网络**。检查通不过的词表改动只发生在 config/banned_platform_words.txt。
"""
import argparse
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORDS_TXT = os.path.join(ROOT, "config", "banned_platform_words.txt")
CJK = re.compile(r"[\u4e00-\u9fff]")


def load_banned():
    words = []
    for raw in open(WORDS_TXT, encoding="utf-8-sig"):
        w = raw.split("#")[0].strip().lower()
        if w:
            words.append(w)
    return words


def check_title(title, banned):
    """→ 违规列表（空=可提报）。两类：竞品平台词 / 汉字。"""
    hits = []
    norm = re.sub(r"[^a-z]", "", (title or "").lower())
    for w in banned:
        wkey = re.sub(r"[^a-z]", "", w)
        if wkey and wkey in norm:
            hits.append("PLATFORM_WORD:%s" % w)
    if CJK.search(title or ""):
        cjk = "".join(CJK.findall(title))[:12]
        hits.append("CJK_RESIDUE:%s" % cjk)
    return hits


def read_rows(path, region_filter=None):
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            out.append(r)
    if region_filter:
        out = [r for r in out if (r.get("站点") or r.get("region") or "").upper() == region_filter]
    return out


def main():
    ap = argparse.ArgumentParser(description="提报前置检查：在售品标题违禁词扫描（纯本地只读）")
    ap.add_argument("--sites", default="my", help="逗号分隔站点（读 erp_data/ttl_list_<site>.csv）")
    ap.add_argument("--list", dest="list_csv", default="",
                    help="直接指定清单 csv（如 erp_client.py ttl-list 落盘），配合 --region 过滤")
    ap.add_argument("--region", default="", help="--list 模式下的站点过滤（MY/TH/VN）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    banned = load_banned()

    items = []          # (site, ttl_id, title)
    missing = []
    if a.list_csv:
        p = a.list_csv if os.path.isabs(a.list_csv) else os.path.join(ROOT, a.list_csv)
        if not os.path.exists(p):
            print("[ready] 清单不存在：%s（先 python3 scripts/erp_client.py ttl-list --region %s）"
                  % (p, a.region or "MY"), file=sys.stderr)
            return 2
        for r in read_rows(p, (a.region or None)):
            items.append(((r.get("站点") or a.region or "?").upper(),
                          r.get("ttl_id") or r.get("货源ID") or "?",
                          r.get("标题") or r.get("title") or ""))
    else:
        for site in [s.strip().lower() for s in a.sites.split(",") if s.strip()]:
            p = os.path.join(ROOT, "erp_data", "ttl_list_%s.csv" % site)
            if os.path.isdir(p):                      # ttl-list --out 会落目录
                p = os.path.join(p, "tiktok_list.csv")
            if not os.path.exists(p):
                missing.append(site)
                continue
            for r in read_rows(p):
                items.append((site.upper(), r.get("ttl_id") or "?",
                              r.get("标题") or r.get("title") or ""))
        if not items:
            print("[ready] 没读到任何在售清单：%s 均缺（先跑 backflow.py sync 或 ttl-list）"
                  % ",".join(missing), file=sys.stderr)
            return 2

    ok, blocked = [], []
    for site, tid, title in items:
        v = check_title(title, banned)
        (blocked if v else ok).append({"site": site, "ttl_id": tid,
                                       "title": title[:70], "violations": v})

    if a.json:
        print(json.dumps({"total": len(items), "ready": ok, "blocked": blocked,
                          "word_list": WORDS_TXT}, ensure_ascii=False, indent=1))
    else:
        print("[ready] 在售 %d 款（站点：%s）：可提报 %d / 被拦 %d"
              % (len(items), ",".join(sorted({i[0] for i in items})), len(ok), len(blocked)))
        for b in blocked:
            print("  ✗ [%s] %s %s" % (b["site"], b["ttl_id"], b["title"]))
            print("      违规: %s → **先改标题（ttl_edit/i18n 替换）再提报**，不要硬进弹窗"
                  % "; ".join(b["violations"]))
        for o in ok:
            print("  ✓ [%s] %s %s" % (o["site"], o["ttl_id"], o["title"]))
        if missing:
            print("  ! 缺清单站点（跳过）：%s" % ",".join(missing))
        if blocked:
            print("[ready] 进「绑定现有商品」时**只勾 ✓ 清单里的品**；同一机会词若只有 ✗ 品可关联，"
                  "先去商品管理改标题（laris Amazon 类 ERP 注入词按 config/i18n/ms.csv 替换）再回来。")
    return 0 if ok else (1 if blocked else 2)


if __name__ == "__main__":
    sys.exit(main())
