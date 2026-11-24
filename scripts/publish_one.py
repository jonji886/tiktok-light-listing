#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""publish_one.py — 单品全链路流水线编排器（2026-11-25 P0-1）

治的痛：2026-11-24 批次里「collect→claim→detail→edit→scan→review→apply→三闸→
publish→等待→四路验证」这条固定链路，5 个品走了 5×15≈75 次 agent 轮次。
编排逻辑不该占用对话轮次——本脚本把它压成状态机，agent 只在**两个人工检查点**介入：
  CP1 edit 确认（dry-run 摘要 + SKU/价/图 diff，人点头才 --save）
  CP2 图片确认（image_review probe 自动判后，仅存疑图需目视，人点头才 apply）
其余全自动，一条 `--next` 推进一步，状态存盘可断点续跑。

用法：
  # 开跑（采集+认领，认领超时自动回查"已认领"，不盲目重试）
  python3 scripts/publish_one.py start --offer-id 529647564532 --site MY

  # 推进（自动判断当前该做哪步并执行；到检查点停下打印待办）
  python3 scripts/publish_one.py next --offer-id 529647564532

  # edit 参数在 start 时给全（与 ttl_edit 同名透传），流水线自己跑 dry-run：
  python3 scripts/publish_one.py start --offer-id X --site MY \
      --category-id 700665 --keep-prefix "..." --attr-names "颜色=Warna" \
      --sku-map f.json --desc-file f.txt --mains-file f.txt --title "..." \
      --stock 300 --weight-g 120 --dims 25x20x4

  # 检查点确认后续跑
  python3 scripts/publish_one.py approve --offer-id X --which edit
  python3 scripts/publish_one.py approve --offer-id X --which images
  python3 scripts/publish_one.py next --offer-id X     # 继续

  # 查状态
  python3 scripts/publish_one.py status --offer-id X

阶段流：collect → claim → detail → edit_dry(CP1) → edit_save → scan → review_img(CP2)
        → apply → guards → publish → wait → verify4 → done
退出码：0 阶段推进成功 / 5 停在检查点等人 / 6 阶段失败（原因已打印，状态保留可重试）
"""
import argparse
import datetime
import glob
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RUNDIR = os.path.join(ROOT, "erp_data", "_run", "pipeline")
STAGES = ["collect", "claim", "detail", "edit_dry", "edit_save", "scan",
          "review_img", "apply", "guards", "publish", "wait", "verify4", "done"]


def spath(oid):
    return os.path.join(RUNDIR, "%s.json" % oid)


def load(oid):
    p = spath(oid)
    if not os.path.exists(p):
        sys.exit("[pipe] 无状态文件 %s（先 start）" % p)
    return json.load(open(p, encoding="utf-8"))


def save(st):
    os.makedirs(RUNDIR, exist_ok=True)
    st["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    json.dump(st, open(spath(st["offer_id"]), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


def run(cmd, timeout=240):
    """跑 scripts/ 下 CLI，返回 (rc, 尾部输出)。"""
    p = subprocess.run([sys.executable] + cmd, cwd=ROOT,
                       capture_output=True, text=True, timeout=timeout)
    tail = (p.stdout or "").strip().splitlines()[-8:]
    if p.returncode != 0:
        tail += ["STDERR: " + (p.stderr or "").strip().splitlines()[-3:]]
    return p.returncode, "\n".join(tail)


def out_path(st, name):
    d = os.path.join(ROOT, "erp_data", "_run", "pipeline", st["offer_id"])
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


# ─────────────────────────── 各阶段 ───────────────────────────
def stage_collect(st):
    rc, out = run(["scripts/erp_client.py", "collect", "--ids", st["offer_id"],
                   "--out", out_path(st, "collect.json")])
    print(out)
    if rc != 0:
        return False
    st["ttl_id"] = None  # 待 claim 后查
    return True


def stage_claim(st):
    rc, out = run(["scripts/erp_client.py", "ttl-claim", "--ids", st["offer_id"],
                   "--region", st["site"], "--out", out_path(st, "claim.json")])
    print(out)
    if rc == 0 and "success" in out.lower():
        return True
    # 超时/异常 → 纪律：先回查 items「已认领」，不盲目重试（2026-11-24 教训）
    print("[pipe] claim 返回异常，回查采集箱确认是否实际成功…")
    rc2, out2 = run(["scripts/wait_for.py", "claim", "--offer-id", st["offer_id"],
                     "--region", st["site"], "--timeout", "60"])
    print(out2)
    return rc2 == 0


def find_ttl_id(st):
    rc, out = run(["scripts/erp_client.py", "ttl-list", "--region", st["site"],
                   "--out", out_path(st, "ttl_list.json")])
    if rc != 0:
        print(out)
        return False
    csvp = glob.glob(os.path.join(ROOT, out_path(st, "ttl_list.json")) + "/*.csv") \
        or [out_path(st, "ttl_list.json") + "/tiktok_list.csv"]
    import csv as _csv
    for f in csvp:
        if not os.path.exists(f):
            continue
        for r in _csv.DictReader(open(f, encoding="utf-8-sig")):
            if st["offer_id"] in (r.get("货源ID") or ""):
                st["ttl_id"] = r["ttl_id"]
                print("[pipe] ttl_id=%s 状态=%s 异常标签=%s"
                      % (r["ttl_id"], r.get("状态"), r.get("异常标签")))
                return True
    print("[pipe] ttl-list 未找到货源 %s（认领可能未生效）" % st["offer_id"])
    return False


def stage_detail(st):
    if not st.get("ttl_id") and not find_ttl_id(st):
        return False
    rc, out = run(["scripts/erp_client.py", "ttl-detail", "--id", st["ttl_id"],
                   "--out", out_path(st, "detail.json")])
    print(out)
    st["detail_file"] = out_path(st, "detail.json") + "/ttl_detail_%s.json" % st["ttl_id"]
    return rc == 0 and os.path.exists(st["detail_file"])


def edit_args(st, save=False):
    e = st.get("edit", {})
    cmd = ["scripts/ttl_edit.py", "--ttl-id", st["ttl_id"], "--site", st["site"]]
    for k, flag in [("category_id", "--category-id"), ("keep_prefix", "--keep-prefix"),
                    ("attr_names", "--attr-names"), ("sku_map", "--sku-map"),
                    ("desc_file", "--desc-file"), ("mains_file", "--mains-file"),
                    ("title", "--title"), ("source_prices", "--source-prices"),
                    ("template", "--template"), ("dims", "--dims")]:
        if e.get(k):
            cmd += [flag, str(e[k])]
    if e.get("stock"):
        cmd += ["--stock", str(e["stock"])]
    if e.get("weight_g"):
        cmd += ["--weight-g", str(e["weight_g"])]
    if e.get("drop_zero", True):
        cmd += ["--drop-zero"]
    if save:
        cmd += ["--save"]
    return cmd


def stage_edit_dry(st):
    rc, out = run(edit_args(st, save=False))
    print(out)
    st["edit_dry_rc"] = rc
    if rc != 0:
        print("[pipe] ✗ dry-run 校验不过（ttl_edit 已打印可选项提示，修参数后重跑 next）")
        return False
    print("[pipe] ⏸ CP1：dry-run 通过。核对上方 SKU/价/图摘要后："
          "python3 scripts/publish_one.py approve --offer-id %s --which edit" % st["offer_id"])
    return "hold"


def stage_edit_save(st):
    rc, out = run(edit_args(st, save=True))
    print(out)
    if rc != 0:
        return False
    # save 后必须重读 detail（异常标签/新 payload），scan 吃最新文件
    return stage_detail(st)


def stage_scan(st):
    rc, out = run(["scripts/image_gate.py", "scan", "--detail", st["detail_file"]],
                  timeout=400)
    print(out)
    return rc == 0


def stage_review_img(st):
    rv = os.path.join(ROOT, "erp_data", "image_gate", st["ttl_id"], "review.json")
    if not os.path.exists(rv):
        print("[pipe] 无 review.json（scan 未产出？）")
        return False
    rc, out = run(["scripts/image_review.py", "--review", rv], timeout=600)
    print(out)
    st["review_file"] = rv
    if rc == 0:  # 全自动判完
        return True
    if rc == 1:
        st["cp2_needed"] = True
        print("[pipe] ⏸ CP2：有存疑图待目视（sheet 见上），判完回填 verdict 后："
              "python3 scripts/publish_one.py approve --offer-id %s --which images" % st["offer_id"])
        return "hold"
    return False


def stage_apply(st):
    rc, out = run(["scripts/image_gate.py", "apply", "--detail", st["detail_file"],
                   "--review", st["review_file"], "--save"], timeout=300)
    print(out)
    return rc == 0


def stage_guards(st):
    rc1, o1 = run(["scripts/prepublish_guard.py", "--refresh", "--ids", st["ttl_id"]])
    print(o1)
    rc2, o2 = run(["scripts/image_gate.py", "verify", "--detail", st["detail_file"],
                   "--review", st["review_file"]])
    print(o2)
    if rc1 != 0 or rc2 != 0:
        print("[pipe] ✗ 闸门未过（guard rc=%d verify rc=%d），按提示修复后重跑 next" % (rc1, rc2))
        return False
    return True


def stage_publish(st):
    e = st.get("edit", {})
    cmd = ["scripts/erp_client.py", "ttl-publish", "--ids", st["ttl_id"]]
    if e.get("weight_g"):
        cmd += ["--weight", "%.3g" % (float(e["weight_g"]) / 1000)]
    if e.get("dims"):
        cmd += ["--dims", e["dims"]]
    rc, out = run(cmd)
    print(out)
    return rc == 0


def stage_wait(st):
    rc, out = run(["scripts/wait_for.py", "publish", "--match", st["offer_id"],
                   "--timeout", "240"], timeout=300)
    print(out)
    return rc == 0


def stage_verify4(st):
    ok = {}
    rc, out = run(["scripts/erp_client.py", "logs", "--months", "1",
                   "--out", out_path(st, "logs.json")])
    logs_txt = ""
    for f in glob.glob(out_path(st, "logs.json") + "/*.csv") + \
             glob.glob(out_path(st, "logs.json") + "*/*.csv"):
        logs_txt += open(f, encoding="utf-8-sig", errors="ignore").read()
    ok["logs_success"] = rc == 0 and st["offer_id"] in logs_txt and \
        not re.search(r"%s[^\n]*(失败|fail)" % st["offer_id"], logs_txt, re.I)
    rc, out = run(["scripts/site_goods.py", "list", "--platform", "tiktok",
                   "--page-size", "100", "--limit", "300",
                   "--out", out_path(st, "site_goods.json")], timeout=300)
    txt = ""
    if os.path.exists(out_path(st, "site_goods.json")):
        txt = open(out_path(st, "site_goods.json"), encoding="utf-8", errors="ignore").read()
    ok["platform_on_sale"] = st["offer_id"] in txt or bool(
        st.get("edit", {}).get("title_hint") and st["edit"]["title_hint"] in txt)
    rc, out = run(["scripts/erp_client.py", "quota", "--region", st["site"]])
    print(out)
    print("[pipe] 四路验证: %s" % json.dumps(ok, ensure_ascii=False))
    miss = [k for k, v in ok.items() if not v]
    if miss:
        print("[pipe] ⚠ 未命中: %s —— platform_on_sale 有翻页/延迟可能，人工复核一次再 done" % miss)
        return False
    return True


HANDLERS = {"collect": stage_collect, "claim": stage_claim, "detail": stage_detail,
            "edit_dry": stage_edit_dry, "edit_save": stage_edit_save, "scan": stage_scan,
            "review_img": stage_review_img, "apply": stage_apply, "guards": stage_guards,
            "publish": stage_publish, "wait": stage_wait, "verify4": stage_verify4}


def cmd_start(a):
    os.makedirs(RUNDIR, exist_ok=True)
    if os.path.exists(spath(a.offer_id)) and not a.force:
        sys.exit("[pipe] 状态已存在（%s）。重跑用 next；确要重来 --force" % spath(a.offer_id))
    # 防呆（2026-11-25 冒烟实测踩坑）：货源已在售时重复 start 会采出第二份草稿→双链接
    rc, out = run(["scripts/site_goods.py", "list", "--platform", "tiktok",
                   "--page-size", "100", "--limit", "300",
                   "--out", os.path.join(RUNDIR, "_on_sale_check.json")], timeout=300)
    chk = os.path.join(RUNDIR, "_on_sale_check.json")
    if os.path.exists(chk) and a.offer_id in open(chk, encoding="utf-8", errors="ignore").read():
        sys.exit("[pipe] ⛔ 货源 %s 已在售（平台侧命中）——重复 start 会产生第二份草稿=自我竞争。"
                 "确为新站点/换款请核对后 --force" % a.offer_id)
    st = {"offer_id": a.offer_id, "site": a.site, "stage": "collect",
          "approved": [], "edit": {}, "started": datetime.datetime.now().isoformat()}
    for k in ("category_id", "keep_prefix", "attr_names", "sku_map", "desc_file",
              "mains_file", "title", "source_prices", "template", "dims", "stock",
              "weight_g", "title_hint"):
        v = getattr(a, k, None)
        if v is not None and v != "":
            st["edit"][k] = v
    if a.no_edit_args:
        print("[pipe] ⚠ 未带 edit 参数——edit_dry 前必须先用 set 补：见 --help")
    save(st)
    print("[pipe] start %s → stage=collect" % a.offer_id)
    return do_next(st)


def do_next(st):
    """一次 next 只执行当前阶段（简单可靠、可断点续跑）。"""
    stage = st["stage"]
    if stage == "done":
        print("[pipe] ✅ 全链路完成（在售验证归档）")
        return 0
    if stage not in HANDLERS:
        print("[pipe] 未知阶段 %s" % stage)
        return 6
    print("\n[pipe] === %s ===" % stage)
    r = HANDLERS[stage](st)
    if r == "hold":
        save(st)
        return 5
    if r is not True:
        print("[pipe] ✗ 阶段 %s 失败，状态保留（修完重跑 next）" % stage)
        save(st)
        return 6
    idx = STAGES.index(stage)
    nxt = STAGES[idx + 1]
    # 检查点闸：edit_save 必须已 approve(edit)；apply 若 CP2 触发过必须已 approve(images)
    if nxt == "edit_save" and "edit" not in st["approved"]:
        st["stage"] = "edit_dry"
        save(st)
        print("[pipe] CP1 未确认——approve --which edit 后再 next")
        return 5
    if nxt == "apply" and st.get("cp2_needed") and "images" not in st["approved"]:
        st["stage"] = "review_img"
        save(st)
        return 5
    if stage == "review_img" and r is True:
        st["cp2_needed"] = False  # 全自动判完无需 CP2
    if stage == "edit_save":
        nxt = "detail"  # save 后重读 detail 刷新异常标签
    st.setdefault("done_stages", []).append(stage)
    st["stage"] = nxt
    save(st)
    print("[pipe] ✔ %s 完成 → 下一阶段: %s" % (stage, nxt))
    return 0


def cmd_next(a):
    st = load(a.offer_id)
    return do_next(st)


def cmd_approve(a):
    st = load(a.offer_id)
    if a.which == "edit":
        if st["stage"] != "edit_dry" or not st.get("edit_dry_rc") == 0:
            print("[pipe] ⚠ 当前不在待确认 edit 状态（stage=%s）" % st["stage"])
        st["approved"].append("edit")
        st["stage"] = "edit_save"
    elif a.which == "images":
        st["approved"].append("images")
        st["stage"] = "apply"
    save(st)
    print("[pipe] approved: %s → stage=%s（跑 next 继续）" % (a.which, st["stage"]))
    return 0


def cmd_status(a):
    st = load(a.offer_id)
    print(json.dumps({k: st[k] for k in
                      ("offer_id", "site", "ttl_id", "stage", "approved", "updated")
                      if k in st}, ensure_ascii=False, indent=1))
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--offer-id", required=True)
    s.add_argument("--site", default="MY")
    s.add_argument("--force", action="store_true")
    s.add_argument("--no-edit-args", action="store_true")
    for k in ("category-id", "keep-prefix", "attr-names", "sku-map", "desc-file",
              "mains-file", "title", "source-prices", "template", "dims", "title-hint"):
        s.add_argument("--" + k, dest=k.replace("-", "_"), default="")
    s.add_argument("--stock", default="")
    s.add_argument("--weight-g", dest="weight_g", default="")
    s.set_defaults(fn=cmd_start)
    n = sub.add_parser("next"); n.add_argument("--offer-id", required=True); n.set_defaults(fn=cmd_next)
    p = sub.add_parser("approve"); p.add_argument("--offer-id", required=True)
    p.add_argument("--which", choices=["edit", "images"], required=True); p.set_defaults(fn=cmd_approve)
    q = sub.add_parser("status"); q.add_argument("--offer-id", required=True); q.set_defaults(fn=cmd_status)
    a = ap.parse_args()
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()
