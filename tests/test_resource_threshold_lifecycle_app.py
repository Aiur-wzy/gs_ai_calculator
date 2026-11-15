"""Private actual App graphs; all declarations are synthetic engineering facts."""
from __future__ import annotations

import copy

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.schema import PlanError
from genshin_engine.state_runtime_mechanics import route_event_id
from tests.test_mechanics_execution import add, rule
from tests.test_plan_mechanics import native
from tests.test_resource_threshold_lifecycle_execution import life_case
from tests.test_resource_threshold_v2_app import part
from tests.test_shared_pool_app_delivery import authorize


def lifecycle_plan(*, end=False):
    plan = native()
    template, rotation, model = life_case(end=end)
    template["characters"][0]["stats"]["energy_recharge"] = 1
    plan.update(manual_template=template, rotation_model=rotation, mechanics_model=model)
    boost = model["rules"][-1]
    model["rules"] = []
    model["initial_state"]["rules"] = []
    parts = plan["effect_bindings"][0]["parts"]
    prototype = copy.deepcopy(parts[0])
    parts.clear()
    for name in ("pool", "life_a", "timer_a", "counter", "observer", "life_b", "timer_b"):
        authority = rule(name, source_id="binding:"+name)
        authority["effects"] = []
        if name == "timer_b":
            authority = copy.deepcopy(boost)
            authority["source_id"] = "binding:timer_b"
        add(model, authority)
        record = copy.deepcopy(prototype)
        record.update(part_id=name, label="工程来源 "+name)
        record["rule"]["rule_id"] = authority["id"]
        parts.append(record)
    model["shared_pools"]["pools"][0]["source_id"] = "binding:pool"
    model["shared_pools"]["operations"][0]["source_id"] = "binding:timer_a"
    for index, name in enumerate(("a", "b")):
        model["state_runtime"]["lifecycles"][index]["source_id"] = "binding:life_"+name
        model["state_runtime"]["timers"][index]["source_id"] = "binding:timer_"+name
    model["resource_counters"]["counters"][0]["source_id"] = "binding:counter"
    for observer in model["resource_threshold_observers"]["observers"]: observer["source_id"] = "binding:observer"
    for binding in model["resource_threshold_triggers"]["bindings"]: binding["source_id"] = "binding:life_b"
    return plan


@pytest.mark.parametrize("end,expected", [(False, [45,90,45,90,45]), (True, [45,90,45,45,45])])
def test_complete_source_to_app_actual_lifecycle_without_manual_b_action(tmp_path, end, expected):
    db = tmp_path / "absent.db"
    plan = authorize(lifecycle_plan(end=end), db)
    before = copy.deepcopy(plan)
    out = run_plan_calc(plan, db)
    assert [h["expected"] for h in out["result"]["hits"]] == expected
    assert plan == before and len(plan["rotation_model"]["actions"]) == 1
    assert not plan["mechanics_model"]["condition_events"] and not db.exists()


@pytest.mark.parametrize("name", ["life_b", "timer_b", "observer"])
def test_excluded_downstream_keeps_independent_consumption_and_counter(tmp_path, name):
    db = tmp_path / "absent.db"
    plan = lifecycle_plan(end=True)
    part(plan, name).update(state="excluded", reason="明确不纳入本工程情景")
    out = run_plan_calc(authorize(plan, db), db)
    assert [h["expected"] for h in out["result"]["hits"]] == [45]*5
    assert out["result"]["resource_counters"]["applied_state"]["values"]["count"]["total"] == 19
    assert [r["accepted"] for r in out["result"]["shared_pools"]["trace"] if r.get("operation") == "spend"] == [8,8,3]
    assert {r["id"] for r in out["report"]["mechanics_model"]["state_runtime"]["lifecycles"]} == {"field"}


def test_downstream_pending_with_fresh_context_keeps_independent_a_graph(tmp_path):
    db = tmp_path / "absent.db"
    plan = lifecycle_plan()
    plan["mechanics_model"]["state_runtime"]["lifecycles"][1]["status"] = "pending"
    report = compose_equipment_plan(authorize(plan, db), db)
    assert report["state"] == "pending"
    assert {r["id"] for r in report["mechanics_model"]["state_runtime"]["lifecycles"]} == {"field"}
    assert report["mechanics_model"]["shared_pools"]["operations"]
    assert report["mechanics_model"]["resource_counters"]["counters"]
    with pytest.raises(PlanError): run_plan_calc(authorize(plan, db), db)


def test_real_context_change_does_not_preserve_stale_upstream_approval(tmp_path):
    db = tmp_path / "absent.db"
    plan = authorize(lifecycle_plan(), db)
    plan["mechanics_model"]["state_runtime"]["timers"][1]["interval_us"] += 1
    report = compose_equipment_plan(plan, db)
    assert report["state"] == "pending"
    assert "state_runtime" not in report["mechanics_model"]
    assert "resource_counters" not in report["mechanics_model"]


@pytest.mark.parametrize("reverse", [False, True])
def test_mixed_excluded_and_pending_group_stays_pending_without_changing_own_source(tmp_path, reverse):
    db = tmp_path / "absent.db"
    plan = lifecycle_plan(end=True)
    part(plan, "life_b").update(state="excluded", reason="明确排除 B")
    plan["mechanics_model"]["state_runtime"]["timers"][1]["status"] = "pending"
    if reverse:
        plan["mechanics_model"]["rules"].reverse()
        plan["mechanics_model"]["state_runtime"]["lifecycles"].reverse()
        plan["mechanics_model"]["state_runtime"]["timers"].reverse()
        plan["mechanics_model"]["resource_threshold_triggers"]["bindings"].reverse()
    report = compose_equipment_plan(authorize(plan, db), db)
    assert report["state"] == "pending"
    life = next(r for r in report["state_runtime_source_adoptions"] if r["kind"] == "lifecycles" and r["id"] == "field_b")
    assert life["own_source_status"] == "excluded" and life["effective_status"] == "dependency_pending"
    assert {r["id"] for r in report["mechanics_model"]["state_runtime"]["lifecycles"]} == {"field"}
    assert report["mechanics_model"]["resource_counters"]["counters"]


def test_shared_authority_is_a_real_dependency_and_closes_both_groups(tmp_path):
    db = tmp_path / "absent.db"
    plan = lifecycle_plan()
    plan["mechanics_model"]["shared_pools"]["pools"][0]["source_id"] = "binding:life_b"
    part(plan, "life_b").update(state="excluded", reason="共同来源不适用")
    out = run_plan_calc(authorize(plan, db), db)
    assert [r["expected"] for r in out["result"]["hits"]] == [45]*5
    assert "state_runtime" not in out["report"]["mechanics_model"]
    assert "resource_counters" not in out["report"]["mechanics_model"]


@pytest.mark.parametrize("change", ["owner", "ref_id", "zero_offset", "end_binding"])
def test_excluded_target_never_hides_invalid_original_graph(tmp_path, change):
    plan = lifecycle_plan(end=True)
    part(plan, "life_b").update(state="excluded", reason="排除仍必须核原图")
    model = plan["mechanics_model"]
    if change == "owner": model["state_runtime"]["lifecycles"][1]["owner"] = "absent"
    elif change == "ref_id": model["resource_threshold_triggers"]["bindings"][0]["payload"]["ref_id"] = "absent"
    elif change == "zero_offset": model["state_runtime"]["timers"][1]["first_offset_us"] = 0
    else: model["state_runtime"]["lifecycles"][1]["end_trigger"]["binding_id"] = "absent"
    with pytest.raises(PlanError): compose_equipment_plan(plan, tmp_path / "absent.db")


@pytest.mark.parametrize("change", ["timer", "qualification", "rule_owner", "rule_value"])
def test_final_delivery_cannot_rewrite_approved_target_graph(tmp_path, monkeypatch, change):
    from genshin_app.equipment import state_runtime_v2
    db = tmp_path / "absent.db"
    plan = authorize(lifecycle_plan(), db)
    original = state_runtime_v2.verify_positioned_delivery
    def verify(before, resolved, characters):
        if change == "timer": resolved["state_runtime"]["timers"][1]["interval_us"] += 1
        elif change == "qualification": resolved["resource_threshold_triggers"]["bindings"][0]["qualification"] = {"owner_presence": "frontline"}
        else:
            target = next(r for r in resolved["rules"] if r["id"] == "boost")
            if change == "rule_owner": target["owner"] = "unreviewed"
            else: target["effects"][0]["value"] = 100
        return original(before, resolved, characters)
    monkeypatch.setattr(state_runtime_v2, "verify_positioned_delivery", verify)
    with pytest.raises(PlanError, match="实际"):
        run_plan_calc(plan, db)


def test_source_only_cannot_authorize_new_lifecycle_subgraph(tmp_path):
    db = tmp_path / "absent.db"
    plan = authorize(lifecycle_plan(), db)
    part(plan, "life_b")["dependencies"] = {"mode": "source_only", "context_fingerprint": None, "note": "仅来源不足"}
    report = compose_equipment_plan(plan, db)
    assert report["state"] == "pending"
    assert all(r["effective_status"] != "ready" for r in report["resource_threshold_trigger_adoptions"])


def test_final_state_copy_mutation_from_source_graph_is_rejected(tmp_path, monkeypatch):
    from genshin_app.equipment import resource_source_graph
    db = tmp_path / "absent.db"
    plan = authorize(lifecycle_plan(), db)
    original = resource_source_graph.prepare_resource_source_graph
    def close(*args, **kwargs):
        out = original(*args, **kwargs)
        out["state"]["timers"][1]["max_ticks"] += 1
        return out
    monkeypatch.setattr(resource_source_graph, "prepare_resource_source_graph", close)
    with pytest.raises(PlanError): run_plan_calc(plan, db)


@pytest.mark.parametrize("kind,base", [("dispatch_rule", "boost"), ("resource_operation", "refill")])
def test_pending_prefixed_payload_id_does_not_close_unrelated_lifecycle(tmp_path, kind, base):
    plan = lifecycle_plan()
    model = plan["mechanics_model"]
    config = model["state_runtime"]
    life = copy.deepcopy(config["lifecycles"][0])
    life.update(id="field_c", source_id="binding:life_c", start_order=3, end_order=3, establishment_order=12)
    config["lifecycles"].append(life)
    timer = copy.deepcopy(config["timers"][0])
    timer.update(id="third_tick", lifecycle_id="field_c", source_id="binding:timer_c", order=40,
        payload={"kind": kind, "ref_id": base+".extra"})
    config["timers"].append(timer)
    for name in ("life_c", "timer_c"):
        authority = rule(name, source_id="binding:"+name)
        authority["effects"] = []
        if kind == "dispatch_rule" and name == "timer_c":
            authority = copy.deepcopy(next(r for r in model["rules"] if r["id"] == base))
            authority.update(id=base+".extra", source_id="binding:timer_c")
            authority["trigger"]["event_ids"] = [route_event_id(kind, base+".extra")]
            authority["trigger"]["condition"] = "unresolved-payload-condition"
        add(model, authority)
        record = copy.deepcopy(part(plan, "life_b"))
        record.update(part_id=name, label="独立第三来源 "+name)
        record["rule"]["rule_id"] = authority["id"]
        plan["effect_bindings"][0]["parts"].append(record)
    if kind == "resource_operation":
        operation = copy.deepcopy(model["shared_pools"]["operations"][0])
        operation.update(id=base+".extra", source_id="binding:timer_c", order=40)
        operation["trigger"]["event_ids"] = [route_event_id(kind, base+".extra")]
        operation["trigger"]["condition"] = "unresolved-payload-condition"
        model["shared_pools"]["operations"].append(operation)
    db = tmp_path / "absent.db"
    report = compose_equipment_plan(authorize(plan, db), db)
    assert report["state"] == "pending"
    delivered = report["mechanics_model"]
    assert {r["id"] for r in delivered["state_runtime"]["lifecycles"]} == {"field", "field_b"}
    assert "boost" in {r["id"] for r in delivered["rules"]}
    assert "refill" in {r["id"] for r in delivered["shared_pools"]["operations"]}
