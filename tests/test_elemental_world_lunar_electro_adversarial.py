"""Adversarial contracts for the world-owned Lunar-Charged deadline consumer.

These tests exercise only the public world clock and event cursor.  Setup calls
the reviewed special state machine directly to create an area thundercloud;
the assertions then require :class:`ElementalWorldRuntime` to consume every
deadline against the exact target-local Hydro/Electro owner ledger.
"""
from __future__ import annotations

import copy

import pytest

from genshin_engine.elemental import ElementalRuntime
from genshin_engine.elemental_schema import MULTI_AURA_RULESET
from genshin_engine.elemental_world import ElementalWorldPending, ElementalWorldRuntime
from genshin_engine.special_reactions import (
    REQUIRED_CAPABILITY_TAGS,
    REVIEWED_SPECIAL_REACTION_PROFILES,
    SpecialReactionPending,
    SpecialReactionRuntime,
)

TICKS = 1_000_000
FIRST_TICK = 250_000
EVIDENCE = {
    "status": "assumed",
    "source": "controlled world lunar-electro adversarial fixture",
}


def _replacement(reaction_type: str, **parameter_overrides) -> dict:
    reviewed = REVIEWED_SPECIAL_REACTION_PROFILES[reaction_type]
    parameters = copy.deepcopy(reviewed["parameters"])
    parameters.update(parameter_overrides)
    mechanics = {
        "status": "assumed" if parameter_overrides else "confirmed",
        "source": "controlled reviewed special-reaction fixture",
        "profile_id": reviewed["profile_id"],
        "profile_revision": reviewed["profile_revision"],
        "profile_content_hash": reviewed["profile_content_hash"],
        **parameters,
    }
    if parameter_overrides:
        mechanics["override_reason"] = "adversarial boundary fixture"
    return {
        "enabled": True,
        "qualification": {
            "status": "confirmed",
            "provider_ids": ["qualification.provider"],
            "capability_tag": REQUIRED_CAPABILITY_TAGS[reaction_type],
            "source": "controlled database capability fixture",
        },
        "mechanics": mechanics,
    }


def special_runtime(*enabled: str) -> SpecialReactionRuntime:
    tags = [REQUIRED_CAPABILITY_TAGS[name] for name in enabled]
    return SpecialReactionRuntime(
        {
            "schema_version": 1,
            "replacements": {name: _replacement(name) for name in enabled},
        },
        party_member_ids=[
            "qualification.provider", "trigger.actor", "consumer", "fourth",
        ],
        party_capability_tags={"qualification.provider": tags},
    )


def target_model(
    target_id: str,
    *,
    hydro: float | None = 1.5,
    electro: float | None = 1.5,
    tracked: bool = True,
    knowledge: str = "known",
) -> dict:
    owners = {
        "hydro": "qualification.provider",
        "electro": "trigger.actor",
    }
    initial_auras = []
    for element, amount in (("hydro", hydro), ("electro", electro)):
        if amount is None:
            continue
        row = {
            "element": element,
            "remaining_gu": amount,
            "decay_gu_per_second": .001,
            "source": f"{target_id}.{element}.controlled",
        }
        if tracked:
            row["source_owner"] = owners[element]
        initial_auras.append(row)
    return {
        "schema_version": 1,
        "ruleset": MULTI_AURA_RULESET,
        "target": {
            "id": target_id,
            "kind": "ordinary",
            "knowledge": knowledge,
            "initial_auras": initial_auras,
            "freeze": {
                "freezable": False,
                "resistance": 0,
                "unfreezable_resolution": "coexist",
                **EVIDENCE,
            },
            **EVIDENCE,
        },
        "profiles": [],
        "swirl_damage": {
            "icd": {
                "kind": "sequence",
                "reset_seconds": .5,
                "sequence": [1, 1, 0],
            },
            **EVIDENCE,
        },
    }


def lunar_reaction(target_id: str, area_id: str, index: int) -> dict:
    return {
        "id": f"ordinary.lunar-electro.{index}",
        "type": "electro_charged",
        "aura_element": "hydro",
        "trigger_element": "electro",
        "owner": "trigger.actor",
        "contributor_ids": ["qualification.provider", "trigger.actor"],
        "target_id": target_id,
        "area_id": area_id,
        "parent_event_id": f"hit.lunar-electro.{index}",
        "atomic_parent_id": f"hit.lunar-electro.{index}",
        "commit_state": "proposal",
    }


def bloom_reaction(target_id: str, area_id: str) -> dict:
    return {
        "id": "ordinary.lunar-bloom.1",
        "type": "bloom",
        "aura_element": "hydro",
        "trigger_element": "dendro",
        "owner": "consumer",
        "contributor_ids": ["qualification.provider", "consumer"],
        "target_id": target_id,
        "area_id": area_id,
        "parent_event_id": "hit.lunar-bloom.1",
        "atomic_parent_id": "hit.lunar-bloom.1",
        "commit_state": "proposal",
    }


def create_world(
    specs: list[dict],
    *,
    enabled: tuple[str, ...] = ("lunar_electro",),
    bindings: list[tuple[str, str]] | None = None,
) -> ElementalWorldRuntime:
    special = special_runtime(*enabled)
    world = ElementalWorldRuntime(special_runtime=special)
    for spec in specs:
        model_keys = {key: spec[key] for key in (
            "hydro", "electro", "tracked", "knowledge") if key in spec}
        world.register_target(
            target_model(spec["target_id"], **model_keys),
            area_ids=spec.get("area_ids", ("area.primary",)),
        )
    for index, (target_id, area_id) in enumerate(bindings or (), start=1):
        special.replace(lunar_reaction(target_id, area_id, index), time=0)
    return world


def aura_amount(world: ElementalWorldRuntime, target_id: str, element: str) -> float:
    return next(
        row["remaining_gu"]
        for row in world.get_target(target_id).state()["auras"]
        if row["element"] == element
    )


def refresh_target_aura(
    world: ElementalWorldRuntime,
    target_id: str,
    *,
    raw_gu: float,
) -> None:
    runtime = world.get_target(target_id)
    runtime.multi_state.add_or_refresh(
        "hydro", raw_gu, f"{target_id}.hydro.refresh",
        "qualification.provider")
    runtime.multi_state.add_or_refresh(
        "electro", raw_gu, f"{target_id}.electro.refresh",
        "trigger.actor")


def event_types(world: ElementalWorldRuntime, cursor: int = 0) -> list[str]:
    return [row["type"] for row in world.drain_generated_events(cursor)["events"]]


def assert_tick_triplet(events: list[dict], tick_id: str) -> None:
    ids = [row["event_id"] for row in events]
    request = ids.index(tick_id)
    admission = ids.index(tick_id + ":admission")
    damage = ids.index(tick_id + ":damage")
    assert request < admission < damage


def test_single_target_tick_consumes_live_gauge_and_publishes_causal_order():
    world = create_world(
        [{"target_id": "enemy.a"}],
        bindings=[("enemy.a", "area.primary")],
    )
    tick_id = world.special_runtime.state()["scheduled"][0]["event_id"]

    receipt = world.advance(FIRST_TICK)
    events = world.drain_generated_events(0)["events"]

    assert receipt["time_tick"] == FIRST_TICK
    assert event_types(world) == [
        "special_reaction_tick_request",
        "special_reaction_tick_admission",
        "special_reaction_damage_request",
    ]
    assert_tick_triplet(events, tick_id)
    assert aura_amount(world, "enemy.a", "hydro") == pytest.approx(1.09975)
    assert aura_amount(world, "enemy.a", "electro") == pytest.approx(1.09975)


def test_two_targets_in_one_area_commit_as_one_stable_deadline_batch():
    world = create_world(
        [{"target_id": "enemy.b"}, {"target_id": "enemy.a"}],
        bindings=[
            ("enemy.b", "area.primary"),
            ("enemy.a", "area.primary"),
        ],
    )

    world.advance(FIRST_TICK)
    events = world.drain_generated_events(0)["events"]
    requests = [row for row in events
                if row["type"] == "special_reaction_tick_request"]

    assert [row["target_id"] for row in requests] == ["enemy.a", "enemy.b"]
    assert len(events) == 6
    for request in requests:
        assert_tick_triplet(events, request["event_id"])
    for target_id in ("enemy.a", "enemy.b"):
        assert aura_amount(world, target_id, "hydro") == pytest.approx(1.09975)
        assert aura_amount(world, target_id, "electro") == pytest.approx(1.09975)


def test_reordered_due_rows_cannot_change_stable_target_publication(monkeypatch):
    world = create_world(
        [{"target_id": "enemy.b"}, {"target_id": "enemy.a"}],
        bindings=[
            ("enemy.b", "area.primary"),
            ("enemy.a", "area.primary"),
        ],
    )
    original = world.special_runtime.advance
    before = copy.deepcopy(world.state())

    def reverse_due(time):
        return list(reversed(original(time)))

    monkeypatch.setattr(world.special_runtime, "advance", reverse_due)
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(FIRST_TICK)

    assert error.value.issue["code"] == "world_lunar_electro_tick_invalid"
    assert world.state() == before
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


def test_unbound_spectator_in_cloud_area_is_not_attacked_or_consumed():
    world = create_world(
        [{"target_id": "enemy.bound"}, {"target_id": "enemy.spectator"}],
        bindings=[("enemy.bound", "area.primary")],
    )

    world.advance(FIRST_TICK)
    events = world.drain_generated_events(0)["events"]

    assert {row.get("target_id") for row in events} == {"enemy.bound"}
    assert aura_amount(world, "enemy.bound", "hydro") == pytest.approx(1.09975)
    assert aura_amount(world, "enemy.spectator", "hydro") == pytest.approx(1.49975)


def test_two_areas_dispatch_without_cross_talk():
    world = create_world(
        [
            {"target_id": "enemy.a", "area_ids": ("area.a",)},
            {"target_id": "enemy.b", "area_ids": ("area.b",)},
        ],
        bindings=[("enemy.a", "area.a"), ("enemy.b", "area.b")],
    )

    world.advance(FIRST_TICK)
    requests = [row for row in world.drain_generated_events(0)["events"]
                if row["type"] == "special_reaction_tick_request"]

    assert {(row["area_id"], row["target_id"]) for row in requests} == {
        ("area.a", "enemy.a"), ("area.b", "enemy.b"),
    }
    assert world.special_runtime.state()["lunar_electro_areas"]["area.a"][
        "lunar_electro_last_attack_by_target"] == {"enemy.a": FIRST_TICK}
    assert world.special_runtime.state()["lunar_electro_areas"]["area.b"][
        "lunar_electro_last_attack_by_target"] == {"enemy.b": FIRST_TICK}


def test_same_target_in_two_due_areas_is_supported_or_fails_closed():
    """An overlap may be sequenced or deferred, but must never leak RuntimeError."""
    world = create_world(
        [{"target_id": "enemy.a", "area_ids": ("area.a", "area.b")}],
        bindings=[("enemy.a", "area.a"), ("enemy.a", "area.b")],
    )
    before = copy.deepcopy(world.state())

    try:
        world.advance(FIRST_TICK)
    except ElementalWorldPending:
        assert world.state() == before
        assert world.drain_generated_events(0) == {"events": [], "cursor": 0}
    else:
        requests = [
            row for row in world.drain_generated_events(0)["events"]
            if row["type"] == "special_reaction_tick_request"
        ]
        assert [(row["area_id"], row["target_id"]) for row in requests] == [
            ("area.a", "enemy.a"),
            ("area.b", "enemy.a"),
        ]
        assert aura_amount(world, "enemy.a", "hydro") == pytest.approx(.69975)
        assert aura_amount(world, "enemy.a", "electro") == pytest.approx(.69975)


def test_one_area_boundary_cannot_silently_mutate_an_unrelated_cloud(monkeypatch):
    world = create_world(
        [
            {"target_id": "enemy.a", "area_ids": ("area.a",)},
            {"target_id": "enemy.b", "area_ids": ("area.b",)},
        ],
        bindings=[("enemy.a", "area.a")],
    )
    world.advance(100_000)
    world.special_runtime.replace(
        lunar_reaction("enemy.b", "area.b", 2), time=100_000)
    original = world.special_runtime.advance
    before = copy.deepcopy(world.state())
    before_events = world.drain_generated_events(0)

    def corrupt_other_area(time):
        due = original(time)
        if time == FIRST_TICK:
            world.special_runtime.lunar_cloud_by_area["area.b"][
                "lunar_electro_expires_at"] = 99_999_999
        return due

    monkeypatch.setattr(world.special_runtime, "advance", corrupt_other_area)
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(FIRST_TICK)

    assert error.value.issue["code"] == "world_lunar_electro_tick_invalid"
    assert world.state() == before
    assert world.drain_generated_events(0) == before_events


def test_long_advance_steps_each_deadline_and_matches_segmented_execution():
    specs = [{"target_id": "enemy.a", "hydro": 2.0, "electro": 2.0}]
    bindings = [("enemy.a", "area.primary")]
    direct = create_world(specs, bindings=bindings)
    segmented = create_world(specs, bindings=bindings)

    direct.advance(4_250_000)
    for boundary in (250_000, 2_250_000, 4_250_000):
        segmented.advance(boundary)

    assert direct.state() == segmented.state()
    assert direct.drain_generated_events(0) == segmented.drain_generated_events(0)
    damage = [row for row in direct.drain_generated_events(0)["events"]
              if row["type"] == "special_reaction_damage_request"]
    assert [row["time_tick"] for row in damage] == [250_000, 2_250_000, 4_250_000]


def test_world_calls_special_advance_once_for_one_multi_target_boundary(monkeypatch):
    world = create_world(
        [{"target_id": "enemy.a"}, {"target_id": "enemy.b"}],
        bindings=[("enemy.a", "area.primary"), ("enemy.b", "area.primary")],
    )
    calls: list[int] = []
    original = world.special_runtime.advance

    def observed(time):
        calls.append(time)
        return original(time)

    monkeypatch.setattr(world.special_runtime, "advance", observed)
    world.advance(FIRST_TICK)

    assert calls == [FIRST_TICK]


def test_known_insufficient_gauge_is_an_executed_empty_tick():
    world = create_world(
        [{"target_id": "enemy.a", "hydro": .39, "electro": 1.0}],
        bindings=[("enemy.a", "area.primary")],
    )

    world.advance(FIRST_TICK)
    events = world.drain_generated_events(0)["events"]
    admission = next(row for row in events
                     if row["type"] == "special_reaction_tick_admission")

    assert admission["allowed"] is False
    assert admission["consumed_gu"] == {"hydro": 0, "electro": 0}
    assert not [row for row in events
                if row["type"] == "special_reaction_damage_request"]
    assert aura_amount(world, "enemy.a", "hydro") == pytest.approx(.38975)
    assert aura_amount(world, "enemy.a", "electro") == pytest.approx(.99975)


def test_known_missing_element_is_an_executed_empty_tick():
    world = create_world(
        [{"target_id": "enemy.a", "electro": None}],
        bindings=[("enemy.a", "area.primary")],
    )

    world.advance(FIRST_TICK)
    events = world.drain_generated_events(0)["events"]
    admission = next(row for row in events
                     if row["type"] == "special_reaction_tick_admission")

    assert admission["allowed"] is False
    assert event_types(world) == [
        "special_reaction_tick_request", "special_reaction_tick_admission"]


def test_unknown_owner_residual_ledger_rolls_back_whole_batch():
    world = create_world(
        [
            {"target_id": "enemy.a"},
            {"target_id": "enemy.b", "tracked": False},
        ],
        bindings=[("enemy.a", "area.primary"), ("enemy.b", "area.primary")],
    )
    before_state = copy.deepcopy(world.state())
    before_trace = copy.deepcopy(world.special_runtime.trace)

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(FIRST_TICK)

    assert error.value.issue["code"] == "lunar_electro_owner_residual_unproven"
    assert world.state() == before_state
    assert world.special_runtime.trace == before_trace
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


def test_unknown_target_knowledge_rolls_back_whole_batch():
    world = create_world(
        [{"target_id": "enemy.a", "knowledge": "unknown"}],
        bindings=[("enemy.a", "area.primary")],
    )
    before = copy.deepcopy(world.state())

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(FIRST_TICK)

    assert error.value.issue["code"] == "lunar_electro_aura_unknown"
    assert world.state() == before
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


def test_unknown_tick_area_rolls_back_instead_of_becoming_empty():
    world = create_world(
        [{"target_id": "enemy.a", "area_ids": ("area.real",)}],
        bindings=[("enemy.a", "area.missing")],
    )
    before = copy.deepcopy(world.state())

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(FIRST_TICK)

    assert error.value.issue["code"] == "world_area_unknown"
    assert world.state() == before


def test_tick_target_outside_declared_area_rolls_back():
    world = create_world(
        [
            {"target_id": "enemy.bound", "area_ids": ("area.other",)},
            {"target_id": "enemy.area-member", "area_ids": ("area.cloud",)},
        ],
        bindings=[("enemy.bound", "area.cloud")],
    )
    before = copy.deepcopy(world.state())

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(FIRST_TICK)

    assert error.value.issue["code"] == "world_lunar_electro_target_outside_area"
    assert world.state() == before


def test_target_clock_desynchronization_fails_before_consumption():
    world = create_world(
        [{"target_id": "enemy.a"}],
        bindings=[("enemy.a", "area.primary")],
    )
    world.get_target("enemy.a").advance(1)
    before = copy.deepcopy(world.state())

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(FIRST_TICK)

    assert error.value.issue["code"] == "world_clock_desynchronized"
    assert world.state() == before
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


def test_special_clock_desynchronization_fails_before_consumption():
    world = create_world(
        [{"target_id": "enemy.a"}],
        bindings=[("enemy.a", "area.primary")],
    )
    world.special_runtime.time = 1
    before = copy.deepcopy(world.state())

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(FIRST_TICK)

    assert error.value.issue["code"] == "world_clock_desynchronized"
    assert world.state() == before


def test_duplicate_tick_in_one_due_batch_is_invalid_and_atomic(monkeypatch):
    world = create_world(
        [{"target_id": "enemy.a"}],
        bindings=[("enemy.a", "area.primary")],
    )
    original = world.special_runtime.advance
    before = copy.deepcopy(world.state())

    def duplicate(time):
        due = original(time)
        tick = next(row for row in due
                    if row.get("type") == "special_reaction_tick_request")
        return [*due, copy.deepcopy(tick)]

    monkeypatch.setattr(world.special_runtime, "advance", duplicate)
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(FIRST_TICK)

    assert error.value.issue["code"] == "world_lunar_electro_tick_invalid"
    assert world.state() == before
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


@pytest.mark.parametrize("mode", ["missing", "extra"])
def test_due_tick_set_must_exactly_match_frozen_schedule(monkeypatch, mode):
    world = create_world(
        [{"target_id": "enemy.a"}],
        bindings=[("enemy.a", "area.primary")],
    )
    original = world.special_runtime.advance
    before = copy.deepcopy(world.state())

    def alter_set(time):
        due = original(time)
        tick = next(row for row in due
                    if row.get("type") == "special_reaction_tick_request")
        if mode == "missing":
            return [row for row in due if row is not tick]
        forged = copy.deepcopy(tick)
        forged["tick_index"] = 99
        forged["event_id"] = forged["event_id"].rsplit(":", 1)[0] + ":99"
        return [*due, forged]

    monkeypatch.setattr(world.special_runtime, "advance", alter_set)
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(FIRST_TICK)

    assert error.value.issue["code"] == "world_lunar_electro_tick_invalid"
    assert world.state() == before
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


def test_deleted_middle_scheduled_tick_is_rederived_from_cloud_phase():
    world = create_world(
        [{"target_id": "enemy.a"}],
        bindings=[("enemy.a", "area.primary")],
    )
    world.special_runtime.scheduled = [
        row for row in world.special_runtime.scheduled
        if row["time_tick"] != 2_250_000
    ]
    before = copy.deepcopy(world.state())

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(2_250_000)

    assert error.value.issue["code"] == "world_lunar_electro_schedule_conflict"
    assert world.state() == before
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


def test_malformed_tick_in_due_batch_is_invalid_and_atomic(monkeypatch):
    world = create_world(
        [{"target_id": "enemy.a"}],
        bindings=[("enemy.a", "area.primary")],
    )
    original = world.special_runtime.advance
    before = copy.deepcopy(world.state())

    def malformed(time):
        due = original(time)
        tick = next(row for row in due
                    if row.get("type") == "special_reaction_tick_request")
        tick["time_tick"] = time + 1
        return due

    monkeypatch.setattr(world.special_runtime, "advance", malformed)
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(FIRST_TICK)

    assert error.value.issue["code"] == "world_lunar_electro_tick_invalid"
    assert world.state() == before


@pytest.mark.parametrize(
    "mutate",
    [
        lambda tick: tick["requirements"].__setitem__("consume_each_gu", .1),
        lambda tick: tick["requirements"].__setitem__(
            "per_target_attack_cooldown_seconds", .1),
        lambda tick: tick["requirements"].__setitem__(
            "coexisting_elements", ["hydro"]),
        lambda tick: tick["requirements"].__setitem__(
            "owner_residual_contract", "forged"),
        lambda tick: tick["requirements"].__setitem__(
            "contributor_application_window_seconds", 99),
        lambda tick: tick.__setitem__("profile", {"profile_id": "forged"}),
        lambda tick: tick.__setitem__("component", "forged"),
        lambda tick: tick.__setitem__("contributor_sample_policy", "forged"),
        lambda tick: tick.__setitem__("owner", "fourth"),
        lambda tick: tick.__setitem__("parent_reaction_id", "forged"),
        lambda tick: tick.__setitem__("tick_index", 0),
        lambda tick: tick.__setitem__("t", 999),
    ],
    ids=(
        "consume-gu", "cooldown", "elements", "owner-ledger-contract",
        "contributor-window", "profile", "component", "sample-policy",
        "owner", "parent", "tick-index", "display-time",
    ),
)
def test_tick_reviewed_contract_tampering_is_invalid_and_atomic(monkeypatch, mutate):
    world = create_world(
        [{"target_id": "enemy.a"}],
        bindings=[("enemy.a", "area.primary")],
    )
    original = world.special_runtime.advance
    before = copy.deepcopy(world.state())

    def tampered(time):
        due = original(time)
        tick = next(row for row in due
                    if row.get("type") == "special_reaction_tick_request")
        mutate(tick)
        return due

    monkeypatch.setattr(world.special_runtime, "advance", tampered)
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(FIRST_TICK)

    assert error.value.issue["code"] == "world_lunar_electro_tick_invalid"
    assert world.state() == before
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


def test_tick_index_and_derived_id_cannot_be_forged_together(monkeypatch):
    """The due row must match the scheduled identity, not merely itself."""
    world = create_world(
        [{"target_id": "enemy.a"}],
        bindings=[("enemy.a", "area.primary")],
    )
    original = world.special_runtime.advance
    before = copy.deepcopy(world.state())

    def forge_consistent_identity(time):
        due = original(time)
        tick = next(row for row in due
                    if row.get("type") == "special_reaction_tick_request")
        tick["tick_index"] = 99
        tick["event_id"] = tick["event_id"].rsplit(":", 1)[0] + ":99"
        return due

    monkeypatch.setattr(
        world.special_runtime, "advance", forge_consistent_identity)
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(FIRST_TICK)

    assert error.value.issue["code"] == "world_lunar_electro_tick_invalid"
    assert world.state() == before
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


@pytest.mark.parametrize(
    "mode",
    ["consumed", "aura_after", "damage", "trace", "cloud"],
)
def test_world_admission_rejects_receipt_trace_or_cloud_side_effect_tampering(
    monkeypatch,
    mode,
):
    world = create_world(
        [{"target_id": "enemy.a"}],
        bindings=[("enemy.a", "area.primary")],
    )
    before = copy.deepcopy(world.state())
    original = SpecialReactionRuntime.admit_lunar_electro_tick

    def tamper(runtime, tick, aura_state):
        admitted = original(runtime, tick, aura_state)
        if mode == "consumed":
            admitted["admission"]["consumed_gu"]["hydro"] = .1
        elif mode == "aura_after":
            admitted["aura_after"]["components"]["hydro"][
                "remaining_gu"] += .1
        elif mode == "damage":
            admitted["damage_event"]["system_factor"] = 999
        elif mode == "trace":
            runtime.trace.append({"type": "forged-trace"})
        else:
            runtime.lunar_cloud_by_area["area.primary"][
                "lunar_electro_expires_at"] += 1
        return admitted

    monkeypatch.setattr(
        SpecialReactionRuntime, "admit_lunar_electro_tick", tamper)
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(FIRST_TICK)

    assert error.value.issue["code"] == (
        "lunar_electro_world_admission_mismatch")
    assert world.state() == before
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


@pytest.mark.parametrize("mode", ["drop", "extra", "reverse"])
def test_target_commit_rows_must_equal_preview_in_fixed_order(
    monkeypatch,
    mode,
):
    world = create_world(
        [{"target_id": "enemy.a"}],
        bindings=[("enemy.a", "area.primary")],
    )
    before = copy.deepcopy(world.state())
    original = ElementalRuntime._commit_lunar_electro_tick_receipt

    def tamper(runtime, tick, admitted):
        rows = original(runtime, tick, admitted)
        if mode == "drop":
            return rows[:-1]
        if mode == "extra":
            return [*rows, {"type": "forged-target-row"}]
        return list(reversed(rows))

    monkeypatch.setattr(
        ElementalRuntime, "_commit_lunar_electro_tick_receipt", tamper)
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(FIRST_TICK)

    assert error.value.issue["code"] == (
        "world_lunar_electro_target_commit_mismatch")
    assert world.state() == before
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


def test_unknown_lunar_electro_due_event_is_not_silently_published(monkeypatch):
    world = create_world(
        [{"target_id": "enemy.a"}],
        bindings=[("enemy.a", "area.primary")],
    )
    original = world.special_runtime.advance
    before = copy.deepcopy(world.state())

    def inject_unknown(time):
        due = original(time)
        return [*due, {
            "type": "special_reaction_unreviewed_world_mutation",
            "event_id": "area.primary:lunar-electro:unreviewed",
            "time_tick": time,
            "t": time / TICKS,
            "target_id": None,
            "area_id": "area.primary",
            "reaction_type": "lunar_electro",
            "status": "ready",
        }]

    monkeypatch.setattr(world.special_runtime, "advance", inject_unknown)
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(FIRST_TICK)

    assert error.value.issue["code"] == "world_lunar_electro_tick_invalid"
    assert world.state() == before
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


def test_malformed_lunar_electro_expiry_state_is_not_silently_published(monkeypatch):
    world = create_world(
        [{"target_id": "enemy.a", "hydro": 4.0, "electro": 4.0}],
        bindings=[("enemy.a", "area.primary")],
    )
    original = SpecialReactionRuntime.finalize_world_lunar_electro_expiry
    before = copy.deepcopy(world.state())

    def tamper_expiry(runtime, time):
        due = original(runtime, time)
        for row in due:
            if row.get("operation") == "expire_area_thundercloud":
                row["operation"] = "forged_world_mutation"
        return due

    monkeypatch.setattr(
        SpecialReactionRuntime,
        "finalize_world_lunar_electro_expiry",
        tamper_expiry,
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(6_000_000)

    assert error.value.issue["code"] == "world_lunar_electro_tick_invalid"
    assert world.state() == before
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


def test_expiry_previous_targets_must_match_the_expiring_cloud(monkeypatch):
    world = create_world(
        [{"target_id": "enemy.a", "hydro": 4.0, "electro": 4.0}],
        bindings=[("enemy.a", "area.primary")],
    )
    original = SpecialReactionRuntime.finalize_world_lunar_electro_expiry
    before = copy.deepcopy(world.state())

    def forge_targets(runtime, time):
        due = original(runtime, time)
        expiry = next((row for row in due
                       if row.get("operation") == "expire_area_thundercloud"), None)
        if expiry is not None:
            expiry["previous_target_ids"] = ["enemy.forged"]
        return due

    monkeypatch.setattr(
        SpecialReactionRuntime,
        "finalize_world_lunar_electro_expiry",
        forge_targets,
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(6_000_000)

    assert error.value.issue["code"] == "world_lunar_electro_tick_invalid"
    assert world.state() == before
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


def test_expiry_state_mutation_cannot_commit_without_its_generated_event(monkeypatch):
    world = create_world(
        [{"target_id": "enemy.a", "hydro": 4.0, "electro": 4.0}],
        bindings=[("enemy.a", "area.primary")],
    )
    original = SpecialReactionRuntime.finalize_world_lunar_electro_expiry
    before = copy.deepcopy(world.state())

    def omit_expiry(runtime, time):
        due = original(runtime, time)
        return [row for row in due
                if row.get("operation") != "expire_area_thundercloud"]

    monkeypatch.setattr(
        SpecialReactionRuntime,
        "finalize_world_lunar_electro_expiry",
        omit_expiry,
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(6_000_000)

    assert error.value.issue["code"] == "world_lunar_electro_tick_invalid"
    assert world.state() == before
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


def test_expiry_event_cannot_commit_with_a_nonexpired_cloud_poststate(monkeypatch):
    world = create_world(
        [{"target_id": "enemy.a", "hydro": 4.0, "electro": 4.0}],
        bindings=[("enemy.a", "area.primary")],
    )
    original = SpecialReactionRuntime.finalize_world_lunar_electro_expiry
    before = copy.deepcopy(world.state())

    def keep_cloud_active(runtime, time):
        due = original(runtime, time)
        if any(row.get("operation") == "expire_area_thundercloud"
               for row in due):
            cloud = runtime.lunar_cloud_by_area["area.primary"]
            cloud["lunar_electro_active"] = True
        return due

    monkeypatch.setattr(
        SpecialReactionRuntime,
        "finalize_world_lunar_electro_expiry",
        keep_cloud_active,
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(6_000_000)

    assert error.value.issue["code"] == "world_lunar_electro_tick_invalid"
    assert world.state() == before
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


def test_live_cloud_tick_requires_its_authoritative_phase_record():
    world = create_world(
        [{"target_id": "enemy.a"}],
        bindings=[("enemy.a", "area.primary")],
    )
    del world.special_runtime.lunar_cloud_by_area["area.primary"][
        "lunar_electro_phase_by_target"]["enemy.a"]
    before = copy.deepcopy(world.state())

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(FIRST_TICK)

    assert error.value.issue["code"] == "lunar_electro_phase_contract_invalid"
    assert world.state() == before
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


def test_second_child_admission_failure_restores_first_child_and_cooldown(monkeypatch):
    world = create_world(
        [{"target_id": "enemy.a"}, {"target_id": "enemy.b"}],
        bindings=[("enemy.a", "area.primary"), ("enemy.b", "area.primary")],
    )
    original = SpecialReactionRuntime.admit_lunar_electro_tick
    calls = 0
    before = copy.deepcopy(world.state())
    before_trace = copy.deepcopy(world.special_runtime.trace)

    def fail_second(runtime, tick, aura_state):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SpecialReactionPending(
                "adversarial_second_admission_pending",
                "controlled second admission failure",
                tick["event_id"],
            )
        return original(runtime, tick, aura_state)

    monkeypatch.setattr(
        SpecialReactionRuntime, "admit_lunar_electro_tick", fail_second)
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(FIRST_TICK)

    assert error.value.issue["code"] == "adversarial_second_admission_pending"
    assert world.state() == before
    assert world.special_runtime.trace == before_trace
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


def test_second_child_commit_failure_restores_every_target_and_shared_identity(monkeypatch):
    world = create_world(
        [{"target_id": "enemy.a"}, {"target_id": "enemy.b"}],
        bindings=[("enemy.a", "area.primary"), ("enemy.b", "area.primary")],
    )
    left = world.get_target("enemy.a")
    right = world.get_target("enemy.b")
    identities = (
        world.special_runtime, left, right,
        world.crystallize_world, world.bloom_core_state,
    )
    before = copy.deepcopy(world.state())
    before_target_traces = (copy.deepcopy(left.trace), copy.deepcopy(right.trace))
    before_special_trace = copy.deepcopy(world.special_runtime.trace)
    original = right._commit_lunar_electro_tick_receipt

    def fail_after_commit(tick, admitted):
        original(tick, admitted)
        raise RuntimeError("controlled second target commit failure")

    monkeypatch.setattr(right, "_commit_lunar_electro_tick_receipt", fail_after_commit)
    with pytest.raises(RuntimeError, match="second target commit failure"):
        world.advance(FIRST_TICK)

    assert (
        world.special_runtime, world.get_target("enemy.a"),
        world.get_target("enemy.b"), world.crystallize_world,
        world.bloom_core_state,
    ) == identities
    assert world.state() == before
    assert (left.trace, right.trace) == before_target_traces
    assert world.special_runtime.trace == before_special_trace
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


def test_success_preserves_identity_and_cursor_is_replayable():
    world = create_world(
        [{"target_id": "enemy.a"}],
        bindings=[("enemy.a", "area.primary")],
    )
    special = world.special_runtime
    target = world.get_target("enemy.a")

    receipt = world.advance(FIRST_TICK)
    first = world.drain_generated_events(0)
    replay = world.drain_generated_events(0)

    assert world.special_runtime is special
    assert world.get_target("enemy.a") is target
    assert first == replay
    assert receipt["cursor"] == first["cursor"] == 3
    assert world.drain_generated_events(first["cursor"]) == {
        "events": [], "cursor": first["cursor"]}


def test_readvance_same_time_does_not_repeat_tick_or_cursor():
    world = create_world(
        [{"target_id": "enemy.a"}],
        bindings=[("enemy.a", "area.primary")],
    )
    world.advance(FIRST_TICK)
    before = world.drain_generated_events(0)

    receipt = world.advance(FIRST_TICK)

    assert receipt["generated_event_count"] == 0
    assert receipt["cursor"] == before["cursor"]
    assert world.drain_generated_events(0) == before


def test_publication_collision_rolls_back_tick_and_clock():
    world = create_world(
        [{"target_id": "enemy.a"}],
        bindings=[("enemy.a", "area.primary")],
    )
    tick_id = world.special_runtime.state()["scheduled"][0]["event_id"]
    world.publish_generated_events([{"type": "reservation", "event_id": tick_id}])
    before = copy.deepcopy(world.state())
    before_events = world.drain_generated_events(0)

    with pytest.raises(ValueError, match="事件ID"):
        world.advance(FIRST_TICK)

    assert world.state() == before
    assert world.drain_generated_events(0) == before_events


def test_other_special_deadline_remains_pending_and_unpublished():
    world = create_world(
        [{"target_id": "enemy.a"}],
        enabled=("lunar_bloom",),
    )
    world.special_runtime.replace(
        bloom_reaction("enemy.a", "area.primary"), time=0)
    before = copy.deepcopy(world.state())
    before_trace = copy.deepcopy(world.special_runtime.trace)

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(2_500_000)

    assert error.value.issue["code"] == "world_special_deadline_consumer_missing"
    assert world.state() == before
    assert world.special_runtime.trace == before_trace
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


def test_tick_exactly_at_expiry_with_stale_contributor_remains_pending():
    """Endpoint ordering does not waive the reviewed five-second recency rule."""
    world = create_world(
        [{"target_id": "enemy.a", "hydro": 4.0, "electro": 4.0}],
        bindings=[("enemy.a", "area.primary")],
    )
    world.advance(FIRST_TICK)
    # Refresh at .25 s after the first tick has committed.  The existing phase
    # then schedules 6.25 s exactly at the refreshed cloud expiry.
    world.special_runtime.replace(
        lunar_reaction("enemy.a", "area.primary", 2), time=FIRST_TICK)
    before = copy.deepcopy(world.state())
    before_events = world.drain_generated_events(0)
    before_trace = copy.deepcopy(world.special_runtime.trace)

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(6_250_000)

    assert error.value.issue["code"] == "lunar_electro_contributor_recency_conflict"
    assert world.state() == before
    assert world.special_runtime.trace == before_trace
    assert world.drain_generated_events(0) == before_events


def _prepare_exact_endpoint_world(specs: list[dict]) -> ElementalWorldRuntime:
    bindings = [(spec["target_id"], "area.primary") for spec in specs]
    world = create_world(specs, bindings=bindings)
    world.advance(FIRST_TICK)
    # Extending at .25 s preserves the original phase and adds tick 7 exactly
    # at the new 6.25 s cloud endpoint.
    world.special_runtime.replace(
        lunar_reaction(specs[0]["target_id"], "area.primary", 99),
        time=FIRST_TICK,
    )
    world.advance(2_000_000)
    return world


def test_exact_endpoint_tick_commits_before_single_target_cloud_expiry():
    world = _prepare_exact_endpoint_world([
        {"target_id": "enemy.a", "hydro": 4.0, "electro": 4.0},
    ])
    refresh_target_aura(world, "enemy.a", raw_gu=4.0)

    world.advance(6_250_000)

    endpoint = [row for row in world.drain_generated_events(0)["events"]
                if row.get("time_tick") == 6_250_000]
    assert [row["type"] for row in endpoint] == [
        "special_reaction_tick_request",
        "special_reaction_tick_admission",
        "special_reaction_damage_request",
        "special_reaction_state",
    ]
    assert endpoint[1]["allowed"] is True
    assert endpoint[-1]["operation"] == "expire_area_thundercloud"
    assert world.special_runtime.state()["lunar_electro_areas"]["area.primary"] == {
        "lunar_electro_active": False,
        "lunar_electro_expires_at": None,
        "lunar_electro_target_ids": [],
        "lunar_electro_last_attack_by_target": {},
        "lunar_electro_phase_by_target": {},
    }


def test_exact_endpoint_known_insufficient_aura_emits_empty_tick_then_expiry():
    world = _prepare_exact_endpoint_world([
        {"target_id": "enemy.a", "hydro": .5, "electro": .5},
    ])
    refresh_target_aura(world, "enemy.a", raw_gu=.4)

    world.advance(6_250_000)

    endpoint = [row for row in world.drain_generated_events(0)["events"]
                if row.get("time_tick") == 6_250_000]
    assert [row["type"] for row in endpoint] == [
        "special_reaction_tick_request",
        "special_reaction_tick_admission",
        "special_reaction_state",
    ]
    assert endpoint[1]["allowed"] is False
    assert endpoint[1]["reason"] == (
        "hydro_electro_coexistence_or_gauge_insufficient")
    assert endpoint[-1]["operation"] == "expire_area_thundercloud"


def test_exact_endpoint_multiple_targets_commit_stably_before_one_expiry():
    world = _prepare_exact_endpoint_world([
        {"target_id": "enemy.b", "hydro": .5, "electro": .5},
        {"target_id": "enemy.a", "hydro": 4.0, "electro": 4.0},
    ])
    refresh_target_aura(world, "enemy.a", raw_gu=4.0)
    refresh_target_aura(world, "enemy.b", raw_gu=.4)

    world.advance(6_250_000)

    endpoint = [row for row in world.drain_generated_events(0)["events"]
                if row.get("time_tick") == 6_250_000]
    requests = [row for row in endpoint
                if row["type"] == "special_reaction_tick_request"]
    admissions = [row for row in endpoint
                  if row["type"] == "special_reaction_tick_admission"]
    assert [row["target_id"] for row in requests] == ["enemy.a", "enemy.b"]
    assert [(row["target_id"], row["allowed"]) for row in admissions] == [
        ("enemy.a", True), ("enemy.b", False),
    ]
    assert endpoint[-1]["operation"] == "expire_area_thundercloud"


def test_exact_endpoint_finalize_failure_rolls_back_ticks_targets_and_expiry(
    monkeypatch,
):
    world = _prepare_exact_endpoint_world([
        {"target_id": "enemy.a", "hydro": 4.0, "electro": 4.0},
    ])
    refresh_target_aura(world, "enemy.a", raw_gu=4.0)
    before = copy.deepcopy(world.state())
    before_events = world.drain_generated_events(0)
    original = SpecialReactionRuntime.finalize_world_lunar_electro_expiry

    def fail_after_finalize(runtime, time):
        original(runtime, time)
        raise SpecialReactionPending(
            "controlled_endpoint_expiry_failure",
            "controlled exact-endpoint expiry failure",
            "area.primary:lunar-electro:expire:6250000",
        )

    monkeypatch.setattr(
        SpecialReactionRuntime,
        "finalize_world_lunar_electro_expiry",
        fail_after_finalize,
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(6_250_000)

    assert error.value.issue["code"] == "controlled_endpoint_expiry_failure"
    assert world.state() == before
    assert world.drain_generated_events(0) == before_events
