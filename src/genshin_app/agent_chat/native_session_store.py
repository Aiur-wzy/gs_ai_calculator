"""App-owned domain metadata for durable native-Agent conversations.

DSH is the sole durable authority for messages, raw reasoning, model attempts,
tool history, questions and context replacements.  This module stores only the
App/DSH binding-adjacent metadata needed to protect the game-domain checkpoint:
turn counters, stage, document lineage, release identities, settlement refs and
writer fences.  It never starts a sidecar, talks to a provider, or reconstructs
model history.  ``visible_messages`` remains an empty legacy compatibility
field while old fixtures and explicit read-only diagnostics are retired; the
production coordinator never appends to it and resume seeds always omit it.

Writer takeover is crash recoverable.  The pending old/new fence pair is
persisted before the document lease is changed.  A restart can consequently
attach to either the old lease or an already-committed new lease and finish
the same compare-and-swap operation without inventing a third fence.
"""
from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from genshin_app.equipment.files import fingerprint, json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_db.locking import LockBusyError, file_lock

from .native_app_call_ledger import (
    INFLIGHT as APP_CALL_INFLIGHT,
)
from .native_app_call_ledger import (
    RECOVERY_APPLIED,
    RECOVERY_CONFLICT,
    NativeAppCallLedger,
)
from .native_app_call_ledger import (
    SETTLED_FAILURE as APP_CALL_SETTLED_FAILURE,
)
from .native_app_call_ledger import (
    SETTLED_SUCCESS as APP_CALL_SETTLED_SUCCESS,
)
from .native_app_call_ledger import (
    UNKNOWN as APP_CALL_UNKNOWN,
)
from .native_app_tool_effects import ALL_EFFECTS as _APP_CALL_EFFECTS
from .native_app_tool_effects import EXTERNAL_EFFECTS
from .native_compatibility_identity import current_native_compatibility_identity
from .native_write_ledger import SessionWriteAuthority, normalize_write_ledger
from .native_write_ledger import _validate_entry as _validate_write_entry
from .sessions import safe_path, write_owned

CONTRACT = "genshin-native-session-v4"
SESSION_FILE = "native-session-v2.json"
SESSION_LOCK_FILE = ".native-session-v2.lock"
WRITE_LEDGER_FILE = "native-write-ledger-v1.json"
MAX_SESSION_BYTES = 128 * 1024 * 1024
MAX_WRITE_LEDGER_BYTES = 128 * 1024 * 1024
MAX_VISIBLE_MESSAGES = 256
MAX_VISIBLE_MESSAGE_CHARS = 32_000
MAX_VISIBLE_TEXT_CHARS = 512_000
MAX_VISIBLE_REDACTION_INPUT_CHARS = 128_000
MAX_SETTLEMENT_REFS = 262_144
MAX_RESOURCE_REFS = 64
MAX_AUXILIARY_REFS = 262_144
MAX_USAGE_RECORDS = 65_536
MAX_RESUME_SETTLEMENTS = 64
MAX_RESUME_RESOURCE_REFS = 64
MAX_RESUME_VISIBLE_MESSAGES = 64
MAX_RESUME_VISIBLE_TEXT_CHARS = 64_000
JSON_SAFE_INTEGER_MAX = 2**53 - 1

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_RESEARCH_REF = re.compile(
    r"research\.v1\.(0|[1-9][0-9]{0,15})\.([0-9a-f]{64})\Z"
)
_APP_ACTION_REF = re.compile(
    r"appaction\.(0|[1-9][0-9]{0,15})\.([0-9a-f]{64})\Z"
)
_APP_CALL_REF = re.compile(
    r"appcalls\.v1\.(0|[1-9][0-9]{0,15})\.([0-9a-f]{64})\Z"
)
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_WRITER_FENCE = re.compile(r"wf_[A-Za-z0-9_-]{43}\Z")
_WRITER_FENCE_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_-])wf_[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])"
)
_SENSITIVE_PREFIXES = (
    "access-token:", "api-key:", "api_key:", "apikey:",
    "authorization:", "basic:", "bearer:", "cookie:", "credential:",
    "password:", "proxy-authorization:", "secret:", "sk-", "token:",
    "wf_", "x-api-key:",
)
_COOKIE = re.compile(r"(?i)\b(?:cookie|set-cookie)\s*:\s*[^\r\n]+")
_SECRET = re.compile(
    r"(?ix)(?:\b(?:api[\s_-]*key|authorization|access[\s_-]*token|token|"
    r"secret|password|passwd|pwd)\b\s*[:=]\s*(?:bearer\s+)?"
    r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;，。；、]+)"
    r"|\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|\bsk-[A-Za-z0-9_-]{8,})"
)
_FILE_URI = re.compile(r"(?i)\bfile:(?://)?[^\s<>\"'，。；、]+")
_UNC_PATH = re.compile(
    r"(?<![:/\\A-Za-z0-9])(?:\\\\|//)[^\r\n<>\"'，。；、]+"
)
_WINDOWS_PATH = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/])[^\r\n<>\"'，。；、]+"
)
_ROOTED_BACKSLASH_PATH = re.compile(
    r"(?<![A-Za-z0-9\\])\\(?![\\\s])"
    r"[A-Za-z0-9_. -]+(?:\\[^\r\n<>\"'，。；、]+)*"
)
_POSIX_PATH = re.compile(
    r"(?ix)(?<![:/\\A-Za-z0-9_])/(?!/)"
    r"(?:(?:home|users|root|tmp|var|etc|opt|usr|mnt|volumes|private)(?:/|\b)"
    r"|[A-Za-z0-9_. -]+/)"
    r"[^\r\n<>\"'，。；、]*"
)
_REDACTED_SECRET = "[已隐藏凭据]"
_REDACTED_PATH = "[已隐藏本地路径]"

_ROOT_KEYS = {
    "contract", "conversation_id", "activation_generation",
    "connection_kind", "runtime_generation", "locked_provider",
    "locked_model", "locked_reasoning_effort", "visible_messages",
    "visible_message_evicted_count", "domain_checkpoint",
    "accepted_user_turn_count", "current_workflow_stage",
    "runtime_release_hash", "prompt_profile_hash", "tool_manifest_hash",
    "release_pair_identity_hash", "app_artifact_identity_hash",
    "skill_bundle_hash", "checkpoint_binding",
    "tool_settlement_refs", "research_ledger_refs", "app_action_refs",
    "usage_records", "last_turn", "writer_takeover",
    "writer_takeover_receipt",
}
_DOMAIN_KEYS = {
    "document_id", "candidate_revision", "stage_revision",
    "database_fingerprint", "receipt_fingerprint", "writer_fence",
}
_MESSAGE_KEYS = {"message_id", "role", "text", "workflow_stage", "user_turn"}
_SETTLEMENT_KEYS = {
    "call_id", "args_hash", "before_fingerprint", "after_fingerprint",
    "status", "effect", "resource_refs", "workflow_stage",
}
_LAST_TURN_KEYS = {
    "turn_id", "status", "provider_request_possible", "terminal_reason",
}
_TAKEOVER_KEYS = {
    "operation_id", "expected_activation_generation", "activation_generation",
    "expected_fence", "proposed_fence",
}
_USAGE_KEYS = {
    "turn_id", "input_tokens", "output_tokens", "reasoning_tokens",
    "cached_input_tokens", "total_tokens",
}
_WRITE_IDENTITY_KEYS = {
    "conversation_id", "turn_id", "step_id", "epoch_id", "document_id",
    "call_id", "tool_name", "canonical_args_hash", "runtime_generation",
    "candidate_revision", "stage_revision", "database_fingerprint",
    "tool_manifest_hash", "writer_fence",
}
_WRITE_ENTRY_KEYS = {
    "identity", "document_id", "state", "started_at", "settled_at",
    "before_fingerprint", "after_fingerprint", "result_json", "result_hash",
    "result_code", "terminal_reason", "post_candidate_revision",
    "post_stage_revision", "post_tool_manifest_hash",
}
_ACTIVATION_OBSERVED_KEYS = {
    "connection_kind", "runtime_generation", "locked_provider", "locked_model",
    "locked_reasoning_effort", "document_id", "candidate_revision",
    "stage_revision", "database_fingerprint", "receipt_fingerprint",
    "runtime_release_hash", "prompt_profile_hash", "tool_manifest_hash",
    "release_pair_identity_hash", "app_artifact_identity_hash",
    "skill_bundle_hash",
}
_CONNECTIONS = frozenset({"cli", "native_api"})
_RUNTIMES = frozenset({"python_v1", "dsh_v2"})
_ROLES = frozenset({"user", "assistant", "notice"})
_TURN_STATES = frozenset({"idle", "completed", "interrupted", "cancelled", "error"})
_SETTLEMENT_STATES = frozenset({
    "settled_success", "settled_failure", "unknown_after_interruption",
})
_MIGRATION_FIELDS = frozenset({
    "database_fingerprint", "runtime_release_hash", "prompt_profile_hash", "tool_manifest_hash",
})
_RLOCK_TYPE = type(threading.RLock())
_APP_CALL_NO_DOMAIN_CHANGE = frozenset({"none", *EXTERNAL_EFFECTS})


@dataclass(frozen=True)
class NativeAuxiliaryRefsV1:
    """Current path-free anchors for the two conversation-owned ledgers."""

    research_ledger_ref: str
    app_action_ref: str
    app_call_ledger_ref: str | None = None


def _fail(message: str, reason: str = "agent_checkpoint_invalid", details=None) -> None:
    raise PlanError(message, reason, details)


def _snapshot(value: Any, name: str) -> Any:
    """Detach caller-owned containers before validation or persistence."""
    try:
        return copy.deepcopy(value)
    except (TypeError, ValueError, RecursionError) as exc:
        raise PlanError(
            f"{name}无法安全复制", "agent_checkpoint_invalid",
        ) from exc


def _commit_record(target: dict, snapshot: dict) -> None:
    if not isinstance(target, dict):
        _fail("原生会话记录必须是JSON对象")
    target.clear()
    target.update(copy.deepcopy(snapshot))


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _fail("原生会话JSON包含重复字段")
        value[key] = item
    return value


def _read_record(root: Path) -> dict:
    path = safe_path(root, SESSION_FILE)
    with path.open("rb") as stream:
        raw = stream.read(MAX_SESSION_BYTES + 1)
    if len(raw) > MAX_SESSION_BYTES:
        _fail("原生会话记录超过读取上限", "agent_size_limit")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: _fail("原生会话JSON包含非有限数值"),
        )
    except PlanError:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise PlanError(
            "原生会话文件不是严格JSON", "agent_checkpoint_invalid",
        ) from exc
    if not isinstance(value, dict):
        _fail("原生会话根必须是JSON对象")
    return value


def _read_write_ledger(root: Path) -> dict:
    path = safe_path(root, WRITE_LEDGER_FILE)
    with path.open("rb") as stream:
        raw = stream.read(MAX_WRITE_LEDGER_BYTES + 1)
    if len(raw) > MAX_WRITE_LEDGER_BYTES:
        _fail("候选写账本超过读取上限", "agent_size_limit")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: _fail("候选写账本包含非有限数值"),
        )
    except PlanError:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise PlanError(
            "候选写账本不是严格JSON", "agent_checkpoint_invalid",
        ) from exc
    if not isinstance(value, dict):
        _fail("候选写账本根必须是JSON对象")
    normalize_write_ledger(value)
    return value


def _lock_existing_handle(handle, *, unlock: bool = False) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(
            handle.fileno(), msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1,
        )
    else:
        import fcntl
        fcntl.flock(
            handle.fileno(),
            fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB,
        )


@contextlib.contextmanager
def _existing_file_lock(path: Path, timeout: float = 30):
    """Join an existing OS lock without ever creating or writing its file."""
    try:
        handle = path.open("rb")
    except FileNotFoundError:
        yield False
        return
    deadline = time.monotonic() + timeout
    locked = False
    try:
        while True:
            try:
                _lock_existing_handle(handle)
                locked = True
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise LockBusyError(
                        f"操作正在进行，请稍后重试: {path.name}",
                    ) from exc
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        yield True
    finally:
        if locked:
            _lock_existing_handle(handle, unlock=True)
        handle.close()


def _identity(value: Any, name: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        _fail(f"{name}不符合原生会话身份语法")
    if value.lower().startswith(_SENSITIVE_PREFIXES):
        _fail(f"{name}不能承载凭据或秘密")
    return value


def _optional_identity(value: Any, name: str) -> str | None:
    return None if value is None else _identity(value, name)


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        _fail(f"{name}必须是小写SHA-256")
    return value


def _optional_digest(value: Any, name: str) -> str | None:
    return None if value is None else _digest(value, name)


def new_writer_fence() -> str:
    """Return a 256-bit, URL-safe capability for one writer generation."""
    value = f"wf_{secrets.token_urlsafe(32)}"
    if _WRITER_FENCE.fullmatch(value) is None:  # pragma: no cover - stdlib contract
        _fail("无法生成writer fence", "native_write_authority_invalid")
    return value


def _writer_fence(value: Any, name: str = "writer_fence") -> str:
    if not isinstance(value, str) or _WRITER_FENCE.fullmatch(value) is None:
        _fail(f"{name}必须是256-bit URL-safe writer capability")
    return value


def _counter(value: Any, name: str) -> int:
    if type(value) is not int or not 0 <= value <= JSON_SAFE_INTEGER_MAX:
        _fail(f"{name}必须是非负安全整数")
    return value


def redact_visible_text(value: str) -> str:
    """Bound and remove credentials and local paths from one visible message."""
    if not isinstance(value, str):
        _fail("可见消息正文必须是字符串")
    if len(value) > MAX_VISIBLE_REDACTION_INPUT_CHARS:
        value = value[:MAX_VISIBLE_REDACTION_INPUT_CHARS] + "…[输入已截断]"
    value = _WRITER_FENCE_TOKEN.sub(_REDACTED_SECRET, value)
    value = _COOKIE.sub(_REDACTED_SECRET, value)
    value = _SECRET.sub(_REDACTED_SECRET, value)
    value = _FILE_URI.sub(_REDACTED_PATH, value)
    value = _UNC_PATH.sub(_REDACTED_PATH, value)
    value = _WINDOWS_PATH.sub(_REDACTED_PATH, value)
    value = _ROOTED_BACKSLASH_PATH.sub(_REDACTED_PATH, value)
    value = _POSIX_PATH.sub(_REDACTED_PATH, value)
    if len(value) > MAX_VISIBLE_MESSAGE_CHARS:
        value = value[:MAX_VISIBLE_MESSAGE_CHARS - 7] + "…[已截断]"
    return value


def _validate_message(value: Any) -> dict:
    if not isinstance(value, dict) or set(value) != _MESSAGE_KEYS:
        _fail("可见消息字段不合法")
    row = copy.deepcopy(value)
    _identity(row.get("message_id"), "message_id")
    if row.get("role") not in _ROLES:
        _fail("可见消息角色不合法")
    text = row.get("text")
    if (not isinstance(text, str) or len(text) > MAX_VISIBLE_MESSAGE_CHARS
            or redact_visible_text(text) != text):
        _fail("持久可见消息没有通过有界脱敏检查")
    _optional_identity(row.get("workflow_stage"), "workflow_stage")
    _counter(row.get("user_turn"), "user_turn")
    return row


def _validate_domain(value: Any) -> dict:
    if not isinstance(value, dict) or set(value) != _DOMAIN_KEYS:
        _fail("领域检查点字段不合法")
    row = copy.deepcopy(value)
    _identity(row.get("document_id"), "document_id")
    _counter(row.get("candidate_revision"), "candidate_revision")
    _counter(row.get("stage_revision"), "stage_revision")
    _digest(row.get("database_fingerprint"), "database_fingerprint")
    _optional_digest(row.get("receipt_fingerprint"), "receipt_fingerprint")
    _writer_fence(row.get("writer_fence"))
    return row


def _validate_settlement_ref(value: Any, *, candidate_write: bool = False) -> dict:
    if not isinstance(value, dict) or set(value) != _SETTLEMENT_KEYS:
        _fail("工具结算引用字段不合法")
    row = _snapshot(value, "工具结算引用")
    _identity(row.get("call_id"), "call_id")
    _digest(row.get("args_hash"), "args_hash")
    before = _digest(row.get("before_fingerprint"), "before_fingerprint")
    after = _digest(row.get("after_fingerprint"), "after_fingerprint")
    status = row.get("status")
    if status not in _SETTLEMENT_STATES:
        _fail("工具结算引用状态不合法")
    effect = row.get("effect")
    if effect not in {"read_only", "candidate_write"}:
        _fail("工具结算引用effect不合法")
    if candidate_write and effect != "candidate_write":
        _fail("候选写账本终态必须标为candidate_write")
    if effect == "candidate_write" and status == "settled_success" and before == after:
        _fail("成功候选写结算的前后领域指纹必须不同")
    if effect == "read_only" and before != after:
        _fail("只读工具结算不能改变领域状态")
    if status == "settled_failure" and before != after:
        _fail("失败工具结算不能掩盖领域状态变化")
    refs = row.get("resource_refs")
    if not isinstance(refs, list) or len(refs) > MAX_RESOURCE_REFS:
        _fail("工具结算资源引用不合法")
    for ref in refs:
        _identity(ref, "resource_ref")
    if len(set(refs)) != len(refs):
        _fail("工具结算资源引用不能重复")
    if effect == "candidate_write" and refs:
        _fail("候选写结算不能携带资源引用")
    _optional_identity(row.get("workflow_stage"), "workflow_stage")
    return row


def _validate_last_turn(value: Any) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != _LAST_TURN_KEYS:
        _fail("末轮记录字段不合法")
    row = copy.deepcopy(value)
    _identity(row.get("turn_id"), "turn_id")
    if row.get("status") not in _TURN_STATES:
        _fail("末轮状态不合法")
    if type(row.get("provider_request_possible")) is not bool:
        _fail("末轮provider请求状态不合法")
    _optional_identity(row.get("terminal_reason"), "terminal_reason")
    return row


def _takeover_operation_id(
    conversation_id: str,
    expected_activation_generation: int,
    activation_generation: int,
    expected_fence: str,
    proposed_fence: str,
) -> str:
    return fingerprint({
        "conversation_id": conversation_id,
        "expected_activation_generation": expected_activation_generation,
        "activation_generation": activation_generation,
        "expected_fence": expected_fence,
        "proposed_fence": proposed_fence,
    })


def _takeover_proposal(
    record: Mapping[str, Any],
    *,
    expected_activation_generation: int,
    expected_fence: str,
    proposed_fence: str,
) -> dict:
    if expected_activation_generation == JSON_SAFE_INTEGER_MAX:
        _fail("原生会话激活代数已达上限", "stale_context")
    activation_generation = expected_activation_generation + 1
    return {
        "operation_id": _takeover_operation_id(
            record["conversation_id"], expected_activation_generation,
            activation_generation, expected_fence, proposed_fence,
        ),
        "expected_activation_generation": expected_activation_generation,
        "activation_generation": activation_generation,
        "expected_fence": expected_fence,
        "proposed_fence": proposed_fence,
    }


def _validate_takeover(
    value: Any,
    record: Mapping[str, Any],
    *,
    completed: bool,
) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != _TAKEOVER_KEYS:
        _fail("writer接管检查点字段不合法")
    row = _snapshot(value, "writer接管检查点")
    operation_id = _digest(row.get("operation_id"), "operation_id")
    expected_generation = _counter(
        row.get("expected_activation_generation"),
        "expected_activation_generation",
    )
    generation = _counter(row.get("activation_generation"), "activation_generation")
    expected = _writer_fence(row.get("expected_fence"), "expected_fence")
    proposed = _writer_fence(row.get("proposed_fence"), "proposed_fence")
    if generation != expected_generation + 1 or expected == proposed:
        _fail("writer接管检查点与激活代数不一致")
    if operation_id != _takeover_operation_id(
            record.get("conversation_id"), expected_generation, generation,
            expected, proposed):
        _fail("writer接管幂等键与持久proposal不一致")
    domain = record.get("domain_checkpoint")
    if not isinstance(domain, dict):
        _fail("writer接管检查点缺少领域边界")
    if completed:
        if (generation > record.get("activation_generation")
                or domain.get("writer_fence") != proposed):
            _fail("writer接管完成回执与领域新fence不一致")
    elif (generation != record.get("activation_generation")
          or domain.get("writer_fence") != expected):
        _fail("writer接管检查点与领域旧fence不一致")
    return row


def _validate_usage(value: Any) -> dict:
    if not isinstance(value, dict) or set(value) != _USAGE_KEYS:
        _fail("usage记录字段不合法")
    row = copy.deepcopy(value)
    _identity(row.get("turn_id"), "turn_id")
    for name in _USAGE_KEYS - {"turn_id"}:
        if row.get(name) is not None:
            _counter(row[name], name)
    return row


def _validate_identity_refs(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_AUXILIARY_REFS:
        _fail(f"{name}不合法")
    for ref in value:
        _identity(ref, name.removesuffix("s"))
    if len(set(value)) != len(value):
        _fail(f"{name}不能重复")
    return value


def _research_registration_projection(
    ledger: Mapping[str, Any], *, revision: int | None = None
) -> dict[str, Any]:
    """Project only immutable registration facts from a research ledger.

    Settlement state is deliberately excluded.  A process may crash after a
    Web result is settled but before the conversation checkpoint is advanced;
    the registration chain remains sufficient to detect rollback or identity
    substitution without making that legitimate recovery impossible.
    """

    if not isinstance(ledger, Mapping):
        _fail("研究账引用来源不合法")
    conversation_id = _identity(ledger.get("conversation_id"), "conversation_id")
    current_revision = _counter(
        ledger.get("revision"), "research_ledger_revision"
    )
    entries = ledger.get("entries")
    if not isinstance(entries, list) or current_revision != len(entries):
        _fail("研究账引用revision不合法")
    selected_revision = current_revision if revision is None else revision
    _counter(selected_revision, "research_ledger_revision")
    if selected_revision > current_revision:
        _fail("研究账引用revision超前")
    immutable_keys = (
        "research_id",
        "call_id",
        "turn_id",
        "epoch_id",
        "workflow_stage",
        "tool",
        "arguments_hash",
        "raw_arguments_hash",
        "started_at",
    )
    projected_entries = []
    for row in entries[:selected_revision]:
        if not isinstance(row, Mapping) or any(key not in row for key in immutable_keys):
            _fail("研究账引用条目不合法")
        projected_entries.append({key: copy.deepcopy(row[key]) for key in immutable_keys})
    return {
        "contract": "genshin-native-research-ref-v1",
        "conversation_id": conversation_id,
        "revision": selected_revision,
        "registrations": projected_entries,
    }


def research_ledger_checkpoint_ref(ledger: Mapping[str, Any]) -> str:
    projection = _research_registration_projection(ledger)
    return (
        f"research.v1.{projection['revision']}."
        f"{fingerprint(projection)}"
    )


def verify_research_ledger_checkpoint_ref(
    reference: str, ledger: Mapping[str, Any]
) -> None:
    """Verify a persisted research anchor against its immutable prefix."""

    if not isinstance(reference, str):
        _fail("研究账引用不合法")
    matched = _RESEARCH_REF.fullmatch(reference)
    if matched is None:
        # Older dev records used opaque identity refs.  They remain readable
        # but cannot be treated as a V1 integrity anchor.
        return
    revision = int(matched.group(1))
    projection = _research_registration_projection(ledger, revision=revision)
    expected = f"research.v1.{revision}.{fingerprint(projection)}"
    if expected != reference:
        _fail("研究账引用与持久账不匹配")


def app_action_checkpoint_ref(projection: Mapping[str, Any]) -> str:
    if not isinstance(projection, Mapping):
        _fail("App操作引用来源不合法")
    reference = projection.get("session_ref")
    if not isinstance(reference, str) or _APP_ACTION_REF.fullmatch(reference) is None:
        _fail("App操作session_ref不合法")
    return reference


def native_app_call_ledger_checkpoint_ref(ledger: Mapping[str, Any]) -> str:
    """Bind the complete local App-call fact source without storing payloads."""

    if not isinstance(ledger, Mapping):
        _fail("原生App调用账引用来源不合法")
    revision = _counter(ledger.get("revision"), "app_call_ledger_revision")
    conversation_id = _identity(ledger.get("conversation_id"), "conversation_id")
    del conversation_id
    return f"appcalls.v1.{revision}.{fingerprint(ledger)}"


def verify_native_app_call_ledger_checkpoint_ref(
    reference: str, ledger: Mapping[str, Any]
) -> None:
    if not isinstance(reference, str) or _APP_CALL_REF.fullmatch(reference) is None:
        _fail("原生App调用账引用不合法")
    if reference != native_app_call_ledger_checkpoint_ref(ledger):
        _fail("原生App调用账引用与持久账不匹配")


def _app_call_terminal_projection(
    value: Mapping[str, Any], *, call_id: str
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("原生App调用终态投影不合法", "unknown_settlement")
    required = {
        "terminal_state",
        "call_id",
        "effect",
        "result_hash",
        "before_state_hash",
        "after_state_hash",
    }
    if not required.issubset(value):
        _fail("原生App调用终态投影不完整", "unknown_settlement")
    projected = {name: copy.deepcopy(value.get(name)) for name in required}
    if projected["call_id"] != call_id:
        _fail("原生App调用终态call_id不匹配", "settlement_conflict")
    _identity(projected["effect"], "effect")
    _digest(projected["before_state_hash"], "before_state_hash")
    _digest(projected["after_state_hash"], "after_state_hash")
    result_hash = projected["result_hash"]
    if result_hash is not None:
        _digest(result_hash, "result_hash")
    return projected


def _verify_app_call_terminal_entry(
    entry: Mapping[str, Any],
    terminal: Mapping[str, Any],
    *,
    conversation_id: str,
    turn_id: str,
    epoch_id: str,
) -> None:
    identity = entry.get("identity")
    if not isinstance(identity, Mapping) or (
        identity.get("conversation_id") != conversation_id
        or identity.get("turn_id") != turn_id
        or identity.get("epoch_id") != epoch_id
        or identity.get("call_id") != terminal["call_id"]
    ):
        _fail("原生App调用终态身份不匹配", "settlement_conflict")
    comparisons = {
        "state": terminal["terminal_state"],
        "effect": terminal["effect"],
        "result_hash": terminal["result_hash"],
        "before_state_hash": terminal["before_state_hash"],
        "after_state_hash": terminal["after_state_hash"],
    }
    if any(entry.get(name) != expected for name, expected in comparisons.items()):
        _fail("原生App调用终态与持久账不匹配", "settlement_conflict")
    if entry.get("effect") not in _APP_CALL_EFFECTS:
        _fail("原生App调用效果类型不受支持", "settlement_conflict")
    if entry.get("state") == APP_CALL_INFLIGHT:
        _fail("原生App调用尚未结算", "unknown_settlement")
    if entry.get("state") == APP_CALL_UNKNOWN:
        _fail("原生App调用结果未知，不能推进主会话", "unknown_settlement")


def append_auxiliary_refs(
    record: dict, refs: NativeAuxiliaryRefsV1
) -> bool:
    """Append current ledger anchors to a session record, if they advanced."""

    if not isinstance(refs, NativeAuxiliaryRefsV1):
        _fail("会话辅助引用不合法")
    if _RESEARCH_REF.fullmatch(refs.research_ledger_ref) is None:
        _fail("研究账引用不合法")
    if _APP_ACTION_REF.fullmatch(refs.app_action_ref) is None:
        _fail("App操作引用不合法")
    if (
        refs.app_call_ledger_ref is not None
        and _APP_CALL_REF.fullmatch(refs.app_call_ledger_ref) is None
    ):
        _fail("原生App调用账引用不合法")
    changed = False
    for field, value, mixed in (
        ("research_ledger_refs", refs.research_ledger_ref, False),
        ("app_action_refs", refs.app_action_ref, True),
        ("app_action_refs", refs.app_call_ledger_ref, True),
    ):
        if value is None:
            continue
        rows = record.get(field)
        if not isinstance(rows, list):
            _fail(f"{field}不合法")
        if rows and rows[-1] == value:
            continue
        if value in rows and not mixed:
            _fail(f"{field}发生回退", "stale_context")
        if value in rows:
            continue
        if len(rows) >= MAX_AUXILIARY_REFS:
            _fail(f"{field}已满，请新建对话", "agent_size_limit")
        rows.append(value)
        changed = True
    if changed:
        refresh_checkpoint_binding(record)
    return changed


def _binding_projection(record: Mapping[str, Any]) -> dict:
    """Return every persisted field except the binding that hashes the view."""
    snapshot = _snapshot(record, "原生会话联合检查点")
    if not isinstance(snapshot, dict):
        _fail("原生会话记录必须是JSON对象")
    snapshot.pop("checkpoint_binding", None)
    return snapshot


def refresh_checkpoint_binding(record: dict) -> str:
    """Recompute the App event/domain boundary after a deliberate mutation."""
    snapshot = _snapshot(record, "原生会话记录")
    if not isinstance(snapshot, dict):
        _fail("原生会话记录必须是JSON对象")
    snapshot["checkpoint_binding"] = fingerprint(_binding_projection(snapshot))
    _commit_record(record, snapshot)
    return snapshot["checkpoint_binding"]


def normalize_native_session(record: dict) -> None:
    """Strictly validate a persisted record; never add or migrate fields."""
    record = _snapshot(record, "原生会话记录")
    if not isinstance(record, dict):
        _fail("原生会话记录字段不合法")
    if record.get("contract") != CONTRACT:
        _fail(
            "原生会话记录版本不受支持",
            "native_session_version_incompatible",
        )
    if set(record) != _ROOT_KEYS:
        _fail("原生会话记录字段不合法")
    _identity(record.get("conversation_id"), "conversation_id")
    _counter(record.get("activation_generation"), "activation_generation")
    if record.get("connection_kind") not in _CONNECTIONS:
        _fail("原生会话连接类型不合法")
    if record.get("runtime_generation") not in _RUNTIMES:
        _fail("原生会话runtime generation不合法")
    if ((record["connection_kind"], record["runtime_generation"])
            not in {("cli", "python_v1"), ("native_api", "dsh_v2")}):
        _fail("连接类型与runtime generation不匹配")
    for name in ("locked_provider", "locked_model", "locked_reasoning_effort"):
        _identity(record.get(name), name)
    messages = record.get("visible_messages")
    if not isinstance(messages, list) or len(messages) > MAX_VISIBLE_MESSAGES:
        _fail("可见消息数量不合法")
    validated_messages = [_validate_message(row) for row in messages]
    if len({row["message_id"] for row in validated_messages}) != len(validated_messages):
        _fail("可见消息身份不能重复")
    if sum(len(row["text"]) for row in validated_messages) > MAX_VISIBLE_TEXT_CHARS:
        _fail("可见消息正文总量超过上限", "agent_size_limit")
    _counter(record.get("visible_message_evicted_count"), "visible_message_evicted_count")
    _counter(record.get("accepted_user_turn_count"), "accepted_user_turn_count")
    _optional_identity(
        record.get("current_workflow_stage"),
        "current_workflow_stage",
    )
    _validate_domain(record.get("domain_checkpoint"))
    _digest(record.get("runtime_release_hash"), "runtime_release_hash")
    _digest(record.get("prompt_profile_hash"), "prompt_profile_hash")
    _digest(record.get("tool_manifest_hash"), "tool_manifest_hash")
    _digest(
        record.get("release_pair_identity_hash"),
        "release_pair_identity_hash",
    )
    _digest(record.get("app_artifact_identity_hash"), "app_artifact_identity_hash")
    _digest(record.get("skill_bundle_hash"), "skill_bundle_hash")
    _digest(record.get("checkpoint_binding"), "checkpoint_binding")
    settlements = record.get("tool_settlement_refs")
    if not isinstance(settlements, list) or len(settlements) > MAX_SETTLEMENT_REFS:
        _fail("工具结算引用数量不合法")
    checked_settlements = [_validate_settlement_ref(row) for row in settlements]
    if len({row["call_id"] for row in checked_settlements}) != len(checked_settlements):
        _fail("工具结算引用call_id不能重复")
    _validate_identity_refs(record.get("research_ledger_refs"), "research_ledger_refs")
    _validate_identity_refs(record.get("app_action_refs"), "app_action_refs")
    usage = record.get("usage_records")
    if not isinstance(usage, list) or len(usage) > MAX_USAGE_RECORDS:
        _fail("usage记录数量不合法")
    for row in usage:
        _validate_usage(row)
    _validate_last_turn(record.get("last_turn"))
    pending = _validate_takeover(
        record.get("writer_takeover"), record, completed=False,
    )
    receipt = _validate_takeover(
        record.get("writer_takeover_receipt"), record, completed=True,
    )
    if pending is None:
        if receipt is None and record["activation_generation"] != 0:
            _fail("非零激活代数缺少writer接管完成回执")
        if (receipt is not None and receipt["activation_generation"]
                != record["activation_generation"]):
            _fail("writer接管完成回执与当前激活代数不一致")
    elif receipt is None:
        if pending["expected_activation_generation"] != 0:
            _fail("首次writer接管proposal必须从激活代数0开始")
    elif (pending["expected_activation_generation"]
          != receipt["activation_generation"]
          or pending["expected_fence"] != receipt["proposed_fence"]):
        _fail("writer接管proposal没有精确承接上一完成回执")
    if record["checkpoint_binding"] != fingerprint(_binding_projection(record)):
        _fail("原生会话联合检查点绑定不匹配")
    if len(json_bytes(record)) > MAX_SESSION_BYTES:
        _fail("原生会话记录超过保存上限", "agent_size_limit")


def create_native_session(
    *,
    conversation_id: str,
    connection_kind: str,
    runtime_generation: str,
    locked_provider: str,
    locked_model: str,
    locked_reasoning_effort: str,
    domain_checkpoint: Mapping[str, Any],
    prompt_profile_hash: str,
    tool_manifest_hash: str,
    runtime_release_hash: str | None = None,
    release_pair_identity_hash: str | None = None,
    app_artifact_identity_hash: str | None = None,
    skill_bundle_hash: str | None = None,
) -> dict:
    """Create an in-memory V2 record without starting any runtime."""
    domain_checkpoint = _snapshot(domain_checkpoint, "领域检查点")
    if not isinstance(domain_checkpoint, Mapping):
        _fail("领域检查点必须是对象")
    if runtime_release_hash is None:
        runtime_release_hash = fingerprint({"runtime_generation": runtime_generation})
    compatibility = current_native_compatibility_identity()
    release_pair_identity_hash = (
        compatibility["release_pair_identity_hash"]
        if release_pair_identity_hash is None
        else release_pair_identity_hash
    )
    app_artifact_identity_hash = (
        compatibility["app_artifact_identity_hash"]
        if app_artifact_identity_hash is None
        else app_artifact_identity_hash
    )
    skill_bundle_hash = (
        compatibility["skill_bundle_hash"]
        if skill_bundle_hash is None
        else skill_bundle_hash
    )
    record = {
        "contract": CONTRACT,
        "conversation_id": conversation_id,
        "activation_generation": 0,
        "connection_kind": connection_kind,
        "runtime_generation": runtime_generation,
        "locked_provider": locked_provider,
        "locked_model": locked_model,
        "locked_reasoning_effort": locked_reasoning_effort,
        "visible_messages": [],
        "visible_message_evicted_count": 0,
        "accepted_user_turn_count": 0,
        "current_workflow_stage": None,
        "domain_checkpoint": dict(domain_checkpoint),
        "runtime_release_hash": runtime_release_hash,
        "prompt_profile_hash": prompt_profile_hash,
        "tool_manifest_hash": tool_manifest_hash,
        "release_pair_identity_hash": release_pair_identity_hash,
        "app_artifact_identity_hash": app_artifact_identity_hash,
        "skill_bundle_hash": skill_bundle_hash,
        "checkpoint_binding": "0" * 64,
        "tool_settlement_refs": [],
        "research_ledger_refs": [],
        "app_action_refs": [],
        "usage_records": [],
        "last_turn": None,
        "writer_takeover": None,
        "writer_takeover_receipt": None,
    }
    refresh_checkpoint_binding(record)
    normalize_native_session(record)
    return record


def accept_user_turn(
    record: dict,
    *,
    workflow_stage: str | None,
) -> int:
    """Advance App-owned turn metadata without copying DSH message text.

    DSH is the durable authority for user/assistant messages.  The App keeps
    only the monotonic counter needed by domain receipts and the current
    workflow stage needed for tool admission.  Neither value can reconstruct
    the original user text.
    """

    snapshot = _snapshot(record, "原生会话记录")
    normalize_native_session(snapshot)
    stage = _optional_identity(workflow_stage, "workflow_stage")
    if snapshot["accepted_user_turn_count"] >= JSON_SAFE_INTEGER_MAX:
        _fail("用户轮次数量超过安全上限", "agent_size_limit")
    snapshot["accepted_user_turn_count"] += 1
    snapshot["current_workflow_stage"] = stage
    refresh_checkpoint_binding(snapshot)
    normalize_native_session(snapshot)
    _commit_record(record, snapshot)
    return snapshot["accepted_user_turn_count"]


def set_current_workflow_stage(
    record: dict,
    *,
    workflow_stage: str | None,
) -> str | None:
    """Advance App tool-admission metadata without creating a message row."""

    snapshot = _snapshot(record, "原生会话记录")
    normalize_native_session(snapshot)
    stage = _optional_identity(workflow_stage, "workflow_stage")
    snapshot["current_workflow_stage"] = stage
    refresh_checkpoint_binding(snapshot)
    normalize_native_session(snapshot)
    _commit_record(record, snapshot)
    return stage


def replace_domain_checkpoint(
    record: dict,
    value: Mapping[str, Any],
    *,
    database_migration: str | None = None,
) -> dict:
    snapshot = _snapshot(record, "原生会话记录")
    replacement_input = _snapshot(value, "领域检查点")
    normalize_native_session(snapshot)
    if snapshot["writer_takeover"] is not None:
        _fail("writer接管未完成时不能替换领域检查点", "stale_context")
    if not isinstance(replacement_input, Mapping):
        _fail("领域检查点必须是对象")
    replacement = _validate_domain(dict(replacement_input))
    current = snapshot["domain_checkpoint"]
    if replacement["writer_fence"] != current["writer_fence"]:
        _fail("writer fence只能通过持久CAS接管改变", "stale_context")
    if replacement["document_id"] != current["document_id"]:
        _fail("普通领域检查点替换不能改变文档身份", "stale_context")
    if (replacement["candidate_revision"] < current["candidate_revision"]
            or replacement["stage_revision"] < current["stage_revision"]):
        _fail("领域检查点修订不能倒退", "stale_context")
    database_changed = (
        replacement["database_fingerprint"] != current["database_fingerprint"]
    )
    if database_changed:
        _digest(database_migration, "database_migration")
        if database_migration != replacement["database_fingerprint"]:
            _fail("资料库迁移必须明确绑定目标指纹", "stale_context")
    elif database_migration is not None:
        _fail("没有资料库变化时不能声明迁移", "stale_context")
    snapshot["domain_checkpoint"] = replacement
    refresh_checkpoint_binding(snapshot)
    normalize_native_session(snapshot)
    _commit_record(record, snapshot)
    return copy.deepcopy(snapshot["domain_checkpoint"])


def _current_workflow_stage(record: Mapping[str, Any]) -> str | None:
    return record["current_workflow_stage"]


def _settlement_from_terminal_entry(
    record: Mapping[str, Any],
    entry: Mapping[str, Any],
    *,
    workflow_stage: str | None,
) -> dict:
    identity = entry.get("identity")
    if (not isinstance(identity, Mapping) or set(identity) != _WRITE_IDENTITY_KEYS
            or set(entry) != _WRITE_ENTRY_KEYS):
        _fail("候选写ledger终态字段不完整或包含扩展字段")
    if (entry.get("document_id") != identity.get("document_id")
            or identity.get("conversation_id") != record["conversation_id"]):
        _fail("候选写ledger终态身份不属于当前会话", "stale_context")
    _validate_write_entry(
        identity.get("call_id"), dict(entry), record["conversation_id"],
    )
    for name in (
        "conversation_id", "turn_id", "step_id", "epoch_id", "document_id",
        "call_id", "tool_name", "runtime_generation",
    ):
        _identity(identity.get(name), name)
    _writer_fence(identity.get("writer_fence"))
    for name in (
        "canonical_args_hash", "database_fingerprint", "tool_manifest_hash",
    ):
        _digest(identity.get(name), name)
    if (not isinstance(entry.get("started_at"), str) or not entry["started_at"]
            or not isinstance(entry.get("settled_at"), str) or not entry["settled_at"]):
        _fail("候选写ledger终态缺少持久时间边界")
    status = entry.get("state")
    if status not in _SETTLEMENT_STATES:
        _fail("候选写ledger记录尚未到达终态")
    candidate_revision = _counter(
        identity.get("candidate_revision"), "candidate_revision",
    )
    stage_revision = _counter(identity.get("stage_revision"), "stage_revision")
    post_candidate_revision = _counter(
        entry.get("post_candidate_revision"), "post_candidate_revision",
    )
    post_stage_revision = _counter(
        entry.get("post_stage_revision"), "post_stage_revision",
    )
    manifest = _digest(identity.get("tool_manifest_hash"), "tool_manifest_hash")
    post_manifest = _digest(
        entry.get("post_tool_manifest_hash"), "post_tool_manifest_hash",
    )
    if identity.get("document_id") != record["domain_checkpoint"]["document_id"]:
        _fail("候选写ledger终态属于其他文档", "stale_context")
    if status == "settled_success" and (
            post_candidate_revision != candidate_revision + 1
            or post_stage_revision < stage_revision):
        _fail("成功候选写ledger终态修订不合法")
    if status == "settled_failure" and (
            post_candidate_revision != candidate_revision
            or post_stage_revision != stage_revision
            or post_manifest != manifest):
        _fail("失败候选写ledger终态不能改变上下文")
    row = {
        "call_id": identity.get("call_id"),
        "args_hash": identity.get("canonical_args_hash"),
        "before_fingerprint": entry.get("before_fingerprint"),
        "after_fingerprint": entry.get("after_fingerprint"),
        "status": status,
        "effect": "candidate_write",
        "resource_refs": [],
        "workflow_stage": workflow_stage,
    }
    return _validate_settlement_ref(row, candidate_write=True)


def _validate_terminal_entry_post_context(
    record: Mapping[str, Any],
    entry: Mapping[str, Any],
) -> None:
    """Bind one durable write terminal to the exact current session context."""
    if record["writer_takeover"] is not None:
        _fail("writer接管未完成时不能归档候选写结算", "stale_context")
    identity = entry["identity"]
    domain = record["domain_checkpoint"]
    if (identity["runtime_generation"] != record["runtime_generation"]
            or identity["document_id"] != domain["document_id"]
            or identity["database_fingerprint"] != domain["database_fingerprint"]
            or identity["writer_fence"] != domain["writer_fence"]):
        _fail("候选写ledger终态与当前会话权威上下文不一致", "stale_context")
    if entry["state"] == "settled_failure":
        matches = (
            identity["candidate_revision"] == domain["candidate_revision"]
            and identity["stage_revision"] == domain["stage_revision"]
            and identity["tool_manifest_hash"] == record["tool_manifest_hash"]
        )
    else:
        matches = (
            entry["post_candidate_revision"] == domain["candidate_revision"]
            and entry["post_stage_revision"] == domain["stage_revision"]
            and entry["post_tool_manifest_hash"] == record["tool_manifest_hash"]
        )
    if not matches:
        _fail("候选写ledger终态没有绑定当前可证明post-context", "stale_context")


def _candidate_checkpoint_from_terminal(
    current: Mapping[str, Any],
    entry: Mapping[str, Any],
    *,
    expected_turn_id: str,
    expected_epoch_id: str,
    post_domain_checkpoint: Mapping[str, Any],
    post_tool_manifest_hash: str,
    workflow_stage: str | None,
) -> tuple[dict, dict, dict[str, str] | None]:
    """Derive the sole admissible checkpoint from one same-root terminal."""

    expected_turn_id = _identity(expected_turn_id, "expected_turn_id")
    expected_epoch_id = _identity(expected_epoch_id, "expected_epoch_id")
    post_domain = _validate_domain(
        _snapshot(post_domain_checkpoint, "候选写post领域检查点"),
    )
    post_tool_manifest_hash = _digest(
        post_tool_manifest_hash,
        "post_tool_manifest_hash",
    )
    row = _settlement_from_terminal_entry(
        current,
        entry,
        workflow_stage=workflow_stage,
    )
    identity = entry["identity"]
    if (
        identity["turn_id"] != expected_turn_id
        or identity["epoch_id"] != expected_epoch_id
    ):
        _fail("候选写ledger终态不属于当前轮次", "stale_context")
    domain = current["domain_checkpoint"]
    common_matches = (
        identity["runtime_generation"] == current["runtime_generation"]
        and identity["document_id"] == domain["document_id"]
        and identity["database_fingerprint"] == domain["database_fingerprint"]
        and identity["writer_fence"] == domain["writer_fence"]
    )
    if not common_matches:
        _fail("候选写ledger终态与当前会话身份不一致", "stale_context")

    proposed = _snapshot(current, "候选写检查点提案")
    already_checkpointed = (
        proposed["domain_checkpoint"] == post_domain
        and proposed["tool_manifest_hash"] == post_tool_manifest_hash
    )
    migrations = None
    if not already_checkpointed:
        pre_matches = (
            identity["candidate_revision"] == domain["candidate_revision"]
            and identity["stage_revision"] == domain["stage_revision"]
            and identity["tool_manifest_hash"] == current["tool_manifest_hash"]
        )
        if not pre_matches:
            _fail("候选写ledger终态没有承接当前pre-context", "stale_context")
        replace_domain_checkpoint(proposed, post_domain)
        if proposed["tool_manifest_hash"] != post_tool_manifest_hash:
            proposed["tool_manifest_hash"] = post_tool_manifest_hash
            refresh_checkpoint_binding(proposed)
            migrations = {"tool_manifest_hash": post_tool_manifest_hash}
        normalize_native_session(proposed)
    _validate_terminal_entry_post_context(proposed, entry)
    return proposed, row, migrations


def append_tool_settlement_ref(
    record: dict,
    value: Mapping[str, Any],
    *,
    workflow_stage: str | None = None,
    candidate_write: bool = False,
) -> dict:
    snapshot = _snapshot(record, "原生会话记录")
    source = _snapshot(value, "工具结算引用")
    normalize_native_session(snapshot)
    if not isinstance(source, Mapping):
        _fail("工具结算引用必须是对象")
    if candidate_write:
        _fail(
            "候选写结算必须由NativeSessionStore从同根持久账本归档",
            "stale_context",
        )
    workflow_stage = _optional_identity(workflow_stage, "workflow_stage")
    source = dict(source)
    compact_keys_without_stage = _SETTLEMENT_KEYS - {"workflow_stage"}
    if set(source) == compact_keys_without_stage:
        source["workflow_stage"] = (
            workflow_stage if workflow_stage is not None
            else _current_workflow_stage(snapshot)
        )
    if set(source) == _SETTLEMENT_KEYS:
        if (workflow_stage is not None
                and source.get("workflow_stage") != workflow_stage):
            _fail("工具结算引用阶段与显式阶段不一致", "stale_context")
        row = _validate_settlement_ref(source)
        if row["effect"] != "read_only":
            _fail(
                "候选写结算必须由NativeSessionStore从同根持久账本归档",
                "stale_context",
            )
    else:
        _fail(
            "调用方不能自报候选写ledger终态；必须按call_id从同根账本归档",
            "stale_context",
        )
    existing = next((item for item in snapshot["tool_settlement_refs"]
                     if item["call_id"] == row["call_id"]), None)
    if existing is not None:
        if existing != row:
            _fail("工具结算引用已经以不同内容保存", "settlement_conflict")
        return copy.deepcopy(existing)
    if len(snapshot["tool_settlement_refs"]) >= MAX_SETTLEMENT_REFS:
        _fail("工具结算引用已满，请新建对话", "agent_size_limit")
    snapshot["tool_settlement_refs"].append(row)
    refresh_checkpoint_binding(snapshot)
    normalize_native_session(snapshot)
    _commit_record(record, snapshot)
    return copy.deepcopy(row)


def set_last_turn(
    record: dict,
    *,
    turn_id: str,
    status: str,
    provider_request_possible: bool,
    terminal_reason: str | None,
) -> dict:
    snapshot = _snapshot(record, "原生会话记录")
    normalize_native_session(snapshot)
    row = _validate_last_turn({
        "turn_id": turn_id,
        "status": status,
        "provider_request_possible": provider_request_possible,
        "terminal_reason": terminal_reason,
    })
    snapshot["last_turn"] = row
    refresh_checkpoint_binding(snapshot)
    normalize_native_session(snapshot)
    _commit_record(record, snapshot)
    return copy.deepcopy(row)


def resume_seed(
    record: dict,
    *,
    workflow_stage: str | None = None,
) -> dict:
    """Return the bounded semantic state that may seed a new in-memory DSH session."""
    snapshot = _snapshot(record, "原生会话记录")
    normalize_native_session(snapshot)
    if snapshot["writer_takeover"] is not None:
        _fail("writer接管未完成时不能生成恢复seed", "stale_context")
    persisted_stage = _current_workflow_stage(snapshot)
    if workflow_stage is None:
        workflow_stage = persisted_stage
    else:
        workflow_stage = _identity(workflow_stage, "workflow_stage")
        # A successful stage-transition write advances the authoritative
        # domain before the next user message can establish the new visible
        # stage.  The App therefore supplies the current stage explicitly;
        # settlement recall remains filtered to that stage instead of pinning
        # the conversation forever to the last visible message's stage.
    eligible = [
        copy.deepcopy(row) for row in snapshot["tool_settlement_refs"]
        if row["workflow_stage"] == workflow_stage
    ][-MAX_RESUME_SETTLEMENTS:]
    remaining_resources = MAX_RESUME_RESOURCE_REFS
    bounded_reversed = []
    for row in reversed(eligible):
        refs = row["resource_refs"]
        if remaining_resources <= 0:
            row["resource_refs"] = []
        elif len(refs) > remaining_resources:
            row["resource_refs"] = refs[-remaining_resources:]
            remaining_resources = 0
        else:
            remaining_resources -= len(refs)
        bounded_reversed.append(row)
    settlements = list(reversed(bounded_reversed))
    # Durable DSH sessions resume their own message/tool/reasoning history.
    # App session storage must never be projected back into the model as a
    # second transcript authority.
    visible_messages: list[dict[str, Any]] = []
    domain = {
        name: copy.deepcopy(value)
        for name, value in snapshot["domain_checkpoint"].items()
        if name != "writer_fence"
    }
    return {
        "conversation_id": snapshot["conversation_id"],
        "activation_generation": snapshot["activation_generation"],
        "connection_kind": snapshot["connection_kind"],
        "runtime_generation": snapshot["runtime_generation"],
        "locked_provider": snapshot["locked_provider"],
        "locked_model": snapshot["locked_model"],
        "locked_reasoning_effort": snapshot["locked_reasoning_effort"],
        "visible_messages": visible_messages,
        "visible_message_evicted_count": snapshot["visible_message_evicted_count"],
        "resume_visible_message_omitted_count": (
            len(snapshot["visible_messages"]) - len(visible_messages)
        ),
        "workflow_stage": workflow_stage,
        "accepted_user_turn_count": snapshot["accepted_user_turn_count"],
        "domain_checkpoint": domain,
        "runtime_release_hash": snapshot["runtime_release_hash"],
        "prompt_profile_hash": snapshot["prompt_profile_hash"],
        "tool_manifest_hash": snapshot["tool_manifest_hash"],
        "release_pair_identity_hash": snapshot["release_pair_identity_hash"],
        "app_artifact_identity_hash": snapshot["app_artifact_identity_hash"],
        "skill_bundle_hash": snapshot["skill_bundle_hash"],
        "tool_settlement_refs": settlements,
        "research_ledger_refs": copy.deepcopy(
            snapshot["research_ledger_refs"][-MAX_RESUME_RESOURCE_REFS:]
        ),
        "app_action_refs": copy.deepcopy(
            snapshot["app_action_refs"][-MAX_RESUME_RESOURCE_REFS:]
        ),
        "last_turn": copy.deepcopy(snapshot["last_turn"]),
    }


def assess_activation(record: dict, observed: Mapping[str, Any]) -> dict:
    """Compare a cold record to live App state without fixing any mismatch.

    The caller can show these stable issue codes before it starts a runtime.
    This function is intentionally read-only and has no sidecar/provider hook.
    """
    record = _snapshot(record, "原生会话记录")
    checked = _snapshot(observed, "原生会话激活观测")
    normalize_native_session(record)
    if not isinstance(checked, Mapping) or set(checked) != _ACTIVATION_OBSERVED_KEYS:
        _fail("原生会话激活观测字段不合法")
    checked = dict(checked)
    if checked.get("connection_kind") not in _CONNECTIONS:
        _fail("激活连接类型不合法")
    if checked.get("runtime_generation") not in _RUNTIMES:
        _fail("激活runtime generation不合法")
    for name in ("locked_provider", "locked_model", "locked_reasoning_effort",
                 "document_id"):
        _identity(checked.get(name), name)
    for name in ("candidate_revision", "stage_revision"):
        _counter(checked.get(name), name)
    for name in (
        "database_fingerprint", "runtime_release_hash",
        "prompt_profile_hash", "tool_manifest_hash",
        "release_pair_identity_hash", "app_artifact_identity_hash",
        "skill_bundle_hash",
    ):
        _digest(checked.get(name), name)
    _optional_digest(checked.get("receipt_fingerprint"), "receipt_fingerprint")

    domain = record["domain_checkpoint"]
    comparisons = (
        ("connection_kind", record["connection_kind"], "connection_changed"),
        ("runtime_generation", record["runtime_generation"], "runtime_changed"),
        ("runtime_release_hash", record["runtime_release_hash"],
         "runtime_release_changed"),
        ("release_pair_identity_hash", record["release_pair_identity_hash"],
         "release_pair_changed"),
        ("app_artifact_identity_hash", record["app_artifact_identity_hash"],
         "app_artifact_changed"),
        ("skill_bundle_hash", record["skill_bundle_hash"],
         "skill_bundle_changed"),
        ("locked_provider", record["locked_provider"], "provider_changed"),
        ("locked_model", record["locked_model"], "model_changed"),
        ("locked_reasoning_effort", record["locked_reasoning_effort"],
         "reasoning_effort_changed"),
        ("document_id", domain["document_id"], "document_changed"),
        ("candidate_revision", domain["candidate_revision"], "candidate_changed"),
        ("stage_revision", domain["stage_revision"], "stage_changed"),
        ("database_fingerprint", domain["database_fingerprint"], "database_changed"),
        ("receipt_fingerprint", domain["receipt_fingerprint"], "receipt_changed"),
        ("prompt_profile_hash", record["prompt_profile_hash"], "prompt_profile_changed"),
        ("tool_manifest_hash", record["tool_manifest_hash"], "tool_manifest_changed"),
    )
    issues = [
        {"code": code, "field": field}
        for field, expected, code in comparisons
        if checked[field] != expected
    ]
    if record["writer_takeover"] is not None:
        issues.append({"code": "writer_takeover_pending", "field": "writer_takeover"})
    return {
        "status": "blocked" if issues else "ready",
        "conversation_id": record["conversation_id"],
        "activation_generation": record["activation_generation"],
        "issues": issues,
        "writer_takeover_pending": record["writer_takeover"] is not None,
    }


def _is_prefix(previous: list, proposed: list) -> bool:
    return len(proposed) >= len(previous) and proposed[:len(previous)] == previous


def _bounded_messages(messages: list[dict]) -> tuple[list[dict], int]:
    bounded = copy.deepcopy(messages)
    evicted = 0
    while (len(bounded) > MAX_VISIBLE_MESSAGES
           or sum(len(item["text"]) for item in bounded) > MAX_VISIBLE_TEXT_CHARS):
        bounded.pop(0)
        evicted += 1
    return bounded, evicted


def _validate_visible_transition(current: Mapping[str, Any], proposed: Mapping[str, Any]) -> None:
    previous = current["visible_messages"]
    following = proposed["visible_messages"]
    previous_ids = {row["message_id"] for row in previous}
    for removed in range(len(previous) + 1):
        retained = previous[removed:]
        if len(following) < len(retained) or following[:len(retained)] != retained:
            continue
        appended = following[len(retained):]
        if any(row["message_id"] in previous_ids for row in appended):
            continue
        bounded, actual_removed = _bounded_messages([*previous, *appended])
        if (bounded == following
                and actual_removed == removed
                and proposed["visible_message_evicted_count"]
                == current["visible_message_evicted_count"] + actual_removed):
            return
    _fail(
        "普通会话保存只允许追加消息及由容量边界触发的前缀淘汰",
        "stale_context",
    )


def _validate_migrations(
    current: Mapping[str, Any],
    proposed: Mapping[str, Any],
    migrations: Mapping[str, Any] | None,
) -> None:
    supplied = {} if migrations is None else _snapshot(migrations, "会话迁移声明")
    if not isinstance(supplied, Mapping) or not set(supplied).issubset(_MIGRATION_FIELDS):
        _fail("会话迁移声明字段不合法", "stale_context")
    supplied = dict(supplied)
    values = {
        "database_fingerprint": proposed["domain_checkpoint"]["database_fingerprint"],
        "runtime_release_hash": proposed["runtime_release_hash"],
        "prompt_profile_hash": proposed["prompt_profile_hash"],
        "tool_manifest_hash": proposed["tool_manifest_hash"],
    }
    previous = {
        "database_fingerprint": current["domain_checkpoint"]["database_fingerprint"],
        "runtime_release_hash": current["runtime_release_hash"],
        "prompt_profile_hash": current["prompt_profile_hash"],
        "tool_manifest_hash": current["tool_manifest_hash"],
    }
    changed = {name for name in _MIGRATION_FIELDS if values[name] != previous[name]}
    if set(supplied) != changed:
        _fail("资料库、Prompt或工具清单变化必须逐项显式声明迁移", "stale_context")
    for name, target in supplied.items():
        _digest(target, f"{name}_migration")
        if target != values[name]:
            _fail("会话迁移声明没有绑定目标指纹", "stale_context")


def _validate_save_transition(
    current: Mapping[str, Any],
    proposed: Mapping[str, Any],
    migrations: Mapping[str, Any] | None,
) -> None:
    immutable = (
        "contract", "conversation_id", "activation_generation",
        "connection_kind", "runtime_generation", "locked_provider",
        "locked_model", "locked_reasoning_effort", "writer_takeover",
        "writer_takeover_receipt", "release_pair_identity_hash",
        "app_artifact_identity_hash", "skill_bundle_hash",
    )
    if any(proposed[name] != current[name] for name in immutable):
        _fail("普通会话保存不能改变身份、锁定配置或writer接管", "stale_context")
    current_domain = current["domain_checkpoint"]
    proposed_domain = proposed["domain_checkpoint"]
    for name in ("document_id", "writer_fence"):
        if proposed_domain[name] != current_domain[name]:
            _fail("普通会话保存不能改变文档身份或writer fence", "stale_context")
    if (proposed_domain["candidate_revision"] < current_domain["candidate_revision"]
            or proposed_domain["stage_revision"] < current_domain["stage_revision"]):
        _fail("普通会话保存不能倒退候选或阶段修订", "stale_context")
    if (
        proposed["accepted_user_turn_count"]
        < current["accepted_user_turn_count"]
    ):
        _fail("普通会话保存不能倒退用户轮次", "stale_context")
    _validate_migrations(current, proposed, migrations)
    _validate_visible_transition(current, proposed)
    for name in (
        "tool_settlement_refs", "research_ledger_refs", "app_action_refs",
        "usage_records",
    ):
        if not _is_prefix(current[name], proposed[name]):
            _fail(f"普通会话保存只能向{name}追加不可变记录", "stale_context")
    appended_settlements = proposed["tool_settlement_refs"][
        len(current["tool_settlement_refs"]):
    ]
    if any(row["effect"] == "candidate_write" for row in appended_settlements):
        _fail(
            "普通会话保存不能绕过同根账本归档候选写结算",
            "stale_context",
        )


class NativeSessionStore:
    """One App-owned V2 conversation file under an already-owned session root."""

    def __init__(
        self,
        root: str | Path,
        *,
        auxiliary_refs_reader: Callable[[], NativeAuxiliaryRefsV1] | None = None,
    ):
        self.root = Path(root).absolute()
        self.path = safe_path(self.root, SESSION_FILE)
        self.lock_path = safe_path(self.root, SESSION_LOCK_FILE)
        if auxiliary_refs_reader is not None and not callable(auxiliary_refs_reader):
            raise TypeError("auxiliary_refs_reader must be callable")
        self._auxiliary_refs_reader = auxiliary_refs_reader

    def _attach_auxiliary_refs(self, record: dict) -> bool:
        if self._auxiliary_refs_reader is None:
            return False
        try:
            refs = self._auxiliary_refs_reader()
        except PlanError:
            raise
        except Exception as exc:
            raise PlanError(
                "读取会话辅助引用失败", "agent_checkpoint_invalid"
            ) from exc
        return append_auxiliary_refs(record, refs)

    def sync_auxiliary_refs(self) -> dict:
        """CAS-publish current path-free ledger anchors before activation."""

        if self._auxiliary_refs_reader is None:
            return self.cold_read()
        with file_lock(self.lock_path):
            current = _read_record(self.root)
            normalize_native_session(current)
            proposed = copy.deepcopy(current)
            if self._attach_auxiliary_refs(proposed):
                normalize_native_session(proposed)
                _validate_save_transition(current, proposed, None)
                write_owned(self.root, SESSION_FILE, proposed, MAX_SESSION_BYTES)
                return copy.deepcopy(proposed)
            return copy.deepcopy(current)

    def cold_read(self) -> dict:
        """Read and validate only; never activate a sidecar or provider."""
        def read() -> dict:
            record = _read_record(self.root)
            normalize_native_session(record)
            return copy.deepcopy(record)

        # A cold open must not mutate an old or incomplete session directory.
        # Existing stores join the same OS lock; legacy/no-record directories
        # are read without manufacturing state as a side effect.
        with _existing_file_lock(self.lock_path):
            return read()

    def checkpoint_document_restore_baseline(
        self,
        *,
        post_domain_checkpoint: Mapping[str, Any],
        post_tool_manifest_hash: str,
        expected_checkpoint_binding: str,
    ) -> dict:
        """Publish a restored candidate baseline into the main session CAS.

        The document restore journal proves Renderer and restore lineage.  This
        seam owns only the native session half: candidate/stage revisions and
        tool manifest remain monotonic, the writer fence and document identity
        cannot change, and an exact retry after a lost acknowledgement is a
        no-op.  It performs no provider call and appends no visible message.
        """

        post_domain_input = _snapshot(
            post_domain_checkpoint, "文档恢复post领域检查点"
        )
        if not isinstance(post_domain_input, Mapping):
            _fail("文档恢复post领域检查点必须是对象")
        post_domain = _validate_domain(dict(post_domain_input))
        post_tool_hash = _digest(
            post_tool_manifest_hash, "post_tool_manifest_hash"
        )
        expected_binding = _digest(
            expected_checkpoint_binding, "expected_checkpoint_binding"
        )
        with file_lock(self.lock_path):
            current = _read_record(self.root)
            normalize_native_session(current)
            if (
                current["domain_checkpoint"] == post_domain
                and current["tool_manifest_hash"] == post_tool_hash
            ):
                return copy.deepcopy(current)
            if current["checkpoint_binding"] != expected_binding:
                _fail("原生会话联合检查点已变化", "stale_context")
            if current["writer_takeover"] is not None:
                _fail("writer接管未完成时不能恢复文档", "stale_context")
            if post_tool_hash != current["tool_manifest_hash"]:
                _fail("文档恢复不能改变工具目录", "stale_context")
            proposed = copy.deepcopy(current)
            replace_domain_checkpoint(proposed, post_domain)
            normalize_native_session(proposed)
            _validate_save_transition(current, proposed, None)
            write_owned(self.root, SESSION_FILE, proposed, MAX_SESSION_BYTES)
            return copy.deepcopy(proposed)

    def create(self, record: dict) -> dict:
        proposed = _snapshot(record, "原生会话记录")
        self._attach_auxiliary_refs(proposed)
        normalize_native_session(proposed)
        if any(row["effect"] == "candidate_write"
               for row in proposed["tool_settlement_refs"]):
            _fail(
                "新建会话不能预注入候选写结算",
                "stale_context",
            )
        with file_lock(self.lock_path):
            if self.path.exists():
                current = _read_record(self.root)
                normalize_native_session(current)
                if current == proposed:
                    return copy.deepcopy(current)
                _fail("原生会话记录已经存在", "file_conflict")
            write_owned(self.root, SESSION_FILE, proposed, MAX_SESSION_BYTES)
        return copy.deepcopy(proposed)

    def save(
        self,
        record: dict,
        *,
        expected_activation_generation: int,
        expected_checkpoint_binding: str,
        migrations: Mapping[str, str] | None = None,
    ) -> dict:
        proposed = _snapshot(record, "原生会话记录")
        self._attach_auxiliary_refs(proposed)
        migration_snapshot = (
            None if migrations is None else _snapshot(migrations, "会话迁移声明")
        )
        try:
            normalize_native_session(proposed)
        except PlanError as exc:
            if exc.reason == "agent_checkpoint_invalid":
                raise PlanError(
                    "普通会话保存提案不是合法的下一状态", "stale_context",
                ) from exc
            raise
        _counter(expected_activation_generation, "expected_activation_generation")
        _digest(expected_checkpoint_binding, "expected_checkpoint_binding")
        with file_lock(self.lock_path):
            current = _read_record(self.root)
            normalize_native_session(current)
            # The prior publication may have committed and only its response
            # was lost.  Exact record equality is the durable idempotency key.
            if current == proposed:
                return copy.deepcopy(current)
            if current["conversation_id"] != proposed["conversation_id"]:
                _fail("原生会话身份发生变化", "stale_context")
            if current["activation_generation"] != expected_activation_generation:
                _fail("原生会话激活代数已变化", "stale_context")
            if current["checkpoint_binding"] != expected_checkpoint_binding:
                _fail("原生会话联合检查点已变化", "stale_context")
            _validate_save_transition(current, proposed, migration_snapshot)
            write_owned(self.root, SESSION_FILE, proposed, MAX_SESSION_BYTES)
        return copy.deepcopy(proposed)

    def checkpoint_app_call_terminal(
        self,
        *,
        call_id: str,
        expected_turn_id: str,
        expected_epoch_id: str,
        terminal_projection: Mapping[str, Any],
        post_domain_checkpoint: Mapping[str, Any],
        post_tool_manifest_hash: str,
        expected_activation_generation: int,
        expected_checkpoint_binding: str,
    ) -> tuple[dict, dict]:
        """Atomically bind one exact App-call terminal to the main session.

        The App-call ledger is the payload authority.  This checkpoint stores
        only its compact hash reference while advancing the authoritative
        domain/tool facts observed after execution.  An exact replay after a
        lost acknowledgement returns the already-published record; unknown
        outcomes never advance it.
        """

        call_id = _identity(call_id, "call_id")
        expected_turn_id = _identity(expected_turn_id, "expected_turn_id")
        expected_epoch_id = _identity(expected_epoch_id, "expected_epoch_id")
        terminal = _app_call_terminal_projection(
            terminal_projection,
            call_id=call_id,
        )
        post_domain = _validate_domain(
            _snapshot(post_domain_checkpoint, "App调用post领域检查点")
        )
        post_tool_hash = _digest(
            post_tool_manifest_hash,
            "post_tool_manifest_hash",
        )
        _counter(expected_activation_generation, "expected_activation_generation")
        _digest(expected_checkpoint_binding, "expected_checkpoint_binding")
        with file_lock(self.lock_path):
            current = _read_record(self.root)
            normalize_native_session(current)
            ledger = NativeAppCallLedger(
                self.root,
                conversation_id=current["conversation_id"],
            ).read()
            entry = ledger["entries"].get(call_id)
            if entry is None:
                _fail("原生App调用账没有对应call_id", "unknown_settlement")
            _verify_app_call_terminal_entry(
                entry,
                terminal,
                conversation_id=current["conversation_id"],
                turn_id=expected_turn_id,
                epoch_id=expected_epoch_id,
            )
            ledger_ref = native_app_call_ledger_checkpoint_ref(ledger)

            # Exact committed replay after a lost return acknowledgement.
            if (
                ledger_ref in current["app_action_refs"]
                and current["domain_checkpoint"] == post_domain
                and current["tool_manifest_hash"] == post_tool_hash
            ):
                return copy.deepcopy(current), copy.deepcopy(entry)
            if current["activation_generation"] != expected_activation_generation:
                _fail("原生会话激活代数已变化", "stale_context")
            if current["checkpoint_binding"] != expected_checkpoint_binding:
                _fail("原生会话联合检查点已变化", "stale_context")
            identity = entry["identity"]
            domain = current["domain_checkpoint"]
            if (
                identity["document_id"] != domain["document_id"]
                or identity["candidate_revision"] != domain["candidate_revision"]
                or identity["stage_revision"] != domain["stage_revision"]
                or identity["database_fingerprint"]
                != domain["database_fingerprint"]
                or identity["tool_manifest_hash"] != current["tool_manifest_hash"]
                or identity["writer_fence"] != domain["writer_fence"]
            ):
                _fail("原生App调用pre状态与主会话不匹配", "stale_context")
            if (
                post_domain["document_id"] != domain["document_id"]
                or post_domain["writer_fence"] != domain["writer_fence"]
                or post_domain["candidate_revision"] < domain["candidate_revision"]
                or post_domain["stage_revision"] < domain["stage_revision"]
            ):
                _fail("原生App调用post领域检查点不合法", "stale_context")
            unchanged_required = (
                entry["state"] == APP_CALL_SETTLED_FAILURE
                or entry["effect"] in _APP_CALL_NO_DOMAIN_CHANGE
            )
            if unchanged_required and (
                post_domain != domain or post_tool_hash != current["tool_manifest_hash"]
            ):
                _fail("无状态或失败App调用改变了领域事实", "settlement_conflict")
            manifest_changed = post_tool_hash != current["tool_manifest_hash"]
            transition = entry.get("stage_transition")
            if manifest_changed:
                transition_effect = entry["effect"] in {
                    "stage_transition",
                    "candidate",
                    "candidate_and_derived",
                }
                if not transition_effect:
                    _fail("非阶段切换调用改变了工具目录", "settlement_conflict")
                if (
                    post_domain["stage_revision"]
                    != domain["stage_revision"] + 1
                ):
                    _fail("阶段切换缺少连续修订证明", "settlement_conflict")
                if not isinstance(transition, Mapping) or (
                    transition.get("pre_stage_revision")
                    != domain["stage_revision"]
                    or transition.get("post_stage_revision")
                    != post_domain["stage_revision"]
                    or transition.get("pre_tool_manifest_hash")
                    != current["tool_manifest_hash"]
                    or transition.get("post_tool_manifest_hash")
                    != post_tool_hash
                ):
                    _fail("阶段切换持久证明不完整", "settlement_conflict")
                if (
                    entry["effect"] in {"candidate", "candidate_and_derived"}
                    and transition.get("kind") != "automatic_invalidation"
                ):
                    _fail("候选修改没有自动回退证明", "settlement_conflict")
            elif (
                entry["effect"] == "stage_transition"
                and entry["state"] == APP_CALL_SETTLED_SUCCESS
            ):
                _fail("阶段切换没有刷新工具目录", "settlement_conflict")
            elif transition is not None:
                _fail("无阶段变化调用携带了切换证明", "settlement_conflict")

            proposed = copy.deepcopy(current)
            proposed["domain_checkpoint"] = post_domain
            proposed["tool_manifest_hash"] = post_tool_hash
            if ledger_ref not in proposed["app_action_refs"]:
                if len(proposed["app_action_refs"]) >= MAX_AUXILIARY_REFS:
                    _fail("app_action_refs已满，请新建对话", "agent_size_limit")
                proposed["app_action_refs"].append(ledger_ref)
            refresh_checkpoint_binding(proposed)
            normalize_native_session(proposed)
            migrations = (
                {"tool_manifest_hash": post_tool_hash}
                if post_tool_hash != current["tool_manifest_hash"]
                else None
            )
            _validate_save_transition(current, proposed, migrations)
            write_owned(self.root, SESSION_FILE, proposed, MAX_SESSION_BYTES)
            return copy.deepcopy(proposed), copy.deepcopy(entry)

    def checkpoint_candidate_write_terminal(
        self,
        *,
        call_id: str,
        expected_turn_id: str,
        expected_epoch_id: str,
        post_domain_checkpoint: Mapping[str, Any],
        post_tool_manifest_hash: str,
        expected_activation_generation: int,
        expected_checkpoint_binding: str,
        workflow_stage: str | None = None,
    ) -> tuple[dict, dict]:
        """Persist a ledger-proven post-context before archiving its terminal.

        This is the public coordinator seam for the required order
        ``ledger terminal -> conversation checkpoint -> archive ref``.  A
        retry after a committed checkpoint with a lost acknowledgement is
        accepted only when the same terminal proves the exact durable post
        context and turn/epoch identity.
        """

        call_id = _identity(call_id, "call_id")
        expected_turn_id = _identity(expected_turn_id, "expected_turn_id")
        expected_epoch_id = _identity(expected_epoch_id, "expected_epoch_id")
        _counter(expected_activation_generation, "expected_activation_generation")
        _digest(expected_checkpoint_binding, "expected_checkpoint_binding")
        workflow_stage = _optional_identity(workflow_stage, "workflow_stage")
        post_domain = _validate_domain(
            _snapshot(post_domain_checkpoint, "候选写post领域检查点"),
        )
        post_tool_manifest_hash = _digest(
            post_tool_manifest_hash,
            "post_tool_manifest_hash",
        )
        with file_lock(self.lock_path):
            current = _read_record(self.root)
            normalize_native_session(current)
            if current["activation_generation"] != expected_activation_generation:
                _fail("原生会话激活代数已变化", "stale_context")
            try:
                ledger = _read_write_ledger(self.root)
            except FileNotFoundError:
                raise PlanError(
                    "候选写账本不存在", "unknown_settlement",
                ) from None
            if ledger["conversation_id"] != current["conversation_id"]:
                _fail("候选写账本不属于当前会话", "stale_context")
            entry = ledger["entries"].get(call_id)
            if entry is None or entry["state"] == "inflight":
                _fail("候选写账本尚未到达终态", "unknown_settlement")
            proposed, row, migrations = _candidate_checkpoint_from_terminal(
                current,
                entry,
                expected_turn_id=expected_turn_id,
                expected_epoch_id=expected_epoch_id,
                post_domain_checkpoint=post_domain,
                post_tool_manifest_hash=post_tool_manifest_hash,
                workflow_stage=workflow_stage,
            )
            if proposed == current:
                return copy.deepcopy(current), copy.deepcopy(row)
            if current["checkpoint_binding"] != expected_checkpoint_binding:
                _fail("原生会话联合检查点已变化", "stale_context")
            _validate_save_transition(current, proposed, migrations)
            write_owned(self.root, SESSION_FILE, proposed, MAX_SESSION_BYTES)
            return copy.deepcopy(proposed), copy.deepcopy(row)

    def recover_interrupted_turn(
        self,
        *,
        observed_domain_checkpoint: Mapping[str, Any],
        observed_tool_manifest_hash: str,
        observed_app_state_hash: str | None = None,
        expected_activation_generation: int,
        expected_checkpoint_binding: str,
    ) -> tuple[dict, tuple[dict, ...]]:
        """Close an idle crash marker and reconcile one provable write terminal.

        Recovery never invents a tool result.  It may adopt at most one
        unarchived terminal from the interrupted turn, and only when that
        ledger entry alone proves the exact live post-context.  Ambiguity fails
        closed before writer takeover or a new provider request.
        """

        observed_domain = _validate_domain(
            _snapshot(observed_domain_checkpoint, "恢复领域检查点"),
        )
        observed_tool_manifest_hash = _digest(
            observed_tool_manifest_hash,
            "observed_tool_manifest_hash",
        )
        if observed_app_state_hash is not None:
            observed_app_state_hash = _digest(
                observed_app_state_hash,
                "observed_app_state_hash",
            )
        _counter(expected_activation_generation, "expected_activation_generation")
        _digest(expected_checkpoint_binding, "expected_checkpoint_binding")
        with file_lock(self.lock_path):
            current = _read_record(self.root)
            normalize_native_session(current)
            if current["activation_generation"] != expected_activation_generation:
                _fail("原生会话激活代数已变化", "stale_context")
            if current["checkpoint_binding"] != expected_checkpoint_binding:
                _fail("原生会话联合检查点已变化", "stale_context")
            last_turn = current["last_turn"]
            if last_turn is None or last_turn["status"] not in {"idle", "error"}:
                return copy.deepcopy(current), ()

            existing_ids = {
                row["call_id"] for row in current["tool_settlement_refs"]
            }
            candidates: list[tuple[dict, dict]] = []
            candidate_ids: set[str] = set()
            try:
                ledger = _read_write_ledger(self.root)
            except FileNotFoundError:
                ledger = None
            if ledger is not None:
                if ledger["conversation_id"] != current["conversation_id"]:
                    _fail("候选写账本不属于当前会话", "stale_context")
                for entry in ledger["entries"].values():
                    identity = entry.get("identity")
                    if (
                        entry.get("state") == "inflight"
                        or not isinstance(identity, Mapping)
                        or identity.get("turn_id") != last_turn["turn_id"]
                        or identity.get("call_id") in existing_ids
                    ):
                        continue
                    try:
                        proposed, row, _migrations = (
                            _candidate_checkpoint_from_terminal(
                                current,
                                entry,
                                expected_turn_id=last_turn["turn_id"],
                                expected_epoch_id=identity.get("epoch_id"),
                                post_domain_checkpoint=observed_domain,
                                post_tool_manifest_hash=observed_tool_manifest_hash,
                                workflow_stage=_current_workflow_stage(current),
                            )
                        )
                    except PlanError:
                        continue
                    candidates.append((proposed, row))
                    candidate_ids.add(identity["call_id"])

            # The generic App-call ledger covers reads and every write class,
            # not only legacy candidate mutations.  It can be reconciled only
            # when the caller supplies the authoritative complete App-state
            # hash; domain revisions alone cannot prove commit versus rollback.
            if observed_app_state_hash is not None:
                app_ledger = NativeAppCallLedger(
                    self.root,
                    conversation_id=current["conversation_id"],
                )
                app_snapshot = app_ledger.read()
                app_ref = native_app_call_ledger_checkpoint_ref(app_snapshot)
                generic_entries = [
                    (call_id, entry)
                    for call_id, entry in app_snapshot["entries"].items()
                    if isinstance(entry.get("identity"), Mapping)
                    and entry["identity"].get("turn_id") == last_turn["turn_id"]
                    and call_id not in existing_ids
                    and call_id not in candidate_ids
                ]
                # Calls are serial and every accepted call checkpoints before
                # another is admitted.  If the current ledger revision is
                # already anchored, no generic recovery remains.  Otherwise
                # only the final insertion can be the crash-edge terminal;
                # replaying or re-adopting older calls would violate that
                # protocol order.
                if app_ref in current["app_action_refs"]:
                    generic_entries = []
                elif generic_entries:
                    generic_entries = [generic_entries[-1]]
                for call_id, entry in generic_entries:
                    identity = entry.get("identity")
                    recovery = app_ledger.classify_recovery(
                        call_id=call_id,
                        observed_state_hash=observed_app_state_hash,
                    )
                    classification = recovery["classification"]
                    if classification == RECOVERY_CONFLICT:
                        _fail(
                            "中断App调用缺少可证明的提交边界",
                            "settlement_conflict",
                        )
                    domain = current["domain_checkpoint"]
                    if (
                        identity.get("document_id") != domain["document_id"]
                        or identity.get("candidate_revision")
                        != domain["candidate_revision"]
                        or identity.get("stage_revision") != domain["stage_revision"]
                        or identity.get("database_fingerprint")
                        != domain["database_fingerprint"]
                        or identity.get("tool_manifest_hash")
                        != current["tool_manifest_hash"]
                        or identity.get("writer_fence") != domain["writer_fence"]
                    ):
                        _fail("中断App调用pre状态不属于当前会话", "stale_context")
                    generic = copy.deepcopy(current)
                    if classification == RECOVERY_APPLIED:
                        generic["domain_checkpoint"] = copy.deepcopy(observed_domain)
                        generic["tool_manifest_hash"] = observed_tool_manifest_hash
                    elif (
                        observed_domain != domain
                        or observed_tool_manifest_hash
                        != current["tool_manifest_hash"]
                    ):
                        _fail("未提交App调用与实时状态冲突", "settlement_conflict")
                    state_changed = (
                        entry["before_state_hash"] != entry["after_state_hash"]
                    )
                    row = _validate_settlement_ref({
                        "call_id": call_id,
                        "args_hash": identity["raw_arguments_hash"],
                        "before_fingerprint": entry["before_state_hash"],
                        "after_fingerprint": entry["after_state_hash"],
                        "status": entry["state"],
                        "effect": (
                            "candidate_write" if state_changed else "read_only"
                        ),
                        "resource_refs": [],
                        "workflow_stage": _current_workflow_stage(current),
                    })
                    if app_ref not in generic["app_action_refs"]:
                        if len(generic["app_action_refs"]) >= MAX_AUXILIARY_REFS:
                            _fail("app_action_refs已满，请新建对话", "agent_size_limit")
                        generic["app_action_refs"].append(app_ref)
                    refresh_checkpoint_binding(generic)
                    normalize_native_session(generic)
                    candidates.append((generic, row))
                    candidate_ids.add(call_id)
            if len(candidates) > 1:
                _fail("中断轮次存在多个可归档候选写终态", "settlement_conflict")

            proposed = _snapshot(current, "中断轮次恢复提案")
            recovered: tuple[dict, ...] = ()
            if candidates:
                proposed, row = candidates[0]
                if len(proposed["tool_settlement_refs"]) >= MAX_SETTLEMENT_REFS:
                    _fail("工具结算引用已满，请新建对话", "agent_size_limit")
                proposed["tool_settlement_refs"].append(row)
                refresh_checkpoint_binding(proposed)
                recovered = (copy.deepcopy(row),)
            if last_turn["status"] == "idle":
                set_last_turn(
                    proposed,
                    turn_id=last_turn["turn_id"],
                    status="interrupted",
                    provider_request_possible=last_turn[
                        "provider_request_possible"
                    ],
                    terminal_reason="process_interrupted",
                )
            elif not candidates:
                return copy.deepcopy(current), ()
            normalize_native_session(proposed)
            write_owned(self.root, SESSION_FILE, proposed, MAX_SESSION_BYTES)
            return copy.deepcopy(proposed), recovered

    def append_candidate_write_settlement(
        self,
        *,
        call_id: str,
        expected_activation_generation: int,
        expected_checkpoint_binding: str,
        workflow_stage: str | None = None,
    ) -> tuple[dict, dict]:
        """Archive one terminal write by reading this session's durable ledger.

        The caller supplies only the call identity and CAS boundary.  It cannot
        inject a terminal payload, choose another ledger path, or project a
        result whose post-context is not the exact current session context.
        """
        call_id = _identity(call_id, "call_id")
        _counter(expected_activation_generation, "expected_activation_generation")
        _digest(expected_checkpoint_binding, "expected_checkpoint_binding")
        workflow_stage = _optional_identity(workflow_stage, "workflow_stage")
        with file_lock(self.lock_path):
            current = _read_record(self.root)
            normalize_native_session(current)
            try:
                ledger = _read_write_ledger(self.root)
            except FileNotFoundError:
                raise PlanError(
                    "候选写账本不存在", "unknown_settlement",
                ) from None
            if ledger["conversation_id"] != current["conversation_id"]:
                _fail("候选写账本不属于当前会话", "stale_context")
            entry = ledger["entries"].get(call_id)
            if entry is None:
                _fail("候选写账本没有对应call_id", "unknown_settlement")
            if entry["state"] == "inflight":
                _fail("候选写账本尚未到达终态", "unknown_settlement")
            stage = (
                workflow_stage if workflow_stage is not None
                else _current_workflow_stage(current)
            )
            row = _settlement_from_terminal_entry(
                current, entry, workflow_stage=stage,
            )
            existing = next(
                (item for item in current["tool_settlement_refs"]
                 if item["call_id"] == call_id),
                None,
            )
            if existing is not None:
                if existing != row:
                    _fail(
                        "工具结算引用已经以不同内容保存",
                        "settlement_conflict",
                    )
                return copy.deepcopy(current), copy.deepcopy(existing)
            if current["activation_generation"] != expected_activation_generation:
                _fail("原生会话激活代数已变化", "stale_context")
            if current["checkpoint_binding"] != expected_checkpoint_binding:
                _fail("原生会话联合检查点已变化", "stale_context")
            _validate_terminal_entry_post_context(current, entry)
            if len(current["tool_settlement_refs"]) >= MAX_SETTLEMENT_REFS:
                _fail("工具结算引用已满，请新建对话", "agent_size_limit")
            current["tool_settlement_refs"].append(row)
            refresh_checkpoint_binding(current)
            normalize_native_session(current)
            write_owned(self.root, SESSION_FILE, current, MAX_SESSION_BYTES)
            return copy.deepcopy(current), copy.deepcopy(row)

    def activate_writer(
        self,
        session,
        *,
        proposed_fence: str | None = None,
        expected_activation_generation: int,
    ) -> tuple[dict, SessionWriteAuthority]:
        """Explicitly activate by attaching the saved fence and CAS-rotating it.

        A pending takeover is resumed with its previously persisted proposed
        fence.  Supplying a different fence in that state is rejected.
        """
        _counter(expected_activation_generation, "expected_activation_generation")
        if proposed_fence is not None:
            proposed_fence = _writer_fence(proposed_fence, "proposed_fence")
        session_lock = getattr(session, "_lock", None)
        if not isinstance(session_lock, _RLOCK_TYPE):
            _fail("writer接管需要session共享RLock", "native_write_lock_required")

        # Keep the in-process session lock for the complete handoff, but never
        # nest the store's cross-process lock with the writer-authority lock.
        # This preserves the candidate writer's session -> authority order and
        # prevents future settlement persistence from creating an AB/BA cycle.
        with session_lock:
            session_root = getattr(session, "root", None)
            if session_root is None or Path(session_root).absolute() != self.root:
                _fail("writer接管会话目录与持久记录不一致", "stale_context")
            if (not callable(getattr(session, "_read_db", None))
                    or getattr(session, "db_path", None) is None):
                _fail(
                    "writer接管需要session数据库锁边界",
                    "native_write_database_boundary_required",
                )
            with file_lock(self.lock_path):
                record = _read_record(self.root)
                normalize_native_session(record)
                pending = record["writer_takeover"]
                receipt = record["writer_takeover_receipt"]
                replay_completed = False
                if pending is None:
                    if (proposed_fence is not None and receipt is not None
                            and expected_activation_generation
                            == receipt["expected_activation_generation"]
                            and proposed_fence == receipt["proposed_fence"]):
                        # Exact replay of the last persisted proposal after the
                        # final conversation publication lost its acknowledgement.
                        pending = copy.deepcopy(receipt)
                        replay_completed = True
                    elif record["activation_generation"] != expected_activation_generation:
                        _fail("原生会话激活代数已变化", "stale_context")
                if pending is None:
                    if proposed_fence is None:
                        _fail("首次writer接管必须提供新fence", "native_write_authority_invalid")
                    old_fence = record["domain_checkpoint"]["writer_fence"]
                    if proposed_fence == old_fence:
                        _fail("新writer fence必须不同于旧值", "native_write_authority_invalid")
                    pending = _takeover_proposal(
                        record,
                        expected_activation_generation=expected_activation_generation,
                        expected_fence=old_fence,
                        proposed_fence=proposed_fence,
                    )
                    record["activation_generation"] = pending["activation_generation"]
                    record["writer_takeover"] = pending
                    refresh_checkpoint_binding(record)
                    normalize_native_session(record)
                    write_owned(self.root, SESSION_FILE, record, MAX_SESSION_BYTES)
                elif not replay_completed:
                    if proposed_fence is not None:
                        if (proposed_fence != pending["proposed_fence"]
                                or expected_activation_generation
                                != pending["expected_activation_generation"]):
                            _fail(
                                "writer接管重试必须精确重放已保存proposal",
                                "stale_context",
                            )
                    elif expected_activation_generation not in {
                            pending["expected_activation_generation"],
                            pending["activation_generation"],
                    }:
                        _fail("writer接管恢复代数与已保存proposal不一致", "stale_context")
                    old_fence = pending["expected_fence"]
                    proposed_fence = pending["proposed_fence"]
                pending = copy.deepcopy(pending)
                document_id = record["domain_checkpoint"]["document_id"]

            authority = None
            if replay_completed:
                authority = SessionWriteAuthority(
                    session,
                    writer_fence=proposed_fence,
                    document_id=document_id,
                )
                authority.current_fence()
                return copy.deepcopy(record), authority
            try:
                authority = SessionWriteAuthority(
                    session,
                    writer_fence=old_fence,
                    document_id=document_id,
                )
                authority.transfer(
                    expected_fence=old_fence,
                    new_fence=proposed_fence,
                )
            except PlanError as old_error:
                # transfer may have durably committed and lost its acknowledgement.
                try:
                    authority = SessionWriteAuthority(
                        session,
                        writer_fence=proposed_fence,
                        document_id=document_id,
                    )
                    authority.current_fence()
                except PlanError:
                    raise old_error

            with file_lock(self.lock_path):
                current = _read_record(self.root)
                normalize_native_session(current)
                # Another process may have completed the exact persisted CAS.
                if (current["activation_generation"] == pending["activation_generation"]
                        and current["writer_takeover"] is None
                        and current["writer_takeover_receipt"] == pending
                        and current["domain_checkpoint"]["writer_fence"]
                        == proposed_fence):
                    return copy.deepcopy(current), authority
                if (current["conversation_id"] != record["conversation_id"]
                        or current["activation_generation"]
                        != pending["activation_generation"]
                        or current["writer_takeover"] != pending
                        or current["domain_checkpoint"]["writer_fence"] != old_fence):
                    _fail("writer接管期间会话检查点发生变化", "stale_context")
                current["domain_checkpoint"]["writer_fence"] = proposed_fence
                current["writer_takeover"] = None
                current["writer_takeover_receipt"] = pending
                refresh_checkpoint_binding(current)
                normalize_native_session(current)
                write_owned(self.root, SESSION_FILE, current, MAX_SESSION_BYTES)
                return copy.deepcopy(current), authority


__all__ = [
    "CONTRACT",
    "MAX_RESUME_VISIBLE_MESSAGES",
    "MAX_RESUME_VISIBLE_TEXT_CHARS",
    "MAX_SESSION_BYTES",
    "MAX_VISIBLE_MESSAGES",
    "MAX_WRITE_LEDGER_BYTES",
    "WRITE_LEDGER_FILE",
    "NativeAuxiliaryRefsV1",
    "NativeSessionStore",
    "accept_user_turn",
    "app_action_checkpoint_ref",
    "append_auxiliary_refs",
    "append_tool_settlement_ref",
    "assess_activation",
    "create_native_session",
    "native_app_call_ledger_checkpoint_ref",
    "new_writer_fence",
    "normalize_native_session",
    "redact_visible_text",
    "refresh_checkpoint_binding",
    "replace_domain_checkpoint",
    "research_ledger_checkpoint_ref",
    "resume_seed",
    "set_current_workflow_stage",
    "set_last_turn",
    "verify_native_app_call_ledger_checkpoint_ref",
    "verify_research_ledger_checkpoint_ref",
]
