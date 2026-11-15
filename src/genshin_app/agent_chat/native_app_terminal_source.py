"""Synchronous checkpoint seam for settled unified App-tool calls."""

from __future__ import annotations

import copy
import re
import threading
from collections.abc import Callable, Mapping
from typing import Any

from genshin_app.equipment.schema import PlanError

from .native_app_call_ledger import INFLIGHT, NativeAppCallLedger

_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_TERMINAL_KEYS = frozenset({
    "terminal_state",
    "call_id",
    "effect",
    "result_hash",
    "before_state_hash",
    "after_state_hash",
})


def _fail(message: str, reason: str = "native_app_terminal_seam_invalid") -> None:
    raise PlanError(message, reason)


class NativeAppTerminalSourceV2:
    """Install one turn-scoped callback and synchronously checkpoint terminals.

    The source does not know about the coordinator.  It verifies that a
    projection exactly matches the already-persisted App-call ledger, then
    invokes the callback installed by the official turn port.  Repeating the
    same terminal is intentional: the coordinator owns exact replay.
    """

    def __init__(self, ledger: NativeAppCallLedger) -> None:
        if not isinstance(ledger, NativeAppCallLedger):
            raise TypeError("ledger must be NativeAppCallLedger")
        self.ledger = ledger
        self._lock = threading.RLock()
        self._installed: tuple[object, Callable[..., Mapping[str, Any]]] | None = None

    def install_terminal_callback(
        self,
        callback: Callable[[str, Mapping[str, Any]], Mapping[str, Any]],
    ) -> Callable[[], None]:
        if not callable(callback):
            raise TypeError("callback must be callable")
        token = object()
        with self._lock:
            if self._installed is not None:
                _fail("App工具终态回调已经安装", "native_app_terminal_callback_busy")
            self._installed = (token, callback)

        def uninstall() -> None:
            with self._lock:
                if self._installed is not None and self._installed[0] is token:
                    self._installed = None

        return uninstall

    def checkpoint(
        self,
        call_id: str,
        terminal_projection: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(call_id, str) or _IDENTITY.fullmatch(call_id) is None:
            _fail("App工具终态call_id不合法", "unknown_settlement")
        if not isinstance(terminal_projection, Mapping):
            _fail("App工具终态投影不合法", "unknown_settlement")
        try:
            supplied = {
                name: copy.deepcopy(terminal_projection.get(name))
                for name in _TERMINAL_KEYS
            }
        except (TypeError, ValueError, RecursionError):
            _fail("App工具终态投影不可复制", "unknown_settlement")
        if supplied["call_id"] != call_id:
            _fail("App工具终态call_id不匹配", "settlement_conflict")

        with self._lock:
            if self._installed is None:
                _fail("当前轮没有安装App工具终态回调", "native_app_terminal_callback_missing")
            ledger = self.ledger.read()
            entry = ledger["entries"].get(call_id)
            if entry is None or entry["state"] == INFLIGHT:
                _fail("App工具调用尚未形成持久终态", "unknown_settlement")
            expected = {
                "terminal_state": entry["state"],
                "call_id": entry["identity"]["call_id"],
                "effect": entry["effect"],
                "result_hash": entry["result_hash"],
                "before_state_hash": entry["before_state_hash"],
                "after_state_hash": entry["after_state_hash"],
            }
            if supplied != expected:
                _fail("App工具终态与持久调用账不匹配", "settlement_conflict")
            callback = self._installed[1]
            response = callback(call_id, copy.deepcopy(expected))
            if not isinstance(response, Mapping):
                _fail("App工具终态回调没有返回JSON对象")
            return copy.deepcopy(dict(response))


__all__ = ["NativeAppTerminalSourceV2"]
