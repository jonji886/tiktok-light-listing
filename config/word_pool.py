# -*- coding: utf-8 -*-
"""选品词库分级（2026-11-25 实测沉淀）

用法：precheck.py --word "围裙 背带 帆布" 会先查本表，命中 tier_a/tier_b 则放行并提示干净率；
      命中 blacklist 直接拒。未收录的词照常放行但打警告。

判定口径：词搜 20 条候选 → 本地 OCR（scripts/img_grade.py）判首图 → A/B 级占比 = 首图干净率。
          注意首图干净 ≠ 整册干净，必须配全图集判级（precheck 已内置）。
数据来源：2026-11-25 MY 站实测，明细见 .workbuddy/memory/2026-11-25.md
"""

TIER_A = [
    dict(word="围裙 背带 帆布 可爱", clean_rate=0.47, sample=15,
         notes="成品服装型供应商，图是挂拍白底；4/4 一次过闸，整册干净图 11-34 张"),
    dict(word="挂钩 免打孔 收纳", clean_rate=0.53, sample=19,
         notes="首图干净率高但整册普遍脏（18 张全带营销字），必须走全图集判级"),
    dict(word="洗衣袋 细网", clean_rate=0.53, sample=7,
         notes="样本小；该词命中过唯一零删图货源 2219378442389，可按店铺维度复用"),
]

TIER_B = [
    dict(word="防尘罩 挂衣袋 服装", clean_rate=0.38, sample=24,
         notes="整册同源重复风险高：同店多 offer 只能取一个（908357121707 与 960746713950 同图包）"),
    dict(word="六件套 旅行收纳袋", clean_rate=0.17, sample=6,
         notes="被拉链袋/包装袋污染，真收纳袋套装少"),
]

TIER_C = [
    dict(word="围裙 防水 厨房 家用", clean_rate=0.08, sample=13,
         notes="内销批发型供应商，图上印满规格参数；与 tier_a 同品类却是两种供给"),
    dict(word="防尘罩 立体 全封闭", clean_rate=0.15, sample=0,
         notes="未单独实测，按同族推断归入 C"),
]

BLACKLIST = {
    "真空压缩袋 收纳": "7 张首图 0 张干净（免抽气/秒速排气/扩容50% 全叠在首图）",
    "收纳袋 抽绳 束口 衣物": "20 条候选全是 ¥0.1-0.4 包装袋，全部卡 ¥2 价格闸",
    "一次性": "低价耗材结构性过不了 ¥2 门槛（挂钩 ¥0.03、海绵 ¥0.18、一次性罩 <¥1）",
}

# 已验证的「干净店铺」——换池线索，同 shopId 的货架可批量用
CLEAN_SHOPS = {
    "2219378442389": "洗衣袋 9/9 图全干净，零删图",
    "2216324997014": "晋州派屹，围裙图集干净图 24-34 张（742399880063 / 733714051312 同族）",
    "2220084530331": "兔耳围裙，11 张图 8 组唯一内容且全干净",
}

# 类目配比软约束（P2-7）：单次铺货某大类占比上限，超出只告警不阻断
CATEGORY_QUOTA = {
    "围裙": 0.40,
    "防尘罩": 0.30,
    "洗衣袋": 0.30,
}


def classify(word):
    """返回 (tier, clean_rate, notes)。未收录返回 ("unknown", None, "")。"""
    w = (word or "").strip()
    for tbl, name in ((TIER_A, "A"), (TIER_B, "B"), (TIER_C, "C")):
        for it in tbl:
            if it["word"] == w:
                return name, it["clean_rate"], it["notes"]
    for k, why in BLACKLIST.items():
        if k in w:
            return "blacklist", 0.0, why
    return "unknown", None, ""


def suggest_words(n=3):
    """按干净率倒序推荐词（供人肉选词参考）。"""
    pool = [(it["clean_rate"], it["word"], name)
            for tbl, name in ((TIER_A, "A"), (TIER_B, "B")) for it in tbl]
    return sorted(pool, reverse=True)[:n]
