"""Finite-state visualization receipts retain evidence without changing damage."""
from __future__ import annotations

import copy

import pytest
from test_mechanics_execution import (
    EVIDENCE,
    REPEAT,
    ability,
    action,
    add,
    fixture,
    rule,
    run,
    second_character,
)

from genshin_engine.buff_state_timeline import CONTRACT, build_buff_state_timeline
from genshin_engine.mechanics_schema import mechanics_capabilities


def test_exhaustion_is_retained_until_expiry_and_does_not_claim_last_hit_failed():
    template, rotation, model = fixture((.2, .3, .4))
    add(model, rule(quota={"capacity": 2, "scope": "team", "refresh": "reset",
                           "consume": {"event": "hit", "phase": "pre"}}))
    before = copy.deepcopy((template, rotation, model))
    output = run(template, rotation, model)
    receipt = output["buff_state_timeline"]
    assert receipt == output["result"]["buff_state_timeline"]
    assert receipt["contract"] == CONTRACT
    state, = receipt["states"]
    assert state["origin"] == "mechanics_rule"
    assert (state["start"], state["end"]) == (0, 2)
    assert [(s["start"], s["end"], s["status"], s["remaining_quota"]) for s in state["slices"]] == [
        (0, .2, "active", 2), (.2, .3, "active", 1), (.3, .4, "exhausted", 0), (.4, 2, "exhausted", 0)]
    assert [h["expected"] for h in output["result"]["hits"]] == [90, 90, 45]
    # Last eligible hit consumes the last charge.  Post-event availability does
    # not replace the pre-phase receipt deciding the hit's benefit.
    assert any("同刻" in n for n in receipt["notes"])
    assert (template, rotation, model) == before


def test_independent_layers_and_refresh_times_are_kept_in_one_instance():
    template, rotation, model = fixture((.2, .4, 1.2))
    add(model, rule(trigger={"event": "hit", "phase": "pre", "event_ids": ["h0", "h1"]}, duration=1,
                    stacking={"max": 2, "add": 1, "policy": "independent"}))
    receipt = run(template, rotation, model)["buff_state_timeline"]
    state, = receipt["states"]
    assert [(s["start"], s["end"], s["layers"], s["end_reason"]) for s in state["slices"]] == [
        (.2, .4, 1, "refresh"), (.4, 1.2, 2, "layer_expiry"), (1.2, 1.4, 1, "natural_expiry")]
    assert [s["activation_time"] for s in state["slices"]] == [.2, .4, .4]
    assert state["end_reason"] == "natural_expiry"


def test_expired_reactivation_creates_separate_intervals_and_identity():
    template, rotation, model = fixture((.2, .4, 1.5, 2.6))
    add(model, rule(trigger={"event": "hit", "phase": "pre", "event_ids": ["h0", "h1", "h2"]}, duration=1))
    states = run(template, rotation, model)["buff_state_timeline"]["states"]
    assert [(s["start"], s["end"]) for s in states] == [(.2, 1.4), (1.5, 2.5)]
    assert states[0]["instance_id"] != states[1]["instance_id"]


def test_per_beneficiary_pool_is_not_broadcast_as_team_exhaustion():
    template, rotation, model = fixture((.2, .3))
    second_character(template, rotation)
    add(model, rule(quota={"capacity": 1, "scope": "per_beneficiary", "refresh": "reset",
                           "consume": {"event": "hit", "phase": "pre"}}))
    states = {s["beneficiary"]: s for s in run(template, rotation, model)["buff_state_timeline"]["states"]}
    assert states["a"]["slices"][-1]["status"] == "exhausted"
    assert states["b"]["slices"][-1]["status"] == "active"
    assert states["b"]["slices"][-1]["remaining_quota"] == 1


def test_initial_state_keeps_scope_filter_and_its_finite_remaining_time():
    template, rotation, model = fixture((.2, .3))
    source = rule(trigger={"event": "hit", "phase": "post", "event_ids": ["h1"]}, duration=1,
                  beneficiaries={"mode": "frontline", "scope": "current_frontline"})
    source["effects"][0]["filter"] = {"event": "hit", "phase": "pre", "elements": ["cryo"]}
    add(model, source)
    model["initial_state"]["rules"][0]["instances"] = [{"beneficiaries": ["a"], "remaining": [.25]}]
    states = run(template, rotation, model)["buff_state_timeline"]["states"]
    initial = next(s for s in states if s["instance_id"] == "boost:initial:0")
    assert initial["slices"][0]["origin"] == "initial_state"
    assert initial["end"] == .25
    assert initial["beneficiaries_scope"] == source["beneficiaries"]
    assert initial["filters"] == [{"effect_id": "value", "filter": source["effects"][0]["filter"]}]


def test_failed_action_clips_an_already_active_state_at_failure_not_next_event():
    template, rotation, model = fixture((.2, 1.2))
    rotation["characters"][0]["initial_energy"] = 0
    rotation["abilities"].append(ability("q", kind="action.elemental_burst", energy={"required": 10, "amount": 10}))
    rotation["actions"].append(action("burst", "q", 1, 1.1, 2))
    rotation["events"].append({"id": "spend", "type": "energy_spend", "t": 1, "order": 3,
        "action_id": "burst", "character": "a", "required": 10, "amount": 10, "repeat": REPEAT, **EVIDENCE})
    model["hit_events"][1]["action_id"] = "burst"
    add(model, rule(duration=8))
    receipt = run(template, rotation, model)["buff_state_timeline"]
    assert receipt["execution_limit"] == 1
    assert receipt["states"][0]["end"] == 1
    assert receipt["states"][0]["end_reason"] == "execution_limit"


@pytest.mark.parametrize("status", ["unproven", "passed", "pending", "failed"])
def test_completed_input_window_is_not_hidden_by_later_cycle_proof_status(status):
    template, rotation, model = fixture()
    add(model, rule(duration=20))
    result = run(template, rotation, model)
    check = copy.deepcopy(result["rotation_check"])
    check["energy_check"]["status"] = "not_verified"
    check["event_log"].append({"status": "failed", "t": 12})
    receipt = build_buff_state_timeline(model, result["mechanics_trace"], check,
        duration=10, execution_status=status, template=template)
    assert receipt["execution_limit"] == 10
    assert receipt["states"][0]["end"] == 10
    assert receipt["states"][0]["end_reason"] == "window_end"


def test_declared_composer_interval_survives_projection_and_is_not_called_triggered():
    template, rotation, model = fixture((.2, 3))
    template["buffs"] = [{"id": "binding:numeric", "name": "明确持续加成", "start": 0, "end": 2,
        "scope": "team", "target": "a", "source": "工程输入", "extensions": {"owner": "a", "source_id": "binding:numeric"},
        "effects": [{"id": "numeric-value", "benefit_type_id": "benefit.dmg_bonus.all", "zone": "dmg_bonus",
                     "stat": "all", "value": .5, "applies_to": {"mode": "all"}}]}]
    output = run(template, rotation, model)
    assert all(b["id"].startswith("mechanics:") for b in output["resolved_template"]["buffs"])
    state, = output["buff_state_timeline"]["states"]
    assert (state["start"], state["end"], state["origin"], state["buff_id"]) == (0, 2, "declared_interval", "binding:numeric")
    assert state["source_id"] == "binding:numeric"
    assert state["evidence_status"] == "declared"
    assert state["slices"][0]["activation_trace_index"] is None
    assert [h["expected"] for h in output["result"]["hits"]] == [67.5, 45]
    # Feeding projection data back cannot invent its 1-microsecond numerical
    # windows as game-rule Buff durations.
    assert build_buff_state_timeline(model, [], {}, duration=10, execution_status="passed",
        template=output["resolved_template"])["states"] == []


def test_fixed_condition_and_zero_duration_are_explained_without_full_axis_bars():
    template, _rotation, model = fixture()
    add(model, rule(duration=0))
    # Builder accepts the already validated rule/source metadata; no source
    # descriptor is turned into an execution rule.
    model["fixed_effect_sources"] = [{"id": "fixed", "source_id": "artifact:conditional", "owner": "a",
                                      "name_zh": "命中条件收益", **EVIDENCE}]
    receipt = build_buff_state_timeline(model, [], {}, duration=10, execution_status="passed", template=template)
    assert receipt["states"] == []
    assert {x["code"] for x in receipt["excluded"]} == {"no_finite_interval", "fixed_condition_without_duration"}
    assert all(x["message"] for x in receipt["excluded"])
    assert mechanics_capabilities()["buff_state_timeline"]["contract"] == CONTRACT


def test_same_timestamp_refresh_then_consume_uses_last_causal_receipt():
    template, rotation, model = fixture((.2, .2, .3))
    add(model, rule(trigger={"event": "hit", "phase": "pre", "event_ids": ["h0", "h1"]},
        quota={"capacity": 1, "scope": "team", "refresh": "reset", "consume": {"event": "hit", "phase": "pre"}}))
    output = run(template, rotation, model)
    state, = output["buff_state_timeline"]["states"]
    assert state["start"] == .2
    assert all(s["status"] == "exhausted" for s in state["slices"])
    assert [h["expected"] for h in output["result"]["hits"]] == [90, 90, 45]


def test_receipt_is_rebuildable_from_published_inputs_and_does_not_alias_them():
    template, rotation, model = fixture()
    add(model, rule())
    output = run(template, rotation, model)
    source = copy.deepcopy((template, model, output["mechanics_trace"], output["rotation_check"]))
    receipt = build_buff_state_timeline(model, output["mechanics_trace"], output["rotation_check"],
        duration=10, execution_status=output["status"], template=template)
    assert receipt == output["buff_state_timeline"]
    receipt["states"][0]["trigger"]["phase"] = "post"
    receipt["states"][0]["beneficiaries"].append("other")
    assert (template, model, output["mechanics_trace"], output["rotation_check"]) == source


@pytest.mark.parametrize("status", ["failed", "pending", "not_verified"])
def test_pending_row_is_an_explicit_cutoff_even_without_later_events(status):
    model = {"rules": [rule()], "initial_state": {"rules": [{"rule_id": "boost",
        "instances": [{"beneficiaries": ["a"], "remaining": [3]}]}]}}
    receipt = build_buff_state_timeline(model, [], {"event_log": [{"status": status, "t": 1}]},
        duration=10, execution_status="pending")
    assert receipt["execution_limit"] == 1
    assert receipt["states"][0]["end"] == 1


def test_resource_only_rule_is_not_misrepresented_as_a_damage_buff():
    template, rotation, model = fixture()
    add(model, rule(effects=[], resources=[{"id": "energy", "kind": "energy_direct", "delay": 0,
                                         "amounts": {"a": 1}, "source_part": "energy"}]))
    model["resource_ownership"] = [{"rule_id": "boost", "action_id": "cast", "source_part": "energy",
                                    "replaces_event_ids": []}]
    output = run(template, rotation, model)
    assert any(t["type"] == "activation" for t in output["mechanics_trace"])
    assert output["buff_state_timeline"]["states"] == []
    assert output["buff_state_timeline"]["excluded"][0]["code"] == "no_effects"


def test_manual_enemy_effect_recipient_union_retains_mixed_self_target_restriction():
    template, _rotation, model = fixture()
    template["characters"].append({"id": "b"})
    template["buffs"] = [{"id": "combined", "source": "测试领域", "start": 1, "end": 4,
        "scope": "frontline", "target": "a", "effects": [
            {"id": "self", "zone": "dmg_bonus", "value": .1},
            {"id": "enemy", "zone": "res_shred", "value": .1}]}]
    receipt = build_buff_state_timeline(model, [], {}, duration=10, execution_status="passed", template=template)
    assert {s["beneficiary"] for s in receipt["states"]} == {"a", "b"}
    state = receipt["states"][0]
    assert state["name_zh"] == "测试领域"
    assert state["beneficiaries_scope"] == {"scope": "frontline", "target": "a", "enemy_effects_ignore_scope": True}
    assert state["effects"] == template["buffs"][0]["effects"]
    applicability = {e["effect_id"]: e for e in state["effect_applicability"]}
    assert applicability["self"]["possible_characters"] == ["a"]
    assert applicability["self"]["frontline_required"] is True
    assert applicability["enemy"]["possible_characters"] == ["a", "b"]
    assert applicability["enemy"]["frontline_required"] is False


def test_unretained_pending_prefix_never_certifies_the_remaining_window():
    model = {"rules": [rule()], "initial_state": {"rules": [{"rule_id": "boost",
        "instances": [{"beneficiaries": ["a"], "remaining": [3]}]}]}}
    receipt = build_buff_state_timeline(model, [{"type": "hit", "t": .4}], {},
        duration=10, execution_status="pending")
    assert receipt["execution_limit"] == .4
    assert receipt["states"][0]["end_reason"] == "execution_limit"


def test_enemy_effect_shared_scope_is_visible_without_fabricating_recipient_pools():
    template, rotation, model = fixture((.3,))
    second_character(template, rotation)
    template["hits"][0]["character"] = "b"
    rotation["abilities"].extend([ability("ab", "a", "action.switch"), ability("be", "b")])
    rotation["actions"].extend([action("switch_b", "ab", .1, .11, order=1, switch_to="b"), action("b_cast", "be", .2, .21)])
    model["hit_events"][0]["action_id"] = "b_cast"
    add(model, rule(benefit="benefit.res_shred.cryo", value=.1, domain="enemy",
                    beneficiaries={"mode": "self", "scope": "locked"}))
    # Finite repeat is enough here; no fake return switch is needed to prove
    # later cycles when the target is one explicit input window.
    for a in rotation["actions"]:
        a["repeat"] = {"mode": "once"}
    output = run(template, rotation, model)
    assert output["result"]["hits"][0]["expected"] == 100
    state, = output["buff_state_timeline"]["states"]
    assert state["beneficiaries"] == ["a"]
    effect, = state["effect_applicability"]
    assert effect["recipient_mode"] == "enemy_shared"
    assert effect["possible_characters"] == ["a", "b"]
    assert effect["requires_quota_receipt"] is False

    model["rules"][0]["quota"] = {"capacity": 1, "scope": "per_beneficiary", "refresh": "reset",
                                  "consume": {"event": "hit", "phase": "pre"}}
    output = run(template, rotation, model)
    assert output["result"]["hits"][0]["expected"] == 90
    effect = output["buff_state_timeline"]["states"][0]["effect_applicability"][0]
    assert effect["possible_characters"] == ["a"]
    assert effect["requires_quota_receipt"] is True


def test_shared_pool_gate_is_self_describing_and_excludes_disallowed_owner_from_state_receipt():
    source = rule(beneficiaries={"mode": "frontline", "scope": "current_frontline"})
    source["owner"] = "a"
    source["resource_gate"] = {"consumer_id": "stock-consumer"}
    model = {"rules": [source], "initial_state": {"rules": [{"rule_id": "boost",
        "instances": [{"beneficiaries": ["a", "b"], "remaining": [5]}]}]},
        "shared_pools": {"schema_version": 2,
            "pools": [{"id": "stock", "name_zh": "共享库存", "owner": "a", "scope": "team",
                       "capacity": 10, "initial": {"team": 10}, "unit_scale": 1,
                       "source_id": "source", "status": "confirmed", "source": "测试来源"}],
            "operations": [],
            "consumers": [{"id": "stock-consumer", "pool_id": "stock", "source_id": "source",
                "filter": {"event": "hit", "phase": "pre", "characters": ["a", "b"]},
                "actor_relation": "other_frontline", "damage_system": "classic", "amount": 1,
                "eligibility_rule_id": "boost", "status": "confirmed", "source": "测试来源"}]}}
    template = {"characters": [{"id": "a"}, {"id": "b"}], "buffs": []}
    receipt = build_buff_state_timeline(model, [], {}, duration=10,
                                        execution_status="passed", template=template)
    state, = receipt["states"]
    assert state["beneficiary"] == "b"
    assert state["beneficiaries"] == ["b"]
    assert state["resource_gate"] == {
        "consumer_id": "stock-consumer", "pool_id": "stock", "actor_relation": "other_frontline",
        "amount": 1, "filter": {"event": "hit", "phase": "pre", "characters": ["a", "b"]},
        "status": "confirmed", "source_id": "source", "pool": model["shared_pools"]["pools"][0],
        "receipt_field": "shared_pools.trace"}
    assert state["effect_applicability"][0]["requires_quota_receipt"] is True
    # Omitting the new optional ledger keeps the legacy duration-only contract.
    assert [(row["start"], row["end"], row["status"]) for row in state["slices"]] == [
        (0, 5, "active")]
    assert all("resource_balance" not in row for row in state["slices"])


def test_shared_pool_balance_cuts_rule_availability_and_grant_reopens_without_owner_state():
    source = rule(beneficiaries={"mode": "frontline", "scope": "current_frontline"})
    source["owner"] = "a"
    source["resource_gate"] = {"consumer_id": "stock-consumer"}
    model = {"rules": [source], "initial_state": {"rules": [{"rule_id": "boost",
        "instances": [{"beneficiaries": ["a", "b"], "remaining": [5]}]}]},
        "shared_pools": {"schema_version": 2,
            "pools": [{"id": "stock", "name_zh": "共享库存", "owner": "a", "scope": "team",
                       "capacity": 10, "initial": {"team": 0}, "unit_scale": 1,
                       "source_id": "source", "status": "confirmed", "source": "测试来源"}],
            "operations": [],
            "consumers": [{"id": "stock-consumer", "pool_id": "stock", "source_id": "source",
                "filter": {"event": "hit", "phase": "pre"}, "actor_relation": "other_frontline",
                "damage_system": "classic", "amount": 1, "eligibility_rule_id": "boost",
                "status": "confirmed", "source": "测试来源"}]}}
    template = {"characters": [{"id": "a"}, {"id": "b"}], "buffs": []}
    pool_trace = [
        {"type": "operation", "pool_id": "stock", "key": "team", "t": 0,
         "before": 0, "after": 2},
        {"type": "consume", "pool_id": "stock", "key": "team", "t": 1,
         "before": 2, "after": 1},
        {"type": "consume", "pool_id": "stock", "key": "team", "t": 2,
         "before": 1, "after": 0},
        {"type": "operation", "pool_id": "stock", "key": "team", "t": 4,
         "before": 0, "after": 3},
    ]
    before = copy.deepcopy((model, template, pool_trace))
    receipt = build_buff_state_timeline(model, [], {}, duration=10, execution_status="passed",
                                        template=template, shared_pool_trace=pool_trace)
    state, = receipt["states"]
    assert state["beneficiary"] == "b"
    assert state["beneficiaries"] == ["b"]
    assert (state["start"], state["end"], state["end_reason"]) == (0, 5, "natural_expiry")
    assert [(row["start"], row["end"], row["status"],
             row["resource_balance"]["remaining"]) for row in state["slices"]] == [
        (0, 1, "active", 2), (1, 2, "active", 1),
        (2, 4, "exhausted", 0), (4, 5, "active", 3)]
    assert all(row["resource_balance"]["pool_id"] == "stock"
               and row["resource_balance"]["pool_key"] == "team"
               and row["resource_balance"]["required"] == 1 for row in state["slices"])
    assert [row["end_reason"] for row in state["slices"]] == [
        "resource_change", "resource_change", "resource_change", "natural_expiry"]
    assert any("规则期限" in note and "共享资源" in note for note in receipt["notes"])
    assert (model, template, pool_trace) == before
