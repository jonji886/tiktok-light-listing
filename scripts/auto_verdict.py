#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto_verdict.py — 用本地 macOS Vision OCR 给 image_gate review.json 批量预填 verdict。

口径（与 img_grade.py 四级一致，只做**保守方向**判定）：
  cjk == 0 且 有文字   → 待人工（可能是纯英文/数字标注，交人看）
  cjk == 0 且 无文字   → L3_keep
  cjk  > 0            → L0_drop（中文一律不进图；宁可删）

不做 L1_inpaint / L2_localize 判定——那两类必须人看后才给。
输出：直接改写 review.json 的 verdict/note，打印统计与「待人工」清单。
退出码：0 全部判定完成；3 有 pending 需人工（脚本不静默放过）。

性能（2026-11-25 实测）：默认走批量 OCR（一个 swift 进程处理整批图），
  8 张 10.75s vs 串行 54s ≈ 5x；40 张收益更大。线程并发无效（swift 冷启动互相拖累），
  故 --batch-size 是「每组图数」而非线程数。批量失败自动回退单图 OCR。

用法：
  python3 scripts/auto_verdict.py --review erp_data/image_gate/<tid>/review.json [--dry] [--batch-size 40]
  python3 scripts/auto_verdict.py --review ... --no-batch   # 回退旧串行模式
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import img_grade as G  # noqa: E402  复用 dl/ocr/ocr_batch/grade_from_ocr，避免两套实现

UNIT_RE = re.compile(r"(?i)\b(cm|mm|m|inch|in|size|saiz|ukuran|pcs|pc|set|keping|unit)\b")


def _only_units(txt):
    """纯尺寸/单位标注（无营销词）→ 可保留。去掉数字、单位、分隔符后应无残余字母。"""
    s = UNIT_RE.sub(" ", txt or "")
    s = re.sub(r"[0-9\s\.x×*·:/\-+()\[\]~approx.]+", " ", s, flags=re.I)
    return not re.search(r"[A-Za-z]", s)


def _chan_rank(im):
    return {"main": 0, "sku": 1, "desc": 2}.get((im.get("channels") or ["desc"])[0], 2)


def sample_mode(rp, n=8, need=5):
    """抽样预判（P1-3，2026-11-26）：只判按 main>sku>desc 排序的前 n 张，
    用于 precheck C 段的全册 OCR 前置淘汰——**只淘汰、不放行**：
      clean ≥ need        → exit 0（抽样见脏率低，值得全册精判）
      clean <  need 且 n 张判满 → 大概率 C⑦ 死（本批 C⑦ 淘汰品 16 个里
                              15 个整册干净 ≤4；高置信直接预杀，省 ~38s/品全册判级）
      下载失败率 > 50%     → exit 2
    不写回 review.json（后续全册 auto_verdict 正常重判）。
    ⚠ 局限：desc 尾部藏干净图的品会被预杀——本批数据 desc-only 干净源
      极少见；若下批出现「抽样杀了但全册其实≥5」的实证，把 n 调大即可。
    """
    d = json.load(open(rp, encoding="utf-8"))
    imgs = sorted(d.get("images", []), key=_chan_rank)
    take = imgs[:n]
    if len(imgs) <= n:
        return 3  # 图本就少，交给全册模式
    base = Path(rp).parent
    clean, failed = 0, 0
    paths = {}
    for im in take:
        lf = im.get("local_file") or ""
        p = base / lf if lf and (base / lf).exists() else base / (im["id"] + ".jpg")
        if not p.exists() and not G.dl(im["url"], p):
            failed += 1
            continue
        paths[im["id"]] = p
    if failed > len(take) // 2:
        print("[sample] ✗ 抽样下载失败 %d/%d → 网络/CDN 问题，本波稍后重跑" % (failed, len(take)))
        return 2
    ocr_map = G.ocr_batch([str(p) for p in paths.values()], max_per_call=n)
    # B：抽样判过的前 8 张存 OCR 缓存，后续 stop-after/全册判命中免重跑（同图同字节）。
    import judge_cache as JC
    for im in take:
        p = paths.get(im["id"])
        if p and ocr_map.get(str(p)):
            JC.save_ocr(p, ocr_map[str(p)], (im.get("content_sha256") or ""))
    for im in take:
        p = paths.get(im["id"])
        if not p:
            continue
        try:
            od = ocr_map.get(str(p)) or G.ocr(p)
            g = G.grade_from_ocr(od, p)
            cjk = g.get("cjk") or 0
            grade = g.get("grade")
            txt = (g.get("text") or "").replace("\n", " ")
            # 与主循环 L3_keep 完全同口径（含 C 级仅尺寸标注的例外），
            # 否则抽样比全册更严 → 误杀
            if cjk == 0 and grade in ("A", "B"):
                clean += 1
            elif cjk == 0 and grade not in ("D",) and _only_units(txt):
                clean += 1
        except Exception:
            pass
    print("[sample] 前 %d 张(按main>sku>desc) 干净=%d / 需=%d" % (len(take), clean, need))
    if clean >= need:
        print("[sample] → 值得全册精判")
        return 0
    print("[sample] → 预杀（整册凑 ≥%d 张干净概率低，省一次全册OCR ~38s）" % need)
    return 5


def stop_after_mode(rp, need=5, batch_size=40, no_cache=False, max_judge=20):
    """C 通道分级（2026-11-26）：按 main>sku>desc 顺序判级，凑够 need 张
    **唯一**干净图（content_sha256 去重，与 pick_mains 同口径）即停。

    与 sample_mode 的区别：sample 只淘汰不放行、不写回；本模式**写回**判定，
    剩余图标 `unjudged`（不是 pending），供 precheck pick_mains 直接凑 5 张，
    跳过剩余 sku/desc 的全册 OCR。

    第一性原理：C 段的需求是「凑够 5 张干净主图」，不是「判完所有图」。
    fast OCR 实测中文召回 0/6（全漏），故仍用 accurate，只减张数不减精度。

    退出码：0 凑够 / 5 全册判完仍不足（交给现有 C⑦ 淘汰） / 2 下载失败过多。
    """
    import judge_cache as JC
    d = json.load(open(rp, encoding="utf-8"))
    base = Path(rp).parent
    imgs = sorted(d.get("images", []), key=_chan_rank)
    if not imgs:
        return 2
    sha_of = {im["id"]: (im.get("content_sha256") or "") for im in imgs}
    paths, failed = {}, 0
    for im in imgs:
        p = base / (im.get("local_file") or (im["id"] + ".jpg"))
        if p.exists() or G.dl(im["url"], p):
            paths[im["id"]] = p
        else:
            failed += 1
            im["verdict"] = "L0_drop"
            im["note"] = "下载失败 → 保守整图删"
    if failed > len(imgs) // 2:
        print("[stop] 下载失败 %d/%d → 网络问题，本波稍后重跑" % (failed, len(imgs)))
        return 2

    # 分批流式 OCR + 判定：每批 8 张（实测 sample 同批大小 ~10s），判完检查是否够 need。
    # 关键：剩余图**根本不跑 OCR**——这才是 C 的省时来源（实测 fast 模式中文召回 0/6，
    # 故仍用 accurate，只减张数不减精度）。缓存命中图免费并入判定，不占 OCR 批额。
    ocr_map = {}
    if not no_cache:
        for iid, p in paths.items():
            hit = JC.get_ocr(p, sha_of.get(iid, ""))
            if hit:
                ocr_map[str(p)] = hit
    step = max(4, min(batch_size, 8))
    # 按序判，缺 OCR 的攒一批跑，判完不够再下一批（剩余图不跑 OCR）
    seen, clean, judged = set(), 0, 0
    for start in range(0, len(imgs), step):
        chunk = imgs[start:start + step]
        need_ocr = [str(paths[im["id"]]) for im in chunk
                    if im["id"] in paths and str(paths[im["id"]]) not in ocr_map]
        if need_ocr:
            ocr_map.update(G.ocr_batch(need_ocr, max_per_call=step))
        stop = False
        for im in chunk:
            p = paths.get(im["id"])
            if not p:
                continue
            im["judged_by"] = "stop-after"   # 剩余未判图靠此标记区分（note 可能非空）
            od = ocr_map.get(str(p)) or {}
            if not no_cache and od:
                JC.save_ocr(p, od, sha_of.get(im["id"], ""))
            try:
                g = G.grade_from_ocr(od, p)
                cjk, grade = g.get("cjk") or 0, g.get("grade")
                txt = (g.get("text") or "").replace("\n", " ")
            except Exception as e:
                im["verdict"] = "L0_drop"
                im["note"] = "OCR 失败(%s) → 保守整图删" % str(e)[:40]
                judged += 1
                continue
            rec_text = [x["text"] for x in (od.get("items") or [])
                        if x.get("conf", 0) >= 0.25 and x.get("text")]
            old_ev = im.get("evidence") or {}
            if old_ev.get("evidence_source") != "aidge":
                im["evidence"] = {"rec_text": rec_text, "evidence_source": "local_vision_ocr",
                                  "noobj_wm": False, "noobj_char": None,
                                  "reason": "stop-after 本地 Vision OCR（非文字水印不可检）"}
            if cjk > 0:
                im["verdict"] = "L0_drop"
                im["note"] = "OCR 判级 %s，含中文 %d 字：%s" % (grade, cjk, txt[:60])
            elif grade in ("A", "B") or (grade not in ("D",) and _only_units(txt)):
                im["verdict"] = "L3_keep"
                im["note"] = "OCR 判级 %s，无中文（stop-after 通道分级）" % grade
                sha = sha_of.get(im["id"]) or im["url"]
                if sha not in seen:
                    seen.add(sha)
                    clean += 1
            else:
                im["verdict"] = "pending"
                im["note"] = "OCR 判级 %s 有非中文字符：%s" % (grade, txt[:60])
            judged += 1
            if clean >= need:
                stop = True
                break
        if stop:
            break
        # 预算闸（2026-11-26 A/B 实测）：脏品可能永远凑不够 need（如 49 图仅 3 张干净），
        # 判满 max_judge 仍不足就提前放弃，避免分批 OCR 冷启动被摊 6 次比全册判还慢
        # （实测最坏样本 A 全册 36.3s vs B 无闸 52.6s，慢 45%）。
        # 放弃语义与 sample 预杀一致：返回 5 让 precheck C⑦ 淘汰，不污染判定。
        if judged >= max_judge and clean < need:
            print("[stop] 预算闸：判了 %d 张仅 %d/%d 张干净 → 提前放弃（脏品）"
                  % (judged, clean, need))
            break
    # 剩余未判：标 unjudged（非 pending，避免 verify 误判为未判定图）。
    # 用 judged_by 标记代替「note 为空」——scan 预置的 note 可能非空，靠 note 判断会漏标。
    for im in imgs:
        if im.get("judged_by") != "stop-after":
            im["verdict"] = "unjudged"
            im["note"] = "stop-after 已凑够 %d 张（或全册判完），本图未判" % need
    json.dump(d, open(rp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if not no_cache:
        n = JC.save_from_review(rp, source="local_vision_ocr")
        print("[stop] 判定缓存 +%d 条" % n)
    print("[stop] 判了 %d/%d 张，唯一干净 %d/%d → %s"
          % (judged, len(imgs), clean, need, "达标" if clean >= need else "不足"))
    return 0 if clean >= need else 5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", required=True)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--sample", type=int, default=0,
                    help="抽样模式：判前N张即止，退出码0=放行全册/5=预杀/2=网络问题")
    ap.add_argument("--need", type=int, default=5, help="抽样模式干净图阈值（默认5，对齐C⑦）")
    ap.add_argument("--batch-size", type=int, default=40, help="批量每组图数（默认40）；不是线程数")
    ap.add_argument("--no-batch", action="store_true", help="回退旧版串行单图 OCR")
    ap.add_argument("--no-cache", action="store_true",
                    help="不把判级写入 judge_cache（默认写，供 Phase3/prefilter 复用）")
    ap.add_argument("--stop-after", type=int, default=0,
                    help="C 通道分级（2026-11-26）：按 main>sku>desc 判到凑够 N 张唯一干净图即停，"
                         "其余标 unjudged 不跑 OCR。0=关闭（全册判，默认）")
    ap.add_argument("--stop-max", type=int, default=20,
                    help="stop-after 预算闸：判满 N 张仍不足干净阈值就提前放弃（默认20；"
                         "防脏品分批冷启动比全册判还慢）")
    a = ap.parse_args()

    if a.sample:
        sys.exit(sample_mode(a.review, n=a.sample, need=a.need))
    if a.stop_after:
        sys.exit(stop_after_mode(a.review, need=a.stop_after,
                                 batch_size=max(1, a.batch_size),
                                 no_cache=a.no_cache, max_judge=a.stop_max))

    rp = Path(a.review)
    d = json.load(open(rp, encoding="utf-8"))
    base = rp.parent
    stat = {"L3_keep": 0, "L0_drop": 0, "pending": 0}
    pend = []

    # 收集所有图的本地路径（下载 IO 不是瓶颈，串行即可）
    paths = {}
    for im in d.get("images", []):
        lf = im.get("local_file") or ""
        p = base / lf
        if lf and p.exists():
            paths[im["id"]] = p
            continue
        if G.dl(im["url"], base / (im["id"] + ".jpg")):
            paths[im["id"]] = base / (im["id"] + ".jpg")
        else:
            im["verdict"] = "L0_drop"
            im["note"] = "下载失败 → 保守整图删"
            stat["L0_drop"] += 1

    # B（2026-11-26）：先查共享 OCR 缓存（prefilter 首图已判过），命中免跑 swift。
    # key 用 review 里的 content_sha256（图片字节），与 prefilter 存的 sha 一致。
    import judge_cache as JC
    sha_by_id = {im["id"]: (im.get("content_sha256") or "")
                 for im in d.get("images", [])}
    ocr_map, cache_hits = {}, 0
    if not a.no_cache:
        for iid, p in paths.items():
            hit = JC.get_ocr(p, sha_by_id.get(iid, ""))
            if hit:
                ocr_map[str(p)] = hit
                cache_hits += 1
    if cache_hits:
        print("[auto] OCR 缓存命中 %d 张（prefilter/历史已判，免重复 swift）" % cache_hits)

    # 未命中的才批量 OCR，批量失败项在 ocr_batch 内已回退单图
    uncached = [str(p) for p in paths.values() if str(p) not in ocr_map]
    if uncached and not a.no_batch:
        ocr_map.update(G.ocr_batch(uncached, max_per_call=max(1, a.batch_size)))

    for im in d.get("images", []):
        if im.get("verdict") == "L0_drop" and "下载失败" in (im.get("note") or ""):
            continue
        p = paths.get(im["id"])
        try:
            od = ocr_map.get(str(p)) if not a.no_batch else None
            if od is None:
                od = G.ocr(p) if p else {}
            # 新跑的 OCR 存缓存（命中缓存的已存在，save_ocr 自动去重）
            if not a.no_cache and od and p:
                JC.save_ocr(p, od, sha_by_id.get(im["id"], ""))
            g = G.grade_from_ocr(od, p)
        except Exception as e:
            im["verdict"] = "L0_drop"
            im["note"] = "OCR 失败(%s) → 保守整图删" % str(e)[:40]
            stat["L0_drop"] += 1
            continue
        # 写本地 OCR 证据（2026-11-26 A：供 Phase3 复用，免重复 probe）。
        # 格式与 image_review._local_evidence_batch 完全一致（Aidge probe 缺钱回退已用同款），
        # 678 硬闸只查 evidence.rec_text 存在且无 CJK，故此格式合法。
        # 不覆盖已有 Aidge 证据（source=aidge 更权威）。
        old_ev = im.get("evidence") or {}
        if old_ev.get("evidence_source") != "aidge":
            rec_text = [x["text"] for x in (od.get("items") or [])
                        if x.get("conf", 0) >= 0.25 and x.get("text")]
            im["evidence"] = {
                "rec_text": rec_text,
                "evidence_source": "local_vision_ocr",
                "noobj_wm": False, "noobj_char": None,
                "reason": "auto_verdict 本地 Vision OCR（非文字水印不可检）",
            }
        # 以下判级分支与原版逐字一致，保证行为不变
        cjk = g.get("cjk") or 0
        grade = g.get("grade")
        txt = (g.get("text") or "").replace("\n", " ")
        if cjk > 0:
            im["verdict"] = "L0_drop"
            im["note"] = "OCR 判级 %s，含中文 %d 字：%s" % (grade, cjk, txt[:60])
            stat["L0_drop"] += 1
        elif grade in ("A", "B"):
            im["verdict"] = "L3_keep"
            im["note"] = "OCR 判级 %s（无中文无文字），白度/背景达标" % grade
            stat["L3_keep"] += 1
        elif _only_units(txt):
            im["verdict"] = "L3_keep"
            im["note"] = "OCR 判级 C 但仅尺寸/单位标注（无营销词）：%s" % txt[:40]
            stat["L3_keep"] += 1
        else:
            im["verdict"] = "pending"
            im["note"] = "OCR 判级 %s 有非中文字符：%s → 需人看是否促销字" % (grade, txt[:60])
            stat["pending"] += 1
            pend.append(im["id"])

    print("[auto] 统计:", stat, "模式:", "批量" if not a.no_batch else "串行")
    for im in d.get("images", []):
        print("  %-8s %-8s %s" % (im["id"], im.get("verdict"), (im.get("note") or "")[:66]))
    if pend:
        print("[auto] 待人工:", ",".join(pend))
    if not a.dry:
        json.dump(d, open(rp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("[auto] 已写回 %s" % rp)
        if not a.no_cache:
            import judge_cache as JC
            detail = (d.get("source_detail") or "")
            n = JC.save_from_review(rp, detail if os.path.exists(detail) else None,
                                    source="local_vision_ocr")
            print("[auto] 判定缓存 +%d 条（key=content_sha256，供 Phase3 免 probe 复用）" % n)
    return 3 if pend else 0


if __name__ == "__main__":
    sys.exit(main())
