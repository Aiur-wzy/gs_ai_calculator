"""EC19 author cases: explicit misses, never an empty-draft damage shortcut."""
import copy
import json

import pytest

from genshin_engine import damage_reporting as dr
from genshin_engine.core import calculate
from genshin_engine.mechanics import execute_mechanics
from genshin_engine.template import TemplateError
from tests.test_automatic_energy_delivery import automatic_fixture
from tests.test_elemental_execution import model
from tests.test_mechanics_execution import (
    EVIDENCE,
    action,
    add,
    condition,
    fixture,
    rule,
)


def missed_fixture(times=(.2, .3), *, repeat=False):
    t, r, m = fixture(times)
    if not repeat:
        r["actions"][0]["repeat"] = {"mode": "once"}
    attach_misses(m)
    return t, r, m


def attach_misses(m):
    m["elemental_model"] = model()
    for descriptor in m["hit_events"]:
        descriptor["application"] = {"profile_id": "cryo", "target_id": "enemy", "landed": False}


def proof(out):
    assert out["status"] in ("passed", "unproven"), out["issues"]
    assert out["resolved_template"]["hits"] == []
    assert out["result"]["hits"] == []
    assert out["result"]["summary"] == {"total_damage": 0, "dps": 0, "by_skill": {}, "by_character": {}}
    return out["result"]["damage_reporting"]


def no_proof(out):
    assert "damage_reporting" not in (out.get("result") or {})
    assert out.get("resolved_template") is None


def test_complete_zero_has_standard_metadata_exact_coverage_and_unchanged_inputs():
    inputs = missed_fixture()
    before = copy.deepcopy(inputs)
    out = execute_mechanics(*inputs)
    coverage = proof(out)
    assert inputs == before
    assert out["status"] == "unproven"
    assert coverage == {"contract": "verified-empty-damage-v1", "status": "verified_zero",
        "reason": "all_declared_contacts_missed", "scope": {"origin": 0, "start_tick": 0,
        "end_tick": 10000000, "tick_unit": "microsecond"}, "planned_contacts": 2, "missed_contacts": 2,
        "reported_damage_events": 0, "contacts": [
            {"event_id": "h0", "run_id": "0:h0", "action_id": "cast", "tick": 200000, "order": 10,
             "report": True, "landed": False, "outcome": "missed"},
            {"event_id": "h1", "run_id": "0:h1", "action_id": "cast", "tick": 300000, "order": 11,
             "report": True, "landed": False, "outcome": "missed"}]}
    assert out["result"]["template_version"] == 2
    assert out["result"]["id_registry_version"] == 1
    assert out["result"]["patches_applied"] == []
    assert out["result"]["assumptions"]
    with pytest.raises(TemplateError):
        calculate(out["resolved_template"])


def test_actual_expanded_patterns_not_prototype_are_the_denominator():
    t, r, m = missed_fixture((.2,))
    application = m["hit_events"][0]["application"]
    m["hit_events"] = []
    m["hit_patterns"] = [{"id": "ticks", "prototype_hit_id": "h0", "action_id": "cast",
        "offsets": [.2, .3, .3], "orders": [10, 11, 12], "application": application}]
    out = execute_mechanics(t, r, m)
    coverage = proof(out)
    assert [row["event_id"] for row in coverage["contacts"]] == ["ticks:1", "ticks:2", "ticks:3"]
    assert coverage["planned_contacts"] == 3
    assert dr.assemble_damage_candidates(t, r, m) == [
        {k: v for k, v in row.items() if k != "outcome"} for row in coverage["contacts"]]


def test_explicit_three_actions_in_one_window_are_all_included():
    t, r, m = missed_fixture((.2, 10.2, 20.2))
    t["loop_duration"] = r["period"] = 30
    r["actions"] += [action("second", start=10, end=10.1, repeat={"mode": "once"}),
                     action("third", start=20, end=20.1, repeat={"mode": "once"})]
    for d, aid in zip(m["hit_events"], ("cast", "second", "third"), strict=True):
        d["action_id"] = aid
    coverage = proof(execute_mechanics(t, r, m))
    assert coverage["scope"]["end_tick"] == 30000000
    assert [row["tick"] for row in coverage["contacts"]] == [200000, 10200000, 20200000]


def test_later_check_windows_do_not_inflate_origin_zero_coverage():
    inputs = missed_fixture(repeat=True)
    out = execute_mechanics(*inputs)
    assert proof(out)["planned_contacts"] == 2
    actual = [row for row in out["rotation_check"]["event_log"] if row["type"] == "mechanics_hit"]
    assert len(actual) > 2
    assert out["rotation_check"]["energy_check"]["completed_cycles"] > 1


def test_right_boundary_is_not_inside_reported_input_window():
    t, r, m = missed_fixture((.2, 10))
    coverage = proof(execute_mechanics(t, r, m))
    assert coverage["planned_contacts"] == 1
    assert coverage["contacts"][0]["event_id"] == "h0"


def test_skill_activation_preserved_without_quota_consumption_or_hit_listener():
    t, r, m = missed_fixture()
    add(m, rule(quota={"capacity": 7, "scope": "per_beneficiary", "refresh": "reset",
                       "consume": {"event": "hit", "phase": "pre"}}),
        rule("on_hit", trigger={"event": "hit", "phase": "post"}))
    out = execute_mechanics(t, r, m)
    proof(out)
    activations = [row for row in out["mechanics_trace"] if row["type"] == "activation"]
    assert len(activations) == 1 and activations[0]["rule_id"] == "boost"
    assert not [row for row in out["mechanics_trace"] if row["type"] == "quota"]
    assert out["sampling_trace"] == []


def test_automatic_spend_keeps_real_balance_and_admission_on_all_miss():
    t, r, m = automatic_fixture()
    attach_misses(m)
    out = execute_mechanics(t, r, m)
    proof(out)
    check = out["rotation_check"]
    assert check["characters"][0]["final_energy"] == 0
    audits = [row for row in check["event_log"] if row.get("delivery") == "automatic_action_start"]
    assert len(audits) == 1 and audits[0]["before"] == 10 and audits[0]["after"] == 0


@pytest.mark.parametrize("late", [False, True])
def test_failed_admission_before_or_after_first_window_never_upgrades_to_zero(late):
    t, r, m = automatic_fixture(initial=10 if late else 0)
    attach_misses(m)
    if late:
        r["actions"][0]["repeat"] = {"mode": "each_cycle"}
    out = execute_mechanics(t, r, m)
    assert out["status"] == "failed"
    no_proof(out)


def test_unknown_condition_after_misses_cannot_be_promoted():
    t, r, m = missed_fixture()
    condition(m, "unknown", .4)
    m["condition_events"][0]["value"] = None
    out = execute_mechanics(t, r, m)
    assert out["status"] == "pending"
    no_proof(out)


@pytest.mark.parametrize("field,value", [("report", False), ("report", None)])
def test_unreported_or_invalid_report_candidate_cannot_be_hidden(field, value):
    t, r, m = missed_fixture()
    m["hit_events"][0][field] = value
    no_proof(execute_mechanics(t, r, m))


@pytest.mark.parametrize("mutation", ["profile_pending", "target_pending", "unknown_icd", "independent_application"])
def test_unknown_source_or_independent_application_cannot_be_hidden_by_miss(mutation):
    t, r, m = missed_fixture()
    if mutation == "profile_pending":
        m["elemental_model"]["profiles"][2]["status"] = "pending"
    elif mutation == "target_pending":
        m["elemental_model"]["target"]["knowledge"] = "unknown"
    elif mutation == "unknown_icd":
        m["elemental_model"]["profiles"][2]["icd"] = {"kind": "unknown"}
    else:
        m["application_events"] = [{"id": "app", "action_id": "cast", "t": .4, "order": 20,
            "application": {"profile_id": "cryo", "target_id": "enemy", "landed": False}, **EVIDENCE}]
    no_proof(execute_mechanics(t, r, m))


def test_landed_numeric_zero_remains_an_actual_hit_and_old_nonempty_branch():
    t, r, m = missed_fixture((.2,))
    m["hit_events"][0]["application"]["landed"] = True
    t["characters"][0]["stats"]["atk"] = 0
    out = execute_mechanics(t, r, m)
    assert len(out["result"]["hits"]) == 1
    assert out["result"]["summary"]["total_damage"] == 0
    assert "damage_reporting" not in out["result"]


def test_pure_assembly_and_classification_do_not_execute_a_second_queue(monkeypatch):
    t, r, m = missed_fixture()
    out = execute_mechanics(t, r, m)
    def forbidden(*args, **kwargs):
        raise AssertionError("offline projection must not execute")
    monkeypatch.setattr("genshin_engine.mechanics._simulate", forbidden)
    before = copy.deepcopy((t, r, m, out))
    candidates = dr.assemble_damage_candidates(t, r, m)
    assert dr.classify_verified_empty_damage(candidates, out["rotation_check"], period_ticks=10000000,
        mechanics_model={"application_events": []}) == proof(out)
    assert (t, r, m, out) == before


@pytest.mark.parametrize("mutation", ["deleted_candidate", "duplicate_log", "log_order_bool", "log_tick_bool",
    "log_origin_bool", "log_cycle_bool", "log_not_verified", "candidate_tick_bool", "candidate_report_int",
    "candidate_landed_int", "candidate_run_duplicate", "missing_cycle", "completed_bool", "energy_pending"])
def test_classifier_rejects_missing_duplicate_or_ill_typed_execution_evidence(mutation):
    t, r, m = missed_fixture()
    out = execute_mechanics(t, r, m)
    candidates = dr.assemble_damage_candidates(t, r, m)
    check = out["rotation_check"]
    actual = next(row for row in check["event_log"] if row["type"] == "mechanics_hit")
    if mutation == "deleted_candidate":
        candidates.pop()
    elif mutation == "duplicate_log":
        check["event_log"].append(copy.deepcopy(actual))
    elif mutation.startswith("log_"):
        key = {"log_order_bool": "order", "log_tick_bool": "t", "log_origin_bool": "source_cycle",
               "log_cycle_bool": "cycle", "log_not_verified": "status"}[mutation]
        actual[key] = "not_verified" if key == "status" else False
    elif mutation.startswith("candidate_"):
        key, value = {"candidate_tick_bool": ("tick", False), "candidate_report_int": ("report", 1),
                      "candidate_landed_int": ("landed", 0), "candidate_run_duplicate": ("run_id", "0:h1")}[mutation]
        candidates[0][key] = value
    elif mutation == "missing_cycle":
        check["cycles"].pop()
    else:
        check["energy_check"]["completed_cycles" if mutation == "completed_bool" else "status"] = (
            True if mutation == "completed_bool" else "pending")
    assert dr.classify_verified_empty_damage(candidates, check, period_ticks=10000000, mechanics_model=m) is None


def test_exact_byte_budget_passes_and_one_byte_short_is_pending(monkeypatch):
    inputs = missed_fixture()
    expected = proof(execute_mechanics(*inputs))
    size = len(json.dumps(expected, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf8"))
    monkeypatch.setattr(dr, "MAX_DAMAGE_REPORTING_BYTES", size)
    assert proof(execute_mechanics(*inputs)) == expected
    monkeypatch.setattr(dr, "MAX_DAMAGE_REPORTING_BYTES", size - 1)
    out = execute_mechanics(*inputs)
    assert out["status"] == "pending"
    assert out["result"] is out["resolved_template"] is None
    assert out["issues"][-1]["code"] == "damage_reporting_limit"


def test_count_budget_does_not_publish_a_truncated_zero_proof(monkeypatch):
    monkeypatch.setattr(dr, "MAX_DAMAGE_REPORTING_CONTACTS", 1)
    out = execute_mechanics(*missed_fixture())
    assert out["status"] == "pending"
    assert out["result"] is out["resolved_template"] is None
    assert out["issues"][-1]["contacts"] == 2


def test_old_nonempty_damage_does_not_acquire_new_coverage_budget(monkeypatch):
    inputs = fixture((.2,))
    expected = execute_mechanics(*inputs)
    assert expected["result"]["summary"]["total_damage"] == 45
    monkeypatch.setattr(dr, "MAX_DAMAGE_REPORTING_CONTACTS", 0)
    monkeypatch.setattr(dr, "MAX_DAMAGE_REPORTING_BYTES", 0)
    assert execute_mechanics(*inputs) == expected
