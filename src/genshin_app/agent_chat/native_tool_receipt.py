"""Bounded native-tool results and App-validated session resource receipts.

The native runtime must not invent a resource handle merely because a tool
result contains a string that looks like one.  This module only advertises a
resource after it has been found in the App-owned session registry and its
saved metadata still matches the registered value.  Large results may then be
returned by reference; otherwise they fail explicitly instead of being
silently truncated.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from genshin_app.equipment.files import fingerprint, json_bytes
from genshin_app.equipment.schema import PlanError

from . import tools
from .bridge_protocol import JSON_SAFE_INTEGER_MAX
from .native_text_safety import sanitize_public_text
from .session_lifecycle_v2 import MAX_TOOL_RESULT_BYTES

RECEIPT_CONTRACT = "genshin-native-read-receipt-v1"
DIRECT_RESULT_MAX_BYTES = 48 * 1024
MAX_RESOURCE_REFS = 16

_RESOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_DESCRIPTOR_KEYS = {
    "id",
    "stage",
    "query_kind",
    "action",
    "entities",
    "selectors",
    "fingerprint",
    "bytes",
}
_RESOURCE_SCOPE_KEYS = {
    "session_scope_token",
    "candidate_revision",
    "stage_revision",
    "tool_manifest_hash",
}
_RESOURCE_REFERENCE_KEYS = {
    "id",
    "kind",
    "reader",
    "pointer",
    "fingerprint",
    "bytes",
    "stage",
    "query_kind",
    "descriptor_identity",
    *_RESOURCE_SCOPE_KEYS,
}
_STRUCTURED_POINTER_FIELDS = frozenset({
    "json_pointer",
    "parameter_pointer",
    "parent_pointer",
})


def _fail(
    message: str, reason: str = "native_tool_receipt_invalid", details=None
) -> None:
    raise PlanError(message, reason, details)


def _portable_json(value: Any, *, depth: int = 0) -> None:
    if depth > 64:
        _fail("原生工具回执JSON嵌套超过上限")
    if value is None or isinstance(value, (str, bool)):
        return
    if type(value) is int:
        if abs(value) > JSON_SAFE_INTEGER_MAX:
            _fail("原生工具回执整数超过JavaScript安全范围")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("原生工具回执包含非有限数值")
        return
    if isinstance(value, list):
        for item in value:
            _portable_json(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("原生工具回执对象字段名必须是字符串")
            _portable_json(item, depth=depth + 1)
        return
    _fail("原生工具回执不属于可移植JSON")


def canonical_receipt_bytes(
    value: Any, *, maximum: int = MAX_TOOL_RESULT_BYTES
) -> bytes:
    """Encode the lossless Python/ECMAScript JSON subset with a hard bound."""
    _portable_json(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError, OverflowError) as exc:
        raise PlanError(
            "原生工具回执不是规范JSON", "native_tool_receipt_invalid"
        ) from exc
    if not encoded or len(encoded) > maximum:
        _fail("原生工具回执超过大小上限", "native_tool_receipt_size")
    return encoded


def _fits_settlement_envelope(
    result: dict,
    resource_ids: tuple[str, ...],
    tool: str,
    *,
    direct_budget: int,
) -> bool:
    """Reserve room for the result after it becomes an escaped JSON string.

    A 48KiB result made mostly of quotes or backslashes can grow substantially
    when embedded in ``tool.settle``.  Test the complete worst-case metadata
    shape here instead of assuming the raw result length is sufficient.
    """
    try:
        result_json = canonical_receipt_bytes(
            result, maximum=min(DIRECT_RESULT_MAX_BYTES, direct_budget)
        ).decode("utf-8")
        canonical_receipt_bytes(
            {
                "contract": "genshin-native-tool-settlement-v1",
                "tool": tool,
                "status": "settled_failure",
                "result_json": result_json,
                "result_hash": "f" * 64,
                "result_code": "x" * 128,
                "resource_ids": list(resource_ids),
                "runtime_generation": "x" * 128,
                "fence_token": "x" * 128,
            }
        )
    except PlanError:
        return False
    return True


def _sanitize_message(value: str) -> str:
    return sanitize_public_text(value)


def _valid_json_pointer(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= 4096
        and (value == "" or value.startswith("/"))
        and re.search(r"~(?![01])", value) is None
    )


def _page_pointer_paths(
    tool: str | None,
    arguments: dict | None,
    response: Any,
) -> frozenset[tuple[Any, ...]]:
    """Identify only the JSON Pointers constructed by ``tools.page``."""
    if (
        tool != "gs_read_context"
        or not isinstance(arguments, dict)
        or not isinstance(response, dict)
        or response.get("code") != 0
    ):
        return frozenset()
    data = response.get("data")
    resource = arguments.get("resource")
    requested_pointer = arguments.get("pointer", "")
    if (
        not isinstance(data, dict)
        or not isinstance(resource, str)
        or data.get("resource") != resource
        or data.get("pointer") != requested_pointer
        or not _valid_json_pointer(requested_pointer)
    ):
        return frozenset()

    paths: set[tuple[Any, ...]] = {("data", "pointer")}
    items = data.get("items")
    if not isinstance(items, list):
        return frozenset(paths)
    for index, row in enumerate(items):
        if not isinstance(row, dict) or set(row) != {"key", "value"}:
            continue
        wrapped = row.get("value")
        if (
            not isinstance(wrapped, dict)
            or set(wrapped) != {"reference", "kind", "length"}
            or wrapped.get("kind") not in {"object", "array", "text"}
        ):
            continue
        reference = wrapped.get("reference")
        if not isinstance(reference, dict) or set(reference) != {"resource", "pointer"}:
            continue
        raw_key = str(row["key"])
        # If the key itself looks like a local path, do not preserve a
        # continuation that would re-encode and disclose the same path.
        if _sanitize_message(raw_key) != raw_key:
            continue
        key = raw_key.replace("~", "~0").replace("/", "~1")
        expected_pointer = f"{requested_pointer}/{key}"
        if (
            reference.get("resource") == resource
            and reference.get("pointer") == expected_pointer
            and _valid_json_pointer(expected_pointer)
        ):
            paths.add(("data", "items", index, "value", "reference", "pointer"))
    return frozenset(paths)


def _sanitize_response_strings(
    value: Any,
    *,
    depth: int = 0,
    path: tuple[Any, ...] = (),
    preserved_pointer_paths: frozenset[tuple[Any, ...]] = frozenset(),
) -> Any:
    if depth > 64:
        return value
    if isinstance(value, str):
        if (
            path in preserved_pointer_paths
            or path
            and (
                path[-1] in _STRUCTURED_POINTER_FIELDS
                or len(path) >= 2 and path[-2] == "field_pointers"
            )
            and _valid_json_pointer(value)
        ):
            return value
        return _sanitize_message(value)
    if isinstance(value, (list, tuple)):
        return [
            _sanitize_response_strings(
                item,
                depth=depth + 1,
                path=(*path, index),
                preserved_pointer_paths=preserved_pointer_paths,
            )
            for index, item in enumerate(value)
        ]
    if isinstance(value, Mapping):
        output = {}
        for key, item in value.items():
            # Rewriting a JSON key can merge two fields and change the
            # response's meaning. Reject path-bearing keys; the caller turns
            # this into one bounded, explicit tool failure.
            if isinstance(key, str) and _sanitize_message(key) != key:
                _fail(
                    "App工具结果的字段名包含本地路径",
                    "native_tool_result_invalid",
                )
            output[key] = _sanitize_response_strings(
                item,
                depth=depth + 1,
                path=(*path, key),
                preserved_pointer_paths=preserved_pointer_paths,
            )
        return output
    return value


def sanitize_native_response(
    value: Any,
    *,
    tool: str | None = None,
    arguments: dict | None = None,
) -> Any:
    """Return one detached, path-safe value in the portable JSON subset.

    Domain projections commonly use tuples for immutable ordered schema
    fields.  JSON has no tuple type, so the native boundary normalizes those
    values to arrays before the call ledger and Node settlement validate the
    response.  Unordered or otherwise non-portable Python containers remain
    rejected instead of being assigned an arbitrary wire meaning.
    """
    copied = copy.deepcopy(value)
    if isinstance(copied, Mapping) and "failure_kind" in copied:
        from genshin_app.app_system_fault import validate_system_failure
        from genshin_app.execution_cancellation import validate_cancellation_response
        try:
            if copied["failure_kind"] == "cancelled":
                validate_cancellation_response(copied)
            else:
                # The system validator also refuses unknown kinds. Do not
                # silently exempt arbitrary non-system failure envelopes.
                validate_system_failure(copied)
        except (ValueError, TypeError, KeyError) as exc:
            raise PlanError("应用系统故障回执格式无效", "native_tool_receipt_invalid") from exc
    sanitized = _sanitize_response_strings(
        copied,
        preserved_pointer_paths=_page_pointer_paths(tool, arguments, copied),
    )
    _portable_json(sanitized)
    return sanitized


def _validate_resource_scope(value: Any) -> dict:
    if not isinstance(value, dict) or set(value) != _RESOURCE_SCOPE_KEYS:
        _fail("原生资料作用域不合法", "native_resource_scope_invalid")
    scope = copy.deepcopy(value)
    if (
        not isinstance(scope["session_scope_token"], str)
        or _HASH.fullmatch(scope["session_scope_token"]) is None
        or type(scope["candidate_revision"]) is not int
        or not 0 <= scope["candidate_revision"] <= JSON_SAFE_INTEGER_MAX
        or type(scope["stage_revision"]) is not int
        or not 0 <= scope["stage_revision"] <= JSON_SAFE_INTEGER_MAX
        or not isinstance(scope["tool_manifest_hash"], str)
        or _HASH.fullmatch(scope["tool_manifest_hash"]) is None
    ):
        _fail("原生资料作用域字段不合法", "native_resource_scope_invalid")
    return scope


def _valid_descriptor(resource_id: str, descriptor: Any) -> bool:
    return (
        isinstance(descriptor, dict)
        and set(descriptor) == _DESCRIPTOR_KEYS
        and descriptor.get("id") == resource_id
        and resource_id.startswith("query-")
        and resource_id[6:].isdigit()
        and descriptor.get("stage") in tools.PHASE_INDEX
        and descriptor.get("query_kind") in ("context", "runtime", "appendix", "appendix_index")
        and (
            descriptor.get("action") is None
            or isinstance(descriptor.get("action"), str)
            and len(descriptor["action"]) <= 128
        )
        and isinstance(descriptor.get("entities"), list)
        and len(descriptor["entities"]) <= 16
        and all(
            isinstance(item, str) and len(item) <= 128
            for item in descriptor["entities"]
        )
        and isinstance(descriptor.get("selectors"), list)
        and len(descriptor["selectors"]) <= 32
        and all(
            isinstance(item, str) and len(item) <= 128
            for item in descriptor["selectors"]
        )
        and isinstance(descriptor.get("fingerprint"), str)
        and _HASH.fullmatch(descriptor["fingerprint"]) is not None
        and type(descriptor.get("bytes")) is int
        and 0 <= descriptor["bytes"] <= tools.MAX_RESOURCE
    )


def _candidate_resource_ids(
    tool: str, arguments: dict, response: dict
) -> tuple[str, ...]:
    candidates: list[str] = []
    data = response.get("data")
    if isinstance(data, dict):
        if isinstance(data.get("resource"), str):
            candidates.append(data["resource"])
        reference = data.get("reference")
        if isinstance(reference, dict) and isinstance(reference.get("resource"), str):
            candidates.append(reference["resource"])
        source_ref = data.get("source_ref")
        if isinstance(source_ref, dict) and isinstance(
            source_ref.get("reference"), str
        ):
            candidates.append(source_ref["reference"])
    if tool == "gs_read_context" and isinstance(arguments.get("resource"), str):
        candidates.append(arguments["resource"])
    return tuple(dict.fromkeys(candidates))


def _registered_resource_ref(
    session, resource_id: str, resource_scope: dict
) -> dict | None:
    if _RESOURCE_ID.fullmatch(resource_id) is None:
        return None
    resources = getattr(session, "resources", None)
    if not isinstance(resources, dict) or resource_id not in resources:
        return None
    value = resources[resource_id]
    try:
        value_bytes = json_bytes(value)
        value_fingerprint = fingerprint(value)
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError):
        return None
    if len(value_bytes) > tools.MAX_RESOURCE:
        return None

    stage = None
    query_kind = None
    descriptor_identity = None
    if getattr(session, "workflow", None) == "staged":
        state = getattr(session, "stage_state", None)
        if not isinstance(state, dict):
            return None
        resource_stages = state.get("resource_stages")
        descriptors = state.get("resource_descriptors")
        if not isinstance(resource_stages, dict) or not isinstance(descriptors, dict):
            return None
        stage = resource_stages.get(resource_id)
        descriptor = descriptors.get(resource_id)
        current_stage = getattr(session, "workflow_stage", None)
        if (
            stage not in tools.PHASE_INDEX
            or current_stage not in tools.PHASE_INDEX
            or tools.PHASE_INDEX[stage] > tools.PHASE_INDEX[current_stage]
            or not _valid_descriptor(resource_id, descriptor)
            or descriptor["stage"] != stage
            or descriptor["fingerprint"] != value_fingerprint
            or descriptor["bytes"] != len(value_bytes)
        ):
            return None
        query_kind = descriptor["query_kind"]
        descriptor_identity = fingerprint(descriptor)
    else:
        descriptor_identity = fingerprint(
            {
                "id": resource_id,
                "workflow": getattr(session, "workflow", None),
                "fingerprint": value_fingerprint,
                "bytes": len(value_bytes),
            }
        )

    return {
        "id": resource_id,
        "kind": "session_resource",
        "reader": "gs_read_context",
        "pointer": "",
        "fingerprint": value_fingerprint,
        "bytes": len(value_bytes),
        "stage": stage,
        "query_kind": query_kind,
        "descriptor_identity": descriptor_identity,
        **resource_scope,
    }


def _claimed_resource_summary(claimed) -> dict:
    values = [item for item in claimed if isinstance(item, str)]
    encoded = json.dumps(
        values,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("ascii")
    return {
        "claimed_resource_count": len(values),
        "claimed_resource_hash": hashlib.sha256(encoded).hexdigest(),
    }


def _failure_result(tool: str, reason: str, message: str, *, claimed=()) -> dict:
    return {
        "code": 1,
        "reason": reason,
        "message": _sanitize_message(message),
        "details": {
            "tool": tool,
            **_claimed_resource_summary(claimed),
        },
        "native_receipt": {
            "contract": RECEIPT_CONTRACT,
            "mode": "rejected",
            "tool": tool,
            "resources": [],
        },
    }


@dataclass(frozen=True)
class NativeToolReceipt:
    """One bounded result plus the exact App-owned resources it references."""

    result: dict
    resource_ids: tuple[str, ...]
    issued_references: tuple[dict, ...] = ()


def rejected_native_tool_receipt(
    *, tool: str, reason: str, message: str
) -> NativeToolReceipt:
    result = _failure_result(tool, reason, message)
    canonical_receipt_bytes(result)
    return NativeToolReceipt(result, ())


def build_native_tool_receipt(
    session,
    *,
    tool: str,
    arguments: dict,
    response: Any,
    policy: str,
    resource_scope: dict,
    direct_budget: int = DIRECT_RESULT_MAX_BYTES,
) -> NativeToolReceipt:
    """Return a direct, referenced, or explicit rejected native result.

    ``session_resource_read`` is a stronger contract than a normal read: a
    successful call must identify a resource that the App has just registered.
    Pure reads may optionally refer to an already registered query resource.
    """
    if policy not in ("pure_read", "session_resource_read"):
        _fail("原生工具读取策略不受支持")
    resource_scope = _validate_resource_scope(resource_scope)
    if type(direct_budget) is not int or direct_budget < 0:
        _fail("原生工具直接回执预算不合法")
    if (
        not isinstance(arguments, dict)
        or not isinstance(response, dict)
        or type(response.get("code")) is not int
    ):
        result = _failure_result(
            tool,
            "native_tool_result_invalid",
            "App工具没有返回JSON对象。",
        )
        return NativeToolReceipt(result, ())

    response_code = response["code"]
    response_reason = response.get("reason")
    valid_reason = (
        isinstance(response_reason, str)
        and _RESOURCE_ID.fullmatch(response_reason) is not None
        and response_reason != "outcome_unknown"
    )
    invalid_terminal = (
        abs(response_code) > JSON_SAFE_INTEGER_MAX
        or response_reason == "outcome_unknown"
        or response_code == 0 and "reason" in response
        or response_code != 0
        and "reason" in response
        and not valid_reason
    )
    if invalid_terminal:
        result = _failure_result(
            tool,
            "native_tool_result_invalid",
            "App工具结果的终态字段互相矛盾。",
        )
        return NativeToolReceipt(result, ())

    claimed = _candidate_resource_ids(tool, arguments, response)
    registered: list[dict] = []
    invalid_claims: list[str] = []
    for resource_id in claimed:
        reference = _registered_resource_ref(session, resource_id, resource_scope)
        if reference is None:
            invalid_claims.append(resource_id)
        else:
            registered.append(reference)
    registered = sorted(
        {row["id"]: row for row in registered}.values(),
        key=lambda row: row["id"],
    )[:MAX_RESOURCE_REFS]

    success = response_code == 0
    if policy == "session_resource_read" and success:
        if invalid_claims:
            result = _failure_result(
                tool,
                "native_resource_receipt_invalid",
                "App查询工具返回了未登记或已失效的资料引用。",
                claimed=invalid_claims,
            )
            return NativeToolReceipt(result, ())
        if not registered:
            result = _failure_result(
                tool,
                "native_resource_receipt_missing",
                "App查询工具成功，但没有返回可验证的会话资料引用。",
                claimed=claimed,
            )
            return NativeToolReceipt(result, ())

    public_resources = [{"id": row["id"]} for row in registered]
    metadata = {
        "contract": RECEIPT_CONTRACT,
        "mode": "direct",
        "tool": tool,
        # The model only needs the opaque ID accepted by gs_read_context.
        # Scope tokens and descriptor bindings remain inside the App host.
        "resources": public_resources,
    }
    try:
        direct = sanitize_native_response(
            response,
            tool=tool,
            arguments=arguments,
        )
    except PlanError:
        result = _failure_result(
            tool,
            "native_tool_result_invalid",
            "App工具结果包含不安全的本地路径字段。",
            claimed=claimed,
        )
        return NativeToolReceipt(result, ())
    direct["native_receipt"] = metadata
    resource_ids = tuple(row["id"] for row in registered)
    if not _fits_settlement_envelope(
        direct,
        resource_ids,
        tool,
        direct_budget=direct_budget,
    ):
        try:
            canonical_receipt_bytes(
                direct,
                maximum=min(DIRECT_RESULT_MAX_BYTES, direct_budget),
            )
        except PlanError as exc:
            if exc.reason != "native_tool_receipt_size":
                result = _failure_result(
                    tool,
                    "native_tool_result_invalid",
                    "App工具结果不属于可移植JSON。",
                    claimed=claimed,
                )
                return NativeToolReceipt(result, ())
    else:
        return NativeToolReceipt(
            direct,
            resource_ids,
            tuple(copy.deepcopy(registered)),
        )

    if not registered:
        result = _failure_result(
            tool,
            "native_result_requires_resource",
            "App工具结果超过直接回执预算，且没有可验证的会话资料引用。",
            claimed=claimed,
        )
        canonical_receipt_bytes(result)
        return NativeToolReceipt(result, ())

    referenced_metadata = {**metadata, "mode": "referenced"}
    continuations = [
        {
            "tool": "gs_read_context",
            "arguments": {
                "resource": row["id"],
                "pointer": "",
                "offset": 0,
                "limit": 50,
            },
            "authorization": "host_issued_resource_reference",
        }
        for row in registered
    ]
    if success:
        referenced = {
            "code": 0,
            "data": {
                "message": "结果已保存在App会话资料中，请按引用分页读取。",
                "resources": public_resources,
                "continuations": continuations,
            },
            "native_receipt": referenced_metadata,
        }
    else:
        reason = response.get("reason")
        referenced = {
            "code": 1,
            "reason": (
                reason
                if isinstance(reason, str) and _RESOURCE_ID.fullmatch(reason)
                else "native_tool_failure"
            ),
            "message": _sanitize_message(
                str(response.get("message", "App只读工具执行失败。"))
            ),
            "details": {
                "resources": public_resources,
                "continuations": continuations,
            },
            "native_receipt": referenced_metadata,
        }
    canonical_receipt_bytes(referenced)
    return NativeToolReceipt(
        referenced,
        resource_ids,
        tuple(copy.deepcopy(registered)),
    )


def resolve_registered_resource_reference(
    session,
    reference: Any,
    *,
    resource_scope: dict,
) -> dict:
    """Revalidate one exact App-issued reference against current session state."""
    scope = _validate_resource_scope(resource_scope)
    if not isinstance(reference, dict) or set(reference) != _RESOURCE_REFERENCE_KEYS:
        _fail("原生资料引用字段不合法", "native_resource_reference_invalid")
    expected = copy.deepcopy(reference)
    if any(expected.get(key) != value for key, value in scope.items()):
        _fail("原生资料引用不属于当前会话作用域", "native_resource_scope_mismatch")
    resource_id = expected.get("id")
    if not isinstance(resource_id, str):
        _fail("原生资料引用缺少ID", "native_resource_reference_invalid")
    current = _registered_resource_ref(session, resource_id, scope)
    if current is None or current != expected:
        _fail("原生资料引用已替换、过期或不可见", "native_resource_reference_stale")
    return current


def issue_registered_resource_reference(
    session,
    resource_id: str,
    *,
    resource_scope: dict,
) -> dict:
    """Issue a reference only for one currently valid App registry entry."""
    scope = _validate_resource_scope(resource_scope)
    if not isinstance(resource_id, str):
        _fail("原生资料引用缺少ID", "native_resource_reference_invalid")
    reference = _registered_resource_ref(session, resource_id, scope)
    if reference is None:
        _fail(
            "App会话资料不存在、不可见或元数据失效", "native_resource_reference_stale"
        )
    return reference


def receipt_result_hash(receipt: NativeToolReceipt) -> str:
    return hashlib.sha256(canonical_receipt_bytes(receipt.result)).hexdigest()
