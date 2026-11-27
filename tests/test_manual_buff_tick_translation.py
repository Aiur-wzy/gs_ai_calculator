"""Periodic hand-authored windows translate on the existing integer clock."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from genshin_app.authoring_assistance import authoring_assistance
from genshin_app.equipment import compose_equipment_plan, new_plan, run_plan_calc
from genshin_app.equipment.files import fingerprint, input_view, read_plan
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.input_editing import prepare_input_edit
from genshin_engine.damage_report_window import CONTRACT
from genshin_engine.mechanics import _Mechanics, execute_mechanics
from genshin_engine.mechanics_validation import _tick
from genshin_engine.rotation_port import TICKS
from tests.test_mechanics_execution import fixture


def runtime(*, period=.1, start=0, end=.2, repeat="each_cycle"):
    return SimpleNamespace(
        model={"manual_buff_repeat": repeat} if repeat is not None else {},
        template={"buffs": [{"id": "window", "start": start, "end": end}]},
        data={"period": _tick(period)},
    )


def windows(state, time):
    return list(_Mechanics.manual_buffs(state, time))


@pytest.mark.parametrize("repeat", [None, "each_cycle"])
def test_repeated_boundary_keeps_every_overlapping_copy_on_exact_ticks(repeat):
    state = runtime(repeat=repeat)
    original = copy.deepcopy(state)
    result = windows(state, 100000)
    assert [(_tick(row["start"]), _tick(row["end"])) for row in result] == [
        (0, 200000), (100000, 300000)]
    assert result[1]["end"] == .3
    assert state.__dict__ == original.__dict__
    with pytest.raises(ValueError):
        _tick(.30000000000000004)  # The author validator was not relaxed.


@pytest.mark.parametrize("repeat", ["each_cycle", "once"])
def test_microsecond_window_has_half_open_edges_and_once_does_not_repeat(repeat):
    state = runtime(period=1, start=.123456, end=.123457, repeat=repeat)
    assert windows(state, 123455) == []
    assert len(windows(state, 123456)) == 1
    assert windows(state, 123457) == []
    assert len(windows(state, TICKS+123456)) == int(repeat == "each_cycle")
    assert windows(state, TICKS+123457) == []


def test_realistic_six_digit_period_does_not_accumulate_decimal_drift():
    state = runtime(period=42.633336, end=42.643335)
    time = 3*state.data["period"]
    result = windows(state, time)
    assert [(_tick(row["start"]), _tick(row["end"])-time) for row in result] == [
        (2*state.data["period"], 9999), (time, 42643335)]


@pytest.mark.parametrize("field", ["start", "end"])
def test_invalid_authored_submicrosecond_endpoints_are_not_rounded(field):
    state = runtime()
    state.template["buffs"][0][field] = .0000001
    before = copy.deepcopy(state.__dict__)
    with pytest.raises(ValueError, match="微秒"):
        windows(state, 100000)
    assert state.__dict__ == before


def test_generic_full_execution_mre_accepts_legal_each_cycle_windows_without_mutation():
    template, rotation, model = fixture((.03,))
    template["loop_duration"] = rotation["period"] = .1
    rotation["actions"][0]["occupation_end"] = .01
    template["buffs"] = [{
        "id": "window", "name": "Engineering window", "scope": "team", "start": 0, "end": .2,
        "effects": [{"id": "zero", "benefit_type_id": "benefit.crit_rate.all", "zone": "crit_rate",
                     "stat": "all", "value": 0, "applies_to": {"mode": "all"}}]}]
    original = copy.deepcopy((template, rotation, model))
    output = execute_mechanics(template, rotation, model)
    assert output["status"] == "passed", output["issues"]
    assert output["result"]["hits"][0]["expected"] == 45
    assert (template, rotation, model) == original


@pytest.mark.parametrize("end", [None, -1])
def test_existing_invalid_template_endpoints_still_fail_validation(end):
    template, rotation, model = fixture((.03,))
    template["buffs"] = [{
        "id": "invalid", "start": 0, "end": end, "effects": [{"id": "zero",
            "benefit_type_id": "benefit.crit_rate.all", "zone": "crit_rate", "stat": "all",
            "value": 0, "applies_to": {"mode": "all"}}]}]
    output = execute_mechanics(template, rotation, model)
    assert output["status"] == "invalid" and output["result"] is None


def finite_input(repeat=None):
    template, rotation, model = fixture((.999999, 1))
    template["loop_duration"] = rotation["period"] = 1
    template["characters"][0]["stats"]["energy_recharge"] = 1
    rotation["actions"][0]["repeat"] = {"mode": "once"}
    rotation["damage_report"] = {"contract": CONTRACT, "mode": "explicit", "end": 1}
    template["buffs"] = [{
        "id": "window", "name": "Declared once or per cycle", "scope": "team",
        "start": 0, "end": 1.000001,
        "extensions": {"owner": "a", "source_id": "fixture:window"},
        "effects": [{"id": "boost", "benefit_type_id": "benefit.dmg_bonus.all",
                     "zone": "dmg_bonus", "stat": "all", "value": .1,
                     "applies_to": {"mode": "all"}}],
    }]
    if repeat is not None:
        model["manual_buff_repeat"] = repeat
    return template, rotation, model


@pytest.mark.parametrize(("repeat", "before_and_at_cutoff"), [
    (None, (1, 2)), ("each_cycle", (1, 2)), ("once", (1, 1)),
])
def test_finite_cutoff_preserves_authored_manual_windows(repeat, before_and_at_cutoff):
    template, rotation, model = finite_input(repeat)
    output = execute_mechanics(template, rotation, model)

    assert output["status"] in {"passed", "unproven"}, output["issues"]
    assert output["result"]["damage_report_window"]["end"] == 1
    assert [row["expected"] for row in output["result"]["hits"]] == pytest.approx([
        45 * (1 + .1 * count) for count in before_and_at_cutoff])
    evidence = [row for row in output["mechanics_trace"]
                if row["type"] == "manual_effect_applied" and row["event_id"] == "h1"]
    expected_windows = [(0, 1.000001)]
    if before_and_at_cutoff[1] == 2:
        expected_windows.append((1, 2.000001))
    assert [(row["window_start"], row["window_end"]) for row in evidence] == expected_windows
    assert all(row["buff_id"] == "window" for row in evidence)

    state = runtime(period=1, end=1.000001, repeat=repeat)
    assert len(windows(state, TICKS + 1)) == int(repeat != "once")


def test_implicit_legacy_overlap_warns_and_new_finite_authoring_reopens_once(tmp_path):
    template, rotation, model = finite_input()
    db = tmp_path / "absent.db"
    draft = enable_mechanics(new_plan(template))
    draft.update(rotation_model=rotation, mechanics_model=model)

    # An existing file retains its established each_cycle semantics. The
    # warning gives a concrete way to fix an unintended cross-boundary copy.
    legacy = compose_equipment_plan(draft, db)
    assert legacy["state"] == "ready", legacy["missing_inputs"]
    [warning] = legacy["warnings"]
    assert warning["code"] == "manual_buff_repeat_at_report_end"
    assert warning["buff_ids"] == ["window"]
    assert warning["t"] == 1

    draft["mechanics_model"] = None
    request = {"kind": "mechanics_fragment", "version": 1, "request_id": "buff.window",
               "expected_revision": draft["revision"],
               "expected_input_fingerprint": fingerprint(input_view(draft)),
               "payload": {"mechanics_model": model}}
    response = authoring_assistance(request, plan=draft)
    authored = prepare_input_edit(draft, response["edit_request"])
    assert authored["mechanics_model"]["manual_buff_repeat"] == "once"
    delivered = run_plan_calc(authored, db)
    assert delivered["report"]["warnings"] == []
    assert delivered["result"]["summary"]["total_damage"] == pytest.approx(99)
    path = tmp_path / "fixed.json"
    path.write_text(json.dumps(delivered["plan"]), encoding="utf-8")
    reopened = read_plan(path)["plan"]
    assert reopened["mechanics_model"]["manual_buff_repeat"] == "once"
    assert run_plan_calc(reopened, db)["result"] == delivered["result"]

    # A fresh author can still deliberately request the legacy repeated scope.
    request["payload"]["mechanics_model"] = {**model, "manual_buff_repeat": "each_cycle"}
    response = authoring_assistance(request, plan=draft)
    assert response["fragment"]["mechanics_model"]["manual_buff_repeat"] == "each_cycle"
