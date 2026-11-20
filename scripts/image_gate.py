#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""image_gate.py — 详情页中文/B2B 素材图片闸门（发布前硬闸）

问题（2026-11-12 实测，见 README §8.5）：
  1688 详情图整版是「跨境电商专用 / 工厂直供 / 一件代发 / 支持OEM / 5000平仓库」
  这类面向中国卖家的 B2B 招商素材。它不是「干净商品图 + 一行字」，擦字没用：
  擦完还剩工厂照片、参数表、批发承诺、二维码。判定单位是**整张图**，命中即整图删。
  而真正大面积漏进 TikTok 详情页的是 ``description`` 里的详情长图（实测 8–25 张），
  不是 ``main_images``（实测恒 5 张）——旧 Step 4.3 只处理主图，一条都没碰。

设计前提（重要）：
  **本脚本不假装能看图**。艺术字、竖排字、印章字 OCR 会漏，所以整图判定交给
  多模态模型逐张目视后回填 review.json 的 verdict。脚本只做确定性的活：
  下载三个通道的图（主图 / description 详情长图 / SKU 规格图）→ 出待判定清单
  → 按判定重建 → 写回 ERP → 回读验收 → 发布拦截。

三通道（2026-11-13 审查补齐 SKU 图）：
  main  main_images[].uri                     实测恒 5 张
  desc  description 里的 <img src>            实测 8–25 张（旧版只处理主图，一张没碰）
  sku   sales_attributes[].sku_img.uri        实测 32/35 个属性带图，全是 1688 原图

四级判定（review.json → images[].verdict）：
  L0_drop      B2B 素材 / 参数表 / 工厂仓库证书 / 整版中文 → 整图删
  L1_inpaint   干净消费者向商品图，只叠了一行营销字   → 保留，列入待擦除清单
  L2_localize  产品功能词                              → 保留，列入待翻译清单
  L3_keep      无中文无水印                            → 原样保留
  pending      未判定 → apply 默认拒绝执行（除非显式 --assume-pending drop）

标准流程：
  # 1) 落盘详情（erp_client.py ttl-detail 默认写 erp_data/ttl_detail_<id>.json）
  python3 scripts/erp_client.py ttl-detail --id <ttl_id>
  # 2) 扫主图 + description 两通道，下载图片，生成待判定清单
  python3 scripts/image_gate.py scan --detail erp_data/ttl_detail_<id>.json
  # 3)【模型/人工】逐张看 erp_data/image_gate/<id>/*.jpg，回填 review.json：
  #    images[].verdict / seen_text / confidence / note
  #    另外可填 localized_text（当地语言纯文本，用空行分段 → 逐段包 <p>；
  #    须 ≥500 字符 / ≥2 段，见 config/image_gate.yaml）与 uri_map（擦字/翻译后的新图 URL）
  # 4) 重建 + 验收（默认只出文件，不碰 ERP）
  python3 scripts/image_gate.py apply --detail erp_data/ttl_detail_<id>.json \
      --review erp_data/image_gate/<id>/review.json
  # 5) 发布前硬闸（退出码 1 = 拦截，禁止 ttl-publish）
  python3 scripts/image_gate.py verify --detail erp_data/image_gate/<id>/cleaned_detail.json
  # 6) 确认后写回 ERP（写操作，必须显式 --save）
  python3 scripts/image_gate.py apply --detail ... --review ... --save

纯标准库。scan / apply（无 --save）/ verify 全程离线；只有 --save 才联网写 ERP。
"""
import argparse
import copy
import hashlib
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "image_gate.yaml"
DETAIL_API = "/item/crossBorder/platformCollect/getDetail"
EDIT_API = "/item/crossBorder/edit"

CJK = re.compile(r"[\u3400-\u9fff]")
IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
SRC_QUOTED = re.compile(r"\bsrc\s*=\s*(['\"])(.*?)\1", re.I | re.S)
SRC_BARE = re.compile(r"\bsrc\s*=\s*([^\s>]+)", re.I)
TEMPLATE_RESIDUE = re.compile(
    r"usemap|styleType|offer-template|<map\b|<area\b|<script\b|<style\b|onerror\s*=", re.I)
URL_QR_HINT = re.compile(r"qrcode|erweima|weixin|wx\.qq", re.I)
TAG_RE = re.compile(r"<[^>]+>")

VALID_VERDICTS = {"L0_drop", "L1_inpaint", "L2_localize", "L3_keep", "pending"}

# review 过期判定：判定表里失效 URL 达到 3 条或占比 >20% 即认为 detail 已换过
STALE_MIN_COUNT = 3
STALE_RATIO = 0.2

DEFAULTS = {
    "b2b_markers": [],
    "min_clean_main_images": 1,
    "min_publish_main_images": 5,   # 2026-11-19 发布闸硬下限（prepublish_guard 用）
    "min_desc_images_after_gate": 0,
    "max_cjk_chars_in_description": 0,
    "require_no_template_residue": True,
    "check_b2b_in_title": True,
    # 2026-11-12 TikTok 后台回执：描述要 ≥500 字符且分段（软提示，发布前由 guard 兜底）
    "description_min_plain_chars": 500,
    "description_min_paragraphs": 2,
}


# ── 配置（极简 YAML 子集：标量 + 单行列表，免 PyYAML 依赖）──
def load_config(path=None):
    cfg = dict(DEFAULTS)
    cfg["b2b_markers"] = list(DEFAULTS["b2b_markers"])
    p = Path(path) if path else CONFIG
    if not p.exists():
        return cfg
    section = None
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = re.sub(r"\s+#.*$", "", raw).rstrip()
        if not line.strip():
            continue
        item = re.match(r"^\s*-\s+(.+)$", line)
        if item and section == "b2b_markers":
            cfg["b2b_markers"].append(item.group(1).strip().strip("\"'"))
            continue
        kv = re.match(r"^\s*([a-z0-9_]+)\s*:\s*(.*)$", line)
        if not kv:
            continue
        key, val = kv.group(1), kv.group(2).strip()
        section = key if not val else None
        if not val:
            continue
        if val.lower() in ("true", "yes", "on"):
            cfg[key] = True
        elif val.lower() in ("false", "no", "off"):
            cfg[key] = False
        else:
            try:
                cfg[key] = int(val)
            except ValueError:
                cfg[key] = val.strip("\"'")
    return cfg


# ── detail JSON 读写 ──
def read_json(path):
    p = Path(path)
    if not p.exists():
        sys.exit(f"[gate] 文件不存在: {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        sys.exit(f"[gate] JSON 解析失败 {p}: {e}")


def get_v2(d, strict=True):
    """返回 (reqInfo, tikTokItemModel, v2)。草稿通道关闭时 v2 为空。"""
    ri = d.get("reqInfo") or (d.get("data") or {}).get("reqInfo") or {}
    ttm = (ri.get("tikTokItemModel") or {}) if isinstance(ri, dict) else {}
    v2 = (ttm.get("productCreateRequestV2") or {}) if isinstance(ttm, dict) else {}
    if strict and not v2:
        sys.exit("[gate] 读不到 reqInfo.tikTokItemModel.productCreateRequestV2 —— "
                 "getDetail 只服务草稿态，可能已被保存清空；不要拿旧快照补位")
    return ri, ttm, v2


def src_of(tag):
    m = SRC_QUOTED.search(tag)
    if m:
        return html.unescape(m.group(2)).strip()
    m = SRC_BARE.search(tag)
    return html.unescape(m.group(1)).strip().rstrip("/>") if m else ""


def desc_image_urls(desc):
    out = []
    for tag in IMG_TAG.findall(desc or ""):
        u = src_of(tag)
        if u:
            out.append(u)
    return out


def plain_text(desc):
    return html.unescape(TAG_RE.sub(" ", desc or ""))


def main_image_urls(v2):
    return [x.get("uri", "").strip() for x in (v2.get("main_images") or [])
            if isinstance(x, dict) and x.get("uri")]


def sku_image_urls(v2):
    """SKU 规格图（sales_attributes[].sku_img.uri）。

    2026-11-13 审查新增：实测 32/35 个 SKU 属性带 sku_img，全部指向 cbu01.alicdn.com
    的 1688 原图。旧版只扫 main_images + description，这条通道一张都没碰——
    而它是买家选规格时第一眼看到的图，中文/水印会原样发布。
    """
    out = []
    for s in (v2.get("skus") or []):
        if not isinstance(s, dict):
            continue
        for a in (s.get("sales_attributes") or []):
            if isinstance(a, dict):
                u = ((a.get("sku_img") or {}) or {}).get("uri")
                if isinstance(u, str) and u.strip():
                    out.append(u.strip())
    return out


def all_image_urls(v2):
    """三通道合集：主图 + 详情图 + SKU 规格图（去重保序）。"""
    seen, out = set(), []
    for u in (main_image_urls(v2)
              + desc_image_urls(v2.get("description") or "")
              + sku_image_urls(v2)):
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def review_coverage(d, review):
    """当前 detail 的图 URL 与判定表的差异，返回 dict：
      missing  当前有图但判定表没有 → 有图从未被目视就发布
      pending  当前有图但仍是 pending
      stale    判定表有但当前已没有 → review 已过期（detail 被 ERP 改写/重新落盘）
      known    判定表 URL ∪ uri_map 译文 URL（apply 之后图已换 URL，仍算已判定）

    为什么必须查 stale（2026-11-13 审查实测）：4 次 apply 的 `before` 与 `after`
    完全相同，却记录 dropped_images=12~17 —— 判定表里的图在当前 detail 里早已不存在，
    闸门在"自我表演"。旧实现只查 missing（当前⊆判定），当前图更少时永远通过。
    """
    v2 = get_v2(d, strict=False)[2]
    cur = all_image_urls(v2)
    images = (review or {}).get("images") or []
    verdicts = {str(x["url"]): (x.get("verdict") or "pending")
                for x in images if isinstance(x, dict) and x.get("url")}
    uri_map = {str(k): str(v) for k, v in ((review or {}).get("uri_map") or {}).items() if v}
    known = set(verdicts) | set(uri_map.values())
    return {
        "missing": [u for u in cur if u not in known],
        "pending": [u for u in cur if verdicts.get(u) == "pending"],
        "stale": [u for u in verdicts if u not in set(cur)],
        "known": known,
        "verdicts": verdicts,
    }


def stale_too_much(stale, total):
    """判定表失效比例是否已达「review 过期」级别（少量失效属正常：ERP 会剔图）。"""
    if not stale:
        return False
    return len(stale) >= STALE_MIN_COUNT or len(stale) / max(1, total) > STALE_RATIO


def download(url, dest, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    if not data:
        raise ValueError("空响应")
    dest.write_bytes(data)
    return len(data)


def image_ext(url):
    """按 URL 后缀取真实扩展名（旧版一律写 .jpg，png/webp 会被下游按错格式解码）。"""
    low = (url or "").lower().split("?")[0]
    for ext in (".png", ".webp", ".jpeg", ".jpg", ".gif", ".bmp"):
        if low.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    return ".jpg"


# ── inspect：verify 与 fix_sku 共用的纯函数 ──
def inspect_detail(d, cfg=None, review=None):
    """语义校验。返回 (issues, stats)；issues 为 (level, code, msg) 列表。

    level = FAIL（禁止发布）/ WARN（提示）/ INFO
    不联网、不写文件，可直接被 fix_sku.py / prep_publish.py 调用。
    """
    cfg = cfg or load_config()
    issues, stats = [], {}
    ri, ttm, v2 = get_v2(d, strict=False)
    stats["readable"] = bool(v2)
    if not v2:
        issues.append(("FAIL", "NO_ITEM_MODEL",
                       "getDetail 未返回 itemModel（草稿通道已关闭或数据被写坏），无法验收"))
        return issues, stats

    desc = v2.get("description") or ""
    main = v2.get("main_images") or []
    title = v2.get("title") or d.get("title") or ""
    stats["description_len"] = len(desc)
    stats["main_images"] = len(main)
    stats["desc_images"] = len(desc_image_urls(desc))
    stats["sku_images"] = len(sku_image_urls(v2))

    if not desc.strip():
        issues.append(("FAIL", "EMPTY_DESCRIPTION", "description 为空 → 平台报「商品描述不能为空」"))
    if len(main) < int(cfg["min_clean_main_images"]):
        issues.append(("FAIL", "NO_MAIN_IMAGE",
                       f"主图仅 {len(main)} 张（要求 ≥{cfg['min_clean_main_images']}）"))

    text = plain_text(desc)
    cjk_chars = CJK.findall(text)
    stats["cjk_text_chars"] = len(cjk_chars)
    if len(cjk_chars) > int(cfg["max_cjk_chars_in_description"]):
        sample = "".join(dict.fromkeys(cjk_chars))[:40]
        issues.append(("FAIL", "CJK_IN_DESCRIPTION",
                       f"description 文本节点含 {len(cjk_chars)} 个汉字（样本「{sample}」）→ "
                       "中文说明必须换成当地语言纯文本"))

    # 属性里的中文（alt/title 会显示在部分端）
    attr_cjk = []
    for tag in IMG_TAG.findall(desc):
        for m in re.finditer(r'\b(alt|title)\s*=\s*(["\'])(.*?)\2', tag, re.I | re.S):
            if CJK.search(m.group(3)):
                attr_cjk.append(m.group(3)[:30])
    stats["cjk_img_attrs"] = len(attr_cjk)
    if attr_cjk:
        issues.append(("FAIL", "CJK_IN_IMG_ATTR",
                       f"图片 alt/title 含中文 {len(attr_cjk)} 处（例「{attr_cjk[0]}」）"))

    if cfg.get("require_no_template_residue"):
        res = sorted({m.group(0) for m in TEMPLATE_RESIDUE.finditer(desc)})
        stats["template_residue"] = res
        if res:
            issues.append(("FAIL", "TEMPLATE_RESIDUE",
                           f"1688 模板残渣未清理: {', '.join(res)} → 必须重建 description"))

    markers = [m for m in cfg["b2b_markers"] if m and m in desc]
    stats["b2b_in_description"] = markers
    if markers:
        issues.append(("FAIL", "B2B_TEXT",
                       f"description 残留 B2B 字样: {', '.join(markers[:6])}"))
    if cfg.get("check_b2b_in_title") and title:
        in_title = [m for m in cfg["b2b_markers"] if m and m in title]
        if in_title:
            issues.append(("FAIL", "B2B_IN_TITLE",
                           f"标题残留 B2B 字样: {', '.join(in_title[:6])}"))

    if int(cfg["min_desc_images_after_gate"]) > 0 and stats["desc_images"] < int(cfg["min_desc_images_after_gate"]):
        issues.append(("WARN", "FEW_DESC_IMAGES",
                       f"重建后详情图仅 {stats['desc_images']} 张"))

    # 与判定表交叉：被判 L0 的 URL 不得残留在任何通道（含 SKU 规格图）
    if review:
        dropped = {x["url"] for x in review.get("images", [])
                   if x.get("verdict") == "L0_drop" and x.get("url")}
        leaked = [u for u in all_image_urls(v2) if u in dropped]
        stats["dropped_url_leaked"] = len(leaked)
        if leaked:
            issues.append(("FAIL", "DROPPED_IMAGE_LEAK",
                           f"被判整图删的 URL 仍残留 {len(leaked)} 处（例 {leaked[0][:70]}）"))

    if not any(x[0] == "FAIL" for x in issues):
        issues.append(("INFO", "PASS", "图片闸门通过"))
    return issues, stats


# ── scan ──
def cmd_scan(args, cfg):
    d = read_json(args.detail)
    ri, ttm, v2 = get_v2(d)
    item_id = str(d.get("id") or Path(args.detail).stem)
    out_dir = Path(args.out) if args.out else ROOT / "erp_data" / "image_gate" / item_id
    out_dir.mkdir(parents=True, exist_ok=True)

    desc = v2.get("description") or ""
    main_urls = main_image_urls(v2)
    desc_urls = desc_image_urls(desc)
    sku_urls = sku_image_urls(v2)
    text = plain_text(desc)
    cjk_count = len(CJK.findall(text))
    cjk_sample = re.sub(r"\s+", " ", "".join(dict.fromkeys(CJK.findall(text))))[:60]
    residue = sorted({m.group(0) for m in TEMPLATE_RESIDUE.finditer(desc)})

    # 按 URL 去重：同一张图可能同时是主图和详情图，只需判定一次
    order, channels = [], {}
    for ch, urls in (("main", main_urls), ("desc", desc_urls), ("sku", sku_urls)):
        for u in urls:
            if u not in channels:
                order.append(u)
                channels[u] = set()
            channels[u].add(ch)

    images = []
    content_dup = {}
    for i, url in enumerate(order, 1):
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
        dest = out_dir / f"{digest}{image_ext(url)}"
        state, size = "skipped", 0
        if not args.no_download:
            if dest.exists():
                state, size = "cached", dest.stat().st_size
            else:
                try:
                    size = download(url, dest, args.timeout)
                    state = "ok"
                except Exception as e:
                    state = f"error: {type(e).__name__}: {str(e)[:80]}"
        # 内容哈希（678 教训：main_05 与 sku_22 URL 不同但字节相同，URL 去重抓不到，
        # 换图后平台 3/4 位同 hash 浪费主图位）
        csha = ""
        if state in ("ok", "cached") and dest.exists():
            csha = hashlib.sha256(dest.read_bytes()).hexdigest()
            if csha in content_dup:
                content_dup[csha].append(url)
            else:
                content_dup[csha] = [url]
        hints = []
        if URL_QR_HINT.search(url):
            hints.append("URL 疑似二维码/微信素材（仅提示，非判定）")
        if "desc" in channels[url] and cjk_count:
            hints.append(f"所在 description 含 {cjk_count} 个中文文本字符（可能同版素材，仅提示）")
        if "desc" in channels[url] and residue:
            hints.append(f"所在 description 含模板残渣 {','.join(residue[:3])}（仅提示）")
        if "sku" in channels[url]:
            hints.append("SKU 规格图：买家选规格第一眼可见，中文/水印同样要处理")
        images.append({
            "id": f"{sorted(channels[url])[0]}_{i:02d}",
            "url": url,
            "channels": sorted(channels[url]),
            # local_file 是权威字段（相对图片目录）：Skill 目录搬家后 local_path 会失效，
            # 相对名始终可用。local_path 仅作便利，不参与任何判断。
            "local_file": dest.name,
            "local_path": str(dest),
            "download": state,
            "bytes": size,
            "content_sha256": csha,
            "content_dupe_of": (content_dup.get(csha, [url])[0] if csha and len(content_dup.get(csha, [])) > 1 and content_dup[csha][0] != url else None),
            "heuristic_flags": hints,
            "verdict": "pending",
            "seen_text": "",
            "confidence": None,
            "note": "",
        })
    # 内容重复提示（678：main_05 与 sku_22 同字节不同 URL，换主图时占两个主图位实际只有 4 张唯一图）
    dup_groups = [urls for urls in content_dup.values() if len(urls) > 1]
    for im in images:
        grp = content_dup.get(im.get("content_sha256")) or []
        if len(grp) > 1:
            im["heuristic_flags"].append(
                "内容与 %s 相同（URL 不同）——换主图时勿同时选，会占位重复" %
                ",".join(os.path.basename(g)[:10] for g in grp if g != im["url"]))

    review = {
        "item_id": item_id,        "source_detail": str(Path(args.detail).resolve()),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "how_to_judge": (
            "逐张打开 local_file（图片目录下的文件名；local_path 搬家后会失效）看图，"
            "不要只看 heuristic_flags。判定单位是整张图："
            "B2B 渠道标注/参数表/工厂仓库证书/整版中文=L0_drop；干净商品图只叠一行营销字=L1_inpaint；"
            "产品功能词=L2_localize；无中文无水印=L3_keep。低置信度一律转人工，不要猜。"
        ),
        "verdict_values": sorted(VALID_VERDICTS),
        "summary": {
            "main_images": len(main_urls),
            "desc_img_tags": len(desc_urls),
            "sku_images": len(sku_urls),
            "unique_images": len(order),
            "cjk_text_chars": cjk_count,
            "cjk_text_sample": cjk_sample,
            "template_residue": residue,
            "b2b_marker_hits_in_text": [m for m in cfg["b2b_markers"] if m and m in text],
        },
        "localized_text": "",   # 填当地语言纯文本段落（会包成 <p>）；也可传 --desc-text
        "uri_map": {},          # 旧 URL → 擦字/翻译/白底后的新 URL
        "images": images,
    }
    review_path = out_dir / "review.json"
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[gate] 详情 {Path(args.detail).name}：主图 {len(main_urls)} 张 / 描述图 {len(desc_urls)} 张"
          f" / SKU 图 {len(sku_urls)} 张 / 去重 {len(order)} 张")
    print(f"[gate] description 中文文本字符 {cjk_count} 个；模板残渣 {residue or '无'}")
    print(f"[gate] 待判定清单: {review_path}")
    print(f"[gate] 图片目录: {out_dir}")
    failed = [x["id"] for x in images if x["download"].startswith("error")]
    if failed:
        print(f"[gate] ⚠ {len(failed)} 张下载失败（不阻断，但无法目视判定）: {failed[:6]}")
    print("[gate] 下一步：逐张看图回填 verdict / localized_text / uri_map，再跑 apply")
    return 0


# ── apply ──
def main_gate_violations(images, keep, uri_map):
    """主图判定硬闸（2026-11-19 678 教训）。返回违规说明列表；空=放行。
    只强制 main 通道（发布 5 张硬闸），desc 通道维持宽松。三道：
      ① L3_keep 必须有 probe 证据（image_fix.py probe，¥0.02/张）——治「没跑探针就标干净」
      ② probe 识别到中文(rec_text 含 CJK) → 不得 L3_keep（英文装饰字放行，避免误伤 main_01）
      ③ 换链后最终 URL 不得落在 TikTok 拉不到的域（发布必「参数无效」）
    """
    bad_hosts = ("dashscope", "kdzs-jxc-vpc")
    img_by_url = {x["url"]: x for x in images if x.get("url")}
    viol = []
    for u in keep:
        im = img_by_url.get(u) or {}
        if "main" not in (im.get("channels") or []):
            continue
        iid = im.get("id", "?")
        if im.get("verdict") == "L3_keep":
            ev = im.get("evidence") or {}
            if "rec_text" not in ev:
                viol.append(f"{iid}: L3_keep 但无 probe 证据——主图先跑 "
                            "`image_fix.py probe --review <此文件> --only L3_keep`")
            else:
                cjk_rt = [t for t in (ev.get("rec_text") or []) if CJK.search(t)]
                if cjk_rt:
                    viol.append(f"{iid}: L3_keep 但探针识别到中文 {cjk_rt[:4]}"
                                "→ 改 L1_inpaint/L2_localize，或从 desc/sku 换一张探针无中文的方形≥600 图")
        if any(h in uri_map.get(u, u) for h in bad_hosts):
            viol.append(f"{iid}: 换链后 URL 落在 TikTok 拉不到的域（发布必「参数无效」）"
                        "——发布用图必须 cbu01.alicdn.com 原链")
    return viol


def cmd_apply(args, cfg):
    d = read_json(args.detail)
    ri, ttm, v2 = get_v2(d)
    review = read_json(args.review)
    images = review.get("images") or []
    if not images:
        sys.exit("[gate] review.json 无 images，先跑 scan")

    verdicts = {x["url"]: (x.get("verdict") or "pending") for x in images if x.get("url")}
    bad = sorted({v for v in verdicts.values() if v not in VALID_VERDICTS})
    if bad:
        sys.exit(f"[gate] 非法 verdict: {bad}；只能是 {sorted(VALID_VERDICTS)}")

    pending = [u for u, v in verdicts.items() if v == "pending"]
    if pending and args.assume_pending == "abort":
        sys.exit(f"[gate] 还有 {len(pending)} 张未判定，拒绝执行。逐张看图回填 verdict，"
                 "或显式 --assume-pending drop（保守全删）")

    # 清单必须覆盖当前 detail 的三个通道，否则有未判定图会被静默放行
    cov = review_coverage(d, review)
    # 反向先查：判定表大面积失效时，"清单未覆盖"只是过期症状，先报根因
    if stale_too_much(cov["stale"], len(verdicts)):
        sys.exit(f"[gate] 判定表 {len(cov['stale'])}/{len(verdicts)} 条 URL 已不在当前 detail："
                 "review 已过期（detail 被 ERP 改写或重新落盘过）。旧快照的判定对现在的商品无效，"
                 "请重跑 scan 后重新判定；不要用 --assume-pending 绕过")
    if cov["missing"]:
        sys.exit(f"[gate] 清单未覆盖 {len(cov['missing'])} 个当前 detail 里的 URL"
                 f"（例 {cov['missing'][0][:70]}）；detail 可能已换过，重跑 scan 并重新判定")

    drop = {u for u, v in verdicts.items()
            if v == "L0_drop" or (v == "pending" and args.assume_pending == "drop")}
    keep = {u for u, v in verdicts.items() if v in ("L1_inpaint", "L2_localize", "L3_keep")}
    uri_map = {str(k): str(v) for k, v in (review.get("uri_map") or {}).items() if v}
    localized = review.get("localized_text") or args.desc_text or ""

    if CJK.search(localized):
        sys.exit("[gate] localized_text / --desc-text 含中文，闸门拒绝（目标语言须为泰文/越南文）")

    # ── 主图判定硬闸（2026-11-19 678 教训：目视拼缩略图把带中文横幅的 main_02/03 填成 L3_keep，
    #    上架后详情页仍是中文，被用户抓到。拼图缩略太小，靠目视必然漏判 → 主图必须机器证据）──
    main_viol = main_gate_violations(images, keep, uri_map)
    if main_viol:
        sys.exit("[gate] 主图判定硬闸拦截（678 教训，防详情页中文/水印漏判）:\n  - " +
                 "\n  - ".join(main_viol))

    # 主图：删 L0，其余按 uri_map 换 URL
    new_main = []
    for x in (v2.get("main_images") or []):
        if not isinstance(x, dict) or not x.get("uri"):
            continue
        u = x["uri"]
        if u in drop:
            continue
        y = dict(x)
        y["uri"] = uri_map.get(u, u)
        new_main.append(y)

    # description：丢弃整段 1688 模板，只重建「当地语言文本 + 保留的干净图」
    kept_tags, kept_urls = [], []
    for tag in IMG_TAG.findall(v2.get("description") or ""):
        u = src_of(tag)
        if not u or u in drop:
            continue
        new_uri = html.escape(uri_map.get(u, u), quote=True)
        kept_tags.append(f'<div style="text-align:center;"><img src="{new_uri}" '
                         'style="max-width:100%;height:auto;" /></div>')
        kept_urls.append(u)

    text_block = ""
    if localized.strip():
        s = localized.strip()
        if s.startswith("<"):
            # 已是 HTML：由作者自行分段（prepublish_guard 会查含文字段落数）
            text_block = s
        else:
            # 空行 = 段落分隔。整段塞进一个 <p> 会被 TikTok 后台提示「描述格式」需优化
            # （2026-11-12 回执，见 pitfalls 第十一章 / config/image_gate.yaml）。
            parts = [p.strip() for p in re.split(r"\n\s*\n", s) if p.strip()]
            text_block = "\n".join(f"<p>{html.escape(p)}</p>" for p in parts)
            if len(parts) < int(cfg["description_min_paragraphs"]):
                print(f"[gate] ⚠ localized_text 只有 {len(parts)} 段"
                      f"（要求 ≥{cfg['description_min_paragraphs']}）→ 请用空行分隔多段，"
                      "否则后台提示「描述格式」需优化")
    new_desc = (text_block + "\n" + "\n".join(kept_tags)).strip()

    if not new_desc:
        sys.exit("[gate] 重建后 description 为空 → 平台必报「商品描述不能为空」。"
                 "在 review.json 填 localized_text（当地语言），或传 --desc-text")
    if len(new_main) < int(cfg["min_clean_main_images"]):
        sys.exit(f"[gate] 删完主图只剩 {len(new_main)} 张（要求 ≥{cfg['min_clean_main_images']}）"
                 " → 该货源换掉，不要靠保留脏图凑数")
    if len(kept_tags) < int(cfg["min_desc_images_after_gate"]):
        print(f"[gate] ⚠ 重建后详情图 {len(kept_tags)} 张（阈值 {cfg['min_desc_images_after_gate']}）")

    clean = copy.deepcopy(d)
    _, _, cv2 = get_v2(clean)
    cv2["main_images"] = new_main
    cv2["description"] = new_desc

    # SKU 规格图：同一套判定。被判 L0 的置空（不留中文缩略图），其余按 uri_map 换 URL。
    # 不做这一步的话，32/35 个 SKU 的 1688 原图会原样上线（买家选规格第一眼可见）。
    sku_dropped, sku_mapped = 0, 0
    for s in (cv2.get("skus") or []):
        if not isinstance(s, dict):
            continue
        for a in (s.get("sales_attributes") or []):
            if not isinstance(a, dict):
                continue
            img = a.get("sku_img") or {}
            u = img.get("uri") if isinstance(img, dict) else None
            if not isinstance(u, str) or not u.strip():
                continue
            if u in drop:
                a["sku_img"] = {}
                sku_dropped += 1
            elif u in uri_map:
                a["sku_img"] = {**img, "uri": uri_map[u]}
                sku_mapped += 1

    # 用同一套硬闸验收重建结果，别只信"我改过了"
    issues, stats = inspect_detail(clean, cfg, review)
    fails = [x for x in issues if x[0] == "FAIL"]
    if fails:
        for _, code, msg in fails:
            print(f"[gate] ✗ {code}: {msg}")
        sys.exit("[gate] 重建结果未通过硬闸，不产文件、不写 ERP")

    out_dir = Path(args.out) if args.out else Path(args.review).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    clean_path = out_dir / "cleaned_detail.json"
    report_path = out_dir / "report.json"
    clean_path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")

    todo_inpaint = [u for u, v in verdicts.items() if v == "L1_inpaint"]
    todo_localize = [u for u, v in verdicts.items() if v == "L2_localize"]
    report = {
        "item_id": str(d.get("id") or ""),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "before": {"main_images": len(v2.get("main_images") or []),
                   "desc_img_tags": len(desc_image_urls(v2.get("description") or "")),
                   "sku_images": len(sku_image_urls(v2)),
                   "description_len": len(v2.get("description") or ""),
                   "cjk_text_chars": len(CJK.findall(plain_text(v2.get("description") or "")))},
        "after": {"main_images": len(new_main), "desc_img_tags": len(kept_tags),
                  "sku_images": len(sku_image_urls(cv2)),
                  "description_len": len(new_desc), "cjk_text_chars": stats.get("cjk_text_chars", 0)},
        "dropped_images": len(drop),
        "kept_images": len(keep),
        "sku_images_dropped": sku_dropped,
        "sku_images_remapped": sku_mapped,
        "review_stale_urls": len(cov["stale"]),
        "uri_map_applied": sum(1 for u in kept_urls + [x["uri"] for x in new_main] if u in uri_map),
        "pending_external_work": {
            "inpaint": todo_inpaint,
            "localize": todo_localize,
            "note": "L1/L2 图已保留但脚本不会擦字/翻译；处理完把新 URL 填回 uri_map 再 apply 一次",
        },
        "hard_gate": [{"level": a, "code": b, "msg": c} for a, b, c in issues],
        "cleaned_detail": str(clean_path),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[gate] 重建完成：主图 {report['before']['main_images']}→{len(new_main)}，"
          f"描述图 {report['before']['desc_img_tags']}→{len(kept_tags)}，"
          f"SKU 图 {report['before']['sku_images']}→{report['after']['sku_images']}"
          f"（删 {sku_dropped} / 换 {sku_mapped}），"
          f"中文文本 {report['before']['cjk_text_chars']}→{report['after']['cjk_text_chars']}")
    print(f"[gate] 整图删 {len(drop)} 张 / 保留 {len(keep)} 张 / 待外部处理 "
          f"{len(todo_inpaint) + len(todo_localize)} 张")
    print(f"[gate] 硬闸通过 → {clean_path}")
    print(f"[gate] 处置报告 → {report_path}")

    if not args.save:
        print("[gate] dry-run：未写 ERP。确认后加 --save；发布前务必再跑 verify")
        return 0
    return push_to_erp(clean, args, cfg)


def push_to_erp(clean, args, cfg):
    """按官方 buildTKRequest 结构写回 edit，再回读验收（唯一联网分支）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from erp_client import _post  # 延迟导入：离线子命令不需要 token

    ns = argparse.Namespace(token=None)
    ri, ttm, v2 = get_v2(clean)
    ttl_id = str(clean.get("id") or args.ttl_id or "")
    if not ttl_id:
        sys.exit("[gate] detail 里没有 id，写回需传 --ttl-id")
    body = {
        "id": ttl_id,
        "platform": "tiktok",
        "sellerId": str(clean.get("toSellerId") or ""),
        "currency": clean.get("currency") or "THB",
        "toCategoryId": clean.get("toCategoryId"),
        "toCategoryIdTree": clean.get("toCategoryIdTree"),
        "toCategoryNameTree": clean.get("toCategoryNameTree"),
        "title": clean.get("title"),
        "itemModel": {**ri, "tikTokItemModel": {**ttm, "productCreateRequestV2": v2}},
    }
    miss = [k for k in ("sellerId", "toCategoryId", "currency", "title") if not body.get(k)]
    if miss:
        sys.exit(f"[gate] edit body 关键字段为空 {miss}，中止（避免写坏记录）")
    r = _post(EDIT_API, body, ns)
    print(f"[gate] edit 已提交: {json.dumps(r, ensure_ascii=False)[:200]}（success 仅表示请求被接受）")

    back = _post(DETAIL_API, {"id": ttl_id}, ns)
    issues, stats = inspect_detail(back, cfg)
    if not stats.get("readable"):
        print("[gate] ⚠ 回读不到 itemModel（getDetail 草稿通道已关闭）——"
              "**无法据此宣称图片已验收**；改以发布日志 + TikTok 后台在售页目视为准")
        return 0
    for level, code, msg in issues:
        print(f"[gate] 回读 {level} {code}: {msg}")
    return 1 if any(x[0] == "FAIL" for x in issues) else 0


# ── verify ──
def review_gate_issues(d, review, require_review=True):
    """判定表覆盖率闸门（与 inspect_detail 分开，因为它需要 review 本体）。

    为什么必须默认开启（2026-11-13 审查实测）：旧 verify 不传 --review 也能退出码 0，
    于是"发布前 verify 必须 0"这条纪律可以在**一张图都没目视过**的情况下被满足。
    文本扫描只能证明"文字里没有汉字"，证明不了图里的中文。
    """
    issues = []
    if not review:
        if require_review:
            issues.append(("FAIL", "NO_REVIEW",
                           "未提供判定表（--review）：等于没有任何图被目视过，不能进 ttl-publish。"
                           "先跑 scan 回填 verdict；确已逐张人工确认再加 --no-require-review"))
        else:
            issues.append(("WARN", "NO_REVIEW",
                           "显式放行：本次未校验判定表覆盖率（图里是否有中文未经本脚本确认）"))
        return issues
    cov = review_coverage(d, review)
    if cov["missing"]:
        issues.append(("FAIL", "REVIEW_MISSING_URL",
                       f"{len(cov['missing'])} 张当前图不在判定表（例 {cov['missing'][0][:70]}）"
                       "→ 这些图从未被判定，重跑 scan"))
    if cov["pending"]:
        issues.append(("FAIL", "REVIEW_PENDING",
                       f"{len(cov['pending'])} 张仍是 pending → 未判定就发布"))
    if stale_too_much(cov["stale"], len(cov["verdicts"])):
        issues.append(("WARN", "REVIEW_STALE",
                       f"{len(cov['stale'])}/{len(cov['verdicts'])} 条判定已失效"
                       "（URL 不在当前 detail，detail 可能被改写或重新落盘）→ 建议重跑 scan"))
    return issues


def cmd_verify(args, cfg):
    d = read_json(args.detail)
    review = read_json(args.review) if args.review else None
    issues, stats = inspect_detail(d, cfg, review)
    issues += review_gate_issues(d, review, getattr(args, "require_review", True))
    if any(x[0] == "FAIL" for x in issues):
        issues = [x for x in issues if x[1] != "PASS"]   # 别在 FAIL 旁边留一行「闸门通过」
    print(f"[gate] {Path(args.detail).name}: {json.dumps(stats, ensure_ascii=False)}")
    for level, code, msg in issues:
        mark = {"FAIL": "✗", "WARN": "⚠", "INFO": "✓"}.get(level, "·")
        print(f"  {mark} [{level}] {code}: {msg}")
    fails = [x for x in issues if x[0] == "FAIL"]
    if args.json:
        Path(args.json).write_text(
            json.dumps({"pass": not fails, "stats": stats,
                        "issues": [{"level": a, "code": b, "msg": c} for a, b, c in issues]},
                       ensure_ascii=False, indent=2), encoding="utf-8")
    if fails:
        print(f"[gate] 拦截：{len(fails)} 项未通过，禁止 ttl-publish")
        return 1
    print("[gate] 通过：可进入发布前其余自检项")
    return 0


def main():
    ap = argparse.ArgumentParser(description="详情页中文/B2B 素材图片闸门")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="扫主图 + description + SKU 图三通道，下载并生成待判定清单")
    s.add_argument("--detail", required=True, help="ttl-detail 落盘的 JSON")
    s.add_argument("--out", help="输出目录（默认 erp_data/image_gate/<item_id>/）")
    s.add_argument("--no-download", action="store_true", help="只出清单不下图（离线自检）")
    s.add_argument("--timeout", type=int, default=15)
    s.add_argument("--config")

    a = sub.add_parser("apply", help="按判定重建 description / main_images；--save 才写 ERP")
    a.add_argument("--detail", required=True)
    a.add_argument("--review", required=True)
    a.add_argument("--out", help="输出目录（默认与 review.json 同目录）")
    a.add_argument("--desc-text", default="", help="当地语言纯文本段落（覆盖 review.localized_text）")
    a.add_argument("--assume-pending", choices=["abort", "drop"], default="abort",
                   help="未判定图的处理：abort=拒绝执行（默认）；drop=保守整图删")
    a.add_argument("--save", action="store_true", help="写操作：按官方 edit body 写回 ERP 并回读")
    a.add_argument("--ttl-id", default="", help="detail 无 id 时提供记录 ID")
    a.add_argument("--config")

    v = sub.add_parser("verify", help="发布前硬闸；退出码 1 = 拦截")
    v.add_argument("--detail", required=True, help="原始或 cleaned_detail.json")
    v.add_argument("--review", help="带 verdict 的清单，用于交叉检查被删 URL 是否残留")
    v.add_argument("--require-review", dest="require_review", action="store_true", default=True,
                   help="要求判定表覆盖全部当前图（默认开启；缺判定即拦截）")
    v.add_argument("--no-require-review", dest="require_review", action="store_false",
                   help="显式放行无判定表的情况（仅当图已逐张人工确认过）")
    v.add_argument("--json", help="把结果另存为 JSON（供其他脚本消费）")
    v.add_argument("--config")

    args = ap.parse_args()
    cfg = load_config(getattr(args, "config", None))
    return {"scan": cmd_scan, "apply": cmd_apply, "verify": cmd_verify}[args.cmd](args, cfg)


if __name__ == "__main__":
    sys.exit(main())
