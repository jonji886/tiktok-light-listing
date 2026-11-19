#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""商品卡质量闸（P0-3）：发布前拦「能发出去但没人点」的链接。

为什么需要（2026-11-18 诊断结论）：
  image_gate / prepublish_guard 只保证「不报错、图不脏、描述够长」，
  全是**合规线**，没有一条是**竞争力线**。实测在售 7 款里：
  主图 3 张（官方建议 ≥5）、product_attributes 空、search_terms 空，
  标题没有覆盖任何热搜词根 —— 商品卡在货架场里没有可被检索、可被点击的理由。

官方口径（商家大学《东南亚新商快速破零》四阶段「可售→可被搜索→被点击→下单」）：
  · 标题字符数越多越容易被搜到，建议 40 个字符以上
  · 主图不少于 5 张，要有卖点 / 促销 / 场景
  · 类目与属性填写完成率、准确率直接影响曝光精准度

定位（与 image_gate 同一哲学）：
  纯标准库、本地只读、不联网、不看图。图片"卖点/场景/尺寸/细节"的角色判定
  属于目视结论，脚本只认回填证据（erp_data/card_review/<ttl_id>.json），
  --strict 时缺证据即阻断。

用法：
  python3 scripts/listing_quality_gate.py --all                # 扫全部 ttl_detail_*.json
  python3 scripts/listing_quality_gate.py --ids 1570...,1570... # 只查指定款
  python3 scripts/listing_quality_gate.py --all --strict        # 主图角色证据缺失也阻断
  python3 scripts/listing_quality_gate.py --all --json
  python3 scripts/listing_quality_gate.py --all --summary       # 落 erp_data/card_quality.csv
  python3 scripts/listing_quality_gate.py --keywords-template --site VN   # 生成热搜词表骨架

退出码：0 全部通过（WARN 不阻断）；1 存在阻断项（禁止 ttl-publish）；2 输入问题
"""
import argparse
import csv
import glob
import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SELECTION_YAML = os.path.join(ROOT, "config", "selection.yaml")
KEYWORD_DIR = os.path.join(ROOT, "config", "keywords")
REVIEW_DIR = os.path.join(ROOT, "erp_data", "card_review")
SUMMARY_CSV = os.path.join(ROOT, "erp_data", "card_quality.csv")
DETAIL_DIRS = [os.path.join(ROOT, "erp_data"), os.path.join(HERE, "erp_data")]

CURRENCY_SITE = {"THB": "TH", "VND": "VN", "MYR": "MY", "PHP": "PH", "SGD": "SG"}

# 兜底阈值（正常从 selection.yaml → card_quality 读取）
DEFAULTS = {
    "title_min_chars": 40,
    "title_min_keyword_hits": 3,
    "min_main_images": 5,
    "min_attributes": 5,
    "require_video": "required",
    "require_card_evidence": "warn",
}

# 官方标题公式（《新商破零》2.2「商品被搜索」）：
#   完整标题 =「核心关键词+属性关键词」+促销关键词+功效关键词+人群关键词+品牌关键词(若有)
# core/attr 是必中角色，其余四类命中 ≥1 即可（品牌「若有」，不强求）。
# 词表带 role 标记时启用（格式：词 # role=core，见 load_keywords）；
# 旧词表没有标记 → 只跑总量阈值（title_min_keyword_hits），行为向后兼容。
TITLE_ROLES = {
    "core": "核心词", "attr": "属性词", "promo": "促销词",
    "effect": "功效词", "audience": "人群词", "brand": "品牌词",
}
TITLE_REQUIRED_ROLES = ("core", "attr")
TITLE_MIN_EXTRA_ROLES = 1

# 主图角色：卖点/场景/尺寸/细节 是硬覆盖项（旧口径）；官方《新商破零》2.3 还要求
# 头图「有促销」——但促销图依赖是否参加营销活动，无促销的品不能因缺图被阻断，
# 故 促销 只作推荐角色：缺失固定 WARN，回填了则计入 roles。
CARD_ROLES = ("卖点", "场景", "尺寸", "细节")
CARD_ROLES_RECOMMENDED = ("促销",)

SUMMARY_HEADER = [
    "ttl_id", "site", "title_chars", "keyword_hits", "main_images",
    "attributes", "has_video", "search_terms", "verdict", "issues",
]


def load_cfg():
    cfg = dict(DEFAULTS)
    if not os.path.exists(SELECTION_YAML):
        return cfg
    vals = {}
    for raw in open(SELECTION_YAML, encoding="utf-8"):
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^\s*([a-z0-9_]+)\s*:\s*([^#]+?)\s*(?:#.*)?$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip().strip("\"'")
        if key not in DEFAULTS:
            continue
        if isinstance(DEFAULTS[key], str):
            cfg[key] = val.lower()
        else:
            try:
                cfg[key] = int(float(val))
            except ValueError:
                pass
    return cfg


def norm(s):
    """去重音 + 小写，用于跨语言词根匹配（越南语带声调、泰语无空格）。"""
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower()


def load_keywords(site):
    """返回 (词根列表, 是否存在词表, 词->角色 dict 或 None)。词表缺失不猜，交由调用方判 MISSING。

    词表格式：每行一个词；可选 `词 # role=core` 标角色（core/attr/promo/effect/audience/brand）。
    全部无标记 → roles 返回 None，调用方跳过角色校验（向后兼容旧词表）。
    """
    path = os.path.join(KEYWORD_DIR, "%s.txt" % site.lower())
    if not os.path.exists(path):
        return [], False, None
    words, roles, tagged = [], {}, False
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line, _, comment = raw.partition("#")
            w = line.strip()
            if not w:
                continue
            words.append(w)
            m = re.search(r"role\s*=\s*([a-z_]+)", comment.lower())
            if m and m.group(1) in TITLE_ROLES:
                roles[w] = m.group(1)
                tagged = True
    return words, True, (roles if tagged else None)


MIN_WORD_LEN = 3   # 短词（如越南语 "tu"/"ke"）会在别的词里误命中，一律不参与匹配


def match_word(norm_title, word):
    """拉丁字母词按词边界匹配（越南语按音节分词），泰语等无空格语言退回子串匹配。"""
    nw = norm(word)
    if len(nw) < MIN_WORD_LEN:
        return False
    if all(ord(c) < 128 for c in nw):
        return re.search(r"(?<![a-z0-9])" + re.escape(nw) + r"(?![a-z0-9])", norm_title) is not None
    return nw in norm_title


def site_of(detail, override=""):
    if override:
        return override.upper()
    cur = ""
    v2 = extract_v2(detail) or {}
    cur = str(v2.get("currency") or detail.get("currency") or "").upper()
    if cur in CURRENCY_SITE:
        return CURRENCY_SITE[cur]
    nick = str(detail.get("toSellerNick") or "").upper()
    for s in ("TH", "VN", "MY", "PH", "SG"):
        if nick.endswith("-" + s):
            return s
    return ""


def extract_v2(detail):
    """兼容三种落盘层级：reqInfo.tikTokItemModel / tikTokItemModel / 根。"""
    if not isinstance(detail, dict):
        return None
    req = detail.get("reqInfo") or {}
    tk = req.get("tikTokItemModel") or detail.get("tikTokItemModel") or {}
    v2 = tk.get("productCreateRequestV2") or detail.get("productCreateRequestV2")
    return v2 if isinstance(v2, dict) else None


def count_attrs(v2):
    n = 0
    for a in v2.get("product_attributes") or []:
        if not isinstance(a, dict):
            continue
        if str(a.get("value_name") or a.get("value") or a.get("name") or "").strip():
            n += 1
    return n


def load_card_review(ttl_id):
    path = os.path.join(REVIEW_DIR, "%s.json" % ttl_id)
    if not os.path.exists(path):
        return None
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def analyze_detail(detail, ttl_id, cfg, site_override="", strict=False):
    """返回 {site, metrics, issues, warns}；不修改任何文件。"""
    issues, warns = [], []
    v2 = extract_v2(detail)
    if v2 is None:
        return {"site": site_override, "metrics": {}, "v2_missing": True,
                "issues": ["NO_ITEM_MODEL: 落盘的 detail 里找不到 productCreateRequestV2，"
                           "先跑 erp_client.py ttl-detail 重新落盘"],
                "warns": []}

    site = site_of(detail, site_override)
    title = str(v2.get("title") or "")
    images = v2.get("main_images") or []
    attrs = count_attrs(v2)
    video = v2.get("video") or {}
    video_url = ""
    if isinstance(video, dict):
        video_url = str(video.get("id") or video.get("url") or "").strip()
    elif isinstance(video, str):
        video_url = video.strip()
    search_terms = v2.get("search_terms") or []
    key_features = v2.get("key_product_features") or []

    # ── 1. 标题长度（官方：≥40 字符更容易被搜到）──
    chars = len(title.strip())
    if chars < cfg["title_min_chars"]:
        issues.append("TITLE_TOO_SHORT: 标题 %d 字符 < %d（官方口径：字符越多越容易被搜到）"
                      % (chars, cfg["title_min_chars"]))

    # ── 2. 标题热搜词根覆盖（≥3 个不同词根）+ 官方公式角色覆盖（词表带 role 才启用）──
    words, has_kw, kw_roles = load_keywords(site) if site else ([], False, None)
    hits, hit_words, hit_roles = 0, [], []
    if site and has_kw:
        nt = norm(title)
        for w in words:
            if match_word(nt, w):
                hits += 1
                hit_words.append(w)
                if kw_roles:
                    hit_roles.append(kw_roles[w])
        if hits < cfg["title_min_keyword_hits"]:
            issues.append("TITLE_KEYWORD_LOW: 标题只命中 %d 个热搜词根（%s）/ 阈值 %d，"
                          "商品卡在搜索里没有入口 → 换标题，不要靠堆无关词"
                          % (hits, "、".join(hit_words) or "无", cfg["title_min_keyword_hits"]))
        if kw_roles:
            missing_req = [r for r in TITLE_REQUIRED_ROLES if r not in hit_roles]
            extra_n = len({r for r in hit_roles} - set(TITLE_REQUIRED_ROLES))
            if missing_req or extra_n < TITLE_MIN_EXTRA_ROLES:
                parts = []
                if missing_req:
                    parts.append("缺必中角色 %s" % "、".join(TITLE_ROLES[r] for r in missing_req))
                if extra_n < TITLE_MIN_EXTRA_ROLES:
                    parts.append("促销/功效/人群/品牌角色只命中 %d 个（需 ≥%d）"
                                 % (extra_n, TITLE_MIN_EXTRA_ROLES))
                issues.append("TITLE_FORMULA: 官方标题公式「核心+属性+促销+功效+人群+品牌(若有)」未满足 —— %s"
                              % ("；".join(parts)))
    else:
        warns.append("NO_KEYWORD_TABLE: 缺 config/keywords/%s.txt，无法判标题词根覆盖"
                     "（生成骨架：--keywords-template --site %s）"
                     % ((site or "??").lower(), site or "VN"))

    # ── 3. 主图数量（官方建议 ≥5 张）──
    n_img = len([x for x in images if x])
    if n_img < cfg["min_main_images"]:
        issues.append("MAIN_IMAGES_LT%d: 主图 %d 张 < %d 张（主图数量直接影响曝光与信任度）"
                      % (cfg["min_main_images"], n_img, cfg["min_main_images"]))

    # ── 4. 主图角色覆盖（目视结论，脚本只认回填证据）──
    review = load_card_review(ttl_id)
    roles = set()
    if review:
        roles = {str(r) for r in (review.get("roles") or [])}
    missing_roles = [r for r in CARD_ROLES if r not in roles]
    if missing_roles:
        msg = ("CARD_ROLES_MISSING: 主图角色缺 %s（需 erp_data/card_review/%s.json 回填 "
               "roles=[卖点/场景/尺寸/细节/促销]）" % ("、".join(missing_roles), ttl_id))
        if strict or cfg["require_card_evidence"] == "required":
            issues.append(msg)
        else:
            warns.append(msg)
    # P1-6 促销图（官方《新商破零》2.3 头图三要素之「有促销」）：
    # 参加营销活动的品必须有促销图；无活动的品不该因此被阻断 → 固定 WARN 不打 --strict 升级。
    if any(r not in roles for r in CARD_ROLES_RECOMMENDED):
        warns.append("CARD_ROLE_NO_PROMO: 缺促销向主图（官方高点击头图=卖点+促销+场景）；"
                     "本款若参加秒杀/大促，必须补促销图并回填 roles 加「促销」")

    # ── 5. 属性完整度（官方：属性完成率影响曝光精准度）──
    if attrs < cfg["min_attributes"]:
        issues.append("ATTRS_LT%d: 已填属性 %d 项 < %d 项（product_attributes 为空时"
                      "搜索与推荐拿不到品类信号）" % (cfg["min_attributes"], attrs,
                                                     cfg["min_attributes"]))

    # ── 6. 商品视频 ──
    if not video_url:
        if cfg["require_video"] == "required":
            issues.append("NO_VIDEO: 无商品视频（官方销售加速器任务与内容场曝光都以视频为入口）")
        else:
            warns.append("NO_VIDEO: 无商品视频")
    elif any(d in video_url for d in ("taobao.com", "alicdn.com", "1688.com")):
        warns.append("VIDEO_EXTERNAL: 视频源是 1688/淘宝域名，平台侧可能无法播放，"
                     "需上传到 TikTok 后再挂")

    # ── 7. 可选的搜索词与卖点字段（有则更好，缺只提醒）──
    if not search_terms:
        warns.append("NO_SEARCH_TERMS: search_terms 为空，可补 3-5 个当地语言搜索词")
    if not key_features:
        warns.append("NO_KEY_FEATURES: key_product_features 为空，可补 3 条卖点短句")

    if re.search(r"[\u4e00-\u9fff]", title):
        warns.append("TITLE_CJK: 标题含中文（发布侧会被 prepublish_guard 拦，这里先提醒）")

    metrics = {
        "site": site, "title": title, "title_chars": chars,
        "keyword_hits": hits, "keyword_words": hit_words, "has_keyword_table": has_kw,
        "keyword_roles": sorted(set(hit_roles)),
        "main_images": n_img, "attributes": attrs,
        "has_video": bool(video_url), "video_external": any(
            d in video_url for d in ("taobao.com", "alicdn.com", "1688.com")),
        "search_terms": len(search_terms), "key_features": len(key_features),
        "card_roles": sorted(roles), "missing_roles": missing_roles,
    }
    return {"site": site, "metrics": metrics, "issues": issues, "warns": warns}


def find_detail(ttl_id):
    for d in DETAIL_DIRS:
        p = os.path.join(d, "ttl_detail_%s.json" % ttl_id)
        if os.path.exists(p):
            return p
    return ""


def all_detail_ids():
    ids = {}
    for d in DETAIL_DIRS:
        for p in glob.glob(os.path.join(d, "ttl_detail_*.json")):
            tid = os.path.basename(p)[len("ttl_detail_"):-len(".json")]
            ids.setdefault(tid, p)
    return ids


def write_keyword_template(site):
    os.makedirs(KEYWORD_DIR, exist_ok=True)
    path = os.path.join(KEYWORD_DIR, "%s.txt" % site.lower())
    if os.path.exists(path):
        return path, False
    seed = {
        "VN": ["nha bep", "luu tru", "hop dung", "ke", "moc treo", "khay",
               "lam sach", "choi", "gio", "tui", "ve sinh", "phong tam",
               "tu", "gap gon", "da nang", "treo tuong", "inox", "nhua"],
        "TH": ["ที่เก็บของ", "ชั้นวาง", "ตะขอ", "ห้องครัว", "กล่อง", "ตะกร้า",
               "ไม้แขวน", "ทำความสะอาด", "ห้องน้ำ", "พับเก็บได้", "อเนกประสงค์",
               "ติดผนัง", "สแตนเลส", "พลาสติก", "ถุง", "ไม้กวาด"],
        "MY": ["penyimpanan", "rak", "dapur", "kotak", "bakul", "sangkut",
               "pembersihan", "bilik mandi", "lipat", "serbaguna", "dinding",
               "keluli", "plastik", "beg"],
    }.get(site.upper(), [])
    with open(path, "w", encoding="utf-8") as f:
        f.write("# %s 商品卡热搜词根（一行一个）。\n" % site.upper())
        f.write("# 种子词仅用于跑通闸门，**必须**用官方来源替换/扩充：\n")
        f.write("#   商家后台 → 数据罗盘 → 商品卡 → 关键词榜单（热门/飙升/高潜力）\n")
        f.write("#   商家后台 → 商品 → 商品机会中心 → 热门搜索 / 精选搜索\n")
        f.write("# 匹配规则：标题去重音小写后做子串匹配，命中 ≥N 个不同词根才通过。\n")
        for w in seed:
            f.write(w + "\n")
    return path, True


def main():
    ap = argparse.ArgumentParser(description="商品卡质量闸（发布前硬闸，纯本地只读）")
    ap.add_argument("--ids", default="", help="逗号分隔 ttl_id")
    ap.add_argument("--all", action="store_true", help="扫描全部 ttl_detail_*.json")
    ap.add_argument("--site", default="", help="强制指定站点（TH/VN/MY）")
    ap.add_argument("--strict", action="store_true", help="主图角色证据缺失也阻断")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--summary", action="store_true", help="落 erp_data/card_quality.csv")
    ap.add_argument("--keywords-template", action="store_true", help="生成热搜词表骨架")
    a = ap.parse_args()

    if a.keywords_template:
        site = (a.site or "VN").upper()
        path, created = write_keyword_template(site)
        print("[card] %s %s" % ("已生成" if created else "已存在，未覆盖", path))
        return 0

    cfg = load_cfg()
    targets = []
    if a.ids:
        for tid in [x.strip() for x in a.ids.split(",") if x.strip()]:
            p = find_detail(tid)
            if not p:
                sys.stderr.write("[card] 找不到 ttl_detail_%s.json（两个落盘目录都查了）\n" % tid)
                return 2
            targets.append((tid, p))
    if a.all:
        for tid, p in sorted(all_detail_ids().items()):
            if tid not in [t for t, _ in targets]:
                targets.append((tid, p))
    if not targets:
        sys.stderr.write("[card] 未指定目标：用 --ids 或 --all\n")
        return 2

    results = []
    for tid, path in targets:
        try:
            detail = json.load(open(path, encoding="utf-8"))
        except (ValueError, OSError) as e:
            results.append({"ttl_id": tid, "site": "", "metrics": {},
                            "issues": ["DETAIL_UNREADABLE: %s" % e], "warns": []})
            continue
        r = analyze_detail(detail, tid, cfg, a.site, a.strict)
        r["ttl_id"] = tid
        results.append(r)

    n_block = sum(1 for r in results if r["issues"])

    if a.summary:
        os.makedirs(os.path.dirname(SUMMARY_CSV), exist_ok=True)
        with open(SUMMARY_CSV, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(SUMMARY_HEADER)
            for r in results:
                m = r.get("metrics") or {}
                w.writerow([
                    r["ttl_id"], r.get("site") or m.get("site", ""),
                    m.get("title_chars", ""), m.get("keyword_hits", ""),
                    m.get("main_images", ""), m.get("attributes", ""),
                    "1" if m.get("has_video") else "0", m.get("search_terms", ""),
                    "BLOCK" if r["issues"] else "PASS",
                    " | ".join(r["issues"]),
                ])
        print("[card] 明细已落 %s" % SUMMARY_CSV)

    if a.json:
        print(json.dumps({"thresholds": cfg, "total": len(results),
                          "blocked": n_block, "results": results},
                         ensure_ascii=False, indent=2))
        return 1 if n_block else 0

    print("=== 商品卡质量闸（发布前，只读；脚本不看图，只认回填的角色证据）===")
    print("[card] 共 %d 款 | 阻断 %d | 通过 %d\n"
          % (len(results), n_block, len(results) - n_block))
    for r in results:
        m = r.get("metrics") or {}
        head = "%-19s %-3s 标题%s字 词根%s 主图%s 属性%s 视频%s" % (
            r["ttl_id"], r.get("site") or "-",
            m.get("title_chars", "-"), m.get("keyword_hits", "-"),
            m.get("main_images", "-"), m.get("attributes", "-"),
            "有" if m.get("has_video") else "无")
        print(("✗ " if r["issues"] else ("! " if r["warns"] else "✓ ")) + head)
        for x in r["issues"]:
            print("   ✗ %s" % x)
        for x in r["warns"]:
            print("   ! %s" % x)
    print()
    if n_block:
        print("[card] ❌ 阻断 %d 款 —— 禁止 ttl-publish。补齐标题词根/主图/属性/视频后重跑。"
              % n_block)
        print("       主图角色证据模板：erp_data/card_review/<ttl_id>.json "
              '{"roles": ["卖点","场景","尺寸","细节"]}')
    else:
        print("[card] ✅ 商品卡质量全部通过（仍须跑 image_gate verify 与 prepublish_guard）")
    return 1 if n_block else 0


if __name__ == "__main__":
    sys.exit(main())
