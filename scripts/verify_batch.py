#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_batch.py — 批量验证「平台侧是否在售」（A1，2026-11-25）

治的痛（本轮最大单笔损耗）：publish_one 的 verify4 是**品内串行**轮询，
5 个品各等 62.7s，合计 375.9s = 6.3 min，占全套发布链路 656s 的 57%。
而等待的是平台审核同一个 SLA，5 个品完全可以叠在同一段时间里等。

本脚本把「发完再审」代替「边发边审」：
  ① 一次拉全量发布日志（原 per-offer 调 5 次 = 5 × 全量 200 条，浪费）
  ② 内存里按 itemId 分组拿 platformItemId
  ③ 只对未 ACTIVATE 的商品查详情，并发（--workers）
  ④ 循环轮询直到全部 ACTIVATE / 出现 fail / 超时

实测口径：单次 verify4 = 62.7s（logs + 详情 + 必要时 sleep30 后再查）。
批量后 5 品 ≈ 一次 logs(≈8s) + 5 详情并发(≈10s) ≈ 20s 起步，
若首轮未激活则每轮 +20s（同轮共享，不乘品数）。

用法：
  set -a; source .env; set +a
  python3 scripts/verify_batch.py --offers 946425355590,1036443288103 [--timeout 900] [--workers 3] [--json out.json]

退出码：0 全部 ACTIVATE / 3 有未激活或失败（详见输出）/ 2 输入问题
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import site_goods as SG  # noqa: E402  复用 post / get_detail / DEFAULT_SELLER


def fetch_logs_all(days=31):
    """一次拉全量 tiktok 发布日志，返回 {offer_id: [rows...]}。

    注意：SG.post 返回的是整个响应（含 data/success），要取 r["data"]["list"]。
    """
    import datetime
    end = datetime.datetime.now()
    start = end - datetime.timedelta(days=days)
    r = SG.post("/item/distribute/queryLogs", {
        "pageNo": 1, "pageSize": 200,
        "toPlatformList": ["tiktok"], "fromPlatformList": ["1688"],
        "startTime": start.strftime("%Y-%m-%d 00:00:00"),
        "endTime": end.strftime("%Y-%m-%d %H:%M:%S")})
    rows = (r.get("data") or {}).get("list") or []
    out = {}
    for x in rows:
        oid = str(x.get("itemId") or "")
        if oid:
            out.setdefault(oid, []).append(x)
    return out


def latest_of(rows):
    """取最新一条发布记录（CSV 里会同时留着上轮 fail，不能用全文判失败）。"""
    return max(rows, key=lambda x: str(x.get("uptTime") or x.get("createTime") or ""))


def judge_one(oid, log_rows, seller):
    """单个货源的一轮判定。返回 dict（结构对齐 site_goods cmd_status 的 out）。"""
    out = {"offer_id": oid, "log_status": None, "tiktok_id": None,
           "product_status": None, "product_status_name": None,
           "on_sale": False, "error": "", "done": False}
    if not log_rows:
        out["error"] = "logs 无该货源记录（未发布或超 31 天）"
        out["done"] = True          # 无记录 → 无需再等
        return out
    latest = latest_of(log_rows)
    ext = latest.get("extInfo") or {}
    out["log_status"] = latest.get("status")
    out["tiktok_id"] = latest.get("platformItemId") or ext.get("TIKTOK_PRODUCT_ID")
    out["error"] = latest.get("errorMessage") or ""
    if out["log_status"] != "success":
        out["done"] = True          # 已明确 fail → 不再等
        return out
    if not out["tiktok_id"]:
        out["error"] = "发布成功但无 platformItemId"
        out["done"] = True
        return out
    try:
        _, d = SG.get_detail(out["tiktok_id"], seller)
    except SystemExit as e:
        out["error"] = str(e)[:120]
        return out
    out["product_status"] = d.get("productStatus")
    out["product_status_name"] = d.get("productStatusName")
    out["on_sale"] = d.get("productStatus") == "ACTIVATE"
    out["done"] = out["on_sale"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offers", required=True, help="逗号分隔的 1688 货源ID")
    ap.add_argument("--timeout", type=int, default=900, help="总超时秒数（默认900）")
    ap.add_argument("--workers", type=int, default=3, help="详情查询并发（默认3，别超4）")
    ap.add_argument("--interval", type=int, default=20, help="未激活时的轮询间隔秒")
    ap.add_argument("--seller", default="", help="店铺 seller id（默认取日志里的 shopId）")
    ap.add_argument("--json", default="", help="把结果写到该文件")
    a = ap.parse_args()

    offers = [x.strip() for x in a.offers.split(",") if x.strip()]
    if not offers:
        print("[vb] 无 offer", file=sys.stderr)
        return 2

    t_start = time.time()
    deadline = t_start + a.timeout
    res = {o: {"offer_id": o, "on_sale": False, "done": False,
               "product_status": None, "log_status": None, "tiktok_id": None,
               "error": ""} for o in offers}
    rnd = 0
    while True:
        rnd += 1
        todo = [o for o in offers if not res[o].get("done")]
        if not todo:
            break
        # 每轮重拉日志：publish 后 platformItemId 才出现，且 fail 可能随时落库
        try:
            logs = fetch_logs_all()
        except Exception as e:
            print("[vb] 拉日志失败（%s）→ 本轮跳过" % str(e)[:80])
            logs = {}
        print("[vb] 第 %d 轮：待判 %d 品" % (rnd, len(todo)))
        seller_default = a.seller or SG.DEFAULT_SELLER

        def work(oid):
            rows = logs.get(oid) or []
            seller = a.seller
            if not seller and rows:
                seller = str(latest_of(rows).get("shopId") or "") or seller_default
            return judge_one(oid, rows, seller or seller_default)

        with ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:
            for r in ex.map(work, todo):
                res[r["offer_id"]] = r
                flag = "✅ACTIVATE" if r["on_sale"] else (
                    "✗失败" if (r["log_status"] not in (None, "success")) else
                    "…%s" % (r["product_status"] or "无记录"))
                print("   %-14s log=%-8s %s %s"
                      % (r["offer_id"], r["log_status"], flag, (r["error"] or "")[:50]))

        if all(res[o].get("done") for o in offers):
            break
        if time.time() > deadline:
            print("[vb] ⚠ 超时 %ds，仍有 %d 品未激活"
                  % (a.timeout, sum(1 for o in offers if not res[o].get("on_sale"))))
            break
        time.sleep(a.interval)

    n_on = sum(1 for o in offers if res[o]["on_sale"])
    dur = time.time() - t_start
    print("\n[vb] 结果：在售 %d/%d，耗时 %.1fs（品均 %.1fs）"
          % (n_on, len(offers), dur, dur / max(len(offers), 1)))
    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        json.dump({"generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "elapsed_s": round(dur, 1), "results": res},
                  open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("[vb] → %s" % a.json)
    return 0 if n_on == len(offers) else 3


if __name__ == "__main__":
    sys.exit(main())
