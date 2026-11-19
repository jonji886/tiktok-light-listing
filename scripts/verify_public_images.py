#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公网商品图逐张回读校验：下载 URL，与本地正式图比对，防止上传后 URL↔图错配。

VN20 教训：批量并发上传返回的 path/URL 映射可能错配；不能把短链当成交付结果。
必须逐商品、逐张回读。

2026-11-13 审查修正两个缺陷：
  1) 旧判定是 `not exact` 即失败。任何 CDN/图床重编码（JPEG 质量重压、EXIF 剥离、
     加 ?x-oss-process 参数）都会改变字节 → 满分误报。现改为「哈希精确 或 像素 RMS
     在阈值内且尺寸一致」；RMS 之前算了却不参与判定，是死代码。
  2) 旧实现按**下标**对齐本地图与远端图，而错配恰恰表现为「顺序/内容错」。按位置比对
     只能发现"这一格不一样"，无法回答"它其实是另一张图"。改为先按 sha256 建本地反查表，
     命中即证明这张远端图就是本地某张图（且能指出是哪张），未命中才回落到位置比对 + RMS。

退出码：0 全部一致；1 有失败项；2 输入问题。
"""
import argparse
import hashlib
import io
import json
import os
import sys
import urllib.request

try:
    from PIL import Image
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False

DEFAULT_RMS = 3.0     # 0-255 尺度下的均方根差阈值（JPEG 重编码通常 < 2）


def as_items(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"]
    if isinstance(data, dict) and all(isinstance(v, dict) for v in data.values()):
        return list(data.values())
    return []


def load_manifest(path):
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def local_files(item, manifest, offer):
    """该商品全部本地图（manifest 优先，其次 payload.local_images），去重保序。"""
    rows = (manifest.get(offer) or {}).get("images") or []
    files = [r.get("file") for r in rows if isinstance(r, dict) and r.get("file")]
    files += [p for p in (item.get("local_images") or []) if p]
    out = []
    for p in files:
        if p and p not in out:
            out.append(p)
    return out


def build_hash_index(files):
    """本地图的 sha256 → 路径。用内容匹配取代位置匹配，能定位「这张其实是哪张」。"""
    idx = {}
    for p in files:
        if not os.path.exists(p):
            continue
        try:
            idx.setdefault(hashlib.sha256(read_bytes(p)).hexdigest(), p)
        except OSError:
            continue
    return idx


def download(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "doubao-light-listing/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def image_stats(data):
    if not HAVE_PIL:
        return None, None
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        # 非可识别图像字节（损坏/非图）：返回 None 让哈希通道继续判，
        # 不在此抛异常——2026-11-19 修复：旧版这里抛 UnidentifiedImageError 被外层
        # except 误标「下载失败」，把下载成功但解析失败的图错误归因
        return None, None
    return list(img.size), img


def rms(remote_img, local_img):
    if remote_img is None or local_img is None or remote_img.size != local_img.size:
        return None
    rb = remote_img.tobytes()
    lb = local_img.tobytes()
    total = sum((a - b) ** 2 for a, b in zip(rb, lb))
    return (total / len(rb)) ** 0.5


def judge_match(exact, rms_value, threshold):
    """纯函数：决定一张图是否算「同一张」。返回 (pass, reason)。

    精确哈希优先（最强证据）；不同字节时接受 RMS 容差（重编码）。
    没有 PIL 就算不出 RMS —— 此时不假装通过。
    """
    if exact:
        return True, "sha256 一致"
    if rms_value is None:
        return False, "哈希不一致且无法计算像素差（缺 PIL 或尺寸不同）"
    if rms_value <= threshold:
        return True, f"像素 RMS {rms_value:.2f} ≤ {threshold:g}（重编码）"
    return False, f"像素 RMS {rms_value:.2f} > {threshold:g}（内容已变）"


def main():
    ap = argparse.ArgumentParser(description="逐张回读公网商品图并与本地图哈希/像素核对")
    ap.add_argument("--payload", required=True)
    ap.add_argument("--manifest", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--rms-threshold", type=float, default=DEFAULT_RMS,
                    help=f"像素 RMS 容差（默认 {DEFAULT_RMS:g}；0 = 只认字节完全一致）")
    a = ap.parse_args()

    if not os.path.exists(a.payload):
        sys.stderr.write(f"[image-verify] 找不到 {a.payload}\n")
        return 2

    with open(a.payload, encoding="utf-8") as f:
        data = json.load(f)
    items = as_items(data)
    manifest = load_manifest(a.manifest)
    results = []

    for item in items:
        offer = str(item.get("offer_id") or "").strip()
        files = local_files(item, manifest, offer)
        hash_idx = build_hash_index(files)
        for index, url in enumerate(item.get("images") or []):
            rec = {"offer_id": offer, "index": index + 1, "url": url, "ok": False,
                   "exact": False, "matched_by": None, "local_file": None, "match": False}
            try:
                remote = download(url, a.timeout)
                rec["ok"] = True
                remote_hash = hashlib.sha256(remote).hexdigest()
                rec["size"], remote_img = image_stats(remote)

                local_path = hash_idx.get(remote_hash)
                if local_path:
                    # 内容命中：证明这张远端图就是本地某张图（可能不在本下标位置）
                    rec["matched_by"] = "sha256"
                    rec["exact"] = True
                    rec["local_file"] = local_path
                    if index < len(files) and files[index] != local_path:
                        rec["position_mismatch"] = (
                            f"远端第{index + 1}张其实是本地 {os.path.basename(local_path)}"
                            f"（第{files.index(local_path) + 1}张）→ URL/下标映射错配")
                    rec["match"], rec["reason"] = judge_match(True, None, a.rms_threshold)
                else:
                    # 未命中哈希：回落到同下标本地图，比像素差（容忍重编码）
                    pos = files[index] if index < len(files) else None
                    rec["matched_by"] = "position"
                    rec["local_file"] = pos
                    local_img = None
                    if pos and os.path.exists(pos):
                        local_img = image_stats(read_bytes(pos))[1]
                    elif pos:
                        rec["error"] = "local file missing"
                    value = rms(remote_img, local_img)
                    rec["rms"] = None if value is None else round(value, 3)
                    rec["match"], rec["reason"] = judge_match(False, value, a.rms_threshold)
            except Exception as e:
                rec["error"] = f"{type(e).__name__}: {e}"
                rec["reason"] = "下载失败"
            results.append(rec)

    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

    failed = [r for r in results if not (r["ok"] and r["match"])]
    mismatched = [r for r in results if r.get("position_mismatch")]
    if not a.out:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(f"[image-verify] 写入 {a.out}：共 {len(results)} 张，失败 {len(failed)} 张，"
              f"位置错配 {len(mismatched)} 张（RMS 容差 {a.rms_threshold:g}）")
        for r in failed:
            print(f"✗ {r['offer_id']} #{r['index']} {r.get('reason') or r.get('error')}")
        for r in mismatched:
            print(f"⚠ {r['offer_id']} #{r['index']} {r['position_mismatch']}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
