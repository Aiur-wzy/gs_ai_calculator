"""Bounded EC08 predicates; observations are distinct from gameplay mutations."""
import copy

import pytest
from test_freeze_runtime import model

from genshin_engine.elemental import ElementalRuntime
from genshin_engine.target_state import (
    TARGET_STATE_PROFILE,
    evaluate_target_predicate,
    predicate_bounds_valid,
    target_sample,
)


def aura(*elements):
    return {"kind": "target_aura_in", "elements": list(elements)}


def observe(runtime):
    return target_sample(runtime, event_id="h", run_id="0:h", cycle=0, time=0, order=10)


def test_observation_is_pure_and_does_not_borrow_state():
    runtime = ElementalRuntime(model({"element": "cryo", "remaining_gu": .8,
                                     "decay_gu_per_second": .1, "source": "test"}))
    before = copy.deepcopy(runtime.__dict__)
    samples = [observe(runtime) for _ in range(3)]
    assert all(s["auras"] == ["cryo"] and s["phase"] == "before_application" for s in samples)
    samples[0]["state"]["ordinary_aura"]["remaining_gu"] = 0
    assert runtime.freeze_state.state() == before["freeze_state"].state()
    assert runtime.icd == before["icd"] == {} and runtime.trace == before["trace"] == []
    assert samples[1]["state"]["ordinary_aura"]["remaining_gu"] == .8


def test_frozen_element_and_freeze_status_are_not_the_same_predicate():
    # Abstract predicate unit, not a public initial-state promise for innate enemies.
    sample = {"status": "known", "profile_id": TARGET_STATE_PROFILE, "auras": ["frozen"], "statuses": []}
    assert evaluate_target_predicate(aura("frozen"), sample) is True
    assert evaluate_target_predicate({"kind": "target_status", "status_id": "element_freeze"}, sample) is False
    assert evaluate_target_predicate({"kind": "all", "children": [aura("cryo"), aura("frozen")]}, sample) is False
    assert evaluate_target_predicate({"kind": "any", "children": [aura("cryo"), aura("frozen")]}, sample) is True
    sample["status"] = "pending"
    assert evaluate_target_predicate({"kind": "any", "children": [aura("frozen"), aura("hydro")]}, sample) is None


def test_missing_runtime_is_explicit_unknown_not_empty_elements():
    sample = observe(None)
    assert sample["status"] == "pending" and sample["auras"] is None and sample["statuses"] is None
    assert evaluate_target_predicate(aura("cryo"), sample) is None


@pytest.mark.parametrize("predicate", [
    {"kind": "all", "children": []}, {"kind": "any", "children": [aura("cryo")]*9},
    aura(), aura("cryo", "cryo"),
    {"kind": "all", "children": [{"kind": "all", "children": [aura("cryo")]*8}]*4},
])
def test_tree_bounds(predicate):
    assert not predicate_bounds_valid(predicate)


def test_exact_node_budget_accepts_32():
    tree = {"kind": "all", "children": [{"kind": "any", "children": [aura("cryo")]*8}]*3 +
            [{"kind": "any", "children": [aura("frozen")]*3}]}
    assert predicate_bounds_valid(tree)
    tree["children"][-1]["children"].append(aura("hydro"))
    assert not predicate_bounds_valid(tree)
