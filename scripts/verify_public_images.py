#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公网商品图逐张回读校验：下载 URL，与本地正式图做 SHA-256 和像素比对。

VN20 教训：批量并发上传返回的 path/URL 映射可能错配；不能把短链当成交付结果。
必须逐商品、逐张回读：URL 可访问、字节哈希一致、尺寸/像素一致后，才允许进入上架负载。

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
    return json.load(open(path, encoding="utf-8"))


def local_candidates(item, manifest, offer, index):
    rows = (manifest.get(offer) or {}).get("images") or []
    if rows and index < len(rows):
        return rows[index].get("file")
    locals_ = item.get("local_images") or []
    return locals_[index] if index < len(locals_) else None


def download(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "doubao-light-listing/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def image_stats(data):
    if not HAVE_PIL:
        return None, None
    img = Image.open(io.BytesIO(data)).convert("RGB")
    return list(img.size), img


def rms(remote_img, local_img):
    if remote_img is None or local_img is None or remote_img.size != local_img.size:
        return None
    rb = remote_img.tobytes()
    lb = local_img.tobytes()
    total = sum((a - b) ** 2 for a, b in zip(rb, lb))
    return (total / len(rb)) ** 0.5


def main():
    ap = argparse.ArgumentParser(description="逐张回读公网商品图并与本地图哈希/像素核对")
    ap.add_argument("--payload", required=True)
    ap.add_argument("--manifest", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--timeout", type=float, default=20.0)
    a = ap.parse_args()

    if not os.path.exists(a.payload):
        sys.stderr.write(f"[image-verify] 找不到 {a.payload}\n")
        return 2

    data = json.load(open(a.payload, encoding="utf-8"))
    items = as_items(data)
    manifest = load_manifest(a.manifest)
    results = []

    for item in items:
        offer = str(item.get("offer_id") or "").strip()
        for index, url in enumerate(item.get("images") or []):
            rec = {"offer_id": offer, "index": index + 1, "url": url, "ok": False, "exact": False}
            local = local_candidates(item, manifest, offer, index)
            rec["local_file"] = local
            try:
                remote = download(url, a.timeout)
                rec["ok"] = True
                remote_img = None
                local_img = None
                if HAVE_PIL:
                    rec["size"], remote_img = image_stats(remote)
                if local and os.path.exists(local):
                    local_bytes = open(local, "rb").read()
                    rec["exact"] = hashlib.sha256(remote).hexdigest() == hashlib.sha256(local_bytes).hexdigest()
                    if HAVE_PIL:
                        local_img = Image.open(io.BytesIO(local_bytes)).convert("RGB")
                        rec["rms"] = rms(remote_img, local_img)
                else:
                    rec["error"] = "local file missing"
            except Exception as e:
                rec["error"] = f"{type(e).__name__}: {e}"
            results.append(rec)

    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        json.dump(results, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    failed = [r for r in results if not r["ok"] or not r["exact"]]
    if not a.out:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(f"[image-verify] 写入 {a.out}：共 {len(results)} 张，失败 {len(failed)} 张")
        for r in failed:
            print(f"✗ {r['offer_id']} #{r['index']} {r.get('error') or '哈希不一致'}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
