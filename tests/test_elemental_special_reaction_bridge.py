from __future__ import annotations

import copy

import pytest

from genshin_engine.elemental import ElementalPending, ElementalRuntime
from genshin_engine.elemental_schema import MULTI_AURA_RULESET
from genshin_engine.special_reactions import (
    REQUIRED_CAPABILITY_TAGS,
    REVIEWED_SPECIAL_REACTION_PROFILES,
    SpecialReactionRuntime,
)

EVIDENCE = {"status": "assumed", "source": "special bridge adversarial fixture"}
PARTY = ["provider", "aura-a", "aura-b", "actor"]


def aura(element: str, gu: float, owner: str) -> dict:
    return {
        "element": element,
        "remaining_gu": gu,
        "decay_gu_per_second": .001,
        "source": "bridge initial aura",
        "source_owner": owner,
    }


def elemental_model(initial=()) -> dict:
    return {
        "schema_version": 1,
        "ruleset": MULTI_AURA_RULESET,
        "target": {
            "id": "enemy", "kind": "ordinary", "knowledge": "known",
            "initial_auras": list(initial),
            "freeze": {"freezable": False, "resistance": 0,
                       "unfreezable_resolution": "coexist", **EVIDENCE},
            **EVIDENCE,
        },
        "profiles": [
            {"id": element, "element": element, "application": "apply",
             "gauge_units": 1, "icd": {"kind": "none"}, **EVIDENCE}
            for element in (
                "pyro", "hydro", "cryo", "electro", "dendro", "anemo", "geo")
        ],
        "swirl_damage": {
            "icd": {"kind": "sequence", "reset_seconds": .5,
                    "sequence": [1, 1, 0]},
            **EVIDENCE,
        },
    }


def special_runtime(*enabled: str, qualification_status="confirmed") -> SpecialReactionRuntime:
    replacements = {}
    tags = []
    for reaction_type in enabled:
        reviewed = REVIEWED_SPECIAL_REACTION_PROFILES[reaction_type]
        parameters = copy.deepcopy(reviewed["parameters"])
        status = "confirmed"
        if reaction_type == "lunar_crystallize":
            parameters["harmony_attack_cooldown_seconds"] = .2
            parameters["harmony_attack_cooldown_scope"] = "area"
            status = "assumed"
        mechanics = {
            "status": status,
            "source": "bridge reviewed profile",
            "profile_id": reviewed["profile_id"],
            "profile_revision": reviewed["profile_revision"],
            "profile_content_hash": reviewed["profile_content_hash"],
            **parameters,
        }
        if status == "assumed":
            mechanics["override_reason"] = "explicit Harmony attack cooldown fixture"
        replacements[reaction_type] = {
            "enabled": True,
            "qualification": {
                "status": qualification_status,
                "provider_ids": ["provider"],
                "capability_tag": REQUIRED_CAPABILITY_TAGS[reaction_type],
                "source": "bridge database capability",
            },
            "mechanics": mechanics,
        }
        tags.append(REQUIRED_CAPABILITY_TAGS[reaction_type])
    return SpecialReactionRuntime(
        {"schema_version": 1, "replacements": replacements},
        party_member_ids=PARTY,
        party_capability_tags={"provider": tags},
    )


def runtime(initial, *enabled, qualification_status="confirmed") -> ElementalRuntime:
    special = (special_runtime(*enabled, qualification_status=qualification_status)
               if enabled else None)
    return ElementalRuntime(elemental_model(initial), special_runtime=special)


def attempt(rt: ElementalRuntime, element: str, *, time=0, event_id=None,
            actor="actor", area_id="battlefield.primary", parent=None,
            packet_size=1, direct_hit=True):
    event_id = event_id or element
    return rt.attempt(
        event_id=event_id,
        stable_event_id=event_id,
        action_id="action",
        actor=actor,
        time=time,
        application={
            "profile_id": element,
            "target_id": "enemy",
            "landed": True,
            "area_id": area_id,
            "atomic_parent_id": parent or event_id,
            "application_packet_size": packet_size,
            "direct_hit": direct_hit,
        },
        trigger_stat_sample=({
            "contract": "ordinary-crystallize-trigger-stat-v1",
            "character": actor, "phase": "reaction_created",
            "time_tick": time, "level": 90, "em": 0.0,
            "source": "special bridge controlled trigger sample",
        } if element == "geo" else None),
    )


def layers(rt: ElementalRuntime) -> dict[str, float]:
    return {row["element"]: row["remaining_gu"]
            for row in rt.state()["auras"]}


def test_stellar_superconduct_routes_before_commit_and_counts_only_later_application():
    rt = runtime([aura("electro", .8, "provider")], "stellar_superconduct")
    first = attempt(rt, "cryo", event_id="establish", parent="attack.1")
    assert first["reaction"]["type"] == "stellar_superconduct"
    assert first["reaction"]["route"] == "special"
    assert "electro" not in layers(rt)
    field = rt.state()["special_reactions"]["stellar_areas"]["battlefield.primary"]
    assert field["stellar_superconduct_layers"] == 0
    assert first["stellar_superconduct_application"]["reason"] == (
        "establishing_application_excluded")

    second = attempt(rt, "electro", time=1_000_000,
                     event_id="later", parent="attack.2")
    assert second["stellar_superconduct_application"]["counted"] is True
    field = rt.state()["special_reactions"]["stellar_areas"]["battlefield.primary"]
    assert field["stellar_superconduct_layers"] == 1
    rt.advance(4_000_000)
    snapshot = rt.special_runtime.stellar_superconduct_snapshot()
    assert snapshot["layers"] == 1
    assert snapshot["system_factor"] == pytest.approx(1.45)


def test_active_stellar_field_counts_a_later_superconduct_trigger_application():
    rt = runtime([aura("electro", .8, "provider")], "stellar_superconduct")
    attempt(rt, "cryo", event_id="establish", parent="attack.1")
    # Restore the opposite aura with a successful application in the active
    # field, then trigger Superconduct again.  Both are later applications;
    # the refresh reaction must not be mistaken for another establishing hit.
    attempt(rt, "electro", time=500_000, event_id="prime", parent="attack.2")
    refreshed = attempt(
        rt, "cryo", time=1_000_000, event_id="refresh", parent="attack.3")
    assert refreshed["reaction"]["type"] == "stellar_superconduct"
    assert refreshed["stellar_superconduct_application"]["counted"] is True
    field = rt.state()["special_reactions"]["stellar_areas"]["battlefield.primary"]
    assert field["stellar_superconduct_layers"] == 2


def test_stellar_swirl_suppresses_ordinary_swirl_route_and_emits_native_requests():
    rt = runtime([aura("cryo", .8, "provider")], "stellar_swirl")
    row = attempt(rt, "anemo", event_id="wind")
    assert row["reaction"]["type"] == "stellar_swirl"
    assert row["reaction"]["ordinary_damage_suppressed"] is True
    assert layers(rt)["cryo"] == pytest.approx(.3)
    events = rt.drain_generated_events()
    immediate = [event for event in events
                 if event.get("component") == "immediate_anemo"]
    assert len(immediate) == 1
    assert not [event for event in events
                if event.get("type") == "reaction_damage_attempt"]


def test_multi_target_stellar_swirl_declaration_fails_closed_without_packet_dispatcher():
    rt = runtime([aura("cryo", .8, "provider")], "stellar_swirl")
    before = copy.deepcopy(rt.state())
    with pytest.raises(ElementalPending) as exc:
        attempt(rt, "anemo", event_id="aoe", parent="attack.aoe", packet_size=2)
    assert exc.value.issue["code"] == "stellar_swirl_packet_dispatch_required"
    assert rt.state() == before
    assert rt.drain_generated_events() == []


def test_vortex_cryo_application_reenters_elemental_runtime_and_reacts_later():
    rt = runtime([aura("cryo", .8, "provider")], "stellar_swirl")
    attempt(rt, "anemo", event_id="wind", parent="attack.wind")
    rt.drain_generated_events()
    rt.advance(3_000_000)
    events = rt.drain_generated_events()
    request = next(row for row in events
                   if row.get("type") == "special_reaction_application_request")
    assert request["application_order"] == "after_damage"
    assert request["gauge_units"] == 1
    assert request["direct_hit"] is False
    assert request["counts_toward_stellar_superconduct"] is True
    descriptor = rt.register_special_application(request, target_id="enemy")
    assert descriptor["counts_toward_stellar_superconduct"] is True
    applied = rt.attempt(
        event_id=request["event_id"], stable_event_id=request["event_id"],
        action_id=request["action_id"], actor=request["owner"],
        time=request["time_tick"], application=descriptor)
    assert applied["element"] == "cryo"
    assert applied["direct_hit"] is False
    assert layers(rt)["cryo"] == pytest.approx(.8)
    melted = attempt(rt, "pyro", time=3_000_001, event_id="follow-up")
    assert melted["reaction"]["type"] == "melt"
    assert melted["reaction"]["aura_element"] == "cryo"


def test_vortex_cryo_application_reacts_and_counts_one_active_polestar_layer():
    rt = runtime(
        [aura("electro", .8, "provider")],
        "stellar_superconduct", "stellar_swirl")
    attempt(rt, "cryo", event_id="field.establish", parent="attack.establish")
    attempt(rt, "cryo", time=100_000, event_id="cryo.prime", parent="attack.cryo")
    # Keep the wind trigger and the currently active Cryo provider distinct;
    # same-character dual-role ordering is intentionally fail-closed.
    attempt(rt, "anemo", time=200_000, event_id="vortex.create",
            parent="attack.wind", actor="aura-b")
    # Clear the residual Cryo, then leave Electro for the vortex's generated
    # 1U Cryo application to react with when the three-second timer expires.
    attempt(rt, "pyro", time=300_000, event_id="clear.cryo", parent="attack.pyro")
    attempt(rt, "electro", time=400_000, event_id="electro.prime", parent="attack.electro")
    rt.drain_generated_events()
    before = rt.state()["special_reactions"]["stellar_areas"]["battlefield.primary"][
        "stellar_superconduct_layers"]
    rt.advance(3_200_000)
    request = next(
        row for row in rt.drain_generated_events()
        if row.get("type") == "special_reaction_application_request")
    descriptor = rt.register_special_application(request, target_id="enemy")
    applied = rt.attempt(
        event_id=request["event_id"], stable_event_id=request["event_id"],
        action_id=request["action_id"], actor=request["owner"],
        time=request["time_tick"], application=descriptor)
    after = rt.state()["special_reactions"]["stellar_areas"]["battlefield.primary"][
        "stellar_superconduct_layers"]
    assert applied["reaction"]["type"] == "stellar_superconduct"
    assert applied["stellar_superconduct_application"]["counted"] is True
    assert applied["direct_hit"] is False
    assert applied["counts_toward_stellar_superconduct"] is True
    assert after == before + 1


def test_lunar_electro_retains_dual_aura_and_tick_checks_live_gauge():
    rt = runtime([aura("hydro", .8, "provider")], "lunar_electro")
    row = attempt(rt, "electro", event_id="electro")
    assert row["reaction"]["type"] == "lunar_electro"
    assert set(layers(rt)) == {"hydro", "electro"}
    rt.drain_generated_events()
    rt.advance(250_000)
    after = layers(rt)
    assert after["hydro"] == pytest.approx(.8-.00025-.4)
    assert after["electro"] == pytest.approx(.8-.25*(.8/9.5)-.4)
    events = rt.drain_generated_events()
    admission = next(event for event in events
                     if event.get("type") == "special_reaction_tick_admission")
    assert admission["allowed"] is True
    assert admission["owner_residual_contract"] == (
        "same-element-owner-residual-max-v1")
    assert admission["consumed_contributor_ids"] == ["provider", "actor"]
    assert admission["contributor_allocations_before"]["hydro"][0][
        "remaining_gu"] == pytest.approx(.8-.00025)
    assert admission["contributor_allocations_after"]["hydro"][0][
        "remaining_gu"] == pytest.approx(.8-.00025-.4)
    assert sum(event.get("type") == "special_reaction_damage_request"
               for event in events) == 1
    rt.advance(2_250_000)
    events = rt.drain_generated_events()
    assert any(event.get("type") == "special_reaction_tick_admission"
               and not event["allowed"] for event in events)


def test_lunar_electro_snapshot_preserves_owner_residual_beyond_recency_window():
    rt = runtime([
        aura("hydro", .8, "provider"), aura("electro", .8, "aura-a")],
        "lunar_electro")
    rt.advance(5_000_001)
    live = rt._lunar_electro_aura_snapshot()
    assert live["owner_residual_contract"] == "same-element-owner-residual-max-v1"
    assert live["components"]["hydro"]["contributor_ids"] == ["provider"]
    assert live["components"]["electro"]["contributor_ids"] == ["aura-a"]
    attempt(rt, "hydro", time=5_000_001, event_id="recent.hydro", actor="actor")
    refreshed = rt._lunar_electro_aura_snapshot()
    assert refreshed["components"]["hydro"]["contributor_ids"] == [
        "provider", "actor"]
    assert refreshed["components"]["electro"]["contributor_ids"] == ["aura-a"]


def test_lunar_electro_stale_positive_owner_is_pending_and_advance_rolls_back():
    rt = runtime([aura("hydro", .8, "provider")], "lunar_electro")
    attempt(rt, "electro", event_id="cloud")
    rt.drain_generated_events()
    # Isolate a reviewed adversarial boundary: a still-positive strong aura
    # whose only cloud tick occurs just outside the five-second contributor
    # recency window.  ElementalRuntime must reject the whole state advance;
    # it cannot consume the aura or leave the scheduled tick half-committed.
    tick = next(row for row in rt.special_runtime.scheduled
                if row.get("reaction_type") == "lunar_electro")
    tick["time_tick"] = 5_000_001
    tick["t"] = 5.000001
    rt.special_runtime.scheduled = [tick]
    before = copy.deepcopy(rt.state())
    generated_before = copy.deepcopy(rt.generated_events)
    history_before = copy.deepcopy((rt.trace, rt.special_runtime.trace))

    with pytest.raises(ElementalPending) as error:
        rt.advance(5_000_001)

    assert error.value.issue["code"] == "lunar_electro_contributor_recency_conflict"
    assert rt.state() == before
    assert rt.generated_events == generated_before
    assert (rt.trace, rt.special_runtime.trace) == history_before


def test_lunar_bloom_uses_ordinary_gauge_and_creates_real_core_lifecycle():
    rt = runtime([aura("hydro", .8, "provider")], "lunar_bloom")
    row = attempt(rt, "dendro", event_id="bloom")
    assert row["reaction"]["type"] == "lunar_bloom"
    assert row["reaction"]["consumed_gu"] == pytest.approx(.8)
    assert row["bloom_core_created"]["id"].endswith(":core")
    assert len(rt.state()["bloom_cores"]["cores"]) == 1
    rt.drain_generated_events()
    rt.advance(6_000_000)
    events = rt.drain_generated_events()
    assert any(event.get("type") == "ordinary_bloom_damage_request"
               and event["reason"] == "natural_expiry" for event in events)
    assert rt.state()["bloom_cores"]["cores"] == []


def test_lunar_bloom_hydro_trigger_uses_half_cost_without_leaving_trigger_aura():
    """Bloom keeps the asymmetric ordinary gauge rule in the special route.

    One Hydro trigger against a Dendro aura consumes only 0.5 GU from the
    aura.  The unused trigger gauge must not reverse-apply Hydro after the
    reaction; it still creates exactly one real Bloom core.
    """
    rt = runtime([aura("dendro", .8, "provider")], "lunar_bloom")
    row = attempt(rt, "hydro", event_id="reverse-bloom")
    assert row["reaction"]["type"] == "lunar_bloom"
    assert row["reaction"]["consumed_gu"] == pytest.approx(.5)
    assert row["reaction"]["trigger_gu_after"] == pytest.approx(0)
    assert layers(rt) == pytest.approx({"dendro": .3})
    assert len(rt.state()["bloom_cores"]["cores"]) == 1


def test_queue_deadline_merges_special_ticks_and_real_core_expiry():
    electro = runtime([aura("hydro", .8, "provider")], "lunar_electro")
    attempt(electro, "electro", event_id="cloud")
    deadline = electro.next_state_deadline()
    assert deadline["time"] == 250_000
    assert deadline["state_kind"] == "special_reaction"

    bloom = runtime([aura("hydro", .8, "provider")], "lunar_bloom")
    attempt(bloom, "dendro", event_id="core")
    # Dew generation at 2.5s wins before the ordinary core's 6s expiry.
    deadline = bloom.next_state_deadline()
    assert deadline["time"] == 2_500_000
    assert deadline["state_kind"] == "special_reaction"
    bloom.advance(2_500_000)
    bloom.drain_generated_events()
    assert bloom.next_state_deadline()["time"] == 6_000_000
    assert bloom.next_state_deadline()["state_kind"] == "bloom_core"


def test_lunar_crystallize_gcd_blocked_electro_keeps_full_geo_for_hydro_replacement():
    rt = runtime([
        aura("electro", .2, "aura-a"), aura("hydro", .8, "provider")],
        "lunar_crystallize")
    rt.crystallize_state.next_reaction_time = 1_000_000
    row = attempt(rt, "geo", event_id="geo.blocked")
    assert [entry["reason"] for entry in row["crystallize_attempts"]] == [
        "crystallize_gcd", "special_replacement_bypasses_crystallize_gcd"]
    assert [reaction["type"] for reaction in row["reactions"]] == [
        "lunar_crystallize"]
    assert row["reactions"][0]["reaction_trigger_tags"] == [
        "crystallize", "lunar_crystallize"]
    assert row["reactions"][0]["ordinary_side_effects_committed"] is False
    assert layers(rt)["electro"] == pytest.approx(.2)
    assert layers(rt)["hydro"] == pytest.approx(.3)
    assert row["crystallize_shards"] == []


def test_lunar_crystallize_small_electro_then_hydro_uses_remaining_geo_once():
    rt = runtime([
        aura("electro", .2, "aura-a"), aura("hydro", .8, "provider")],
        "lunar_crystallize")
    row = attempt(rt, "geo", event_id="geo.split")
    assert [reaction["type"] for reaction in row["reactions"]] == [
        "crystallize", "lunar_crystallize"]
    # The ordinary Electro branch is one real Crystallize occurrence.  The
    # Hydro replacement is one Lunar Crystallize occurrence that additionally
    # exposes the broad Crystallize trigger tag; no duplicate reaction is made.
    assert row["reactions"][1]["reaction_trigger_tags"] == [
        "crystallize", "lunar_crystallize"]
    assert len(row["reactions"]) == 2
    assert row["reactions"][0]["consumed_gu"] == pytest.approx(.2)
    assert row["reactions"][1]["consumed_gu"] == pytest.approx(.3)
    assert "electro" not in layers(rt)
    assert layers(rt)["hydro"] == pytest.approx(.5)
    assert len(row["crystallize_shards"]) == 1
    assert row["crystallize_shards"][0]["element"] == "electro"


@pytest.mark.parametrize(("electro_gu", "hydro_gu", "expected"), [
    (.2, .8, {"hydro": .5}),
    (.8, .2, {"electro": .3, "hydro": .2}),
])
def test_electro_charged_dual_aura_swirl_keeps_fixed_electro_then_hydro_route(
        electro_gu, hydro_gu, expected):
    rt = runtime([
        aura("electro", electro_gu, "aura-a"),
        aura("hydro", hydro_gu, "provider")])
    row = attempt(rt, "anemo", event_id="dual-swirl")
    assert [reaction["reaction_element"] for reaction in row["reactions"]] == (
        ["electro", "hydro"] if electro_gu < .5 else ["electro"])
    actual = layers(rt)
    assert set(actual) == set(expected)
    for element, gu in expected.items():
        assert actual[element] == pytest.approx(gu)


def test_special_pending_becomes_elemental_pending_and_rolls_back_all_state():
    rt = runtime([aura("electro", .8, "provider")],
                 "stellar_superconduct", qualification_status="pending")
    before = copy.deepcopy(rt.state())
    generated_before = rt.drain_generated_events()
    assert generated_before == []
    with pytest.raises(ElementalPending) as exc:
        attempt(rt, "cryo", event_id="pending")
    assert exc.value.issue["code"] == "special_qualification_pending"
    assert rt.state() == before
    assert rt.drain_generated_events() == []


def test_preview_has_isolated_special_and_core_state():
    rt = runtime([aura("hydro", .8, "provider")], "lunar_bloom")
    preview = rt.preview_copy()
    attempt(preview, "dendro", event_id="preview-bloom")
    assert len(preview.state()["bloom_cores"]["cores"]) == 1
    assert rt.state()["bloom_cores"]["cores"] == []
    assert rt.state()["special_reactions"]["party"]["lunar_bloom_backlog"] == 0
