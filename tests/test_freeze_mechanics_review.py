"""Independent EC06 queue, package and whole-input numerical review."""
from __future__ import annotations

import copy
import json

import pytest
from test_freeze_review import (
    EVIDENCE,
    ORACLE,
    SAFE_CONTACT,
    fresh_model,
    state_matches,
)
from test_mechanics_execution import add, fixture, rule

from genshin_engine.mechanics import execute_mechanics


def setup(elements=("hydro", "cryo", "anemo"), gauges=(2, 1, 2), times=None, aura=None):
    t, r, m = fixture(times or tuple(0 for _ in elements))
    r["actions"][0]["repeat"] = {"mode": "once"}
    m["elemental_model"] = fresh_model(aura)
    for h, d, element, gauge in zip(t["hits"], m["hit_events"], elements, gauges, strict=True):
        h["element"] = element
        d["application"] = {"profile_id": f"{element}{gauge}", "target_id": "enemy", "landed": True}
        d["contact"] = copy.deepcopy(SAFE_CONTACT)
    return t, r, m


def successful(out):
    assert out["result"] is not None, out["issues"]
    assert out["status"] in ("passed", "unproven"), out["issues"]
    return out


def swirls(out):
    return [h for h in successful(out)["result"]["hits"] if h["damage_system"] == "transformative"]


def rejected(out, reason):
    assert out["status"] == "pending", out["issues"]
    assert out["result"] is None and out["resolved_template"] is None
    assert reason in [row["code"] for row in out["issues"]]


def test_review_full_continuous_oracle_has_only_requested_damage_and_fresh_window_end():
    oracle = json.loads(ORACLE.read_text("utf8"))
    t, r, m = setup([x["element"] for x in oracle["rows"]], [x["gu"] for x in oracle["rows"]], [x["t"] for x in oracle["rows"]])
    t["loop_duration"] = r["period"] = 14
    for h, d, expected in zip(t["hits"], m["hit_events"], oracle["rows"], strict=True):
        h["id"] = d["hit_id"] = expected["id"]
    original = copy.deepcopy((t, r, m))
    out = successful(execute_mechanics(t, r, m))
    assert out["result"]["summary"]["total_damage"] == pytest.approx(7150.392)
    assert len(out["result"]["hits"]) == 28
    assert len(swirls(out)) == 8
    applied = [row for row in out["elemental_trace"] if row["type"] == "elemental_application"]
    assert len(applied) == 20
    for actual, expected in zip(applied, oracle["rows"], strict=True):
        state_matches(actual["after"], expected)
    elem = out["result"]["elemental"]
    state_matches(elem["input_window_state"], oracle["window_end"])
    assert elem["evidence_tier"] == "experimental_reference"
    assert (t, r, m) == original


def test_review_deadline_wins_over_extreme_negative_user_order_and_does_not_react_twice():
    t, r, m = setup(("cryo", "anemo"), (1, 1), (0, 2),
                    aura={"element": "hydro", "remaining_gu": .5, "decay_gu_per_second": .1, "source": "fixture"})
    m["hit_events"][1]["order"] = -(10**30)
    out = successful(execute_mechanics(t, r, m))
    assert swirls(out) == []
    trace = out["mechanics_trace"]
    expiry = [(i, row) for i, row in enumerate(trace) if row["type"] == "elemental_state_transition" and row["reason"] == "frozen_expired"]
    wind = next(i for i, row in enumerate(trace) if row["type"] == "elemental_application" and row["event_id"] == "0:h1")
    assert len(expiry) == 1 and expiry[0][1]["t"] == 2
    assert expiry[0][0] < wind
    assert len([row for row in trace if row["type"] == "elemental_reaction"]) == 1


def test_review_stale_expiry_cannot_remove_refreshed_freeze_in_real_queue():
    t, r, m = setup(("cryo", "hydro", "cryo", "anemo"), (1, 1, 1, 1), (0, 1, 1, 2),
                    aura={"element": "hydro", "remaining_gu": .5, "decay_gu_per_second": .1, "source": "fixture"})
    out = successful(execute_mechanics(t, r, m))
    wind = next(row for row in out["elemental_trace"] if row["type"] == "elemental_application" and row["event_id"] == "0:h3")
    assert wind["before"]["frozen"]["remaining_gu"] == pytest.approx(1.05)
    assert wind["after"]["frozen"]["remaining_gu"] == pytest.approx(.55)
    assert len(swirls(out)) == 1
    assert not any(row["type"] == "elemental_state_transition" and row["reason"] == "frozen_expired" and row["t"] == 2 for row in out["elemental_trace"])


@pytest.mark.parametrize("mode", ["physical", "no_application", "icd_blocked", "unreported", "pattern", "application_only"])
def test_review_all_contact_carriers_reject_unsafe_freeze_contact(mode):
    t, r, m = setup(("hydro", "cryo", "cryo"), (2, 1, 1))
    last = m["hit_events"][-1]
    last["contact"] = {"kind": "profile", "profile_id": "unsafe"}
    if mode in ("physical", "no_application"):
        element = "physical" if mode == "physical" else "cryo"
        t["hits"][-1]["element"] = element
        m["elemental_model"]["profiles"].append({"id": "no-apply", "element": element, "application": "none", **EVIDENCE})
        last["application"]["profile_id"] = "no-apply"
    elif mode == "icd_blocked":
        next(p for p in m["elemental_model"]["profiles"] if p["id"] == "cryo1")["icd"] = {"kind": "standard", "group": "ice"}
    elif mode == "unreported":
        last["report"] = False
    elif mode == "pattern":
        m["hit_events"].pop()
        m["hit_patterns"] = [{"id": "unsafe-series", "prototype_hit_id": "h2", "action_id": "cast", "offsets": [0], "orders": [12],
                              "application": last["application"], "contact": last["contact"]}]
    elif mode == "application_only":
        m["hit_events"].pop()
        t["hits"].pop()
        m["application_events"] = [{"id": "unsafe-application", "action_id": "cast", "t": 0, "order": 12,
                                    "application": last["application"], "contact": last["contact"]}]
    out = execute_mechanics(t, r, m)
    rejected(out, "freeze_contact_unknown")


def test_review_same_element_two_layer_packet_cannot_double_refund():
    t, r, m = setup(("hydro", "cryo", "cryo", "anemo"), (2, 2, 1, 2))
    refund = rule("refund", trigger={"event": "elemental_reaction", "phase": "post", "reaction_types": ["swirl"]})
    refund["effects"] = []
    refund["resources"] = [{"id": "refund", "kind": "energy_direct", "delay": 0, "amounts": {"a": 1}, "source_part": "refund"}]
    m["resource_ownership"] = [{"rule_id": "refund", "action_id": "cast", "source_part": "refund", "replaces_event_ids": []}]
    add(m, refund)
    rejected(execute_mechanics(t, r, m), "freeze_package_effect_ambiguous")


@pytest.mark.parametrize("cross", [False, True])
def test_review_own_element_shred_commutes_but_cross_element_shred_must_be_pending(cross):
    t, r, m = setup()
    for element, other in (("hydro", "cryo"), ("cryo", "hydro")):
        add(m, rule("shred-"+element, "benefit.res_shred."+(other if cross else element), .4, domain="enemy",
                    trigger={"event": "elemental_reaction", "phase": "post", "reaction_types": ["swirl"], "reaction_elements": [element]}))
    out = execute_mechanics(t, r, m)
    if cross:
        rejected(out, "freeze_package_effect_ambiguous")
    else:
        assert [h["expected"] for h in swirls(out)] == pytest.approx([998.3265, 998.3265])


def test_review_parent_post_new_provider_bonus_is_not_seen_by_either_packet_member():
    t, r, m = setup()
    add(m, rule("new-bonus", "benefit.reaction_bonus.swirl", .6, domain="provider",
                trigger={"event": "hit", "phase": "post", "event_ids": ["h2"]}))
    out = execute_mechanics(t, r, m)
    assert [h["expected"] for h in swirls(out)] == pytest.approx([781.299, 781.299])
    add(m, rule("old-bonus", "benefit.reaction_bonus.swirl", .6, domain="provider"))
    assert [h["expected"] for h in swirls(execute_mechanics(t, r, m))] == pytest.approx([1250.0784, 1250.0784])


def test_review_first_damage_post_cannot_write_into_second_damage_enemy_state():
    t, r, m = setup()
    add(m, rule("damage-post", "benefit.res_shred.cryo", .4, domain="enemy",
                trigger={"event": "reaction_damage", "phase": "post", "reaction_elements": ["hydro"]}))
    rejected(execute_mechanics(t, r, m), "freeze_package_effect_ambiguous")


def test_review_explicit_false_condition_is_not_a_potential_ambiguous_listener():
    t, r, m = setup()
    m["condition_events"] = [{"id": "off", "t": 0, "order": 5, "condition": "disabled", "value": False,
                              "repeat": {"mode": "once"}, **EVIDENCE}]
    add(m, rule("disabled-counter", trigger={"event": "elemental_reaction", "phase": "post", "reaction_types": ["swirl"], "condition": "disabled"},
                stacking={"max": 2, "add": 1, "policy": "extend"}))
    assert [h["expected"] for h in swirls(execute_mechanics(t, r, m))] == pytest.approx([781.299, 781.299])


@pytest.mark.parametrize("case", ["reaction_id", "layer_selector", "system_deadline_id"])
def test_review_reserved_generated_identities_cannot_be_occupied_or_turned_into_listeners(case):
    t, r, m = setup(("cryo", "anemo"), (1, 1), (0, 2),
                    aura={"element": "hydro", "remaining_gu": .5, "decay_gu_per_second": .1, "source": "fixture"})
    if case == "layer_selector":
        add(m, rule("layer", trigger={"event": "elemental_reaction", "phase": "post", "event_ids": ["h1:aura_layer:frozen"]}))
    else:
        eid = "h0:reaction:swirl:cryo" if case == "reaction_id" else "@elemental_state:enemy:2000000:1"
        m["condition_events"] = [{"id": eid, "t": 1, "order": 1, "condition": "user", "value": True,
                                  "repeat": {"mode": "once"}, **EVIDENCE}]
    out = execute_mechanics(t, r, m)
    assert out["status"] == "invalid", out["issues"]
    assert out["result"] is None


def test_review_deadline_and_history_recovery_continue_across_input_window_boundary():
    t, r, m = setup(("hydro", "cryo"), (2, 1))
    t["loop_duration"] = r["period"] = 2
    out = successful(execute_mechanics(t, r, m))
    first = out["result"]["elemental"]["input_window_state"]
    assert first["frozen"]["remaining_gu"] == pytest.approx(1)
    assert first["freeze_history_seconds"] == 2
    assert first["ordinary_aura"]["remaining_gu"] == pytest.approx(1/3)
    ended = [row for row in out["elemental_trace"] if row["type"] == "elemental_state_transition" and row["reason"] == "frozen_expired"]
    recovered = [row for row in out["elemental_trace"] if row["type"] == "elemental_state_transition" and row["reason"] == "freeze_history_recovered"]
    assert len(ended) == len(recovered) == 1
    assert ended[0]["t"] == 3.483315
    assert recovered[0]["t"] == 5.224973
    final = out["result"]["elemental"]["final_checked_state"]
    assert final["frozen"] is None and final["ordinary_aura"] is None and final["freeze_history_seconds"] == 0
    assert out["result"]["summary"]["total_damage"] == 90, "later check windows must not repeat finite input damage"


def test_review_no_damage_application_stream_creates_only_real_swirl_hits():
    t, r, m = setup()
    t["hits"] = []
    descriptors = m.pop("hit_events")
    m["hit_events"] = []
    m["application_events"] = [{"id": f"apply{i}", "action_id": "cast", "t": 0, "order": 10+i,
                                 "application": d["application"], "contact": d["contact"]} for i, d in enumerate(descriptors)]
    out = successful(execute_mechanics(t, r, m))
    assert len(out["result"]["hits"]) == len(swirls(out)) == 2
    assert out["result"]["summary"]["total_damage"] == pytest.approx(1562.598)
    assert all("reaction_damage:swirl:" in h["id"] for h in out["result"]["hits"])
