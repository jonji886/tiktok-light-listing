#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量预处理编排（把"逐条手工跑 4 个脚本"压成一条命令）

背景：`fix_sku.py` / `set_price.py` / `prep_publish.py` 都是**单商品** CLI。
实测 20 条采集箱商品要跑 60+ 次命令；按每天 3 小时可投入，150 款根本铺不完。
本脚本只做**可确定的三件事**，其余坚决不猜：

  1. 破带重定价 —— 把 26~55 THB（$0.79~1.65）这类亏损价线性映射进价格带
     （`batchOps opsType:2` 公式模式，保留 SKU 之间梯度）
  2. 库存收敛 —— >999999 一律改 1000（不改发布必败，错误码 12052055）
  3. 复检 —— 处理完立刻重跑闸门判定，输出仍阻断的条目

**不做的**（缺数据不猜，转人工）：
  - 售价区间为空的商品（无当前价 → 公式改价无从下手，需 prep_publish --sku-prices）
  - 重量（CSV 里没有真实重量；猜一个数会直接虚报体积重影响头程）
  - 发布（publish 是写操作，且要真实重量/尺寸，必须人工确认）
  - 类目未匹配（CATEGORY_NOT_MATCH 需要类目 ID，得查 case_category_tree.json 人工确认）

用法：
  # 先看（默认 dry-run，不打任何写请求）
  python3 scripts/batch_prep.py --site VN

  # 真跑，但只对点名的 id（写操作必须点名）
  python3 scripts/batch_prep.py --site VN --ids 1500000000000000021,1500000000000000022 --save

  # 指定目标价位（默认落在带下限的 1.10~1.45 倍之间，主打价格优势）
  python3 scripts/batch_prep.py --site VN --lo-mult 1.10 --hi-mult 1.40 --save --ids ...

退出码：0 全部处理完且复检通过；2 处理完但仍有阻断（需人工）；1 参数/数据错误
"""
import argparse
import csv
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)
DEFAULT_CSV = os.path.join(ROOT, "erp_data", "tiktok_list.csv")

from pricing_calc import load_config, merged  # noqa: E402
import price_gate as gate  # noqa: E402

PY = sys.executable or "python3"


def run(script, args, save):
    """调子脚本；dry-run 时去掉 --save"""
    cmd = [PY, os.path.join(HERE, script + ".py")] + list(args)
    if save:
        cmd.append("--save")
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode, out.strip()


def plan_price(cur_lo, cur_hi, cfg, lo_mult, hi_mult):
    """把当前价区间映射到目标区间；返回 (rate, add, target_lo, target_hi)

    公式改价语义：新价 = 当前价 × rate + add（保留 SKU 梯度）
    """
    band = cfg["price_band_usd"]
    rate_local = cfg["rate_to_local"]
    cny_usd = cfg["cny_per_usd"]
    floor_local = band["min"] * cny_usd * rate_local
    ceil_local = band["max"] * cny_usd * rate_local
    t_lo = floor_local * lo_mult
    t_hi = min(floor_local * hi_mult, ceil_local * 0.97)
    if t_hi < t_lo:
        t_hi = t_lo
    if cur_hi is None or cur_hi <= 0:
        cur_hi = cur_lo
    if cur_lo is None or cur_lo <= 0:
        return None
    if abs(cur_hi - cur_lo) < 1e-9:
        rate, add = 1.0, t_lo - cur_lo
    else:
        rate = (t_hi - t_lo) / (cur_hi - cur_lo)
        add = t_lo - cur_lo * rate
    return round(rate, 4), round(add, 2), round(t_lo, 2), round(t_hi, 2)


def main():
    ap = argparse.ArgumentParser(description="批量预处理：重定价 + 收库存 + 复检")
    ap.add_argument("--csv", default=DEFAULT_CSV)
    ap.add_argument("--site", choices=["TH", "VN"], required=True)
    ap.add_argument("--ids", help="点名处理的 ttl_id，逗号分隔（--save 必填）")
    ap.add_argument("--save", action="store_true", help="真正提交写操作")
    ap.add_argument("--lo-mult", type=float, default=1.10,
                    help="目标低价 = 价格带下限 × 该倍数（默认 1.10，主打价格优势）")
    ap.add_argument("--hi-mult", type=float, default=1.45,
                    help="目标高价 = 价格带下限 × 该倍数（默认 1.45，受带上限约束）")
    ap.add_argument("--stock", type=int, default=1000, help="统一收敛到的库存值")
    ap.add_argument("--skip-published", action="store_true",
                    help="跳过状态=1（已发布）的商品，只处理未发布")
    a = ap.parse_args()

    if a.save and not a.ids:
        ap.error("--save 是写操作，必须用 --ids 点名要改的商品（不做批量无差别写）")

    if not os.path.exists(a.csv):
        sys.exit(f"[batch] 找不到 {a.csv}；先跑 erp_client.py ttl-list --region {a.site}")

    cfg_all = load_config()
    cfg = merged(cfg_all, a.site)
    cfg["site"] = a.site

    rows = [r for r in csv.DictReader(open(a.csv, encoding="utf-8-sig"))
            if (r.get("站点") or "").strip() == a.site]
    if a.ids:
        want = {x.strip() for x in a.ids.split(",") if x.strip()}
        rows = [r for r in rows if (r.get("ttl_id") or "").strip() in want]
    if a.skip_published:
        rows = [r for r in rows if str(r.get("状态", "")).strip() != "1"]

    if not rows:
        sys.exit("[batch] 没有匹配的商品")

    mode = "执行（写操作）" if a.save else "DRY-RUN（不提交）"
    print(f"[batch] 站点 {a.site} · {len(rows)} 条 · {mode}\n")

    todo, manual, failed = [], [], []

    for r in rows:
        tid = (r.get("ttl_id") or "").strip()
        title = (r.get("标题") or "")[:34]
        low, high = gate.parse_price_range(r.get("售价区间"))
        stock = gate.fnum(r.get("库存"), 0)
        tags = (r.get("异常标签") or "").strip()
        steps = []

        # ① 破带重定价 —— **只动破带的，已在带内的不动**
        # （否则会把已经卖得掉的合理价砍掉，比如 129~199 THB 本就在带内，不该重定价）
        if low is None:
            manual.append((tid, title, "售价为空，无当前价可算倍数 → 需 prep_publish --sku-prices"))
            continue
        band = cfg["price_band_usd"]
        _lo = band["min"] * cfg["cny_per_usd"] * cfg["rate_to_local"]
        _hi = band["max"] * cfg["cny_per_usd"] * cfg["rate_to_local"]
        _h = high if high else low
        already_in_band = (low >= _lo * 0.98) and (_h <= _hi)
        if already_in_band:
            steps_note = "已在价格带内，跳过重定价"
        else:
            steps_note = None
        pl = plan_price(low, high, cfg, a.lo_mult, a.hi_mult)
        rate, add, t_lo, t_hi = pl
        if already_in_band:
            print(f"  · {tid} {steps_note}（当前 {low:.0f}~{_h:.0f} {cfg['currency']}）")
        else:
            steps.append(("set_price", ["--id", tid, "--rate", str(rate), "--add", str(add)],
                          f"当前 {low:.0f}~{_h:.0f} → 目标 {t_lo:.0f}~{t_hi:.0f} "
                          f"{cfg['currency']}（rate={rate} add={add}）"))

        # ② 库存收敛
        if stock > gate.STOCK_HARD_MAX:
            steps.append(("fix_sku", ["--id", tid, "--stock", str(a.stock)],
                          f"库存 {stock:,.0f} → {a.stock}"))

        # ③ 类目异常不自动改（需类目 ID，猜错会被平台判跨类目）
        warns = []
        if "CATEGORY_NOT_MATCH" in tags:
            warns.append("类目未匹配 → 需人工确定类目 ID 后走 prep_publish")
        if "MISSING_ITEM_DATA" in tags:
            warns.append("tikTokItemModel 为空 → 删除重认领（ttl-del + ttl-claim）")
        if "MISSING_WEIGHT" in tags:
            warns.append("缺重量 → 发布前 prep_publish --weight <真实kg>")

        ok = True
        for script, args, note in steps:
            rc, out = run(script, args, a.save)
            tail = " / ".join(out.splitlines()[-2:]) if out else ""
            print(f"  {'✓' if rc == 0 else '✗'} {tid} {script}: {note}")
            if tail:
                print(f"      {tail}")
            if rc != 0:
                ok = False
                failed.append((tid, script, tail))
        for w in warns:
            print(f"  ! {tid} {w}")
        if ok:
            todo.append(tid)
        print()

    # ④ 复检：重新判一次（dry-run 下价格未变，故只作展示）
    print(f"[batch] 计划处理 {len(todo)} 条 | 转人工 {len(manual)} 条 | 失败 {len(failed)} 条")
    if manual:
        print("\n[需人工]")
        for tid, title, why in manual:
            print(f"  {tid} {title} —— {why}")
    if failed:
        print("\n[失败]")
        for tid, script, tail in failed:
            print(f"  {tid} {script}: {tail}")
    if not a.save:
        print("\n[batch] DRY-RUN 结束。确认无误后加 --save --ids <上面要改的 ttl_id> 执行。")
    else:
        print("\n[batch] 已提交。下一步：重跑 `erp_client.py ttl-list --region "
              f"{a.site}` 刷新 CSV，再跑 `price_gate.py --site {a.site}` 复检，然后才 ttl-publish。")
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
