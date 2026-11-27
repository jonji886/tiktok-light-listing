# -*- coding: utf-8 -*-
"""品类 → TikTok 叶子类目 ID 映射（2026-11-25 沉淀）

今天每个品发布都要手动去 erp_data/case_category_tree.json 里翻类目 ID（查了 6 次）。
本表把「标题/货源类目关键词 → 叶子类目」固化，precheck 直接给建议值。

注意：只收录**已验证 AVAILABLE 的叶子**。新品类必须先核 permissionStatuses 再加进来。
"""

# (关键词列表, category_id, full_name, 备注)
MAP = [
    (["围裙", "罩衣", "围腰"], "600427", "家居用品>家居护理用品>围裙", "11-25 实发 7 个品"),
    (["防尘罩", "挂衣袋", "衣罩", "防尘袋"], "700665", "家居用品>家居护理用品>防尘罩", "含立体/平口/欧根纱"),
    (["洗衣袋", "洗鞋袋", "洗衣网", "护洗袋"], "600747", "家居用品>洗衣工具与配件>洗衣袋", ""),
    (["防溅罩", "防油盖", "防油溅", "微波炉盖", "微波炉防溅", "微波炉加热盖"], "817296",
     "家居用品>家居护理用品>防溅罩", "标题常写「微波炉加热中/防油溅」"),
    (["挂钩", "粘钩", "粘钩钩"], "853128", "家居用品>家居收纳>挂钩", ""),
    (["马桶刷", "地板刷", "地刷", "缝隙刷", "清洁刷"], "600405",
     "家居用品>卫浴用品>马桶刷和马桶疏通器", "长柄刷归此"),
    (["收纳盒", "储物盒"], "600621", "家居用品>家居收纳>收纳盒", ""),
    (["收纳筐", "收纳篮"], "600686", "家居用品>家居收纳>收纳筐", ""),
    (["收纳架", "置物架"], "852872", "家居用品>家居收纳>收纳架", ""),
    (["收纳袋", "收纳包", "旅行袋"], "852744", "家居用品>家居收纳>收纳袋", ""),
    (["密封罐", "储物罐", "收纳瓶"], "853000", "家居用品>家居收纳>收纳瓶罐", ""),
    (["棉签盒", "皂盒", "肥皂盒"], "600678", "家居用品>浴室用品>皂盒", "家居树叶子仅 131 个，先核"),
    (["海绵", "百洁布", "洗碗布"], "855048", "家居用品>家居护理用品>海绵与百洁布", ""),
]

# 已知在家居树里没有对应叶子的方向（避免再浪费时间查）
NOT_IN_TREE = {
    "桌椅脚套": "家居树无叶子，11-25 实测弃",
    "碗碟沥水架": "归 852872 收纳架需人工确认，默认不自动给",
}


def suggest(title="", source_category=""):
    """返回 (category_id, full_name, matched_keyword) 或 (None, None, None)"""
    hay = "%s %s" % (title or "", source_category or "")
    for keys, cid, name, _ in MAP:
        for k in keys:
            if k in hay:
                return cid, name, k
    return None, None, None


def check_available(cid, tree_path="erp_data/case_category_tree.json"):
    """核 permissionStatuses 是否 AVAILABLE。返回 (ok, msg)。"""
    import json
    try:
        t = json.load(open(tree_path, encoding="utf-8"))
    except Exception as e:
        return False, "类目树读取失败: %s" % e

    hit = {"node": None}

    def walk(n):
        if isinstance(n, dict):
            if str(n.get("categoryId")) == str(cid):
                hit["node"] = n
            for c in (n.get("children") or []):
                walk(c)
        elif isinstance(n, list):
            for c in n:
                walk(c)

    walk(t.get("data") or t)
    nd = hit["node"]
    if not nd:
        return False, "类目 %s 不在树里" % cid
    if not nd.get("isLeaf"):
        return False, "类目 %s 不是叶子" % cid
    ps = nd.get("permissionStatuses") or []
    if ps and "AVAILABLE" not in ps:
        return False, "类目 %s 权限 %s" % (cid, ps)
    return True, nd.get("fullName", "")
