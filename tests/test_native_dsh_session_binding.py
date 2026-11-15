from __future__ import annotations

import copy
import hashlib
import json

import pytest

from genshin_app.agent_chat.native_dsh_session_binding import (
    ACTIVE,
    ARCHIVED,
    AUTHORITATIVE_DURABLE,
    AUTHORITATIVE_NEW,
    DELETE_PENDING,
    DSH_SESSION_RESERVED,
    LEGACY_COMPATIBILITY,
    LEGACY_INCOMPATIBLE,
    TOMBSTONED,
    NativeDshSessionBindingStore,
)
from genshin_app.agent_chat.native_session_store import (
    create_native_session,
    refresh_checkpoint_binding,
)
from genshin_app.equipment.schema import PlanError

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
FENCE = "wf_" + "a" * 43


def _record(conversation_id: str = "conversation-1") -> dict:
    return create_native_session(
        conversation_id=conversation_id,
        connection_kind="native_api",
        runtime_generation="dsh_v2",
        locked_provider="deepseek",
        locked_model="deepseek-chat",
        locked_reasoning_effort="high",
        domain_checkpoint={
            "document_id": "document-1",
            "candidate_revision": 0,
            "stage_revision": 0,
            "database_fingerprint": HASH_A,
            "receipt_fingerprint": HASH_B,
            "writer_fence": FENCE,
        },
        prompt_profile_hash=HASH_C,
        tool_manifest_hash=HASH_D,
    )


def _dsh_checkpoint(session_id: str, *, event_count: int = 1) -> dict:
    row = {
        "contract": "genshin-native-dsh-checkpoint-v1",
        "session_id": session_id,
        "storage_format": "dsh-jsonl-zstd-v1",
        "composition_hash": "e" * 64,
        "revision": f"revision-{event_count}",
        "last_event_seq": None if event_count == 0 else event_count - 1,
        "event_count": event_count,
        "size_bytes": event_count * 128,
    }
    return {
        **row,
        "flush_digest": hashlib.sha256(
            json.dumps(row, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def test_new_binding_is_authoritative_stable_and_path_free(tmp_path):
    store = NativeDshSessionBindingStore(
        tmp_path, conversation_id="conversation-1"
    )
    first = store.create_authoritative(_record())
    second = store.create_authoritative(_record())

    assert second == first
    assert first["binding_origin"] == AUTHORITATIVE_NEW
    assert first["history_state"] == DSH_SESSION_RESERVED
    assert first["lifecycle_state"] == ACTIVE
    assert first["dsh_session_id"].startswith("dsh-session-")
    assert "writer_fence" not in first["domain_checkpoint"]
    assert store.cold_read() == first


def test_v1_binding_is_reported_as_incompatible_instead_of_reinterpreted(tmp_path):
    store = NativeDshSessionBindingStore(
        tmp_path, conversation_id="conversation-1"
    )
    created = store.create_authoritative(_record())
    legacy = {key: value for key, value in created.items() if key != "dsh_checkpoint"}
    legacy["contract"] = "genshin-native-dsh-domain-binding-v1"
    store.path.write_text(json.dumps(legacy), encoding="utf-8")

    with pytest.raises(PlanError) as caught:
        store.cold_read()
    assert caught.value.reason == "native_dsh_binding_version_incompatible"


def test_legacy_cold_recovery_never_claims_complete_dsh_history(tmp_path):
    record = _record()
    store = NativeDshSessionBindingStore(
        tmp_path, conversation_id=record["conversation_id"]
    )

    recovered = store.cold_recover(record)
    repeated = store.cold_recover(record)

    assert repeated == recovered
    assert recovered["binding_origin"] == LEGACY_COMPATIBILITY
    assert recovered["history_state"] == LEGACY_INCOMPATIBLE
    assert recovered["dsh_session_id"].startswith("dsh-session-")


def test_new_binding_activates_only_after_explicit_dsh_checkpoint_receipt(tmp_path):
    record = _record()
    store = NativeDshSessionBindingStore(
        tmp_path, conversation_id=record["conversation_id"]
    )
    reserved = store.create_authoritative(record)

    activated = store.activate_reserved_session(
        dsh_checkpoint=_dsh_checkpoint(reserved["dsh_session_id"]),
        expected_revision=reserved["revision"],
    )

    assert activated["history_state"] == AUTHORITATIVE_DURABLE
    assert store.activate_reserved_session(
        dsh_checkpoint=_dsh_checkpoint(reserved["dsh_session_id"]),
        expected_revision=activated["revision"],
    ) == activated


def test_legacy_binding_cannot_be_activated_as_new_dsh_history(tmp_path):
    record = _record()
    store = NativeDshSessionBindingStore(
        tmp_path, conversation_id=record["conversation_id"]
    )
    store.cold_recover(record)

    with pytest.raises(PlanError, match="旧会话不能激活"):
        store.activate_reserved_session(
            dsh_checkpoint=_dsh_checkpoint(
                store.cold_read()["dsh_session_id"]
            )
        )


def test_domain_checkpoint_advances_without_changing_dsh_identity(tmp_path):
    record = _record()
    store = NativeDshSessionBindingStore(
        tmp_path, conversation_id=record["conversation_id"]
    )
    created = store.create_authoritative(record)
    changed = copy.deepcopy(record)
    changed["domain_checkpoint"]["candidate_revision"] = 1
    refresh_checkpoint_binding(changed)

    checkpointed = store.checkpoint(
        changed, expected_revision=created["revision"]
    )

    assert checkpointed["dsh_session_id"] == created["dsh_session_id"]
    assert checkpointed["revision"] == created["revision"] + 1
    assert checkpointed["domain_checkpoint"]["candidate_revision"] == 1
    with pytest.raises(PlanError, match="修订已变化"):
        store.checkpoint(changed, expected_revision=created["revision"])


def test_archive_delete_precondition_and_tombstone_mapping(tmp_path):
    record = _record()
    store = NativeDshSessionBindingStore(
        tmp_path, conversation_id=record["conversation_id"]
    )
    created = store.create_authoritative(record)
    archived = store.set_archived(
        archived=True, expected_revision=created["revision"]
    )
    assert archived["lifecycle_state"] == ARCHIVED

    pending = store.prepare_delete(
        intent_id="delete-1", expected_revision=archived["revision"]
    )
    assert pending["lifecycle_state"] == DELETE_PENDING
    assert store.runtime_deletion_precondition() == {
        "conversation_id": "conversation-1",
        "dsh_session_id": created["dsh_session_id"],
        "lifecycle_state": DELETE_PENDING,
        "binding_revision": pending["revision"],
        "can_delete_app_files": False,
        "delete_intent_id": "delete-1",
        "runtime_storage_action": "quiesce_tombstone_delete",
    }

    tombstone = store.tombstone(intent_id="delete-1")
    assert tombstone["lifecycle_state"] == TOMBSTONED
    assert tombstone["dsh_session_id"] == created["dsh_session_id"]
    with pytest.raises(PlanError, match="不能重新绑定"):
        store.cold_recover(record)


def test_ambiguous_delete_cannot_be_silently_reactivated(tmp_path):
    record = _record()
    store = NativeDshSessionBindingStore(
        tmp_path, conversation_id=record["conversation_id"]
    )
    created = store.create_authoritative(record)
    store.prepare_delete(
        intent_id="delete-crash", expected_revision=created["revision"]
    )

    with pytest.raises(PlanError, match="删除需要恢复"):
        store.cold_recover(record)
    pending = store.cold_read()
    assert pending["lifecycle_state"] == DELETE_PENDING
    assert pending["delete_intent"]["intent_id"] == "delete-crash"
    assert pending["dsh_session_id"] == created["dsh_session_id"]
