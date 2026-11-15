"""Synthetic DB -> composer -> Host-shaped native feedback -> real recovery."""
from __future__ import annotations

import copy
import uuid

from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.equipment import compose_equipment_plan
from tests.test_action_resources_app import confirm, resource_plan
from tests.test_runtime_action_resource import (  # noqa: F401
    fake_repo,
    resource_db,
    stub_fetcher,
)

# ruff: noqa: F811


def test_producer_binding_comparison_is_delivered_without_another_query(resource_db, tmp_path):
    plan = confirm(resource_plan(), resource_db)
    source = plan["action_resource_sources"][0]
    source["expected_scope_fingerprint"] = source["expected_resource_fingerprint"]
    current = CandidateSession(str(uuid.uuid4()), tmp_path / "candidate", resource_db, "native", plan)
    reply = current.call_tool("gs_calculate", {"expected_revision": 0})
    assert reply["code"] == 1 and reply["reason"] == "missing_input"
    primary = reply["failure_summary"]["primary_reported_issue"]
    assert primary["code"] == "source_changed"
    mismatch = primary["differences"]
    assert len(mismatch) == 1 and mismatch[0]["field"] == "expected_scope_fingerprint"
    assert mismatch[0]["actual"] == source["expected_resource_fingerprint"]
    fresh = compose_equipment_plan(plan, resource_db)
    expected = fresh["action_resource_compilations"][0]["required_confirmation"]["expected_scope_fingerprint"]
    assert mismatch[0]["expected"] == expected
    assert reply["latest_attempt"]["engine_execution"] == "not_run"
    restored = CandidateSession.load(current.session_id, current.root, resource_db)
    evidence = restored.call_tool("gs_read_context", {"resource": "calculation_attempt",
        "pointer": "/evidence/details/missing_inputs/0/differences"})
    assert evidence["code"] == 0 and [row["value"] for row in evidence["data"]["items"]] == mismatch
    replacement = copy.deepcopy(restored.candidate)
    replacement["action_resource_sources"][0]["expected_scope_fingerprint"] = expected
    restored.replace_candidate(replacement, 0, "核对本次动作绑定后修正")
    assert restored.call_tool("gs_calculate", {"expected_revision": restored.revision})["code"] == 0


def test_real_duplicate_event_reports_both_input_locations_and_survives_reload(tmp_path):
    from genshin_app.equipment import new_plan
    from genshin_app.equipment.mechanics import enable_mechanics
    from tests.test_mechanics_execution import fixture

    template, rotation, mechanics = fixture()
    template["characters"][0]["stats"]["energy_recharge"] = 1
    rotation["actions"][0]["id"] = "h0"
    for row in mechanics["hit_events"]:
        row["action_id"] = "h0"
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=mechanics)
    current = CandidateSession(str(uuid.uuid4()), tmp_path / "candidate", tmp_path / "absent.db", "native", plan)
    response = current.call_tool("gs_calculate", {"expected_revision": 0})
    assert response["code"] == 1, response
    primary = response["failure_summary"]["primary_reported_issue"]
    assert primary["code"] == "duplicate_id"
    conflict = next(row for row in primary["conflicts"] if row["id"] == "h0")
    assert {row["path"] for row in conflict["occurrences"]} == {
        "rotation_model.actions[0].id", "template.hits[0].id"}
    restored = CandidateSession.load(current.session_id, current.root, current.db_path)
    read = restored.call_tool("gs_read_context", {"resource": "calculation_attempt",
        "pointer": "/evidence/details/issues/0/conflicts"})
    assert read["code"] == 0 and read["data"]["items"][0]["value"] == conflict
