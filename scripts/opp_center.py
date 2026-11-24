#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""opp_center.py — 机会中心 DOM 抄录辅助器（2026-11-25 P1-1）

背景与路线结论（先读这段，别再走弯路）：
  接口重放路线已**四重证据排除**：
    ① Keychain/Chrome Cookie 提取在 WorkBuddy 沙箱被 SIGTERM；
    ② browser-use skill 硬约束「只使用 mcp__browser__* 工具，不使用 CDP/命令行浏览器」；
    ③ 匿名 curl 页面壳 302（登录墙）；
    ④ TikTok 后台接口带签名头（msToken 类），纯 Cookie 重放大概率 403。
  且实测弹窗正文（参考价/类目）**不进 DOM 可访问树**——抄弹窗内容仍必须截图+多模态。
  所以本脚本做的是能确定性的两件事：
    A. parse   —— 把 dom_cua_get_visible_dom 快照解析成卡片骨架清单（预览按钮 node_id、
                  卡片序号、可读到的标题），坐标点击 → DOM node 点击，消灭坐标盲试；
    B. record  —— 结构化参数写入 opportunity_signals.csv（列序/去重/转义交给脚本），
                  替代手写 heredoc 拼 CSV（2026-11-24 手拼出过 BOM/列错位）。

用法：
  # A. Agent 调 mcp__browser__dom_cua_get_visible_dom 拿快照 → 存文件 → 解析
  python3 scripts/opp_center.py parse --dom /tmp/snapshot.txt [--page 1]
  # 输出每张卡：序号 | 预览按钮 node_id | 标题（若 tabpanel 可读）——
  # Agent 用 dom_cua_click(node_id) 开弹窗 → tab_screenshot → 多模态读 → 下一步

  # B. 抄完一张卡记录一行（弹窗截图读出什么就传什么，缺项留空，禁止猜）
  python3 scripts/opp_center.py record --title "QB Clothes Dust Cover..." \
      --sales "3.7k+" --price-ref "RM1.60 - RM4.70" \
      --category "Bekalan Rumah > Bekalan Penjagaan Rumah > Pelindung Habuk" \
      --site MY --metrics yes --keyword "衣服防尘罩 透明" --note "第1页卡1;趋势线4K平"

  # C. 查重：本页已抄过哪些（防翻页后重复抄）
  python3 scripts/opp_center.py list [--site MY]

退出码：0 成功 / 2 输入问题
"""
import argparse
import csv
import json
import os
import re
import sys
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SIGNALS = os.path.join(ROOT, "erp_data", "opportunity_signals.csv")
COLS = ["title", "sales", "price_ref", "category", "source_site",
        "tiktok_metrics", "image_url", "search_keyword", "note"]

CARD_SPLIT = "添加同款商品"


def parse_dom(text):
    """DOM 快照 → 卡片骨架。卡片组=预览按钮到下一个「添加同款商品」按钮。"""
    cards = []
    # tabpanel 里第一个卡片标题可读（其余卡片标题不在可访问树）
    m = re.search(r'role="tabpanel">(.*)', text)
    first_title = ""
    if m:
        t = m.group(1)
        # 标题从筛选按钮文本后开始，取最长英文串
        mm = re.search(r'([A-Za-z][A-Za-z0-9 ,\-\.\'\(\)]{25,})', t)
        first_title = mm.group(1).strip() if mm else ""
    # 预览按钮与其所属卡片：按「添加同款商品」切段
    segs = text.split(CARD_SPLIT)
    for i, seg in enumerate(segs):
        pv = re.findall(r'node_id=(\d+) aria-label="预览"', seg)
        if pv:
            cards.append({"card": i + 1, "preview_node_id": int(pv[-1]),
                          "title_if_known": first_title if i == 0 else ""})
    # 翻页按钮（底部 pagination，aria-label 为纯数字且段外还有收藏/提报按钮区分）
    pages = []
    tail = segs[-1] if segs else ""
    for pm in re.finditer(r'node_id=(\d+)[^>]*aria-label="(\d{1,2})"[^>]*/?>', tail):
        pages.append({"page_no": int(pm.group(2)), "node_id": int(pm.group(1))})
    return {"cards": cards, "pager": pages,
            "pager_confidence": "低：卡片轮播点同为 aria-label 数字，翻页前须先滚动到底并截图确认",
            "hint": "逐卡：dom_cua_click(preview_node_id) → tab_screenshot → 多模态读弹窗 → "
                    "record 落一行 → Escape 关弹窗。翻页：点 pager.node_id 后重新 parse。"}


def cmd_parse(a):
    text = open(a.dom, encoding="utf-8").read() if a.dom != "-" else sys.stdin.read()
    out = parse_dom(text)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print("[parse] %d 张卡片骨架（页=%s）" % (len(out["cards"]), a.page))
    return 0


def cmd_record(a):
    if not a.title.strip():
        print("[err] --title 必填（弹窗里读到的商品标题原文）", file=sys.stderr)
        return 2
    row = {"title": a.title.strip(), "sales": a.sales, "price_ref": a.price_ref,
           "category": a.category, "source_site": a.site, "tiktok_metrics": a.metrics,
           "image_url": a.image_url, "search_keyword": a.keyword,
           "note": ((a.note or "") + ";%s 第%s页 opp_center.record"
                    % (datetime.date.today(), a.page)).strip(";")}
    exists = {}
    if os.path.exists(SIGNALS):
        for r in csv.DictReader(open(SIGNALS, encoding="utf-8-sig")):
            exists[(r.get("source_site") or "").upper()] = exists.get(
                (r.get("source_site") or "").upper(), []) + [r]
    dup = [r for r in exists.get(a.site.upper(), [])
           if (r.get("title") or "")[:40] == row["title"][:40]]
    if dup and not a.force:
        print("[record] 已存在同名卡片（site=%s）: %r —— 跳过（--force 强写）"
              % (a.site, dup[0]["title"][:50]))
        return 0
    new = not os.path.exists(SIGNALS)
    with open(SIGNALS, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        if new:
            w.writeheader()
        w.writerow(row)
    print("[record] 写入 1 行 → %s（site=%s 累计 %d 行）"
          % (SIGNALS, a.site, len(exists.get(a.site.upper(), [])) + 1))
    return 0


def cmd_list(a):
    if not os.path.exists(SIGNALS):
        print("[list] 尚无抄录")
        return 0
    rows = [r for r in csv.DictReader(open(SIGNALS, encoding="utf-8-sig"))
            if not a.site or (r.get("source_site") or "").upper() == a.site.upper()]
    print("[list] site=%s 已抄 %d 张：" % (a.site or "ALL", len(rows)))
    for i, r in enumerate(rows, 1):
        print("  %2d. %s | %s | %s" % (i, (r.get("title") or "")[:56],
                                       r.get("price_ref") or "-",
                                       (r.get("note") or "")[:24]))
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("parse")
    p.add_argument("--dom", required=True, help="DOM快照文件（- 读 stdin）")
    p.add_argument("--page", default="1")
    p.set_defaults(fn=cmd_parse)
    r = sub.add_parser("record")
    r.add_argument("--title", required=True)
    r.add_argument("--sales", default="")
    r.add_argument("--price-ref", dest="price_ref", default="")
    r.add_argument("--category", default="")
    r.add_argument("--site", default="MY")
    r.add_argument("--metrics", default="unknown", help="yes/no/unknown（弹窗四项站内指标）")
    r.add_argument("--image-url", dest="image_url", default="")
    r.add_argument("--keyword", default="", help="中文品类检索词（判读闸门用）")
    r.add_argument("--note", default="")
    r.add_argument("--page", default="1")
    r.add_argument("--force", action="store_true")
    r.set_defaults(fn=cmd_record)
    l = sub.add_parser("list")
    l.add_argument("--site", default="")
    l.set_defaults(fn=cmd_list)
    a = ap.parse_args()
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()
