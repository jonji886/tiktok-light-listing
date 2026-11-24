#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ttl_edit.py — 待发布记录的**一次 edit 到位**工具（2026-11-20 P0，固化当日手写逻辑）。

背景：针线盒全流程实测里，「类目+重量+SKU精简+定价+主图+描述」分散在 4 次手写 edit，
每轮还要等 ERP 回读。本工具一次组装 body 一次提交，默认 dry-run。

数据来源纪律（与 select_gate/apply_listing_fix 一致）：
- 定价：pricing_calc.compute（官方公式），SKU 售价**按各自 source_price 分别算**，
  不用模板 weight 一刀切（模板档只用于申报 package_weight 兜底）。
- SKU 精简：--keep-prefix 按 seller_sku 前缀点名（款型级裁剪，如椭圆款 00012250）；
  零库存剔除；规格名**原样保留**（ERP 自动翻译可用；禁止按序号套色名——11-13 教训）。
- 主图：--mains-file 每行一个 URL（必须 cbu01.alicdn.com——kdzs/dashscope 域 TikTok 拉不到，
  713 实测；非 cbu01 直接拒绝）。不传则保留现状。
- 描述：--template 走 category_template.render（占位未填=拒绝）；或 --desc-file 现成文案。
- 类目：--template 优先，显式 --category-id 覆盖；沉淀前校验 AVAILABLE（category_chain）。

用法（典型：模板品第二件起）：
  python3 scripts/ttl_edit.py --ttl-id <id> --template 收纳盒 \
      --source-prices "00011546-1=7.9,00011546-2=7.9" --stock 1000 --weight-g 300 \
      --dims 12x12x6 --mains-file erp_data/mains.txt \
      --desc-fields "product_name=Kotak Jahit;feature1=...;..." --save
退出码：0 成功（dry-run 或提交） / 1 校验拒绝 / 2 参数问题
"""
import argparse
import copy
import html as H
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from erp_client import _post  # noqa: E402
import pricing_calc as pc     # noqa: E402
import category_template as ct  # noqa: E402

DETAIL = "/item/crossBorder/platformCollect/getDetail"
EDIT = "/item/crossBorder/edit"
OK_MAIN_HOST = "cbu01.alicdn.com"
BAD_MAIN_HOSTS = ("dashscope", "kdzs-jxc-vpc")


def parse_kv(s):
    out = {}
    for part in (s or "").split(","):
        k, _, v = part.partition("=")
        if k.strip():
            out[k.strip()] = v.strip()
    return out


def build_body(d, args):
    v2 = (d.get("reqInfo") or {}).get("tikTokItemModel", {}).get("productCreateRequestV2") or {}
    if not v2.get("skus"):
        return None, None, ["detail 无 skus（可能已被清）"]
    tpls = ct.load_templates()
    tpl = tpls.get(args.template) if args.template else None
    problems = []

    # ── 类目 ──
    cid = args.category_id or (tpl or {}).get("erp_category_id")
    if not cid:
        problems.append("无类目：--template 或 --category-id 必须给一个")
        return None, None, problems
    ids, names, perm = ct.category_chain(cid)
    if "AVAILABLE" not in perm:
        problems.append("类目 %s 权限=%s 不含 AVAILABLE（跨类目红线）" % (cid, perm))
        return None, None, problems
    tree = (tpl or {}).get("erp_category_id_tree") or ids
    ntree = (tpl or {}).get("erp_category_name_tree") or names

    # ── SKU：精简 + 定价 ──
    sp_map = parse_kv(args.source_prices)
    # getattr 兜底：旧测试/旧调用方手工构造 Namespace 时不传新参数（2026-11-24）
    sku_map = {}
    if getattr(args, "sku_map", ""):
        sku_map = json.load(open(args.sku_map, encoding="utf-8"))
    sku_code = {}
    if getattr(args, "sku_code", ""):
        sku_code = json.load(open(args.sku_code, encoding="utf-8"))
    attr_names_map = {}
    if getattr(args, "attr_names", ""):
        attr_names_map = parse_kv(args.attr_names)
    cfg = pc.merged(pc.load_config(), args.site)
    cfg["site"] = args.site
    kept = []
    for s in v2["skus"]:
        ssid = str(s.get("seller_sku") or "")
        if args.keep_prefix and not ssid.startswith(tuple(args.keep_prefix.split("|"))):
            continue
        src = None
        try:
            src = float((s.get("price") or {}).get("source_price") or 0) or None
        except (TypeError, ValueError):
            pass
        if sp_map.get(ssid):
            try:
                src = float(sp_map[ssid])
            except ValueError:
                problems.append("source_prices 值非数字: %s" % ssid)
        if not src:
            problems.append("SKU %s 无 source_price（OAuth/ERP 双缺，禁止猜价）" % ssid)
            continue
        inv = sum((i.get("quantity") or 0) for i in (s.get("inventory") or []))
        if args.drop_zero and inv <= 0:
            continue
        s = copy.deepcopy(s)
        dims_tuple = None
        dims_raw = args.dims or (tpl or {}).get("dims_cm") or ""
        lm0 = re.match(r"(\d+\.?\d*)x(\d+\.?\d*)x(\d+\.?\d*)", str(dims_raw))
        if lm0:
            dims_tuple = tuple(float(x) for x in lm0.groups())
        tgt = pc.compute(cfg, src, args.scheme, args.pieces,
                         float(args.weight_g or (tpl or {}).get("weight_g") or 0) or None,
                         dims_tuple)
        price = round(tgt["price_local"], 2)
        if not tgt["in_band"]:
            problems.append("SKU %s 采购 ¥%s → %s %s 出带 %s（不提交）"
                            % (ssid, src, price, tgt["currency"], tgt["band"]))
            continue
        p = s.setdefault("price", {})
        p["amount"] = p["sale_price"] = str(price)
        p["currency"] = tgt["currency"]
        if args.stock:
            s["inventory"] = [{"quantity": int(args.stock)}]
        # --sku-map：重写规格值为消费者向文案（英文/当地语），按维度顺序对齐。
        # 只改 value_name 不改 name——name 由 --attr-names 统一改，两者分工明确。
        if sku_map.get(ssid):
            vals = sku_map[ssid]
            for i, v in enumerate(vals):
                if i < len(s.get("sales_attributes") or []):
                    s["sales_attributes"][i]["value_name"] = v
        # --sku-code：汉字货号换 ASCII 码（seller_sku 直接进发布 payload，含汉字必拒 [12052250]）
        if sku_code.get(ssid):
            s["seller_sku"] = sku_code[ssid]
        kept.append(s)
    if not kept:
        problems.append("无合格 SKU 可保留")
        # 自解释（2026-11-25 P2）：keep_prefix 不命中是最高频试错点，直接打印可选值
        if args.keep_prefix:
            allss = sorted({str(s.get("seller_sku") or "") for s in v2["skus"]})
            problems.append("  ↳ 草稿实际 seller_sku 共 %d 个（--keep-prefix 从中选）: %s"
                            % (len(allss), ", ".join(allss[:40])))
        return None, None, problems

    m = copy.deepcopy(v2)
    m["skus"] = kept
    m["category_id"] = str(cid)

    # --attr-names：规格属性名译文（ERP 认领不译 name——2026-11-24 本批 5 品全中，
    # 不改则 prepublish [12052250] 汉字拒；name 全 SKU 统一改，确定性规则）。
    if attr_names_map:
        for s in m["skus"]:
            for a in (s.get("sales_attributes") or []):
                nm = str(a.get("name") or "")
                if nm in attr_names_map:
                    a["name"] = attr_names_map[nm]
    # 自解释（2026-11-25 P2）：草稿里含汉字的维度名若没被 --attr-names 覆盖，直接点名
    _cn_names = sorted({str(a.get("name") or "") for s in m["skus"]
                        for a in (s.get("sales_attributes") or [])
                        if re.search(r"[\u4e00-\u9fff]", str(a.get("name") or ""))})
    if _cn_names:
        problems.append("维度名仍含汉字 %s —— 补 --attr-names（注意第二维度常叫\"规格\"不是\"尺寸\"）"
                        % _cn_names)

    # 属性含中文自动清空（2026-11-24 固化：平台曾因 product_attributes 汉字拒发 [12052250]，
    # 一件代发铺货阶段属性非必要不带——prepublish_guard 同款建议的确定性执行版）
    pa = m.get("product_attributes") or []
    if any(re.search(r"[\u4e00-\u9fff]", json.dumps(a, ensure_ascii=False)) for a in pa):
        m["product_attributes"] = [a for a in pa
                                   if not re.search(r"[\u4e00-\u9fff]", json.dumps(a, ensure_ascii=False))]
    if getattr(args, "title", ""):
        # --title：消费者向当地语标题（含汉字直接拒——发布 payload 汉字零容忍）
        if re.search(r"[\u4e00-\u9fff]", args.title):
            problems.append("TITLE_CJK: --title 含汉字，拒绝（标题必须是当地语/英文）")
            return None, None, problems
        m["title"] = args.title

    # ── 申报重量/尺寸（模板档兜底，新品与模板出入>30% 应回改模板） ──
    wkg = args.weight_g or (tpl or {}).get("weight_g")
    if wkg:
        m["package_weight"] = {"value": str(round(float(wkg) / 1000, 3)), "unit": "KILOGRAM"}
    dims = args.dims or (tpl or {}).get("dims_cm")
    if dims:
        lm = re.match(r"(\d+\.?\d*)x(\d+\.?\d*)x(\d+\.?\d*)", str(dims))
        if lm:
            m["package_dimensions"] = {"length": lm.group(1), "width": lm.group(2),
                                       "height": lm.group(3), "unit": "CENTIMETER"}

    # ── 主图（只接受 cbu01；不传保留现状） ──
    if args.mains_file:
        urls = [u.strip() for u in open(args.mains_file, encoding="utf-8") if u.strip()]
        bad = [u for u in urls if any(h in u for h in BAD_MAIN_HOSTS) or OK_MAIN_HOST not in u]
        if bad:
            problems.append("主图域名违规（TikTok 拉不到，713 实测）: %s" % bad[:2])
            return None, None, problems
        if len(urls) < 1:
            problems.append("主图列表为空")
            return None, None, problems
        m["main_images"] = [{"uri": u} for u in urls]

    # ── 描述 ──
    if args.desc_file:
        s = open(args.desc_file, encoding="utf-8").read()
    elif tpl and args.desc_fields:
        fields = {}
        for kv in args.desc_fields:
            k, _, v = kv.partition("=")
            fields[k.strip()] = v
        s, left = ct.render_desc(tpl, fields)
        if left:
            problems.append("描述模板占位未填: %s" % ",".join(sorted(set(left))))
            return None, None, problems
    else:
        s = ""
    if s:
        _cjk_hits = re.findall(r"[\u4e00-\u9fff]+", s)
        if _cjk_hits:
            problems.append("描述含中文，拒绝。命中词: %s（用 sed/python 清掉再提交）"
                            % sorted(set(_cjk_hits))[:10])
            return None, None, problems
        parts = [p.strip() for p in re.split(r"\n\s*\n", s) if p.strip()]
        if ct.plain_len(s) < 500 or len(parts) < 2:
            problems.append("描述质量不足（纯文本 %d/段 %d，要求 ≥500/≥2）" % (ct.plain_len(s), len(parts)))
            return None, None, problems
        m["description"] = "\n".join("<p>%s</p>" % H.escape(p) for p in parts)

    ri = d["reqInfo"]; ttm = ri["tikTokItemModel"]
    eff_title = getattr(args, "title", "") or d.get("title")
    # 汉字终检（2026-11-24 固化）：把 prepublish_guard 的 [12052250] 拦截前移到提交前。
    # 扫整个将要上传的 payload：SKU 码/规格名值、属性名、标题、描述、attribute。
    blob = json.dumps({"skus": m["skus"], "attrs": m.get("product_attributes"),
                       "title": eff_title, "desc": m.get("description")}, ensure_ascii=False)
    hits = re.findall(r"[\u4e00-\u9fff]{2,}", blob)
    if hits:
        problems.append("PAYLOAD_CJK: 提交体仍含汉字 %s——按提示用 --attr-names/--sku-code/--sku-map "
                        "清掉，或该品换货源（禁止带病提交等发布端拒）" % hits[:5])
        return None, m, problems
    body = {"id": str(d.get("id") or args.ttl_id), "platform": "tiktok",
            "sellerId": str(d.get("toSellerId") or ""),
            "currency": d.get("currency") or "MYR",
            "toCategoryId": str(cid), "toCategoryIdTree": tree, "toCategoryNameTree": ntree,
            "title": eff_title,
            "itemModel": {**ri, "tikTokItemModel": {**ttm, "productCreateRequestV2": m}}}
    miss = [k for k in ("sellerId", "toCategoryId", "toCategoryIdTree", "toCategoryNameTree", "title")
            if not body.get(k)]
    if miss:
        problems.append("edit body 关键字段缺失 %s" % miss)
        return None, m, problems
    return body, m, problems


def main():
    ap = argparse.ArgumentParser(description="待发布记录一次 edit 到位（dry-run 默认）")
    ap.add_argument("--ttl-id", required=True)
    ap.add_argument("--template", default="", help="category_templates.yaml 模板名")
    ap.add_argument("--category-id", default="", help="覆盖模板类目")
    ap.add_argument("--site", default="MY", choices=["MY", "TH", "VN"])
    ap.add_argument("--scheme", default="shelf")
    ap.add_argument("--pieces", type=int, default=1)
    ap.add_argument("--source-prices", default="", help="seller_sku=价 逗号分隔（缺省用 ERP source_price）")
    ap.add_argument("--keep-prefix", default="", help="seller_sku 前缀白名单，| 分隔（款型裁剪）")
    ap.add_argument("--drop-zero", action="store_true", help="剔除零库存 SKU")
    ap.add_argument("--stock", default="", help="统一收敛库存（SKU 上限纪律：单SKU≤300）")
    ap.add_argument("--weight-g", default="", help="申报重量档（g），缺省用模板")
    ap.add_argument("--dims", default="", help="LxWxH cm，缺省用模板")
    ap.add_argument("--mains-file", default="", help="主图 URL 清单文件（每行一个，须 cbu01）")
    ap.add_argument("--desc-file", default="", help="成品描述纯文本（空行分段）")
    ap.add_argument("--desc-fields", action="append", default=[], help="渲染占位 k=v 多次")
    # ── 2026-11-24 固化（edit_my5.py 一次性脚本并进主工具；本批 5 品实测的三项末端拦截）──
    ap.add_argument("--title", default="", help="消费者向当地语标题（MY=马来语，须命中 ≥3 热搜词根）")
    ap.add_argument("--attr-names", default="",
                    help='属性名译文 "颜色=Warna,尺寸=Saiz,规格=Saiz"（ERP 不译属性名，不改必被汉字拒）')
    ap.add_argument("--sku-map", default="",
                    help="JSON {原seller_sku: [维度值1,值2]}，重写 value_name 为英文/当地语（消费者向）")
    ap.add_argument("--sku-code", default="",
                    help="JSON {原货号: ASCII码}（linkfox_oauth apply 自动产出 erp_data/sku_code_map.json）")
    ap.add_argument("--save", action="store_true", help="真正提交（写操作）")
    args = ap.parse_args()

    d = _post(DETAIL, {"id": args.ttl_id}, None)
    body, m, problems = build_body(d, args)
    if problems:
        for p in problems:
            print("[ttl_edit] ✗ %s" % p, file=sys.stderr)
        return 1
    skus = m["skus"]
    print("[ttl_edit] 类目=%s(%s) SKU=%d 价=%s 重量=%s 主图=%s desc=%s"
          % (body["toCategoryId"], body["toCategoryNameTree"], len(skus),
             [s["price"]["amount"] for s in skus], (m.get("package_weight") or {}).get("value"),
             len(m.get("main_images") or []), ct.plain_len(m.get("description") or "")))
    if not args.save:
        print("[ttl_edit] dry-run（加 --save 提交）")
        return 0
    r = _post(EDIT, body, None)
    print("[ttl_edit] edit 提交: %s" % json.dumps(r, ensure_ascii=False)[:150])
    # P3 顺序纪律（2026-11-23）：edit 会改写 detail → 旧 review 判定表整批作废
    # （683 批次实测：apply 时报"21/24 判定已失效"，重扫重判多花 5 分钟）。
    # 正确顺序 = mains/desc **全部定稿**（本脚本 --save）之后，才进 image_gate scan→判定→apply。
    print("[ttl_edit] ⚠ 本商品 detail 已被改写：此前所有 image_gate review.json 判定作废。"
          "\n    若后面还要走图片闸门：现在才是 scan 的正确时机（顺序=mains/desc定稿→scan→判定→apply，"
          "scan 之后不要再 --save 本脚本，否则又得重判）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
