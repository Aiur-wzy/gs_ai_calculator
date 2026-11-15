"""Native action intent resolves through its declared ability, never the first actor."""
from __future__ import annotations

import copy
import uuid

import pytest

from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.agent_chat.candidate_input_draft import build_draft
from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry
from genshin_app.agent_chat.native_dsh_workspace_compatibility import (
    current_workspace_identity,
)
from genshin_app.equipment.files import fingerprint
from tests.test_calculation_delivery import delivery_plan
from tests.test_queries import db_path as db_path  # noqa: PLC0414


def native_actions():
    plan = delivery_plan()
    plan.update(plan_version=3, mechanics_model=None)
    plan["manual_template"]["characters"].append({"id": "b", "name": "second actor", "level": 90, "stats": {}})
    plan["rotation_model"] = {
        "schema_version": 1, "period": 12,
        "characters": [{"id": "a"}, {"id": "b"}],
        "abilities": [
            {"id": "ability.a", "character": "a", "action_type_id": "action.elemental_skill", "variant": "tap"},
            {"id": "ability.b", "character": "b", "action_type_id": "action.charged_attack", "variant": "hold"},
        ],
        "actions": [
            {"id": "action.b", "ability_id": "ability.b", "start": 3.25, "order": 7, "repeat": {"mode": "once"}},
            {"id": "action.a", "ability_id": "ability.a", "start": None, "order": -2, "repeat": {"mode": "once"}},
        ],
        "events": [],
    }
    return plan


def make_session(tmp_path, plan=None, db=None):
    return ArtifactCandidateSession(str(uuid.uuid4()), tmp_path / "session", db or tmp_path / "absent.db", "native", plan or native_actions())


def test_native_document_initialization_projects_actual_ability_owner_type_variant_and_times(tmp_path):
    session = make_session(tmp_path)
    view = session._current_board()
    assert view["duration"] == 12  # actual rotation period, not template's 2s
    assert view["actions"] == [
        {"id": "action.b", "character": "b", "action_type_id": "action.charged_attack", "start": 3.25, "variant": "hold", "order": 7},
        {"id": "action.a", "character": "a", "action_type_id": "action.elemental_skill", "start": None, "variant": "tap", "order": -2},
    ]
    checkpoint = session._current_action_checkpoint()
    assert checkpoint["action_ids"] == ["action.b", "action.a"]
    assert session.candidate["extensions"]["event_topology"]["action_checkpoint"] == checkpoint


@pytest.mark.parametrize("unknown", ["null", "missing"])
def test_unknown_native_period_and_start_remain_unknown(tmp_path, unknown):
    plan = native_actions()
    if unknown == "null":
        plan["rotation_model"]["period"] = None
    else:
        plan["rotation_model"].pop("period")
        plan["rotation_model"]["actions"][1].pop("start")
    session = make_session(tmp_path, plan)
    assert session._current_board()["duration"] is None
    assert session._current_board()["actions"][1]["start"] is None


def test_native_action_checkpoint_changes_for_ability_metadata_and_actual_order(tmp_path):
    session = make_session(tmp_path)
    before = session._current_action_checkpoint()["fingerprint"]
    changed = copy.deepcopy(session.candidate)
    changed["rotation_model"]["abilities"][1]["variant"] = "new-hold"
    assert session._current_action_checkpoint(changed)["fingerprint"] != before
    changed = copy.deepcopy(session.candidate)
    changed["rotation_model"]["actions"][0]["order"] += 1
    assert session._current_action_checkpoint(changed)["fingerprint"] != before


@pytest.mark.parametrize("broken", ["missing-ability", "duplicate-ability", "missing-owner"])
def test_unresolvable_native_relationships_have_structured_errors_not_guessed_actions(tmp_path, broken):
    plan = native_actions()
    if broken == "missing-ability":
        plan["rotation_model"]["actions"][0]["ability_id"] = "missing"
    elif broken == "duplicate-ability":
        plan["rotation_model"]["abilities"].append(copy.deepcopy(plan["rotation_model"]["abilities"][1]))
    else:
        plan["rotation_model"]["abilities"][1]["character"] = "not-in-team"
    session = make_session(tmp_path, plan)
    view = session._current_board()
    assert view["issues"][0]["path"].startswith("rotation_model.")
    assert view["actions"][0]["character"] is None
    assert session.summary()["input_issues"]
    assert session.summary()["saveable"] is True
    assert session.summary()["applicable"] is False


def test_native_document_cold_restore_retains_action_checkpoint(tmp_path):
    session = make_session(tmp_path)
    expected = session._current_action_checkpoint()
    summary = session.summary()
    session.close()
    restored = CandidateSession.load(session.session_id, session.root, session.db_path)
    assert type(restored) is ArtifactCandidateSession
    assert restored._current_action_checkpoint() == expected
    assert restored.summary() == summary


def test_new_native_input_draft_import_and_cold_registry_restore_use_same_projection(tmp_path, db_path):
    source = make_session(tmp_path / "source", db=db_path)
    draft = build_draft(source, workspace_identity=current_workspace_identity(), database_release_id="fixture-db",
                        producer={"app_version": "fixture", "app_release_id": "fixture"})
    root = tmp_path / "target"
    registry = NativeDshWorkspaceRegistry(root, database_path=db_path)
    try:
        result = registry.prepare_candidate_input_draft("new-native-draft", draft)
        assert result["imported"] is True
        scope = registry.activate("new-native-draft")
        expected = scope.candidate_session._current_action_checkpoint()
        assert scope.candidate_session._current_board()["actions"][0]["character"] == "b"
        assert scope.candidate_session.calculated is None
        candidate_hash = fingerprint(scope.candidate_session.candidate)
    finally:
        registry.close()
    reopened = NativeDshWorkspaceRegistry(root, database_path=db_path)
    try:
        restored = reopened.activate("new-native-draft").candidate_session
        assert restored._current_action_checkpoint() == expected
        assert fingerprint(restored.candidate) == candidate_hash
        assert restored.calculated is None
    finally:
        reopened.close()
