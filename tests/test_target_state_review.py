"""EC08 independent state-sampling oracles; no DB and no compiler-generated expectations."""
from __future__ import annotations

import copy

import pytest
from test_elemental_execution import model as old_elemental_model
from test_freeze_review import EVIDENCE, SAFE_CONTACT, fresh_model
from test_mechanics_execution import add, fixture, rule, second_character

from genshin_engine.mechanics import execute_mechanics


def aura(element="cryo", remaining=.8, decay=.01):
    return {"element": element, "remaining_gu": remaining,
            "decay_gu_per_second": decay, "source": "independent controlled initial aura"}


def predicate(elements=("cryo", "frozen")):
    return {"kind": "target_aura_in", "elements": list(elements)}


def fixed_source(owner="a"):
    return {"id": "review-fixed", "name_zh": "独立目标条件两段收益", "owner": owner,
            "source_id": "review-source", "availability": "fixed", "damage_scope": "classic_hits_only",
            "target_state_profile": "freeze-reference-target-v1", **EVIDENCE,
            "effects": [
                {"id": "ice", "benefit_type_id": "benefit.crit_rate.all", "domain": "self", "value": .2,
                 "filter": {"event": "hit", "phase": "pre"}, "target_predicate": predicate()},
                {"id": "frozen", "benefit_type_id": "benefit.crit_rate.all", "domain": "self", "value": .2,
                 "filter": {"event": "hit", "phase": "pre"},
                 "target_predicate": {"kind": "target_status", "status_id": "element_freeze"}},
            ]}


def inputs(elements=("cryo", "physical"), times=None, initial=None, gauges=None):
    times = tuple(0 for _ in elements) if times is None else times
    gauges = tuple(1 for _ in elements) if gauges is None else gauges
    t, r, m = fixture(times)
    t["characters"][0]["stats"]["crit_dmg"] = 1
    r["actions"][0]["repeat"] = {"mode": "once"}
    m["elemental_model"] = fresh_model(initial)
    m["elemental_model"]["profiles"].append(
        {"id": "physical-none", "element": "physical", "application": "none", **EVIDENCE})
    m["fixed_effect_sources"] = [fixed_source()]
    for h, d, element, gu in zip(t["hits"], m["hit_events"], elements, gauges, strict=True):
        h["element"] = element
        d["application"] = {"profile_id": "physical-none" if element == "physical" else f"{element}{gu}",
                            "target_id": "enemy", "landed": True}
        d["contact"] = copy.deepcopy(SAFE_CONTACT)
    return t, r, m


def execute(data, matches=None):
    original = copy.deepcopy(data)
    out = execute_mechanics(*data, matches=matches)
    assert data == original, "execution must not fill source inputs with sampled answers"
    assert out["result"] is not None, out["issues"]
    assert out["status"] in ("passed", "unproven"), out["issues"]
    return out


def direct(out):
    return {h["id"]: h["expected"] for h in out["result"]["hits"] if h["damage_system"] == "classic"}


def traces(out, hit_id=None, cycle=0):
    return [r for r in out["target_condition_trace"]
            if r["cycle"] == cycle and (hit_id is None or r["event_id"] == hit_id)]


def match(hit="h0", effect="ice", operation="include"):
    return {"fixed_source_id": "review-fixed", "effect_id": effect,
            "hit_id": hit, "operation": operation}


def test_review_before_application_and_next_same_tick_see_different_committed_states():
    data = inputs(("cryo", "hydro", "physical", "anemo", "physical"), gauges=(1, 1, 1, 4, 1))
    out = execute(data)
    # 100 ATK * 1 * .5 DEF * .9 RES = 45. CR .2/.4 and CD 1 => 54/63.
    assert direct(out) == pytest.approx({"h0": 45, "h1": 54, "h2": 63, "h3": 63, "h4": 45})
    for hid, values in {"h0": [False, False], "h1": [True, False], "h2": [True, True],
                        "h3": [True, True], "h4": [False, False]}.items():
        rows = traces(out, hid)
        assert [r["automatic_value"] for r in rows] == values
        assert all(r["target_sample"]["phase"] == "before_application" for r in rows)
        assert all(r["evidence_tier"] == "experimental_reference" for r in rows)
    swirls = [h for h in out["result"]["hits"] if h["damage_system"] == "transformative"]
    assert [h["expected"] for h in swirls] == pytest.approx([781.299])
    assert not any("reaction_damage" in row["event_id"] and row["applied"]
                   for row in out["target_condition_trace"])


def test_review_array_position_does_not_override_same_timestamp_event_order():
    data = inputs(("cryo", "physical", "physical"))
    for desc, order in zip(data[2]["hit_events"], (20, 10, 30), strict=True):
        desc["order"] = order
    out = execute(data)
    assert direct(out) == pytest.approx({"h0": 45, "h1": 45, "h2": 54})
    assert traces(out, "h1")[0]["automatic_value"] is False
    assert traces(out, "h2")[0]["automatic_value"] is True


def test_review_target_condition_is_dynamic_even_when_actor_attack_panel_was_snapshotted():
    data = inputs(("cryo", "physical", "anemo", "physical"), (.1, .2, .3, .4), gauges=(1, 1, 4, 1))
    m = data[2]
    for desc in m["hit_events"]:
        desc["self_snapshot_event"] = "cast"
    add(m, rule("late-atk", "benefit.stat_add.atk", 100,
                trigger={"event": "hit", "phase": "post", "event_ids": ["h0"]}))
    out = execute(data)
    assert direct(out) == pytest.approx({"h0": 45, "h1": 54, "h2": 54, "h3": 45})
    # A target observation cannot accidentally thaw the independently frozen ATK=100 panel.
    assert out["resolved_template"]["characters"][0]["stats"]["atk"] == 100


def test_review_positive_frozen_layer_does_not_invent_underlying_cryo_aura():
    data = inputs(("hydro", "cryo", "physical"))
    src = data[2]["fixed_effect_sources"][0]
    src["effects"][0].update(value=.1, target_predicate=predicate(("cryo",)))
    src["effects"][1]["value"] = .3
    extra = copy.deepcopy(src["effects"][0])
    extra.update(id="frozen-aura", value=.2, target_predicate=predicate(("frozen",)))
    src["effects"].append(extra)
    out = execute(data)
    assert direct(out)["h2"] == pytest.approx(67.5)
    values = {r["effect_id"]: r["automatic_value"] for r in traces(out, "h2")}
    assert values == {"ice": False, "frozen": True, "frozen-aura": True}
    sample = traces(out, "h2")[0]["target_sample"]
    assert "cryo" not in sample["auras"] and "frozen" in sample["auras"]
    assert "element_freeze" in sample["statuses"]


def test_review_composite_predicates_observe_hydro_and_frozen_layers_independently():
    data = inputs(("hydro", "cryo", "physical"), gauges=(2, 1, 1))
    src = data[2]["fixed_effect_sources"][0]
    src["effects"] = [src["effects"][0]]
    src["effects"][0]["target_predicate"] = {"kind": "all", "children": [
        predicate(("hydro",)), {"kind": "any", "children": [predicate(("cryo",)), predicate(("frozen",))]}]}
    out = execute(data)
    assert direct(out) == pytest.approx({"h0": 45, "h1": 45, "h2": 54})


def test_review_frozen_expiry_at_exact_hit_time_precedes_arbitrarily_low_user_order():
    data = inputs(("cryo", "physical", "physical"), (0, 1.999999, 2),
                  initial=aura("hydro", .5, .1))
    data[2]["hit_events"][2]["order"] = -(10**30)
    out = execute(data)
    # F(0)=1; F(t)=1-.4t-.05t², hence the exact expiry is t=2.
    assert direct(out) == pytest.approx({"h0": 45, "h1": 63, "h2": 45})
    assert traces(out, "h2")[1]["automatic_value"] is False


def test_review_fixed_source_has_no_arbitrary_duration_or_initial_state_requirement():
    data = inputs(("physical",), (400,), initial=aura(remaining=3.2, decay=1e-9))
    data[0]["loop_duration"] = data[1]["period"] = 500
    out = execute(data)
    assert direct(out) == pytest.approx({"h0": 54})
    assert data[2]["initial_state"] == {"rules": []}
    assert data[2]["rules"] == []


def test_review_fixed_source_persists_across_check_cycles_without_duplicating_layers():
    data = inputs(("physical",), (.2,), initial=aura(remaining=3.2, decay=1e-9))
    data[1]["actions"][0]["repeat"] = {"mode": "each_cycle"}
    out = execute(data)
    assert direct(out) == pytest.approx({"h0": 54})
    rows = out["target_condition_trace"]
    cycles = {row["cycle"] for row in rows}
    assert len(cycles) >= 2
    for cycle in cycles:
        these = traces(out, "h0", cycle)
        assert len(these) == 2 and [row["applied"] for row in these] == [True, False]
        assert all(row["run_id"].startswith(f"{cycle}:") for row in these)


def test_review_miss_does_not_consume_aura_or_emit_conditional_damage():
    data = inputs(("cryo", "anemo", "physical"), gauges=(1, 4, 1))
    data[2]["hit_events"][1]["application"]["landed"] = False
    out = execute(data)
    assert direct(out) == pytest.approx({"h0": 45, "h2": 54})
    assert not any(row["applied"] for row in traces(out, "h1"))


def test_review_icd_blocked_hit_keeps_conditional_bonus_and_is_not_a_miss():
    data = inputs(("cryo", "cryo", "physical"))
    next(p for p in data[2]["elemental_model"]["profiles"] if p["id"] == "cryo1")["icd"] = {
        "kind": "standard", "group": "independent-same-group"}
    out = execute(data)
    assert direct(out) == pytest.approx({"h0": 45, "h1": 54, "h2": 54})
    assert any(row.get("reason") == "icd_blocked" for row in out["elemental_trace"])


def test_review_pattern_expansion_uses_instance_samples_and_does_not_advance_icd_when_observing():
    data = inputs(("cryo",))
    m = data[2]
    desc = m["hit_events"].pop()
    next(p for p in m["elemental_model"]["profiles"] if p["id"] == "cryo1")["icd"] = {
        "kind": "standard", "group": "pattern-shared"}
    m["hit_patterns"] = [{"id": "series", "prototype_hit_id": "h0", "action_id": "cast",
                          "offsets": [0, 0, 0, 0], "orders": [10, 11, 12, 13],
                          "application": desc["application"], "contact": desc["contact"]}]
    out = execute(data)
    assert direct(out) == pytest.approx({"series:1": 45, "series:2": 54, "series:3": 54, "series:4": 54})
    applied = [row for row in out["elemental_trace"] if row["type"] == "elemental_application"]
    assert [row["allowed"] for row in applied[:4]] == [True, False, False, True]
    assert len({row["run_id"] for row in traces(out)}) == 4


def test_review_target_observations_are_owned_immutable_copies_not_live_state_or_shared_trace_aliases():
    out = execute(inputs(("cryo", "physical", "anemo", "physical"), gauges=(1, 1, 4, 1)))
    first = traces(out, "h1")[0]
    before = copy.deepcopy(out["result"]["target_condition_trace"])
    first["target_sample"]["auras"].append("synthetic-change")
    first["target_sample"]["state"]["ordinary_aura"]["remaining_gu"] = 999
    assert out["result"]["target_condition_trace"] == before
    assert "synthetic-change" not in traces(out, "h1")[1]["target_sample"]["auras"]
    assert traces(out, "h3")[0]["target_sample"]["auras"] == []


def test_review_owner_scope_cannot_be_overridden_by_include():
    data = inputs(("physical",), initial=aura())
    second_character(data[0], data[1])
    data[2]["fixed_effect_sources"][0]["owner"] = "b"
    out = execute(data, [match()])
    assert direct(out) == pytest.approx({"h0": 45})
    assert not any(row["applied"] for row in traces(out))


def test_review_known_false_include_and_known_true_exclude_are_separate_from_automatic_truth():
    data = inputs(("physical", "cryo", "physical"))
    out = execute(data, [match(), match("h2", operation="exclude")])
    assert direct(out) == pytest.approx({"h0": 54, "h1": 45, "h2": 45})
    first = next(row for row in traces(out, "h0") if row["effect_id"] == "ice")
    last = next(row for row in traces(out, "h2") if row["effect_id"] == "ice")
    assert (first["automatic_value"], first["override"], first["applied"]) == (False, "include", True)
    assert (last["automatic_value"], last["override"], last["applied"]) == (True, "exclude", False)


@pytest.mark.parametrize("mode", ["no_model", "older_model"])
@pytest.mark.parametrize("operation", ["include", "exclude"])
def test_review_unknown_target_profile_stays_pending_even_with_explicit_match(mode, operation):
    data = inputs(("cryo",))
    if mode == "no_model":
        data[2].pop("elemental_model")
        data[2]["hit_events"][0].pop("application")
        data[2]["hit_events"][0].pop("contact")
    else:
        data[2]["elemental_model"] = old_elemental_model()
        data[2]["hit_events"][0]["application"]["profile_id"] = "cryo"
        data[2]["hit_events"][0].pop("contact")
    out = execute_mechanics(*data, matches=[match(operation=operation)])
    assert out["status"] == "pending", out["issues"]
    assert out["result"] is None and out["resolved_template"] is None


@pytest.mark.parametrize("mode", ["empty_any", "unknown_leaf", "duplicate_element", "depth_5", "duplicate_id",
                                  "rule_id_collision", "same_owner_source", "pending_source", "expression_value"])
def test_review_closed_schema_and_source_identity_do_not_silently_drop_bad_fixed_sources(mode):
    data = inputs(("physical",), initial=aura())
    m = data[2]
    src = m["fixed_effect_sources"][0]
    leaf = src["effects"][0]
    if mode == "empty_any":
        leaf["target_predicate"] = {"kind": "any", "children": []}
    elif mode == "unknown_leaf":
        leaf["target_predicate"] = {"kind": "target_status", "status_id": "guessed_frozen_icon"}
    elif mode == "duplicate_element":
        leaf["target_predicate"] = predicate(("cryo", "cryo"))
    elif mode == "depth_5":
        for _ in range(4):
            leaf["target_predicate"] = {"kind": "all", "children": [leaf["target_predicate"]]}
    elif mode == "duplicate_id":
        m["fixed_effect_sources"].append(copy.deepcopy(src))
    elif mode in ("rule_id_collision", "same_owner_source"):
        old = rule(src["id"] if mode == "rule_id_collision" else "other-rule")
        if mode == "same_owner_source":
            old["source_id"] = src["source_id"]
        add(m, old)
    elif mode == "pending_source":
        src["status"] = "pending"
    else:
        leaf["value"] = {"kind": "stat", "subject": "owner", "stat": "atk", "factor": .1, "sample": "current"}
    out = execute_mechanics(*data)
    assert out["status"] in ("invalid", "pending"), out["issues"]
    assert out["result"] is None
