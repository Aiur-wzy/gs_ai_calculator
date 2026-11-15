"""EC08 controlled classic hits, not a character/team damage benchmark."""
import copy

import pytest
from test_freeze_runtime import inputs
from test_mechanics_execution import add, fixture, rule
from test_target_state import aura

from genshin_engine.mechanics import (
    execute_mechanics,
    mechanics_capabilities,
    validate_mechanics_model,
)


def source(sid="fixed", owner="a"):
    return {"id": sid, "name_zh": "受限目标状态条件", "owner": owner, "source_id": sid+":part",
            "availability": "fixed", "damage_scope": "classic_hits_only",
            "target_state_profile": "freeze-reference-target-v1", "status": "assumed", "source": "工程条件例",
            "effects": [{"id": "ice", "benefit_type_id": "benefit.crit_rate.all", "domain": "self", "value": .2,
                         "filter": {"event": "hit", "phase": "pre"}, "target_predicate": aura("cryo", "frozen")},
                        {"id": "frozen", "benefit_type_id": "benefit.crit_rate.all", "domain": "self", "value": .2,
                         "filter": {"event": "hit", "phase": "pre"},
                         "target_predicate": {"kind": "target_status", "status_id": "element_freeze"}}]}


def fixed_inputs(elements=("cryo", "physical", "hydro", "physical", "anemo", "physical"), times=None, initial=None):
    data = inputs(elements, times or (0,)*len(elements), initial)
    data[0]["characters"][0]["stats"]["crit_dmg"] = 1
    data[2]["fixed_effect_sources"] = [source()]
    return data


def successful(data, matches=None):
    original = copy.deepcopy(data)
    out = execute_mechanics(*data, matches=matches)
    assert out["result"] is not None, out["issues"]
    assert out["status"] in ("passed", "unproven"), out["issues"]
    assert data == original
    return out


def classic(out):
    return [h["expected"] for h in out["result"]["hits"] if h["damage_system"] == "classic"]


def test_ordered_same_tick_first_application_and_last_consumption():
    data = fixed_inputs()
    data[2]["elemental_model"]["profiles"][2]["gauge_units"] = 4
    out = successful(data)
    assert classic(out) == pytest.approx([45, 54, 54, 63, 63, 45])
    samples = [r["target_sample"] for r in out["target_condition_trace"] if r["effect_id"] == "ice" and r["event_id"] in ("h0", "h1", "h4", "h5")]
    assert [s["auras"] for s in samples] == [[], ["cryo"], ["frozen"], []]
    assert [s["order"] for s in samples] == [10, 11, 14, 15]
    without_fixed = copy.deepcopy(data)
    without_fixed[2].pop("fixed_effect_sources")
    baseline = execute_mechanics(*without_fixed)
    assert out["elemental_trace"] == baseline["elemental_trace"]
    assert out["result"]["elemental"]["event_counts"] == baseline["result"]["elemental"]["event_counts"]
    swirl = next(h for h in out["result"]["hits"] if h["damage_system"] == "transformative")
    assert swirl["expected"] == pytest.approx(781.299)
    assert not any(r["applied"] for r in out["target_condition_trace"] if r["event_id"] == swirl["id"])
    assert not any("fixed" in r.get("rule_id", "") for r in out["mechanics_trace"] if r["type"] == "expiry")
    assert out["result"]["target_condition_trace"] == out["target_condition_trace"]


def test_fixed_effect_is_independent_of_self_snapshot_and_late_rule():
    data = fixed_inputs(("cryo", "physical"), (0, .2))
    data[2]["hit_events"][1]["self_snapshot_event"] = "cast"
    add(data[2], rule("late-attack", "benefit.stat_add.atk", 100,
                      trigger={"event": "hit", "phase": "post", "event_ids": ["h0"]}))
    out = successful(data)
    assert classic(out) == pytest.approx([45, 54])
    row = next(r for r in out["target_condition_trace"] if r["event_id"] == "h1" and r["effect_id"] == "ice")
    assert row["target_sample"]["t"] == .2 and row["automatic_value"] is True


@pytest.mark.parametrize("operation", ["include", "exclude"])
def test_missing_target_model_cannot_be_hidden_by_match(operation):
    data = fixture((.2,))
    data[2]["fixed_effect_sources"] = [source()]
    out = execute_mechanics(*data, matches=[{"fixed_source_id": "fixed", "effect_id": "ice", "hit_id": "h0", "operation": operation}])
    assert out["status"] == "pending" and out["result"] is None and out["resolved_template"] is None
    rows = out["target_condition_trace"]
    assert rows[0]["override"] == operation and rows[0]["automatic_value"] is None and not rows[0]["applied"]


def test_include_cannot_bypass_explicit_filter_and_known_false_is_audited():
    data = fixed_inputs(("physical", "physical"))
    data[2]["fixed_effect_sources"][0]["effects"][0]["filter"]["event_ids"] = ["h0"]
    matches = [{"fixed_source_id": "fixed", "effect_id": "ice", "hit_id": h, "operation": "include"} for h in ("h0", "h1")]
    out = successful(data, matches)
    assert classic(out) == pytest.approx([54, 45])
    rows = [r for r in out["target_condition_trace"] if r["effect_id"] == "ice"]
    assert rows[0]["automatic_value"] is False and rows[0]["reason"] == "agent_included"
    assert rows[1]["reason"] == "filter_mismatch" and not rows[1]["applied"]


def test_unreported_hit_still_has_condition_receipt_without_inflating_damage():
    data = fixed_inputs(("cryo", "physical"))
    data[2]["hit_events"][1]["report"] = False
    out = successful(data)
    assert classic(out) == pytest.approx([45])
    rows = [r for r in out["target_condition_trace"] if r["event_id"] == "h1"]
    assert rows[0]["applied"] and not rows[0]["reported_damage"]


@pytest.mark.parametrize("mutation", ["empty_effects", "nine_effects", "65_sources", "unknown_predicate", "depth5",
                                     "domain_enemy", "wrong_benefit", "bool_value", "nan_value", "too_large", "trigger", "condition_filter"])
def test_schema_and_semantic_bounds(mutation):
    m = fixed_inputs()[2]
    fixed = m["fixed_effect_sources"][0]
    effect = fixed["effects"][0]
    if mutation == "empty_effects":
        fixed["effects"] = []
    elif mutation == "nine_effects":
        fixed["effects"] = [{**effect, "id": str(i)} for i in range(9)]
    elif mutation == "65_sources":
        m["fixed_effect_sources"] = [source(str(i)) for i in range(65)]
    elif mutation == "unknown_predicate":
        effect["target_predicate"]["path"] = "/frozen"
    elif mutation == "depth5":
        tree = aura("cryo")
        for _ in range(4):
            tree = {"kind": "all", "children": [tree]}
        effect["target_predicate"] = tree
    elif mutation == "domain_enemy":
        effect["domain"] = "enemy"
    elif mutation == "wrong_benefit":
        effect["benefit_type_id"] = "benefit.dmg_bonus.all"
    elif mutation == "trigger":
        fixed["trigger"] = {"event": "action_start", "phase": "pre"}
    elif mutation == "condition_filter":
        effect["filter"]["condition"] = "manual-frozen"
    else:
        effect["value"] = {"bool_value": True, "nan_value": float("nan"), "too_large": 1.01}[mutation]
    assert validate_mechanics_model(m)["status"] == "invalid"


def test_caps_and_no_source_output_are_backward_compatible():
    caps = mechanics_capabilities()
    assert caps["target_state_conditions"]["limits"]["max_nodes"] == 32
    assert "fixed_effect_sources" not in caps["empty_model"]
    out = execute_mechanics(*fixture())
    assert "fixed_effect_sources" not in out and "target_condition_trace" not in out["result"]
