"""Optional suggestions never change execution or turn unknowns into facts."""
import copy

from genshin_app.calculation_assistance import inspect_calculation_assistance
from genshin_app.equipment import run_plan_calc


def test_q_summary_distinguishes_rejection_from_later_unverified_and_missing_balance():
    plan = {"rotation_model": {"abilities": [{"id": "q", "character": "a", "action_type_id": "action.elemental_burst",
        "energy": {"required": 60, "amount": 60}}], "actions": [{"id": "first", "ability_id": "q"},
        {"id": "second", "ability_id": "q"}], "events": []}}
    execution = {"rotation_check": {"event_log": [
        {"id": "first", "type": "action", "action_type_id": "action.elemental_burst", "character": "a",
         "t": 2, "status": "failed", "before": 40, "required": 60},
        {"id": "second", "type": "action", "t": 12, "status": "not_verified"}], "energy_check": {"status": "failed"}}}
    result = inspect_calculation_assistance(execution, {"view": "energy"}, plan=plan)
    rows = result["energy"]["bursts"]["items"]
    assert rows[0]["deficit"] == 20 and rows[1]["before"] is None and rows[1]["deficit"] is None
    assert result["authority"] == "advisory_only" and result["acceptance_effect"] == "none"


def test_optional_inventory_does_not_claim_excluded_buff_is_already_included():
    plan = {"manual_template": {"characters": [{"id": "a", "stats": {"energy_recharge": 2}}], "hits": []},
        "effect_bindings": [{"binding_id": "gear", "parts": [{"part_id": "conditional", "state": "excluded",
            "reason": "还没有做输入"}]}]}
    before = copy.deepcopy(plan)
    output = inspect_calculation_assistance({}, {"view": "coverage", "expected": [{"id": "buff",
        "input_refs": ["/missing"], "execution_ids": ["buff-trigger"]}]}, plan=plan)
    assert plan == before and "missing_inputs" not in output
    assert output["coverage"]["expected"]["items"][0]["execution"] == "unknown"
    assert "不能据此认定" in output["coverage"]["effect_parts"]["items"][0]["interpretation"]


def test_hints_and_empty_expectations_cannot_change_actual_calc_admission(tmp_path):
    from tests.test_energy_trials import plan as make_plan
    plan = make_plan()
    plan["manual_template"]["characters"][0]["stats"]["energy_recharge"] = 2
    before = copy.deepcopy(plan)
    first = run_plan_calc(plan, tmp_path / "absent.db")
    inspect_calculation_assistance(first, {"expected": [{"id": "unknown", "input_refs": ["/does-not-exist"]}]}, plan=plan)
    second = run_plan_calc(plan, tmp_path / "absent.db")
    assert first["result"] == second["result"] and plan == before


def test_duplicate_resource_hint_is_a_soft_question_not_a_game_rule():
    plan = {"rotation_model": {"events": [{"type": "energy_spawn", "id": name, "action_id": "e", "t": 1,
             "element": "cryo", "particle_type": "particle"} for name in ("p1", "p2")]}}
    output = inspect_calculation_assistance({}, {"view": "energy"}, plan=plan)
    hint = output["resource_hints"]["items"][0]
    assert hint["event_ids"] == ["p1", "p2"] and "可能合法" in hint["message"]
    assert "missing_inputs" not in output and "accepted" not in output


def test_diagnostic_wrapper_explains_actual_effect_matches_and_unverified_tail():
    plan = {"mechanics_model": {"rules": [{"id": "buff", "owner": "a",
        "beneficiaries": {"mode": "team", "scope": "locked"},
        "effects": [{"id": effect, "filter": {"event": "reaction_damage", "reaction_elements": [element]}}
                    for effect, element in (("cryo-swirl", "cryo"), ("anemo-swirl", "anemo"))]}]}}
    result = {"mechanics_trace": [{"type": "effect_applied", "rule_id": "buff",
        "effect_id": "cryo-swirl", "event_id": "one", "projected_effect_id": "buff@one"}],
        "hits": [{"matched_effect_refs": [{"effect_id": "buff@one"}, {"effect_id": "buff@one"}]}],
        "mechanics": {"complete_first_cycle": False}}
    wrapper = {"kind": "genshin-calculation-diagnostic", "schema_version": 1,
               "diagnostic": {"result": result}}
    matches = inspect_calculation_assistance(wrapper, {"view": "coverage"}, plan=plan)["coverage"]["effect_matches"]["items"]
    assert matches[0]["applied_trace_rows"] == matches[0]["reported_hit_refs"] == 1
    assert matches[0]["reaction_elements"] == ["cryo"] and matches[0]["sample_event_id"] == "one"
    assert matches[1]["match_state"] == "not_observed_in_available_trace"
    result["mechanics"]["complete_first_cycle"] = True
    matches = inspect_calculation_assistance(wrapper, {"view": "coverage"}, plan=plan)["coverage"]["effect_matches"]["items"]
    assert matches[1]["match_state"] == "zero_observed_in_complete_window"
