# -*- coding: utf-8 -*-
"""测试公共装配：把 scripts/ 与 run 目录挂上 sys.path，全部离线可跑。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
RUN_VN20 = os.path.join(ROOT, "erp_data", "_run", "2026-11-12_vn20")
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

for p in (SCRIPTS, RUN_VN20):
    if p not in sys.path:
        sys.path.insert(0, p)


def detail_v2(main_images=(), desc_imgs=(), sku_imgs=(), title="t"):
    """造一个最小可用 getDetail 结构（层级与真实响应一致：reqInfo.tikTokItemModel.productCreateRequestV2）。"""
    as_list = lambda v: [v] if isinstance(v, str) else list(v)
    main_images, desc_imgs, sku_imgs = as_list(main_images), as_list(desc_imgs), as_list(sku_imgs)
    desc = "".join('<div><img src="%s" alt="" /></div>' % u for u in desc_imgs)
    skus = []
    for i, u in enumerate(sku_imgs):
        skus.append({
            "seller_sku": "sku%d" % i,
            "price": {"amount": "100", "currency": "THB", "source_price": "5"},
            "inventory": [{"quantity": 10}],
            "sales_attributes": [{"name": "สี", "value_name": "v%d" % i, "sku_img": {"uri": u}}],
        })
    v2 = {
        "title": title,
        "description": desc,
        "main_images": [{"uri": u} for u in main_images],
        "skus": skus,
    }
    return {
        "id": "1570393952287219712",
        "toSellerId": "<SELLER_ID>",
        "toSellerNick": "<SHOP_NICK>",
        "toCategoryId": 852744,
        "toCategoryIdTree": "600001,600584,852744",
        "toCategoryNameTree": "家居用品,家居收纳,收纳包",
        "currency": "THB",
        "title": title,
        "reqInfo": {"shopeeItemModel": None, "temuItemModel": None,
                    "tikTokItemModel": {"productCreateRequestV2": v2}},
    }


def review(urls, verdict="L3_keep", uri_map=None, localized_text=""):
    if isinstance(urls, str):
        urls = [urls]
    return {
        "item_id": "1570393952287219712",
        "uri_map": uri_map or {},
        "localized_text": localized_text,
        "images": [{"url": u, "verdict": verdict, "seen_text": "", "confidence": 0.9, "note": ""}
                   for u in urls],
    }
