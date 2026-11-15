"""Artifact freedom never bypasses formal generic calculation qualification."""
from __future__ import annotations

import copy
import uuid

import pytest

from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.agent_chat.native_app_tool_catalog import (
    build_native_app_tool_snapshot,
)
from genshin_app.agent_chat.native_app_tool_effects import stage_transition_proof
from genshin_app.agent_chat.native_prompt_v2 import (
    build_native_prompt_v2,
    validate_native_prompt_v2_bundle,
)
from genshin_app.agent_chat.tools import _query, definitions_for_session
from genshin_app.equipment import new_plan
from genshin_app.equipment.files import fingerprint
from genshin_app.whiteboard import default_panel, new_whiteboard
from genshin_db import queries


def native_plan():
    plan = new_plan()
    plan["manual_template"].update(loop_duration=2, characters=[
        {"id": "a", "name": "工程角色", "level": 90, "stats": {"atk": 100, "crit_rate": 0, "crit_dmg": .5}}],
        hits=[{"id": "h", "t": 1, "character": "a", "skill": "工程命中", "multiplier": 1,
               "damage_type_ids": ["damage.elemental_skill"], "damage_system": "classic", "element": "cryo"}])
    return plan


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setattr(queries, "get_character_detail", lambda *a, **kw: {
        "character": {"id": 1, "name": "工程角色", "weapon_type": "WEAPON_SWORD_ONE_HAND"}})
    return ArtifactCandidateSession(uuid.uuid4().hex, tmp_path / "session", tmp_path / "absent.db",
                                    "whiteboard", new_whiteboard())


def focus(session, phase, **options):
    return session.call_tool("gs_transition_phase", {
        "target_phase": phase, "expected_revision": session.revision,
        "expected_stage_revision": session.stage_state["stage_revision"], "reason": "选择工作重点", **options})


def test_empty_draft_focus_is_not_permission_and_cached_evidence_remains_readable(board):
    assert focus(board, "calculation_closure")["code"] == 0
    assert board._is_board() and board.workflow_stage == "calculation_closure"
    assert board.stage_state["stage_revision"] == 0
    assert board.stage_state["focus_revision"] == 1
    names = {row["name"] for row in definitions_for_session(board)}
    assert {"gs_patch_whiteboard", "gs_add_character", "gs_query_runtime", "gs_query_appendix",
            "gs_compose", "gs_calculate"} <= names
    assert board.call_tool("gs_patch_whiteboard", {
        "expected_revision": 0, "reason": "任何工作重点都能编辑草稿",
        "operations": [{"op": "replace", "path": "/title", "value": "可继续编辑的草稿"}]})["code"] == 0
    resource = _query(board, {"request": {}}, lambda *_: {"fact": 123})["resource"]
    assert focus(board, "loadout_draft")["code"] == 0
    assert board.call_tool("gs_read_context", {"resource": resource})["code"] == 0
    restored = CandidateSession.load(board.session_id, board.root, board.db_path)
    assert type(restored) is ArtifactCandidateSession
    assert restored.resources[resource] == {"fact": 123}
    assert restored.candidate["title"] == "可继续编辑的草稿"


def test_preparation_is_explicit_and_real_structure_is_still_required(board):
    before = fingerprint(board.candidate)
    rejected = focus(board, "event_topology", prepare_calculation=True)
    assert rejected["code"] == 1 and rejected["reason"] == "whiteboard_incomplete"
    assert fingerprint(board.candidate) == before
    assert board.workflow_stage == "loadout_draft"
    # Ordinary compose is available to explain its true data prerequisite.
    rejection = board.call_tool("gs_compose", {"expected_revision": 0})
    assert rejection["reason"] == "agent_native_artifact_required"
    assert board.stage_state["representation"] == "whiteboard"
    assert board.summary()["saveable"] is True
    assert board.summary()["representation"] == "whiteboard"
    assert board.summary()["assessment"] == "draft"


def test_explicit_handoff_retains_empty_topology_as_draft_and_works_at_any_focus(board):
    actor = {"id": "a", "db_character_id": 1, "name": "工程角色", "level": 90, "constellation": 0,
             "weapon": None, "artifact_sets": [], "panel": default_panel(),
             "panel_basis_mode": "manual", "notes": ""}
    action = {"id": "a.e", "character": "a", "action_type_id": "action.elemental_skill",
              "label": "元素战技", "start": 0, "variant": "", "notes": ""}
    assert board.call_tool("gs_patch_whiteboard", {
        "expected_revision": 0, "reason": "声明可编辑动作骨架", "operations": [
            {"op": "add", "path": "/team/-", "value": actor},
            {"op": "add", "path": "/actions/-", "value": action}]})["code"] == 0
    assert focus(board, "calculation_closure", prepare_calculation=True,
                 energy_intent="single_window_legal")["code"] == 0
    assert not board._is_board() and board.stage_state["stage_revision"] == 1
    assert board.topology_receipt()["status"] != "numeric_ready"
    assert {"gs_patch_candidate", "gs_query_appendix", "gs_calculate", "gs_validate_topology"} <= {
        row["name"] for row in definitions_for_session(board)}
    restored = CandidateSession.load(board.session_id, board.root, board.db_path)
    assert restored.stage_state["representation"] == "native"
    assert restored.stage_state["energy_intent"] == "single_window_legal"
    assert restored.summary()["applicable"] is False


def test_native_diagnostic_at_any_focus_can_recover_and_never_be_applied(tmp_path):
    from tests.test_energy_trials import plan as diagnostic_plan
    document = diagnostic_plan()
    document["manual_template"]["meta"]["name"] = "真实能量不足的工程诊断"
    session = ArtifactCandidateSession(uuid.uuid4().hex, tmp_path / "session", tmp_path / "absent.db",
                                       "native", document)
    assert session.workflow_stage == "loadout_draft"
    assert session.call_tool("gs_compose", {"expected_revision": 0})["code"] == 0
    computed = session.call_tool("gs_calculate", {"expected_revision": 0})
    assert computed["code"] == 1 and computed["reason"] == "agent_calculation_rejected"
    assert session.calculated is not None
    assert session.summary()["applicable"] is False
    basis = copy.deepcopy(session.stage_state["calculation_basis"])
    calculated = fingerprint(session.calculated)
    assert focus(session, "event_topology")["code"] == 0
    assert session.stage_state["calculation_basis"] == basis
    assert fingerprint(session.calculated) == calculated
    restored = CandidateSession.load(session.session_id, session.root, session.db_path)
    assert restored.calculated is not None and restored.workflow_stage == "event_topology"
    assert restored.calculation_acceptance()["status"] != "accepted"
    with pytest.raises(Exception) as exc:
        restored.accept("native", restored.document, restored.revision, "f" * 64)
    assert getattr(exc.value, "reason", None) == "agent_calculation_rejected"
    # Re-entering a focus does not unlock output cheating or disable the schema.
    assert restored.call_tool("gs_patch_candidate", {"expected_revision": 0, "reason": "伪造计算产物",
        "operations": [{"op": "replace", "path": "/resolved_template", "value": {}}]})["code"] == 1
    assert restored.call_tool("gs_patch_candidate", {"expected_revision": 0, "reason": "正常修订输入",
        "operations": [{"op": "replace", "path": "/manual_template/meta/name", "value": "修订数值场景"}]})["code"] == 0
    assert restored.calculated is None


def test_focus_keeps_signed_authorization_catalog_and_semantic_generation_stable(board):
    def snapshot():
        return build_native_app_tool_snapshot(board, document_id=board.document["board_id"],
            database_fingerprint=fingerprint(board.database_snapshot), tool_manifest_hash="a" * 64,
            runtime_generation="dsh_v2", writer_fence="writer-one")
    before = snapshot()
    assert focus(board, "numeric_model")["code"] == 0
    after = snapshot()
    assert before == after
    base = {"workflow": "artifact", "workflow_stage": "artifact", "stage_revision": 0,
            "tool_manifest_hash": "a" * 64, "stage_state_hash": "before"}
    proof = stage_transition_proof("stage_transition", base, {**base, "stage_state_hash": "after"})
    assert proof["kind"] == "artifact_focus"
    assert stage_transition_proof("candidate", base, {**base, "stage_state_hash": "after"}) is None


def test_artifact_prompt_replaces_obsolete_hard_phase_and_retry_ban():
    bundle = build_native_prompt_v2(workflow="artifact", runtime_context={"workflow": "artifact"})
    restored = validate_native_prompt_v2_bundle(bundle.public())
    assert restored.workflow == "artifact"
    assert "不是权限" in bundle.system_prompt
    assert "不自动重试收费请求" not in bundle.system_prompt
    assert "references/workflow/index.md" == bundle.required_skill_resource
