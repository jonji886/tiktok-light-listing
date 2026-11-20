#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""category_template.py — 类目模板表工具（2026-11-20 P0-1，对标店小秘「产品模板」）。

模板表 = config/category_templates.yaml：类目 → ERP类目ID/树/重量档/尺寸/货代城市/描述模板文件。
目的：消灭每品现场「查类目树 + 定重量档 + 写描述 + 定城市」的重复决策（实测 ≈12 分钟/品）。

子命令：
  match    按候选表/标题匹配模板（title 词根命中 aliases），输出该品的模板套用结果
  show     列出全部模板
  record   从一个**已发布成功**的 ttl_id 回读 detail，自动沉淀/更新模板（类目树 DFS 反查祖先链，
           校验 AVAILABLE）——今天手抄祖先链的活儿以后都由它干
  render   把 desc_text_file 模板用 1688 标题/规格值/颜色表渲染成品文案（ttl_edit 内部同源调用）

用法：
  python3 scripts/category_template.py match --csv erp_data/selection_candidates_针线盒.csv
  python3 scripts/category_template.py record --ttl-id 1573354985664102400 --name 收纳盒 --aliases 针线盒,缝纫套装
  python3 scripts/category_template.py render --template 收纳盒 --fields "product_name=Kotak Jahit Magnetik;color_list=Merah Jambu, Kuning, Biru;..."
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TPL_YAML = os.path.join(ROOT, "config", "category_templates.yaml")
TREE_JSON = os.path.join(ROOT, "erp_data", "case_category_tree.json")

# 词根 → 模板名（match 用；record 可追加）
ALIASES = {
    "收纳盒": ["收纳", "储物", "simpanan", "盒"],
    "围裙": ["围裙", "apron", "厨房专用"],
}


# ────────── YAML 迷你读写（无第三方依赖；结构固定 templates: 两级）──────────
def load_templates():
    if not os.path.exists(TPL_YAML):
        return {}
    out, cur, name = {}, None, None
    for raw in open(TPL_YAML, encoding="utf-8"):
        line = raw.rstrip("\n")
        if not line.strip() or line.strip().startswith("#"):
            continue
        m = re.match(r"^  (\S+):\s*$", line)
        if m:
            name = m.group(1); cur = out[name] = {}; continue
        m = re.match(r"^    ([a-z0-9_]+):\s*(.+?)\s*(?:#.*)?$", line)
        if m and cur is not None:
            k, v = m.group(1), m.group(2).strip().strip("\"'")
            cur[k] = v
        # >- 多行 notes 简化：跳过缩进更深的续行（record 重写时不保留换行版式，可接受）
    return out


def save_templates(tpls):
    lines = ["# 类目模板表（category_template.py record 可自动追加/更新；手写纪律见文件头注释）",
             "templates:"]
    for name, t in tpls.items():
        lines.append("  %s:" % name)
        for k, v in t.items():
            v1 = str(v).replace("\n", " ").strip()
            lines.append("    %s: \"%s\"" % (k, v1))
    open(TPL_YAML, "w", encoding="utf-8").write("\n".join(lines) + "\n")


def category_chain(cid):
    """case_category_tree.json DFS 反查祖先链 → (id_tree, name_tree, permissionStatuses)。"""
    if not os.path.exists(TREE_JSON):
        raise RuntimeError("缺 %s（先跑类目树刷新）" % TREE_JSON)
    d = json.load(open(TREE_JSON, encoding="utf-8"))
    root = d["data"] if isinstance(d.get("data"), list) else [d.get("data")]
    path = []
    def walk(nodes):
        for n in nodes:
            path.append(n)
            if str(n.get("categoryId")) == str(cid):
                return True
            if walk(n.get("children") or []):
                return True
            path.pop()
        return False
    if not walk(root):
        raise RuntimeError("类目 %s 不在树里" % cid)
    ids = ",".join(str(x.get("categoryId")) for x in path)
    names = ",".join((x.get("fullName") or ">").split(">")[-1].strip() for x in path)
    return ids, names, (path[-1].get("permissionStatuses") or [])


# ────────── render：描述模板占位填充 ──────────
def render_desc(tpl, fields):
    """占位 {k} 用 fields 填；未填的占位**保留原样并报 WARN**（禁止静默把 {x} 发上线）。"""
    f = os.path.join(ROOT, tpl.get("desc_text_file") or "")
    if not os.path.exists(f):
        raise RuntimeError("描述模板文件缺失: %s" % f)
    s = open(f, encoding="utf-8").read()
    for k, v in fields.items():
        s = s.replace("{%s}" % k, str(v))
    left = re.findall(r"\{([a-z0-9_]+)\}", s)
    return s, left


def plain_len(s):
    import html as H
    t = re.sub(r"<[^>]+>", "", s)
    return len(H.unescape(t))


# ────────── 子命令 ──────────
def cmd_match(args):
    tpls = load_templates()
    aliases = dict(ALIASES)
    for name, t in tpls.items():
        for a in str(t.get("aliases") or "").split(","):
            if a.strip():
                aliases.setdefault(name, []).append(a.strip())
    pairs = []
    if args.title:
        pairs.append(("-", args.title))
    if args.csv:
        import csv as _csv
        for r in _csv.DictReader(open(args.csv, encoding="utf-8-sig")):
            t = r.get("title_zh") or r.get("offer_title") or r.get("标题") or ""
            if t:
                pairs.append((r.get("offer_id") or r.get("货源ID") or "?", t))
    if not pairs:
        print("[match] 无输入（--csv 或 --title）", file=sys.stderr)
        return 2
    hit = False
    for oid, t in pairs:
        name = None
        for n, words in aliases.items():
            if any(w and w in t for w in words):
                name = n; break
        if name and name in tpls:
            hit = True
            tp = tpls[name]
            print("[match] %s 「%s」→ 模板「%s」: cat=%s(%s) 重量档=%sg 尺寸=%s 货代=%s desc=%s"
                  % (oid, t[:20], name, tp.get("erp_category_id"),
                     tp.get("erp_category_name_tree"), tp.get("weight_g"),
                     tp.get("dims_cm"), tp.get("forwarder_city"), tp.get("desc_text_file")))
        else:
            print("[match] %s 「%s」→ 无模板（首品走全流程后 record 沉淀）" % (oid, t[:20]))
    return 0 if hit else 1


def cmd_show(args):
    for name, t in load_templates().items():
        print("[tpl] %s | cat=%s | %s | w=%sg dims=%s | fwd=%s | desc=%s | source=%s"
              % (name, t.get("erp_category_id"), t.get("erp_category_name_tree"),
                 t.get("weight_g"), t.get("dims_cm"), t.get("forwarder_city"),
                 t.get("desc_text_file"), t.get("source")))
    return 0


def cmd_record(args):
    """从已发布成功的 ttl_id 回读 detail → 沉淀模板。类目必须 AVAILABLE，否则拒绝。"""
    sys.path.insert(0, HERE)
    from erp_client import _post
    d = _post("/item/crossBorder/platformCollect/getDetail", {"id": args.ttl_id}, None)
    v2 = (d.get("reqInfo") or {}).get("tikTokItemModel", {}).get("productCreateRequestV2") or {}
    if not v2:
        sys.exit("[record] detail 无 productCreateRequestV2")
    cid = str(v2.get("category_id") or "")
    if not cid.isdigit():
        sys.exit("[record] category_id=%r 非数字（未匹配态不能沉淀模板）" % cid)
    ids, names, perm = category_chain(cid)
    if "AVAILABLE" not in perm:
        sys.exit("[record] 类目 %s(%s) 权限=%s 不含 AVAILABLE —— 拒绝沉淀（跨类目红线）"
                 % (cid, names, perm))
    w = (v2.get("package_weight") or {})
    wg = int(round(float(w.get("value") or 0) * 1000)) or None
    dim = v2.get("package_dimensions") or {}
    dims = "%sx%sx%s" % (dim.get("length"), dim.get("width"), dim.get("height")) if dim.get("length") else ""
    tpls = load_templates()
    t = tpls.setdefault(args.name, {})
    t.update({
        "erp_category_id": cid,
        "erp_category_id_tree": ids,
        "erp_category_name_tree": names,
        "weight_g": str(wg) if wg else t.get("weight_g", ""),
        "dims_cm": dims or t.get("dims_cm", ""),
        "forwarder_city": args.forwarder_city or t.get("forwarder_city", ""),
        "ship_transit_days": "1",
        "desc_text_file": t.get("desc_text_file", "erp_data/desc_templates/desc_my_%s.txt" % args.name),
        "keywords_ms": args.keywords_ms or t.get("keywords_ms", ""),
        "aliases": args.aliases or t.get("aliases", ""),
        "source": "measured",
        "notes": "record 自 ttl_id=%s (%s)" % (args.ttl_id, args.desc or "已发布"),
    })
    save_templates(tpls)
    print("[record] 模板「%s」已沉淀: cat=%s(%s) 重量=%sg dims=%s（校验 AVAILABLE 通过）"
          % (args.name, cid, names, wg, dims))
    if args.aliases:
        ALIASES.setdefault(args.name, []).extend([a.strip() for a in args.aliases.split(",") if a.strip()])
    return 0


def cmd_render(args):
    tpls = load_templates()
    tp = tpls.get(args.template)
    if not tp:
        sys.exit("[render] 无模板 %s（现有: %s）" % (args.template, ",".join(tpls)))
    fields = {}
    for kv in (args.fields or []):
        k, _, v = kv.partition("=")
        fields[k.strip()] = v
    s, left = render_desc(tp, fields)
    if left:
        print("[render] ⚠ 未填占位（禁止带上线）: %s" % ",".join(sorted(set(left))), file=sys.stderr)
    out = args.out or "/tmp/desc_rendered.txt"
    open(out, "w", encoding="utf-8").write(s)
    print("[render] → %s（纯文本 %d 字符，段落 %d，要求 ≥500/≥2）"
          % (out, plain_len(s), len([p for p in s.split("\n\n") if p.strip()])))
    return 0 if not left else 1


def main():
    ap = argparse.ArgumentParser(description="类目模板表工具（match/show/record/render）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("match", help="候选 CSV/标题 → 模板命中")
    p1.add_argument("--csv", default="")
    p1.add_argument("--title", default="")
    p2 = sub.add_parser("show", help="列出全部模板")
    p3 = sub.add_parser("record", help="从已发布成功 ttl_id 沉淀模板（校验 AVAILABLE）")
    p3.add_argument("--ttl-id", required=True)
    p3.add_argument("--name", required=True, help="模板名（如 收纳盒）")
    p3.add_argument("--aliases", default="", help="match 词根，逗号分隔（如 针线盒,缝纫套装）")
    p3.add_argument("--forwarder-city", default="")
    p3.add_argument("--keywords-ms", default="")
    p3.add_argument("--desc", default="")
    p4 = sub.add_parser("render", help="渲染描述模板")
    p4.add_argument("--template", required=True)
    p4.add_argument("--fields", action="append", default=[], help="k=v 可多次")
    p4.add_argument("--out", default="")
    args = ap.parse_args()
    if args.cmd == "match":
        return cmd_match(args)
    return {"show": cmd_show, "record": cmd_record, "render": cmd_render}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
