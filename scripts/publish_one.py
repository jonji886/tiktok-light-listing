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
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import timing  # noqa: E402  耗时账本
import agentlog  # noqa: E402  任务级结构化轨迹（ERP 调用/阶段事件）
import tasklock  # noqa: E402  同一任务禁止双进程并发（state 读改写会互相覆盖）


def _bind_task(st, extra=None):
    """把当前进程的 task_id 绑定为该货源的稳定任务 id：同一商品所有阶段、
    所有重试共用 task_id，重试以 attempt 计数区分；不同商品天然不同 id。"""
    tid = "task_%s" % st["offer_id"]
    agentlog.set_current_task(tid)
    if extra:
        st["task_id"] = tid


def _attempt(st, stage):
    st.setdefault("attempts", {})
    st["attempts"][stage] = st["attempts"].get(stage, 0) + 1
    return st["attempts"][stage]
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
    """跑 scripts/ 下 CLI，返回 (rc, 尾部输出)。子进程耗时单独入账（sub.*）。"""
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable] + cmd, cwd=ROOT,
                           capture_output=True, text=True, timeout=timeout)
        rc = p.returncode
        tail = (p.stdout or "").strip().splitlines()[-8:]
        if rc != 0:
            err = (p.stderr or "").strip().splitlines()[-3:]
            tail += ["STDERR: " + " | ".join(err)]
        return rc, "\n".join(tail)
    finally:
        name = os.path.basename(cmd[0]).replace(".py", "")
        sub = cmd[1] if len(cmd) > 1 and not str(cmd[1]).startswith("-") else ""
        timing.rec("sub.%s%s" % (name, ("." + sub) if sub else ""), t0, time.time(),
                   {"cmd": " ".join(str(c) for c in cmd[1:4])[:120]})


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
    # 错误分类（2026-11-26 事故）：接口对参数/配置非法回 code=500 / For input string
    # 这类**确定性业务错误**——重试和回查都不可能成功，回查轮询每品白烧 ~90s。
    # 仅网络/超时类错误才走回查确认（2026-11-24 盲目重试教训保留）。
    low = out.lower()
    if "for input string" in low or "code=500" in low or "未配置" in out:
        print("[pipe] ✗ claim 业务拒绝（参数/店铺配置非法），**跳过回查轮询**立即失败："
              "查 .env 的 *_SELLER_ID（erp_client.py shops 可取真实值），修完重跑 next")
        return False
    # 超时/异常 → 纪律：先回查 items「已认领」，不盲目重试（2026-11-24 教训）
    print("[pipe] claim 疑似超时/网络，回查采集箱确认是否实际成功…")
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
                    ("template", "--template"), ("dims", "--dims"),
                    ("sku_code", "--sku-code"), ("sku_map_seq", "--sku-map-seq"),
                    ("sku_code_seq", "--sku-code-seq"), ("sku_img_seq", "--sku-img-seq")]:
        if e.get(k):
            cmd += [flag, str(e[k])]
    if e.get("stock"):
        cmd += ["--stock", str(e["stock"])]
    if e.get("weight_g"):
        cmd += ["--weight-g", str(e["weight_g"])]
    if e.get("markup"):
        cmd += ["--markup", str(e["markup"])]
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
    # B4（2026-11-25 实损）：review 里带 pending 就进 apply → image_gate 直接 sys.exit，
    # agent 再去手工回填 evidence，白跑一轮。这里前置断言，报错时给可执行的下一步。
    rv0 = st.get("review_file")
    if rv0 and os.path.exists(rv0):
        rj0 = json.load(open(rv0, encoding="utf-8"))
        pend = [x.get("id") for x in (rj0.get("images") or [])
                if (x.get("verdict") or "pending") == "pending"]
        if pend:
            print("[pipe] ✗ 仍有 %d 张未判定：%s" % (len(pend), pend[:6]))
            print("[pipe]   下一步：python3 scripts/image_review.py --review %s"
                  "（已内置 Aidge 失败自动本地回退）" % rv0)
            return False
    # review.json 的 localized_text 默认是空串，而 apply 要用它重建 description
    # （空则报「重建后 description 为空」，2026-11-25 品1 踩过）。这里直接从 desc_file 回填。
    rv = st.get("review_file")
    if rv and os.path.exists(rv):
        rj = json.load(open(rv, encoding="utf-8"))
        if not (rj.get("localized_text") or "").strip():
            df = (st.get("edit") or {}).get("desc_file")
            if df and os.path.exists(os.path.join(ROOT, df)):
                txt = open(os.path.join(ROOT, df), encoding="utf-8").read().strip()
                rj["localized_text"] = txt
                json.dump(rj, open(rv, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                print("[pipe] review.json 缺 localized_text → 已从 %s 回填 %d 字符" % (df, len(txt)))
    rc, out = run(["scripts/image_gate.py", "apply", "--detail", st["detail_file"],
                   "--review", st["review_file"], "--save"], timeout=300)
    print(out)
    return rc == 0


def stage_guards(st):
    # apply 之后必须**重新拉一次 detail** 再验：verify 读的是 st["detail_file"]，
    # 若还停在 apply 前的快照，会把已删图的 URL 当残留误报 DROPPED_IMAGE_LEAK
    # （2026-11-25 品2 踩过）。ERP 详情有数分钟缓存，故失败后等一轮再试。
    def reload_detail():
        rc, out = run(["scripts/erp_client.py", "ttl-detail", "--id", st["ttl_id"],
                       "--out", out_path(st, "detail.json")])
        return rc == 0

    last = ""
    for attempt in (1, 2, 3):
        reload_detail()
        gcmd = ["scripts/prepublish_guard.py", "--refresh", "--ids", st["ttl_id"]]
        # 干净 cbu01 图确实凑不满 5 张时的**显式降级**（须在 state 里留痕 allow_few_mains）
        if st.get("allow_few_mains"):
            gcmd.append("--allow-few-mains")
        rc1, o1 = run(gcmd)
        print(o1)
        rc2, o2 = run(["scripts/image_gate.py", "verify", "--detail", st["detail_file"],
                       "--review", st["review_file"]])
        print(o2)
        if rc1 == 0 and rc2 == 0:
            return True
        last = o1 + o2
        if "DROPPED_IMAGE_LEAK" in last and attempt < 3:
            print("[pipe] 疑似 ERP 详情缓存未刷（第 %d 次），等 90s 重试…" % attempt)
            time.sleep(90)
            continue
        break
    print("[pipe] ✗ 闸门未过（guard/verify 见上），按提示修复后重跑 next")
    return False


def stage_publish(st):
    # 重发纪律（2026-11-25）：ttl-publish 默认 filterAlreadyPublish 会把「已提交过」的
    # ttl_id 静默跳过（计数不动、logs 无新行）。所以：
    #   ① 先查平台侧是否已 ACTIVATE —— 已在售就不要再发（避免双链接自我竞争）；
    #   ② 未在售但曾有失败记录 → 带 --force 真正重提。
    sj = out_path(st, "site_status_pre.json")
    run(["scripts/site_goods.py", "status", "--offer", st["offer_id"], "--out", sj], timeout=120)
    pre = {}
    if os.path.exists(sj):
        try:
            pre = json.load(open(sj, encoding="utf-8"))
        except Exception:
            pre = {}
    if pre.get("on_sale"):
        print("[pipe] 平台侧已 ACTIVATE（tiktok_id=%s），跳过重发" % pre.get("tiktok_id"))
        return True
    e = st.get("edit", {})
    cmd = ["scripts/erp_client.py", "ttl-publish", "--ids", st["ttl_id"]]
    if e.get("weight_g"):
        cmd += ["--weight", "%.3g" % (float(e["weight_g"]) / 1000)]
    if e.get("dims"):
        cmd += ["--dims", e["dims"]]
    if pre.get("log_status") in ("fail", "success") and not pre.get("on_sale"):
        # 曾提交过（成功但未激活 / 失败）→ 必须 --force 才不会被过滤器吞掉
        cmd += ["--force"]
        print("[pipe] 该货源曾提交（log_status=%s）→ 使用 --force 重提" % pre.get("log_status"))
    rc, out = run(cmd)
    print(out)
    return rc == 0


def _log_status_of(offer_id):
    """直接读 erp_client logs 落盘的 csv，取该货源最新一条的 status / error。

    2026-11-25 教训（马桶刷）：ttl-publish 返回 success=True、wait_for 也报 success，
    但 logs 里最终是 fail（「主图上传失败：参数无效」）。ERP 会先写「任务已接受」
    再写真实结果，只看接口返回值或 wait 首次结果会误判。故 publish 后必须以 csv 为准复核。
    """
    import csv as _csv
    tmp = "erp_data/_run/_logs_check"
    # 注意：run() 内部已拼 sys.executable，cmd[0] 必须是脚本路径，不能再写 "python3"
    rc, _ = run(["scripts/erp_client.py", "logs", "--months", "1", "--out", tmp])
    if rc != 0:
        return None, ""
    f = os.path.join(tmp, "tiktok_logs.csv")
    if not os.path.exists(f):
        return None, ""
    best, st, err = "", None, ""
    with open(f, encoding="utf-8-sig") as fh:
        for row in _csv.reader(fh):
            if not row or str(row[0]) != str(offer_id):
                continue
            t = row[3] if len(row) > 3 else ""
            if t >= best:
                best = t
                st = (row[1] or "").strip().lower()
                err = row[5] if len(row) > 5 else ""
    return st, err


def stage_wait(st):
    rc, out = run(["scripts/wait_for.py", "publish", "--match", st["offer_id"],
                   "--timeout", "420"], timeout=480)
    print(out)
    if rc != 0:
        return False
    # 二次复核：等真实结果落库，以 logs csv 为唯一口径
    for _ in range(3):
        time.sleep(6)
        status, err = _log_status_of(st["offer_id"])
        if status == "fail":
            print("[pipe] ✗ logs 复核为 fail：%s" % (err or "(无 errorMessage)"))
            st["publish_error"] = err
            return False
        if status == "success":
            print("[pipe] ✔ logs 复核 success")
            return True
    print("[pipe] ⚠ logs 复核未取到终态，继续（verify4 会再判 productStatus）")
    return True


def stage_verify4(st):
    """四路验证。platform_on_sale 只看 productStatus==ACTIVATE（2026-11-25 重写）。

    正确判定链：logs(按 itemId 精确匹配, status 精确值) → platformItemId(TikTok 商品ID)
    → queryPlatformItemDetail → productStatus。
    踩过的坑：① 拿 itemCenterNumIid(1574...) 去查详情必报 does not exist；
             ② 拿 itemAlias/子串在站点商品列表里搜 → 新发布商品还没进该库，永远搜不到；
             ③ 发布 success 但状态仍是 INITIAL（待平台审核），需等待转 ACTIVATE。
    """
    ok = {}
    sj = out_path(st, "site_status.json")
    deadline = time.time() + int(os.environ.get("VERIFY_ON_SALE_TIMEOUT", "900"))
    verdict = {}
    while True:
        rc, out = run(["scripts/site_goods.py", "status", "--offer", st["offer_id"],
                       "--out", sj], timeout=120)
        print(out)
        verdict = {}
        if os.path.exists(sj):
            try:
                verdict = json.load(open(sj, encoding="utf-8"))
            except Exception:
                verdict = {}
        if verdict.get("on_sale"):
            break
        if verdict.get("log_status") == "fail":
            break
        if time.time() > deadline:
            print("[pipe] ⚠ 等待 %ds 仍在 %s（未激活），停在此阶段稍后重跑 next 复查"
                  % (int(os.environ.get("VERIFY_ON_SALE_TIMEOUT", "900")),
                     verdict.get("product_status") or "无记录"))
            break
        time.sleep(30)

    # logs 只看**最新一条**：CSV 里会同时留着上轮的 fail 记录，
    # 用全文正则判「有无失败」会把重发成功的商品误判为失败（2026-11-25 品2 踩过）
    ok["logs_success"] = verdict.get("log_status") == "success"
    ok["platform_on_sale"] = bool(verdict.get("on_sale"))
    st["verify"] = {"tiktok_id": verdict.get("tiktok_id"),
                    "product_status": verdict.get("product_status"),
                    "product_status_name": verdict.get("product_status_name"),
                    "log_status": verdict.get("log_status"),
                    "on_sale": bool(verdict.get("on_sale")),
                    "error": verdict.get("error", "")}

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
    for k in ("category_id", "keep_prefix", "attr_names", "sku_map", "sku_map_seq",
              "desc_file", "mains_file", "title", "source_prices", "template", "dims",
              "stock", "weight_g", "title_hint", "sku_code", "sku_code_seq", "sku_img_seq", "markup"):
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
        agentlog.event("task_done", stage=stage, status="succeeded",
                       product_id=st.get("offer_id"), tool="publish_one")
        return 0
    if stage not in HANDLERS:
        print("[pipe] 未知阶段 %s" % stage)
        return 6
    # 进程锁：两个执行者同时 next 同一任务 → 后者立即失败而不是静默覆盖 state
    try:
        lk = tasklock.acquire("pipeline_%s" % st["offer_id"])
    except tasklock.LockedError as e:
        print("[pipe] ⛔ %s" % e)
        return 7
    try:
        return _do_next_locked(st, stage)
    finally:
        lk.release()


def _do_next_locked(st, stage):
    _bind_task(st)
    attempt = _attempt(st, stage)
    agentlog.event("stage_start", stage=stage, status="start", attempt=attempt,
                   product_id=st["offer_id"], tool="publish_one")
    print("\n[pipe] === %s (attempt %d) ===" % (stage, attempt))
    oid = st.get("offer_id") or ""
    prev_parent = os.environ.get("TT_PARENT")
    os.environ["TT_PARENT"] = "pipeline.%s" % stage      # 子进程挂到本阶段下
    t0 = time.time()
    try:
        r = HANDLERS[stage](st)
    finally:
        timing.rec("pipeline.%s" % stage, t0, time.time(), {"offer_id": oid, "stage": stage},
                   parent=prev_parent or "pipeline")
        if prev_parent is None:
            os.environ.pop("TT_PARENT", None)
        else:
            os.environ["TT_PARENT"] = prev_parent
    if r == "hold":
        agentlog.event("stage_hold", stage=stage, status="hold", attempt=attempt,
                       product_id=oid, tool="publish_one")
        save(st)
        return 5
    if r is not True:
        print("[pipe] ✗ 阶段 %s 失败，状态保留（修完重跑 next）" % stage)
        agentlog.event("stage_failed", stage=stage, status="failed", attempt=attempt,
                       product_id=oid, tool="publish_one",
                       error={"error_type": "unknown_error", "retryable": True,
                              "human_intervention": False},
                       meta={"note": "详情见 stdout 与 trace 上一事件；修完重跑 next"})
        save(st)
        return 6
    agentlog.event("stage_ok", stage=stage, status="success", attempt=attempt,
                   product_id=oid, tool="publish_one",
                   duration_ms=(time.time() - t0) * 1000)
    idx = STAGES.index(stage)
    nxt = STAGES[idx + 1]
    # 检查点闸：edit_save 必须已 approve(edit)；apply 若 CP2 触发过必须已 approve(images)
    if nxt == "edit_save" and "edit" not in st["approved"]:
        st["stage"] = "edit_dry"
        save(st)
        print("[pipe] CP1 未确认——approve --which edit 后再 next")
        return 5
    # 2026-11-25 修：先前把清零放在 CP2 拦截之后，导致「自动判完」也永久卡在 CP2
    if stage == "review_img" and r is True:
        st["cp2_needed"] = False  # 全自动判完无需 CP2
    if nxt == "apply" and st.get("cp2_needed") and "images" not in st["approved"]:
        st["stage"] = "review_img"
        save(st)
        return 5
    if stage == "edit_save":
        nxt = "detail"  # save 后重读 detail 刷新异常标签
    elif stage == "detail" and "edit_save" in st.get("done_stages", []):
        nxt = "scan"  # 重读完成后不回 edit_dry（2026-11-25 修：原顺序会死循环）
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
    # 人工授权与具体任务绑定：记入该 task_id 轨迹（CP1/CP2 审计线索）
    _bind_task(st)
    agentlog.event("human_approve", stage=st["stage"], status="success",
                   product_id=st["offer_id"], tool="publish_one",
                   meta={"which": a.which})
    print("[pipe] approved: %s → stage=%s（跑 next 继续）" % (a.which, st["stage"]))
    return 0


def cmd_set(a):
    """补/改 edit 参数（不重跑已过的阶段）。

    存在意义：edit 参数里的 sku_map 必须等 detail 阶段跑完、prep_one 生成后才有，
    但 start 时就要落参数 → 原来的顺序无解（2026-11-25 加）。
    """
    st = load(a.offer_id)
    n = 0
    for k in ("category_id", "keep_prefix", "attr_names", "sku_map", "sku_map_seq",
              "desc_file", "mains_file", "title", "source_prices", "template", "dims",
              "title_hint", "sku_code", "sku_code_seq", "sku_img_seq", "markup", "stock", "weight_g"):
        v = getattr(a, k, None)
        if v not in (None, ""):
            st["edit"][k] = v
            n += 1
    save(st)
    print("[pipe] set %d 个 edit 参数 → %s（stage=%s 不变）"
          % (n, json.dumps(st["edit"], ensure_ascii=False), st["stage"]))
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
    for k in ("category-id", "keep-prefix", "attr-names", "sku-map", "sku-map-seq",
              "desc-file", "mains-file", "title", "source-prices", "template", "dims",
              "title-hint", "sku-code", "sku-code-seq", "sku-img-seq", "markup"):
        s.add_argument("--" + k, dest=k.replace("-", "_"), default="")
    s.add_argument("--stock", default="")
    s.add_argument("--weight-g", dest="weight_g", default="")
    s.set_defaults(fn=cmd_start)
    n = sub.add_parser("next"); n.add_argument("--offer-id", required=True); n.set_defaults(fn=cmd_next)
    p = sub.add_parser("approve"); p.add_argument("--offer-id", required=True)
    p.add_argument("--which", choices=["edit", "images"], required=True); p.set_defaults(fn=cmd_approve)
    q = sub.add_parser("status"); q.add_argument("--offer-id", required=True); q.set_defaults(fn=cmd_status)
    t = sub.add_parser("set", help="补/改 edit 参数（sku_map 等 prep 产物）")
    t.add_argument("--offer-id", required=True)
    for k in ("category-id", "keep-prefix", "attr-names", "sku-map", "sku-map-seq",
              "desc-file", "mains-file", "title", "source-prices", "template", "dims",
              "title-hint", "sku-code", "sku-code-seq", "sku-img-seq", "markup", "stock", "weight-g"):
        t.add_argument("--" + k, dest=k.replace("-", "_"), default="")
    t.set_defaults(fn=cmd_set)
    a = ap.parse_args()
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()
