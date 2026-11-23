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
图片供给预测列（P0-3，2026-11-19 新增；**2026-11-23 口径收紧**；表头存在即强制，缺列只 WARN）：
  clean_main_images_after_fix  「可发布」干净主图数——**只认 cbu01.alicdn.com 原链**：
    = L3_keep 的 cbu01 主图 + 可降级复用为发布主图的干净 cbu01 SKU/详情图。
    ⛔ image_fix 修图救回的 L1/L2 **不计入**：产物落 kdzs-vpc/dashscope 域，
      ttl_edit --mains-file 白名单只收 cbu01，且 vpc 链实测 1 小时内 404（2026-11-20 晚 683 重擦即失效）。
    发布硬闸 min_publish_main_images=5：此列 <5 = 该货源发布必挂，选品期直接换源，别进采集。
    证据来源：scripts/hero_main_images.py 款型级预检（唯一正确入口），selection_intake 拉主图 +
    image_fix probe 只用于识别 L3_keep（探针判干净），不是"修完就能发布"。
价格竞争力证据列（P0-1，2026-11-18 新增；表头存在即强制，旧清单缺列只 WARN）：
  market_median_price_usd  站内同款中位价（USD，买家实付口径要注明）
  market_price_source      来源：官方价格诊断 / fastmoss:floorPrice / aibuy:opprank
  market_price_n           样本量（< market_price_min_sample 只 WARN）
  market_price_sampled_at  采集日期 YYYY-MM-DD（价格是时效数据）
推荐列（给值才校验，缺失只 WARN）：
  category_id         平台类目 ID；非空时必须在家居用品树内且类目可用
  ship_city / forwarder_city  地域聚簇信息（strategy 2.0）

官方重点类目信号（2026-11-18 新增）：
  config/official_focus_categories.json 收录官方 2026 ✅ 重点类目（家居树内 18 项 + 跨类目参考）。
  在「category_id 对应类目名 + title_zh」上匹配：命中在结果标 [官方✅:...]，未命中只 WARN。
  ✅ 是平台「优先方向」不是准入白名单，未命中绝不阻断 collect。
  来源页面元数据 2024-09 / 正文署名 2026-01，时间不一致已写进配置文件。

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
FOCUS_CONFIG = os.path.join(ROOT, "config", "official_focus_categories.json")

# 阈值兜底（正常应从 selection.yaml 读取覆盖；与 config 当前值保持一致）
DEFAULTS = {
    "clean_consumer_images_min": 3,
    # 2026-11-19：图片供给预测闸。发布硬闸 min_publish_main_images=5 已落地（prepublish_guard），
    # 货源在选品期就要预测「修复后能凑几张干净主图」：L3_keep + 可修L1/L2 + 可提升SKU/详情图。
    # 不足 5 的货源最终必被发布闸拦截（围裙 5→1 实测），选品期拦下才不白跑。
    "clean_images_after_fix_min": 5,
    "stock_min": 200,
    "min_order_qty": 1,
    "chargeable_weight_max_g": 400,
    "max_transit_days": 1,
    "true_free_shipping": "required",
    "seller_ships_48h_proof": "off",  # 2026-11-20 用户指示暂不阻断；机验档 machine_or_chat 能力已就绪待启用
    # 价格竞争力闸（P0-1）：挂价上限相对「站内同款中位价」的倍数
    "market_price_tolerance": 1.05,
    "market_price_min_sample": 5,
}

# 价格竞争力证据列：只在表头存在时强制（旧清单缺列 → WARN，不静默通过）
MARKET_COLS = [
    "market_median_price_usd", "market_price_source",
    "market_price_n", "market_price_sampled_at",
]

# 图片供给预测列（2026-11-19）：同样"表头存在即强制"，缺列只 WARN
SUPPLY_COL = "clean_main_images_after_fix"

REQUIRED_COLS = [
    "offer_id", "region", "cost_cny", "max_sku_cost_cny", "clean_main_images",
    "desc_rebuildable", "stock", "min_order_qty", "ship_transit_days",
    "seller_48h_proof", "free_shipping", "free_shipping_proof",
    "weight_g", "dims_cm", "redline_check", "pieces",
]

TEMPLATE_HEADER = [
    "offer_id", "offer_url", "title_zh", "region", "category_id",
    "cost_cny", "max_sku_cost_cny", "pieces",
    "clean_main_images", "desc_rebuildable", "clean_main_images_after_fix",
    "stock", "min_order_qty", "ship_transit_days", "seller_48h_proof",
    "free_shipping", "free_shipping_proof",
    "weight_g", "dims_cm", "redline_check",
    "market_median_price_usd", "market_price_source", "market_price_n",
    "market_price_sampled_at",
    "ship_city", "forwarder_city", "domestic_shipping_cny", "markup",
    "seller_48h_proof_text", "late_ship_compensate", "pickup_24h_rate", "reason",
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


# ── 官方 2026 ✅ 重点类目（优先方向，非准入白名单）──
def load_official_focus():
    """返回官方重点类目配置；文件缺失返回 None（调用方降级为不检查）。"""
    if not os.path.exists(FOCUS_CONFIG):
        return None
    try:
        with open(FOCUS_CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return None


def build_category_name_map():
    """categoryId -> 自身与祖先名称串（小写），用于把平台类目 ID 对回官方中英文名。"""
    if not os.path.exists(CATEGORY_TREE):
        return {}
    with open(CATEGORY_TREE, encoding="utf-8") as f:
        tree = json.load(f)
    names = {}

    def walk(node, ancestors):
        cid = str(node.get("categoryId", ""))
        here = [str(node.get("name") or ""), str(node.get("fullName") or "")] + list(ancestors)
        if cid:
            names[cid] = " ".join(x for x in here if x).lower()
        for child in node.get("children") or []:
            walk(child, here)

    for node in tree.get("data") or []:
        walk(node, [])
    return names


def match_official_focus(text, focus_cfg):
    """在「类目名称路径 + 候选标题」上匹配官方 ✅ 关键词。

    返回 (hit/miss/unknown, 命中的中文类目名列表)。
    未命中不阻断：官方 ✅ 是优先方向，不是准入资格。
    """
    if not focus_cfg or not str(text or "").strip():
        return "unknown", []
    hay = str(text).lower()
    hits = []
    for cat in focus_cfg.get("categories", []):
        if any(str(k).lower() in hay for k in cat.get("keywords", [])):
            hits.append(cat.get("zh") or cat.get("en") or "")
    return ("hit" if hits else "miss"), hits


def judge(row, idx, thr, price_cfg_all, home_ids, market_gate=False,
          focus_cfg=None, cat_names=None):
    issues, warns = [], []
    ident = str(row.get("offer_id") or "").strip() or f"第{idx + 1}行"

    def miss(col):
        issues.append(f"MISSING_FIELD: 必需列 {col} 为空或无法解析")

    region = str(row.get("region") or "").strip().upper()
    if region not in ("TH", "VN", "MY"):
        issues.append(f"REGION_INVALID: region={row.get('region')!r}（只接受 TH/VN/MY）")
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

    # 图片供给预测闸（2026-11-19；**2026-11-23 口径修正**）：发布硬闸要 5 张，选品期就得预测。
    # ⚠ 修正原因（683 换源 2 次教训）：旧口径把「image_fix 修图救回的 L1/L2」计入供给，
    #   但修图产物落 kdzs-vpc 域——ttl_edit --mains-file 白名单只收 cbu01，且实测 vpc 链
    #   1 小时内 404（2026-11-20 晚 main_01 重擦）。**修复救回 = 发布不可用，不得计入**。
    # 新口径 = 干净 cbu01 原链图（main L3_keep + 款型无关限制已解除：SKU/详情图降级复用仍是正路）。
    # 表头存在才强制（同 MARKET_COLS 渐进策略，旧清单只 WARN）；<5 = 发布必挂，别浪费采集坑位。
    fix_imgs = fnum(row.get("clean_main_images_after_fix"))
    if "clean_main_images_after_fix" in row:
        if fix_imgs is None:
            miss("clean_main_images_after_fix")
        elif fix_imgs < thr["clean_images_after_fix_min"]:
            issues.append(
                f"IMG_SUPPLY_LT5: 可发布干净主图（cbu01 原链口径，修复救回不计）仅 {fix_imgs:g} 张 < "
                f"{thr['clean_images_after_fix_min']}（发布硬闸会拦；补图路径=SKU图/详情图降级复用"
                "（须 cbu01 原链）、或换图源充足的同款供应商。image_fix 修图救回**不可**用于发布主图）")
    else:
        warns.append("IMG_SUPPLY_UNPREDICTED: 缺 clean_main_images_after_fix 列——"
                     "发布闸要 5 张主图（只认 cbu01 原链，修图产物落 vpc 域发布必 404），"
                     "选品期先跑 hero_main_images.py 预检再填")

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
    # 48h 证据闸（2026-11-20 升级：机验档优先）。三档模式：
    #   off                              = 只 WARN 不阻断（2026-11-19 临时态，保留可回退）
    #   platform_compensation_or_chat    = 只认人工书面（旧行为）
    #   machine_or_chat（新默认）        = 百炼官方标签「晚发必赔=Y」机验通过 → 免人工书面；
    #       揽收率≥95% 但无赔付承诺 → 放行+WARN；两者皆缺/无 bailian 数据 → 回退人工书面要求。
    # 依据：晚发必赔=供应商向平台投保的自动赔付承诺，证据等级高于客服口头确认（wiki.1688 分销白皮书）；
    # TikTok 侧 LDR≥4% 即警告限单，履约红线第一权重。
    # ⚠ 揽收率 0.0 与"无数据"不可区分（2026-11-20 实测：30天零成交新品必为 0.0）→ 一律按 UNKNOWN，不判 FAIL。
    lsc = str(row.get("late_ship_compensate") or "").strip().upper()
    p24 = fnum(row.get("pickup_24h_rate"))
    p24_known = p24 is not None and p24 > 0
    mode48 = thr["seller_ships_48h_proof"]
    if mode48 == "off":
        if fbool(row.get("seller_48h_proof")) is not True and lsc != "Y":
            warns.append("48H_PROOF_SKIPPED(拦截已关闭): 无 48h 书面证据；出延迟发货事故立即改回阻断")
    elif mode48 == "machine_or_chat":
        if lsc == "Y":
            pass  # 官方机验通过（bailian serviceTags.is_late_ship_compensate）
        elif p24_known and p24 >= 0.95:
            warns.append("48H_MACHINE_SOFT: 24h揽收率 %.2f≥0.95 但无晚发必赔；放行但履约兜底缺失，"
                         "爆单前补书面确认或换供应商" % p24)
        elif fbool(row.get("seller_48h_proof")) is not True:
            issues.append("NO_48H_PROOF: 无晚发必赔标签(bailian 未覆盖或=N)且无人工书面——"
                          "跑 bailian 通道取 is_late_ship_compensate，或客服书面确认填 seller_48h_proof")
        elif not seller_proof_text or not any(k in seller_proof_text for k in ("晚发必赔", "延必赔", "客服书面")):
            issues.append("NO_48H_PROOF_TEXT: seller_48h_proof_text 需记录晚发必赔书面条款或客服书面确认")
    else:  # platform_compensation_or_chat（纯人工档，旧行为）
        if fbool(row.get("seller_48h_proof")) is not True:
            issues.append("NO_48H_PROOF: 缺供应商 48h 发货有效书面证据")
        elif not seller_proof_text or not any(k in seller_proof_text for k in ("晚发必赔", "延必赔", "客服书面")):
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
        if free_ship is False and domestic_shipping is not None and domestic_shipping >= 0:
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
    if max_sku and cost and region in ("TH", "VN", "MY"):
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

    # ── 价格竞争力（P0-1）：挂价不得高于站内同款中位价 × tolerance ──
    # 价格带校验只回答「这单赚不赚钱」，不回答「挂这个价有没有人点」。
    # 在 $3.5–8 的白牌家居里，比站内中位价贵 5% 就等于把点击让给同款。
    if market_gate:
        median = fnum(row.get("market_median_price_usd"))
        source = str(row.get("market_price_source") or "").strip()
        n = fnum(row.get("market_price_n"))
        sampled = str(row.get("market_price_sampled_at") or "").strip()
        if median is None or median <= 0:
            issues.append("MARKET_PRICE_MISSING: market_median_price_usd 为空 —— 选品阶段就必须查"
                          "站内同款中位价（官方价格诊断 / FastMoss floorPrice / 遨虾同款中位价），"
                          "不许先铺货再补")
        else:
            if not source:
                issues.append("MARKET_PRICE_NO_SOURCE: 缺 market_price_source，中位价无法复核来源")
            if not sampled:
                warns.append("market_price_sampled_at 为空：价格是时效数据，没采集日期判不出是否过期")
            if n is not None and n < thr["market_price_min_sample"]:
                warns.append("市场价样本只有 %g 条 < %d 条，中位价可能被单款带偏"
                             % (n, thr["market_price_min_sample"]))
            if price_result:
                order_usd = price_result["order_usd"]
                cap = median * thr["market_price_tolerance"]
                if order_usd > cap:
                    issues.append(
                        "PRICE_ABOVE_MARKET: 挂价 $%s > 站内中位价 $%g × %g = $%.2f "
                        "→ 货架场比价必输（换货源/换品，不要靠压净利硬调）"
                        % (order_usd, median, thr["market_price_tolerance"], cap))
            band_min = float((price_result or {}).get("band", [0, 0])[0] or 0)
            if band_min and median < band_min * 0.5:
                warns.append("中位价 $%g 低于价格带下限 $%s 的一半，口径可疑（含税/不含税？）"
                             % (median, band_min))
    else:
        warns.append("未启用价格竞争力闸：候选表缺 %s 列（用 --init-template 生成新表头）"
                     % "/".join(MARKET_COLS))

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

    # ── 官方 2026 ✅ 重点类目（优先方向，非准入；给信号不阻断）──
    focus_status, focus_hits = "unknown", []
    if focus_cfg is not None:
        hay = " ".join(x for x in (
            cat_names.get(cat, "") if cat else "",
            str(row.get("title_zh") or ""),
        ) if x).strip()
        focus_status, focus_hits = match_official_focus(hay, focus_cfg)
        if focus_status == "miss":
            warns.append("OFFICIAL_FOCUS_MISS: 类目/标题未命中官方 2026 ✅ 重点类目；"
                         "家居树内仍可发品（✅ 是优先方向，不是准入资格，不阻断）")

    if not str(row.get("ship_city") or "").strip() or not str(row.get("forwarder_city") or "").strip():
        warns.append("ship_city/forwarder_city 未填齐，地域聚簇与货代覆盖无法复核（strategy 2.0）。"
                     "ship_city 证据链（2026-11-20）：详情页 location → linkfox sendGoodsAddressText"
                     "（opportunity_sourcing.csv 的 ship_city_raw 列，seed 已自动带）→ aibuy companyCity"
                     "（注册地口径，较松）→ 全空才人工抄；聚簇按城市粒度，省级不算填齐")

    if "pass" not in str(row.get("redline_check") or "").lower():
        issues.append("REDLINE_UNCHECKED: redline_check 不含 pass（strategy 5.1 十条红线须逐条核查留痕）")

    return (ident, issues, warns, price_result,
            {"status": focus_status, "hits": focus_hits})


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
    cat_names = build_category_name_map()
    focus_cfg = load_official_focus()
    market_gate = all(c in header for c in MARKET_COLS)

    results = []
    for i, row in enumerate(rows):
        ident, issues, warns, price_result, focus = judge(
            row, i, thr, price_cfg_all, home_ids, market_gate, focus_cfg, cat_names)
        results.append({"offer_id": ident, "issues": issues, "warns": warns,
                        "price": price_result, "official_focus": focus})

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
            focus = r.get("official_focus") or {}
            focus_note = (" [官方✅: %s]" % "/".join(focus.get("hits", [])[:2])
                          if focus.get("status") == "hit" else "")
            print(f"{mark} {r['offer_id']}{price_note}{focus_note}")
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
