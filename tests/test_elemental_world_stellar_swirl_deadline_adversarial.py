"""Adversarial contracts for the world-owned Star-Swirl detonation consumer.

The producer is the shared :class:`SpecialReactionRuntime`; the consumer is
the multi-target :class:`ElementalWorldRuntime`.  These tests deliberately
corrupt the producer/consumer seam.  A valid implementation must consume the
three-second timeout and the sixth-reaction early detonation through the same
atomic batch path, select a stable area snapshot once, publish Cryo damage
before the generated 1U Cryo applications, and restore every participant on
any mismatch.
"""
from __future__ import annotations

import copy
from collections.abc import Callable
from types import MethodType

import pytest

import genshin_engine.elemental as elemental_module
from genshin_engine.elemental import ElementalRuntime
from genshin_engine.elemental_schema import MULTI_AURA_RULESET
from genshin_engine.elemental_world import ElementalWorldPending, ElementalWorldRuntime
from genshin_engine.special_reactions import (
    REQUIRED_CAPABILITY_TAGS,
    REVIEWED_SPECIAL_REACTION_PROFILES,
    SpecialReactionRuntime,
)

AREA = "arena.primary"
OTHER_AREA = "arena.other"
EVIDENCE = {"status": "assumed", "source": "stellar world adversarial fixture"}
PARTY = ["provider", "wind", "electro", "fourth"]


def aura(element: str, *, owner: str = "provider", gu: float = .8) -> dict:
    return {
        "element": element,
        "remaining_gu": gu,
        "decay_gu_per_second": .001,
        "source": "stellar world initial aura",
        "source_owner": owner,
    }


def target_model(
    target_id: str,
    *initial_auras: dict,
    knowledge: str = "known",
) -> dict:
    return {
        "schema_version": 1,
        "ruleset": MULTI_AURA_RULESET,
        "target": {
            "id": target_id,
            "kind": "ordinary",
            "knowledge": knowledge,
            "initial_auras": list(initial_auras),
            "freeze": {
                "freezable": False,
                "resistance": 0,
                "unfreezable_resolution": "coexist",
                **EVIDENCE,
            },
            **EVIDENCE,
        },
        "profiles": [
            {
                "id": element,
                "element": element,
                "application": "apply",
                "gauge_units": 1,
                "icd": {"kind": "none"},
                **EVIDENCE,
            }
            for element in (
                "pyro", "hydro", "cryo", "electro", "dendro", "anemo", "geo"
            )
        ],
        "swirl_damage": {
            "icd": {"kind": "sequence", "reset_seconds": .5, "sequence": [1, 1, 0]},
            **EVIDENCE,
        },
    }


def special_runtime(*enabled: str) -> SpecialReactionRuntime:
    replacements = {}
    tags = []
    for reaction_type in enabled:
        reviewed = REVIEWED_SPECIAL_REACTION_PROFILES[reaction_type]
        replacements[reaction_type] = {
            "enabled": True,
            "qualification": {
                "status": "confirmed",
                "provider_ids": ["provider"],
                "capability_tag": REQUIRED_CAPABILITY_TAGS[reaction_type],
                "source": "stellar world reviewed capability",
            },
            "mechanics": {
                "status": "confirmed",
                "source": "stellar world reviewed profile",
                "profile_id": reviewed["profile_id"],
                "profile_revision": reviewed["profile_revision"],
                "profile_content_hash": reviewed["profile_content_hash"],
                **copy.deepcopy(reviewed["parameters"]),
            },
        }
        tags.append(REQUIRED_CAPABILITY_TAGS[reaction_type])
    return SpecialReactionRuntime(
        {"schema_version": 1, "replacements": replacements},
        party_member_ids=PARTY,
        party_capability_tags={"provider": tags},
    )


def swirl_proposal(index: int, *, target_id: str = "seed") -> dict:
    return {
        "id": f"seed.swirl.{index}",
        "type": "swirl",
        "aura_element": "cryo",
        "trigger_element": "anemo",
        "owner": "wind",
        "action_id": f"wind.seed.{index}",
        "trigger_owner": "wind",
        "aura_owner": "provider",
        "contributor_ids": ["provider", "wind"],
        "target_id": target_id,
        "parent_event_id": f"seed.attack.{index}",
        "commit_state": "proposal",
        "area_id": AREA,
        "atomic_parent_id": f"seed.attack.{index}",
        "application_packet_size": 1,
    }


def superconduct_proposal(*, target_id: str = "seed") -> dict:
    return {
        "id": "seed.superconduct",
        "type": "superconduct",
        "aura_element": "cryo",
        "trigger_element": "electro",
        "owner": "electro",
        "target_id": target_id,
        "parent_event_id": "seed.superconduct.attack",
        "atomic_parent_id": "seed.superconduct.attack",
        "area_id": AREA,
        "commit_state": "proposal",
        "contributor_ids": ["provider", "electro"],
    }


def lunar_electro_proposal(target_id: str) -> dict:
    return {
        "id": "seed.lunar-electro",
        "type": "electro_charged",
        "aura_element": "hydro",
        "trigger_element": "electro",
        "owner": "electro",
        "target_id": target_id,
        "parent_event_id": "seed.lunar-electro.attack",
        "atomic_parent_id": "seed.lunar-electro.attack",
        "area_id": AREA,
        "commit_state": "proposal",
        "contributor_ids": ["provider", "electro"],
    }


def world_with_targets(
    targets: list[tuple[str, tuple[dict, ...], str]],
    *,
    enabled: tuple[str, ...] = ("stellar_swirl",),
) -> ElementalWorldRuntime:
    value = ElementalWorldRuntime(special_runtime=special_runtime(*enabled))
    for target_id, initial, knowledge in targets:
        value.register_target(
            target_model(target_id, *initial, knowledge=knowledge),
            area_ids=(AREA,),
        )
    return value


def seed_vortex(
    world: ElementalWorldRuntime,
    *,
    count: int = 1,
    area_id: str = AREA,
) -> None:
    for index in range(1, count + 1):
        proposal = swirl_proposal(index)
        proposal["area_id"] = area_id
        world.special_runtime.replace_stellar_swirl_packet(
            [proposal],
            time=world.time,
            area_id=area_id,
            parent_application_id=proposal["atomic_parent_id"],
        )


def packet_child(target_id: str, *, parent: str = "finish.attack") -> dict:
    return {
        "event_id": f"finish:{target_id}",
        "stable_event_id": f"finish:stable:{target_id}",
        "action_id": "wind.finish",
        "actor": "wind",
        "time_tick": 0,
        "packet_child_order": 0,
        "damage_element": "anemo",
        "application": {
            "profile_id": "anemo",
            "target_id": target_id,
            "landed": True,
            "area_id": AREA,
            "atomic_parent_id": parent,
            "application_packet_size": 1,
        },
    }


def generated_since(world: ElementalWorldRuntime, cursor: int = 0) -> list[dict]:
    return world.drain_generated_events(cursor)["events"]


def area_damage(rows: list[dict]) -> dict:
    return next(
        row
        for row in rows
        if row.get("type") == "special_reaction_damage_request"
        and row.get("reaction_type") in {
            "stellar_swirl_cryo_lv2",
            "stellar_swirl_cryo_lv3",
        }
    )


def generated_applications(rows: list[dict]) -> list[dict]:
    return [
        row
        for row in rows
        if row.get("type") == "elemental_application"
        and ":application:target:" in row.get("event_id", "")
    ]


def reaction_types(application: dict) -> list[str]:
    reactions = application.get("reactions")
    if isinstance(reactions, list):
        return [row.get("type") for row in reactions]
    reaction = application.get("reaction")
    return [reaction.get("type")] if isinstance(reaction, dict) else []


def assert_world_unchanged(
    world: ElementalWorldRuntime,
    before: dict,
    before_events: dict,
    identities: tuple[object, ...],
) -> None:
    assert world.state() == before
    assert world.drain_generated_events(0) == before_events
    assert world.special_runtime is identities[0]
    assert world.crystallize_world is identities[1]
    assert world.bloom_core_state is identities[2]
    for target_id, runtime in zip(sorted(world.state()["target_ids"]), identities[3:], strict=True):
        assert world.get_target(target_id) is runtime


def rollback_probe(world: ElementalWorldRuntime) -> tuple[dict, dict, tuple[object, ...]]:
    return (
        copy.deepcopy(world.state()),
        world.drain_generated_events(0),
        (
            world.special_runtime,
            world.crystallize_world,
            world.bloom_core_state,
            *(world.get_target(target_id) for target_id in sorted(world.state()["target_ids"])),
        ),
    )


def test_three_second_timeout_uses_one_stable_area_snapshot_and_damage_precedes_applications():
    world = world_with_targets([
        ("enemy.b", (aura("pyro", owner="fourth"),), "known"),
        ("enemy.a", (), "known"),
    ])
    seed_vortex(world)
    identities = (
        world.special_runtime,
        world.crystallize_world,
        world.bloom_core_state,
        world.get_target("enemy.a"),
        world.get_target("enemy.b"),
    )

    receipt = world.advance(3_000_000)
    rows = generated_since(world)
    damage = area_damage(rows)
    applications = generated_applications(rows)

    assert receipt["time_tick"] == 3_000_000
    assert damage["resolved_target_ids"] == ["enemy.a", "enemy.b"]
    assert isinstance(damage.get("selection_audit_id"), str)
    assert [row["target_id"] for row in applications] == ["enemy.a", "enemy.b"]
    assert rows.index(damage) < min(rows.index(row) for row in applications)
    assert all(row["application_gu"] == 1 for row in applications)
    assert all(row["icd_audit"]["mode"] == "none" for row in applications)
    assert all(row["direct_hit"] is False for row in applications)
    assert len({row["atomic_parent_id"] for row in applications}) == 1
    assert len({row["event_id"] for row in rows}) == len(rows)
    assert world.drain_generated_events(0) == world.drain_generated_events(0)
    assert world.drain_generated_events(0)["cursor"] == len(rows)
    assert world.special_runtime is identities[0]
    assert world.crystallize_world is identities[1]
    assert world.bloom_core_state is identities[2]
    assert world.get_target("enemy.a") is identities[3]
    assert world.get_target("enemy.b") is identities[4]


def test_reverse_registration_order_keeps_target_projection_and_event_ids_stable():
    def execute(order: tuple[str, str]) -> list[dict]:
        specs = {
            "enemy.a": ("enemy.a", (), "known"),
            "enemy.b": ("enemy.b", (), "known"),
        }
        world = world_with_targets([specs[target_id] for target_id in order])
        seed_vortex(world)
        world.advance(3_000_000)
        return generated_since(world)

    forward = execute(("enemy.a", "enemy.b"))
    reverse = execute(("enemy.b", "enemy.a"))

    assert forward == reverse
    assert [row["target_id"] for row in generated_applications(forward)] == [
        "enemy.a",
        "enemy.b",
    ]


def test_pyro_aura_does_not_retroactively_melt_vortex_damage_but_application_does():
    world = world_with_targets([
        ("enemy.pyro", (aura("pyro", owner="fourth"),), "known"),
    ])
    seed_vortex(world)

    world.advance(3_000_000)
    rows = generated_since(world)
    damage = area_damage(rows)
    application = generated_applications(rows)[0]

    assert damage["application"] is None
    assert "reaction" not in damage
    assert "reactions" not in damage
    assert reaction_types(application) == ["melt"]
    assert rows.index(damage) < rows.index(application)


def test_active_stellar_superconduct_field_replaces_electro_reactions_and_counts_parent_once():
    world = world_with_targets(
        [
            ("enemy.b", (aura("electro", owner="electro"),), "known"),
            ("enemy.a", (aura("electro", owner="electro"),), "known"),
        ],
        enabled=("stellar_swirl", "stellar_superconduct"),
    )
    world.special_runtime.replace(superconduct_proposal(), time=0)
    seed_vortex(world)

    world.advance(3_000_000)
    rows = generated_since(world)
    applications = generated_applications(rows)
    layer_receipts = [
        row
        for row in rows
        if row.get("reaction_type") == "stellar_superconduct"
        and row.get("operation") == "count_successful_application"
    ]

    assert [reaction_types(row) for row in applications] == [
        ["stellar_superconduct"],
        ["stellar_superconduct"],
    ]
    assert len({row["atomic_parent_id"] for row in layer_receipts}) == 1
    assert sum(row["counted"] is True for row in layer_receipts) == 1
    assert {row["reason"] for row in layer_receipts} == {
        "layer_added",
        "atomic_parent_already_counted",
    }
    for application in applications:
        target = world.get_target(application["target_id"])
        traced = next(
            row for row in target.trace
            if row.get("event_id") == application["event_id"]
        )
        assert traced == application
        assert traced["stellar_superconduct_application"] in layer_receipts
        assert traced["after"] == target.state()
    field = world.special_runtime.state()["stellar_areas"][AREA]
    assert field["stellar_superconduct_layers"] == 1


def test_detonation_that_establishes_polestar_field_does_not_count_an_origin_layer():
    world = world_with_targets(
        [
            ("enemy.a", (aura("electro", owner="electro"),), "known"),
            ("enemy.b", (aura("electro", owner="electro"),), "known"),
        ],
        enabled=("stellar_swirl", "stellar_superconduct"),
    )
    seed_vortex(world)

    world.advance(3_000_000)
    rows = generated_since(world)
    layer_receipts = [
        row
        for row in rows
        if row.get("reaction_type") == "stellar_superconduct"
        and row.get("operation") == "count_successful_application"
    ]
    field = world.special_runtime.state()["stellar_areas"][AREA]

    assert field["stellar_superconduct_active"] is True
    assert field["stellar_superconduct_layers"] == 0
    assert layer_receipts
    assert all(row["counted"] is False for row in layer_receipts)
    assert {row["reason"] for row in layer_receipts} == {
        "establishing_application_excluded"
    }


def test_sixth_reaction_early_detonation_uses_the_same_consumer_immediately():
    world = world_with_targets([
        ("enemy", (aura("cryo", owner="provider", gu=2),), "known"),
    ])
    seed_vortex(world, count=5)
    cursor = world.drain_generated_events(0)["cursor"]

    receipt = world.attempt_stellar_swirl_packet([packet_child("enemy")])
    rows = generated_since(world, cursor)
    damage = area_damage(rows)
    applications = generated_applications(rows)

    assert receipt["status"] == "executed"
    assert damage["reaction_type"] == "stellar_swirl_cryo_lv3"
    assert damage["time_tick"] == 0
    assert [row["target_id"] for row in applications] == ["enemy"]
    assert rows.index(damage) < rows.index(applications[0])
    vortex = world.special_runtime.state()["areas"][AREA]
    assert vortex["stellar_swirl_count"] == 0
    assert vortex["stellar_swirl_detonate_at"] is None
    assert world.special_runtime.next_deadline() is None


def test_packet_preview_rejects_an_unconsumed_current_world_deadline():
    world = world_with_targets([("enemy", (aura("cryo", gu=2),), "known")])
    seed_vortex(world)
    world.special_runtime.vortex_by_area[AREA]["stellar_swirl_detonate_at"] = 0
    before, before_events, identities = rollback_probe(world)

    with pytest.raises(ElementalWorldPending) as error:
        world.attempt_stellar_swirl_packet([packet_child("enemy")])

    assert error.value.issue["code"] == "stellar_swirl_packet_deadline_not_consumed"
    assert_world_unchanged(world, before, before_events, identities)


def _first(rows: list[dict], row_type: str) -> dict:
    return next(row for row in rows if row.get("type") == row_type)


def _remove_type(row_type: str) -> Callable[[list[dict]], None]:
    def mutate(rows: list[dict]) -> None:
        rows[:] = [row for row in rows if row.get("type") != row_type]

    return mutate


def _remove_pair(rows: list[dict]) -> None:
    rows[:] = [
        row for row in rows
        if row.get("type") not in {
            "special_reaction_damage_request",
            "special_reaction_application_request",
        }
    ]


def _remove_detonation_triplet(rows: list[dict]) -> None:
    rows[:] = [
        row for row in rows
        if not (
            (row.get("type") == "special_reaction_state"
             and row.get("operation") == "detonate_vortex")
            or row.get("type") in {
                "special_reaction_damage_request",
                "special_reaction_application_request",
            }
        )
    ]


def _change_both_owners(rows: list[dict]) -> None:
    _first(rows, "special_reaction_damage_request")["owner"] = "fourth"
    _first(rows, "special_reaction_application_request")["owner"] = "fourth"


def _duplicate_type(row_type: str) -> Callable[[list[dict]], None]:
    def mutate(rows: list[dict]) -> None:
        duplicate = copy.deepcopy(_first(rows, row_type))
        duplicate["event_id"] += ":forged-extra"
        rows.append(duplicate)
        rows.sort(key=lambda row: (row["time_tick"], row["event_id"]))

    return mutate


def _change(row_type: str, key: str, value) -> Callable[[list[dict]], None]:
    def mutate(rows: list[dict]) -> None:
        _first(rows, row_type)[key] = copy.deepcopy(value)

    return mutate


def _change_profile(row_type: str) -> Callable[[list[dict]], None]:
    def mutate(rows: list[dict]) -> None:
        _first(rows, row_type)["profile"]["profile_revision"] += 1

    return mutate


def _change_role(key: str, value) -> Callable[[list[dict]], None]:
    def mutate(rows: list[dict]) -> None:
        _first(rows, "special_reaction_damage_request")[
            "role_provenance"][key] = copy.deepcopy(value)

    return mutate


@pytest.mark.parametrize(
    "mutate",
    [
        _remove_type("special_reaction_damage_request"),
        _remove_type("special_reaction_application_request"),
        _remove_pair,
        _remove_detonation_triplet,
        _duplicate_type("special_reaction_damage_request"),
        _duplicate_type("special_reaction_application_request"),
        _duplicate_type("special_reaction_state"),
        _change(
            "special_reaction_application_request",
            "parent_damage_event_id",
            "forged.parent",
        ),
        _change("special_reaction_damage_request", "area_id", OTHER_AREA),
        _change("special_reaction_application_request", "area_id", OTHER_AREA),
        _change("special_reaction_damage_request", "time_tick", 2_999_999),
        _change("special_reaction_application_request", "time_tick", 2_999_999),
        _change("special_reaction_damage_request", "t", 2.999999),
        _change("special_reaction_application_request", "t", 2.999999),
        _change("special_reaction_damage_request", "event_id", "forged.damage"),
        _change("special_reaction_application_request", "event_id", "forged.application"),
        _change("special_reaction_damage_request", "reaction_type", "stellar_swirl_cryo_lv3"),
        _change("special_reaction_damage_request", "component", "forged_component"),
        _change("special_reaction_damage_request", "target_id", "enemy"),
        _change("special_reaction_damage_request", "owner", "forged.owner"),
        _change("special_reaction_application_request", "owner", "forged.owner"),
        _change_both_owners,
        _change(
            "special_reaction_application_request",
            "action_id",
            "forged.action",
        ),
        _change(
            "special_reaction_damage_request",
            "contributor_sample_policy",
            "forged_policy",
        ),
        _change(
            "special_reaction_application_request",
            "contributor_ids",
            ["forged.owner"],
        ),
        _change(
            "special_reaction_application_request",
            "source",
            "forged source",
        ),
        _change("special_reaction_state", "count", 0),
        _change_role("contract", "forged-role-contract"),
        _change("special_reaction_application_request", "gauge_units", .5),
        _change("special_reaction_application_request", "icd", {"kind": "sequence"}),
        _change("special_reaction_application_request", "application_order", "before_damage"),
        _change("special_reaction_application_request", "element", "hydro"),
        _change("special_reaction_application_request", "direct_hit", True),
        _change(
            "special_reaction_application_request",
            "counts_toward_stellar_superconduct",
            False,
        ),
        _change_profile("special_reaction_damage_request"),
        _change_profile("special_reaction_application_request"),
        _change(
            "special_reaction_damage_request",
            "target_selection_policy",
            "event_time_nearby_entities_in_area",
        ),
        _change(
            "special_reaction_application_request",
            "target_selection_policy",
            "event_time_nearby_entities_in_area",
        ),
    ],
    ids=(
        "missing-damage",
        "missing-application",
        "missing-damage-and-application",
        "missing-entire-triplet",
        "extra-damage",
        "extra-application",
        "extra-detonation-state",
        "parent",
        "damage-area",
        "application-area",
        "damage-time",
        "application-time",
        "damage-t",
        "application-t",
        "damage-id",
        "application-id",
        "damage-reaction-type",
        "damage-component",
        "damage-target",
        "damage-owner",
        "application-owner",
        "internally-consistent-wrong-owner",
        "application-action-id",
        "damage-contributor-policy",
        "application-contributors",
        "application-source",
        "state-count",
        "role-contract",
        "one-unit",
        "icd",
        "order",
        "application-element",
        "application-direct-hit",
        "application-layer-eligibility",
        "damage-profile",
        "application-profile",
        "damage-nearby",
        "application-nearby",
    ),
)
def test_corrupted_detonation_pair_fails_closed_and_restores_every_identity(
    monkeypatch,
    mutate: Callable[[list[dict]], None],
):
    world = world_with_targets([("enemy", (), "known")])
    seed_vortex(world)
    before, before_events, identities = rollback_probe(world)
    original = SpecialReactionRuntime.advance_world_pre_expiry

    def corrupt(runtime, time_tick):
        rows = original(runtime, time_tick)
        mutate(rows)
        return rows

    monkeypatch.setattr(SpecialReactionRuntime, "advance_world_pre_expiry", corrupt)
    with pytest.raises((ElementalWorldPending, ValueError)):
        world.advance(3_000_000)

    assert_world_unchanged(world, before, before_events, identities)


def test_unknown_area_is_not_treated_as_an_empty_area():
    world = world_with_targets([("enemy", (), "known")])
    seed_vortex(world, area_id=OTHER_AREA)
    before, before_events, identities = rollback_probe(world)

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(3_000_000)

    assert error.value.issue["code"] in {
        "world_area_unknown",
        "world_stellar_swirl_area_unknown",
    }
    assert_world_unchanged(world, before, before_events, identities)


def test_second_target_pending_restores_first_target_special_state_trace_and_cursor():
    world = world_with_targets([
        ("enemy.a", (), "known"),
        ("enemy.b", (), "unknown"),
    ])
    seed_vortex(world)
    before, before_events, identities = rollback_probe(world)
    target_traces = {
        target_id: copy.deepcopy(world.get_target(target_id).trace)
        for target_id in ("enemy.a", "enemy.b")
    }
    special_trace = copy.deepcopy(world.special_runtime.trace)

    with pytest.raises(ElementalWorldPending):
        world.advance(3_000_000)

    assert_world_unchanged(world, before, before_events, identities)
    assert {
        target_id: world.get_target(target_id).trace
        for target_id in ("enemy.a", "enemy.b")
    } == target_traces
    assert world.special_runtime.trace == special_trace


def test_second_target_proposal_drift_restores_first_target_and_shared_special_state(
    monkeypatch,
):
    world = world_with_targets([
        ("enemy.a", (aura("pyro", owner="fourth"),), "known"),
        ("enemy.b", (aura("pyro", owner="fourth"),), "known"),
    ])
    seed_vortex(world)
    before, before_events, identities = rollback_probe(world)
    original = ElementalRuntime._attempt_with_world_reaction_routes

    def drift_before_commit(runtime, *args, **kwargs):
        if runtime.target == "enemy.b":
            runtime.multi_state.add_or_refresh(
                "pyro", 1, "adversarial proposal drift", "fourth"
            )
        return original(runtime, *args, **kwargs)

    monkeypatch.setattr(
        ElementalRuntime,
        "_attempt_with_world_reaction_routes",
        drift_before_commit,
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(3_000_000)

    assert error.value.issue["code"] in {
        "world_reaction_proposal_changed",
        "world_stellar_swirl_target_preview_mismatch",
        "world_stellar_swirl_target_state_changed_before_commit",
        "world_stellar_swirl_target_commit_mismatch",
    }
    assert_world_unchanged(world, before, before_events, identities)


def test_world_log_collision_rolls_back_target_application_and_vortex_state():
    world = world_with_targets([("enemy", (), "known")])
    seed_vortex(world)
    parent = world.special_runtime.state()["areas"][AREA][
        "stellar_swirl_parent_reaction_id"
    ]
    request_id = parent + ":detonation:cryo:lv2:application"
    collision_id = request_id + ":target:0:enemy"
    world.publish_generated_events([
        {"type": "collision_fixture", "event_id": collision_id}
    ])
    before, before_events, identities = rollback_probe(world)

    with pytest.raises(ValueError, match="ID"):
        world.advance(3_000_000)

    assert_world_unchanged(world, before, before_events, identities)


def test_same_microsecond_lunar_tick_and_vortex_application_require_explicit_order():
    world = world_with_targets(
        [
            (
                "enemy",
                (
                    aura("hydro", owner="provider"),
                    aura("electro", owner="electro"),
                ),
                "known",
            ),
        ],
        enabled=("stellar_swirl", "lunar_electro"),
    )
    seed_vortex(world)
    world.advance(2_750_000)
    world.special_runtime.replace(
        lunar_electro_proposal("enemy"),
        time=2_750_000,
    )
    before, before_events, identities = rollback_probe(world)

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(3_000_000)

    assert error.value.issue["code"] in {
        "world_special_simultaneous_order_required",
        "world_stellar_swirl_simultaneous_order_required",
    }
    assert_world_unchanged(world, before, before_events, identities)


def test_commit_receipt_tamper_and_publication_failure_are_both_atomic(monkeypatch):
    world = world_with_targets([("enemy", (), "known")])
    seed_vortex(world)
    before, before_events, identities = rollback_probe(world)
    original = ElementalRuntime._attempt_with_world_reaction_routes

    def tamper_receipt(runtime, *args, **kwargs):
        receipt = original(runtime, *args, **kwargs)
        receipt["application_gu"] = .5
        return receipt

    monkeypatch.setattr(
        ElementalRuntime,
        "_attempt_with_world_reaction_routes",
        tamper_receipt,
    )
    with pytest.raises((ElementalWorldPending, ValueError)):
        world.advance(3_000_000)
    assert_world_unchanged(world, before, before_events, identities)

    monkeypatch.setattr(
        ElementalRuntime,
        "_attempt_with_world_reaction_routes",
        original,
    )

    def reject_publish(_events):
        raise ValueError("forced world log publication failure")

    monkeypatch.setattr(world, "publish_generated_events", reject_publish)
    with pytest.raises(ValueError, match="forced world log"):
        world.advance(3_000_000)
    assert_world_unchanged(world, before, before_events, identities)


def test_live_commit_reaction_tamper_differs_from_routed_preview_and_rolls_back(
    monkeypatch,
):
    world = world_with_targets([
        ("enemy", (aura("pyro", owner="fourth"),), "known"),
    ])
    seed_vortex(world)
    before, before_events, identities = rollback_probe(world)
    original = ElementalRuntime._attempt_with_world_reaction_routes
    call_count = 0

    def tamper_live_commit(runtime, *args, **kwargs):
        nonlocal call_count
        receipt = original(runtime, *args, **kwargs)
        call_count += 1
        if call_count == 2:
            receipt["reactions"][0]["type"] = "forged_reaction"
        return receipt

    monkeypatch.setattr(
        ElementalRuntime,
        "_attempt_with_world_reaction_routes",
        tamper_live_commit,
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(3_000_000)

    assert error.value.issue["code"] == "world_stellar_swirl_target_commit_mismatch"
    assert_world_unchanged(world, before, before_events, identities)


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda runtime: runtime.party_state.__setitem__(
            "lunar_bloom_dew", 999),
        lambda runtime: runtime.stellar_field_by_area.__setitem__(
            "forged.area", {"forged": True}),
    ],
    ids=("party-state", "unrelated-stellar-area"),
)
def test_deadline_producer_cannot_pollute_unrelated_special_domains(
    monkeypatch,
    corrupt,
):
    world = world_with_targets([("enemy", (), "known")])
    seed_vortex(world)
    before, before_events, identities = rollback_probe(world)
    original = SpecialReactionRuntime.advance_world_pre_expiry

    def corrupted(runtime, time_tick):
        rows = original(runtime, time_tick)
        corrupt(runtime)
        return rows

    monkeypatch.setattr(
        SpecialReactionRuntime,
        "advance_world_pre_expiry",
        corrupted,
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(3_000_000)

    assert error.value.issue["code"] == "world_special_deadline_producer_mismatch"
    assert_world_unchanged(world, before, before_events, identities)


def test_instance_deadline_producer_cannot_append_to_world_journal():
    world = world_with_targets([("enemy", (), "known")])
    seed_vortex(world)
    before, before_events, identities = rollback_probe(world)
    original = world.special_runtime.advance_world_pre_expiry

    def corrupt_world(_runtime, time_tick):
        world._generated_events.append({
            "type": "forged",
            "event_id": "forged:deadline-wrapper",
        })
        return original(time_tick)

    world.special_runtime.advance_world_pre_expiry = MethodType(
        corrupt_world,
        world.special_runtime,
    )

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(3_000_000)

    assert error.value.issue["code"] == (
        "world_transaction_commit_invariant_mismatch")
    assert_world_unchanged(world, before, before_events, identities)


def test_target_profile_and_extra_trace_tamper_are_inside_commit_boundary(
    monkeypatch,
):
    world = world_with_targets([("enemy", (), "known")])
    seed_vortex(world)
    before, before_events, identities = rollback_probe(world)
    before_profiles = copy.deepcopy(world.get_target("enemy").profiles)
    original = ElementalRuntime._attempt_with_world_reaction_routes

    def corrupted(runtime, *args, **kwargs):
        receipt = original(runtime, *args, **kwargs)
        profile_id = next(
            key for key in runtime.profiles if key.startswith("@special-application:"))
        runtime.profiles[profile_id]["gauge_units"] = 999
        runtime.trace.append({"type": "forged_trace"})
        return receipt

    monkeypatch.setattr(
        ElementalRuntime,
        "_attempt_with_world_reaction_routes",
        corrupted,
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(3_000_000)

    assert error.value.issue["code"] in {
        "world_stellar_swirl_target_preview_mismatch",
        "world_stellar_swirl_target_commit_mismatch",
    }
    assert_world_unchanged(world, before, before_events, identities)
    assert world.get_target("enemy").profiles == before_profiles


def test_special_route_candidate_receipt_and_unrelated_state_are_exact(
    monkeypatch,
):
    world = world_with_targets(
        [("enemy", (aura("electro", owner="electro", gu=2),), "known")],
        enabled=("stellar_swirl", "stellar_superconduct"),
    )
    seed_vortex(world)
    before, before_events, identities = rollback_probe(world)
    original = SpecialReactionRuntime.replace

    def corrupted(runtime, *args, **kwargs):
        result = original(runtime, *args, **kwargs)
        runtime.party_state["lunar_bloom_dew"] = 999
        result["generated_events"].append({
            "type": "forged_special_event",
            "event_id": "forged.special.route",
            "time_tick": kwargs["time"],
        })
        return result

    monkeypatch.setattr(SpecialReactionRuntime, "replace", corrupted)
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(3_000_000)

    assert error.value.issue["code"] == (
        "world_stellar_swirl_route_producer_mismatch")
    assert_world_unchanged(world, before, before_events, identities)


def test_special_route_reference_isolates_shared_internal_helper(
    monkeypatch,
):
    world = world_with_targets(
        [("enemy", (aura("electro", owner="electro", gu=2),), "known")],
        enabled=("stellar_swirl", "stellar_superconduct"),
    )
    world.special_runtime.replace(superconduct_proposal(), time=0)
    seed_vortex(world)
    before, before_events, identities = rollback_probe(world)
    original = SpecialReactionRuntime._profile_audit

    def pollute_shared_helper(runtime, reaction_type):
        result = original(runtime, reaction_type)
        if reaction_type == "stellar_superconduct":
            runtime.party_state["lunar_bloom_dew"] = 777
        return result

    monkeypatch.setattr(
        SpecialReactionRuntime,
        "_profile_audit",
        pollute_shared_helper,
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(3_000_000)

    assert error.value.issue["code"] in {
        "world_stellar_swirl_route_producer_mismatch",
        "world_stellar_swirl_layer_producer_mismatch",
    }
    assert_world_unchanged(world, before, before_events, identities)


def test_deadline_expectation_helper_runs_on_an_isolated_stable_reference(
    monkeypatch,
):
    world = world_with_targets([("enemy", (), "known")])
    seed_vortex(world)
    before, before_events, identities = rollback_probe(world)
    original = SpecialReactionRuntime._stellar_swirl_detonation

    def corrupted(runtime, *args, **kwargs):
        runtime.party_state["lunar_bloom_dew"] = 777
        return original(runtime, *args, **kwargs)

    monkeypatch.setattr(
        SpecialReactionRuntime,
        "_stellar_swirl_detonation",
        corrupted,
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(3_000_000)

    assert error.value.issue["code"] == "world_special_deadline_producer_mismatch"
    assert_world_unchanged(world, before, before_events, identities)


def test_routed_preview_after_must_equal_its_actual_target_state(monkeypatch):
    world = world_with_targets([("enemy", (), "known")])
    seed_vortex(world)
    before, before_events, identities = rollback_probe(world)
    original = ElementalRuntime._attempt_with_world_reaction_routes

    def forge_after(runtime, *args, **kwargs):
        receipt = original(runtime, *args, **kwargs)
        receipt["after"]["knowledge"] = "forged"
        runtime.trace[-1]["after"]["knowledge"] = "forged"
        return receipt

    monkeypatch.setattr(
        ElementalRuntime,
        "_attempt_with_world_reaction_routes",
        forge_after,
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(3_000_000)

    assert error.value.issue["code"] == (
        "world_stellar_swirl_target_preview_mismatch")
    assert_world_unchanged(world, before, before_events, identities)


def test_live_receipt_after_must_equal_live_target_state(monkeypatch):
    world = world_with_targets([("enemy", (), "known")])
    seed_vortex(world)
    before, before_events, identities = rollback_probe(world)
    original = ElementalRuntime._attempt_with_world_reaction_routes
    call_count = 0

    def forge_live_after(runtime, *args, **kwargs):
        nonlocal call_count
        receipt = original(runtime, *args, **kwargs)
        call_count += 1
        if call_count == 2:
            receipt["after"]["knowledge"] = "forged"
            runtime.trace[-1]["after"]["knowledge"] = "forged"
        return receipt

    monkeypatch.setattr(
        ElementalRuntime,
        "_attempt_with_world_reaction_routes",
        forge_live_after,
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(3_000_000)

    assert error.value.issue["code"] == (
        "world_stellar_swirl_target_commit_mismatch")
    assert_world_unchanged(world, before, before_events, identities)


def test_dynamic_routed_preview_cannot_share_deterministic_model_pollution(
    monkeypatch,
):
    world = world_with_targets([("enemy", (), "known")])
    seed_vortex(world)
    before, before_events, identities = rollback_probe(world)
    original = ElementalRuntime._attempt_with_world_reaction_routes

    def pollute_model(runtime, *args, **kwargs):
        receipt = original(runtime, *args, **kwargs)
        runtime.model["forged_world_route_state"] = {"accepted": True}
        return receipt

    monkeypatch.setattr(
        ElementalRuntime,
        "_attempt_with_world_reaction_routes",
        pollute_model,
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(3_000_000)

    assert error.value.issue["code"] == (
        "world_stellar_swirl_target_preview_mismatch")
    assert_world_unchanged(world, before, before_events, identities)


def test_dynamic_route_receipt_and_trace_cannot_share_reaction_forgery(
    monkeypatch,
):
    world = world_with_targets([
        ("enemy", (aura("pyro", owner="fourth"),), "known"),
    ])
    seed_vortex(world)
    before, before_events, identities = rollback_probe(world)
    original = ElementalRuntime._attempt_with_world_reaction_routes

    def forge_reaction(runtime, *args, **kwargs):
        receipt = original(runtime, *args, **kwargs)
        receipt["reactions"][0]["type"] = "forged_reaction"
        receipt["reaction"] = receipt["reactions"][0]
        runtime.trace[-1]["reactions"][0]["type"] = "forged_reaction"
        runtime.trace[-1]["reaction"] = runtime.trace[-1]["reactions"][0]
        return receipt

    monkeypatch.setattr(
        ElementalRuntime,
        "_attempt_with_world_reaction_routes",
        forge_reaction,
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(3_000_000)

    assert error.value.issue["code"] == (
        "world_stellar_swirl_target_preview_mismatch")
    assert_world_unchanged(world, before, before_events, identities)


def test_reference_target_owns_an_isolated_mutable_reaction_table(monkeypatch):
    world = world_with_targets([
        ("enemy", (aura("pyro", owner="fourth"),), "known"),
    ])
    seed_vortex(world)
    before, before_events, identities = rollback_probe(world)
    monkeypatch.setitem(
        elemental_module._REACTIONS,
        ("pyro", "cryo"),
        ("forged_melt", 999.0, .5),
    )

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(3_000_000)

    assert error.value.issue["code"] == (
        "world_stellar_swirl_target_preview_mismatch")
    assert_world_unchanged(world, before, before_events, identities)


def test_reference_target_copy_strips_instance_method_wrappers_and_stays_frozen():
    world = world_with_targets([("enemy", (), "known")])
    runtime = world.get_target("enemy")

    def forged_route(_runtime, *args, **kwargs):
        raise AssertionError((args, kwargs))

    runtime._attempt_with_world_reaction_routes = MethodType(
        forged_route, runtime)
    reference = world._reference_target_copy(runtime)
    nested = reference.preview_copy()

    assert "_attempt_with_world_reaction_routes" not in reference.__dict__
    assert "_attempt_with_world_reaction_routes" not in nested.__dict__
    assert type(nested) is type(reference)
    frozen_route = type(reference).__dict__["_attempt_with_world_reaction_routes"]
    assert (reference._attempt_with_world_reaction_routes.__func__
            is frozen_route)
    assert (nested._attempt_with_world_reaction_routes.__func__
            is frozen_route)
    assert (frozen_route
            is not ElementalRuntime.__dict__["_attempt_with_world_reaction_routes"])


def test_deadline_reference_isolates_shared_advance_helper_poison(monkeypatch):
    world = world_with_targets([("enemy", (), "known")])
    seed_vortex(world)
    before, before_events, identities = rollback_probe(world)
    original = SpecialReactionRuntime._advance_deadlines

    def corrupted(runtime, *args, **kwargs):
        rows = original(runtime, *args, **kwargs)
        runtime.party_state["lunar_bloom_dew"] = 777
        return rows

    monkeypatch.setattr(
        SpecialReactionRuntime,
        "_advance_deadlines",
        corrupted,
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(3_000_000)

    assert error.value.issue["code"] == "world_special_deadline_producer_mismatch"
    assert_world_unchanged(world, before, before_events, identities)


@pytest.mark.parametrize("field", ["crystallize_state", "bloom_core_state"])
def test_deadline_live_target_cannot_rebind_shared_state_objects(
    monkeypatch,
    field,
):
    world = world_with_targets([("enemy", (), "known")])
    seed_vortex(world)
    before, before_events, identities = rollback_probe(world)
    original = ElementalRuntime._attempt_with_world_reaction_routes

    def rebind_shared_state(runtime, *args, **kwargs):
        receipt = original(runtime, *args, **kwargs)
        setattr(runtime, field, copy.deepcopy(getattr(runtime, field)))
        return receipt

    monkeypatch.setattr(
        ElementalRuntime,
        "_attempt_with_world_reaction_routes",
        rebind_shared_state,
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(3_000_000)

    assert error.value.issue["code"] == (
        "world_stellar_swirl_target_commit_mismatch")
    assert_world_unchanged(world, before, before_events, identities)
    target = world.get_target("enemy")
    assert target.crystallize_state.world is world.crystallize_world
    assert target.bloom_core_state is world.bloom_core_state


def test_layer_binding_helper_cannot_silently_drop_public_application_record(
    monkeypatch,
):
    world = world_with_targets(
        [("enemy", (aura("electro", owner="electro", gu=2),), "known")],
        enabled=("stellar_swirl", "stellar_superconduct"),
    )
    world.special_runtime.replace(superconduct_proposal(), time=0)
    seed_vortex(world)
    before, before_events, identities = rollback_probe(world)

    def no_binding(*args, **kwargs):
        return None

    monkeypatch.setattr(
        ElementalWorldRuntime,
        "_bind_stellar_layer_to_target_receipt",
        staticmethod(no_binding),
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(3_000_000)

    assert error.value.issue["code"] == (
        "world_stellar_swirl_target_reference_invalid")
    assert_world_unchanged(world, before, before_events, identities)


@pytest.mark.parametrize("mode", ["receipt", "runtime"])
def test_layer_binding_helper_cannot_mutate_other_public_target_state(
    monkeypatch,
    mode,
):
    world = world_with_targets(
        [("enemy", (aura("electro", owner="electro", gu=2),), "known")],
        enabled=("stellar_swirl", "stellar_superconduct"),
    )
    world.special_runtime.replace(superconduct_proposal(), time=0)
    seed_vortex(world)
    before, before_events, identities = rollback_probe(world)
    original = ElementalWorldRuntime._bind_stellar_layer_to_target_receipt

    def corrupt_binding(runtime, receipt, layer_event, **kwargs):
        original(runtime, receipt, layer_event, **kwargs)
        if mode == "receipt":
            receipt["reactions"][0]["type"] = "forged_special"
            receipt["reaction"] = receipt["reactions"][0]
            runtime.trace[-1]["reactions"][0]["type"] = "forged_special"
            runtime.trace[-1]["reaction"] = (
                runtime.trace[-1]["reactions"][0])
        else:
            runtime.model["forged_layer_binding_state"] = True

    monkeypatch.setattr(
        ElementalWorldRuntime,
        "_bind_stellar_layer_to_target_receipt",
        staticmethod(corrupt_binding),
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(3_000_000)

    assert error.value.issue["code"] == (
        "world_stellar_swirl_target_reference_invalid")
    assert_world_unchanged(world, before, before_events, identities)
