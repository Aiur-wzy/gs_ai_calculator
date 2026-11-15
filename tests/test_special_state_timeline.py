from __future__ import annotations

import copy

import pytest

from genshin_engine.mechanics_reporting import attach_buff_state_timeline
from genshin_engine.special_state_observation import canonical_stellar_deadline_row
from genshin_engine.special_state_timeline import build_special_state_timeline


def event(t, operation, *, area="arena", **fields):
    return {"type": "special_reaction_state", "reaction_type": "stellar_superconduct",
            "status": "executed", "event_id": f"{area}:{operation}:{t}",
            "t": t, "time_tick": round(t * 1_000_000), "area_id": area,
            "operation": operation, **fields}


def establish(t=1, *, area="arena", refresh=False, layers=0, expires=10):
    return event(t, "establish_or_refresh_field", area=area, current_layers=layers,
                 expires_at=expires, next_settlement_at=5,
                 trigger_application_layer_eligibility=(
                     "later_application" if refresh else "establishing_application_excluded"))


def settle(t=5, *, area="arena", layers=2, expires=9):
    return event(t, "settle_four_second_window", area=area, layers=layers,
                 system_factor=1.5, ordinary_cryo_electro_bonus=.2, buff_expires_at=expires)


def project(rows, duration=12, limit=None):
    return build_special_state_timeline(rows, duration=duration,
                                       execution_limit=duration if limit is None else limit,
                                       execution_status="passed")


def intervals(report, kind):
    return [s for s in report["states"] if s["kind"] == kind]


def test_accumulation_clear_and_settled_bonus_are_independent_and_pure():
    rows = [establish(), event(2, "count_successful_application", counted=True, layers=1),
            event(3, "count_successful_application", counted=True, layers=2), settle(),
            event(6, "count_successful_application", counted=True, layers=1),
            event(9, "expire_settlement_buff"), event(10, "expire_field")]
    before = copy.deepcopy(rows)
    result = project(rows)
    assert result["status"] == "complete"
    assert [(s["start"], s["end"], s["layers"]) for s in intervals(result, "field_accumulator")] == [
        (1, 2, 0), (2, 3, 1), (3, 5, 2), (5, 6, 0), (6, 10, 1)]
    bonus, = intervals(result, "settled_damage_bonus")
    assert (bonus["start"], bonus["end"], bonus["layers"], bonus["system_factor"]) == (5, 9, 2, 1.5)
    assert before == rows


def test_refresh_preserves_establishment_and_supplied_clock():
    rows = [establish(), establish(4, refresh=True, layers=0, expires=12),
            settle(), event(9, "expire_settlement_buff"), event(12, "expire_field")]
    result = project(rows)
    states = intervals(result, "field_accumulator")
    assert len({s["instance_id"] for s in states}) == 1
    assert states[1]["next_settlement_at"] == 5  # not refresh+4
    assert intervals(result, "settled_damage_bonus")[0]["start"] == 5


def test_field_expiry_keeps_bonus_and_reestablishment_does_not_reattribute_it():
    rows = [establish(expires=6), settle(expires=9), event(6, "expire_field"),
            establish(7, expires=12), event(9, "expire_settlement_buff"), event(12, "expire_field")]
    result = project(rows)
    a, b = intervals(result, "field_accumulator")[::2]
    bonus, = intervals(result, "settled_damage_bonus")
    assert a["instance_id"] != b["instance_id"]
    assert bonus["instance_id"] == a["instance_id"]
    assert bonus["end"] == 9
    assert bonus["end"] > intervals(result, "field_accumulator")[1]["end"]
    expiry = next(c for c in result["changes"] if c["kind"] == "settled_damage_bonus" and c["status"] == "expired")
    assert expiry["source_instance_id"] == a["instance_id"]
    assert expiry["instance_id"] == a["instance_id"]


def test_same_tick_business_order_remains_observable_without_fake_interval():
    rows = [establish(), settle(), event(5, "count_successful_application", counted=True, layers=1),
            event(9, "expire_settlement_buff"), event(10, "expire_field")]
    result = project(rows)
    changes = [c for c in result["changes"] if c["t"] == 5]
    assert [(c["kind"], c["layers"]) for c in changes] == [
        ("field_accumulator", 0), ("settled_damage_bonus", 2), ("field_accumulator", 1)]
    assert changes[0]["boundary"]["trace_index"] < changes[-1]["boundary"]["trace_index"]
    assert not any(s["start"] == s["end"] for s in result["states"])


def test_failure_prefix_does_not_extend_to_declared_lifetime_or_future_settlement():
    result = project([establish(), settle(), event(6, "count_successful_application", counted=True, layers=1)], limit=4)
    assert [(s["start"], s["end"]) for s in result["states"]] == [(1, 4)]
    assert result["states"][0]["end_reason"] == "execution_limit"


def test_zero_layer_settlement_is_valid_not_unknown():
    result = project([establish(), settle(layers=0), event(9, "expire_settlement_buff"), event(10, "expire_field")])
    bonus, = intervals(result, "settled_damage_bonus")
    assert bonus["layers"] == 0 and bonus["knowledge"] == "known"


def test_unknown_prefix_does_not_fabricate_a_field():
    result = project([event(2, "count_successful_application", counted=True, layers=1), settle()])
    assert result["states"] == []
    assert result["status"] == "partial"


def test_inactive_observation_is_not_known_zero_active_buff():
    result = project([event(2, "count_successful_application", counted=False, layers=0, reason="field_inactive")])
    assert result["states"] == []
    assert result["changes"][0]["status"] == "inactive"
    assert result["changes"][0]["layers"] is None


def test_missing_expiry_receipt_is_partial_and_never_extends_past_known_expiry():
    result = project([establish(expires=3)])
    assert result["status"] == "partial"
    assert result["states"][0]["end"] == 3
    assert result["states"][0]["end_reason"] == "declared_expiry_unobserved"


def test_area_states_and_interleaved_trace_are_isolated():
    rows = [establish(area="a", expires=12), establish(2, area="b", expires=12),
            settle(8, area="a", expires=12), settle(6, area="b", expires=12)]
    result = project(rows, duration=11)
    assert result["status"] == "complete"
    assert {(s["area_id"], s["start"]) for s in intervals(result, "settled_damage_bonus")} == {("a", 8), ("b", 6)}


def test_unknown_operation_is_reported_not_guessed():
    result = project([event(1, "new_future_transition")])
    assert result["status"] == "partial" and result["issues"][0]["code"] == "state_operation_unsupported"


def test_incomplete_mid_window_observation_ends_certainty_and_can_recover():
    rows = [establish(), event(2, "count_successful_application", counted=True, layers=1),
            event(3, "count_successful_application", counted=True, layers=None),
            event(4, "count_successful_application", counted=True, layers=3),
            event(10, "expire_field")]
    result = project(rows)
    states = intervals(result, "field_accumulator")
    assert [(s["start"], s["end"], s["layers"], s["knowledge"]) for s in states] == [
        (1, 2, 0, "known"), (2, 3, 1, "known"), (3, 4, None, "unknown"), (4, 10, 3, "known")]
    assert result["status"] == "partial"
    assert len({s["instance_id"] for s in states}) == 1


def test_unknown_establishment_is_not_zero_and_later_layer_receipt_restores_value():
    result = project([establish(layers=None),
                      event(3, "count_successful_application", counted=True, layers=2),
                      event(10, "expire_field")])
    a, b = intervals(result, "field_accumulator")
    assert (a["layers"], a["knowledge"], a["end"]) == (None, "unknown", 3)
    assert (b["layers"], b["knowledge"], b["start"]) == (2, "known", 3)
    assert result["status"] == "partial"


def test_unknown_transition_does_not_extend_old_known_layer_or_bonus():
    result = project([establish(expires=12), settle(expires=12),
                      event(6, "future_state_transition"), event(12, "expire_field")])
    for kind in ("field_accumulator", "settled_damage_bonus"):
        states = intervals(result, kind)
        assert all(s["end"] <= 6 for s in states if s["knowledge"] == "known")
        assert any(s["start"] == 6 and s["knowledge"] == "unknown" for s in states)
    assert result["status"] == "partial"


def test_incomplete_settlement_cannot_carry_forward_old_factor():
    result = project([establish(expires=12), settle(expires=12),
                      event(7, "settle_four_second_window", layers=8, system_factor=None),
                      event(12, "expire_field")])
    known = [s for s in intervals(result, "settled_damage_bonus") if s["knowledge"] == "known"]
    assert [(s["start"], s["end"], s["system_factor"]) for s in known] == [(5, 7, 1.5)]
    unknown, = [s for s in intervals(result, "settled_damage_bonus") if s["knowledge"] == "unknown"]
    assert unknown["layers"] is None and "system_factor" not in unknown
    assert result["status"] == "partial"


def test_clock_is_only_read_from_explicit_settlement_observation():
    legacy = project([establish(), settle(), event(10, "expire_field")], duration=8)
    assert intervals(legacy, "field_accumulator")[-1]["next_settlement_at"] is None
    observed = settle()
    observed["next_settlement_at"] = 9
    current = project([establish(), observed], duration=8)
    assert intervals(current, "field_accumulator")[-1]["next_settlement_at"] == 9


def test_reference_normalization_drops_only_the_named_settlement_observation():
    row = settle()
    row.update(next_settlement_at=9, future_field="do_not_ignore", layers=3)
    before = copy.deepcopy(row)
    normalized = canonical_stellar_deadline_row(row)
    assert row == before
    assert normalized == {key: value for key, value in row.items() if key != "next_settlement_at"}
    wrong_operation = {**row, "operation": "expire_field"}
    wrong_family = {**row, "reaction_type": "stellar_swirl"}
    assert canonical_stellar_deadline_row(wrong_operation) == wrong_operation
    assert canonical_stellar_deadline_row(wrong_family) == wrong_family


def test_reporting_attaches_same_projection_without_changing_damage_or_trace():
    rows = [establish(), settle(), event(9, "expire_settlement_buff"), event(10, "expire_field")]
    output = {"status": "passed", "mechanics_trace": [], "rotation_check": {"event_log": []},
              "special_reactions": {"trace": rows},
              "result": {"summary": {"total_damage": 12345}, "hits": [{"expected": 12345}]}}
    before = copy.deepcopy(output)
    attach_buff_state_timeline(output, {"rules": []}, {"characters": []}, 12)
    assert output["special_state_timeline"] == project(rows)
    assert output["result"]["special_state_timeline"] == output["special_state_timeline"]
    assert output["result"]["special_state_timeline"] is not output["special_state_timeline"]
    assert output["result"]["summary"] == before["result"]["summary"]
    assert output["result"]["hits"] == before["result"]["hits"]
    assert output["special_reactions"] == before["special_reactions"]


@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf")])
def test_invalid_window_rejected(value):
    with pytest.raises(ValueError):
        project([], duration=value)
