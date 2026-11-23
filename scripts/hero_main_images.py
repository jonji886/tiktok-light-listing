#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hero_main_images.py — 选品期**款型级主图供给预检**（2026-11-20 P0-2）。

根因（针线盒 53 分里最贵的一次决策）：候选期 clean_main_images_after_fix=13 预测通过，
但该口径**不分款型**——图片闸门阶段才发现「所卖方款的干净主图只有 3 张」，
被迫中途换款型（椭圆→方款），花 8 分钟。本工具把这道判断前移到选品期。

口径（全是今天踩过坑的固化）：
- 款型分组：OAuth sku 回包的属性值按 --model-regex 提取（如 方款|圆款|磁吸针线盒）。
- 某款型可用主图 = ①通用干净图（main 通道 verdict=L3_keep 且 probe 无中文，款型无关的
  场景/白底图）+ ②该款型专属 SKU 图（OAuth skuImageUrl，**仅 cbu01 域 + 在 review 里
  L3_keep**；L1/L2 不算——修图产物落 kdzs/dashscope 域，TikTok 拉不到，713 实测）。
- 供给 ≥ min_main（默认 5，发布硬闸 min_publish_main_images）→ PASS；否则 FAIL。
- FAIL 的处置顺序（写进输出）：换款型卖（供给最高款）> 换货源 > 剔除。禁止靠修图凑数。

用法：
  python3 scripts/hero_main_images.py --sku-json erp_data/linkfox/mcp/sku_<oid>.json \
      --review erp_data/image_gate/<品>/review.json --model-regex "方款|圆款|磁吸针线盒"
  # review 还没有时（选品期未进图片闸门）：--sku-probe 模式仅按 OAuth SKU 图数量给保守上限
退出码：0 至少一款 PASS / 1 全部 FAIL（换款/换源）/ 2 输入问题
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CBU01 = "cbu01.alicdn.com"


def load_review(review_path):
    """→ (generic_clean_urls, verdict_by_url, model_tag_by_url)

    review 图片条目可带 "model" 字段（多模态判定时顺手标款型归属：方款/圆款/椭圆/通用）。
    款型专属的干净 main 图计入该款供给、不计入通用——这是"中途换款"8 分钟教训的正解。
    """
    r = json.load(open(review_path, encoding="utf-8"))
    generic, verdict_by_url, model_tag = set(), {}, {}
    for x in r.get("images") or []:
        u = x.get("url") or ""
        v = x.get("verdict") or "pending"
        verdict_by_url.setdefault(u, v)
        if x.get("model"):
            model_tag[u] = x["model"]
        chs = x.get("channels") or []
        if "main" in chs and v == "L3_keep" and x.get("evidence"):
            rt = x["evidence"].get("rec_text") or []
            if not any(re.search(r"[\u4e00-\u9fff]", str(t)) for t in rt):
                generic.add(u)
    return generic, verdict_by_url, model_tag


def model_of(value, regex):
    m = re.search(regex, value or "")
    return m.group(0) if m else "其他"


def main():
    ap = argparse.ArgumentParser(description="款型级主图供给预检")
    ap.add_argument("--sku-json", required=True, help="OAuth alibaba1688/sku 落盘文件")
    ap.add_argument("--review", default="", help="image_gate review.json（有则精确判定）")
    ap.add_argument("--model-regex", required=True, help='款型分组正则，如 "方款|圆款|磁吸针线盒"')
    ap.add_argument("--min-main", type=int, default=5)
    a = ap.parse_args()

    skus = json.load(open(a.sku_json, encoding="utf-8"))
    sku_list = skus.get("skuList") or skus.get("skus") or []
    if not sku_list:
        print("[hero] sku-json 无 skuList", file=sys.stderr)
        return 2

    generic_all, verdict_by_url, model_tag = (set(), {}, {})
    if a.review:
        generic_all, verdict_by_url, model_tag = load_review(a.review)

    # 第一轮：按款型归集 SKU 图（cbu01 且 L3_keep 才算款型专属可用图）
    groups = {}
    all_model_imgs = set()
    for s in sku_list:
        attrs = s.get("attributes") or [{}]
        value = " ".join(str(x.get("value") or "") for x in attrs)
        g = model_of(value, a.model_regex)
        img = str(s.get("skuImageUrl") or "")
        grp = groups.setdefault(g, {"skus": 0, "sku_imgs_ok": [], "sku_imgs_bad": []})
        grp["skus"] += 1
        if not img or CBU01 not in img:
            continue
        all_model_imgs.add(img)
        if not a.review:
            grp["sku_imgs_ok"].append(img)
            continue
        v = verdict_by_url.get(img, "pending")
        if v == "L3_keep":
            grp["sku_imgs_ok"].append(img)
        else:
            grp["sku_imgs_bad"].append("%s(%s)" % (img[-24:], v))

    # 关键修正（2026-11-20 反事实验证）：款型专属图**不得**计入其他款型的通用供给。
    # 通用 = 干净 main 图里排除「任何款型的专属 SKU 图」与「被标注 model≠通用 的 main 图」。
    generic = {u for u in generic_all if u not in all_model_imgs
               and model_tag.get(u, "通用") == "通用"}

    any_pass = False
    for g, info in sorted(groups.items()):
        if a.review:
            tagged_ok = [u for u in generic_all if model_tag.get(u) == g]
            supply = len(generic) + len(info["sku_imgs_ok"]) + len(tagged_ok)
            note = "通用%d + 本款SKU图%d + 本款标注main图%d" % (len(generic), len(info["sku_imgs_ok"]), len(tagged_ok))
            if info["sku_imgs_bad"]:
                note += "；本款不可用: %s" % ",".join(info["sku_imgs_bad"][:3])
        else:
            # ⚠ 保守上限口径（2026-11-23 加注）：无 review 时把该款型全部 SKU 图暂计——
            # 这是**上限不是预测**，选品期用它做初筛可以，但它会系统性高估
            # （SKU 图带中文横幅很常见，683 三源实测唯一 0 中文横幅主图组都没出现）。
            # 正确流程 = 初筛通过后立即拉主图目视/probe 转精确口径，再决定是否进采集。
            supply = len(generic_all) + info["skus"]
            note = "保守**上限**口径（无review，全SKU图暂计）%d 张——会高估，进采集前必须转精确口径" % info["skus"]
        ok = supply >= a.min_main
        any_pass = any_pass or ok
        print("[hero] 款型「%s」SKU%d 可用主图供给=%d(需%d) %s | %s"
              % (g, info["skus"], supply, a.min_main, "PASS" if ok else "FAIL", note))
    if not any_pass:
        print("[hero] 全部款型 FAIL → 处置顺序：换货源 > 剔除；禁止靠 image_fix 换链凑数"
              "（kdzs/dashscope 域 TikTok 拉不到，713 实测）")
    elif a.review:
        def sup(kv):
            tg = len([u for u in generic_all if model_tag.get(u) == kv[0]])
            return len(generic) + len(kv[1]["sku_imgs_ok"]) + tg
        best = max(groups.items(), key=sup)
        print("[hero] 建议款型=「%s」（供给最高 %d 张，选品期即定，不留到图片闸门中途换款）"
              % (best[0], sup(best)))
    return 0 if any_pass else 1


if __name__ == "__main__":
    sys.exit(main())
