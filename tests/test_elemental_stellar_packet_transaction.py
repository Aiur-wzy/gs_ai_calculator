from __future__ import annotations

import copy

import pytest

from genshin_engine.elemental_stellar_packet import PACKET_CONTRACT_ID
from genshin_engine.elemental_world import ElementalWorldPending, ElementalWorldRuntime
from genshin_engine.special_reactions import (
    REQUIRED_CAPABILITY_TAGS,
    REVIEWED_SPECIAL_REACTION_PROFILES,
    SpecialReactionPending,
    SpecialReactionRuntime,
)

EVIDENCE = {"status": "assumed", "source": "controlled stellar packet fixture"}


def target_model(target_id: str, element: str = "cryo", owner: str = "cryo-owner") -> dict:
    return {
        "schema_version": 1,
        "ruleset": "gauge-multi-aura-v4",
        "target": {
            "id": target_id,
            "kind": "ordinary",
            "knowledge": "known",
            "initial_auras": [{
                "element": element,
                "remaining_gu": .8,
                "decay_gu_per_second": .001,
                "source": "controlled packet aura",
                "source_owner": owner,
            }],
            "freeze": {
                "freezable": False,
                "resistance": 0,
                "unfreezable_resolution": "coexist",
                **EVIDENCE,
            },
            **EVIDENCE,
        },
        "profiles": [{
            "id": "anemo",
            "element": "anemo",
            "application": "apply",
            "gauge_units": 1,
            "icd": {"kind": "none"},
            **EVIDENCE,
        }],
        "swirl_damage": {
            "icd": {"kind": "sequence", "reset_seconds": .5,
                    "sequence": [1, 1, 0]},
            **EVIDENCE,
        },
    }


def stellar_runtime(*, qualification_status: str = "confirmed") -> SpecialReactionRuntime:
    reviewed = REVIEWED_SPECIAL_REACTION_PROFILES["stellar_swirl"]
    tag = REQUIRED_CAPABILITY_TAGS["stellar_swirl"]
    return SpecialReactionRuntime(
        {
            "schema_version": 1,
            "replacements": {
                "stellar_swirl": {
                    "enabled": True,
                    "qualification": {
                        "status": qualification_status,
                        "provider_ids": ["provider"],
                        "capability_tag": tag,
                        "source": "controlled packet capability",
                    },
                    "mechanics": {
                        "status": "confirmed",
                        "source": "controlled reviewed packet profile",
                        "profile_id": reviewed["profile_id"],
                        "profile_revision": reviewed["profile_revision"],
                        "profile_content_hash": reviewed["profile_content_hash"],
                        **copy.deepcopy(reviewed["parameters"]),
                    },
                },
            },
        },
        party_member_ids=["provider", "wind", "cryo-owner", "fourth"],
        party_capability_tags={"provider": [tag]},
    )


def world(*targets: tuple[str, str, str], qualification_status="confirmed") -> ElementalWorldRuntime:
    value = ElementalWorldRuntime(
        special_runtime=stellar_runtime(qualification_status=qualification_status))
    for target_id, element, owner in targets:
        value.register_target(
            target_model(target_id, element, owner), area_ids=("arena",))
    return value


def child(target_id: str, order: int, size: int, *, parent="attack.aoe", time=0) -> dict:
    return {
        "event_id": f"run:{parent}:{target_id}",
        "stable_event_id": f"stable:{parent}:{target_id}",
        "action_id": "wind.action",
        "actor": "wind",
        "time_tick": time,
        "packet_child_order": order,
        "damage_element": "anemo",
        "application": {
            "profile_id": "anemo",
            "target_id": target_id,
            "landed": True,
            "area_id": "arena",
            "atomic_parent_id": parent,
            "application_packet_size": size,
        },
    }


def aura_gu(value: ElementalWorldRuntime, target_id: str) -> float:
    return value.get_target(target_id).state()["auras"][0]["remaining_gu"]


def test_same_parent_packet_commits_two_targets_and_creates_one_vortex():
    value = world(
        ("enemy-a", "cryo", "cryo-owner"),
        ("enemy-b", "cryo", "cryo-owner"),
    )
    receipt = value.attempt_stellar_swirl_packet([
        child("enemy-b", 1, 2),
        child("enemy-a", 0, 2),
    ])

    assert receipt["contract_id"] == PACKET_CONTRACT_ID
    assert receipt["target_ids"] == ["enemy-a", "enemy-b"]
    assert receipt["packet_id"] == "attack.aoe:stellar-swirl-packet"
    assert all(row["reaction"]["type"] == "stellar_swirl"
               for row in receipt["application_receipts"])
    assert aura_gu(value, "enemy-a") == pytest.approx(.3)
    assert aura_gu(value, "enemy-b") == pytest.approx(.3)
    area = value.special_runtime.state()["areas"]["arena"]
    assert area["stellar_swirl_count"] == 1
    immediate = [row for row in receipt["generated_events"]
                 if row.get("component") == "immediate_anemo"]
    assert [row["target_id"] for row in immediate] == ["enemy-a", "enemy-b"]
    replay = value.drain_generated_events(0)
    assert value.drain_generated_events(0) == replay
    assert replay["cursor"] == receipt["cursor"]


def test_single_child_uses_same_packet_contract_and_existing_vortex_counts_children():
    value = world(("enemy-a", "cryo", "cryo-owner"))
    first = value.attempt_stellar_swirl_packet([
        child("enemy-a", 0, 1, parent="attack.one"),
    ])
    assert first["child_count"] == 1
    assert value.special_runtime.state()["areas"]["arena"][
        "stellar_swirl_count"] == 1

    # Restore enough Cryo through the reviewed target state for a second
    # independent parent; this test is about the packet bridge, not an action
    # database producer.
    value.get_target("enemy-a").multi_state.add_or_refresh(
        "cryo", 1, "second-aura", "cryo-owner")
    second = value.attempt_stellar_swirl_packet([
        child("enemy-a", 0, 1, parent="attack.two"),
    ])
    assert second["child_count"] == 1
    assert value.special_runtime.state()["areas"]["arena"][
        "stellar_swirl_count"] == 2


def test_wrong_or_missing_child_reaction_rejects_and_restores_whole_world():
    value = world(
        ("enemy-a", "cryo", "cryo-owner"),
        ("enemy-b", "hydro", "cryo-owner"),
    )
    before = copy.deepcopy(value.state())
    with pytest.raises(ElementalWorldPending) as error:
        value.attempt_stellar_swirl_packet([
            child("enemy-a", 0, 2), child("enemy-b", 1, 2),
        ])
    assert error.value.issue["code"] == (
        "stellar_swirl_packet_child_not_unique_cryo_swirl")
    assert value.state() == before
    assert value.drain_generated_events() == {"events": [], "cursor": 0}


def test_any_child_special_pending_restores_all_targets_and_shared_special_state():
    value = world(
        ("enemy-a", "cryo", "cryo-owner"),
        # The trigger and aura owner collide only on the second child.
        ("enemy-b", "cryo", "wind"),
    )
    before = copy.deepcopy(value.state())
    with pytest.raises(SpecialReactionPending):
        value.attempt_stellar_swirl_packet([
            child("enemy-a", 0, 2), child("enemy-b", 1, 2),
        ])
    assert value.state() == before
    assert value.drain_generated_events() == {"events": [], "cursor": 0}


def test_complete_proposal_mismatch_during_commit_rolls_back_packet(monkeypatch):
    value = world(
        ("enemy-a", "cryo", "cryo-owner"),
        ("enemy-b", "cryo", "cryo-owner"),
    )
    before = copy.deepcopy(value.state())
    original = SpecialReactionRuntime.replace_stellar_swirl_packet

    def route_then_change_target(runtime, *args, **kwargs):
        result = original(runtime, *args, **kwargs)
        # Simulate a state change between preview and target commit.  Matching
        # only by reaction ID would incorrectly admit this packet.
        value.get_target("enemy-b").multi_state.consume("cryo", .1)
        return result

    monkeypatch.setattr(
        SpecialReactionRuntime, "replace_stellar_swirl_packet",
        route_then_change_target)
    with pytest.raises(ElementalWorldPending) as error:
        value.attempt_stellar_swirl_packet([
            child("enemy-a", 0, 2), child("enemy-b", 1, 2),
        ])
    assert error.value.issue["code"] == (
        "stellar_swirl_target_state_changed_before_commit")
    assert value.state() == before
    assert value.drain_generated_events() == {"events": [], "cursor": 0}


def test_packet_requires_explicit_time_alignment_and_area_membership():
    value = world(("enemy-a", "cryo", "cryo-owner"))
    late = child("enemy-a", 0, 1, time=1)
    with pytest.raises(ElementalWorldPending) as error:
        value.attempt_stellar_swirl_packet([late])
    assert error.value.issue["code"] == "stellar_swirl_packet_time_not_aligned"

    outside = child("enemy-a", 0, 1)
    outside["application"]["area_id"] = "unknown-area"
    with pytest.raises(ElementalWorldPending) as error:
        value.attempt_stellar_swirl_packet([outside])
    assert error.value.issue["code"] == "world_area_unknown"


def test_packet_size_and_deterministic_order_are_closed_contracts():
    value = world(
        ("enemy-a", "cryo", "cryo-owner"),
        ("enemy-b", "cryo", "cryo-owner"),
    )
    malformed = [child("enemy-a", 0, 3), child("enemy-b", 1, 3)]
    with pytest.raises(ValueError, match="application_packet_size"):
        value.attempt_stellar_swirl_packet(malformed)

    unordered = [child("enemy-a", 0, 2), child("enemy-b", 2, 2)]
    with pytest.raises(ValueError, match="连续"):
        value.attempt_stellar_swirl_packet(unordered)

    boolean_size = [child("enemy-a", 0, 1)]
    boolean_size[0]["application"]["application_packet_size"] = True
    with pytest.raises(ValueError, match="application_packet_size"):
        value.attempt_stellar_swirl_packet(boolean_size)


def test_router_decisions_cannot_be_swapped_between_children(monkeypatch):
    value = world(
        ("enemy-a", "cryo", "cryo-owner"),
        ("enemy-b", "cryo", "cryo-owner"),
    )
    before = copy.deepcopy(value.state())
    original = SpecialReactionRuntime.replace_stellar_swirl_packet

    def route_then_swap_decisions(runtime, *args, **kwargs):
        result = original(runtime, *args, **kwargs)
        result["decisions"] = list(reversed(result["decisions"]))
        return result

    monkeypatch.setattr(
        SpecialReactionRuntime, "replace_stellar_swirl_packet",
        route_then_swap_decisions)
    with pytest.raises(ElementalWorldPending) as error:
        value.attempt_stellar_swirl_packet([
            child("enemy-a", 0, 2), child("enemy-b", 1, 2),
        ])
    assert error.value.issue["code"] == (
        "stellar_swirl_packet_decision_identity_mismatch")
    assert value.state() == before
    assert value.drain_generated_events() == {"events": [], "cursor": 0}
