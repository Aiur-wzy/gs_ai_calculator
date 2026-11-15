"""Counter views need their own source review and monotone full-graph closure."""
from __future__ import annotations

import copy

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.resource_counter_views import prepare_resource_counter_views
from genshin_app.equipment.schema import PlanError
from tests.test_mechanics_execution import add, rule
from tests.test_resource_counter_app import counter_plan
from tests.test_resource_counter_view_execution import view_case
from tests.test_shared_pool_app_delivery import authorize


def view_plan():
    """A complete importable native App plan, with independent view authority."""
    plan = counter_plan()
    model = plan["mechanics_model"]
    model["rules"][0]["effects"][0]["value"] = 0
    reference = view_case()
    for old, new in zip(plan["manual_template"]["hits"], reference[0]["hits"], strict=True):
        old["t"] = new["t"]
    model["resource_counter_views"] = copy.deepcopy(reference[2]["resource_counter_views"])
    model["resource_counter_views"]["views"][0]["source_id"] = "binding:view"
    add(model, rule("view", source_id="binding:view", domain="provider", value={
        "kind": "counter_view", "view_id": "read", "factor": 1, "sample": "current"}))
    part = copy.deepcopy(plan["effect_bindings"][0]["parts"][0])
    part.update(part_id="view", label="资源读数独立来源")
    part["rule"]["rule_id"] = "view"
    plan["effect_bindings"][0]["parts"].append(part)
    return plan


def raw(*, mutate=None, excluded=(), pending=(), missing_pools=False, missing_counters=False, removed_active=(), reverse=False):
    plan = view_plan()
    model = plan["mechanics_model"]
    if mutate: mutate(model)
    if reverse:
        model["rules"].reverse()
        for field, groups in (("shared_pools", ("pools", "operations", "consumers")),
                              ("state_runtime", ("lifecycles", "timers")),
                              ("resource_counters", ("counters",)), ("resource_counter_views", ("views",))):
            for group in groups: model[field][group].reverse()
    rules = {r["id"]: r for r in model["rules"]}
    active = set(rules)-set(excluded)-set(pending)
    reviews = {r["source_id"]: {"mode": "manual_context", "context_fingerprint": "current", "note": "工程全图复核"} for r in rules.values()}
    groups = (("shared_pools", ("pools", "operations", "consumers")), ("state_runtime", ("lifecycles", "timers")),
              ("resource_counters", ("counters",)))
    records = [[{"kind": group, "id": row["id"], "source_id": row["source_id"], "status": "ready"}
                for group in parts for row in model[field][group]] for field, parts in groups]
    before = copy.deepcopy((model, records))
    issues = []
    out = prepare_resource_counter_views(model, rules, active, active-set(removed_active), set(rules), set(excluded), lambda *a: issues.append(a),
        source_reviews=reviews, context_fingerprint="current", pools=None if missing_pools else model["shared_pools"],
        state=model["state_runtime"], counters=None if missing_counters else model["resource_counters"],
        pool_adoptions=records[0], state_adoptions=records[1], counter_adoptions=records[2], character_ids={"a"})
    assert (model, records) == before
    return out, issues


def test_native_view_sources_run_with_real_damage_and_source_receipts(tmp_path):
    db = tmp_path/"absent.db"
    plan = authorize(view_plan(), db)
    before = copy.deepcopy(plan)
    out = run_plan_calc(plan, db)
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([81, 117, 130.5])
    assert out["report"]["resource_counter_view_adoptions"][0]["status"] == "ready"
    assert plan == before


def test_excluding_only_view_preserves_independent_counter_and_spend(tmp_path):
    plan = view_plan()
    plan["effect_bindings"][0]["parts"][1].update(state="excluded", reason="工程情景不使用此读数")
    db = tmp_path/"absent.db"
    out = run_plan_calc(authorize(plan, db), db)
    assert [h["expected"] for h in out["result"]["hits"]] == [45, 45, 45]
    assert "resource_counter_views" not in out["result"]
    assert out["result"]["resource_counters"]["state"]["values"] == {"count": {"total": 19}}
    assert [r["accepted"] for r in out["result"]["shared_pools"]["trace"]] == [8, 8, 3]


def test_source_only_confirmation_cannot_authorize_new_cap(tmp_path):
    db = tmp_path/"absent.db"
    plan = authorize(view_plan(), db)
    plan["effect_bindings"][0]["parts"][1]["dependencies"]["mode"] = "source_only"
    out = compose_equipment_plan(plan, db)
    assert out["state"] == "pending"
    assert "resource_counter_views" not in out["mechanics_model"]
    assert any(i["code"] == "counter_view_source_scope_unreviewed" for i in out["missing_inputs"])


@pytest.mark.parametrize("mutate", [
    lambda m: m["resource_counter_views"]["views"][0].update(counter_id="missing"),
    lambda m: m["resource_counter_views"]["views"][0].update(owner="b"),
    lambda m: m["resource_counter_views"]["views"][0].update(key={"kind": "recipient"}),
    lambda m: m["resource_counter_views"]["views"][0]["transform"].update(max_atoms=0),
    lambda m: m["rules"][1]["effects"][0].update(domain="self"),
])
def test_excluded_view_still_validates_original_graph(mutate):
    with pytest.raises(PlanError): raw(mutate=mutate, excluded={"view"})


def test_no_delivered_pool_never_resurrects_from_original():
    out, issues = raw(missing_pools=True)
    assert out["pools"] is None and out["views"] is None
    assert out["view_adoptions"][0]["effective_status"] == "dependency_pending" and issues


def test_ready_view_source_with_pending_counter_dependency_stays_pending():
    out, issues = raw(mutate=lambda m: m["resource_counters"]["counters"][0].update(status="pending"))
    assert out["view_adoptions"][0]["own_source_status"] == "ready"
    assert out["view_adoptions"][0]["effective_status"] == "dependency_pending"
    assert out["views"] is None and issues


def test_pending_cannot_be_laundered_by_separate_exclusion():
    out, issues = raw(excluded={"view"}, pending={"boost"})
    reversed_out, _ = raw(excluded={"view"}, pending={"boost"}, reverse=True)
    record = out["view_adoptions"][0]
    assert record["own_source_status"] == "excluded"
    assert record["effective_status"] == "dependency_pending" and issues
    assert out == reversed_out


def test_view_authority_dependency_closes_pool_state_counter_and_view():
    def cycle(model):
        model["shared_pools"]["pools"][0]["source_id"] = "binding:view"
    a, _ = raw(mutate=cycle, excluded={"view"})
    b, _ = raw(mutate=cycle, excluded={"view"}, reverse=True)
    assert a == b
    assert all(a[k] is None for k in ("pools", "state", "counters", "views"))
    assert "view" in a["disabled_rules"]


def test_unknown_source_is_pending_without_inventing_authority():
    out, issues = raw(mutate=lambda m: m["resource_counter_views"]["views"][0].update(source_id="unknown"))
    assert out["views"] is None and out["view_adoptions"][0]["own_source_status"] == "pending"
    assert "view" in out["disabled_rules"] and issues


def test_old_helper_removed_authority_never_revives_ready_view_source():
    out, issues = raw(removed_active={"boost"})
    assert out["view_adoptions"][0]["own_source_status"] == "ready"
    assert out["view_adoptions"][0]["effective_status"] == "dependency_pending"
    assert out["views"] is None and out["counters"] is None and issues


def test_missing_actual_counter_with_ready_receipt_is_pending():
    out, issues = raw(missing_counters=True)
    assert out["counters"] is None and out["views"] is None and issues


def test_view_edit_invalidates_context_without_mutating_original_source(tmp_path):
    db = tmp_path/"absent.db"
    plan = authorize(view_plan(), db)
    plan["mechanics_model"]["resource_counter_views"]["views"][0]["transform"]["max_atoms"] = 10
    before = copy.deepcopy(plan)
    out = compose_equipment_plan(plan, db)
    assert out["state"] == "pending" and "resource_counter_views" not in out["mechanics_model"]
    assert plan == before


def test_pending_view_declaration_closes_only_readers():
    out, issues = raw(mutate=lambda m: m["resource_counter_views"]["views"][0].update(status="pending"))
    assert out["views"] is None and "view" in out["disabled_rules"] and issues
    assert out["pools"] and out["state"] and out["counters"]
    assert out["counter_adoptions"][0]["status"] == "ready"


def test_full_original_view_validation_runs_before_old_pruners(monkeypatch, tmp_path):
    import genshin_app.equipment.shared_pools as old
    plan = view_plan()
    plan["effect_bindings"][0]["parts"][1].update(state="excluded", reason="工程明确排除")
    plan["mechanics_model"]["resource_counter_views"]["views"][0]["counter_id"] = "unknown"
    def should_not_run(*args, **kwargs):
        pytest.fail("original view graph was not validated before pool pruning")
    monkeypatch.setattr(old, "prepare_shared_pools", should_not_run)
    with pytest.raises(PlanError):
        compose_equipment_plan(plan, tmp_path/"absent.db")
