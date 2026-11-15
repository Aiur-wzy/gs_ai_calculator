from __future__ import annotations

import copy

import pytest

from genshin_app.agent_chat.native_dsh_session_binding import (
    TOMBSTONED,
    NativeDshSessionBindingStore,
)
from genshin_app.agent_chat.native_dsh_session_lifecycle import (
    DELETE_RECEIPT_CONTRACT,
    finalize_runtime_session_delete,
    prepare_runtime_session_delete,
)
from genshin_app.agent_chat.native_session_store import create_native_session
from genshin_app.equipment.schema import PlanError


def _record() -> dict:
    return create_native_session(
        conversation_id="conversation-1",
        connection_kind="native_api",
        runtime_generation="dsh_v2",
        locked_provider="deepseek",
        locked_model="deepseek-chat",
        locked_reasoning_effort="high",
        domain_checkpoint={
            "document_id": "document-1",
            "candidate_revision": 0,
            "stage_revision": 0,
            "database_fingerprint": "a" * 64,
            "receipt_fingerprint": "b" * 64,
            "writer_fence": "wf_" + "a" * 43,
        },
        prompt_profile_hash="c" * 64,
        tool_manifest_hash="d" * 64,
    )


def _receipt(request: dict) -> dict:
    return {
        "contract": DELETE_RECEIPT_CONTRACT,
        "conversation_id": request["conversation_id"],
        "dsh_session_id": request["dsh_session_id"],
        "delete_intent_id": request["delete_intent_id"],
        "writer_quiesced": True,
        "runtime_tombstone_written": True,
        "session_log_deleted": True,
        "projection_cache_deleted": True,
        "query_index_deleted": True,
    }


def test_runtime_receipt_releases_app_deletion_only_after_all_surfaces(tmp_path):
    store = NativeDshSessionBindingStore(
        tmp_path, conversation_id="conversation-1"
    )
    created = store.create_authoritative(_record())
    request = prepare_runtime_session_delete(
        store,
        intent_id="delete-1",
        expected_revision=created["revision"],
    )

    before = store.runtime_deletion_precondition()
    assert before["can_delete_app_files"] is False
    assert before["runtime_storage_action"] == "quiesce_tombstone_delete"

    completed = finalize_runtime_session_delete(
        store,
        request=request,
        receipt=_receipt(request),
    )

    assert completed["app_files_may_delete"] is True
    assert completed["binding"]["lifecycle_state"] == TOMBSTONED
    assert len(completed["runtime_delete_receipt_hash"]) == 64


def test_partial_or_stale_runtime_receipt_never_releases_app_files(tmp_path):
    store = NativeDshSessionBindingStore(
        tmp_path, conversation_id="conversation-1"
    )
    created = store.create_authoritative(_record())
    request = prepare_runtime_session_delete(
        store,
        intent_id="delete-1",
        expected_revision=created["revision"],
    )
    partial = _receipt(request)
    partial["query_index_deleted"] = False

    with pytest.raises(PlanError, match="未证明全部存储"):
        finalize_runtime_session_delete(
            store,
            request=request,
            receipt=partial,
        )
    assert store.runtime_deletion_precondition()["can_delete_app_files"] is False

    stale = copy.deepcopy(_receipt(request))
    stale["dsh_session_id"] = "dsh-session-other"
    with pytest.raises(PlanError, match="不属于当前请求"):
        finalize_runtime_session_delete(
            store,
            request=request,
            receipt=stale,
        )
