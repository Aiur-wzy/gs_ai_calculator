from __future__ import annotations

from genshin_app.agent_chat.native_document_restore_points import (
    STORE_FILE,
    NativeDocumentRestorePointStore,
)
from genshin_app.agent_chat.native_document_restore_repository import (
    CATALOG_DIRECTORY,
    NativeDocumentRestoreRepository,
)


def _initialized(root, document_id="board-one"):
    store = NativeDocumentRestorePointStore(root, document_id=document_id)
    store.initialize(
        document_kind="whiteboard",
        document={"board_id": document_id, "revision": 0},
        application_payload={
            "kind": "whiteboard",
            "board": {"board_id": document_id, "revision": 0},
            "calculated": None,
        },
    )
    return store


def test_repository_routes_by_document_identity_not_conversation(tmp_path):
    repository = NativeDocumentRestoreRepository(tmp_path)
    first = repository.resolve("board-one")
    second = repository.resolve("board-one")
    other = repository.resolve("board-two")

    assert first == second
    assert first != other
    assert first.parent.name == CATALOG_DIRECTORY
    assert first.name.startswith("document-")


def test_repository_copies_and_validates_legacy_store_without_deleting_it(tmp_path):
    legacy = tmp_path / "conversation-one"
    legacy.mkdir()
    old = _initialized(legacy)
    created = old.create_checkpoint(
        expected_store_revision=0,
        request_id="manual-one",
        name="保留点",
        document={"board_id": "board-one", "revision": 0},
        application_payload={
            "kind": "whiteboard",
            "board": {"board_id": "board-one", "revision": 0},
            "calculated": None,
        },
        source={
            "conversation_id": "conversation-one",
            "turn_id": "turn-one",
            "event_cursor": "1",
        },
    )

    repository = NativeDocumentRestoreRepository(tmp_path)
    root = repository.resolve("board-one", legacy_root=legacy)
    migrated = NativeDocumentRestorePointStore(root, document_id="board-one").read()

    assert migrated["checkpoints"][0]["checkpoint_id"] == created["checkpoint"]["checkpoint_id"]
    assert (legacy / STORE_FILE).is_file()
    assert root != legacy


def test_existing_global_store_is_not_overwritten_by_stale_legacy_copy(tmp_path):
    repository = NativeDocumentRestoreRepository(tmp_path)
    global_root = repository.resolve("board-one")
    global_store = _initialized(global_root)
    global_store.create_checkpoint(
        expected_store_revision=0,
        request_id="global-one",
        name="全局点",
        document={"board_id": "board-one", "revision": 0},
        application_payload={
            "kind": "whiteboard",
            "board": {"board_id": "board-one", "revision": 0},
            "calculated": None,
        },
    )
    legacy = tmp_path / "conversation-two"
    legacy.mkdir()
    _initialized(legacy)

    resolved = repository.resolve("board-one", legacy_root=legacy)
    value = NativeDocumentRestorePointStore(resolved, document_id="board-one").read()

    assert value["checkpoints"][0]["name"] == "全局点"
    assert value["store_revision"] == 1


def test_source_state_becomes_tombstone_after_conversation_directory_is_removed(tmp_path):
    repository = NativeDocumentRestoreRepository(tmp_path)
    source = tmp_path / "conversation-one"
    source.mkdir()
    assert repository.source_state("conversation-one") == "available"
    source.rmdir()
    assert repository.source_state("conversation-one") == "tombstone"
    assert repository.source_state(None) == "unattributed"


def test_integrity_validation_reads_every_referenced_artifact(tmp_path):
    store = _initialized(tmp_path / "store")
    proof = store.validate_integrity()
    assert proof["status"] == "valid"
    assert proof["verified_artifact_count"] == 2
    assert proof["verified_artifact_bytes"] > 0
