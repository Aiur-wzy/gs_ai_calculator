"""Pure numerical counter views; no frame authority or damage delivery claims."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.resource_counter_views import CounterViewError, CounterViewProgram
from genshin_engine.resource_counters import CounterProgram
from tests.test_resource_counters import definitions


def setup(*, kind="scaled", scope="pool_total", key=None, raw=401, scale=10):
    counters, pools, lives = definitions(scope=scope)
    pools["pools"][0]["unit_scale"] = scale
    if scope == "pool_key": pools["pools"][0].update(scope="per_character", initial={"a": 10, "b": 20})
    counter_program = CounterProgram(counters, pools, lives, character_ids=["a", "b"])
    state = counter_program.initial_state()
    state["values"]["count"] = {"total": raw} if scope == "pool_total" else {"a": raw, "b": raw+1}
    view = {"id": "read", "name_zh": "有界游戏读数", "owner": "a", "source_id": "source:view",
        "counter_id": "count", "key": key or {"kind": "total"},
        "transform": {"kind": "scaled", "max_atoms": 400} if kind == "scaled" else {"kind": "steps", "step_atoms": 10, "max_steps": 40},
        "status": "assumed", "source": "虚构数学工程定义，非游戏事实"}
    config = {"schema_version": 1, "views": [view]}
    return config, counter_program, state


@pytest.mark.parametrize("raw,numerator", [(399, 399), (400, 400), (401, 400)])
def test_scaled_cap_is_integer_before_unit_conversion_and_never_changes_raw(raw, numerator):
    conf, counters, state = setup(raw=raw)
    before = copy.deepcopy(state)
    result = CounterViewProgram(conf, counters).read("read", state)
    assert result["raw_atoms"] == raw and result["unit_scale"] == 10
    assert (result["numerator"], result["denominator"]) == (numerator, 10)
    assert result["cap_applied"] == (raw > 400) and state == before
    assert result["qualification"] == "not_evaluated"


@pytest.mark.parametrize("raw,steps,remainder", [(399, 39, 9), (400, 40, 0), (401, 40, 1), (420, 40, 0)])
def test_integer_steps_are_explicit_and_keep_remainder(raw, steps, remainder):
    conf, counters, state = setup(raw=raw, kind="steps")
    result = CounterViewProgram(conf, counters).read("read", state)
    assert (result["numerator"], result["denominator"]) == (steps, 1)
    assert result["remainder_atoms"] == remainder
    assert result["cap_applied"] == (raw//10 > 40)


def test_scaled_and_steps_do_not_conflate_fractional_quantity():
    results = []
    for kind in ("scaled", "steps"):
        conf, counters, state = setup(raw=19, kind=kind)
        results.append(CounterViewProgram(conf, counters).read("read", state))
    assert [(r["numerator"], r["denominator"]) for r in results] == [(19, 10), (1, 1)]


@pytest.mark.parametrize("key,recipient,expected", [
    ({"kind": "owner"}, None, 401), ({"kind": "character", "character_id": "b"}, None, 402),
    ({"kind": "recipient"}, "b", 402),
])
def test_per_key_selects_exact_owner_character_or_actual_recipient(key, recipient, expected):
    conf, counters, state = setup(scope="pool_key", key=key)
    conf["views"][0]["transform"].pop("max_atoms")
    result = CounterViewProgram(conf, counters).read("read", state, recipient=recipient)
    assert result["raw_atoms"] == expected


@pytest.mark.parametrize("recipient", [None, "missing", [], False])
def test_recipient_missing_never_falls_back_to_owner_or_zero(recipient):
    conf, counters, state = setup(scope="pool_key", key={"kind": "recipient"})
    with pytest.raises(CounterViewError): CounterViewProgram(conf, counters).read("read", state, recipient=recipient)


@pytest.mark.parametrize("mutation", ["unknown_counter", "wrong_owner", "key_scope", "duplicate_id", "zero_step", "bool_cap", "pending", "unknown_field"])
def test_bad_or_unverified_view_definitions_fail_explicitly(mutation):
    conf, counters, _ = setup(kind="steps")
    view = conf["views"][0]
    if mutation == "unknown_counter": view["counter_id"] = "missing"
    if mutation == "wrong_owner": view["owner"] = "b"
    if mutation == "key_scope": view["key"] = {"kind": "owner"}
    if mutation == "duplicate_id": conf["views"].append(copy.deepcopy(view))
    if mutation == "zero_step": view["transform"]["step_atoms"] = 0
    if mutation == "bool_cap": view["transform"]["max_steps"] = True
    if mutation == "pending": view["status"] = "pending"
    if mutation == "unknown_field": view["transform"]["clock"] = .1
    with pytest.raises(CounterViewError): CounterViewProgram(conf, counters)


def test_definitions_and_readings_are_detached_without_ledger_mutation():
    conf, counters, state = setup()
    program = CounterViewProgram(conf, counters)
    before = copy.deepcopy(state)
    conf["views"][0]["transform"]["max_atoms"] = 1
    first = program.read("read", state)
    first["transform"]["max_atoms"] = 2
    second = program.read("read", state)
    assert second["numerator"] == 400 and second["transform"]["max_atoms"] == 400
    assert state == before
    assert second["counter_cursor"] == {"control_sequence": 0, "ledger_sequence": 0}


def test_known_unstarted_state_is_zero_arithmetic_without_qualification_claim():
    conf, counters, _ = setup()
    result = CounterViewProgram(conf, counters).read("read", counters.initial_state())
    assert result["numerator"] == 0 and result["lifecycle"]["phase"] == "inactive"
    assert result["qualification"] == "not_evaluated"


def test_scale_changes_exact_fraction_and_definition_fingerprint():
    rows = []
    for scale in (10, 100):
        conf, counters, state = setup(raw=19, scale=scale)
        rows.append(CounterViewProgram(conf, counters).read("read", state))
    assert [r["denominator"] for r in rows] == [10, 100]
    assert rows[0]["definition_fingerprint"] != rows[1]["definition_fingerprint"]


def test_largest_supported_integer_is_read_without_float_rounding():
    conf, counters, state = setup(raw=10**15, scale=999_983)
    conf["views"][0]["transform"] = {"kind": "scaled"}
    result = CounterViewProgram(conf, counters).read("read", state)
    assert (result["numerator"], result["denominator"]) == (10**15, 999_983)
    assert type(result["numerator"]) is int and type(result["denominator"]) is int


@pytest.mark.parametrize("bad", [None, {}, [], True])
def test_unstructured_state_is_rejected_with_view_error(bad):
    conf, counters, _ = setup()
    with pytest.raises(CounterViewError) as exc: CounterViewProgram(conf, counters).read("read", bad)
    assert exc.value.issue["code"] == "counter_view_state"


def test_state_from_different_counter_definition_cannot_supply_view_value():
    conf, _, state = setup()
    _, other, _ = setup(scale=100)
    with pytest.raises(CounterViewError): CounterViewProgram(conf, other).read("read", state)


@pytest.mark.parametrize("field,value", [("max_atoms", 0), ("max_atoms", 10**15+1), ("max_atoms", 1.5)])
def test_cap_requires_bounded_positive_integer_atoms(field, value):
    conf, counters, _ = setup()
    conf["views"][0]["transform"][field] = value
    with pytest.raises(CounterViewError): CounterViewProgram(conf, counters)


def test_views_do_not_acquire_timing_or_domain_permissions_from_fields():
    for field, value in [("sample", "current"), ("domain", "provider"), ("event_id", "cast")]:
        conf, counters, _ = setup()
        conf["views"][0][field] = value
        with pytest.raises(CounterViewError): CounterViewProgram(conf, counters)


def test_definition_order_does_not_reorder_numerical_identity():
    conf, counters, state = setup()
    conf["views"].append({**copy.deepcopy(conf["views"][0]), "id": "another", "source_id": "source:another"})
    first = CounterViewProgram(conf, counters).read("read", state)
    conf["views"].reverse()
    assert CounterViewProgram(conf, counters).read("read", state) == first
