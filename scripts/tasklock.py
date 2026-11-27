#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tasklock.py — 任务级进程锁（2026-11-27 工程化：防两个进程同时跑同一任务）

现状问题：publish_one 的状态文件是「读-改-写」整份 JSON，双开同一 offer 会互相覆盖
（实测纪律场景：driver 与手工 next 并发）。引入 fcntl 独占锁即可，不需要 Redis/DB。

用法：
    import tasklock
    with tasklock.acquire("pipeline_529647564532") as lk:
        ...            # 拿不到锁抛 LockedError
语义：
    - 独占、非阻塞、同机；进程退出（含崩溃）由内核自动释放；
    - 锁文件 = erp_data/_run/locks/<name>.lock，内容为持有者 pid（供人排查）。
"""
import errno
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOCK_DIR = os.path.join(ROOT, "erp_data", "_run", "locks")


class LockedError(RuntimeError):
    """锁被其他进程持有。附带持有者 pid（读到才有）。"""

    def __init__(self, name, holder_pid=None):
        self.name = name
        self.holder_pid = holder_pid
        extra = "（持有者 pid=%s）" % holder_pid if holder_pid else ""
        super().__init__("任务 %s 正被另一进程执行%s——禁止并发跑同一任务" % (name, extra))


def _pid_of(path):
    try:
        return int((open(path, encoding="utf-8").read() or "0").strip() or 0) or None
    except Exception:
        return None


class Lock:
    def __init__(self, name, path, fh):
        self.name, self.path, self._fh = name, path, fh

    def release(self):
        try:
            import fcntl
            fcntl.flock(self._fh, fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            self._fh.close()
        except Exception:
            pass


def acquire(name, blocking=False):
    """非阻塞获取独占锁；已持有则抛 LockedError。blocking=True 时等待最多 60s。"""
    import fcntl
    os.makedirs(LOCK_DIR, exist_ok=True)
    path = os.path.join(LOCK_DIR, "%s.lock" % name)
    fh = open(path, "a+")
    try:
        if blocking:
            import time
            deadline = time.time() + 60
            while True:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as e:
                    if e.errno not in (errno.EAGAIN, errno.EACCES) or time.time() > deadline:
                        raise LockedError(name, _pid_of(path))
                    time.sleep(0.5)
        else:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        if e.errno in (errno.EAGAIN, errno.EACCES):
            fh.close()
            raise LockedError(name, _pid_of(path))
        raise
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    return Lock(name, path, fh)
