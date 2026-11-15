"""Periodic hand-authored windows translate on the existing integer clock."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

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
