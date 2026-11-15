"""Independent adversarial checks of the complete App resource review context."""
from __future__ import annotations

import copy

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.files import context_view, fingerprint, input_view
from genshin_app.equipment.mechanics import prepare_mechanics
from genshin_app.equipment.schema import PlanError
from tests.test_shared_pool_app_delivery import authorize, pool_plan


def refill_plan():
    plan = pool_plan()
    plan["mechanics_model"]["shared_pools"]["operations"] = [{
        "id": "refill", "pool_id": "pool", "source_id": "binding:part",
        "trigger": {"event": "hit", "phase": "post"}, "order": 1,
        "operation": "grant", "amount": 1,
        "targets": {"mode": "all_pool_keys"},
        "cooldown": {"seconds": 0, "anchor": "success"},
        "status": "assumed", "source": "Independent invented refill, not a game rule",
    }]
    return plan


@pytest.mark.parametrize("mutation", [
    "capacity", "initial", "refill_amount", "refill_phase", "refill_cooldown",
    "refill_actor_filter", "consumer_amount", "consumer_filter", "actor_relation",
    "benefit_value", "benefit_duration", "evidence",
])
def test_every_semantic_resource_edit_requires_a_fresh_full_context_review(tmp_path, mutation):
    db = tmp_path / "never-created.db"
    plan = authorize(refill_plan(), db)
    before = run_plan_calc(plan, db)
    assert [h["expected"] for h in before["result"]["hits"]] == [90, 90, 90]
    model = plan["mechanics_model"]
    pool = model["shared_pools"]["pools"][0]
    op = model["shared_pools"]["operations"][0]
    consumer = model["shared_pools"]["consumers"][0]
    benefit = model["rules"][0]
    if mutation == "capacity": pool["capacity"] = 11
    elif mutation == "initial": pool["initial"]["team"] = 2
    elif mutation == "refill_amount": op["amount"] = 2
    elif mutation == "refill_phase": op["trigger"]["phase"] = "pre"
    elif mutation == "refill_cooldown": op["cooldown"]["seconds"] = 1
    elif mutation == "refill_actor_filter":
        op["trigger"]["characters"] = ["a"]
    elif mutation == "consumer_amount": consumer["amount"] = 2
    elif mutation == "consumer_filter": consumer["filter"]["elements"] = ["pyro"]
    elif mutation == "actor_relation": consumer["actor_relation"] = "owner"
    elif mutation == "benefit_value": benefit["effects"][0]["value"] = .5
    elif mutation == "benefit_duration": benefit["timing"]["duration"] = 5
    else: op["source"] = "Revised evidence, requiring another human/Agent review"
    native_before = copy.deepcopy(plan)
    out = compose_equipment_plan(plan, db)
    assert out["state"] == "pending", out
    assert any(i["code"] == "pool_source_scope_unreviewed" for i in out["missing_inputs"])
    assert "shared_pools" not in out["mechanics_model"]
    assert not any(r.get("resource_gate") for r in out["mechanics_model"]["rules"])
    with pytest.raises(PlanError):
        run_plan_calc(plan, db)
    assert plan == native_before and not db.exists()


def test_approval_of_just_old_rule_context_cannot_authorize_a_resource_graph(tmp_path):
    db = tmp_path / "never-created.db"
    plan = authorize(refill_plan(), db)
    previous = copy.deepcopy(plan)
    previous["mechanics_model"].pop("shared_pools")
    previous["mechanics_model"]["rules"][0].pop("resource_gate")
    dependency = plan["effect_bindings"][0]["parts"][0]["dependencies"]
    dependency["context_fingerprint"] = fingerprint(context_view(input_view(previous)))
    out = compose_equipment_plan(plan, db)
    assert out["state"] == "pending"
    assert all(r["status"] == "pending" for r in out["shared_pool_source_adoptions"])
    with pytest.raises(PlanError): run_plan_calc(plan, db)


def test_fresh_review_enables_the_changed_semantics_without_mutating_native_plan(tmp_path):
    db = tmp_path / "never-created.db"
    plan = authorize(refill_plan(), db)
    pool = plan["mechanics_model"]["shared_pools"]["pools"][0]
    pool["initial"]["team"] = 0
    assert compose_equipment_plan(plan, db)["state"] == "pending"
    authorize(plan, db)
    native_before = copy.deepcopy(plan)
    out = run_plan_calc(plan, db)
    assert [h["expected"] for h in out["result"]["hits"]] == [45, 90, 90]
    assert all(r["status"] == "ready" for r in out["report"]["shared_pool_source_adoptions"])
    assert plan == native_before and not db.exists()


def test_a_registered_ready_automatic_rule_does_not_approve_added_resource_logic():
    # Exercise the actual App binding stage with a ready compiler receipt. The
    # compiler identity approves only its delivered rule, not arbitrary pools.
    plan = refill_plan()
    part = copy.deepcopy(plan["effect_bindings"][0]["parts"][0])
    plan["effect_bindings"] = []
    plan["passive_sources"] = [{"id": "binding", "actor": "a"}]
    receipt = {"id": "binding", "actor": "a", "status": "ready",
               "parts": [part], "rule_ids": ["boost"]}
    issues, adoptions = [], []
    model, *_ = prepare_mechanics(
        plan, [], plan["manual_template"], "synthetic", lambda *args: issues.append(args),
        passive_compilations=[receipt], shared_pool_adoptions=adoptions,
    )
    assert "shared_pools" not in model and model["rules"] == []
    assert any(i[0] == "pool_source_scope_unreviewed" for i in issues)
    assert all(r["status"] == "pending" for r in adoptions)
