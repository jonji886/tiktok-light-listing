# -*- coding: utf-8 -*-
"""ms_terms 词表与「SKU 可译性」判定的唯一出处（2026-11-26 my10b D3 固化）

为什么要有这个模块：precheck 与 batch_listing 的 edit 对「合格 SKU」的口径
必须逐字一致，否则过闸品会在 edit 二次死亡。20261126_my10b 实证：
833710035247 / 971253800616 过了 C⑦，edit 报「无合格 SKU 可保留」——
每品白烧一轮 B0（认领+detail）+ 一次人工回合。

口径（与 batch_listing.pick_skus 的 cand 过滤同源）：
  可译 = 每个销售属性 value_name 经 tr_value 后非空；
  定价 = price.source_price > 0；两者齐才算一个候选 SKU。
"""
import csv
import re
from collections import Counter

TERM_CSV = "config/i18n/ms_terms.csv"


def load_terms():
    shapes, colors, attrs = [], {}, {}
    with open(TERM_CSV, encoding="utf-8-sig") as fh:
        lines = [l for l in fh if not l.lstrip().startswith("#")]  # 头部注释行剔除
    for r in csv.DictReader(lines):
        if not (r.get("src") or "").strip():
            continue
        k = r["kind"]
        if k == "shape":
            shapes.append((r["src"], r["tgt"]))
        elif k == "color":
            colors[r["src"]] = r["tgt"]
        elif k == "attr":
            attrs[r["src"]] = r["tgt"]
    # 长词优先替换，避免「口袋」吃掉「大口袋」
    shapes.sort(key=lambda x: -len(x[0]))
    return shapes, colors, attrs


def tr_value(v, shapes, colors):
    s = str(v)
    for zh, ms in shapes:
        if zh in s:
            s = s.replace(zh, " %s " % ms)
    for zh, ms in colors.items():
        if zh in s:
            s = s.replace(zh, " %s " % ms)
    toks = [t for t in re.split(r"[\s【】\[\]()（）@/＋+，,、\-]+", s) if t]
    keep = [t for t in toks if not re.search(r"[\u4e00-\u9fff]", t)]
    return " ".join(dict.fromkeys(keep)) if keep else None


def check_sku_selectable(skus, need_min=2, cap=3):
    """复刻「pick_skus 选款 → driver 组 --drop-sku → ttl_edit 等值砍」三层链，
    在认领前回答：edit 会不会「无合格 SKU 可保留」。

    my10b 实证（833710035247）：可译 10/10、有库存、价在带——但 5 个 SKU
    共用 seller_sku=7703。pick_skus 选中其中 2 个，drop 名单按原始顺序收集
    未选中 SKU 的 ssid（等值语义）→ "7703" 进 drop → ttl_edit 把 7703 的
    全部 5 个一起砍（含被选中的 2 个）→ kept=0 →「无合格 SKU 可保留」。
    971253800616 同型（小熊/墨绿款货号重复）。单靠可译性查不出来，必须三层一起演。

    返回 dict(ok, kept, reason)。价格出带不在这里演（价格闸在 price_gate 已前置，
    B⑦ 只管 edit 结构性死亡）。
    """
    shapes, colors, _ = load_terms()
    info = []  # (i, sid, translatable, inv_ok, priced)
    for i, s in enumerate(skus or []):
        sid = str(s.get("seller_sku") or "")
        try:
            src = float((s.get("price") or {}).get("source_price") or 0) or 0
        except (TypeError, ValueError):
            src = 0
        vals = [str(a.get("value_name") or "")
                for a in (s.get("sales_attributes") or [])]
        t = [tr_value(x, shapes, colors) for x in vals]
        translatable = bool(t) and not any(not x for x in t)
        inv = sum(int(x.get("quantity") or 0) for x in (s.get("inventory") or []))
        info.append((i, sid, translatable, inv > 0, src > 0))
    cand = [x for x in info if x[2] and x[4]]
    if len(cand) < need_min:
        return dict(ok=False, kept=0,
                    reason="可译且有价 SKU %d < %d" % (len(cand), need_min))
    sel_i = {x[0] for x in cand[:cap]}
    # driver 的 drop：全部 skus 中索引不在 sel_i 的 ssid（含不可译的）
    drop_sid = {sid for (i, sid, *_x) in info if i not in sel_i}
    # ttl_edit：先等值砍 drop，再砍零库存
    kept = {sid for (i, sid, tr, inv_ok, priced) in info
            if i in sel_i and sid not in drop_sid and inv_ok}
    if not kept:
        dup = sorted({sid for sid, n in
                      Counter(x[1] for x in cand).items()
                      if n > 1})[:3]
        return dict(ok=False, kept=0,
                    reason="seller_sku 重复 %s → drop 等值砍连带选中款, kept=0"
                           % "/".join(dup))
    return dict(ok=True, kept=len(kept))
