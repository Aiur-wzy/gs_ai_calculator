"""Independent EC06 state/contact oracles fixed before the product implementation."""
from __future__ import annotations

import copy
import json
import math
from fractions import Fraction
from pathlib import Path

import pytest

from genshin_engine.elemental import (
    ElementalPending,
    ElementalRuntime,
    validate_elemental_model,
)
from genshin_engine.elemental_freeze import FreezeState
from genshin_engine.elemental_schema import FREEZE_RULESET, elemental_capabilities

EVIDENCE = {"status": "assumed", "source": "独立工程参考情景，非游戏客户端复测"}
SAFE_CONTACT = {"kind": "none", **EVIDENCE}
ORACLE = Path(__file__).resolve().parent / "fixtures/freeze_review_expected.json"


def profile(element, gu=1):
    return {"id": f"{element}{gu}", "element": element, "application": "apply", "gauge_units": gu,
            "icd": {"kind": "none"}, **EVIDENCE}


def fresh_model(aura=None):
    contract = elemental_capabilities()["reference_contract"]
    return {"schema_version": 1, "ruleset": FREEZE_RULESET,
            "target": {"id": "enemy", "kind": "ordinary", "knowledge": "known", **EVIDENCE,
                       "initial_state": {"ordinary_aura": aura, "frozen": None, "freeze_history_seconds": 0, **EVIDENCE},
                       "freeze": {"freezable": True, "resistance": 0, **EVIDENCE}},
            "profiles": [profile(e, g) for e in ("hydro", "cryo", "anemo") for g in (1, 2, 4)],
            "contact_profiles": [{"id": "unsafe", "freeze_interaction": "may_reduce", **EVIDENCE},
                                 {"id": "safe", "freeze_interaction": "none", **EVIDENCE}],
            "swirl_damage": {"icd": {"kind": "sequence", "reset_seconds": .5, "sequence": [1, 1, *([0]*8)]}, **EVIDENCE},
            "reference_acceptance": {"contract_id": contract["contract_id"], "contract_fingerprint": contract["contract_fingerprint"],
                                     "acknowledged": True, "source": "按明确列出的实验规则计算；不声称已验证全部游戏机制"}}


def apply(rt, element, gu=1, t=0, eid=None, actor="a", contact=SAFE_CONTACT, landed=True):
    eid = eid or f"{element}-{len(rt.trace)}"
    return rt.attempt(event_id="0:"+eid, stable_event_id=eid, action_id="action", actor=actor,
                      time=round(t*1_000_000), contact=contact,
                      application={"profile_id": f"{element}{gu}", "target_id": "enemy", "landed": landed})


def state_matches(actual, expected):
    ordinary = expected["ordinary"]
    if ordinary is None:
        assert actual["ordinary_aura"] is None
    else:
        assert actual["ordinary_aura"]["element"] == ordinary[0]
        assert actual["ordinary_aura"]["remaining_gu"] == pytest.approx(ordinary[1], abs=1e-10)
    f = float(Fraction(expected["F_fraction"])) if "F_fraction" in expected else expected["F"]
    if f:
        assert actual["frozen"]["remaining_gu"] == pytest.approx(f, abs=1e-10)
    else:
        assert actual["frozen"] is None
    assert actual["freeze_history_seconds"] == pytest.approx(expected["x"], abs=1e-10)


def test_review_complete_twenty_input_chain_matches_preimplementation_ledger():
    oracle = json.loads(ORACLE.read_text("utf8"))
    model = fresh_model()
    original = copy.deepcopy(model)
    assert validate_elemental_model(model) == []
    rt, kinds, layers, attempts = ElementalRuntime(model), [], [], []
    for row in oracle["rows"]:
        receipt = apply(rt, row["element"], row["gu"], row["t"], row["id"])
        state_matches(receipt["after"], row)
        actual = [r["type"]+(":"+r["reaction_element"] if r["type"] == "swirl" else "") for r in receipt["reactions"]]
        assert actual == row["reactions"], row["id"]
        kinds.extend(r["type"] for r in receipt["reactions"])
        layers.extend(receipt["layer_consumptions"])
        attempts.extend(rt.attempt_reaction_damage(r) for r in receipt["reactions"] if r["type"] == "swirl")
    rt.advance(14_000_000)
    state_matches(rt.state(), oracle["window_end"])
    assert kinds.count("freeze") == 6
    assert kinds.count("swirl") == len(attempts) == 8
    assert len(layers) == 10 and all(row["allowed"] for row in attempts)
    assert model == original


def test_review_simultaneous_ordinary_and_frozen_expiry_cannot_leave_zero_deadline():
    state = FreezeState({"ordinary_aura": {"element": "hydro", "remaining_gu": 1, "decay_gu_per_second": .5, "source": "fixture"},
                         "frozen": {"remaining_gu": 1, "source": "fixture"}, "freeze_history_seconds": 0})
    rows = state.advance(2_000_000)
    assert state.state()["ordinary_aura"] is None
    assert state.state()["frozen"] is None
    assert sorted(r["reason"] for r in rows) == ["frozen_expired", "ordinary_aura_expired"]
    assert state.next_deadline()["time"] == 3_000_000
    assert state.advance(2_000_000) == []


def test_review_long_jump_equals_piecewise_time_and_preserves_recovery_history():
    initial = {"ordinary_aura": None, "frozen": {"remaining_gu": 2, "source": "fixture"}, "freeze_history_seconds": 0}
    jump, split = FreezeState(initial), FreezeState(initial)
    jump.advance(5_000_000)
    for tick in range(1, 501):
        split.advance(tick*10_000)
    expected_x = 3*(2*math.sqrt(14)-4)-10
    for state in (jump, split):
        assert state.frozen is None and state.aura is None
        assert state.history == pytest.approx(expected_x, abs=1e-10)
        assert state.state()["freeze_decay_gu_per_second"] == pytest.approx(.4+.1*expected_x, abs=1e-10)
    assert jump.state() == pytest.approx(split.state(), abs=1e-10)


def test_review_strong_refreeze_cancels_old_deadline_without_resetting_history():
    rt = ElementalRuntime(fresh_model({"element": "hydro", "remaining_gu": .5, "decay_gu_per_second": .1, "source": "fixture"}))
    apply(rt, "cryo", eid="first")
    assert rt.freeze_state.next_deadline()["time"] == 2_000_000
    apply(rt, "hydro", t=1, eid="refill")
    apply(rt, "cryo", t=1, eid="again")
    rt.advance(2_000_000)
    assert rt.state()["frozen"]["remaining_gu"] == pytest.approx(1.05)
    assert rt.state()["freeze_history_seconds"] == 2
    expected = math.ceil((1+(math.sqrt(.57)-.5)/.1)*1_000_000)
    assert rt.freeze_state.next_deadline()["time"] == expected
    assert not any(row["type"] == "elemental_state_transition" and row["reason"] == "frozen_expired" for row in rt.trace)


@pytest.mark.parametrize("t,expect_swirl", [(1.999999, True), (2, False), (2.000001, False)])
def test_review_expiry_microsecond_boundary_is_not_ghost_frozen(t, expect_swirl):
    rt = ElementalRuntime(fresh_model({"element": "hydro", "remaining_gu": .5, "decay_gu_per_second": .1, "source": "fixture"}))
    apply(rt, "cryo", eid="first")
    row = apply(rt, "anemo", t=t, eid="wind")
    assert bool(row["reactions"]) is expect_swirl
    assert rt.state()["frozen"] is None


@pytest.mark.parametrize("contact", [None, {"kind": "profile", "profile_id": "unsafe"},
                                     {"kind": "none", "status": "pending", "source": "unknown"}])
def test_review_freeze_creation_checks_contact_before_real_icd_and_aura_mutation(contact):
    m = fresh_model({"element": "hydro", "remaining_gu": 1.6, "decay_gu_per_second": .1, "source": "fixture"})
    next(p for p in m["profiles"] if p["id"] == "cryo1")["icd"] = {"kind": "standard", "group": "ice"}
    rt = ElementalRuntime(m)
    before = rt.state()
    with pytest.raises(ElementalPending) as caught:
        apply(rt, "cryo", contact=contact)
    assert caught.value.issue["code"] == "freeze_contact_unknown"
    assert rt.state() == before
    assert rt.icd == {}


def test_review_miss_bypasses_contact_and_does_not_use_icd_but_blocked_hit_does_not():
    m = fresh_model()
    next(p for p in m["profiles"] if p["id"] == "cryo1")["icd"] = {"kind": "standard", "group": "ice"}
    rt = ElementalRuntime(m)
    apply(rt, "hydro", 2)
    apply(rt, "cryo")
    before = rt.state()
    assert apply(rt, "cryo", contact=None, landed=False)["reason"] == "missed"
    assert rt.state() == before
    with pytest.raises(ElementalPending) as caught:
        apply(rt, "cryo", contact=None)
    assert caught.value.issue["code"] == "freeze_contact_unknown"
    assert rt.state() == before and next(iter(rt.icd.values()))["hits"] == 1


def test_review_cf_aggregation_never_advances_damage_sequence_twice_or_imports_global_gcd():
    rt = ElementalRuntime(fresh_model())
    apply(rt, "hydro", 2)
    apply(rt, "cryo", 2)
    apply(rt, "cryo")
    row = apply(rt, "anemo", 2, eid="cf")
    assert len(row["layer_consumptions"]) == 2 and len(row["reactions"]) == 1
    reaction = row["reactions"][0]
    assert reaction["id"] == "cf:reaction:swirl:cryo"
    first = rt.attempt_reaction_damage(reaction)
    second_reaction = apply(rt, "anemo", t=.01, eid="next")["reactions"][0]
    second = rt.attempt_reaction_damage(second_reaction)
    other_reaction = apply(rt, "anemo", t=.02, eid="other", actor="b")["reactions"][0]
    other = rt.attempt_reaction_damage(other_reaction)
    third_reaction = apply(rt, "anemo", t=.03, eid="third")["reactions"][0]
    third = rt.attempt_reaction_damage(third_reaction)
    assert [r["allowed"] for r in (first, second, other, third)] == [True, True, True, False]
    assert [r["sequence_index"] for r in (first, second, other, third)] == [0, 1, 0, 2]


@pytest.mark.parametrize("mutation", [
    lambda m: m["reference_acceptance"].update(acknowledged=False),
    lambda m: m["reference_acceptance"].update(contract_fingerprint="outdated"),
    lambda m: m["reference_acceptance"].update(source=" "),
    lambda m: m["target"]["initial_state"].update(frozen={"remaining_gu": 1, "source": "not public"}),
    lambda m: m["target"]["initial_state"].update(freeze_history_seconds=1),
    lambda m: m["target"]["freeze"].update(resistance=.5),
    lambda m: m["target"]["freeze"].update(freezable=False),
    lambda m: m["target"].update(initial_aura=None),
])
def test_review_public_fresh_initial_state_and_explicit_reference_acceptance_cannot_be_bypassed(mutation):
    m = fresh_model()
    mutation(m)
    assert validate_elemental_model(m)
