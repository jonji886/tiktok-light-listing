#!/usr/bin/env python3
"""发布前最后一道自检：补 image_gate / price_gate 都扫不到的洞。

2026-11-12 第二批实测新增。三个闸门的分工：
  price_gate.py       售价 / 库存 / 类目（读 tiktok_list.csv）
  image_gate.py verify description 与图片的中文、B2B 素材、模板残渣
  prepublish_guard.py  ← 全 productCreateRequestV2 递归扫汉字、product_attributes、
                         SKU 规格名、weight/dims 提醒、重复认领、
                         描述质量（≥500 纯文本字符 + ≥2 段）

真实事故 1：#3 沥水篮 description/主图全干净、两个闸门都过，仍被 TikTok 拒绝
  [12052250] 不允许使用汉字。修改产品属性的属性值名称: 悬挂
根因是 product_attributes 残留中文「悬挂/塑料」，image_gate 不扫这个字段。
清空 product_attributes=[] 后重发 success。

真实回执 2（2026-11-12 第三批，见 pitfalls 第十一章）：TikTok 后台编辑页弹窗
「2 个问题可能需要优化」——描述格式「将你的描述分成段落…」、描述质量
「撰写至少含 500 个字符的描述…」。这是**发布后的软提示**（弹窗仍可提交、商品已在售），
不是 12052xxx 硬错误。但本 Skill 长尾档的「短 description」流程必然触发它，
故发布前按「去标签纯文本 ≥500 字符 + ≥2 个含文字段落」兜底拦截。
平台字符计数口径 UNKNOWN，按纯文本保守实现（偏严，见 config/image_gate.yaml）。

只读、不写 ERP。退出码 0 = 全部通过，1 = 存在阻断项。
--allow-short-desc 可把描述质量降级为提醒（仅用户明确同意时使用）。

⚠ 读的是 erp_data/ttl_detail_<id>.json 本地快照。实测过：edit 清空 product_attributes
后若没重新 ttl-detail 落盘，快照仍是旧值 → guard 会误报 FAIL。
所以发布前请加 --refresh 强制重新落盘，或先手动跑 ttl-detail。
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DETAIL_DIR = os.path.join(ROOT, "erp_data")
LIST_CSV = os.path.join(DETAIL_DIR, "tiktok_list.csv")
CJK = re.compile(r"[\u4e00-\u9fff]")
TEMPLATE_JUNK = ("offer-template", "usemap", "1688.com", "alibaba")
BLOCK_RE = re.compile(
    r"<(?:p|div|li|h[1-6])\b[^>]*>(.*?)</(?:p|div|li|h[1-6])>", re.I | re.S)

# 阈值唯一来源 = config/image_gate.yaml（与 image_gate.py 共用）；读不到时保守兜底
try:
    from image_gate import load_config as _load_gate_config
    _gate_cfg = _load_gate_config()
except Exception:
    _gate_cfg = {}
DESC_MIN_CHARS = int(_gate_cfg.get("description_min_plain_chars", 500))
DESC_MIN_PARAS = int(_gate_cfg.get("description_min_paragraphs", 2))


def find_v2(obj):
    """getDetail 的层级不固定，递归取 productCreateRequestV2（可能是 JSON 字符串）。"""
    if isinstance(obj, dict):
        if "productCreateRequestV2" in obj:
            return obj["productCreateRequestV2"]
        for v in obj.values():
            r = find_v2(v)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_v2(v)
            if r is not None:
                return r
    return None


def as_dict(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return {}
    return v if isinstance(v, dict) else {}


def cjk_paths(obj, path="v2"):
    """列出 v2 内所有含中文的字符串及其路径。v2 是提交给平台的 payload，
    顶层的 toCategoryNameTree / regionName 是 ERP 内部字段，不在此列、不误报。"""
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            hits += cjk_paths(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits += cjk_paths(v, f"{path}[{i}]")
    elif isinstance(obj, str) and CJK.search(obj):
        hits.append((path, obj[:80]))
    return hits


def desc_paragraphs(desc):
    """含可见文字的块级段落数；1688 模板里只包 <img> 的空块不算分段。
    <br> 分隔视为段落界线（仅当确实存在文字块时）。"""
    blocks = [re.sub(r"<[^>]+>", "", b).strip() for b in BLOCK_RE.findall(desc)]
    n_text = sum(1 for b in blocks if b)
    brs = len(re.findall(r"<br\s*/?>", desc, re.I))
    return max(n_text, brs + 1 if brs and n_text else 0)


def check_one(tid, detail_path, list_rows, allow_short_desc=False):
    issues = []   # 阻断
    warns = []    # 提醒，不阻断
    stats = {}
    if not os.path.exists(detail_path):
        return ["detail 文件不存在，先跑 erp_client.py ttl-detail 或加 --refresh"], warns, stats
    age_min = (time.time() - os.path.getmtime(detail_path)) / 60
    stats["snapshot_age_min"] = round(age_min, 1)
    if age_min > 30:
        warns.append(f"本地详情快照已 {age_min:.0f} 分钟未更新；edit 后未重新落盘会读到旧值，建议加 --refresh")
    try:
        d = json.load(open(detail_path, encoding="utf-8"))
    except Exception as e:
        return [f"detail 解析失败: {e}"], warns, stats

    v2 = as_dict(find_v2(d))
    if not v2:
        return ["读不到 productCreateRequestV2（数据可能被错误 body 写坏，见 pitfalls 第二章）"], warns, stats

    # 1) 全 v2 递归扫汉字 —— 本轮唯一的发布失败根因在这里
    for p, text in cjk_paths(v2):
        issues.append(f"v2 含中文: {p} = {text}")
    pa = v2.get("product_attributes") or []
    if pa:
        warns.append(f"product_attributes 非空({len(pa)}项)：平台曾因其中中文拒发 [12052250]，"
                     f"无必要就清空为 []")

    # 2) description：非空 / 无中文 / 无模板残渣 + 后台回执的两条描述质量要求
    desc = v2.get("description") or ""
    plain = re.sub(r"<[^>]+>", " ", desc).replace("&nbsp;", " ")
    plain = re.sub(r"\s+", " ", plain).strip()
    stats["desc_len"] = len(desc)
    stats["desc_plain_len"] = len(plain)
    stats["desc_paragraphs"] = desc_paragraphs(desc)
    stats["desc_cjk"] = len(CJK.findall(plain))
    if not desc.strip():
        issues.append("description 为空")
    if stats["desc_cjk"]:
        issues.append(f"description 含 {stats['desc_cjk']} 个汉字")
    for junk in TEMPLATE_JUNK:
        if junk in desc:
            issues.append(f"description 残留 1688 模板/招商标记: {junk}")

    desc_quality = []
    if stats["desc_plain_len"] < DESC_MIN_CHARS:
        desc_quality.append(
            f"描述纯文本 {stats['desc_plain_len']} 字符 < {DESC_MIN_CHARS}"
            " → 后台「描述质量」：撰写至少含 500 个字符的描述")
    if stats["desc_paragraphs"] < DESC_MIN_PARAS:
        desc_quality.append(
            f"描述仅 {stats['desc_paragraphs']} 段 < {DESC_MIN_PARAS}"
            " → 后台「描述格式」：将你的描述分成段落")
    if desc_quality:
        (warns if allow_short_desc else issues).extend(desc_quality)

    # 3) 主图
    imgs = v2.get("main_images") or []
    stats["main_images"] = len(imgs)
    if not imgs:
        issues.append("main_images = 0（min_clean_main_images 硬闸）")
    elif not (imgs[0].get("uri") if isinstance(imgs[0], dict) else imgs[0]):
        issues.append("首图 uri 为空")

    # 4) SKU
    skus = v2.get("skus") or []
    stats["skus"] = len(skus)
    if not skus:
        issues.append("skus = 0")
    for i, s in enumerate(skus):
        price = (s.get("price") or {}).get("amount")
        try:
            if float(price) <= 0:
                issues.append(f"skus[{i}] 售价异常: {price}")
        except (TypeError, ValueError):
            issues.append(f"skus[{i}] 售价解析失败: {price}")
        inv = s.get("inventory") or []
        qty = (inv[0].get("quantity") if inv and isinstance(inv[0], dict) else None)
        try:
            if qty is None or int(qty) > 999999:
                issues.append(f"skus[{i}] 库存超 999999: {qty}")
        except (TypeError, ValueError):
            issues.append(f"skus[{i}] 库存异常: {qty}")
        for a in (s.get("sales_attributes") or []):
            if CJK.search(str(a.get("name") or "")) or CJK.search(str(a.get("value_name") or "")):
                issues.append(f"skus[{i}] 规格名含中文: {a.get('name')}={a.get('value_name')}")
    stats["specs"] = [a.get("value_name") for s in skus for a in (s.get("sales_attributes") or [])]
    if stats["specs"]:
        warns.append("SKU 规格名须与 1688 原始 skus 逐品对齐（图案/颜色/数量/尺寸+颜色各不相同，"
                     "禁止整批套同一套颜色名）")

    # 5) 类目（顶层字段，不在 v2 内）
    cat = str(d.get("toCategoryId") or "")
    stats["category"] = cat
    if cat in ("", "-9999", "0", "None"):
        issues.append(f"类目未匹配: {cat}")

    # 6) 重量尺寸：publishConfig 是整批统一参数，这里只能提醒
    warns.append("ttl-publish 的 --weight/--dims 必须按品单条提交；"
                 "禁用脚本默认 1kg / 10x10x10；尺寸按产品参数表推算的值标 INFERRED")
    if v2.get("package_weight"):
        warns.append(f"detail.package_weight={v2['package_weight']} 只是定价模型参数，"
                     f"不是平台申报重量（申报值在 publishConfig）")

    # 7) 重复认领
    if list_rows:
        row = next((x for x in list_rows if str(x.get("ttl_id")) == str(tid)), None)
        if row:
            oid = str(row.get("货源ID") or "")
            n = sum(1 for x in list_rows if str(x.get("货源ID") or "") == oid and oid)
            if n > 1:
                issues.append(f"同一货源ID 在采集箱出现 {n} 次（认领超时后盲目重试所致，需 ttl-del 去重）")
    return issues, warns, stats


def main():
    ap = argparse.ArgumentParser(description="发布前自检：v2 全字段汉字 / 属性 / SKU / 类目 / 重复 / 描述质量")
    ap.add_argument("--ids", help="ttl_id 逗号分隔")
    ap.add_argument("--detail", action="append", default=[], help="直接指定 detail json（可多次）")
    ap.add_argument("--refresh", action="store_true",
                    help="先重新 ttl-detail 落盘再检查（edit 之后必加，否则读旧快照）")
    ap.add_argument("--allow-short-desc", action="store_true",
                    help="把描述质量（≥500字符/≥2段）从阻断降级为提醒，需用户明确同意")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()

    paths = []
    for p in a.detail:
        paths.append((os.path.basename(p), p))
    for tid in [x.strip() for x in (a.ids or "").split(",") if x.strip()]:
        paths.append((tid, os.path.join(DETAIL_DIR, f"ttl_detail_{tid}.json")))
    if not paths:
        ap.error("需要 --ids 或 --detail")

    if a.refresh:
        cli = os.path.join(ROOT, "scripts", "erp_client.py")
        for tid, p in paths:
            if not p.startswith(DETAIL_DIR + os.sep + "ttl_detail_"):
                continue
            r = subprocess.run([sys.executable, cli, "ttl-detail", "--id", tid],
                              cwd=ROOT, capture_output=True, text=True)
            if r.returncode != 0:
                print(f"[warn] {tid} 刷新失败，仍用本地快照: {(r.stderr or '').strip()[:120]}",
                      file=sys.stderr)

    rows = []
    if os.path.exists(LIST_CSV):
        with open(LIST_CSV, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))

    results, failed = [], 0
    for tid, p in paths:
        issues, warns, stats = check_one(tid, p, rows, a.allow_short_desc)
        ok = not issues
        failed += 0 if ok else 1
        results.append({"id": tid, "ok": ok, "issues": issues, "warns": warns, "stats": stats})

    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("=== prepublish_guard（发布前最后一道，只读）===")
        for r in results:
            s = r["stats"]
            print(f"\n{'PASS' if r['ok'] else 'FAIL'} {r['id']} "
                  f"主图{s.get('main_images','?')} 描述{s.get('desc_len','?')}字符"
                  f"(纯文本{s.get('desc_plain_len','?')}/{s.get('desc_paragraphs','?')}段)"
                  f"/汉字{s.get('desc_cjk','?')} SKU{s.get('skus','?')} 类目{s.get('category','?')}")
            for x in r["issues"]:
                print(f"   ✗ {x}")
            for x in r["warns"]:
                print(f"   ! {x}")
        print(f"\n汇总: {len(results)-failed} PASS / {failed} FAIL "
              f"→ {'可进入 ttl-publish（仍须 price_gate 与 image_gate verify 退出码 0）' if failed == 0 else '禁止发布'}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
