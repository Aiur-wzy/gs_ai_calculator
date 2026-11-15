"""Small public client for the explicit CLI session; not an Agent runtime."""
from __future__ import annotations

import io
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path
from uuid import uuid4


class CliTransportError(RuntimeError):
    """The process did not return a usable CLI session frame."""


class CliSessionClient:
    """UTF-8 structured calls, one stream owner, no shell and no automatic retry."""

    def __init__(self, data_dir, *, command=None, log_path=None, cwd=None):
        argv = [*(command or [sys.executable, "-m", "genshin_app"]), "--data-dir", str(data_dir), "session"]
        if log_path is not None:
            argv += ["--log", str(log_path)]
        self._lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._stderr = deque(maxlen=128)
        self._frames = queue.Queue()
        self._pending = None
        self._last_response = None
        self._closed = False
        self._broken = False
        if os.name == "nt":
            from genshin_app.agent_chat.transport import _OwnedWindowsProcess
            argv[0] = shutil.which(argv[0]) or argv[0]
            self._process = _OwnedWindowsProcess(argv, Path(cwd or Path.cwd()), dict(os.environ))
        else:
            self._process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=cwd, start_new_session=True)
        self._stdout = threading.Thread(target=self._read_frames, daemon=True)
        self._errors = threading.Thread(target=self._drain_errors, daemon=True)
        self._stdout.start()
        self._errors.start()
        try:
            self.ready = self._read()
            if self.ready.get("contract") != "genshin-cli-session-ready-v1":
                raise RuntimeError("CLI会话未返回完整准入回执")
        except BaseException:
            self.cancel()
            raise

    def _drain_errors(self):
        try:
            while chunk := self._process.stderr.read(4096):
                self._stderr.append(chunk)
        except (OSError, ValueError):
            pass

    def _read_frames(self):
        stream = self._process.stdout
        reader = io.BufferedReader(stream) if isinstance(stream, io.RawIOBase) else stream
        try:
            for line in reader:
                self._frames.put(json.loads(line.decode("utf-8")))
        except (OSError, ValueError) as exc:
            self._frames.put(exc)
        finally:
            self._frames.put(None)

    def _read(self, timeout=None):
        reply = self._frames.get(timeout=timeout)
        if not isinstance(reply, dict):
            self._broken = True
            evidence = b"".join(self._stderr.copy()).decode("utf-8", errors="replace")
            message = ("CLI启动未通过，尚未提交工具请求。" if not hasattr(self, "ready") else
                       "CLI响应未能完整读取。请核对目标文件及写入回执后再决定是否重试。")
            if isinstance(reply, Exception):
                message += f" {type(reply).__name__}"
            raise CliTransportError(message + ("\n" + evidence if evidence else ""))
        return reply

    def call(self, argv, *, request_id=None, timeout=None):
        """Submit one request. A finite timeout only ends the observation."""
        with self._lock:
            if self._closed or self._broken:
                raise RuntimeError("CLI会话已结束，请检查上一次请求的回执。")
            if self._pending is not None:
                raise RuntimeError("已有请求等待收取。请先调用 receive()。")
            ident = request_id or uuid4().hex
            line = (json.dumps({"id": ident, "argv": list(argv)}, ensure_ascii=False) + "\n").encode("utf-8")
            self._pending = ident
            try:
                # The native Windows job transport exposes unbuffered pipes.
                remaining = memoryview(line)
                while remaining:
                    count = self._process.stdin.write(remaining)
                    if not count:
                        raise BrokenPipeError("CLI request pipe closed")
                    remaining = remaining[count:]
                self._process.stdin.flush()
            except OSError:
                self._broken = True
                raise
            return self._receive(timeout)

    def receive(self, *, timeout=None):
        """Observe the outstanding request without submitting it again."""
        with self._lock:
            if self._pending is None or self._broken:
                raise RuntimeError("没有可继续收取的 CLI 请求。")
            return self._receive(timeout)

    def _receive(self, timeout):
        try:
            reply = self._read(timeout)
        except queue.Empty:
            return {"id": self._pending, "transport_state": "in_flight",
                    "operation_state": "in_progress", "write_outcome": "unknown",
                    "process_state": "running" if self._process.poll() is None else "exited",
                    "automatically_retried": False}
        if reply.get("id") != self._pending or reply.get("contract") != "genshin-cli-session-result-v1":
            self._broken = True
            raise RuntimeError("CLI响应身份不符。请先核对目标文件与操作回执。")
        self._pending = None
        self._last_response = reply
        code = self._process.poll()
        reply["process_state"] = "running" if code is None else "exited"
        if code is not None:
            reply["process_exit_code"] = code
        return reply

    def batch(self, requests, *, stop_on_failure=False, accepted=None):
        """Ordered, non-atomic calls. Opt in to gating on the previous result."""
        results = []
        skipped = []
        failure = None
        for index, argv in enumerate(requests):
            ident = uuid4().hex
            if failure is not None:
                skipped.append({"index": index, "id": ident, "argv": list(argv),
                                "operation_state": "skipped", "depends_on": failure})
                continue
            try:
                results.append(self.call(argv, request_id=ident))
            except (OSError, RuntimeError, ValueError) as exc:
                # Transport failure cannot roll back earlier independent calls.
                # Keep the exact completed prefix; never replay a possible write.
                return {"atomic": False, "mode": "serial", "completed": len(results),
                        "results": results, "status": "interrupted",
                        "interrupted_request_id": ident, "write_outcome": "unknown",
                        "automatically_retried": False, "error": str(exc)}
            response = results[-1]
            if stop_on_failure and (response.get("exit_code") != 0 or
                    response.get("operation_state") in {"diagnostic", "rejected", "cancelled"} or (
                    accepted is not None and not accepted(response))):
                failure = ident
        result = {"atomic": False, "mode": "serial", "completed": len(results), "results": results}
        if stop_on_failure:
            result.update(status="rejected" if failure else "completed", skipped=skipped)
        return result

    def _terminate_tree(self):
        if os.name == "nt":
            self._process.terminate_tree()
        else:
            try:
                os.killpg(self._process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def _close_pipes(self):
        with self._cleanup_lock:
            if self._closed:
                return
            self._closed = True
            self._terminate_tree()
            for reader in (self._stdout, self._errors):
                reader.join(timeout=3)
            if os.name == "nt":
                self._process.close()
            else:
                for stream in (self._process.stdin, self._process.stdout, self._process.stderr):
                    if not stream.closed:
                        stream.close()

    def close(self):
        with self._lock:
            if self._closed:
                return self._last_response
            if not self._process.stdin.closed:
                self._process.stdin.close()
            try:
                if self._pending is not None and not self._broken:
                    self._receive(None)
                self._process.wait(timeout=None)
            finally:
                self._close_pipes()
            return self._last_response

    def cancel(self):
        # End the tree before taking the request lock so a blocked receive wakes up.
        if not self._closed:
            self._terminate_tree()
        with self._lock:
            if not self._closed:
                self._process.wait(timeout=None)
                if self._pending is not None and not self._broken:
                    try:
                        self._receive(3)
                    except RuntimeError:
                        pass
                self._close_pipes()
            if self._pending is None and self._last_response is not None:
                return self._last_response
            return {"id": self._pending, "state": "cancelled", "process_state": "exited",
                    "write_outcome": "unknown" if self._pending is not None else "not_applicable",
                    "automatically_retried": False}

    def __enter__(self):
        return self

    def __exit__(self, kind, value, trace):
        if kind is None:
            self.close()
        else:
            self.cancel()
