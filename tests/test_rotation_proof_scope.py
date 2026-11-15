"""Finite execution is evidence for its own window, never for later idle DPS."""

from __future__ import annotations

import copy

import pytest
from test_rotation_energy import _ability, _action, _check, _model, _repeat


def _once_model():
    model = _model()
    for item in [*model["actions"], *model["events"]]:
        item["repeat"] = _repeat("once")
    return model


def test_once_actions_do_not_prove_a_cycle_through_two_empty_windows():
    model = _once_model()
    before = copy.deepcopy(model)
    result = _check(model)
    assert result["status"] == "unproven"
    assert result["action_check"]["status"] == "passed"
    assert result["energy_check"]["opening_status"] == "passed"
    assert result["energy_check"]["proven_cycle"] is None
    assert [c["executed_action_count"] for c in result["cycles"]] == [2, 0, 0]
    assert [c["burst_energy_spend_count"] for c in result["cycles"]] == [1, 0, 0]
    assert all(not c["periodic_action_coverage"] for c in result["cycles"])
    scope = result["energy_check"]["proof_scope"]
    assert scope["kind"] == "checked_windows"
    assert scope["periodic_action_ids"] == []
    assert scope["finite_action_ids"] == ["e1", "q1"]
    assert scope["damage_verified"] is False
    assert model == before


def test_real_periodic_actions_keep_their_energy_proof_and_counts():
    result = _check(_model())
    assert result["status"] == "passed"
    assert result["energy_check"]["proven_cycle"] == 2
    assert [c["executed_action_count"] for c in result["cycles"]] == [2, 2, 2]
    assert [c["burst_energy_spend_count"] for c in result["cycles"]] == [1, 1, 1]
    assert all(c["periodic_action_coverage"] for c in result["cycles"])
    scope = result["energy_check"]["proof_scope"]
    assert scope["kind"] == "periodic_action_energy"
    assert scope["periodic_action_ids"] == ["e1", "q1"]
    assert scope["finite_action_ids"] == []
    assert scope["damage_verified"] is False


def test_mixed_finite_burst_and_periodic_normal_only_proves_the_periodic_part():
    model = _once_model()
    model["abilities"].append(_ability("n", "action.normal_attack"))
    model["actions"].append(_action("normal", "n", 2, 2.5))
    result = _check(model)
    assert result["status"] == "passed"
    assert [c["executed_action_count"] for c in result["cycles"]] == [3, 1, 1]
    assert [c["burst_energy_spend_count"] for c in result["cycles"]] == [1, 0, 0]
    scope = result["energy_check"]["proof_scope"]
    assert scope["periodic_action_ids"] == ["normal"]
    assert scope["finite_action_ids"] == ["e1", "q1"]
    assert scope["damage_verified"] is False


@pytest.mark.parametrize("mode", ["once", "cycles"])
def test_explicit_finite_actions_never_become_an_implicit_repeating_schedule(mode):
    model = _once_model()
    if mode == "cycles":
        for item in [*model["actions"], *model["events"]]:
            item["repeat"] = _repeat("cycles", cycles=[0, 1, 2])
        expected = [2, 2, 2, 0, 0, 0]
        model["max_cycles"] = 6
    else:
        # Three explicit rotations in one 30-second plan; the engine must not
        # turn them into another 60 seconds of input on the user's behalf.
        originals = copy.deepcopy(model)
        model["period"] = 30
        for cycle in (1, 2):
            for old in originals["actions"]:
                item = copy.deepcopy(old)
                item.update(id=f"{old['id']}-{cycle}", start=old["start"] + cycle * 10,
                            occupation_end=old["occupation_end"] + cycle * 10)
                for outcome in item.get("rule_outcomes", []):
                    outcome["event_ids"] = [f"{eid}-{cycle}" for eid in outcome["event_ids"]]
                model["actions"].append(item)
            for old in originals["events"]:
                item = copy.deepcopy(old)
                item["id"] = f"{old['id']}-{cycle}"
                time_key = "credit_at" if item["type"] == "energy_credit" else "t"
                item[time_key] += cycle * 10
                for key in ("action_id", "spawn_id"):
                    if key in item:
                        item[key] = f"{item[key]}-{cycle}"
                model["events"].append(item)
        expected = [6, 0, 0]
    result = _check(model)
    assert result["status"] == "unproven", result["issues"]
    assert [c["executed_action_count"] for c in result["cycles"]] == expected
    assert result["energy_check"]["proven_cycle"] is None


def test_legacy_delayed_spend_is_counted_in_the_window_where_energy_is_debited():
    model = _once_model()
    model["actions"] = [_action("q1", "q", 9.9, 10.1, repeat=_repeat("once"))]
    model["events"] = [model["events"][1]]
    model["events"][0]["t"] = 10
    result = _check(model)
    assert result["status"] == "unproven", result["issues"]
    assert [c["executed_action_count"] for c in result["cycles"]] == [1, 0, 0]
    assert [c["burst_action_count"] for c in result["cycles"]] == [1, 0, 0]
    assert [c["burst_energy_spend_count"] for c in result["cycles"]] == [0, 1, 0]


def test_failed_spend_does_not_count_as_a_successful_energy_debit():
    model = _once_model()
    model["characters"][0]["initial_energy"] = 0
    result = _check(model)
    assert result["status"] == "failed"
    assert result["cycles"][0]["burst_energy_spend_count"] == 0
    assert result["cycles"][0]["energy_spend_count"] == 0
    assert result["energy_check"]["proof_scope"]["kind"] == "checked_windows"


@pytest.mark.parametrize("kind", ["action.wait", "action.switch"])
def test_idle_or_switch_only_periodicity_never_claims_damage_or_burst_coverage(kind):
    model = _once_model()
    model["abilities"] = [_ability("idle", kind)]
    model["actions"] = [_action("idle", "idle", 0, 0.1)]
    model["events"] = []
    model["initial_cooldowns"] = []
    if kind == "action.switch":
        model["characters"].append({**copy.deepcopy(model["characters"][0]), "id": "b"})
        model["abilities"].append(_ability("back", kind, character="b"))
        model["actions"][0]["switch_to"] = "b"
        model["actions"].append(_action("back", "back", 1, 1.1, switch_to="a"))
        # Keep this test about receipt scope rather than the legacy projection.
        from genshin_engine.rotation import check_rotation
        result = check_rotation({"characters": [{"id": "a"}, {"id": "b"}], "hits": [],
                                 "switches": [{"t": 0, "character": "b"},
                                              {"t": 1, "character": "a"}]}, model)
    else:
        result = _check(model)
    assert result["status"] == "passed", result["issues"]
    assert all(c["burst_action_count"] == c["burst_energy_spend_count"] == 0
               for c in result["cycles"])
    assert result["energy_check"]["proof_scope"]["damage_verified"] is False


def test_zero_cost_burst_counts_casts_but_no_energy_debits():
    model = _model()
    model["abilities"][1]["energy"]["amount"] = 0
    model["events"][1]["amount"] = 0
    result = _check(model)
    assert result["status"] == "passed"
    assert [c["burst_action_count"] for c in result["cycles"]] == [1, 1, 1]
    assert [c["burst_energy_spend_count"] for c in result["cycles"]] == [0, 0, 0]


def test_mechanics_atomic_admission_counts_a_burst_debit_once():
    from test_runtime_compiler import runtime_fixture

    from genshin_engine.mechanics import execute_mechanics

    template, rotation, mechanics = runtime_fixture((1.1,))
    result = execute_mechanics(template, rotation, mechanics)
    check = result["rotation_check"]
    assert result["status"] == "unproven", result["issues"]
    assert [c["burst_energy_spend_count"] for c in check["cycles"]] == [1, 0, 0]
    assert [c["energy_spend_count"] for c in check["cycles"]] == [1, 0, 0]
    assert len(result["result"]["hits"]) == 1
