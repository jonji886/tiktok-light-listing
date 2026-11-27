#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prefilter.py — 图片判级左移 + IP 品牌前置（P0-1 / P2 共用）。

在 collect+claim（不可逆写 ERP）之前，只凭候选 CSV 的 image_url 做低成本预筛：
  1. 下载首图（或前 N 张）→ 本地批量 Vision OCR → 品牌/IP 词命中直接杀
  2. 首图含中文营销字 → 杀（实测 38 个淘汰中 23 个是干净图不足，此信号首图就有）
  3. 白底纯净度/背景 → 只作软分，不单独杀（高召回优先，避免错杀）

设计原则（第一性原理）：**高召回，只杀确定脏品**。宁可放过让 C 段多跑，不可错杀
一个本来能发的品。首图干净 ≠ 整册干净（多图集后面几张才脏的情况存在），
所以本脚本**不替代 C 段全图集判级**，只提前消灭「首图就脏」的确定品。

输入：候选 CSV，需含 offer_id + image_url（兼容 offerId/imageUrl）。
输出：过滤后 CSV（同列）+ JSON 报告（淘汰原因/耗时/命中词）。

用法：
  python3 scripts/prefilter.py --pool pool.csv --out pool_clean.csv --report rep.json
  python3 scripts/prefilter.py --pool pool.csv --out pool_clean.csv --keep-dirty  # 只报告不剔除
  python3 scripts/prefilter.py --check-image <url> --check-title "标题"  # 单品快查
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import img_grade as G  # noqa: E402
import timing         # noqa: E402

# 品牌/IP 硬红线：命中即杀（不区分大小写；含图内 OCR 文本与标题）
# B1（2026-11-25 641912316438 实损）：原表缺拉丁变体，且 "Hello" 单独成词会误杀
# 装饰字（HELLO 字样很常见）→ 收紧为 Hello Kitty 组合，并补齐卡通形象族。
BRAND_PATTERN = (
    # 中文（原有）
    r"日本|茶花|米奇|米老鼠|迪士尼|凯蒂|史努比|皮卡丘|哆啦|机器猫|耐克|阿迪|苹果|三星|"
    r"屈臣氏|飞利浦|无印|宜家|乐高|布朗熊|可妮兔|冰雪奇缘|蜘蛛侠|漫威|三丽鸥|美乐蒂|"
    r"库洛米|玉桂狗|初音|"
    # 中文（B1 补充：具体形象与授权话术）
    r"米妮|唐老鸭|小熊维尼|维尼熊|艾莎|雪宝|史迪奇|大耳狗|布丁狗|蛋黄哥|蜡笔小新|"
    r"樱桃小丸子|灌篮高手|火影|海贼王|鬼灭|奥特曼|变形金刚|小猪佩奇|汪汪队|"
    r"官方授权|正版授权|联名款|IP联名|"
    # 拉丁（原有，Hello 收紧为组合）
    r"Disney|Hello\s*Kitty|HelloKitty|Kitty|Snoopy|Nike|Adidas|Apple|Samsung|LG|"
    r"Watsons|Philips|MUJI|IKEA|LEGO|LINE|FRIENDS|Frozen|Spider|Marvel|Sanrio|Kuromi|"
    r"My\s*Melody|"
    # 拉丁（B1 补充：OCR 常见写法 + 更多 IP）
    r"Mickey|Minnie|Donald\s*Duck|Winnie|Pooh|Elsa|Olaf|Stitch|Cinnamoroll|Pompompurin|"
    r"Gudetama|Pochacco|Pokemon|Pikachu|Doraemon|Totoro|Ghibli|Sailor\s*Moon|"
    r"Dragon\s*Ball|Naruto|One\s*Piece|Demon\s*Slayer|Ultraman|Transformers|Peppa|Paw\s*Patrol|"
    r"Kety|Ketty|Snoopy|Marvel|"
    # OCR 噪声容错：品牌词常被读成近似串（641912316438 的 kitty 被读成 slelo slitluy）
    r"slelo|slitluy|helo\s*kity|hello\s*kity"
)


def _load_known_brands():
    """合并 opportunity_intake.yaml 的 known_brands（与机会提报红线同源，避免两套词表漂移）。"""
    p = HERE.parent / "config/opportunity_intake.yaml"
    if not p.exists():
        return ""
    extra = []
    in_block = False
    for line in p.read_text(encoding="utf-8").splitlines():
        if re.match(r"^known_brands:\s*$", line):
            in_block = True
            continue
        if in_block:
            m = re.match(r"^\s*-\s+(.+?)\s*$", line)
            if m:
                extra.append(re.escape(m.group(1)))
            elif line.strip() and not line.startswith(" "):
                break
    return "|" + "|".join(extra) if extra else ""


BRAND_RE = re.compile(BRAND_PATTERN + _load_known_brands(), re.I)

# B2B 批发行话：首图出现即硬杀（擦字也救不回，这类图本质是工厂素材）
B2B_HARD_RE = re.compile(r"源头|厂家|批发|一件代发|起批|拿货|供货|工厂|定制|OEM|ODM|MOQ|加盟|贴标")

# 中文营销字/尺寸参数：只作**软信号**记分，不单独杀。
# 教训（2026-11-25）：875478374226 首图带中文参数表，但 C 段擦字后成功 ACTIVATE。
# 「首图含中文」≠「不可救」，硬杀会错杀可擦字救回的品。
MKT_SOFT_RE = re.compile(r"包邮|热销|爆款|限时|特价|促销|尺寸|规格|材质|颜色|型号|重量|箱规|包装|克重")
CJK = G.CJK


def _text_of(ocr_dict):
    return " ".join(x["text"] for x in (ocr_dict.get("items") or [])
                    if x.get("conf", 0) >= 0.25)


def screen_one(oid, url, title, path, ocr_dict):
    """返回 (verdict, reason, evidence_dict)。verdict ∈ keep/drop/flag。

    硬杀只保留两类不可逆红线：品牌/IP、B2B 工厂素材行话。
    「首图含中文」只标 flag 交 C 段全图集判级——因为擦字能救回（实测案例存在）。
    """
    txt = _text_of(ocr_dict)
    title_hit = BRAND_RE.search(title or "")
    img_hit = BRAND_RE.search(txt)
    if title_hit or img_hit:
        hit = (title_hit or img_hit).group(0)
        return "drop", "P2 品牌/IP 红线: " + hit, {"text": txt[:80], "brand": hit}

    b2b = B2B_HARD_RE.search(txt)
    if b2b:
        return "drop", "P0 B2B 工厂素材行话: " + b2b.group(0), {"text": txt[:80], "word": b2b.group(0)}

    cjk = len(CJK.findall(txt))
    soft = MKT_SOFT_RE.search(txt)
    if cjk > 0 or soft:
        g = G.grade_from_ocr(ocr_dict, path)
        return "flag", "首图中文 %d 字/营销词（交 C 段判级，可擦字救）" % cjk, {
            "text": txt[:80], "cjk": cjk, "grade": g["grade"]}
    return "keep", "", {"text": txt[:60], "cjk": 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", help="候选 CSV")
    ap.add_argument("--out", help="过滤后 CSV")
    ap.add_argument("--report", help="JSON 报告")
    ap.add_argument("--keep-dirty", action="store_true", help="只报告不剔除")
    ap.add_argument("--limit", type=int, default=0, help="最多处理几个（0=全部）")
    ap.add_argument("--check-image", help="单品快查：图片 URL")
    ap.add_argument("--check-title", default="", help="单品快查：标题")
    ap.add_argument("--no-ocr-cache", action="store_true",
                    help="不把首图 OCR 结果写入共享缓存（默认写，供 C 段免重复 OCR）")
    args = ap.parse_args()

    tmp = Path("/tmp/prefilter_imgs")
    if not tmp.exists():
        tmp.mkdir(parents=True)

    # 单品快查
    if args.check_image:
        p = tmp / ("single_%d.jpg" % int(time.time()))
        if not G.dl(args.check_image, p):
            print("下载失败")
            return 2
        od = G.ocr(p)
        v, why, ev = screen_one("single", args.check_image, args.check_title, p, od)
        print(json.dumps({"verdict": v, "reason": why, "evidence": ev}, ensure_ascii=False, indent=1))
        return 0 if v == "keep" else 1

    if not args.pool:
        sys.exit("[prefilter] 需 --pool 或 --check-image")

    rows = []
    with open(args.pool, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        sys.exit("[prefilter] 池为空")

    t0 = time.time()
    # 下载首图（并行下载，网络 IO 可并发；与 OCR 冷启动无关）
    from concurrent.futures import ThreadPoolExecutor
    def dl_one(item):
        i, r = item
        oid = str(r.get("offer_id") or r.get("offerId") or "").strip()
        url = r.get("image_url") or r.get("imageUrl") or ""
        p = tmp / (oid + ".jpg")
        ok = bool(url) and G.dl(url, p)
        return i, oid, url, p, ok
    with ThreadPoolExecutor(max_workers=8) as ex:
        dl_res = list(ex.map(dl_one, enumerate(rows)))

    # 批量 OCR（一个 swift 进程）
    ok_items = [x for x in dl_res if x[4]]
    ocr_map = G.ocr_batch([str(x[3]) for x in ok_items], max_per_call=40)
    # B（2026-11-26）：首图 OCR 结果写共享缓存，C 段判同一张图时直接复用、不再跑 swift。
    # 实测首图 OCR 是选品期最大单项（prefilter 8 首图 ~8s），而 C 段还会对同一 URL 重判。
    if not args.no_ocr_cache:
        import judge_cache as JC
        saved = 0
        for x in ok_items:
            od = ocr_map.get(str(x[3]))
            if od and JC.save_ocr(x[3], od):
                saved += 1
        if saved:
            print("[prefilter] OCR 结果入共享缓存 %d 张（C 段同图免重复判）" % saved)

    dropped, kept, no_img, flagged = [], [], [], []
    verdict_by_oid = {}          # A2：把判级信号带出到 CSV，供 precheck 排序
    for i, oid, url, p, ok in dl_res:
        r = rows[i]
        if not ok:
            no_img.append({"offer_id": oid, "reason": "首图下载失败（保守放过，交 C 段）"})
            verdict_by_oid[oid] = "no_image"
            kept.append(r)
            continue
        od = ocr_map.get(str(p), {})
        v, why, ev = screen_one(oid, url, r.get("title", ""), p, od)
        if v == "drop":
            dropped.append({"offer_id": oid, "reason": why, "evidence": ev})
            verdict_by_oid[oid] = "drop"
            if not args.keep_dirty:
                continue
        elif v == "flag":
            flagged.append({"offer_id": oid, "reason": why, "evidence": ev})
            verdict_by_oid[oid] = "flag"
        else:
            verdict_by_oid[oid] = "keep"
        kept.append(r)

    # 输出
    if args.out:
        # A2（2026-11-25）：带出首图判级列。本批数据证明首图中文≠整册脏（硬杀会误杀），
        # 但它是零成本排序信号：让序贯筛选先检 keep 品、flag 品排队尾，达标即停，
        # 可省掉「整册干净图不足」淘汰品的无效认领（本批这类占淘汰 100%）。
        cols = list(rows[0].keys())
        if "prefilter_verdict" not in cols:
            cols.append("prefilter_verdict")
        with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in kept:
                r["prefilter_verdict"] = verdict_by_oid.get(
                    str(r.get("offer_id") or r.get("offerId") or ""), "no_image")
                w.writerow(r)
    report = dict(total=len(rows), kept=len(kept), dropped=len(dropped),
                  flagged=len(flagged), no_image=len(no_img),
                  elapsed_s=round(time.time() - t0, 1),
                  mode="report_only" if args.keep_dirty else "filtered",
                  dropped_items=dropped, flagged_items=flagged, no_image_items=no_img)
    if args.report:
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print("[prefilter] %d 候选 → 留 %d / 硬杀 %d / 软标 %d / 无图 %d / 耗时 %.1fs"
          % (len(rows), len(kept), len(dropped), len(flagged), len(no_img), report["elapsed_s"]))
    for d in dropped:
        print("  ✗", d["offer_id"], d["reason"][:70])
    if args.report:
        print("报告 →", args.report)
    return 0


if __name__ == "__main__":
    _t0 = time.time()
    try:
        _rc = main()
    finally:
        timing.rec("prefilter", _t0, time.time(), parent="batch")
    sys.exit(_rc)
