from __future__ import annotations

import hashlib
import json

import pytest

from genshin_app.agent_chat.native_conversation_ui import (
    CONTRACT,
    STORE_FILE,
    NativeConversationUiError,
    NativeConversationUiStore,
)


def test_defaults_do_not_create_store_and_first_mutation_uses_revision_zero(tmp_path):
    root = tmp_path / "conversation-one"
    root.mkdir()
    store = NativeConversationUiStore(root, conversation_id="conversation-one")

    assert store.read() == {
        "contract": CONTRACT,
        "conversation_id": "conversation-one",
        "title": None,
        "archived": False,
        "unread": False,
        "metadata_revision": 0,
        "activity_revision": 0,
        "read_activity_revision": 0,
        "status": "current",
    }
    assert not (root / STORE_FILE).exists()

    renamed = store.rename(
        title="  冰 队   循环  ",
        expected_revision=0,
        mutation_id="rename-0001",
    )
    assert renamed["title"] == "冰 队 循环"
    assert renamed["metadata_revision"] == 1
    assert (root / STORE_FILE).exists()


def test_cas_idempotency_and_conflicting_reuse_survive_restart(tmp_path):
    root = tmp_path / "conversation-two"
    root.mkdir()
    first = NativeConversationUiStore(root, conversation_id="conversation-two")
    archived = first.set_archived(
        archived=True,
        expected_revision=0,
        mutation_id="archive-0001",
    )
    assert archived["archived"] is True
    assert archived["metadata_revision"] == 1

    restarted = NativeConversationUiStore(root, conversation_id="conversation-two")
    replay = restarted.set_archived(
        archived=True,
        expected_revision=0,
        mutation_id="archive-0001",
    )
    assert replay["status"] == "already_applied"
    assert replay["metadata_revision"] == 1

    with pytest.raises(NativeConversationUiError) as conflict:
        restarted.set_archived(
            archived=False,
            expected_revision=1,
            mutation_id="archive-0001",
        )
    assert conflict.value.code == "native_session_mutation_conflict"

    with pytest.raises(NativeConversationUiError) as stale:
        restarted.rename(
            title="new",
            expected_revision=0,
            mutation_id="rename-0002",
        )
    assert stale.value.code == "native_session_metadata_stale"


def test_durable_activity_and_mark_read_are_host_authoritative(tmp_path):
    root = tmp_path / "conversation-three"
    root.mkdir()
    store = NativeConversationUiStore(root, conversation_id="conversation-three")

    activity = store.note_visible_activity(
        fact_id="turn-0001-completed",
        source="workbench",
        source_revision=10,
    )
    assert activity["unread"] is True
    assert activity["activity_revision"] == 1
    assert activity["metadata_revision"] == 1
    replay = store.note_visible_activity(
        fact_id="turn-0001-completed",
        source="workbench",
        source_revision=10,
    )
    assert replay["status"] == "already_applied"
    assert replay["activity_revision"] == 1

    read = store.mark_read(
        expected_revision=1,
        mutation_id="mark-read-0001",
    )
    assert read["unread"] is False
    assert read["read_activity_revision"] == 1

    store.note_visible_activity(
        fact_id="question-batch-0002",
        source="workbench",
        source_revision=11,
    )
    persisted = NativeConversationUiStore(
        root, conversation_id="conversation-three"
    ).read()
    assert persisted["unread"] is True
    assert persisted["activity_revision"] == 2
    assert persisted["read_activity_revision"] == 1


def test_activity_high_water_rejects_old_replay_after_mutation_window(tmp_path):
    root = tmp_path / "conversation-high-water"
    root.mkdir()
    store = NativeConversationUiStore(root, conversation_id="conversation-high-water")

    for revision in range(1, 521):
        store.note_visible_activity(
            fact_id=f"workbench-fact-{revision}",
            source="workbench",
            source_revision=revision,
        )
    read = store.mark_read(
        expected_revision=520,
        mutation_id="mark-read-after-window",
    )
    assert read["unread"] is False

    replay = NativeConversationUiStore(
        root, conversation_id="conversation-high-water"
    ).note_visible_activity(
        fact_id="workbench-fact-1",
        source="workbench",
        source_revision=1,
    )
    assert replay["status"] == "already_applied"
    assert replay["unread"] is False
    assert replay["activity_revision"] == 520
    assert replay["metadata_revision"] == 521


def test_activity_sources_are_isolated_and_same_revision_conflicts(tmp_path):
    root = tmp_path / "conversation-source-isolation"
    root.mkdir()
    store = NativeConversationUiStore(
        root, conversation_id="conversation-source-isolation"
    )
    store.note_visible_activity(
        fact_id="workbench-7",
        source="workbench",
        source_revision=7,
    )
    other = store.note_visible_activity(
        fact_id="app-action-1",
        source="app_action",
        source_revision=1,
    )
    assert other["activity_revision"] == 2

    with pytest.raises(NativeConversationUiError) as conflict:
        store.note_visible_activity(
            fact_id="different-workbench-7",
            source="workbench",
            source_revision=7,
        )
    assert conflict.value.code == "native_session_activity_conflict"
    assert store.read()["activity_revision"] == 2

    other_root = tmp_path / "conversation-source-isolation-two"
    other_root.mkdir()
    other_conversation = NativeConversationUiStore(
        other_root, conversation_id="conversation-source-isolation-two"
    ).note_visible_activity(
        fact_id="different-workbench-7",
        source="workbench",
        source_revision=7,
    )
    assert other_conversation["activity_revision"] == 1


def test_legacy_store_without_activity_sources_is_migrated_on_next_write(tmp_path):
    root = tmp_path / "conversation-legacy-activity"
    root.mkdir()
    path = root / STORE_FILE
    path.write_text(
        json.dumps({
            "contract": CONTRACT,
            "conversation_id": "conversation-legacy-activity",
            "revision": 0,
            "title": None,
            "archived": False,
            "activity_revision": 0,
            "read_activity_revision": 0,
            "mutations": [],
        }),
        encoding="utf-8",
    )

    store = NativeConversationUiStore(
        root, conversation_id="conversation-legacy-activity"
    )
    assert store.read()["unread"] is False
    store.note_visible_activity(
        fact_id="first-current-fact",
        source="workbench",
        source_revision=15,
    )
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["activity_sources"] == {
        "workbench": {
            "revision": 15,
            "fact_digest": hashlib.sha256(b"first-current-fact").hexdigest(),
        }
    }


def test_rejects_corrupt_or_cross_conversation_store(tmp_path):
    root = tmp_path / "conversation-four"
    root.mkdir()
    path = root / STORE_FILE
    path.write_text(
        json.dumps({
            "contract": CONTRACT,
            "conversation_id": "another-conversation",
            "revision": 0,
            "title": None,
            "archived": False,
            "activity_revision": 0,
            "read_activity_revision": 0,
            "mutations": [],
        }),
        encoding="utf-8",
    )

    with pytest.raises(NativeConversationUiError) as error:
        NativeConversationUiStore(
            root, conversation_id="conversation-four"
        ).read()
    assert error.value.code == "native_session_metadata_invalid"


def test_session_title_redacts_credentials_and_local_paths_before_persisting(tmp_path):
    root = tmp_path / "conversation-safe-title"
    root.mkdir()
    store = NativeConversationUiStore(
        root, conversation_id="conversation-safe-title"
    )

    renamed = store.rename(
        title=r"排查 C:\Users\demo\private.json sk-abcdefghijklmnopqrstuvwxyz123456",
        expected_revision=0,
        mutation_id="rename-sensitive-title",
    )

    assert "C:\\Users" not in renamed["title"]
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in renamed["title"]
    assert "已隐藏本地路径" in renamed["title"]
    persisted = (root / STORE_FILE).read_text(encoding="utf-8")
    assert "C:\\\\Users" not in persisted
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in persisted
