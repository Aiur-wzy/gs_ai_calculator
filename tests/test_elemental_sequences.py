"""Finite ICD sequence inputs, independently transcribed from two raw records.

Source: YuanShenResources commit 342084292ba4c3c6ec2bc34c045de4de72ae8d53,
ExcelBinOutput/AttackAttenuationExcelConfigData.json, rows /391 and /56.
The records prove sequence parameters, not any attack-to-group assignment or GU.
"""
from __future__ import annotations

import copy

import pytest

from genshin_engine.elemental import (
    ElementalPending,
    ElementalRuntime,
    validate_elemental_model,
)
from genshin_engine.elemental_schema import elemental_capabilities

REAL_ROWS = {
    "default": {"reset_seconds": 2.5, "sequence": [1, 0, 0] * 8, "pointer": "/391"},
    "Diluc": {"reset_seconds": 5, "sequence": [1, 0, 0, 0, 0] * 6, "pointer": "/56"},
}


def model(name="default"):
    row = REAL_ROWS[name]
    return {"schema_version": 1, "ruleset": "gauge-basic-v1",
            "target": {"id": "enemy", "kind": "ordinary", "knowledge": "known", "initial_aura": None,
                       "status": "assumed", "source": "controlled ordinary-target initial state"},
            "profiles": [{"id": "attack", "element": "pyro", "application": "apply", "gauge_units": 1,
                          "icd": {"kind": "sequence", "group": "explicit-shared-group",
                                  "reset_seconds": row["reset_seconds"], "sequence": list(row["sequence"])},
                          "status": "assumed", "source": "342084292ba4c3c6ec2bc34c045de4de72ae8d53:"
                          "ExcelBinOutput/AttackAttenuationExcelConfigData.json" + row["pointer"]
                          + "; sequence fields only; assignment and GU are controlled test assumptions"}]}


def attempt(rt, microseconds=0, *, profile="attack", actor="a", action="cast", landed=True,
            attempted=True):
    return rt.attempt(event_id=f"event-{len(rt.trace)}", action_id=action, actor=actor, time=microseconds,
                      application={"profile_id": profile, "target_id": rt.target, "landed": landed,
                                   "attempted": attempted})


@pytest.mark.parametrize(("name", "allowed_hits"), [
    ("default", [1, 4, 7, 10, 13, 16, 19, 22]),
    ("Diluc", [1, 6, 11, 16, 21, 26]),
])
def test_real_source_sequences_have_finite_not_wrapping_tails(name, allowed_hits):
    value = model(name)
    assert validate_elemental_model(value) == []
    original = copy.deepcopy(value)
    rt = ElementalRuntime(value)
    rows = [attempt(rt, i * 10_000) for i in range(38)]
    assert [i + 1 for i, row in enumerate(rows) if row["allowed"]] == allowed_hits
    assert rows[-1]["after"]["icd"][0]["hits"] == 38
    assert rows[-1]["source"] == value["profiles"][0]["source"]
    assert value == original


def test_standard_is_the_infinite_two_point_five_second_or_three_hit_rule():
    standard = model()
    standard["profiles"][0]["icd"] = {"kind": "standard", "group": "explicit-shared-group"}
    explicit = copy.deepcopy(standard)
    explicit["profiles"][0]["icd"] = {
        "kind": "timer_counter", "group": "explicit-shared-group",
        "reset_seconds": 2.5, "hit_interval": 3}
    left, right = ElementalRuntime(standard), ElementalRuntime(explicit)
    times = [i * 10_000 for i in range(30)] + [2_499_999, 2_500_000, 2_600_000]
    assert [attempt(left, time)["allowed"] for time in times] == [
        attempt(right, time)["allowed"] for time in times]
    assert left.boundary(3_000_000, 1) == right.boundary(3_000_000, 1)


def test_finite_repeating_sequence_is_not_inferred_as_an_infinite_counter_rule():
    finite = ElementalRuntime(model())
    standard_model = model()
    standard_model["profiles"][0]["icd"] = {
        "kind": "standard", "group": "explicit-shared-group"}
    standard = ElementalRuntime(standard_model)

    finite_rows = [attempt(finite, index) for index in range(28)]
    standard_rows = [attempt(standard, index) for index in range(28)]

    assert not finite_rows[27]["allowed"]
    assert standard_rows[27]["allowed"]


def test_timer_counter_remains_infinite_beyond_finite_database_sequence_limits():
    value = model()
    value["profiles"][0]["icd"] = {
        "kind": "timer_counter", "group": "explicit-shared-group",
        "reset_seconds": 2.5, "hit_interval": 3}
    rt = ElementalRuntime(value)

    rows = [attempt(rt, index) for index in range(130)]

    assert [index + 1 for index, row in enumerate(rows) if row["allowed"]] == list(
        range(1, 131, 3))
    assert rows[-1]["after"]["icd"][0]["attempts"] == 130


def test_diluc_count_application_does_not_move_five_second_timer():
    rt = ElementalRuntime(model("Diluc"))
    times = (0, 100_000, 200_000, 300_000, 400_000, 4_900_000, 5_000_000, 5_100_000)
    rows = [attempt(rt, time) for time in times]
    assert [row["allowed"] for row in rows] == [True, False, False, False, False, True, True, False]
    assert rows[5]["after"]["icd"][0]["timer_remaining"] == .1
    assert rows[6]["reason"] == "icd_timer_reset"
    assert rows[6]["after"]["icd"][0]["timer_remaining"] == 5


def test_mutated_zero_first_item_is_respected_on_first_and_timer_reset_hits():
    value = model()
    value["profiles"][0]["icd"].update(sequence=[0, 1, 0], reset_seconds=1)
    rt = ElementalRuntime(value)
    times = (0, 100_000, 200_000, 300_000, 999_999, 1_000_000, 1_100_000)
    rows = [attempt(rt, time) for time in times]
    assert [row["allowed"] for row in rows] == [False, True, False, False, False, False, True]
    assert rows[0]["after"]["aura"] is None
    assert rows[5]["after"]["icd"][0]["hits"] == 1


def test_one_item_sequence_stays_exhausted_and_late_hit_reanchors_timer():
    value = model()
    value["profiles"][0]["icd"].update(sequence=[1], reset_seconds=.000003)
    rt = ElementalRuntime(value)
    rows = [attempt(rt, time) for time in (0, 1, 2, 10, 11, 12, 13)]
    assert [row["allowed"] for row in rows] == [True, False, False, True, False, False, True]


def test_shared_sequence_survives_actions_and_profiles_but_not_actor_identity():
    value = model()
    value["profiles"][0]["icd"]["sequence"] = [1, 0, 1]
    value["profiles"].append({**copy.deepcopy(value["profiles"][0]), "id": "other-profile"})
    assert validate_elemental_model(value) == []
    rt = ElementalRuntime(value)
    assert attempt(rt, actor="a", action="first")["allowed"]
    assert not attempt(rt, 1, profile="other-profile", action="second")["allowed"]
    assert attempt(rt, 2, actor="b")["allowed"]
    assert attempt(rt, 3, actor="a", action="third")["allowed"]
    assert set(rt.icd) == {("a", "explicit-shared-group", "enemy"), ("b", "explicit-shared-group", "enemy")}
    other = model()
    other["target"]["id"] = "other-enemy"
    second = ElementalRuntime(other)
    assert attempt(second)["allowed"]
    assert set(second.icd) == {("a", "explicit-shared-group", "other-enemy")}


def test_miss_does_not_consume_sequence_position_or_start_timer():
    value = model()
    value["profiles"][0]["icd"]["sequence"] = [0, 1]
    rt = ElementalRuntime(value)
    assert attempt(rt, landed=False)["reason"] == "missed"
    assert rt.state()["icd"] == []
    assert not attempt(rt, 100_000)["allowed"]
    assert attempt(rt, 200_000, landed=False)["reason"] == "missed"
    assert attempt(rt, 300_000)["allowed"]
    assert rt.state()["icd"][0]["timer_remaining"] == 2.3


@pytest.mark.parametrize("change", [
    {"sequence": []}, {"sequence": [1] * 65}, {"sequence": [1, .5]}, {"sequence": [1., 0]},
    {"sequence": [True, 0]}, {"sequence": [1, False]}, {"sequence": [-1]}, {"sequence": [2]},
    {"reset_seconds": 0}, {"reset_seconds": -1}, {"reset_seconds": True},
    {"reset_seconds": float("nan")}, {"reset_seconds": float("inf")}, {"reset_seconds": 10**400},
    {"reset_seconds": .0000001}, {"reset_seconds": 2.50000001}, {"reset_seconds": "2.5"},
    {"kind": "fractional"}, {"kind": "standard"}, {"kind": "none"},
])
def test_unknown_fractional_invalid_or_unused_parameters_are_not_executed(change):
    value = model()
    value["profiles"][0]["icd"].update(change)
    assert validate_elemental_model(value)


@pytest.mark.parametrize("field", ["sequence", "reset_seconds", "group"])
def test_sequence_requires_its_complete_explicit_contract(field):
    value = model()
    del value["profiles"][0]["icd"][field]
    assert validate_elemental_model(value)


def test_maximum_length_allows_exactly_sixty_four_nonzero_entries():
    value = model()
    value["profiles"][0]["icd"].update(sequence=[1] * 64, reset_seconds=.000001)
    assert validate_elemental_model(value) == []
    rt = ElementalRuntime(value)
    assert all(attempt(rt)["allowed"] for _ in range(64))
    assert not attempt(rt)["allowed"]
    assert attempt(rt, 1)["allowed"]


@pytest.mark.parametrize("change", [{"reset_seconds": 3}, {"sequence": [1, 0]}, {"sequence": [1, 0, 0] * 9}])
def test_same_group_conflicting_profile_rules_are_rejected(change):
    value = model()
    second = copy.deepcopy(value["profiles"][0])
    second["id"] = "conflict"
    second["icd"].update(change)
    value["profiles"].append(second)
    issues = validate_elemental_model(value)
    assert any(issue["code"] == "icd_rule_group_conflict" for issue in issues)


def test_finite_sequence_and_infinite_standard_cannot_share_one_rule_group():
    value = model()
    second = copy.deepcopy(value["profiles"][0])
    second["id"] = "standard"
    second["icd"] = {"kind": "standard", "group": "explicit-shared-group"}
    value["profiles"].append(second)
    issues = validate_elemental_model(value)
    assert any(issue["code"] == "icd_rule_group_conflict" for issue in issues)


@pytest.mark.parametrize(("interval", "allowed"), [
    (2, [True, False, True, False, True, False, True]),
    (3, [True, False, False, True, False, False, True]),
])
def test_timer_counter_uses_timer_or_hit_counter_without_moving_anchor(interval, allowed):
    value = model()
    value["profiles"][0]["icd"] = {
        "kind": "timer_counter", "group": "explicit-shared-group",
        "reset_seconds": 2.5, "hit_interval": interval}
    rt = ElementalRuntime(value)

    rows = [attempt(rt, index * 100_000) for index in range(len(allowed))]

    assert [row["allowed"] for row in rows] == allowed
    assert all(row["after"]["icd"][0]["anchor_t"] == 0 for row in rows)
    state = rows[-1]["after"]["icd"][0]
    assert state["kind"] == "timer_counter"
    assert state["hit_interval"] == interval
    assert state["next_attempt_number"] == len(allowed) + 1
    assert state["next_counter_position"] == len(allowed) % interval + 1
    reset = attempt(rt, 2_500_000)
    assert reset["allowed"] and reset["reason"] == "icd_timer_reset"
    assert reset["after"]["icd"][0]["anchor_t"] == 2.5


def test_timer_counter_only_consumes_landed_attempted_contacts_and_shares_exact_identity():
    value = model()
    value["profiles"][0]["icd"] = {
        "kind": "timer_counter", "counter_tag": "shared-counter",
        "rule_group": "reviewed-x2", "reset_seconds": 2.5, "hit_interval": 2}
    value["profiles"].append({**copy.deepcopy(value["profiles"][0]), "id": "other-profile"})
    assert validate_elemental_model(value) == []
    rt = ElementalRuntime(value)

    assert attempt(rt, 0, landed=False)["reason"] == "missed"
    assert attempt(rt, 1, attempted=False)["reason"] == "application_schedule_blocked"
    first = attempt(rt, 2, profile="other-profile", action="other")
    second = attempt(rt, 3, action="third")
    third = attempt(rt, 4, profile="other-profile", action="fourth")

    assert [first["allowed"], second["allowed"], third["allowed"]] == [True, False, True]
    assert first["icd_audit"]["attempt_number"] == 1
    assert third["icd_audit"]["attempt_number"] == 3
    assert len(rt.icd) == 1
    assert attempt(rt, 5, actor="b")["allowed"]
    assert len(rt.icd) == 2


def test_same_counter_and_normalized_timer_signature_share_across_rule_groups():
    value = model()
    value["profiles"][0]["icd"] = {
        "kind": "standard", "counter_tag": "shared-counter", "rule_group": "database-standard"}
    explicit = copy.deepcopy(value["profiles"][0])
    explicit.update(id="adapted", source="reviewed local adapter")
    explicit["icd"] = {
        "kind": "timer_counter", "counter_tag": "shared-counter",
        "rule_group": "adapter-derived-x3", "reset_seconds": 2.5, "hit_interval": 3}
    value["profiles"].append(explicit)
    assert validate_elemental_model(value) == []
    rt = ElementalRuntime(value)

    rows = [attempt(rt, index, profile=profile) for index, profile in enumerate(
        ("attack", "adapted", "attack", "adapted"))]

    assert [row["allowed"] for row in rows] == [True, False, False, True]
    assert [row["icd_audit"]["rule_group"] for row in rows] == [
        "database-standard", "adapter-derived-x3", "database-standard", "adapter-derived-x3"]
    state = rt.state()["icd"][0]
    assert state["rule_group"] == "database-standard"
    assert state["rule_groups"] == ["database-standard", "adapter-derived-x3"]
    assert rows[-1]["icd_audit"]["shared_rule_groups"] == state["rule_groups"]


def test_rule_group_aliases_do_not_change_behavioral_cycle_boundary():
    value = model()
    value["profiles"][0]["icd"] = {
        "kind": "timer_counter", "counter_tag": "shared-counter",
        "rule_group": "reviewed-a", "reset_seconds": 2.5, "hit_interval": 3}
    alias = copy.deepcopy(value["profiles"][0])
    alias.update(id="alias", source="same rule, second provenance alias")
    alias["icd"]["rule_group"] = "reviewed-b"
    value["profiles"].append(alias)
    assert validate_elemental_model(value) == []

    left = ElementalRuntime(value)
    right = ElementalRuntime(value)
    attempt(left, 0, profile="attack")
    attempt(right, 0, profile="alias")

    assert left.state()["icd"][0]["rule_group"] != right.state()["icd"][0]["rule_group"]
    assert left.boundary(1_000_000, 1) == right.boundary(1_000_000, 1)


def test_same_counter_rejects_distinct_normalized_timer_signatures_even_across_groups():
    value = model()
    value["profiles"][0]["icd"] = {
        "kind": "timer_counter", "counter_tag": "shared-counter",
        "rule_group": "reviewed-x2", "reset_seconds": 2.5, "hit_interval": 2}
    conflicting = copy.deepcopy(value["profiles"][0])
    conflicting.update(id="conflicting", source="different reviewed rule")
    conflicting["icd"].update(rule_group="reviewed-x3", hit_interval=3)
    value["profiles"].append(conflicting)
    assert validate_elemental_model(value) == []
    rt = ElementalRuntime(value)
    assert attempt(rt, profile="attack")["allowed"]
    with pytest.raises(ValueError, match="执行规则"):
        attempt(rt, 1, profile="conflicting")


def test_unknown_icd_still_stops_execution_instead_of_selecting_a_sequence():
    value = model()
    value["profiles"][0]["icd"] = {"kind": "unknown"}
    assert validate_elemental_model(value) == []
    rt = ElementalRuntime(value)
    with pytest.raises(ElementalPending) as exc:
        attempt(rt)
    assert exc.value.issue["code"] == "unknown_icd"
    assert rt.trace[-1]["status"] == "pending"
    assert rt.state()["icd"] == []


def test_capability_advertises_finite_sequences_and_infinite_standard_without_mutable_leaks():
    caps = elemental_capabilities()
    assert caps["icd_contract"]["standard"] == {
        "kind": "timer_counter", "reset_seconds": 2.5,
        "hit_interval": 3, "first_hit": 1}
    assert caps["icd_contract"]["sequence"]["max_length"] == 64
    caps["icd_contract"]["standard"]["hit_interval"] = 1
    caps["schema"]["properties"]["profiles"]["items"]["properties"]["icd"].clear()
    assert elemental_capabilities()["icd_contract"]["standard"]["hit_interval"] == 3
    assert "kind" in elemental_capabilities()["schema"]["properties"]["profiles"]["items"]["properties"]["icd"]["properties"]
