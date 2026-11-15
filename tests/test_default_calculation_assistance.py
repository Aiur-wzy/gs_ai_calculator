import copy

from genshin_app.calculation_assistance import energy_summary
from genshin_app.calculation_scope import validation_scope
from genshin_app.panel_assistance import compare_plan_panels
from tests.test_panel_reference import request

pytest_plugins = ["tests.test_panel_reference"]


def panel_plan():
    req = request()
    return {"manual_template": {"characters": [{"id": "a", "level": 90, "stats": {"base_atk": 1400, "atk": 2000}}]},
            "equipment_inputs": {"a": {"character_ref": {"db_character_id": 1, "version_line": "release"},
                "weapon": copy.deepcopy(req["weapon"]), "artifact_sets": req["artifact_sets"],
                "panel_provenance": {"origin": "agent_constructed", "entered_by": "agent", "reference_request": req}}}}


def test_construction_is_soft_but_comparison_uses_current_panel(source):
    plan = panel_plan()
    original = copy.deepcopy(plan)
    row = compare_plan_panels(plan, source[0])[0]
    assert row["state"] == "compared"
    assert any(d["stat"] == "base_atk" and d["reference"] == 974 for d in row["differences"])
    assert plan == original and row["acceptance_effect"] == "none"
    plan["equipment_inputs"]["a"]["weapon"]["weapon_id"] = 2
    stale = compare_plan_panels(plan, source[0])[0]
    assert stale["state"] == "construction_outdated"
    assert stale["mismatched_fields"] == ["weapon.weapon_id"]


def test_user_panel_and_legacy_unknown_do_not_infer_composition(source):
    plan = panel_plan()
    gear = plan["equipment_inputs"]["a"]
    del gear["panel_provenance"]
    row = compare_plan_panels(plan, source[0])[0]
    assert row["origin"] == "unknown" and row["state"] == "basis_compared"
    assert all(d["stat"] == "base_atk" for d in row["differences"])
    gear["panel_provenance"] = {"origin": "user_panel", "entered_by": "agent"}
    row = compare_plan_panels(plan, source[0])[0]
    assert row["origin"] == "user_panel" and "未反推" in row["message"]


def test_malformed_optional_construction_does_not_break_primary_work(source):
    plan = panel_plan()
    plan["equipment_inputs"]["a"]["panel_provenance"]["reference_request"]["weapon"] = []
    before = copy.deepcopy(plan)
    assert compare_plan_panels(plan, source[0])[0]["state"] == "comparison_unavailable"
    assert plan == before


def test_preflight_has_plan_counts_not_fictional_balances():
    plan = {"rotation_model": {"abilities": [{"id": "q", "character": "a", "action_type_id": "action.elemental_burst",
             "energy": {"required": 70, "amount": 70}}],
             "actions": [{"id": f"q{i}", "ability_id": "q", "start": i * 20} for i in range(4)], "events": []}}
    out = energy_summary({}, plan)
    assert out["planned_burst_count"] == 4 and out["observed_burst_count"] == 0
    assert out["declared_income_event_count"] == 0 and out["state"] == "not_checked"
    assert all(row["before"] is None and row["deficit"] is None for row in out["bursts"])


def test_static_accepted_is_not_dynamic_proof():
    plan = {"manual_template": {"characters": [{"id": "a"}], "hits": [
        {"reaction": {"type": "stellar_superconduct", "layers": 10}}]}}
    scope = validation_scope(plan, {"summary": {"total_damage": 10}})
    assert scope["prefilled_reaction_hit_count"] == 1
    assert scope["elemental"]["declared"] is False
    assert scope["elemental"]["execution_observed"] is False
    assert scope["elemental"]["state"] == "not_declared"
    assert scope["unlinked_equipment_characters"] == ["a"]
    assert scope["acceptance_effect"] == "none"


def test_short_assistance_keeps_actual_counts_and_pointer_without_mutating():
    from genshin_app.default_assistance import compact_default_assistance
    value = {"energy": {"planned_burst_count": 100, "bursts": [{"id": i} for i in range(100)]}}
    original = copy.deepcopy(value)
    short = compact_default_assistance(value)
    assert short["energy"]["planned_burst_count"] == 100
    assert len(short["energy"]["bursts"]) == 8
    assert short["presentation"]["omissions"] == [{"path": "/energy/bursts", "total": 100, "shown": 8, "omitted": 92}]
    assert value == original


def test_window_counts_do_not_hide_upstream_tail_or_multiply_event_count():
    from genshin_app.damage_count_observation import observe_damage_counts
    plan = {"manual_template": {"loop_duration": 10, "hits": [
        {"id": "a", "t": 10, "count": 3}, {"id": "b", "t": 10.01}]}}
    result = {"hits": [{"id": "a"}], "damage_execution_counts": {"executed": 1}}
    counts = observe_damage_counts(plan, result)
    assert counts["declaration"]["manual_rows"] == 2
    assert counts["declaration"]["manual_count_sum"] == 4
    assert counts["report"]["declared_manual_outside_window"] == 1
    assert counts["execution"]["executed"] == 1
    assert counts["composition"]["template_rows"] is None
