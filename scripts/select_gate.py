#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采集（collect）前选品硬闸：把 Step 0 的四道供应链门槛变成机器拦截。

为什么需要（2026-11-12 第二批 10 品白跑 6 个的根因）：
  clean_consumer_images_min=3 等门槛只写在 selection.yaml / strategy 5.2，
  选品时没有任何脚本强制执行，5 品铺到图片闸门才发现 0 张干净主图、
  1 品只能落 NON_MAIN_CATEGORY，采集→认领→编辑全流程白跑。
  price_gate / image_gate / prepublish_guard 都在 collect 之后，
  本脚本补的是 collect 之前的那道闸，退出码 1 时禁止 erp_client.py collect。

定位（与 image_gate 同一哲学）：
  纯标准库、本地只读、不联网、不看图。图片是否干净、客服是否书面确认、
  是否真包邮，由人工/多模态目视后把结论回填进候选 CSV，本脚本只认回填证据，
  缺证据或证据不达阈值即阻断——它不替代目视，它保证目视结论不被跳过。

候选 CSV 必需列（模板：python3 scripts/select_gate.py --init-template）：
  offer_id            1688 offerId
  region              TH / VN
  cost_cny            真实单件采购价（可履约的最低单件价）
  max_sku_cost_cny    最高 SKU 采购价（多规格货源必填；单规格可与 cost_cny 相同）
  clean_main_images   多模态目视后的干净消费者向主图数（阈值 clean_consumer_images_min=3）
  desc_rebuildable    true/false，详情长图能否整图删后用干净图+当地语文本重建
  stock               商品页库存
  min_order_qty       最小起订量（阈值 1）
  ship_transit_days   发货城市→货代仓快递天数（阈值 max_transit_days=1）
  seller_48h_proof    true/false，48h 发货是否有客服书面确认（页面标注不算）
  free_shipping       true/false，是否真包邮（无满件/隐形条件，true_free_shipping=required）
  free_shipping_proof 包邮证据文本（非空，如「1件起包邮，客服确认无满件条件」）
  weight_g            单件实重 g（与 dims_cm 至少给一个，否则无法验抛重）
  dims_cm             包装长宽高 cm，如 20x15x8；抛重=L×W×H÷6000×1000，与实重取大
  redline_check       红线核查结论，必须含 pass（strategy 5.1 十条红线）
  pieces              必须为 1 或留空（一件代发禁多件装，见 pricing_calc 硬闸）
推荐列（给值才校验，缺失只 WARN）：
  category_id         平台类目 ID；非空时必须在家居用品树内且类目可用
  ship_city / forwarder_city  地域聚簇信息（strategy 2.0）

用法：
  python3 scripts/select_gate.py --init-template                 # 生成候选 CSV 模板
  python3 scripts/select_gate.py --csv erp_data/selection_candidates.csv
  python3 scripts/select_gate.py --csv ... --json

退出码：0 全部通过（WARN 不阻断）；1 存在阻断项（禁止 collect）；2 输入文件问题
"""
import argparse
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from pricing_calc import load_config, merged, compute  # noqa: E402

ROOT = os.path.dirname(HERE)
SELECTION_YAML = os.path.join(ROOT, "config", "selection.yaml")
DEFAULT_CSV = os.path.join(ROOT, "erp_data", "selection_candidates.csv")
CATEGORY_TREE = os.path.join(ROOT, "erp_data", "case_category_tree.json")
HOME_CATEGORY_ROOT = "600001"   # 家居用品根类目

# 阈值兜底（正常应从 selection.yaml 读取覆盖；与 config 当前值保持一致）
DEFAULTS = {
    "clean_consumer_images_min": 3,
    "stock_min": 200,
    "min_order_qty": 1,
    "chargeable_weight_max_g": 400,
    "max_transit_days": 1,
    "true_free_shipping": "required",
    "seller_ships_48h_proof": "required",
}

REQUIRED_COLS = [
    "offer_id", "region", "cost_cny", "max_sku_cost_cny", "clean_main_images",
    "desc_rebuildable", "stock", "min_order_qty", "ship_transit_days",
    "seller_48h_proof", "free_shipping", "free_shipping_proof",
    "weight_g", "dims_cm", "redline_check", "pieces",
]

TEMPLATE_HEADER = [
    "offer_id", "offer_url", "title_zh", "region", "category_id",
    "cost_cny", "max_sku_cost_cny", "pieces",
    "clean_main_images", "desc_rebuildable",
    "stock", "min_order_qty", "ship_transit_days", "seller_48h_proof",
    "free_shipping", "free_shipping_proof",
    "weight_g", "dims_cm", "redline_check",
    "ship_city", "forwarder_city", "domestic_shipping_cny", "markup",
    "seller_48h_proof_text", "reason",
]


# ── selection.yaml 标量阈值读取（只取需要的标量，内联列表由定价配置负责）──
def load_thresholds():
    th = dict(DEFAULTS)
    if not os.path.exists(SELECTION_YAML):
        return th
    found = {}
    for raw in open(SELECTION_YAML, encoding="utf-8"):
        m = re.match(r"^\s*([a-z0-9_]+)\s*:\s*([^#]+?)\s*(?:#.*)?$", raw.rstrip("\n"))
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip().strip("\"'")
        if key in DEFAULTS:
            found.setdefault(key, []).append(val)
    for key, vals in found.items():
        if key in ("true_free_shipping", "seller_ships_48h_proof"):
            th[key] = vals[-1].lower()
            continue
        nums = []
        for v in vals:
            try:
                nums.append(float(v))
            except ValueError:
                pass
        if nums:
            # 同名阈值出现在 TH/VN 多节点时取最严（最小值）
            th[key] = int(min(nums)) if all(n == int(n) for n in nums) else min(nums)
    return th


def fnum(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError, AttributeError):
        return None


def fbool(v):
    s = str(v or "").strip().lower()
    if s in ("true", "1", "yes", "y", "是"):
        return True
    if s in ("false", "0", "no", "n", "否"):
        return False
    return None


def parse_dims(v):
    """'20x15x8' / '20*15*8' -> (20,15,8)；空/非法返回 (None, 错误串)。"""
    s = str(v or "").strip().lower().replace("*", "x")
    if not s:
        return None, None
    parts = s.split("x")
    if len(parts) != 3:
        return None, f"dims_cm 格式错误: {v!r}（需形如 20x15x8）"
    try:
        return tuple(float(p) for p in parts), None
    except ValueError:
        return None, f"dims_cm 含非数字: {v!r}"


# ── 家居类目树 ──
def load_home_category_ids():
    """返回家居用品根下全部 categoryId 集合；文件缺失时返回 None（调用方降级为 WARN）。"""
    if not os.path.exists(CATEGORY_TREE):
        return None
    tree = json.load(open(CATEGORY_TREE, encoding="utf-8"))
    ids = set()

    def walk(node):
        cid = str(node.get("categoryId", ""))
        if cid:
            ids.add(cid)
        for ch in node.get("children") or []:
            walk(ch)

    home_root = None
    for node in tree.get("data") or []:
        if str(node.get("categoryId")) == HOME_CATEGORY_ROOT:
            home_root = node
            break
    if home_root is None:
        return set()
    walk(home_root)
    return ids


def judge(row, idx, thr, price_cfg_all, home_ids):
    issues, warns = [], []
    ident = str(row.get("offer_id") or "").strip() or f"第{idx + 1}行"

    def miss(col):
        issues.append(f"MISSING_FIELD: 必需列 {col} 为空或无法解析")

    region = str(row.get("region") or "").strip().upper()
    if region not in ("TH", "VN"):
        issues.append(f"REGION_INVALID: region={row.get('region')!r}（只接受 TH/VN）")
        return ident, issues, warns, None

    # ── 字段解析（缺失逐项报，不提前 return，一次给全问题）──
    cost = fnum(row.get("cost_cny"))
    if cost is None or cost <= 0:
        miss("cost_cny")
    max_sku = fnum(row.get("max_sku_cost_cny"))
    if max_sku is None or max_sku <= 0:
        if cost and cost > 0:
            max_sku = cost            # 单规格货源容忍缺列，回落到单件采购价
        else:
            miss("max_sku_cost_cny")

    pieces_raw = str(row.get("pieces") or "").strip()
    pieces = 1
    if pieces_raw:
        p = fnum(row.get("pieces"))
        if p is None:
            miss("pieces")
        elif int(p) != p or p != 1:
            issues.append("PIECES_BUNDLE: pieces 必须为 1（一件代发禁多件装，"
                          "捆绑发货能力 UNKNOWN，见 pricing_calc --allow-bundle）")
        else:
            pieces = int(p)

    imgs = fnum(row.get("clean_main_images"))
    if imgs is None:
        miss("clean_main_images")
    elif imgs < thr["clean_consumer_images_min"]:
        issues.append(f"IMG_CLEAN_LT3: 干净消费者向主图 {imgs:g} 张 "
                      f"< {thr['clean_consumer_images_min']} 张（选品线，不是发布线 1 张）")

    if fbool(row.get("desc_rebuildable")) is not True:
        issues.append("DESC_NOT_REBUILDABLE: desc_rebuildable 非 true，详情长图无法干净重建")

    stock = fnum(row.get("stock"))
    if stock is None:
        miss("stock")
    elif stock < thr["stock_min"]:
        issues.append(f"STOCK_LOW: 库存 {stock:g} < {thr['stock_min']}，一周内必断货")

    moq = fnum(row.get("min_order_qty"))
    if moq is None:
        miss("min_order_qty")
    elif moq > thr["min_order_qty"]:
        issues.append(f"MOQ_GT1: 起订量 {moq:g} > {thr['min_order_qty']}（隐形起订门槛，不满足一件代发）")

    transit = fnum(row.get("ship_transit_days"))
    if transit is None:
        miss("ship_transit_days")
    elif transit > thr["max_transit_days"]:
        issues.append(f"TRANSIT_OVER1: 快递到仓 {transit:g} 天 > {thr['max_transit_days']} 天"
                      "（红/黄档不进 collect，先换发货地）")

    seller_proof_text = str(row.get("seller_48h_proof_text") or "").strip()
    if fbool(row.get("seller_48h_proof")) is not True:
        issues.append("NO_48H_PROOF: 缺供应商 48h 发货有效书面证据")
    elif thr["seller_ships_48h_proof"] == "platform_compensation_or_chat":
        if not seller_proof_text or not any(k in seller_proof_text for k in ("晚发必赔", "延必赔", "客服书面")):
            issues.append("NO_48H_PROOF_TEXT: seller_48h_proof_text 需记录晚发必赔书面条款或客服书面确认")

    # 国内运费：required=必须真包邮；cost_in_price=允许把逐品页面运费计入成本
    free_ship = fbool(row.get("free_shipping"))
    proof_text = str(row.get("free_shipping_proof") or "").strip()
    domestic_shipping = fnum(row.get("domestic_shipping_cny"))
    if thr["true_free_shipping"] == "required":
        if free_ship is not True:
            issues.append("FREE_SHIP_REQUIRED: free_shipping 非 true（selection.yaml 要求真包邮，"
                          "警惕「满3件才包邮」的假包邮）")
        if not proof_text:
            issues.append("FREE_SHIP_PROOF: free_shipping_proof 为空，需记录包邮条件核查证据")
    elif thr["true_free_shipping"] == "cost_in_price":
        if free_ship is False and (domestic_shipping is None or domestic_shipping < 0):
            issues.append("DOMESTIC_SHIPPING_MISSING: 非包邮货源必须填 domestic_shipping_cny 页面运费")
        if free_ship is False:
            warns.append(f"国内运费 ¥{domestic_shipping:g} 已计入成本；页面报价不等于实际货代仓最终报价")
        elif not proof_text:
            issues.append("FREE_SHIP_PROOF: 包邮货源需记录包邮条件核查证据")
    elif free_ship is False:
        warns.append("该货源非包邮：确认含邮总价仍在采购价边界内（strategy 3.4 双轨定价）")

    # ── 抛重 / 实重 ──
    weight = fnum(row.get("weight_g"))
    dims, dim_err = parse_dims(row.get("dims_cm"))
    if dim_err:
        issues.append(f"DIMS_BAD: {dim_err}")
    if weight is None and dims is None:
        issues.append("MISSING_FIELD: weight_g 与 dims_cm 至少给一个，否则无法验证 400g 抛重硬闸")
    chargeable = None
    if weight is not None or dims is not None:
        cfg_site = merged(price_cfg_all, region)
        divisor = cfg_site.get("volumetric_divisor", 6000)
        vol = (dims[0] * dims[1] * dims[2] / divisor * 1000) if dims else None
        chargeable = max(x for x in (weight, vol) if x is not None)
        if chargeable > thr["chargeable_weight_max_g"]:
            issues.append(f"WEIGHT_OVER400: 计费重 {chargeable:.0f}g "
                          f"> {thr['chargeable_weight_max_g']}g（实重/抛重取大，按抛重判不按直觉）")

    # ── 价格带：用最高 SKU 采购价现场重算，不信任人工填写的 in_band ──
    price_result = None
    if max_sku and cost and region in ("TH", "VN"):
        cfg_site = merged(price_cfg_all, region)
        cfg_site["site"] = region
        markup_override = fnum(row.get("markup"))
        if markup_override is not None and markup_override <= 1:
            issues.append(f"MARKUP_INVALID: markup={markup_override:g} 必须大于 1")
            markup_override = None
        if domestic_shipping is not None:
            cfg_site["domestic_shipping"] = domestic_shipping
        if markup_override is not None:
            cfg_site["schemes"] = {
                **cfg_site["schemes"],
                "shelf": {**cfg_site["schemes"]["shelf"], "markup": markup_override},
            }
        price_result = compute(cfg_site, max_sku, "shelf", pieces,
                               weight, dims, None)
        if not price_result["in_band"]:
            issues.append(
                f"PRICE_OOB: 按最高 SKU 采购价 ¥{max_sku}/{price_result['weight_g']:.0f}g 重算，"
                f"客单价 ${price_result['order_usd']} 落在价格带 "
                f"${price_result['band'][0]}–{price_result['band'][1]} 之外 → 换品，不要硬调 markup")

    # ── 类目（推荐列：给值才硬校验，缺值提醒）──
    cat = str(row.get("category_id") or "").strip()
    if cat:
        if home_ids is None:
            warns.append(f"类目树文件缺失，无法校验 category_id={cat}")
        elif cat not in home_ids:
            issues.append(f"CATEGORY_NOT_HOME: category_id={cat} 不在家居用品（{HOME_CATEGORY_ROOT}）"
                          "可用类目树内，跨类目/不可发类目会被下架")
    else:
        warns.append("category_id 为空：认领后必须复核 ERP 匹配结果属家居用品 AVAILABLE 类目")

    if not str(row.get("ship_city") or "").strip() or not str(row.get("forwarder_city") or "").strip():
        warns.append("ship_city/forwarder_city 未填齐，地域聚簇与货代覆盖无法复核（strategy 2.0）")

    if "pass" not in str(row.get("redline_check") or "").lower():
        issues.append("REDLINE_UNCHECKED: redline_check 不含 pass（strategy 5.1 十条红线须逐条核查留痕）")

    return ident, issues, warns, price_result


def write_template(path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerow(TEMPLATE_HEADER)
    return path


def main():
    ap = argparse.ArgumentParser(description="collect 前选品硬闸（纯本地只读，退出码 1 = 禁止采集）")
    ap.add_argument("--csv", default=DEFAULT_CSV, help="候选清单 CSV 路径")
    ap.add_argument("--init-template", action="store_true", help="在默认路径生成带表头的候选 CSV 模板后退出")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.init_template:
        p = write_template(DEFAULT_CSV)
        print(f"[gate] 已生成候选清单模板：{p}")
        print("       按列回填人工/多模态核查证据后，重跑本脚本（不带 --init-template）。")
        return 0

    if not os.path.exists(a.csv):
        sys.stderr.write(f"[gate] 找不到候选清单 {a.csv}\n"
                         f"       先生成模板：python3 scripts/select_gate.py --init-template\n")
        return 2

    with open(a.csv, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        header = reader.fieldnames or []
    if not rows:
        sys.stderr.write(f"[gate] {a.csv} 没有数据行\n")
        return 2

    missing_cols = [c for c in REQUIRED_COLS if c not in header]
    if missing_cols:
        sys.stderr.write(f"[gate] CSV 缺少必需列：{', '.join(missing_cols)}\n"
                         "       用 --init-template 生成的表头对齐字段后重试\n")
        return 2

    thr = load_thresholds()
    price_cfg_all = load_config()
    home_ids = load_home_category_ids()

    results = []
    for i, row in enumerate(rows):
        ident, issues, warns, price_result = judge(row, i, thr, price_cfg_all, home_ids)
        results.append({"offer_id": ident, "issues": issues, "warns": warns,
                        "price": price_result})

    n_block = sum(1 for r in results if r["issues"])
    n_warn = sum(1 for r in results if r["warns"] and not r["issues"])

    if a.json:
        print(json.dumps({"thresholds": thr, "total": len(rows),
                          "blocked": n_block, "results": results},
                         ensure_ascii=False, indent=2))
    else:
        print("=== select_gate（collect 前硬闸，只读；脚本不看图，只认回填证据）===")
        print(f"[gate] 共 {len(rows)} 条 | 阻断 {n_block} | 仅提醒 {n_warn} "
              f"| 通过 {len(rows) - n_block}\n")
        for r in results:
            mark = "✗" if r["issues"] else ("!" if r["warns"] else "✓")
            price_note = ""
            if r["price"]:
                p = r["price"]
                price_note = f" 客单${p['order_usd']} 带${p['band'][0]}-{p['band'][1]}"
            print(f"{mark} {r['offer_id']}{price_note}")
            for x in r["issues"]:
                print(f"   ✗ {x}")
            for x in r["warns"]:
                print(f"   ! {x}")
        print()
        if n_block:
            print(f"[gate] ❌ 阻断 {n_block} 条 —— 禁止 erp_client.py collect；"
                  "在 1688 换货源或补齐证据后重跑，直到退出码 0。")
        else:
            print("[gate] ✅ 全部通过，可把 offer_id 传入 Step 1 collect"
                  "（写操作仍需用户明确点名清单）。")
    return 1 if n_block else 0


if __name__ == "__main__":
    sys.exit(main())
