"""Ordinary aura commits must survive freezable observations and clocks."""
import copy

import pytest

from genshin_engine.elemental_multiaura import MultiAuraState


@pytest.mark.parametrize("freezable", [True, False])
def test_coexisting_water_survives_observations_and_decay(freezable):
    state = MultiAuraState(freezable=freezable)
    state.add_or_refresh("electro", 1, "lightning", "a")
    state.add_or_refresh("hydro", 1, "water", "b")
    before = copy.deepcopy(state.auras)
    for _ in range(3):
        assert {a["element"] for a in state.state()["auras"]} == {"hydro", "electro"}
        assert state.auras == before
    state.advance(1_000_000)
    assert state.auras["hydro"]["remaining_gu"] == pytest.approx(.8 - .8 / 9.5)
    assert state.contributor_allocations("hydro")[0]["remaining_gu"] == pytest.approx(.8 - .8 / 9.5)


def test_consumption_has_one_ordinary_authority_and_updates_ownership():
    state = MultiAuraState(freezable=True)
    state.add_or_refresh("hydro", 1, "water", "a")
    assert state.freeze_state.aura is state.auras["hydro"]
    assert state.consume("hydro", .3) == pytest.approx(.3)
    assert state.state()["auras"][0]["remaining_gu"] == pytest.approx(.5)
    assert state.contributor_allocations("hydro")[0]["remaining_gu"] == pytest.approx(.5)
    state.consume("hydro", 1)
    assert state.freeze_state.aura is None
    assert state.state()["auras"] == []


def test_freeze_commit_retains_attributed_residual_and_same_element_refresh():
    state = MultiAuraState(freezable=True)
    state.add_or_refresh("hydro", 2, "water", "a")
    state.apply_freezable("cryo", 1, "ice", "b")
    assert state.auras["hydro"]["remaining_gu"] == pytest.approx(.6)
    assert state.contributor_allocations("hydro")[0]["remaining_gu"] == pytest.approx(.6)
    state.apply_freezable("hydro", 1, "new-water", "c")
    assert {a["owner"] for a in state.contributor_allocations("hydro")} == {"a", "c"}
    assert state.auras["hydro"]["remaining_gu"] == pytest.approx(.8)


def test_incompatible_ordinary_commit_is_rejected_before_state_change():
    state = MultiAuraState(freezable=True)
    state.add_or_refresh("cryo", 1, "ice", "a")
    before = state.state()
    with pytest.raises(ValueError, match="冻结状态"):
        state.add_or_refresh("hydro", 1, "water", "b")
    assert state.state() == before
