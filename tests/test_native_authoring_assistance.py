from __future__ import annotations

import copy
import uuid

import pytest

from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.agent_chat.native_app_tool_host import (
    _derived_view,
    _durable_candidate_view,
)
from genshin_app.agent_chat.tools import definitions_for_session
from genshin_app.authoring_assistance import (
    authoring_assistance,
    authoring_request_schema,
)
from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.whiteboard import default_panel, new_whiteboard
from genshin_db import queries
from tests.test_calculation_delivery import delivery_plan


def make(tmp_path, board=False):
    return ArtifactCandidateSession(str(uuid.uuid4()), tmp_path / "candidate", tmp_path / "absent.db",
                                    "whiteboard" if board else "native", new_whiteboard() if board else delivery_plan())


def test_native_and_helper_share_catalog_without_mutation(tmp_path):
    current = make(tmp_path)
    request = {"kind": "catalog", "version": 1, "request_id": "catalog", "expected_revision": 0}
    before = copy.deepcopy((_derived_view(current), _durable_candidate_view(current)))
    response = current.call_tool("gs_validate_agent_materialization", {"expected_revision": 0, "request": request})
    assert response["code"] == 0, response
    assert response["data"] == {**authoring_assistance(request, plan=current.candidate), "candidate_revision": 0}
    assert before == (_derived_view(current), _durable_candidate_view(current))
    schema = next(row for row in definitions_for_session(current) if row["name"] == "gs_validate_agent_materialization")["inputSchema"]
    assert schema["properties"]["request"] == authoring_request_schema()


def test_initial_whiteboard_can_discover_authoring_without_converting(tmp_path):
    current = make(tmp_path, board=True)
    request = {"kind": "catalog", "version": 1, "request_id": "catalog", "expected_revision": 0}
    response = current.call_tool("gs_validate_agent_materialization", {"expected_revision": 0, "request": request})
    assert response["code"] == 0, response
    assert current._is_board() and current.revision == 0
    assert response["data"]["plan_modified"] is False
    assert current.call_tool("gs_validate_agent_materialization", {"expected_revision": 0})["reason"] == "agent_native_artifact_required"


def test_nested_expected_revision_cannot_create_a_stale_draft(tmp_path):
    current = make(tmp_path)
    request = {"kind": "catalog", "version": 1, "request_id": "catalog", "expected_revision": 1}
    result = current.call_tool("gs_validate_agent_materialization", {"expected_revision": 0, "request": request})
    assert result["reason"] == "agent_stale_revision"
    assert result["details"] == {"path": "/request/expected_revision", "actual": 1, "expected": 0}
    assert current.revision == 0


@pytest.fixture
def converted(tmp_path, monkeypatch):
    """Exercise the real conversion that separates session and file revisions."""
    monkeypatch.setattr(queries, "get_character_detail", lambda *a, **kw: {
        "character": {"id": 1, "name": "工程角色", "weapon_type": "WEAPON_SWORD_ONE_HAND"}})
    current = make(tmp_path, board=True)
    actor = {"id": "a", "db_character_id": 1, "name": "工程角色", "level": 90, "constellation": 0,
             "weapon": None, "artifact_sets": [], "panel": default_panel(),
             "panel_basis_mode": "manual", "notes": ""}
    action = {"id": "a.e", "character": "a", "action_type_id": "action.elemental_skill",
              "label": "工程技能", "start": 0, "variant": "", "notes": ""}
    added = current.call_tool("gs_patch_whiteboard", {"expected_revision": 0, "reason": "工程测试动作", "operations": [
        {"op": "add", "path": "/team/-", "value": actor}, {"op": "add", "path": "/actions/-", "value": action}]})
    assert added["code"] == 0, added
    prepared = current.call_tool("gs_transition_phase", {
        "target_phase": "numeric_model", "expected_revision": current.revision,
        "expected_stage_revision": current.stage_state["stage_revision"], "reason": "准备原生输入",
        "prepare_calculation": True})
    assert prepared["code"] == 0, prepared
    assert current.revision != current.candidate["revision"]
    return current


def test_catalog_after_real_handoff_preserves_native_identity(converted):
    current = converted
    before = copy.deepcopy(current.candidate)
    revision = current.revision
    response = current.call_tool("gs_validate_agent_materialization", {"expected_revision": revision,
        "request": {"kind": "catalog", "version": 1, "request_id": "catalog", "expected_revision": revision}})
    assert response["code"] == 0, response
    assert response["data"]["candidate_revision"] == revision
    assert response["data"]["input_identity"]["revision"] == before["revision"]
    assert response["data"]["input_identity"]["input_fingerprint"] == fingerprint(input_view(before))
    assert current.candidate == before and current.revision == revision
    # The existing no-request validator also uses the session CAS. A missing
    # materialization is an input issue, not a fabricated stale session.
    result = current.call_tool("gs_validate_agent_materialization", {"expected_revision": revision})
    assert result["reason"] == "agent_materialization_required"


@pytest.mark.parametrize("kind", ["mechanics_fragment", "batch"])
def test_generated_edit_after_handoff_is_adoptable_and_stale_safe(converted, kind):
    current = converted
    before, revision = copy.deepcopy(current.candidate), current.revision
    payload = {"mechanics_model": {"schema_version": 1, "rules": [], "hit_events": [], "hit_patterns": [],
                                   "condition_events": [], "initial_state": {"rules": []}}}
    if kind == "batch":
        payload = {"steps": [{"id": "model", "kind": "mechanics_fragment", "payload": payload}]}
    request = {"kind": kind, "version": 1, "request_id": "fragment", "expected_revision": revision,
               "expected_input_fingerprint": fingerprint(input_view(before)), "payload": payload}
    response = current.call_tool("gs_validate_agent_materialization", {"expected_revision": revision, "request": request})
    assert response["code"] == 0, response
    edit = response["data"]["edit_request"]
    assert edit["expected_revision"] == revision
    assert edit["expected_input_fingerprint"] == fingerprint(input_view(before))
    assert current.candidate == before and current.revision == revision
    adopted = current.call_tool("gs_patch_candidate", edit)
    assert adopted["code"] == 0, adopted
    assert current.candidate["mechanics_model"] is not None
    assert current.revision == revision + 1
    assert current.call_tool("gs_patch_candidate", edit)["reason"] == "agent_stale_revision"
    assert current.call_tool("gs_validate_agent_materialization", {
        "expected_revision": revision, "request": request})["reason"] == "agent_stale_revision"


def test_handoff_does_not_accept_file_revision_or_stale_fingerprint(converted):
    current = converted
    before = copy.deepcopy(current.candidate)
    request = {"kind": "catalog", "version": 1, "request_id": "catalog", "expected_revision": current.revision}
    for outer, inner in [(before["revision"], current.revision), (current.revision, before["revision"])]:
        result = current.call_tool("gs_validate_agent_materialization", {
            "expected_revision": outer, "request": {**request, "expected_revision": inner}})
        assert result["reason"] == "agent_stale_revision"
    result = current.call_tool("gs_validate_agent_materialization", {"expected_revision": current.revision,
        "request": {**request, "expected_input_fingerprint": "0" * 64}})
    assert result["reason"] == "agent_stale_revision"
    assert current.candidate == before
