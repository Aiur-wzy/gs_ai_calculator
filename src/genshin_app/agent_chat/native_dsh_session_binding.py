"""Durable App-domain binding for one DSH conversation session.

The DSH persistence store owns model messages, reasoning, tool history and
compaction.  The App continues to own the whiteboard/candidate checkpoint and
every side-effect settlement.  This small registry is the only durable link
between those authorities; it deliberately stores neither DSH event payloads
nor a filesystem path to DSH storage.

Bindings live outside individual conversation directories so an App-side
delete can leave a tombstone without deleting, opening or rewriting DSH files.
Every mutation is a single-file atomic publication guarded by a per-binding
cross-process lock.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from genshin_app.equipment.files import json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_db.locking import file_lock

from .native_dsh_checkpoint import (
    compare_native_dsh_checkpoints,
    normalize_native_dsh_checkpoint,
)
from .sessions import safe_path, write_owned

CONTRACT = "genshin-native-dsh-domain-binding-v2"
REGISTRY_DIRECTORY = ".native-dsh-domain-bindings-v1"
MAX_BINDING_BYTES = 64 * 1024
MAX_SAFE_INTEGER = 2**53 - 1

AUTHORITATIVE_NEW = "authoritative_new"
LEGACY_COMPATIBILITY = "legacy_compatibility"
AUTHORITATIVE_DURABLE = "authoritative_durable"
DSH_SESSION_RESERVED = "dsh_session_reserved"
LEGACY_INCOMPATIBLE = "legacy_incompatible"

ACTIVE = "active"
ARCHIVED = "archived"
DELETE_PENDING = "delete_pending"
TOMBSTONED = "tombstoned"

_ORIGINS = frozenset({AUTHORITATIVE_NEW, LEGACY_COMPATIBILITY})
_HISTORY_STATES = frozenset({
    AUTHORITATIVE_DURABLE,
    DSH_SESSION_RESERVED,
    LEGACY_INCOMPATIBLE,
})
_LIFECYCLE_STATES = frozenset({ACTIVE, ARCHIVED, DELETE_PENDING, TOMBSTONED})
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ROOT_KEYS = {
    "contract",
    "conversation_id",
    "dsh_session_id",
    "binding_origin",
    "history_state",
    "lifecycle_state",
    "revision",
    "domain_checkpoint",
    "native_checkpoint_binding",
    "dsh_checkpoint",
    "delete_intent",
}
_DOMAIN_KEYS = {
    "document_id",
    "candidate_revision",
    "stage_revision",
    "database_fingerprint",
    "receipt_fingerprint",
}
_DELETE_INTENT_KEYS = {"intent_id", "prior_state"}


def _fail(message: str, reason: str = "native_dsh_binding_invalid") -> None:
    raise PlanError(message, reason)


def _identity(value: object, field: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        _fail(f"{field}不符合DSH绑定身份语法")
    return value


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        _fail(f"{field}必须是小写SHA-256")
    return value


def _counter(value: object, field: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SAFE_INTEGER:
        _fail(f"{field}必须是非负安全整数")
    return value


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("DSH绑定JSON包含重复字段")
        result[key] = value
    return result


def _native_projection(record: Mapping[str, Any]) -> tuple[str, dict, str]:
    """Project only App-owned public domain facts from a native session."""

    if not isinstance(record, Mapping):
        _fail("原生会话记录不是对象")
    conversation_id = _identity(record.get("conversation_id"), "conversation_id")
    checkpoint_binding = _digest(
        record.get("checkpoint_binding"), "native_checkpoint_binding"
    )
    raw_domain = record.get("domain_checkpoint")
    if not isinstance(raw_domain, Mapping) or not _DOMAIN_KEYS.issubset(raw_domain):
        _fail("原生会话缺少公开领域检查点")
    domain = {name: copy.deepcopy(raw_domain[name]) for name in _DOMAIN_KEYS}
    _identity(domain.get("document_id"), "document_id")
    _counter(domain.get("candidate_revision"), "candidate_revision")
    _counter(domain.get("stage_revision"), "stage_revision")
    _digest(domain.get("database_fingerprint"), "database_fingerprint")
    if domain.get("receipt_fingerprint") is not None:
        _digest(domain["receipt_fingerprint"], "receipt_fingerprint")
    # writer_fence is intentionally excluded.  The native checkpoint binding
    # commits to it without copying the capability into the global registry.
    return conversation_id, domain, checkpoint_binding


def normalize_native_dsh_binding(
    value: Mapping[str, Any], *, conversation_id: str | None = None
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("DSH领域绑定字段不合法")
    if value.get("contract") != CONTRACT:
        _fail("DSH领域绑定版本不受支持", "native_dsh_binding_version_incompatible")
    if set(value) != _ROOT_KEYS:
        _fail("DSH领域绑定字段不合法")
    row = copy.deepcopy(dict(value))
    checked_conversation = _identity(row.get("conversation_id"), "conversation_id")
    if conversation_id is not None and checked_conversation != _identity(
        conversation_id, "conversation_id"
    ):
        _fail("DSH领域绑定不属于当前对话", "stale_context")
    _identity(row.get("dsh_session_id"), "dsh_session_id")
    origin = row.get("binding_origin")
    history = row.get("history_state")
    lifecycle = row.get("lifecycle_state")
    if origin not in _ORIGINS or history not in _HISTORY_STATES:
        _fail("DSH领域绑定来源不合法")
    if origin == AUTHORITATIVE_NEW and history not in {
        DSH_SESSION_RESERVED,
        AUTHORITATIVE_DURABLE,
    }:
        _fail("DSH历史状态与绑定来源不一致")
    if origin == LEGACY_COMPATIBILITY and history != LEGACY_INCOMPATIBLE:
        _fail("DSH历史状态与绑定来源不一致")
    dsh_checkpoint = row.get("dsh_checkpoint")
    if history == AUTHORITATIVE_DURABLE:
        if not isinstance(dsh_checkpoint, Mapping):
            _fail("已激活DSH会话缺少持久化检查点")
        row["dsh_checkpoint"] = normalize_native_dsh_checkpoint(
            dsh_checkpoint,
            expected_session_id=row["dsh_session_id"],
        )
    elif dsh_checkpoint is not None:
        _fail("未激活DSH会话不能携带持久化检查点")
    if lifecycle not in _LIFECYCLE_STATES:
        _fail("DSH领域绑定生命周期不合法")
    _counter(row.get("revision"), "revision")
    domain = row.get("domain_checkpoint")
    if not isinstance(domain, Mapping) or set(domain) != _DOMAIN_KEYS:
        _fail("DSH领域绑定的公开领域检查点不合法")
    _native_projection({
        "conversation_id": checked_conversation,
        "checkpoint_binding": row.get("native_checkpoint_binding"),
        "domain_checkpoint": {**domain, "writer_fence": "not-persisted"},
    })
    delete_intent = row.get("delete_intent")
    if lifecycle == DELETE_PENDING:
        if (
            not isinstance(delete_intent, Mapping)
            or set(delete_intent) != _DELETE_INTENT_KEYS
        ):
            _fail("DSH删除前置状态缺少意图")
        _identity(delete_intent.get("intent_id"), "delete_intent_id")
        if delete_intent.get("prior_state") not in {ACTIVE, ARCHIVED}:
            _fail("DSH删除前置状态的来源不合法")
        row["delete_intent"] = copy.deepcopy(dict(delete_intent))
    elif delete_intent is not None:
        _fail("非删除前置状态不能携带删除意图")
    try:
        if len(json_bytes(row)) > MAX_BINDING_BYTES:
            _fail("DSH领域绑定超过保存上限", "agent_size_limit")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise PlanError(
            "DSH领域绑定不是严格JSON", "native_dsh_binding_invalid"
        ) from exc
    return row


class NativeDshSessionBindingStore:
    """Per-conversation durable mapping; never opens or deletes DSH storage."""

    def __init__(self, sessions_root: str | Path, *, conversation_id: str) -> None:
        self.sessions_root = Path(sessions_root).absolute()
        self.conversation_id = _identity(conversation_id, "conversation_id")
        key = hashlib.sha256(self.conversation_id.encode("utf-8")).hexdigest()
        self.registry_root = safe_path(self.sessions_root, REGISTRY_DIRECTORY)
        self.filename = f"{key}.json"
        self.lock_filename = f".{key}.lock"

    @classmethod
    def for_session_root(
        cls, session_root: str | Path, *, conversation_id: str
    ) -> NativeDshSessionBindingStore:
        return cls(Path(session_root).absolute().parent, conversation_id=conversation_id)

    def _ensure_registry(self) -> None:
        self.sessions_root.mkdir(parents=True, exist_ok=True)
        path = safe_path(self.sessions_root, REGISTRY_DIRECTORY)
        path.mkdir(exist_ok=True)
        if not path.is_dir() or path.is_symlink():
            _fail("DSH绑定仓目录不安全", "agent_path_rejected")

    @property
    def path(self) -> Path:
        return safe_path(self.registry_root, self.filename)

    @property
    def lock_path(self) -> Path:
        return safe_path(self.registry_root, self.lock_filename)

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(MAX_BINDING_BYTES + 1)
        except FileNotFoundError:
            raise PlanError(
                "DSH领域绑定不存在", "native_dsh_binding_missing"
            ) from None
        if len(raw) > MAX_BINDING_BYTES:
            _fail("DSH领域绑定超过读取上限", "agent_size_limit")
        try:
            value = json.loads(
                raw.decode("utf-8", errors="strict"),
                object_pairs_hook=_strict_object,
                parse_constant=lambda _value: _fail("DSH绑定包含非有限数值"),
            )
        except PlanError:
            raise
        except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
            raise PlanError(
                "DSH领域绑定不是严格JSON", "native_dsh_binding_invalid"
            ) from exc
        return normalize_native_dsh_binding(
            value, conversation_id=self.conversation_id
        )

    def _write_unlocked(self, value: Mapping[str, Any]) -> dict[str, Any]:
        checked = normalize_native_dsh_binding(
            value, conversation_id=self.conversation_id
        )
        write_owned(
            self.registry_root,
            self.filename,
            checked,
            MAX_BINDING_BYTES,
        )
        return copy.deepcopy(checked)

    def cold_read(self) -> dict[str, Any]:
        """Read without creating a registry, runtime or provider request."""

        if not self.registry_root.is_dir():
            raise PlanError(
                "DSH领域绑定不存在", "native_dsh_binding_missing"
            )
        return self._read_unlocked()

    def _new(
        self,
        native_record: Mapping[str, Any],
        *,
        origin: str,
        history_state: str,
    ) -> dict[str, Any]:
        conversation_id, domain, native_binding = _native_projection(native_record)
        if conversation_id != self.conversation_id:
            _fail("DSH绑定与原生会话身份不一致", "stale_context")
        return normalize_native_dsh_binding({
            "contract": CONTRACT,
            "conversation_id": conversation_id,
            "dsh_session_id": f"dsh-session-{uuid.uuid4()}",
            "binding_origin": origin,
            "history_state": history_state,
            "lifecycle_state": ACTIVE,
            "revision": 0,
            "domain_checkpoint": domain,
            "native_checkpoint_binding": native_binding,
            "dsh_checkpoint": None,
            "delete_intent": None,
        }, conversation_id=conversation_id)

    def create_authoritative(
        self, native_record: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Atomically reserve the durable DSH identity for a new App chat."""

        self._ensure_registry()
        with file_lock(self.lock_path):
            if self.path.exists():
                current = self._read_unlocked()
                if current["binding_origin"] != AUTHORITATIVE_NEW:
                    _fail("DSH绑定来源冲突", "file_conflict")
                return self._checkpoint_unlocked(current, native_record)
            return self._write_unlocked(self._new(
                native_record,
                origin=AUTHORITATIVE_NEW,
                history_state=DSH_SESSION_RESERVED,
            ))

    def cold_recover(
        self, native_record: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Recover a cold mapping without claiming legacy DSH history exists.

        Missing mappings belong to sessions created before this contract.  A
        stable identity is recorded only so the incompatibility decision is
        durable.  It is never opened, seeded, or resumed as a DSH session.
        """

        self._ensure_registry()
        with file_lock(self.lock_path):
            if not self.path.exists():
                return self._write_unlocked(self._new(
                    native_record,
                    origin=LEGACY_COMPATIBILITY,
                    history_state=LEGACY_INCOMPATIBLE,
                ))
            current = self._read_unlocked()
            if current["lifecycle_state"] == TOMBSTONED:
                _fail("已删除对话不能重新绑定DSH会话", "stale_context")
            if current["lifecycle_state"] == DELETE_PENDING:
                # The runtime may already have removed some or all DSH-owned
                # storage before the Host received its durable receipt.  Only
                # the deletion path may retry that exact intent; activation
                # must never silently turn an ambiguous delete back into an
                # active conversation.
                _fail("DSH会话删除需要恢复后才能重新绑定", "stale_context")
            return self._checkpoint_unlocked(current, native_record)

    def _checkpoint_unlocked(
        self,
        current: Mapping[str, Any],
        native_record: Mapping[str, Any],
    ) -> dict[str, Any]:
        conversation_id, domain, native_binding = _native_projection(native_record)
        if conversation_id != self.conversation_id:
            _fail("DSH绑定与原生会话身份不一致", "stale_context")
        checked = normalize_native_dsh_binding(
            current, conversation_id=self.conversation_id
        )
        if checked["lifecycle_state"] in {DELETE_PENDING, TOMBSTONED}:
            _fail("删除中的DSH绑定不能推进领域检查点", "stale_context")
        previous_domain = checked["domain_checkpoint"]
        if (
            domain["document_id"] != previous_domain["document_id"]
            or domain["database_fingerprint"]
            != previous_domain["database_fingerprint"]
            or domain["candidate_revision"]
            < previous_domain["candidate_revision"]
            or domain["stage_revision"] < previous_domain["stage_revision"]
        ):
            _fail("DSH绑定不能改写或回退App领域身份", "stale_context")
        if (
            checked["domain_checkpoint"] == domain
            and checked["native_checkpoint_binding"] == native_binding
        ):
            return copy.deepcopy(checked)
        proposed = copy.deepcopy(checked)
        proposed["domain_checkpoint"] = domain
        proposed["native_checkpoint_binding"] = native_binding
        proposed["revision"] += 1
        return self._write_unlocked(proposed)

    def checkpoint(
        self,
        native_record: Mapping[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        self._ensure_registry()
        with file_lock(self.lock_path):
            current = self._read_unlocked()
            if expected_revision is not None and current["revision"] != _counter(
                expected_revision, "expected_revision"
            ):
                _fail("DSH领域绑定修订已变化", "stale_context")
            return self._checkpoint_unlocked(current, native_record)

    def activate_reserved_session(
        self,
        *,
        dsh_checkpoint: Mapping[str, Any],
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Mark a new DSH session durable after its explicit flush receipt.

        A provider completion is not sufficient.  The digest must come from
        the runtime lifecycle acknowledgement that the DSH JSONL checkpoint
        for this exact session was durably flushed.
        """

        checked_checkpoint = normalize_native_dsh_checkpoint(
            dsh_checkpoint,
        )
        self._ensure_registry()
        with file_lock(self.lock_path):
            current = self._read_unlocked()
            if expected_revision is not None and current["revision"] != _counter(
                expected_revision, "expected_revision"
            ):
                _fail("DSH领域绑定修订已变化", "stale_context")
            if current["lifecycle_state"] in {DELETE_PENDING, TOMBSTONED}:
                _fail("删除中的DSH绑定不能激活", "stale_context")
            if current["binding_origin"] != AUTHORITATIVE_NEW:
                _fail("旧会话不能激活为新的DSH历史", "stale_context")
            if current["history_state"] == AUTHORITATIVE_DURABLE:
                compare_native_dsh_checkpoints(
                    current["dsh_checkpoint"], checked_checkpoint
                )
                return copy.deepcopy(current)
            if current["history_state"] != DSH_SESSION_RESERVED:
                _fail("DSH预留状态不能激活", "stale_context")
            proposed = copy.deepcopy(current)
            if checked_checkpoint["session_id"] != current["dsh_session_id"]:
                _fail("DSH检查点不属于预留会话", "stale_context")
            proposed["history_state"] = AUTHORITATIVE_DURABLE
            proposed["dsh_checkpoint"] = checked_checkpoint
            proposed["revision"] += 1
            return self._write_unlocked(proposed)

    def observe_dsh_checkpoint(
        self,
        *,
        dsh_checkpoint: Mapping[str, Any],
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Advance the path-free receipt for an already durable DSH session."""

        checked_checkpoint = normalize_native_dsh_checkpoint(dsh_checkpoint)
        self._ensure_registry()
        with file_lock(self.lock_path):
            current = self._read_unlocked()
            if expected_revision is not None and current["revision"] != _counter(
                expected_revision, "expected_revision"
            ):
                _fail("DSH领域绑定修订已变化", "stale_context")
            if current["lifecycle_state"] in {DELETE_PENDING, TOMBSTONED}:
                _fail("删除中的DSH绑定不能更新检查点", "stale_context")
            if current["history_state"] != AUTHORITATIVE_DURABLE:
                _fail("未激活DSH会话不能更新检查点", "stale_context")
            compare_native_dsh_checkpoints(
                current["dsh_checkpoint"], checked_checkpoint
            )
            if current["dsh_checkpoint"] == checked_checkpoint:
                return copy.deepcopy(current)
            proposed = copy.deepcopy(current)
            proposed["dsh_checkpoint"] = checked_checkpoint
            proposed["revision"] += 1
            return self._write_unlocked(proposed)

    def set_archived(
        self,
        *,
        archived: bool,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        if type(archived) is not bool:
            _fail("DSH归档状态不合法")
        self._ensure_registry()
        with file_lock(self.lock_path):
            current = self._read_unlocked()
            if expected_revision is not None and current["revision"] != _counter(
                expected_revision, "expected_revision"
            ):
                _fail("DSH领域绑定修订已变化", "stale_context")
            if current["lifecycle_state"] in {DELETE_PENDING, TOMBSTONED}:
                _fail("删除中的DSH绑定不能改变归档状态", "stale_context")
            target = ARCHIVED if archived else ACTIVE
            if current["lifecycle_state"] == target:
                return copy.deepcopy(current)
            proposed = copy.deepcopy(current)
            proposed["lifecycle_state"] = target
            proposed["revision"] += 1
            return self._write_unlocked(proposed)

    def prepare_delete(
        self,
        *,
        intent_id: str,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        intent_id = _identity(intent_id, "delete_intent_id")
        self._ensure_registry()
        with file_lock(self.lock_path):
            current = self._read_unlocked()
            if current["lifecycle_state"] == DELETE_PENDING:
                if current["delete_intent"]["intent_id"] == intent_id:
                    return copy.deepcopy(current)
                _fail("DSH绑定已有其他删除意图", "file_conflict")
            if current["lifecycle_state"] == TOMBSTONED:
                _fail("DSH绑定已经进入删除墓碑", "stale_context")
            if expected_revision is not None and current["revision"] != _counter(
                expected_revision, "expected_revision"
            ):
                _fail("DSH领域绑定修订已变化", "stale_context")
            proposed = copy.deepcopy(current)
            proposed["lifecycle_state"] = DELETE_PENDING
            proposed["delete_intent"] = {
                "intent_id": intent_id,
                "prior_state": current["lifecycle_state"],
            }
            proposed["revision"] += 1
            return self._write_unlocked(proposed)

    def cancel_delete(self, *, intent_id: str) -> dict[str, Any]:
        intent_id = _identity(intent_id, "delete_intent_id")
        self._ensure_registry()
        with file_lock(self.lock_path):
            current = self._read_unlocked()
            if current["lifecycle_state"] != DELETE_PENDING:
                return copy.deepcopy(current)
            if current["delete_intent"]["intent_id"] != intent_id:
                _fail("DSH删除意图不匹配", "stale_context")
            proposed = copy.deepcopy(current)
            proposed["lifecycle_state"] = current["delete_intent"]["prior_state"]
            proposed["delete_intent"] = None
            proposed["revision"] += 1
            return self._write_unlocked(proposed)

    def tombstone(self, *, intent_id: str) -> dict[str, Any]:
        """Finalize the binding after the DSH owner proved durable deletion."""

        intent_id = _identity(intent_id, "delete_intent_id")
        self._ensure_registry()
        with file_lock(self.lock_path):
            current = self._read_unlocked()
            if current["lifecycle_state"] == TOMBSTONED:
                return copy.deepcopy(current)
            if (
                current["lifecycle_state"] != DELETE_PENDING
                or current["delete_intent"]["intent_id"] != intent_id
            ):
                _fail("DSH绑定没有匹配的删除前置状态", "stale_context")
            proposed = copy.deepcopy(current)
            proposed["lifecycle_state"] = TOMBSTONED
            proposed["delete_intent"] = None
            proposed["revision"] += 1
            return self._write_unlocked(proposed)

    def runtime_deletion_precondition(self) -> dict[str, Any]:
        """Return the fail-closed request for the DSH owner runtime."""

        current = self.cold_read()
        intent = current["delete_intent"]
        pending = current["lifecycle_state"] == DELETE_PENDING
        return {
            "conversation_id": current["conversation_id"],
            "dsh_session_id": current["dsh_session_id"],
            "lifecycle_state": current["lifecycle_state"],
            "binding_revision": current["revision"],
            "delete_intent_id": None if intent is None else intent["intent_id"],
            "runtime_storage_action": (
                "quiesce_tombstone_delete" if pending else "none"
            ),
            # The runtime receipt, not this binding record, releases App files.
            "can_delete_app_files": False,
        }


__all__ = [
    "ACTIVE",
    "ARCHIVED",
    "AUTHORITATIVE_DURABLE",
    "AUTHORITATIVE_NEW",
    "CONTRACT",
    "DELETE_PENDING",
    "DSH_SESSION_RESERVED",
    "LEGACY_COMPATIBILITY",
    "LEGACY_INCOMPATIBLE",
    "MAX_BINDING_BYTES",
    "REGISTRY_DIRECTORY",
    "TOMBSTONED",
    "NativeDshSessionBindingStore",
    "normalize_native_dsh_binding",
]
