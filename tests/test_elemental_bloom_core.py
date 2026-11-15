from __future__ import annotations

from genshin_engine.elemental_bloom_core import TICKS, BloomCoreState


def create(state: BloomCoreState, index: int, time: float):
    return state.create(
        time=round(time*TICKS), event_id=f"core.{index}", target_id="enemy",
        area_id="battlefield.primary", owner="hydro", parent_reaction_id=f"bloom.{index}",
        contributor_ids=["dendro", "hydro"], source="fixture",
    )


def test_core_expires_at_six_seconds_and_emits_one_ordinary_bloom_request():
    state = BloomCoreState()
    create(state, 1, 1)
    assert state.advance(round(6.999999*TICKS)) == []
    due = state.advance(7*TICKS)
    assert [(row["event_id"], row["reason"], row["t"]) for row in due] == [
        ("core.1:detonate", "natural_expiry", 7)]
    request = due[0]
    assert request["owner"] == "hydro"
    assert request["core_variant"] == "ordinary"
    assert request["core_lifetime_seconds"] == 6
    assert request["field_limit"] == 5
    assert request["damage_sequence"] == {
        "reset_seconds": .5,
        "max_hits": 2,
        "scope": ["owner", "target_id", "reaction_type"],
    }
    assert request["status"] == "ready_for_damage_projection"
    assert state.state()["cores"] == []


def test_sixth_core_detonates_oldest_without_removing_newest_five():
    state = BloomCoreState()
    for index in range(1, 6):
        assert create(state, index, index/10)["overflow_detonation"] is None
    receipt = create(state, 6, .6)
    assert receipt["overflow_detonation"]["event_id"] == "core.1:detonate"
    assert receipt["overflow_detonation"]["reason"] == "field_limit_oldest"
    assert [row["id"] for row in state.state()["cores"]] == [
        "core.2", "core.3", "core.4", "core.5", "core.6"]
    assert receipt["overflow_detonation"]["owner"] == "hydro"
    assert receipt["overflow_detonation"]["core_variant"] == "ordinary"


def test_deadline_and_areas_are_explicit_and_replayable():
    state = BloomCoreState()
    create(state, 1, 2)
    public = state.state()
    assert public["next_deadline"] == 8
    assert public["core_variant"] == "ordinary"
    assert public["cores"][0]["area_id"] == "battlefield.primary"
    assert public["cores"][0]["core_variant"] == "ordinary"
    assert public["unsupported_core_reactions"] == ["hyperbloom", "burgeon"]


def test_conversion_does_not_evict_other_area_cores_or_replay_old_damage():
    state = BloomCoreState(field_limit=2)
    create(state, 1, 0)
    state.create(time=TICKS, event_id="unrelated", target_id="other", area_id="secondary",
                 owner="hydro", parent_reaction_id="other-bloom", contributor_ids=["hydro"], source="fixture")
    state.open_conversion(time=TICKS, event_id="convert", target_id="enemy", area_id="battlefield.primary",
                          owner="a", duration_ticks=2*TICKS, lifetime_ticks=10*TICKS, convert_existing=True)
    for index in range(2, 7):
        assert create(state, index, 2)["overflow_detonation"] is None
    assert [core["id"] for core in state.state()["cores"]] == ["unrelated"]
    assert len(state.state()["replacement_cores"]) == 6
    assert not state.absorb_replacements(time=2*TICKS, target_id="enemy", area_id="battlefield.primary",
                                        owner="b", maximum=2)["amount"]
    consumed = state.absorb_replacements(time=2*TICKS, target_id="enemy", area_id="battlefield.primary",
                                         owner="a", maximum=2)
    assert consumed["amount"] == 2
    assert [row["ordinary_core_id"] for row in consumed["absorbed_cores"]] == ["core.1", "core.2"]
    assert create(state, 7, 3)["core"]["core_variant"] == "ordinary"
    due = state.advance(15*TICKS)
    assert {row["event_id"] for row in due if row["type"] == "ordinary_bloom_damage_request"} == {
        "unrelated:detonate", "core.7:detonate"}
    assert len([row for row in due if row["type"] == "bloom_replacement_core_expired"]) == 4
