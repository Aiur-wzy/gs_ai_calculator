"""Independent integration review: initial frontline and semantic receipt identity."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from genshin_app.rotation_check import evaluate_rotation


def _document():
    return json.loads((Path(__file__).resolve().parents[1] / "examples" /
                       "rotation-energy-check.json").read_text(encoding="utf-8"))


def test_initial_frontline_cannot_disagree_with_damage_default_without_an_explicit_marker():
    document = _document()
    template, model = document["template"], document["rotation_model"]
    template["characters"].insert(0, {**copy.deepcopy(template["characters"][0]), "id": "b"})
    model["characters"].insert(0, {**copy.deepcopy(model["characters"][0]), "id": "b"})
    result = evaluate_rotation(template, model)
    assert result["status"] == "invalid"
    assert any(item["code"] == "initial_frontline_conflict" for item in result["issues"])
    template["switches"] = [{"t": 0, "character": "a"}]
    assert evaluate_rotation(template, model)["status"] == "passed"


def test_changing_enemy_repeat_strategy_changes_the_input_receipt_and_result():
    document = _document()
    template, model = document["template"], document["rotation_model"]
    model["abilities"][0]["rules"] = []
    model["actions"][0]["rule_outcomes"] = []
    spawn = model["events"][0]
    spawn.pop("action_id")
    spawn["source_kind"] = "enemy"
    before = evaluate_rotation(template, model)
    assert before["status"] == "passed"
    for event in model["events"]:
        if event["type"] in ("energy_spawn", "energy_credit"):
            event["repeat"] = {"mode": "once"}
    after = evaluate_rotation(template, model)
    assert after["status"] == "failed"
    assert after["input_fingerprint"] != before["input_fingerprint"]
    assert after["damage_interpretation"]["kind"] == "planned_damage"
    assert after["damage_interpretation"]["executable_dps"] is None


def test_native_panel_binding_changes_only_er_and_preserves_declared_unknowns():
    document = _document()
    template, model = document["template"], document["rotation_model"]
    template["characters"][0]["stats"]["energy_recharge"] = 2
    model["characters"][0]["initial_energy"] = None
    original = copy.deepcopy(model)
    result = evaluate_rotation(template, model, native=True)
    assert result["status"] == "pending"
    resolved = result["resolved_rotation_model"]["characters"][0]
    assert resolved["energy_recharge"] == 2 and resolved["initial_energy"] is None
    assert model == original


def test_same_time_switch_order_must_agree_with_the_damage_layer():
    document = _document()
    template, model = document["template"], document["rotation_model"]
    template["characters"].append({**copy.deepcopy(template["characters"][0]), "id": "b"})
    model["characters"].append({**copy.deepcopy(model["characters"][0]), "id": "b"})
    evidence = {"status": "assumed", "source": "纯接口顺序情景，不声明真实游戏可连续零时长切人"}
    for cid, target, order in (("a", "b", 0), ("b", "a", 1)):
        model["abilities"].append({"id": f"switch_{cid}", "character": cid,
            "action_type_id": "action.switch", "cooldown": None, "energy": None, **evidence})
        model["actions"].append({"id": f"swap_{cid}", "ability_id": f"switch_{cid}",
            "start": 2, "occupation_end": 2, "order": order, "switch_to": target,
            "repeat": {"mode": "each_cycle"}, **evidence})
    template["switches"] = [{"t": 0, "character": "a"}, {"t": 2, "character": "b"},
                            {"t": 2, "character": "a"}]
    assert evaluate_rotation(template, model)["status"] == "passed"
    template["switches"][1:] = reversed(template["switches"][1:])
    check = evaluate_rotation(template, model)
    assert check["status"] == "invalid"
    assert any(item["code"] == "switch_conflict" for item in check["issues"])
