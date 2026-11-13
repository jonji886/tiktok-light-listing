#!/usr/bin/env python3
"""从本机 Chrome 的 Session Storage 自动提取快递助手跨境版的登录 JWT。

原理：
  ERP 前端把 JWT 存在 sessionStorage.qnquerystring，接口鉴权头就叫 qnquerystring。
  Chrome 会把 sessionStorage 持久化到 LevelDB（Session Storage 目录），
  但 .ldb 文件用 snappy 压缩、字符串用 UTF-16LE 编码，所以直接 grep 必然失败。
  本脚本解析 LevelDB block（含 snappy 解压）取出完整明文。

用法：
  python3 erp_token_grab.py            # 打印 token 及有效期
  python3 erp_token_grab.py --save     # 同时写入 erp_token.txt
  python3 erp_token_grab.py --check    # 顺带验证接口鉴权

前提：Chrome 处于登录状态（页面开着或曾开过 ERP）。
token 过期后：重新登录一次 ERP，再跑本脚本即可，无需手工复制。
"""
import os
import sys
import glob
import json
import time
import struct
import base64
import argparse
import urllib.request

CHROME_BASE = os.path.expanduser(
    "~/Library/Application Support/Google/Chrome/Default"
)
SESSION_DIRS = [
    os.path.join(CHROME_BASE, "Session Storage"),
]
TOKEN_KEY = "qnquerystring"
HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(HERE, "erp_token.txt")
BASE_URL = "https://erpg.kuaidizs.cn"

try:
    import cramjam
    def snappy_decompress(b):
        return bytes(cramjam.snappy.decompress_raw(b))
except Exception:                                    # pragma: no cover
    def snappy_decompress(b):
        raise RuntimeError("缺少 cramjam：pip install cramjam")


# ---------------------------------------------------------------- LevelDB 解析
def read_varint(buf, pos):
    result = shift = 0
    while True:
        if pos >= len(buf):
            raise ValueError("varint 越界")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, pos
        shift += 7
        if shift > 63:
            raise ValueError("varint 过长")


def parse_block_entries(block):
    if len(block) < 4:
        return []
    num_restarts = struct.unpack("<I", block[-4:])[0]
    if num_restarts == 0 or num_restarts * 4 + 4 > len(block):
        return []
    end = len(block) - 4 - num_restarts * 4
    pos, last_key, out = 0, b"", []
    while pos < end:
        try:
            shared, pos = read_varint(block, pos)
            unshared, pos = read_varint(block, pos)
            vlen, pos = read_varint(block, pos)
        except ValueError:
            break
        if pos + unshared + vlen > len(block):
            break
        key = last_key[:shared] + block[pos:pos + unshared]
        pos += unshared
        value = block[pos:pos + vlen]
        pos += vlen
        last_key = key
        out.append((key, value))
    return out


def read_block(raw, off, size):
    if off + size + 5 > len(raw):
        return None
    payload = raw[off:off + size]
    ctype = raw[off + size]
    if ctype == 0:
        return payload
    if ctype == 1:
        try:
            return snappy_decompress(payload)
        except Exception:
            return None
    return None


def iter_ldb_kv(path):
    raw = open(path, "rb").read()
    if len(raw) < 48:
        return
    foot = raw[-48:]
    if struct.unpack("<Q", foot[-8:])[0] != 0xdb4775248b80fb57:
        return
    try:
        _, p = read_varint(foot, 0)
        _, p = read_varint(foot, p)
        idx_off, p = read_varint(foot, p)
        idx_size, p = read_varint(foot, p)
    except ValueError:
        return
    index = read_block(raw, idx_off, idx_size)
    if index is None:
        return
    for _k, v in parse_block_entries(index):
        try:
            off, q = read_varint(v, 0)
            size, q = read_varint(v, q)
        except ValueError:
            continue
        data = read_block(raw, off, size)
        if data is None:
            continue
        for k, val in parse_block_entries(data):
            yield k, val


def iter_log_kv(path):
    raw = open(path, "rb").read()
    BS = 32768
    pending, pos = b"", 0
    while pos + 7 <= len(raw):
        block_off = pos % BS
        if BS - block_off < 7:
            pos += BS - block_off
            continue
        length = struct.unpack("<H", raw[pos + 4:pos + 6])[0]
        rtype = raw[pos + 6]
        data = raw[pos + 7:pos + 7 + length]
        pos += 7 + length
        if rtype == 1:
            rec = data
        elif rtype == 2:
            pending = data
            continue
        elif rtype == 3:
            pending += data
            continue
        elif rtype == 4:
            rec, pending = pending + data, b""
        else:
            continue
        try:
            p = 12
            count = struct.unpack("<I", rec[8:12])[0]
            for _ in range(count):
                if p >= len(rec):
                    break
                p += 1
                klen, p = read_varint(rec, p)
                key = rec[p:p + klen]
                p += klen
                vlen, p = read_varint(rec, p)
                val = rec[p:p + vlen]
                p += vlen
                yield key, val
        except Exception:
            continue


# ---------------------------------------------------------------- token 提取
def decode_value(raw):
    """Chrome 的存储值可能是 UTF-16LE（带 \\x00），也可能是纯 ASCII"""
    if b"\x00" in raw:
        return raw.decode("utf-16-le", "ignore")
    return raw.decode("utf-8", "ignore")


def grab_tokens(src_dirs=None):
    """返回 [(mtime, token), ...]，按新到旧排序"""
    src_dirs = src_dirs or SESSION_DIRS
    found = {}
    for d in src_dirs:
        if not os.path.isdir(d):
            continue
        files = glob.glob(os.path.join(d, "*.ldb")) + glob.glob(os.path.join(d, "*.log"))
        for f in files:
            gen = iter_log_kv if f.endswith(".log") else iter_ldb_kv
            try:
                for key, val in gen(f):
                    if TOKEN_KEY.encode() not in key:
                        continue
                    tok = decode_value(val).strip()
                    if tok.startswith("eyJ") and tok.count(".") == 2:
                        found[tok] = max(found.get(tok, 0), os.path.getmtime(f))
            except Exception:
                continue
    return sorted(((m, t) for t, m in found.items()), reverse=True)


def jwt_payload(token):
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:
        return {}


def call(path, token, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(BASE_URL + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("qnquerystring", token)
    req.add_header("appType", "3")
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true", help="写入 erp_token.txt")
    ap.add_argument("--check", action="store_true", help="验证接口鉴权")
    ap.add_argument("--quiet", action="store_true", help="只输出 token 本身")
    args = ap.parse_args()

    tokens = grab_tokens()
    if not tokens:
        print("未在 Chrome 存储中找到 token。请先在浏览器登录 ERP 后台。", file=sys.stderr)
        return 1

    now = time.time()
    alive = []
    for mtime, tok in tokens:
        pl = jwt_payload(tok)
        exp = pl.get("exp", 0)
        if exp > now:
            alive.append((exp, tok, pl))
    alive.sort(key=lambda x: x[0], reverse=True)

    if not alive:
        newest = jwt_payload(tokens[0][1])
        print(
            "找到 token 但已全部过期，最近一条 exp="
            + time.strftime("%Y-%m-%d %H:%M", time.localtime(newest.get("exp", 0)))
            + "。请重新登录 ERP 后再跑一次。",
            file=sys.stderr,
        )
        return 2

    exp, token, pl = alive[0]
    if args.quiet:
        print(token)
        return 0

    print(f"可用 token 数: {len(alive)} / 共 {len(tokens)}")
    print(f"userId : {pl.get('userId')}")
    print(f"exp    : {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(exp))}"
          f"（还有 {(exp - now) / 86400:.1f} 天）")

    if args.check:
        try:
            res = call("/index/aliLP/auth/status", token)
            d = res.get("data") or {}
            print(f"鉴权   : OK | 1688 授权={d.get('authorized')}")
            for a in d.get("authList") or []:
                print(f"         账号 {a.get('accountName')} ({a.get('accountId')}) "
                      f"状态={a.get('authStatusDesc')} 授权到期={a.get('tokenExpireTime')}")
        except Exception as e:
            print(f"鉴权   : 失败 {e}")
            return 3

    if args.save:
        with open(TOKEN_FILE, "w") as f:
            f.write(token)
        os.chmod(TOKEN_FILE, 0o600)
        print(f"已写入 : {TOKEN_FILE}")

    print()
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
