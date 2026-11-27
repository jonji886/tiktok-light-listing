#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""batch_run.py — 批次驱动：先发完所有品，再统一验在售（A1 落地）

治的痛：publish_one 是「单品状态机」，逐品跑就是「边发边等」——
5 品各等 62.7s 平台审核，串行 375.9s（本轮占发布链路 57%）。
平台审核是同一段 SLA，品与品之间没有依赖，完全可以叠着等。

本脚本只做编排，不改 publish_one 的阶段逻辑：
  Phase 1 制造：逐品推进状态机，推进到 stage=verify4 就**停下**（不发不验）
  Phase 2 验证：全部到位后调 verify_batch.py 统一轮询一次
  Phase 3 归档：把在售结果回写每个品的 state，stage 置 done

检查点纪律不变：
  CP1（edit 干跑）：默认自动放行并打印摘要；--no-auto-cp1 则停在检查点等人
  CP2（图片存疑）：先跑 image_review.py（已内置 Aidge 失败本地回退），
                  仍有存疑图才停；--auto-cp2 跳过人工确认须谨慎（默认关）

用法：
  set -a; source .env; set +a
  python3 scripts/batch_run.py --offers 946425355590,1036443288103,970510652008 \
      --run-tag 20261125_p5t [--no-auto-cp1] [--verify-timeout 900]

退出码：0 全部在售 / 5 停在人工检查点 / 6 有品制造阶段失败
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import timing  # noqa: E402

PIPE = os.path.join(ROOT, "erp_data", "_run", "pipeline")


def spath(oid):
    return os.path.join(PIPE, "%s.json" % oid)


def load(oid):
    return json.load(open(spath(oid), encoding="utf-8"))


def save(oid, st):
    st["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    json.dump(st, open(spath(oid), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def call(args, timeout=1800):
    p = subprocess.run([sys.executable] + args, cwd=ROOT,
                       capture_output=True, text=True, timeout=timeout)
    lines = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()
    return p.returncode, lines


def next_once(oid):
    return call(["scripts/publish_one.py", "next", "--offer-id", oid])


def approve(oid, which):
    return call(["scripts/publish_one.py", "approve", "--offer-id", oid, "--which", which])[0]


def fix_cp2(oid):
    """CP2 有存疑图：先跑 image_review（内含 Aidge 失败→本地 OCR 回退），再放行。"""
    st = load(oid)
    rv = os.path.join(ROOT, "erp_data", "image_gate", str(st.get("ttl_id")), "review.json")
    if not os.path.exists(rv):
        print("[batch] ✗ %s 无 review.json，无法自动判图" % oid)
        return False
    rc, out = call(["scripts/image_review.py", "--review", rv], timeout=900)
    print("\n".join(out[-8:]))
    if st.get("review_file") is None:
        st["review_file"] = rv
        save(oid, st)
    if rc == 0:
        approve(oid, "images")
        print("[batch] %s CP2 全自动判完 → 已放行" % oid)
        return True
    # rc=1 仍有存疑 → 交人看（sheet 已打印在上方）
    print("[batch] ⏸ %s 仍有存疑图待目视，sheet 见上（判完回填 verdict 后重跑本脚本）" % oid)
    return False


def drive_to_verify(oid, auto_cp1):
    """推进单品到 stage=verify4（即发布已提交、待验在售），返回 (ok, reason)。"""
    st = load(oid)
    if st.get("stage") in ("verify4", "done"):
        return True, st.get("stage")
    # 防呆：未成功干跑就进了 edit_save → 退回干跑（保留 CP1 校验，不偷跳）
    if st.get("stage") == "edit_save" and st.get("edit_dry_rc") != 0:
        st["stage"] = "edit_dry"
        save(oid, st)
    for _ in range(24):
        rc, out = next_once(oid)
        print("\n".join(out[-10:]))
        st = load(oid)
        stage = st.get("stage")
        if stage in ("verify4", "done"):
            return True, stage
        if rc == 0:
            continue
        if rc == 5:
            if stage == "edit_dry":
                if not auto_cp1:
                    return False, "CP1 待人工确认"
                approve(oid, "edit")
                print("[batch] %s CP1 自动放行（干跑摘要见上）" % oid)
                continue
            if stage == "review_img":
                if fix_cp2(oid):
                    continue
                return False, "CP2 存疑图待目视"
            return False, "停在 %s" % stage
        return False, "阶段失败(%s)" % stage
    return False, "推进次数超限"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offers", required=True, help="逗号分隔 offer_id")
    ap.add_argument("--run-tag", default="", help="计时账本标签（默认用环境变量 TT_RUN）")
    ap.add_argument("--no-auto-cp1", action="store_true", help="CP1 不自动放行")
    ap.add_argument("--auto-cp2", action="store_true", help="CP2 存疑也不再停（危险）")
    ap.add_argument("--verify-timeout", type=int, default=900)
    ap.add_argument("--verify-workers", type=int, default=3)
    a = ap.parse_args()

    offers = [x.strip() for x in a.offers.split(",") if x.strip()]
    if a.run_tag:
        os.environ.setdefault("TT_RUN", a.run_tag)

    t_all = time.time()
    state = {o: {"ok": False, "reason": "", "stage": ""} for o in offers}

    print("=" * 70)
    print("[batch] Phase 1 制造：%d 个品推进到「已提交待审核」" % len(offers))
    print("=" * 70)
    t1 = time.time()
    for oid in offers:
        print("\n########## %s ##########" % oid)
        ok, why = drive_to_verify(oid, not a.no_auto_cp1)
        st = load(oid)
        state[oid] = {"ok": ok, "reason": why, "stage": st.get("stage")}
        print("[batch] %s → %s（%s）" % (oid, "✔ 到位" if ok else "✗ 停下", why))
    t_make = time.time() - t1

    ready = [o for o in offers if state[o]["ok"]]
    print("\n[batch] Phase 1 完成：%d/%d 到位，耗时 %.1fs（%.1f min）"
          % (len(ready), len(offers), t_make, t_make / 60))

    if not ready:
        print("[batch] 无品可验证，退出")
        return 6

    print("\n" + "=" * 70)
    print("[batch] Phase 2 批量验证：%d 个品统一轮询（不再逐品串行等）" % len(ready))
    print("=" * 70)
    t2 = time.time()
    vj = os.path.join(ROOT, "erp_data", "_run", "%s_verify_batch.json"
                      % (os.environ.get("TT_RUN") or time.strftime("%Y%m%d_%H%M")))
    rc, out = call(["scripts/verify_batch.py", "--offers", ",".join(ready),
                    "--timeout", str(a.verify_timeout),
                    "--workers", str(a.verify_workers), "--json", vj],
                   timeout=a.verify_timeout + 300)
    print("\n".join(out))
    t_ver = time.time() - t2
    timing.rec("batch.verify_all", t2, time.time(),
               {"n": len(ready), "elapsed": round(t_ver, 1)}, parent="batch")

    vr = {}
    if os.path.exists(vj):
        try:
            vr = (json.load(open(vj, encoding="utf-8")) or {}).get("results") or {}
        except Exception:
            vr = {}

    print("\n" + "=" * 70)
    print("[batch] Phase 3 归档")
    print("=" * 70)
    n_on = 0
    for oid in ready:
        r = vr.get(oid) or {}
        st = load(oid)
        st["verify"] = {"tiktok_id": r.get("tiktok_id"),
                        "product_status": r.get("product_status"),
                        "product_status_name": r.get("product_status_name"),
                        "log_status": r.get("log_status"),
                        "on_sale": bool(r.get("on_sale")),
                        "error": r.get("error", ""),
                        "verified_by": "verify_batch"}
        if r.get("on_sale"):
            st["stage"] = "done"
            n_on += 1
        elif r.get("log_status") not in (None, "success"):
            st["stage"] = "verify4"          # 失败留在此阶段，便于单独复查
            st["publish_error"] = r.get("error", "")
        else:
            st["stage"] = "verify4"          # 未激活：稍后重跑 verify_batch 复查
        save(oid, st)
        print("  %-14s %-10s tiktok_id=%s"
              % (oid, "ACTIVATE" if r.get("on_sale") else (r.get("product_status") or "无记录"),
                 r.get("tiktok_id") or "-"))

    if rc == 0:
        call(["scripts/erp_client.py", "quota", "--region",
              (load(ready[0]).get("site") or "MY")], timeout=120)

    dur = time.time() - t_all
    timing.rec("batch.total", t_all, time.time(),
               {"n": len(offers), "on_sale": n_on,
                "make_s": round(t_make, 1), "verify_s": round(t_ver, 1)}, parent="")
    print("\n[batch] ══ 汇总 ══")
    print("  在售 %d/%d | Phase1 制造 %.1f min | Phase2 验证 %.1f min | 总计 %.1f min"
          % (n_on, len(offers), t_make / 60, t_ver / 60, dur / 60))
    miss = [o for o in offers if not (vr.get(o) or {}).get("on_sale")]
    if miss:
        print("  未在售：%s" % ", ".join(miss))
    return 0 if n_on == len(offers) else (5 if any(not state[o]["ok"] for o in offers) else 3)


if __name__ == "__main__":
    sys.exit(main())
