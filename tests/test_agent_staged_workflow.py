from __future__ import annotations

import copy
import json
import uuid

import pytest

from genshin_app.agent_chat.board_candidate import BoardCandidateSession
from genshin_app.agent_chat.calculation_qualification import qualify_calculation
from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.agent_chat.staged_candidate import StagedCandidateSession
from genshin_app.agent_chat.tools import (
    _numeric_patch_allowed,
    _query,
    _validate_numeric_action_evidence,
    definitions,
    skill_resource_names,
    staged_disclosure,
)
from genshin_app.equipment.agent_execution_materialization import (
    new_agent_execution_materialization,
)
from genshin_app.equipment.calculation_overrides.ownership_receipt import (
    FIELD as OWNERSHIP_PROOF_FIELD,
)
from genshin_app.equipment.calculation_overrides.ownership_receipt import (
    build_ownership_result_proof,
)
from genshin_app.equipment.delivery import _state as delivery_state
from genshin_app.equipment.event_topology import (
    AUTHORITY,
    CONTRACT_ID,
    event_topology_fingerprint,
)
from genshin_app.equipment.event_topology_binding import new_event_topology_bindings
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_app.whiteboard import new_whiteboard
from genshin_db import queries


@pytest.fixture
def staged(tmp_path, monkeypatch, request):
    db = tmp_path / "engineering.db"
    db.touch()
    meta = {"version_lines": ["release"], "datasets": {"equipment": {"lines": {
        "release": {"state": "ready", "schema_version": 1, "upstream_commit": "a" * 40,
                    "built_at": "2026-10-05T00:00:00Z"}}}}}
    monkeypatch.setattr(queries, "get_meta", lambda _: copy.deepcopy(meta))
    monkeypatch.setattr(queries, "get_character_detail", lambda *a, **kw: {
        "character": {"id": 1, "name": "工程角色", "weapon_type": "WEAPON_SWORD_ONE_HAND",
                      "atk_90": 300, "hp_90": 12000, "def_90": 800}})
    monkeypatch.setattr(queries, "get_weapon_detail", lambda *a, **kw: {
        "weapon": {"id": 101, "name": "工程剑", "weapon_type": "WEAPON_SWORD_ONE_HAND"},
        "effects": [], "data_version": None})
    monkeypatch.setattr(queries, "get_weapon_stats", lambda *a, **kw: {
        "properties": [{"prop_type": "FIGHT_PROP_BASE_ATTACK", "value": 500,
                        "source_refs": []}],
        "warnings": [], "data_version": {"snapshot_id": "fixture"}})
    monkeypatch.setattr(queries, "get_artifact_set_detail", lambda *a, **kw: {
        "artifact_set": {"id": 201, "name": "工程套装"}})
    board = new_whiteboard()
    if getattr(request, "param", None) == "explicit_full_window":
        # This fixture tests staged ownership/qualification, not automatic
        # identification of a last active damage component.
        board["damage_report"] = {
            "contract": "damage-report-window-v1", "mode": "explicit",
            "end": board["duration"],
        }
    return StagedCandidateSession(uuid.uuid4().hex, tmp_path / "session", db,
                                  "whiteboard", board)


def _call(session, name, **args):
    return session.call_tool(name, args)


def _transition(session, target, **extra):
    return _call(session, "gs_transition_phase", target_phase=target,
        expected_revision=session.revision,
        expected_stage_revision=session.stage_state["stage_revision"],
        reason="阶段数据已完成，进入下一阶段", **extra)


def _complete_topology(session):
    checkpoint = session._current_action_checkpoint()
    action_id = checkpoint["action_ids"][0]
    return {
        "contract_id": CONTRACT_ID,
        "schema_version": 1,
        "topology_id": "topology.fixture",
        "authority": AUTHORITY,
        "declaration": {"character_semantics": "agent_declared",
                        "completeness": "declared_complete", "note": "已完成事件盘点"},
        "action_checkpoint": checkpoint,
        "dependencies": [{"dependency_id": "database_snapshot", "kind": "database_snapshot",
                          "fingerprint": fingerprint(session.database_snapshot), "required": True}],
        "sources": [{"source_id": "source.skill", "category": "talent",
                    "owner_ref": "character-1", "description": "Agent声明的动作来源",
                    "disposition": "modeled", "node_ids": ["node.action"], "reason": None,
                    "source_refs": [{"kind": "agent_research",
                                     "reference": "fixture reviewed declaration",
                                     "fingerprint": None, "status": "declared"}]}],
        "nodes": [{"node_id": "node.action", "kind": "action_anchor", "status": "bound",
                   "schedule": {"kind": "point",
                                "anchor": {"kind": "action", "ref_id": action_id, "phase": "start"},
                                "offset_seconds": 0, "order": 0},
                   "source_ids": ["source.skill"], "parameter_slots": [], "note": ""}],
        "edges": [],
    }


@pytest.mark.parametrize(("phase", "stage_card"), [
    ("loadout_draft", "references/workflow/focus/loadout.md"),
    ("action_checkpoint", "references/workflow/focus/actions.md"),
    ("event_topology", "references/workflow/focus/events.md"),
    ("numeric_model", "references/workflow/focus/numbers.md"),
    ("calculation_closure", "references/workflow/focus/closure.md"),
])
def test_every_stage_opens_full_skill_bundle_and_only_scopes_recommendations(
    staged, phase, stage_card,
):
    staged.stage_state["phase"] = phase
    disclosure = staged_disclosure(staged)
    listed = _call(staged, "gs_read_skill")

    assert listed["data"]["resources"] == skill_resource_names()
    assert disclosure["skill_access"] == {
        "policy": "all_bundled_markdown_on_demand",
        "index_tool": "gs_read_skill",
        "resource_count": len(skill_resource_names()),
    }
    assert "allowed_skill_resources" not in disclosure
    assert disclosure["current_stage_skill"] == stage_card
    assert {
        "gs_read_context", "gs_read_skill", "gs_query_context",
        "gs_query_runtime", "gs_query_appendix", "gs_list_appendix_categories",
        "gs_character_panel_basis", "gs_search_catalog",
    } <= set(disclosure["allowed_tools"])
    assert set(disclosure["recommended_tools"]) <= set(disclosure["allowed_tools"])
    assert stage_card in disclosure["recommended_skill_resources"]
    assert len(disclosure["recommended_skill_resources"]) <= 10
    assert len(json.dumps(
        disclosure["recommended_skill_resources"],
        ensure_ascii=False,
        separators=(",", ":"),
    )) <= 1024
    assert set(disclosure["recommended_skill_resources"]).issubset(
        listed["data"]["resources"]
    )
    required = [
        "references/runtime/native-agent.md",
        "SKILL.md",
        "references/workflow/context-use.md",
        stage_card,
    ]
    for resource in required:
        reply = _call(staged, "gs_read_skill", resource=resource)
        assert reply["code"] == 0
        page = reply["data"]
        assert page["resource"] == resource
        assert 0 < page["delivered_chars"] == len(page["text"]) <= page["total"]
    assert {
        "SKILL.md",
        "references/runtime/native-agent.md",
        "references/data/query.md",
        "references/data/action-timing.md",
        "references/data/frame-rate-timing.md",
        "references/app-mechanics/loadouts.md",
        "references/app-mechanics/actions/rotation-energy.md",
        "references/app-mechanics/elements/runtime.md",
        "references/mechanics/damage-formula.md",
        "references/mechanics/reactions.md",
        "references/app-mechanics/review/corrections.md",
        "references/outputs/app-and-excel.md",
    }.issubset(listed["data"]["resources"])


def test_staged_session_progressively_discloses_and_persists(staged):
    initial = staged.summary()
    assert initial["workflow"] == "staged"
    assert initial["workflow_stage"] == "loadout_draft"
    assert initial["saveable"] is True
    assert initial["computable"] is False
    assert initial["applicable"] is False
    assert initial["stage_completion"] == {
        "minimum_structure": "incomplete",
        "agent_audit": "not_declared",
        "next_stage_eligible": False,
    }
    assert "energy_status" not in initial
    assert "calculation_acceptance" not in initial
    assert _call(staged, "gs_calculate", expected_revision=0)["reason"] == "agent_tool_forbidden"
    skill_index = _call(staged, "gs_read_skill")
    assert skill_index["code"] == 0
    assert skill_index["data"]["resources"] == skill_resource_names()
    assert {
        "SKILL.md",
        "references/runtime/native-agent.md",
        "references/workflow/context-use.md",
        "references/workflow/focus/loadout.md",
        "references/mechanics/damage-formula.md",
    }.issubset(skill_index["data"]["resources"])
    stage_context = _call(staged, "gs_read_context", resource="workflow_stage")
    stage_items = {row["key"]: row["value"] for row in stage_context["data"]["items"]}
    assert stage_items["current_stage_skill"] == "references/workflow/focus/loadout.md"
    assert "gs_calculate" not in stage_items["allowed_tools"]
    assert "event_topology_capabilities" not in stage_items["allowed_context_resources"]
    assert "gs_character_panel_basis" in stage_items["allowed_tools"]

    assert _call(staged, "gs_add_character", expected_revision=0, reason="添加用户指定角色",
                 db_character_id=1)["code"] == 0
    structural = staged.summary()["stage_completion"]
    assert structural == {
        "minimum_structure": "ready",
        "agent_audit": "not_declared",
        "next_stage_eligible": True,
    }
    assert _transition(staged, "action_checkpoint")["code"] == 0
    assert _call(staged, "gs_character_panel_basis",
                 db_character_id=1)["code"] == 0
    assert _call(staged, "gs_read_skill")["data"]["resources"] == skill_resource_names()
    action = {"id": "action.skill", "character": "character-1",
              "action_type_id": "action.elemental_skill", "label": "元素战技",
              "start": 1, "variant": "tap", "notes": ""}
    assert _call(staged, "gs_patch_whiteboard", expected_revision=staged.revision,
        reason="完成动作检查点", operations=[{"op": "add", "path": "/actions/-", "value": action}])["code"] == 0
    assert _transition(staged, "event_topology")["code"] == 0
    assert staged.summary()["stage_completion"] == {
        "minimum_structure": "ready",
        "agent_audit": "in_progress",
        "next_stage_eligible": False,
    }
    assert _call(staged, "gs_read_skill")["data"]["resources"] == skill_resource_names()
    assert staged.summary()["application_kind"] == "native_calculation_draft"
    assert staged.summary()["event_topology_coverage_receipt"]["status"] == "draft"
    assert _call(staged, "gs_read_context", resource="candidate")["reason"] == "agent_stage_resource_unavailable"
    assert _call(staged, "gs_read_context", resource="event_topology")["code"] == 0
    assert _call(staged, "gs_calculate", expected_revision=staged.revision)["reason"] == "agent_tool_forbidden"

    topology = _complete_topology(staged)
    result = _call(staged, "gs_patch_candidate", expected_revision=staged.revision,
        reason="写入完整Agent事件声明",
        operations=[{"op": "replace", "path": "/extensions/event_topology", "value": topology}])
    assert result["code"] == 0, result
    receipt = _call(staged, "gs_validate_topology", expected_revision=staged.revision)
    assert receipt["code"] == 0 and receipt["data"]["status"] == "numeric_ready"
    assert _transition(staged, "numeric_model")["code"] == 0
    pending = staged.summary()["calculation_acceptance"]
    assert pending["event_inventory"] == "declared_complete"
    assert pending["numeric_and_binding_validation"] == "incomplete"

    loaded = CandidateSession.load(staged.session_id, staged.root, staged.db_path)
    assert isinstance(loaded, StagedCandidateSession)
    assert loaded.workflow_stage == "numeric_model"
    assert loaded.summary()["event_topology_coverage_receipt"]["status"] == "numeric_ready"

    changed = _call(loaded, "gs_patch_candidate", expected_revision=loaded.revision,
        reason="数值阶段不应直接微调动作时点",
        operations=[
            {"op": "replace", "path": "/rotation_model/actions/0/start", "value": 1.25},
            {"op": "replace", "path": "/extensions/whiteboard_handoff/board/actions/0/start", "value": 1.25},
        ])
    assert changed["reason"] == "agent_stage_patch_forbidden"
    assert loaded.workflow_stage == "numeric_model" and loaded.revision == staged.revision

    assert _transition(loaded, "event_topology")["code"] == 0
    changed = _call(loaded, "gs_patch_candidate", expected_revision=loaded.revision,
        reason="回退事件阶段后成对微调动作时点",
        operations=[
            {"op": "replace", "path": "/rotation_model/actions/0/start", "value": 1.25},
            {"op": "replace", "path": "/extensions/whiteboard_handoff/board/actions/0/start", "value": 1.25},
        ])
    assert changed["code"] == 0, changed
    assert loaded.workflow_stage == "event_topology"
    assert loaded.summary()["event_topology_coverage_receipt"]["status"] == "numeric_ready"

    assert _transition(loaded, "action_checkpoint")["code"] == 0
    changed = _call(loaded, "gs_patch_candidate", expected_revision=loaded.revision,
        reason="改变动作变体，重新建立动作检查点",
        operations=[{"op": "replace", "path":
                     "/extensions/whiteboard_handoff/board/actions/0/variant",
                     "value": "hold"}])
    assert changed["code"] == 0, changed
    assert loaded.workflow_stage == "action_checkpoint"
    assert _call(loaded, "gs_patch_whiteboard", expected_revision=loaded.revision,
                 reason="错误表示", operations=[])["reason"] == "agent_tool_forbidden"
    # Re-entering topology binds a fresh checkpoint instead of reusing the old graph.
    assert _transition(loaded, "event_topology")["code"] == 0
    assert loaded.summary()["event_topology_coverage_receipt"]["status"] == "draft"


def test_database_change_blocks_whiteboard_to_native_handoff_atomically(staged, monkeypatch):
    assert _call(staged, "gs_add_character", expected_revision=staged.revision,
                 reason="添加工程角色", db_character_id=1)["code"] == 0
    assert _transition(staged, "action_checkpoint")["code"] == 0
    action = {"id": "action.skill", "character": "character-1",
              "action_type_id": "action.elemental_skill", "label": "元素战技",
              "start": 1, "variant": "tap", "notes": ""}
    assert _call(staged, "gs_patch_whiteboard", expected_revision=staged.revision,
        reason="建立动作检查点", operations=[{
            "op": "add", "path": "/actions/-", "value": action,
        }])["code"] == 0
    original_candidate = copy.deepcopy(staged.candidate)
    monkeypatch.setattr(staged, "_db_snapshot", lambda: {"state": "changed"})
    stale_summary = staged.summary()
    assert stale_summary["database_current"] is False
    assert stale_summary["saveable"] is True
    assert stale_summary["computable"] is False
    assert stale_summary["applicable"] is False
    result = _transition(staged, "event_topology")
    assert result["reason"] == "agent_database_changed"
    assert staged.workflow_stage == "action_checkpoint"
    assert staged.candidate == original_candidate


def test_native_rollback_edits_retained_board_and_regenerates_native_atomically(staged):
    assert _call(staged, "gs_add_character", expected_revision=staged.revision,
                 reason="添加工程角色", db_character_id=1)["code"] == 0
    assert _call(staged, "gs_patch_whiteboard", expected_revision=staged.revision,
        reason="选择配装", operations=[
            {"op": "replace", "path": "/team/0/weapon", "value": {
                "weapon_id": 101, "name": "工程剑", "level": 90,
                "ascension": 6, "refinement": 3,
            }},
            {"op": "add", "path": "/team/0/artifact_sets/-", "value": {
                "set_id": 201, "name": "工程套装", "count": 4,
            }},
        ])["code"] == 0
    assert _transition(staged, "action_checkpoint")["code"] == 0
    action = {"id": "action.skill", "character": "character-1",
              "action_type_id": "action.elemental_skill", "label": "元素战技",
              "start": 1, "variant": "tap", "notes": ""}
    assert _call(staged, "gs_patch_whiteboard", expected_revision=staged.revision,
        reason="建立动作检查点", operations=[{
            "op": "add", "path": "/actions/-", "value": action,
        }])["code"] == 0
    action_view = _call(staged, "gs_read_context", resource="candidate_board")
    actor = next(row["value"] for row in action_view["data"]["items"]
                 if row["key"] == "team")[0]
    assert actor["loadout_summary"] == {
        "weapon": {"weapon_id": 101, "refinement": 3},
        "artifact_sets": [{"set_id": 201, "count": 4}],
        "panel_basis_mode": "database",
    }
    assert "panel" not in actor and "name" not in actor["loadout_summary"]["weapon"]

    transitioned = _transition(staged, "event_topology")
    assert transitioned["code"] == 0, transitioned
    assert "gs_validate_execution_bindings" not in staged.stage_context()["allowed_tools"]
    assert "event_topology_bindings" not in staged.stage_context()["allowed_context_resources"]
    assert _call(staged, "gs_validate_execution_bindings",
                 expected_revision=staged.revision)["reason"] == "agent_tool_forbidden"
    assert _call(staged, "gs_read_context",
                 resource="event_topology_bindings")["reason"] == (
                     "agent_stage_resource_unavailable")
    event_view = _call(staged, "gs_read_context", resource="candidate_board")
    event_actor = next(row["value"] for row in event_view["data"]["items"]
                       if row["key"] == "team")[0]
    assert event_actor["loadout_summary"] == actor["loadout_summary"]
    assert _transition(staged, "action_checkpoint")["code"] == 0
    rolled_back_capabilities = _resource_items(staged, "capabilities")
    assert rolled_back_capabilities["editable"] == ["duration", "actions"]
    assert "composition" not in rolled_back_capabilities
    revision = staged.revision
    before = copy.deepcopy(staged.candidate)
    rejected = _call(staged, "gs_patch_candidate", expected_revision=revision,
        reason="无效轴长不应产生半写入", operations=[{
            "op": "replace", "path":
            "/extensions/whiteboard_handoff/board/duration", "value": 0,
        }])
    assert rejected["reason"] == "invalid_whiteboard"
    assert staged.revision == revision and staged.candidate == before
    changed = _call(staged, "gs_patch_candidate", expected_revision=revision,
        reason="回退动作阶段后改变动作变体与轴长", operations=[
            {"op": "replace", "path":
             "/extensions/whiteboard_handoff/board/duration", "value": 22},
            {"op": "replace", "path":
             "/extensions/whiteboard_handoff/board/actions/0/variant", "value": "hold"},
        ])
    assert changed["code"] == 0, changed
    assert staged._current_board()["duration"] == 22
    assert staged.candidate["manual_template"]["loop_duration"] == 22
    assert staged.candidate["rotation_model"]["period"] == 22
    assert staged.candidate["rotation_model"]["abilities"][0]["variant"] == "hold"
    assert staged._topology() is None

    loaded = CandidateSession.load(staged.session_id, staged.root, staged.db_path)
    restored_view = _call(loaded, "gs_read_context", resource="candidate_board")
    restored_actor = next(row["value"] for row in restored_view["data"]["items"]
                          if row["key"] == "team")[0]
    assert restored_actor["loadout_summary"] == actor["loadout_summary"]
    assert _transition(loaded, "loadout_draft")["code"] == 0
    rolled_back_capabilities = _resource_items(loaded, "capabilities")
    assert rolled_back_capabilities["editable"] == ["title", "notes", "team"]
    assert "action_types" not in rolled_back_capabilities
    changed = _call(loaded, "gs_patch_candidate", expected_revision=loaded.revision,
        reason="回退配装阶段后改用手工面板", operations=[
            {"op": "replace", "path":
             "/extensions/whiteboard_handoff/board/team/0/panel_basis_mode",
             "value": "manual"},
            {"op": "replace", "path":
             "/extensions/whiteboard_handoff/board/team/0/panel/atk", "value": 2345},
        ])
    assert changed["code"] == 0, changed
    assert loaded._current_board()["team"][0]["panel"]["atk"] == 2345
    assert loaded.candidate["manual_template"]["characters"][0]["stats"]["atk"] == 2345
    assert loaded.candidate["equipment_inputs"]["character-1"]["panel_basis_mode"] == "manual"

    restored = CandidateSession.load(loaded.session_id, loaded.root, loaded.db_path)
    assert restored.workflow_stage == "loadout_draft"
    assert restored._current_board()["team"][0]["panel"]["atk"] == 2345
    assert restored.candidate["manual_template"]["characters"][0]["stats"]["atk"] == 2345


def test_staged_query_issues_whole_resource_source_reference(staged):
    result = _query(staged, {"request": {}}, lambda _db, _request: {"facts": [1, 2, 3]},
                    "context")
    assert result["source_ref"] == {
        "kind": "database", "reference": result["resource"],
        "fingerprint": fingerprint({"facts": [1, 2, 3]}), "status": "verified",
        "integrity_status": "verified", "execution_authority": "none",
    }
    cached = _query(staged, {"request": {}}, lambda _db, _request: {"facts": [1, 2, 3]},
                    "context")
    assert cached["resource"] == result["resource"]
    assert cached["source_ref"] == result["source_ref"]


def _advance_to_action_checkpoint(staged):
    assert _call(staged, "gs_add_character", expected_revision=staged.revision,
                 reason="添加工程角色", db_character_id=1)["code"] == 0
    assert _transition(staged, "action_checkpoint")["code"] == 0


def _add_checkpoint_action_and_advance(staged):
    action = {"id": "action.skill", "character": "character-1",
              "action_type_id": "action.elemental_skill", "label": "元素战技",
              "start": 1, "variant": "tap", "notes": ""}
    assert _call(staged, "gs_patch_whiteboard", expected_revision=staged.revision,
        reason="写入动作骨架", operations=[{
            "op": "add", "path": "/actions/-", "value": action,
        }])["code"] == 0
    assert _transition(staged, "event_topology")["code"] == 0


def _advance_to_event_topology(staged):
    _advance_to_action_checkpoint(staged)
    _add_checkpoint_action_and_advance(staged)


def _appendix_row(request, **extra):
    return {
        "id": 71,
        "target_type": request["target_type"],
        "target_id": request["target_id"],
        "version_line": request["version_line"],
        "category": request["category"],
        "title": "施放时点候选",
        "content": "命中时点为动作开始后0.4秒。",
        "status": request["status"],
        "created_at": "2026-10-01T00:00:00Z",
        "updated_at": "2026-10-02T00:00:00Z",
        "private_review_note": "不得向Agent披露",
        "prototype": {"multiplier": 9.99},
        **extra,
    }


def _mock_appendix_query(monkeypatch, calls):
    def list_annotations(_db, **request):
        calls.append(copy.deepcopy(request))
        return {
            "total": 1,
            "page": request["page"],
            "page_size": request["page_size"],
            "items": [_appendix_row(request)],
        }

    monkeypatch.setattr(queries, "list_annotations", list_annotations)
    monkeypatch.setattr(queries, "annotation_target_scope", lambda _db, target_type,
        target_id, version_line: {
            "target": {"target_type": target_type, "target_id": target_id},
            "character_ids": [1], "weapon_ids": [], "artifact_set_ids": [],
        })


def _mock_appendix_categories(monkeypatch, calls):
    def list_annotation_categories(_db, target_type, target_id, status, version_line):
        calls.append({
            "target_type": target_type, "target_id": target_id,
            "status": status, "version_line": version_line,
        })
        return {
            "target": {"target_type": target_type, "target_id": target_id},
            "status": status, "version_line": version_line, "total": 2,
            "content": "不得披露的聚合正文",
            "categories": [{"category": "action_timing", "count": 2,
                            "title": "不得披露的条目标题"}],
        }

    monkeypatch.setattr(queries, "list_annotation_categories",
                        list_annotation_categories)


def test_appendix_query_is_readable_in_every_stage_and_has_closed_selector_schema(
        staged, monkeypatch):
    calls = []
    category_calls = []
    _mock_appendix_query(monkeypatch, calls)
    _mock_appendix_categories(monkeypatch, category_calls)
    valid = {
        "target_type": "skill", "target_id": 11,
        "status": "approved", "category": "action_timing",
        "version_line": "release",
    }
    category_request = {key: value for key, value in valid.items() if key != "category"}
    for change in (
        {"status": "draft"},
        {"page_size": 11},
        {"unexpected": True},
    ):
        result = _call(staged, "gs_query_appendix", **{**valid, **change})
        assert result["reason"] == "agent_tool_input"
    missing = {key: value for key, value in valid.items() if key != "category"}
    assert _call(staged, "gs_query_appendix", **missing)["reason"] == "agent_tool_input"
    assert calls == []

    result = _call(staged, "gs_query_appendix", **valid)
    assert result["code"] == 0, result
    assert calls == [{**valid, "page": 1, "page_size": 10}]
    category_result = _call(staged, "gs_list_appendix_categories", **category_request)
    assert category_result["code"] == 0, category_result
    assert category_result["data"] == {
        "contract_id": "agent-appendix-category-index-v1",
        "schema_version": 1,
        "target": {"target_type": "skill", "target_id": 11},
        "status": "approved", "version_line": "release",
        "annotation_authority": "approved", "execution_authority": "none",
        "total": 2,
        "categories": [{
            "category": "action_timing", "count": 2,
            "annotation_authority": "approved", "execution_authority": "none",
        }],
    }
    assert "content" not in json.dumps(category_result, ensure_ascii=False)
    assert "不得披露" not in json.dumps(category_result, ensure_ascii=False)
    assert category_calls == [category_request]
    assert "gs_query_appendix" in staged.stage_context()["allowed_tools"]
    assert "gs_list_appendix_categories" in staged.stage_context()["allowed_tools"]

    _advance_to_action_checkpoint(staged)
    assert "gs_query_appendix" in staged.stage_context()["allowed_tools"]


def test_appendix_query_keeps_content_authority_and_cache_manifest(staged, monkeypatch):
    calls = []
    _mock_appendix_query(monkeypatch, calls)
    _advance_to_event_topology(staged)
    request = {
        "target_type": "skill", "target_id": 11,
        "status": "pending", "category": "action_timing",
        "version_line": "beta", "page": 2, "page_size": 3,
    }
    result = _call(staged, "gs_query_appendix", **request)
    assert result["code"] == 0, result
    data = result["data"]
    assert data["source_ref"]["kind"] == "database_appendix"
    assert data["source_ref"]["status"] == "verified"
    resource = staged.resources[data["resource"]]
    assert resource["contract_id"] == "agent-appendix-query-v1"
    assert resource["target"] == {"target_type": "skill", "target_id": 11}
    assert resource["owner_scope"] == {
        "target": {"target_type": "skill", "target_id": 11},
        "character_ids": [1], "weapon_ids": [], "artifact_set_ids": [],
    }
    assert resource["selection"] == {
        "status": "pending", "category": "action_timing", "version_line": "beta"}
    assert resource["annotation_authority"] == "pending"
    assert resource["execution_authority"] == "none"
    assert resource["items"] == [{
        "id": 71, "target_type": "skill", "target_id": 11,
        "version_line": "beta", "category": "action_timing",
        "title": "施放时点候选", "content": "命中时点为动作开始后0.4秒。",
        "status": "pending", "annotation_authority": "pending",
        "execution_authority": "none",
    }]
    descriptor = staged.stage_state["resource_descriptors"][data["resource"]]
    assert descriptor["query_kind"] == "appendix"
    assert descriptor["action"] is None
    assert descriptor["entities"] == ["skill:11"]
    assert descriptor["selectors"] == [
        "status=pending", "category=action_timing", "version_line=beta",
        "page=2", "page_size=3",
    ]
    assert descriptor["fingerprint"] == fingerprint(resource)
    assert "private_review_note" not in json.dumps(resource, ensure_ascii=False)
    assert "prototype" not in json.dumps(resource, ensure_ascii=False)
    assert "created_at" not in json.dumps(resource, ensure_ascii=False)

    cached = _call(staged, "gs_query_appendix", **request)
    assert cached["code"] == 0
    assert cached["data"]["resource"] == data["resource"]
    assert cached["data"]["source_ref"] == data["source_ref"]
    assert calls == [{**request}, {**request}]
    manifest = staged.stage_context()["visible_cached_resource_manifest"]
    assert manifest[-1] == descriptor


def test_appendix_projection_survives_event_stage_and_approved_stays_non_executable(
        staged, monkeypatch):
    calls = []
    _mock_appendix_query(monkeypatch, calls)
    _advance_to_event_topology(staged)
    result = _call(staged, "gs_query_appendix", target_type="skill", target_id=11,
                   status="approved", category="action_timing",
                   version_line="release")
    assert result["code"] == 0, result
    resource = staged.resources[result["data"]["resource"]]
    assert resource["items"][0]["content"] == "命中时点为动作开始后0.4秒。"
    assert resource["items"][0]["annotation_authority"] == "approved"
    assert resource["items"][0]["execution_authority"] == "none"
    assert "stage_projection" not in resource


def test_appendix_tool_is_absent_from_ordinary_build_and_calculate(staged, monkeypatch):
    calls = []
    _mock_appendix_query(monkeypatch, calls)
    ordinary = BoardCandidateSession(uuid.uuid4().hex, staged.root.parent / "ordinary-appendix",
                                     staged.db_path, "whiteboard", new_whiteboard())
    request = {
        "target_type": "skill", "target_id": 11,
        "status": "pending", "category": "action_timing",
        "version_line": "release",
    }
    assert _call(ordinary, "gs_query_appendix", **request)["reason"] == "agent_tool_forbidden"
    category_request = {key: value for key, value in request.items() if key != "category"}
    assert _call(ordinary, "gs_list_appendix_categories",
                 **category_request)["reason"] == "agent_tool_forbidden"
    assert "gs_query_appendix" not in {row["name"] for row in definitions("build")}
    assert "gs_query_appendix" not in {row["name"] for row in definitions("calculate")}
    assert "gs_list_appendix_categories" not in {
        row["name"] for row in definitions("build")}
    assert "gs_list_appendix_categories" not in {
        row["name"] for row in definitions("calculate")}
    assert calls == []


def _resource_items(session, resource):
    result = _call(session, "gs_read_context", resource=resource)
    assert result["code"] == 0, result
    return {row["key"]: row["value"] for row in result["data"]["items"]}


def test_write_schemas_are_staged_but_read_capabilities_are_complete(staged):
    loadout = staged.stage_context()
    assert "whiteboard_capabilities" not in loadout["allowed_context_resources"]
    assert "runtime_capabilities" in loadout["allowed_context_resources"]
    assert "mechanics_capabilities" in loadout["allowed_context_resources"]
    assert "event_topology_capabilities" not in loadout["allowed_context_resources"]
    capabilities = _resource_items(staged, "capabilities")
    assert capabilities["editable"] == ["title", "notes", "team"]
    assert "action_types" not in capabilities
    assert "capacity_intents" not in capabilities
    schema = _resource_items(staged, "whiteboard_schema")
    assert schema["editable"] == ["title", "notes", "team"]
    assert "action" not in schema and "capacity_intent" not in schema
    context = _resource_items(staged, "context_capabilities")
    assert "skills" in context["sections"]["character"]
    assert "effects" in context["sections"]["weapon"]
    assert "effects" in context["sections"]["artifact_set"]
    runtime = _resource_items(staged, "runtime_capabilities")
    assert "periodic_attack_profile" in runtime["actions"]

    assert _call(staged, "gs_add_character", expected_revision=staged.revision,
                 reason="添加工程角色", db_character_id=1)["code"] == 0
    assert _transition(staged, "action_checkpoint")["code"] == 0
    capabilities = _resource_items(staged, "capabilities")
    assert capabilities["editable"] == ["duration", "actions"]
    assert "default_panel" not in capabilities and capabilities["action_types"]
    schema = _resource_items(staged, "whiteboard_schema")
    assert schema["editable"] == ["duration", "actions"]
    assert "actor" not in schema and "action" in schema
    runtime = _resource_items(staged, "runtime_capabilities")
    assert "action_dispatch_profile" in runtime["actions"]
    assert "periodic_attack_profile" in runtime["actions"]
    assert _call(staged, "gs_read_context",
                 resource="whiteboard_capabilities")["reason"] == (
                     "agent_stage_resource_unavailable")


def test_ordinary_build_resources_and_queries_are_not_stage_projected(staged):
    ordinary = BoardCandidateSession(uuid.uuid4().hex, staged.root.parent / "ordinary-build",
                                     staged.db_path, "whiteboard", new_whiteboard())
    capabilities = _resource_items(ordinary, "capabilities")
    assert "chat_workflows" in capabilities and "capacity_intents" in capabilities
    schema = _resource_items(ordinary, "whiteboard_schema")
    assert "capacity_intent" in schema and "action_admission_intent" in schema
    raw = {"items": [{"description": "完整普通流程正文", "params": {"10": [9.99]}}]}
    result = _query(ordinary, {"request": {}},
                    lambda _db, _request: copy.deepcopy(raw), "context")
    assert ordinary.resources[result["resource"]] == raw
    assert "SKILL.md" in _call(ordinary, "gs_read_skill")["data"]["resources"]


def test_staged_query_manifest_is_bodyless_bounded_and_restored(staged):
    for index in range(18):
        request = {"characters": [{"id": index + 1, "sections": ["character"]}]}
        result = _query(staged, {"request": request},
            lambda _db, _request, index=index: {
                "items": [{"id": index + 1, "description": "正文" * 100}],
            }, "context")
        assert result["resource"] == f"query-{index + 1}"
    stage = staged.stage_context()
    manifest = stage["visible_cached_resource_manifest"]
    assert len(manifest) == 16
    assert stage["visible_cached_resource_total"] == 18
    assert stage["visible_cached_resource_manifest_truncated"] is True
    assert manifest[0]["id"] == "query-3" and manifest[-1]["id"] == "query-18"
    assert set(manifest[-1]) == {
        "id", "stage", "query_kind", "action", "entities", "selectors",
        "fingerprint", "bytes"}
    assert manifest[-1]["entities"] == ["character:18"]
    assert manifest[-1]["selectors"] == ["character:18.sections=character"]
    assert "正文" not in json.dumps(manifest, ensure_ascii=False)

    loaded = CandidateSession.load(staged.session_id, staged.root, staged.db_path)
    assert loaded.stage_context()["visible_cached_resource_manifest"] == manifest
    checkpoint = json.loads((staged.root / "checkpoint.json").read_text(encoding="utf-8"))
    descriptor = checkpoint["stage_state"]["resource_descriptors"]["query-18"]
    assert descriptor == manifest[-1]
    assert "items" not in descriptor and "description" not in descriptor


def test_read_only_query_resource_remains_visible_after_phase_rollback(staged):
    staged.stage_state["phase"] = "event_topology"
    result = _query(
        staged,
        {"request": {"characters": [{"id": 1}]}},
        lambda _db, _request: {"items": [{"id": 1, "name": "工程角色"}]},
        "context",
    )
    resource = result["resource"]

    staged.stage_state["phase"] = "loadout_draft"
    reread = _call(staged, "gs_read_context", resource=resource)

    assert reread["code"] == 0
    assert any(
        row["id"] == resource
        for row in staged.stage_context()["visible_cached_resource_manifest"]
    )


def test_workflow_stage_history_discloses_only_recent_eight(staged):
    assert _call(staged, "gs_add_character", expected_revision=staged.revision,
                 reason="添加工程角色", db_character_id=1)["code"] == 0
    for _ in range(5):
        assert _transition(staged, "action_checkpoint")["code"] == 0
        assert _transition(staged, "loadout_draft")["code"] == 0
    stage = staged.stage_context()
    assert len(stage["stage_history"]) == 8
    assert stage["stage_history_total"] == 10
    assert stage["stage_history_truncated"] is True
    assert stage["stage_history"][0]["stage_revision"] == 3
    loaded = CandidateSession.load(staged.session_id, staged.root, staged.db_path)
    assert loaded.stage_context()["stage_history"] == stage["stage_history"]


def test_compact_response_discloses_receipts_only_in_their_readable_stage(staged):
    loadout = staged.compact_tool_response()
    assert loadout["read_next"] == [
        "workflow_stage", "capabilities", "whiteboard_schema", "candidate_board"]
    assert loadout["required_context_reads"] == loadout["read_next"]
    assert loadout["recommended_context_reads"] == [
        "context_capabilities", "database_snapshot", "findings"]
    assert "calculation_acceptance" not in loadout
    assert "event_topology_coverage_receipt" not in loadout
    assert "event_topology_execution_binding_receipt" not in loadout

    stage_contract = staged.stage_context()
    assert stage_contract["required_context_reads"] == loadout["required_context_reads"]
    assert set(stage_contract["required_context_reads"]) <= set(
        stage_contract["allowed_context_resources"])
    assert set(stage_contract["recommended_context_reads"]) <= set(
        stage_contract["allowed_context_resources"])

    assert _call(staged, "gs_add_character", expected_revision=staged.revision,
                 reason="添加工程角色", db_character_id=1)["code"] == 0
    action_stage = _transition(staged, "action_checkpoint")["data"]
    assert action_stage["read_next"] == [
        "workflow_stage", "capabilities", "whiteboard_schema", "candidate_board"]
    assert "calculation_acceptance" not in action_stage
    assert "event_topology_coverage_receipt" not in action_stage
    assert "event_topology_execution_binding_receipt" not in action_stage

    action = {"id": "action.skill", "character": "character-1",
              "action_type_id": "action.elemental_skill", "label": "元素战技",
              "start": 1, "variant": "tap", "notes": ""}
    assert _call(staged, "gs_patch_whiteboard", expected_revision=staged.revision,
        reason="建立动作检查点",
        operations=[{"op": "add", "path": "/actions/-", "value": action}])["code"] == 0
    event_stage = _transition(staged, "event_topology")["data"]
    assert event_stage["read_next"] == [
        "workflow_stage", "event_topology_capabilities", "candidate_board",
        "event_topology", "event_topology_receipt"]
    assert "event_topology_coverage_receipt" in event_stage
    assert "event_topology_execution_binding_receipt" not in event_stage
    assert set(event_stage["calculation_acceptance"]) == {
        "contract", "status", "can_accept", "diagnostic_attempt",
        "character_semantics", "event_inventory", "issues", "issues_total",
        "issues_truncated",
    }
    assert event_stage["calculation_acceptance"]["issues"] == []

    topology = _complete_topology(staged)
    assert _call(staged, "gs_patch_candidate", expected_revision=staged.revision,
        reason="写入完整事件声明", operations=[{
            "op": "replace", "path": "/extensions/event_topology", "value": topology,
        }])["code"] == 0
    numeric_stage = _transition(staged, "numeric_model")["data"]
    assert numeric_stage["read_next"] == [
        "workflow_stage", "plan_schema", "event_topology",
        "event_topology_receipt", "event_topology_binding_capabilities",
        "agent_execution_materialization_capabilities"]
    assert "event_topology_coverage_receipt" in numeric_stage
    assert "event_topology_execution_binding_receipt" in numeric_stage
    assert "numeric_and_binding_validation" in numeric_stage["calculation_acceptance"]
    assert "generic_engine_validation" not in numeric_stage["calculation_acceptance"]
    capabilities = _resource_items(staged, "capabilities")
    override = capabilities["calculation_override_contract"]
    assert override["contract"] == "calculation-event-overrides-v1"
    assert override["execution_connected"] is True
    assert override["modes"]["replace_computed_value"] == "implemented"
    provisional = capabilities["provisional_entity_contract"]
    assert provisional["contract"] == "provisional-public-entities-v1"
    assert provisional["formal_result_usage"]["audit_only"] is True
    assert provisional["formal_result_usage"]["unused_registry_payloads_exported"] is False

    rolled_back = _transition(staged, "action_checkpoint")["data"]
    assert rolled_back["read_next"] == [
        "workflow_stage", "capabilities", "whiteboard_schema", "candidate_board"]
    assert "calculation_acceptance" not in rolled_back
    assert "event_topology_coverage_receipt" not in rolled_back
    assert "event_topology_execution_binding_receipt" not in rolled_back
    public = staged.summary()
    assert "calculation_acceptance" not in public
    assert "event_topology_coverage_receipt" not in public
    assert "event_topology_execution_binding_receipt" not in public
    assert "energy_status" not in public and "energy_detail" not in public
    assert public["diff"] == [] and public["summary"] == {}
    assert "node.action" not in json.dumps(public, ensure_ascii=False)


def test_numeric_stage_can_author_and_validate_bound_agent_materialization(staged):
    assert _call(staged, "gs_add_character", expected_revision=staged.revision,
                 reason="添加工程角色", db_character_id=1)["code"] == 0
    assert _transition(staged, "action_checkpoint")["code"] == 0
    action = {"id": "action.skill", "character": "character-1",
              "action_type_id": "action.elemental_skill", "label": "元素战技",
              "start": 1, "variant": "tap", "notes": ""}
    assert _call(staged, "gs_patch_whiteboard", expected_revision=staged.revision,
        reason="建立动作检查点",
        operations=[{"op": "add", "path": "/actions/-", "value": action}])["code"] == 0
    assert _transition(staged, "event_topology")["code"] == 0
    topology = _complete_topology(staged)
    assert _call(staged, "gs_patch_candidate", expected_revision=staged.revision,
        reason="写入完整事件声明", operations=[{
            "op": "replace", "path": "/extensions/event_topology", "value": topology,
        }])["code"] == 0
    assert _transition(staged, "numeric_model")["code"] == 0

    document = new_agent_execution_materialization(
        staged.candidate, staged._topology(), "materialization.tool-fixture")
    saved = _call(staged, "gs_patch_candidate", expected_revision=staged.revision,
        reason="写入当前方案Agent材料化输入", operations=[{
            "op": "add", "path": "/extensions/agent_execution_materialization",
            "value": document,
        }])
    assert saved["code"] == 0
    checked = _call(staged, "gs_validate_agent_materialization",
                    expected_revision=staged.revision)
    assert checked["code"] == 0
    assert checked["data"]["status"] == "ready"
    assert checked["data"]["database_access"] == "none"


@pytest.mark.parametrize(("staged", "expect_acceptance"), [
    ("explicit_full_window", True), (None, False),
], indirect=["staged"])
def test_materialized_rule_completes_binding_and_enters_real_calculation_closure(
        staged, expect_acceptance):
    """A staged document may bind a newly created executable through D1.

    This uses the public staged patch/transition tools rather than calling the
    binding linter directly.  The automatic action anchor stays automatic;
    only the explicitly Agent-owned rule node is materialized.
    """
    assert _call(staged, "gs_add_character", expected_revision=staged.revision,
                 reason="添加工程角色", db_character_id=1)["code"] == 0
    panel = {
        "atk": 100, "hp": 10000, "def": 500,
        "crit_rate": 0, "crit_dmg": 0.5, "em": 0,
        "energy_recharge": 1, "base_atk": 50,
        "base_hp": 5000, "base_def": 250,
        "dmg_bonus": {
            "all": 0, "physical": 0, "pyro": 0, "hydro": 0,
            "electro": 0, "cryo": 0, "anemo": 0, "geo": 0, "dendro": 0,
        },
    }
    assert _call(staged, "gs_patch_whiteboard", expected_revision=staged.revision,
        reason="为闭环夹具写入完整用户面板", operations=[
            {"op": "replace", "path": "/team/0/panel_basis_mode", "value": "manual"},
            {"op": "replace", "path": "/team/0/panel", "value": panel},
        ])["code"] == 0
    assert _transition(staged, "action_checkpoint")["code"] == 0
    action = {"id": "action.skill", "character": "character-1",
              "action_type_id": "action.elemental_skill", "label": "元素战技",
              "start": 1, "variant": "tap", "notes": ""}
    assert _call(staged, "gs_patch_whiteboard", expected_revision=staged.revision,
        reason="建立动作检查点",
        operations=[{"op": "add", "path": "/actions/-", "value": action}])["code"] == 0
    assert _transition(staged, "event_topology")["code"] == 0

    topology = _complete_topology(staged)
    topology["sources"][0]["node_ids"].extend(["node.damage", "node.rule"])
    topology["nodes"].append({
        "node_id": "node.damage", "kind": "damage", "status": "bound",
        "schedule": {
            "kind": "point",
            "anchor": {"kind": "action", "ref_id": "action.skill", "phase": "start"},
            "offset_seconds": 0.1, "order": 1,
        },
        "source_ids": ["source.skill"], "parameter_slots": [],
        "note": "由既有手工命中拥有的伤害节点",
    })
    topology["nodes"].append({
        "node_id": "node.rule", "kind": "buff_interval", "status": "bound",
        "schedule": {
            "kind": "point",
            "anchor": {"kind": "action", "ref_id": "action.skill", "phase": "start"},
            "offset_seconds": 0, "order": 2,
        },
        "source_ids": ["source.skill"], "parameter_slots": [],
        "note": "由Agent强类型规则包建立的通用状态",
    })
    assert _call(staged, "gs_patch_candidate", expected_revision=staged.revision,
        reason="写入含Agent材料化节点的完整事件声明", operations=[{
            "op": "replace", "path": "/extensions/event_topology", "value": topology,
        }])["code"] == 0
    assert _transition(staged, "numeric_model")["code"] == 0

    refreshed = _call(
        staged, "gs_refresh_data_refs", expected_revision=staged.revision,
        reason="将候选装备引用同步到当前固定测试快照",
    )
    assert refreshed["code"] == 0, refreshed
    projected = copy.deepcopy(staged.candidate)
    manual_hit = {
        "id": "hit.staged", "damage_type_ids": ["damage.elemental_skill"],
        "t": 1.1, "character": "character-1", "skill": "staged",
        "damage_system": "classic", "element": "cryo", "multiplier": 1,
        "count": 1,
    }
    projected["manual_template"]["hits"].append(manual_hit)
    projected["rotation_model"]["initial_frontline"] = "character-1"
    projected["rotation_model"]["characters"][0].update({
        "element": "cryo", "energy_capacity": 60, "initial_energy": 60,
        "status": "assumed", "source": "Agent为通用闭环夹具声明的场景输入",
    })
    projected["rotation_model"]["abilities"][0].update({
        "status": "assumed", "source": "Agent为通用闭环夹具声明的能力输入",
    })
    projected["rotation_model"]["actions"][0]["occupation_end"] = 1.1
    projected["rotation_model"]["actions"][0]["switch_allowed_at"] = 1.1
    projected["rotation_model"]["actions"][0].update({
        "status": "assumed", "source": "Agent核实当前场景的动作占用时点",
    })
    mechanics_model = {
        "schema_version": 1, "rules": [],
        "hit_events": [{
            "hit_id": "hit.staged", "action_id": "action.skill", "order": 10,
        }],
        "hit_patterns": [], "condition_events": [],
        "initial_state": {"rules": []},
    }
    projected["mechanics_model"] = mechanics_model
    bindings = new_event_topology_bindings(topology)
    bindings["bindings"] = [{
        "binding_id": "binding.damage", "node_id": "node.damage",
        "target_kind": "hit", "pointer": "/manual_template/hits/0",
        "target_fingerprint": fingerprint(manual_hit),
        "note": "自动输入已拥有的命中",
    }]
    projected["extensions"]["event_topology_bindings"] = bindings
    document = new_agent_execution_materialization(
        projected, topology, "materialization.staged-rule")
    document["sources"] = [{
        "source_id": "source.skill", "kind": "agent_research",
        "reference": "fixture://staged-rule", "fingerprint": "c" * 64,
        "status": "verified", "note": "测试中冻结的Agent输入",
    }]
    document["entries"] = [{
        "entry_id": "rule.entry", "family": "mechanics_rule_bundle",
        "handler_version": 1, "node_ids": ["node.rule"],
        "source_ids": ["source.skill"], "coverage_keys": ["rule.staged"],
        "payload": {
            "bundle_id": "bundle.staged",
            "rules": [{
                "id": "rule.staged", "name_zh": "通用动作状态",
                "owner": "character-1", "source_id": "part.staged",
                "trigger": {"event": "action_start", "phase": "pre",
                            "action_ids": ["action.skill"]},
                "beneficiaries": {"mode": "self", "scope": "locked"},
                "timing": {"delay": 0, "duration": 1, "cooldown": 0,
                           "cooldown_anchor": "success"},
                "stacking": {"max": 1, "add": 1, "policy": "refresh"},
                "effects": [], "status": "assumed",
                "source": "Agent材料化通用规则测试",
            }],
            "initial_state": {"rules": [{
                "rule_id": "rule.staged", "cooldown_remaining": 0,
                "instances": [],
            }]},
            "resource_ownership": [],
            "parts": [{
                "part_id": "part.staged", "rule_id": "rule.staged",
                "node_ids": ["node.rule"], "source_ids": ["source.skill"],
                "note": "精确绑定当前拓扑节点",
            }],
        },
        "note": "",
    }]
    saved = _call(staged, "gs_patch_candidate", expected_revision=staged.revision,
        reason="同时绑定当前拓扑并写入Agent规则材料化", operations=[
            {"op": "add", "path": "/manual_template/hits/-",
             "value": manual_hit},
            {"op": "add", "path": "/rotation_model/initial_frontline",
             "value": "character-1"},
            {"op": "replace", "path": "/rotation_model/characters/0/element",
             "value": "cryo"},
            {"op": "replace", "path": "/rotation_model/characters/0/energy_capacity",
             "value": 60},
            {"op": "replace", "path": "/rotation_model/characters/0/initial_energy",
             "value": 60},
            {"op": "replace", "path": "/rotation_model/characters/0/status",
             "value": "assumed"},
            {"op": "replace", "path": "/rotation_model/characters/0/source",
             "value": "Agent为通用闭环夹具声明的场景输入"},
            {"op": "replace", "path": "/rotation_model/abilities/0/status",
             "value": "assumed"},
            {"op": "replace", "path": "/rotation_model/abilities/0/source",
             "value": "Agent为通用闭环夹具声明的能力输入"},
            {"op": "add", "path": "/rotation_model/actions/0/occupation_end",
             "value": 1.1},
            {"op": "add", "path": "/rotation_model/actions/0/switch_allowed_at",
             "value": 1.1},
            {"op": "replace", "path": "/rotation_model/actions/0/status",
             "value": "assumed"},
            {"op": "replace", "path": "/rotation_model/actions/0/source",
             "value": "Agent核实当前场景的动作占用时点"},
            {"op": "replace", "path": "/mechanics_model",
             "value": mechanics_model},
            {"op": "replace", "path": "/extensions/event_topology_bindings",
             "value": bindings},
            {"op": "add", "path": "/extensions/agent_execution_materialization",
             "value": document},
        ])
    assert saved["code"] == 0, saved
    binding_receipt = staged.topology_binding_receipt()
    assert binding_receipt["status"] == "complete"
    assert binding_receipt["coverage"]["materialized_node_ids"] == ["node.rule"]

    closed = _transition(
        staged, "calculation_closure", energy_intent="single_window_legal")
    assert closed["code"] == 0, json.dumps(closed, ensure_ascii=False, indent=2)
    assert staged.workflow_stage == "calculation_closure"
    runtime = staged.report["agent_execution_runtime_receipt"]
    installed = {row["family"]: row for row in runtime["entries"]}
    assert installed["mechanics_rule_bundle"]["status"] == "installed"
    assert installed["mechanics_rule_bundle"]["installed"][0]["rule_id"] == "rule.staged"
    calculated = _call(
        staged, "gs_calculate", expected_revision=staged.revision)
    diagnostics = (_call(
        staged, "gs_diagnose_result", expected_revision=staged.revision,
        request={}) if calculated["code"] else None)
    if not expect_acceptance:
        assert calculated["code"] == 1
        assert diagnostics["code"] == 0
        assert "damage_report_anchor_missing" in {
            row["identity"]["code"] for row in diagnostics["data"]["items"]
        }
        assert staged.calculation_acceptance()["status"] == "rejected"
        assert staged.summary()["applicable"] is False
        return
    assert calculated["code"] == 0, json.dumps(
        {"calculated": calculated, "diagnostics": diagnostics},
        ensure_ascii=False, indent=2)
    assert staged.calculated["plan"]["rotation_model"]["actions"][0]["status"] == "assumed"
    assert staged.calculated["plan"]["rotation_model"]["actions"][0]["source"] == (
        "Agent核实当前场景的动作占用时点"
    )
    assert staged.calculation_acceptance()["status"] == "accepted"
    summary = staged.summary()
    assert summary["kind"] == "whiteboard"
    assert summary["application_kind"] == "whiteboard_calculated"
    assert summary["ready"] is True
    assert summary["applicable"] is True
    accepted = staged.accept(
        "whiteboard",
        staged.document,
        staged.revision,
        summary["receipt_fingerprint"],
    )
    assert accepted["kind"] == "whiteboard"
    assert accepted["result_path"].endswith(".calculated.json")


def test_energy_intent_is_declared_only_for_closure_and_hidden_from_early_stages(staged):
    assert "energy_intent" not in staged.stage_context()
    assert _call(staged, "gs_add_character", expected_revision=staged.revision,
                 reason="添加工程角色", db_character_id=1)["code"] == 0
    rejected = _transition(
        staged, "action_checkpoint", energy_intent="multi_round_legal")
    assert rejected["reason"] == "agent_stage_energy_intent_forbidden"
    assert staged.workflow_stage == "loadout_draft"
    assert staged.stage_state["energy_intent"] is None

    assert _transition(staged, "action_checkpoint")["code"] == 0
    assert "energy_intent" not in staged.stage_context()
    action = {"id": "action.skill", "character": "character-1",
              "action_type_id": "action.elemental_skill", "label": "元素战技",
              "start": 1, "variant": "tap", "notes": ""}
    assert _call(staged, "gs_patch_whiteboard", expected_revision=staged.revision,
        reason="建立动作检查点",
        operations=[{"op": "add", "path": "/actions/-", "value": action}])["code"] == 0
    assert _transition(staged, "event_topology")["code"] == 0
    assert "energy_intent" not in staged.stage_context()
    assert _call(staged, "gs_patch_candidate", expected_revision=staged.revision,
        reason="写入完整事件声明", operations=[{
            "op": "replace", "path": "/extensions/event_topology",
            "value": _complete_topology(staged),
        }])["code"] == 0
    assert _transition(staged, "numeric_model")["code"] == 0
    assert staged.stage_context()["energy_intent"] is None

    # An old closure policy survives a numeric-only correction, but returning
    # to event design clears it so it cannot silently govern a rebuilt graph.
    staged.stage_state["energy_intent"] = "multi_round_legal"
    assert _transition(staged, "event_topology")["code"] == 0
    assert staged.stage_state["energy_intent"] is None
    assert "energy_intent" not in staged.stage_context()

    checkpoint_path = staged.root / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["stage_state"]["energy_intent"] = "sustainable_loop"
    checkpoint_path.write_text(
        json.dumps(checkpoint, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    restored = CandidateSession.load(staged.session_id, staged.root, staged.db_path)
    assert restored.workflow_stage == "event_topology"
    assert restored.stage_state["energy_intent"] is None
    assert "energy_intent" not in restored.stage_context()


def test_stage_history_redacts_later_phase_reasons_after_rollback(staged):
    assert _call(staged, "gs_add_character", expected_revision=staged.revision,
                 reason="添加工程角色", db_character_id=1)["code"] == 0
    assert _call(staged, "gs_transition_phase", target_phase="action_checkpoint",
        expected_revision=staged.revision,
        expected_stage_revision=staged.stage_state["stage_revision"],
        reason="动作阶段公开原因")["code"] == 0
    action = {"id": "action.skill", "character": "character-1",
              "action_type_id": "action.elemental_skill", "label": "元素战技",
              "start": 1, "variant": "tap", "notes": ""}
    assert _call(staged, "gs_patch_whiteboard", expected_revision=staged.revision,
        reason="建立动作检查点",
        operations=[{"op": "add", "path": "/actions/-", "value": action}])["code"] == 0
    assert _call(staged, "gs_transition_phase", target_phase="event_topology",
        expected_revision=staged.revision,
        expected_stage_revision=staged.stage_state["stage_revision"],
        reason="未来拓扑秘密 multiplier=9.99")["code"] == 0
    topology = _complete_topology(staged)
    assert _call(staged, "gs_patch_candidate", expected_revision=staged.revision,
        reason="写入完整事件声明", operations=[{
            "op": "replace", "path": "/extensions/event_topology", "value": topology,
        }])["code"] == 0
    assert _call(staged, "gs_transition_phase", target_phase="numeric_model",
        expected_revision=staged.revision,
        expected_stage_revision=staged.stage_state["stage_revision"],
        reason="未来数值秘密 314159")["code"] == 0
    assert _call(staged, "gs_transition_phase", target_phase="action_checkpoint",
        expected_revision=staged.revision,
        expected_stage_revision=staged.stage_state["stage_revision"],
        reason="从数值阶段回退的内部说明")["code"] == 0

    history = staged.stage_context()["stage_history"]
    assert history[0]["reason"] == "动作阶段公开原因"
    assert history[0]["reason_redacted"] is False
    assert all(row["reason"] == "后续阶段记录已在回退时隐藏"
               and row["reason_redacted"] is True for row in history[1:])
    serialized = json.dumps(history, ensure_ascii=False)
    assert "multiplier=9.99" not in serialized
    assert "314159" not in serialized
    assert "从数值阶段回退的内部说明" not in serialized

    loaded = CandidateSession.load(staged.session_id, staged.root, staged.db_path)
    assert loaded.stage_context()["stage_history"] == history


def test_declared_database_reference_cannot_claim_verified_provenance(staged):
    assert _call(staged, "gs_add_character", expected_revision=staged.revision,
                 reason="添加工程角色", db_character_id=1)["code"] == 0
    assert _transition(staged, "action_checkpoint")["code"] == 0
    action = {"id": "action.skill", "character": "character-1",
              "action_type_id": "action.elemental_skill", "label": "元素战技",
              "start": 1, "variant": "tap", "notes": ""}
    assert _call(staged, "gs_patch_whiteboard", expected_revision=staged.revision,
        reason="建立动作检查点",
        operations=[{"op": "add", "path": "/actions/-", "value": action}])["code"] == 0
    assert _transition(staged, "event_topology")["code"] == 0
    topology = _complete_topology(staged)
    topology["sources"][0]["source_refs"] = [{
        "kind": "database", "reference": "query-1", "fingerprint": None,
        "status": "declared",
    }]
    result = _call(staged, "gs_patch_candidate", expected_revision=staged.revision,
        reason="尝试使用未签发的数据库引用", operations=[{
            "op": "replace", "path": "/extensions/event_topology", "value": topology}])
    assert result["code"] == 0
    receipt = staged.topology_receipt()
    assert receipt["status"] == "draft"
    assert "event_topology_database_source_unverified" in {
        row["code"] for row in receipt["issues"]}


def _calculated(*, executed_prefix=False, opening="passed", energy_status="unproven",
                proven_cycle=None, mechanics="unproven", materialization="unknown"):
    # Explicit synthetic, zero-hit formula data for pure qualification tests;
    # this helper does not claim a database, engine or sealed execution proof.
    materialization_class = ("normal" if materialization in ("complete", "not_applicable")
                             else "diagnostic")
    value = {"plan": {}, "report": {"missing_inputs": []},
             "result_receipt": {}, "result": {
        "summary": {"total_damage": 0, "dps": 0}, "hits": [],
        "rotation_check": {
            "status": "passed" if energy_status == "passed" else "unproven",
            "action_check": {"status": "passed"},
            "energy_check": {"status": energy_status, "opening_status": opening,
                             "proven_cycle": proven_cycle,
                             "cycle_status": ("passed" if energy_status == "passed"
                                              and proven_cycle is not None else "unproven")},
            "damage_interpretation": {"kind": "executed_prefix" if executed_prefix else "planned_damage",
                                      "complete_first_cycle": not executed_prefix},
        },
        "mechanics_status": mechanics,
        "mechanics": {"complete_first_cycle": not executed_prefix, "execution_status": mechanics},
        "action_materialization_receipt": {"status": materialization,
                                            "calculation_class": materialization_class},
    }}
    value["report"].update({
        "plan": value["plan"],
        "input_fingerprint": fingerprint(value["plan"]),
        "calculation_ownership_audit": {
            "contract": "calculation-coverage-ownership-audit-v1",
            "status": "verified",
            "claims": [],
            "ownership_ledger_fingerprint": fingerprint([]),
        },
    })
    proof = build_ownership_result_proof(value["report"])
    for target in (value["result"], value["report"], value["result_receipt"]):
        target[OWNERSHIP_PROOF_FIELD] = copy.deepcopy(proof)
    return value


def _qualification_topology(*kinds):
    ordered = sorted({"action_anchor", *kinds})
    return {
        "status": "numeric_ready",
        "counts": {"active_nodes": len(ordered)},
        "active_node_kinds": ordered,
        "active_node_kind_counts": {kind: 1 for kind in ordered},
    }


def test_calculation_qualification_separates_unknown_dispatch_from_generic_failures():
    topology = _qualification_topology("damage")
    bindings = {"status": "complete"}
    accepted = qualify_calculation(_calculated(), topology_receipt=topology,
                                   topology_binding_receipt=bindings,
                                   energy_intent="multi_round_legal")
    assert accepted["status"] == "accepted"
    assert accepted["action_materialization"] == {"status": "unknown", "unknown_is_blocking": False}
    assert accepted["character_semantics"] == "agent_declared"

    for calculated, policy, code in (
        (_calculated(executed_prefix=True), "single_window_legal", "executed_prefix_only"),
        (_calculated(opening="failed"), "multi_round_legal", "explicit_window_energy_failed"),
        (_calculated(), "sustainable_loop", "sustainable_energy_unproven"),
        (_calculated(materialization="incomplete"), "single_window_legal",
         "declared_event_materialization_incomplete"),
    ):
        rejected = qualify_calculation(calculated, topology_receipt=topology,
                                       topology_binding_receipt=bindings, energy_intent=policy)
        assert rejected["status"] == "rejected" and rejected["diagnostic_attempt"]
        assert code in {row["code"] for row in rejected["issues"]}

    sustainable = qualify_calculation(
        _calculated(energy_status="passed", proven_cycle=2, mechanics="passed"),
        topology_receipt=topology, topology_binding_receipt=bindings,
        energy_intent="sustainable_loop")
    assert sustainable["status"] == "accepted"

    rejected = qualify_calculation(_calculated(), topology_receipt=topology,
                                   topology_binding_receipt={"status": "incomplete"},
                                   energy_intent="multi_round_legal")
    assert rejected["status"] == "rejected"
    assert "event_topology_execution_binding_incomplete" in {
        row["code"] for row in rejected["issues"]}


@pytest.mark.parametrize(("verification", "accepted"), [
    (None, False),
    ({"status": "unverified", "declared_reaction_hit_count": 0}, False),
    ({"status": "failed", "declared_reaction_hit_count": 3}, False),
    ({"status": "verified", "mode": "elemental_runtime_simulated"}, True),
])
def test_reaction_topology_cannot_bypass_generic_runtime_verification(verification, accepted):
    calculated = _calculated(mechanics="passed")
    if verification is not None:
        calculated["result"]["rotation_check"]["damage_interpretation"][
            "reaction_verification"] = verification
    result = qualify_calculation(
        calculated,
        topology_receipt=_qualification_topology("reaction_candidate"),
        topology_binding_receipt={"status": "complete"},
        energy_intent="multi_round_legal",
    )
    assert result["can_accept"] is accepted
    codes = {row["code"] for row in result["issues"]}
    assert ("generic_elemental_runtime_unverified" in codes) is not accepted


@pytest.mark.parametrize(("verification", "accepted"), [
    (None, False),
    ({"status": "failed", "coverage_status": "complete_input_window"}, False),
    ({"status": "passed", "coverage_status": "partial"}, False),
    ({"status": "passed", "coverage_status": "complete_input_window"}, True),
])
def test_application_topology_requires_passed_covered_execution_receipt(
        verification, accepted):
    calculated = _calculated(mechanics="passed")
    if verification is not None:
        calculated["result"]["rotation_check"]["damage_interpretation"][
            "application_verification"] = verification
    result = qualify_calculation(
        calculated,
        topology_receipt=_qualification_topology("application"),
        topology_binding_receipt={"status": "complete"},
        energy_intent="multi_round_legal",
    )
    assert result["can_accept"] is accepted
    codes = {row["code"] for row in result["issues"]}
    assert ("generic_elemental_application_unverified" in codes) is not accepted


def test_active_kind_summary_cannot_hide_generic_requirements_with_zero_count():
    topology = _qualification_topology("reaction_candidate")
    topology["active_node_kind_counts"]["reaction_candidate"] = 0
    calculated = _calculated(mechanics="passed")
    result = qualify_calculation(
        calculated, topology_receipt=topology,
        topology_binding_receipt={"status": "complete"},
        energy_intent="multi_round_legal")
    codes = {row["code"] for row in result["issues"]}
    assert "event_topology_active_kind_summary_invalid" in codes
    assert "generic_elemental_runtime_unverified" in codes

    malformed = copy.deepcopy(topology)
    malformed["active_node_kinds"] = [{"kind": "reaction_candidate"}]
    malformed["counts"] = "forged"
    result = qualify_calculation(
        calculated, topology_receipt=malformed,
        topology_binding_receipt={"status": "complete"},
        energy_intent="multi_round_legal")
    assert result["status"] == "rejected"
    assert "event_topology_active_kind_summary_invalid" in {
        row["code"] for row in result["issues"]}


def _advance_to_numeric(session):
    assert _call(session, "gs_add_character", expected_revision=session.revision,
                 reason="添加工程角色", db_character_id=1)["code"] == 0
    assert _transition(session, "action_checkpoint")["code"] == 0
    action = {"id": "action.skill", "character": "character-1",
              "action_type_id": "action.elemental_skill", "label": "元素战技",
              "start": 1, "variant": "tap", "notes": ""}
    assert _call(session, "gs_patch_whiteboard", expected_revision=session.revision,
        reason="建立动作检查点",
        operations=[{"op": "add", "path": "/actions/-", "value": action}])["code"] == 0
    assert _transition(session, "event_topology")["code"] == 0
    assert _call(session, "gs_patch_candidate", expected_revision=session.revision,
        reason="写入完整Agent事件声明", operations=[{
            "op": "replace", "path": "/extensions/event_topology",
            "value": _complete_topology(session)}])["code"] == 0
    assert _transition(session, "numeric_model")["code"] == 0


def _refresh_empty_execution_bindings(session):
    topology = session._topology()
    result = _call(session, "gs_patch_candidate", expected_revision=session.revision,
        reason="将空执行绑定合同绑定到当前仅动作锚点的拓扑", operations=[
            {"op": "replace", "path": "/extensions/event_topology_bindings/topology_id",
             "value": topology["topology_id"]},
            {"op": "replace", "path": "/extensions/event_topology_bindings/topology_fingerprint",
             "value": event_topology_fingerprint(topology)},
        ])
    assert result["code"] == 0, result


def test_numeric_computability_uses_only_required_consumed_task_inputs(staged):
    _advance_to_numeric(staged)
    staged.report = {"missing_inputs": [{
        "code": "optional_appendix_missing", "message": "未消费的提示资料缺失",
        "consumer": "optional_hint", "required": False, "consumed": False,
    }]}
    optional = staged.summary()
    assert optional["task_capability"]["status"] == "degraded"
    assert optional["computable"] is True

    staged.report = {"missing_inputs": [{
        "code": "current_hit_multiplier_missing", "message": "当前命中缺倍率",
        "consumer": "hit:h1", "required": True, "consumed": True,
        "repair_hint": "补齐当前命中倍率",
    }]}
    required = staged.summary()
    assert required["task_capability"]["status"] == "blocked"
    assert required["computable"] is False


def test_backward_topology_edit_preserves_agent_event_inventory(staged):
    _advance_to_numeric(staged)
    before = copy.deepcopy(staged._topology())
    result = _transition(staged, "event_topology")
    assert result["code"] == 0
    assert staged._topology() == before
    assert staged.summary()["event_topology_coverage_receipt"]["status"] == "numeric_ready"


def test_execution_binding_receipt_is_disclosed_and_required_by_acceptance(staged):
    assert _call(staged, "gs_read_context",
                 resource="event_topology_binding_capabilities")["reason"] == (
                     "agent_stage_resource_unavailable")
    _advance_to_numeric(staged)
    stale = _call(staged, "gs_validate_execution_bindings", expected_revision=staged.revision)
    assert stale["code"] == 0 and stale["data"]["status"] == "incomplete"
    _refresh_empty_execution_bindings(staged)
    complete = _call(staged, "gs_validate_execution_bindings", expected_revision=staged.revision)
    assert complete["code"] == 0 and complete["data"]["status"] == "complete"
    assert _call(staged, "gs_read_context",
                 resource="event_topology_binding_capabilities")["code"] == 0

    staged.stage_state.update(phase="calculation_closure",
                              energy_intent="multi_round_legal")
    staged.calculated = _calculated()
    staged.stage_state["calculation_basis"] = staged._current_calculation_basis()
    assert staged.calculation_acceptance()["status"] == "accepted"
    staged.candidate["extensions"]["event_topology_bindings"]["topology_fingerprint"] = "0" * 64
    rejected = staged.calculation_acceptance()
    assert rejected["status"] == "rejected"
    assert {row["code"] for row in rejected["issues"]} >= {
        "event_topology_execution_binding_incomplete", "calculation_basis_stale"}


def test_database_stale_closure_remains_saveable_but_is_not_accepted(
    staged, monkeypatch,
):
    _advance_to_numeric(staged)
    _refresh_empty_execution_bindings(staged)
    staged.stage_state.update(phase="calculation_closure",
                              energy_intent="multi_round_legal")
    staged.calculated = _calculated()
    staged.stage_state["calculation_basis"] = staged._current_calculation_basis()
    assert staged.calculation_acceptance()["status"] == "accepted"

    monkeypatch.setattr(staged, "_db_snapshot", lambda: {"state": "changed"})
    acceptance = staged.calculation_acceptance()
    assert acceptance["status"] == "rejected"
    assert "agent_database_changed" in {row["code"] for row in acceptance["issues"]}
    summary = staged.summary()
    assert summary["saveable"] is True
    assert summary["computable"] is False
    assert summary["applicable"] is False
    assert summary["can_accept"] is False


def test_energy_policy_change_cannot_requalify_an_old_diagnostic(staged):
    _advance_to_numeric(staged)
    staged.stage_state.update(phase="calculation_closure", energy_intent="sustainable_loop")
    staged.calculated = {**_calculated(), "result_receipt": {"fixture": True}}
    rejected = staged.summary()
    assert rejected["calculation_acceptance"]["status"] == "rejected"
    assert rejected["receipt_fingerprint"] is None and not rejected["can_accept"]
    with pytest.raises(PlanError) as error:
        staged.accept("whiteboard", staged.document, staged.revision, None)
    assert error.value.reason == "agent_calculation_rejected"

    stage_revision = staged.stage_state["stage_revision"]
    changed = _transition(staged, "calculation_closure", energy_intent="single_window_legal")
    assert changed["code"] == 0
    assert staged.workflow_stage == "numeric_model"
    assert staged.stage_state["stage_revision"] == stage_revision + 1
    assert staged.calculated is None
    summary = staged.summary()
    assert summary["calculation_acceptance"]["status"] == "not_attempted"
    assert summary["receipt_fingerprint"] is None and not summary["can_accept"]


def test_checkpoint_cannot_forge_closure_while_candidate_is_whiteboard(staged):
    checkpoint_path = staged.root / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["stage_state"]["phase"] = "calculation_closure"
    checkpoint["stage_state"]["stage_revision"] = 99
    checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, separators=(",", ":")),
                               encoding="utf-8")
    with pytest.raises(PlanError) as error:
        CandidateSession.load(staged.session_id, staged.root, staged.db_path)
    assert error.value.reason == "agent_checkpoint_invalid"


def test_checkpoint_cannot_promote_event_draft_into_numeric_stage(staged):
    assert _call(staged, "gs_add_character", expected_revision=staged.revision,
                 reason="添加工程角色", db_character_id=1)["code"] == 0
    assert _transition(staged, "action_checkpoint")["code"] == 0
    action = {"id": "action.skill", "character": "character-1",
              "action_type_id": "action.elemental_skill", "label": "元素战技",
              "start": 1, "variant": "tap", "notes": ""}
    assert _call(staged, "gs_patch_whiteboard", expected_revision=staged.revision,
        reason="建立动作检查点",
        operations=[{"op": "add", "path": "/actions/-", "value": action}])["code"] == 0
    assert _transition(staged, "event_topology")["code"] == 0
    assert staged.topology_receipt()["status"] == "draft"

    checkpoint_path = staged.root / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    state = checkpoint["stage_state"]
    old_revision = state["stage_revision"]
    state["phase"] = "numeric_model"
    state["stage_revision"] = old_revision + 1
    state["history"].append({
        "stage_revision": old_revision + 1,
        "from": "event_topology",
        "to": "numeric_model",
        "candidate_revision": checkpoint["revision"],
        "reason": "伪造但结构自洽的阶段晋升",
    })
    checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, separators=(",", ":")),
                               encoding="utf-8")

    with pytest.raises(PlanError) as error:
        CandidateSession.load(staged.session_id, staged.root, staged.db_path)
    assert error.value.reason == "agent_checkpoint_invalid"


def test_closure_skill_and_context_remain_explicitly_disclosed(staged):
    _advance_to_numeric(staged)
    # The provider sees a stable superset of tool schemas, while every call is
    # still checked against the trusted phase state.
    staged.stage_state["phase"] = "calculation_closure"
    listed = _call(staged, "gs_read_skill")
    assert listed["code"] == 0
    resources = listed["data"]["resources"]
    assert "references/app-mechanics/review/corrections.md" in resources
    assert "references/workflow/whiteboard.md" in resources
    assert "references/workflow/whiteboard.md" not in staged_disclosure(staged)[
        "recommended_skill_resources"
    ]
    staged.stage_state["phase"] = "event_topology"
    assert _call(staged, "gs_read_context", resource="candidate")["reason"] == (
        "agent_stage_resource_unavailable")
    assert _call(staged, "gs_read_context", resource="calculated")["reason"] == (
        "agent_stage_resource_unavailable")
    assert _call(staged, "gs_calculate", expected_revision=staged.revision)["reason"] == (
        "agent_tool_forbidden")


def test_staged_patch_paths_are_fail_closed_and_event_time_moves_are_atomic(staged):
    forbidden = _call(staged, "gs_patch_whiteboard", expected_revision=staged.revision,
        reason="配装阶段越权写动作", operations=[{
            "op": "add", "path": "/actions/-", "value": {
                "id": "action.bad", "character": "character-1",
                "action_type_id": "action.elemental_skill", "label": "错误动作",
                "start": 0, "variant": "", "notes": "",
            }}])
    assert forbidden["reason"] == "agent_stage_patch_forbidden"
    assert staged.revision == 0

    assert _call(staged, "gs_add_character", expected_revision=staged.revision,
                 reason="添加工程角色", db_character_id=1)["code"] == 0
    assert _call(staged, "gs_patch_whiteboard", expected_revision=staged.revision,
        reason="配装阶段填写面板", operations=[{
            "op": "replace", "path": "/team/0/panel/atk", "value": 1000,
        }])["code"] == 0
    assert _transition(staged, "action_checkpoint")["code"] == 0

    assert _call(staged, "gs_add_character", expected_revision=staged.revision,
                 reason="动作阶段越权添加角色", db_character_id=1)["reason"] == (
                     "agent_tool_forbidden")
    assert _call(staged, "gs_patch_whiteboard", expected_revision=staged.revision,
        reason="动作阶段越权修改面板", operations=[{
            "op": "replace", "path": "/team/0/panel/atk", "value": 2000,
        }])["reason"] == "agent_stage_patch_forbidden"
    action = {"id": "action.skill", "character": "character-1",
              "action_type_id": "action.elemental_skill", "label": "元素战技",
              "start": 1, "variant": "tap", "notes": ""}
    assert _call(staged, "gs_patch_whiteboard", expected_revision=staged.revision,
        reason="写入动作骨架", operations=[{
            "op": "add", "path": "/actions/-", "value": action,
        }])["code"] == 0
    assert _transition(staged, "event_topology")["code"] == 0

    disclosure = staged_disclosure(staged)
    assert "event_topology" in disclosure["required_context_reads"]
    assert "event_topology" not in disclosure["recommended_context_reads"]
    assert disclosure["write_contract"] == {
        "tool": "gs_patch_candidate",
        "candidate_root_pointer": "/extensions/event_topology",
        "read_resource": "event_topology",
        "read_pointer_scope": "subtree_relative",
        "replace_whole_document_supported": True,
    }

    revision = staged.revision
    for path, value in (
        ("/manual_template/loop_duration", 99),
        ("/extensions/event_topology_bindings/bindings", []),
        ("/extensions/whiteboard_handoff/board/actions/0/variant", "hold"),
    ):
        result = _call(staged, "gs_patch_candidate", expected_revision=revision,
            reason="事件阶段越权写未来输入", operations=[{
                "op": "replace", "path": path, "value": value,
            }])
        assert result["reason"] == "agent_stage_patch_forbidden"
        assert staged.revision == revision

    unpaired = _call(staged, "gs_patch_candidate", expected_revision=revision,
        reason="只修改原生动作时点", operations=[{
            "op": "replace", "path": "/rotation_model/actions/0/start", "value": 1.25,
        }])
    assert unpaired["reason"] == "agent_stage_patch_forbidden"
    assert staged.revision == revision

    wrong_topology_root = _call(
        staged,
        "gs_patch_candidate",
        expected_revision=revision,
        reason="验证公开合同可修复错误根路径",
        operations=[{
            "op": "replace",
            "path": "/event_topology/declaration/completeness",
            "value": "declared_complete",
        }],
    )
    assert wrong_topology_root["reason"] == "agent_stage_patch_forbidden"
    assert wrong_topology_root["details"]["allowed_write_roots"][0] == (
        "/extensions/event_topology"
    )
    assert "/extensions/event_topology" in wrong_topology_root["details"][
        "repair_hint"
    ]
    assert staged.revision == revision

    paired = _call(staged, "gs_patch_candidate", expected_revision=revision,
        reason="成对微调稳定动作时点", operations=[
            {"op": "replace", "path": "/rotation_model/actions/0/start", "value": 1.25},
            {"op": "replace", "path":
             "/extensions/whiteboard_handoff/board/actions/0/start", "value": 1.25},
        ])
    assert paired["code"] == 0, paired
    assert staged.candidate["rotation_model"]["actions"][0]["start"] == 1.25
    assert staged._current_board()["actions"][0]["start"] == 1.25
    assert staged._topology()["action_checkpoint"] == staged._current_action_checkpoint()

    topology = _complete_topology(staged)
    assert _call(staged, "gs_patch_candidate", expected_revision=staged.revision,
        reason="事件阶段写入事件拓扑", operations=[{
            "op": "replace", "path": "/extensions/event_topology", "value": topology,
        }])["code"] == 0


def test_time_move_cannot_auto_sign_topology_changed_in_same_batch(staged):
    _advance_to_numeric(staged)
    _refresh_empty_execution_bindings(staged)
    assert _transition(staged, "event_topology")["code"] == 0
    before_checkpoint = copy.deepcopy(staged._topology()["action_checkpoint"])
    before_binding_fingerprint = staged._topology_bindings()["topology_fingerprint"]

    changed = _call(staged, "gs_patch_candidate", expected_revision=staged.revision,
        reason="同时调整动作时点与事件声明", operations=[
            {"op": "replace", "path": "/rotation_model/actions/0/start", "value": 1.25},
            {"op": "replace", "path":
             "/extensions/whiteboard_handoff/board/actions/0/start", "value": 1.25},
            {"op": "replace", "path":
             "/extensions/event_topology/declaration/note", "value": "尚待重新审查"},
        ])
    assert changed["code"] == 0, changed
    assert staged.workflow_stage == "event_topology"
    assert staged._topology()["action_checkpoint"] == before_checkpoint
    assert staged._topology_bindings()["topology_fingerprint"] == before_binding_fingerprint
    receipt = staged.topology_receipt()
    assert receipt["status"] == "draft"
    assert "event_topology_action_checkpoint_stale" in {
        issue["code"] for issue in receipt["issues"]}


def test_numeric_patch_surface_is_closed_and_panel_edits_are_atomic(staged):
    _advance_to_numeric(staged)
    revision = staged.revision
    for path, value in (
        ("/rotation_model/actions/0/start", 2),
        ("/rotation_model/abilities/0/variant", "hold"),
        ("/equipment_inputs/character-1/weapon", None),
        ("/extensions/event_topology/nodes/0/kind", "damage"),
        ("/manual_template/loop_duration", 30),
    ):
        result = _call(staged, "gs_patch_candidate", expected_revision=revision,
            reason="数值阶段越权修改早期结构", operations=[{
                "op": "replace", "path": path, "value": value,
            }])
        assert result["reason"] == "agent_stage_patch_forbidden"
        assert staged.revision == revision

    unpaired = _call(staged, "gs_patch_candidate", expected_revision=revision,
        reason="只改原生充能面板", operations=[{
            "op": "add", "path":
            "/manual_template/characters/0/stats/energy_recharge", "value": 1.5,
        }])
    assert unpaired["reason"] == "agent_stage_patch_forbidden"
    assert staged.revision == revision

    paired = _call(staged, "gs_patch_candidate", expected_revision=revision,
        reason="按充能需求同步重新分配面板", operations=[
            {"op": "replace", "path":
             "/extensions/whiteboard_handoff/board/team/0/panel/energy_recharge", "value": 1.5},
            {"op": "add", "path":
             "/manual_template/characters/0/stats/energy_recharge", "value": 1.5},
            {"op": "replace", "path":
             "/rotation_model/characters/0/energy_recharge", "value": 1.5},
        ])
    assert paired["code"] == 0, paired
    assert staged.workflow_stage == "numeric_model"
    assert staged._current_board()["team"][0]["panel"]["energy_recharge"] == 1.5
    assert staged.candidate["manual_template"]["characters"][0]["stats"][
        "energy_recharge"] == 1.5
    assert staged.candidate["rotation_model"]["characters"][0]["energy_recharge"] == 1.5


def test_numeric_and_closure_surface_exposes_unified_override_root_only():
    assert _numeric_patch_allowed(
        ["calculation_event_overrides"], set(), "/calculation_event_overrides"
    ) is True
    assert _numeric_patch_allowed(
        ["provisional_entities"], set(), "/provisional_entities"
    ) is True
    # App-issued qualification and execution proofs are never Agent-writable.
    # The Agent may declare typed inputs only; Composer/Service own every
    # resolved status and receipt/result projection derived from execution.
    for root in (
        "task_capability",
        "result_receipt",
        "calculation_ownership_proof",
        "agent_execution_runtime_receipt",
    ):
        assert _numeric_patch_allowed([root], set(), f"/{root}") is False


def test_numeric_stage_allows_only_controlled_action_timing_completion():
    assert _numeric_patch_allowed(
        ["rotation_model", "initial_frontline"], set(),
        "/rotation_model/initial_frontline",
    ) is True
    for field in ("occupation_end", "switch_allowed_at", "status", "source"):
        assert _numeric_patch_allowed(
            ["rotation_model", "actions", "0", field], set(),
            f"/rotation_model/actions/0/{field}",
        ) is True
    for field in ("id", "character", "skill", "start", "action_type_id"):
        assert _numeric_patch_allowed(
            ["rotation_model", "actions", "0", field], set(),
            f"/rotation_model/actions/0/{field}",
        ) is False


def test_numeric_action_evidence_requires_paired_review_status_and_source():
    _validate_numeric_action_evidence([
        {"op": "replace", "path": "/rotation_model/actions/0/status",
         "value": "assumed"},
        {"op": "replace", "path": "/rotation_model/actions/0/source",
         "value": "Agent按当前场景核实的通用动作时点"},
    ])
    with pytest.raises(PlanError) as error:
        _validate_numeric_action_evidence([{
            "op": "replace", "path": "/rotation_model/actions/0/status",
            "value": "confirmed",
        }])
    assert error.value.reason == "agent_stage_patch_forbidden"
    with pytest.raises(PlanError):
        _validate_numeric_action_evidence([
            {"op": "replace", "path": "/rotation_model/actions/0/status",
             "value": "pending"},
            {"op": "replace", "path": "/rotation_model/actions/0/source",
             "value": "still pending"},
        ])


def _all_mapping_keys(value):
    keys = []
    if isinstance(value, dict):
        for key, item in value.items():
            keys.append(key)
            keys.extend(_all_mapping_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.extend(_all_mapping_keys(item))
    return keys


def test_staged_queries_keep_complete_public_evidence_in_every_stage(staged):
    assert _call(staged, "gs_add_character", expected_revision=staged.revision,
                 reason="添加工程角色", db_character_id=1)["code"] == 0
    assert _transition(staged, "action_checkpoint")["code"] == 0

    context_raw = {"version_line": "release", "snapshot": {
        "version_line": "release", "snapshot_id": "snapshot.fixture",
        "upstream_commit": "a" * 40, "secret_future": 999,
    }, "items": [{"entity_type": "character", "id": 1, "status": "ok",
        "request": {"id": 1}, "content_fingerprint": "raw",
        "data": {"character": {"id": 1, "name": "工程角色", "element": "冰",
                                      "atk_90": 9999, "crit_rate": .95},
                 "skills": [{"id": 11, "character_id": 1, "slot": "skill",
                              "name": "工程战技", "effect_type": "damage",
                              "desc_template": "造成999%伤害", "cooldown": 10,
                              "params": {"10": {"param_list": [9.99]}}}],
                 "extra_info": [{"id": 12, "target_id": 1,
                                  "info_type": "sub_skill", "name": "派生动作",
                                  "content": "每2秒造成500%伤害"}],
                 "annotations": [{"text": "未来数值"}]}}]}
    context = _query(staged, {"request": {"characters": [{
        "id": 1, "sections": ["character", "skills", "extra_info"]}]}},
        lambda _db, _request: copy.deepcopy(context_raw), "context")
    saved_context = staged.resources[context["resource"]]
    skill = saved_context["items"][0]["data"]["skills"][0]
    assert skill["params"] == {"10": {"param_list": [9.99]}}
    assert skill["desc_template"] == "造成999%伤害"
    assert saved_context["items"][0]["data"]["character"]["atk_90"] == 9999
    assert "secret_future" not in saved_context["snapshot"]

    runtime_raw = {"contract_id": "fixture", "schema_version": 1, "status": "ready",
        "selector": {"character_id": 1, "variant": "tap", "effective_level": 10},
        "matched_profile_ids": ["profile.skill"], "coverage_status": "ready",
        "profile": {"profile_id": "profile.skill", "status": "confirmed",
                    "selector": {"character_id": 1, "variant": "tap"},
                    "emissions": [{"component_id": "hit", "prototype": {
                        "multiplier": 9.99}, "schedule": {"offset_seconds": .5}}],
                    "lifecycle": {"instance_end_offset_seconds": 10}},
        "issues": []}
    runtime = _query(staged, {"request": {"action": "action_dispatch_profile"}},
        lambda _db, _request: copy.deepcopy(runtime_raw), "runtime")
    saved_runtime = staged.resources[runtime["resource"]]
    assert saved_runtime == runtime_raw

    action = {"id": "action.skill", "character": "character-1",
              "action_type_id": "action.elemental_skill", "label": "元素战技",
              "start": 1, "variant": "tap", "notes": ""}
    assert _call(staged, "gs_patch_whiteboard", expected_revision=staged.revision,
        reason="写入动作骨架", operations=[{
            "op": "add", "path": "/actions/-", "value": action,
        }])["code"] == 0
    assert _transition(staged, "event_topology")["code"] == 0

    event_raw = {"status": "ready", "profile": {"profile_id": "profile.periodic",
        "emissions": [{"component_id": "periodic.hit", "kind": "periodic",
            "prototype": {"multiplier": 8.88, "damage_type_ids": ["damage.skill"]},
            "attack_source": {"kind": "curated", "candidate_id": "candidate.hit"},
            "application": {"mode": "sequence", "counter_tag": "skill.shared",
                            "gu": 2, "icd": "normal", "hit_count": 3},
            "schedule": {"kind": "periodic", "first_offset_seconds": .5,
                         "interval_seconds": 2, "duration_seconds": 8, "count": 4,
                         "cooldown": 10, "cost": 80},
            "payload": {"compiled_damage": 777}, "damage_ratio": 7.77,
            "execution": {"value": 666, "commands": ["apply_damage"]},
            "damage": 555, "amount": 444}],
        "lifecycle": {"establishment_offset_seconds": .1,
                      "instance_end_offset_seconds": 8,
                      "replacement_policy": "replace"},
        "description": "持续8秒，每2秒造成888%伤害"}}
    event = _query(staged, {"request": {"action": "periodic_attack_profile"}},
        lambda _db, _request: copy.deepcopy(event_raw), "runtime")
    saved_event = staged.resources[event["resource"]]
    assert saved_event == event_raw


def test_action_query_keeps_mechanics_but_scrubs_private_review_fields(staged):
    assert _call(staged, "gs_add_character", expected_revision=staged.revision,
                 reason="添加工程角色", db_character_id=1)["code"] == 0
    assert _transition(staged, "action_checkpoint")["code"] == 0
    raw = {
        "status": "ready",
        "selector": {"character_id": 1, "variant": "tap"},
        "matched_profile_ids": ["profile.reviewed"],
        "coverage_status": "ready",
        "profile": {
            "profile_id": "profile.reviewed",
            "status": "confirmed",
            "selector": {"character_id": 1, "variant": "tap"},
            "review": {"status": "reviewed", "date": "2026-10-22",
                       "reviewer": "fixture", "notes": "hidden"},
            "emissions": [{
                "component_id": "hit", "kind": "periodic",
                "schedule": {"first_offset_seconds": .5, "interval_seconds": 2,
                             "count": 9, "precision_seconds": .01},
                "evidence": {"timing": {"status": "confirmed"}},
                "prototype": {"multiplier": 9.99},
                "application": {"mode": "sequence", "counter_tag": "secret"},
            }],
            "lifecycle": {"establishment_offset_seconds": .1,
                          "instance_end_offset_seconds": 8,
                          "precision_seconds": .01,
                          "evidence": {"status": "confirmed"},
                          "replacement_policy": "replace", "secret": 999},
        },
        "issues": [],
    }
    result = _query(staged, {"request": {"action": "action_dispatch_profile"}},
                    lambda _db, _request: copy.deepcopy(raw), "runtime")
    profile = staged.resources[result["resource"]]["profile"]
    assert profile["review"] == {
        "status": "reviewed", "date": "2026-10-22", "reviewer": "fixture"}
    keys = set(_all_mapping_keys(profile))
    assert {"count", "prototype", "multiplier", "application", "counter_tag",
            "replacement_policy"} <= keys
    assert not ({"secret", "notes"} & keys)


def test_qualification_without_numeric_result_remains_diagnostic():
    missing = _calculated()
    missing["result"].pop("summary")
    assert delivery_state(missing)["calculation_status"] == "diagnostic"
    qualification = qualify_calculation(
        missing, topology_receipt=_qualification_topology("damage"),
        topology_binding_receipt={"status": "complete"},
        energy_intent="multi_round_legal")
    assert qualification["status"] == "rejected"
    assert "numeric_result_unavailable" in qualification["completion"]["reasons"]


def test_materialization_unknown_is_informational_but_incomplete_is_diagnostic():
    unknown = _calculated(materialization="unknown")
    incomplete = _calculated(materialization="incomplete")
    assert delivery_state(unknown)["calculation_status"] == "complete"
    assert delivery_state(incomplete)["calculation_status"] == "diagnostic"


def test_staged_skill_opens_the_complete_bundle_but_not_arbitrary_files(staged):
    listed = _call(staged, "gs_read_skill")
    assert listed["code"] == 0
    assert listed["data"]["resources"] == skill_resource_names()
    for resource in (
        "SKILL.md",
        "references/runtime/software-entrypoints.md",
        "references/runtime/native-agent.md",
        "references/mechanics/damage-formula.md",
        "references/workflow/focus/closure.md",
    ):
        assert _call(staged, "gs_read_skill", resource=resource)["code"] == 0

    assert _call(staged, "gs_read_skill", resource="../pyproject.toml")["reason"] == (
        "agent_resource_missing"
    )
    assert _call(
        staged,
        "gs_read_skill",
        resource="C:/Users/example/secret.md",
    )["reason"] == "agent_resource_missing"
    assert _call(
        staged,
        "gs_read_skill",
        resource="references/mechanics/reactions.json",
    )["reason"] == "agent_resource_missing"
    assert _call(staged, "gs_read_skill", resource="references/not-present.md")[
        "reason"
    ] == "agent_resource_missing"
