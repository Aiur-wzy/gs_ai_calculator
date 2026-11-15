from __future__ import annotations

import copy
import json
import threading
from contextlib import contextmanager

import pytest

from genshin_app.agent_chat import native_session_store as store_module
from genshin_app.agent_chat.native_app_call_ledger import (
    UNKNOWN as APP_CALL_UNKNOWN,
)
from genshin_app.agent_chat.native_app_call_ledger import (
    NativeAppCallLedger,
    make_app_call_identity,
)
from genshin_app.agent_chat.native_app_tool_effects import ALL_EFFECTS
from genshin_app.agent_chat.native_session_store import (
    MAX_VISIBLE_MESSAGES,
    NativeSessionStore,
    append_tool_settlement_ref,
    assess_activation,
    create_native_session,
    new_writer_fence,
    normalize_native_session,
    replace_domain_checkpoint,
    resume_seed,
    set_last_turn,
)
from genshin_app.agent_chat.native_write_ledger import (
    create_write_ledger,
    normalize_write_ledger,
)
from genshin_app.agent_chat.sessions import write_owned
from genshin_app.equipment.schema import PlanError
from tests.legacy_native_agent_fixtures import append_legacy_visible_message

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64
HASH_F = "f" * 64
FENCE_OLD = "wf_" + "a" * 43
FENCE_NEW = "wf_" + "b" * 43
FENCE_NEWER = "wf_" + "c" * 43
FENCE_THIRD = "wf_" + "d" * 43


def test_session_store_uses_the_shared_app_tool_effect_contract():
    assert store_module._APP_CALL_EFFECTS is ALL_EFFECTS


def test_legacy_visible_message_writer_is_not_a_production_api():
    assert "append_visible_message" not in store_module.__all__
    assert not hasattr(store_module, "append_visible_message")


def _domain(**changes):
    value = {
        "document_id": "document-1",
        "candidate_revision": 7,
        "stage_revision": 3,
        "database_fingerprint": HASH_A,
        "receipt_fingerprint": HASH_B,
        "writer_fence": FENCE_OLD,
    }
    value.update(changes)
    return value


def _record(**changes):
    value = create_native_session(
        conversation_id="conversation-1",
        connection_kind="native_api",
        runtime_generation="dsh_v2",
        locked_provider="deepseek",
        locked_model="deepseek-chat",
        locked_reasoning_effort="high",
        domain_checkpoint=_domain(),
        prompt_profile_hash=HASH_C,
        tool_manifest_hash=HASH_D,
    )
    value.update(changes)
    if changes:
        store_module.refresh_checkpoint_binding(value)
    return value


class _Session:
    def __init__(self, root):
        self._lock = threading.RLock()
        self.root = root
        self.db_path = root / "game.db"
        self.db_path.write_bytes(b"")
        self.kind = "native"
        self.document = {"plan_id": "document-1"}

    @contextmanager
    def _read_db(self, *, check):
        assert check is True
        yield


def _settlement(**changes):
    value = {
        "call_id": "call-1",
        "args_hash": HASH_A,
        "before_fingerprint": HASH_B,
        "after_fingerprint": HASH_B,
        "status": "settled_success",
        "effect": "read_only",
        "resource_refs": ["resource-1"],
        "workflow_stage": "action_draft",
    }
    value.update(changes)
    return value


def _set_pending_takeover(record, *, proposed_fence=FENCE_NEW):
    pending = store_module._takeover_proposal(
        record,
        expected_activation_generation=record["activation_generation"],
        expected_fence=record["domain_checkpoint"]["writer_fence"],
        proposed_fence=proposed_fence,
    )
    record["activation_generation"] = pending["activation_generation"]
    record["writer_takeover"] = pending
    store_module.refresh_checkpoint_binding(record)
    return pending


def _ledger_terminal(**changes):
    identity = {
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "step_id": "step-1",
        "epoch_id": "epoch-1",
        "document_id": "document-1",
        "call_id": "write-call-1",
        "tool_name": "gs_patch_candidate",
        "canonical_args_hash": HASH_A,
        "runtime_generation": "dsh_v2",
        "candidate_revision": 7,
        "stage_revision": 3,
        "database_fingerprint": HASH_A,
        "tool_manifest_hash": HASH_D,
        "writer_fence": FENCE_OLD,
    }
    value = {
        "identity": identity,
        "document_id": "document-1",
        "state": "settled_success",
        "started_at": "2026-10-23T00:00:00Z",
        "settled_at": "2026-10-23T00:00:01Z",
        "before_fingerprint": HASH_B,
        "after_fingerprint": HASH_C,
        "result_json": '{"code":0}',
        "result_hash": store_module.fingerprint({"code": 0}),
        "result_code": 0,
        "terminal_reason": None,
        "post_candidate_revision": 8,
        "post_stage_revision": 3,
        "post_tool_manifest_hash": HASH_D,
    }
    identity_changes = changes.pop("identity", None)
    if identity_changes:
        identity.update(identity_changes)
    value.update(changes)
    return value


def _observed(**changes):
    current = _record()
    value = {
        "connection_kind": "native_api",
        "runtime_generation": "dsh_v2",
        "locked_provider": "deepseek",
        "locked_model": "deepseek-chat",
        "locked_reasoning_effort": "high",
        "document_id": "document-1",
        "candidate_revision": 7,
        "stage_revision": 3,
        "database_fingerprint": HASH_A,
        "receipt_fingerprint": HASH_B,
        "runtime_release_hash": current["runtime_release_hash"],
        "prompt_profile_hash": HASH_C,
        "tool_manifest_hash": HASH_D,
        "release_pair_identity_hash": current["release_pair_identity_hash"],
        "app_artifact_identity_hash": current["app_artifact_identity_hash"],
        "skill_bundle_hash": current["skill_bundle_hash"],
    }
    value.update(changes)
    return value


def _persist_write_ledger(root, *entries):
    ledger = create_write_ledger("conversation-1")
    ledger["entries"] = {
        entry["identity"]["call_id"]: copy.deepcopy(entry)
        for entry in entries
    }
    normalize_write_ledger(ledger)
    write_owned(
        root, store_module.WRITE_LEDGER_FILE, ledger,
        store_module.MAX_WRITE_LEDGER_BYTES,
    )
    return ledger


def _usage(turn_id="turn-1", **changes):
    value = {
        "turn_id": turn_id,
        "input_tokens": 10,
        "output_tokens": 5,
        "reasoning_tokens": 2,
        "cached_input_tokens": 1,
        "total_tokens": 17,
    }
    value.update(changes)
    return value


def test_create_append_and_resume_seed_only_expose_bounded_semantic_state():
    record = _record()
    message = append_legacy_visible_message(
        record,
        message_id="message-1",
        role="user",
        text=(
            "请读取 C:\\Users\\person\\private.json 和 /home/person/private.json；"
            "file:///D:/secret.txt；api_key=sk-1234567890；"
            "Bearer abcdefghijklmnop；参考 https://example.com/docs/page。"
        ),
        workflow_stage="action_draft",
        user_turn=1,
    )
    assert "C:\\" not in message["text"]
    assert "/home/" not in message["text"]
    assert "file:" not in message["text"]
    assert "sk-" not in message["text"]
    assert "abcdefghijklmnop" not in message["text"]
    assert "[已隐藏本地路径]" in message["text"]
    assert "[已隐藏凭据]" in message["text"]
    assert "https://example.com/docs/page" in message["text"]

    append_tool_settlement_ref(record, _settlement())
    set_last_turn(
        record,
        turn_id="turn-1",
        status="completed",
        provider_request_possible=True,
        terminal_reason=None,
    )
    seed = resume_seed(record)
    encoded = json.dumps(seed, ensure_ascii=False)
    assert "raw_reasoning" not in encoded
    assert "arguments" not in encoded
    assert "provider_stream" not in encoded
    assert "api_key" not in encoded
    assert seed["locked_model"] == "deepseek-chat"
    assert "writer_fence" not in seed["domain_checkpoint"]
    assert "checkpoint_binding" not in seed
    assert seed["tool_settlement_refs"][0]["call_id"] == "call-1"


def test_visible_message_does_not_treat_chinese_ratio_separator_as_local_path():
    record = _record()
    message = append_legacy_visible_message(
        record,
        message_id="message-ratio",
        role="user",
        text="全部伤害加成和各元素/物理伤害加成都为0。",
        workflow_stage="loadout_draft",
        user_turn=1,
    )

    assert message["text"] == "全部伤害加成和各元素/物理伤害加成都为0。"


def test_writer_fence_is_high_entropy_capability_and_weak_values_fail_closed():
    first = new_writer_fence()
    second = new_writer_fence()
    assert first != second
    assert len(first) == len("wf_") + 43
    assert first.startswith("wf_")
    weak = _record()
    weak["domain_checkpoint"]["writer_fence"] = "fence-old"
    store_module.refresh_checkpoint_binding(weak)
    with pytest.raises(PlanError, match="256-bit"):
        normalize_native_session(weak)


def test_visible_history_is_bounded_and_tracks_eviction():
    record = _record()
    for index in range(MAX_VISIBLE_MESSAGES + 3):
        append_legacy_visible_message(
            record,
            message_id=f"message-{index}",
            role="assistant",
            text="ok",
            workflow_stage="event_topology",
            user_turn=index,
        )
    assert len(record["visible_messages"]) == MAX_VISIBLE_MESSAGES
    assert record["visible_message_evicted_count"] == 3
    assert record["visible_messages"][0]["message_id"] == "message-3"


def test_resume_seed_has_tighter_context_bound_and_is_deeply_detached():
    record = _record()
    for index in range(store_module.MAX_RESUME_VISIBLE_MESSAGES + 8):
        append_legacy_visible_message(
            record,
            message_id=f"resume-message-{index}",
            role="assistant",
            text="x" * 2_000,
            workflow_stage="event_topology",
            user_turn=index,
        )
    seed = resume_seed(record)
    assert len(seed["visible_messages"]) <= store_module.MAX_RESUME_VISIBLE_MESSAGES
    assert sum(len(row["text"]) for row in seed["visible_messages"]) <= (
        store_module.MAX_RESUME_VISIBLE_TEXT_CHARS
    )
    assert seed["resume_visible_message_omitted_count"] > 0
    saved_text = record["visible_messages"][-1]["text"]
    assert seed["visible_messages"] == []
    assert record["visible_messages"][-1]["text"] == saved_text


def test_cold_read_has_no_runtime_or_provider_side_effect(tmp_path, monkeypatch):
    root = tmp_path / "session"
    root.mkdir()
    store = NativeSessionStore(root)
    store.create(_record())

    def forbidden(*_args, **_kwargs):
        raise AssertionError("cold read must not attach writer or start runtime")

    monkeypatch.setattr(store_module, "SessionWriteAuthority", forbidden)
    loaded = store.cold_read()
    assert loaded["conversation_id"] == "conversation-1"
    assert loaded["activation_generation"] == 0


def test_activation_assessment_reports_migration_without_mutating_record():
    record = _record()
    before = copy.deepcopy(record)
    ready = assess_activation(record, _observed())
    assert ready == {
        "status": "ready",
        "conversation_id": "conversation-1",
        "activation_generation": 0,
        "issues": [],
        "writer_takeover_pending": False,
    }
    blocked = assess_activation(
        record,
        _observed(
            locked_model="deepseek-reasoner",
            database_fingerprint=HASH_D,
            runtime_release_hash=HASH_E,
            prompt_profile_hash=HASH_A,
        ),
    )
    assert blocked["status"] == "blocked"
    assert {row["code"] for row in blocked["issues"]} == {
        "model_changed", "database_changed", "runtime_release_changed",
        "prompt_profile_changed",
    }
    assert record == before


def test_activation_assessment_blocks_release_app_and_skill_identity_changes():
    record = _record()

    blocked = assess_activation(
        record,
        _observed(
            release_pair_identity_hash=HASH_D,
            app_artifact_identity_hash=HASH_E,
            skill_bundle_hash=HASH_F,
        ),
    )

    assert blocked["status"] == "blocked"
    assert {row["code"] for row in blocked["issues"]} == {
        "release_pair_changed",
        "app_artifact_changed",
        "skill_bundle_changed",
    }


def test_cold_read_rejects_tampered_secret_path_or_raw_field(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    store = NativeSessionStore(root)
    record = _record()
    append_legacy_visible_message(
        record, message_id="message-1", role="user", text="safe",
        workflow_stage="action_draft", user_turn=1,
    )
    store.create(record)

    path = root / "native-session-v2.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["visible_messages"][0]["text"] = "C:\\Users\\person\\secret.txt"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PlanError, match="脱敏"):
        store.cold_read()

    raw["visible_messages"][0]["text"] = "safe"
    raw["raw_reasoning"] = "must not persist"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PlanError, match="字段"):
        store.cold_read()


def test_cold_read_rejects_duplicate_json_fields(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    store = NativeSessionStore(root)
    record = _record()
    store.create(record)
    path = root / "native-session-v2.json"
    raw = path.read_text(encoding="utf-8")
    path.write_text(
        raw[:-1] + ',"conversation_id":"conversation-shadow"}',
        encoding="utf-8",
    )
    with pytest.raises(PlanError, match="重复字段"):
        store.cold_read()


def test_store_generation_cas_prevents_lost_activation_update(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    store = NativeSessionStore(root)
    store.create(_record())
    stale = store.cold_read()
    current = store.cold_read()
    original_binding = current["checkpoint_binding"]
    append_legacy_visible_message(
        current, message_id="message-current", role="assistant", text="current",
        workflow_stage="action_draft", user_turn=1,
    )
    store.save(
        current,
        expected_activation_generation=0,
        expected_checkpoint_binding=original_binding,
    )

    append_legacy_visible_message(
        stale, message_id="message-stale", role="user", text="stale",
        workflow_stage="action_draft", user_turn=1,
    )
    with pytest.raises(PlanError) as error:
        store.save(
            stale,
            expected_activation_generation=0,
            expected_checkpoint_binding=original_binding,
        )
    assert error.value.reason == "stale_context"


def test_plain_save_cannot_change_activation_locked_config_or_fence(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    store = NativeSessionStore(root)
    store.create(_record())
    current = store.cold_read()
    for mutate in (
        lambda row: row.__setitem__("activation_generation", 1),
        lambda row: row.__setitem__("locked_model", "deepseek-reasoner"),
        lambda row: row["domain_checkpoint"].__setitem__("writer_fence", FENCE_NEW),
    ):
        changed = copy.deepcopy(current)
        mutate(changed)
        store_module.refresh_checkpoint_binding(changed)
        with pytest.raises(PlanError) as error:
            store.save(
                changed,
                expected_activation_generation=0,
                expected_checkpoint_binding=current["checkpoint_binding"],
            )
        assert error.value.reason == "stale_context"


def test_writer_activation_restores_old_fence_then_cas_transfers(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    store = NativeSessionStore(root)
    store.create(_record())
    session = _Session(root)

    activated, authority = store.activate_writer(
        session,
        proposed_fence=FENCE_NEW,
        expected_activation_generation=0,
    )
    assert activated["activation_generation"] == 1
    assert activated["domain_checkpoint"]["writer_fence"] == FENCE_NEW
    assert activated["writer_takeover"] is None
    assert authority.current_fence() == FENCE_NEW
    assert store.cold_read() == activated

    restored_session = _Session(root)
    second, second_authority = store.activate_writer(
        restored_session,
        proposed_fence=FENCE_NEWER,
        expected_activation_generation=1,
    )
    assert second["activation_generation"] == 2
    assert second["domain_checkpoint"]["writer_fence"] == FENCE_NEWER
    assert second["writer_takeover_receipt"]["expected_activation_generation"] == 1
    assert second["writer_takeover_receipt"]["proposed_fence"] == FENCE_NEWER
    assert second_authority.current_fence() == FENCE_NEWER


def test_writer_activation_retry_after_final_record_ack_loss_is_idempotent(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    store = NativeSessionStore(root)
    store.create(_record())
    activated, _ = store.activate_writer(
        _Session(root),
        proposed_fence=FENCE_NEW,
        expected_activation_generation=0,
    )
    retried, authority = store.activate_writer(
        _Session(root),
        proposed_fence=FENCE_NEW,
        expected_activation_generation=0,
    )
    assert retried == activated
    assert retried["activation_generation"] == 1
    assert authority.current_fence() == FENCE_NEW


def test_concurrent_exact_writer_takeover_converges_without_deadlock(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    store = NativeSessionStore(root)
    store.create(_record())
    sessions = [_Session(root), _Session(root)]
    barrier = threading.Barrier(2)
    results = []
    errors = []

    def activate(session):
        try:
            barrier.wait(2)
            results.append(store.activate_writer(
                session,
                proposed_fence=FENCE_NEW,
                expected_activation_generation=0,
            ))
        except (OSError, PlanError) as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=activate, args=(session,))
               for session in sessions]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == 2
    assert {row[0]["activation_generation"] for row in results} == {1}
    assert {row[0]["domain_checkpoint"]["writer_fence"] for row in results} == {
        FENCE_NEW,
    }


def test_writer_activation_recovers_crash_after_lease_cas(tmp_path, monkeypatch):
    root = tmp_path / "session"
    root.mkdir()
    store = NativeSessionStore(root)
    store.create(_record())
    session = _Session(root)
    original_write = store_module.write_owned
    writes = 0

    def fail_final(root_arg, name, value, maximum):
        nonlocal writes
        if name == "native-session-v2.json":
            writes += 1
            if writes == 2:
                raise PlanError("simulated final record crash", "file_conflict")
        return original_write(root_arg, name, value, maximum)

    monkeypatch.setattr(store_module, "write_owned", fail_final)
    with pytest.raises(PlanError, match="simulated"):
        store.activate_writer(
            session,
            proposed_fence=FENCE_NEW,
            expected_activation_generation=0,
        )

    pending = store.cold_read()
    assert pending["activation_generation"] == 1
    assert pending["domain_checkpoint"]["writer_fence"] == FENCE_OLD
    assert pending["writer_takeover"]["activation_generation"] == 1
    assert pending["writer_takeover"]["expected_activation_generation"] == 0
    assert pending["writer_takeover"]["expected_fence"] == FENCE_OLD
    assert pending["writer_takeover"]["proposed_fence"] == FENCE_NEW
    assert len(pending["writer_takeover"]["operation_id"]) == 64

    monkeypatch.setattr(store_module, "write_owned", original_write)
    recovered, authority = store.activate_writer(
        _Session(root),
        expected_activation_generation=1,
    )
    assert recovered["activation_generation"] == 1
    assert recovered["writer_takeover"] is None
    assert recovered["domain_checkpoint"]["writer_fence"] == FENCE_NEW
    assert authority.current_fence() == FENCE_NEW


def test_native_app_call_ref_binds_revision_and_full_ledger_without_payload_leak():
    ledger = {
        "contract": "genshin-native-app-call-ledger-v1",
        "conversation_id": "conversation-1",
        "revision": 0,
        "entries": {},
    }
    reference = store_module.native_app_call_ledger_checkpoint_ref(ledger)
    assert reference.startswith("appcalls.v1.0.")
    assert "conversation-1" not in reference
    store_module.verify_native_app_call_ledger_checkpoint_ref(reference, ledger)

    changed = copy.deepcopy(ledger)
    changed["revision"] = 1
    with pytest.raises(PlanError, match="不匹配"):
        store_module.verify_native_app_call_ledger_checkpoint_ref(
            reference,
            changed,
        )
def test_pending_writer_takeover_rejects_invented_third_fence(tmp_path, monkeypatch):
    root = tmp_path / "session"
    root.mkdir()
    store = NativeSessionStore(root)
    record = _record()
    _set_pending_takeover(record)
    store.create(record)

    with pytest.raises(PlanError) as error:
        store.activate_writer(
            _Session(root), proposed_fence=FENCE_THIRD,
            expected_activation_generation=1,
        )
    assert error.value.reason == "stale_context"
    assert not (root / "native-writer-authority.json").exists()


def test_locked_configuration_and_checkpoint_binding_fail_closed():
    record = _record()
    for field, value in (
        ("locked_model", "secret:credential"),
        ("locked_model", "x-api-key:credential"),
        ("locked_model", FENCE_OLD),
        ("runtime_generation", "python_v1"),
    ):
        broken = copy.deepcopy(record)
        broken[field] = value
        with pytest.raises(PlanError):
            normalize_native_session(broken)

    broken = copy.deepcopy(record)
    broken["domain_checkpoint"]["candidate_revision"] += 1
    with pytest.raises(PlanError, match="绑定"):
        normalize_native_session(broken)


def test_domain_checkpoint_cannot_change_during_pending_takeover():
    record = _record()
    _set_pending_takeover(record)
    normalize_native_session(record)
    with pytest.raises(PlanError) as error:
        replace_domain_checkpoint(
            record, _domain(candidate_revision=8),
        )
    assert error.value.reason == "stale_context"


def test_domain_checkpoint_cannot_bypass_writer_fence_cas():
    record = _record()
    with pytest.raises(PlanError) as error:
        replace_domain_checkpoint(
            record, _domain(writer_fence=FENCE_THIRD),
        )
    assert error.value.reason == "stale_context"


def test_settlement_ref_is_idempotent_but_conflicts_fail():
    record = _record()
    first = append_tool_settlement_ref(record, _settlement())
    assert append_tool_settlement_ref(record, _settlement()) == first
    with pytest.raises(PlanError) as error:
        append_tool_settlement_ref(
            record, _settlement(args_hash=HASH_D),
        )
    assert error.value.reason == "settlement_conflict"


@pytest.mark.parametrize("field", [
    "earlier_message", "settlement", "research_ref", "app_action_ref", "usage",
])
def test_checkpoint_binding_covers_every_persisted_semantic_field(field):
    record = _record()
    append_legacy_visible_message(
        record, message_id="message-1", role="user", text="first",
        workflow_stage="action_draft", user_turn=1,
    )
    append_legacy_visible_message(
        record, message_id="message-2", role="assistant", text="second",
        workflow_stage="action_draft", user_turn=1,
    )
    if field == "earlier_message":
        record["visible_messages"][0]["text"] = "changed"
    elif field == "settlement":
        record["tool_settlement_refs"].append(_settlement())
    elif field == "research_ref":
        record["research_ledger_refs"].append("research-1")
    elif field == "app_action_ref":
        record["app_action_refs"].append("action-1")
    else:
        record["usage_records"].append(_usage())
    with pytest.raises(PlanError, match="绑定"):
        normalize_native_session(record)
    store_module.refresh_checkpoint_binding(record)
    normalize_native_session(record)


def test_save_allows_only_append_only_state_and_controlled_eviction(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    store = NativeSessionStore(root)
    record = _record()
    for index in range(MAX_VISIBLE_MESSAGES):
        append_legacy_visible_message(
            record, message_id=f"message-{index}", role="assistant", text="ok",
            workflow_stage="action_draft", user_turn=index,
        )
    record["research_ledger_refs"].append("research-1")
    record["app_action_refs"].append("action-1")
    record["usage_records"].append(_usage())
    store_module.refresh_checkpoint_binding(record)
    store.create(record)

    current = store.cold_read()
    expected_binding = current["checkpoint_binding"]
    append_legacy_visible_message(
        current, message_id="message-next", role="user", text="next",
        workflow_stage="action_draft", user_turn=MAX_VISIBLE_MESSAGES,
    )
    append_tool_settlement_ref(
        current, _settlement(call_id="call-next", resource_refs=[]),
    )
    current["research_ledger_refs"].append("research-2")
    current["app_action_refs"].append("action-2")
    current["usage_records"].append(_usage("turn-2"))
    current["domain_checkpoint"]["candidate_revision"] += 1
    current["domain_checkpoint"]["stage_revision"] += 1
    store_module.refresh_checkpoint_binding(current)
    saved = store.save(
        current,
        expected_activation_generation=0,
        expected_checkpoint_binding=expected_binding,
    )
    assert saved["visible_message_evicted_count"] == 1
    assert saved["visible_messages"][0]["message_id"] == "message-1"
    assert saved["visible_messages"][-1]["message_id"] == "message-next"


@pytest.mark.parametrize("mutation", [
    "rewrite_message", "unneeded_eviction", "rewrite_settlement",
    "remove_research", "rewrite_usage", "candidate_regression", "stage_regression",
])
def test_save_rejects_rewrites_removals_and_revision_regressions(tmp_path, mutation):
    root = tmp_path / mutation
    root.mkdir()
    store = NativeSessionStore(root)
    record = _record()
    append_legacy_visible_message(
        record, message_id="message-1", role="user", text="one",
        workflow_stage="action_draft", user_turn=1,
    )
    append_legacy_visible_message(
        record, message_id="message-2", role="assistant", text="two",
        workflow_stage="action_draft", user_turn=1,
    )
    append_tool_settlement_ref(record, _settlement(resource_refs=[]))
    record["research_ledger_refs"].append("research-1")
    record["usage_records"].append(_usage())
    store_module.refresh_checkpoint_binding(record)
    store.create(record)
    changed = store.cold_read()
    binding = changed["checkpoint_binding"]
    if mutation == "rewrite_message":
        changed["visible_messages"][0]["text"] = "rewritten"
    elif mutation == "unneeded_eviction":
        changed["visible_messages"].pop(0)
        changed["visible_message_evicted_count"] += 1
    elif mutation == "rewrite_settlement":
        changed["tool_settlement_refs"][0]["after_fingerprint"] = HASH_D
    elif mutation == "remove_research":
        changed["research_ledger_refs"].clear()
    elif mutation == "rewrite_usage":
        changed["usage_records"][0]["output_tokens"] += 1
    elif mutation == "candidate_regression":
        changed["domain_checkpoint"]["candidate_revision"] -= 1
    else:
        changed["domain_checkpoint"]["stage_revision"] -= 1
    store_module.refresh_checkpoint_binding(changed)
    with pytest.raises(PlanError) as error:
        store.save(
            changed,
            expected_activation_generation=0,
            expected_checkpoint_binding=binding,
        )
    assert error.value.reason == "stale_context"


def test_database_prompt_and_manifest_changes_require_exact_migration_targets(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    store = NativeSessionStore(root)
    store.create(_record())
    current = store.cold_read()
    binding = current["checkpoint_binding"]
    current["domain_checkpoint"]["database_fingerprint"] = HASH_E
    current["prompt_profile_hash"] = HASH_F
    current["tool_manifest_hash"] = "1" * 64
    store_module.refresh_checkpoint_binding(current)
    with pytest.raises(PlanError) as error:
        store.save(
            current,
            expected_activation_generation=0,
            expected_checkpoint_binding=binding,
        )
    assert error.value.reason == "stale_context"
    with pytest.raises(PlanError):
        store.save(
            current,
            expected_activation_generation=0,
            expected_checkpoint_binding=binding,
            migrations={
                "database_fingerprint": HASH_E,
                "prompt_profile_hash": HASH_F,
                "tool_manifest_hash": "2" * 64,
            },
        )
    saved = store.save(
        current,
        expected_activation_generation=0,
        expected_checkpoint_binding=binding,
        migrations={
            "database_fingerprint": HASH_E,
            "prompt_profile_hash": HASH_F,
            "tool_manifest_hash": "1" * 64,
        },
    )
    assert saved["domain_checkpoint"]["database_fingerprint"] == HASH_E


@pytest.mark.parametrize(("status", "before", "after", "accepted"), [
    ("settled_success", HASH_A, HASH_A, True),
    ("settled_success", HASH_A, HASH_B, False),
    ("settled_failure", HASH_A, HASH_B, False),
    ("settled_failure", HASH_A, HASH_A, True),
    ("unknown_after_interruption", HASH_A, HASH_A, True),
    ("unknown_after_interruption", HASH_A, HASH_B, False),
])
def test_settlement_fingerprint_semantics_are_fail_closed(
    status, before, after, accepted,
):
    record = _record()
    row = _settlement(
        status=status,
        before_fingerprint=before,
        after_fingerprint=after,
        resource_refs=[],
    )
    if accepted:
        assert append_tool_settlement_ref(record, row)["status"] == status
    else:
        with pytest.raises(PlanError):
            append_tool_settlement_ref(record, row)


def test_candidate_write_settlement_is_loaded_from_same_root_terminal_ledger(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    record = _record(domain_checkpoint=_domain(candidate_revision=8))
    append_legacy_visible_message(
        record, message_id="message-1", role="assistant", text="stage",
        workflow_stage="event_topology", user_turn=1,
    )
    store = NativeSessionStore(root)
    store.create(record)
    _persist_write_ledger(root, _ledger_terminal())
    updated, saved = store.append_candidate_write_settlement(
        call_id="write-call-1",
        workflow_stage="event_topology",
        expected_activation_generation=0,
        expected_checkpoint_binding=record["checkpoint_binding"],
    )
    assert saved == {
        "call_id": "write-call-1",
        "args_hash": HASH_A,
        "before_fingerprint": HASH_B,
        "after_fingerprint": HASH_C,
        "status": "settled_success",
        "effect": "candidate_write",
        "resource_refs": [],
        "workflow_stage": "event_topology",
    }
    assert updated == store.cold_read()
    with pytest.raises(PlanError, match="同根持久账本"):
        append_tool_settlement_ref(
            _record(), _settlement(resource_refs=["resource-1"]),
            candidate_write=True,
        )


def test_public_candidate_checkpoint_binds_turn_epoch_before_archive(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    store = NativeSessionStore(root)
    before = store.create(_record())
    _persist_write_ledger(root, _ledger_terminal())
    post_domain = _domain(candidate_revision=8, receipt_fingerprint=HASH_E)

    checkpointed, terminal = store.checkpoint_candidate_write_terminal(
        call_id="write-call-1",
        expected_turn_id="turn-1",
        expected_epoch_id="epoch-1",
        post_domain_checkpoint=post_domain,
        post_tool_manifest_hash=HASH_D,
        expected_activation_generation=before["activation_generation"],
        expected_checkpoint_binding=before["checkpoint_binding"],
        workflow_stage="event_topology",
    )

    assert checkpointed["domain_checkpoint"] == post_domain
    assert checkpointed["tool_settlement_refs"] == []
    assert terminal["call_id"] == "write-call-1"
    archived, row = store.append_candidate_write_settlement(
        call_id="write-call-1",
        expected_activation_generation=checkpointed["activation_generation"],
        expected_checkpoint_binding=checkpointed["checkpoint_binding"],
        workflow_stage="event_topology",
    )
    assert archived["tool_settlement_refs"] == [row]


def test_public_candidate_checkpoint_rejects_terminal_from_other_turn(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    store = NativeSessionStore(root)
    before = store.create(_record())
    _persist_write_ledger(root, _ledger_terminal(identity={"turn_id": "turn-old"}))

    with pytest.raises(PlanError) as error:
        store.checkpoint_candidate_write_terminal(
            call_id="write-call-1",
            expected_turn_id="turn-1",
            expected_epoch_id="epoch-1",
            post_domain_checkpoint=_domain(
                candidate_revision=8,
                receipt_fingerprint=HASH_E,
            ),
            post_tool_manifest_hash=HASH_D,
            expected_activation_generation=before["activation_generation"],
            expected_checkpoint_binding=before["checkpoint_binding"],
            workflow_stage="event_topology",
        )
    assert error.value.reason == "stale_context"
    assert store.cold_read()["domain_checkpoint"]["candidate_revision"] == 7


def test_interrupted_turn_recovers_one_exact_terminal_before_writer_takeover(
    tmp_path,
):
    root = tmp_path / "session"
    root.mkdir()
    store = NativeSessionStore(root)
    record = _record()
    append_legacy_visible_message(
        record,
        message_id="message-user-1",
        role="user",
        text="修改候选。",
        workflow_stage="event_topology",
        user_turn=1,
    )
    set_last_turn(
        record,
        turn_id="turn-1",
        status="idle",
        provider_request_possible=True,
        terminal_reason=None,
    )
    before = store.create(record)
    _persist_write_ledger(root, _ledger_terminal())

    recovered, rows = store.recover_interrupted_turn(
        observed_domain_checkpoint=_domain(
            candidate_revision=8,
            receipt_fingerprint=HASH_E,
        ),
        observed_tool_manifest_hash=HASH_D,
        expected_activation_generation=before["activation_generation"],
        expected_checkpoint_binding=before["checkpoint_binding"],
    )

    assert recovered["domain_checkpoint"]["candidate_revision"] == 8
    assert [row["call_id"] for row in rows] == ["write-call-1"]
    assert recovered["tool_settlement_refs"] == list(rows)
    assert recovered["last_turn"] == {
        "turn_id": "turn-1",
        "status": "interrupted",
        "provider_request_possible": True,
        "terminal_reason": "process_interrupted",
    }


def test_interrupted_turn_adopts_proven_generic_app_call_without_replay(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    store = NativeSessionStore(root)
    record = _record()
    set_last_turn(
        record,
        turn_id="turn-1",
        status="idle",
        provider_request_possible=True,
        terminal_reason=None,
    )
    before = store.create(record)
    identity = make_app_call_identity(
        conversation_id="conversation-1",
        turn_id="turn-1",
        step_id="step-1",
        epoch_id="epoch-1",
        document_id="document-1",
        call_id="app-call-1",
        tool_name="gs_patch_candidate",
        raw_arguments_hash=HASH_A,
        runtime_generation="dsh_v2",
        candidate_revision=7,
        stage_revision=3,
        database_fingerprint=HASH_A,
        tool_manifest_hash=HASH_D,
        catalog_hash=HASH_F,
        writer_fence=FENCE_OLD,
    )
    app_ledger = NativeAppCallLedger(root, conversation_id="conversation-1")
    app_ledger.begin(
        identity=identity, effect="candidate", before_state_hash=HASH_B,
    )
    app_ledger.settle(
        identity=identity,
        effect="candidate",
        state=APP_CALL_UNKNOWN,
        after_state_hash=HASH_E,
        terminal_reason="effect_mask_violation",
    )

    recovered, rows = store.recover_interrupted_turn(
        observed_domain_checkpoint=_domain(
            candidate_revision=8,
            receipt_fingerprint=HASH_E,
        ),
        observed_tool_manifest_hash=HASH_D,
        observed_app_state_hash=HASH_E,
        expected_activation_generation=before["activation_generation"],
        expected_checkpoint_binding=before["checkpoint_binding"],
    )

    assert recovered["domain_checkpoint"]["candidate_revision"] == 8
    assert [row["call_id"] for row in rows] == ["app-call-1"]
    assert rows[0]["status"] == "unknown_after_interruption"
    assert rows[0]["effect"] == "candidate_write"


def test_candidate_manifest_rollover_requires_durable_transition_proof(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    store = NativeSessionStore(root)
    before = store.create(_record())
    identity = make_app_call_identity(
        conversation_id="conversation-1",
        turn_id="turn-1",
        step_id="step-1",
        epoch_id="epoch-1",
        document_id="document-1",
        call_id="app-call-transition",
        tool_name="gs_patch_candidate",
        raw_arguments_hash=HASH_A,
        runtime_generation="dsh_v2",
        candidate_revision=7,
        stage_revision=3,
        database_fingerprint=HASH_A,
        tool_manifest_hash=HASH_D,
        catalog_hash=HASH_F,
        writer_fence=FENCE_OLD,
    )
    app_ledger = NativeAppCallLedger(root, conversation_id="conversation-1")
    app_ledger.begin(
        identity=identity, effect="candidate", before_state_hash=HASH_B,
    )
    transition = {
        "contract": "genshin-native-stage-transition-v1",
        "kind": "automatic_invalidation",
        "from_stage": "numeric_model",
        "to_stage": "event_topology",
        "pre_stage_revision": 3,
        "post_stage_revision": 4,
        "pre_tool_manifest_hash": HASH_D,
        "post_tool_manifest_hash": HASH_E,
    }
    terminal = app_ledger.settle(
        identity=identity,
        effect="candidate",
        state="settled_success",
        after_state_hash=HASH_C,
        result={"code": 0},
        stage_transition=transition,
    )["entry"]
    projection = {
        "terminal_state": terminal["state"],
        "call_id": identity["call_id"],
        "effect": terminal["effect"],
        "result_hash": terminal["result_hash"],
        "before_state_hash": terminal["before_state_hash"],
        "after_state_hash": terminal["after_state_hash"],
    }
    checkpointed, _ = store.checkpoint_app_call_terminal(
        call_id=identity["call_id"],
        expected_turn_id="turn-1",
        expected_epoch_id="epoch-1",
        terminal_projection=projection,
        post_domain_checkpoint=_domain(
            candidate_revision=8,
            stage_revision=4,
            receipt_fingerprint=HASH_E,
        ),
        post_tool_manifest_hash=HASH_E,
        expected_activation_generation=before["activation_generation"],
        expected_checkpoint_binding=before["checkpoint_binding"],
    )
    assert checkpointed["domain_checkpoint"]["stage_revision"] == 4
    assert checkpointed["tool_manifest_hash"] == HASH_E


def test_failed_stage_tool_checkpoints_without_inventing_a_transition(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    store = NativeSessionStore(root)
    before = store.create(_record())
    identity = make_app_call_identity(
        conversation_id="conversation-1",
        turn_id="turn-1",
        step_id="step-1",
        epoch_id="epoch-1",
        document_id="document-1",
        call_id="app-call-stage-rejected",
        tool_name="gs_transition_phase",
        raw_arguments_hash=HASH_A,
        runtime_generation="dsh_v2",
        candidate_revision=7,
        stage_revision=3,
        database_fingerprint=HASH_A,
        tool_manifest_hash=HASH_D,
        catalog_hash=HASH_F,
        writer_fence=FENCE_OLD,
    )
    app_ledger = NativeAppCallLedger(root, conversation_id="conversation-1")
    app_ledger.begin(
        identity=identity,
        effect="stage_transition",
        before_state_hash=HASH_B,
    )
    terminal = app_ledger.settle(
        identity=identity,
        effect="stage_transition",
        state="settled_failure",
        after_state_hash=HASH_B,
        result={"code": 1, "reason": "agent_stage_incomplete"},
        terminal_reason="agent_stage_incomplete",
    )["entry"]
    projection = {
        "terminal_state": terminal["state"],
        "call_id": identity["call_id"],
        "effect": terminal["effect"],
        "result_hash": terminal["result_hash"],
        "before_state_hash": terminal["before_state_hash"],
        "after_state_hash": terminal["after_state_hash"],
    }

    checkpointed, saved = store.checkpoint_app_call_terminal(
        call_id=identity["call_id"],
        expected_turn_id="turn-1",
        expected_epoch_id="epoch-1",
        terminal_projection=projection,
        post_domain_checkpoint=_domain(),
        post_tool_manifest_hash=HASH_D,
        expected_activation_generation=before["activation_generation"],
        expected_checkpoint_binding=before["checkpoint_binding"],
    )

    assert saved["stage_transition"] is None
    assert checkpointed["domain_checkpoint"] == before["domain_checkpoint"]


def test_interrupted_generic_call_without_commit_proof_fails_closed(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    store = NativeSessionStore(root)
    record = _record()
    set_last_turn(
        record,
        turn_id="turn-1",
        status="idle",
        provider_request_possible=True,
        terminal_reason=None,
    )
    before = store.create(record)
    identity = make_app_call_identity(
        conversation_id="conversation-1",
        turn_id="turn-1",
        step_id="step-1",
        epoch_id="epoch-1",
        document_id="document-1",
        call_id="app-call-1",
        tool_name="gs_patch_candidate",
        raw_arguments_hash=HASH_A,
        runtime_generation="dsh_v2",
        candidate_revision=7,
        stage_revision=3,
        database_fingerprint=HASH_A,
        tool_manifest_hash=HASH_D,
        catalog_hash=HASH_F,
        writer_fence=FENCE_OLD,
    )
    app_ledger = NativeAppCallLedger(root, conversation_id="conversation-1")
    app_ledger.begin(
        identity=identity, effect="candidate", before_state_hash=HASH_B,
    )
    app_ledger.recover_inflight(after_state_hash=HASH_E)

    with pytest.raises(PlanError) as error:
        store.recover_interrupted_turn(
            observed_domain_checkpoint=_domain(candidate_revision=8),
            observed_tool_manifest_hash=HASH_D,
            observed_app_state_hash=HASH_E,
            expected_activation_generation=before["activation_generation"],
            expected_checkpoint_binding=before["checkpoint_binding"],
        )
    assert error.value.reason == "settlement_conflict"


def test_candidate_write_rejects_caller_reported_terminal_mapping():
    with pytest.raises(PlanError, match="调用方不能自报"):
        append_tool_settlement_ref(_record(), _ledger_terminal())
    compact = _settlement(
        effect="candidate_write", resource_refs=[],
        before_fingerprint=HASH_B, after_fingerprint=HASH_C,
    )
    with pytest.raises(PlanError, match="同根持久账本"):
        append_tool_settlement_ref(_record(), compact)


def test_create_and_plain_save_cannot_inject_candidate_write_refs(tmp_path):
    candidate_ref = _settlement(
        effect="candidate_write", resource_refs=[],
        before_fingerprint=HASH_B, after_fingerprint=HASH_C,
    )
    injected = _record()
    injected["tool_settlement_refs"].append(candidate_ref)
    store_module.refresh_checkpoint_binding(injected)
    new_root = tmp_path / "new"
    new_root.mkdir()
    with pytest.raises(PlanError) as create_error:
        NativeSessionStore(new_root).create(injected)
    assert create_error.value.reason == "stale_context"

    root = tmp_path / "existing"
    root.mkdir()
    store = NativeSessionStore(root)
    store.create(_record())
    current = store.cold_read()
    binding = current["checkpoint_binding"]
    current["tool_settlement_refs"].append(candidate_ref)
    store_module.refresh_checkpoint_binding(current)
    with pytest.raises(PlanError) as save_error:
        store.save(
            current,
            expected_activation_generation=0,
            expected_checkpoint_binding=binding,
        )
    assert save_error.value.reason == "stale_context"


@pytest.mark.parametrize("entry", [
    _ledger_terminal(extra="field"),
    _ledger_terminal(
        state="settled_failure",
        after_fingerprint=HASH_C,
        post_candidate_revision=7,
        result_json='{"code":1}',
        result_hash=store_module.fingerprint({"code": 1}),
        result_code=1,
        terminal_reason="1",
    ),
])
def test_same_root_candidate_ledger_rejects_forged_terminal_shape(tmp_path, entry):
    root = tmp_path / "session"
    root.mkdir()
    record = _record(domain_checkpoint=_domain(candidate_revision=8))
    store = NativeSessionStore(root)
    store.create(record)
    ledger = create_write_ledger("conversation-1")
    ledger["entries"]["write-call-1"] = entry
    write_owned(
        root, store_module.WRITE_LEDGER_FILE, ledger,
        store_module.MAX_WRITE_LEDGER_BYTES,
    )
    with pytest.raises(PlanError):
        store.append_candidate_write_settlement(
            call_id="write-call-1",
            expected_activation_generation=0,
            expected_checkpoint_binding=record["checkpoint_binding"],
        )


def test_candidate_write_terminal_must_match_current_post_context(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    record = _record()
    store = NativeSessionStore(root)
    store.create(record)
    future = _ledger_terminal(
        identity={
            "candidate_revision": 999,
            "stage_revision": 999,
            "runtime_generation": "foreign-runtime",
            "writer_fence": FENCE_NEW,
            "database_fingerprint": HASH_E,
            "tool_manifest_hash": HASH_F,
        },
        post_candidate_revision=1000,
        post_stage_revision=999,
        post_tool_manifest_hash=HASH_F,
    )
    _persist_write_ledger(root, future)
    with pytest.raises(PlanError) as error:
        store.append_candidate_write_settlement(
            call_id="write-call-1",
            expected_activation_generation=0,
            expected_checkpoint_binding=record["checkpoint_binding"],
        )
    assert error.value.reason == "stale_context"


def test_candidate_archive_missing_same_root_ledger_is_stable_error(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    record = _record(domain_checkpoint=_domain(candidate_revision=8))
    store = NativeSessionStore(root)
    store.create(record)
    with pytest.raises(PlanError) as error:
        store.append_candidate_write_settlement(
            call_id="write-call-1",
            expected_activation_generation=0,
            expected_checkpoint_binding=record["checkpoint_binding"],
        )
    assert error.value.reason == "unknown_settlement"
    assert error.value.__cause__ is None


def test_same_root_candidate_archive_preserves_all_three_terminal_states(tmp_path):
    entries = [
        _ledger_terminal(),
        _ledger_terminal(
            state="settled_failure",
            after_fingerprint=HASH_B,
            result_json='{"code":1}',
            result_hash=store_module.fingerprint({"code": 1}),
            result_code=1,
            terminal_reason="1",
            post_candidate_revision=7,
            post_stage_revision=3,
        ),
        _ledger_terminal(
            state="unknown_after_interruption",
            result_json=None,
            result_hash=None,
            result_code="outcome_unknown",
            terminal_reason="bridge_interrupted",
            post_candidate_revision=8,
            post_stage_revision=3,
        ),
    ]
    for index, entry in enumerate(entries):
        root = tmp_path / str(index)
        root.mkdir()
        post_revision = entry["post_candidate_revision"]
        record = _record(
            domain_checkpoint=_domain(candidate_revision=post_revision),
        )
        store = NativeSessionStore(root)
        store.create(record)
        _persist_write_ledger(root, entry)
        _, row = store.append_candidate_write_settlement(
            call_id="write-call-1",
            expected_activation_generation=0,
            expected_checkpoint_binding=record["checkpoint_binding"],
        )
        assert row["status"] == entry["state"]
        assert row["effect"] == "candidate_write"


def test_candidate_archive_retry_after_publication_ack_loss_is_idempotent(
    tmp_path, monkeypatch,
):
    root = tmp_path / "session"
    root.mkdir()
    record = _record(domain_checkpoint=_domain(candidate_revision=8))
    store = NativeSessionStore(root)
    store.create(record)
    _persist_write_ledger(root, _ledger_terminal())
    original_write = store_module.write_owned
    lost = False

    def commit_then_lose_ack(root_arg, name, value, maximum):
        nonlocal lost
        result = original_write(root_arg, name, value, maximum)
        if name == store_module.SESSION_FILE and not lost:
            lost = True
            raise PlanError("candidate archive ack lost", "file_conflict")
        return result

    monkeypatch.setattr(store_module, "write_owned", commit_then_lose_ack)
    with pytest.raises(PlanError, match="ack lost"):
        store.append_candidate_write_settlement(
            call_id="write-call-1",
            expected_activation_generation=0,
            expected_checkpoint_binding=record["checkpoint_binding"],
        )
    monkeypatch.setattr(store_module, "write_owned", original_write)
    replayed, row = store.append_candidate_write_settlement(
        call_id="write-call-1",
        expected_activation_generation=0,
        expected_checkpoint_binding=record["checkpoint_binding"],
    )
    assert row["call_id"] == "write-call-1"
    assert replayed == store.cold_read()
    assert len(replayed["tool_settlement_refs"]) == 1


def test_resume_seed_hides_writer_capability_and_filters_bounded_current_stage():
    record = _record()
    append_legacy_visible_message(
        record, message_id="message-old", role="assistant", text="old",
        workflow_stage="action_draft", user_turn=1,
    )
    append_tool_settlement_ref(
        record,
        _settlement(
            call_id="old-call", workflow_stage="action_draft",
            resource_refs=["old-resource"],
        ),
    )
    append_legacy_visible_message(
        record, message_id="message-current", role="assistant", text="current",
        workflow_stage="event_topology", user_turn=2,
    )
    for index in range(store_module.MAX_RESUME_SETTLEMENTS + 6):
        append_tool_settlement_ref(
            record,
            _settlement(
                call_id=f"current-call-{index}",
                before_fingerprint=HASH_A,
                after_fingerprint=HASH_A,
                workflow_stage="event_topology",
                resource_refs=[f"resource-{index}-a", f"resource-{index}-b"],
            ),
        )
    seed = resume_seed(record)
    encoded = json.dumps(seed, ensure_ascii=False)
    assert "writer_fence" not in encoded
    assert FENCE_OLD not in encoded
    assert "checkpoint_binding" not in seed
    assert seed["workflow_stage"] == "event_topology"
    assert len(seed["tool_settlement_refs"]) == store_module.MAX_RESUME_SETTLEMENTS
    assert all(row["workflow_stage"] == "event_topology"
               for row in seed["tool_settlement_refs"])
    assert (
        sum(len(row["resource_refs"]) for row in seed["tool_settlement_refs"])
        <= store_module.MAX_RESUME_RESOURCE_REFS
    )
    assert "old-call" not in encoded
    assert "old-resource" not in encoded
    prior_stage = resume_seed(record, workflow_stage="action_draft")
    assert prior_stage["workflow_stage"] == "action_draft"
    assert [row["call_id"] for row in prior_stage["tool_settlement_refs"]] == [
        "old-call",
    ]


def test_pending_takeover_blocks_activation_assessment_with_stable_issue():
    record = _record()
    _set_pending_takeover(record)
    result = assess_activation(record, _observed())
    assert result["status"] == "blocked"
    assert result["writer_takeover_pending"] is True
    assert result["issues"] == [
        {"code": "writer_takeover_pending", "field": "writer_takeover"},
    ]
    with pytest.raises(PlanError) as error:
        resume_seed(record)
    assert error.value.reason == "stale_context"


@pytest.mark.parametrize("secret", [
    r"\Users\alice\private.txt",
    r"\\server\share\private.txt",
    "//server/share/private.txt",
    "api key = abcdefghijk",
    "X-API-Key: abcdefghijk",
    "password='correct horse battery staple'",
    "passwd: hunter2",
    "Cookie: session=abcdef; theme=dark",
    "Set-Cookie: session=abcdef; HttpOnly",
    FENCE_OLD,
    r"C:\Users\Doe, John\private.txt",
    r"/home/Doe, John/private.txt",
    r"C:\Users\Alice;Archive\private.txt",
])
def test_redaction_covers_absolute_backslash_unc_and_common_credentials(secret):
    redacted = store_module.redact_visible_text(f"before {secret} after")
    assert secret not in redacted
    assert "[已隐藏" in redacted


def test_redaction_preserves_https_urls_and_non_path_backslash_escapes():
    text = r"https://example.com/a/b and markdown \*escape"
    assert store_module.redact_visible_text(text) == text


def test_cold_read_never_creates_a_lock_file(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    store = NativeSessionStore(root)
    store_module.write_owned(
        root, store_module.SESSION_FILE, _record(), store_module.MAX_SESSION_BYTES,
    )
    assert not store.lock_path.exists()
    assert store.cold_read()["conversation_id"] == "conversation-1"
    assert not store.lock_path.exists()

    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    empty_store = NativeSessionStore(empty_root)
    with pytest.raises(FileNotFoundError):
        empty_store.cold_read()
    assert not empty_store.lock_path.exists()


def test_create_establishes_lock_and_cold_read_does_not_rewrite_it(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    store = NativeSessionStore(root)
    store.create(_record())
    before = store.lock_path.read_bytes()
    stat = store.lock_path.stat()
    store.cold_read()
    assert store.lock_path.read_bytes() == before
    assert store.lock_path.stat().st_mtime_ns == stat.st_mtime_ns


def test_cold_read_joins_an_existing_writer_lock_without_mutating_it(tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    store = NativeSessionStore(root)
    store.create(_record())
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    failures = []

    def hold_lock():
        with store_module.file_lock(store.lock_path):
            entered.set()
            release.wait(2)

    def read_locked():
        try:
            store.cold_read()
        except (OSError, PlanError) as exc:  # pragma: no cover - asserted below
            failures.append(exc)
        finally:
            finished.set()

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert entered.wait(1)
    reader = threading.Thread(target=read_locked)
    reader.start()
    assert not finished.wait(0.05)
    release.set()
    holder.join(2)
    reader.join(2)
    assert finished.is_set()
    assert failures == []


def test_entry_points_detach_caller_owned_nested_values(tmp_path, monkeypatch):
    domain = _domain()
    record = create_native_session(
        conversation_id="conversation-1",
        connection_kind="native_api",
        runtime_generation="dsh_v2",
        locked_provider="deepseek",
        locked_model="deepseek-chat",
        locked_reasoning_effort="high",
        domain_checkpoint=domain,
        prompt_profile_hash=HASH_C,
        tool_manifest_hash=HASH_D,
    )
    domain["candidate_revision"] = 999
    assert record["domain_checkpoint"]["candidate_revision"] == 7

    settlement = _settlement(resource_refs=["resource-1"])
    append_tool_settlement_ref(record, settlement)
    settlement["resource_refs"].append("resource-attacker")
    assert record["tool_settlement_refs"][0]["resource_refs"] == ["resource-1"]

    root = tmp_path / "session"
    root.mkdir()
    native_store = NativeSessionStore(root)
    original_write = store_module.write_owned

    def mutate_create_input(root_arg, name, value, maximum):
        record["locked_model"] = "attacker-model"
        return original_write(root_arg, name, value, maximum)

    monkeypatch.setattr(store_module, "write_owned", mutate_create_input)
    native_store.create(record)
    assert native_store.cold_read()["locked_model"] == "deepseek-chat"

    current = native_store.cold_read()
    binding = current["checkpoint_binding"]
    append_legacy_visible_message(
        current, message_id="message-save", role="assistant", text="safe",
        workflow_stage="action_draft", user_turn=2,
    )

    def mutate_save_input(root_arg, name, value, maximum):
        current["visible_messages"][-1]["text"] = "attacker rewrite"
        return original_write(root_arg, name, value, maximum)

    monkeypatch.setattr(store_module, "write_owned", mutate_save_input)
    native_store.save(
        current,
        expected_activation_generation=0,
        expected_checkpoint_binding=binding,
    )
    assert native_store.cold_read()["visible_messages"][-1]["text"] == "safe"


def test_create_retry_after_committed_ack_loss_is_idempotent(tmp_path, monkeypatch):
    root = tmp_path / "session"
    root.mkdir()
    native_store = NativeSessionStore(root)
    record = _record()
    original_write = store_module.write_owned
    lost = False

    def commit_then_lose_ack(root_arg, name, value, maximum):
        nonlocal lost
        result = original_write(root_arg, name, value, maximum)
        if name == store_module.SESSION_FILE and not lost:
            lost = True
            raise PlanError("create ack lost", "file_conflict")
        return result

    monkeypatch.setattr(store_module, "write_owned", commit_then_lose_ack)
    with pytest.raises(PlanError, match="ack lost"):
        native_store.create(record)
    monkeypatch.setattr(store_module, "write_owned", original_write)
    assert native_store.create(record) == record

    conflict = copy.deepcopy(record)
    conflict["locked_model"] = "deepseek-reasoner"
    store_module.refresh_checkpoint_binding(conflict)
    with pytest.raises(PlanError) as error:
        native_store.create(conflict)
    assert error.value.reason == "file_conflict"


def test_save_retry_after_committed_ack_loss_is_idempotent(tmp_path, monkeypatch):
    root = tmp_path / "session"
    root.mkdir()
    native_store = NativeSessionStore(root)
    native_store.create(_record())
    proposed = native_store.cold_read()
    binding = proposed["checkpoint_binding"]
    append_legacy_visible_message(
        proposed, message_id="message-1", role="assistant", text="saved",
        workflow_stage="action_draft", user_turn=1,
    )
    original_write = store_module.write_owned
    lost = False

    def commit_then_lose_ack(root_arg, name, value, maximum):
        nonlocal lost
        result = original_write(root_arg, name, value, maximum)
        if name == store_module.SESSION_FILE and not lost:
            lost = True
            raise PlanError("save ack lost", "file_conflict")
        return result

    monkeypatch.setattr(store_module, "write_owned", commit_then_lose_ack)
    with pytest.raises(PlanError, match="ack lost"):
        native_store.save(
            proposed,
            expected_activation_generation=0,
            expected_checkpoint_binding=binding,
        )
    monkeypatch.setattr(store_module, "write_owned", original_write)
    assert native_store.save(
        proposed,
        expected_activation_generation=0,
        expected_checkpoint_binding=binding,
    ) == proposed


def test_writer_final_publication_ack_loss_replays_exact_persisted_proposal(
    tmp_path, monkeypatch,
):
    root = tmp_path / "session"
    root.mkdir()
    native_store = NativeSessionStore(root)
    native_store.create(_record())
    original_write = store_module.write_owned
    writes = 0

    def commit_final_then_lose_ack(root_arg, name, value, maximum):
        nonlocal writes
        result = original_write(root_arg, name, value, maximum)
        if name == store_module.SESSION_FILE:
            writes += 1
            if writes == 2:
                raise PlanError("takeover ack lost", "file_conflict")
        return result

    monkeypatch.setattr(store_module, "write_owned", commit_final_then_lose_ack)
    with pytest.raises(PlanError, match="ack lost"):
        native_store.activate_writer(
            _Session(root), proposed_fence=FENCE_NEW,
            expected_activation_generation=0,
        )
    committed = native_store.cold_read()
    assert committed["writer_takeover"] is None
    assert committed["writer_takeover_receipt"]["proposed_fence"] == FENCE_NEW

    monkeypatch.setattr(store_module, "write_owned", original_write)
    replayed, authority = native_store.activate_writer(
        _Session(root), proposed_fence=FENCE_NEW,
        expected_activation_generation=0,
    )
    assert replayed == committed
    assert authority.current_fence() == FENCE_NEW
    with pytest.raises(PlanError):
        native_store.activate_writer(
            _Session(root), proposed_fence=FENCE_NEW,
            expected_activation_generation=1,
        )


def test_pending_takeover_rejects_nonexact_same_fence_retry(tmp_path, monkeypatch):
    root = tmp_path / "session"
    root.mkdir()
    native_store = NativeSessionStore(root)
    native_store.create(_record())
    original_write = store_module.write_owned
    writes = 0

    def stop_after_pending(root_arg, name, value, maximum):
        nonlocal writes
        if name == store_module.SESSION_FILE:
            writes += 1
            if writes == 2:
                raise PlanError("stop before final", "file_conflict")
        return original_write(root_arg, name, value, maximum)

    monkeypatch.setattr(store_module, "write_owned", stop_after_pending)
    with pytest.raises(PlanError, match="stop before final"):
        native_store.activate_writer(
            _Session(root), proposed_fence=FENCE_NEW,
            expected_activation_generation=0,
        )
    monkeypatch.setattr(store_module, "write_owned", original_write)
    with pytest.raises(PlanError) as error:
        native_store.activate_writer(
            _Session(root), proposed_fence=FENCE_NEW,
            expected_activation_generation=1,
        )
    assert error.value.reason == "stale_context"
    recovered, _ = native_store.activate_writer(
        _Session(root), proposed_fence=FENCE_NEW,
        expected_activation_generation=0,
    )
    assert recovered["domain_checkpoint"]["writer_fence"] == FENCE_NEW
