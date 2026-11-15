"""Bounded, atomic Host receipt for one DSH question continuation.

The DSH log remains the authority for model/session execution. This receipt
is the Host transaction journal which prevents a completed continuation from
being submitted to the DSH runtime twice and records whether the App-side
terminal projection has also committed. It deliberately contains no prompt,
message, assistant text, tool result, or raw reasoning.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from genshin_app.equipment.files import fingerprint, json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_db.locking import file_lock

from .native_dsh_checkpoint import (
    normalize_native_dsh_checkpoint,
    require_native_dsh_checkpoint_progress,
)
from .sessions import safe_path, write_owned

CONTRACT = "genshin-native-question-resume-receipt-v1"
DIRECTORY = ".native-question-resume-receipts-v1"
MAX_RECEIPT_BYTES = 512 * 1024
PREPARED = "prepared"
RUNTIME_SETTLED = "runtime_settled"
APP_APPLIED = "app_applied"

_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_STATES = frozenset({PREPARED, RUNTIME_SETTLED, APP_APPLIED})
_ROOT_KEYS = {
    "contract",
    "identity",
    "identity_hash",
    "state",
    "baseline_dsh_checkpoint",
    "resume_ack",
    "terminal_dsh_checkpoint",
    "terminal_outcome",
    "app_result",
    "post_checkpoint_binding",
}
_IDENTITY_KEYS = {
    "conversation_id",
    "dsh_session_id",
    "request_id",
    "message_id",
    "runtime_turn_id",
    "epoch_id",
    "source_turn_id",
    "source_epoch_id",
    "call_id",
    "answer_receipt",
    "question_fingerprint",
    "answers_fingerprint",
    "prompt_profile_hash",
    "runtime_context_hash",
    "tool_manifest_hash",
    "workflow_stage",
    "provider",
    "model",
    "reasoning_effort",
    "credential_generation",
    "pre_checkpoint_binding",
}
_ACK_KEYS = {"status", "call_id", "answer_receipt", "starts_turn", "reason"}
_OUTCOME_KEYS = {
    "terminal_type",
    "event_frames",
    "acknowledged_event_frames",
    "provider_request_possible",
    "terminal_reason",
    "segment_restart_reason",
    "segment_budget_usage",
    "usage",
    "settled_tool_call_ids",
}
_APP_RESULT_KEYS = {"status", "usage"}


def _fail(message: str, reason: str = "native_question_resume_receipt_invalid") -> None:
    raise PlanError(message, reason)


def _identity(value: object, field: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        _fail(f"{field}不符合问题恢复回执身份语法")
    return value


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        _fail(f"{field}必须是小写SHA-256")
    return value


def _counter(value: object, field: str) -> int:
    if type(value) is not int or not 0 <= value <= 2**53 - 1:
        _fail(f"{field}必须是非负安全整数")
    return value


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def normalize_resume_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _IDENTITY_KEYS:
        _fail("问题恢复身份字段不合法")
    row = copy.deepcopy(dict(value))
    for field in (
        "conversation_id",
        "dsh_session_id",
        "request_id",
        "runtime_turn_id",
        "epoch_id",
        "source_turn_id",
        "source_epoch_id",
        "call_id",
        "answer_receipt",
        "provider",
        "model",
        "reasoning_effort",
    ):
        _identity(row.get(field), field)
    message_id = row.get("message_id")
    if not isinstance(message_id, str) or not 1 <= len(message_id) <= 128:
        _fail("message_id不合法")
    for field in (
        "question_fingerprint",
        "answers_fingerprint",
        "prompt_profile_hash",
        "runtime_context_hash",
        "tool_manifest_hash",
        "pre_checkpoint_binding",
    ):
        _digest(row.get(field), field)
    if row.get("workflow_stage") is not None:
        _identity(row["workflow_stage"], "workflow_stage")
    _counter(row.get("credential_generation"), "credential_generation")
    return row


def _normalize_ack(value: object, identity: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _ACK_KEYS:
        _fail("问题恢复确认字段不合法")
    row = copy.deepcopy(dict(value))
    status = row.get("status")
    starts_turn = row.get("starts_turn")
    reason = row.get("reason")
    if (
        status
        not in {"accepted", "already_accepted", "unavailable", "conflict"}
        or row.get("call_id") != identity["call_id"]
        or row.get("answer_receipt") != identity["answer_receipt"]
        or type(starts_turn) is not bool
        or status in {"accepted", "already_accepted"}
        and reason is not None
        or status in {"unavailable", "conflict"}
        and (not isinstance(reason, str) or _IDENTITY.fullmatch(reason) is None)
        or status == "accepted"
        and starts_turn is not True
        or status in {"unavailable", "conflict"}
        and starts_turn is not False
    ):
        _fail("问题恢复确认与请求不匹配")
    return row


def _normalize_usage(value: object, field: str) -> dict[str, int | None] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or len(value) > 32:
        _fail(f"{field}不合法")
    row: dict[str, int | None] = {}
    for name, amount in value.items():
        _identity(name, field)
        if amount is not None:
            _counter(amount, field)
        row[name] = amount
    return row


def _normalize_outcome(value: object, *, starts_turn: bool) -> dict[str, Any] | None:
    if not starts_turn:
        if value is not None:
            _fail("无执行段的问题恢复不能携带终态摘要")
        return None
    if not isinstance(value, Mapping) or set(value) != _OUTCOME_KEYS:
        _fail("问题恢复终态摘要字段不合法")
    row = copy.deepcopy(dict(value))
    if row.get("terminal_type") not in {
        "turn.completed",
        "turn.cancelled",
        "turn.error",
    }:
        _fail("问题恢复终态类型不合法")
    frames = _counter(row.get("event_frames"), "event_frames")
    acknowledged = _counter(
        row.get("acknowledged_event_frames"), "acknowledged_event_frames"
    )
    if frames < 1 or acknowledged != frames:
        _fail("问题恢复事件确认不完整")
    if type(row.get("provider_request_possible")) is not bool:
        _fail("问题恢复provider状态不合法")
    for field in ("terminal_reason", "segment_restart_reason"):
        if row.get(field) is not None:
            _identity(row[field], field)
    row["usage"] = _normalize_usage(row.get("usage"), "usage")
    budget = row.get("segment_budget_usage")
    if budget is not None:
        if not isinstance(budget, Mapping) or len(budget) > 32:
            _fail("问题恢复预算摘要不合法")
        for name, amount in budget.items():
            _identity(name, "segment_budget_usage")
            _counter(amount, "segment_budget_usage")
        row["segment_budget_usage"] = copy.deepcopy(dict(budget))
    settled = row.get("settled_tool_call_ids")
    if (
        not isinstance(settled, list)
        or len(settled) > 4096
        or len(settled) != len(set(settled))
        or any(
            not isinstance(item, str) or _IDENTITY.fullmatch(item) is None
            for item in settled
        )
    ):
        _fail("问题恢复工具结算摘要不合法")
    return row


def _normalize_app_result(value: object, outcome: Mapping[str, Any] | None) -> dict:
    if not isinstance(value, Mapping) or set(value) != _APP_RESULT_KEYS:
        _fail("问题恢复App应用回执不合法")
    row = copy.deepcopy(dict(value))
    if row.get("status") not in {"completed", "cancelled", "error", "no_turn"}:
        _fail("问题恢复App应用状态不合法")
    if outcome is None:
        if row["status"] != "no_turn" or row.get("usage") is not None:
            _fail("无执行段的问题恢复App应用回执不合法")
    else:
        expected = {
            "turn.completed": "completed",
            "turn.cancelled": "cancelled",
            "turn.error": "error",
        }[outcome["terminal_type"]]
        if row["status"] != expected:
            _fail("问题恢复App应用状态与DSH终态不匹配")
        row["usage"] = _normalize_usage(row.get("usage"), "app_result_usage")
    return row


def normalize_resume_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _ROOT_KEYS:
        _fail("问题恢复回执字段不合法")
    row = copy.deepcopy(dict(value))
    if row.get("contract") != CONTRACT or row.get("state") not in _STATES:
        _fail("问题恢复回执版本或状态不受支持")
    identity = normalize_resume_identity(row.get("identity"))
    if _digest(row.get("identity_hash"), "identity_hash") != fingerprint(identity):
        _fail("问题恢复身份摘要不匹配")
    baseline = normalize_native_dsh_checkpoint(
        row.get("baseline_dsh_checkpoint"),
        expected_session_id=identity["dsh_session_id"],
    )
    checked = {**row, "identity": identity, "baseline_dsh_checkpoint": baseline}
    terminal_fields = (
        "resume_ack",
        "terminal_dsh_checkpoint",
        "terminal_outcome",
        "app_result",
        "post_checkpoint_binding",
    )
    if row["state"] == PREPARED:
        if any(row.get(field) is not None for field in terminal_fields):
            _fail("准备中的问题恢复回执不能携带终态")
    else:
        ack = _normalize_ack(row.get("resume_ack"), identity)
        terminal = normalize_native_dsh_checkpoint(
            row.get("terminal_dsh_checkpoint"),
            expected_session_id=identity["dsh_session_id"],
            expected_composition_hash=baseline["composition_hash"],
        )
        if ack["starts_turn"]:
            require_native_dsh_checkpoint_progress(baseline, terminal)
        elif terminal != baseline:
            _fail("无执行段的问题恢复检查点发生变化")
        outcome = _normalize_outcome(
            row.get("terminal_outcome"), starts_turn=ack["starts_turn"]
        )
        checked.update(
            {
                "resume_ack": ack,
                "terminal_dsh_checkpoint": terminal,
                "terminal_outcome": outcome,
            }
        )
        if row["state"] == RUNTIME_SETTLED:
            if (
                row.get("app_result") is not None
                or row.get("post_checkpoint_binding") is not None
            ):
                _fail("未应用的运行时回执不能携带App终态")
        else:
            checked["app_result"] = _normalize_app_result(
                row.get("app_result"), outcome
            )
            checked["post_checkpoint_binding"] = _digest(
                row.get("post_checkpoint_binding"), "post_checkpoint_binding"
            )
    try:
        if len(json_bytes(checked)) > MAX_RECEIPT_BYTES:
            _fail("问题恢复回执超过保存上限", "agent_size_limit")
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise PlanError(
            "问题恢复回执不是严格JSON",
            "native_question_resume_receipt_invalid",
        ) from error
    return checked


class NativeQuestionResumeReceiptStore:
    """One-file-per-request CAS store scoped to one App conversation root."""

    def __init__(self, session_root: str | Path, *, conversation_id: str) -> None:
        self.session_root = Path(session_root).absolute()
        self.conversation_id = _identity(conversation_id, "conversation_id")
        self.root = safe_path(self.session_root, DIRECTORY)

    def _paths(self, request_id: str) -> tuple[Path, Path]:
        request_id = _identity(request_id, "request_id")
        key = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
        return safe_path(self.root, f"{key}.json"), safe_path(self.root, f".{key}.lock")

    def _read(self, path: Path) -> dict[str, Any]:
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            raise PlanError(
                "问题恢复回执不存在", "native_question_resume_receipt_missing"
            ) from None
        if len(raw) > MAX_RECEIPT_BYTES:
            _fail("问题恢复回执超过读取上限", "agent_size_limit")
        try:
            value = json.loads(
                raw.decode("utf-8", errors="strict"),
                object_pairs_hook=_strict_object,
                parse_constant=lambda _: (_ for _ in ()).throw(
                    ValueError("non-finite")
                ),
            )
        except (UnicodeError, ValueError, TypeError, RecursionError) as error:
            raise PlanError(
                "问题恢复回执不是严格JSON",
                "native_question_resume_receipt_invalid",
            ) from error
        return normalize_resume_receipt(value)

    def prepare(
        self,
        *,
        identity: Mapping[str, Any],
        baseline_dsh_checkpoint: Mapping[str, Any],
    ) -> dict[str, Any]:
        checked_identity = normalize_resume_identity(identity)
        if checked_identity["conversation_id"] != self.conversation_id:
            _fail("问题恢复回执不属于当前对话", "stale_context")
        baseline = normalize_native_dsh_checkpoint(
            baseline_dsh_checkpoint,
            expected_session_id=checked_identity["dsh_session_id"],
        )
        self.root.mkdir(parents=True, exist_ok=True)
        path, lock = self._paths(checked_identity["request_id"])
        with file_lock(lock):
            if path.exists():
                current = self._read(path)
                if (
                    current["identity"] != checked_identity
                    or current["baseline_dsh_checkpoint"] != baseline
                ):
                    _fail("问题恢复回执身份冲突", "file_conflict")
                return current
            row = normalize_resume_receipt(
                {
                    "contract": CONTRACT,
                    "identity": checked_identity,
                    "identity_hash": fingerprint(checked_identity),
                    "state": PREPARED,
                    "baseline_dsh_checkpoint": baseline,
                    "resume_ack": None,
                    "terminal_dsh_checkpoint": None,
                    "terminal_outcome": None,
                    "app_result": None,
                    "post_checkpoint_binding": None,
                }
            )
            write_owned(self.root, path.name, row, MAX_RECEIPT_BYTES)
            return copy.deepcopy(row)

    def settle_runtime(
        self,
        *,
        identity: Mapping[str, Any],
        resume_ack: Mapping[str, Any],
        terminal_dsh_checkpoint: Mapping[str, Any],
        terminal_outcome: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        checked_identity = normalize_resume_identity(identity)
        path, lock = self._paths(checked_identity["request_id"])
        with file_lock(lock):
            current = self._read(path)
            if current["identity"] != checked_identity:
                _fail("问题恢复回执身份冲突", "file_conflict")
            proposed = normalize_resume_receipt(
                {
                    **current,
                    "state": RUNTIME_SETTLED,
                    "resume_ack": resume_ack,
                    "terminal_dsh_checkpoint": terminal_dsh_checkpoint,
                    "terminal_outcome": terminal_outcome,
                    "app_result": None,
                    "post_checkpoint_binding": None,
                }
            )
            if current["state"] != PREPARED:
                comparable = {
                    **current,
                    "state": RUNTIME_SETTLED,
                    "app_result": None,
                    "post_checkpoint_binding": None,
                }
                if comparable != proposed:
                    _fail("问题恢复运行时终态回执冲突", "file_conflict")
                return current
            write_owned(self.root, path.name, proposed, MAX_RECEIPT_BYTES)
            return copy.deepcopy(proposed)

    def mark_app_applied(
        self,
        *,
        identity: Mapping[str, Any],
        app_result: Mapping[str, Any],
        post_checkpoint_binding: str,
    ) -> dict[str, Any]:
        checked_identity = normalize_resume_identity(identity)
        path, lock = self._paths(checked_identity["request_id"])
        with file_lock(lock):
            current = self._read(path)
            if current["identity"] != checked_identity:
                _fail("问题恢复回执身份冲突", "file_conflict")
            if current["state"] == PREPARED:
                _fail("运行时未结算的问题恢复不能标记为已应用")
            proposed = normalize_resume_receipt(
                {
                    **current,
                    "state": APP_APPLIED,
                    "app_result": app_result,
                    "post_checkpoint_binding": post_checkpoint_binding,
                }
            )
            if current["state"] == APP_APPLIED:
                if current != proposed:
                    _fail("问题恢复App应用回执冲突", "file_conflict")
                return current
            write_owned(self.root, path.name, proposed, MAX_RECEIPT_BYTES)
            return copy.deepcopy(proposed)

    def read(self, *, request_id: str) -> dict[str, Any] | None:
        path, lock = self._paths(request_id)
        if not self.root.is_dir() or not path.exists():
            return None
        with file_lock(lock):
            return self._read(path)


__all__ = [
    "APP_APPLIED",
    "CONTRACT",
    "DIRECTORY",
    "MAX_RECEIPT_BYTES",
    "PREPARED",
    "RUNTIME_SETTLED",
    "NativeQuestionResumeReceiptStore",
    "normalize_resume_identity",
    "normalize_resume_receipt",
]
