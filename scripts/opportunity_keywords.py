#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""opportunity_keywords.py — 商品机会「热门关键词」tab 收割器（本地，不联网）。

为什么需要（2026-11-20 优先级重排的落地工具）：
  商品机会页三 tab 中，**热门关键词是唯一直接反映 MY 站内真实需求的信号**，
  它的价值不在"选品灵感"而在两处消费：
    a) 词根进 config/keywords/my.txt → listing_quality_gate.py 标题命中校验
       （破零期"有曝光没人点"的直接原因就是标题没吃到官方热搜词根）；
    b) 官方原词整串进 erp_data/keywords_my.txt → selection_intake.py pool
       拉 FastMoss 候选（词根粒度太粗不能直接喂 pool，喂的是整串长词）。
  旧流程这两步全靠人工记忆手抄，周常纪律经常漏做。本脚本把它固化：
  浏览器抄一张 CSV → 一条命令同时更新两张表 + 输出待人工复核清单。

输入 CSV 列（模板：python3 scripts/opportunity_keywords.py --init-template --site my）：
  word         后台原词整串（必填，如 "drawstring store plastik sampah"）
  volume       搜索量档位原文（必填，如 "2.8k+"；只作记录，不参与判定）
  rank         页内排序位次（可空）
  copied_at    抄录日期 YYYY-MM-DD（必填——词库时效性证据，AGENTS.md 时间敏感约束）
  note         备注（可空）

判定规则（保守，宁缺勿滥）：
  - 修饰词表（config/keywords/modifiers_<site>.txt，一行一个）里的词**只进整串表、
    不拆成品类词根**（tahan lasak=耐撕裂、murah=便宜 是修饰语，当品类词会污染闸门）。
  - 拆根 = 整串按空格分词，逐词：长度<3 丢弃；纯数字丢弃；已在词根表则视为命中不重复加。
  - 新词一律进 my.txt 末尾的「待复核」注释区？**否**——直接进词根表，但脚本打印
    新增清单，人工目视一遍再 commit（git diff 即复核面）。
  - 整串表去重忽略大小写/多余空格；保留后台原词（做标题用原词整串）。

用法：
  python3 scripts/opportunity_keywords.py --init-template --site my
  python3 scripts/opportunity_keywords.py --csv erp_data/opportunity_keywords_my.csv --site my          # dry-run 看计划
  python3 scripts/opportunity_keywords.py --csv ... --site my --apply                                   # 写两张表
  python3 scripts/opportunity_keywords.py --audit --site my                                             # 只审计：现有词根 vs 整串表覆盖

退出码：0 正常；2 输入/参数问题。--apply 幂等（重跑不产生重复行）。
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
KW_DIR = os.path.join(ROOT, "config", "keywords")
POOL_DIR = os.path.join(ROOT, "erp_data")

MIN_ROOT_LEN = 3  # 与 listing_quality_gate.MIN_WORD_LEN 对齐：短词会在别的词里误命中
UNIT_STOP = {"pcs", "pc", "set", "sets", "kg", "gr", "ml", "cm", "mm", "inch", "box", "pcs/set"}
# 马来语/英语虚词：任何标题都会带，进词根表等于闸门失效（命中3根的判据被白送）
STOPWORDS = {"dan", "untuk", "dengan", "tidak", "yang", "atau", "ini", "itu", "dan/atau",
             "a", "an", "the", "of", "and", "for", "with", "large", "small", "new"}
DEFAULT_MIN_FREQ = 2  # 词根在本批 CSV 出现 ≥2 次才进表（单次=场景词，留人工复核区）


def paths(site):
    site = site.lower()
    return {
        "roots": os.path.join(KW_DIR, "%s.txt" % site),
        "pool": os.path.join(POOL_DIR, "keywords_%s.txt" % site),
        "modifiers": os.path.join(KW_DIR, "modifiers_%s.txt" % site),
        "template": os.path.join(POOL_DIR, "opportunity_keywords_%s.csv" % site),
    }


def read_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return f.read().splitlines()


def parse_entries(lines):
    """词根/整串表通用解析：跳空行与纯注释行，剥行尾注释。返回小写规范化后的条目列表。"""
    out = []
    for raw in lines:
        line, _, _comment = raw.partition("#")
        w = " ".join(line.split())
        if w:
            out.append(w.lower())
    return out


def load_modifiers(site):
    p = paths(site)["modifiers"]
    mods = set()
    for raw in read_lines(p):
        line = raw.split("#")[0].strip().lower()
        if line:
            mods.add(line)
    return mods


def read_csv(path):
    import csv
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for i, r in enumerate(csv.DictReader(f)):
            word = (r.get("word") or "").strip()
            if not word:
                continue
            if not (r.get("copied_at") or "").strip():
                print("  [跳过] 第%d行缺 copied_at（时效性证据必填）: %s" % (i + 2, word))
                continue
            rows.append({"word": word,
                         "volume": (r.get("volume") or "").strip(),
                         "rank": (r.get("rank") or "").strip(),
                         "copied_at": r["copied_at"].strip()})
    return rows


def split_roots(word, modifiers):
    """整串 → 候选词根。修饰词整串本身不拆出的词单独处理：串中出现的修饰词不作为品类根。"""
    toks = [" ".join(word.split())]  # 多词修饰串（如 bilik mandi）先整体保留
    parts = re.split(r"\s+", word.strip().lower())
    multi = [m for m in modifiers if " " in m]
    rest = []
    consumed = set()
    for m in multi:
        if m in " ".join(parts):
            toks.append(m)
            consumed.update(m.split())
    for p in parts:
        if p in consumed:
            continue
        rest.append(p)
    roots = []
    for p in rest:
        p = re.sub(r"[^a-z0-9\u00c0-\u024f\u4e00-\u9fff]", "", p)
        if len(p) < MIN_ROOT_LEN:
            continue
        if p.isdigit() or p in UNIT_STOP or p in STOPWORDS:
            continue
        if p in modifiers:
            continue
        roots.append(p)
    return toks, roots


def cmd_init_template(site):
    p = paths(site)["template"]
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        f.write("word,volume,rank,copied_at,note\n")
    print("模板已写出：%s" % p)
    print("抄录方法：商家中心 → 商品 → 商品机会 → 热门关键词 tab（shop_region=%s），"
          "按搜索次数排序抄前 2-3 页，volume 记档位原文（如 2.8k+）。" % site.upper())
    print("降本（SKILL 0.2② 守则）：定步长滚动（每次≈整屏80%留重叠）一滚一截，截完全部再离线逐屏抄；"
          "最后一屏拍到分页控件对账行数。")


def cmd_sync(args):
    site = args.site.lower()
    P = paths(site)
    if not os.path.exists(args.csv):
        print("CSV 不存在：%s（先 --init-template 生成模板并抄录）" % args.csv)
        return 2
    rows = read_csv(args.csv)
    if not rows:
        print("CSV 无有效行。")
        return 2
    modifiers = load_modifiers(site)
    existing_roots = set(parse_entries(read_lines(P["roots"])))
    existing_pool = set(parse_entries(read_lines(P["pool"])))

    new_pool, mod_hits = [], []
    seen_pool = set()
    from collections import Counter
    freq = Counter()
    for r in rows:
        w = r["word"].lower()
        toks, roots = split_roots(w, modifiers)
        if w not in existing_pool and w not in seen_pool:
            seen_pool.add(w)
            new_pool.append((r, toks))
        for t in toks:
            # 多词修饰串（如 tahan lasak）不进整串表——喂 pool 会拉回无关品
            if t != w and t not in existing_pool and t not in seen_pool \
                    and " " in t and t not in modifiers:
                seen_pool.add(t)
                new_pool.append((dict(r, word=t), [t]))
        for rt in set(roots):  # 同一行重复词只计一次
            freq[rt] += 1
            if rt in modifiers:
                mod_hits.append(rt)
    min_freq = getattr(args, "min_freq", DEFAULT_MIN_FREQ) or 1
    new_roots = [rt for rt, _n in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
                 if rt not in existing_roots and freq[rt] >= min_freq]
    review_roots = [rt for rt, _n in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
                    if rt not in existing_roots and freq[rt] < min_freq]

    print("来源 CSV：%s（有效 %d 行，copied_at 覆盖 %s）"
          % (args.csv, len(rows), ",".join(sorted({r["copied_at"] for r in rows}))))
    print("整串表 %s：现有 %d，新增 %d" % (P["pool"], len(existing_pool), len(new_pool)))
    for r, toks in new_pool:
        print("  + %s  [%s]  拆根→ %s" % (r["word"], r["volume"], " ".join(toks)))
    print("词根表 %s：现有 %d，新增 %d（本批出现 ≥%d 次）" % (P["roots"], len(existing_roots), len(new_roots), min_freq))
    print("  新增词根: %s" % (" ".join("%s×%d" % (rt, freq[rt]) for rt in new_roots) if new_roots else "（无）"))
    if review_roots:
        print("  [待复核·仅出现1次，未入库] %s" % " ".join(review_roots))
    if mod_hits:
        print("  [提示] 命中修饰词表已排除: %s" % " ".join(sorted(set(mod_hits))))

    if not args.apply:
        print("\ndry-run（未写盘）。确认无误后加 --apply。")
        return 0

    stamp = max(r["copied_at"] for r in rows)
    with open(P["pool"], "a", encoding="utf-8") as f:
        f.write("\n# ---- opportunity_keywords.py sync @ %s（官方热门关键词 tab 原词整串，来源 VERIFIED）----\n" % stamp)
        for r, _ in new_pool:
            f.write("%s\n" % r["word"])
    with open(P["roots"], "a", encoding="utf-8") as f:
        f.write("\n# ---- %s 商品机会→热门关键词 tab 拆根（opportunity_keywords.py --apply，VERIFIED 当日实测）----\n" % stamp)
        for rt in new_roots:
            f.write("%s\n" % rt)
    print("\n已写入两张表。下一步：")
    print("  1) git diff 目视复核新增词根（误拆的删掉再 commit）")
    print("  2) python3 scripts/listing_quality_gate.py --site %s --summary   # 重跑标题命中" % site.upper())
    return 0


def cmd_audit(args):
    site = args.site.lower()
    P = paths(site)
    pool = parse_entries(read_lines(P["pool"]))
    roots = set(parse_entries(read_lines(P["roots"])))
    modifiers = load_modifiers(site)
    orphan_roots = [r for r in sorted(roots) if r not in modifiers and not any(r in w.split() for w in pool)]
    uncovered = [w for w in pool if not any(rt in w.split() for rt in roots)]
    print("整串表 %d 条 / 词根表 %d 条" % (len(pool), len(roots)))
    print("词根未出现在任何整串中（可能是手加种子词，核对是否仍要保留）: %s"
          % (" ".join(orphan_roots) if orphan_roots else "（无）"))
    print("整串无任何已登记词根覆盖（这些词做标题时闸门吃不到，优先补根）: %s"
          % (" | ".join(uncovered[:10]) if uncovered else "（无）"))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--init-template", action="store_true", dest="init_tpl",
                    help="生成抄录模板 CSV")
    ap.add_argument("--site", default="my", help="站点（my/th/vn），默认 my")
    ap.add_argument("--csv", help="抄录 CSV 路径（sync 用）")
    ap.add_argument("--apply", action="store_true", help="真正写盘（默认 dry-run）")
    ap.add_argument("--min-freq", type=int, default=DEFAULT_MIN_FREQ, dest="min_freq",
                    help="词根进表的本批最低出现次数（默认 %d；1=全部进表）" % DEFAULT_MIN_FREQ)
    ap.add_argument("--audit", action="store_true", help="两表覆盖审计")
    a = ap.parse_args()
    if a.init_tpl:
        return cmd_init_template(a.site)
    if a.audit:
        return cmd_audit(a)
    if a.csv:
        return cmd_sync(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
