"""App-owned controller for V2 file and navigation operation cards.

The model may only enqueue a path-free card through the fixed tool host.  A
trusted UI must present that card and explicitly invoke the corresponding App
callback.  Agent turns and file UI callbacks share one fail-closed serial
gate, while a durable execution claim prevents a second process or a restart
from replaying a callback whose outcome is unknown.

The action ledger and its checkpoint companion intentionally remain separate:
the ledger is the user-visible lifecycle authority, and the companion anchors
the latest complete ledger revision/digest plus a bounded history of session
references.  Callers persist ``checkpoint_projection()["session_ref"]`` in the
V2 conversation checkpoint.  Supplying that value as
``expected_session_ref`` on reopen detects rollback of both otherwise-valid
JSON files.
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
import weakref
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from itertools import pairwise
from pathlib import Path
from typing import Any

from genshin_app.equipment.files import fingerprint, json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_db.locking import LockBusyError, file_lock

from .native_app_action_ledger import (
    ACTION_TYPES,
    NativeAppActionError,
    NativeAppActionLedger,
    normalize_action_binding,
    normalize_action_workspace,
)
from .native_app_action_tools import NativeAppActionToolHost
from .sessions import read_owned, safe_path, write_owned

CHECKPOINT_CONTRACT = "genshin-native-app-action-checkpoint-v2"
CHECKPOINT_FILE = "native-app-action-checkpoint-v2.json"
CHECKPOINT_LOCK_FILE = ".native-app-action-checkpoint-v2.lock"
OPERATION_LOCK_FILE = ".native-app-action-operation-v1.lock"
EXECUTION_MARKER_CONTRACT = "genshin-native-app-action-execution-marker-v2"
EXECUTION_MARKER_PREFIX = "native-app-action-execution-v2-"
EXECUTION_MARKER_SUFFIX = ".json"
MAX_EXECUTION_MARKER_BYTES = 2048
MAX_CHECKPOINT_BYTES = 512 * 1024
MAX_CHECKPOINT_HISTORY = 64

_CARD_STATES = (
    "queued",
    "presented",
    "completed",
    "cancelled",
    "failed",
    "expired",
)
_TERMINAL_STATES = frozenset({"completed", "cancelled", "failed", "expired"})
_ACTION_CAPABILITIES = {
    "save_current": "can_save",
    "save_as": "can_save",
    "open_document": "can_open",
    "export_result": "can_export",
}
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ACTION_ID = re.compile(r"action_[A-Za-z0-9_-]{32}\Z")
_UI_RECEIPT = re.compile(r"ui_[A-Za-z0-9_-]{43}\Z")
_SESSION_REF = re.compile(r"appaction(?:2)?\.(0|[1-9][0-9]{0,15})\.([0-9a-f]{64})\Z")
_EXPLICIT_UNKNOWN_RECOVERY_MESSAGE = (
    "App操作的副作用可能已经发生，但界面结果已丢失；"
    "系统已将该操作标记为失败且未自动重放。"
)


class NativeAppActionControllerError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise NativeAppActionControllerError(code)


def _counter(value: object, field: str) -> int:
    if type(value) is not int or not 0 <= value <= 2**53 - 1:
        _fail(f"app_action_checkpoint_{field}_invalid")
    return value


def _identity(value: object, field: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        _fail(f"app_action_checkpoint_{field}_invalid")
    return value


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        _fail(f"app_action_checkpoint_{field}_invalid")
    return value


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _ledger_summary(ledger: Mapping[str, Any]) -> dict[str, Any]:
    cards = ledger["cards"]
    counts = {state: 0 for state in _CARD_STATES}
    for card in cards:
        status = card["status"]
        if status not in counts:  # ledger.read() should make this unreachable
            _fail("app_action_checkpoint_ledger_invalid")
        counts[status] += 1
    latest = cards[-1] if cards else None
    return {
        "total_cards": len(cards),
        "counts": counts,
        "latest_action_id": None if latest is None else latest["action_id"],
        "latest_status": None if latest is None else latest["status"],
    }


def _session_ref_from_hashes(
    *,
    ledger_revision: int,
    ledger_hash: str,
    summary_hash: str,
    prior_session_ref: str | None,
    legacy: bool = False,
) -> str:
    binding_hash = fingerprint(
        {
            "ledger_revision": ledger_revision,
            "ledger_hash": ledger_hash,
            "summary_hash": summary_hash,
            "prior_session_ref": prior_session_ref,
        }
    )
    value = f"{'appaction' if legacy else 'appaction2'}.{ledger_revision}.{binding_hash}"
    return _identity(value, "session_ref")


def _derived_entry(ledger: Mapping[str, Any]) -> dict[str, Any]:
    revision = _counter(ledger.get("revision"), "ledger_revision")
    summary = _ledger_summary(ledger)
    ledger_hash = fingerprint(ledger)
    return {
        "ledger_revision": revision,
        "ledger_hash": ledger_hash,
        "summary": summary,
    }


def _validate_summary(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "total_cards",
        "counts",
        "latest_action_id",
        "latest_status",
    }:
        _fail("app_action_checkpoint_summary_invalid")
    counts = value.get("counts")
    if not isinstance(counts, Mapping) or set(counts) != set(_CARD_STATES):
        _fail("app_action_checkpoint_summary_invalid")
    checked_counts = {
        state: _counter(counts.get(state), f"count_{state}")
        for state in _CARD_STATES
    }
    total = _counter(value.get("total_cards"), "total_cards")
    if total != sum(checked_counts.values()):
        _fail("app_action_checkpoint_summary_invalid")
    action_id = value.get("latest_action_id")
    status = value.get("latest_status")
    if total == 0:
        if action_id is not None or status is not None:
            _fail("app_action_checkpoint_summary_invalid")
    elif (
        not isinstance(action_id, str)
        or _ACTION_ID.fullmatch(action_id) is None
        or status not in _CARD_STATES
    ):
        _fail("app_action_checkpoint_summary_invalid")
    return {
        "total_cards": total,
        "counts": checked_counts,
        "latest_action_id": action_id,
        "latest_status": status,
    }


def _validate_history_entry(value: object, *, legacy=False) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "ledger_revision",
        "ledger_hash",
        "summary_hash",
        "prior_session_ref",
        "session_ref",
    }:
        _fail("app_action_checkpoint_history_invalid")
    checked = {
        "ledger_revision": _counter(
            value.get("ledger_revision"), "ledger_revision"
        ),
        "ledger_hash": _digest(value.get("ledger_hash"), "ledger_hash"),
        "summary_hash": _digest(value.get("summary_hash"), "summary_hash"),
        "prior_session_ref": (
            None
            if value.get("prior_session_ref") is None
            else _identity(
                value.get("prior_session_ref"), "prior_session_ref"
            )
        ),
        "session_ref": _identity(value.get("session_ref"), "session_ref"),
    }
    if checked["session_ref"] != _session_ref_from_hashes(
        ledger_revision=checked["ledger_revision"],
        ledger_hash=checked["ledger_hash"],
        summary_hash=checked["summary_hash"],
        prior_session_ref=checked["prior_session_ref"],
        legacy=legacy,
    ):
        _fail("app_action_checkpoint_history_invalid")
    session_match = _SESSION_REF.fullmatch(checked["session_ref"])
    if (
        session_match is None
        or int(session_match.group(1)) != checked["ledger_revision"]
    ):
        _fail("app_action_checkpoint_history_invalid")
    prior = checked["prior_session_ref"]
    if prior is not None:
        prior_match = _SESSION_REF.fullmatch(prior)
        if (
            prior_match is None
            or int(prior_match.group(1)) >= checked["ledger_revision"]
        ):
            _fail("app_action_checkpoint_history_invalid")
    return checked


def _validate_inflight(value: object, *, legacy=False) -> dict[str, Any] | None:
    if value is None:
        return None
    required = {
        "action_id",
        "ui_receipt_hash",
        "claimed_ledger_revision",
    }
    if not legacy:
        required.add("target_fingerprint")
    if not isinstance(value, Mapping):
        _fail("app_action_checkpoint_inflight_invalid")
    fields = set(value)
    if fields not in (required, required | {"dirty_open_guard_required"}):
        _fail("app_action_checkpoint_inflight_invalid")
    action_id = value.get("action_id")
    if not isinstance(action_id, str) or _ACTION_ID.fullmatch(action_id) is None:
        _fail("app_action_checkpoint_inflight_invalid")
    guard = value.get("dirty_open_guard_required", False)
    if type(guard) is not bool:
        _fail("app_action_checkpoint_inflight_invalid")
    checked = {
        "action_id": action_id,
        "ui_receipt_hash": _digest(
            value.get("ui_receipt_hash"), "ui_receipt_hash"
        ),
        "claimed_ledger_revision": _counter(
            value.get("claimed_ledger_revision"), "claimed_ledger_revision"
        ),
        "dirty_open_guard_required": guard,
    }
    if not legacy:
        checked["target_fingerprint"] = _digest(value["target_fingerprint"], "target_fingerprint")
    return checked


def _validate_checkpoint(
    value: object, *, conversation_id: str, legacy=False
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "contract",
        "conversation_id",
        "sequence",
        "ledger_revision",
        "ledger_hash",
        "summary",
        "session_ref",
        "history",
        "inflight",
    }:
        _fail("app_action_checkpoint_invalid")
    contract = CHECKPOINT_CONTRACT.removesuffix("2") + "1" if legacy else CHECKPOINT_CONTRACT
    if value.get("contract") != contract:
        _fail("app_action_checkpoint_invalid")
    if value.get("conversation_id") != conversation_id:
        _fail("app_action_checkpoint_identity_mismatch")
    revision = _counter(value.get("ledger_revision"), "ledger_revision")
    ledger_hash = _digest(value.get("ledger_hash"), "ledger_hash")
    summary = _validate_summary(value.get("summary"))
    session_ref = _identity(value.get("session_ref"), "session_ref")
    history_value = value.get("history")
    if (
        not isinstance(history_value, list)
        or not history_value
        or len(history_value) > MAX_CHECKPOINT_HISTORY
    ):
        _fail("app_action_checkpoint_history_invalid")
    history = [_validate_history_entry(item, legacy=legacy) for item in history_value]
    if any(
        left["ledger_revision"] >= right["ledger_revision"]
        for left, right in pairwise(history)
    ):
        _fail("app_action_checkpoint_history_invalid")
    if any(
        right["prior_session_ref"] != left["session_ref"]
        for left, right in pairwise(history)
    ):
        _fail("app_action_checkpoint_history_invalid")
    latest = history[-1]
    if latest != {
        "ledger_revision": revision,
        "ledger_hash": ledger_hash,
        "summary_hash": fingerprint(summary),
        "prior_session_ref": latest["prior_session_ref"],
        "session_ref": session_ref,
    }:
        _fail("app_action_checkpoint_history_invalid")
    if len({item["session_ref"] for item in history}) != len(history):
        _fail("app_action_checkpoint_history_invalid")
    inflight = _validate_inflight(value.get("inflight"), legacy=legacy)
    if inflight is not None and inflight["claimed_ledger_revision"] > revision:
        _fail("app_action_checkpoint_inflight_invalid")
    checked = {
        "contract": contract,
        "conversation_id": conversation_id,
        "sequence": _counter(value.get("sequence"), "sequence"),
        "ledger_revision": revision,
        "ledger_hash": ledger_hash,
        "summary": summary,
        "session_ref": session_ref,
        "history": history,
        "inflight": inflight,
    }
    if len(json_bytes(checked)) > MAX_CHECKPOINT_BYTES:
        _fail("app_action_checkpoint_too_large")
    return checked


class NativeAppActionCheckpoint:
    """Durable monotonic anchor and cross-process execution claim."""

    def __init__(
        self,
        root: str | Path,
        *,
        conversation_id: str,
        expected_session_ref: str | None = None,
        legacy_recovery: bool = False,
    ) -> None:
        self.root = Path(root).absolute()
        self.legacy_recovery = legacy_recovery
        self.contract = CHECKPOINT_CONTRACT.removesuffix("2") + "1" if legacy_recovery else CHECKPOINT_CONTRACT
        self.file_name = CHECKPOINT_FILE.replace("-v2.", "-v1.") if legacy_recovery else CHECKPOINT_FILE
        self.lock_name = CHECKPOINT_LOCK_FILE.replace("-v2.", "-v1.") if legacy_recovery else CHECKPOINT_LOCK_FILE
        self.marker_contract = EXECUTION_MARKER_CONTRACT.removesuffix("2") + "1" if legacy_recovery else EXECUTION_MARKER_CONTRACT
        self._targets = {}
        self.conversation_id = _identity(conversation_id, "conversation_id")
        self.expected_session_ref = (
            None
            if expected_session_ref is None
            else _identity(expected_session_ref, "expected_session_ref")
        )
        try:
            self.path = safe_path(self.root, self.file_name)
            self.lock_path = safe_path(self.root, self.lock_name)
            self.operation_lock_path = safe_path(
                self.root, OPERATION_LOCK_FILE
            )
        except (OSError, PlanError):
            _fail("app_action_checkpoint_storage_invalid")
        self._expected_anchor_verified = expected_session_ref is None

    def _checked_path(self, name: str) -> Path:
        try:
            return safe_path(self.root, name)
        except (OSError, PlanError):
            _fail("app_action_checkpoint_storage_invalid")

    def _execution_marker_name(self, action_id: str) -> str:
        if (
            not isinstance(action_id, str)
            or _ACTION_ID.fullmatch(action_id) is None
        ):
            _fail("app_action_execution_identity_invalid")
        prefix = "native-app-action-execution-" if self.legacy_recovery else EXECUTION_MARKER_PREFIX
        return f"{prefix}{action_id}{EXECUTION_MARKER_SUFFIX}"

    def _target_fingerprint(self, action_id):
        if action_id not in self._targets:
            _fail("app_action_execution_target_missing")
        return self._targets[action_id]

    def _read_execution_marker(
        self, *, action_id: str
    ) -> dict[str, Any] | None:
        name = self._execution_marker_name(action_id)
        path = self._checked_path(name)
        if not path.exists():
            return None
        try:
            if (
                path.is_symlink()
                or path.stat().st_size > MAX_EXECUTION_MARKER_BYTES
            ):
                _fail("app_action_execution_marker_invalid")
            value = json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=_strict_object,
                parse_constant=lambda _value: (_ for _ in ()).throw(
                    ValueError()
                ),
            )
        except NativeAppActionControllerError:
            raise
        except (OSError, UnicodeError, ValueError, RecursionError):
            _fail("app_action_execution_marker_invalid")
        fields = {
            "contract",
            "conversation_id",
            "action_id",
            "ui_receipt_hash",
        }
        if not self.legacy_recovery:
            fields.add("target_fingerprint")
        if not isinstance(value, Mapping) or set(value) != fields:
            _fail("app_action_execution_marker_invalid")
        if (
            value.get("contract") != self.marker_contract
            or value.get("conversation_id") != self.conversation_id
            or value.get("action_id") != action_id
        ):
            _fail("app_action_execution_marker_invalid")
        result = {
            "contract": self.marker_contract,
            "conversation_id": self.conversation_id,
            "action_id": action_id,
            "ui_receipt_hash": _digest(
                value.get("ui_receipt_hash"), "ui_receipt_hash"
            ),
        }
        if not self.legacy_recovery:
            if value["target_fingerprint"] != self._target_fingerprint(action_id):
                _fail("app_action_execution_target_mismatch")
            result["target_fingerprint"] = value["target_fingerprint"]
        return result

    def persist_execution_marker(
        self, *, action_id: str, ui_receipt: str
    ) -> bool:
        """Create a never-removed write-ahead marker before any callback.

        Returns ``True`` only for the process that created the marker.  An
        existing exact marker means execution may already have happened and
        therefore must never be replayed.
        """

        if (
            not isinstance(ui_receipt, str)
            or _UI_RECEIPT.fullmatch(ui_receipt) is None
        ):
            _fail("app_action_execution_receipt_invalid")
        name = self._execution_marker_name(action_id)
        path = self._checked_path(name)
        expected = {
            "contract": self.marker_contract,
            "conversation_id": self.conversation_id,
            "action_id": action_id,
            "ui_receipt_hash": fingerprint(ui_receipt),
        }
        if not self.legacy_recovery:
            expected["target_fingerprint"] = self._target_fingerprint(action_id)
        existing = self._read_execution_marker(action_id=action_id)
        if existing is not None:
            if existing != expected:
                _fail("app_action_execution_marker_conflict")
            return False
        raw = json_bytes(expected)
        if len(raw) > MAX_EXECUTION_MARKER_BYTES:  # pragma: no cover
            _fail("app_action_execution_marker_invalid")
        self.root.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            existing = self._read_execution_marker(action_id=action_id)
            if existing != expected:
                _fail("app_action_execution_marker_conflict")
            return False
        except OSError:
            _fail("app_action_execution_marker_invalid")
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            _fail("app_action_execution_marker_invalid")
        if self._checked_path(name) != path:
            _fail("app_action_execution_marker_invalid")
        return True

    def has_execution_marker(
        self, *, action_id: str, ui_receipt: str
    ) -> bool:
        if (
            not isinstance(ui_receipt, str)
            or _UI_RECEIPT.fullmatch(ui_receipt) is None
        ):
            _fail("app_action_execution_receipt_invalid")
        marker = self._read_execution_marker(action_id=action_id)
        if marker is None:
            return False
        if marker["ui_receipt_hash"] != fingerprint(ui_receipt):
            _fail("app_action_execution_marker_conflict")
        return True

    @contextmanager
    def _locked(self) -> Iterator[None]:
        lock_path = self._checked_path(self.lock_name)
        self._checked_path(self.file_name)
        with file_lock(lock_path):
            if self._checked_path(self.lock_name) != lock_path:
                _fail("app_action_checkpoint_storage_invalid")
            if self._checked_path(self.file_name) != self.path:
                _fail("app_action_checkpoint_storage_invalid")
            yield

    @contextmanager
    def exclusive_operation(self) -> Iterator[None]:
        """Fail-fast cross-process admission for Agent turns and App UI."""

        lock_path = self._checked_path(OPERATION_LOCK_FILE)
        try:
            with file_lock(lock_path, timeout=0):
                if self._checked_path(OPERATION_LOCK_FILE) != lock_path:
                    _fail("app_action_checkpoint_storage_invalid")
                yield
        except LockBusyError:
            _fail("app_action_serial_gate_busy")

    def read_cards_snapshot(self, ledger_reader):
        """Read a coherent UI cut without taking the operation/write gate.

        A ledger may be ahead of its companion during an active transaction.
        The UI may show that cut conservatively; it must not sync/advance the
        companion or prevent a real Agent operation from entering.
        """
        with self._locked():
            checkpoint = self._read_unlocked()
            ledger = ledger_reader()
            if checkpoint is None:
                if ledger["revision"] != 0:
                    _fail("app_action_checkpoint_missing")
                return ledger, {"inflight": None}
            if checkpoint["ledger_revision"] > ledger["revision"]:
                _fail("app_action_checkpoint_rollback")
            if (checkpoint["ledger_revision"] == ledger["revision"]
                    and checkpoint["ledger_hash"] != fingerprint(ledger)):
                _fail("app_action_checkpoint_ledger_invalid")
            return ledger, checkpoint

    def _read_unlocked(self) -> dict[str, Any] | None:
        if self._checked_path(self.file_name) != self.path:
            _fail("app_action_checkpoint_storage_invalid")
        if not self.path.exists():
            return None
        try:
            if (
                self.path.is_symlink()
                or self.path.stat().st_size > MAX_CHECKPOINT_BYTES
            ):
                _fail("app_action_checkpoint_storage_invalid")
            value = json.loads(
                self.path.read_text(encoding="utf-8"),
                object_pairs_hook=_strict_object,
                parse_constant=lambda _value: (_ for _ in ()).throw(
                    ValueError()
                ),
            )
        except NativeAppActionControllerError:
            raise
        except (OSError, UnicodeError, ValueError, RecursionError):
            _fail("app_action_checkpoint_storage_invalid")
        return _validate_checkpoint(value, conversation_id=self.conversation_id, legacy=self.legacy_recovery)

    def _write_unlocked(self, value: Mapping[str, Any]) -> dict[str, Any]:
        checked = _validate_checkpoint(
            value, conversation_id=self.conversation_id, legacy=self.legacy_recovery
        )
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            write_owned(
                self.root,
                self.file_name,
                checked,
                MAX_CHECKPOINT_BYTES,
            )
        except PlanError:
            _fail("app_action_checkpoint_storage_invalid")
        return checked

    def _history_entry(
        self,
        derived: Mapping[str, Any], *, prior_session_ref: str | None
    ) -> dict[str, Any]:
        entry = {
            "ledger_revision": derived["ledger_revision"],
            "ledger_hash": derived["ledger_hash"],
            "summary_hash": fingerprint(derived["summary"]),
            "prior_session_ref": prior_session_ref,
        }
        entry["session_ref"] = _session_ref_from_hashes(
            ledger_revision=entry["ledger_revision"],
            ledger_hash=entry["ledger_hash"],
            summary_hash=entry["summary_hash"],
            prior_session_ref=prior_session_ref,
            legacy=self.legacy_recovery,
        )
        return entry

    def _sync_unlocked(
        self,
        ledger: Mapping[str, Any],
        *,
        current: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        derived = _derived_entry(ledger)
        if not self.legacy_recovery:
            self._targets = {card["action_id"]: fingerprint(card["target"]) for card in ledger["cards"]}
        stored = self._read_unlocked() if current is None else copy.deepcopy(current)
        if stored is None:
            if self.expected_session_ref is not None:
                _fail("app_action_checkpoint_session_anchor_missing")
            history_entry = self._history_entry(
                derived, prior_session_ref=None
            )
            return self._write_unlocked(
                {
                    "contract": self.contract,
                    "conversation_id": self.conversation_id,
                    "sequence": 0,
                    **derived,
                    "session_ref": history_entry["session_ref"],
                    "history": [history_entry],
                    "inflight": None,
                }
            )
        old_revision = stored["ledger_revision"]
        new_revision = derived["ledger_revision"]
        if new_revision < old_revision:
            _fail("app_action_history_rollback")
        if (
            new_revision == old_revision
            and derived["ledger_hash"] != stored["ledger_hash"]
        ):
            _fail("app_action_history_diverged")
        known_refs = {item["session_ref"] for item in stored["history"]}
        if (
            not self._expected_anchor_verified
            and self.expected_session_ref is not None
            and self.expected_session_ref not in known_refs
        ):
            _fail("app_action_session_checkpoint_mismatch")
        self._expected_anchor_verified = True
        if new_revision == old_revision:
            return copy.deepcopy(stored)
        history_entry = self._history_entry(
            derived, prior_session_ref=stored["session_ref"]
        )
        history = [*stored["history"], history_entry]
        history = history[-MAX_CHECKPOINT_HISTORY:]
        return self._write_unlocked(
            {
                "contract": self.contract,
                "conversation_id": self.conversation_id,
                "sequence": stored["sequence"] + 1,
                **derived,
                "session_ref": history_entry["session_ref"],
                "history": history,
                "inflight": stored["inflight"],
            }
        )

    def sync(self, ledger: Mapping[str, Any]) -> dict[str, Any]:
        with self._locked():
            return copy.deepcopy(self._sync_unlocked(ledger))

    def read(self) -> dict[str, Any] | None:
        with self._locked():
            value = self._read_unlocked()
            return None if value is None else copy.deepcopy(value)

    def claim_execution(
        self,
        *,
        ledger: Mapping[str, Any],
        action_id: str,
        ui_receipt: str,
        dirty_open_guard_required: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(action_id, str) or _ACTION_ID.fullmatch(action_id) is None:
            _fail("app_action_execution_identity_invalid")
        if (
            not isinstance(ui_receipt, str)
            or _UI_RECEIPT.fullmatch(ui_receipt) is None
        ):
            _fail("app_action_execution_receipt_invalid")
        if type(dirty_open_guard_required) is not bool:
            _fail("app_action_execution_dirty_guard_invalid")
        with self._locked():
            current = self._sync_unlocked(ledger)
            if current["inflight"] is not None:
                _fail("app_action_execution_already_claimed")
            current["sequence"] += 1
            current["inflight"] = {
                "action_id": action_id,
                "ui_receipt_hash": fingerprint(ui_receipt),
                "claimed_ledger_revision": current["ledger_revision"],
                "dirty_open_guard_required": dirty_open_guard_required,
            }
            if not self.legacy_recovery:
                current["inflight"]["target_fingerprint"] = self._target_fingerprint(action_id)
            return copy.deepcopy(self._write_unlocked(current))

    def finish_execution(
        self,
        *,
        ledger: Mapping[str, Any],
        action_id: str,
        ui_receipt: str,
    ) -> dict[str, Any]:
        with self._locked():
            current = self._sync_unlocked(ledger)
            inflight = current["inflight"]
            if inflight is None:
                _fail("app_action_execution_claim_missing")
            if (
                inflight["action_id"] != action_id
                or inflight["ui_receipt_hash"] != fingerprint(ui_receipt)
            ):
                _fail("app_action_execution_claim_mismatch")
            if not self.legacy_recovery and inflight["target_fingerprint"] != self._target_fingerprint(action_id):
                _fail("app_action_execution_target_mismatch")
            current["sequence"] += 1
            current["inflight"] = None
            return copy.deepcopy(self._write_unlocked(current))

    def projection(self, ledger: Mapping[str, Any]) -> dict[str, Any]:
        checkpoint = self.sync(ledger)
        return {
            "contract": self.contract,
            "conversation_id": checkpoint["conversation_id"],
            "ledger_revision": checkpoint["ledger_revision"],
            "ledger_hash": checkpoint["ledger_hash"],
            "summary": copy.deepcopy(checkpoint["summary"]),
            "session_ref": checkpoint["session_ref"],
            "execution_outcome_unknown": checkpoint["inflight"] is not None,
        }


class _SerialGate:
    """One in-process admission gate shared by Agent turns and file UI."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._mode: str | None = None
        self._owner: int | None = None
        self._generation = 0
        self._listeners: set[Callable[[], None]] = set()

    def snapshot(self):
        with self._lock:
            return {"busy": self._mode is not None, "generation": self._generation}

    def subscribe(self, callback):
        with self._lock:
            self._listeners.add(callback)

        def dispose():
            with self._lock:
                self._listeners.discard(callback)
        return dispose

    @staticmethod
    def _notify(listeners):
        for callback in listeners:
            try:
                callback()
            except Exception:  # noqa: BLE001, S110 - notification cannot roll back a settled operation
                pass

    @contextmanager
    def enter(self, mode: str) -> Iterator[None]:
        with self._lock:
            if self._mode is not None:
                _fail("app_action_serial_gate_busy")
            self._mode = mode
            self._owner = threading.get_ident()
            self._generation += 1
        try:
            yield
        finally:
            with self._lock:
                if (
                    self._mode != mode
                    or self._owner != threading.get_ident()
                ):  # pragma: no cover - defensive guard
                    _fail("app_action_serial_gate_corrupt")
                self._mode = None
                self._owner = None
                self._generation += 1
                listeners = tuple(self._listeners)
            # Never call the admission queue while holding the gate lock: its
            # subscribe-then-recheck path takes a snapshot under that lock.
            self._notify(listeners)

    def active(self, mode: str) -> bool:
        with self._lock:
            return self._mode == mode

    def owned(self, mode: str) -> bool:
        with self._lock:
            return (
                self._mode == mode
                and self._owner == threading.get_ident()
            )


_SHARED_GATES_LOCK = threading.Lock()
_SHARED_GATES: weakref.WeakValueDictionary[str, _SerialGate] = (
    weakref.WeakValueDictionary()
)


def _reset_shared_gates_after_fork() -> None:
    global _SHARED_GATES_LOCK, _SHARED_GATES
    _SHARED_GATES_LOCK = threading.Lock()
    _SHARED_GATES = weakref.WeakValueDictionary()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_shared_gates_after_fork)


def _shared_serial_gate(root: Path, conversation_id: str) -> _SerialGate:
    key = f"{os.path.normcase(str(root.resolve()))}\0{conversation_id}"
    with _SHARED_GATES_LOCK:
        gate = _SHARED_GATES.get(key)
        if gate is None:
            gate = _SerialGate()
            _SHARED_GATES[key] = gate
        return gate


def _callback_outcome(value: object) -> dict[str, Any]:
    required = {
        "status",
        "message",
        "dirty_open_guard_passed",
    }
    if not isinstance(value, Mapping) or set(value) not in (
        required,
        required | {"read_only_open"},
    ):
        _fail("app_action_callback_result_invalid")
    status = value.get("status")
    message = value.get("message")
    guard = value.get("dirty_open_guard_passed")
    read_only = value.get("read_only_open", False)
    if status not in {"completed", "cancelled", "failed"}:
        _fail("app_action_callback_result_invalid")
    if not isinstance(message, str) or len(message) > 1000:
        _fail("app_action_callback_result_invalid")
    try:
        encoded_message = message.encode("utf-8")
    except UnicodeEncodeError:
        _fail("app_action_callback_result_invalid")
    if len(encoded_message) > 4000:
        _fail("app_action_callback_result_invalid")
    if type(guard) is not bool or type(read_only) is not bool:
        _fail("app_action_callback_result_invalid")
    return {
        "status": status,
        "message": message,
        "dirty_open_guard_passed": guard,
        "read_only_open": read_only,
    }


def _ui_card(card: Mapping[str, Any]) -> dict[str, Any]:
    """Trusted UI projection with immutable, path-free origin evidence."""

    projected = {
        field: copy.deepcopy(card[field])
        for field in (
            "action_id",
            "action",
            "reason",
            "suggested_name",
            "status",
            "created_at",
            "presented_at",
            "settled_at",
            "result_message",
            "ui_receipt",
            "dirty_open_guard_required",
        )
    }
    binding = card["binding"]
    projected["origin_binding"] = {
        "document_id": binding["document_id"],
        "candidate_revision": binding["candidate_revision"],
        "stage_revision": binding["stage_revision"],
        "receipt_fingerprint": binding["receipt_fingerprint"],
    }
    projected["origin_workspace"] = copy.deepcopy(card["workspace"])
    if "target" in card:
        projected["target"] = copy.deepcopy(card["target"])
    return projected


class NativeAppActionController:
    """Coordinate model requests, UI execution, lifecycle events and restart."""

    def __init__(
        self,
        *,
        ledger: NativeAppActionLedger,
        context_reader: Callable[[], Mapping[str, Any]],
        callbacks: Mapping[str, Callable[[Mapping[str, Any]], Mapping[str, Any]]],
        event_callback: Callable[[Mapping[str, Any]], None] | None = None,
        expected_session_ref: str | None = None,
        candidate_draft_store=None,
        turn_active_reader=None,
        diagnostic_data_dir=None,
    ) -> None:
        if not isinstance(ledger, NativeAppActionLedger):
            raise TypeError("ledger must be NativeAppActionLedger")
        if not callable(context_reader):
            raise TypeError("context_reader must be callable")
        if not isinstance(callbacks, Mapping):
            raise TypeError("callbacks must be a mapping")
        checked_callbacks: dict[str, Callable[[Mapping[str, Any]], Mapping[str, Any]]] = {}
        for action, callback in callbacks.items():
            if action not in ACTION_TYPES or not callable(callback):
                raise TypeError("callbacks contain an invalid action or callback")
            checked_callbacks[action] = callback
        if event_callback is not None and not callable(event_callback):
            raise TypeError("event_callback must be callable")
        self.ledger = ledger
        self.candidate_draft_store = candidate_draft_store
        self._turn_active_reader = turn_active_reader
        self._diagnostic_data_dir = Path(diagnostic_data_dir) if diagnostic_data_dir is not None else ledger.root
        self._legacy_cards = []
        self._context_reader = context_reader
        self._callbacks = checked_callbacks
        self._event_callback = event_callback
        self._gate = _shared_serial_gate(ledger.root, ledger.conversation_id)
        if not ledger.legacy_recovery:
            from .native_app_action_ledger import LEGACY_LEDGER_FILE

            if safe_path(ledger.root, LEGACY_LEDGER_FILE).exists():
                legacy = NativeAppActionLedger(ledger.root, conversation_id=ledger.conversation_id, legacy_recovery=True)
                if not safe_path(ledger.root, "native-app-action-checkpoint-v1.json").exists():
                    _fail("app_action_legacy_checkpoint_missing")
                previous = NativeAppActionController(ledger=legacy, context_reader=context_reader,
                    callbacks={}, expected_session_ref=expected_session_ref if expected_session_ref and expected_session_ref.startswith("appaction.") else None)
                previous.recover_interrupted_execution()
                self._legacy_cards = [{**_ui_card(card), "legacy_read_only": True,
                    "legacy_status": card["status"],
                    "status": "expired" if card["status"] in {"queued", "presented"} else card["status"]}
                    for card in legacy.read()["cards"]]
                if expected_session_ref and expected_session_ref.startswith("appaction."):
                    expected_session_ref = None
        self.checkpoint = NativeAppActionCheckpoint(
            ledger.root,
            conversation_id=ledger.conversation_id,
            expected_session_ref=expected_session_ref,
            legacy_recovery=ledger.legacy_recovery,
        )
        self.checkpoint.sync(self.ledger.read())

    def _read_context(self) -> tuple[dict[str, Any], dict[str, Any]]:
        def read_once() -> tuple[dict[str, Any], dict[str, Any]]:
            try:
                value = copy.deepcopy(self._context_reader())
            except (TypeError, ValueError, RecursionError):
                _fail("app_action_context_invalid")
            if not isinstance(value, Mapping) or set(value) != {
                "binding",
                "workspace",
            }:
                _fail("app_action_context_invalid")
            return (
                normalize_action_binding(value["binding"]),
                normalize_action_workspace(value["workspace"]),
            )

        first = read_once()
        second = read_once()
        if first != second:
            _fail("app_action_context_changed")
        return second

    def _emit(
        self,
        event: str,
        *,
        ledger: Mapping[str, Any],
        card: Mapping[str, Any] | None = None,
        action_ids: list[str] | None = None,
    ) -> None:
        if self._event_callback is None:
            return
        projection = self.checkpoint.projection(ledger)
        payload: dict[str, Any] = {
            "event": event,
            "ledger_revision": ledger["revision"],
            "session_ref": projection["session_ref"],
        }
        if card is not None:
            payload["card"] = _ui_card(card)
        if action_ids is not None:
            payload["action_ids"] = list(action_ids)
        try:
            self._event_callback(copy.deepcopy(payload))
        except Exception:  # noqa: BLE001 - renderer notification is isolated
            # A renderer notification is never allowed to roll back or replay
            # an already-persisted user operation.
            return

    def _sync_and_emit_expired(
        self, expired: list[dict[str, Any]], *, ledger: Mapping[str, Any]
    ) -> None:
        self.checkpoint.sync(ledger)
        if expired:
            self._emit(
                "cards_expired",
                ledger=ledger,
                action_ids=[item["action_id"] for item in expired],
            )

    @contextmanager
    def agent_call(self) -> Iterator[None]:
        """Reserve the App/file-operation resource while an App call executes.

        This is a domain exclusion lease, not the owner of a DSH Turn. New
        scoped-tool callers release it after durable settlement, including
        while the model itself may continue thinking or wait for the user.
        """

        if self.ledger.legacy_recovery:
            _fail("app_action_legacy_read_only")
        with self._gate.enter("agent"), self.checkpoint.exclusive_operation():
            ledger = self.ledger.read()
            self.checkpoint.sync(ledger)
            # Model domain tools edit the isolated candidate, not the formal
            # document/file owned by a claimed UI operation. Keep that exact
            # operation's durable claim (and prohibit its replay), but do not
            # lock unrelated candidate work while a picker waits for the user.
            # Formal document application still checks execution_pending().
            yield

    @contextmanager
    def agent_turn(self) -> Iterator[None]:
        """Legacy V2 caller scope; retire with the old whole-Turn adapter."""
        with self.agent_call():
            yield

    @property
    def agent_turn_active(self) -> bool:
        return self._gate.active("agent")

    def resource_snapshot(self):
        return self._gate.snapshot()

    def subscribe_resource(self, callback):
        return self._gate.subscribe(callback)

    def call_model_tool(
        self, name: str, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Run the fixed path-free tool host inside an admitted Agent turn."""

        if not self._gate.owned("agent"):
            _fail("app_action_agent_turn_required")
        binding, workspace = self._read_context()
        before = self.ledger.read()
        host = NativeAppActionToolHost(
            ledger=self.ledger,
            binding_reader=lambda: copy.deepcopy(binding),
            workspace_reader=lambda: copy.deepcopy(workspace),
            candidate_target_factory=self.candidate_draft_store.capture if self.candidate_draft_store else None,
        )
        try:
            result = host.call(name, arguments)
        finally:
            after = self.ledger.read()
            self.checkpoint.sync(after)
        if after["revision"] != before["revision"]:
            card = after["cards"][-1] if after["cards"] else None
            self._emit("card_ledger_changed", ledger=after, card=card)
        return result

    def list_cards(self, *, include_terminal: bool = True) -> list[dict[str, Any]]:
        # The separate checkpoint lock provides a stable read, not mutation
        # authority. Rendering/TaskCenter polling must not reject Agent tools.
        ledger, checkpoint = self.checkpoint.read_cards_snapshot(self.ledger.read)
        pending_ids = set()
        inflight = checkpoint["inflight"]
        if inflight is not None:
            pending_ids.add(inflight["action_id"])
        for card in ledger["cards"]:
            receipt = card["ui_receipt"]
            if (
                card["status"] == "presented"
                and isinstance(receipt, str)
                and self.checkpoint.has_execution_marker(
                    action_id=card["action_id"], ui_receipt=receipt
                )
            ):
                pending_ids.add(card["action_id"])
        cards = ledger["cards"]
        if not include_terminal:
            cards = [
                card
                for card in cards
                if card["status"] not in _TERMINAL_STATES
            ]
        result = []
        for card in cards:
            projected = _ui_card(card)
            publication = self._draft_publication(card)
            if publication:
                status, report = publication
                projected["draft_publication"] = {"status": status}
                if isinstance(report.get("path"), str):
                    projected["draft_publication"]["path"] = report["path"]
            if card["action_id"] in pending_ids:
                projected["status"] = "claimed_for_external_execution"
                projected["execution_outcome_unknown"] = not publication or publication[0] != "verified"
            result.append(projected)
        return [*self._legacy_cards, *result]

    def request_candidate_input_draft(self, *, expected_revision):
        """Trusted user command, not a synthetic model tool call."""
        if self.ledger.legacy_recovery or self.candidate_draft_store is None:
            _fail("app_action_candidate_draft_unavailable")
        with self._gate.enter("app_request"), self.checkpoint.exclusive_operation():
            ledger = self.ledger.read()
            if self.checkpoint.sync(ledger)["inflight"] is not None:
                _fail("app_action_execution_already_claimed")
            binding, workspace = self._read_context()
            target = self.candidate_draft_store.capture(expected_revision)
            result = self.ledger.request(expected_revision=ledger["revision"], action="export_candidate_input_draft",
                reason="用户请求保存当前候选输入草稿", suggested_name=f"候选输入草稿-r{expected_revision}.gencdraft.json",
                binding=binding, workspace=workspace, target=target)
            updated = self.ledger.read()
            self.checkpoint.sync(updated)
            self._emit("card_ledger_changed", ledger=updated, card=result["card"])
            return {"status": result["status"], "card": _ui_card(result["card"]),
                    "ledger_revision": result["ledger_revision"]}

    def _draft_publication(self, card):
        if card["action"] != "export_candidate_input_draft" or card["ui_receipt"] is None:
            return None
        identity = {"action_id": card["action_id"], "ui_receipt_hash": fingerprint(card["ui_receipt"]),
                    "target_fingerprint": fingerprint(card["target"])}
        for status, prefix in (("verified", "draft-publication-"), ("written_unverified", "draft-write-unverified-")):
            try:
                row = read_owned(self.ledger.root, prefix + card["action_id"] + ".json", 8192)
            except (OSError, PlanError):
                continue
            if type(row) is not dict or any(row.get(key) != value for key, value in identity.items()):
                continue
            report = row.get("report")
            if type(report) is not dict:
                continue
            if status == "verified" and (report.get("saved") is not True or
                    report.get("artifact_id") != card["target"]["artifact_id"] or
                    report.get("input_snapshot_fingerprint") != card["target"]["input_snapshot_fingerprint"] or
                    not _HASH.fullmatch(str(report.get("file_sha")))):
                continue
            return status, report
        return None

    def publish_candidate_input_draft(self, *, action_id, ui_receipt, output_path,
                                      expected_file_sha=None, overwrite=False):
        """Trusted user file port. The model never receives this pathful API."""
        with self._gate.enter("file_ui"), self.checkpoint.exclusive_operation():
            ledger = self.ledger.read()
            checkpoint = self.checkpoint.sync(ledger)
            card = next((row for row in ledger["cards"] if row["action_id"] == action_id), None)
            if (card is None or card["action"] != "export_candidate_input_draft"
                    or card["status"] != "presented" or card["ui_receipt"] != ui_receipt):
                _fail("app_action_not_executable")
            claim = checkpoint["inflight"]
            if (claim is None or claim["action_id"] != action_id
                    or claim["ui_receipt_hash"] != fingerprint(ui_receipt)
                    or claim["target_fingerprint"] != fingerprint(card["target"])):
                _fail("app_action_execution_claim_mismatch")
            if self.candidate_draft_store is None:
                _fail("app_action_candidate_draft_unavailable")
            proof_name = "draft-publication-" + action_id + ".json"
            if safe_path(self.ledger.root, proof_name).exists():
                # A dropped UI response is not permission to repeat a write.
                publication = self._draft_publication(card)
                if publication is None or publication[0] != "verified":
                    _fail("app_action_draft_publication_outcome_unknown")
                return publication[1]
            attempt_name = "draft-write-attempt-" + action_id + ".json"
            if safe_path(self.ledger.root, attempt_name).exists():
                _fail("app_action_draft_publication_outcome_unknown")
            write_owned(self.ledger.root, attempt_name,
                        {"target_fingerprint": fingerprint(card["target"]), "action_id": action_id}, 2048)
            identity = {"action_id": action_id, "ui_receipt_hash": fingerprint(ui_receipt),
                        "target_fingerprint": fingerprint(card["target"])}
            try:
                report = self.candidate_draft_store.publish_snapshot(card["target"], output_path,
                    expected_file_sha=expected_file_sha, overwrite=overwrite)
            except PlanError as error:
                details = error.details or {}
                if details.get("publication_state") == "written_unverified":
                    with suppress(OSError, PlanError):
                        write_owned(self.ledger.root, "draft-write-unverified-" + action_id + ".json",
                                    {**identity, "report": {"path": details["path"]}}, 8192)
                raise
            try:
                write_owned(self.ledger.root, proof_name, {**identity, "report": report}, 8192)
            except (OSError, PlanError) as error:
                raise PlanError("草稿已写入，但未能登记保存凭据。请先核对所选文件。",
                                "candidate_input_draft_write_unconfirmed",
                                {"publication_state": "written_unverified", "path": report["path"]}) from error
            return report

    def present_action(self, action_id: str) -> dict[str, Any]:
        """Present one queued card.  This never invokes a file callback."""
        with (
            self._gate.enter("file_ui"),
            self.checkpoint.exclusive_operation(),
        ):
            existing_ledger = self.ledger.read()
            existing_checkpoint = self.checkpoint.sync(existing_ledger)
            if existing_checkpoint["inflight"] is not None:
                _fail("app_action_execution_already_claimed")
            for candidate in existing_ledger["cards"]:
                candidate_receipt = candidate["ui_receipt"]
                if (
                    candidate["status"] == "presented"
                    and isinstance(candidate_receipt, str)
                    and self.checkpoint.has_execution_marker(
                        action_id=candidate["action_id"],
                        ui_receipt=candidate_receipt,
                    )
                ):
                    _fail("app_action_execution_already_claimed")
            binding, workspace = self._read_context()
            expired = self.ledger.refresh_binding(current_binding=binding)
            current = self.ledger.read()
            self._sync_and_emit_expired(expired, ledger=current)
            before_present_revision = current["revision"]
            try:
                result = self.ledger.present(
                    action_id=action_id,
                    agent_turn_active=False,
                    current_binding=binding,
                    current_workspace=workspace,
                )
            except NativeAppActionError:
                failed_ledger = self.ledger.read()
                self.checkpoint.sync(failed_ledger)
                if failed_ledger["revision"] != before_present_revision:
                    failed_card = next(
                        (
                            item
                            for item in failed_ledger["cards"]
                            if item["action_id"] == action_id
                        ),
                        None,
                    )
                    self._emit(
                        "card_ledger_changed",
                        ledger=failed_ledger,
                        card=failed_card,
                    )
                raise
            ledger = self.ledger.read()
            self.checkpoint.sync(ledger)
            event = (
                "card_presented"
                if result["card"]["status"] == "presented"
                else "card_ledger_changed"
            )
            self._emit(event, ledger=ledger, card=result["card"])
            return {
                "status": result["status"],
                "ledger_revision": result["ledger_revision"],
                "card": _ui_card(result["card"]),
            }

    @staticmethod
    def _execution_request(
        card: Mapping[str, Any], workspace: Mapping[str, Any]
    ) -> dict[str, Any]:
        dirty_open_guard_required = bool(
            card["dirty_open_guard_required"]
            or (card["action"] == "open_document" and workspace["dirty"])
        )
        return {
            "action_id": card["action_id"],
            "action": card["action"],
            "reason": card["reason"],
            "suggested_name": card["suggested_name"],
            "document_kind": workspace["document_kind"],
            "display_name": workspace["display_name"],
            "current_view": workspace["current_view"],
            "dirty_open_guard_required": dirty_open_guard_required,
            "target": copy.deepcopy(card["target"]),
        }

    def _claim_presented_locked(
        self,
        *,
        action_id: str,
        ui_receipt: str,
        callback_required: bool,
    ) -> dict[str, Any]:
        """Persist the one-shot execution boundary without invoking UI."""

        existing_ledger = self.ledger.read()
        existing_checkpoint = self.checkpoint.sync(existing_ledger)
        if existing_checkpoint["inflight"] is not None:
            _fail("app_action_execution_already_claimed")
        binding, workspace = self._read_context()
        expired = self.ledger.refresh_binding(current_binding=binding)
        ledger = self.ledger.read()
        self._sync_and_emit_expired(expired, ledger=ledger)
        card = next(
            (item for item in ledger["cards"] if item["action_id"] == action_id),
            None,
        )
        if card is None:
            _fail("app_action_not_executable")
        if card["ui_receipt"] != ui_receipt:
            _fail("app_action_execution_receipt_mismatch")
        if card["status"] in _TERMINAL_STATES:
            return {
                "status": "already_settled",
                "ledger_revision": ledger["revision"],
                "card": _ui_card(card),
                "request": None,
                "expired_action_ids": [],
            }
        if card["status"] != "presented":
            _fail("app_action_not_executable")
        if self.ledger.legacy_recovery:
            _fail("app_action_legacy_read_only")
        if card["action"] == "export_candidate_input_draft":
            if self.candidate_draft_store is None:
                _fail("app_action_candidate_draft_unavailable")
            self.candidate_draft_store.read_target(card["target"], check_current=False)
        if callback_required and self._callbacks.get(card["action"]) is None:
            _fail("app_action_callback_unavailable")

        checkpoint = self.checkpoint.sync(ledger)
        if checkpoint["inflight"] is not None:
            _fail("app_action_execution_already_claimed")

        capability = _ACTION_CAPABILITIES.get(card["action"])
        unavailable = (
            capability is not None and workspace[capability] is not True
        ) or (
            card["action"] == "save_current"
            and workspace["has_path"] is not True
        )
        if unavailable:
            resolved = self.ledger.resolve(
                action_id=action_id,
                status="failed",
                result_message="当前工作区已不允许该操作。",
                current_binding=binding,
                agent_turn_active=False,
                ui_receipt=ui_receipt,
                dirty_open_guard_passed=False,
            )
            final_ledger = self.ledger.read()
            self.checkpoint.sync(final_ledger)
            self._emit(
                "card_resolved",
                ledger=final_ledger,
                card=resolved["card"],
            )
            return {
                "status": resolved["status"],
                "ledger_revision": final_ledger["revision"],
                "card": _ui_card(resolved["card"]),
                "request": None,
                "expired_action_ids": [],
            }

        request = self._execution_request(card, workspace)
        marker_created = self.checkpoint.persist_execution_marker(
            action_id=action_id,
            ui_receipt=ui_receipt,
        )
        if not marker_created:
            # The marker is a write-ahead proof that the external side effect
            # may already have happened.  Never hand the request to Renderer a
            # second time, even when the checkpoint claim was lost in a crash.
            checkpoint = self.checkpoint.sync(ledger)
            inflight = checkpoint["inflight"]
            if inflight is None:
                self.checkpoint.claim_execution(
                    ledger=ledger,
                    action_id=action_id,
                    ui_receipt=ui_receipt,
                    dirty_open_guard_required=request[
                        "dirty_open_guard_required"
                    ],
                )
            else:
                if (
                    inflight["action_id"] != action_id
                    or inflight["ui_receipt_hash"] != fingerprint(ui_receipt)
                ):
                    _fail("app_action_execution_already_claimed")
                _fail("app_action_execution_already_claimed")
            resolved = self.ledger.resolve(
                action_id=action_id,
                status="failed",
                result_message=(
                    "检测到既有执行标记，操作结果未知；系统未自动重试。"
                ),
                current_binding=binding,
                agent_turn_active=False,
                ui_receipt=ui_receipt,
                dirty_open_guard_passed=False,
            )
            final_ledger = self.ledger.read()
            self.checkpoint.finish_execution(
                ledger=final_ledger,
                action_id=action_id,
                ui_receipt=ui_receipt,
            )
            self._emit(
                "execution_recovered",
                ledger=final_ledger,
                card=resolved["card"],
            )
            return {
                "status": "recovered_without_replay",
                "ledger_revision": final_ledger["revision"],
                "card": _ui_card(resolved["card"]),
                "request": None,
                "expired_action_ids": [],
            }

        # This claim is durable before the request is returned to Renderer.
        # A crash after this line is outcome-unknown and must be settled or
        # recovered; the file/navigation operation is never replayed.
        self.checkpoint.claim_execution(
            ledger=ledger,
            action_id=action_id,
            ui_receipt=ui_receipt,
            dirty_open_guard_required=request["dirty_open_guard_required"],
        )
        return {
            "status": "claimed_for_external_execution",
            "ledger_revision": ledger["revision"],
            "card": _ui_card(card),
            "request": request,
            "expired_action_ids": [],
        }

    def claim_presented_action(
        self, *, action_id: str, ui_receipt: str
    ) -> dict[str, Any]:
        """Claim a presented card before Renderer performs its side effect.

        The returned request contains display metadata only.  Renderer owns
        the native file picker/navigation operation and must later call
        :meth:`settle_claimed_action` with the same receipt.
        """

        with (
            self._gate.enter("file_ui"),
            self.checkpoint.exclusive_operation(),
        ):
            return self._claim_presented_locked(
                action_id=action_id,
                ui_receipt=ui_receipt,
                callback_required=False,
            )

    def _settle_claimed_locked(
        self,
        *,
        action_id: str,
        ui_receipt: str,
        outcome: Mapping[str, Any],
    ) -> dict[str, Any]:
        checked_outcome = _callback_outcome(outcome)
        post_binding, _post_workspace = self._read_context()
        ledger = self.ledger.read()
        card = next(
            (item for item in ledger["cards"] if item["action_id"] == action_id),
            None,
        )
        if card is None:
            _fail("app_action_not_executable")
        if card["ui_receipt"] != ui_receipt:
            _fail("app_action_execution_receipt_mismatch")

        if card["action"] == "export_candidate_input_draft" and checked_outcome["status"] == "completed":
            publication = self._draft_publication(card)
            if publication is None or publication[0] != "verified":
                _fail("app_action_draft_publication_unconfirmed")
        # The file card consumes the exact captured draft. A changed editor
        # after the write cannot undo an already proven publication.
        if card["action"] == "export_candidate_input_draft":
            post_binding = card["binding"]

        read_only_open = checked_outcome["read_only_open"]
        if read_only_open and (
            card["action"] != "open_document"
            or checked_outcome["status"] != "completed"
            or post_binding != card["binding"]
        ):
            _fail("app_action_read_only_open_binding_invalid")
        # A historical report or a separate recovery conversation never
        # leaves the formal editor's document.
        if read_only_open:
            checked_outcome["dirty_open_guard_passed"] = True

        checkpoint = self.checkpoint.sync(ledger)
        inflight = checkpoint["inflight"]
        if card["status"] in {"completed", "cancelled", "failed"}:
            # Exact settlement replay is read-only and lets a caller recover
            # after losing the first response.  A conflicting replay is
            # rejected by the ledger before the durable claim is cleared.
            self.ledger.resolve(
                action_id=action_id,
                status=checked_outcome["status"],
                result_message=checked_outcome["message"],
                current_binding=post_binding,
                agent_turn_active=False,
                ui_receipt=ui_receipt,
                dirty_open_guard_passed=checked_outcome[
                    "dirty_open_guard_passed"
                ],
            )
            expired_after_open: list[dict[str, Any]] = []
            if inflight is not None:
                if (
                    inflight["action_id"] != action_id
                    or inflight["ui_receipt_hash"] != fingerprint(ui_receipt)
                ):
                    _fail("app_action_execution_already_claimed")
                if card["action"] == "open_document" and card["status"] == "completed" and not read_only_open:
                    expired_after_open = self.ledger.expire_pending(
                        exclude_action_id=action_id,
                        result_message=(
                            "App已打开另一份文档；旧会话中的操作请求已经失效。"
                        ),
                    )
                    ledger = self.ledger.read()
                self.checkpoint.finish_execution(
                    ledger=ledger,
                    action_id=action_id,
                    ui_receipt=ui_receipt,
                )
            final_ledger = self.ledger.read()
            final_card = next(
                item
                for item in final_ledger["cards"]
                if item["action_id"] == action_id
            )
            self._emit(
                "card_resolved",
                ledger=final_ledger,
                card=final_card,
            )
            if expired_after_open:
                self._emit(
                    "cards_expired",
                    ledger=final_ledger,
                    action_ids=[
                        item["action_id"] for item in expired_after_open
                    ],
                )
            return {
                "status": "already_settled",
                "ledger_revision": final_ledger["revision"],
                "card": _ui_card(final_card),
                "expired_action_ids": [
                    item["action_id"] for item in expired_after_open
                ],
            }
        if card["status"] != "presented":
            _fail("app_action_not_executable")
        if inflight is None:
            _fail("app_action_execution_claim_missing")
        if (
            inflight["action_id"] != action_id
            or inflight["ui_receipt_hash"] != fingerprint(ui_receipt)
        ):
            _fail("app_action_execution_claim_mismatch")
        if not self.checkpoint.has_execution_marker(
            action_id=action_id,
            ui_receipt=ui_receipt,
        ):
            _fail("app_action_execution_marker_missing")

        dirty_open_guard_required = bool(
            card["dirty_open_guard_required"]
            or inflight["dirty_open_guard_required"]
        )
        dirty_guard_rejected = (
            dirty_open_guard_required
            and checked_outcome["status"] == "completed"
            and not checked_outcome["dirty_open_guard_passed"]
        )
        if dirty_guard_rejected:
            checked_outcome = {
                "status": "failed",
                "message": (
                    "未取得脏文档处理确认，操作结果未知；系统不会自动重试。"
                ),
                "dirty_open_guard_passed": False,
            }

        try:
            resolved = self.ledger.resolve(
                action_id=action_id,
                status=checked_outcome["status"],
                result_message=checked_outcome["message"],
                # A completed native open may already have switched the
                # document before Renderer reports that its dirty-document
                # guard was missing.  Record that safety failure against the
                # proven pre-open binding, then expire other old-document
                # cards against the observed post-open binding below.
                current_binding=(
                    card["binding"] if dirty_guard_rejected else post_binding
                ),
                agent_turn_active=False,
                ui_receipt=ui_receipt,
                dirty_open_guard_passed=checked_outcome[
                    "dirty_open_guard_passed"
                ],
            )
        except NativeAppActionError:
            # ``resolve`` may have expired the card after an unexpected
            # binding change.  A durable terminal can close the claim; any
            # nonterminal state stays outcome-unknown for explicit recovery.
            current = self.ledger.read()
            current_card = next(
                item
                for item in current["cards"]
                if item["action_id"] == action_id
            )
            if current_card["status"] in _TERMINAL_STATES:
                self.checkpoint.finish_execution(
                    ledger=current,
                    action_id=action_id,
                    ui_receipt=ui_receipt,
                )
                self._emit(
                    "card_resolved",
                    ledger=current,
                    card=current_card,
                )
            raise

        # A successful open settles against its legal post-open binding before
        # stale cards belonging to the old document are expired.
        expired_after_open: list[dict[str, Any]] = []
        if card["action"] == "open_document" and checked_outcome["status"] == "completed" and not read_only_open:
            expired_after_open = self.ledger.expire_pending(
                exclude_action_id=action_id,
                result_message=(
                    "App已打开另一份文档；旧会话中的操作请求已经失效。"
                ),
            )
        elif (
            card["action"] == "open_document"
            and dirty_guard_rejected
            and card["binding"] != post_binding
        ):
            expired_after_open = self.ledger.refresh_binding(
                current_binding=post_binding
            )
        final_ledger = self.ledger.read()
        self.checkpoint.finish_execution(
            ledger=final_ledger,
            action_id=action_id,
            ui_receipt=ui_receipt,
        )
        self._emit("card_resolved", ledger=final_ledger, card=resolved["card"])
        if expired_after_open:
            self._emit(
                "cards_expired",
                ledger=final_ledger,
                action_ids=[item["action_id"] for item in expired_after_open],
            )
        return {
            "status": resolved["status"],
            "ledger_revision": final_ledger["revision"],
            "card": _ui_card(resolved["card"]),
            "expired_action_ids": [
                item["action_id"] for item in expired_after_open
            ],
        }

    def settle_claimed_action(
        self,
        *,
        action_id: str,
        ui_receipt: str,
        outcome: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist Renderer outcome for an earlier one-shot claim."""

        with (
            self._gate.enter("file_ui"),
            self.checkpoint.exclusive_operation(),
        ):
            return self._settle_claimed_locked(
                action_id=action_id,
                ui_receipt=ui_receipt,
                outcome=outcome,
            )

    def execute_presented(
        self, *, action_id: str, ui_receipt: str
    ) -> dict[str, Any]:
        """Compatibility path for trusted synchronous App callbacks."""

        with (
            self._gate.enter("file_ui"),
            self.checkpoint.exclusive_operation(),
        ):
            claimed = self._claim_presented_locked(
                action_id=action_id,
                ui_receipt=ui_receipt,
                callback_required=True,
            )
            request = claimed["request"]
            if request is None:
                claimed.pop("request", None)
                return claimed
            card = claimed["card"]
            callback = self._callbacks[card["action"]]
            try:
                outcome = _callback_outcome(callback(copy.deepcopy(request)))
            except Exception as error:  # noqa: BLE001 - the effect may already have committed
                from genshin_app.app_fault_store import fault_scope
                from genshin_app.app_system_fault import system_failure

                with fault_scope(self._diagnostic_data_dir, conversation_id=self.ledger.conversation_id,
                                 operation_id=action_id):
                    failure = system_failure(error, component="app_domain_binding", operation="app.file_callback")
                unknown = NativeAppActionControllerError("app_action_execution_outcome_unknown")
                unknown.details = {"action_id": action_id, "outcome": "unknown", "system_failure": failure}
                # Keep the original claim/marker; a callback exception is not
                # evidence that the file operation failed before writing.
                raise unknown from None
            return self._settle_claimed_locked(
                action_id=action_id,
                ui_receipt=ui_receipt,
                outcome=outcome,
            )

    def recover_interrupted_execution(self) -> dict[str, Any] | None:
        """Close a persisted unknown callback claim without invoking it again."""

        with (
            self._gate.enter("file_ui"),
            self.checkpoint.exclusive_operation(),
        ):
            ledger = self.ledger.read()
            checkpoint = self.checkpoint.sync(ledger)
            inflight = checkpoint["inflight"]
            if inflight is None:
                for candidate in ledger["cards"]:
                    receipt = candidate["ui_receipt"]
                    if (
                        candidate["status"] == "presented"
                        and isinstance(receipt, str)
                        and self.checkpoint.has_execution_marker(
                            action_id=candidate["action_id"],
                            ui_receipt=receipt,
                        )
                    ):
                        self.checkpoint.claim_execution(
                            ledger=ledger,
                            action_id=candidate["action_id"],
                            ui_receipt=receipt,
                        )
                        inflight = {
                            "action_id": candidate["action_id"],
                            "ui_receipt_hash": fingerprint(receipt),
                            "claimed_ledger_revision": ledger["revision"],
                        }
                        break
                if inflight is None:
                    return None
            action_id = inflight["action_id"]
            card = next(
                (item for item in ledger["cards"] if item["action_id"] == action_id),
                None,
            )
            if card is None:
                _fail("app_action_execution_claim_orphaned")
            ui_receipt = card["ui_receipt"]
            if not isinstance(ui_receipt, str):
                _fail("app_action_execution_claim_orphaned")
            expired_after_open: list[dict[str, Any]] = []
            if (
                card["status"] == "completed"
                and card["action"] == "open_document"
            ):
                expired_after_open = self.ledger.expire_pending(
                    exclude_action_id=action_id,
                    result_message=(
                        "App可能已经打开另一份文档；旧会话中的操作请求已经失效。"
                    ),
                )
                ledger = self.ledger.read()
                card = next(
                    item
                    for item in ledger["cards"]
                    if item["action_id"] == action_id
                )
            if card["status"] not in _TERMINAL_STATES:
                # Recovery runs before a cold Router has rebuilt its live App
                # context.  The durable card binding is therefore the only
                # safe identity available here.  Resolve the claimed card
                # against that exact identity and never infer the document
                # currently visible in the Renderer.
                self.ledger.resolve(
                    action_id=action_id,
                    status="failed",
                    result_message=(
                        "上次操作在结果确认前中断，结果未知；系统未自动重试。"
                    ),
                    current_binding=card["binding"],
                    agent_turn_active=False,
                    ui_receipt=ui_receipt,
                    dirty_open_guard_passed=False,
                )
                if card["action"] == "open_document":
                    expired_after_open = self.ledger.expire_pending(
                        exclude_action_id=action_id,
                        result_message=(
                            "上次打开操作的结果未知；旧会话中的其他操作请求已经失效。"
                        ),
                    )
                ledger = self.ledger.read()
                card = next(
                    item
                    for item in ledger["cards"]
                    if item["action_id"] == action_id
                )
                if card["status"] not in _TERMINAL_STATES:
                    _fail("app_action_execution_recovery_incomplete")
            self.checkpoint.finish_execution(
                ledger=ledger,
                action_id=action_id,
                ui_receipt=ui_receipt,
            )
            self._emit("execution_recovered", ledger=ledger, card=card)
            if expired_after_open:
                self._emit(
                    "cards_expired",
                    ledger=ledger,
                    action_ids=[
                        item["action_id"] for item in expired_after_open
                    ],
                )
            return {
                "status": "recovered_without_replay",
                "ledger_revision": ledger["revision"],
                "card": _ui_card(card),
                "expired_action_ids": [
                    item["action_id"] for item in expired_after_open
                ],
            }

    def recover_claimed_action_without_replay(
        self, *, action_id: str, ui_receipt: str
    ) -> dict[str, Any]:
        """Explicitly close one exact outcome-unknown Renderer claim.

        This recovery endpoint is intentionally narrower than cold-start
        recovery: the caller must present the durable action identity and its
        UI receipt.  It never returns an execution request and therefore can
        be used after a WebView reload without repeating a file or navigation
        side effect whose result was lost with Renderer memory.
        """

        if not isinstance(action_id, str) or _ACTION_ID.fullmatch(action_id) is None:
            _fail("app_action_execution_identity_invalid")
        if (
            not isinstance(ui_receipt, str)
            or _UI_RECEIPT.fullmatch(ui_receipt) is None
        ):
            _fail("app_action_execution_receipt_invalid")

        with (
            self._gate.enter("file_ui"),
            self.checkpoint.exclusive_operation(),
        ):
            ledger = self.ledger.read()
            card = next(
                (item for item in ledger["cards"] if item["action_id"] == action_id),
                None,
            )
            if card is None:
                _fail("app_action_not_executable")
            if card["ui_receipt"] != ui_receipt:
                _fail("app_action_execution_receipt_mismatch")
            if not self.checkpoint.has_execution_marker(
                action_id=action_id,
                ui_receipt=ui_receipt,
            ):
                _fail("app_action_execution_marker_missing")

            checkpoint = self.checkpoint.sync(ledger)
            inflight = checkpoint["inflight"]
            if inflight is not None and (
                inflight["action_id"] != action_id
                or inflight["ui_receipt_hash"] != fingerprint(ui_receipt)
            ):
                _fail("app_action_execution_claim_mismatch")

            exact_recovery_replay = (
                card["status"] == "failed"
                and card["result_message"] == _EXPLICIT_UNKNOWN_RECOVERY_MESSAGE
            )
            if card["status"] in _TERMINAL_STATES and not exact_recovery_replay:
                _fail("app_action_execution_not_recoverable")
            if card["status"] not in {"presented", "failed"}:
                _fail("app_action_execution_not_recoverable")

            if inflight is None and card["status"] == "presented":
                # Cover the crash window between durable marker creation and
                # checkpoint claim persistence.  The marker is proof that the
                # side effect may already have happened, never permission to
                # return its request again.
                self.checkpoint.claim_execution(
                    ledger=ledger,
                    action_id=action_id,
                    ui_receipt=ui_receipt,
                    dirty_open_guard_required=bool(
                        card["dirty_open_guard_required"]
                    ),
                )
                inflight = self.checkpoint.sync(ledger)["inflight"]
            elif inflight is None and not exact_recovery_replay:
                _fail("app_action_execution_claim_missing")

            publication = self._draft_publication(card)
            if card["status"] == "presented" and publication and publication[0] == "verified":
                return self._settle_claimed_locked(action_id=action_id, ui_receipt=ui_receipt, outcome={
                    "status": "completed", "message": "已核实候选输入草稿保存成功，已补记操作结果。",
                    "dirty_open_guard_passed": False,
                })

            expired_after_open: list[dict[str, Any]] = []
            if card["status"] == "presented":
                self.ledger.resolve(
                    action_id=action_id,
                    status="failed",
                    result_message=_EXPLICIT_UNKNOWN_RECOVERY_MESSAGE,
                    current_binding=card["binding"],
                    agent_turn_active=False,
                    ui_receipt=ui_receipt,
                    dirty_open_guard_passed=False,
                )
                if card["action"] == "open_document":
                    expired_after_open = self.ledger.expire_pending(
                        exclude_action_id=action_id,
                        result_message=(
                            "上次打开操作的结果未知；"
                            "旧会话中的其他操作请求已经失效。"
                        ),
                    )
                ledger = self.ledger.read()
                card = next(
                    item
                    for item in ledger["cards"]
                    if item["action_id"] == action_id
                )

            if inflight is not None:
                self.checkpoint.finish_execution(
                    ledger=ledger,
                    action_id=action_id,
                    ui_receipt=ui_receipt,
                )
            self._emit("execution_recovered", ledger=ledger, card=card)
            if expired_after_open:
                self._emit(
                    "cards_expired",
                    ledger=ledger,
                    action_ids=[
                        item["action_id"] for item in expired_after_open
                    ],
                )
            return {
                "status": (
                    "already_recovered"
                    if exact_recovery_replay
                    else "recovered_without_replay"
                ),
                "ledger_revision": ledger["revision"],
                "card": _ui_card(card),
                "request": None,
                "expired_action_ids": [
                    item["action_id"] for item in expired_after_open
                ],
            }

    def execution_pending(self) -> bool:
        """Return whether an external side effect may still need settlement.

        This read includes both the normal durable inflight claim and the
        narrower marker-only crash window.  It never recovers or replays the
        operation; callers use it to prevent an activation from being closed
        while its Renderer may still own the result callback.
        """

        with (
            self._gate.enter("file_ui"),
            self.checkpoint.exclusive_operation(),
        ):
            ledger = self.ledger.read()
            checkpoint = self.checkpoint.sync(ledger)
            if checkpoint["inflight"] is not None:
                return True
            return any(
                card["status"] == "presented"
                and isinstance(card["ui_receipt"], str)
                and self.checkpoint.has_execution_marker(
                    action_id=card["action_id"],
                    ui_receipt=card["ui_receipt"],
                )
                for card in ledger["cards"]
            )

    def checkpoint_projection(self) -> dict[str, Any]:
        """Return the exact path-free value to bind into a V2 checkpoint."""

        return self.checkpoint.projection(self.ledger.read())


__all__ = [
    "CHECKPOINT_CONTRACT",
    "CHECKPOINT_FILE",
    "NativeAppActionCheckpoint",
    "NativeAppActionController",
    "NativeAppActionControllerError",
]
