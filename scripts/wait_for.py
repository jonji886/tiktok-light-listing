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
    """拉发布记录，返回原始 dict 行（含 status / itemId / platformItemId）。

    ⚠️ 2026-11-25 修：erp_client._post 返回的是 **data 本体**（已解包），
    原代码又做了一次 d.get("data") → 永远拿到空列表 → 发布永远判不到终态。
    """
    import datetime
    end = datetime.datetime.now()
    start = end - datetime.timedelta(days=31 * months)
    body = {"pageNo": 1, "pageSize": 200,
            "toPlatformList": ["tiktok"], "fromPlatformList": ["1688"],
            "startTime": start.strftime("%Y-%m-%d 00:00:00"),
            "endTime": end.strftime("%Y-%m-%d %H:%M:%S")}
    d = _post("/item/distribute/queryLogs", body, None)
    if not isinstance(d, dict):
        return []
    return d.get("list") or d.get("rows") or []


def check_publish(matches, verbose=False):
    """返回 (done_map, pending)。done_map: match -> 'success'|'fail'

    判定用 status **精确值**（processing/success/fail），不再对整条 JSON 做子串匹配
    ——标题或 errorMessage 里出现 success/fail 会造成误判（2026-11-25 修）。
    """
    rows = logs_rows()
    done, ids = {}, {}
    for m in matches:
        hits = [r for r in rows
                if str(r.get("itemId")) == str(m) or str(m) == str(r.get("platformItemId"))]
        if not hits:
            continue
        latest = max(hits, key=lambda x: str(x.get("uptTime") or x.get("createTime") or ""))
        st = str(latest.get("status") or "").lower()
        ext = latest.get("extInfo") or {}
        pid = latest.get("platformItemId") or ext.get("TIKTOK_PRODUCT_ID")
        if st in ("success", "fail"):
            done[m] = st
            ids[m] = pid
            if verbose:
                print("  %s -> %s (tiktok_id=%s, %s)" % (m, st, pid,
                      latest.get("errorMessage") or ""))
        elif verbose:
            print("  %s 仍 %s" % (m, st or "未知"))
    return done, [m for m in matches if m not in done], ids


def check_claim(offer_id, region):
    d = _post("/item/crossBorder/platformCollect/queryListPage",
              {"pageNo": 1, "pageSize": 50, "toRegion": region}, None)
    lst = d.get("list") if isinstance(d, dict) else None   # 同样是 data 本体，勿再 .get("data")
    for it in (lst or []):
        if str(it.get("itemId")) == str(offer_id) or \
                str(it.get("fromItemId")) == str(offer_id) or \
                offer_id in json.dumps(it, ensure_ascii=False):
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
    done, pending, ids = check_publish(matches)
    for m, st in done.items():
        print("  %s -> %s (tiktok_id=%s)" % (m, st, ids.get(m)))
    if not pending:
        return (done, [m for m, s in done.items() if s != "success"]), None
    return None, None


if __name__ == "__main__":
    main()
