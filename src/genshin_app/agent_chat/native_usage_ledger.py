"""Durable provider-usage accounting independent of turn completion.

Provider usage is an accounting fact, not a successful-result field.  The
runtime writes every reported cumulative observation here before acknowledging
the corresponding bridge frame.  A later provider/runtime failure therefore
cannot make already-reported usage disappear.  Missing usage is represented by
an explicit sealed turn with ``usage_state=unknown``; it is never rewritten as
zero.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from genshin_app.equipment.files import fingerprint, json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_db.locking import file_lock

from .sessions import safe_path, write_owned

CONTRACT = "genshin-native-provider-usage-ledger-v2"
LEDGER_FILE = "native-provider-usage-ledger-v2.json"
LOCK_FILE = ".native-provider-usage-ledger-v2.lock"
MAX_LEDGER_BYTES = 128 * 1024 * 1024
MAX_ATTEMPTS = 262_144
MAX_TURNS = 16_384
MAX_SAFE_INTEGER = 2**53 - 1

_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_USAGE_KEYS = frozenset({
    "input_tokens", "output_tokens", "reasoning_tokens",
    "cached_input_tokens", "total_tokens",
})
_TERMINAL = frozenset({"completed", "cancelled", "error", "interrupted"})
_PROVIDER_PURPOSES = frozenset({"agent", "compaction", "overflow-retry"})


def _fail(message: str, reason: str = "agent_checkpoint_invalid") -> None:
    raise PlanError(message, reason)


def _identity(value: object, name: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        _fail(f"{name}不合法")
    return value


def _counter(value: object, name: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SAFE_INTEGER:
        _fail(f"{name}不合法")
    return value


def _stamp(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) > 64:
        _fail(f"{name}不合法")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _fail(f"{name}不合法")
    if parsed.tzinfo is None:
        _fail(f"{name}缺少时区")
    return value


def _strict_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _usage(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping) or not value or set(value) - _USAGE_KEYS:
        _fail("provider usage不合法")
    result = {str(key): _counter(amount, str(key)) for key, amount in value.items()}
    return result


def _empty(conversation_id: str) -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "conversation_id": conversation_id,
        "revision": 0,
        "attempts": [],
        "turns": [],
    }


def _validate(value: object, *, conversation_id: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "contract", "conversation_id", "revision", "attempts", "turns",
    }:
        _fail("provider usage账字段不合法")
    if value.get("contract") != CONTRACT:
        _fail("provider usage账版本不受支持")
    if _identity(value.get("conversation_id"), "conversation_id") != conversation_id:
        _fail("provider usage账不属于当前会话")
    _counter(value.get("revision"), "revision")
    attempts = value.get("attempts")
    turns = value.get("turns")
    if not isinstance(attempts, list) or len(attempts) > MAX_ATTEMPTS:
        _fail("provider usage attempt数量不合法", "agent_size_limit")
    if not isinstance(turns, list) or len(turns) > MAX_TURNS:
        _fail("provider usage turn数量不合法", "agent_size_limit")
    attempt_ids = set()
    for row in attempts:
        if not isinstance(row, dict) or set(row) != {
            "attempt_id", "provider_attempt_id", "provider_purpose",
            "turn_id", "epoch_id", "step_id", "usage", "usage_hash",
            "observation_count", "first_reported_at", "last_reported_at",
        }:
            _fail("provider usage attempt字段不合法")
        attempt_id = _identity(row.get("attempt_id"), "attempt_id")
        if attempt_id in attempt_ids:
            _fail("provider usage attempt重复")
        attempt_ids.add(attempt_id)
        for name in ("turn_id", "epoch_id", "step_id"):
            _identity(row.get(name), name)
        _identity(row.get("provider_attempt_id"), "provider_attempt_id")
        if row.get("provider_purpose") not in _PROVIDER_PURPOSES:
            _fail("provider purpose不合法")
        checked = _usage(row.get("usage"))
        claimed = row.get("usage_hash")
        if not isinstance(claimed, str) or _HASH.fullmatch(claimed) is None:
            _fail("provider usage hash不合法")
        if claimed != fingerprint(checked):
            _fail("provider usage hash不匹配")
        _counter(row.get("observation_count"), "observation_count")
        if row["observation_count"] < 1:
            _fail("provider usage observation_count不合法")
        _stamp(row.get("first_reported_at"), "first_reported_at")
        _stamp(row.get("last_reported_at"), "last_reported_at")
    turn_ids = set()
    for row in turns:
        if not isinstance(row, dict) or set(row) != {
            "turn_id", "epoch_id", "status", "usage_state", "sealed_at",
        }:
            _fail("provider usage turn字段不合法")
        identity = (
            _identity(row.get("turn_id"), "turn_id"),
            _identity(row.get("epoch_id"), "epoch_id"),
        )
        if identity in turn_ids:
            _fail("provider usage turn重复")
        turn_ids.add(identity)
        if row.get("status") not in _TERMINAL:
            _fail("provider usage turn状态不合法")
        if row.get("usage_state") not in {"reported", "unknown"}:
            _fail("provider usage可知状态不合法")
        _stamp(row.get("sealed_at"), "sealed_at")
    if len(json_bytes(value)) > MAX_LEDGER_BYTES:
        _fail("provider usage账超过保存上限", "agent_size_limit")
    return value


class NativeProviderUsageLedger:
    """One conversation's durable, replay-safe provider usage journal."""

    def __init__(
        self,
        root: str | Path,
        *,
        conversation_id: str,
        clock: Callable[[], str] = _now,
    ) -> None:
        self.root = Path(root).absolute()
        self.conversation_id = _identity(conversation_id, "conversation_id")
        self.path = safe_path(self.root, LEDGER_FILE)
        self.lock_path = safe_path(self.root, LOCK_FILE)
        self._clock = clock

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty(self.conversation_id)
        if self.path.is_symlink() or self.path.stat().st_size > MAX_LEDGER_BYTES:
            _fail("provider usage账不可读取", "agent_storage")
        try:
            value = json.loads(
                self.path.read_text(encoding="utf-8"),
                object_pairs_hook=_strict_object,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
        except (OSError, UnicodeError, ValueError, RecursionError):
            _fail("provider usage账不是严格JSON")
        return _validate(value, conversation_id=self.conversation_id)

    def _write_unlocked(self, value: dict[str, Any]) -> None:
        checked = _validate(value, conversation_id=self.conversation_id)
        self.root.mkdir(parents=True, exist_ok=True)
        write_owned(self.root, LEDGER_FILE, checked, MAX_LEDGER_BYTES)

    def read(self) -> dict[str, Any]:
        with file_lock(self.lock_path):
            return copy.deepcopy(self._read_unlocked())

    def public_turn_usage(self) -> list[dict[str, Any]]:
        """Aggregate attempt facts without hiding terminal unknown usage.

        These rows are a read-only telemetry overlay, not session-store input.
        ``usage_state`` therefore deliberately records whether every sealed
        execution segment reported usage.  A provider/runtime failure that
        never delivered usage must stay visible as ``unknown`` or ``partial``;
        absence must never be presented as an exact zero-token turn.
        """
        ledger = self.read()
        grouped: dict[str, dict[str, Any]] = {}
        for attempt in ledger["attempts"]:
            row = grouped.setdefault(
                attempt["turn_id"],
                {"turn_id": attempt["turn_id"], "usage_state": "reported"},
            )
            for key, amount in attempt["usage"].items():
                row[key] = row.get(key, 0) + amount
        sealed_by_turn: dict[str, list[str]] = {}
        for sealed in ledger["turns"]:
            sealed_by_turn.setdefault(sealed["turn_id"], []).append(
                sealed["usage_state"],
            )
        for turn_id, states in sealed_by_turn.items():
            row = grouped.get(turn_id)
            if row is None:
                grouped[turn_id] = {
                    "turn_id": turn_id,
                    "usage_state": "unknown",
                    **{key: None for key in sorted(_USAGE_KEYS)},
                }
            elif "unknown" in states:
                row["usage_state"] = "partial"
        return list(grouped.values())

    def record(
        self,
        *,
        turn_id: str,
        epoch_id: str,
        step_id: str,
        provider_attempt_id: str | None = None,
        provider_purpose: str = "agent",
        usage: Mapping[str, Any],
    ) -> dict[str, Any]:
        turn = _identity(turn_id, "turn_id")
        epoch = _identity(epoch_id, "epoch_id")
        step = _identity(step_id, "step_id")
        provider_attempt = _identity(
            f"legacy:{step}"
            if provider_attempt_id is None
            else provider_attempt_id,
            "provider_attempt_id",
        )
        if provider_purpose not in _PROVIDER_PURPOSES:
            _fail("provider purpose不合法")
        checked = _usage(usage)
        attempt_id = (
            f"usage-{fingerprint([turn, epoch, provider_attempt])[:48]}"
        )
        now = self._clock()
        _stamp(now, "reported_at")
        with file_lock(self.lock_path):
            ledger = self._read_unlocked()
            existing = next(
                (row for row in ledger["attempts"] if row["attempt_id"] == attempt_id),
                None,
            )
            if existing is None:
                if len(ledger["attempts"]) >= MAX_ATTEMPTS:
                    _fail("provider usage账已满", "agent_size_limit")
                row = {
                    "attempt_id": attempt_id,
                    "provider_attempt_id": provider_attempt,
                    "provider_purpose": provider_purpose,
                    "turn_id": turn,
                    "epoch_id": epoch,
                    "step_id": step,
                    "usage": checked,
                    "usage_hash": fingerprint(checked),
                    "observation_count": 1,
                    "first_reported_at": now,
                    "last_reported_at": now,
                }
                ledger["attempts"].append(row)
            else:
                if (
                    existing["step_id"] != step
                    or existing["provider_attempt_id"] != provider_attempt
                    or existing["provider_purpose"] != provider_purpose
                ):
                    _fail(
                        "provider usage attempt绑定冲突",
                        "provider_usage_attempt_conflict",
                    )
                if any(
                    checked.get(key, 0) < amount
                    for key, amount in existing["usage"].items()
                ):
                    _fail("provider usage累计值回退", "provider_usage_regressed")
                if existing["usage"] == checked:
                    return copy.deepcopy(existing)
                existing["usage"] = checked
                existing["usage_hash"] = fingerprint(checked)
                existing["observation_count"] += 1
                existing["last_reported_at"] = now
                row = existing
            ledger["revision"] += 1
            self._write_unlocked(ledger)
            return copy.deepcopy(row)

    def seal_turn(
        self,
        *,
        turn_id: str,
        epoch_id: str,
        status: str,
    ) -> dict[str, Any]:
        turn = _identity(turn_id, "turn_id")
        epoch = _identity(epoch_id, "epoch_id")
        if status not in _TERMINAL:
            _fail("provider usage turn状态不合法")
        now = self._clock()
        _stamp(now, "sealed_at")
        with file_lock(self.lock_path):
            ledger = self._read_unlocked()
            reported = any(
                row["turn_id"] == turn and row["epoch_id"] == epoch
                for row in ledger["attempts"]
            )
            row = next(
                (item for item in ledger["turns"] if (
                    item["turn_id"], item["epoch_id"]
                ) == (turn, epoch)),
                None,
            )
            intended = "reported" if reported else "unknown"
            if row is not None:
                if row["status"] != status or row["usage_state"] != intended:
                    _fail("provider usage turn结算冲突", "provider_usage_seal_conflict")
                return copy.deepcopy(row)
            if len(ledger["turns"]) >= MAX_TURNS:
                _fail("provider usage turn账已满", "agent_size_limit")
            row = {
                "turn_id": turn,
                "epoch_id": epoch,
                "status": status,
                "usage_state": intended,
                "sealed_at": now,
            }
            ledger["turns"].append(row)
            ledger["revision"] += 1
            self._write_unlocked(ledger)
            return copy.deepcopy(row)


__all__ = [
    "CONTRACT", "LEDGER_FILE", "NativeProviderUsageLedger",
]
