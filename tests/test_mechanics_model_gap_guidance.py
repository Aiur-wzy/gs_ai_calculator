"""Missing Agent-authored models are actionable inputs, not missing DB facts."""
from __future__ import annotations

import copy
import uuid

import pytest

from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.agent_chat.native_app_tool_host import (
    _derived_view,
    _durable_candidate_view,
)
from genshin_app.agent_chat.tools import _calculation_preflight_details
from genshin_app.domain_errors import classify_domain_failure
from genshin_app.equipment import compose_equipment_plan, new_plan
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.mechanics import prepare_mechanics
from tests.test_mechanics_execution import fixture
from tests.test_native_app_tool_host import call, host


@pytest.mark.parametrize("missing", [("mechanics_model",), ("rotation_model",),
                                    ("rotation_model", "mechanics_model")])
def test_producer_names_only_actual_missing_models_with_existing_materialization_category(missing):
    plan = {"plan_version": 3, "mechanics_model": {}, "rotation_model": {}}
    plan.update(dict.fromkeys(missing))
    rows = []
    prepare_mechanics(plan, [], {"characters": []}, "source", lambda *args, **kw: rows.append((args, kw)))
    assert {args[1] for args, _ in rows} == set(missing)
    for args, metadata in rows:
        assert args[0] == "missing_input"
        assert metadata["category"] == "knowledge_missing"
        assert metadata["blocking_scope"] == "agent_materialization"
        assert metadata["consumer"] == "mechanics.execution_graph"
        assert metadata["required"] is metadata["consumed"] is True
        assert metadata["impact"] == "high"
        assert "/" + args[1] in metadata["repair_hint"] and "gs_patch_candidate" in metadata["repair_hint"]
        if args[1] == "mechanics_model":
            assert "mechanics_capabilities" in metadata["repair_hint"]
            assert "/empty_model" in metadata["repair_hint"] and "/schema" in metadata["repair_hint"]
            assert "plan_schema" in metadata["repair_hint"]


def _object(page):
    assert page["kind"] == "object" and page["next_offset"] is None
    return {row["key"]: row["value"] for row in page["items"]}


def test_real_artifact_host_reports_model_bootstrap_then_preserves_existing_actions(tmp_path):
    template, rotation, _model = fixture((.2,))
    plan = new_plan(template)
    plan.update(plan_version=3, rotation_model=rotation, mechanics_model=None)
    session = ArtifactCandidateSession(uuid.uuid4().hex, tmp_path / "session", tmp_path / "absent.db", "native", plan)
    actions_before = fingerprint(session.candidate["rotation_model"])
    state_before = copy.deepcopy({"derived": _derived_view(session), "durable": _durable_candidate_view(session)})
    checkpoint = (session.root / "checkpoint.json").read_bytes()
    current = host(tmp_path / "ledger", session, lambda s, name, arguments: s.call_tool(name, arguments))
    terminal = call(current, tool="gs_calculate", raw='{"expected_revision":0}')
    assert terminal["terminal_state"] == "settled_failure" and terminal["terminal_reason"] == "missing_input"
    assert terminal["state_changes"] == []
    assert {"derived": _derived_view(session), "durable": _durable_candidate_view(session)} == state_before
    assert (session.root / "checkpoint.json").read_bytes() == checkpoint
    failure = terminal["result"]
    assert failure["category"] == "knowledge_missing" and failure["blocking_scope"] == "agent_materialization"
    assert failure["details"]["missing_inputs_total"] == 1
    issue = failure["details"]["missing_inputs"][0]
    assert issue["path"] == "mechanics_model" and "/empty_model" in issue["repair_hint"]
    assert failure["repair_hint"] == issue["repair_hint"]
    assert failure["details"]["recovery"]["tool"] == "gs_compose"
    assert failure["details"]["report_updated"] is False
    skeleton = session.call_tool("gs_read_context", {"resource": "mechanics_capabilities", "pointer": "/empty_model"})
    assert skeleton["code"] == 0
    assert session.call_tool("gs_read_context", {"resource": "mechanics_capabilities", "pointer": "/schema"})["code"] == 0
    assert session.call_tool("gs_read_context", {"resource": "plan_schema", "pointer": "/properties/mechanics_model"})["code"] == 0
    patched = session.call_tool("gs_patch_candidate", {"expected_revision": 0, "reason": "仅补合法机制输入骨架，不宣称事件已完整",
        "operations": [{"op": "add", "path": "/mechanics_model", "value": _object(skeleton["data"])}]})
    assert patched["code"] == 0, patched
    assert fingerprint(session.candidate["rotation_model"]) == actions_before
    composed = session.call_tool("gs_compose", {"expected_revision": 1})
    assert composed["code"] == 0
    assert not any(row["path"] == "mechanics_model" for row in session.report["missing_inputs"])
    assert session.candidate["manual_template"]["hits"]  # A real declared hit still needs its execution binding.
    assert session.candidate["mechanics_model"]["hit_events"] == []
    calculation = session.call_tool("gs_calculate", {"expected_revision": 1})
    assert calculation["code"] == 1  # Existing declared-hit/topology checks remain unaccepted.
    assert session.calculation_acceptance()["can_accept"] is False
    assert session.summary()["applicable"] is False
    assert fingerprint(session.candidate["rotation_model"]) == actions_before


@pytest.mark.parametrize("identity", [{}, {"owner": "a", "source_id": ""},
                                    {"owner": "not-in-team", "source_id": "manual.source"}])
def test_missing_manual_buff_owner_or_source_is_materialization_not_database_failure(tmp_path, identity):
    template, rotation, model = fixture((.2,))
    template["buffs"] = [{"id": "manual.buff", "start": 0, "end": 1, "extensions": identity,
        "effects": [{"id": "e", "benefit_type_id": "benefit.dmg_bonus.all", "zone": "dmg_bonus",
                     "stat": "all", "value": .1, "applies_to": {"mode": "all"}}]}]
    plan = new_plan(template)
    plan.update(plan_version=3, rotation_model=rotation, mechanics_model=model)
    report = compose_equipment_plan(plan, tmp_path / "absent.db")
    rows = [row for row in report["missing_inputs"] if row.get("consumer") == "buff.source.manual.buff"]
    assert len(rows) == 1
    row = rows[0]
    assert row["category"] == "knowledge_missing" and row["blocking_scope"] == "agent_materialization"
    assert row["required"] is row["consumed"] is True and row["impact"] == "high"
    assert "gs_patch_candidate" in row["repair_hint"] and "/manual_template/buffs/0/extensions" in row["repair_hint"]
    assert "owner" in row["repair_hint"] and "source_id" in row["repair_hint"]


def test_preflight_specific_input_hint_wins_without_removing_full_report_recovery():
    result = _calculation_preflight_details({"state": "pending", "missing_inputs": [
        {"code": "effect_pending", "action": "review_effect"},
        {"code": "missing_input", "path": "mechanics_model", "repair_hint": "Read mechanics_capabilities /empty_model"},
    ]}, 8)
    assert result["repair_hint"] == "Read mechanics_capabilities /empty_model"
    assert result["recovery"] == {"action": "compose_then_read_report", "tool": "gs_compose",
        "expected_revision": 8, "resource": "report", "retry_same_request": False}
    assert result["report_updated"] is False


@pytest.mark.parametrize("reason,category,scope", [
    ("weapon_not_found", "core_data_missing", "task_capability"),
    ("engine_capability_missing", "engine_capability_missing", "engine_capability"),
])
def test_real_data_and_engine_failures_keep_their_original_classification(reason, category, scope):
    result = classify_domain_failure(reason)
    assert result["category"] == category and result["blocking_scope"] == scope
