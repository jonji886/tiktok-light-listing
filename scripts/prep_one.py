#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prep_one.py — 单品 edit 参数自动装配（2026-11-25 batch12）

读 publish_one 落盘的 detail.json，生成 ttl_edit 需要的：
  sku_map.json  {seller_sku: [value_name, ...]}  —— 规格值中→马来语（ERP 不译 value_name，实测 11-25）
  sellers.txt   seller_sku 清单（供 keep-prefix 选择）
CJK 未命中即报错并列出待补词条（不静默写中文，也不编造）。

用法：
  python3 scripts/prep_one.py --detail <detail.json> --out-dir <dir> [--extra dict.json]
"""
import argparse
import json
import os
import re
import sys

CJK = re.compile(r"[\u4e00-\u9fff]")

DICT = {
    # 颜色（细网洗衣袋）
    "70克细网-少女粉": "Jaring Halus - Pink Muda",
    "70克细网-极致灰": "Jaring Halus - Kelabu Gelap",
    "70克细网-明亮黄": "Jaring Halus - Kuning Cerah",
    "70克细网-青柠绿": "Jaring Halus - Hijau Limau",
    # 空调罩
    "天鹅蓝": "Biru Swan", "天鹅粉": "Pink Swan", "天鹅紫": "Ungu Swan",
    "小兔黄": "Kuning Arnab", "小兔绿": "Hijau Arnab", "天鹅灰": "Kelabu Swan",
    "小兔蓝": "Biru Arnab", "小兔粉": "Pink Arnab",
    # 微波炉罩
    "玻璃瓶": "Botol Kaca", "叶子": "Daun", "几何": "Geometri", "趣味猫": "Kucing Comel",
    "小树": "Pokok Kecil", "森林小兔": "Arnab Hutan", "枫叶": "Daun Maple", "扇形花": "Bunga Kipas",
    # 通用色
    "透明": "Lutsinar", "红色": "Merah", "蓝色": "Biru", "米色": "Beige",
    "黑色": "Hitam", "粉色": "Pink", "灰色": "Kelabu", "藏青色": "Biru Gelap",
    "酒红色": "Marun", "咖啡色": "Kopi", "颜色随机": "Warna Rawak",
    # 浴室挂袋
    "新款挂袋蓝色": "Beg Gantung Biru", "新款挂袋米色": "Beg Gantung Beige",
    "新款挂袋粉色": "Beg Gantung Pink", "高级黑": "Hitam Premium",
    "珍珠白": "Putih Mutiara", "樱花粉": "Pink Sakura",
    # 床底袋
    "黑边小号": "Bintang Hitam S", "黑边中号": "Bintang Hitam M", "黑边大号": "Bintang Hitam L",
    "黑边特大号": "Bintang Hitam XL", "黑边床底巨大号": "Bintang Hitam XXL",
    "白边小号": "Bintang Putih S", "白边中号": "Bintang Putih M", "白边大号": "Bintang Putih L",
    "白边特大号": "Bintang Putih XL", "白边床底巨大号": "Bintang Putih XXL",
    # 袜夹
    "粉色12夹": "Pink 12 Klip", "绿色12夹": "Hijau 12 Klip", "紫色12夹": "Ungu 12 Klip",
    # 规格
    "小号30*40cm": "Saiz Kecil 30x40cm", "中号40*50cm": "Saiz Sederhana 40x50cm",
    "大号50*60cm": "Saiz Besar 50x60cm", "特大号60*60cm": "Saiz XXL 60x60cm",
    "圆筒22*33cm": "Silinder 22x33cm", "文胸袋16*16cm": "Beg Bra 16x16cm",
    "白色三层20蛋": "Putih 3 Tingkat 20 Biji", "黄色三层20蛋": "Kuning 3 Tingkat 20 Biji",
    "透明三层20蛋": "Lutsinar 3 Tingkat 20 Biji", "白色四层30蛋": "Putih 4 Tingkat 30 Biji",
    "黄色四层30蛋": "Kuning 4 Tingkat 30 Biji", "透明四层30蛋": "Lutsinar 4 Tingkat 30 Biji",
    "盒装": "Berboks", "ONE SIZE": "ONE SIZE",
    "1.5-2P": "1.5-2P",
    "颜色随机": "Warna Rawak",
    "A款上开门波轮款约55*58*85cm": "Model A Atas 55x58x85cm",
    "B款前开门滚筒款约58*62*85cm": "Model B Depan 58x62x85cm",
    # 搬家袋
    "72L": "72L", "88L": "88L", "95L": "95L",
    "六件套（小中大特大+圆筒+文胸）": "Set 6 Keping",
}


def norm(v):
    v = (v or "").strip()
    if v in DICT:
        return _tidy(DICT[v])
    # 已无中文直接返回（数字/英文保留）
    if not CJK.search(v):
        return _tidy(v)
    # 去掉空白后再查一次
    v2 = re.sub(r"\s+", "", v)
    if v2 in DICT:
        return _tidy(DICT[v2])
    return None


FULLWIDTH = {"【": "(", "】": ")", "（": "(", "）": ")", "★": " ", "＊": "x"}


def _tidy(s):
    """全角括号/星号统一成半角，避免平台端在 SKU 值里显示奇怪字符。"""
    out = "".join(FULLWIDTH.get(c, c) for c in str(s))
    return re.sub(r"\s{2,}", " ", out).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--extra", default="", help="补充词表 JSON 文件（覆盖内置）")
    a = ap.parse_args()

    d = json.load(open(a.detail, encoding="utf-8"))
    v2 = d.get("reqInfo", {}).get("tikTokItemModel", {}).get("productCreateRequestV2")
    if not v2:
        sys.exit("[prep] detail 结构异常：缺 reqInfo.tikTokItemModel.productCreateRequestV2")
    skus = v2.get("skus") or []
    if not skus:
        sys.exit("[prep] detail 内 skus 为空")

    if a.extra:
        DICT.update(json.load(open(a.extra, encoding="utf-8")))

    os.makedirs(a.out_dir, exist_ok=True)
    smap, sellers, missing = {}, [], []
    seq = []                       # 按 skus 原始顺序 —— 重复 seller_sku 时靠它对齐
    for s in skus:
        ssku = str(s.get("seller_sku") or "")
        sellers.append(ssku)
        vals = []
        for at in (s.get("sales_attributes") or []):
            raw = at.get("value_name")
            t = norm(raw)
            if t is None:
                missing.append("%s: %r" % (ssku, raw))
                t = raw
            vals.append(t)
        smap[ssku] = vals
        seq.append(vals)

    json.dump(smap, open(os.path.join(a.out_dir, "sku_map.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump(seq, open(os.path.join(a.out_dir, "sku_map_seq.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    open(os.path.join(a.out_dir, "sellers.txt"), "w", encoding="utf-8").write("\n".join(sellers) + "\n")

    # 重复 seller_sku：ERP 侧 6 个规格可能共用同一货号，dict 会互相覆盖 →
    # 按顺序生成唯一新货号（原货号加 -2/-3 后缀），配合 --sku-code-seq 使用。
    from collections import Counter
    cnt = Counter(sellers)
    dup = {k: v for k, v in cnt.items() if v > 1}
    codes, seen = [], {}
    for ssku in sellers:
        if cnt[ssku] <= 1:
            codes.append(None)
            continue
        seen[ssku] = seen.get(ssku, 0) + 1
        codes.append(ssku if seen[ssku] == 1 else "%s-%d" % (ssku, seen[ssku]))
    # 2026-11-25 修复：原先 dup 分支只打印「已产 sku_code_seq.json」却漏了 json.dump，
    # 导致下游 --sku-code-seq 读不到文件（batch13 踩坑，手工补写才过）。
    # 现在无论有无重复都写一份，调用方可无条件使用。
    json.dump(codes, open(os.path.join(a.out_dir, "sku_code_seq.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    if dup:
        print("[prep] ⚠ 重复 seller_sku %d 组（%s）→ 已产 sku_code_seq.json，"
              "ttl_edit 必须用 --sku-map-seq/--sku-code-seq，否则 dict 会互相覆盖"
              % (len(dup), ", ".join("%s×%d" % (k, v) for k, v in list(dup.items())[:4])))

    print("[prep] %d SKU → %s/sku_map.json" % (len(skus), a.out_dir))
    print("[prep] seller_sku: %s" % ", ".join(sellers[:30]))
    if missing:
        print("[prep] ⚠ %d 个值未命中词表（已原样写入，ttl_edit 会因为 CJK 拒）：" % len(missing))
        for m in missing[:20]:
            print("   - " + m)
        return 1
    print("[prep] ✅ 全部值已转为非中文")
    return 0


if __name__ == "__main__":
    sys.exit(main())
