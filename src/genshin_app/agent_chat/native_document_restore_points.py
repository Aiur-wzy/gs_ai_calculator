"""Durable, document-owned restore points for the native Agent workbench.

This is deliberately separate from the execution ``domain_checkpoint``.  It
stores immutable document/application payload artifacts and a small restore
transaction journal.  Router and Renderer integration belongs to a higher
layer; this module performs no provider, chat, file-export, or GUI work.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import secrets
import stat
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from genshin_app.calculation_capacity import (
    CALCULATED_ARTIFACT_BYTES,
    CALCULATION_DELIVERY_TOTAL_BYTES,
)
from genshin_app.equipment.files import json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_db.locking import file_lock

from .sessions import safe_path, write_owned

STORE_CONTRACT = "genshin-agent-document-checkpoint-store-v1"
CHECKPOINT_CONTRACT = "genshin-agent-document-checkpoint-v1"
CHECKPOINT_TYPE = "document_restore_point"
ARTIFACT_CONTRACT = "genshin-agent-document-restore-artifact-v1"
RESTORE_INTENT_CONTRACT = "genshin-agent-document-restore-intent-v1"
RESTORE_RECEIPT_CONTRACT = "genshin-agent-document-restore-receipt-v1"
QUEUE_REBIND_CONTRACT = "genshin-agent-restore-queue-rebind-v1"

STORE_FILE = "document-restore-points-v1.json"
LOCK_FILE = ".document-restore-points-v1.lock"
MAX_DOCUMENT_BYTES = CALCULATED_ARTIFACT_BYTES
MAX_APPLICATION_PAYLOAD_BYTES = CALCULATION_DELIVERY_TOTAL_BYTES
MAX_ARTIFACT_OVERHEAD_BYTES = 4096
# The frozen 505-hit gen17 package contains 5,466,125 plain-JSON nodes; its
# real restore payload also exceeds the old 2,000,000-node scan ceiling.
# Keep this independent from byte/depth checks and Agent/DSH work budgets.
MAX_STRUCTURE_NODES = 8_000_000
MAX_STRUCTURE_DEPTH = 64
MAX_STORE_BYTES = 4 * 1024 * 1024
MAX_CHECKPOINTS = 256
MAX_RESTORE_INTENTS = 128
MAX_QUEUE_REVISIONS = 2048
MAX_AUTOMATIC_CHECKPOINTS = 32
MAX_AUTOMATIC_ARTIFACT_BYTES = 512 * 1024 * 1024
MAX_TERMINAL_RESTORE_INTENTS = 32
MAX_RESTORE_RECEIPTS = 128
MAX_RESTORE_RECEIPT_ARTIFACT_BYTES = 512 * 1024 * 1024
MAX_SAFE_INTEGER = 2**53 - 1
_EXPIRED_FILTER_BITS = 262_144
_EXPIRED_FILTER_HASHES = 4
_EXPIRED_FILTER_HEX_LENGTH = _EXPIRED_FILTER_BITS // 4

_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_CHECKPOINT_ID = re.compile(r"checkpoint_[A-Za-z0-9_-]{16,64}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ARTIFACT_FILE = re.compile(
    r"(?:document-body|application-payload)-[0-9a-f]{64}\.json\Z"
)
_WINDOWS_PATH = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/])")
_UNC_PATH = re.compile(r"(?<![:/\\A-Za-z0-9])(?:\\\\|//)[^/]", re.ASCII)
_POSIX_PATH = re.compile(
    r"(?i)(?<![:/\\A-Za-z0-9_])/(?:Users|home|etc|tmp|var|opt|root|mnt|"
    r"Volumes|private|usr|dev|proc|sys|run|srv|workspace)(?:/|\\|\Z)"
)
_FILE_URI = re.compile(r"(?i)\bfile:(?://)?")
_SECRET_VALUE = re.compile(
    r"(?ix)(?:\bsk-[A-Za-z0-9_-]{8,}|\bbearer\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"\b(?:authorization|proxy-authorization)\s*:\s*[^\s]+)"
)
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "authorization",
        "proxy_authorization",
        "cookie",
        "set_cookie",
        "password",
        "passwd",
        "pwd",
        "secret",
        "credential",
        "writer_fence",
        "capability_token",
        "raw_reasoning",
    }
)
_KINDS = frozenset({"whiteboard", "native_plan"})
_ARTIFACT_KINDS = frozenset({"document", "application_payload"})
_STATES = frozenset(
    {
        "prepared",
        "renderer_applied",
        "baseline_advanced",
        "committed",
        "aborted",
        "recovery_required",
        "outcome_unknown",
    }
)
_TERMINAL_STATES = frozenset({"committed", "aborted"})
_PROVEN_STATES = frozenset(
    {"prepared", "renderer_applied", "baseline_advanced", "committed", "aborted"}
)
_GUARD_KEYS = frozenset(
    {
        "active_turn",
        "unsettled_write",
        "candidate_application_intent",
        "app_action",
        "outcome_unknown",
        "writer_takeover",
        "inbox_promoting_or_admitted",
    }
)
_QUEUE_STATES = frozenset(
    {"queued", "held", "held_action_required", "held_failure", "needs_rebind"}
)
_INVALIDATED_KINDS = (
    "last_applied_proof",
    "candidate_receipt",
    "calculated_capability",
    "writer_authorization",
    "tool_authorization",
)


def _fail(message: str, reason: str = "document_restore_invalid", details=None) -> None:
    raise PlanError(message, reason, details)


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _read_strict_json(path: Path, maximum: int, message: str) -> object:
    try:
        with path.open("rb") as stream:
            raw = stream.read(maximum + 1)
        if len(raw) > maximum:
            _fail(message, "agent_size_limit")
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except PlanError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError):
        _fail(message, "document_restore_integrity")


def _identity(value: object, name: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        _fail(f"{name}不合法")
    if _SECRET_VALUE.search(value):
        _fail(f"{name}包含凭据", "document_restore_sensitive")
    return value


def _optional_identity(value: object, name: str) -> str | None:
    return None if value is None else _identity(value, name)


def _counter(value: object, name: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SAFE_INTEGER:
        _fail(f"{name}不合法")
    return value


def _hash(value: object, name: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        _fail(f"{name}不合法")
    return value


def _timestamp(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) > 64:
        _fail(f"{name}不合法")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _fail(f"{name}不合法")
    if parsed.tzinfo is None:
        _fail(f"{name}缺少时区")
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _new_checkpoint_id() -> str:
    return "checkpoint_" + secrets.token_urlsafe(18)


def _digest_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _expired_filter(value: object) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) != _EXPIRED_FILTER_HEX_LENGTH
        or re.fullmatch(r"[0-9a-f]+", value) is None
    ):
        _fail("过期restore身份过滤器不合法", "document_restore_integrity")
    return value


def _expired_filter_indexes(kind: str, identity: str) -> tuple[int, ...]:
    digest = hashlib.sha256(f"{kind}\0{identity}".encode()).digest()
    return tuple(
        int.from_bytes(digest[index * 4 : index * 4 + 4], "big")
        % _EXPIRED_FILTER_BITS
        for index in range(_EXPIRED_FILTER_HASHES)
    )


def _expired_filter_contains(value: str | None, kind: str, identity: str) -> bool:
    if value is None:
        return False
    bits = bytes.fromhex(value)
    return all(
        bits[index // 8] & (1 << (index % 8))
        for index in _expired_filter_indexes(kind, identity)
    )


def _expired_filter_add(value: str | None, kind: str, identity: str) -> str:
    bits = bytearray(
        _EXPIRED_FILTER_BITS // 8 if value is None else bytes.fromhex(value)
    )
    for index in _expired_filter_indexes(kind, identity):
        bits[index // 8] |= 1 << (index % 8)
    return bits.hex()


def _safe_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 128
        or any(ord(character) < 0x20 for character in value)
    ):
        _fail("恢复点名称不合法")
    checked = value.strip()
    _scan_sensitive(checked)
    return checked


def _scan_sensitive(value: object) -> None:
    """Reject credentials and host-local paths without rejecting JSON pointers.

    Relative upstream provenance such as ``BinOutput/Foo.json`` and JSON
    pointers are valid domain data.  Absolute host paths and file URIs are not.
    """

    stack: list[tuple[object, int]] = [(value, 0)]
    visited = 0
    while stack:
        item, depth = stack.pop()
        visited += 1
        if depth > MAX_STRUCTURE_DEPTH or visited > MAX_STRUCTURE_NODES:
            _fail("恢复点内容结构超过安全上限", "agent_size_limit")
        if isinstance(item, Mapping):
            for key, child in item.items():
                if not isinstance(key, str):
                    _fail("恢复点JSON对象键必须为字符串")
                normalized = key.strip().lower().replace("-", "_")
                if normalized in _SENSITIVE_KEYS:
                    _fail("恢复点内容包含凭据字段", "document_restore_sensitive")
                stack.append((child, depth + 1))
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            if "\x00" in item or _SECRET_VALUE.search(item):
                _fail("恢复点内容包含凭据", "document_restore_sensitive")
            if any(
                pattern.search(item) is not None
                for pattern in (_FILE_URI, _WINDOWS_PATH, _UNC_PATH, _POSIX_PATH)
            ):
                _fail("恢复点内容包含本地路径", "document_restore_sensitive")
        elif item is not None and type(item) not in {bool, int, float}:
            _fail("恢复点内容不是JSON值")


def _json_mapping(value: object, name: str, maximum: int) -> tuple[dict[str, Any], bytes]:
    if not isinstance(value, Mapping):
        _fail(f"{name}必须是JSON对象")
    checked = copy.deepcopy(dict(value))
    _scan_sensitive(checked)
    try:
        raw = json_bytes(checked)
    except (TypeError, ValueError, RecursionError) as exc:
        raise PlanError(f"{name}不是严格JSON", "document_restore_invalid") from exc
    if len(raw) > maximum:
        _fail(f"{name}超过{maximum}字节输入上限", "agent_size_limit")
    return checked, raw


def _fingerprints(value: object) -> dict[str, str | None]:
    if not isinstance(value, Mapping) or set(value) != {
        "database",
        "engine",
        "schema",
    }:
        _fail("运行身份字段不完整")
    return {
        name: None if value[name] is None else _hash(value[name], f"{name}_fingerprint")
        for name in ("database", "engine", "schema")
    }


def _source(value: object) -> dict[str, str | None]:
    if not isinstance(value, Mapping) or set(value) != {
        "conversation_id",
        "turn_id",
        "event_cursor",
    }:
        _fail("恢复点来源字段不完整")
    return {
        name: _optional_identity(value[name], f"source_{name}")
        for name in ("conversation_id", "turn_id", "event_cursor")
    }


def _artifact_ref(value: object, expected_kind: str | None = None) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "artifact_kind",
        "sha256",
        "byte_length",
    }:
        _fail("恢复点工件引用不合法")
    kind = value["artifact_kind"]
    if kind not in _ARTIFACT_KINDS or expected_kind is not None and kind != expected_kind:
        _fail("恢复点工件类型不合法")
    maximum = (
        MAX_DOCUMENT_BYTES if kind == "document" else MAX_APPLICATION_PAYLOAD_BYTES
    )
    length = _counter(value["byte_length"], "artifact_byte_length")
    if length > maximum:
        _fail("恢复点工件超过输入上限", "agent_size_limit")
    return {
        "artifact_kind": kind,
        "sha256": _hash(value["sha256"], "artifact_sha256"),
        "byte_length": length,
    }


def _artifact_name(ref: Mapping[str, Any]) -> str:
    prefix = "document-body" if ref["artifact_kind"] == "document" else "application-payload"
    return f"{prefix}-{ref['sha256']}.json"


def _current(value: object, *, document_id: str) -> dict[str, Any]:
    expected = {
        "document_id",
        "document_kind",
        "document_lineage_epoch",
        "revision",
        "document_artifact",
        "application_payload_artifact",
        "fingerprints",
        "updated_at",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        _fail("当前文档基线字段不合法")
    if value["document_id"] != document_id or value["document_kind"] not in _KINDS:
        _fail("当前文档所有权不合法")
    return {
        "document_id": document_id,
        "document_kind": value["document_kind"],
        "document_lineage_epoch": _counter(
            value["document_lineage_epoch"], "document_lineage_epoch"
        ),
        "revision": _counter(value["revision"], "document_revision"),
        "document_artifact": _artifact_ref(value["document_artifact"], "document"),
        "application_payload_artifact": _artifact_ref(
            value["application_payload_artifact"], "application_payload"
        ),
        "fingerprints": _fingerprints(value["fingerprints"]),
        "updated_at": _timestamp(value["updated_at"], "current_updated_at"),
    }


def _checkpoint(value: object, *, document_id: str) -> dict[str, Any]:
    expected = {
        "contract",
        "type",
        "checkpoint_id",
        "create_request_id",
        "creation_digest",
        "original_name",
        "name",
        "automatic",
        "reason",
        "document_id",
        "document_kind",
        "document_lineage_epoch",
        "revision",
        "document_artifact",
        "application_payload_artifact",
        "fingerprints",
        "source",
        "created_at",
        "renamed_at",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        _fail("恢复点记录字段不合法")
    checkpoint_id = value["checkpoint_id"]
    if not isinstance(checkpoint_id, str) or _CHECKPOINT_ID.fullmatch(checkpoint_id) is None:
        _fail("checkpoint_id不合法")
    if (
        value["contract"] != CHECKPOINT_CONTRACT
        or value["type"] != CHECKPOINT_TYPE
        or value["document_id"] != document_id
        or value["document_kind"] not in _KINDS
        or type(value["automatic"]) is not bool
    ):
        _fail("恢复点记录身份不合法")
    reason = _identity(value["reason"], "checkpoint_reason")
    renamed_at = value["renamed_at"]
    if renamed_at is not None:
        renamed_at = _timestamp(renamed_at, "checkpoint_renamed_at")
    result = {
        "contract": CHECKPOINT_CONTRACT,
        "type": CHECKPOINT_TYPE,
        "checkpoint_id": checkpoint_id,
        "create_request_id": _identity(value["create_request_id"], "create_request_id"),
        "creation_digest": _hash(value["creation_digest"], "creation_digest"),
        "original_name": _safe_name(value["original_name"]),
        "name": _safe_name(value["name"]),
        "automatic": value["automatic"],
        "reason": reason,
        "document_id": document_id,
        "document_kind": value["document_kind"],
        "document_lineage_epoch": _counter(
            value["document_lineage_epoch"], "checkpoint_lineage_epoch"
        ),
        "revision": _counter(value["revision"], "checkpoint_revision"),
        "document_artifact": _artifact_ref(value["document_artifact"], "document"),
        "application_payload_artifact": _artifact_ref(
            value["application_payload_artifact"], "application_payload"
        ),
        "fingerprints": _fingerprints(value["fingerprints"]),
        "source": _source(value["source"]),
        "created_at": _timestamp(value["created_at"], "checkpoint_created_at"),
        "renamed_at": renamed_at,
    }
    expected_creation_digest = _digest_bytes(
        json_bytes(
            {
                "request_id": result["create_request_id"],
                "name": result["original_name"],
                "automatic": result["automatic"],
                "reason": result["reason"],
                "source": result["source"],
                "document_lineage_epoch": result["document_lineage_epoch"],
                "revision": result["revision"],
                "document_digest": result["document_artifact"]["sha256"],
                "application_payload_digest": result[
                    "application_payload_artifact"
                ]["sha256"],
            }
        )
    )
    if result["creation_digest"] != expected_creation_digest:
        _fail("恢复点创建身份摘要不匹配", "document_restore_integrity")
    return result


def _queue_revisions(value: object) -> list[dict[str, Any]]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) > MAX_QUEUE_REVISIONS
    ):
        _fail("Queue revision集合不合法")
    result = []
    seen = set()
    for row in value:
        if not isinstance(row, Mapping) or set(row) != {
            "queue_item_id",
            "revision",
            "document_lineage_epoch",
            "document_revision",
            "state",
        }:
            _fail("Queue revision记录字段不合法")
        queue_id = _identity(row["queue_item_id"], "queue_item_id")
        if queue_id in seen or row["state"] not in _QUEUE_STATES:
            _fail("Queue revision记录重复或状态不合法")
        seen.add(queue_id)
        result.append(
            {
                "queue_item_id": queue_id,
                "revision": _counter(row["revision"], "queue_revision"),
                "document_lineage_epoch": _counter(
                    row["document_lineage_epoch"], "queue_document_lineage_epoch"
                ),
                "document_revision": _counter(
                    row["document_revision"], "queue_document_revision"
                ),
                "state": row["state"],
            }
        )
    return sorted(result, key=lambda row: row["queue_item_id"])


def _authorization_invalidation(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    expected = {
        "old_lineage_authorizations_valid",
        "invalidated_lineage_epoch",
        "current_lineage_epoch",
        "invalidated_kinds",
        "queue_items_needing_rebind",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        _fail("旧lineage授权失效结果不合法")
    if value["old_lineage_authorizations_valid"] is not False:
        _fail("旧lineage授权必须显式失效")
    kinds = value["invalidated_kinds"]
    queue_ids = value["queue_items_needing_rebind"]
    if kinds != list(_INVALIDATED_KINDS) or not isinstance(queue_ids, list):
        _fail("旧lineage授权失效范围不合法")
    checked_ids = [_identity(item, "queue_item_id") for item in queue_ids]
    if len(set(checked_ids)) != len(checked_ids):
        _fail("旧lineage Queue失效记录重复")
    return {
        "old_lineage_authorizations_valid": False,
        "invalidated_lineage_epoch": _counter(
            value["invalidated_lineage_epoch"], "invalidated_lineage_epoch"
        ),
        "current_lineage_epoch": _counter(
            value["current_lineage_epoch"], "current_lineage_epoch"
        ),
        "invalidated_kinds": list(_INVALIDATED_KINDS),
        "queue_items_needing_rebind": checked_ids,
    }


def _intent(value: object) -> dict[str, Any]:
    expected = {
        "contract",
        "intent_id",
        "request_id",
        "state",
        "last_proven_state",
        "target_checkpoint_id",
        "before_restore_checkpoint_id",
        "source_lineage_epoch",
        "source_revision",
        "target_lineage_epoch",
        "target_revision",
        "result_lineage_epoch",
        "result_revision",
        "pre_document_digest",
        "pre_application_payload_digest",
        "target_document_digest",
        "target_application_payload_digest",
        "queue_revisions",
        "queue_revision_digest",
        "created_at",
        "updated_at",
        "renderer_receipt_id",
        "baseline_receipt_id",
        "commit_receipt_id",
        "abort_receipt_id",
        "queue_rebind_receipt_digest",
        "last_recovery_evidence_id",
        "last_recovery_identity_digest",
        "last_recovery_result",
        "recovery_authorization_digest",
        "recovery_reason",
        "outcome_unknown_reason",
        "authorization_invalidation",
    }
    if not isinstance(value, Mapping) or frozenset(value) not in {
        frozenset(expected),
        frozenset(expected | {"source"}),
    }:
        _fail("restore intent字段不合法")
    if value["contract"] != RESTORE_INTENT_CONTRACT:
        _fail("restore intent合同不合法")
    state = value["state"]
    last_proven = value["last_proven_state"]
    if state not in _STATES or last_proven not in _PROVEN_STATES:
        _fail("restore intent状态不合法")
    queue = _queue_revisions(value["queue_revisions"])
    if _digest_bytes(json_bytes(queue)) != value["queue_revision_digest"]:
        _fail("restore intent Queue摘要不匹配", "document_restore_integrity")
    result = {
        "contract": RESTORE_INTENT_CONTRACT,
        "intent_id": _identity(value["intent_id"], "intent_id"),
        "request_id": _identity(value["request_id"], "restore_request_id"),
        # ``source`` was added before the first release.  Accepting a missing
        # value keeps dev stores readable, but every normalized/write path now
        # materializes the field so exact request replay can bind provenance.
        "source": _source(
            value.get("source")
            or {"conversation_id": None, "turn_id": None, "event_cursor": None}
        ),
        "state": state,
        "last_proven_state": last_proven,
        "target_checkpoint_id": value["target_checkpoint_id"],
        "before_restore_checkpoint_id": value["before_restore_checkpoint_id"],
        "source_lineage_epoch": _counter(
            value["source_lineage_epoch"], "source_lineage_epoch"
        ),
        "source_revision": _counter(value["source_revision"], "source_revision"),
        "target_lineage_epoch": _counter(
            value["target_lineage_epoch"], "target_lineage_epoch"
        ),
        "target_revision": _counter(value["target_revision"], "target_revision"),
        "result_lineage_epoch": _counter(
            value["result_lineage_epoch"], "result_lineage_epoch"
        ),
        "result_revision": _counter(value["result_revision"], "result_revision"),
        "pre_document_digest": _hash(
            value["pre_document_digest"], "pre_document_digest"
        ),
        "pre_application_payload_digest": _hash(
            value["pre_application_payload_digest"],
            "pre_application_payload_digest",
        ),
        "target_document_digest": _hash(
            value["target_document_digest"], "target_document_digest"
        ),
        "target_application_payload_digest": _hash(
            value["target_application_payload_digest"],
            "target_application_payload_digest",
        ),
        "queue_revisions": queue,
        "queue_revision_digest": _hash(
            value["queue_revision_digest"], "queue_revision_digest"
        ),
        "created_at": _timestamp(value["created_at"], "restore_created_at"),
        "updated_at": _timestamp(value["updated_at"], "restore_updated_at"),
        "renderer_receipt_id": _optional_identity(
            value["renderer_receipt_id"], "renderer_receipt_id"
        ),
        "baseline_receipt_id": _optional_identity(
            value["baseline_receipt_id"], "baseline_receipt_id"
        ),
        "commit_receipt_id": _optional_identity(
            value["commit_receipt_id"], "commit_receipt_id"
        ),
        "abort_receipt_id": _optional_identity(
            value["abort_receipt_id"], "abort_receipt_id"
        ),
        "queue_rebind_receipt_digest": (
            None
            if value["queue_rebind_receipt_digest"] is None
            else _hash(
                value["queue_rebind_receipt_digest"],
                "queue_rebind_receipt_digest",
            )
        ),
        "last_recovery_evidence_id": _optional_identity(
            value["last_recovery_evidence_id"], "last_recovery_evidence_id"
        ),
        "last_recovery_identity_digest": (
            None
            if value["last_recovery_identity_digest"] is None
            else _hash(
                value["last_recovery_identity_digest"],
                "last_recovery_identity_digest",
            )
        ),
        "last_recovery_result": (
            None
            if value["last_recovery_result"] is None
            else _identity(value["last_recovery_result"], "last_recovery_result")
        ),
        "recovery_authorization_digest": (
            None
            if value["recovery_authorization_digest"] is None
            else _hash(
                value["recovery_authorization_digest"],
                "recovery_authorization_digest",
            )
        ),
        "recovery_reason": _optional_identity(
            value["recovery_reason"], "recovery_reason"
        ),
        "outcome_unknown_reason": _optional_identity(
            value["outcome_unknown_reason"], "outcome_unknown_reason"
        ),
        "authorization_invalidation": _authorization_invalidation(
            value["authorization_invalidation"]
        ),
    }
    for key in ("target_checkpoint_id", "before_restore_checkpoint_id"):
        if not isinstance(result[key], str) or _CHECKPOINT_ID.fullmatch(result[key]) is None:
            _fail(f"{key}不合法")
    if result["result_lineage_epoch"] <= result["source_lineage_epoch"]:
        _fail("恢复后的lineage必须单调推进")
    if result["result_revision"] <= result["source_revision"]:
        _fail("恢复后的revision必须单调推进")
    if state == "committed" and result["authorization_invalidation"] is None:
        _fail("已提交恢复缺少旧lineage授权失效结果")
    if state == "aborted" and result["abort_receipt_id"] is None:
        _fail("已取消恢复缺少回执")
    if state in _PROVEN_STATES and state != last_proven:
        _fail("restore intent当前状态与已证明阶段不一致")
    if last_proven in {"renderer_applied", "baseline_advanced", "committed"} and (
        result["renderer_receipt_id"] is None
    ):
        _fail("restore intent缺少Renderer证明")
    if last_proven in {"baseline_advanced", "committed"} and (
        result["baseline_receipt_id"] is None
    ):
        _fail("restore intent缺少baseline证明")
    if last_proven == "committed" and result["commit_receipt_id"] is None:
        _fail("restore intent缺少commit证明")
    if (
        state == "committed"
        and result["queue_revisions"]
        and result["queue_rebind_receipt_digest"] is None
    ):
        _fail("已提交恢复缺少Queue改绑回执")
    invalidation = result["authorization_invalidation"]
    if invalidation is not None and (
        invalidation["invalidated_lineage_epoch"]
        != result["source_lineage_epoch"]
        or invalidation["current_lineage_epoch"]
        != result["result_lineage_epoch"]
        or invalidation["queue_items_needing_rebind"]
        != [row["queue_item_id"] for row in result["queue_revisions"]]
    ):
        _fail("旧lineage授权失效结果与restore intent不匹配")
    recovery_fields = (
        result["last_recovery_evidence_id"],
        result["last_recovery_identity_digest"],
        result["last_recovery_result"],
    )
    if any(item is None for item in recovery_fields) != all(
        item is None for item in recovery_fields
    ):
        _fail("restore recovery回执字段不完整")
    return result


def _restore_receipt(value: object) -> dict[str, Any]:
    expected = {
        "contract",
        "intent_id",
        "request_id",
        "terminal_state",
        "target_checkpoint_identity",
        "before_restore_checkpoint_identity",
        "pre_document_artifact",
        "pre_application_payload_artifact",
        "target_document_artifact",
        "target_application_payload_artifact",
        "queue_revision_digest",
        "result_lineage_epoch",
        "result_revision",
        "renderer_receipt_id",
        "baseline_receipt_id",
        "commit_receipt_id",
        "abort_receipt_id",
        "queue_rebind_receipt_digest",
        "authorization_invalidation",
        "completed_at",
    }
    if not isinstance(value, Mapping) or frozenset(value) not in {
        frozenset(expected),
        frozenset(expected | {"source"}),
    }:
        _fail("restore receipt字段不合法")
    if value["contract"] != RESTORE_RECEIPT_CONTRACT:
        _fail("restore receipt合同不合法")
    terminal_state = value["terminal_state"]
    if terminal_state not in _TERMINAL_STATES:
        _fail("restore receipt不是终态")
    result = {
        "contract": RESTORE_RECEIPT_CONTRACT,
        "intent_id": _identity(value["intent_id"], "intent_id"),
        "request_id": _identity(value["request_id"], "restore_request_id"),
        "source": _source(
            value.get("source")
            or {"conversation_id": None, "turn_id": None, "event_cursor": None}
        ),
        "terminal_state": terminal_state,
        "target_checkpoint_identity": value["target_checkpoint_identity"],
        "before_restore_checkpoint_identity": value[
            "before_restore_checkpoint_identity"
        ],
        "pre_document_artifact": _artifact_ref(
            value["pre_document_artifact"], "document"
        ),
        "pre_application_payload_artifact": _artifact_ref(
            value["pre_application_payload_artifact"], "application_payload"
        ),
        "target_document_artifact": _artifact_ref(
            value["target_document_artifact"], "document"
        ),
        "target_application_payload_artifact": _artifact_ref(
            value["target_application_payload_artifact"], "application_payload"
        ),
        "queue_revision_digest": _hash(
            value["queue_revision_digest"], "queue_revision_digest"
        ),
        "result_lineage_epoch": _counter(
            value["result_lineage_epoch"], "result_lineage_epoch"
        ),
        "result_revision": _counter(value["result_revision"], "result_revision"),
        "renderer_receipt_id": _optional_identity(
            value["renderer_receipt_id"], "renderer_receipt_id"
        ),
        "baseline_receipt_id": _optional_identity(
            value["baseline_receipt_id"], "baseline_receipt_id"
        ),
        "commit_receipt_id": _optional_identity(
            value["commit_receipt_id"], "commit_receipt_id"
        ),
        "abort_receipt_id": _optional_identity(
            value["abort_receipt_id"], "abort_receipt_id"
        ),
        "queue_rebind_receipt_digest": (
            None
            if value["queue_rebind_receipt_digest"] is None
            else _hash(
                value["queue_rebind_receipt_digest"],
                "queue_rebind_receipt_digest",
            )
        ),
        "authorization_invalidation": _authorization_invalidation(
            value["authorization_invalidation"]
        ),
        "completed_at": _timestamp(value["completed_at"], "receipt_completed_at"),
    }
    for key in (
        "target_checkpoint_identity",
        "before_restore_checkpoint_identity",
    ):
        checkpoint_id = result[key]
        if (
            not isinstance(checkpoint_id, str)
            or _CHECKPOINT_ID.fullmatch(checkpoint_id) is None
        ):
            _fail(f"{key}不合法")
    if (
        result["pre_document_artifact"]["sha256"]
        != value["pre_document_artifact"]["sha256"]
        or result["pre_application_payload_artifact"]["sha256"]
        != value["pre_application_payload_artifact"]["sha256"]
    ):
        _fail("restore receipt恢复前工件不合法", "document_restore_integrity")
    if terminal_state == "committed":
        if (
            result["renderer_receipt_id"] is None
            or result["baseline_receipt_id"] is None
            or result["commit_receipt_id"] is None
            or result["abort_receipt_id"] is not None
            or result["authorization_invalidation"] is None
        ):
            _fail("已提交restore receipt证明不完整")
        queue_was_present = bool(
            result["authorization_invalidation"]["queue_items_needing_rebind"]
        )
        if queue_was_present != (result["queue_rebind_receipt_digest"] is not None):
            _fail("restore receipt Queue证明不完整")
    elif (
        result["abort_receipt_id"] is None
        or result["renderer_receipt_id"] is not None
        or result["baseline_receipt_id"] is not None
        or result["commit_receipt_id"] is not None
        or result["queue_rebind_receipt_digest"] is not None
        or result["authorization_invalidation"] is not None
    ):
        _fail("已取消restore receipt证明不合法")
    return result


def _validate_store(value: object, *, document_id: str) -> dict[str, Any]:
    required = {
        "contract",
        "document_id",
        "store_revision",
        "current",
        "checkpoints",
        "restore_intents",
    }
    allowed = required | {"restore_receipts", "expired_restore_filter"}
    if (
        not isinstance(value, Mapping)
        or not required <= set(value)
        or not set(value) <= allowed
    ):
        _fail("恢复点存储字段不合法")
    if value["contract"] != STORE_CONTRACT or value["document_id"] != document_id:
        _fail("恢复点存储所有权不合法")
    checkpoints_value = value["checkpoints"]
    intents_value = value["restore_intents"]
    receipts_value = value.get("restore_receipts", [])
    if (
        not isinstance(checkpoints_value, list)
        or len(checkpoints_value) > MAX_CHECKPOINTS
        or not isinstance(intents_value, list)
        or len(intents_value) > MAX_RESTORE_INTENTS
        or not isinstance(receipts_value, list)
        or len(receipts_value) > MAX_RESTORE_RECEIPTS
    ):
        _fail("恢复点存储记录数超过上限", "agent_size_limit")
    checkpoints = [_checkpoint(item, document_id=document_id) for item in checkpoints_value]
    intents = [_intent(item) for item in intents_value]
    receipts = [_restore_receipt(item) for item in receipts_value]
    checkpoint_ids = [item["checkpoint_id"] for item in checkpoints]
    if len(set(checkpoint_ids)) != len(checkpoint_ids):
        _fail("checkpoint_id重复")
    if len({item["create_request_id"] for item in checkpoints}) != len(checkpoints):
        _fail("恢复点创建请求重复")
    if len({item["intent_id"] for item in intents}) != len(intents):
        _fail("restore intent重复")
    if len({item["request_id"] for item in intents}) != len(intents):
        _fail("restore request重复")
    if len({item["intent_id"] for item in receipts}) != len(receipts):
        _fail("restore receipt intent重复")
    if len({item["request_id"] for item in receipts}) != len(receipts):
        _fail("restore receipt request重复")
    if {item["intent_id"] for item in intents} & {
        item["intent_id"] for item in receipts
    } or {item["request_id"] for item in intents} & {
        item["request_id"] for item in receipts
    }:
        _fail("restore intent与receipt身份重复")
    if sum(item["state"] not in _TERMINAL_STATES for item in intents) > 1:
        _fail("存在多个未终结restore intent")
    known = set(checkpoint_ids)
    if any(
        item["target_checkpoint_id"] not in known
        or item["before_restore_checkpoint_id"] not in known
        for item in intents
    ):
        _fail("restore intent引用未知恢复点")
    checkpoints_by_id = {item["checkpoint_id"]: item for item in checkpoints}
    for intent in intents:
        target = checkpoints_by_id[intent["target_checkpoint_id"]]
        before = checkpoints_by_id[intent["before_restore_checkpoint_id"]]
        if (
            intent["target_document_digest"]
            != target["document_artifact"]["sha256"]
            or intent["target_application_payload_digest"]
            != target["application_payload_artifact"]["sha256"]
            or intent["target_lineage_epoch"] != target["document_lineage_epoch"]
            or intent["target_revision"] != target["revision"]
            or intent["pre_document_digest"]
            != before["document_artifact"]["sha256"]
            or intent["pre_application_payload_digest"]
            != before["application_payload_artifact"]["sha256"]
            or intent["source_lineage_epoch"]
            != before["document_lineage_epoch"]
            or intent["source_revision"] != before["revision"]
        ):
            _fail("restore intent与恢复点工件绑定不一致", "document_restore_integrity")
    checked = {
        "contract": STORE_CONTRACT,
        "document_id": document_id,
        "store_revision": _counter(value["store_revision"], "store_revision"),
        "current": _current(value["current"], document_id=document_id),
        "checkpoints": checkpoints,
        "restore_intents": intents,
        "restore_receipts": receipts,
        "expired_restore_filter": _expired_filter(
            value.get("expired_restore_filter")
        ),
    }
    artifact_lengths: dict[tuple[str, str], int] = {}
    artifact_owners = [
        (checked["current"], ("document_artifact", "application_payload_artifact")),
        *((item, ("document_artifact", "application_payload_artifact")) for item in checkpoints),
        *((
            item,
            (
                "pre_document_artifact",
                "pre_application_payload_artifact",
                "target_document_artifact",
                "target_application_payload_artifact",
            ),
        ) for item in receipts),
    ]
    for owner, keys in artifact_owners:
        for key in keys:
            ref = owner[key]
            identity = (ref["artifact_kind"], ref["sha256"])
            previous_length = artifact_lengths.setdefault(identity, ref["byte_length"])
            if previous_length != ref["byte_length"]:
                _fail(
                    "同一内容寻址工件存在冲突长度",
                    "document_restore_integrity",
                )
    if len(json_bytes(checked)) > MAX_STORE_BYTES:
        _fail("恢复点元数据超过4MiB上限", "agent_size_limit")
    return checked


def _guards(value: object) -> dict[str, bool]:
    if not isinstance(value, Mapping) or set(value) != _GUARD_KEYS:
        _fail("恢复前守卫快照字段不完整", "restore_guard_invalid")
    checked = {}
    for key in sorted(_GUARD_KEYS):
        if type(value[key]) is not bool:
            _fail("恢复前守卫快照不合法", "restore_guard_invalid")
        checked[key] = value[key]
    return checked


def _queue_receipt(
    value: object,
    *,
    queue: list[dict[str, Any]],
    result_lineage_epoch: int,
    result_revision: int,
) -> dict[str, Any] | None:
    if not queue and value is None:
        return None
    expected = {
        "contract",
        "queue_revision_digest",
        "result_lineage_epoch",
        "result_revision",
        "rebound",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        _fail("Queue改绑回执缺失或不合法", "restore_queue_transaction_required")
    rebound = value["rebound"]
    if not isinstance(rebound, list) or len(rebound) != len(queue):
        _fail("Queue改绑回执没有完整覆盖revision集合", "restore_queue_conflict")
    checked_rebound = []
    for item in rebound:
        if not isinstance(item, Mapping) or set(item) != {
            "queue_item_id",
            "from_revision",
            "to_revision",
            "state",
        }:
            _fail("Queue改绑回执字段不合法", "restore_queue_conflict")
        if item["state"] != "needs_rebind":
            _fail("Queue改绑结果必须为needs_rebind", "restore_queue_conflict")
        checked_rebound.append(
            {
                "queue_item_id": _identity(item["queue_item_id"], "queue_item_id"),
                "from_revision": _counter(item["from_revision"], "from_queue_revision"),
                "to_revision": _counter(item["to_revision"], "to_queue_revision"),
                "state": "needs_rebind",
            }
        )
    checked_rebound.sort(key=lambda item: item["queue_item_id"])
    expected_rows = [
        (row["queue_item_id"], row["revision"]) for row in queue
    ]
    actual_rows = [
        (row["queue_item_id"], row["from_revision"]) for row in checked_rebound
    ]
    if actual_rows != expected_rows or any(
        row["to_revision"] != row["from_revision"] + 1
        for row in checked_rebound
    ):
        _fail("Queue改绑回执revision不匹配", "restore_queue_conflict")
    if (
        value["contract"] != QUEUE_REBIND_CONTRACT
        or value["queue_revision_digest"] != _digest_bytes(json_bytes(queue))
        or value["result_lineage_epoch"] != result_lineage_epoch
        or value["result_revision"] != result_revision
    ):
        _fail("Queue改绑回执身份不匹配", "restore_queue_conflict")
    return {
        "contract": QUEUE_REBIND_CONTRACT,
        "queue_revision_digest": value["queue_revision_digest"],
        "result_lineage_epoch": result_lineage_epoch,
        "result_revision": result_revision,
        "rebound": checked_rebound,
    }


class NativeDocumentRestorePointStore:
    """Document-owned checkpoint store with a recoverable restore journal."""

    def __init__(
        self,
        root: str | Path,
        *,
        document_id: str,
        clock: Callable[[], str] = _now,
        checkpoint_id_factory: Callable[[], str] = _new_checkpoint_id,
    ) -> None:
        self.root = Path(root).absolute()
        self.document_id = _identity(document_id, "document_id")
        self.path = safe_path(self.root, STORE_FILE)
        self.lock_path = safe_path(self.root, LOCK_FILE)
        self._clock = clock
        self._checkpoint_id_factory = checkpoint_id_factory

    def _read_unlocked(self) -> dict[str, Any]:
        path = safe_path(self.root, STORE_FILE)
        if not path.exists():
            _fail("恢复点存储尚未初始化", "document_restore_not_initialized")
        if path.is_symlink() or path.stat().st_size > MAX_STORE_BYTES:
            _fail("恢复点存储不可读取", "agent_storage")
        value = _read_strict_json(path, MAX_STORE_BYTES, "恢复点存储不是严格JSON")
        return _validate_store(value, document_id=self.document_id)

    def _write_unlocked(self, store: dict[str, Any]) -> None:
        checked = _validate_store(store, document_id=self.document_id)
        self.root.mkdir(parents=True, exist_ok=True)
        write_owned(self.root, STORE_FILE, checked, MAX_STORE_BYTES)

    @staticmethod
    def _artifact_refs(store: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        """Return every durable artifact reference, including compact proofs."""

        refs: dict[str, dict[str, Any]] = {}
        owners = [
            (
                store["current"],
                ("document_artifact", "application_payload_artifact"),
            ),
            *((
                item,
                ("document_artifact", "application_payload_artifact"),
            ) for item in store["checkpoints"]),
            *((
                item,
                (
                    "pre_document_artifact",
                    "pre_application_payload_artifact",
                    "target_document_artifact",
                    "target_application_payload_artifact",
                ),
            ) for item in store.get("restore_receipts", [])),
        ]
        for owner, keys in owners:
            for key in keys:
                ref = owner[key]
                name = _artifact_name(ref)
                previous = refs.setdefault(name, ref)
                if previous["byte_length"] != ref["byte_length"]:
                    _fail(
                        "内容寻址工件引用长度冲突",
                        "document_restore_integrity",
                    )
        return refs

    @staticmethod
    def _logical_artifact_bytes(owners: Sequence[Mapping[str, Any]], keys) -> int:
        refs: dict[tuple[str, str], int] = {}
        for owner in owners:
            for key in keys:
                ref = owner[key]
                identity = (ref["artifact_kind"], ref["sha256"])
                previous = refs.setdefault(identity, ref["byte_length"])
                if previous != ref["byte_length"]:
                    _fail(
                        "内容寻址工件引用长度冲突",
                        "document_restore_integrity",
                    )
        return sum(refs.values())

    @classmethod
    def _automatic_usage(
        cls,
        checkpoints: Sequence[Mapping[str, Any]],
        *,
        prospective_current: Mapping[str, Any] | None = None,
    ) -> tuple[int, int]:
        owners = [item for item in checkpoints if item["automatic"]]
        if prospective_current is not None:
            owners.append(prospective_current)
        return (
            len(owners),
            cls._logical_artifact_bytes(
                owners,
                ("document_artifact", "application_payload_artifact"),
            ),
        )

    @classmethod
    def _receipt_artifact_bytes(cls, receipts: Sequence[Mapping[str, Any]]) -> int:
        return cls._logical_artifact_bytes(
            receipts,
            (
                "pre_document_artifact",
                "pre_application_payload_artifact",
                "target_document_artifact",
                "target_application_payload_artifact",
            ),
        )

    @staticmethod
    def _protected_checkpoint_ids(store: Mapping[str, Any]) -> set[str]:
        return {
            checkpoint_id
            for intent in store["restore_intents"]
            for checkpoint_id in (
                intent["target_checkpoint_id"],
                intent["before_restore_checkpoint_id"],
            )
        }

    def _receipt_from_intent_unlocked(
        self,
        store: Mapping[str, Any],
        intent: Mapping[str, Any],
    ) -> dict[str, Any]:
        if intent["state"] not in _TERMINAL_STATES:
            _fail("非终态restore intent不能归档", "restore_state_conflict")
        target = self._find_checkpoint(store, intent["target_checkpoint_id"])
        before = self._find_checkpoint(
            store, intent["before_restore_checkpoint_id"]
        )
        receipt = {
            "contract": RESTORE_RECEIPT_CONTRACT,
            "intent_id": intent["intent_id"],
            "request_id": intent["request_id"],
            "source": copy.deepcopy(intent["source"]),
            "terminal_state": intent["state"],
            "target_checkpoint_identity": intent["target_checkpoint_id"],
            "before_restore_checkpoint_identity": intent[
                "before_restore_checkpoint_id"
            ],
            "pre_document_artifact": copy.deepcopy(before["document_artifact"]),
            "pre_application_payload_artifact": copy.deepcopy(
                before["application_payload_artifact"]
            ),
            "target_document_artifact": copy.deepcopy(target["document_artifact"]),
            "target_application_payload_artifact": copy.deepcopy(
                target["application_payload_artifact"]
            ),
            "queue_revision_digest": intent["queue_revision_digest"],
            "result_lineage_epoch": intent["result_lineage_epoch"],
            "result_revision": intent["result_revision"],
            "renderer_receipt_id": intent["renderer_receipt_id"],
            "baseline_receipt_id": intent["baseline_receipt_id"],
            "commit_receipt_id": intent["commit_receipt_id"],
            "abort_receipt_id": intent["abort_receipt_id"],
            "queue_rebind_receipt_digest": intent["queue_rebind_receipt_digest"],
            "authorization_invalidation": copy.deepcopy(
                intent["authorization_invalidation"]
            ),
            "completed_at": intent["updated_at"],
        }
        return _restore_receipt(receipt)

    def _retire_intent_unlocked(
        self,
        store: dict[str, Any],
        intent: Mapping[str, Any],
        report: dict[str, list[str]],
    ) -> None:
        receipt = self._receipt_from_intent_unlocked(store, intent)
        store["restore_intents"] = [
            item
            for item in store["restore_intents"]
            if item["intent_id"] != intent["intent_id"]
        ]
        store["restore_receipts"].append(receipt)
        report["retired_intent_ids"].append(intent["intent_id"])
        while (
            len(store["restore_receipts"]) > MAX_RESTORE_RECEIPTS
            or self._receipt_artifact_bytes(store["restore_receipts"])
            > MAX_RESTORE_RECEIPT_ARTIFACT_BYTES
        ):
            if len(store["restore_receipts"]) <= 1:
                _fail(
                    "单个restore receipt超过安全保留空间",
                    "agent_size_limit",
                )
            removed = store["restore_receipts"].pop(0)
            store["expired_restore_filter"] = _expired_filter_add(
                store["expired_restore_filter"],
                "request",
                removed["request_id"],
            )
            store["expired_restore_filter"] = _expired_filter_add(
                store["expired_restore_filter"],
                "intent",
                removed["intent_id"],
            )
            report["expired_receipt_intent_ids"].append(removed["intent_id"])

    def _prune_for_new_restore_unlocked(
        self,
        store: dict[str, Any],
        *,
        target_checkpoint_id: str,
    ) -> dict[str, list[str]]:
        """Reserve one intent and one automatic checkpoint without unsafe eviction."""

        if (
            MAX_TERMINAL_RESTORE_INTENTS < 1
            or MAX_AUTOMATIC_CHECKPOINTS < 1
            or MAX_AUTOMATIC_ARTIFACT_BYTES < 1
            or MAX_RESTORE_RECEIPTS < 1
            or MAX_RESTORE_RECEIPT_ARTIFACT_BYTES < 1
        ):
            _fail("恢复点生命周期上限配置不合法", "agent_size_limit")
        report: dict[str, list[str]] = {
            "retired_intent_ids": [],
            "expired_receipt_intent_ids": [],
            "pruned_checkpoint_ids": [],
        }
        terminal = [
            item
            for item in store["restore_intents"]
            if item["state"] in _TERMINAL_STATES
        ]
        while len(terminal) >= MAX_TERMINAL_RESTORE_INTENTS:
            self._retire_intent_unlocked(store, terminal.pop(0), report)

        prospective = store["current"]
        while True:
            automatic_count, automatic_bytes = self._automatic_usage(
                store["checkpoints"], prospective_current=prospective
            )
            if (
                len(store["checkpoints"]) + 1 <= MAX_CHECKPOINTS
                and automatic_count <= MAX_AUTOMATIC_CHECKPOINTS
                and automatic_bytes <= MAX_AUTOMATIC_ARTIFACT_BYTES
            ):
                return report
            protected = self._protected_checkpoint_ids(store) | {
                target_checkpoint_id
            }
            candidate = next(
                (
                    item
                    for item in store["checkpoints"]
                    if item["automatic"]
                    and item["checkpoint_id"] not in protected
                ),
                None,
            )
            if candidate is None:
                retireable = next(
                    (
                        item
                        for item in store["restore_intents"]
                        if item["state"] in _TERMINAL_STATES
                        and target_checkpoint_id
                        not in {
                            item["target_checkpoint_id"],
                            item["before_restore_checkpoint_id"],
                        }
                    ),
                    None,
                )
                if retireable is not None:
                    self._retire_intent_unlocked(store, retireable, report)
                    continue
                _fail(
                    "自动恢复点达到保留上限且均被引用",
                    "document_restore_capacity",
                )
            store["checkpoints"] = [
                item
                for item in store["checkpoints"]
                if item["checkpoint_id"] != candidate["checkpoint_id"]
            ]
            report["pruned_checkpoint_ids"].append(candidate["checkpoint_id"])

    def _garbage_collect_unlocked(
        self, store: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Delete only exact, unreferenced content-addressed files.

        Metadata is always committed before this method is called.  A crash or
        unsafe filesystem entry therefore leaks bytes instead of deleting live
        data.
        """

        referenced = set(self._artifact_refs(store))
        deleted: list[str] = []
        skipped: list[str] = []
        if not self.root.exists():
            return {"deleted": deleted, "skipped": skipped, "deleted_bytes": 0}
        deleted_bytes = 0
        try:
            entries = list(self.root.iterdir())
        except OSError:
            return {"deleted": deleted, "skipped": ["store_root"], "deleted_bytes": 0}
        for entry in entries:
            name = entry.name
            if _ARTIFACT_FILE.fullmatch(name) is None or name in referenced:
                continue
            try:
                candidate = safe_path(self.root, name)
                info = candidate.lstat()
                if (
                    candidate.is_symlink()
                    or not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or getattr(info, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
                ):
                    skipped.append(name)
                    continue
                size = info.st_size
                candidate.unlink()
                deleted.append(name)
                deleted_bytes += size
            except (OSError, PlanError):
                skipped.append(name)
        return {
            "deleted": deleted,
            "skipped": skipped,
            "deleted_bytes": deleted_bytes,
        }

    def _lock(self):
        """Revalidate the lock path on every operation, including hard links."""

        return file_lock(safe_path(self.root, LOCK_FILE))

    @staticmethod
    def _assert_revision(store: Mapping[str, Any], expected: int) -> None:
        _counter(expected, "expected_store_revision")
        if store["store_revision"] != expected:
            _fail("恢复点存储revision已变化", "stale_context")

    def _artifact_ref_unlocked(
        self,
        value: object,
        *,
        kind: str,
    ) -> dict[str, Any]:
        maximum = (
            MAX_DOCUMENT_BYTES if kind == "document" else MAX_APPLICATION_PAYLOAD_BYTES
        )
        checked, raw = _json_mapping(value, kind, maximum)
        digest = _digest_bytes(raw)
        ref = {"artifact_kind": kind, "sha256": digest, "byte_length": len(raw)}
        name = _artifact_name(ref)
        path = safe_path(self.root, name)
        if path.exists():
            observed = self._read_artifact_unlocked(ref)
            if observed != checked:
                _fail("内容寻址工件被篡改", "document_restore_integrity")
            return ref
        envelope = {
            "contract": ARTIFACT_CONTRACT,
            "artifact_kind": kind,
            "sha256": digest,
            "byte_length": len(raw),
            "value": checked,
        }
        write_owned(
            self.root,
            name,
            envelope,
            maximum + MAX_ARTIFACT_OVERHEAD_BYTES,
        )
        self._read_artifact_unlocked(ref)
        return ref

    def _read_artifact_unlocked(self, ref_value: object) -> dict[str, Any]:
        ref = _artifact_ref(ref_value)
        path = safe_path(self.root, _artifact_name(ref))
        maximum = (
            MAX_DOCUMENT_BYTES
            if ref["artifact_kind"] == "document"
            else MAX_APPLICATION_PAYLOAD_BYTES
        )
        if (
            not path.exists()
            or path.is_symlink()
            or path.stat().st_size > maximum + MAX_ARTIFACT_OVERHEAD_BYTES
        ):
            _fail("恢复点工件缺失或超限", "document_restore_integrity")
        envelope = _read_strict_json(
            path,
            maximum + MAX_ARTIFACT_OVERHEAD_BYTES,
            "恢复点工件不是严格JSON",
        )
        if not isinstance(envelope, Mapping) or set(envelope) != {
            "contract",
            "artifact_kind",
            "sha256",
            "byte_length",
            "value",
        }:
            _fail("恢复点工件封套不合法", "document_restore_integrity")
        if (
            envelope["contract"] != ARTIFACT_CONTRACT
            or envelope["artifact_kind"] != ref["artifact_kind"]
            or envelope["sha256"] != ref["sha256"]
            or envelope["byte_length"] != ref["byte_length"]
        ):
            _fail("恢复点工件身份不匹配", "document_restore_integrity")
        checked, raw = _json_mapping(envelope["value"], ref["artifact_kind"], maximum)
        if len(raw) != ref["byte_length"] or _digest_bytes(raw) != ref["sha256"]:
            _fail("恢复点工件摘要不匹配", "document_restore_integrity")
        return checked

    def initialize(
        self,
        *,
        document_kind: str,
        document: Mapping[str, Any],
        application_payload: Mapping[str, Any],
        document_lineage_epoch: int = 0,
        revision: int = 0,
        fingerprints: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if document_kind not in _KINDS:
            _fail("document_kind不合法")
        lineage = _counter(document_lineage_epoch, "document_lineage_epoch")
        document_revision = _counter(revision, "document_revision")
        identities = _fingerprints(
            fingerprints or {"database": None, "engine": None, "schema": None}
        )
        document_value, document_raw = _json_mapping(
            document, "document", MAX_DOCUMENT_BYTES
        )
        payload_value, payload_raw = _json_mapping(
            application_payload,
            "application_payload",
            MAX_APPLICATION_PAYLOAD_BYTES,
        )
        expected_document_ref = {
            "artifact_kind": "document",
            "sha256": _digest_bytes(document_raw),
            "byte_length": len(document_raw),
        }
        expected_payload_ref = {
            "artifact_kind": "application_payload",
            "sha256": _digest_bytes(payload_raw),
            "byte_length": len(payload_raw),
        }
        with self._lock():
            self.root.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                existing = self._read_unlocked()
                current = existing["current"]
                if (
                    current["document_kind"] == document_kind
                    and current["document_lineage_epoch"] == lineage
                    and current["revision"] == document_revision
                    and current["document_artifact"] == expected_document_ref
                    and current["application_payload_artifact"]
                    == expected_payload_ref
                    and current["fingerprints"] == identities
                ):
                    if (
                        self._read_artifact_unlocked(current["document_artifact"])
                        != document_value
                        or self._read_artifact_unlocked(
                            current["application_payload_artifact"]
                        )
                        != payload_value
                    ):
                        _fail(
                            "恢复点当前工件被篡改",
                            "document_restore_integrity",
                        )
                    return {"status": "already_initialized", **copy.deepcopy(existing)}
                _fail("恢复点存储已经绑定其他文档基线", "document_restore_conflict")
            document_ref = self._artifact_ref_unlocked(
                document_value, kind="document"
            )
            payload_ref = self._artifact_ref_unlocked(
                payload_value, kind="application_payload"
            )
            store = {
                "contract": STORE_CONTRACT,
                "document_id": self.document_id,
                "store_revision": 0,
                "current": {
                    "document_id": self.document_id,
                    "document_kind": document_kind,
                    "document_lineage_epoch": lineage,
                    "revision": document_revision,
                    "document_artifact": document_ref,
                    "application_payload_artifact": payload_ref,
                    "fingerprints": identities,
                    "updated_at": self._clock(),
                },
                "checkpoints": [],
                "restore_intents": [],
                "restore_receipts": [],
                "expired_restore_filter": None,
            }
            self._write_unlocked(store)
            self._garbage_collect_unlocked(store)
            return {"status": "initialized", **copy.deepcopy(store)}

    def read(self) -> dict[str, Any]:
        with self._lock():
            return copy.deepcopy(self._read_unlocked())

    def write_gate(self) -> dict[str, Any]:
        """Return the durable restore write gate for non-restore callers."""

        with self._lock():
            store = self._read_unlocked()
            active = next(
                (
                    item
                    for item in store["restore_intents"]
                    if item["state"] not in _TERMINAL_STATES
                ),
                None,
            )
            return {
                "writes_frozen": active is not None,
                "store_revision": store["store_revision"],
                "intent_id": None if active is None else active["intent_id"],
                "state": None if active is None else active["state"],
            }

    def record_document_revision(
        self,
        *,
        expected_store_revision: int,
        expected_lineage_epoch: int,
        expected_document_revision: int,
        revision: int,
        document: Mapping[str, Any],
        application_payload: Mapping[str, Any],
        fingerprints: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Advance a normal edit inside the current lineage; never roll back."""

        lineage = _counter(expected_lineage_epoch, "expected_lineage_epoch")
        expected_revision = _counter(
            expected_document_revision, "expected_document_revision"
        )
        new_revision = _counter(revision, "document_revision")
        identities = _fingerprints(fingerprints)
        with self._lock():
            store = self._read_unlocked()
            self._assert_revision(store, expected_store_revision)
            if any(item["state"] not in _TERMINAL_STATES for item in store["restore_intents"]):
                _fail("restore intent未终结，禁止文档写入", "restore_blocked")
            current = store["current"]
            if (
                current["document_lineage_epoch"] != lineage
                or current["revision"] != expected_revision
            ):
                _fail("当前文档基线已变化", "stale_context")
            if new_revision <= current["revision"]:
                _fail("文档revision必须单调递增", "document_revision_regression")
            document_ref = self._artifact_ref_unlocked(document, kind="document")
            payload_ref = self._artifact_ref_unlocked(
                application_payload, kind="application_payload"
            )
            store["current"] = {
                **current,
                "revision": new_revision,
                "document_artifact": document_ref,
                "application_payload_artifact": payload_ref,
                "fingerprints": identities,
                "updated_at": self._clock(),
            }
            store["store_revision"] += 1
            self._write_unlocked(store)
            self._garbage_collect_unlocked(store)
            return copy.deepcopy(store["current"] | {"store_revision": store["store_revision"]})

    def _creation_digest(
        self,
        *,
        request_id: str,
        name: str,
        automatic: bool,
        reason: str,
        source: Mapping[str, Any],
        current: Mapping[str, Any],
    ) -> str:
        return _digest_bytes(
            json_bytes(
                {
                    "request_id": request_id,
                    "name": name,
                    "automatic": automatic,
                    "reason": reason,
                    "source": source,
                    "document_lineage_epoch": current["document_lineage_epoch"],
                    "revision": current["revision"],
                    "document_digest": current["document_artifact"]["sha256"],
                    "application_payload_digest": current[
                        "application_payload_artifact"
                    ]["sha256"],
                }
            )
        )

    def _new_checkpoint_unlocked(
        self,
        store: dict[str, Any],
        *,
        request_id: str,
        name: str,
        automatic: bool,
        reason: str,
        source: Mapping[str, Any],
    ) -> dict[str, Any]:
        if len(store["checkpoints"]) >= MAX_CHECKPOINTS:
            _fail("恢复点数量达到上限", "agent_size_limit")
        checkpoint_id = self._checkpoint_id_factory()
        if not isinstance(checkpoint_id, str) or _CHECKPOINT_ID.fullmatch(checkpoint_id) is None:
            _fail("checkpoint_id生成失败")
        if any(item["checkpoint_id"] == checkpoint_id for item in store["checkpoints"]):
            _fail("checkpoint_id冲突", "document_restore_conflict")
        current = store["current"]
        checkpoint = {
            "contract": CHECKPOINT_CONTRACT,
            "type": CHECKPOINT_TYPE,
            "checkpoint_id": checkpoint_id,
            "create_request_id": request_id,
            "creation_digest": self._creation_digest(
                request_id=request_id,
                name=name,
                automatic=automatic,
                reason=reason,
                source=source,
                current=current,
            ),
            "original_name": name,
            "name": name,
            "automatic": automatic,
            "reason": reason,
            "document_id": self.document_id,
            "document_kind": current["document_kind"],
            "document_lineage_epoch": current["document_lineage_epoch"],
            "revision": current["revision"],
            "document_artifact": copy.deepcopy(current["document_artifact"]),
            "application_payload_artifact": copy.deepcopy(
                current["application_payload_artifact"]
            ),
            "fingerprints": copy.deepcopy(current["fingerprints"]),
            "source": copy.deepcopy(source),
            "created_at": self._clock(),
            "renamed_at": None,
        }
        store["checkpoints"].append(_checkpoint(checkpoint, document_id=self.document_id))
        return store["checkpoints"][-1]

    def create_checkpoint(
        self,
        *,
        expected_store_revision: int,
        request_id: str,
        name: str,
        document: Mapping[str, Any],
        application_payload: Mapping[str, Any],
        source: Mapping[str, Any] | None = None,
        reason: str = "manual",
    ) -> dict[str, Any]:
        request = _identity(request_id, "create_request_id")
        checked_name = _safe_name(name)
        checked_reason = _identity(reason, "checkpoint_reason")
        checked_source = _source(
            source
            or {"conversation_id": None, "turn_id": None, "event_cursor": None}
        )
        document_value, document_raw = _json_mapping(
            document, "document", MAX_DOCUMENT_BYTES
        )
        payload_value, payload_raw = _json_mapping(
            application_payload,
            "application_payload",
            MAX_APPLICATION_PAYLOAD_BYTES,
        )
        with self._lock():
            store = self._read_unlocked()
            existing = next(
                (
                    item
                    for item in store["checkpoints"]
                    if item["create_request_id"] == request
                ),
                None,
            )
            if existing is not None:
                if (
                    existing["original_name"] != checked_name
                    or existing["reason"] != checked_reason
                    or existing["source"] != checked_source
                    or existing["document_artifact"]["sha256"]
                    != _digest_bytes(document_raw)
                    or existing["application_payload_artifact"]["sha256"]
                    != _digest_bytes(payload_raw)
                ):
                    _fail("恢复点创建请求已绑定其他内容", "request_id_conflict")
                return {
                    "status": "already_created",
                    "store_revision": store["store_revision"],
                    "checkpoint": copy.deepcopy(existing),
                }
            current = store["current"]
            self._assert_revision(store, expected_store_revision)
            if any(item["state"] not in _TERMINAL_STATES for item in store["restore_intents"]):
                _fail("restore intent未终结，禁止建立恢复点", "restore_blocked")
            if (
                _digest_bytes(document_raw) != current["document_artifact"]["sha256"]
                or _digest_bytes(payload_raw)
                != current["application_payload_artifact"]["sha256"]
            ):
                _fail("Renderer文档与Host基线不一致", "stale_context")
            # Read both artifacts before referencing them from new metadata.
            if self._read_artifact_unlocked(current["document_artifact"]) != document_value:
                _fail("当前文档工件不完整", "document_restore_integrity")
            if (
                self._read_artifact_unlocked(current["application_payload_artifact"])
                != payload_value
            ):
                _fail("当前应用载荷工件不完整", "document_restore_integrity")
            checkpoint = self._new_checkpoint_unlocked(
                store,
                request_id=request,
                name=checked_name,
                automatic=False,
                reason=checked_reason,
                source=checked_source,
            )
            store["store_revision"] += 1
            self._write_unlocked(store)
            return {
                "status": "created",
                "store_revision": store["store_revision"],
                "checkpoint": copy.deepcopy(checkpoint),
            }

    def rename_checkpoint(
        self,
        *,
        checkpoint_id: str,
        name: str,
        expected_store_revision: int,
    ) -> dict[str, Any]:
        checked_name = _safe_name(name)
        with self._lock():
            store = self._read_unlocked()
            self._assert_revision(store, expected_store_revision)
            checkpoint = self._find_checkpoint(store, checkpoint_id)
            if checkpoint["name"] == checked_name:
                return {
                    "status": "no_op",
                    "store_revision": store["store_revision"],
                    "checkpoint": copy.deepcopy(checkpoint),
                }
            if any(
                item["state"] not in _TERMINAL_STATES
                for item in store["restore_intents"]
            ):
                _fail(
                    "restore intent未终结，禁止重命名恢复点",
                    "restore_blocked",
                )
            checkpoint["name"] = checked_name
            checkpoint["renamed_at"] = self._clock()
            store["store_revision"] += 1
            self._write_unlocked(store)
            return {
                "status": "renamed",
                "store_revision": store["store_revision"],
                "checkpoint": copy.deepcopy(checkpoint),
            }

    def delete_checkpoint(
        self,
        *,
        checkpoint_id: str,
        expected_store_revision: int,
    ) -> dict[str, Any]:
        """Explicitly remove one checkpoint without weakening live references."""

        if (
            not isinstance(checkpoint_id, str)
            or _CHECKPOINT_ID.fullmatch(checkpoint_id) is None
        ):
            _fail("checkpoint_id不合法")
        with self._lock():
            store = self._read_unlocked()
            checkpoint = next(
                (
                    item
                    for item in store["checkpoints"]
                    if item["checkpoint_id"] == checkpoint_id
                ),
                None,
            )
            if checkpoint is None:
                return {
                    "status": "already_deleted",
                    "checkpoint_id": checkpoint_id,
                    "store_revision": store["store_revision"],
                }
            self._assert_revision(store, expected_store_revision)
            referencing_intents = [
                item["intent_id"]
                for item in store["restore_intents"]
                if checkpoint_id
                in {
                    item["target_checkpoint_id"],
                    item["before_restore_checkpoint_id"],
                }
            ]
            if referencing_intents:
                _fail(
                    "恢复点仍被restore intent引用",
                    "document_restore_in_use",
                    {"intent_ids": referencing_intents},
                )
            if any(
                item["state"] not in _TERMINAL_STATES
                for item in store["restore_intents"]
            ):
                _fail(
                    "restore intent未终结，禁止删除恢复点",
                    "restore_blocked",
                )
            store["checkpoints"] = [
                item
                for item in store["checkpoints"]
                if item["checkpoint_id"] != checkpoint_id
            ]
            store["store_revision"] += 1
            self._write_unlocked(store)
            garbage = self._garbage_collect_unlocked(store)
            return {
                "status": "deleted",
                "checkpoint_id": checkpoint_id,
                "automatic": checkpoint["automatic"],
                "store_revision": store["store_revision"],
                "garbage_collection": garbage,
            }

    def collect_garbage(self, *, expected_store_revision: int) -> dict[str, Any]:
        """Reclaim crash leftovers without changing logical store revision."""

        with self._lock():
            store = self._read_unlocked()
            self._assert_revision(store, expected_store_revision)
            return {
                "status": "collected",
                "store_revision": store["store_revision"],
                **self._garbage_collect_unlocked(store),
            }

    def storage_usage(self) -> dict[str, Any]:
        with self._lock():
            store = self._read_unlocked()
            refs = self._artifact_refs(store)
            referenced_bytes = sum(item["byte_length"] for item in refs.values())
            artifact_files = 0
            artifact_disk_bytes = 0
            unreferenced_files = 0
            unsafe_entries = 0
            try:
                entries = list(self.root.iterdir())
            except OSError:
                entries = []
                unsafe_entries = 1
            for entry in entries:
                if _ARTIFACT_FILE.fullmatch(entry.name) is None:
                    continue
                try:
                    candidate = safe_path(self.root, entry.name)
                    info = candidate.lstat()
                    if (
                        candidate.is_symlink()
                        or not stat.S_ISREG(info.st_mode)
                        or info.st_nlink != 1
                    ):
                        unsafe_entries += 1
                        continue
                except (OSError, PlanError):
                    unsafe_entries += 1
                    continue
                artifact_files += 1
                artifact_disk_bytes += info.st_size
                if entry.name not in refs:
                    unreferenced_files += 1
            automatic_count, automatic_bytes = self._automatic_usage(
                store["checkpoints"]
            )
            return {
                "store_revision": store["store_revision"],
                "manual_checkpoint_count": sum(
                    not item["automatic"] for item in store["checkpoints"]
                ),
                "automatic_checkpoint_count": automatic_count,
                "automatic_checkpoint_logical_bytes": automatic_bytes,
                "restore_intent_count": len(store["restore_intents"]),
                "restore_receipt_count": len(store["restore_receipts"]),
                "restore_receipt_logical_bytes": self._receipt_artifact_bytes(
                    store["restore_receipts"]
                ),
                "referenced_artifact_count": len(refs),
                "referenced_artifact_logical_bytes": referenced_bytes,
                "artifact_file_count": artifact_files,
                "artifact_disk_bytes": artifact_disk_bytes,
                "unreferenced_artifact_file_count": unreferenced_files,
                "unsafe_artifact_entry_count": unsafe_entries,
            }

    def validate_integrity(self) -> dict[str, Any]:
        """Read and verify every content-addressed artifact in the store.

        Ordinary metadata reads remain cheap.  Migration, release validation
        and explicit maintenance use this stronger boundary before declaring a
        copied repository authoritative.
        """

        with self._lock():
            store = self._read_unlocked()
            refs = self._artifact_refs(store)
            verified_bytes = 0
            for ref in refs.values():
                self._read_artifact_unlocked(ref)
                verified_bytes += ref["byte_length"]
            return {
                "status": "valid",
                "document_id": self.document_id,
                "store_revision": store["store_revision"],
                "verified_artifact_count": len(refs),
                "verified_artifact_bytes": verified_bytes,
            }

    @staticmethod
    def _find_checkpoint(store: Mapping[str, Any], checkpoint_id: str) -> dict[str, Any]:
        if not isinstance(checkpoint_id, str) or _CHECKPOINT_ID.fullmatch(checkpoint_id) is None:
            _fail("checkpoint_id不合法")
        found = next(
            (item for item in store["checkpoints"] if item["checkpoint_id"] == checkpoint_id),
            None,
        )
        if found is None:
            _fail("恢复点不存在", "document_restore_not_found")
        return found

    @staticmethod
    def _find_intent(store: Mapping[str, Any], intent_id: str) -> dict[str, Any]:
        intent = next(
            (item for item in store["restore_intents"] if item["intent_id"] == intent_id),
            None,
        )
        if intent is None:
            if _expired_filter_contains(
                store.get("expired_restore_filter"), "intent", intent_id
            ):
                _fail(
                    "restore ACK已经超过完整回执保留窗口",
                    "restore_ack_expired",
                )
            _fail("restore intent不存在", "document_restore_not_found")
        return intent

    @staticmethod
    def _find_receipt_by_intent(
        store: Mapping[str, Any], intent_id: str
    ) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in store.get("restore_receipts", [])
                if item["intent_id"] == intent_id
            ),
            None,
        )

    def list_checkpoints(self, *, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        start = _counter(offset, "offset")
        if type(limit) is not int or not 1 <= limit <= 100:
            _fail("limit不合法")
        store = self.read()
        rows = store["checkpoints"][start : start + limit]
        return {
            "contract": CHECKPOINT_CONTRACT,
            "type": CHECKPOINT_TYPE,
            "document_id": self.document_id,
            "document_lineage_epoch": store["current"]["document_lineage_epoch"],
            "document_revision": store["current"]["revision"],
            "store_revision": store["store_revision"],
            "offset": start,
            "limit": limit,
            "total": len(store["checkpoints"]),
            "next_offset": (
                start + len(rows) if start + len(rows) < len(store["checkpoints"]) else None
            ),
            "items": copy.deepcopy(rows),
        }

    def verify_checkpoint(self, checkpoint_id: str) -> dict[str, Any]:
        with self._lock():
            store = self._read_unlocked()
            checkpoint = self._find_checkpoint(store, checkpoint_id)
            document = self._read_artifact_unlocked(checkpoint["document_artifact"])
            payload = self._read_artifact_unlocked(
                checkpoint["application_payload_artifact"]
            )
            return {
                "status": "verified",
                "checkpoint_id": checkpoint["checkpoint_id"],
                "document_digest": _digest_bytes(json_bytes(document)),
                "application_payload_digest": _digest_bytes(json_bytes(payload)),
                "document_bytes": checkpoint["document_artifact"]["byte_length"],
                "application_payload_bytes": checkpoint[
                    "application_payload_artifact"
                ]["byte_length"],
            }

    def read_checkpoint_payload(self, checkpoint_id: str) -> dict[str, Any]:
        """Return verified Host-internal restore material, never a UI event."""

        with self._lock():
            store = self._read_unlocked()
            checkpoint = self._find_checkpoint(store, checkpoint_id)
            return {
                "checkpoint": copy.deepcopy(checkpoint),
                "document": self._read_artifact_unlocked(checkpoint["document_artifact"]),
                "application_payload": self._read_artifact_unlocked(
                    checkpoint["application_payload_artifact"]
                ),
            }

    @staticmethod
    def _key_diff(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
        left_keys = set(left)
        right_keys = set(right)
        changed = sorted(key for key in left_keys & right_keys if left[key] != right[key])
        added = sorted(right_keys - left_keys)
        removed = sorted(left_keys - right_keys)
        all_rows = [("added", key) for key in added]
        all_rows += [("removed", key) for key in removed]
        all_rows += [("changed", key) for key in changed]
        selected = all_rows[:32]
        return {
            "added_keys": [key for kind, key in selected if kind == "added"],
            "removed_keys": [key for kind, key in selected if kind == "removed"],
            "changed_keys": [key for kind, key in selected if kind == "changed"],
            "changed_key_count": len(all_rows),
            "truncated": len(all_rows) > len(selected),
        }

    def diff_summary(
        self,
        *,
        checkpoint_id: str,
        from_checkpoint_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock():
            store = self._read_unlocked()
            target = self._find_checkpoint(store, checkpoint_id)
            if from_checkpoint_id is None:
                source_record = store["current"]
            else:
                source_record = self._find_checkpoint(store, from_checkpoint_id)
            left_document = self._read_artifact_unlocked(source_record["document_artifact"])
            right_document = self._read_artifact_unlocked(target["document_artifact"])
            left_payload = self._read_artifact_unlocked(
                source_record["application_payload_artifact"]
            )
            right_payload = self._read_artifact_unlocked(
                target["application_payload_artifact"]
            )
            fingerprint_match = {
                name: source_record["fingerprints"][name] == target["fingerprints"][name]
                for name in ("database", "engine", "schema")
            }
            return {
                "checkpoint_id": target["checkpoint_id"],
                "from_checkpoint_id": from_checkpoint_id,
                "source_lineage_epoch": source_record["document_lineage_epoch"],
                "source_revision": source_record["revision"],
                "target_lineage_epoch": target["document_lineage_epoch"],
                "target_revision": target["revision"],
                "document": {
                    "changed": source_record["document_artifact"]["sha256"]
                    != target["document_artifact"]["sha256"],
                    "from_digest": source_record["document_artifact"]["sha256"],
                    "to_digest": target["document_artifact"]["sha256"],
                    **self._key_diff(left_document, right_document),
                },
                "application_payload": {
                    "changed": source_record["application_payload_artifact"]["sha256"]
                    != target["application_payload_artifact"]["sha256"],
                    "from_digest": source_record["application_payload_artifact"]["sha256"],
                    "to_digest": target["application_payload_artifact"]["sha256"],
                    **self._key_diff(left_payload, right_payload),
                },
                "fingerprint_match": fingerprint_match,
                "application_payload_reusable": all(fingerprint_match.values()),
                "large_content_inlined": False,
            }

    @staticmethod
    def _observed_digests(
        document: Mapping[str, Any], application_payload: Mapping[str, Any]
    ) -> tuple[str, str]:
        _, document_raw = _json_mapping(document, "document", MAX_DOCUMENT_BYTES)
        _, payload_raw = _json_mapping(
            application_payload,
            "application_payload",
            MAX_APPLICATION_PAYLOAD_BYTES,
        )
        return _digest_bytes(document_raw), _digest_bytes(payload_raw)

    @staticmethod
    def _assert_application_payload_reusable(
        current: Mapping[str, Any], target: Mapping[str, Any]
    ) -> None:
        fingerprint_match = {
            name: current["fingerprints"][name] == target["fingerprints"][name]
            for name in ("database", "engine", "schema")
        }
        if not all(fingerprint_match.values()):
            _fail(
                "恢复点绑定的数据库、引擎或方案结构已经变化，旧应用载荷不能恢复到当前版本；可在界面详情中复制提示词，手动让Agent检查当前文件",
                "document_restore_application_payload_stale",
                {
                    "fingerprint_match": fingerprint_match,
                    "application_payload_reusable": False,
                    "compatibility": "version_incompatible",
                    "recovery_action": (
                        "agent_guided_current_file_compatibility"
                    ),
                },
            )

    def prepare_restore(
        self,
        *,
        expected_store_revision: int,
        request_id: str,
        checkpoint_id: str,
        observed_document: Mapping[str, Any],
        observed_application_payload: Mapping[str, Any],
        queue_revisions: Sequence[Mapping[str, Any]],
        guard_snapshot: Mapping[str, bool],
        source: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = _identity(request_id, "restore_request_id")
        guards = _guards(guard_snapshot)
        queue = _queue_revisions(queue_revisions)
        pre_document_digest, pre_payload_digest = self._observed_digests(
            observed_document, observed_application_payload
        )
        checked_source = _source(
            source
            or {"conversation_id": None, "turn_id": None, "event_cursor": None}
        )
        intent_id = "restore_" + _digest_text(request)[:32]
        with self._lock():
            store = self._read_unlocked()
            # A terminal receipt is an idempotency fact, not an instruction to
            # re-apply historical payload.  It remains replayable even after
            # its checkpoint artifacts have aged out of normal retention.
            receipt = next(
                (
                    item
                    for item in store["restore_receipts"]
                    if item["request_id"] == request
                ),
                None,
            )
            if receipt is not None:
                if (
                    receipt["target_checkpoint_identity"] != checkpoint_id
                    or receipt["pre_document_artifact"]["sha256"]
                    != pre_document_digest
                    or receipt["pre_application_payload_artifact"]["sha256"]
                    != pre_payload_digest
                    or receipt["queue_revision_digest"]
                    != _digest_bytes(json_bytes(queue))
                    or receipt["source"] != checked_source
                ):
                    _fail("恢复请求已绑定其他事务", "request_id_conflict")
                return {
                    "status": "already_" + receipt["terminal_state"],
                    "store_revision": store["store_revision"],
                    "receipt": copy.deepcopy(receipt),
                }
            target = self._find_checkpoint(store, checkpoint_id)
            self._read_artifact_unlocked(target["document_artifact"])
            self._read_artifact_unlocked(target["application_payload_artifact"])
            current = store["current"]
            self._assert_application_payload_reusable(current, target)
            existing = next(
                (item for item in store["restore_intents"] if item["request_id"] == request),
                None,
            )
            if existing is not None:
                if (
                    existing["target_checkpoint_id"] != checkpoint_id
                    or existing["pre_document_digest"] != pre_document_digest
                    or existing["pre_application_payload_digest"] != pre_payload_digest
                    or existing["queue_revisions"] != queue
                    or existing["source"] != checked_source
                ):
                    _fail("恢复请求已绑定其他事务", "request_id_conflict")
                return {
                    "status": "already_" + existing["state"],
                    "store_revision": store["store_revision"],
                    "intent": copy.deepcopy(existing),
                }
            if _expired_filter_contains(
                store["expired_restore_filter"], "request", request
            ):
                _fail(
                    "恢复请求的幂等回执已经超过完整保留窗口",
                    "restore_request_expired",
                )
            self._assert_revision(store, expected_store_revision)
            blockers = [key for key, present in guards.items() if present]
            if blockers:
                _fail(
                    "当前状态禁止恢复",
                    "restore_blocked",
                    {"blockers": blockers},
                )
            if any(item["state"] not in _TERMINAL_STATES for item in store["restore_intents"]):
                _fail("已有未终结restore intent", "restore_blocked")
            if any(
                row["state"] == "needs_rebind"
                or row["document_lineage_epoch"]
                != current["document_lineage_epoch"]
                or row["document_revision"] > current["revision"]
                for row in queue
            ):
                _fail(
                    "Queue revision集合不属于当前可恢复lineage",
                    "restore_queue_conflict",
                )
            if (
                current["document_artifact"]["sha256"] != pre_document_digest
                or current["application_payload_artifact"]["sha256"]
                != pre_payload_digest
            ):
                _fail("Renderer文档与Host恢复前基线不一致", "stale_context")
            if (
                target["document_artifact"]["sha256"] == pre_document_digest
                and target["application_payload_artifact"]["sha256"]
                == pre_payload_digest
            ):
                return {
                    "status": "no_op",
                    "store_revision": store["store_revision"],
                    "document_lineage_epoch": current["document_lineage_epoch"],
                    "document_revision": current["revision"],
                    "old_lineage_authorizations_invalidated": False,
                }
            retention = self._prune_for_new_restore_unlocked(
                store,
                target_checkpoint_id=target["checkpoint_id"],
            )
            before = self._new_checkpoint_unlocked(
                store,
                request_id="before_" + _digest_text(request)[:32],
                name=f"恢复前安全点 {current['revision']}",
                automatic=True,
                reason="before_restore",
                source=checked_source,
            )
            now = self._clock()
            intent = {
                "contract": RESTORE_INTENT_CONTRACT,
                "intent_id": intent_id,
                "request_id": request,
                "source": checked_source,
                "state": "prepared",
                "last_proven_state": "prepared",
                "target_checkpoint_id": target["checkpoint_id"],
                "before_restore_checkpoint_id": before["checkpoint_id"],
                "source_lineage_epoch": current["document_lineage_epoch"],
                "source_revision": current["revision"],
                "target_lineage_epoch": target["document_lineage_epoch"],
                "target_revision": target["revision"],
                "result_lineage_epoch": current["document_lineage_epoch"] + 1,
                "result_revision": current["revision"] + 1,
                "pre_document_digest": pre_document_digest,
                "pre_application_payload_digest": pre_payload_digest,
                "target_document_digest": target["document_artifact"]["sha256"],
                "target_application_payload_digest": target[
                    "application_payload_artifact"
                ]["sha256"],
                "queue_revisions": queue,
                "queue_revision_digest": _digest_bytes(json_bytes(queue)),
                "created_at": now,
                "updated_at": now,
                "renderer_receipt_id": None,
                "baseline_receipt_id": None,
                "commit_receipt_id": None,
                "abort_receipt_id": None,
                "queue_rebind_receipt_digest": None,
                "last_recovery_evidence_id": None,
                "last_recovery_identity_digest": None,
                "last_recovery_result": None,
                "recovery_authorization_digest": None,
                "recovery_reason": None,
                "outcome_unknown_reason": None,
                "authorization_invalidation": None,
            }
            store["restore_intents"].append(_intent(intent))
            store["store_revision"] += 1
            self._write_unlocked(store)
            garbage = self._garbage_collect_unlocked(store)
            return {
                "status": "prepared",
                "store_revision": store["store_revision"],
                "intent": copy.deepcopy(intent),
                "retention": retention,
                "garbage_collection": garbage,
            }

    @staticmethod
    def _target_matches(
        intent: Mapping[str, Any],
        document_digest: str,
        payload_digest: str,
    ) -> bool:
        return (
            intent["target_document_digest"] == document_digest
            and intent["target_application_payload_digest"] == payload_digest
        )

    def mark_renderer_applied(
        self,
        *,
        intent_id: str,
        expected_store_revision: int,
        receipt_id: str,
        observed_document: Mapping[str, Any],
        observed_application_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        receipt = _identity(receipt_id, "renderer_receipt_id")
        document_digest, payload_digest = self._observed_digests(
            observed_document, observed_application_payload
        )
        with self._lock():
            store = self._read_unlocked()
            retired = self._find_receipt_by_intent(store, intent_id)
            if retired is not None:
                if (
                    retired["renderer_receipt_id"] is None
                    or retired["renderer_receipt_id"] != receipt
                    or retired["target_document_artifact"]["sha256"]
                    != document_digest
                    or retired["target_application_payload_artifact"]["sha256"]
                    != payload_digest
                ):
                    _fail("Renderer ACK冲突", "restore_ack_conflict")
                return {
                    "status": "already_renderer_applied",
                    "store_revision": store["store_revision"],
                    "receipt": copy.deepcopy(retired),
                }
            intent = self._find_intent(store, intent_id)
            target = self._find_checkpoint(store, intent["target_checkpoint_id"])
            self._assert_application_payload_reusable(store["current"], target)
            if intent["renderer_receipt_id"] is not None:
                if (
                    intent["renderer_receipt_id"] != receipt
                    or not self._target_matches(
                        intent, document_digest, payload_digest
                    )
                ):
                    _fail("Renderer ACK冲突", "restore_ack_conflict")
                return {
                    "status": "already_renderer_applied",
                    "store_revision": store["store_revision"],
                    "intent": copy.deepcopy(intent),
                }
            self._assert_revision(store, expected_store_revision)
            if intent["state"] not in {"prepared", "recovery_required"}:
                _fail("当前restore状态不能接收Renderer ACK", "restore_state_conflict")
            if intent["last_proven_state"] != "prepared" or not self._target_matches(
                intent, document_digest, payload_digest
            ):
                _fail("Renderer ACK没有证明完整目标载荷", "restore_ack_conflict")
            intent.update(
                state="renderer_applied",
                last_proven_state="renderer_applied",
                renderer_receipt_id=receipt,
                recovery_reason=None,
                outcome_unknown_reason=None,
                updated_at=self._clock(),
            )
            store["store_revision"] += 1
            self._write_unlocked(store)
            return {
                "status": "renderer_applied",
                "store_revision": store["store_revision"],
                "intent": copy.deepcopy(intent),
            }

    def mark_baseline_advanced(
        self,
        *,
        intent_id: str,
        expected_store_revision: int,
        receipt_id: str,
        observed_document: Mapping[str, Any],
        observed_application_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        receipt = _identity(receipt_id, "baseline_receipt_id")
        document_digest, payload_digest = self._observed_digests(
            observed_document, observed_application_payload
        )
        with self._lock():
            store = self._read_unlocked()
            retired = self._find_receipt_by_intent(store, intent_id)
            if retired is not None:
                if (
                    retired["baseline_receipt_id"] is None
                    or retired["baseline_receipt_id"] != receipt
                    or retired["target_document_artifact"]["sha256"]
                    != document_digest
                    or retired["target_application_payload_artifact"]["sha256"]
                    != payload_digest
                ):
                    _fail("Baseline ACK冲突", "restore_ack_conflict")
                return {
                    "status": "already_baseline_advanced",
                    "store_revision": store["store_revision"],
                    "receipt": copy.deepcopy(retired),
                }
            intent = self._find_intent(store, intent_id)
            target = self._find_checkpoint(store, intent["target_checkpoint_id"])
            self._assert_application_payload_reusable(store["current"], target)
            if intent["baseline_receipt_id"] is not None:
                if (
                    intent["baseline_receipt_id"] != receipt
                    or not self._target_matches(
                        intent, document_digest, payload_digest
                    )
                ):
                    _fail("Baseline ACK冲突", "restore_ack_conflict")
                return {
                    "status": "already_baseline_advanced",
                    "store_revision": store["store_revision"],
                    "intent": copy.deepcopy(intent),
                }
            self._assert_revision(store, expected_store_revision)
            if intent["state"] not in {"renderer_applied", "recovery_required"}:
                _fail("当前restore状态不能推进baseline", "restore_state_conflict")
            if (
                intent["last_proven_state"] != "renderer_applied"
                or intent["renderer_receipt_id"] is None
                or not self._target_matches(intent, document_digest, payload_digest)
            ):
                _fail("Baseline ACK没有证明目标载荷", "restore_ack_conflict")
            intent.update(
                state="baseline_advanced",
                last_proven_state="baseline_advanced",
                baseline_receipt_id=receipt,
                recovery_reason=None,
                outcome_unknown_reason=None,
                updated_at=self._clock(),
            )
            store["store_revision"] += 1
            self._write_unlocked(store)
            return {
                "status": "baseline_advanced",
                "store_revision": store["store_revision"],
                "intent": copy.deepcopy(intent),
            }

    def preflight_baseline_advanced(
        self,
        *,
        intent_id: str,
        expected_store_revision: int,
        receipt_id: str,
        observed_document: Mapping[str, Any],
        observed_application_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Validate a baseline transition before the Host mutates its domain.

        This intentionally duplicates the final transition checks under a
        read-only lock.  The service keeps its own operation lock across this
        preflight, the App-domain write, and ``mark_baseline_advanced``; the
        final call remains the durable authority.
        """

        receipt = _identity(receipt_id, "baseline_receipt_id")
        document_digest, payload_digest = self._observed_digests(
            observed_document, observed_application_payload
        )
        with self._lock():
            store = self._read_unlocked()
            retired = self._find_receipt_by_intent(store, intent_id)
            if retired is not None:
                if (
                    retired["baseline_receipt_id"] is None
                    or retired["baseline_receipt_id"] != receipt
                    or retired["target_document_artifact"]["sha256"]
                    != document_digest
                    or retired["target_application_payload_artifact"]["sha256"]
                    != payload_digest
                ):
                    _fail("Baseline ACK冲突", "restore_ack_conflict")
                return {
                    "status": "already_baseline_advanced",
                    "store_revision": store["store_revision"],
                }
            intent = self._find_intent(store, intent_id)
            target = self._find_checkpoint(store, intent["target_checkpoint_id"])
            self._assert_application_payload_reusable(store["current"], target)
            if intent["baseline_receipt_id"] is not None:
                if (
                    intent["baseline_receipt_id"] != receipt
                    or not self._target_matches(
                        intent, document_digest, payload_digest
                    )
                ):
                    _fail("Baseline ACK冲突", "restore_ack_conflict")
                return {
                    "status": "already_baseline_advanced",
                    "store_revision": store["store_revision"],
                }
            self._assert_revision(store, expected_store_revision)
            if intent["state"] not in {"renderer_applied", "recovery_required"}:
                _fail("当前restore状态不能推进baseline", "restore_state_conflict")
            if (
                intent["last_proven_state"] != "renderer_applied"
                or intent["renderer_receipt_id"] is None
                or not self._target_matches(intent, document_digest, payload_digest)
            ):
                _fail("Baseline ACK没有证明目标载荷", "restore_ack_conflict")
            return {
                "status": "ready",
                "store_revision": store["store_revision"],
            }

    def preflight_commit_restore(
        self,
        *,
        intent_id: str,
        expected_store_revision: int,
        receipt_id: str,
        observed_document: Mapping[str, Any],
        observed_application_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Validate commit inputs before Queue rows are rebound."""

        receipt = _identity(receipt_id, "commit_receipt_id")
        document_digest, payload_digest = self._observed_digests(
            observed_document, observed_application_payload
        )
        with self._lock():
            store = self._read_unlocked()
            retired = self._find_receipt_by_intent(store, intent_id)
            if retired is not None:
                if (
                    retired["terminal_state"] != "committed"
                    or retired["commit_receipt_id"] != receipt
                    or retired["target_document_artifact"]["sha256"]
                    != document_digest
                    or retired["target_application_payload_artifact"]["sha256"]
                    != payload_digest
                ):
                    _fail("Commit ACK冲突", "restore_ack_conflict")
                return {
                    "status": "already_committed",
                    "store_revision": store["store_revision"],
                }
            intent = self._find_intent(store, intent_id)
            target = self._find_checkpoint(store, intent["target_checkpoint_id"])
            self._assert_application_payload_reusable(store["current"], target)
            self._assert_revision(store, expected_store_revision)
            if intent["state"] not in {"baseline_advanced", "recovery_required"}:
                _fail("当前restore状态不能提交", "restore_state_conflict")
            if (
                intent["last_proven_state"] != "baseline_advanced"
                or intent["baseline_receipt_id"] is None
                or not self._target_matches(intent, document_digest, payload_digest)
            ):
                _fail("恢复提交没有证明完整目标载荷", "restore_ack_conflict")
            current = store["current"]
            if (
                current["document_lineage_epoch"] != intent["source_lineage_epoch"]
                or current["revision"] != intent["source_revision"]
                or current["document_artifact"]["sha256"]
                != intent["pre_document_digest"]
                or current["application_payload_artifact"]["sha256"]
                != intent["pre_application_payload_digest"]
            ):
                _fail("恢复提交前Host基线发生变化", "stale_context")
            return {
                "status": "ready",
                "store_revision": store["store_revision"],
            }

    def commit_restore(
        self,
        *,
        intent_id: str,
        expected_store_revision: int,
        receipt_id: str,
        observed_document: Mapping[str, Any],
        observed_application_payload: Mapping[str, Any],
        observed_queue_revisions: Sequence[Mapping[str, Any]],
        queue_rebind_receipt: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Commit after the higher Host boundary has rebound Queue rows.

        A non-empty captured Queue set requires a complete rebind receipt.  The
        future Router integration must publish that receipt and the Queue state
        in its own atomic Host transaction; this standalone store never claims
        to have mutated the existing workbench ledger.
        """

        receipt = _identity(receipt_id, "commit_receipt_id")
        document_digest, payload_digest = self._observed_digests(
            observed_document, observed_application_payload
        )
        observed_queue = _queue_revisions(observed_queue_revisions)
        with self._lock():
            store = self._read_unlocked()
            retired = self._find_receipt_by_intent(store, intent_id)
            if retired is not None:
                checked_queue_receipt = _queue_receipt(
                    queue_rebind_receipt,
                    queue=observed_queue,
                    result_lineage_epoch=retired["result_lineage_epoch"],
                    result_revision=retired["result_revision"],
                )
                queue_receipt_digest = (
                    None
                    if checked_queue_receipt is None
                    else _digest_bytes(json_bytes(checked_queue_receipt))
                )
                if (
                    retired["terminal_state"] != "committed"
                    or retired["commit_receipt_id"] != receipt
                    or retired["target_document_artifact"]["sha256"]
                    != document_digest
                    or retired["target_application_payload_artifact"]["sha256"]
                    != payload_digest
                    or retired["queue_revision_digest"]
                    != _digest_bytes(json_bytes(observed_queue))
                    or retired["queue_rebind_receipt_digest"]
                    != queue_receipt_digest
                ):
                    _fail("Commit ACK冲突", "restore_ack_conflict")
                return {
                    "status": "already_committed",
                    "store_revision": store["store_revision"],
                    "receipt": copy.deepcopy(retired),
                    "current": copy.deepcopy(store["current"]),
                }
            intent = self._find_intent(store, intent_id)
            target = self._find_checkpoint(store, intent["target_checkpoint_id"])
            self._assert_application_payload_reusable(store["current"], target)
            if intent["commit_receipt_id"] is not None:
                checked_queue_receipt = _queue_receipt(
                    queue_rebind_receipt,
                    queue=intent["queue_revisions"],
                    result_lineage_epoch=intent["result_lineage_epoch"],
                    result_revision=intent["result_revision"],
                )
                queue_receipt_digest = (
                    None
                    if checked_queue_receipt is None
                    else _digest_bytes(json_bytes(checked_queue_receipt))
                )
                if (
                    intent["commit_receipt_id"] != receipt
                    or not self._target_matches(
                        intent, document_digest, payload_digest
                    )
                    or observed_queue != intent["queue_revisions"]
                    or intent["queue_rebind_receipt_digest"]
                    != queue_receipt_digest
                ):
                    _fail("Commit ACK冲突", "restore_ack_conflict")
                return {
                    "status": "already_committed",
                    "store_revision": store["store_revision"],
                    "intent": copy.deepcopy(intent),
                    "current": copy.deepcopy(store["current"]),
                }
            self._assert_revision(store, expected_store_revision)
            if intent["state"] not in {"baseline_advanced", "recovery_required"}:
                _fail("当前restore状态不能提交", "restore_state_conflict")
            if (
                intent["last_proven_state"] != "baseline_advanced"
                or intent["baseline_receipt_id"] is None
                or not self._target_matches(intent, document_digest, payload_digest)
            ):
                _fail("恢复提交没有证明完整目标载荷", "restore_ack_conflict")
            if observed_queue != intent["queue_revisions"]:
                _fail("Queue revision集合在恢复中发生变化", "restore_queue_conflict")
            queue_receipt = _queue_receipt(
                queue_rebind_receipt,
                queue=intent["queue_revisions"],
                result_lineage_epoch=intent["result_lineage_epoch"],
                result_revision=intent["result_revision"],
            )
            queue_receipt_digest = (
                None
                if queue_receipt is None
                else _digest_bytes(json_bytes(queue_receipt))
            )
            current = store["current"]
            if (
                current["document_lineage_epoch"] != intent["source_lineage_epoch"]
                or current["revision"] != intent["source_revision"]
                or current["document_artifact"]["sha256"]
                != intent["pre_document_digest"]
                or current["application_payload_artifact"]["sha256"]
                != intent["pre_application_payload_digest"]
            ):
                _fail("恢复提交前Host基线发生变化", "stale_context")
            invalidation = {
                "old_lineage_authorizations_valid": False,
                "invalidated_lineage_epoch": intent["source_lineage_epoch"],
                "current_lineage_epoch": intent["result_lineage_epoch"],
                "invalidated_kinds": list(_INVALIDATED_KINDS),
                "queue_items_needing_rebind": [
                    row["queue_item_id"] for row in intent["queue_revisions"]
                ],
            }
            store["current"] = {
                "document_id": self.document_id,
                "document_kind": target["document_kind"],
                "document_lineage_epoch": intent["result_lineage_epoch"],
                "revision": intent["result_revision"],
                "document_artifact": copy.deepcopy(target["document_artifact"]),
                "application_payload_artifact": copy.deepcopy(
                    target["application_payload_artifact"]
                ),
                "fingerprints": copy.deepcopy(target["fingerprints"]),
                "updated_at": self._clock(),
            }
            intent.update(
                state="committed",
                last_proven_state="committed",
                commit_receipt_id=receipt,
                queue_rebind_receipt_digest=queue_receipt_digest,
                authorization_invalidation=invalidation,
                recovery_reason=None,
                outcome_unknown_reason=None,
                updated_at=self._clock(),
            )
            store["store_revision"] += 1
            self._write_unlocked(store)
            return {
                "status": "committed",
                "store_revision": store["store_revision"],
                "intent": copy.deepcopy(intent),
                "current": copy.deepcopy(store["current"]),
                "authorization_invalidation": copy.deepcopy(invalidation),
                "chat_deleted": False,
                "external_side_effects_reverted": False,
                "provider_request_sent": False,
            }

    def abort_restore(
        self,
        *,
        intent_id: str,
        expected_store_revision: int,
        receipt_id: str,
        observed_document: Mapping[str, Any],
        observed_application_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        receipt = _identity(receipt_id, "abort_receipt_id")
        document_digest, payload_digest = self._observed_digests(
            observed_document, observed_application_payload
        )
        with self._lock():
            store = self._read_unlocked()
            retired = self._find_receipt_by_intent(store, intent_id)
            if retired is not None:
                if (
                    retired["terminal_state"] != "aborted"
                    or retired["abort_receipt_id"] != receipt
                    or retired["pre_document_artifact"]["sha256"]
                    != document_digest
                    or retired["pre_application_payload_artifact"]["sha256"]
                    != payload_digest
                ):
                    _fail("Abort ACK冲突", "restore_ack_conflict")
                return {
                    "status": "already_aborted",
                    "store_revision": store["store_revision"],
                    "receipt": copy.deepcopy(retired),
                }
            intent = self._find_intent(store, intent_id)
            if intent["abort_receipt_id"] is not None:
                if (
                    intent["abort_receipt_id"] != receipt
                    or document_digest != intent["pre_document_digest"]
                    or payload_digest
                    != intent["pre_application_payload_digest"]
                ):
                    _fail("Abort ACK冲突", "restore_ack_conflict")
                return {
                    "status": "already_aborted",
                    "store_revision": store["store_revision"],
                    "intent": copy.deepcopy(intent),
                }
            self._assert_revision(store, expected_store_revision)
            if (
                intent["state"] not in {"prepared", "recovery_required"}
                or intent["last_proven_state"] != "prepared"
                or intent["renderer_receipt_id"] is not None
            ):
                _fail("Renderer应用后禁止abort", "restore_abort_forbidden")
            current = store["current"]
            if (
                document_digest != intent["pre_document_digest"]
                or payload_digest != intent["pre_application_payload_digest"]
                or current["document_lineage_epoch"] != intent["source_lineage_epoch"]
                or current["revision"] != intent["source_revision"]
                or current["document_artifact"]["sha256"] != document_digest
                or current["application_payload_artifact"]["sha256"] != payload_digest
            ):
                _fail("无法证明Renderer仍为恢复前完整基线", "restore_abort_forbidden")
            intent.update(
                state="aborted",
                last_proven_state="aborted",
                abort_receipt_id=receipt,
                recovery_reason=None,
                outcome_unknown_reason=None,
                updated_at=self._clock(),
            )
            store["store_revision"] += 1
            self._write_unlocked(store)
            return {
                "status": "aborted",
                "store_revision": store["store_revision"],
                "intent": copy.deepcopy(intent),
            }

    def mark_recovery_required(
        self,
        *,
        intent_id: str,
        expected_store_revision: int,
        reason: str,
        manual_verification_id: str | None = None,
    ) -> dict[str, Any]:
        checked_reason = _identity(reason, "recovery_reason")
        checked_verification = _optional_identity(
            manual_verification_id, "manual_verification_id"
        )
        with self._lock():
            store = self._read_unlocked()
            if self._find_receipt_by_intent(store, intent_id) is not None:
                _fail("终态restore不能进入恢复", "restore_state_conflict")
            intent = self._find_intent(store, intent_id)
            if (
                intent["state"] == "recovery_required"
                and intent["recovery_reason"] == checked_reason
            ):
                requested_authorization = (
                    None
                    if checked_verification is None
                    else _digest_text(checked_verification)
                )
                if (
                    intent["recovery_authorization_digest"]
                    != requested_authorization
                ):
                    _fail("恢复核验回执冲突", "restore_ack_conflict")
                return {
                    "status": "already_recovery_required",
                    "store_revision": store["store_revision"],
                    "intent": copy.deepcopy(intent),
                }
            self._assert_revision(store, expected_store_revision)
            if intent["state"] in _TERMINAL_STATES:
                _fail("终态restore不能进入恢复", "restore_state_conflict")
            if intent["state"] == "outcome_unknown" and checked_verification is None:
                _fail(
                    "outcome_unknown需要新证据或人工核验",
                    "restore_evidence_required",
                )
            intent.update(
                state="recovery_required",
                recovery_authorization_digest=(
                    None
                    if checked_verification is None
                    else _digest_text(checked_verification)
                ),
                recovery_reason=checked_reason,
                outcome_unknown_reason=None,
                updated_at=self._clock(),
            )
            store["store_revision"] += 1
            self._write_unlocked(store)
            return {
                "status": "recovery_required",
                "store_revision": store["store_revision"],
                "intent": copy.deepcopy(intent),
            }

    def reconcile_restore(
        self,
        *,
        intent_id: str,
        expected_store_revision: int,
        evidence_id: str,
        observed_document: Mapping[str, Any] | None,
        observed_application_payload: Mapping[str, Any] | None,
        observed_lineage_epoch: int | None,
        observed_revision: int | None,
        renderer_applied_proof: bool = False,
        baseline_advanced_proof: bool = False,
    ) -> dict[str, Any]:
        """Reconcile a nonterminal intent, failing to ``outcome_unknown``.

        Booleans are not sufficient alone: complete target digests and exact
        source ownership must also be observed.  They represent evidence
        already verified by the future Host/Renderer bridge.
        """

        evidence = _identity(evidence_id, "recovery_evidence_id")
        if type(renderer_applied_proof) is not bool or type(baseline_advanced_proof) is not bool:
            _fail("恢复证据标记不合法")
        if baseline_advanced_proof and not renderer_applied_proof:
            _fail("baseline证据不能缺少Renderer证据")
        digests = None
        if observed_document is not None and observed_application_payload is not None:
            digests = self._observed_digests(
                observed_document, observed_application_payload
            )
        elif observed_document is not None or observed_application_payload is not None:
            _fail("恢复证据必须同时包含文档与application payload")
        lineage = (
            None
            if observed_lineage_epoch is None
            else _counter(observed_lineage_epoch, "observed_lineage_epoch")
        )
        revision = (
            None
            if observed_revision is None
            else _counter(observed_revision, "observed_revision")
        )
        evidence_identity_digest = _digest_bytes(
            json_bytes(
                {
                    "evidence_id": evidence,
                    "digests": None if digests is None else list(digests),
                    "observed_lineage_epoch": lineage,
                    "observed_revision": revision,
                    "renderer_applied_proof": renderer_applied_proof,
                    "baseline_advanced_proof": baseline_advanced_proof,
                }
            )
        )
        with self._lock():
            store = self._read_unlocked()
            if self._find_receipt_by_intent(store, intent_id) is not None:
                _fail("终态restore不能重新核验", "restore_state_conflict")
            intent = self._find_intent(store, intent_id)
            if renderer_applied_proof or baseline_advanced_proof:
                target = self._find_checkpoint(
                    store, intent["target_checkpoint_id"]
                )
                self._assert_application_payload_reusable(
                    store["current"], target
                )
            if intent["last_recovery_evidence_id"] == evidence:
                if (
                    intent["last_recovery_identity_digest"]
                    != evidence_identity_digest
                ):
                    _fail("恢复证据ID已经绑定其他事实", "restore_ack_conflict")
                return {
                    "status": "already_" + intent["last_recovery_result"],
                    "store_revision": store["store_revision"],
                    "intent": copy.deepcopy(intent),
                    "writes_frozen": intent["state"] not in _TERMINAL_STATES,
                    "queue_promotion_frozen": intent["state"]
                    not in _TERMINAL_STATES,
                }
            self._assert_revision(store, expected_store_revision)
            if intent["state"] != "recovery_required":
                _fail("restore不在可核验状态", "restore_state_conflict")
            pre_match = (
                digests
                == (
                    intent["pre_document_digest"],
                    intent["pre_application_payload_digest"],
                )
                and lineage == intent["source_lineage_epoch"]
                and revision == intent["source_revision"]
            )
            target_source_match = (
                digests
                == (
                    intent["target_document_digest"],
                    intent["target_application_payload_digest"],
                )
                and lineage == intent["source_lineage_epoch"]
                and revision == intent["source_revision"]
            )
            if pre_match and intent["last_proven_state"] == "prepared":
                intent.update(
                    state="recovery_required",
                    recovery_reason=f"verified_pre:{evidence}",
                    outcome_unknown_reason=None,
                    updated_at=self._clock(),
                )
                status = "prepared_state_verified"
            elif (
                target_source_match
                and renderer_applied_proof
                and not (
                    intent["last_proven_state"] == "baseline_advanced"
                    and not baseline_advanced_proof
                )
            ):
                intent["renderer_receipt_id"] = intent["renderer_receipt_id"] or evidence
                if baseline_advanced_proof:
                    intent.update(
                        state="baseline_advanced",
                        last_proven_state="baseline_advanced",
                        baseline_receipt_id=intent["baseline_receipt_id"] or evidence,
                    )
                    status = "baseline_advanced"
                else:
                    intent.update(
                        state="renderer_applied",
                        last_proven_state="renderer_applied",
                    )
                    status = "renderer_applied"
                intent.update(
                    recovery_reason=None,
                    outcome_unknown_reason=None,
                    updated_at=self._clock(),
                )
            else:
                intent.update(
                    state="outcome_unknown",
                    outcome_unknown_reason=f"insufficient_evidence:{evidence}",
                    recovery_reason=None,
                    updated_at=self._clock(),
                )
                status = "outcome_unknown"
            intent.update(
                last_recovery_evidence_id=evidence,
                last_recovery_identity_digest=evidence_identity_digest,
                last_recovery_result=status,
            )
            store["store_revision"] += 1
            self._write_unlocked(store)
            return {
                "status": status,
                "store_revision": store["store_revision"],
                "intent": copy.deepcopy(intent),
                # Every non-terminal restore state owns the document write
                # gate.  ``outcome_unknown`` is not special here: verified
                # prepared/renderer/baseline states still need the same freeze
                # until an explicit commit or abort becomes durable.
                "writes_frozen": intent["state"] not in _TERMINAL_STATES,
                "queue_promotion_frozen": intent["state"]
                not in _TERMINAL_STATES,
            }


__all__ = [
    "ARTIFACT_CONTRACT",
    "CHECKPOINT_CONTRACT",
    "CHECKPOINT_TYPE",
    "MAX_APPLICATION_PAYLOAD_BYTES",
    "MAX_AUTOMATIC_ARTIFACT_BYTES",
    "MAX_AUTOMATIC_CHECKPOINTS",
    "MAX_DOCUMENT_BYTES",
    "MAX_RESTORE_RECEIPTS",
    "MAX_TERMINAL_RESTORE_INTENTS",
    "QUEUE_REBIND_CONTRACT",
    "RESTORE_INTENT_CONTRACT",
    "RESTORE_RECEIPT_CONTRACT",
    "STORE_CONTRACT",
    "NativeDocumentRestorePointStore",
]
