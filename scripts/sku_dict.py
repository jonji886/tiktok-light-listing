#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sku_dict.py — 自动生成 SKU 短值词表（A5，2026-11-25）

背景：本批 5 品全部要我手工读 detail.json 找 value_name，再写 ≤40 字符短值，
      纯规则可自动化。本脚本读 detail.json，输出可直接喂 prep_one/auto_assemble
      `--extra` 的 JSON；不能可靠翻译的条目给「建议值」+ 标注需人工确认。

用法：
  python3 scripts/sku_dict.py --offer-id 671250031819 > dict/671250031819.json
  python3 scripts/sku_dict.py --offer-id 671250031819 --strict   # 含未确认项则 exit 2
  python3 scripts/sku_dict.py --offer-id 671250031819 --report-only   # 只打印不产 JSON

规则（保守，宁可标「待确认」也不编造）：
  1. 已在 prep_one.DICT 的整条命中 → 直接用
  2. 拉丁且 ≤40 字符 → 原样保留
  3. 中文 → 用颜色/图案/规格分量词表替换；全部分量都命中才自动产出，
     否则给建议值并在 `_needs_review` 里列出原文（不静默机翻）
  4. 生成后按「维度+值」去重；撞车自动加 -2/-3 后缀
  5. 最终任何值 >40 字符 → 截断到 40 并标待确认
"""
import argparse
import glob
import json
import os
import re
import sys

CJK = re.compile(r"[\u4e00-\u9fff]")
MAX = 40

# 分量词表：只在「整段被这个词表完全覆盖」时才自动翻译，避免机器乱译
COLORS = {
    "黑色": "Hitam", "黑": "Hitam", "白色": "Putih", "白": "Putih",
    "红色": "Merah", "红": "Merah", "粉色": "Pink", "粉": "Pink",
    "玫色": "Rose", "玫瑰色": "Rose", "浅粉": "Pink Muda", "深粉": "Pink Tua",
    "蓝色": "Biru", "蓝": "Biru", "绿色": "Hijau", "绿": "Hijau",
    "咖色": "Kopi", "咖啡色": "Kopi", "棕色": "Kopi", "紫": "Ungu", "紫色": "Ungu",
    "黄色": "Kuning", "黄": "Kuning", "灰色": "Kelabu", "灰": "Kelabu",
    "藏青": "Biru Gelap", "米色": "Beige", "透明": "Lutsinar",
    "玫红": "Rose", "酒红": "Marun", "深蓝": "Navy",
}
PATTERN = {
    "寿司": "Sushi", "彩虹": "Pelangi", "无图": "Polos", "格纹": "Grid",
    "花朵": "Bunga", "花": "Bunga", "兔子": "Arnab", "兔耳": "Telinga Arnab",
    "猫脸": "Muka Kucing", "大脸": "Muka Besar", "蝴蝶结": "Riben",
    "公主": "Puteri", "太阳花": "Bunga Matahari", "向日葵": "Bunga Matahari",
    "刺绣": "Sulaman", "蕾丝": "Renda", "星星": "Bintang", "波点": "Titik",
}
FEATURE = {
    "拉链兜": "Zip", "拉料兜": "Zip", "口袋": "Poket", "防水": "Kalis Air",
    "超防水": "Kalis Air Pro", "薄款": "Nipis", "厚款": "Tebal",
    "长袖": "Lengan Panjang", "短袖": "Lengan Pendek", "背带": "Tali",
    "棉质": "Kapas", "帆布": "Kanvas", "立体": "3D",
    "新款": "", "款": "", "面料无味": "", "面料": "",
}
# 按长度降序替换，避免「粉」先于「粉色」命中
ALL_TERMS = sorted(
    list(COLORS.items()) + list(PATTERN.items()) + list(FEATURE.items()),
    key=lambda kv: -len(kv[0]),
)


def detail_path(offer_id):
    fs = glob.glob("erp_data/_run/pipeline/%s/detail.json/*" % offer_id)
    if not fs:
        sys.exit("[sku_dict] %s 无 detail.json（先跑 precheck 采集）" % offer_id)
    return sorted(fs)[-1]


def values_of(offer_id):
    d = json.load(open(detail_path(offer_id), encoding="utf-8"))
    v2 = d["reqInfo"]["tikTokItemModel"]["productCreateRequestV2"]
    out, seen = [], set()
    for s in v2.get("skus") or []:
        for at in s.get("sales_attributes") or []:
            v = str(at.get("value_name") or "").strip()
            if v and v not in seen:
                seen.add(v)
                out.append(v)
    return out


def translate(v):
    """分量替换。返回 (建议值, 是否完全覆盖)。"""
    rest, parts = v, []
    for zh, ms in ALL_TERMS:
        while zh in rest:
            rest = rest.replace(zh, " ", 1)
            if ms:
                parts.append(ms)
    # 拉丁词/数字原样保留
    for tok in re.findall(r"[A-Za-z0-9]+(?:\.[0-9]+)?", rest):
        parts.append(tok)
    left = re.sub(r"[\s\-_()\[\]【】（）、,，/·+*×xX]+", "", rest)
    leftover = CJK.sub("", left)
    # 去掉纯装饰符后仍有中文/未知字符 → 未完全覆盖
    full = not CJK.search(left)
    return " ".join(dict.fromkeys(parts)).strip(), full


def tidy(s):
    out = "".join({"【": "(", "】": ")", "（": "(", "）": ")", "★": " ", "＊": "x"}.get(c, c)
                  for c in str(s))
    return re.sub(r"\s{2,}", " ", out).strip()


def build(offer_id):
    try:
        import prep_one
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import prep_one

    mapping, needs_review, used = {}, [], set()
    for raw in values_of(offer_id):
        if raw in prep_one.DICT:
            val = tidy(prep_one.DICT[raw])
        elif not CJK.search(raw) and len(raw) <= MAX:
            val = tidy(raw)
        else:
            sugg, full = translate(raw)
            if full and sugg:
                val = tidy(sugg)
            else:
                val = tidy(sugg or ("SKU%d" % (len(mapping) + 1)))
                needs_review.append({"原文": raw, "建议值": val,
                                     "原因": "分量词未完全覆盖" if sugg else "无可用分量"})
        # 截断 + 唯一化
        val = val[:MAX].strip()
        base, n = val, 2
        while val in used:
            suf = "-%d" % n
            val = base[:MAX - len(suf)] + suf
            n += 1
        used.add(val)
        mapping[raw] = val
        if len(val) >= MAX:
            needs_review.append({"原文": raw, "建议值": val, "原因": "达到/超过 40 字符"})
    return mapping, needs_review


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offer-id", required=True)
    ap.add_argument("--report-only", action="store_true", help="只打印，不输出 JSON")
    ap.add_argument("--strict", action="store_true", help="有待确认项则 exit 2")
    a = ap.parse_args()

    mapping, needs = build(a.offer_id)
    print("[sku_dict] %d 条，最长 %d 字符" %
          (len(mapping), max(len(v) for v in mapping.values())), file=sys.stderr)
    for n in needs:
        print("[sku_dict] ⚠ 待确认 %s → %s（%s）" %
              (n["原文"], n["建议值"], n["原因"]), file=sys.stderr)

    if not a.report_only:
        json.dump(mapping, sys.stdout, ensure_ascii=False, indent=1)
        print(file=sys.stdout)
    if a.strict and needs:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
