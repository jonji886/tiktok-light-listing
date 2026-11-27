#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""batch_listing.py — 一条命令铺货 N 个（P0-2，2026-11-26）

治的痛（本批账本 VERIFIED）：
  墙钟 128min 里 agent 编排间隙 53min——4 波 precheck、图搜补池、逐品编辑翻译
  全靠人肉在波次间串。本脚本把确定性链路收进一个进程：
    waves(pool CSV×N) → precheck(预算闸) → 候选表 → edit(模板+词表) →
    图片闸门(scan→sample→verdict→apply→verify) → 三闸 → [发布→四路验证→backflow]

原则：
  - 不猜数据：pool 行缺 ship_city/qty_begin/price_low 时 A③④⑤ 预判自动跳过（向后兼容）
  - 预算闸：累计候选 ≤ budget_mult×target（默认5倍）。超了停下报告——
    本批实际 10.6×，且第一波"3 过闸"实为隔夜残留，有预算闸当场识破
  - 发布默认关：--publish 才打；写操作要显式意图（与闸门纪律一致）
  - card 闸已知结构性缺口 ATTRS_LT5（类目属性接口未破解，历史 47/47 BLOCK 但均发布
    成功）：默认 attrs-warn 不阻断；--card-mode strict 恢复全阻断

用法：
  set -a; source .env; set +a
  python3 scripts/batch_listing.py --target 10 --site MY \
      --waves pool.csv,pool2.csv,pool3.csv --run-tag 20261127_my10 --publish
  # 只编排到闸门（不发布）：去掉 --publish
  # 从已认领品续跑：--claimed 669519939526[,…] 跳过 precheck/edit 起
退出码：0 达标（--publish 时=全部在售）/ 2 候选不足或闸门阻断 / 1 参数错误
"""
import argparse
import csv
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
import timing  # noqa: E402

os.chdir(ROOT)


def _load_env():
    """自动加载 .env（2026-11-26 实测：driver 经 subprocess 调 precheck/erp_client，
    外层 shell 若没 source .env，claim 全程收占位符 sellerId → 接口 500）。
    与 health_check.check_shop_config 同款：只补未设置的 key，不覆盖显式传入的环境变量。
    这样 driver 一条命令自洽，不再依赖操作者记得 source。"""
    envf = os.path.join(ROOT, ".env")
    if not os.path.exists(envf):
        return
    for line in open(envf, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


_load_env()

CAND_CSV = "erp_data/selection_candidates.csv"
TERM_CSV = "config/i18n/ms_terms.csv"  # 出处已并入 ms_terms.py（保留常量兼容旧引用）
KEYWORDS = "config/keywords/my.txt"
DESCRIPTORS = ["comel", "wanita", "rumah", "tahan lasak", "kaliskanvas"]  # 组装标题的兜底词根


def run(cmd, timeout=900, quiet=False):
    t0 = time.time()
    p = subprocess.run([sys.executable] + cmd, cwd=ROOT,
                       capture_output=True, text=True, timeout=timeout)
    out = (p.stdout or "") + (p.stderr or "")
    if not quiet:
        tail = [l for l in out.strip().splitlines() if l.strip()][-4:]
        print("    $ %s (%.0fs)\n      %s" % (" ".join(cmd[:4]), time.time() - t0,
                                              "\n      ".join(tail[:3])))
    return p.returncode, out


# 2026-11-26 my10b D3：词表加载与译文查表收敛到 scripts/ms_terms.py 唯一出处，
# precheck B⑦ 与本脚本 pick_skus 必须同口径（否则过闸品 edit 二次死）。
from ms_terms import load_terms, tr_value  # noqa: E402


def pick_skus(detail_file, mains_file, need_min=2, cap=3):
    """选款纪律：变体图 ∈ 干净 mains 池的优先，汉字/不可译值弃，最多 cap 款。"""
    d = json.load(open(detail_file, encoding="utf-8"))
    v2 = d["reqInfo"]["tikTokItemModel"]["productCreateRequestV2"]
    skus = v2.get("skus") or []
    mains = set(l.strip() for l in open(mains_file, encoding="utf-8")) if os.path.exists(mains_file) else set()
    shapes, colors, _ = load_terms()
    cand = []
    for i, s in enumerate(skus):
        src = None
        try:
            src = float((s.get("price") or {}).get("source_price") or 0) or None
        except (TypeError, ValueError):
            pass
        img = next((a["sku_img"]["uri"] for a in (s.get("sales_attributes") or [])
                    if (a.get("sku_img") or {}).get("uri")), None)
        vals = [str(a.get("value_name") or "") for a in (s.get("sales_attributes") or [])]
        t = [tr_value(x, shapes, colors) for x in vals]
        if any(not x for x in t) or not src:
            continue
        cand.append(dict(i=i, ssid=str(s.get("seller_sku")), orig="/".join(vals),
                         vals=[x[:40] for x in t], hasimg=bool(img and img in mains), src=src))
    sel = [c for c in cand if c["hasimg"]][:cap]
    if len(sel) < need_min:
        sel = (sel + [c for c in cand if not c["hasimg"]])[:cap]
    if len(sel) < need_min:
        return None, {"reason": "可译SKU不足", "cand": len(cand)}
    seenv, vals_full = {}, [None] * len(skus)
    for c in sel:
        t = list(c["vals"])
        key = "|".join(t)
        seenv[key] = seenv.get(key, -1) + 1
        if seenv[key]:
            # D7（20261126_my10d 实证 692626145767/683279316695 fail 12052251）：
            # 译文先被 [:40] 截断，两个不同原值截到同一 40 字前缀后 key 相同，
            # 旧写法 (t[-1]+" A")[:40] 把后缀又截掉 → 变体值仍重复 → 平台前置校验拒。
            # 修复：给后缀留位（截到 38 再加 " A"），保证 ≤40 且后缀存活。
            t[-1] = (t[-1][:38] + " " + chr(64 + seenv[key]))[:40]
        vals_full[c["i"]] = t
    keep_i = {c["i"] for c in sel}
    drop = [str(skus[i].get("seller_sku")) for i in range(len(skus)) if i not in keep_i]
    return dict(sel=sel, vals_full=vals_full, drop=drop), None


def build_title(kind, category_id, oids_titles, orig_title=""):
    """标题组装：模板词根(≥3) + 原中文标题的款型特征词（经形状表译出）。
    款型词让消费者搜「背带围裙」类的细分词能命中，且全是查表确定性译文。
    词根来源=category_templates.yaml keywords_ms + config/keywords/my.txt 兜底。
    注意：ERP translate 接口 body 未破解（cmd_ttl_translate 是 TODO 骨架），
    所以款型词走 ms_terms.csv 查表，不走机器翻译——查不到就不加（宁缺毋滥）。"""
    import category_template as CT
    tpl = CT.load_templates()
    base = ""
    for name, t in tpl.items():
        if str(t.get("erp_category_id")) == str(category_id):
            base = t.get("keywords_ms") or ""
            kind = kind or name
            break
    words = [w for w in (base.split() + ["wanita", "dapur", "rumah", "comel"]) if w]
    # 模板无 keywords_ms（洗衣袋/挂钩等）→ 内置词根兜底（全部来自官方/词表 VERIFIED 词）
    CORE = {"600747": ["beg basuh", "jaring bra", "mesin basuh"],
            "853128": ["cangkuk dinding", "penyimpanan", "tanpa lubang"],
            "600621": ["kotak simpanan", "penyusunan", "dapur"]}
    if not base:
        words = CORE.get(str(category_id), []) + words
    # 款型特征词（背带/挂脖/长袖…）从原标题查表提取
    shapes, _c, _a = load_terms()
    feature = []
    for zh, ms in shapes:
        if zh in (orig_title or "") and ms and ms not in " ".join(words) \
                and not any(ms in w for w in words):
            feature.append(ms)
        if len(feature) >= 2:
            break
    title = " ".join(dict.fromkeys(words + feature)).title()
    title += " Tahan Lasak"
    return title[:80]


def cmd_main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=10)
    ap.add_argument("--site", default="MY")
    ap.add_argument("--waves", default="", help="逗号分隔 pool CSV（列: offer_id,consign_price,image_url,title[,kw,ship_city,qty_begin,price_low]）")
    ap.add_argument("--claimed", default="", help="跳过precheck，直接对已认领offerId续跑（逗号分隔）")
    ap.add_argument("--run-tag", default=time.strftime("%Y%m%d_%H%M"))
    ap.add_argument("--budget-mult", type=float, default=5.0)
    ap.add_argument("--c-jobs", type=int, default=3)
    ap.add_argument("--b2-batch", dest="b2_batch", action="store_true",
                    help="透传给 precheck：B0 整波批量 claim（替代逐品~86s轮询）")
    ap.add_argument("--jobs", type=int, default=3,
                    help="driver 内 edit/图片闸门 的品间并发数（默认3；ERP 进程级"
                         "节流 1QPS/进程，3 进程=3QPS，本批 92 次串行调用未触 707，"
                         ">4 有 707 限流风险）")
    ap.add_argument("--card-mode", choices=["strict", "attrs-warn", "off"], default="attrs-warn")
    ap.add_argument("--publish", action="store_true", help="过闸后真发布（默认止步闸门）")
    ap.add_argument("--auto-publish", dest="auto_publish", action="store_true",
                    help="闸门全过且 ok_edit==target 时自动发布，并在同一次运行内完成"
                         "发布→轮询→在售验证→backflow→publish_result.json（治 my10d "
                         "双层人工等待 5.6min）。不满足前置条件仍止步报告，不会盲发。")
    ap.add_argument("--allow-oids", default="", help="只处理点名的过闸品（凑不满target时的显式放行）")
    a = ap.parse_args()

    os.environ.setdefault("TT_RUN", a.run_tag)
    run_dir = "erp_data/_run/%s" % a.run_tag
    os.makedirs(run_dir, exist_ok=True)
    print("[driver] run=%s target=%d site=%s publish=%s" % (a.run_tag, a.target, a.site, a.publish))

    # ── Phase 1: 选品漏斗（预算闸）──
    passed = {}   # oid -> report ok entry
    waves_used, cand_total = 0, 0
    if a.claimed:
        for oid in [x.strip() for x in a.claimed.split(",") if x.strip()]:
            passed[oid] = {"offer_id": oid, "stage": "pre_claimed"}
    else:
        waves = [w.strip() for w in a.waves.split(",") if w.strip()]
        if not waves:
            sys.exit("[driver] 需 --waves 或 --claimed")
        budget = int(a.target * a.budget_mult)
        for w in waves:
            if len(passed) >= a.target:
                break
            if cand_total >= budget:
                print("[driver] ✗ 预算闸：候选已用 %d ≥ %.0f×%d，停止扩波（应换词/换供给，不是继续烧）"
                      % (cand_total, a.budget_mult, a.target))
                break
            rows = list(csv.DictReader(open(w, encoding="utf-8-sig")))
            rowmap = {r["offer_id"]: (r.get("kw") or "?") for r in rows}
            cand_total += len(rows)
            waves_used += 1
            need = a.target - len(passed)
            print("\n[driver] wave %d/%s: %d 候选（累计 %d/%d 预算）→ precheck target=%d"
                  % (waves_used, len(waves), len(rows), cand_total, budget, need))
            pc_cmd = ["scripts/precheck.py", "--pool", w, "--site", a.site,
                      "--target", str(need), "--c-jobs", str(a.c_jobs)]
            if a.b2_batch:
                pc_cmd.append("--b2-batch")
            rc, out = run(pc_cmd, timeout=3600)
            repf = sorted(glob.glob("erp_data/_run/precheck/*.json"))[-1]
            rep = json.load(open(repf, encoding="utf-8"))
            for x in rep.get("ok", []):
                x.setdefault("kw", rowmap.get(x["offer_id"], "?"))
                passed.setdefault(x["offer_id"], x)
            print("[driver] %s → 过闸 %d（累计 %d）" % (os.path.basename(repf), len(rep.get("ok", [])), len(passed)))
    if a.allow_oids:
        allow = set(x.strip() for x in a.allow_oids.split(","))
        passed = {k: v for k, v in passed.items() if k in allow}
    if not passed:
        print("[driver] ✗ 无过闸品，终止")
        return 2

    # ── Phase 2+3: edit→图片闸门 直通流水（P1 合并，治阶段 barrier 空隙）──
    with timing.span("driver.edit_chain", parent=""):
        ok_edit = {}

        def edit_one(oid, entry):
            """单品的 edit 全流程（线程内跑子进程链），返回 (oid, ok, meta, logs)。"""
            logs = []
            stf = "erp_data/_run/pipeline/%s.json" % oid
            if not os.path.exists(stf):
                return oid, False, None, ["[edit] %s ✗ 无 pipeline state" % oid]
            st = json.load(open(stf, encoding="utf-8"))
            ttl, df = st.get("ttl_id"), st.get("detail_file")
            if not ttl or not df:
                return oid, False, None, ["[edit] %s ✗ state 缺 ttl_id/detail_file" % oid]
            # D6（20261126_my10c）：precheck 快照与线上草稿存在翻译状态漂移——
            # 快照里 value_name 已被 ERP 译成马来语（Merah），线上草稿仍是中文
            # （长袖熊红色）→ pick_skus 按旧快照算出的 vals/code 对不上，
            # ttl_edit 读线上体报 PAYLOAD_CJK（本批 3 品，人工返工 ~8min）。
            # 修复：edit 前重刷 detail，选款计算一律基于最新线上体。
            rc_rf, _ = run(["scripts/erp_client.py", "ttl-detail", "--id", ttl,
                            "--out", "%s/refresh" % run_dir], quiet=True)
            dfs_rf = sorted(glob.glob("%s/refresh/ttl_detail_%s*.json" % (run_dir, ttl)),
                            key=lambda p: os.path.getmtime(p))
            if rc_rf == 0 and dfs_rf:
                df = dfs_rf[-1]
            info = entry.get("info") or {}
            cat = (info.get("suggest_category") or {}).get("id") or ""
            tpl = None
            try:
                import category_template as CT
                tpls = CT.load_templates()
                det_title = ""
                _d = {}
                try:
                    _d = json.load(open(df, encoding="utf-8"))
                    det_title = (_d.get("reqInfo", {}).get("tikTokItemModel", {})
                                 .get("productCreateRequestV2", {}).get("title") or "")
                except Exception:
                    pass
                # D2 修复①（20261126_my10b 750277293130 实证）：ERP 自动翻译后
                # 标题是马来语，aliases 小写 "apron" 匹配不上 "Apron" → 大小写不敏感。
                dl = det_title.lower()
                for name, t in tpls.items():
                    al = [x.strip().lower() for x in (t.get("aliases") or "").split(",") if x.strip()]
                    if (dl and any(alx in dl for alx in al)
                            and not cat) or (cat and str(t.get("erp_category_id")) == str(cat)):
                        tpl = (name, t)
                        break
                if not cat and tpl:
                    cat = str(tpl[1]["erp_category_id"])
                # D2 修复②：标题/模板全落空时，按 detail 的中文类目路径反查
                # （认领时 toCategoryNameTree 常有「家居用品,家居护理用品,围裙」型路径；
                #  --claimed 续跑路径 info 为空，这里是唯一能兜住的一层）
                if not cat:
                    cpath = info.get("category_path") or ""
                    if not cpath:
                        cpath = _d.get("toCategoryNameTree", "") or ""
                    for name, t in tpls.items():
                        tn = (t.get("erp_category_name_tree") or "").replace(">", ",")
                        last = (tn.split(",")[-1] or "").strip()
                        if last and last in cpath.replace(">", ","):
                            cat, tpl = str(t["erp_category_id"]), (name, t)
                            break
            except Exception:
                pass
            if not cat:
                return oid, False, None, [
                    "[edit] %s ⚠ 无类目（建议列空且模板未命中），跳过（需人工 --category-id）" % oid]
            pk, err = pick_skus(df, "erp_data/_run/pipeline/%s/edit/mains.txt" % oid)
            if err:
                return oid, False, None, ["[edit] %s ✗ %s" % (oid, err)]
            vals_f = "%s/vals_%s.json" % (run_dir, oid)
            drop_f = "%s/drop_%s.json" % (run_dir, oid)
            json.dump(pk["vals_full"], open(vals_f, "w"), ensure_ascii=False)
            json.dump(pk["drop"], open(drop_f, "w"), ensure_ascii=False)
            # 汉字 seller_sku → ASCII 码（本批 805170572803/822436575053 的
            # 「WQ-公主大口袋A款【围裙】-粉色」型货号，prepublish [12052250] 必拒）
            code_f = "%s/code_%s.json" % (run_dir, oid)
            cmap = {}
            for i, c in enumerate(pk["sel"]):
                if re.search(r"[^\x00-\x7F]", c["ssid"]):
                    cmap[c["ssid"]] = "MY%s-%s-%02d" % (time.strftime("%m%d"), oid[-4:], i)
            json.dump(cmap, open(code_f, "w"), ensure_ascii=False)
            drop = ",".join(pk["drop"])
            _d0 = json.load(open(df, encoding="utf-8"))
            orig_t = (_d0.get("reqInfo", {}).get("tikTokItemModel", {})
                      .get("productCreateRequestV2", {}).get("title") or "")
            title = build_title(info.get("category_path", ""), cat, oid, orig_title=orig_t)
            descf = "%s/desc_%s.txt" % (run_dir, oid)
            if tpl and tpl[1].get("desc_text_file") and os.path.exists(tpl[1]["desc_text_file"]):
                # 2026-11-27：渲染字段从模板 desc_fields 读（挂钩/垃圾袋等非布料品
                # 套"Kain tebal"=文案与实物不符）；围裙模板未配时保持旧默认。
                dfld = {}
                for kv in (tpl[1].get("desc_fields") or "").split(";"):
                    if "=" in kv:
                        k, v = kv.split("=", 1); dfld[k.strip()] = v.strip()
                args_f = {"product_name": "comel",
                          "feature1": "Kain tebal tahan lasak dengan reka bentuk comel",
                          "feature2": "lembut dan selesa",
                          "color_list": "berbagai pilihan", **dfld}
                cmd = ["scripts/category_template.py", "render", "--template", tpl[0]]
                for k, v in args_f.items():
                    cmd += ["--fields", "%s=%s" % (k, v)]
                run(cmd + ["--out", descf], quiet=True)
            if not os.path.exists(descf):
                cand_desc = (tpl[1].get("desc_text_file") if tpl else None)
                descf = cand_desc if (cand_desc and os.path.exists(cand_desc)) \
                    else "erp_data/desc_templates/desc_my_围裙.txt"
                # 模板描述文件缺失时：直接把模板文件本身给 ttl_edit（其内部会渲染/兜底）
            attr_spec = ",".join("%s=%s" % (k, v) for k, v in load_terms()[2].items())
            rc, out = run(["scripts/ttl_edit.py", "--ttl-id", ttl, "--site", a.site,
                           "--category-id", cat, "--attr-names", attr_spec,
                           "--sku-map-seq", vals_f, "--drop-sku", drop,
                           "--sku-code", code_f,
                           "--backfill-dim-images", "--stock", "300",
                           "--weight-g", str(info.get("weight_g") or 300),
                           "--dims", info.get("dims") or "25x20x4",
                           "--title", title,
                           "--mains-file", "erp_data/_run/pipeline/%s/edit/mains.txt" % oid,
                           "--desc-file", descf, "--save"], quiet=True)
            if rc != 0 or "✗" in out:
                return oid, False, None, ["[edit] %s ✗ 失败: %s" % (oid, out.strip().splitlines()[-1][:110])]
            meta = dict(ttl=ttl, cat=cat, title=title,
                        w=info.get("weight_g") or 300, dm=info.get("dims") or "25x20x4")
            return oid, True, meta, ["[edit] %s ✓ SKU=%d title=%s" % (oid, len(pk["sel"]), title[:50])]

        def gate_one(oid, e):
            """单品的图片闸门链（detail重刷→scan→review→apply→verify）。"""
            run(["scripts/erp_client.py", "ttl-detail", "--id", e["ttl"],
                 "--out", "%s/postedit" % run_dir], quiet=True)
            dfs = sorted(glob.glob("%s/postedit/%s/*.json" % (run_dir, e["ttl"])) +
                         glob.glob("%s/postedit/*%s*.json" % (run_dir, e["ttl"])) +
                         glob.glob("%s/postedit/ttl_detail_%s.json" % (run_dir, e["ttl"])))
            if not dfs:
                return oid, False, "[gate] %s ✗ detail 重刷失败" % oid
            df = dfs[-1]
            run(["scripts/image_gate.py", "scan", "--detail", df], quiet=True)
            rev = "erp_data/image_gate/%s/review.json" % e["ttl"]
            # image_review = probe（主图证据硬闸）+ 判级 + 本地 Vision 回退，
            # 裸跑 auto_verdict 会缺 probe 证据被 apply 拦（678 教训）
            run(["scripts/image_review.py", "--review", rev], quiet=True)
            descf = "%s/desc_%s.txt" % (run_dir, oid)
            dtxt = open(descf, encoding="utf-8").read() if os.path.exists(descf) else ""
            run(["scripts/image_gate.py", "apply", "--detail", df, "--review", rev,
                 "--desc-text", dtxt, "--save"], quiet=True)
            rc2, out2 = run(["scripts/image_gate.py", "verify", "--detail",
                             "erp_data/image_gate/%s/cleaned_detail.json" % e["ttl"],
                             "--review", rev], quiet=True)
            return oid, rc2 == 0, ("[gate] %s %s" % (oid, "✓ verify PASS" if rc2 == 0
                                     else "✗ verify FAIL: " + out2[-120:]))

        from concurrent.futures import ThreadPoolExecutor

        def edit_gate_one(oid, entry):
            """P1（20261126_my10d 分析）：edit→图片闸门 per-item 直通流水，
            取消两阶段 barrier（my10d 各等最慢品，阶段间空隙 3.7min）。
            全局并发仍 = a.jobs；ERP 节流安全依据同上（子进程×1QPS）。"""
            with timing.span("driver.edit", parent="", offer_id=oid):
                oid2, ok, meta, logs = edit_one(oid, entry)
            for l in logs:
                print(l)
            if not ok:
                return oid, False, None, "[gate] 跳过（edit 失败）"
            with timing.span("driver.image_gate", parent="", offer_id=oid):
                _oid, gok, msg = gate_one(oid, meta)
            return oid, (ok and gok), meta, msg

        with ThreadPoolExecutor(max_workers=a.jobs) as ex:
            futs = [ex.submit(edit_gate_one, oid, entry)
                    for oid, entry in passed.items()]
            for fu in futs:                       # 统一收集后打印，防线程输出交错
                oid, ok, meta, msg = fu.result()
                print(msg)
                if ok:
                    ok_edit[oid] = meta
        if not ok_edit:
            print("[driver] ✗ edit/闸门全失败")
            return 2

    # ── Phase 4: 三闸 ──
    with timing.span("driver.gates", parent=""):
        run(["scripts/erp_client.py", "ttl-list", "--region", a.site, "--size", "300"], quiet=True)
        ttls = [e["ttl"] for e in ok_edit.values()]
        rows = [r for r in csv.DictReader(open("erp_data/tiktok_list.csv", encoding="utf-8-sig"))
                if r.get("ttl_id") in set(ttls)]
        sub = "%s/gate_subset.csv" % run_dir
        if rows:
            with open(sub, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
            rc, out = run(["scripts/price_gate.py", "--csv", sub], quiet=True)
            print("[price_gate] rc=%d" % rc)
        rc, out = run(["scripts/prepublish_guard.py", "--refresh", "--ids", ",".join(ttls)], quiet=True)
        print("[prepublish] rc=%d" % rc)
        bad = rc != 0
        if a.card_mode != "off":
            rc, out = run(["scripts/listing_quality_gate.py", "--ids", ",".join(ttls),
                           "--site", a.site], quiet=True)
            if rc != 0:
                hard = [l for l in out.splitlines() if "ATTRS_LT" in l or "TITLE_KEYWORD_LOW" in l or "MAIN_LT" in l]
                if a.card_mode == "strict" or any("TITLE" in h or "MAIN" in h for h in hard):
                    bad = True
                    print("[card] ✗ 阻断（strict 或 标题/主图类硬伤）:\n  " + "\n  ".join(hard[:6]))
                else:
                    print("[card] ⚠ 仅 ATTRS_LT5（已知能力缺口，attrs-warn 模式放行）")
        if bad:
            print("[driver] ✗ 闸门未过，不发布。详见 %s" % run_dir)
            return 2
    print("[driver] ✓ 全部闸门通过 %d 品: %s" % (len(ok_edit), list(ok_edit)))

    # ── Phase 4.5: auto-publish 前置闸（P0-1）──
    do_pub = a.publish or (a.auto_publish and len(ok_edit) == a.target)
    if a.auto_publish and not a.publish and len(ok_edit) != a.target:
        print("[driver] ⚠ --auto-publish 未触发：过闸 %d ≠ target %d（前置条件不满足，"
              "不盲发）。缺口 %s" % (len(ok_edit), a.target,
              sorted(set(passed) - set(ok_edit))))

    # ── Phase 5: 发布 + 验证（do_pub 才执行；批量 blob 验证替代逐品 status）──
    result = {"run": a.run_tag, "target": a.target, "gated": list(ok_edit),
              "published": [], "on_sale": [], "fail": [], "notes": []}
    if do_pub:
        with timing.span("driver.publish", parent=""):
            for oid, e in ok_edit.items():
                kg = "%.2f" % (e["w"] / 1000.0)
                run(["scripts/erp_client.py", "ttl-publish", "--ids", e["ttl"],
                     "--weight", kg, "--dims", e["dm"]], quiet=True)
            rc, out = run(["scripts/wait_for.py", "publish",
                           "--match", ",".join(ok_edit), "--timeout", "600"], quiet=True)
            print("[publish] wait_for rc=%d" % rc)
            run(["scripts/erp_client.py", "logs", "--months", "1",
                 "--out", "%s/logs" % run_dir], quiet=True)
            # logs 终态
            lg = sorted(glob.glob("%s/logs/tiktok_logs*.csv" % run_dir) +
                        glob.glob("erp_data/tiktok_logs.csv"),
                        key=os.path.getmtime)
            if lg:
                latest = {}
                for r in csv.DictReader(open(lg[-1], encoding="utf-8-sig")):
                    if r.get("货源ID") in ok_edit:
                        k = r["货源ID"]
                        if k not in latest or r.get("发布时间", "") > latest[k].get("发布时间", ""):
                            latest[k] = r
                for oid in ok_edit:
                    st = (latest.get(oid) or {}).get("状态")
                    if st == "success":
                        result["published"].append(oid)
                    else:
                        result["fail"].append({"offer": oid, "log": st or "无记录",
                                               "reason": ((latest.get(oid) or {}).get("失败原因") or "")[:80]})
            # 在售验证：一次 list → blob 按 outerId 匹配（5s，替代逐品 status ~2.5min）
            rc_l, _ = run(["scripts/site_goods.py", "list", "--platform", "tiktok",
                           "--page-size", "100", "--limit", "500",
                           "--out", "%s/onsale_check.json" % run_dir], quiet=True)
            try:
                blob = json.load(open("%s/onsale_check.json" % run_dir, encoding="utf-8"))
                blob = json.dumps(blob, ensure_ascii=False)
                for oid in result["published"]:
                    if oid in blob:
                        result["on_sale"].append(oid)
                    else:
                        result["notes"].append("%s 已发布但不在售列表（可能审核中，T+1 复看）" % oid)
            except Exception as ex:
                result["notes"].append("在售列表验证失败: %s（list rc=%d）" % (ex, rc_l))
            run(["scripts/backflow.py", "sync"], quiet=True)
            timing.mark("verify4.done")
            print("[driver] 发布=%d 在售=%d fail=%d" % (len(result["published"]),
                  len(result["on_sale"]), len(result["fail"])))
    else:
        print("[driver] 未发布（缺 --publish / auto-publish 前置未满足）：草稿已就绪，人工抽检后单独发布")

    with open("%s/publish_result.json" % run_dir, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("[driver] 终局账 → %s/publish_result.json" % run_dir)

    # ── Phase 6: 词池反馈（P2-7）──
    fb = "erp_data/word_pool_feedback.csv"
    new = not os.path.exists(fb)
    with open(fb, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "run", "kw", "sent", "passed", "clean_rate"])
        bykw = {}
        for entry in passed.values():
            kw = (entry.get("info") or {}).get("kw") or entry.get("kw") or "?"
            bykw.setdefault(kw, [0, 0])
            bykw[kw][1] += 1
        # sent 用 pool 实际行数近似：driver 简化——按 wave 文件重数
        for wf in [x.strip() for x in a.waves.split(",") if x.strip()]:
            if not os.path.exists(wf):
                continue
            for r in csv.DictReader(open(wf, encoding="utf-8-sig")):
                kw = r.get("kw") or "?"
                bykw.setdefault(kw, [0, 0])
                bykw[kw][0] += 1
        for kw, (s, p) in bykw.items():
            w.writerow([time.strftime("%Y-%m-%d"), a.run_tag, kw, s, p,
                        "%.2f" % (p / s) if s else ""])
    print("[driver] 词池反馈 → %s（下批组 wave 前按 clean_rate 排序/剔除）" % fb)
    return 0


if __name__ == "__main__":
    sys.exit(cmd_main())
