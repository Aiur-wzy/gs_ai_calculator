"""Independent EC04 oracles: elementary gauge arithmetic and fixed damage numbers."""
from __future__ import annotations

import copy

import pytest
from test_mechanics_execution import (
    ability,
    action,
    add,
    fixture,
    rule,
    second_character,
)

from genshin_engine.elemental import ElementalRuntime
from genshin_engine.mechanics import execute_mechanics


def inputs(times=(.2,), aura="cryo"):
    template, rotation, mechanics = fixture(times)
    rotation["actions"][0]["repeat"] = {"mode": "once"}
    rotation["characters"][0]["element"] = "anemo"
    mechanics["elemental_model"] = {
        "schema_version": 1, "ruleset": "gauge-swirl-v2",
        "target": {"id": "enemy", "kind": "ordinary", "knowledge": "known",
                   "initial_aura": {"element": aura, "remaining_gu": 3.2,
                                    "decay_gu_per_second": 3.2 / 17, "source": "controlled 4U initial aura"},
                   "status": "confirmed", "source": "ordinary single target; no special branch"},
        "profiles": [{"id": "wind", "element": "anemo", "application": "apply", "gauge_units": 1,
                      "icd": {"kind": "none"}, "status": "confirmed", "source": "explicit wind application"},
                     {"id": "physical", "element": "physical", "application": "none",
                      "status": "confirmed", "source": "explicit nonapplication"}],
        "swirl_damage": {"icd": {"kind": "sequence", "reset_seconds": .5, "sequence": [1, 1] + [0] * 8},
                         "status": "confirmed", "source": "KQM ordinary origin-target swirl; exact independent damage ICD"},
    }
    for hit, descriptor in zip(template["hits"], mechanics["hit_events"], strict=True):
        hit["element"] = "anemo"
        descriptor["application"] = {"profile_id": "wind", "target_id": "enemy", "landed": True}
    return template, rotation, mechanics


def execute(data):
    before = copy.deepcopy(data)
    result = execute_mechanics(*data)
    assert data == before, "execution must not mutate native input"
    assert result["result"] is not None, result["issues"]
    return result


def swirl_hits(result):
    return [hit for hit in result["result"]["hits"] if hit["damage_system"] == "transformative"]


def shred(mechanics, *, delay=0, owner="a", presence=None):
    trigger = {"event": "elemental_reaction", "phase": "post", "reaction_types": ["swirl"],
               "reaction_elements": ["cryo"]}
    if presence:
        trigger["owner_presence"] = presence
    item = rule("cold_shred", "benefit.res_shred.cryo", .4, trigger=trigger, domain="enemy")
    item["owner"] = owner
    item["timing"]["delay"] = delay
    add(mechanics, item)


@pytest.mark.parametrize("aura", ["hydro", "cryo"])
def test_review_origin_damage_and_no_reapplication(aura):
    data = inputs((0,), aura)
    output = execute(data)
    assert [hit["expected"] for hit in swirl_hits(output)] == pytest.approx([781.299])
    receipt = output["elemental_trace"][0]
    assert receipt["after"]["aura"]["remaining_gu"] == pytest.approx(2.7)
    assert receipt["after"]["aura"]["element"] == aura
    assert sum(row["type"] == "elemental_application" for row in output["elemental_trace"]) == 1, "derived damage must never become a new application"


def test_review_damage_timer_does_not_follow_second_allowed_hit():
    output = execute(inputs((0, .1, .2, .5, .6)))
    assert [hit["t"] for hit in swirl_hits(output)] == [0, .1, .5, .6]
    reactions = [r for r in output["elemental_trace"] if r.get("reaction")]
    assert len(reactions) == 5
    assert reactions[-1]["after"]["aura"]["remaining_gu"] == pytest.approx(3.2 - .6 * 3.2 / 17 - 2.5)


def test_review_damage_timer_reanchors_late_and_sequence_does_not_repeat():
    # First two attempts pass; the 11th is still outside the finite sequence.
    times = tuple(i / 100 for i in range(11)) + (.7, .8, 1.199999, 1.2)
    data = inputs(times)
    # Refresh underlying aura before every attempt without dealing extra damage.
    model = data[2]["elemental_model"]
    model["profiles"].append({"id": "cold4", "element": "cryo", "application": "apply", "gauge_units": 4,
                             "icd": {"kind": "none"}, "status": "confirmed", "source": "controlled refill"})
    data[2]["application_events"] = [{"id": f"refill{i}", "action_id": "cast", "t": t, "order": 9,
        "application": {"profile_id": "cold4", "target_id": "enemy", "landed": True}} for i, t in enumerate(times)]
    output = execute(data)
    assert [hit["t"] for hit in swirl_hits(output)] == [0, .01, .7, .8, 1.2]


def test_review_nondamage_wind_application_still_derives_real_damage():
    data = inputs((.9,))
    data[0]["hits"][0]["element"] = "physical"
    data[2]["hit_events"][0]["application"]["profile_id"] = "physical"
    data[2]["application_events"] = [{"id": "wind_contact", "action_id": "cast", "t": .2, "order": 5,
        "application": {"profile_id": "wind", "target_id": "enemy", "landed": True}}]
    output = execute(data)
    assert [hit["expected"] for hit in swirl_hits(output)] == pytest.approx([781.299])
    assert swirl_hits(output)[0]["t"] == .2


def test_review_no_aura_wind_and_surplus_wind_never_leave_wind_aura():
    data = inputs((0, .1, .2))
    data[2]["elemental_model"]["target"]["initial_aura"].update(remaining_gu=.8)
    data[2]["elemental_model"]["profiles"][0]["gauge_units"] = 4
    output = execute(data)
    assert len(swirl_hits(output)) == 1
    assert all(row["after"]["aura"] is None for row in output["elemental_trace"] if row["type"] == "elemental_application")


@pytest.mark.parametrize("delay, expected", [(0, 998.3265), (.000001, 781.299)])
def test_review_same_reaction_shred_is_current_but_positive_delay_is_not(delay, expected):
    data = inputs()
    shred(data[2], delay=delay)
    output = execute(data)
    assert swirl_hits(output)[0]["expected"] == pytest.approx(expected)
    # Shred is cryo only; parent's anemo direct damage remains unchanged.
    assert output["result"]["hits"][0]["expected"] == pytest.approx(45)


def test_review_reaction_shred_checks_rule_owner_not_event_actor():
    data = inputs()
    second_character(data[0], data[1])
    shred(data[2], owner="b", presence="frontline")
    assert swirl_hits(execute(data))[0]["expected"] == pytest.approx(781.299)
    data[1]["abilities"].append(ability("switch_b", "a", "action.switch"))
    data[1]["actions"].append(action("go_b", "switch_b", .11, .12, switch_to="b", repeat={"mode": "once"}))
    assert swirl_hits(execute(data))[0]["expected"] == pytest.approx(998.3265)


def test_review_actor_changes_isolate_damage_icd_but_action_changes_do_not():
    data = inputs((0, .1, .2, .3))
    second_character(data[0], data[1])
    data[1]["abilities"].extend([ability("another_a"), ability("b_skill", "b"), ability("switch_b", "a", "action.switch")])
    data[1]["actions"].extend([action("cast_a2", "another_a", .12, .13, 1, repeat={"mode": "once"}),
                                 action("go_b", "switch_b", .21, .22, switch_to="b", repeat={"mode": "once"}),
                                 action("cast_b", "b_skill", .23, .24, 2, repeat={"mode": "once"})])
    data[2]["hit_events"][2]["action_id"] = "cast_a2"
    data[2]["hit_events"][3]["action_id"] = "cast_b"
    data[0]["hits"][3]["character"] = "b"
    output = execute(data)
    assert [(hit["t"], hit["character"]) for hit in swirl_hits(output)] == [(0, "a"), (.1, "a"), (.3, "b")]


def test_review_damage_is_independent_of_direct_damage_stats_and_uses_fixed_em():
    data = inputs()
    stats = data[0]["characters"][0]["stats"]
    stats.update(atk=999999, crit_rate=1, crit_dmg=5, em=1000, dmg_bonus={"all": 10})
    data[0]["enemy"]["level"] = 200
    assert swirl_hits(execute(data))[0]["expected"] == pytest.approx(4948.227)


def test_review_damage_name_reservation_rejects_collision_even_without_reaction():
    data = inputs((.2, .3))
    data[2]["elemental_model"]["target"]["initial_aura"] = None
    data[0]["hits"][1]["id"] = "h0:reaction_damage:0"
    data[2]["hit_events"][1]["hit_id"] = "h0:reaction_damage:0"
    output = execute_mechanics(*data)
    assert output["status"] == "invalid"
    assert output["result"] is None


@pytest.mark.parametrize("level", [1, 79, 89])
def test_review_missing_level_base_never_falls_back_to_90(level):
    data = inputs()
    data[0]["characters"][0]["level"] = level
    output = execute_mechanics(*data)
    assert output["status"] in ("invalid", "pending")
    assert output["result"] is None


@pytest.mark.parametrize("domain", ["self", "provider"])
def test_review_same_reaction_new_bonus_waits_until_next_reaction_but_shred_is_current(domain):
    data = inputs((.2, .3))
    shred(data[2])
    add(data[2], rule("swirl_bonus", "benefit.reaction_bonus.swirl", .6, domain=domain,
        trigger={"event": "elemental_reaction", "phase": "post", "reaction_types": ["swirl"]}))
    assert [hit["expected"] for hit in swirl_hits(execute(data))] == pytest.approx([998.3265, 1597.3224])


def test_review_same_reaction_flat_bonus_is_not_retroactive():
    data = inputs((.2, .3))
    add(data[2], rule("swirl_flat", "benefit.reaction_flat.all", 100, domain="provider",
        trigger={"event": "hit", "phase": "post"}))
    assert [hit["expected"] for hit in swirl_hits(execute(data))] == pytest.approx([781.299, 871.299])


def test_review_miss_and_application_icd_rejection_do_not_advance_damage_channel():
    data = inputs((0, .1, .2, .3, .4))
    data[2]["elemental_model"]["profiles"][0]["icd"] = {
        "kind": "sequence", "group": "wind-attack", "reset_seconds": 2.5, "sequence": [1, 0, 1, 1]}
    data[2]["hit_events"][0]["application"]["landed"] = False
    output = execute(data)
    assert [hit["t"] for hit in swirl_hits(output)] == [.1, .3]
    attempts = [row for row in output["elemental_trace"] if row["type"] == "reaction_damage_attempt"]
    assert [row["sequence_index"] for row in attempts] == [0, 1, 2]
    assert [row["allowed"] for row in attempts] == [True, True, False]


def test_review_unused_unknown_damage_profile_does_not_block_no_aura_wind():
    data = inputs()
    data[2]["elemental_model"]["target"]["initial_aura"] = None
    data[2]["elemental_model"]["swirl_damage"].update(icd={"kind": "unknown"}, status="pending")
    del data[0]["characters"][0]["stats"]["em"]
    output = execute(data)
    assert not swirl_hits(output)
    assert output["result"]["hits"][0]["expected"] == pytest.approx(45)


def test_review_current_provider_expression_reads_reaction_snapshot_not_post_hit_stats():
    data = inputs((.2, .3))
    add(data[2], rule("ratio_bonus", "benefit.reaction_bonus.swirl",
        {"kind": "stat", "subject": "owner", "stat": "atk", "factor": .006, "sample": "current"}, domain="provider"))
    add(data[2], rule("after_hit_attack", "benefit.stat_add.atk", 100,
        trigger={"event": "hit", "phase": "post"}))
    # Reaction creation sees ATK100 then ATK200; same-hit +100 cannot retroactively change either ratio.
    assert [hit["expected"] for hit in swirl_hits(execute(data))] == pytest.approx([1250.0784, 1718.8578])


def test_review_damage_state_survives_a_cycle_boundary_until_its_actual_timer():
    runtime = ElementalRuntime(inputs()[2]["elemental_model"])

    def contact(t):
        row = runtime.attempt(event_id=f"event_{t}", action_id="cast", actor="a", time=round(t * 1_000_000),
            application={"profile_id": "wind", "target_id": "enemy", "landed": True})
        return runtime.attempt_reaction_damage(row["reaction"])

    assert contact(.4)["allowed"] is True
    boundary = runtime.boundary(500000, 1)
    assert boundary["reaction_damage_icd"][0]["attempts"] == 1
    assert contact(.6)["allowed"] is True
    assert contact(.7)["allowed"] is False
    assert runtime.boundary(900000, 2)["reaction_damage_icd"] == []
    assert contact(.9)["allowed"] is True


def test_review_different_swirl_elements_do_not_share_damage_attempts():
    data = inputs((0, .1, .2, .3))
    data[2]["elemental_model"]["target"]["initial_aura"]["remaining_gu"] = .8
    data[2]["elemental_model"]["profiles"].append({"id": "wet", "element": "hydro", "application": "apply", "gauge_units": 4,
        "icd": {"kind": "none"}, "status": "confirmed", "source": "controlled replacement after exhausted cold"})
    data[2]["application_events"] = [{"id": "water_contact", "action_id": "cast", "t": .2, "order": 9,
        "application": {"profile_id": "wet", "target_id": "enemy", "landed": True}}]
    output = execute(data)
    assert [hit["element"] for hit in output["resolved_template"]["hits"] if hit["damage_system"] == "transformative"] == ["cryo", "cryo", "hydro", "hydro"]


def test_review_unreported_parent_still_uses_damage_icd_but_child_stays_unreported():
    data = inputs((0, .1, .2))
    data[2]["hit_events"][0]["report"] = False
    output = execute(data)
    assert [hit["t"] for hit in swirl_hits(output)] == [.1]
    attempts = [row for row in output["elemental_trace"] if row["type"] == "reaction_damage_attempt"]
    assert [row["allowed"] for row in attempts] == [True, True, False]


def test_review_reaction_damage_never_spends_ordinary_hit_quota():
    data = inputs((0, .1, .2))
    add(data[2], rule("ordinary_quota", "benefit.base_add.all", 1000,
        quota={"capacity": 2, "scope": "team", "refresh": "reset", "consume": {"event": "hit", "phase": "pre"}}))
    output = execute(data)
    assert [hit["expected"] for hit in output["result"]["hits"] if hit["damage_system"] == "classic"] == pytest.approx([495, 495, 45])
    assert [hit["expected"] for hit in swirl_hits(output)] == pytest.approx([781.299, 781.299])
    assert all(not row["event_id"].endswith(":reaction_damage:0") for row in output["mechanics_trace"] if row["type"] == "quota")


def test_review_v1_initial_state_compatibility_and_reaction_identity_remain_unchanged():
    data = inputs()
    model = data[2]["elemental_model"]
    model["ruleset"] = "gauge-basic-v1"
    del model["swirl_damage"]
    model["target"]["initial_aura"]["remaining_gu"] = 4.0  # Historically accepted explicit state, not a newly inferred game aura.
    model["profiles"][0]["element"] = data[0]["hits"][0]["element"] = "pyro"
    output = execute(data)
    assert output["result"]["hits"][0]["expected"] == pytest.approx(90)
    assert output["elemental_trace"][0]["reaction"]["id"] == "0:h0:reaction"
    assert "reaction_damage_icd" not in output["elemental_trace"][0]["after"]
