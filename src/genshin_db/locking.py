"""跨线程、跨进程互斥；锁文件不删除，进程退出由操作系统释放所有权。"""

from __future__ import annotations

import contextlib
import errno
import functools
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO


class LockBusyError(TimeoutError):
    """另一写入/更新/服务实例持锁，调用方可以稍后重试。"""


@dataclass
class _State:
    mutex: threading.RLock = field(default_factory=threading.RLock)
    depth: int = 0
    handle: BinaryIO | None = None


_states: dict[str, _State] = {}
_states_lock = threading.Lock()


def _after_fork() -> None:
    global _states, _states_lock
    # fork 不得把父进程的重入深度当成子进程所有权。
    for state in _states.values():
        if state.handle is not None:
            state.handle.close()
    _states = {}
    _states_lock = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


def _os_lock(handle: BinaryIO, unlock: bool = False) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB)


@contextlib.contextmanager
def file_lock(path: Path, timeout: float | None = 30):
    """同一线程可重入。0 立即返回占用，None 等待，等待可被中断。"""
    path = Path(path).resolve()
    with _states_lock:
        state = _states.setdefault(os.path.normcase(str(path)), _State())
    deadline = None if timeout is None else time.monotonic() + timeout
    if timeout is None:
        while not state.mutex.acquire(timeout=0.05):
            pass
    elif not state.mutex.acquire(timeout=max(0, timeout)):
        raise LockBusyError(f"操作正在进行，请稍后重试: {path.name}")
    entered = False
    try:
        if not state.depth:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("a+b")
            try:
                if path.stat().st_size == 0:
                    handle.write(b"\0")
                    handle.flush()
                while True:
                    try:
                        _os_lock(handle)
                        break
                    except OSError as e:
                        if timeout is None and e.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                            raise
                        remaining = None if deadline is None else deadline - time.monotonic()
                        if remaining is not None and remaining <= 0:
                            raise LockBusyError(
                                f"操作正在进行，请稍后重试: {path.name}"
                            ) from e
                        time.sleep(0.05 if remaining is None else min(0.05, remaining))
            except BaseException:
                handle.close()
                raise
            state.handle = handle
        state.depth += 1
        entered = True
        yield
    finally:
        if entered:
            state.depth -= 1
            if not state.depth:
                try:
                    _os_lock(state.handle, unlock=True)
                finally:
                    state.handle.close()
                    state.handle = None
        state.mutex.release()


def database_lock(db_path: Path, timeout: float = 30):
    db_path = Path(db_path).resolve()
    return file_lock(db_path.with_name(f".{db_path.name}.write.lock"), timeout)


def database_write(func):
    """首参数为数据库路径的写入口必须在取得锁后才打开连接。"""
    @functools.wraps(func)
    def locked(db_path, *args, **kwargs):
        with database_lock(db_path):
            return func(db_path, *args, **kwargs)
    return locked


def lock_is_held(path: Path) -> bool:
    try:
        with file_lock(path, timeout=0):
            return False
    except LockBusyError:
        return True
