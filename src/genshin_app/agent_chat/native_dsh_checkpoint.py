"""Path-free receipt proving that one DSH session checkpoint was flushed.

The Node runtime owns DSH persistence.  The App must not inspect or duplicate
that event log, but it does need a small, authenticated fact before changing a
reserved DSH identity into a durable conversation binding.  This module is the
shared Python-side validator for that fact.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from genshin_app.equipment.schema import PlanError

CONTRACT = "genshin-native-dsh-checkpoint-v1"
STORAGE_FORMAT = "dsh-jsonl-zstd-v1"
MAX_SAFE_INTEGER = 2**53 - 1
MAX_REVISION_UTF16_UNITS = 512

_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_KEYS = {
    "contract",
    "session_id",
    "storage_format",
    "composition_hash",
    "revision",
    "last_event_seq",
    "event_count",
    "size_bytes",
    "flush_digest",
}


def _fail(message: str, reason: str = "native_dsh_checkpoint_invalid") -> None:
    raise PlanError(message, reason)


def _identity(value: object, field: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        _fail(f"{field}不符合DSH身份语法")
    return value


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        _fail(f"{field}必须是小写SHA-256")
    return value


def _counter(value: object, field: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SAFE_INTEGER:
        _fail(f"{field}必须是非负安全整数")
    return value


def _utf16_units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _canonical_receipt_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    # Keep this order identical to the Node runtime's JSON.stringify input.
    return {
        "contract": value["contract"],
        "session_id": value["session_id"],
        "storage_format": value["storage_format"],
        "composition_hash": value["composition_hash"],
        "revision": value["revision"],
        "last_event_seq": value["last_event_seq"],
        "event_count": value["event_count"],
        "size_bytes": value["size_bytes"],
    }


def normalize_native_dsh_checkpoint(
    value: Mapping[str, Any],
    *,
    expected_session_id: str | None = None,
    expected_composition_hash: str | None = None,
) -> dict[str, Any]:
    """Validate a runtime receipt without accepting paths or log contents."""

    if not isinstance(value, Mapping) or set(value) != _KEYS:
        _fail("DSH检查点回执字段不合法")
    row = copy.deepcopy(dict(value))
    if row.get("contract") != CONTRACT or row.get("storage_format") != STORAGE_FORMAT:
        _fail("DSH检查点回执版本不受支持", "native_dsh_checkpoint_incompatible")
    session_id = _identity(row.get("session_id"), "session_id")
    if expected_session_id is not None and session_id != _identity(
        expected_session_id, "expected_session_id"
    ):
        _fail("DSH检查点不属于当前会话", "stale_context")
    composition_hash = _digest(row.get("composition_hash"), "composition_hash")
    if expected_composition_hash is not None and composition_hash != _digest(
        expected_composition_hash, "expected_composition_hash"
    ):
        _fail("DSH检查点运行时组合已变化", "stale_context")
    revision = row.get("revision")
    if (
        not isinstance(revision, str)
        or not revision
        or _utf16_units(revision) > MAX_REVISION_UTF16_UNITS
        or any(character in revision for character in ("\x00", "\r", "\n"))
    ):
        _fail("DSH检查点修订标识不合法")
    last_event_seq = row.get("last_event_seq")
    if last_event_seq is not None:
        last_event_seq = _counter(last_event_seq, "last_event_seq")
    event_count = _counter(row.get("event_count"), "event_count")
    _counter(row.get("size_bytes"), "size_bytes")
    if (event_count == 0) != (last_event_seq is None):
        _fail("DSH检查点事件范围不一致")
    flush_digest = _digest(row.get("flush_digest"), "flush_digest")
    detached = _canonical_receipt_payload(row)
    encoded = json.dumps(
        detached,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if hashlib.sha256(encoded).hexdigest() != flush_digest:
        _fail("DSH检查点刷新摘要不匹配")
    return {**detached, "flush_digest": flush_digest}


def compare_native_dsh_checkpoints(
    previous: Mapping[str, Any], current: Mapping[str, Any]
) -> None:
    """Reject identity changes and monotonic persistence regressions."""

    prior = normalize_native_dsh_checkpoint(previous)
    observed = normalize_native_dsh_checkpoint(
        current,
        expected_session_id=prior["session_id"],
        expected_composition_hash=prior["composition_hash"],
    )
    for field in ("event_count", "size_bytes"):
        if observed[field] < prior[field]:
            _fail("DSH检查点发生持久化回退", "stale_context")
    prior_seq = prior["last_event_seq"]
    observed_seq = observed["last_event_seq"]
    if prior_seq is not None and (observed_seq is None or observed_seq < prior_seq):
        _fail("DSH检查点事件序号发生回退", "stale_context")


def require_native_dsh_checkpoint_progress(
    previous: Mapping[str, Any], current: Mapping[str, Any]
) -> None:
    """Prove that a later lifecycle boundary appended durable DSH events.

    Monotonicity alone is insufficient for a terminal receipt: replaying the
    start checkpoint would otherwise let a runtime claim that the terminal
    event was flushed when no durable event advanced at all.
    """

    compare_native_dsh_checkpoints(previous, current)
    prior = normalize_native_dsh_checkpoint(previous)
    observed = normalize_native_dsh_checkpoint(current)
    if (
        observed["revision"] == prior["revision"]
        or observed["flush_digest"] == prior["flush_digest"]
    ):
        _fail("DSH终态检查点修订没有前进", "stale_context")
    if observed["event_count"] <= prior["event_count"]:
        _fail("DSH终态检查点没有持久化新事件", "stale_context")
    prior_seq = prior["last_event_seq"]
    observed_seq = observed["last_event_seq"]
    if prior_seq is not None and (
        observed_seq is None or observed_seq <= prior_seq
    ):
        _fail("DSH终态检查点事件序号没有前进", "stale_context")
