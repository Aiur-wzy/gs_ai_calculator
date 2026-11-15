"""App-owned Phase 4 write transaction ledger and candidate patch seam.

The DSH runtime is an untrusted, ephemeral caller.  This module records the
durable boundary around one App-owned candidate mutation without giving the
runtime authority over candidate state.  Raw tool arguments are reduced to
their exact UTF-8 hash before admission.  A terminal settlement is immutable;
an interrupted or otherwise ambiguous write becomes
``unknown_after_interruption`` and can never be executed again automatically.

This Phase 4A module is deliberately local-only.  It does not own a pipe,
provider, GUI, or runtime process.  Cross-process dispatch and crash injection
are later phase gates.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import threading
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_db.locking import database_lock, file_lock

from . import tools
from .bridge_protocol import JSON_SAFE_INTEGER_MAX, decode_json_object, raw_json_hash
from .sessions import MAX_CHECKPOINT, read_owned, safe_path, write_owned

CONTRACT = "genshin-native-write-transaction-ledger-v1"
PATCH_TOOL = "gs_patch_candidate"
MAX_RESULT_BYTES = 64 * 1024
MAX_ENTRIES = 131_072
MAX_AUTHORITY_BYTES = 16 * 1024
AUTHORITY_CONTRACT = "genshin-native-writer-authority-v1"
AUTHORITY_FILE = "native-writer-authority.json"
AUTHORITY_LOCK_FILE = ".native-writer-authority.lock"

INFLIGHT = "inflight"
SETTLED_SUCCESS = "settled_success"
SETTLED_FAILURE = "settled_failure"
UNKNOWN = "unknown_after_interruption"
TERMINAL_STATES = frozenset({SETTLED_SUCCESS, SETTLED_FAILURE, UNKNOWN})

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SENSITIVE_PREFIXES = (
    "api-key:", "api_key:", "authorization:", "basic:", "bearer:",
    "secret:", "sk-", "token:",
)
_RLOCK_TYPE = type(threading.RLock())
_IDENTITY_KEYS = {
    "conversation_id", "turn_id", "step_id", "epoch_id", "document_id", "call_id",
    "tool_name", "canonical_args_hash", "runtime_generation",
    "candidate_revision", "stage_revision", "database_fingerprint",
    "tool_manifest_hash", "writer_fence",
}
_ENTRY_KEYS = {
    "identity", "document_id", "state", "started_at", "settled_at",
    "before_fingerprint", "after_fingerprint", "result_json",
    "result_hash", "result_code", "terminal_reason",
    "post_candidate_revision", "post_stage_revision",
    "post_tool_manifest_hash",
}
_ROOT_KEYS = {
    "contract", "conversation_id", "created_at", "updated_at", "entries",
}


def _fail(message: str, reason: str = "native_write_ledger_invalid", details=None) -> None:
    raise PlanError(message, reason, details)


def _stamp() -> str:
    return datetime.now(UTC).isoformat()


def _identity(value: Any, name: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        _fail(f"{name}不符合桥协议身份语法")
    if value.lower().startswith(_SENSITIVE_PREFIXES):
        _fail(f"{name}不能承载凭据或秘密")
    return value


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        _fail(f"{name}必须是小写SHA-256")
    return value


def _revision(value: Any, name: str) -> int:
    if type(value) is not int or not 0 <= value <= JSON_SAFE_INTEGER_MAX:
        _fail(f"{name}必须是非负安全整数")
    return value


def _portable_json(value: Any, *, depth: int = 0) -> None:
    if depth > 64:
        _fail("写工具结果JSON嵌套超过上限")
    if value is None or isinstance(value, (str, bool)):
        return
    if type(value) is int:
        if abs(value) > JSON_SAFE_INTEGER_MAX:
            _fail("写工具结果整数超过JavaScript安全范围")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("写工具结果包含非有限数值")
        if value == 0.0 and math.copysign(1.0, value) < 0:
            _fail("写工具结果不能包含负零")
        return
    if isinstance(value, list):
        for item in value:
            _portable_json(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("写工具结果对象字段名必须是字符串")
            _portable_json(item, depth=depth + 1)
        return
    _fail("写工具结果不属于可移植JSON")


def _result_record(result: Mapping) -> tuple[str, str]:
    if not isinstance(result, Mapping):
        _fail("写工具结果必须是JSON对象")
    detached = copy.deepcopy(dict(result))
    _portable_json(detached)
    try:
        encoded = json.dumps(
            detached,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError, OverflowError) as exc:
        raise PlanError(
            "写工具结果不是规范JSON", "native_write_ledger_invalid",
        ) from exc
    if not encoded or len(encoded) > MAX_RESULT_BYTES:
        _fail("写工具结算结果超过64KiB", "native_write_result_limit")
    return encoded.decode("utf-8"), hashlib.sha256(encoded).hexdigest()


def _result_code(value: Any) -> str | int | None:
    if value is None:
        return None
    if type(value) is int and -JSON_SAFE_INTEGER_MAX <= value <= JSON_SAFE_INTEGER_MAX:
        return value
    if isinstance(value, str):
        code = _identity(value, "result_code")
        if code == "0":
            _fail("result_code字符串不能伪装成成功码0")
        return code
    _fail("result_code必须是短机器码或安全整数")


def _validate_terminal_result(
    state: str,
    *,
    result: Mapping,
    result_code: str | int | None,
    terminal_reason: str | None,
) -> None:
    """Bind a terminal ledger state to the exact stored tool result."""
    code = _result_code(result_code)
    raw_code = result.get("code")
    if state == SETTLED_SUCCESS:
        if type(code) is not int or code != 0:
            _fail("成功写事务必须使用整数result_code 0")
        if type(raw_code) is not int or raw_code != 0:
            _fail("成功写事务的结果JSON必须包含整数code 0")
        if terminal_reason is not None:
            _fail("成功写事务不能携带失败原因")
        return
    if state != SETTLED_FAILURE:
        _fail("只有成功或失败结算可以保存普通工具结果")
    if code is None or code == 0:
        _fail("失败写事务必须使用非零result_code")
    normalized_raw_code = _result_code(raw_code)
    if normalized_raw_code is None or normalized_raw_code == 0:
        _fail("失败写事务的结果JSON必须包含非零code")
    raw_reason = result.get("reason")
    reason_matches = (
        isinstance(raw_reason, str)
        and _IDENTITY.fullmatch(raw_reason) is not None
        and code == raw_reason
    )
    if code != normalized_raw_code and not reason_matches:
        _fail("失败写事务的result_code必须匹配结果code或reason")
    if terminal_reason != str(code):
        _fail("失败写事务的terminal_reason必须匹配result_code")


def make_write_identity(
    *,
    conversation_id: str,
    turn_id: str,
    step_id: str,
    epoch_id: str,
    document_id: str,
    call_id: str,
    tool_name: str,
    canonical_args_hash: str,
    runtime_generation: str,
    candidate_revision: int,
    stage_revision: int,
    database_fingerprint: str,
    tool_manifest_hash: str,
    writer_fence: str,
) -> dict:
    """Build the complete immutable identity of one candidate write."""
    row = {
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "step_id": step_id,
        "epoch_id": epoch_id,
        "document_id": document_id,
        "call_id": call_id,
        "tool_name": tool_name,
        "canonical_args_hash": canonical_args_hash,
        "runtime_generation": runtime_generation,
        "candidate_revision": candidate_revision,
        "stage_revision": stage_revision,
        "database_fingerprint": database_fingerprint,
        "tool_manifest_hash": tool_manifest_hash,
        "writer_fence": writer_fence,
    }
    return _validate_write_identity(row)


def _validate_write_identity(value: Any) -> dict:
    if not isinstance(value, dict) or set(value) != _IDENTITY_KEYS:
        _fail("写工具调用身份字段不合法")
    row = copy.deepcopy(value)
    for name in (
        "conversation_id", "turn_id", "step_id", "epoch_id", "document_id", "call_id",
        "tool_name", "runtime_generation", "writer_fence",
    ):
        _identity(row.get(name), name)
    for name in (
        "canonical_args_hash", "database_fingerprint", "tool_manifest_hash",
    ):
        _digest(row.get(name), name)
    _revision(row.get("candidate_revision"), "candidate_revision")
    _revision(row.get("stage_revision"), "stage_revision")
    return row


def create_write_ledger(conversation_id: str) -> dict:
    conversation_id = _identity(conversation_id, "conversation_id")
    now = _stamp()
    return {
        "contract": CONTRACT,
        "conversation_id": conversation_id,
        "created_at": now,
        "updated_at": now,
        "entries": {},
    }


def _validate_entry(call_id: str, value: Any, conversation_id: str) -> None:
    _identity(call_id, "call_id")
    if not isinstance(value, dict) or set(value) != _ENTRY_KEYS:
        _fail("写事务记录字段不合法")
    identity = _validate_write_identity(value.get("identity"))
    if (identity["call_id"] != call_id
            or identity["conversation_id"] != conversation_id):
        _fail("写事务记录索引或会话不一致")
    document_id = _identity(value.get("document_id"), "document_id")
    if document_id != identity["document_id"]:
        _fail("写事务记录的文档身份与调用身份不一致")
    state = value.get("state")
    if state not in {INFLIGHT, *TERMINAL_STATES}:
        _fail("写事务状态不合法")
    if not isinstance(value.get("started_at"), str) or not value["started_at"]:
        _fail("写事务开始时间不合法")
    _digest(value.get("before_fingerprint"), "before_fingerprint")
    if state == INFLIGHT:
        if any(value.get(name) is not None for name in (
            "settled_at", "after_fingerprint", "result_json", "result_hash",
            "result_code", "terminal_reason", "post_candidate_revision",
            "post_stage_revision", "post_tool_manifest_hash",
        )):
            _fail("执行中的写事务不能包含终态字段")
        return
    if not isinstance(value.get("settled_at"), str) or not value["settled_at"]:
        _fail("写事务结算时间不合法")
    _digest(value.get("after_fingerprint"), "after_fingerprint")
    post_candidate_revision = _revision(
        value.get("post_candidate_revision"), "post_candidate_revision",
    )
    post_stage_revision = _revision(
        value.get("post_stage_revision"), "post_stage_revision",
    )
    _digest(value.get("post_tool_manifest_hash"), "post_tool_manifest_hash")
    if state == SETTLED_SUCCESS:
        if post_candidate_revision != identity["candidate_revision"] + 1:
            _fail("成功写事务的候选修订必须按patch合同精确递增一次")
        if post_stage_revision < identity["stage_revision"]:
            _fail("成功写事务的阶段修订不能倒退")
        if value["after_fingerprint"] == value["before_fingerprint"]:
            _fail("成功写事务的前后领域指纹不能相同")
    if state == UNKNOWN:
        if value.get("result_json") is not None or value.get("result_hash") is not None:
            _fail("未知写结果不能保存结果载荷")
        if value.get("result_code") != "outcome_unknown":
            _fail("未知写结果必须使用outcome_unknown机器码")
        _identity(value.get("terminal_reason"), "terminal_reason")
        return
    result_json = value.get("result_json")
    if not isinstance(result_json, str):
        _fail("已结算写事务缺少结果JSON")
    try:
        decoded = json.loads(result_json)
    except (ValueError, RecursionError) as exc:
        raise PlanError("写事务结果JSON损坏", "native_write_ledger_invalid") from exc
    actual_json, actual_hash = _result_record(decoded)
    if actual_json != result_json or actual_hash != value.get("result_hash"):
        _fail("写事务结果哈希或规范编码不匹配")
    if value.get("terminal_reason") is not None:
        _identity(value["terminal_reason"], "terminal_reason")
    _validate_terminal_result(
        state,
        result=decoded,
        result_code=value.get("result_code"),
        terminal_reason=value.get("terminal_reason"),
    )
    if state == SETTLED_FAILURE and value["after_fingerprint"] != value["before_fingerprint"]:
        _fail("失败结算不能掩盖候选状态变化")
    if state == SETTLED_FAILURE and (
            post_candidate_revision != identity["candidate_revision"]
            or post_stage_revision != identity["stage_revision"]
            or value["post_tool_manifest_hash"] != identity["tool_manifest_hash"]):
        _fail("失败结算不能掩盖候选、阶段或工具清单变化")


def normalize_write_ledger(ledger: dict) -> None:
    """Strictly validate a persisted write ledger without adding defaults."""
    if not isinstance(ledger, dict) or set(ledger) != _ROOT_KEYS:
        _fail("写事务账字段不合法")
    if ledger.get("contract") != CONTRACT:
        _fail("写事务账版本不受支持")
    conversation_id = _identity(ledger.get("conversation_id"), "conversation_id")
    for name in ("created_at", "updated_at"):
        if not isinstance(ledger.get(name), str) or not ledger[name]:
            _fail(f"{name}不合法")
    entries = ledger.get("entries")
    if not isinstance(entries, dict) or len(entries) > MAX_ENTRIES:
        _fail("写事务记录数量不合法")
    inflight_count = 0
    for call_id, entry in entries.items():
        _validate_entry(call_id, entry, conversation_id)
        inflight_count += entry["state"] == INFLIGHT
    if inflight_count > 1:
        _fail("持久化写事务账不能包含多条inflight记录")


def _conflict(call_id: str) -> None:
    raise PlanError(
        "相同call_id对应了不同写事务身份",
        "call_id_conflict",
        {"call_id": call_id},
    )


def begin_write(ledger: dict, *, identity: dict, before_fingerprint: str) -> dict:
    """Admit once, or return the immutable existing state without executing."""
    normalize_write_ledger(ledger)
    identity = _validate_write_identity(identity)
    before_fingerprint = _digest(before_fingerprint, "before_fingerprint")
    if identity["conversation_id"] != ledger["conversation_id"]:
        _fail("写事务不属于当前会话", "stale_context")
    call_id = identity["call_id"]
    existing = ledger["entries"].get(call_id)
    if existing is not None:
        if existing["identity"] != identity:
            _conflict(call_id)
        return {
            "disposition": "inflight" if existing["state"] == INFLIGHT else "replay",
            "entry": copy.deepcopy(existing),
        }
    other_inflight = [
        call_id for call_id, entry in ledger["entries"].items()
        if entry["state"] == INFLIGHT
    ]
    if other_inflight:
        _fail(
            "首版原生Agent同一会话只允许一个在途写事务",
            "native_write_concurrency",
            {"inflight_call_id": other_inflight[0]},
        )
    if len(ledger["entries"]) >= MAX_ENTRIES:
        _fail("写事务账已满，请新建对话", "agent_size_limit")
    entry = {
        "identity": copy.deepcopy(identity),
        "document_id": identity["document_id"],
        "state": INFLIGHT,
        "started_at": _stamp(),
        "settled_at": None,
        "before_fingerprint": before_fingerprint,
        "after_fingerprint": None,
        "result_json": None,
        "result_hash": None,
        "result_code": None,
        "terminal_reason": None,
        "post_candidate_revision": None,
        "post_stage_revision": None,
        "post_tool_manifest_hash": None,
    }
    ledger["entries"][call_id] = entry
    ledger["updated_at"] = _stamp()
    return {"disposition": "execute", "entry": copy.deepcopy(entry)}


def settle_write(
    ledger: dict,
    *,
    identity: dict,
    state: str,
    after_fingerprint: str,
    post_candidate_revision: int,
    post_stage_revision: int,
    post_tool_manifest_hash: str,
    result: Mapping | None = None,
    result_code: str | int | None = None,
    terminal_reason: str | None = None,
) -> dict:
    """Create one immutable terminal record for a previously admitted write."""
    normalize_write_ledger(ledger)
    identity = _validate_write_identity(identity)
    after_fingerprint = _digest(after_fingerprint, "after_fingerprint")
    post_candidate_revision = _revision(
        post_candidate_revision, "post_candidate_revision",
    )
    post_stage_revision = _revision(post_stage_revision, "post_stage_revision")
    post_tool_manifest_hash = _digest(
        post_tool_manifest_hash, "post_tool_manifest_hash",
    )
    if state not in TERMINAL_STATES:
        _fail("写事务终态不受支持")
    call_id = identity["call_id"]
    existing = ledger["entries"].get(call_id)
    if existing is None:
        _fail("写事务没有对应的inflight记录", "unknown_settlement")
    if existing["identity"] != identity:
        _conflict(call_id)
    if state == SETTLED_FAILURE and after_fingerprint != existing["before_fingerprint"]:
        _fail(
            "写工具报告失败但候选已经变化，结果必须标记为未知",
            "native_write_failure_changed_state",
        )
    if state == SETTLED_FAILURE and (
            post_candidate_revision != identity["candidate_revision"]
            or post_stage_revision != identity["stage_revision"]
            or post_tool_manifest_hash != identity["tool_manifest_hash"]):
        _fail(
            "写工具报告失败但候选、阶段或工具清单已经变化，结果必须标记为未知",
            "native_write_failure_changed_context",
        )
    if (state == SETTLED_SUCCESS
            and post_candidate_revision != identity["candidate_revision"] + 1):
        _fail(
            "成功写事务没有按patch合同精确推进候选修订",
            "native_write_success_revision_mismatch",
        )
    if state == SETTLED_SUCCESS and post_stage_revision < identity["stage_revision"]:
        _fail(
            "成功写事务的阶段修订倒退",
            "native_write_success_stage_revision_regressed",
        )
    if state == SETTLED_SUCCESS and after_fingerprint == existing["before_fingerprint"]:
        _fail(
            "写工具报告成功但候选没有变化",
            "native_write_success_without_effect",
        )
    result_json = result_hash = None
    if state != UNKNOWN:
        if result is None:
            _fail("成功或失败结算必须提供有界结果")
        result_json, result_hash = _result_record(result)
    elif result is not None:
        _fail("未知结算不能保存结果载荷")
    terminal_reason = (
        _identity(terminal_reason, "terminal_reason")
        if terminal_reason is not None else None
    )
    normalized_result_code = _result_code(result_code)
    if state == UNKNOWN and terminal_reason is None:
        _fail("未知结算必须说明机器原因")
    if state == UNKNOWN:
        if normalized_result_code != "outcome_unknown":
            _fail("未知写结果必须使用outcome_unknown机器码")
    else:
        _validate_terminal_result(
            state,
            result=json.loads(result_json),
            result_code=normalized_result_code,
            terminal_reason=terminal_reason,
        )
    if existing["state"] != INFLIGHT:
        proposed = {
            "state": state,
            "after_fingerprint": after_fingerprint,
            "result_json": result_json,
            "result_hash": result_hash,
            "result_code": normalized_result_code,
            "terminal_reason": terminal_reason,
            "post_candidate_revision": post_candidate_revision,
            "post_stage_revision": post_stage_revision,
            "post_tool_manifest_hash": post_tool_manifest_hash,
        }
        if any(existing[name] != value for name, value in proposed.items()):
            _fail(
                "写事务已经以不同终态结算",
                "settlement_conflict",
                {"call_id": call_id},
            )
        return {"disposition": "replay", "entry": copy.deepcopy(existing)}
    entry = {
        **copy.deepcopy(existing),
        "state": state,
        "settled_at": _stamp(),
        "after_fingerprint": after_fingerprint,
        "result_json": result_json,
        "result_hash": result_hash,
        "result_code": normalized_result_code,
        "terminal_reason": terminal_reason,
        "post_candidate_revision": post_candidate_revision,
        "post_stage_revision": post_stage_revision,
        "post_tool_manifest_hash": post_tool_manifest_hash,
    }
    ledger["entries"][call_id] = entry
    ledger["updated_at"] = _stamp()
    normalize_write_ledger(ledger)
    return {"disposition": "settled", "entry": copy.deepcopy(entry)}


def lookup_write(ledger: dict, *, identity: dict) -> dict:
    normalize_write_ledger(ledger)
    identity = _validate_write_identity(identity)
    entry = ledger["entries"].get(identity["call_id"])
    if entry is None:
        return {"state": "absent"}
    if entry["identity"] != identity:
        _conflict(identity["call_id"])
    return {"state": entry["state"], "entry": copy.deepcopy(entry)}


def snapshot_write_ledger(ledger: dict) -> dict:
    normalize_write_ledger(ledger)
    return copy.deepcopy(ledger)


def _replace_ledger(target: dict, value: dict) -> None:
    target.clear()
    target.update(copy.deepcopy(value))


def recover_interrupted_writes(
    ledger: dict,
    *,
    observed_document_id: str,
    observed_fingerprint: str,
    observed_candidate_revision: int,
    observed_stage_revision: int,
    observed_tool_manifest_hash: str,
    persist: Callable[[dict], None],
    terminal_reason: str = "app_restarted",
) -> list[dict]:
    """Atomically mark every persisted inflight write unknown, never retry it."""
    normalize_write_ledger(ledger)
    observed_document_id = _identity(observed_document_id, "observed_document_id")
    observed_fingerprint = _digest(observed_fingerprint, "observed_fingerprint")
    observed_candidate_revision = _revision(
        observed_candidate_revision, "observed_candidate_revision",
    )
    observed_stage_revision = _revision(
        observed_stage_revision, "observed_stage_revision",
    )
    observed_tool_manifest_hash = _digest(
        observed_tool_manifest_hash, "observed_tool_manifest_hash",
    )
    trial = copy.deepcopy(ledger)
    changed = []
    for entry in list(trial["entries"].values()):
        if entry["state"] != INFLIGHT:
            continue
        if entry["identity"]["document_id"] != observed_document_id:
            _fail("恢复写事务不属于当前文档", "stale_context")
        settled = settle_write(
            trial,
            identity=entry["identity"],
            state=UNKNOWN,
            after_fingerprint=observed_fingerprint,
            post_candidate_revision=observed_candidate_revision,
            post_stage_revision=observed_stage_revision,
            post_tool_manifest_hash=observed_tool_manifest_hash,
            result_code="outcome_unknown",
            terminal_reason=terminal_reason,
        )
        changed.append(settled["entry"])
    if changed:
        try:
            persist(snapshot_write_ledger(trial))
        finally:
            # Persistence can commit and then lose its acknowledgement.  The
            # first unknown settlement is therefore fixed in memory before the
            # caller can retry recovery with a different observation.
            _replace_ledger(ledger, trial)
    else:
        unknown = [
            entry for entry in trial["entries"].values()
            if entry["state"] == UNKNOWN
        ]
        if unknown:
            if any(entry["identity"]["document_id"] != observed_document_id
                   for entry in unknown):
                _fail("未知写事务不属于当前文档", "stale_context")
            # Idempotent persistence retry: never regenerate an UNKNOWN from
            # the caller's newer observation.
            persist(snapshot_write_ledger(trial))
    return copy.deepcopy(changed)


def _derive_document_id(session) -> str | None:
    """Read the stable user-document identity without using mutable revisions."""
    kind = getattr(session, "kind", None)
    document = getattr(session, "document", None)
    candidate = getattr(session, "candidate", None)
    if isinstance(document, dict):
        key = "board_id" if kind == "whiteboard" else "plan_id" if kind == "native" else None
        if key is not None and document.get(key) is not None:
            return _identity(document[key], "document_id")
        for key in ("board_id", "plan_id"):
            if document.get(key) is not None:
                return _identity(document[key], "document_id")
    if isinstance(candidate, dict):
        key = "board_id" if kind == "whiteboard" else "plan_id"
        if candidate.get(key) is not None:
            return _identity(candidate[key], "document_id")
        extensions = candidate.get("extensions")
        handoff = extensions.get("whiteboard_handoff") if isinstance(extensions, dict) else None
        board = handoff.get("board") if isinstance(handoff, dict) else None
        if kind == "whiteboard" and isinstance(board, dict) and board.get("board_id") is not None:
            return _identity(board["board_id"], "document_id")
    return None


class SessionWriteAuthority:
    """Persistent writer lease and the one authoritative mutation boundary.

    A native candidate write holds, in this order, the session RLock, a
    document-scoped cross-process lock, and the database read/update lock.
    Candidate publication and write-ledger settlement therefore complete
    before a competing writer can rotate the fence or a database updater can
    replace the snapshot.  The persisted fence also rejects a stale App
    process after it later acquires the document lock.

    The fence is a concurrency capability, not a discoverable session field.
    Phase 5 conversation persistence must restore the exact previous fence on
    App restart.  A replacement App first attaches with that old fence and
    then calls :meth:`transfer`; constructing directly with a different fence
    fails closed and cannot silently take ownership.

    On first activation Phase 5 must durably record the proposed fence in the
    conversation before constructing this authority.  Retrying construction
    with that same fence is idempotent, so a crash cannot strand an
    undiscoverable lease between the two files.
    """

    def __init__(
        self,
        session,
        *,
        writer_fence: str,
        document_id: str | None = None,
    ):
        lock = getattr(session, "_lock", None)
        if not isinstance(lock, _RLOCK_TYPE):
            _fail("写权限需要session共享RLock", "native_write_lock_required")
        if not callable(getattr(session, "_read_db", None)):
            _fail(
                "写权限需要session数据库锁边界",
                "native_write_database_boundary_required",
            )
        root = getattr(session, "root", None)
        if root is None:
            _fail("写权限需要受管会话目录", "native_write_storage_required")
        db_path = getattr(session, "db_path", None)
        if db_path is None:
            _fail(
                "写权限需要受管数据库路径",
                "native_write_database_boundary_required",
            )
        root = Path(root).absolute()
        derived_document_id = _derive_document_id(session)
        if document_id is None:
            if derived_document_id is None:
                _fail(
                    "写权限无法取得稳定document_id，必须显式注入",
                    "native_write_document_required",
                )
            document_id = derived_document_id
        self.session = session
        self.lock = lock
        self.root = root
        self.db_path = Path(db_path).resolve()
        self.document_id = _identity(document_id, "document_id")
        self._writer_fence = _identity(writer_fence, "writer_fence")
        self._requires_derived_document_id = derived_document_id is not None
        if derived_document_id is not None and derived_document_id != self.document_id:
            _fail("显式document_id与session文档身份不一致", "stale_context")
        self._lease_path = safe_path(root, AUTHORITY_FILE)
        self._boundary_lock_path = safe_path(root, AUTHORITY_LOCK_FILE)
        with self.lock, file_lock(self._boundary_lock_path):
            lease = self._read_lease(optional=True)
            if lease is None:
                self._write_lease({
                    "contract": AUTHORITY_CONTRACT,
                    "document_id": self.document_id,
                    "writer_fence": self._writer_fence,
                    "generation": 0,
                })
            else:
                self._assert_lease(lease)

    def _read_lease(self, *, optional: bool = False) -> dict | None:
        if not self._lease_path.exists():
            if optional:
                return None
            _fail("持久writer lease不存在", "stale_context")
        lease = read_owned(self.root, AUTHORITY_FILE, MAX_AUTHORITY_BYTES)
        if not isinstance(lease, dict) or set(lease) != {
            "contract", "document_id", "writer_fence", "generation",
        }:
            _fail("持久writer lease结构损坏", "native_write_authority_invalid")
        if lease.get("contract") != AUTHORITY_CONTRACT:
            _fail("持久writer lease版本不受支持", "native_write_authority_invalid")
        _identity(lease.get("document_id"), "document_id")
        _identity(lease.get("writer_fence"), "writer_fence")
        _revision(lease.get("generation"), "writer_generation")
        return lease

    def _write_lease(self, lease: dict) -> None:
        write_owned(self.root, AUTHORITY_FILE, lease, MAX_AUTHORITY_BYTES)

    def _assert_document(self) -> None:
        derived = _derive_document_id(self.session)
        if self._requires_derived_document_id and derived is None:
            _fail("session已无法派生稳定document_id", "stale_context")
        if derived is not None and derived != self.document_id:
            _fail("session文档身份已经变化", "stale_context")

    def _assert_lease(self, lease: Mapping) -> None:
        if (lease["document_id"] != self.document_id
                or lease["writer_fence"] != self._writer_fence):
            _fail("writer lease已被其他写入者接管", "stale_context")

    def _assert_candidate_checkpoint(self) -> None:
        """Prove the in-memory candidate is the last durable publication."""
        checkpoint_path = safe_path(self.root, "checkpoint.json")
        if not checkpoint_path.is_file():
            _fail("候选持久检查点不存在", "native_write_checkpoint_required")
        checkpoint = read_owned(self.root, "checkpoint.json", MAX_CHECKPOINT)
        session_id = getattr(self.session, "session_id", None)
        candidate = getattr(self.session, "candidate", None)
        stage_state = getattr(self.session, "stage_state", None)
        if (not isinstance(checkpoint, dict)
                or checkpoint.get("session_id") != session_id
                or checkpoint.get("kind") != getattr(self.session, "kind", None)
                or checkpoint.get("revision") != getattr(self.session, "revision", None)
                or checkpoint.get("candidate_fingerprint") != fingerprint(candidate)
                or fingerprint(checkpoint.get("database_snapshot"))
                != fingerprint(getattr(self.session, "database_snapshot", None))
                or fingerprint(checkpoint.get("stage_state"))
                != fingerprint(stage_state)):
            _fail(
                "内存候选与最后持久检查点不一致",
                "stale_context",
            )

    @contextmanager
    def atomic_boundary(self):
        """Hold writer, document and database authority through settlement."""
        with self.lock, file_lock(self._boundary_lock_path):
            self._assert_document()
            self._assert_lease(self._read_lease())
            try:
                # Always lock the path, including the initially-missing DB
                # case where CandidateSession._read_db uses a null context.
                with database_lock(self.db_path), self.session._read_db(check=True):
                    self._assert_candidate_checkpoint()
                    yield
                    self._assert_candidate_checkpoint()
            except PlanError as exc:
                if exc.reason == "agent_database_changed":
                    raise PlanError(
                        "资料库已变化，原生写上下文失效",
                        "stale_context",
                    ) from exc
                raise

    def current_fence(self) -> str:
        """Return the live fence only while checking the persistent lease."""
        with self.lock, file_lock(self._boundary_lock_path):
            self._assert_lease(self._read_lease())
            return self._writer_fence

    def transfer(self, *, expected_fence: str, new_fence: str) -> dict:
        """CAS-transfer ownership; waits for any active write to settle first."""
        expected_fence = _identity(expected_fence, "expected_fence")
        new_fence = _identity(new_fence, "new_fence")
        if new_fence == expected_fence:
            _fail("新writer fence必须不同于旧值", "native_write_authority_invalid")
        with self.lock, file_lock(self._boundary_lock_path):
            self._assert_document()
            lease = self._read_lease()
            self._assert_lease(lease)
            if expected_fence != self._writer_fence:
                _fail("writer lease CAS条件不匹配", "stale_context")
            if lease["generation"] == JSON_SAFE_INTEGER_MAX:
                _fail("writer lease代数已达上限", "native_write_authority_invalid")
            replacement = {
                **lease,
                "writer_fence": new_fence,
                "generation": lease["generation"] + 1,
            }
            self._write_lease(replacement)
            self._writer_fence = new_fence
            return copy.deepcopy(replacement)


class SessionCandidatePatchAdapter:
    """Narrow local adapter for the existing authoritative candidate tool.

    ``SessionWriteAuthority`` owns the lease, document lock and database lock.
    The manifest remains a live reader because stage transitions can change it
    inside the same atomic candidate transaction.
    """

    def __init__(
        self,
        session,
        *,
        tool_manifest_hash: Callable[[], str],
        authority: SessionWriteAuthority,
        document_id: str | None = None,
    ):
        lock = getattr(session, "_lock", None)
        if not isinstance(lock, _RLOCK_TYPE):
            _fail("候选写适配需要session共享RLock", "native_write_lock_required")
        if not callable(tool_manifest_hash):
            _fail("候选写适配缺少manifest读取器")
        if not isinstance(authority, SessionWriteAuthority):
            _fail("候选写适配缺少正式原子写权限", "native_write_authority_required")
        if authority.session is not session or authority.lock is not lock:
            _fail("写权限与候选session没有共享同一RLock", "native_write_lock_required")
        self.session = session
        self.lock = lock
        self.authority = authority
        self._tool_manifest_hash = tool_manifest_hash
        derived_document_id = _derive_document_id(session)
        if document_id is None:
            document_id = authority.document_id
        self._document_id = _identity(document_id, "document_id")
        if self._document_id != authority.document_id:
            _fail("候选写适配与writer lease文档身份不一致", "stale_context")
        self._requires_derived_document_id = derived_document_id is not None
        if derived_document_id is not None and derived_document_id != self._document_id:
            _fail("显式document_id与session文档身份不一致", "stale_context")

    def current_context(self) -> dict:
        derived_document_id = _derive_document_id(self.session)
        if self._requires_derived_document_id and derived_document_id is None:
            _fail("session已无法派生稳定document_id", "stale_context")
        if derived_document_id is not None and derived_document_id != self._document_id:
            _fail("session文档身份已经变化", "stale_context")
        stage_state = getattr(self.session, "stage_state", {})
        stage_revision = (
            stage_state.get("stage_revision", 0)
            if isinstance(stage_state, dict) else 0
        )
        return {
            "document_id": self._document_id,
            "candidate_revision": _revision(
                getattr(self.session, "revision", None), "candidate_revision",
            ),
            "stage_revision": _revision(stage_revision, "stage_revision"),
            "database_fingerprint": fingerprint(
                getattr(self.session, "database_snapshot", None),
            ),
            "tool_manifest_hash": _digest(
                self._tool_manifest_hash(), "tool_manifest_hash",
            ),
            "writer_fence": _identity(
                self.authority.current_fence(), "writer_fence",
            ),
            "domain_fingerprint": fingerprint({
                "candidate": getattr(self.session, "candidate", None),
                "workflow_phase": getattr(self.session, "workflow_stage", None),
                "stage_state": stage_state,
            }),
        }

    def atomic_boundary(self):
        return self.authority.atomic_boundary()

    def execute(self, tool_name: str, arguments: dict) -> dict:
        with self.atomic_boundary():
            if tool_name != PATCH_TOOL:
                _fail("Phase 4A适配只允许候选patch", "native_write_tool_forbidden")
            visible = {
                row.get("name") for row in tools.definitions_for_session(self.session)
                if isinstance(row, dict)
            }
            if tool_name not in visible:
                _fail("当前阶段未披露候选patch", "native_write_tool_forbidden")
            return tools.call_tool(self.session, tool_name, arguments)


class NativeWriteCoordinator:
    """Persist-before-execute coordinator for one restricted candidate write."""

    _BINDING_KEYS = frozenset({
        "conversation_id", "turn_id", "step_id", "epoch_id", "document_id",
        "runtime_generation", "database_fingerprint", "tool_manifest_hash",
        "writer_fence",
    })

    def __init__(
        self,
        adapter: SessionCandidatePatchAdapter,
        *,
        binding: Mapping,
        ledger: dict,
        persist: Callable[[dict], None],
    ):
        if not isinstance(binding, Mapping) or set(binding) != self._BINDING_KEYS:
            _fail("候选写绑定字段不合法")
        if not callable(getattr(adapter, "atomic_boundary", None)):
            _fail(
                "候选写适配没有统一原子边界",
                "native_write_authority_required",
            )
        self.adapter = adapter
        self.binding = {
            name: (_digest(binding[name], name) if name in {
                "database_fingerprint", "tool_manifest_hash",
            } else _identity(binding[name], name))
            for name in self._BINDING_KEYS
        }
        normalize_write_ledger(ledger)
        if ledger["conversation_id"] != self.binding["conversation_id"]:
            _fail("候选写账本与绑定会话不一致", "stale_context")
        if not callable(persist):
            _fail("候选写账本缺少原子持久化回调")
        self.ledger = ledger
        self.persist = persist

    def _persist_mutation(
        self,
        operation: Callable[[dict], dict],
        *,
        preserve_on_error: bool = False,
    ) -> dict:
        trial = copy.deepcopy(self.ledger)
        result = operation(trial)
        try:
            self.persist(snapshot_write_ledger(trial))
        except Exception:
            if preserve_on_error:
                # The persistence callback may have durably written and then
                # failed to acknowledge.  Retaining the inflight admission in
                # memory prevents this process from executing the write.
                _replace_ledger(self.ledger, trial)
            raise
        _replace_ledger(self.ledger, trial)
        return result

    @staticmethod
    def _decode_result(entry: dict) -> dict:
        if entry["state"] == UNKNOWN:
            _fail(
                "该写调用结果未知，必须核对当前候选，不能自动重放",
                "write_outcome_unknown",
                {"call_id": entry["identity"]["call_id"]},
            )
        if entry["state"] == INFLIGHT:
            _fail(
                "相同写调用仍处于inflight，不能自动重放",
                "write_call_inflight",
                {"call_id": entry["identity"]["call_id"]},
            )
        try:
            value = json.loads(entry["result_json"])
        except (ValueError, TypeError, RecursionError) as exc:
            raise PlanError(
                "写工具结算结果损坏", "native_write_ledger_invalid",
            ) from exc
        if not isinstance(value, dict):
            _fail("写工具结算结果必须是对象")
        return value

    @staticmethod
    def _validate_replay_context(entry: dict, context: Mapping) -> None:
        identity = entry["identity"]
        if (context["document_id"] != identity["document_id"]
                or context["writer_fence"] != identity["writer_fence"]
                or context["database_fingerprint"] != identity["database_fingerprint"]):
            _fail(
                "旧写结算绑定的文档、数据库或writer fence已经失效",
                "stale_context",
            )
        if entry["state"] == INFLIGHT:
            expected_candidate = identity["candidate_revision"]
            expected_stage = identity["stage_revision"]
            expected_manifest = identity["tool_manifest_hash"]
            expected_domain = entry["before_fingerprint"]
        else:
            expected_candidate = entry["post_candidate_revision"]
            expected_stage = entry["post_stage_revision"]
            expected_manifest = entry["post_tool_manifest_hash"]
            expected_domain = entry["after_fingerprint"]
        if (context["candidate_revision"] != expected_candidate
                or context["stage_revision"] != expected_stage
                or context["tool_manifest_hash"] != expected_manifest
                or context["domain_fingerprint"] != expected_domain):
            _fail("旧写结算不再对应当前领域检查点", "stale_context")

    def _identity(
        self,
        *,
        call_id: str,
        tool_name: str,
        canonical_args_hash: str,
        candidate_revision: int,
        stage_revision: int,
    ) -> dict:
        return make_write_identity(
            conversation_id=self.binding["conversation_id"],
            turn_id=self.binding["turn_id"],
            step_id=self.binding["step_id"],
            epoch_id=self.binding["epoch_id"],
            document_id=self.binding["document_id"],
            call_id=call_id,
            tool_name=tool_name,
            canonical_args_hash=canonical_args_hash,
            runtime_generation=self.binding["runtime_generation"],
            candidate_revision=candidate_revision,
            stage_revision=stage_revision,
            database_fingerprint=self.binding["database_fingerprint"],
            tool_manifest_hash=self.binding["tool_manifest_hash"],
            writer_fence=self.binding["writer_fence"],
        )

    def execute_patch(
        self,
        *,
        call_id: str,
        arguments_json: str,
        canonical_args_hash: str,
        candidate_revision: int,
        stage_revision: int,
    ) -> dict:
        """Execute one candidate patch exactly once within the App boundary."""
        claimed_hash = _digest(canonical_args_hash, "canonical_args_hash")
        if raw_json_hash(arguments_json) != claimed_hash:
            _fail("候选写参数哈希不匹配", "native_write_args_hash_mismatch")
        arguments = decode_json_object(arguments_json)
        identity = self._identity(
            call_id=_identity(call_id, "call_id"),
            tool_name=PATCH_TOOL,
            canonical_args_hash=claimed_hash,
            candidate_revision=_revision(candidate_revision, "candidate_revision"),
            stage_revision=_revision(stage_revision, "stage_revision"),
        )

        # The boundary remains held through inflight persistence, authoritative
        # candidate publication and terminal ledger persistence.  A writer
        # transfer or database update cannot enter the commit window.
        with self.adapter.atomic_boundary():
            prior = lookup_write(self.ledger, identity=identity)
            if prior["state"] != "absent":
                self._validate_replay_context(
                    prior["entry"], self.adapter.current_context(),
                )
                return self._decode_result(prior["entry"])

            context = self.adapter.current_context()
            expected = {
                "document_id": self.binding["document_id"],
                "candidate_revision": candidate_revision,
                "stage_revision": stage_revision,
                "database_fingerprint": self.binding["database_fingerprint"],
                "tool_manifest_hash": self.binding["tool_manifest_hash"],
                "writer_fence": self.binding["writer_fence"],
            }
            if any(context[name] != value for name, value in expected.items()):
                _fail("候选写上下文、修订或writer fence已经失效", "stale_context")
            before = context["domain_fingerprint"]
            self._persist_mutation(lambda trial: begin_write(
                trial,
                identity=identity,
                before_fingerprint=before,
            ), preserve_on_error=True)

            def settle_unknown(after_context: Mapping, reason: str) -> None:
                trial = copy.deepcopy(self.ledger)
                settle_write(
                    trial,
                    identity=identity,
                    state=UNKNOWN,
                    after_fingerprint=after_context["domain_fingerprint"],
                    post_candidate_revision=after_context["candidate_revision"],
                    post_stage_revision=after_context["stage_revision"],
                    post_tool_manifest_hash=after_context["tool_manifest_hash"],
                    result_code="outcome_unknown",
                    terminal_reason=reason,
                )
                try:
                    self.persist(snapshot_write_ledger(trial))
                finally:
                    # Even when persistence remains unavailable, this live App
                    # instance must never execute or replay the uncertain call.
                    _replace_ledger(self.ledger, trial)

            def mark_unknown(after_context: Mapping, reason: str) -> None:
                try:
                    settle_unknown(after_context, reason)
                except Exception as exc:
                    raise PlanError(
                        "候选写结果未知且未知终态暂时无法持久化；不能重放",
                        "write_unknown_unpersisted",
                    ) from exc

            try:
                response = self.adapter.execute(PATCH_TOOL, arguments)
                after_context = self.adapter.current_context()
                after = after_context["domain_fingerprint"]
                if (after_context["document_id"] != self.binding["document_id"]
                        or after_context["writer_fence"] != self.binding["writer_fence"]
                        or after_context["database_fingerprint"]
                        != self.binding["database_fingerprint"]):
                    mark_unknown(after_context, "context_changed_during_write")
                    _fail(
                        "候选写执行期间上下文或writer fence变化，必须人工核对",
                        "write_outcome_unknown",
                    )
                if not isinstance(response, dict):
                    _fail("候选写工具返回值不合法")
                code = response.get("code")
                if type(code) is int and code == 0:
                    if (after_context["candidate_revision"] != candidate_revision + 1
                            or after_context["stage_revision"] < stage_revision):
                        mark_unknown(after_context, "success_revision_mismatch")
                        _fail(
                            "候选写成功回执没有按patch合同推进修订，必须人工核对",
                            "write_outcome_unknown",
                        )
                    state = SETTLED_SUCCESS
                    reason = None
                    result_code: str | int | None = 0
                elif (type(code) is int and code != 0
                      and after == before
                      and after_context["candidate_revision"] == candidate_revision
                      and after_context["stage_revision"] == stage_revision
                      and after_context["tool_manifest_hash"]
                      == self.binding["tool_manifest_hash"]):
                    state = SETTLED_FAILURE
                    raw_reason = response.get("reason")
                    result_code = (
                        raw_reason if isinstance(raw_reason, str)
                        and _IDENTITY.fullmatch(raw_reason) else code
                    )
                    reason = str(result_code)
                else:
                    # A reported failure after a mutation is precisely the
                    # uncertain commit window.  Never describe or replay it as
                    # a clean failure.
                    mark_unknown(after_context, "failure_changed_state")
                    _fail(
                        "候选写报告失败但状态已变化，必须人工核对",
                        "write_outcome_unknown",
                    )

                self._persist_mutation(lambda trial: settle_write(
                    trial,
                    identity=identity,
                    state=state,
                    after_fingerprint=after,
                    post_candidate_revision=after_context["candidate_revision"],
                    post_stage_revision=after_context["stage_revision"],
                    post_tool_manifest_hash=after_context["tool_manifest_hash"],
                    result=response,
                    result_code=result_code,
                    terminal_reason=reason,
                ))
                return copy.deepcopy(response)
            except PlanError:
                # Expected App validation failures normally arrive as a code=1
                # response.  A raised error can happen before or after a custom
                # adapter mutates state, so it is ambiguous by construction.
                current = lookup_write(self.ledger, identity=identity)
                if current["state"] == INFLIGHT:
                    mark_unknown(
                        self.adapter.current_context(), "execution_interrupted",
                    )
                raise
            except Exception as exc:
                current = lookup_write(self.ledger, identity=identity)
                if current["state"] == INFLIGHT:
                    mark_unknown(
                        self.adapter.current_context(), "execution_interrupted",
                    )
                raise PlanError(
                    "候选写执行中断，结果未知；必须核对当前候选",
                    "write_outcome_unknown",
                ) from exc
