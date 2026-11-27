#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto_assemble.py — 一次装配一个品（P1：替代 6 步手工）。

把 batch13 逐品手做的机械步骤合成一条命令：
  1. prep_one 生成 SKU 映射（自动带 extra 词表；重复 seller_sku 自动用 seq）
  2. category_template 渲染描述（马来语模板，字段由调用方给）
  3. assemble_guard 前置校验（SKU 长度/中文/重复货号/markup 反解）
  4. 生成 args json 并写入 pipeline state（publish_one set 等价）
  5. 输出 dry-run 命令，可直接接 `publish_one.py next`

标题/描述卖点属语义内容，需调用方给 --title / --desc-fields；
其它全部自动。拒绝猜价——markup 由 guard 反解给出可行区间，
调用方可传 --markup 覆盖，否则用 pricing.yaml 默认并校验。

用法（单个）：
  python3 scripts/auto_assemble.py --offer-id 954165453333 --template 围裙 \
      --title "Apron Kanvas Bunga Tali Comel..." \
      --desc-fields "product_name=...;feature1=...;color_list=Biru,Pink" \
      --extra-dict erp_data/_run/20261125_p10/dict_954165453333.json \
      --category-id 600427 --weight-g 320 --dims 25x20x4

预检模式（只校验不写）：
  python3 scripts/auto_assemble.py --offer-id ... --check-only
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
RUN = ROOT / "erp_data/_run"

sys.path.insert(0, str(HERE))
import timing  # noqa: E402


def _run(cmd):
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    nm = Path(cmd[1]).stem if len(cmd) > 1 else "?"
    timing.rec("sub.%s" % nm, t0, time.time(), {"cmd": " ".join(cmd[2:4])[:80]},
               parent="assemble")
    return r.returncode, r.stdout, r.stderr


def auto_detail(oid):
    fs = glob.glob(str(ROOT / ("erp_data/_run/pipeline/%s/detail.json/*" % oid)))
    if not fs:
        sys.exit("[assemble] %s 无 detail（先跑 precheck 采集）" % oid)
    return fs[0]


def auto_prep(oid, out_dir, extra=None):
    """生成 sku_map / sku_map_seq / sku_code_seq，返回主映射文件路径。"""
    cmd = [sys.executable, str(HERE / "prep_one.py"), "--detail", auto_detail(oid),
           "--out-dir", str(out_dir)]
    if extra:
        cmd += ["--extra", str(extra)]
    rc, out, err = _run(cmd)
    print("[assemble] prep_one rc=%d %s" % (rc, out.strip().splitlines()[-1][:80] if out else err[-80:]))
    smap = out_dir / "sku_map.json"
    if not smap.exists():
        sys.exit("[assemble] prep_one 未产出 sku_map.json")
    return smap


def detect_seq(oid, out_dir):
    """重复 seller_sku 时返回 (sku_map_seq, sku_code_seq) 路径，否则 None。"""
    codes = json.load(open(auto_detail(oid), encoding="utf-8")) \
        .get("reqInfo", {}).get("tikTokItemModel", {}).get("productCreateRequestV2", {}).get("skus", [])
    sellers = [str(s.get("seller_sku") or "") for s in codes]
    from collections import Counter
    dup = {k: v for k, v in Counter(sellers).items() if v > 1}
    if not dup:
        return None
    seq, code_seq = out_dir / "sku_map_seq.json", out_dir / "sku_code_seq.json"
    for p in (seq, code_seq):
        if not p.exists():
            sys.exit("[assemble] 重复 seller_sku 但缺 %s（prep_one bug？）" % p.name)
    return seq, code_seq


def render_desc(template, fields_str, out):
    fields = {}
    for kv in fields_str.split(";"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            fields[k.strip()] = v.strip()
    cmd = [sys.executable, str(HERE / "category_template.py"), "render",
           "--template", template, "--out", str(out)]
    for k, v in fields.items():
        cmd += ["--fields", "%s=%s" % (k, v)]
    rc, out_s, err = _run(cmd)
    if rc != 0 or not Path(out).exists():
        sys.exit("[assemble] 描述渲染失败: %s" % (err[-150:] or out_s[-150:]))
    n = len(Path(out).read_text(encoding="utf-8"))
    print("[assemble] 描述 %d 字符 → %s" % (n, out))
    return n


def suggest_markup(site, weight, dims, extra=None):
    """调用 assemble_guard 反解 markup 区间。extra 为 --src-min/--src-max 覆盖。"""
    cmd = [sys.executable, str(HERE / "assemble_guard.py"), "--suggest-markup",
           "--site", site, "--weight-g", str(weight), "--dims", dims]
    if extra:
        cmd += extra
    rc, out, err = _run(cmd)
    try:
        return json.loads(out)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offer-id", required=True)
    ap.add_argument("--template", default="围裙", help="描述模板名")
    ap.add_argument("--title", required=True, help="马来语标题（语义内容，需人工给）")
    ap.add_argument("--desc-fields", required=True, help="分号分隔 k=v，模板字段")
    ap.add_argument("--extra-dict", default="", help="SKU 中文补充词表 JSON")
    ap.add_argument("--category-id", required=True)
    ap.add_argument("--weight-g", type=float, default=300)
    ap.add_argument("--dims", default="25x20x4")
    ap.add_argument("--stock", default="300")
    ap.add_argument("--attr-names", default="颜色=Warna,规格=Saiz")
    ap.add_argument("--site", default="MY")
    ap.add_argument("--markup", default="", help="不加则用 pricing.yaml 默认并由 guard 校验")
    ap.add_argument("--batch-dir", default="", help="产物根目录（默认 _run/<timestamp>_auto）")
    ap.add_argument("--check-only", action="store_true", help="只校验不写 state")
    a = ap.parse_args()

    oid = a.offer_id
    base = Path(a.batch_dir) if a.batch_dir else RUN / (os.environ.get("BATCH_TAG", "auto_assemble"))
    prep_dir = base / "prep" / oid
    edit_dir = base / "edit"
    prep_dir.mkdir(parents=True, exist_ok=True)
    edit_dir.mkdir(parents=True, exist_ok=True)

    # 1. prep + SKU 映射
    smap = auto_prep(oid, prep_dir, a.extra_dict or None)
    seq = detect_seq(oid, prep_dir)

    # 2. 描述
    desc = edit_dir / ("desc_%s.txt" % oid)
    n = render_desc(a.template, a.desc_fields, desc)

    # 3. markup（显式优先，否则默认）
    mk = a.markup or ""
    if not mk:
        # 用 detail 里有货 SKU 的 source_price 反解
        detail = json.load(open(auto_detail(oid), encoding="utf-8")) \
            .get("reqInfo", {}).get("tikTokItemModel", {}).get("productCreateRequestV2", {})
        srcs = []
        for s in detail.get("skus", []):
            inv = sum(int(i.get("quantity") or 0) for i in (s.get("inventory") or []))
            src = (s.get("price") or {}).get("source_price")
            if inv > 0 and src:
                try:
                    srcs.append(float(src))
                except (TypeError, ValueError):
                    pass
        if srcs:
            sug = suggest_markup(a.site, a.weight_g, a.dims,
                                 ["--src-min", str(min(srcs)), "--src-max", str(max(srcs))])
            if sug and sug.get("in_band"):
                mk = str(sug["current"])
                print("[assemble] markup 默认 %.3f 在带内" % sug["current"])
            elif sug:
                mk = str(sug.get("recommend") or sug["markup_lo"])
                print("[assemble] ⚠ 默认出带，自动改为 %.3f（区间 [%.3f,%.3f]）"
                      % (float(mk), sug["markup_lo"], sug["markup_hi"]))
        if not mk:
            mk = "1.295"   # MY 免佣期默认，兜底
            print("[assemble] ⚠ 无 source_price，用 MY 默认 markup 1.295（未校验）")

    # 4. 组装 args
    def _rel(p):
        try:
            return str(Path(p).relative_to(ROOT))
        except ValueError:
            return str(p)   # 外部目录（测试用）保留绝对路径

    args = dict(category_id=a.category_id, title=a.title, dims=a.dims,
                weight_g=str(a.weight_g), stock=a.stock,
                attr_names=a.attr_names, markup=mk,
                desc_file=_rel(desc),
                mains_file="erp_data/_run/pipeline/%s/edit/mains.txt" % oid)
    if seq:
        args["sku_map_seq"] = _rel(seq[0])
        args["sku_code_seq"] = _rel(seq[1])
    else:
        args["sku_map"] = _rel(smap)
    args_file = edit_dir / ("args_%s.json" % oid)
    json.dump(args, open(args_file, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # 5. guard 校验
    guard_cmd = [sys.executable, str(HERE / "assemble_guard.py"),
                 "--detail", auto_detail(oid), "--site", a.site,
                 "--weight-g", str(a.weight_g), "--dims", a.dims]
    guard_cmd += (["--sku-map", str(seq[0])] if seq else ["--sku-map", str(smap)])
    guard_cmd += ["--markup", str(mk)]   # 校验装配实际用的 markup，不是 yaml 默认
    rc, gout, gerr = _run(guard_cmd)
    print(gout.strip()[:500])
    if rc != 0:
        print("[assemble] ✗ guard 拦截，未写 state。args → %s" % args_file)
        return rc

    if a.check_only:
        print("[assemble] --check-only，跳过写 state")
        return 0

    # 6. 写 pipeline state（等价于 publish_one set ...）
    state_path = ROOT / ("erp_data/_run/pipeline/%s.json" % oid)
    if not state_path.exists():
        sys.exit("[assemble] 无 pipeline state %s（先 precheck）" % state_path)
    state = json.load(open(state_path, encoding="utf-8"))
    state.setdefault("edit", {}).update(args)
    json.dump(state, open(state_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("\n[assemble] ✅ %s 装配完成" % oid)
    print("  args → %s" % args_file)
    print("  下一步: python3 scripts/publish_one.py next --offer-id %s" % oid)
    return 0


if __name__ == "__main__":
    _t0 = time.time()
    try:
        _rc = main()
    finally:
        timing.rec("assemble", _t0, time.time(),
                    {"script": "auto_assemble",
                     "offer_id": (sys.argv[sys.argv.index("--offer-id") + 1]
                                  if "--offer-id" in sys.argv else "")}, parent="batch")
    sys.exit(_rc)
