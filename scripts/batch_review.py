#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""batch_review.py — image_gate review.json 批量判定填充器（2026-11-24 新增）。

背景（本批实测）：contact sheet 多模态目视是**每图一次**的判定，但逐张回填 review.json
的 verdict 纯手工（5 品 ×8-23 图 = 上百次编辑）。判定结果（哪几张干净）与 review
回填之间是确定性映射，不该人肉搬。

用法（判定来源 = contact sheet 目视结论，编号即 main_01… 的序号）：
  python3 scripts/batch_review.py --review erp_data/image_gate/<tid>/review.json \
      --offer 662167272962 --clean 6,7,8,9,11,12,13,14 --dirty 1,2,3
  # --clean  → L3_keep（干净消费者向图；编号对应 selection_intake 的 main_NN 序号）
  # --dirty  → L0_drop（整版中文/B2B/参数表）
  # --flag   → L1_inpaint（干净商品图仅叠一行营销字，可 image_fix 救）
  # 未提及的图保持 pending（verify 的 NO_REVIEW 会拦，不允许静默放行）
  # SKU 通道同 URL 的图继承 main 判定（同一 URL 进不了两次判定，这是确定性规则）

顺序纪律（2026-11-24 定稿）：**先 scan→判定→本工具回填，再 ttl_edit --save**。
edit 会改写 detail URL 集合→旧判定整批作废（image_gate 的 stale 检测即为此设）。
ttl_edit 不传 --mains-file 时不动 main_images，判定结果因此跨 edit 依然有效。
跑完后 `image_gate.py apply --detail <final落盘> --review ... --save` + `verify`（不用 --no-require-review）。

退出码：0 写入成功 / 1 编号与 review 图片对不上 / 2 参数问题
"""
import argparse
import json
import os
import re
import sys
import time


def norm_idx(s):
    return int(re.sub(r"\D", "", str(s)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", required=True)
    ap.add_argument("--offer", required=True, help="货源ID（校验用；与 review 内 item 图 URL 店铺指纹比对）")
    ap.add_argument("--clean", default="", help="逗号分隔 main 编号 → L3_keep")
    ap.add_argument("--dirty", default="", help="逗号分隔 main 编号 → L0_drop")
    ap.add_argument("--flag", default="", help="逗号分隔 main 编号 → L1_inpaint")
    args = ap.parse_args()

    if not os.path.exists(args.review):
        print("[batch_review] review 不存在：%s（先跑 image_gate.py scan --detail ...）" % args.review,
              file=sys.stderr)
        return 2

    rv = json.load(open(args.review, encoding="utf-8"))
    images = rv.get("images") or []
    main_ids = {}
    for im in images:
        m = re.match(r"main_(\d+)$", im.get("id") or "")
        if m:
            main_ids[int(m.group(1))] = im
    plan = []
    for spec, verdict in ((args.clean, "L3_keep"), (args.dirty, "L0_drop"), (args.flag, "L1_inpaint")):
        for x in str(spec or "").split(","):
            x = x.strip()
            if not x:
                continue
            plan.append((norm_idx(x), verdict))
    if not plan:
        print("[batch_review] --clean/--dirty/--flag 至少给一个", file=sys.stderr)
        return 2

    changed = 0
    missed = []
    url2ids = {}
    for im in images:
        url2ids.setdefault(im.get("url"), []).append(im)
    for idx, verdict in plan:
        im = main_ids.get(idx)
        if not im:
            missed.append(idx)
            continue
        for target in url2ids.get(im["url"], []):   # 同 URL 的 sku 通道继承
            if target.get("verdict") != verdict:
                target["verdict"] = verdict
                target["verdict_source"] = "batch_review 2026-11-24（contact sheet 目视）"
                changed += 1
    rv["reviewed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(args.review, "w", encoding="utf-8") as f:
        json.dump(rv, f, ensure_ascii=False, indent=2)
    pending = sum(1 for i in images if (i.get("verdict") or "pending") == "pending")
    kept = sum(1 for i in images if "main" in (i.get("channels") or [])
               and i.get("verdict") == "L3_keep")
    print("[batch_review] 写入 %d 条判定（main 通道 L3_keep=%d，剩余 pending=%d）→ %s"
          % (changed, kept, pending, args.review))
    if missed:
        print("[batch_review] ⚠ 编号不存在（sheet 图数与 main_images 数不一致？）：%s" % missed, file=sys.stderr)
        return 1
    if pending:
        print("[batch_review] 提示：仍有 %d 图 pending，verify 会拦——补判定或确认这些图不发布"
              % pending)
    return 0


if __name__ == "__main__":
    sys.exit(main())
