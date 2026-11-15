"""Finite ordinary swirl kernel: hand expectations independent of the projector."""
from __future__ import annotations

import copy

import pytest
from test_elemental_execution import attempt, model, profile

from genshin_engine.elemental import (
    ElementalPending,
    ElementalRuntime,
    validate_elemental_model,
)


def swirl_model(element="cryo", gu=3.2):
    value = model()
    value["ruleset"] = "gauge-swirl-v2"
    value["profiles"].append(profile("anemo"))
    value["target"]["initial_aura"] = {"element": element, "remaining_gu": gu,
                                         "decay_gu_per_second": .001, "source": "明确工程单底"} if element else None
    value["swirl_damage"] = {"icd": {"kind": "sequence", "reset_seconds": .5, "sequence": [1, 1, 0, 0, 0, 0, 0, 0, 0, 0]},
                              "status": "assumed", "source": "普通冰水原目标扩散，AttackAttenuation组1的damageSequence"}
    return value


def damage(runtime, t=0, actor="a", eid=None):
    receipt = attempt(runtime, "anemo", t, actor, eid)
    return runtime.attempt_reaction_damage(receipt["reaction"]) if receipt["reaction"] else None


@pytest.mark.parametrize("element", ["cryo", "hydro"])
def test_swirl_consumes_half_raw_gu_and_never_reapplies_or_leaves_anemo(element):
    m = swirl_model(element, .7)
    m["profiles"][-1]["gauge_units"] = 2
    original = copy.deepcopy(m)
    runtime = ElementalRuntime(m)
    row = attempt(runtime, "anemo", eid="h")
    assert row["reaction"]["consumed_gu"] == .7
    assert row["reaction"]["reaction_element"] == element
    assert row["reaction"]["id"] == "h:reaction:0"
    assert row["after"]["aura"] is None
    assert attempt(runtime, "anemo", .1)["outcome"] == "anemo_without_aura"
    assert runtime.aura is None
    assert m == original


def test_real_damage_sequence_and_boundary_anchor_are_independent_of_application_icd():
    runtime = ElementalRuntime(swirl_model())
    receipts = [damage(runtime, t) for t in (0, .1, .2, .5, .6)]
    assert [r["allowed"] for r in receipts] == [True, True, False, True, True]
    assert [r["anchor_t"] for r in receipts] == [0, 0, 0, .5, .5]
    assert all(r["channel"] == "damage" for r in receipts)
    assert runtime.state()["icd"] == []
    state = runtime.state()["reaction_damage_icd"][0]
    assert state["attempts"] == 2 and state["timer_remaining"] == .4
    assert runtime.boundary(1_000_000, 1)["reaction_damage_icd"] == []


def test_first_zero_and_exhausted_sequence_do_not_wrap_or_move_timer():
    m = swirl_model()
    m["swirl_damage"]["icd"]["sequence"] = [0, 1]
    runtime = ElementalRuntime(m)
    receipts = [damage(runtime, t) for t in (0, .1, .2, .3, .5)]
    assert [r["allowed"] for r in receipts] == [False, True, False, False, False]
    assert [r["sequence_index"] for r in receipts] == [0, 1, 2, 3, 0]
    assert receipts[3]["reset_at"] == .5


def test_damage_identity_shares_actions_but_isolates_actors_and_swirl_elements():
    runtime = ElementalRuntime(swirl_model())
    assert damage(runtime, actor="a")["sequence_index"] == 0
    assert damage(runtime, .01, actor="a")["sequence_index"] == 1
    assert damage(runtime, .02, actor="b")["sequence_index"] == 0
    reaction = runtime.attempt(event_id="0:other", stable_event_id="other", action_id="different_cast", actor="a", time=30_000,
                               application={"profile_id": "anemo", "target_id": "enemy", "landed": True})["reaction"]
    assert runtime.attempt_reaction_damage(reaction)["allowed"] is False
    runtime.aura = {"element": "hydro", "remaining_gu": .8, "decay_gu_per_second": .01}
    row = damage(runtime, .04, actor="a")
    assert row["sequence_index"] == 0
    assert len(runtime.state()["reaction_damage_icd"]) == 3
    assert reaction["id"] == "other:reaction:0" and reaction["run_id"] == "0:other:reaction:0"


def test_unknown_damage_is_lazy_and_keeps_reaction_receipt():
    m = swirl_model(None)
    m["swirl_damage"]["icd"] = {"kind": "unknown"}
    runtime = ElementalRuntime(m)
    assert damage(runtime) is None
    attempt(runtime, "cryo", .1)
    with pytest.raises(ElementalPending, match="ICD"):
        damage(runtime, .2)
    assert runtime.trace[-2]["reaction"]["type"] == "swirl"
    assert runtime.trace[-1]["allowed"] is None
    assert runtime.state()["reaction_damage_icd"] == []


def test_unsupported_pyro_aura_and_v1_wind_are_pending():
    unsupported = swirl_model("pyro")
    unsupported["profiles"][-1] = profile("anemo", group="wind-application")
    runtime = ElementalRuntime(unsupported)
    with pytest.raises(ElementalPending, match="冰/水"):
        attempt(runtime, "anemo", .1, eid="unsupported-pyro-swirl")
    assert runtime.state()["icd"] == []
    runtime.aura = None
    recovered = attempt(runtime, "anemo", .1, eid="supported-after-repair")
    assert recovered["allowed"] is True
    assert recovered["icd_audit"]["attempt_number"] == 1
    old = model()
    old["profiles"].append(profile("anemo"))
    with pytest.raises(ElementalPending, match="元素"):
        attempt(ElementalRuntime(old), "anemo")


@pytest.mark.parametrize("mutation", [
    lambda m: m.pop("swirl_damage"),
    lambda m: m.update(ruleset="gauge-basic-v1"),
    lambda m: m["swirl_damage"]["icd"].update(group="escape"),
    lambda m: m["swirl_damage"]["icd"].update(sequence=[]),
    lambda m: m["swirl_damage"]["icd"].update(sequence=[1]*65),
    lambda m: m["swirl_damage"]["icd"].update(sequence=[True]),
    lambda m: m["swirl_damage"]["icd"].update(sequence=[.5]),
    lambda m: m["swirl_damage"]["icd"].update(reset_seconds=True),
    lambda m: m["swirl_damage"]["icd"].update(reset_seconds=0),
    lambda m: m["swirl_damage"]["icd"].update(reset_seconds=1e-7),
    lambda m: m["swirl_damage"]["icd"].update(reset_seconds=1e9+1),
    lambda m: m["swirl_damage"]["icd"].update(reset_seconds=float("inf")),
    lambda m: m["swirl_damage"]["icd"].update(kind="unknown"),
    lambda m: m["swirl_damage"].update(source=" "),
])
def test_invalid_damage_contract_rejected(mutation):
    m = swirl_model()
    mutation(m)
    assert validate_elemental_model(m)


def test_v1_state_and_ids_do_not_gain_v2_fields():
    runtime = ElementalRuntime(model())
    attempt(runtime, "hydro", eid="0:wet")
    row = attempt(runtime, "pyro", eid="0:fire")
    assert row["reaction"]["id"] == "0:fire:reaction"
    assert "stable_event_id" not in row
    assert "reaction_damage_icd" not in row["after"]
