"""Independent source review and exclusion-closure checks without a game DB."""
import copy
from pathlib import Path

import pytest
from test_mechanics_execution import add, rule
from test_shared_pool_app_delivery import authorize, pool_plan
from test_shared_pool_sources import graph, invoke

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.schema import PlanError

ABSENT_DB = Path(__file__).resolve().parents[1] / "benchmarks/history/engine-automation/20261006/elemental-design/review-absent.db"


@pytest.mark.parametrize("kind", ["pools", "operations", "consumers"])
def test_each_resource_component_requires_current_context_review(kind):
    args = graph()
    reviews = {r["source_id"]: {"mode": "manual_context", "context_fingerprint": "current", "note": "Reviewed graph"}
               for r in args[1].values()}
    reviews[args[0]["shared_pools"][kind][0]["source_id"]]["mode"] = "source_only"
    compiled, disabled, _records, issues = invoke(*args, reviews=reviews)
    assert compiled is None and disabled == {"benefit"}
    assert any(i[0] == "pool_source_scope_unreviewed" for i in issues)


def test_excluded_pool_cannot_hide_forged_grant_owner():
    model, rules, active, linked = graph()
    active.remove("authority")
    rules["grant"]["owner"] = "forged"
    with pytest.raises(PlanError):
        invoke(model, rules, active, linked, ["authority"])


def test_excluded_pool_cannot_turn_unknown_grant_source_into_success():
    model, rules, active, linked = graph()
    active.remove("authority")
    model["shared_pools"]["operations"][0]["source_id"] = "unknown"
    compiled, disabled, _records, issues = invoke(model, rules, active, linked, ["authority"])
    assert compiled is None and "benefit" in disabled
    assert any(i[0] == "pool_source_pending" for i in issues)


def app_graph():
    plan = pool_plan()
    model = plan["mechanics_model"]
    pools = model["shared_pools"]
    pools["pools"][0]["source_id"] = "binding:authority"
    pool_rule = rule("authority", source_id="binding:authority")
    grant_rule = rule("grant", source_id="binding:grant")
    pool_rule["effects"] = []
    grant_rule["effects"] = []
    add(model, pool_rule, grant_rule)
    prototype = plan["effect_bindings"][0]["parts"][0]
    for ident in ("authority", "grant"):
        part = copy.deepcopy(prototype)
        part.update(part_id=ident, rule={"kind": "mechanics", "rule_id": ident})
        plan["effect_bindings"][0]["parts"].append(part)
    pools["operations"] = [{"id": "grant", "pool_id": "pool", "source_id": "binding:grant",
                             "trigger": {"event": "hit", "phase": "post", "event_ids": ["h0"]},
                             "order": 0, "operation": "grant", "amount": 1,
                             "targets": {"mode": "all_pool_keys"},
                             "cooldown": {"seconds": 0, "anchor": "success"},
                             "status": "assumed", "source": "Synthetic post-hit grant"}]
    return plan


def test_app_excluding_only_grant_preserves_pool_and_other_reviewed_damage():
    plan = app_graph()
    authorize(plan, ABSENT_DB)
    baseline = run_plan_calc(plan, ABSENT_DB)
    assert [hit["expected"] for hit in baseline["result"]["hits"]] == [90, 90, 45]
    part = next(p for p in plan["effect_bindings"][0]["parts"] if p["part_id"] == "grant")
    part.update(state="excluded", reason="This source does not apply")
    # Exclusion changes the explicit graph; remaining sources are re-reviewed.
    authorize(plan, ABSENT_DB)
    before = copy.deepcopy(plan)
    output = run_plan_calc(plan, ABSENT_DB)
    assert before == plan
    assert [hit["expected"] for hit in output["result"]["hits"]] == [90, 45, 45]
    model = output["report"]["mechanics_model"]
    assert model["shared_pools"]["operations"] == []
    assert len(model["shared_pools"]["pools"]) == len(model["shared_pools"]["consumers"]) == 1


def test_app_excluded_pool_removes_entire_dependent_gate_and_match():
    plan = app_graph()
    part = next(p for p in plan["effect_bindings"][0]["parts"] if p["part_id"] == "authority")
    part.update(state="excluded", reason="Pool unavailable for this scenario")
    plan["rotation_tuning"]["buff_hit_matches"] = [{
        "id": "include", "effect_ref": {"buff_id": "boost", "effect_id": "value"},
        "mode": "adjust", "include_hit_ids": ["h0"], "exclude_hit_ids": [],
        "reason": "Cannot recreate unavailable pool", "source_refs": [{"kind": "manual", "note": "Synthetic"}],
        "state": "pending", "basis_fingerprint": None}]
    authorize(plan, ABSENT_DB)
    before = copy.deepcopy(plan)
    output = run_plan_calc(plan, ABSENT_DB)
    assert before == plan
    assert [hit["expected"] for hit in output["result"]["hits"]] == [45, 45, 45]
    model = output["report"]["mechanics_model"]
    assert "shared_pools" not in model
    assert all(rule["id"] != "boost" for rule in model["rules"])
    assert all(row["rule_id"] != "boost" for row in model["initial_state"]["rules"])
    preview = next(p for p in output["report"]["match_previews"] if p["id"] == "include")
    assert preview["status"] == "resource_dependency_unavailable"
    assert preview["final_hit_ids"] == []


def test_app_unknown_source_under_excluded_pool_is_still_pending():
    plan = app_graph()
    part = next(p for p in plan["effect_bindings"][0]["parts"] if p["part_id"] == "authority")
    part.update(state="excluded", reason="Pool unavailable")
    plan["mechanics_model"]["shared_pools"]["operations"][0]["source_id"] = "unknown"
    authorize(plan, ABSENT_DB)
    report = compose_equipment_plan(plan, ABSENT_DB)
    assert report["state"] == "pending"
    assert any(i["code"] == "pool_source_pending" for i in report["missing_inputs"])
    with pytest.raises(PlanError):
        run_plan_calc(plan, ABSENT_DB)
