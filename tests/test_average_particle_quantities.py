"""Numerical conservation and reporting of explicit average particle scenarios."""
from __future__ import annotations

import json

import pytest

from genshin_app.equipment.energy_generation_receipt import _energy_arithmetic
from genshin_app.rotation_check import check_sheets, describe_rotation_check
from tests.test_mechanics_execution import add, fixture, rule, run
from tests.test_rotation_energy import _action, _check, _event, _evidence, _model, _row

pytest_plugins = ("test_energy_generation_sources", "test_pipeline")


def resource_only(count=4 / 3, *, credit_at=1.2, lost=0):
    model = _model()
    model.update(abilities=[{**model["abilities"][0], "cooldown": None, "rules": []}],
                 actions=[{**_action("cast", "e", 0, .1), "repeat": {"mode": "once"}}], initial_cooldowns=[])
    model["characters"][0].update(initial_energy=0, energy_capacity=100)
    model["events"] = [
        _event("spawn", "energy_spawn", .3, particle_type="particle", element="cryo",
               count=count, lost_count=lost, source_kind="enemy"),
        _event("credit", "energy_credit", credit_at, spawn_id="spawn", count=count - lost),
    ]
    return model


def test_120_seconds_and_cross_cycle_credit_use_actual_recipient_er_and_caps():
    model = resource_only(8 / 3, credit_at=20.2)
    model.update(period=20, max_cycles=6)
    model["characters"][0].update(energy_capacity=10)
    model["characters"].append({"id": "b", "element": "hydro", "energy_recharge": 1.5,
                               "energy_capacity": 100, "initial_energy": 0, **_evidence()})
    model["energy_recharge_windows"] = [{"id": "double", "character": "a", "start": 0,
        "end": .5, "delta": 1, "repeat": {"mode": "each_cycle"}, **_evidence()}]
    check = _check(model)
    assert check["energy_check"]["checked_cycles"] == 6
    assert check["cycles"][0]["boundary_state"]["in_flight"] == [("spawn", -1, 8 / 3)]
    credit = _row(check, "credit")
    assert credit["t"] == 20.2 and credit["cycle"] == 1
    assert credit["count"] == 8 / 3
    assert credit["remaining_count"] == 0
    front, off = credit["energy"]
    assert front["raw"] == pytest.approx(16) and front["gain"] == 10
    assert front["overflow"] == pytest.approx(6) and front["energy_recharge"] == 2
    assert off["raw"] == pytest.approx((8 / 3) * .8 * 1.5)
    assert check["energy_check"]["particle_quantity"]["count_mode"] == "average"


@pytest.mark.parametrize("count", [.3, .1 + .2])
def test_split_collection_and_loss_leave_no_phantom_particles(count):
    model = resource_only(count, lost=.1)
    model["events"][1]["count"] = .1
    model["events"].append(_event("second", "energy_credit", 1.3, spawn_id="spawn", count=.1))
    result = _check(model)
    assert result["status"] == "unproven"
    assert _row(result, "second")["remaining_count"] == 0
    assert all(not cycle["boundary_state"]["in_flight"] for cycle in result["cycles"])
    model["events"].append(_event("repeated", "energy_credit", 1.4, spawn_id="spawn", count=.1))
    assert _check(model)["issues"][0]["code"] == "particle_count"


def test_rounding_reconciles_only_the_available_supply():
    model = resource_only(.3)
    model["events"][1]["count"] = .1
    model["events"].append(_event("second", "energy_credit", 1.3, spawn_id="spawn", count=.2 + 5e-17))
    result = _check(model)
    credit = _row(result, "second")
    assert credit["remaining_count"] == 0
    assert credit["count"] == .2
    assert credit["declared_count"] == .2 + 5e-17
    assert credit["energy"][0]["raw"] == .2 * 3


@pytest.mark.parametrize("count", [-.5, float("inf"), float("nan"), True])
def test_invalid_particle_quantities_fail_before_execution(count):
    model = resource_only()
    model["events"][0]["count"] = count
    assert _check(model)["status"] == "invalid"


def test_fixed_declaration_cannot_silently_turn_into_average():
    model = resource_only()
    model["events"][0]["count_mode"] = "fixed"
    assert _check(model)["issues"][0]["code"] == "particle_count_mode_conflict"


def test_zero_branch_mean_includes_zero_and_preserves_branch_evidence():
    model = resource_only(1)
    distribution = [{"count": 0, "probability": .5}, {"count": 2, "probability": .5}]
    model["events"][0].update(count_mode="average", count_distribution=distribution)
    check = _check(model)
    assert _row(check, "spawn")["count_distribution"] == distribution
    assert _row(check, "credit")["energy"][0]["raw"] == 3
    assert [row["type"] for row in check["event_log"] if row["source_cycle"] == 0] == [
        "action", "energy_spawn", "energy_credit"]
    assert check["energy_check"]["particle_quantity"]["proof_scope"] == "deterministic_average_scenario"
    model["events"][0]["count"] = 2
    assert _check(model)["issues"][0]["code"] == "particle_distribution_mean"
    model["events"][0]["count_distribution"][0]["probability"] = .1
    assert _check(model)["issues"][0]["code"] == "particle_distribution_probability"


@pytest.mark.parametrize("shortfall", [0, 1e-10])
def test_q_threshold_does_not_use_legacy_absolute_tolerance_for_average_income(shortfall):
    model = resource_only(4 / 3 - shortfall)
    model["abilities"] = [_model()["abilities"][1]]
    model["abilities"][0]["energy"] = {"required": 4, "amount": 4}
    model["actions"] = [_action("q1", "q", 2, 2.5)]
    model["events"].append(_event("spend", "energy_spend", 2, order=1, action_id="q1",
                                  character="a", required=4, amount=4))
    result = _check(model)
    assert (_row(result, "spend")["status"] == "failed") == bool(shortfall)


def test_derived_average_uses_same_ledger_and_survives_json_and_excel_views():
    template, model, mechanics = fixture((.2,))
    model["characters"][0].update(initial_energy=0)
    distribution = [{"count": 1, "probability": 2 / 3}, {"count": 2, "probability": 1 / 3}]
    add(mechanics, rule(resources=[{"id": "particles", "kind": "particles", "delay": 9,
        "travel_time": 2, "count": 4 / 3, "count_mode": "average", "count_distribution": distribution,
        "particle_type": "particle", "element": "cryo", "source_part": "particles"}]))
    mechanics["resource_ownership"] = [{"rule_id": "boost", "action_id": "cast",
                                       "source_part": "particles", "replaces_event_ids": []}]
    check = run(template, model, mechanics)["rotation_check"]
    check = describe_rotation_check(check, template, model, [], mechanics_model=mechanics)
    check = json.loads(json.dumps(check, allow_nan=False))
    credit = next(row for row in check["event_log"] if row["type"] == "energy_credit")
    manual = resource_only(4 / 3, credit_at=11)
    manual["characters"][0]["energy_capacity"] = 10
    assert credit["energy"] == _row(_check(manual), "credit")["energy"]
    _energy_arithmetic({"rotation_check": check})
    sheets = dict(check_sheets(check))
    assert ["产球口径", check["energy_check"]["particle_quantity"]["message"]] in sheets["动作能量结论"]
    assert any(row[-4:] == [4 / 3, None, 0.0, "平均产球"] for row in sheets["能量流水"][1:])


def test_existing_integer_check_and_weapon_income_keep_their_fixed_meaning():
    model = _model()
    result = _check(model)
    assert result["status"] == "passed"
    assert "particle_quantity" not in result["energy_check"]
    assert _row(result, "credit")["energy"][0]["raw"] == 12
    weapon = resource_only(3)
    weapon["events"][0].update(source_kind="weapon", action_id="cast", element="clear")
    weapon["abilities"] = [_model()["abilities"][0]]
    weapon["abilities"][0].update(cooldown=None, rules=[])
    weapon["actions"] = [_action("cast", "e", 0, .2)]
    check = _check(weapon)
    assert _row(check, "credit")["energy"][0]["raw"] == 6


def test_native_plan_with_fixed_source_and_manual_average_roundtrips_export(trigger_db):
    from io import BytesIO
    from xml.etree import ElementTree
    from zipfile import ZipFile

    from test_energy_generation_sources import confirm_energy, energy_plan

    from genshin_app.equipment import run_plan_calc
    from genshin_app.equipment.delivery import validate_delivery_calculated
    from genshin_app.equipment.service import result_bytes

    plan = energy_plan(trigger_db)
    assert plan["energy_generation_sources"]
    once = {"mode": "once"}
    plan["rotation_model"]["events"].extend([
        _event("manual-drop", "energy_spawn", .55, source_kind="enemy", count=4 / 3,
               particle_type="particle", element="clear", repeat=once),
        _event("manual-credit", "energy_credit", .75, spawn_id="manual-drop", count=4 / 3,
               repeat=once),
    ])
    result = run_plan_calc(confirm_energy(plan, trigger_db), trigger_db, capture_display=True)
    reopened = json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False))
    validate_delivery_calculated(reopened)
    rotation = reopened["result"]["rotation_check"]
    assert _row(rotation, "manual-credit")["count"] == 4 / 3
    assert rotation["energy_check"]["particle_quantity"]["count_mode"] == "average"
    workbook = result_bytes(reopened["result"], reopened["result_receipt"], "xlsx")
    with ZipFile(BytesIO(workbook)) as archive:
        sheets = [ElementTree.fromstring(archive.read(name)) for name in archive.namelist()
                  if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")]
    assert any(node.tag.endswith("}v") and node.text == str(4 / 3)
               for sheet in sheets for node in sheet.iter())
