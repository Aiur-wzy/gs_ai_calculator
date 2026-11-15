"""Independent regressions for lost finite Buff inputs and display boundaries."""
from __future__ import annotations

import copy

import pytest
from test_mechanics_execution import damages, fixture, run, second_character
from test_plan_mechanics import native, review

from genshin_app.equipment import run_plan_calc


def manual_buff(*, start=0, end=2, scope="team", target="a", effects=None):
    return {"id": "direct", "name": "直接手工持续增伤", "source": "独立工程审计",
            "start": start, "end": end, "scope": scope, "target": target,
            "effects": effects or [{"id": "manual-value", "benefit_type_id": "benefit.dmg_bonus.all",
                "zone": "dmg_bonus", "stat": "all", "value": .1, "applies_to": {"mode": "all"}}],
            "extensions": {"owner": "a", "source_id": "audit:manual"}}


def test_app_keeps_composed_and_manual_intervals_when_resolved_contains_only_projections(tmp_path):
    plan = native()
    part = copy.deepcopy(plan["effect_bindings"][0]["parts"][0])
    part.update(part_id="numeric", label="合成持续增伤", rule={"kind": "constant", "value": .5},
        delivery={"kind": "buff", "start": 0, "end": 2, "scope": "team", "target": "a",
            "effect": {"id": "numeric-value", "benefit_type_id": "benefit.dmg_bonus.all",
                       "zone": "dmg_bonus", "stat": "all", "applies_to": {"mode": "all"}}})
    plan["effect_bindings"][0]["parts"].append(part)
    plan["manual_template"]["buffs"] = [manual_buff()]
    db = tmp_path / "absent.db"
    review(plan, db)
    original = copy.deepcopy(plan)
    output = run_plan_calc(plan, db)
    assert plan == original
    assert output["result"]["summary"]["total_damage"] == pytest.approx(351)
    assert all(b["id"].startswith("mechanics:") for b in output["plan"]["resolved_template"]["buffs"])
    timeline = output["result"]["buff_state_timeline"]
    assert timeline["contract"] == "buff-state-timeline-v1"
    declared = [s for s in timeline["states"] if s["origin"] == "declared_interval"]
    originals = output["report"]["mechanics_input_template"]["buffs"]
    assert {s["buff_id"] for s in declared} == {b["id"] for b in originals}
    assert len(declared) == 2
    assert {s["source_id"] for s in declared} == {"audit:manual", "binding:numeric"}
    for state in declared:
        assert state["buff"] == next(b for b in originals if b["id"] == state["buff_id"])
        assert (state["start"], state["end"]) == (0, 2)
        assert state["evidence_status"] == "declared"
        assert state["beneficiary"] == "a"


def test_declared_frontline_target_and_hit_selector_survive_without_becoming_always_applied():
    template, rotation, model = fixture()
    second_character(template, rotation)
    buff = manual_buff(scope="frontline", target="a")
    buff["effects"][0]["applies_to"] = {"mode": "hits", "hit_ids": ["h0"]}
    template["buffs"] = [buff]
    output = run(template, rotation, model)
    assert damages(output) == pytest.approx([49.5, 45, 45])
    states = output["result"]["buff_state_timeline"]["states"]
    assert len(states) == 1
    assert states[0]["beneficiary"] == "a"
    assert states[0]["beneficiaries_scope"]["scope"] == "frontline"
    assert states[0]["buff"] == buff
    assert states[0]["filters"][0]["filter"] == {"mode": "hits", "hit_ids": ["h0"]}
    assert (states[0]["start"], states[0]["end"]) == (0, 2)


def test_snapshot_damage_after_declared_expiry_does_not_extend_visible_state():
    template, rotation, model = fixture((.2, 2.3))
    template["buffs"] = [manual_buff(end=1)]
    for descriptor in model["hit_events"]:
        descriptor["self_snapshot_event"] = "cast"
    output = run(template, rotation, model)
    assert damages(output) == pytest.approx([49.5, 49.5])
    state = output["result"]["buff_state_timeline"]["states"][0]
    assert (state["start"], state["end"]) == (0, 1)
    applied = [row for row in output["mechanics_trace"]
               if row["type"] == "manual_effect_applied" and row["event_id"] == "h1" and row["cycle"] == 0]
    assert applied[0]["t"] == 2.3
    assert applied[0]["sample_t"] == 0


def test_enemy_only_debuff_ignores_character_target_but_keeps_original_constraints():
    template, rotation, model = fixture()
    second_character(template, rotation)
    buff = manual_buff(scope="frontline", target="b", effects=[{
        "id": "resistance", "benefit_type_id": "benefit.res_shred.cryo",
        "zone": "res_shred", "stat": "cryo", "value": .1, "applies_to": {"mode": "all"}}])
    template["buffs"] = [buff]
    output = run(template, rotation, model)
    assert damages(output) == pytest.approx([50, 50, 50])
    states = output["result"]["buff_state_timeline"]["states"]
    assert {s["beneficiary"] for s in states} == {"a", "b"}
    assert all(s["buff"] == buff for s in states)
    assert all(s["beneficiaries_scope"]["enemy_effects_ignore_scope"] for s in states)
