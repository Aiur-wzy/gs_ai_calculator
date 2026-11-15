"""Independent review: public identity, cycle edges and serializable numeric results."""

from __future__ import annotations

import json

from genshin_engine.rotation import check_rotation


def _model():
    evidence = {"status": "confirmed", "source": "合成机制：独立检查接口边界"}
    return {
        "schema_version": 1, "period": 10, "max_cycles": 3,
        "initial_frontline": "a",
        "characters": [{"id": "a", "element": "cryo", "energy_recharge": 1,
                        "energy_capacity": 10, "initial_energy": 5, **evidence}],
        "abilities": [{"id": "attack", "character": "a",
                       "action_type_id": "action.normal_attack", "cooldown": None,
                       "energy": None, **evidence}],
        "actions": [{"id": "opening", "ability_id": "attack", "start": 0,
                     "occupation_end": 1, "order": 0,
                     "repeat": {"mode": "each_cycle"}, **evidence}],
        "events": [],
    }


def _check(model):
    return check_rotation({"characters": [{"id": c["id"]} for c in model["characters"]],
                           "hits": [], "switches": []}, model)


def test_end_of_period_gap_retains_its_declared_reason_in_all_cycles():
    model = _model()
    model["gap_explanations"] = [{"start": 1, "end": 10, "reason": "等待下个输出窗口"}]
    result = _check(model)
    assert result["status"] == "passed"
    assert [(g["start"], g["end"]) for g in result["gaps"]] == [(1, 10), (11, 20), (21, 30)]
    assert all(g["status"] == "explained" and g["reason"] == "等待下个输出窗口"
               for g in result["gaps"])


def test_legal_character_and_group_ids_cannot_alias_another_characters_cooldown():
    model = _model()
    model["characters"].append({**model["characters"][0], "id": "a/b"})
    model["abilities"][0]["cooldown"] = {
        "group": "b/c", "duration": 3, "start": "action_start", "charges": 1,
        "recovery": "sequential",
    }
    second = {**model["abilities"][0], "id": "second", "character": "a/b",
              "cooldown": {**model["abilities"][0]["cooldown"], "group": "c"}}
    model["abilities"].append(second)
    model["initial_cooldowns"] = [{"character": "a", "group": "b/c", "ready_in": []},
                                  {"character": "a/b", "group": "c", "ready_in": []}]
    result = _check(model)
    assert result["status"] == "passed", result["issues"]
    assert len(result["cycles"][0]["boundary_state"]["cooldown_ready_in"]) == 2


def test_finite_inputs_that_overflow_energy_arithmetic_return_a_serializable_issue():
    model = _model()
    model["characters"][0]["energy_recharge"] = 1e308
    evidence = {"status": "confirmed", "source": "数值范围边界"}
    model["events"] = [
        {"id": "spawn", "type": "energy_spawn", "t": 1, "order": 0,
         "repeat": {"mode": "each_cycle"}, "particle_type": "particle",
         "element": "cryo", "count": 1, "source_kind": "enemy", **evidence},
        {"id": "credit", "type": "energy_credit", "credit_at": 2, "order": 0,
         "repeat": {"mode": "each_cycle"}, "spawn_id": "spawn", "count": 1, **evidence},
    ]
    result = _check(model)
    json.dumps(result, allow_nan=False)
    assert result["status"] in {"invalid", "failed"}
    assert result["issues"]


def test_future_once_energy_loss_prevents_a_false_periodicity_certificate():
    model = _model()
    evidence = {"status": "confirmed", "source": "第八轮才出现的一次动作"}
    model["abilities"].append({"id": "late_q", "character": "a",
                               "action_type_id": "action.elemental_burst", "cooldown": None,
                               "energy": {"required": 10, "amount": 10}, **evidence})
    model["actions"].append({"id": "late", "ability_id": "late_q", "start": 2,
                             "occupation_end": 3, "order": 0,
                             "repeat": {"mode": "cycles", "cycles": [7]}, **evidence})
    model["events"].append({"id": "late_spend", "type": "energy_spend", "t": 2,
                            "order": 1, "repeat": {"mode": "cycles", "cycles": [7]},
                            "action_id": "late", "character": "a", "required": 10,
                            "amount": 10, **evidence})
    short = _check(model)
    assert short["status"] == "unproven"
    model["max_cycles"] = 8
    full = _check(model)
    assert full["status"] == "failed"
    assert full["issues"][0]["event_id"] == "late_spend"
    assert full["issues"][0]["cycle"] == 7
