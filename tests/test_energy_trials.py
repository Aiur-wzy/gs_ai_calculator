"""Real two-Q execution with changing ER and rejected/stale trial evidence."""
import copy

import pytest

from genshin_app.energy_trials import trial_energy_recharge
from genshin_app.equipment import new_plan
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_engine.execution_control import ExecutionCancelled


def plan():
    from tests.test_mechanics_execution import fixture
    template, rotation, mechanics = fixture((.2, 4.2))
    template["loop_duration"] = 10
    template["meta"] = {"version_line": "release"}
    template["characters"][0]["stats"].update(energy_recharge=1, crit_rate=0)
    result = enable_mechanics(new_plan(template))
    evidence = {"status": "assumed", "source": "虚构工程两次Q，不能作为真实角色资料"}
    once = {"mode": "once"}
    rotation.update(period=10, max_cycles=3, initial_frontline="a")
    rotation["characters"] = [{"id": "a", "element": "cryo", "energy_recharge": 1,
        "energy_capacity": 10, "initial_energy": 10, **evidence}]
    rotation["abilities"] = [{"id": "q", "character": "a", "action_type_id": "action.elemental_burst",
        "cooldown": None, "energy": {"required": 10, "amount": 10}, **evidence}]
    rotation["actions"] = [{"id": name, "ability_id": "q", "start": t, "occupation_end": t + .1,
        "order": index, "repeat": once, **evidence} for index, (name, t) in enumerate((("q1", 0), ("q2", 4)))]
    rotation["events"] = [{"id": "return", "type": "energy_direct", "t": 1, "order": 0,
        "amounts": {"a": 4}, "repeat": once, **evidence}]
    rotation["events"].extend({"id": "spend." + name, "type": "energy_spend", "t": t, "order": 5,
        "character": "a", "action_id": name, "required": 10, "amount": 10, "repeat": once, **evidence}
        for name, t in (("q1", 0), ("q2", 4)))
    rotation["energy_recharge_windows"] = [{"id": "er-window", "character": "a", "start": .5,
        "end": 3, "delta": 1, "repeat": once, **evidence}]
    mechanics["rules"], mechanics["initial_state"] = [], {"rules": []}
    mechanics["hit_events"] = [{"hit_id": hit["id"], "action_id": "q" + str(index + 1), "order": 10 + index}
        for index, hit in enumerate(template["hits"])]
    result.update(rotation_model=rotation, mechanics_model=mechanics, effect_bindings=[])
    return result


def request():
    return {"trials": [{"id": "er-200", "stats": {"a": {"energy_recharge": 2}},
                         "roll_costs": {"ER": 3}, "note": "工程试算，不声明最佳"}]}


def test_actual_engine_checks_both_qs_and_dynamic_er_without_editing_input(tmp_path):
    original = plan()
    before = copy.deepcopy(original)
    output = trial_energy_recharge(original, tmp_path / "absent.db", request())
    assert original == before and output["status"] == "completed"
    baseline, trial = output["trials"]
    assert baseline["status"] == "tested" and baseline["calculation"]["task_admission"] != "accepted"
    assert baseline["energy"]["bursts"][1]["before"] == 8
    assert baseline["energy"]["bursts"][1]["deficit"] == 2
    assert trial["status"] == "tested", trial
    assert trial["energy"]["bursts"][1]["before"] == 10
    assert trial["energy"]["bursts"][1]["deficit"] == 0
    assert trial["roll_costs"] == {"ER": 3}
    assert "全局最低" in output["interpretation"] and output["candidate_effect"] == "none"


def test_trial_cannot_resign_stale_attribute_evidence(tmp_path, monkeypatch):
    original = plan()
    original["attribute_review"] = {"mode": "manual_context", "context_fingerprint": "a" * 64,
                                     "note": "旧输入的人工属性确认"}
    before = copy.deepcopy(original)
    def forbidden(*_args, **_kwargs):
        pytest.fail("read-only ER trial attempted to reapprove attributes")
    monkeypatch.setattr("genshin_app.equipment.attributes.prepare_attribute_review", forbidden)
    output = trial_energy_recharge(original, tmp_path / "absent.db", request())
    assert original == before
    assert all(row["status"] == "incomplete" for row in output["trials"])
    assert all("tested_damage_summary" not in row for row in output["trials"])


def test_trial_never_calls_live_provisional_publisher_or_expands_approval(tmp_path):
    original = plan()
    original["equipment_inputs"]["a"] = {"mode": "panel", "character_entity_ref": {
        "kind": "provisional", "entity_type": "character", "provisional_id": "missing-character"}}
    class Live:
        def publish_provisional_usage_receipt(self, **_):
            pytest.fail("trial published a usage receipt on the live session")
    output = trial_energy_recharge(original, tmp_path / "absent.db", request(), candidate_session=Live())
    assert all(row["status"] == "incomplete" for row in output["trials"])
    assert all(row["failure"]["reason"] == "energy_trial_trust_scope_unavailable" for row in output["trials"])


def test_cancel_keeps_completed_comparisons_without_restarting_them(tmp_path, monkeypatch):
    import genshin_app.energy_trials as module
    calls = []
    def check():
        calls.append(True)
        if len(calls) > 1:
            raise ExecutionCancelled()
    monkeypatch.setattr(module, "check_execution_cancelled", check)
    output = trial_energy_recharge(plan(), tmp_path / "absent.db", request())
    assert output["status"] == "cancelled" and output["trials"][0]["id"] == "baseline"
    assert output["trials"][0]["status"] == "tested" and output["trials"][1]["status"] == "cancelled"


def test_ordinary_trial_execution_qualification_is_advisory_not_a_publication_claim(tmp_path):
    output = trial_energy_recharge(plan(), tmp_path / "absent.db", request(), energy_intent="multi_round_legal")
    baseline, changed = output["trials"]
    assert changed["energy_qualification"]["execution_passed"] is True
    assert changed["energy_qualification"]["formal_result_qualification"] == "not_applicable"
    assert "can_accept" not in changed["energy_qualification"]
    assert baseline["energy_qualification"]["execution_passed"] is False
    assert baseline["energy_qualification"]["energy_validation"] == "failed"


def test_auxiliary_execution_preserves_real_numeric_and_source_failures(tmp_path):
    import copy

    from genshin_app.calculation_qualification import qualify_auxiliary_execution
    from genshin_app.equipment.service import run_plan_calc
    source = plan()
    source["manual_template"]["characters"][0]["stats"]["energy_recharge"] = 2
    actual = run_plan_calc(source, tmp_path / "absent.db")
    missing = copy.deepcopy(actual)
    missing["result"]["summary"].pop("total_damage")
    if "calculation_totals" in missing["result"]:
        missing["result"]["calculation_totals"].pop("total_damage", None)
    failed = qualify_auxiliary_execution(missing, energy_intent="multi_round_legal")
    assert failed["execution_passed"] is False
    assert "numeric_result_unavailable" in failed["completion"]["reasons"]
    missing = copy.deepcopy(actual)
    missing["report"]["state"] = "pending"
    failed = qualify_auxiliary_execution(missing, energy_intent="multi_round_legal")
    assert failed["execution_passed"] is False
    assert "assembly_not_ready" in failed["completion"]["reasons"]
