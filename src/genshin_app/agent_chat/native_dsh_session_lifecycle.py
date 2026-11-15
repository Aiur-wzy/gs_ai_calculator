"""Fail-closed Python contract for deleting one durable DSH session.

The runtime owns DSH JSONL, projection cache, and query storage.  Python owns
the App conversation and its DSH identity binding.  This module deliberately
does not touch DSH paths: it prepares a path-free lifecycle request and
validates the exact runtime receipt needed before App deletion may continue.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError

from .native_dsh_session_binding import (
    DELETE_PENDING,
    NativeDshSessionBindingStore,
)

DELETE_REQUEST_CONTRACT = "genshin-native-dsh-delete-request-v1"
DELETE_RECEIPT_CONTRACT = "genshin-native-dsh-delete-receipt-v1"

_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_REQUEST_KEYS = {
    "contract",
    "conversation_id",
    "dsh_session_id",
    "delete_intent_id",
    "binding_revision",
    "operation",
}
_RECEIPT_KEYS = {
    "contract",
    "conversation_id",
    "dsh_session_id",
    "delete_intent_id",
    "writer_quiesced",
    "runtime_tombstone_written",
    "session_log_deleted",
    "projection_cache_deleted",
    "query_index_deleted",
}


def _fail(message: str, reason: str = "native_dsh_delete_receipt_invalid") -> None:
    raise PlanError(message, reason)


def _identity(value: object, field: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        _fail(f"{field}不符合DSH删除身份语法")
    return value


def prepare_runtime_session_delete(
    store: NativeDshSessionBindingStore,
    *,
    intent_id: str,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Prepare the binding and return a path-free runtime delete request."""

    pending = store.prepare_delete(
        intent_id=intent_id,
        expected_revision=expected_revision,
    )
    precondition = store.runtime_deletion_precondition()
    if (
        pending["lifecycle_state"] != DELETE_PENDING
        or precondition["runtime_storage_action"]
        != "quiesce_tombstone_delete"
        or precondition["delete_intent_id"] != intent_id
        or precondition["can_delete_app_files"] is not False
    ):
        _fail("DSH删除前置状态不完整")
    return {
        "contract": DELETE_REQUEST_CONTRACT,
        "conversation_id": pending["conversation_id"],
        "dsh_session_id": pending["dsh_session_id"],
        "delete_intent_id": intent_id,
        "binding_revision": pending["revision"],
        "operation": "quiesce_tombstone_delete",
    }


def validate_runtime_delete_request(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize the path-free request accepted by the DSH owner runtime."""

    if not isinstance(request, Mapping) or set(request) != _REQUEST_KEYS:
        _fail("DSH删除请求字段不合法")
    checked = copy.deepcopy(dict(request))
    if checked.get("contract") != DELETE_REQUEST_CONTRACT:
        _fail("DSH删除请求版本不受支持")
    for field in ("conversation_id", "dsh_session_id", "delete_intent_id"):
        _identity(checked.get(field), field)
    if (
        type(checked.get("binding_revision")) is not int
        or checked["binding_revision"] < 0
        or checked.get("operation") != "quiesce_tombstone_delete"
    ):
        _fail("DSH删除请求状态不合法")
    return checked


def validate_runtime_delete_receipt(
    request: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate that every DSH-owned durable surface was deleted."""

    checked_request = validate_runtime_delete_request(request)
    if not isinstance(receipt, Mapping) or set(receipt) != _RECEIPT_KEYS:
        _fail("DSH删除回执字段不合法")
    checked = copy.deepcopy(dict(receipt))
    if checked.get("contract") != DELETE_RECEIPT_CONTRACT:
        _fail("DSH删除回执版本不受支持")
    for field in ("conversation_id", "dsh_session_id", "delete_intent_id"):
        if _identity(checked.get(field), field) != checked_request[field]:
            _fail("DSH删除回执不属于当前请求", "stale_context")
    for field in (
        "writer_quiesced",
        "runtime_tombstone_written",
        "session_log_deleted",
        "projection_cache_deleted",
        "query_index_deleted",
    ):
        if checked.get(field) is not True:
            _fail("DSH删除回执未证明全部存储已清理")
    return checked


def finalize_runtime_session_delete(
    store: NativeDshSessionBindingStore,
    *,
    request: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Tombstone the App binding only after a complete runtime receipt."""

    checked = validate_runtime_delete_receipt(request, receipt)
    precondition = store.runtime_deletion_precondition()
    if (
        precondition["lifecycle_state"] != DELETE_PENDING
        or precondition["delete_intent_id"] != request["delete_intent_id"]
        or precondition["dsh_session_id"] != request["dsh_session_id"]
        or precondition["binding_revision"] != request["binding_revision"]
    ):
        _fail("DSH删除回执对应的绑定状态已变化", "stale_context")
    binding = store.tombstone(intent_id=request["delete_intent_id"])
    return {
        "binding": binding,
        "runtime_delete_receipt_hash": fingerprint(checked),
        "app_files_may_delete": True,
    }


__all__ = [
    "DELETE_RECEIPT_CONTRACT",
    "DELETE_REQUEST_CONTRACT",
    "finalize_runtime_session_delete",
    "prepare_runtime_session_delete",
    "validate_runtime_delete_receipt",
    "validate_runtime_delete_request",
]
