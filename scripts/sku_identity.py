#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sku_identity.py — SKU 身份校验纯函数（可离线回归）

来源：2026-11-13 审查期在 VN20 批运行目录（erp_data/_run/2026-11-12_vn20/
vn20_editor.py，未入库的一次性脚本）里固化并验证过的两个纯函数，2026-11-27
提取入库——当时测试 import 运行目录模块，导致干净克隆 CI 直接 ImportError
（工具与一次性脚本混放的代价，如实记录）。逻辑与原实现逐行一致。

为什么需要（VN20 批实测）：59/70 个 SKU 与同品其它 SKU 采购价完全相同，
仅靠 ±0.011 的价格断言，索引错位时照样通过 → 颜色/尺寸/库存挂到错误变体。
因此「按位置回退」必须另有身份证据：skuId 相等，或规格串可直接比对且一致。
两者都拿不到、且同品存在同价 SKU 时，拒绝写入（转人工），不赌。
"""
import re


def spec_identity_matches(want, sku):
    """源规格串与 ERP 规格值是否一致。

    ERP 若已把规格名翻成泰/越文，就无法反查中文原文 → 返回 None（未知），
    不把它当成「校验通过」（2026-11-13 审查：假装通过比不校验更危险）。
    """
    want_spec = str(want.get('source_spec_attrs') or '').strip()
    attrs = (sku.get('sales_attributes') or [{}])
    got = str((attrs[0] if attrs else {}).get('value_name') or '').strip()
    if not want_spec or not got or not re.search(r'[\u4e00-\u9fff]', got):
        return None
    return want_spec == got


def positional_fallback_allowed(want, sku, idx, dup_price):
    """走「按位置回退」前的身份校验；返回 None 放行，返回字符串为拒绝原因。"""
    sid = str(want.get('source_sku_id') or '').strip()
    got = str(sku.get('seller_sku') or '').strip()
    if sid and got and sid == got:
        return None
    if spec_identity_matches(want, sku) is True:
        return None
    if dup_price:
        return (f'SKU 身份无法证明 @idx{idx}: ERP seller_sku={got or "(空)"} ≠ payload '
                f'source_sku_id={sid or "(空)"}，且同品存在相同采购价的 SKU、规格名已被翻译'
                f'（无法反查原文）→ 拒绝按位置写入，否则会挂错变体')
    return None
