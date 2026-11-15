"""Read-side feedback must not upgrade wrappers or imprecise times to facts."""
from __future__ import annotations

import copy

import pytest

from genshin_app.calculation_scope import validation_scope
from genshin_app.damage_count_observation import observe_damage_counts
from genshin_app.default_assistance import read_default_assistance
from genshin_app.failure_analysis import calculation_feedback
from genshin_engine.damage_report_window import filter_report_template, tick


@pytest.mark.parametrize("section,legacy,model", [
    ("elemental", "elemental_trace", "elemental_model"),
    ("special_reactions", "special_reaction_trace", "special_reaction_model"),
])
@pytest.mark.parametrize("shape,state,count", [
    ({}, "declared_without_trace", None),
    ({"state": {"known": True}}, "declared_without_trace", None),
    ({"trace": []}, "trace_empty", 0),
    ({"trace": [{"type": "application_attempt"}]}, "trace_saved", 1),
])
def test_shared_feedback_distinguishes_wrapper_empty_and_saved(section, legacy, model, shape, state, count):
    plan = {"mechanics_model": {model: {"ruleset": "test"}}}
    result = {section: shape, "hits": []}
    before = copy.deepcopy((plan, result))
    scope = validation_scope(plan, result)
    assert scope[section]["state"] == state
    assert scope[section]["trace_event_count"] == count
    assert scope[section]["execution_observed"] is (state == "trace_saved")
    feedback = calculation_feedback({"plan": plan, "result": result})
    assert scope[section]["state"] == feedback[section]
    assert scope["game_mechanism_completeness"] == "not_independently_certified"
    assert before == (plan, result)


@pytest.mark.parametrize("section,legacy", [("elemental", "elemental_trace"),
                                           ("special_reactions", "special_reaction_trace")])
def test_legacy_trace_is_visible_even_without_model_declaration(section, legacy):
    result = {legacy: [{"type": "saved_attempt"}], section: {"state": {}}}
    scope = read_default_assistance({}, result=result)["validation_scope"]
    assert scope[section]["state"] == "trace_saved"
    assert scope[section]["declared"] is False
    assert scope[section]["execution_observed"] is True
    assert scope["acceptance_effect"] == "none"


def test_elemental_world_state_only_does_not_claim_observed_execution():
    assert validation_scope({}, {"elemental_world": {"targets": ["enemy"]}})["elemental"] == {
        "state": "not_declared", "trace_event_count": None, "declared": False, "execution_observed": False}
    scope = validation_scope({}, {"elemental_world": {"trace": []}})
    assert scope["elemental"]["state"] == "trace_empty"
    assert not scope["elemental"]["execution_observed"]


def test_window_observation_matches_real_inclusive_microsecond_filter():
    plan = {"manual_template": {"loop_duration": 10, "buffs": [], "hits": [
        {"id": "before", "t": -0.000001}, {"id": "start", "t": 0},
        {"id": "end", "t": 10}, {"id": "outside", "t": 10.000001},
    ]}}
    report = {"end": 10}
    projected = filter_report_template(plan["manual_template"], report)
    counts = observe_damage_counts(plan, {"damage_report_window": report})["report"]
    kept = {row["id"] for row in projected["hits"]}
    assert kept == {"start", "end"}
    assert {row["id"] for row in counts["outside_samples"]} == {"before", "outside"}
    assert counts["declared_manual_outside_window"] == 2
    assert counts["window_classification"] == "classified"


def test_non_microsecond_input_is_unclassified_not_falsely_outside():
    value = 10.00000000001
    with pytest.raises(ValueError):
        tick(value)  # The engine does not silently round this value.
    plan = {"manual_template": {"loop_duration": 10, "hits": [{"id": "uncertain", "t": value}]}}
    counts = read_default_assistance(plan, result={"hits": []})["damage_counts"]["report"]
    assert counts["window_classification"] == "partial"
    assert counts["declared_manual_outside_window"] == 0
    assert counts["unclassified_manual_count"] == 1
    assert counts["unclassified_samples"][0]["reason"] == "invalid_time"


@pytest.mark.parametrize("end", [None, 10.00000000001, float("nan"), float("inf"), 0])
def test_unknown_or_invalid_window_never_reports_all_input_in_bounds(end):
    plan = {"manual_template": {"loop_duration": 30, "hits": [{"id": "h", "t": 20}]}}
    observed = observe_damage_counts(plan, {"damage_report_window": {"end": end}})["report"]
    assert observed["window_classification"] in ("unknown", "invalid_time")
    assert observed["declared_manual_outside_window"] is None
    assert observed["unclassified_manual_count"] == 1
