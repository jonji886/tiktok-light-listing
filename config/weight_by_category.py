# -*- coding: utf-8 -*-
"""品类计费重量 / 体积模板（2026-11-25 沉淀）

用途：1688 detail 常缺 package_weight（异常标签 MISSING_WEIGHT），precheck 用本表兜底，
      避免每次人工估算。单位 g / cm。
纪律：CATEGORY 里的值是 INFERRED（按品类典型包装推算），首单必须回看物流实重；
      实际值补进 CONFIRMED 后即转为 VERIFIED。
上限（MY 站）：重量 ≤400g、体积 ≤2400cm³。
"""

DEFAULTS = dict(weight_g=300, dims="25x20x4")   # 25*20*4 = 2000cm³

CATEGORY = [
    (["围裙", "罩衣"], 320, "25x20x4", "帆布/棉布长袖 300-350g；纯背带款偏轻 250g"),
    (["防尘罩", "挂衣袋", "衣罩"], 250, "25x18x6", "PEVA/无纺布可压扁；立体款 280g"),
    (["洗衣袋", "洗鞋袋", "洗衣网"], 200, "18x12x8", "细网袋轻；鞋袋含拉链 200-220g"),
    (["防溅罩", "微波炉盖"], 180, "22x16x8", "塑料折叠款"),
    (["挂钩", "粘钩"], 150, "12x10x5", "含背胶与包装"),
    (["马桶刷", "地刷", "缝隙刷", "地板刷"], 250, "40x10x5", "长柄 88cm 拆三段约 2000cm³"),
    (["收纳袋", "收纳包"], 280, "25x18x5", "套装按最大件算"),
    (["收纳盒", "收纳筐", "置物架"], 400, "30x25x12", "⚠️ 体积易超 2400cm³，采集前必须核 dims"),
    (["沥水篮", "洗菜篮", "密封罐"], 320, "22x18x12", "塑料件；玻璃罐不可套用"),
]

# 已实测跑通的品，直接复用
CONFIRMED = {
    "856367808910": dict(weight_g=200, dims="22x16x8", note="微波炉防溅盖，2 SKU"),
    "960746713950": dict(weight_g=250, dims="25x18x6", note="挂衣袋防尘罩，8 SKU"),
    "1045815200012": dict(weight_g=200, dims="18x12x8", note="洗鞋袋，4 色"),
    "914257511246": dict(weight_g=150, dims="30x25x3", note="挂脖防水围裙，9 色"),
    "733714051312": dict(weight_g=350, dims="25x20x4", note="波点长袖围裙"),
    "742399880063": dict(weight_g=320, dims="25x20x4", note="千鸟格爱心围裙"),
    "895771650525": dict(weight_g=320, dims="25x20x4", note="几何耳朵兜围裙"),
    "743716191001": dict(weight_g=300, dims="25x20x3", note="纯棉背带围裙"),
    "669669516561": dict(weight_g=300, dims="25x20x3", note="涤棉帆布围裙"),
    "679966596775": dict(weight_g=320, dims="25x20x4", note="波点拉链兜围裙"),
    "954592404608": dict(weight_g=320, dims="25x20x4", note="兔耳围裙"),
}


def volume(dims):
    """'25x20x4' → 2000"""
    try:
        w, d, h = [float(x) for x in str(dims).lower().split("x")]
        return w * d * h
    except Exception:
        return 0.0


def lookup(offer_id="", title="", category_path=""):
    """先查已实测表，再按标题/类目匹配，最后回默认值。

    返回 dict(weight_g, dims, volume, source)
    """
    oid = str(offer_id or "")
    if oid in CONFIRMED:
        c = CONFIRMED[oid]
        return dict(weight_g=c["weight_g"], dims=c["dims"],
                    volume=volume(c["dims"]), source="confirmed:" + c["note"])

    hay = "%s %s" % (title or "", category_path or "")
    for keys, w, dm, note in CATEGORY:
        if any(k in hay for k in keys):
            return dict(weight_g=w, dims=dm, volume=volume(dm), source="category:" + note)

    return dict(weight_g=DEFAULTS["weight_g"], dims=DEFAULTS["dims"],
                volume=volume(DEFAULTS["dims"]), source="default")


def check(weight_g, dims):
    """返回 (ok, reasons)。超上限即 not ok。"""
    reasons = []
    if weight_g and float(weight_g) > 400:
        reasons.append("重量 %sg > 400g" % weight_g)
    v = volume(dims)
    if v and v > 2400:
        reasons.append("体积 %scm³ > 2400" % int(v))
    return (not reasons), reasons
