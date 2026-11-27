#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""接口契约 smoke test（P1-10，2026-11-12 新增）

一次跑完「跨 skill 依赖 + 外部契约 + 本地配置/数据」的最小健康检查。
目的：接口变版或依赖漂移，要在**开工前**发现，而不是在铺了一半货之后才发现。

设计原则：
- **只读**：不发采集/认领/改价/发布任何写请求，最坏情况是打一次列表接口。
- 三档判定：PASS / WARN / FAIL。FAIL 才让退出码为 1；WARN 只是「需人工复核」。
- 每项独立 try，单点失败不中断其它检查。

用法：
  python3 scripts/health_check.py              # 全量（含网络）
  python3 scripts/health_check.py --skip-net   # 只查本地依赖与配置（可离线跑）
  python3 scripts/health_check.py --only dep,erp

单项说明：
  dep      aibuy_rank_client.py 本地副本与 dependency_contract.json 的哈希一致性 + 上游漂移
  runtime  python3 / swift / curl 可用性（contact sheet 与 1688 取数依赖）
  token    erp_token.txt 是否存在、是否被放宽权限
  erp      ERP 鉴权（只读列表接口 pageSize=1）
  fastmoss FastMoss 类目树 / 单品搜索是否仍免鉴权可用
  quota    铺货配额接口探测（未封装，只报可否读到，WARN 不阻断）
  frontend ERP 前端版本指纹，变版即 WARN（接口可能已变，需回归关键接口）
  config   pricing_calc --check（费率与 markup 是否仍自洽）
  data     backflow.csv 列契约 + tiktok_list.csv 可读性
"""

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ERP = "https://erp.kuaidizs.cn"
ERP_API = "https://erpg.kuaidizs.cn"
TOKEN_FILE = os.path.join(HERE, "erp_token.txt")
CONTRACT = os.path.join(ROOT, "config", "dependency_contract.json")
STATE_FILE = os.path.join(ROOT, "erp_data", "_run", "health_state.json")
BACKFLOW = os.path.join(ROOT, "erp_data", "backflow.csv")
TTL_LIST = os.path.join(ROOT, "erp_data", "tiktok_list.csv")

HEADERS = {
    "Content-Type": "application/json;charset=UTF-8",
    "appType": "3",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

BACKFLOW_HEADER = [
    "ttl_id", "offer_id", "title", "region", "shop", "publish_date",
    "due_t2", "due_t7", "due_t20",
    "t2_exposure", "t2_note", "t7_exposure", "t7_clicks", "t7_add_cart",
    "t20_orders", "t20_gmv", "t20_refund_rate", "t20_late_ship_rate",
    "verdict", "action", "note", "updated_at",
]

RESULTS = []          # [(group, name, status, detail)]
_LAST_NET = [0.0]
MIN_INTERVAL = 0.6    # ERP 侧有 707 限流，检查也守节奏


# ── 工具 ──────────────────────────────────────────────
def add(group, name, status, detail):
    RESULTS.append((group, name, status, detail))


def check_aidge():
    """Aidge 账户健康探测（P1：开工即暴露欠费，不再第 40 分钟才发现）。

    用空参数调用 ImageMatting：网关回 MissingImageUrl（参数错误）= 鉴权通过且账户正常，
    不产生任何计费。若返回 Service suspended / overdue payment / InsufficientBalance
    则为欠费或额度耗尽 → FAIL，提示充值或走本地 Vision OCR 降级。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "aidge_http", os.path.join(os.path.dirname(__file__), "aidge_http.py"))
    try:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        st, r = mod.call("ImageMatting")   # 空参数：只探网关，不计费
    except SystemExit as e:
        add("aidge", "Aidge 凭证", "WARN", "未加载 .env（先 source .env）；" + str(e)[:50])
        return
    except Exception as e:
        add("aidge", "Aidge 账户", "WARN", "探测失败: %s" % str(e)[:60])
        return
    msg = json.dumps(r, ensure_ascii=False) if not isinstance(r, str) else r
    low = msg.lower()
    if any(k in low for k in ("suspended", "overdue", "arrears", "insufficient", "欠费", "arrearage")):
        add("aidge", "Aidge 账户", "FAIL",
            "欠费/额度耗尽：" + (msg[:70]) + " → 充值，或走本地 Vision OCR 降级（scripts/evidence_local.py）")
    elif st in (400, 403) and ("mandatory" in low or "invalidparameter" in low or "missing" in low):
        add("aidge", "Aidge 账户", "PASS", "鉴权通过、账户正常（空参数探测 HTTP %s，不计费）" % st)
    elif st == 401 or "unauthorized" in low or "signature" in low:
        add("aidge", "Aidge 凭证", "FAIL", "鉴权失败 HTTP %s: %s" % (st, msg[:60]))
    else:
        add("aidge", "Aidge 账户", "WARN", "未知响应 HTTP %s: %s" % (st, msg[:70]))

    # ② 真实计费接口探测（2026-11-25 补：只测 ImageMatting 会「假绿」）
    # 实测教训：ImageMatting 空参返回 400 MissingImageUrl → 报 PASS，但当天 ImageRecognition
    # 实际返回 Service suspended（欠费），image_review 全线 pending，白跑 5 张图 + 一次人工介入。
    # 这里用**不存在的图 URL** 调 ImageRecognition：不下载图片、不产生有效计费，
    # 但足以区分「欠费 suspend」与「账户正常（回图片下载/img 参数类错误）」。
    try:
        st2, r2 = mod.call("ImageRecognition", {
            "ImageUrl": "https://example.invalid/healthcheck.png",
            "ReturnCharacter": "1"})
        m2 = json.dumps(r2, ensure_ascii=False) if not isinstance(r2, str) else r2
        l2 = m2.lower()
        if any(k in l2 for k in ("suspended", "overdue", "insufficient", "arrearage")):
            add("aidge", "Aidge OCR(计费接口)", "FAIL",
                "ImageRecognition 欠费停服 → image_review 会全线 pending。"
                "改用 auto_verdict 本地 Vision OCR 判级（scripts/auto_verdict.py）")
        elif st2 in (200, 400, 403, 500):
            add("aidge", "Aidge OCR(计费接口)", "PASS",
                "ImageRecognition 可调用（HTTP %s，非法 URL 探针不计费）" % st2)
        else:
            add("aidge", "Aidge OCR(计费接口)", "WARN", "HTTP %s: %s" % (st2, m2[:60]))
    except Exception as e:
        add("aidge", "Aidge OCR(计费接口)", "WARN", "探测异常: %s" % str(e)[:60])


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def http_post(url, body, token=None, timeout=25):
    """返回 (ok, payload, note)。payload 为 dict 或原始文本。"""
    now = time.time()
    if now - _LAST_NET[0] < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - (now - _LAST_NET[0]))
    _LAST_NET[0] = time.time()
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    for k, v in HEADERS.items():
        req.add_header(k, v)
    if token:
        req.add_header("qnquerystring", token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            code = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        code = e.code
    except Exception as e:
        return False, None, "网络失败: %s" % e
    try:
        j = json.loads(raw)
    except Exception:
        return False, raw[:160], "响应非 JSON (HTTP %s)" % code
    return True, j, "HTTP %s" % code


def http_get(url, timeout=20):
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", HEADERS["User-Agent"])
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return False, str(e)


def load_token():
    if not os.path.exists(TOKEN_FILE):
        return ""
    return io.open(TOKEN_FILE, encoding="utf-8").read().strip()


def save_state(**kw):
    try:
        d = {}
        if os.path.exists(STATE_FILE):
            d = json.loads(io.open(STATE_FILE, encoding="utf-8").read() or "{}")
        d.update(kw)
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        io.open(STATE_FILE, "w", encoding="utf-8").write(
            json.dumps(d, ensure_ascii=False, indent=2))
    except Exception as e:
        add("state", "写状态文件", "WARN", str(e))


def load_state():
    try:
        return json.loads(io.open(STATE_FILE, encoding="utf-8").read() or "{}")
    except Exception:
        return {}


# ── 检查项 ────────────────────────────────────────────
def check_dep():
    group = "dep"
    if not os.path.exists(CONTRACT):
        add(group, "dependency_contract.json", "FAIL", "缺失：跨 skill 依赖没有登记就谈不上防漂移")
        return
    try:
        c = json.loads(io.open(CONTRACT, encoding="utf-8").read())
    except Exception as e:
        add(group, "dependency_contract.json", "FAIL", "解析失败: %s" % e)
        return
    add(group, "dependency_contract.json", "PASS", "%d 条依赖已登记" % len(c.get("dependencies") or []))

    for dep in c.get("dependencies") or []:
        local = os.path.join(ROOT, dep["local"])
        if not os.path.exists(local):
            add(group, dep["name"], "FAIL", "本地副本缺失 %s（选品取 1688 的入口断了）" % dep["local"])
            continue
        got = sha256_file(local)
        if got != dep.get("sha256"):
            add(group, dep["name"], "FAIL",
                "本地副本哈希与登记不符（被改过）：%s… vs 登记 %s…\n"
                "        → 按 contract_surface 核对接口后更新 config/dependency_contract.json"
                % (got[:12], str(dep.get("sha256"))[:12]))
        else:
            add(group, dep["name"], "PASS", "本地副本哈希一致（%d 行，纯标准库）" % dep.get("lines", 0))

        up_rel = dep.get("upstream")
        if up_rel:
            up = os.path.join(ROOT, up_rel)
            if not os.path.exists(up):
                add(group, "上游漂移", "SKIP", "上游源不在本机（%s），跳过比对" % up_rel)
            elif sha256_file(up) != dep.get("sha256"):
                add(group, "上游漂移", "WARN",
                    "上游 %s 已变更，本地副本可能过时\n        → %s" % (up_rel, dep.get("on_drift")))
            else:
                add(group, "上游漂移", "PASS", "上游与本地副本一致，无漂移")


def check_runtime():
    group = "runtime"
    add(group, "python3", "PASS", "%s (%s)" % (sys.version.split()[0], sys.executable.split("/")[-1] or "python3"))
    for cmd, why, level in (("swift", "contact sheet 拼图（选品目视取证）", "WARN"),
                            ("curl", "1688 详情页取数（urllib 指纹会被 baxia 拦）", "WARN")):
        p = shutil.which(cmd)
        if p:
            add(group, cmd, "PASS", p)
        else:
            add(group, cmd, level, "未找到 —— %s 会降级或不可用" % why)


def check_token():
    group = "token"
    if not os.path.exists(TOKEN_FILE):
        add(group, "erp_token.txt", "FAIL",
            "缺失：先 python3 scripts/erp_token_grab.py --save --check")
        return ""
    tok = load_token()
    if not tok:
        add(group, "erp_token.txt", "FAIL", "文件为空")
        return ""
    mode = os.stat(TOKEN_FILE).st_mode & 0o777
    perm_note = "权限 %o" % mode
    if mode & 0o077:
        add(group, "erp_token.txt", "WARN",
            "存在但%s，组/其他用户可读（JWT 可冒用）→ chmod 600 scripts/erp_token.txt" % perm_note)
    else:
        add(group, "erp_token.txt", "PASS", "存在，%s，长度 %d" % (perm_note, len(tok)))
    return tok


def check_shop_config():
    """店铺凭证配置 fail-fast 自检（2026-11-26 事故：.env 缺 *_SELLER_ID，
    第一波 20 品 claim 全把占位符发给接口 → 每品白烧 ~90s 回查轮询，一波 -35min。
    token 有效 ≠ 能干活：此检查与网络无关，纯本地读环境变量）。"""
    group = "erp"
    # 自动加载 .env（TT_SHOPS 在 import 时读环境变量；health_check 平时靠
    # erp_token.txt 过鉴权，不代表 shell 里 source 过 .env）
    envf = os.path.join(ROOT, ".env")
    if os.path.exists(envf):
        for line in open(envf, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    try:
        import erp_client as EC
        shops = EC.TT_SHOPS
    except Exception as e:
        add(group, "店铺配置", "FAIL", "erp_client 导入失败: %s" % str(e)[:60])
        return
    bad = []
    for region, s in shops.items():
        sid = str(s.get("sellerId") or "")
        nick = str(s.get("nick") or "")
        if not sid or sid.startswith("<") or nick.startswith("<"):
            bad.append(region)
    if bad:
        add(group, "店铺配置(sellerId)", "FAIL",
            "%s 未配置或仍是占位符 → claim/publish 必炸。修法：erp_client.py shops "
            "取真实值写入 .env，set -a; source .env; set +a" % "/".join(bad))
    else:
        add(group, "店铺配置(sellerId)", "PASS",
            "TH/VN/MY sellerId 均已配置（MY=%s）" % shops["MY"]["sellerId"][-6:])


def check_erp(token):
    group = "erp"
    if not token:
        add(group, "鉴权", "SKIP", "无 token，跳过")
        return
    ok, payload, note = http_post(
        ERP + "/item/crossBorder/platformCollect/queryListPage",
        {"pageNo": 1, "pageSize": 1}, token=token)
    if not ok:
        add(group, "鉴权（只读列表）", "FAIL", note)
        return
    if payload.get("success"):
        data = payload.get("data") or {}
        add(group, "鉴权（只读列表）", "PASS",
            "success, total=%s（token 有效）" % data.get("total"))
    else:
        code = payload.get("errorCode")
        msg = payload.get("errorMessage", "")
        if code == 703 or "token" in str(msg):
            add(group, "鉴权（只读列表）", "FAIL",
                "token 失效(703)：重新登录 ERP 后跑 erp_token_grab.py --save")
        elif code == 707:
            add(group, "鉴权（只读列表）", "WARN", "限流(707)：稍后重跑，非契约问题")
        else:
            add(group, "鉴权（只读列表）", "FAIL", "code=%s msg=%s" % (code, msg))


def check_fastmoss():
    group = "fastmoss"
    ok, payload, note = http_post(ERP_API + "/item/fastmoss/category/list", {})
    if ok and payload.get("success"):
        data = payload.get("data")
        n = len(data) if isinstance(data, list) else (len(data or {}) if isinstance(data, dict) else 0)
        add(group, "类目树（免鉴权）", "PASS", "可读，顶层节点 %d" % n)
    else:
        add(group, "类目树（免鉴权）", "FAIL",
            "不再免鉴权或接口变更 → 选品 L2 层（FastMoss 单品批量）失效：%s" % note)

    # 2026-11-19：主攻站点改为 MY，探活跟着主站点走（接口对任一站点可用即代表通道通）
    body = {"keywords": None, "filter": {"region": "MY", "shopType": 2},
            "orderby": [{"field": "day7_units_sold", "order": "desc"}],
            "page": 1, "pagesize": 2}
    ok, payload, note = http_post(ERP_API + "/item/fastmoss/product/search", body)
    if ok and payload.get("success"):
        d = payload.get("data") or {}
        rows = d.get("list") or []
        sample = rows[0] if rows else {}
        has_cor = "creatorOrderRate" in sample
        add(group, "单品搜索", "PASS" if has_cor else "WARN",
            "total=%s，返回 %d 条%s" % (d.get("total"), len(rows),
                                       "" if has_cor else "；⚠ 缺 creatorOrderRate 字段，判读口径要改"))
    else:
        add(group, "单品搜索", "FAIL",
            "接口不可用 → 选品只能降级到 proxy_mode（上限 6 分）：%s" % note)


def check_quota(token):
    group = "quota"
    if not token:
        add(group, "铺货配额", "SKIP", "无 token，跳过")
        return
    ok, payload, note = http_post(ERP + "/trade/crossItemDistrib/quotaQuery", {}, token=token)
    if ok and payload.get("success"):
        add(group, "铺货配额", "PASS", "可读：%s" % json.dumps(payload.get("data"), ensure_ascii=False)[:120])
    elif ok:
        add(group, "铺货配额", "WARN", "未封装成命令（body 待实测）：code=%s %s"
            % (payload.get("errorCode"), payload.get("errorMessage", "")[:80]))
    else:
        add(group, "铺货配额", "WARN", note)


def check_frontend():
    group = "frontend"
    ok, html = http_get(ERP + "/index.html")
    if not ok:
        add(group, "前端版本", "WARN", "取不到 index.html：%s" % html[:80])
        return
    m = re.search(r"erp(?:g)?/(\d+\.\d+\.\d+)/", html) or re.search(r"erp/(\d+\.\d+\.\d+)", html)
    ver = m.group(1) if m else "?"
    prev = (load_state() or {}).get("frontend_version")
    if ver == "?":
        add(group, "前端版本", "WARN", "index.html 里认不出版本号，chunk 路径规则可能改了")
    elif prev and prev != ver:
        add(group, "前端版本", "WARN",
            "版本变了 %s → %s：接口/chunk 布局可能已改\n"
            "        → 回归 ttl-list / edit / batchPublish 三个关键接口再开工" % (prev, ver))
    elif prev:
        add(group, "前端版本", "PASS", "%s（与上次一致）" % ver)
    else:
        add(group, "前端版本", "PASS", "%s（首次记录）" % ver)
    save_state(frontend_version=ver, checked_at=time.strftime("%Y-%m-%d %H:%M:%S"))


def check_config():
    group = "config"
    p = subprocess.run([sys.executable, os.path.join(HERE, "pricing_calc.py"), "--check"],
                       cwd=ROOT, capture_output=True, text=True)
    if p.returncode == 0:
        add(group, "pricing_calc --check", "PASS", "费率与 markup 自洽")
    else:
        tail = (p.stdout or p.stderr).strip().splitlines()[-3:]
        add(group, "pricing_calc --check", "FAIL", "退出码 %d：%s" % (p.returncode, " / ".join(tail)))
    for f in ("config/pricing.yaml", "config/selection.yaml", "config/image_gate.yaml"):
        add(group, f, "PASS" if os.path.exists(os.path.join(ROOT, f)) else "FAIL", "")

    try:
        sys.path.insert(0, HERE)
        import select_gate  # noqa
        thresholds = select_gate.load_thresholds()
        miss = [k for k in ("clean_consumer_images_min", "stock_min", "min_order_qty",
                            "chargeable_weight_max_g", "max_transit_days") if k not in thresholds]
        add(group, "selection.yaml 阈值读回", "FAIL" if miss else "PASS",
            ("缺 %s" % miss) if miss else "%d 项阈值可读" % len(thresholds))
    except Exception as e:
        add(group, "selection.yaml 阈值读回", "FAIL", "select_gate 读不回：%s" % e)


def check_data():
    group = "data"
    if not os.path.exists(BACKFLOW):
        add(group, "backflow.csv", "FAIL", "缺失 → python3 scripts/backflow.py sync")
    else:
        first = io.open(BACKFLOW, encoding="utf-8-sig").readline().strip()
        cols = [c.strip() for c in first.split(",")]
        if cols == BACKFLOW_HEADER:
            add(group, "backflow.csv 列契约", "PASS", "%d 列一致" % len(cols))
        else:
            add(group, "backflow.csv 列契约", "FAIL",
                "列与 schema 不符：多 %s / 缺 %s"
                % (sorted(set(cols) - set(BACKFLOW_HEADER)),
                   sorted(set(BACKFLOW_HEADER) - set(cols))))
    if os.path.exists(TTL_LIST):
        add(group, "tiktok_list.csv", "PASS", "存在")
    else:
        add(group, "tiktok_list.csv", "WARN", "缺失：先 erp_client.py ttl-list 落盘")
    cand = os.path.join(ROOT, "erp_data", "selection_candidates.csv")
    if os.path.exists(cand):
        first = io.open(cand, encoding="utf-8-sig").readline().strip()
        n = len([c for c in first.split(",") if c.strip()])
        add(group, "selection_candidates.csv", "PASS" if n >= 20 else "WARN", "%d 列" % n)
    else:
        add(group, "selection_candidates.csv", "WARN",
            "缺失 → select_gate.py --init-template")


# ── 输出 ──────────────────────────────────────────────
def render():
    icon = {"PASS": "✅", "WARN": "⚠️ ", "FAIL": "❌", "SKIP": "⏭ "}
    lines = []
    cur = None
    for group, name, status, detail in RESULTS:
        if group != cur:
            lines.append("")
            lines.append("[%s]" % group)
            cur = group
        lines.append("  %s %-26s %s" % (icon.get(status, "?"), name, detail if detail else ""))
    counts = {}
    for _, _, st, _ in RESULTS:
        counts[st] = counts.get(st, 0) + 1
    lines.append("")
    lines.append("汇总: PASS %d / WARN %d / FAIL %d / SKIP %d"
                 % (counts.get("PASS", 0), counts.get("WARN", 0),
                    counts.get("FAIL", 0), counts.get("SKIP", 0)))
    if counts.get("FAIL"):
        lines.append("结论: 有 FAIL —— 先修 FAIL 项再开工（WARN 只代表需人工复核）")
    elif counts.get("WARN"):
        lines.append("结论: 无硬故障，但有 %d 项需人工复核" % counts.get("WARN", 0))
    else:
        lines.append("结论: 全绿，接口契约与依赖均可用")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="接口契约 smoke test（只读）")
    ap.add_argument("--skip-net", action="store_true", help="只跑本地检查，不发网络请求")
    ap.add_argument("--only", default="", help="逗号分隔：dep,runtime,token,aidge,erp,fastmoss,quota,frontend,config,data")
    args = ap.parse_args()

    only = {s.strip() for s in args.only.split(",") if s.strip()}

    def want(g):
        return (not only) or (g in only)

    if want("dep"):
        check_dep()
    if want("runtime"):
        check_runtime()
    token = check_token() if want("token") else ""
    if want("aidge"):
        if args.skip_net:
            add("aidge", "Aidge 账户", "SKIP", "--skip-net")
        else:
            check_aidge()
    if args.skip_net:
        for g in ("erp", "fastmoss", "quota", "frontend"):
            if want(g):
                add(g, "跳过", "SKIP", "--skip-net")
        check_shop_config()  # 店铺配置是本地检查，skip-net 也要跑
        if want("erp") or want("fastmoss") or want("quota") or want("frontend"):
            add("net", "网络检查", "SKIP", "--skip-net：仅本地检查")
    else:
        if want("erp"):
            check_erp(token)
            check_shop_config()
        if want("fastmoss"):
            check_fastmoss()
        if want("quota"):
            check_quota(token)
        if want("frontend"):
            check_frontend()
    if want("config") and not only:
        check_config()
    elif want("config"):
        check_config()
    if want("data") and not only:
        check_data()
    elif want("data"):
        check_data()

    print(render())
    sys.exit(1 if any(st == "FAIL" for _, _, st, _ in RESULTS) else 0)


if __name__ == "__main__":
    main()
