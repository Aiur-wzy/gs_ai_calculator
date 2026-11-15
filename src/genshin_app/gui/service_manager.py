"""db serve 生命周期自动化（app.md 前提 6 / G1）。

- 随 GUI 启动拉起本地服务；已有实例运行则复用（ADR-0007 单实例）
- GUI 退出时仅清理自己拉起的实例（owned），不动别人启动的服务
- 自有实例崩溃或复用实例退出时，看门线程重新拉起或复用并发启动的赢家
- 多窗口共享服务允许短暂重连；每个窗口仍只终止自己拉起的实例
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from subprocess import DEVNULL, Popen, TimeoutExpired

from genshin_db.locking import LockBusyError, file_lock, lock_is_held
from genshin_db.service import SERVICE_INFO_NAME, check_existing_service

# A normal GUI admission deeply verifies the database before this child is
# launched, and the standalone ``db serve`` process deliberately repeats that
# verification at its own production boundary.  A cold 600+ MiB database can
# need more than 20 seconds, and a slow HDD/antivirus pass can make the repeated
# verification take well over one minute.  This is deterministic local work:
# while the child is alive the loop keeps publishing no false failure, and a
# real child exit is still detected immediately.  Keep a generous last-resort
# ceiling only for a genuinely wedged child instead of treating an otherwise
# healthy cold start as an error.
SPAWN_TIMEOUT = 300.0


def _serve_cmd(data_dir: Path) -> list[str]:
    """打包态优先使用同目录 CLI 入口，后台命令不经过 GUI 错误弹窗。

    兼容仅有一个入口的旧包；frozen 均不能再传 -m genshin_app。
    """
    if getattr(sys, "frozen", False):
        console = Path(sys.executable).with_name("GenshinCalc.exe")
        executable = str(console) if console.is_file() else sys.executable
        return [executable, "--data-dir", str(data_dir), "db", "serve"]
    return [sys.executable, "-m", "genshin_app", "--data-dir", str(data_dir),
            "db", "serve"]


class ServiceManager:
    def __init__(self, data_dir: Path, check_interval: float = 2.0, restart_delay: float = 1.0):
        self.data_dir = Path(data_dir)
        self.check_interval = check_interval
        self.restart_delay = restart_delay
        self._proc: Popen | None = None
        self._owned = False
        self._stop = threading.Event()
        self._watchdog: threading.Thread | None = None
        self._lifecycle_lock = threading.RLock()

    def service_info(self) -> dict | None:
        path = self.data_dir / SERVICE_INFO_NAME
        if not path.exists():
            return None
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
            return info if isinstance(info, dict) else None
        except (OSError, ValueError):
            return None

    def _available_service(self) -> dict | None:
        existing = check_existing_service(self.data_dir)
        info = self.service_info() or {}
        if existing and info.get("base_url") == existing:
            return {"base_url": existing, "pid": info.get("pid")}
        return None

    def ensure_running(self) -> dict:
        """确保服务可用。复用已有实例或自己拉起；返回 {base_url, owned, pid}。"""
        with self._lifecycle_lock:
            if self._stop.is_set():
                raise RuntimeError("数据服务管理器已停止")
            existing = self._available_service()
            if existing:
                owned = bool(self._owned and self._proc and self._proc.poll() is None
                             and existing["pid"] == self._proc.pid)
                self._owned = owned
                self._start_watchdog()
                return {**existing, "owned": owned}
            info = self._spawn()
            self._owned = info["owned"]
            self._start_watchdog()
            return info

    def _spawn(self) -> dict:
        if self._stop.is_set():
            raise RuntimeError("数据服务启动已取消")
        # frozen 态避免子进程弹控制台黑窗（CREATE_NO_WINDOW 仅 Windows 有）
        kwargs = {}
        if getattr(sys, "frozen", False) and hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        if not getattr(sys, "frozen", False):
            # pythonw + 绝对 app_entry.py 不会把父进程 sys.path 传给子进程。
            # 显式提供源码包根目录，使 -m 启动与快捷方式工作目录无关。
            env = os.environ.copy()
            source_root = str(Path(__file__).resolve().parents[2])
            env["PYTHONPATH"] = source_root + (
                os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
            )
            kwargs["env"] = env
        proc = self._proc = Popen(
            _serve_cmd(self.data_dir),
            stdout=DEVNULL, stderr=DEVNULL, **kwargs,
        )
        deadline = time.monotonic() + SPAWN_TIMEOUT
        try:
            while time.monotonic() < deadline:
                if self._stop.is_set():
                    raise RuntimeError("数据服务启动已取消")
                returncode = proc.poll()
                existing = self._available_service()
                if existing:
                    if existing["pid"] == proc.pid and returncode is None:
                        return {**existing, "owned": True}
                    if existing["pid"] != proc.pid:
                        # 并发启动的赢家已就绪；自己的输家子进程可能尚未退出。
                        self._terminate(proc)
                        self._proc = None
                        return {**existing, "owned": False}
                if returncode is not None and not lock_is_held(
                    self.data_dir / ".service.lock"
                ):
                    raise RuntimeError(
                        f"db serve 启动失败（退出码 {returncode}），"
                        "通常因为数据库不存在或版本不兼容，请安装或迁移已发布的数据库版本"
                    )
                self._stop.wait(0.1)
            raise RuntimeError(f"db serve 启动超时（{SPAWN_TIMEOUT}s 内未发现 service.json）")
        except (RuntimeError, OSError):
            self._terminate(proc)
            self._cleanup_info(proc.pid)
            raise

    @staticmethod
    def _terminate(proc: Popen) -> None:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    def _cleanup_info(self, pid: int) -> None:
        # 和 serve 创建发现文件使用同一把锁，防止核对后出现新服务。
        try:
            with file_lock(self.data_dir / ".service.lock", timeout=0):
                if (self.service_info() or {}).get("pid") == pid:
                    (self.data_dir / SERVICE_INFO_NAME).unlink(missing_ok=True)
        except (LockBusyError, OSError):
            pass  # 活跃的新服务持锁或文件不可写时，保留发现文件交给下次探测。

    def _start_watchdog(self) -> None:
        if self._watchdog and self._watchdog.is_alive():
            return
        self._watchdog = threading.Thread(target=self._watch_loop, daemon=True)
        self._watchdog.start()

    def _watch_loop(self) -> None:
        while not self._stop.wait(self.check_interval):
            try:
                with self._lifecycle_lock:
                    if self._stop.is_set():
                        return
                    if self._owned and self._proc:
                        if self._proc.poll() is None:
                            # 自有进程仍活着时不另起子进程，避免短暂探测失败丢失所有权。
                            continue
                        self._cleanup_info(self._proc.pid)
                    elif self._available_service():
                        continue
                if self._stop.wait(self.restart_delay):
                    return
                # 等待期间其他窗口可能已恢复服务；统一经过复查和竞争安全的启动流程。
                # 复用赢家后继续看门，赢家所属的窗口仍可能先于本窗口关闭。
                self.ensure_running()
            except (RuntimeError, OSError):
                pass  # 探测或重启失败留待下一轮重试；stop 事件始终优先。

    def stop(self) -> None:
        """停止看门；仅终止自己拉起的实例。"""
        self._stop.set()
        if self._watchdog:
            self._watchdog.join(timeout=3)
        with self._lifecycle_lock:
            if self._owned and self._proc:
                self._terminate(self._proc)
                self._cleanup_info(self._proc.pid)
            self._owned = False
            self._proc = None
