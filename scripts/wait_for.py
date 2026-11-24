#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wait_for.py — 发布/认领终态轮询（替代手打 sleep + logs 重查，2026-11-25 P2）

背景：2026-11-24 批次实测 24 次 sleep（共 320 秒）+ 12 次 logs 重查，全是 agent 轮次浪费。
本脚本内部指数退避轮询，一条命令阻塞到终态，退出码即结论。

用法：
  # 等发布终态（按货源ID或ttl_id匹配日志行）
  python3 scripts/wait_for.py publish --match 529647564532 [--timeout 180]
  python3 scripts/wait_for.py publish --match 1574881186748473344,686342613651

  # 等认领出现在 TikTok 采集箱（ttl-list 里出现该货源ID）
  python3 scripts/wait_for.py claim --offer-id 651892280915 --region MY [--timeout 120]

退出码：0 全部到终态且 success / 3 到终态但有失败（详情打印）/ 4 超时仍 processing / 2 参数错误
"""
import argparse
import csv
import glob
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from erp_client import _post  # noqa: E402

FAIL_MARK = ("失败", "fail", "error", "驳回")
OK_MARK = ("成功", "success")


def logs_rows(months=1):
    """拉发布记录，返回 [(raw_line, status_word)]。复用 queryLogs 接口。"""
    import datetime
    end = datetime.datetime.now()
    start = end - datetime.timedelta(days=31 * months)
    rows = []
    body = {"toPlatformList": ["tiktok"], "fromPlatformList": ["1688"],
            "startTime": start.strftime("%Y-%m-%d 00:00:00"),
            "endTime": end.strftime("%Y-%m-%d %H:%M:%S")}
    d = _post("/item/distribute/queryLogs", body, None)
    data = d.get("data") or {}
    lst = data.get("list") or data.get("rows") or []
    for it in lst:
        rows.append(json.dumps(it, ensure_ascii=False))
    return rows


def check_publish(matches, verbose=False):
    """返回 (done_map, pending) done_map: match -> 'success'|'fail'"""
    rows = logs_rows()
    done = {}
    for m in matches:
        hit = None
        for line in rows:
            if m in line:
                low = line
                if any(k in low for k in FAIL_MARK):
                    hit = "fail"
                    break
                if any(k in low for k in OK_MARK):
                    hit = "success"
                    break
                hit = "processing"
        if hit and hit != "processing":
            done[m] = hit
        elif verbose and hit:
            print("  %s 日志在但未见终态词" % m)
    return done, [m for m in matches if m not in done]


def check_claim(offer_id, region):
    d = _post("/item/crossBorder/platformCollect/queryListPage",
              {"pageNo": 1, "pageSize": 50, "toRegion": region}, None)
    data = d.get("data") or {}
    for it in (data.get("list") or []):
        blob = json.dumps(it, ensure_ascii=False)
        if offer_id in blob:
            return it.get("id") or it.get("ttlId")
    return None


def poll(fn, timeout, backoff=(3, 5, 10, 15, 20, 30)):
    t0 = time.time()
    i = 0
    while time.time() - t0 < timeout:
        r = fn()
        if r is not None:
            return r, time.time() - t0
        time.sleep(backoff[min(i, len(backoff) - 1)])
        i += 1
    return None, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("publish")
    p.add_argument("--match", required=True, help="逗号分隔：货源ID或ttl_id，全部到终态才退出")
    p.add_argument("--timeout", type=int, default=180)
    c = sub.add_parser("claim")
    c.add_argument("--offer-id", required=True)
    c.add_argument("--region", default="MY")
    c.add_argument("--timeout", type=int, default=120)
    args = ap.parse_args()

    if args.cmd == "publish":
        matches = [s.strip() for s in args.match.split(",") if s.strip()]
        res, el = poll(lambda: _pub_once(matches, args.timeout), args.timeout)
        if res is None:
            print("[wait_for] 超时 %.0fs 仍未终态: %s" % (el, matches))
            sys.exit(4)
        ok, fails = res
        if fails:
            print("[wait_for] %.0fs 完成，失败 %d: %s" % (el, len(fails), fails))
            sys.exit(3)
        print("[wait_for] %.0fs 全部 success: %s" % (el, matches))
        sys.exit(0)
    else:
        ttl, el = poll(lambda: check_claim(args.offer_id, args.region), args.timeout)
        if ttl:
            print("[wait_for] %.0fs 已出现在采集箱 ttl_id=%s" % (el, ttl))
            sys.exit(0)
        print("[wait_for] 超时未见 %s（先回查 items 的\"已认领\"字段，勿盲目重试 claim）" % args.offer_id)
        sys.exit(4)


def _pub_once(matches, timeout):
    done, pending = check_publish(matches)
    for m, st in done.items():
        print("  %s -> %s" % (m, st))
    if not pending:
        return (done, [m for m, s in done.items() if s != "success"]), None
    return None, None


if __name__ == "__main__":
    main()
