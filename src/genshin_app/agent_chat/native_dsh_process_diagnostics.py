"""Bounded process exit evidence; no raw stderr or model/credential payloads."""
from __future__ import annotations

import re
import threading
from collections import deque
from datetime import UTC, datetime

from .sessions import write_owned

_CODE = re.compile(rb"(?:(?:native_|dsh_|ERR_)[A-Za-z0-9_]{1,145}|ENOENT|EACCES|EPERM)\Z")


class NativeDshProcessDiagnostics:
    def __init__(self, root=None):
        self._root = root
        self._lock = threading.Lock()
        self._codes = deque(maxlen=64)
        self._exit_code = None
        self._persist_failed = False

    def record_code(self, line):
        code = line.strip()
        if len(code) > 160 or _CODE.fullmatch(code) is None:
            return
        with self._lock:
            self._codes.append({"code": code.decode("ascii"), "at": datetime.now(UTC).isoformat()})
            self._persist()

    def record_exit(self, code):
        with self._lock:
            self._exit_code = code
            self._persist()

    def _persist(self):
        if self._root is None:
            return
        try:
            write_owned(self._root, "last-process-status.json", self._snapshot(), 64 * 1024)
        except OSError:
            self._persist_failed = True

    def _snapshot(self):
        return {"contract": "genshin-native-dsh-process-diagnostics-v1", "exit_code": self._exit_code,
            "codes": list(self._codes), "persistence_failed": self._persist_failed}

    def snapshot(self):
        with self._lock:
            return self._snapshot()

    def drain(self, stream, process, *, on_exit=None):
        # Read bounded chunks rather than readline(), which can allocate an
        # arbitrarily long provider stderr line before the filter sees it.
        pending = b""
        discarding = False
        try:
            while block := stream.read(4096):
                for segment in block.splitlines(keepends=True):
                    if not discarding:
                        pending += segment
                        if len(pending) > 162:
                            pending = b""
                            discarding = True
                    if segment.endswith(b"\n"):
                        if not discarding:
                            self.record_code(pending)
                        pending, discarding = b"", False
            if pending and not discarding:
                self.record_code(pending)
        finally:
            stream.close()
            code = process.wait()
            self.record_exit(code)
            if on_exit is not None:
                try:
                    on_exit(code)
                except Exception:  # noqa: BLE001 - cleanup must not discard the recorded exit
                    self.record_code(b"dsh_connect_exit_cleanup_failed")
