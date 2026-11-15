"""Independent adversarial tests for the world Star-Swirl packet consumer.

The packet is a two-phase commit boundary: target-local elemental proposals are
previewed first, the area reaction is routed once, and only then may every
target plus the shared vortex/event journal commit.  These tests deliberately
exercise failures between those phases so an event-id-only implementation
cannot satisfy the contract.
"""
from __future__ import annotations

import copy
from types import MethodType

import pytest

import genshin_engine.elemental as elemental_module
import genshin_engine.special_reactions as special_reactions_module
from genshin_engine.elemental import ElementalPending, ElementalRuntime
from genshin_engine.elemental_schema import MULTI_AURA_RULESET
from genshin_engine.elemental_world import ElementalWorldPending, ElementalWorldRuntime
from genshin_engine.special_reactions import (
    REQUIRED_CAPABILITY_TAGS,
    REVIEWED_SPECIAL_REACTION_PROFILES,
    SpecialReactionRuntime,
)

EVIDENCE = {"status": "assumed", "source": "stellar packet adversarial fixture"}
AREA = "area.shared"
PARTY = ["qualification.provider", "cryo.provider", "wind.actor", "fourth"]


def aura(element: str, *, owner: str = "cryo.provider", gauge: float = .8) -> dict:
    return {
        "element": element,
        "remaining_gu": gauge,
        "decay_gu_per_second": .001,
        "source": "stellar packet initial aura",
        "source_owner": owner,
    }


def elemental_model(
    target_id: str,
    *,
    initial_auras: tuple[dict, ...] = (),
    pending_anemo: bool = False,
) -> dict:
    profiles = []
    for element in ("pyro", "hydro", "cryo", "electro", "dendro", "anemo", "geo"):
        evidence = (
            {"status": "pending", "source": "deliberately unresolved test profile"}
            if pending_anemo and element == "anemo"
            else EVIDENCE
        )
        profiles.append({
            "id": element,
            "element": element,
            "application": "apply",
            "gauge_units": 1,
            "icd": {"kind": "none"},
            **evidence,
        })
    return {
        "schema_version": 1,
        "ruleset": MULTI_AURA_RULESET,
        "target": {
            "id": target_id,
            "kind": "ordinary",
            "knowledge": "known",
            "initial_auras": list(initial_auras),
            "freeze": {
                "freezable": False,
                "resistance": 0,
                "unfreezable_resolution": "coexist",
                **EVIDENCE,
            },
            **EVIDENCE,
        },
        "profiles": profiles,
        "swirl_damage": {
            "icd": {"kind": "sequence", "reset_seconds": .5,
                    "sequence": [1, 1, 0]},
            **EVIDENCE,
        },
    }


def stellar_runtime() -> SpecialReactionRuntime:
    reviewed = REVIEWED_SPECIAL_REACTION_PROFILES["stellar_swirl"]
    replacement = {
        "enabled": True,
        "qualification": {
            "status": "confirmed",
            "provider_ids": ["qualification.provider"],
            "capability_tag": REQUIRED_CAPABILITY_TAGS["stellar_swirl"],
            "source": "stellar packet adversarial capability",
        },
        "mechanics": {
            "status": "confirmed",
            "source": "stellar packet reviewed profile",
            "profile_id": reviewed["profile_id"],
            "profile_revision": reviewed["profile_revision"],
            "profile_content_hash": reviewed["profile_content_hash"],
            **copy.deepcopy(reviewed["parameters"]),
        },
    }
    return SpecialReactionRuntime(
        {"schema_version": 1, "replacements": {"stellar_swirl": replacement}},
        party_member_ids=PARTY,
        party_capability_tags={
            "qualification.provider": [REQUIRED_CAPABILITY_TAGS["stellar_swirl"]]
        },
    )


def world_with_targets(
    *,
    target_count: int = 2,
    pending_target: int | None = None,
    extra_aura_target: int | None = None,
) -> ElementalWorldRuntime:
    world = ElementalWorldRuntime(special_runtime=stellar_runtime())
    for index in range(target_count):
        small_multi = extra_aura_target == index
        initial = [aura("cryo", gauge=(.2 if small_multi else .8))]
        if extra_aura_target == index:
            initial.append(aura(
                "hydro", owner="qualification.provider", gauge=.2))
        world.register_target(
            elemental_model(
                f"enemy.{index}",
                initial_auras=tuple(initial),
                pending_anemo=(pending_target == index),
            ),
            area_ids=(AREA,),
        )
    return world


def child(
    index: int,
    *,
    count: int = 2,
    parent: str = "attack.aoe",
    time: int = 0,
    area: str = AREA,
    target: str | None = None,
) -> dict:
    event_id = f"wind.packet:{index}"
    return {
        "event_id": event_id,
        "stable_event_id": event_id,
        "action_id": "action.wind",
        "actor": "wind.actor",
        "time_tick": time,
        "packet_child_order": index,
        "damage_element": "anemo",
        "application": {
            "profile_id": "anemo",
            "target_id": target or f"enemy.{index}",
            "landed": True,
            "attempted": True,
            "area_id": area,
            "atomic_parent_id": parent,
            "application_packet_size": count,
            "direct_hit": True,
        },
    }


def packet(count: int = 2, **overrides) -> list[dict]:
    return [child(index, count=count, **overrides) for index in range(count)]


def cryo_gauge(world: ElementalWorldRuntime, target_id: str) -> float:
    return next(
        row["remaining_gu"]
        for row in world.get_target(target_id).state()["auras"]
        if row["element"] == "cryo"
    )


def vortex(world: ElementalWorldRuntime) -> dict:
    return world.special_runtime.state()["areas"][AREA]


def test_same_parent_time_and_area_routes_once_before_any_live_child_commit(monkeypatch):
    world = world_with_targets()
    before = {target: cryo_gauge(world, target) for target in ("enemy.0", "enemy.1")}
    original = SpecialReactionRuntime.replace_stellar_swirl_packet
    observations = []

    def observe_before_router_commit(runtime, proposals, **kwargs):
        observations.append({
            "proposal_count": len(proposals),
            "live_gauges": {
                target: cryo_gauge(world, target)
                for target in ("enemy.0", "enemy.1")
            },
        })
        return original(runtime, proposals, **kwargs)

    monkeypatch.setattr(
        SpecialReactionRuntime, "replace_stellar_swirl_packet",
        observe_before_router_commit,
    )
    receipt = world.attempt_stellar_swirl_packet(packet())

    assert observations == [{"proposal_count": 2, "live_gauges": before}]
    assert receipt["child_count"] == 2
    assert [row["target_id"] for row in receipt["reactions"]] == [
        "enemy.0", "enemy.1"]
    assert all(cryo_gauge(world, target) < before[target] for target in before)
    assert vortex(world)["stellar_swirl_count"] == 1
    immediate = [
        row for row in receipt["generated_events"]
        if row.get("component") == "immediate_anemo"
    ]
    created = [
        row for row in receipt["generated_events"]
        if row.get("operation") == "create_vortex_from_packet"
    ]
    assert len(immediate) == 2
    assert len(created) == 1
    assert created[0]["counted_children"] == 1


def test_packet_against_existing_vortex_counts_every_child():
    world = world_with_targets()
    first = [child(0, count=1, parent="attack.first")]
    world.attempt_stellar_swirl_packet(first)
    assert vortex(world)["stellar_swirl_count"] == 1

    world.advance(100_000)
    second = packet(parent="attack.second", time=100_000)
    for row in second:
        row["event_id"] = "second:" + row["event_id"]
        row["stable_event_id"] = row["event_id"]
    receipt = world.attempt_stellar_swirl_packet(second)

    assert vortex(world)["stellar_swirl_count"] == 3
    assert vortex(world)["stellar_swirl_level"] == 2
    advances = [
        row for row in receipt["generated_events"]
        if row.get("operation") == "advance_existing_vortex_from_packet_child"
    ]
    assert [row["count"] for row in advances] == [2, 3]
    assert len([row for row in receipt["generated_events"]
                if row.get("component") == "immediate_anemo"]) == 2


def test_pending_child_restores_all_targets_icd_shared_state_and_event_journal():
    world = world_with_targets(pending_target=1)
    targets = [world.get_target("enemy.0"), world.get_target("enemy.1")]
    before_state = copy.deepcopy(world.state())
    before_target_traces = [copy.deepcopy(runtime.trace) for runtime in targets]
    before_special_trace = copy.deepcopy(world.special_runtime.trace)
    before_events = world.drain_generated_events(0)

    with pytest.raises(ElementalPending) as error:
        world.attempt_stellar_swirl_packet(packet())
    assert error.value.issue["code"] == "unknown_application"
    assert world.state() == before_state
    assert [runtime.trace for runtime in targets] == before_target_traces
    assert world.special_runtime.trace == before_special_trace
    assert world.drain_generated_events(0) == before_events


@pytest.mark.parametrize(
    ("mutate", "error_type"),
    [
        (lambda rows: rows[1]["application"].__setitem__(
            "atomic_parent_id", "attack.other"), ValueError),
        (lambda rows: rows[1].__setitem__("time_tick", 1), ValueError),
        (lambda rows: rows[1]["application"].__setitem__(
            "area_id", "area.other"), ValueError),
        (lambda rows: rows[1]["application"].__setitem__(
            "application_packet_size", 3), ValueError),
        (lambda rows: rows[1]["application"].__setitem__(
            "target_id", "enemy.0"), ValueError),
        (lambda rows: rows[1].__setitem__(
            "event_id", rows[0]["event_id"]), ValueError),
    ],
    ids=("parent", "time", "area", "packet-size", "target", "child-id"),
)
def test_malformed_packet_identity_fails_closed_without_mutation(mutate, error_type):
    world = world_with_targets()
    rows = packet()
    mutate(rows)
    before = copy.deepcopy(world.state())

    with pytest.raises(error_type):
        world.attempt_stellar_swirl_packet(rows)

    assert world.state() == before
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


def test_target_outside_declared_area_and_unaligned_world_time_fail_closed():
    world = ElementalWorldRuntime(special_runtime=stellar_runtime())
    world.register_target(
        elemental_model("enemy.0", initial_auras=(aura("cryo"),)),
        area_ids=(AREA,),
    )
    world.register_target(
        elemental_model("enemy.1", initial_auras=(aura("cryo"),)),
        area_ids=("area.other",),
    )
    before = copy.deepcopy(world.state())

    with pytest.raises(ElementalWorldPending) as area_error:
        world.attempt_stellar_swirl_packet(packet())
    assert area_error.value.issue["code"] == "stellar_swirl_packet_target_outside_area"
    assert world.state() == before

    aligned_world = world_with_targets()
    with pytest.raises(ElementalWorldPending) as time_error:
        aligned_world.attempt_stellar_swirl_packet(packet(time=1))
    assert time_error.value.issue["code"] == "stellar_swirl_packet_time_not_aligned"
    assert aligned_world.time == 0


def test_extra_reaction_in_one_child_rejects_the_whole_packet():
    world = world_with_targets(extra_aura_target=1)
    before = copy.deepcopy(world.state())

    with pytest.raises(ElementalWorldPending) as error:
        world.attempt_stellar_swirl_packet(packet())
    assert error.value.issue["code"] == "stellar_swirl_packet_child_not_unique_cryo_swirl"
    summaries = error.value.issue["reaction_summaries"]
    assert {row["aura_element"] for row in summaries} == {"cryo", "hydro"}
    assert world.state() == before
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


def test_complete_proposal_equality_prevents_same_id_changed_state_commit(monkeypatch):
    world = world_with_targets()
    before = copy.deepcopy(world.state())
    original = ElementalRuntime._attempt_with_world_reaction_replacements

    def mutate_after_routing(runtime, **kwargs):
        if runtime.target == "enemy.0":
            # Keep the event/reaction id stable while changing the trigger GU
            # carried by the proposal.  An id-only check would wrongly accept
            # it, even though consumed_gu/trigger_gu are now different.
            runtime.profiles["anemo"]["gauge_units"] = .5
        return original(runtime, **kwargs)

    monkeypatch.setattr(
        ElementalRuntime,
        "_attempt_with_world_reaction_replacements",
        mutate_after_routing,
    )
    with pytest.raises(ElementalPending) as error:
        world.attempt_stellar_swirl_packet(packet())

    assert error.value.issue["code"] == "world_reaction_proposal_changed"
    assert world.state() == before
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


def test_generated_event_ids_are_deterministic_unique_and_replayable():
    left = world_with_targets()
    right = world_with_targets()

    left_receipt = left.attempt_stellar_swirl_packet(packet())
    right_receipt = right.attempt_stellar_swirl_packet(packet())
    left_ids = [row["event_id"] for row in left_receipt["generated_events"]]
    right_ids = [row["event_id"] for row in right_receipt["generated_events"]]

    assert left_ids == right_ids
    assert len(left_ids) == len(set(left_ids))
    first = left.drain_generated_events(0)
    replay = left.drain_generated_events(0)
    assert first == replay
    assert [row["event_id"] for row in first["events"]] == left_ids
    assert first["cursor"] == left_receipt["cursor"]
    assert left.drain_generated_events(first["cursor"]) == {
        "events": [], "cursor": first["cursor"]}


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda runtime, result: result.__setitem__(
            "generated_events",
            [row for row in result["generated_events"]
             if row.get("component") != "immediate_anemo"],
        ),
        lambda runtime, result: result.__setitem__(
            "generated_events",
            [row for row in result["generated_events"]
             if row.get("operation") != "create_vortex_from_packet"],
        ),
        lambda runtime, result: runtime.party_state.__setitem__(
            "lunar_bloom_dew", 999),
        lambda runtime, result: runtime.vortex_by_area[AREA].__setitem__(
            "stellar_swirl_count", 42),
    ],
    ids=(
        "missing-immediate-anemo",
        "missing-create-state",
        "unrelated-special-state-pollution",
        "vortex-poststate-pollution",
    ),
)
def test_packet_producer_result_and_complete_poststate_are_exact(
    monkeypatch,
    corrupt,
):
    world = world_with_targets()
    before = copy.deepcopy(world.state())
    before_events = world.drain_generated_events(0)
    original = SpecialReactionRuntime.replace_stellar_swirl_packet

    def corrupted(runtime, *args, **kwargs):
        result = original(runtime, *args, **kwargs)
        corrupt(runtime, result)
        return result

    monkeypatch.setattr(
        SpecialReactionRuntime,
        "replace_stellar_swirl_packet",
        corrupted,
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.attempt_stellar_swirl_packet(packet())

    assert error.value.issue["code"] == "stellar_swirl_packet_producer_mismatch"
    assert world.state() == before
    assert world.drain_generated_events(0) == before_events


def test_packet_target_receipt_and_hidden_poststate_tamper_roll_back(monkeypatch):
    world = world_with_targets()
    before = copy.deepcopy(world.state())
    before_events = world.drain_generated_events(0)
    before_profiles = {
        target_id: copy.deepcopy(world.get_target(target_id).profiles)
        for target_id in ("enemy.0", "enemy.1")
    }
    original = ElementalRuntime._attempt_with_world_reaction_replacements

    def corrupted(runtime, **kwargs):
        receipt = original(runtime, **kwargs)
        receipt["application_gu"] = .123
        runtime.profiles["anemo"]["gauge_units"] = 999
        runtime.trace.append({"type": "forged_trace"})
        return receipt

    monkeypatch.setattr(
        ElementalRuntime,
        "_attempt_with_world_reaction_replacements",
        corrupted,
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.attempt_stellar_swirl_packet(packet())

    assert error.value.issue["code"] in {
        "stellar_swirl_packet_target_preview_mismatch",
        "stellar_swirl_packet_target_commit_mismatch",
    }
    assert world.state() == before
    assert world.drain_generated_events(0) == before_events
    assert {
        target_id: world.get_target(target_id).profiles
        for target_id in ("enemy.0", "enemy.1")
    } == before_profiles


@pytest.mark.parametrize(
    "method_name",
    [
        "_attempt_with_world_reaction_replacements",
        "_attempt_with_world_reaction_routes",
    ],
)
def test_packet_target_reference_rejects_shared_method_graph_pollution(
    monkeypatch,
    method_name: str,
):
    world = world_with_targets()
    before = copy.deepcopy(world.state())
    before_events = world.drain_generated_events(0)
    original = getattr(ElementalRuntime, method_name)

    def pollute_model(runtime, **kwargs):
        receipt = original(runtime, **kwargs)
        runtime.model["audit_target_pollution"] = "forged"
        return receipt

    monkeypatch.setattr(ElementalRuntime, method_name, pollute_model)

    with pytest.raises(ElementalWorldPending) as error:
        world.attempt_stellar_swirl_packet(packet())

    assert error.value.issue["code"] in {
        "stellar_swirl_packet_target_preview_mismatch",
        "stellar_swirl_packet_target_commit_mismatch",
    }
    assert world.state() == before
    assert world.drain_generated_events(0) == before_events


def test_packet_reference_isolates_elemental_module_route_helper(monkeypatch):
    world = world_with_targets()
    before = copy.deepcopy(world.state())
    before_events = world.drain_generated_events(0)
    original = elemental_module._world_route_decision_drift

    def forge_binding(proposal, replacement, decision, time_tick):
        drift = original(proposal, replacement, decision, time_tick)
        replacement["type"] = "forged_stellar_swirl"
        decision["special_reaction_type"] = "forged_stellar_swirl"
        return {name: [] for name in drift}

    monkeypatch.setattr(
        elemental_module,
        "_world_route_decision_drift",
        forge_binding,
    )

    with pytest.raises(ElementalWorldPending) as error:
        world.attempt_stellar_swirl_packet(packet())

    assert error.value.issue["code"] in {
        "stellar_swirl_packet_target_preview_mismatch",
        "stellar_swirl_packet_target_commit_mismatch",
    }
    assert world.state() == before
    assert world.drain_generated_events(0) == before_events


def test_packet_reference_isolates_shared_role_merge_helper_poison(monkeypatch):
    world = world_with_targets()
    before = copy.deepcopy(world.state())
    before_events = world.drain_generated_events(0)
    special_identity = world.special_runtime
    target_identities = {
        target_id: world.get_target(target_id)
        for target_id in ("enemy.0", "enemy.1")
    }
    original = SpecialReactionRuntime._merge_stellar_swirl_roles

    def corrupted(state, provenance, event_id):
        original(state, provenance, event_id)
        state["stellar_swirl_owner"] = "poisoned.owner"

    monkeypatch.setattr(
        SpecialReactionRuntime,
        "_merge_stellar_swirl_roles",
        staticmethod(corrupted),
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.attempt_stellar_swirl_packet(packet())

    assert error.value.issue["code"] == "stellar_swirl_packet_producer_mismatch"
    assert world.state() == before
    assert world.drain_generated_events(0) == before_events
    assert world.special_runtime is special_identity
    assert all(
        world.get_target(target_id) is runtime
        for target_id, runtime in target_identities.items()
    )


@pytest.mark.parametrize(
    "helper_name",
    ["_stellar_swirl_roles", "_damage_request", "_vortex_state"],
)
def test_packet_reference_isolates_other_dynamic_helper_poison(
    monkeypatch,
    helper_name,
):
    world = world_with_targets()
    before = copy.deepcopy(world.state())
    before_events = world.drain_generated_events(0)
    original = getattr(SpecialReactionRuntime, helper_name)

    def corrupted(runtime, *args, **kwargs):
        result = original(runtime, *args, **kwargs)
        runtime.party_state["lunar_bloom_dew"] = 777
        return result

    monkeypatch.setattr(SpecialReactionRuntime, helper_name, corrupted)
    with pytest.raises(ElementalWorldPending) as error:
        world.attempt_stellar_swirl_packet(packet())

    assert error.value.issue["code"] == "stellar_swirl_packet_producer_mismatch"
    assert world.state() == before
    assert world.drain_generated_events(0) == before_events


def test_packet_reference_isolates_module_factor_helper_poison(monkeypatch):
    world = world_with_targets()
    before = copy.deepcopy(world.state())
    before_events = world.drain_generated_events(0)
    special_identity = world.special_runtime
    target_identities = {
        target_id: world.get_target(target_id)
        for target_id in ("enemy.0", "enemy.1")
    }
    original = special_reactions_module._factor_for

    def corrupted(reaction_type, *, layers=None):
        kind, factor = original(reaction_type, layers=layers)
        return kind, factor + 999

    monkeypatch.setattr(special_reactions_module, "_factor_for", corrupted)

    with pytest.raises(ElementalWorldPending) as error:
        world.attempt_stellar_swirl_packet(packet())

    assert error.value.issue["code"] == "stellar_swirl_packet_producer_mismatch"
    assert world.state() == before
    assert world.drain_generated_events(0) == before_events
    assert world.special_runtime is special_identity
    assert all(
        world.get_target(target_id) is runtime
        for target_id, runtime in target_identities.items()
    )


def test_packet_reference_isolates_mutated_factor_constant(monkeypatch):
    world = world_with_targets()
    before = copy.deepcopy(world.state())
    before_events = world.drain_generated_events(0)
    original_factor = special_reactions_module.STELLAR_FACTOR[
        "stellar_swirl_anemo"
    ]

    monkeypatch.setitem(
        special_reactions_module.STELLAR_FACTOR,
        "stellar_swirl_anemo",
        original_factor + 777,
    )

    with pytest.raises(ElementalWorldPending) as error:
        world.attempt_stellar_swirl_packet(packet())

    assert error.value.issue["code"] == "stellar_swirl_packet_producer_mismatch"
    assert world.state() == before
    assert world.drain_generated_events(0) == before_events

    # Restoring the live constant must make a later transaction agree with the
    # original reference snapshot.  This also proves the failed call did not
    # mutate the private canonical mapping through a shared dictionary.
    monkeypatch.undo()
    restored_world = world_with_targets()
    receipt = restored_world.attempt_stellar_swirl_packet(packet())
    immediate = [
        row
        for row in receipt["generated_events"]
        if row.get("component") == "immediate_anemo"
    ]
    assert len(immediate) == 2
    assert all(row["system_factor"] == original_factor for row in immediate)


def test_packet_reference_entry_isolates_module_time_helper_poison(monkeypatch):
    world = world_with_targets()
    before = copy.deepcopy(world.state())
    before_events = world.drain_generated_events(0)
    original = special_reactions_module._seconds_to_ticks

    def corrupted(value, path):
        if path == "detonation_delay_seconds":
            return 123
        return original(value, path)

    monkeypatch.setattr(special_reactions_module, "_seconds_to_ticks", corrupted)

    with pytest.raises(ElementalWorldPending) as error:
        world.attempt_stellar_swirl_packet(packet())

    assert error.value.issue["code"] == "stellar_swirl_packet_producer_mismatch"
    assert world.state() == before
    assert world.drain_generated_events(0) == before_events

    monkeypatch.undo()
    restored_world = world_with_targets()
    receipt = restored_world.attempt_stellar_swirl_packet(packet())
    create = next(
        row
        for row in receipt["generated_events"]
        if row.get("operation") == "create_vortex_from_packet"
    )
    assert create["detonate_at"] != 123 / 1_000_000


def test_packet_rejects_unexpected_special_runtime_instance_field(monkeypatch):
    world = world_with_targets()
    before = copy.deepcopy(world.state())
    before_events = world.drain_generated_events(0)
    original = SpecialReactionRuntime.replace_stellar_swirl_packet

    def add_hidden_state(runtime, *args, **kwargs):
        result = original(runtime, *args, **kwargs)
        runtime.forged_hidden_attr = {"committed": True}
        return result

    monkeypatch.setattr(
        SpecialReactionRuntime,
        "replace_stellar_swirl_packet",
        add_hidden_state,
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.attempt_stellar_swirl_packet(packet())

    assert error.value.issue["code"] == "world_special_runtime_schema_mismatch"
    assert world.state() == before
    assert world.drain_generated_events(0) == before_events
    assert not hasattr(world.special_runtime, "forged_hidden_attr")


@pytest.mark.parametrize("field", ["crystallize_state", "bloom_core_state"])
def test_packet_live_target_cannot_rebind_shared_state_objects(
    monkeypatch,
    field,
):
    world = world_with_targets()
    before = copy.deepcopy(world.state())
    before_events = world.drain_generated_events(0)
    original = ElementalRuntime._attempt_with_world_reaction_replacements

    def rebind_shared_state(runtime, **kwargs):
        receipt = original(runtime, **kwargs)
        setattr(runtime, field, copy.deepcopy(getattr(runtime, field)))
        return receipt

    monkeypatch.setattr(
        ElementalRuntime,
        "_attempt_with_world_reaction_replacements",
        rebind_shared_state,
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.attempt_stellar_swirl_packet(packet())

    assert error.value.issue["code"] == (
        "stellar_swirl_packet_target_commit_mismatch")
    assert world.state() == before
    assert world.drain_generated_events(0) == before_events
    for target_id in ("enemy.0", "enemy.1"):
        target = world.get_target(target_id)
        assert target.crystallize_state.world is world.crystallize_world
        assert target.bloom_core_state is world.bloom_core_state


@pytest.mark.parametrize("pollution", ["clock", "journal", "reverse_registry"])
def test_instance_producer_wrapper_cannot_mutate_world_coordinator(
    pollution,
):
    world = world_with_targets()
    before = copy.deepcopy(world.state())
    before_events = world.drain_generated_events(0)
    original = world.special_runtime.replace_stellar_swirl_packet

    def corrupt_world(_runtime, *args, **kwargs):
        if pollution == "clock":
            world.time = 777
        elif pollution == "journal":
            world._generated_events.append({
                "type": "forged",
                "event_id": "forged:external",
            })
        else:
            world._targets_by_area[AREA].clear()
        return original(*args, **kwargs)

    world.special_runtime.replace_stellar_swirl_packet = MethodType(
        corrupt_world, world.special_runtime)

    with pytest.raises(ElementalWorldPending):
        world.attempt_stellar_swirl_packet(packet())

    assert world.state() == before
    assert world.drain_generated_events(0) == before_events
    assert world.targets_in_area(AREA) == ("enemy.0", "enemy.1")
    assert world.time == 0


def test_rollback_keeps_target_and_special_instance_wrappers_bound_live():
    world = world_with_targets()
    target = world.get_target("enemy.0")
    special = world.special_runtime
    original_advance = target.advance
    original_replace = special.replace_stellar_swirl_packet
    before = copy.deepcopy(world.state())

    def observed_advance(_target, time):
        return original_advance(time)

    def observed_replace(_special, *args, **kwargs):
        return original_replace(*args, **kwargs)

    target.advance = MethodType(observed_advance, target)
    special.replace_stellar_swirl_packet = MethodType(observed_replace, special)

    def abort(_world):
        target.time = 777
        special.time = 777
        raise ElementalWorldPending("fixture_abort", "controlled")

    with pytest.raises(ElementalWorldPending, match="controlled"):
        world.execute_atomic(abort)

    assert target.advance.__self__ is target
    assert special.replace_stellar_swirl_packet.__self__ is special
    assert target.time == special.time == 0
    assert world.state() == before


def test_generic_atomic_success_rejects_special_hidden_data():
    world = world_with_targets()
    special = world.special_runtime
    before = copy.deepcopy(world.state())

    def add_hidden(_world):
        special.forged_hidden_data = {"accepted": True}

    with pytest.raises(ElementalWorldPending) as error:
        world.execute_atomic(add_hidden)

    assert error.value.issue["code"] == "world_transaction_commit_invariant_mismatch"
    assert "special_instance_schema" in error.value.issue["violations"]
    assert not hasattr(special, "forged_hidden_data")
    assert world.state() == before


def test_successful_packet_keeps_preinstalled_special_wrapper_bound_live():
    world = world_with_targets()
    special = world.special_runtime
    original = SpecialReactionRuntime.replace_stellar_swirl_packet
    calls = []

    def observed_replace(_special, *args, **kwargs):
        calls.append(kwargs.get("area_id"))
        return original(_special, *args, **kwargs)

    wrapper = MethodType(observed_replace, special)
    special.replace_stellar_swirl_packet = wrapper

    receipt = world.attempt_stellar_swirl_packet(packet())

    assert receipt["packet_id"] == "attack.aoe:stellar-swirl-packet"
    assert calls == [AREA]
    assert special.replace_stellar_swirl_packet is wrapper
    assert special.replace_stellar_swirl_packet.__self__ is special


def test_generic_atomic_rejects_new_target_and_special_method_overrides():
    world = world_with_targets()
    target = world.get_target("enemy.0")
    special = world.special_runtime

    def forged_advance(_target, _time):
        return []

    def forged_deadline(_special):
        return None

    def add_allowed_names(_world):
        target.advance = MethodType(forged_advance, target)
        special.next_deadline = MethodType(forged_deadline, special)

    with pytest.raises(ElementalWorldPending) as error:
        world.execute_atomic(add_allowed_names)

    assert set(error.value.issue["violations"]) >= {
        "target_callable_overrides:enemy.0",
        "special_callable_overrides",
    }
    assert "advance" not in target.__dict__
    assert "next_deadline" not in special.__dict__


@pytest.mark.parametrize(
    ("root_name", "violation"),
    [
        ("world", "world_type"),
        ("target", "target_type:enemy.0"),
        ("special", "special_runtime_type"),
        ("crystallize", "crystallize_world_instance_schema:type"),
        ("bloom", "bloom_core_instance_schema:type"),
    ],
)
def test_generic_atomic_success_rejects_runtime_class_drift(
    root_name,
    violation,
):
    world = world_with_targets()
    roots = {
        "world": world,
        "target": world.get_target("enemy.0"),
        "special": world.special_runtime,
        "crystallize": world.crystallize_world,
        "bloom": world.bloom_core_state,
    }
    root = roots[root_name]
    original_type = type(root)
    drifted_type = type(
        f"Drifted{original_type.__name__}", (original_type,), {})
    before = copy.deepcopy(world.state())

    def drift(_world):
        root.__class__ = drifted_type

    with pytest.raises(ElementalWorldPending) as error:
        world.execute_atomic(drift)

    assert violation in error.value.issue["violations"]
    assert type(root) is original_type
    assert world.state() == before


def test_base_exception_rollback_restores_all_runtime_classes_and_nested_roots():
    class ControlledAbort(BaseException):
        pass

    world = world_with_targets()
    target = world.get_target("enemy.0")
    roots = tuple(root for root in (
        world,
        target,
        world.special_runtime,
        world.crystallize_world,
        world.bloom_core_state,
        target.multi_state,
        target.multi_state.freeze_state,
        target.crystallize_state,
    ) if root is not None)
    original_types = {id(root): type(root) for root in roots}
    before = copy.deepcopy(world.state())

    def drift_then_abort(_world):
        for root in roots:
            original_type = original_types[id(root)]
            root.__class__ = type(
                f"Drifted{original_type.__name__}",
                (original_type,),
                {},
            )
        target.time = 777
        world.special_runtime.time = 777
        raise ControlledAbort()

    with pytest.raises(ControlledAbort):
        world.execute_atomic(drift_then_abort)

    assert all(type(root) is original_types[id(root)] for root in roots)
    assert target.crystallize_state in roots
    assert world.state() == before


@pytest.mark.parametrize(
    ("field", "corrupt_value"),
    [
        ("trace", {}),
        ("scheduled", {}),
        ("state_by_target", []),
    ],
)
def test_generic_atomic_rejects_special_runtime_field_structure_drift(
    field,
    corrupt_value,
):
    world = world_with_targets()
    special = world.special_runtime
    before = copy.deepcopy(world.state())

    def corrupt(_world):
        setattr(special, field, corrupt_value)

    with pytest.raises(ElementalWorldPending) as error:
        world.execute_atomic(corrupt)

    assert (
        f"special_runtime_structure:{field}"
        in error.value.issue["violations"]
    )
    assert world.state() == before
