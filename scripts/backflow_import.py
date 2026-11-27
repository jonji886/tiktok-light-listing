#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""罗盘导出文件 → 本地回流（backflow.csv）批量回填（P0-1 数据回流闭环）。

背景：TikTok 商家后台「罗盘 → 商品数据分析 → 详细信息」可导出商品级宽表
（xlsx，实测 2026-11-25）。本脚本只读取**已落盘的导出文件**，不联网、不碰
cookie/签名，把商品级曝光/点击/加购/成交按时点回填进 `erp_data/backflow.csv`。

关联链（实测验证，2026-11-25 MY 店）：
    罗盘「商品 ID」(numIid)
      → 发布后商品列表快照 data.list：numIid → outerId(1688 货源ID)
      → 本地 tiktok_list*.csv：货源ID → ttl_id
      → backflow.csv：ttl_id 主键

时点口径（与 backflow.py 一致，显式 --stage 指定，不自动推断窗口）：
    t2  只回填曝光（入池检查）
    t7  曝光 / 点击 / 加购（四象限判定）
    t20 订单数 / GMV（退款率、延迟发货率罗盘商品表没有，保持留空）

纪律（沿用 backflow）：
  - 只读本地文件；读不到的指标标缺失，绝不填 0 冒充「零曝光/零成交」。
  - 默认 dry-run；--apply 才写盘。已有非空值默认保护不覆盖，--overwrite 才覆盖。
  - `-` / `--` / 空 = 缺失；`0` / `RM0.00` = 真实零值，二者必须区分。

用法：
    python3 scripts/backflow_import.py --file ~/Downloads/product_list.xlsx \
        --stage t7 --region MY            # 预览，不写盘
    python3 scripts/backflow_import.py --file product_list.xlsx \
        --stage t7 --region MY --apply   # 写回 backflow.csv
"""

import argparse
import csv
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.realpath(os.path.dirname(HERE))
sys.path.insert(0, HERE)

import backflow  # noqa: E402  复用 HEADER / read_rows / write_rows

ERP_DIR = os.path.join(ROOT, "erp_data")

# 罗盘列名 → 记录键（取「全部」渠道段；同名指标在各渠道段重复出现）
COLUMN_ALIASES = {
    "num_iid": ["商品 ID", "商品ID", "numIid", "item_id"],
    "title": ["商品名", "商品名称", "标题", "itemAlias"],
    "exposure": ["商品曝光次数", "曝光次数", "曝光"],
    "clicks": ["商品点击量", "点击量", "点击"],
    "add_cart": ["加购次数", "加购数"],
    "orders": ["订单数", "SKU 订单数"],
    "gmv": ["GMV", "成交额"],
}

# 时点 → {记录键: backflow.csv 字段}
STAGE_FIELDS = {
    "t2": {"exposure": "t2_exposure"},
    "t7": {"exposure": "t7_exposure", "clicks": "t7_clicks", "add_cart": "t7_add_cart"},
    "t20": {"orders": "t20_orders", "gmv": "t20_gmv"},
}

# 计数字段（回填成整数）；其余按金额/原值
COUNT_KEYS = {"exposure", "clicks", "add_cart", "orders"}

MISSING_TOKENS = {"", "-", "--", "—", "n/a", "na", "null", "none", "/"}


# ────────────────────────── 数值清洗 ──────────────────────────
def parse_number(raw):
    """`-`/`--`/空 → None（缺失）；`0`/`RM0.00` → 0（真实零值）。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if s.lower() in MISSING_TOKENS:
        return None
    for token in ("RM", "MYR", "rm", "￥", "¥", ",", "%", " "):
        s = s.replace(token, "")
    if s.lower() in MISSING_TOKENS:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fmt_value(key, val):
    if val is None:
        return ""
    if key in COUNT_KEYS:
        return str(int(round(val)))
    return ("%.2f" % val)


# ────────────────────────── 文件解析 ──────────────────────────
def _detect_columns(header_row, group_row):
    """返回 {记录键: 列索引}。优先取「全部」渠道段内的列，找不到退回首次出现。"""
    group = group_row or []
    all_cols = {i for i, g in enumerate(group) if str(g or "").strip() == "全部"}
    cols = {}
    for rec_key, aliases in COLUMN_ALIASES.items():
        picked = None
        if all_cols:
            for i, name in enumerate(header_row):
                if i in all_cols and str(name or "").strip() in aliases:
                    picked = i
                    break
        if picked is None:
            for i, name in enumerate(header_row):
                if str(name or "").strip() in aliases:
                    picked = i
                    break
        if picked is not None:
            cols[rec_key] = picked
    return cols


def _rows_to_records(rows):
    """把二维行数组转成记录列表；自动定位表头行（含「商品 ID」）。"""
    header_idx = None
    for i, row in enumerate(rows):
        joined = {str(c or "").strip() for c in row}
        if {"商品 ID", "商品ID", "numIid"} & joined:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("未找到含「商品 ID」的表头行，确认是罗盘商品明细导出（不是渠道汇总表）")

    header = rows[header_idx]
    group_row = rows[header_idx - 1] if header_idx > 0 else []
    cols = _detect_columns(header, group_row)
    if "num_iid" not in cols:
        raise ValueError("表头里没有「商品 ID」列")

    records = []
    for row in rows[header_idx + 1:]:
        if not row or len(row) <= cols["num_iid"]:
            continue
        num = str(row[cols["num_iid"]] or "").strip()
        if not num:
            continue
        rec = {"num_iid": num, "title": ""}
        for key, ci in cols.items():
            if key == "num_iid":
                continue
            raw = row[ci] if ci < len(row) else None
            if key == "title":
                rec[key] = str(raw or "").strip()
            else:
                rec[key] = parse_number(raw)
        records.append(rec)
    return records


def read_compass_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = [list(r) for r in csv.reader(f)]
    return _rows_to_records(rows)


def read_compass_xlsx(path):
    try:
        import openpyxl  # 可选依赖：仅读 xlsx 时需要
    except ImportError as exc:  # pragma: no cover - 环境差异
        raise SystemExit("读取 .xlsx 需要 openpyxl：pip3 install openpyxl（或先把导出另存为 .csv）") from exc
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    rows = [[("" if c is None else c) for c in row] for row in ws.iter_rows(values_only=True)]
    return _rows_to_records(rows)


def read_compass_file(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        return read_compass_csv(path)
    return read_compass_xlsx(path)


# ────────────────────────── ID 映射 ──────────────────────────
def _iter_local_list_csv(erp_dir):
    for f in glob.glob(os.path.join(erp_dir, "**", "tiktok_list*.csv"), recursive=True):
        if not os.path.exists(f):
            continue
        with open(f, encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                yield row


def build_id_map(erp_dir=ERP_DIR):
    """返回 (num_to_ttl, num_to_offer, offer_to_ttl)。"""
    offer_to_ttl = {}
    for row in _iter_local_list_csv(erp_dir):
        offer = str(row.get("货源ID") or "").strip()
        ttl = str(row.get("ttl_id") or "").strip()
        if offer and ttl:
            offer_to_ttl[offer] = ttl

    num_to_offer = {}
    for f in glob.glob(os.path.join(erp_dir, "**", "*.json"), recursive=True):
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
        except (ValueError, OSError):
            continue
        items = []
        if isinstance(data, dict):
            inner = data.get("data")
            if isinstance(inner, dict) and isinstance(inner.get("list"), list):
                items = inner["list"]
            elif isinstance(data.get("list"), list):
                items = data["list"]
        elif isinstance(data, list):
            items = data
        for r in items:
            if not isinstance(r, dict):
                continue
            # 快照常混着 shopee 草稿/采集记录（platform=shopees，8 位 numIid，
            # 其它商户）；只采纳本店 tiktok 平台商品，numIid 为 19 位商品 ID。
            if str(r.get("platform") or "").lower() != "tiktok":
                continue
            num = str(r.get("numIid") or "").strip()
            offer = str(r.get("outerId") or "").strip()
            if num and offer and len(num) >= 15:
                num_to_offer[num] = offer

    num_to_ttl = {
        num: offer_to_ttl[offer]
        for num, offer in num_to_offer.items()
        if offer in offer_to_ttl
    }
    return num_to_ttl, num_to_offer, offer_to_ttl


# ────────────────────────── 回填应用 ──────────────────────────
def apply_import(backflow_rows, records, num_to_ttl, stage,
                 overwrite=False, region=""):
    field_map = STAGE_FIELDS[stage]
    by_ttl = {str(r.get("ttl_id") or ""): r for r in backflow_rows}

    updated, skipped, unmatched = [], [], []
    for rec in records:
        num = rec["num_iid"]
        ttl = num_to_ttl.get(num)
        if not ttl:
            unmatched.append({**rec, "reason": "快照/本地列表未找到该商品 ID"})
            continue
        row = by_ttl.get(ttl)
        if row is None:
            unmatched.append({**rec, "reason": "backflow.csv 无此 ttl_id（先 sync）"})
            continue
        if region and str(row.get("region") or "").strip() != region:
            unmatched.append({**rec, "reason": "站点 %s 不匹配 %s" % (row.get("region"), region)})
            continue

        changes, blocked = {}, []
        for rec_key, bf_field in field_map.items():
            val = rec.get(rec_key)
            if val is None:
                continue  # 缺失不写，不填 0
            existing = str(row.get(bf_field) or "").strip()
            if existing and not overwrite:
                blocked.append(bf_field)
                continue
            changes[bf_field] = fmt_value(rec_key, val)

        if blocked and not changes:
            skipped.append({"ttl_id": ttl, "title": rec["title"], "blocked": blocked})
            continue
        if changes:
            row.update(changes)
            row["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            row["note"] = (str(row.get("note") or "") +
                            " | import:compass-%s %s" % (stage, time.strftime("%Y%m%d"))).strip(" |")
            updated.append({"ttl_id": ttl, "title": rec["title"], "changes": changes})
        else:
            skipped.append({"ttl_id": ttl, "title": rec["title"], "blocked": blocked})

    return {"updated": updated, "skipped": skipped, "unmatched": unmatched}


# ────────────────────────── CLI ──────────────────────────
def main():
    ap = argparse.ArgumentParser(description="罗盘导出文件批量回填 backflow.csv")
    ap.add_argument("--file", required=True, help="罗盘商品明细导出 .xlsx 或 .csv")
    ap.add_argument("--stage", required=True, choices=["t2", "t7", "t20"],
                    help="回填时点：t2 曝光 / t7 曝光点击加购 / t20 订单GMV")
    ap.add_argument("--region", default="", help="只回填该站点（如 MY）")
    ap.add_argument("--apply", action="store_true", help="写回 backflow.csv（默认 dry-run）")
    ap.add_argument("--overwrite", action="store_true", help="覆盖已有非空回填值")
    args = ap.parse_args()

    if not os.path.exists(args.file):
        print("[import] 文件不存在：%s" % args.file)
        return 2

    records = read_compass_file(args.file)
    print("[import] 解析到 %d 条商品记录" % len(records))

    num_to_ttl, num_to_offer, offer_to_ttl = build_id_map()
    print("[import] 映射：商品ID→货源ID %d 条；货源ID→ttl_id %d 条；闭环 %d 条"
          % (len(num_to_offer), len(offer_to_ttl), len(num_to_ttl)))

    backflow_rows = backflow.read_rows(backflow.BACKFLOW)
    if not backflow_rows:
        print("[import] backflow.csv 为空，先跑 python3 scripts/backflow.py sync")
        return 2

    result = apply_import(backflow_rows, records, num_to_ttl, args.stage,
                         overwrite=args.overwrite, region=args.region)

    print("\n【将写入】%d 条" % len(result["updated"]))
    for u in result["updated"]:
        print("  %-19s %s %s" % (u["ttl_id"], u["changes"], (u["title"] or "")[:24]))
    if result["skipped"]:
        print("\n【保留原值未覆盖】%d 条（加 --overwrite 可覆盖）" % len(result["skipped"]))
        for s in result["skipped"]:
            print("  %-19s 已填 %s" % (s["ttl_id"], ",".join(s["blocked"])))
    if result["unmatched"]:
        print("\n【未匹配】%d 条" % len(result["unmatched"]))
        for u in result["unmatched"]:
            print("  %-19s %s | %s" % (u["num_iid"], u["reason"], (u["title"] or "")[:24]))

    if not args.apply:
        print("\n[import] dry-run：未写盘。确认无误后加 --apply 写回 %s" % backflow.BACKFLOW)
        return 0

    backflow.write_rows(backflow_rows, backflow.BACKFLOW)
    print("\n[import] 已写回 %s（更新 %d / 跳过 %d / 未匹配 %d）"
          % (backflow.BACKFLOW, len(result["updated"]),
             len(result["skipped"]), len(result["unmatched"])))
    print("[import] 下一步：python3 scripts/backflow.py judge")
    return 0


if __name__ == "__main__":
    sys.exit(main())
