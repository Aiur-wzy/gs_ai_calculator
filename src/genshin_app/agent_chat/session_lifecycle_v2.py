"""App-owned V2 tool-call identity and terminal settlement ledger.

The DSH runtime is intentionally ephemeral.  This ledger keeps only the
minimum App-owned information needed to fence late frames and make a tool
side effect exactly-once across bridge retries.  Raw arguments, tool results,
model reasoning and credentials are never retained here; callers may pass raw
arguments/results only to the hashing helpers and settlement API, where they
are reduced to SHA-256 digests before the call returns.

This module is deliberately independent from :mod:`session_lifecycle`, which
remains the V1 session implementation.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from genshin_app.equipment.schema import PlanError

CONTRACT = "genshin-native-tool-settlement-ledger-v2"
TERMINAL_STATES = frozenset({
    "settled_success",
    "settled_failure",
    "unknown_after_interruption",
})
MAX_INFLIGHT_CALLS = 256
MAX_SETTLEMENTS = 131_072
MAX_TOOL_RESULT_BYTES = 64 * 1024

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
# Keep this grammar identical to bridge_protocol._IDENTITY.  Ledger identities
# may cross the Python/Node boundary and therefore cannot accept arbitrary
# Unicode, whitespace, path separators or control characters.
_BRIDGE_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SENSITIVE_ID_PREFIXES = (
    "api-key:",
    "api_key:",
    "authorization:",
    "basic:",
    "bearer:",
    "secret:",
    "sk-",
    "token:",
)
_JSON_SAFE_INTEGER = 2**53 - 1
_IDENTITY_KEYS = (
    "conversation_id",
    "turn_id",
    "step_id",
    "epoch_id",
    "call_id",
    "tool",
    "args_hash",
    "runtime_generation",
    "db_revision",
    "manifest_hash",
    "fence_token",
)
_CONTEXT_KEYS = (
    "conversation_id",
    "turn_id",
    "step_id",
    "epoch_id",
    "runtime_generation",
    "db_revision",
    "manifest_hash",
    "fence_token",
)
_ROOT_KEYS = {
    "contract",
    "conversation_id",
    "created_at",
    "updated_at",
    "active_context",
    "inflight",
    "settlements",
}
_INFLIGHT_KEYS = {"identity", "started_at"}
_SETTLEMENT_KEYS = {
    "identity",
    "terminal_state",
    "settled_at",
    "result_code",
    "result_hash",
    "resource_ids",
    "terminal_reason",
}
_UNSET = object()


def _stamp() -> str:
    return datetime.now(UTC).isoformat()


def _invalid(message: str = "V2 工具结算账不合法") -> None:
    raise PlanError(message, "agent_checkpoint_invalid")


def _canonical_digest(value: Any) -> str:
    def validate(item: Any, depth: int = 0) -> None:
        if depth > 64:
            _invalid("待哈希内容嵌套超过上限")
        if isinstance(item, dict):
            for key, nested in item.items():
                if not isinstance(key, str):
                    _invalid("待哈希JSON对象字段名必须是字符串")
                validate(nested, depth + 1)
        elif isinstance(item, list):
            for nested in item:
                validate(nested, depth + 1)
        elif item is None or isinstance(item, (str, bool, float)):
            return
        elif isinstance(item, int):
            if abs(item) > _JSON_SAFE_INTEGER:
                _invalid("待哈希整数超过JavaScript安全范围")
        else:
            _invalid("待哈希内容不是规范JSON")

    validate(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PlanError("待哈希内容不是规范 JSON", "agent_checkpoint_invalid") from exc
    if len(encoded) > MAX_TOOL_RESULT_BYTES:
        _invalid("工具结果超过哈希输入大小上限")
    return hashlib.sha256(encoded).hexdigest()


def canonical_args_hash(arguments_json: str) -> str:
    """Hash the exact completed DSH argument JSON bytes.

    The historical name is retained in the persisted identity contract.  The
    value itself is deliberately the exact UTF-8 text hash: reserializing a
    parsed object in Python and JavaScript would disagree for some numbers.
    Validation and schema admission happen separately after the block closes.
    """
    if not isinstance(arguments_json, str) or not arguments_json:
        _invalid("工具参数必须是非空原始JSON字符串")
    try:
        encoded = arguments_json.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        _invalid("工具参数不是有效UTF-8")
    if len(encoded) > 256 * 1024:
        _invalid("工具参数超过大小上限")
    return hashlib.sha256(encoded).hexdigest()


def canonical_result_hash(result: Any) -> str:
    """Hash a raw tool result without retaining it in the ledger."""
    return _canonical_digest(result)


def _nonempty_text(value: Any, name: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        _invalid(f"{name} 不合法")
    return value


def _identity_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or _BRIDGE_IDENTITY_RE.fullmatch(value) is None:
        _invalid(f"{name} 不符合桥协议 ASCII 身份语法")
    if value.lower().startswith(_SENSITIVE_ID_PREFIXES):
        _invalid(f"{name} 不能承载凭据或秘密")
    return value


def _digest_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        _invalid(f"{name} 必须是 SHA-256")
    return value


def _safe_code(value: Any, name: str, *, allow_none: bool = True) -> str | int | None:
    if value is None and allow_none:
        return None
    if type(value) is int:
        if -_JSON_SAFE_INTEGER <= value <= _JSON_SAFE_INTEGER:
            return value
        _invalid(f"{name} 超出 JSON 安全整数范围")
    if isinstance(value, str):
        return _identity_text(value, name)
    _invalid(f"{name} 只能保存短机器码")


def _resource_id_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)) or len(value) > 64:
        _invalid("resource_ids 不合法")
    resources = list(value)
    for item in resources:
        _identity_text(item, "resource_id")
    return resources


def _validate_identity(identity: Any) -> dict:
    if not isinstance(identity, dict) or set(identity) != set(_IDENTITY_KEYS):
        _invalid("工具调用身份字段不合法")
    row = copy.deepcopy(identity)
    for name in (
        "conversation_id",
        "turn_id",
        "step_id",
        "epoch_id",
        "call_id",
        "runtime_generation",
        "db_revision",
        "fence_token",
    ):
        _identity_text(row.get(name), name)
    _identity_text(row.get("tool"), "tool")
    _digest_text(row.get("args_hash"), "args_hash")
    _digest_text(row.get("manifest_hash"), "manifest_hash")
    return row


def _context_from_identity(identity: dict) -> dict:
    return {key: identity[key] for key in _CONTEXT_KEYS}


def make_call_identity(
    *,
    conversation_id: str,
    turn_id: str,
    step_id: str,
    epoch_id: str,
    call_id: str,
    tool: str,
    args_hash: str,
    runtime_generation: str,
    db_revision: str,
    manifest_hash: str,
    fence_token: str,
) -> dict:
    """Build and validate the immutable identity of one host tool call."""
    return _validate_identity({
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "step_id": step_id,
        "epoch_id": epoch_id,
        "call_id": call_id,
        "tool": tool,
        "args_hash": args_hash,
        "runtime_generation": runtime_generation,
        "db_revision": db_revision,
        "manifest_hash": manifest_hash,
        "fence_token": fence_token,
    })


def create_ledger(conversation_id: str) -> dict:
    """Create a JSON-serializable ledger owned by one App conversation."""
    conversation_id = _identity_text(conversation_id, "conversation_id")
    now = _stamp()
    return {
        "contract": CONTRACT,
        "conversation_id": conversation_id,
        "created_at": now,
        "updated_at": now,
        "active_context": None,
        "inflight": {},
        "settlements": {},
    }


def _validate_context(context: Any) -> dict:
    if not isinstance(context, dict) or set(context) != set(_CONTEXT_KEYS):
        _invalid("活动上下文字段不合法")
    row = copy.deepcopy(context)
    for name in (
        "conversation_id",
        "turn_id",
        "step_id",
        "epoch_id",
        "runtime_generation",
        "db_revision",
        "fence_token",
    ):
        _identity_text(row.get(name), name)
    _digest_text(row.get("manifest_hash"), "manifest_hash")
    return row


def _validate_settlement(row: Any, call_id: str) -> dict:
    if not isinstance(row, dict) or set(row) != _SETTLEMENT_KEYS:
        _invalid("工具终态结算字段不合法")
    identity = _validate_identity(row.get("identity"))
    if identity["call_id"] != call_id:
        _invalid("工具终态结算索引不一致")
    if row.get("terminal_state") not in TERMINAL_STATES:
        _invalid("工具终态不合法")
    _nonempty_text(row.get("settled_at"), "settled_at")
    _safe_code(row.get("result_code"), "result_code")
    _digest_text(row.get("result_hash"), "result_hash")
    resources = row.get("resource_ids")
    if not isinstance(resources, list):
        _invalid("resource_ids 不合法")
    _resource_id_list(resources)
    _safe_code(row.get("terminal_reason"), "terminal_reason")
    return row


def normalize(ledger: dict) -> None:
    """Strictly validate a persisted V2 ledger without adding defaults."""
    if not isinstance(ledger, dict) or set(ledger) != _ROOT_KEYS:
        _invalid()
    if ledger.get("contract") != CONTRACT:
        _invalid("V2 工具结算账版本不受支持")
    conversation_id = _identity_text(ledger.get("conversation_id"), "conversation_id")
    _nonempty_text(ledger.get("created_at"), "created_at")
    _nonempty_text(ledger.get("updated_at"), "updated_at")
    active = ledger.get("active_context")
    if active is not None:
        active = _validate_context(active)
        if active["conversation_id"] != conversation_id:
            _invalid("活动上下文与会话不一致")
    inflight = ledger.get("inflight")
    settlements = ledger.get("settlements")
    if not isinstance(inflight, dict) or len(inflight) > MAX_INFLIGHT_CALLS:
        _invalid("V2 未结算工具记录不合法")
    if not isinstance(settlements, dict) or len(settlements) > MAX_SETTLEMENTS:
        _invalid("V2 工具终态记录不合法")
    if len(inflight) + len(settlements) > MAX_SETTLEMENTS:
        _invalid("V2 工具调用总数超过结算账上限")
    if set(inflight).intersection(settlements):
        _invalid("工具调用不能同时处于执行中和终态")
    for call_id, row in inflight.items():
        _identity_text(call_id, "call_id")
        if not isinstance(row, dict) or set(row) != _INFLIGHT_KEYS:
            _invalid("V2 未结算工具字段不合法")
        identity = _validate_identity(row.get("identity"))
        if identity["call_id"] != call_id:
            _invalid("V2 未结算工具索引不一致")
        if identity["conversation_id"] != conversation_id:
            _invalid("工具调用与会话不一致")
        _nonempty_text(row.get("started_at"), "started_at")
    for call_id, row in settlements.items():
        _identity_text(call_id, "call_id")
        settlement = _validate_settlement(row, call_id)
        if settlement["identity"]["conversation_id"] != conversation_id:
            _invalid("工具终态与会话不一致")


def _touch(ledger: dict) -> None:
    ledger["updated_at"] = _stamp()


def _raise_call_id_conflict(call_id: str) -> None:
    raise PlanError(
        "相同 call_id 对应了不同的工具调用身份",
        "call_id_conflict",
        {"call_id": call_id},
    )


def _raise_stale_context() -> None:
    raise PlanError("工具调用的 epoch 或 fence 已失效", "stale_context")


def _assert_conversation(ledger: dict, identity: dict) -> None:
    if identity["conversation_id"] != ledger["conversation_id"]:
        _raise_stale_context()


def assert_current_context(ledger: dict, identity_or_context: dict) -> None:
    """Reject a late frame without mutating the ledger."""
    normalize(ledger)
    if (isinstance(identity_or_context, dict)
            and set(identity_or_context) == set(_IDENTITY_KEYS)):
        context = _context_from_identity(_validate_identity(identity_or_context))
    else:
        context = _validate_context(identity_or_context)
    if context["conversation_id"] != ledger["conversation_id"]:
        _raise_stale_context()
    if ledger["active_context"] != context:
        _raise_stale_context()


def _settlement_payload(
    identity: dict,
    *,
    terminal_state: str,
    result: Any = _UNSET,
    result_hash: str | None = None,
    result_code: str | int | None = None,
    resource_ids: tuple[str, ...] | list[str] = (),
    terminal_reason: str | int | None = None,
) -> dict:
    if terminal_state not in TERMINAL_STATES:
        _invalid("工具终态不合法")
    if result is not _UNSET and result_hash is not None:
        _invalid("result 与 result_hash 只能提供一个")
    if result_hash is None:
        result_hash = _canonical_digest(None if result is _UNSET else result)
    else:
        _digest_text(result_hash, "result_hash")
    result_code = _safe_code(result_code, "result_code")
    terminal_reason = _safe_code(terminal_reason, "terminal_reason")
    resources = _resource_id_list(resource_ids)
    return {
        "identity": copy.deepcopy(identity),
        "terminal_state": terminal_state,
        "result_code": result_code,
        "result_hash": result_hash,
        "resource_ids": resources,
        "terminal_reason": terminal_reason,
    }


def _same_settlement(existing: dict, proposed: dict) -> bool:
    return all(existing.get(key) == value for key, value in proposed.items())


def _terminalize(ledger: dict, identity: dict, payload: dict) -> dict:
    row = {**payload, "settled_at": _stamp()}
    ledger["settlements"][identity["call_id"]] = row
    ledger["inflight"].pop(identity["call_id"], None)
    _touch(ledger)
    return copy.deepcopy(row)


def activate_context(
    ledger: dict,
    *,
    turn_id: str,
    step_id: str,
    epoch_id: str,
    runtime_generation: str,
    db_revision: str,
    manifest_hash: str,
    fence_token: str,
) -> dict:
    """Activate an epoch/fence and close older inflight calls as unknown.

    Changing any context field invalidates all prior inflight calls.  They are
    settled as ``unknown_after_interruption`` so a late success/failure frame
    can never apply a side effect or erase a newer call.
    """
    normalize(ledger)
    context = _validate_context({
        "conversation_id": ledger["conversation_id"],
        "turn_id": turn_id,
        "step_id": step_id,
        "epoch_id": epoch_id,
        "runtime_generation": runtime_generation,
        "db_revision": db_revision,
        "manifest_hash": manifest_hash,
        "fence_token": fence_token,
    })
    if ledger["active_context"] == context:
        return {"context": copy.deepcopy(context), "invalidated": []}

    invalidated = []
    for call_id in list(ledger["inflight"]):
        row = ledger["inflight"][call_id]
        payload = _settlement_payload(
            row["identity"],
            terminal_state="unknown_after_interruption",
            result_code="context_invalidated",
            terminal_reason="epoch_or_fence_advanced",
        )
        invalidated.append(_terminalize(ledger, row["identity"], payload))
    ledger["active_context"] = context
    _touch(ledger)
    return {"context": copy.deepcopy(context), "invalidated": invalidated}


def begin_call(ledger: dict, *, identity: dict) -> dict:
    """Admit a call, replay its terminal row, or report it already inflight."""
    normalize(ledger)
    identity = _validate_identity(identity)
    _assert_conversation(ledger, identity)
    call_id = identity["call_id"]

    settled = ledger["settlements"].get(call_id)
    if settled is not None:
        if settled["identity"] != identity:
            _raise_call_id_conflict(call_id)
        return {"disposition": "replay", "settlement": copy.deepcopy(settled)}
    pending = ledger["inflight"].get(call_id)
    if pending is not None:
        if pending["identity"] != identity:
            _raise_call_id_conflict(call_id)
        return {"disposition": "inflight", "call": copy.deepcopy(pending)}

    assert_current_context(ledger, identity)
    if len(ledger["inflight"]) >= MAX_INFLIGHT_CALLS:
        raise PlanError("未结算工具调用已达到上限", "agent_size_limit")
    if len(ledger["settlements"]) + len(ledger["inflight"]) >= MAX_SETTLEMENTS:
        # Never evict old terminal rows: doing so would break exactly-once.
        raise PlanError("工具结算账已达到上限，请新建会话", "agent_size_limit")
    row = {"identity": copy.deepcopy(identity), "started_at": _stamp()}
    ledger["inflight"][call_id] = row
    _touch(ledger)
    return {"disposition": "execute", "call": copy.deepcopy(row)}


def settle_call(
    ledger: dict,
    *,
    identity: dict,
    terminal_state: str,
    result: Any = _UNSET,
    result_hash: str | None = None,
    result_code: str | int | None = None,
    resource_ids: tuple[str, ...] | list[str] = (),
    terminal_reason: str | int | None = None,
) -> dict:
    """Write one immutable terminal settlement for a previously admitted call."""
    normalize(ledger)
    identity = _validate_identity(identity)
    _assert_conversation(ledger, identity)
    proposed = _settlement_payload(
        identity,
        terminal_state=terminal_state,
        result=result,
        result_hash=result_hash,
        result_code=result_code,
        resource_ids=resource_ids,
        terminal_reason=terminal_reason,
    )
    call_id = identity["call_id"]

    settled = ledger["settlements"].get(call_id)
    if settled is not None:
        if settled["identity"] != identity:
            _raise_call_id_conflict(call_id)
        if not _same_settlement(settled, proposed):
            raise PlanError(
                "工具调用已经以不同终态结算",
                "settlement_conflict",
                {"call_id": call_id},
            )
        return {"disposition": "replay", "settlement": copy.deepcopy(settled)}

    pending = ledger["inflight"].get(call_id)
    if pending is None:
        # Distinguish an unregistered call in the active epoch from a frame
        # that arrived after its epoch/fence was retired.
        assert_current_context(ledger, identity)
        # In particular, do not clear or rewrite any other inflight call.
        raise PlanError(
            "工具结算没有对应的已接纳调用",
            "unknown_settlement",
            {"call_id": call_id},
        )
    if pending["identity"] != identity:
        _raise_call_id_conflict(call_id)
    assert_current_context(ledger, identity)
    row = _terminalize(ledger, identity, proposed)
    return {"disposition": "settled", "settlement": row}


def mark_unknown_after_interruption(
    ledger: dict,
    *,
    identity: dict,
    terminal_reason: str = "transport_interrupted",
) -> dict:
    """Terminalize an admitted call without retrying its possible side effect."""
    return settle_call(
        ledger,
        identity=identity,
        terminal_state="unknown_after_interruption",
        result_code="outcome_unknown",
        terminal_reason=terminal_reason,
    )


def lookup_call(ledger: dict, *, identity: dict) -> dict:
    """Read a call state without changing timestamps or ledger contents."""
    normalize(ledger)
    identity = _validate_identity(identity)
    _assert_conversation(ledger, identity)
    call_id = identity["call_id"]
    settled = ledger["settlements"].get(call_id)
    if settled is not None:
        if settled["identity"] != identity:
            _raise_call_id_conflict(call_id)
        return {"state": "settled", "settlement": copy.deepcopy(settled)}
    pending = ledger["inflight"].get(call_id)
    if pending is not None:
        if pending["identity"] != identity:
            _raise_call_id_conflict(call_id)
        return {"state": "inflight", "call": copy.deepcopy(pending)}
    return {"state": "absent"}


def snapshot(ledger: dict) -> dict:
    """Return a validated, JSON-safe copy suitable for App persistence."""
    normalize(ledger)
    return copy.deepcopy(ledger)
