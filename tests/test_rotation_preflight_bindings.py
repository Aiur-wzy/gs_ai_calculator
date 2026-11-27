"""Public inspection of v3 bindings and recoverable declared energy failures."""

from copy import deepcopy
import json
import uuid

import pytest

from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.cli import main
from genshin_app.equipment import new_plan, save_plan
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.rotation_check import check_document
from tests.test_mechanics_execution import EVIDENCE, REPEAT, action, ability, fixture
from tests.test_plan_mechanics import native as bound_plan, review


def native_fixture():
    template, rotation, mechanics = fixture((.2, .3))
    template["characters"][0]["stats"]["energy_recharge"] = 1
    plan = enable_mechanics(new_plan(template))
    return {**plan, "rotation_model": rotation, "mechanics_model": mechanics}


@pytest.mark.parametrize("case", ["mechanics", "legacy", "both", "conflict", "dangling", "absent"])
def test_binding_scope_uses_stable_identity(case, tmp_path):
    plan = native_fixture()
    model, mechanics = plan["rotation_model"], plan["mechanics_model"]
    if case == "legacy":
        mechanics["hit_events"] = []
    if case in {"legacy", "both", "conflict"}:
        model["action_hit_links"] = [{"hit_id": h, "action_id": "cast"} for h in ("h0", "h1")]
    if case == "conflict":
        model["actions"].append(action("other", start=.1, end=.2, order=1))
        model["action_hit_links"][0]["action_id"] = "other"
    if case == "dangling":
        mechanics["hit_events"][0]["action_id"] = "missing-cast"
    if case == "absent":
        mechanics["hit_events"] = []
    before = deepcopy(plan)
    check = check_document(plan, tmp_path / "absent.db", preflight=True)
    assert plan == before
    summary = check["preflight"]["checks"]
    expected = "failed" if case in {"conflict", "dangling"} else "not_checked" if case == "absent" else "passed"
    assert summary["action_associations"]["status"] == expected
    if case == "conflict":
        issue = summary["action_associations"]["first_issue"]
        assert issue["code"] == "action_hit_link_conflict"
        assert {row["action_id"] for row in issue["conflicting_links"]} == {"cast", "other"}
        assert {row["source_path"] for row in issue["conflicting_links"]} == {
            "rotation_model.action_hit_links[0]", "mechanics_model.hit_events[0]",
        }
    if case == "legacy":
        assert summary["action_associations"]["legacy_only_bindings"] == 2
    elif case == "both":
        assert summary["action_associations"]["legacy_only_bindings"] == 0
    assert summary["action_timing"]["status"] == "passed"
    assert summary["declared_energy"]["status"] == "not_applicable"


def test_hit_pattern_uses_runtime_occurrences(tmp_path):
    plan = native_fixture()
    plan["manual_template"]["hits"] = plan["manual_template"]["hits"][:1]
    plan["mechanics_model"]["hit_events"] = []
    plan["mechanics_model"]["hit_patterns"] = [{
        "id": "pattern", "prototype_hit_id": "h0", "action_id": "cast",
        "offsets": [.2, .3], "orders": [10, 11],
    }]
    check = check_document(plan, tmp_path / "absent.db", preflight=True)
    bindings = check["association_check"]["bindings"]
    assert [(r["hit_id"], r["action_id"], r["order"]) for r in bindings] == [
        ("pattern:1", "cast", 10), ("pattern:2", "cast", 11),
    ]
    full = check_document(plan, tmp_path / "absent.db")
    assert full["status"] == check["preflight"]["declared_status"] == "passed"


def test_source_pending_is_an_explicit_skip(tmp_path):
    plan = bound_plan()
    check = check_document(plan, tmp_path / "absent.db", preflight=True)
    assert check["input_fingerprint"]
    for row in check["preflight"]["checks"].values():
        assert row["status"] == "not_checked"
        assert row["first_issue"]["first_missing_input"]["code"] == "effect_pending"
    assert not check["preflight"]["closure_verified"]


def test_missing_model_does_not_claim_energy_passed(tmp_path):
    plan = native_fixture()
    plan["rotation_model"] = None
    check = check_document(plan, tmp_path / "absent.db", preflight=True)
    assert check["preflight"]["declared_status"] == "not_checked"
    assert check["preflight"]["checks"]["declared_energy"]["status"] != "passed"


def burst_fixture(energy):
    plan = native_fixture()
    rotation = plan["rotation_model"]
    rotation["characters"][0]["initial_energy"] = energy
    rotation["abilities"] = [ability(kind="action.elemental_burst", energy={"required": 10, "amount": 10})]
    rotation["events"] = [
        {"id": "spend", "type": "energy_spend", "t": 0, "order": 1, "action_id": "cast",
         "character": "a", "required": 10, "amount": 10, "repeat": REPEAT, **EVIDENCE},
        {"id": "return", "type": "energy_direct", "t": 1, "order": 2, "amounts": {"a": 10},
         "repeat": REPEAT, **EVIDENCE},
    ]
    return plan


def test_undeclared_hit_does_not_skip_energy_check(tmp_path):
    plan = burst_fixture(9.75)
    plan["mechanics_model"]["hit_events"].pop()
    check = check_document(plan, tmp_path / "absent.db", preflight=True)
    checks = check["preflight"]["checks"]
    assert checks["action_associations"]["status"] == "not_checked"
    assert checks["declared_energy"]["status"] == "failed"
    failure = checks["declared_energy"]["first_issue"]
    assert (failure["code"], failure["action_id"], failure["character"], failure["t"]) == (
        "insufficient_energy", "cast", "a", 0,
    )
    assert (failure["required"], failure["before"], failure["deficit"]) == (10, 9.75, .25)


def test_derived_resources_do_not_become_declared_proof(tmp_path):
    plan = burst_fixture(10)
    plan["rotation_model"]["events"].pop()
    mechanics = plan["mechanics_model"]
    bound = bound_plan()
    mechanics["rules"] = bound["mechanics_model"]["rules"]
    mechanics["initial_state"] = bound["mechanics_model"]["initial_state"]
    plan["effect_bindings"] = bound["effect_bindings"]
    mechanics["rules"][0]["resources"] = [{
        "id": "refund", "kind": "energy_direct", "delay": 0,
        "amounts": {"a": 10}, "source_part": "refund",
    }]
    mechanics["resource_ownership"] = [{
        "rule_id": "boost", "action_id": "cast", "source_part": "refund", "replaces_event_ids": [],
    }]
    review(plan, tmp_path / "absent.db")
    check = check_document(plan, tmp_path / "absent.db", preflight=True)
    assert check["preflight"]["checks"]["declared_energy"]["status"] == "failed"
    assert not check["preflight"]["mechanics_executed"]
    full = check_document(plan, tmp_path / "absent.db")
    assert full["status"] == "passed"


def test_native_candidate_and_cli_report_same_failure_and_recover(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("genshin_app.cli._require_runtime_database", lambda _: None)
    plan = burst_fixture(9.75)
    session = ArtifactCandidateSession(str(uuid.uuid4()), tmp_path / "session", tmp_path / "genshin.db", "native", plan)
    path = tmp_path / "draft.plan.json"
    save_plan(path, session.candidate)
    assert main(["--data-dir", str(tmp_path), "engine", "check-rotation", str(path), "--preflight"]) == 0
    cli = json.loads(capsys.readouterr().out)["data"]
    before = (session.root / "checkpoint.json").read_bytes()
    request = {"expected_revision": 0, "request": {"view": "rotation_preflight"}}
    response = session.call_tool("gs_diagnose_result", request)
    assert response["code"] == 0, response
    assert response["data"]["preflight"] == cli["preflight"]
    assert (session.root / "checkpoint.json").read_bytes() == before
    assert cli["preflight"]["checks"]["declared_energy"]["status"] == "failed"

    replacement = deepcopy(session.candidate)
    replacement["rotation_model"]["characters"][0]["initial_energy"] = 10
    session.replace_candidate(replacement, 0, "纠正初始能量")
    restored = ArtifactCandidateSession.load(session.session_id, session.root, session.db_path)
    result = restored.call_tool("gs_diagnose_result", {**request, "expected_revision": restored.revision})
    assert result["code"] == 0 and result["data"]["preflight"]["checks"]["declared_energy"]["status"] == "passed"
    assert restored.call_tool("gs_calculate", {"expected_revision": restored.revision})["code"] == 0
