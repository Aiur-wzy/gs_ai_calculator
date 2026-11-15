"""Real candidate intents: explicit recovery abandonment never claims an ACK."""
from __future__ import annotations

import copy
import json

import pytest

from genshin_app.agent_chat import native_dsh_workspace_compatibility as compatibility
from genshin_app.agent_chat.native_candidate_transaction_contracts import (
    APPLICATION_INTENT_FILE,
)
from genshin_app.agent_chat.native_dsh_mux_client import NativeDshMuxError
from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry
from genshin_app.agent_chat.native_incompatible_application_retirements import (
    NativeIncompatibleApplicationRetirements,
)
from tests.test_native_dsh_candidate_application import command, ready_registry
from tests.test_queries import db_path as db_path  # noqa: PLC0414


@pytest.fixture
def identity(monkeypatch):
    value = copy.deepcopy(compatibility.current_workspace_identity())
    monkeypatch.setattr(compatibility, "current_native_compatibility_identity", lambda: value)
    return value


def review_args(registry, sid):
    scope = registry.activate(sid)
    baseline = copy.deepcopy(scope.candidate_session.document)
    base = {"kind": "native", "expected_revision": 0,
        "expected_receipt": scope.candidate_session.summary()["receipt_fingerprint"]}
    target = command(registry, sid, "candidate.review", {**base, "document": baseline})["result"]["plan"]
    return {**base, "document": target, "baseline_payload": {"kind": "native", "plan": baseline},
        "target_payload": {"kind": "native", "plan": target}}


def prepared(tmp_path, db_path, state="prepared"):
    registry, scope = ready_registry(tmp_path, db_path)
    args = review_args(registry, "one")
    assert command(registry, "one", "candidate.apply.prepare", args)["result"]["status"] == "prepared"
    if state != "prepared":
        store = registry.application_backend._read_application_intent(scope.session_root, scope.conversation_id)
        store["intent"]["state"] = state
        registry.application_backend._write_application_intent(scope.session_root, store)
    return registry, scope, args


@pytest.mark.parametrize("state", ["prepared", "baseline_advanced"])
def test_explicit_exact_abandonment_releases_new_candidate_without_rewriting_old_intent(tmp_path, db_path, identity, state):
    registry, old, _ = prepared(tmp_path, db_path, state)
    repo = registry.application_backend.incompatible_applications_repository
    old_path = old.session_root / APPLICATION_INTENT_FILE
    before = old_path.read_bytes()
    identity["skill_bundle_hash"] = "0" * 64
    registry.prepare_document("new", "native", old.candidate_session.document)
    registry.activate("new").candidate_session.calculate(0)
    args = review_args(registry, "new")
    with pytest.raises(Exception, match="native_candidate_application_global_busy"):
        command(registry, "new", "candidate.apply.prepare", args)
    item, = repo.list_pending()["items"]
    assert item["conversation_id"] == old.conversation_id and item["state"] == "result_unknown"
    with pytest.raises(NativeDshMuxError, match="decision_invalid"):
        repo.abandon(old.conversation_id, item["expected_fingerprint"], "reject")
    assert registry.application_backend._pending_application_owner()[0] == old.conversation_id
    result = repo.abandon(old.conversation_id, item["expected_fingerprint"], "abandon_recovery")
    assert result["status"] == "recovery_abandoned" and result["state"] == "result_unknown"
    assert result["disposition"] == "recorded"
    assert repo.abandon(old.conversation_id, item["expected_fingerprint"], "abandon_recovery")["disposition"] == "replay"
    assert not repo.list_pending()["items"]
    assert command(registry, "new", "candidate.apply.prepare", args)["result"]["status"] == "prepared"
    assert old_path.read_bytes() == before
    with pytest.raises(NativeDshMuxError, match="compatibility_changed"):
        registry.domain_projection("one")
    registry.close()
    cold = NativeDshWorkspaceRegistry(tmp_path, database_path=db_path, workflow="calculate")
    assert cold.application_backend.incompatible_applications_repository.is_retired(old.session_root,
        cold.application_backend._read_application_intent(old.session_root, old.conversation_id)["intent"])
    assert old_path.read_bytes() == before
    cold.close()


@pytest.mark.parametrize("change", ["workspace", "intent", "current_identity"])
def test_changed_evidence_rejects_stale_user_decision(tmp_path, db_path, identity, change):
    registry, scope, _ = prepared(tmp_path, db_path)
    repo = registry.application_backend.incompatible_applications_repository
    identity["skill_bundle_hash"] = "0" * 64
    item, = repo.list_pending()["items"]
    if change == "current_identity":
        identity["skill_bundle_hash"] = "1" * 64
    else:
        path = scope.session_root / ("dsh-workspace.json" if change == "workspace" else APPLICATION_INTENT_FILE)
        path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(NativeDshMuxError, match="stale"):
        repo.abandon(scope.conversation_id, item["expected_fingerprint"], "abandon_recovery")
    assert registry.application_backend._pending_application_owner()[0] == scope.conversation_id
    assert not repo.journal_root.exists()
    registry.close()


def test_same_version_and_reverted_compatibility_cannot_borrow_abandonment(tmp_path, db_path, identity):
    registry, scope, _ = prepared(tmp_path, db_path)
    repo = registry.application_backend.incompatible_applications_repository
    original = copy.deepcopy(identity)
    assert repo.list_pending() == {"items": []}
    with pytest.raises(NativeDshMuxError, match="not_eligible"):
        repo.abandon(scope.conversation_id, "0" * 64, "abandon_recovery")
    identity["skill_bundle_hash"] = "0" * 64
    item, = repo.list_pending()["items"]
    repo.abandon(scope.conversation_id, item["expected_fingerprint"], "abandon_recovery")
    identity.clear()
    identity.update(original)
    intent = registry.application_backend._read_application_intent(scope.session_root, scope.conversation_id)["intent"]
    assert repo.is_retired(scope.session_root, intent) is False
    assert registry.application_backend._pending_application_owner()[0] == scope.conversation_id
    registry.close()


def test_strict_legacy_missing_identity_can_only_drop_recovery_not_gain_current_identity(tmp_path, db_path, identity):
    registry, scope, _ = prepared(tmp_path, db_path)
    path = scope.session_root / "dsh-workspace.json"
    row = json.loads(path.read_bytes())
    row["contract"] = "genshin-native-dsh-workspace-binding-v1"
    del row["compatibility_identity"]
    path.write_text(json.dumps(row), encoding="utf-8")
    before = {name: (scope.session_root / name).read_bytes() for name in ("dsh-workspace.json", APPLICATION_INTENT_FILE)}
    repo = registry.application_backend.incompatible_applications_repository
    item, = repo.list_pending()["items"]
    repo.abandon(scope.conversation_id, item["expected_fingerprint"], "abandon_recovery")
    assert registry.application_backend._pending_application_owner() is None
    assert all((scope.session_root / name).read_bytes() == raw for name, raw in before.items())
    with pytest.raises(NativeDshMuxError, match="compatibility_missing"):
        registry.activate("one")
    registry.close()


@pytest.mark.parametrize("source", ["workspace", "intent"])
def test_unknown_or_damaged_schemas_cannot_be_abandoned(tmp_path, db_path, identity, source):
    registry, scope, _ = prepared(tmp_path, db_path)
    identity["skill_bundle_hash"] = "0" * 64
    path = scope.session_root / ("dsh-workspace.json" if source == "workspace" else APPLICATION_INTENT_FILE)
    row = json.loads(path.read_bytes())
    row["contract"] = "unknown-schema"
    path.write_text(json.dumps(row), encoding="utf-8")
    repo = registry.application_backend.incompatible_applications_repository
    with pytest.raises(Exception, match="invalid"):
        repo.list_pending()
    with pytest.raises(Exception, match="invalid"):
        repo.abandon(scope.conversation_id, "0" * 64, "abandon_recovery")
    assert not repo.journal_root.exists()
    registry.close()


def test_multiple_historical_occupants_require_individual_new_version_decisions(tmp_path, db_path, identity):
    registry, first, _ = prepared(tmp_path, db_path)
    repo = registry.application_backend.incompatible_applications_repository
    identity["skill_bundle_hash"] = "0" * 64
    item, = repo.list_pending()["items"]
    repo.abandon(first.conversation_id, item["expected_fingerprint"], "abandon_recovery")
    registry.prepare_document("two", "native", first.candidate_session.document)
    second = registry.activate("two")
    second.candidate_session.calculate(0)
    command(registry, "two", "candidate.apply.prepare", review_args(registry, "two"))
    identity["skill_bundle_hash"] = "1" * 64
    # A source change invalidates only that exact old retirement. Without
    # this change a later App version must not revive the first occupant.
    first_path = first.session_root / APPLICATION_INTENT_FILE
    first_path.write_bytes(first_path.read_bytes() + b"\n")
    items = repo.list_pending()["items"]
    assert {item["conversation_id"] for item in items} == {first.conversation_id, second.conversation_id}
    with pytest.raises(Exception, match="global_invalid"):
        registry.application_backend._pending_application_owner()
    repo.abandon(items[0]["conversation_id"], items[0]["expected_fingerprint"], "abandon_recovery")
    assert registry.application_backend._pending_application_owner() is not None
    repo.abandon(items[1]["conversation_id"], items[1]["expected_fingerprint"], "abandon_recovery")
    assert registry.application_backend._pending_application_owner() is None
    registry.close()


def test_later_app_version_does_not_revive_exact_abandoned_recovery(tmp_path, db_path, identity):
    registry, scope, _ = prepared(tmp_path, db_path)
    repo = registry.application_backend.incompatible_applications_repository
    identity["skill_bundle_hash"] = "0" * 64
    item, = repo.list_pending()["items"]
    result = repo.abandon(scope.conversation_id, item["expected_fingerprint"], "abandon_recovery")
    journal = (repo.journal_root / "decisions.json").read_bytes()
    identity["native_shell_release"] = {**identity["native_shell_release"], "state": "pinned", "manifest_sha256": "1" * 64}
    identity["skill_bundle_hash"] = "2" * 64
    assert repo.list_pending() == {"items": []}
    assert registry.application_backend._pending_application_owner() is None
    assert (repo.journal_root / "decisions.json").read_bytes() == journal
    assert result["state"] == "result_unknown"
    registry.close()


def test_compatible_fast_path_does_not_read_raw_intent_or_create_decision_journal(tmp_path, db_path, identity, monkeypatch):
    registry, scope, _ = prepared(tmp_path, db_path)
    repo = registry.application_backend.incompatible_applications_repository
    monkeypatch.setattr(repo, "_raw", lambda *_: pytest.fail("compatible path must not reread/hash raw intent"))
    assert repo.list_pending() == {"items": []}
    assert registry.application_backend._pending_application_owner()[0] == scope.conversation_id
    assert not repo.journal_root.exists()
    registry.close()


def test_fixture_without_trusted_workspace_validator_keeps_original_pending_gate(tmp_path, db_path, identity):
    registry, scope, _ = prepared(tmp_path, db_path)
    repo = NativeIncompatibleApplicationRetirements(tmp_path,
        intent_reader=registry.application_backend._read_application_intent)
    identity["skill_bundle_hash"] = "0" * 64
    intent = registry.application_backend._read_application_intent(scope.session_root, scope.conversation_id)["intent"]
    assert repo.list_pending() == {"items": []}
    assert not repo.is_retired(scope.session_root, intent)
    with pytest.raises(NativeDshMuxError, match="not_eligible"):
        repo.abandon(scope.conversation_id, "0" * 64, "abandon_recovery")
    assert not repo.journal_root.exists()
    registry.close()
