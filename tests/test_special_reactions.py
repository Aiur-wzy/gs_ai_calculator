from __future__ import annotations

import copy
import json

import pytest

from genshin_engine.constants import REACTION_BASE_VALUE
from genshin_engine.special_reactions import (
    LUNAR_STELLAR_WEIGHTS,
    REQUIRED_CAPABILITY_TAGS,
    REVIEWED_SPECIAL_REACTION_PROFILES,
    SpecialReactionPending,
    SpecialReactionRuntime,
    calculate_direct_special_damage,
    calculate_weighted_special_damage,
    direct_lunar_damage_profile_content_hash,
    resolve_special_damage_event,
    special_reaction_capabilities,
    validate_direct_lunar_damage_profile,
    validate_special_reaction_model,
)

ELEMENTS = {
    "stellar_superconduct": ("cryo", "electro", "superconduct"),
    "stellar_swirl": ("cryo", "anemo", "swirl"),
    "lunar_electro": ("hydro", "electro", "electro_charged"),
    "lunar_bloom": ("hydro", "dendro", "bloom"),
    "lunar_crystallize": ("hydro", "geo", "crystallize"),
}


def qualification(reaction_type: str, status: str = "confirmed") -> dict:
    return {
        "status": status,
        "provider_ids": ["qualification.provider"],
        "capability_tag": REQUIRED_CAPABILITY_TAGS[reaction_type],
        "source": "test database capability projection",
    }


def mechanics(reaction_type: str, status: str | None = None, **overrides) -> dict:
    reviewed = REVIEWED_SPECIAL_REACTION_PROFILES[reaction_type]
    parameters = copy.deepcopy(reviewed["parameters"])
    if reaction_type == "lunar_crystallize":
        # Exact Harmony attack cooldown/scope remains an unresolved native
        # boundary.  Deterministic state-machine tests use an explicit,
        # auditable assumption rather than treating it as reviewed fact.
        parameters["harmony_attack_cooldown_seconds"] = .2
        parameters["harmony_attack_cooldown_scope"] = "area"
    parameters.update(overrides)
    has_delta = parameters != reviewed["parameters"]
    if status is None:
        status = "assumed" if has_delta else "confirmed"
    value = {
        "status": status,
        "source": "reviewed test mechanism profile",
        "profile_id": reviewed["profile_id"],
        "profile_revision": reviewed["profile_revision"],
        "profile_content_hash": reviewed["profile_content_hash"],
        **parameters,
    }
    if status == "assumed":
        value["override_reason"] = "test assumption for unresolved parameter"
    return value


def model(*enabled: str, qualification_status="confirmed", mechanics_status=None) -> dict:
    return {
        "schema_version": 1,
        "replacements": {
            reaction_type: {
                "enabled": True,
                "qualification": qualification(reaction_type, qualification_status),
                "mechanics": mechanics(reaction_type, mechanics_status),
            }
            for reaction_type in enabled
        },
    }


def runtime(*enabled: str, value: dict | None = None, with_tags=True) -> SpecialReactionRuntime:
    value = value if value is not None else model(*enabled)
    tags = {
        "qualification.provider": [REQUIRED_CAPABILITY_TAGS[name] for name in enabled]
    } if with_tags else {}
    return SpecialReactionRuntime(
        value,
        party_member_ids=["qualification.provider", "trigger.actor", "consumer", "fourth"],
        party_capability_tags=tags,
    )


def reaction(reaction_type: str, index: int = 1, target="enemy",
             parent: str | None = None, **extra) -> dict:
    aura, trigger, ordinary = ELEMENTS[reaction_type]
    value = {
        "id": f"ordinary.{index}",
        "type": ordinary,
        "aura_element": aura,
        "trigger_element": trigger,
        "owner": "trigger.actor",
        "target_id": target,
        "parent_event_id": parent or f"hit.{index}",
        "commit_state": "proposal",
    }
    if reaction_type == "stellar_swirl":
        value.update(
            trigger_owner="trigger.actor",
            aura_owner="qualification.provider",
            contributor_ids=["qualification.provider", "trigger.actor"],
        )
    value.update(extra)
    return value


def contributor(name, *, level=90, em=0, crit_rate=0, crit_dmg=0, **extra):
    return {
        "id": name, "level": level, "em": em,
        "crit_rate": crit_rate, "crit_dmg": crit_dmg,
        "source_refs": [{"kind": "test", "id": name}], **extra,
    }


def direct_lunar_profile(reaction_type="lunar_bloom", *, status="assumed",
                         **parameter_overrides):
    elements = {
        "lunar_electro": "electro",
        "lunar_bloom": "dendro",
        "lunar_crystallize": "geo",
    }
    parameters = {
        "reaction_type": reaction_type,
        "element": elements[reaction_type],
        "multiplier": 2.4,
        "scaling_stat": "em",
        "count": 2,
        "component": f"reviewed_ability_direct_{reaction_type}",
        **parameter_overrides,
    }
    value = {
        "profile_id": f"ability.test.direct-{reaction_type}.v2",
        "profile_revision": 1,
        "profile_content_hash": "0"*64,
        "authority": ("reviewed_database_projection" if status == "confirmed"
                      else "explicit_assumption"),
        "status": status,
        "source": ("test reviewed ability projection" if status == "confirmed"
                   else "explicit test ability projection assumption"),
        "source_refs": [{"kind": "test_database", "id": "ability.test"}],
        "parameters": parameters,
        "ability_binding": {
            "character_id": "consumer",
            "ability_id": "consumer.skill",
            "component_id": parameters["component"],
            "variant_id": "default",
            "effective_level": 10,
            "database_fingerprint": "1" * 64,
        },
    }
    value["profile_content_hash"] = direct_lunar_damage_profile_content_hash(value)
    return value


def direct_lunar_bloom_profile(*, status="assumed", **parameter_overrides):
    """Compatibility fixture imported by older Mechanics projection tests."""
    return direct_lunar_profile(
        "lunar_bloom", status=status, **parameter_overrides)


def stellar_swirl_roles(*, policy="vortex_cryo_best_then_anemo_best",
                        participants=("cryo", "anemo"),
                        anemo=("anemo",), cryo=("cryo",)):
    return {
        "contract": "stellar-swirl-role-provenance-v1",
        "component_policy": policy,
        "participant_ids": list(participants),
        "anemo_trigger_ids": list(anemo),
        "cryo_applier_ids": list(cryo),
        "status": "ready",
    }


def damage_events(receipt, reaction_type=None):
    rows = [row for row in receipt["generated_events"]
            if row["type"] == "special_reaction_damage_request"]
    return [row for row in rows if reaction_type is None or row["reaction_type"] == reaction_type]


def lunar_electro_aura_snapshot(*, time_tick=250_000, hydro=1.0, electro=.5):
    values = {
        "hydro": (hydro, "qualification.provider"),
        "electro": (electro, "trigger.actor"),
    }
    return {
        "knowledge": "known",
        "owner_residual_contract": "same-element-owner-residual-max-v1",
        "sample_time_tick": time_tick,
        "components": {
            element: {
                "remaining_gu": remaining,
                "contributor_ids": [owner] if remaining > 0 else [],
                "contributor_allocations": ([{
                    "owner": owner, "remaining_gu": remaining,
                    "last_application_at": 0.0, "source": f"{element}.fixture",
                }] if remaining > 0 else []),
                "owner_residual_status": "tracked",
                "decay_gu_per_second": .001,
            }
            for element, (remaining, owner) in values.items()
        },
    }


def test_default_is_disabled_and_input_is_not_mutated():
    value = model()
    original = copy.deepcopy(value)
    rt = SpecialReactionRuntime(value)
    ordinary = reaction("lunar_electro")
    receipt = rt.replace(ordinary, time=0)
    assert receipt["decision"]["decision"] == "ordinary"
    assert receipt["reaction"]["commit_state"] == "committed"
    assert receipt["reaction"]["route"] == "ordinary"
    assert ordinary["commit_state"] == "proposal"
    assert value == original


def test_disabled_draft_is_inert():
    value = {"schema_version": 1, "replacements": {
        "lunar_electro": {"enabled": False, "qualification": {"status": "pending"}}}}
    assert validate_special_reaction_model(value) == []
    assert SpecialReactionRuntime(value).replace(
        reaction("lunar_electro"), time=0)["decision"]["reason"] == "switch_disabled"


@pytest.mark.parametrize("reaction_type", ELEMENTS)
def test_all_five_replacements_require_switch_roster_and_database_capability(reaction_type):
    rt = runtime(reaction_type)
    receipt = rt.replace(reaction(reaction_type), time=0)
    assert receipt["reaction"]["type"] == reaction_type
    assert receipt["reaction"]["route"] == "special"
    assert receipt["reaction"]["ordinary_side_effects_committed"] is False
    assert receipt["decision"]["decision"] == "replace"
    assert receipt["reaction"]["profile"]["proven_provider_ids"] == ["qualification.provider"]


def test_roster_must_be_bound_and_provider_must_be_present():
    unbound = SpecialReactionRuntime(model("lunar_electro"))
    with pytest.raises(SpecialReactionPending) as exc:
        unbound.replace(reaction("lunar_electro"), time=0)
    assert exc.value.issue["code"] == "special_party_unbound"
    absent = SpecialReactionRuntime(
        model("lunar_electro"), party_member_ids=["someone.else"],
        party_capability_tags={"someone.else": [REQUIRED_CAPABILITY_TAGS["lunar_electro"]]},
    )
    with pytest.raises(SpecialReactionPending) as exc:
        absent.replace(reaction("lunar_electro"), time=0)
    assert exc.value.issue["code"] == "special_provider_absent"


def test_provider_string_without_database_capability_fails_closed():
    rt = runtime("lunar_electro", with_tags=False)
    with pytest.raises(SpecialReactionPending) as exc:
        rt.replace(reaction("lunar_electro"), time=0)
    assert exc.value.issue["code"] == "special_capability_unproven"
    assert [row["decision"] for row in rt.trace] == ["pending"]


def test_replacement_accepts_only_uncommitted_proposal():
    rt = runtime("lunar_electro")
    value = reaction("lunar_electro")
    value["commit_state"] = "committed"
    with pytest.raises(ValueError, match="proposal"):
        rt.replace(value, time=0)
    assert rt.trace == []


def test_profile_identity_is_pinned_and_confirmed_cannot_override():
    value = model("stellar_superconduct")
    value["replacements"]["stellar_superconduct"]["mechanics"]["profile_content_hash"] = "fake"
    assert any(row["code"] == "profile_identity" for row in validate_special_reaction_model(value))
    value = model("stellar_superconduct")
    value["replacements"]["stellar_superconduct"]["mechanics"]["field_duration_seconds"] = 7
    assert any(row["code"] == "profile_override_status" for row in validate_special_reaction_model(value))


def test_assumed_override_records_difference_and_downgrades_confidence():
    value = model("stellar_superconduct")
    value["replacements"]["stellar_superconduct"]["mechanics"] = mechanics(
        "stellar_superconduct", status="assumed", field_duration_seconds=7)
    rt = runtime("stellar_superconduct", value=value)
    audit = rt.replace(reaction("stellar_superconduct"), time=0)["reaction"]["profile"]
    assert audit["native_confidence"] == "assumed_override"
    assert audit["override_differences"]["field_duration_seconds"] == {
        "reviewed": 6, "configured": 7}


def test_pending_transaction_has_no_false_replace_trace_or_state():
    value = model("lunar_electro", qualification_status="pending")
    rt = runtime("lunar_electro", value=value)
    before = rt.state()
    with pytest.raises(SpecialReactionPending):
        rt.replace(reaction("lunar_electro"), time=0)
    assert rt.state() == before
    assert len(rt.trace) == 1 and rt.trace[0]["decision"] == "pending"


def test_stellar_superconduct_reaction_only_establishes_field():
    rt = runtime("stellar_superconduct")
    receipt = rt.replace(reaction("stellar_superconduct"), time=0)
    event = next(row for row in receipt["generated_events"]
                 if row.get("operation") == "establish_or_refresh_field")
    assert event["initial_application_counted"] is False
    assert event["damage_emitted"] is False
    assert damage_events(receipt) == []
    assert receipt["state"]["stellar_areas"]["battlefield.primary"]["stellar_superconduct_layers"] == 0


def test_stellar_superconduct_counts_only_later_successful_atomic_applications():
    rt = runtime("stellar_superconduct")
    rt.replace(reaction("stellar_superconduct", parent="origin.hit"), time=0)
    origin = rt.record_stellar_superconduct_application(
        event_id="origin.child", target_id="enemy", actor="a", element="cryo",
        time=1, application_allowed=True, atomic_parent_id="origin.hit")
    assert origin["event"]["reason"] == "establishing_application_excluded"
    first = rt.record_stellar_superconduct_application(
        event_id="later.1", target_id="enemy", actor="a", element="cryo",
        time=2, application_allowed=True, atomic_parent_id="aoe.packet")
    duplicate = rt.record_stellar_superconduct_application(
        event_id="later.2", target_id="enemy.2", actor="a", element="electro",
        time=3, application_allowed=True, atomic_parent_id="aoe.packet")
    blocked = rt.record_stellar_superconduct_application(
        event_id="later.3", target_id="enemy", actor="a", element="electro",
        time=4, application_allowed=False, atomic_parent_id="other")
    assert first["event"]["counted"] is True
    assert duplicate["event"]["reason"] == "atomic_parent_already_counted"
    assert blocked["event"]["reason"] == "application_not_allowed"
    assert rt.state()["stellar_areas"]["battlefield.primary"]["stellar_superconduct_layers"] == 1


def test_stellar_superconduct_settles_clears_and_emits_four_second_buff():
    rt = runtime("stellar_superconduct")
    rt.replace(reaction("stellar_superconduct"), time=0)
    rt.record_stellar_superconduct_application(
        event_id="a", target_id="enemy", actor="a", element="cryo",
        time=1, application_allowed=True)
    due = rt.advance(4_000_000)
    settle = next(row for row in due if row.get("operation") == "settle_four_second_window")
    assert settle["layers"] == 1
    assert settle["system_factor"] == pytest.approx(1.45)
    assert settle["ordinary_cryo_electro_bonus"] == pytest.approx(.29)
    assert settle["field_physical_res_delta"] == pytest.approx(-.4)
    assert settle["buff_expires_at"] == 8
    again = rt.record_stellar_superconduct_application(
        event_id="b", target_id="enemy", actor="a", element="cryo",
        time=4_000_001, application_allowed=True, atomic_parent_id="a")
    assert again["event"]["counted"] is True


def test_stellar_superconduct_zero_layer_buff_and_field_expiry_are_independent():
    rt = runtime("stellar_superconduct")
    rt.replace(reaction("stellar_superconduct"), time=0)
    settle = next(row for row in rt.advance(4_000_000)
                  if row.get("operation") == "settle_four_second_window")
    assert settle["layers"] == 0 and settle["ordinary_cryo_electro_bonus"] == .2
    rt.advance(6_000_000)
    assert rt.stellar_superconduct_snapshot()["settled_buff_active"] is True
    expired = rt.advance(8_000_000)
    assert any(row.get("operation") == "expire_settlement_buff" for row in expired)
    assert rt.stellar_superconduct_snapshot()["ordinary_cryo_electro_bonus"] == 0


def test_stellar_superconduct_refresh_does_not_reset_listener_window():
    rt = runtime("stellar_superconduct")
    rt.replace(reaction("stellar_superconduct", 1), time=0)
    rt.replace(reaction("stellar_superconduct", 2), time=3_000_000)
    state = rt.state()["stellar_areas"]["battlefield.primary"]
    assert state["stellar_superconduct_next_settlement"] == 4_000_000
    assert state["stellar_superconduct_expires_at"] == 9_000_000


def test_stellar_superconduct_physical_shred_follows_field_not_settlement_buff():
    rt = runtime("stellar_superconduct")
    established = rt.replace(reaction("stellar_superconduct"), time=0)
    field_event = next(row for row in established["generated_events"]
                       if row.get("operation") == "establish_or_refresh_field")
    assert field_event["physical_res_delta"] == pytest.approx(-.4)
    assert rt.stellar_superconduct_snapshot()["physical_res_delta"] == pytest.approx(-.4)
    rt.advance(3_999_999)
    assert rt.stellar_superconduct_snapshot()["physical_res_delta"] == pytest.approx(-.4)
    rt.advance(6_000_000)
    snapshot = rt.stellar_superconduct_snapshot()
    assert snapshot["active"] is False
    assert snapshot["settled_buff_active"] is True
    assert snapshot["physical_res_delta"] == 0


def test_stellar_superconduct_refresh_extends_field_physical_shred_to_refreshed_expiry():
    rt = runtime("stellar_superconduct")
    rt.replace(reaction("stellar_superconduct", 1), time=0)
    rt.replace(reaction("stellar_superconduct", 2), time=5_000_000)
    rt.advance(10_999_999)
    assert rt.stellar_superconduct_snapshot()["active"] is True
    assert rt.stellar_superconduct_snapshot()["physical_res_delta"] == pytest.approx(-.4)
    expired = rt.advance(11_000_000)
    assert any(row.get("operation") == "expire_field" for row in expired)
    assert rt.stellar_superconduct_snapshot()["physical_res_delta"] == 0


def test_stellar_superconduct_fields_are_area_scoped_not_target_scoped():
    rt = runtime("stellar_superconduct")
    rt.replace(reaction("stellar_superconduct", 1, target="enemy.a", area_id="area.a"), time=0)
    rt.replace(reaction("stellar_superconduct", 2, target="enemy.b", area_id="area.b"), time=0)
    rt.record_stellar_superconduct_application(
        event_id="area.a.hit", target_id="enemy.a", actor="a", element="cryo",
        time=1, application_allowed=True, area_id="area.a")
    state = rt.state()["stellar_areas"]
    assert state["area.a"]["stellar_superconduct_layers"] == 1
    assert state["area.b"]["stellar_superconduct_layers"] == 0


def test_direct_stellar_superconduct_formula_uses_layer_factor():
    actor = contributor("source", em=1000, crit_rate=.5, crit_dmg=1.0,
                        stellar_base=.2, stellar_mult=1.1, stellar_elevate=.1,
                        reaction_bonus=.3, resistance=.1)
    low = calculate_direct_special_damage(
        "stellar_superconduct", actor, multiplier=2, scaling=1000, layers=0)
    high = calculate_direct_special_damage(
        "stellar_superconduct", actor, multiplier=2, scaling=1000, layers=12)
    assert high["result"]["expected"] == pytest.approx(low["result"]["expected"] * 2)


def test_stellar_swirl_only_replaces_cryo_swirl():
    rt = runtime("stellar_swirl")
    ordinary = reaction("stellar_swirl")
    ordinary["aura_element"] = "hydro"
    receipt = rt.replace(ordinary, time=0)
    assert receipt["reaction"]["type"] == "swirl"
    assert receipt["reaction"]["route"] == "ordinary"


def test_stellar_swirl_multichild_proposal_cannot_bypass_packet_api():
    rt = runtime("stellar_swirl")
    with pytest.raises(ValueError, match="replace_stellar_swirl_packet"):
        rt.replace(reaction(
            "stellar_swirl", application_packet_size=2), time=0)


def test_stellar_swirl_runtime_fails_closed_when_child_role_source_is_missing():
    rt = runtime("stellar_swirl")
    before = rt.state()
    with pytest.raises(SpecialReactionPending) as error:
        rt.replace(reaction(
            "stellar_swirl", contributor_ids=["trigger.actor"],
            aura_owner=None), time=0)
    assert error.value.issue["code"] == "stellar_swirl_role_provenance_missing"
    assert rt.state() == before


def test_stellar_swirl_vortex_cross_reaction_dual_role_collision_rolls_back():
    rt = runtime("stellar_swirl")
    rt.replace(reaction("stellar_swirl"), time=0)
    before = rt.state()
    with pytest.raises(SpecialReactionPending) as error:
        rt.replace(reaction(
            "stellar_swirl", 2,
            owner="qualification.provider",
            trigger_owner="qualification.provider",
            aura_owner="consumer",
            contributor_ids=["consumer", "qualification.provider"],
        ), time=1)
    assert error.value.issue["code"] == "stellar_swirl_dual_role_collision_pending"
    assert rt.state() == before


def test_empty_area_aoe_packet_deals_each_anemo_but_creates_only_level_one_vortex():
    rt = runtime("stellar_swirl")
    children = [reaction("stellar_swirl", index, target=f"enemy.{index}", parent="attack.aoe")
                for index in range(1, 5)]
    receipt = rt.replace_stellar_swirl_packet(children, time=0)
    immediate = damage_events(receipt, "stellar_swirl_anemo")
    assert len(immediate) == 4
    assert all(row["role_provenance"]["component_policy"] ==
               "immediate_anemo_trigger_fixed_first" for row in immediate)
    assert all(row["role_provenance"]["anemo_trigger_ids"] == ["trigger.actor"]
               for row in immediate)
    assert all(row["role_provenance"]["cryo_applier_ids"] == [
        "qualification.provider"] for row in immediate)
    area = receipt["state"]["areas"]["battlefield.primary"]
    assert area["stellar_swirl_count"] == 1 and area["stellar_swirl_level"] == 1


def test_existing_vortex_counts_every_packet_child_and_upgrades_on_total_three():
    rt = runtime("stellar_swirl")
    rt.replace_stellar_swirl_packet([reaction("stellar_swirl", 1, parent="first")], time=0)
    receipt = rt.replace_stellar_swirl_packet([
        reaction("stellar_swirl", 2, target="enemy.2", parent="second"),
        reaction("stellar_swirl", 3, target="enemy.3", parent="second"),
    ], time=1)
    area = receipt["state"]["areas"]["battlefield.primary"]
    assert area["stellar_swirl_count"] == 3 and area["stellar_swirl_level"] == 2


def test_sixth_stellar_swirl_detonates_once_and_remaining_packet_does_not_reseed():
    rt = runtime("stellar_swirl")
    rt.replace_stellar_swirl_packet([reaction("stellar_swirl", 1, parent="first")], time=0)
    rt.replace_stellar_swirl_packet([
        reaction("stellar_swirl", index, target=f"enemy.{index}", parent="build")
        for index in (2, 3, 4)], time=1)
    receipt = rt.replace_stellar_swirl_packet([
        reaction("stellar_swirl", index, target=f"enemy.{index}", parent="finish")
        for index in (5, 6, 7)], time=2)
    assert len(damage_events(receipt, "stellar_swirl_anemo")) == 3
    cryo = [row for row in damage_events(receipt)
            if row["reaction_type"].startswith("stellar_swirl_cryo")]
    assert len(cryo) == 1 and cryo[0]["reaction_type"] == "stellar_swirl_cryo_lv3"
    area = receipt["state"]["areas"]["battlefield.primary"]
    assert area["stellar_swirl_count"] == 0 and area["stellar_swirl_detonate_at"] is None
    assert any(row.get("operation") == "remaining_packet_children_do_not_reseed"
               for row in receipt["generated_events"])


def test_stellar_swirl_is_spatially_bucketed_and_timeout_applies_cryo_after_damage():
    rt = runtime("stellar_swirl")
    rt.replace_stellar_swirl_packet(
        [reaction("stellar_swirl", 1, parent="a")], time=0, area_id="area.a")
    rt.replace_stellar_swirl_packet(
        [reaction("stellar_swirl", 2, parent="b")], time=0, area_id="area.b")
    due = rt.advance(3_000_000)
    damage = [row for row in due if row["type"] == "special_reaction_damage_request"]
    applications = [row for row in due if row["type"] == "special_reaction_application_request"]
    assert len(damage) == 2 and len(applications) == 2
    assert {row["area_id"] for row in damage} == {"area.a", "area.b"}
    assert all(row["reaction_type"] == "stellar_swirl_cryo_lv2" for row in damage)
    assert all(row["role_provenance"]["component_policy"] ==
               "vortex_cryo_best_then_anemo_best" for row in damage)
    assert all(row["gauge_units"] == 1 and row["application_order"] == "after_damage"
               for row in applications)


def test_lunar_electro_is_area_cloud_with_independent_target_attack_schedules():
    rt = runtime("lunar_electro")
    first = rt.replace(reaction("lunar_electro", 1, target="enemy.a"), time=0)
    assert next(row for row in first["generated_events"]
                if row.get("operation") == "schedule_aura_checked_ticks")["scheduled_count"] == 3
    second = rt.replace(reaction("lunar_electro", 2, target="enemy.b"), time=1_000_000)
    schedule = next(row for row in second["generated_events"]
                    if row.get("operation") == "extend_cloud_without_phase_reset")
    assert schedule["cloud_scope"] == "area"
    assert schedule["scheduled_target_ids"] == ["enemy.a", "enemy.b"]
    assert schedule["new_scheduled_times"] == [6.25, 1.25, 3.25, 5.25]
    ticks = rt.advance(7_000_000)
    assert [(row["target_id"], row["t"]) for row in ticks
            if row["type"] == "special_reaction_tick_request"] == [
                ("enemy.b", 1.25), ("enemy.a", 2.25),
                ("enemy.b", 3.25), ("enemy.a", 4.25),
                ("enemy.b", 5.25), ("enemy.a", 6.25)]


def test_lunar_electro_same_target_refresh_extends_duration_without_resetting_phase():
    rt = runtime("lunar_electro")
    rt.replace(reaction("lunar_electro", 1, target="enemy.a"), time=0)
    refreshed = rt.replace(
        reaction("lunar_electro", 2, target="enemy.a"), time=1_000_000)
    schedule = next(row for row in refreshed["generated_events"]
                    if row.get("operation") == "extend_cloud_without_phase_reset")
    assert schedule["new_target_added"] is False
    assert schedule["new_scheduled_times"] == [6.25]
    ticks = rt.advance(7_000_000)
    assert [row["t"] for row in ticks
            if row["type"] == "special_reaction_tick_request"] == [2.25, 4.25, 6.25]


def test_lunar_electro_clouds_in_different_areas_do_not_replace_each_other():
    rt = runtime("lunar_electro")
    rt.replace(reaction(
        "lunar_electro", 1, target="enemy.a", area_id="area.a"), time=0)
    second = rt.replace(reaction(
        "lunar_electro", 2, target="enemy.b", area_id="area.b"), time=100_000)
    assert not [row for row in second["generated_events"]
                if row.get("operation") == "replace_pending_ticks"]
    state = rt.state()["lunar_electro_areas"]
    assert set(state) == {"area.a", "area.b"}
    assert state["area.a"]["lunar_electro_active"] is True
    assert state["area.b"]["lunar_electro_active"] is True


def test_lunar_electro_tick_requires_live_dual_aura_consumes_each_and_samples_sources():
    rt = runtime("lunar_electro")
    rt.replace(reaction("lunar_electro"), time=0)
    tick = next(row for row in rt.advance(250_000)
                if row["type"] == "special_reaction_tick_request")
    result = rt.admit_lunar_electro_tick(tick, lunar_electro_aura_snapshot())
    assert result["admission"]["allowed"] is True
    assert result["aura_after"]["components"]["hydro"]["remaining_gu"] == pytest.approx(.6)
    assert result["aura_after"]["components"]["electro"]["remaining_gu"] == pytest.approx(.1)
    assert result["damage_event"]["contributor_ids"] == ["qualification.provider", "trigger.actor"]
    assert result["admission"]["consumed_contributor_ids"] == [
        "qualification.provider", "trigger.actor"]
    assert result["admission"]["contributor_allocations_after"]["hydro"][0][
        "remaining_gu"] == pytest.approx(.6)


def test_lunar_electro_tick_fails_closed_for_unknown_or_insufficient_aura():
    rt = runtime("lunar_electro")
    rt.replace(reaction("lunar_electro"), time=0)
    tick = next(row for row in rt.advance(250_000)
                if row["type"] == "special_reaction_tick_request")
    with pytest.raises(SpecialReactionPending, match="实时水雷"):
        rt.admit_lunar_electro_tick(tick, {"knowledge": "unknown"})
    denied = rt.admit_lunar_electro_tick(
        tick, lunar_electro_aura_snapshot(hydro=.39, electro=1))
    assert denied["admission"]["allowed"] is False and denied["damage_event"] is None


def test_lunar_electro_tick_decrements_every_positive_owner_residual_without_summing():
    rt = runtime("lunar_electro")
    rt.replace(reaction("lunar_electro"), time=0)
    tick = next(row for row in rt.advance(250_000)
                if row["type"] == "special_reaction_tick_request")
    snapshot = lunar_electro_aura_snapshot()
    snapshot["components"]["hydro"].update(
        remaining_gu=1.0,
        contributor_ids=["qualification.provider", "consumer"],
        contributor_allocations=[
            {"owner": "qualification.provider", "remaining_gu": 1.0,
             "last_application_at": 0.0, "source": "hydro.primary"},
            {"owner": "consumer", "remaining_gu": .6,
             "last_application_at": .1, "source": "hydro.secondary"},
        ],
    )
    result = rt.admit_lunar_electro_tick(tick, snapshot)
    assert result["admission"]["consumed_contributor_ids"] == [
        "qualification.provider", "consumer", "trigger.actor"]
    assert [row["remaining_gu"] for row in result["aura_after"]["components"][
            "hydro"]["contributor_allocations"]] == pytest.approx([.6, .2])
    assert result["aura_after"]["components"]["hydro"]["remaining_gu"] == pytest.approx(.6)


def test_lunar_electro_tick_rejects_historical_contributor_list_without_residual_ledger():
    rt = runtime("lunar_electro")
    rt.replace(reaction("lunar_electro"), time=0)
    tick = next(row for row in rt.advance(250_000)
                if row["type"] == "special_reaction_tick_request")
    with pytest.raises(SpecialReactionPending) as error:
        rt.admit_lunar_electro_tick(tick, {
            "knowledge": "known", "sample_time_tick": 250_000,
            "components": {
                "hydro": {"remaining_gu": 1, "contributor_ids": ["consumer"]},
                "electro": {"remaining_gu": 1, "contributor_ids": ["trigger.actor"]},
            },
        })
    assert error.value.issue["code"] == "lunar_electro_owner_residual_unproven"


def test_lunar_electro_tick_fails_closed_when_positive_residual_is_older_than_five_seconds():
    rt = runtime("lunar_electro")
    rt.replace(reaction("lunar_electro"), time=0)
    tick = next(row for row in rt.advance(250_000)
                if row["type"] == "special_reaction_tick_request")
    tick = copy.deepcopy(tick)
    tick.update(time_tick=5_000_001, t=5.000001)
    snapshot = lunar_electro_aura_snapshot(time_tick=5_000_001)
    trace_before = copy.deepcopy(rt.trace)
    with pytest.raises(SpecialReactionPending) as error:
        rt.admit_lunar_electro_tick(tick, snapshot)
    assert error.value.issue["code"] == "lunar_electro_contributor_recency_conflict"
    assert error.value.issue["excluded_stale_contributor_ids"] == [
        "qualification.provider", "trigger.actor"]
    assert rt.trace == trace_before


def test_lunar_bloom_generates_core_and_backlog_but_no_immediate_dew_or_damage():
    rt = runtime("lunar_bloom")
    receipt = rt.replace(reaction("lunar_bloom"), time=0)
    core = next(row for row in receipt["generated_events"]
                if row["type"] == "special_reaction_core_request")
    assert core["ordinary_core_created"] is True
    assert damage_events(receipt) == []
    assert receipt["state"]["party"]["lunar_bloom_dew"] == 0
    assert receipt["state"]["party"]["lunar_bloom_backlog"] == 1


def test_lunar_bloom_uses_one_continuous_party_fill_rate_without_parallel_speedup():
    rt = runtime("lunar_bloom")
    rt.replace(reaction("lunar_bloom", 1, target="enemy.a"), time=0)
    rt.replace(reaction("lunar_bloom", 2, target="enemy.b"), time=2_000_000)
    rt.advance(4_500_000)
    state = rt.state()["party"]
    assert state["lunar_bloom_dew"] == 1
    assert state["lunar_bloom_backlog"] == 1
    assert state["lunar_bloom_fill_progress"] == pytest.approx(.8)
    rt.advance(5_000_000)
    state = rt.state()["party"]
    assert state["lunar_bloom_dew"] == 2 and state["lunar_bloom_backlog"] == 0


def test_lunar_bloom_same_parent_adds_one_backlog_but_each_reaction_keeps_core():
    rt = runtime("lunar_bloom")
    receipts = [
        rt.replace(reaction("lunar_bloom", 1, target="a", parent="aoe"), time=0),
        rt.replace(reaction("lunar_bloom", 2, target="b", parent="aoe"), time=0),
    ]
    assert sum(row["type"] == "special_reaction_core_request"
               for receipt in receipts for row in receipt["generated_events"]) == 2
    assert rt.state()["party"]["lunar_bloom_backlog"] == 1


def test_lunar_bloom_full_cap_overflow_is_deferred_pending_not_unlimited_bank():
    rt = runtime("lunar_bloom")
    for index in range(1, 5):
        rt.replace(reaction("lunar_bloom", index), time=0)
    due = rt.advance(7_500_000)
    state = rt.state()["party"]
    assert state["lunar_bloom_dew"] == 3
    assert state["lunar_bloom_backlog"] == 0
    assert state["lunar_bloom_backlog_sources"] == []
    assert state["lunar_bloom_full_cap_unresolved"] == ["hit.4"]
    boundary = next(row for row in due
                    if row.get("operation") == "resolve_full_cap_queue_boundary")
    assert boundary["status"] == "pending"
    assert boundary["full_cap_queue_policy"] == "pending"
    with pytest.raises(SpecialReactionPending) as exc:
        rt.consume_lunar_bloom(
            event_id="consume.after-cap", target_id="enemy", actor="consumer",
            time=7_500_001)
    assert exc.value.issue["code"] == "lunar_bloom_full_cap_queue_pending"


def test_lunar_bloom_discard_full_cap_queue_requires_assumed_override():
    value = model("lunar_bloom")
    value["replacements"]["lunar_bloom"]["mechanics"] = mechanics(
        "lunar_bloom", full_cap_queue_policy="discard_new")
    rt = runtime("lunar_bloom", value=value)
    for index in range(1, 4):
        rt.replace(reaction("lunar_bloom", index), time=0)
    rt.advance(7_500_000)
    full = rt.replace(reaction("lunar_bloom", 4), time=7_500_001)
    queue = next(row for row in full["generated_events"]
                 if row.get("operation") == "enqueue_party_dew_fill")
    assert queue["queue_policy_status"] == "assumed_discarded"
    assert queue["profile"]["native_confidence"] == "assumed_override"
    consumed = rt.consume_lunar_bloom(
        event_id="consume.discard", target_id="enemy", actor="consumer",
        time=7_500_002)
    assert consumed["resource_event"]["after"] == 2
    assert rt.next_deadline() is None


def test_lunar_bloom_retain_policy_keeps_at_most_one_assumed_fill_window():
    value = model("lunar_bloom")
    value["replacements"]["lunar_bloom"]["mechanics"] = mechanics(
        "lunar_bloom", full_cap_queue_policy="retain_active_fill_window")
    rt = runtime("lunar_bloom", value=value)
    for index in range(1, 4):
        rt.replace(reaction("lunar_bloom", index), time=0)
    rt.advance(7_500_000)
    rt.replace(reaction("lunar_bloom", 4), time=7_500_001)
    rt.replace(reaction("lunar_bloom", 5), time=7_500_002)
    state = rt.state()["party"]
    assert state["lunar_bloom_backlog"] == 1
    assert state["lunar_bloom_backlog_sources"] == ["hit.5"]
    rt.consume_lunar_bloom(
        event_id="consume.retain", target_id="enemy", actor="consumer",
        time=7_500_003)
    assert rt.next_deadline() == 10_000_003
    rt.advance(10_000_003)
    assert rt.state()["party"]["lunar_bloom_dew"] == 3
    assert rt.state()["party"]["lunar_bloom_backlog"] == 0


def test_lunar_bloom_dew_is_party_resource_and_requires_explicit_consumer():
    rt = runtime("lunar_bloom")
    rt.replace(reaction("lunar_bloom", target="enemy.a"), time=0)
    rt.advance(2_500_000)
    consumed = rt.consume_lunar_bloom(
        event_id="skill.consume", target_id="enemy.b", actor="consumer", time=2_500_001)
    assert consumed["resource_event"]["resource_scope"] == "party"
    assert consumed["resource_event"]["after"] == 0
    assert consumed["resource_event"]["lunar_damage_emitted"] is False
    assert not any(row.get("type") == "special_reaction_damage_request"
                   for row in consumed["generated_events"])
    rejected = rt.consume_lunar_bloom(
        event_id="skill.empty", target_id="enemy.a", actor="consumer", time=2_500_002)
    assert rejected["resource_event"]["status"] == "rejected"


def test_direct_lunar_bloom_requires_bound_hashed_ability_profile_and_is_not_reaction_base():
    rt = runtime("lunar_bloom")
    rt.party_capability_tags = {
        **rt.party_capability_tags,
        "consumer": (REQUIRED_CAPABILITY_TAGS["lunar_bloom"],),
    }
    profile = direct_lunar_bloom_profile()
    assert validate_direct_lunar_damage_profile(profile) == []
    receipt = rt.request_direct_lunar_bloom_damage(
        event_id="ability.direct-lb.1", target_id="enemy", actor="consumer",
        action_id="consumer.skill.cast", time=1_250_000,
        ability_profile=profile, ability_id="consumer.skill",
        ability_variant="default", ability_effective_level=10)
    request = receipt["damage_event"]
    assert request["reaction_type"] == "lunar_bloom"
    assert request["reaction_base"] is False
    assert request["multiplier"] == 2.4
    assert request["scaling_stat"] == "em"
    assert request["count"] == 2
    assert request["owner"] == "consumer"
    assert request["ability_profile"] == profile
    assert request["contributor_ids"] == []
    resolved = resolve_special_damage_event(
        request, [contributor("consumer", em=1000)], scaling=1000)
    assert resolved["damage"]["reaction_base"] is False
    assert resolved["damage"]["context"]["multiplier"] == 2.4


def test_direct_lunar_bloom_compatibility_wrapper_accepts_v2_binding():
    rt = runtime("lunar_bloom")
    rt.party_capability_tags = {
        **rt.party_capability_tags,
        "consumer": (REQUIRED_CAPABILITY_TAGS["lunar_bloom"],),
    }
    profile = direct_lunar_bloom_profile()
    profile["ability_binding"] = {
        "character_id": "consumer",
        "ability_id": "consumer.skill",
        "component_id": profile["parameters"]["component"],
        "variant_id": "tap",
        "effective_level": 10,
        "database_fingerprint": "1" * 64,
    }
    profile["profile_content_hash"] = direct_lunar_damage_profile_content_hash(
        profile)

    request = rt.request_direct_lunar_bloom_damage(
        event_id="ability.direct-lb.v2",
        target_id="enemy",
        actor="consumer",
        action_id="consumer.skill.cast",
        time=1,
        ability_profile=profile,
        ability_id="consumer.skill",
        ability_variant="tap",
        ability_effective_level=10,
    )["damage_event"]

    assert request["ability_profile"]["ability_binding"] == (
        profile["ability_binding"])


@pytest.mark.parametrize(("variant", "effective_level"), [
    ("hold", 10),
    ("tap", 9),
])
def test_direct_lunar_v2_binding_mismatch_rolls_back(variant, effective_level):
    rt = runtime("lunar_bloom")
    rt.party_capability_tags = {
        **rt.party_capability_tags,
        "consumer": (REQUIRED_CAPABILITY_TAGS["lunar_bloom"],),
    }
    profile = direct_lunar_bloom_profile()
    profile["ability_binding"] = {
        "character_id": "consumer",
        "ability_id": "consumer.skill",
        "component_id": profile["parameters"]["component"],
        "variant_id": "tap",
        "effective_level": 10,
        "database_fingerprint": "1" * 64,
    }
    profile["profile_content_hash"] = direct_lunar_damage_profile_content_hash(
        profile)
    before = copy.deepcopy(rt.__dict__)

    with pytest.raises(SpecialReactionPending) as error:
        rt.request_direct_lunar_damage(
            event_id="ability.direct-lb.binding-mismatch",
            target_id="enemy",
            area_id="battlefield.primary",
            actor="consumer",
            action_id="consumer.skill.cast",
            time=1,
            ability_profile=profile,
            ability_id="consumer.skill",
            ability_variant=variant,
            ability_effective_level=effective_level,
        )

    assert error.value.issue["code"] == (
        "direct_special_ability_binding_mismatch")
    assert rt.__dict__ == before


def test_unregistered_confirmed_direct_lunar_profile_fails_closed():
    profile = direct_lunar_profile(status="confirmed")

    issues = validate_direct_lunar_damage_profile(profile)

    assert "reviewed_profile" in {row["code"] for row in issues}


def test_legacy_v1_direct_lunar_profile_is_migration_pending_and_cannot_execute():
    profile = direct_lunar_profile()
    profile.pop("ability_binding")
    profile["profile_content_hash"] = direct_lunar_damage_profile_content_hash(
        profile)

    issues = validate_direct_lunar_damage_profile(profile)

    assert [row["code"] for row in issues] == ["ability_binding_required"]
    rt = runtime("lunar_bloom")
    before = copy.deepcopy(rt.__dict__)
    with pytest.raises(SpecialReactionPending) as error:
        rt.request_direct_lunar_damage(
            event_id="ability.direct-lb.legacy", target_id="enemy",
            area_id="battlefield.primary", actor="consumer",
            action_id="consumer.skill.cast", time=1,
            ability_profile=profile, ability_id="consumer.skill",
            ability_variant="default", ability_effective_level=10,
        )
    assert error.value.issue["code"] == "direct_lunar_ability_profile_invalid"
    assert rt.__dict__ == before


@pytest.mark.parametrize(("reaction_type", "element", "factor"), [
    ("lunar_electro", "electro", 3.0),
    ("lunar_bloom", "dendro", 1.0),
    ("lunar_crystallize", "geo", 1.6),
])
def test_direct_lunar_damage_infers_reviewed_type_element_and_system_factor(
        reaction_type, element, factor):
    rt = runtime(reaction_type)
    rt.party_capability_tags = {
        **rt.party_capability_tags,
        "consumer": (REQUIRED_CAPABILITY_TAGS[reaction_type],),
    }
    profile = direct_lunar_profile(reaction_type)
    receipt = rt.request_direct_lunar_damage(
        event_id=f"ability.direct.{reaction_type}", target_id="enemy",
        area_id="arena.a", actor="consumer", action_id="consumer.skill.cast",
        time=1, ability_id="consumer.skill", ability_variant="default",
        ability_effective_level=10, ability_profile=profile,
    )
    request = receipt["damage_event"]
    assert request["reaction_type"] == reaction_type
    assert request["element"] == element
    assert request["system_factor"] == factor
    assert request["area_id"] == "arena.a"
    assert request["reaction_base"] is False
    assert request["contributor_ids"] == []


@pytest.mark.parametrize(("reaction_type", "wrong_element"), [
    ("lunar_electro", "dendro"),
    ("lunar_bloom", "geo"),
    ("lunar_crystallize", "electro"),
])
def test_direct_lunar_damage_rejects_type_element_mismatch(
        reaction_type, wrong_element):
    profile = direct_lunar_profile(reaction_type, element=wrong_element)
    # Rehash the deliberately invalid semantic combination so this is not
    # merely a stale-hash rejection.
    profile["profile_content_hash"] = direct_lunar_damage_profile_content_hash(profile)
    issues = validate_direct_lunar_damage_profile(profile)
    assert [row["path"] for row in issues] == ["ability_profile.parameters.element"]
    rt = runtime(reaction_type)
    with pytest.raises(SpecialReactionPending) as error:
        rt.request_direct_lunar_damage(
            event_id="ability.direct.invalid", target_id="enemy",
            area_id="battlefield.primary", actor="consumer",
            action_id="consumer.skill", ability_variant="default",
            time=0, ability_profile=profile,
        )
    assert error.value.issue["code"] == "direct_lunar_ability_profile_invalid"


def test_direct_lunar_damage_rejects_unsupported_type_even_with_fresh_hash():
    profile = direct_lunar_profile("lunar_bloom")
    profile["parameters"]["reaction_type"] = "lunar_fictional"
    profile["profile_content_hash"] = direct_lunar_damage_profile_content_hash(profile)
    issues = validate_direct_lunar_damage_profile(profile)
    assert [row["path"] for row in issues] == ["ability_profile.parameters.reaction_type"]


def test_direct_lunar_bloom_profile_hash_covers_numeric_payload_and_fails_closed():
    rt = runtime("lunar_bloom")
    profile = direct_lunar_bloom_profile()
    profile["parameters"]["multiplier"] = 999
    issues = validate_direct_lunar_damage_profile(profile)
    assert [row["code"] for row in issues] == ["hash"]
    with pytest.raises(SpecialReactionPending) as exc:
        rt.request_direct_lunar_bloom_damage(
            event_id="ability.direct-lb.tampered", target_id="enemy", actor="consumer",
            action_id="consumer.skill", time=0, ability_profile=profile)
    assert exc.value.issue["code"] == "direct_lunar_ability_profile_invalid"


def test_lunar_bloom_reaction_base_damage_is_not_a_public_formula_path():
    with pytest.raises(ValueError, match="不是受支持的多贡献者反应伤害"):
        calculate_weighted_special_damage(
            "lunar_bloom", [contributor("trigger.actor")])


def test_lunar_crystallize_first_creates_three_cages_later_only_advances_and_third_fires_three():
    rt = runtime("lunar_crystallize")
    first = rt.replace(reaction(
        "lunar_crystallize", 1, target="enemy.a",
        contributor_ids=["qualification.provider", "trigger.actor"]), time=0)
    second = rt.replace(reaction(
        "lunar_crystallize", 2, target="enemy.b",
        contributor_ids=["consumer", "trigger.actor"]), time=1)
    third = rt.replace(reaction(
        "lunar_crystallize", 3, target="enemy.c",
        contributor_ids=["qualification.provider", "fourth"]), time=2)
    assert first["reaction"]["type"] == "lunar_crystallize"
    assert first["reaction"]["reaction_trigger_tags"] == [
        "crystallize", "lunar_crystallize"]
    assert len(first["reaction"]["reaction_trigger_tags"]) == 2
    state1 = next(row for row in first["generated_events"]
                  if row.get("operation") == "create_moondrift_set")
    state2 = next(row for row in second["generated_events"]
                  if row.get("operation") == "advance_harmony")
    assert state1["cages_created"] == 3 and state2["cages_created"] == 0
    assert state1["ordinary_shard_created"] is False
    assert len(damage_events(third, "lunar_crystallize")) == 3
    assert third["state"]["moondrift_areas"]["battlefield.primary"]["lunar_crystallize_cages"] == 3
    assert third["state"]["party"]["lunar_crystallize_harmony_count"] == 0
    assert all(row["target_id"] is None for row in damage_events(third))
    assert all(row["trigger_target_id"] == "enemy.c" for row in damage_events(third))
    assert all(row["target_selection_policy"] == "event_time_nearby_entities_in_area"
               for row in damage_events(third))
    assert all(row["contributor_ids"] == [
        "qualification.provider", "trigger.actor", "consumer", "fourth"]
               for row in damage_events(third))


def test_lunar_crystallize_uses_global_cycle_but_area_objects_and_does_not_refresh_idle_area():
    rt = runtime("lunar_crystallize")
    rt.replace(reaction(
        "lunar_crystallize", 1, target="a", area_id="area.a",
        contributor_ids=["qualification.provider"]), time=0)
    rt.replace(reaction(
        "lunar_crystallize", 2, target="b", area_id="area.b",
        contributor_ids=["trigger.actor"]), time=1_000_000)
    third = rt.replace(reaction(
        "lunar_crystallize", 3, target="c", area_id="area.b",
        contributor_ids=["consumer"]), time=2_000_000)
    assert len(damage_events(third)) == 3
    assert all(row["area_id"] == "area.b" for row in damage_events(third))
    state = rt.state()["moondrift_areas"]
    assert state["area.a"]["lunar_crystallize_expires_at"] == 9_000_000
    assert state["area.b"]["lunar_crystallize_expires_at"] == 11_000_000
    rt.replace(reaction(
        "lunar_crystallize", 4, target="a", area_id="area.a",
        contributor_ids=["fourth"]), time=5_000_000)
    assert rt.state()["moondrift_areas"]["area.a"]["lunar_crystallize_expires_at"] == 9_000_000
    due = rt.advance(9_000_000)
    assert any(row.get("operation") == "expire_moondrift_set"
               and row["area_id"] == "area.a" for row in due)
    assert rt.state()["moondrift_areas"]["area.b"]["lunar_crystallize_cages"] == 3


def test_lunar_crystallize_new_set_suppresses_historical_overflow_then_preserves_remainder():
    rt = runtime("lunar_crystallize")
    rt.replace(reaction(
        "lunar_crystallize", 1, area_id="area.old",
        contributor_ids=["qualification.provider"]), time=0)
    rt.replace(reaction(
        "lunar_crystallize", 2, area_id="area.old",
        contributor_ids=["trigger.actor"]), time=1)
    rt.advance(9_000_001)
    created = rt.replace(reaction(
        "lunar_crystallize", 3, area_id="area.new",
        contributor_ids=["consumer"]), time=9_000_001)
    created_state = next(row for row in created["generated_events"]
                         if row.get("operation") == "create_moondrift_set")
    assert created_state["count"] == 3
    assert created_state["harmony_dispatch"] == "suppressed_on_initial_creation"
    assert damage_events(created, "lunar_crystallize") == []

    fired = rt.replace(reaction(
        "lunar_crystallize", 4, area_id="area.new",
        contributor_ids=["fourth"]), time=9_000_002)
    hits = damage_events(fired, "lunar_crystallize")
    assert len(hits) == 3
    assert all(row["contributor_ids"] == [
        "qualification.provider", "trigger.actor", "consumer"] for row in hits)
    state = fired["state"]["party"]
    assert state["lunar_crystallize_harmony_count"] == 1
    assert state["lunar_crystallize_contributor_ids"] == ["fourth"]
    assert [entry["reaction_id"] for entry in state[
        "lunar_crystallize_harmony_ledger"]] == [
            "ordinary.4:special:lunar_crystallize"]


def test_lunar_crystallize_harmony_counter_caps_at_four_during_assumed_cooldown():
    rt = runtime("lunar_crystallize")
    for index in range(1, 4):
        rt.replace(reaction("lunar_crystallize", index), time=index)
    # The first three reactions dispatched Harmony at t=3us.  Rebuild a
    # counter inside the explicit 0.2s assumed cooldown so it cannot fire.
    blocked = None
    for index in range(4, 9):
        blocked = rt.replace(reaction("lunar_crystallize", index), time=index)
    state = rt.state()["party"]
    assert state["lunar_crystallize_harmony_count"] == 4
    assert len(state["lunar_crystallize_harmony_ledger"]) == 4
    marker = next(row for row in blocked["generated_events"]
                  if row.get("operation") == "advance_harmony")
    assert marker["counter_capped"] is True
    assert marker["harmony_dispatch"] == "blocked_by_assumed_attack_cooldown"

    released = rt.replace(
        reaction("lunar_crystallize", 9), time=200_003)
    assert len(damage_events(released, "lunar_crystallize")) == 3
    assert released["state"]["party"]["lunar_crystallize_harmony_count"] == 1


def test_lunar_crystallize_reviewed_profile_fails_closed_without_cooldown_override():
    value = model("lunar_crystallize")
    reviewed = REVIEWED_SPECIAL_REACTION_PROFILES["lunar_crystallize"]
    value["replacements"]["lunar_crystallize"]["mechanics"] = {
        "status": "assumed",
        "source": "unresolved reviewed profile without invented cooldown",
        "profile_id": reviewed["profile_id"],
        "profile_revision": reviewed["profile_revision"],
        "profile_content_hash": reviewed["profile_content_hash"],
        "override_reason": "preserve unresolved cooldown boundary",
        **copy.deepcopy(reviewed["parameters"]),
    }
    issues = validate_special_reaction_model(value)
    assert any(row["code"] == "mechanics_pending" for row in issues)


def test_lunar_crystallize_replaces_only_hydro_branch_of_dual_aura_crystallize():
    rt = runtime("lunar_crystallize")
    ordinary_electro = reaction(
        "lunar_crystallize", 1, parent="geo.dual-aura",
        aura_element="electro", trigger_element="geo")
    electro_receipt = rt.replace(ordinary_electro, time=0)
    assert electro_receipt["decision"]["decision"] == "ordinary"
    assert electro_receipt["decision"]["reason"] == "element_scope_not_matched"
    assert electro_receipt["reaction"]["type"] == "crystallize"
    assert electro_receipt["reaction"]["route"] == "ordinary"

    hydro_receipt = rt.replace(reaction(
        "lunar_crystallize", 2, parent="geo.dual-aura",
        aura_element="hydro", trigger_element="geo"), time=0)
    assert hydro_receipt["decision"]["decision"] == "replace"
    assert hydro_receipt["reaction"]["type"] == "lunar_crystallize"
    assert hydro_receipt["reaction"]["ordinary_side_effects_committed"] is False
    assert hydro_receipt["state"]["party"]["lunar_crystallize_harmony_count"] == 1


def test_weighted_damage_enumerates_actual_crit_outcomes_without_normalizing_missing_weights():
    weak = contributor("weak", level=90)
    strong = contributor("strong", level=100, em=1000, crit_rate=1, crit_dmg=2)
    result = calculate_weighted_special_damage("lunar_electro", [weak, strong])
    by_id = {row["contributor_id"]: row for row in result["components"]}
    assert by_id["strong"]["expected_weight_share"] == pytest.approx(.6)
    assert by_id["weak"]["expected_weight_share"] == pytest.approx(.3)
    assert result["weights"] == list(LUNAR_STELLAR_WEIGHTS)
    assert result["ranking_policy"] == "independent_crit_then_actual_damage_desc"
    assert result["joint_outcome_count"] == 4
    assert result["joint_probability_total"] == pytest.approx(1)


def test_weighted_damage_ranks_each_actual_crit_outcome_instead_of_expected_values():
    fixed = contributor(
        "fixed-100", lunar_mult=0, reaction_flat=100,
        crit_rate=0, crit_dmg=0)
    volatile = contributor(
        "volatile-60", lunar_mult=0, reaction_flat=60,
        crit_rate=.5, crit_dmg=2)
    result = calculate_weighted_special_damage(
        "lunar_electro", [fixed, volatile])
    assert result["no_crit"] == pytest.approx(100*.6 + 60*.3)
    assert result["crit"] == pytest.approx(180*.6 + 100*.3)
    assert result["expected"] == pytest.approx(108)
    by_id = {row["contributor_id"]: row for row in result["components"]}
    assert by_id["fixed-100"]["rank_probabilities"] == pytest.approx(
        {"1": .5, "2": .5})
    assert by_id["volatile-60"]["rank_probabilities"] == pytest.approx(
        {"1": .5, "2": .5})
    assert by_id["fixed-100"]["expected_weight_share"] == pytest.approx(.45)
    assert by_id["volatile-60"]["expected_weight_share"] == pytest.approx(.45)


def test_lunar_reaction_factor_and_weights_match_published_single_source_coefficients():
    electro = calculate_weighted_special_damage(
        "lunar_electro", [contributor("electro")])
    crystallize = calculate_weighted_special_damage(
        "lunar_crystallize", [contributor("geo")])
    # Runtime factors are normalized against the 60/30/5/5 contribution
    # weights.  3*0.6 and 1.6*0.6 are the published one-source 1.8/0.96,
    # so replacing the stored factors with 1.8/0.96 would double-apply 0.6.
    assert (electro["system_factor"]
            * electro["components"][0]["expected_weight_share"]) == pytest.approx(1.8)
    assert (crystallize["system_factor"]
            * crystallize["components"][0]["expected_weight_share"]) == pytest.approx(.96)


def test_weighted_damage_uses_each_contributors_own_formula_context():
    result = calculate_weighted_special_damage("lunar_crystallize", [
        contributor("a", level=70, resistance=.1),
        contributor("b", level=100, em=2000, crit_rate=1, crit_dmg=1, resistance=-.2),
    ])
    by_id = {row["contributor_id"]: row for row in result["components"]}
    assert by_id["a"]["result"]["zones"]["base_in"] == pytest.approx(REACTION_BASE_VALUE[70])
    assert by_id["b"]["result"]["zones"]["crit_expected"] == 2


def test_stellar_swirl_cryo_melt_requires_unique_explicit_owner_context():
    rows = [contributor("cryo"), contributor("anemo")]
    roles = stellar_swirl_roles()
    plain = calculate_weighted_special_damage(
        "stellar_swirl_cryo_lv2", rows, role_provenance=roles)
    melted = calculate_weighted_special_damage(
        "stellar_swirl_cryo_lv2", rows,
        amplify={"owner_id": "cryo", "em": 0, "reaction_bonus": 0},
        role_provenance=roles)
    assert melted["expected"] == pytest.approx(plain["expected"] * 1.5)
    with pytest.raises(ValueError, match="只有反应星扩散冰"):
        calculate_weighted_special_damage(
            "lunar_electro", rows,
            amplify={"owner_id": "cryo", "em": 0, "reaction_bonus": 0})


def test_stellar_swirl_damage_fails_closed_without_role_provenance():
    with pytest.raises(SpecialReactionPending) as error:
        calculate_weighted_special_damage(
            "stellar_swirl_anemo", [contributor("anemo")])
    assert error.value.issue["code"] == "stellar_swirl_role_provenance_missing"


def test_stellar_swirl_immediate_anemo_keeps_trigger_in_fixed_point_six_slot():
    rows = [
        contributor("anemo", stellar_mult=0, reaction_flat=10),
        contributor("cryo", stellar_mult=0, reaction_flat=100),
    ]
    result = calculate_weighted_special_damage(
        "stellar_swirl_anemo", rows,
        role_provenance=stellar_swirl_roles(
            policy="immediate_anemo_trigger_fixed_first",
            participants=("anemo", "cryo")),
    )
    assert result["expected"] == pytest.approx(10*.6 + 100*.3)
    by_id = {row["contributor_id"]: row for row in result["components"]}
    assert by_id["anemo"]["expected_weight_share"] == pytest.approx(.6)
    assert by_id["cryo"]["expected_weight_share"] == pytest.approx(.3)
    assert result["ranking_policy"] == (
        "stellar_swirl_role_slots_then_independent_actual_damage")


def test_stellar_swirl_vortex_assigns_best_cryo_then_best_anemo_then_remaining():
    rows = [
        contributor("cryo.low", stellar_mult=0, reaction_flat=40),
        contributor("cryo.high", stellar_mult=0, reaction_flat=100),
        contributor("anemo", stellar_mult=0, reaction_flat=10),
        contributor("other", stellar_mult=0, reaction_flat=200),
    ]
    roles = stellar_swirl_roles(
        participants=("cryo.low", "cryo.high", "anemo", "other"),
        cryo=("cryo.low", "cryo.high"))
    result = calculate_weighted_special_damage(
        "stellar_swirl_cryo_lv3", rows, role_provenance=roles)
    assert result["expected"] == pytest.approx(
        100*.6 + 10*.3 + 200*.05 + 40*.05)


def test_stellar_swirl_dual_role_collision_is_pending_not_double_weighted():
    roles = stellar_swirl_roles(
        participants=("dual",), anemo=("dual",), cryo=("dual",))
    with pytest.raises(SpecialReactionPending) as error:
        calculate_weighted_special_damage(
            "stellar_swirl_cryo_lv2", [contributor("dual")],
            role_provenance=roles)
    assert error.value.issue["code"] == "stellar_swirl_dual_role_collision_pending"


def test_generated_damage_resolution_retains_event_and_profile_context():
    rt = runtime("lunar_crystallize")
    event = None
    for index in (1, 2, 3):
        receipt = rt.replace(reaction("lunar_crystallize", index), time=index)
        event = next(iter(damage_events(receipt)), event)
    resolved = resolve_special_damage_event(
        event, [contributor("a", em=100, crit_rate=.5, crit_dmg=1)])
    assert resolved["type"] == "special_reaction_damage"
    assert resolved["profile"]["native_confidence"] == "assumed_override"
    assert resolved["damage"]["components"][0]["context"]["em"] == 100


@pytest.mark.parametrize("bad", [
    {"dmg_bonus": 1}, {"enemy_level": 100}, {"def_ignore": 1}, {"atk": 1000},
])
def test_special_formula_rejects_traditional_or_unread_fields(bad):
    with pytest.raises(ValueError, match="不会读取"):
        calculate_weighted_special_damage("lunar_electro", [contributor("a", **bad)])


@pytest.mark.parametrize(("mutation", "code"), [
    (lambda m: m["replacements"]["lunar_electro"]["qualification"].update(provider_ids=[]),
     "qualification_provider"),
    (lambda m: m["replacements"]["lunar_electro"]["qualification"].update(capability_tag="invented"),
     "qualification_capability"),
    (lambda m: m["replacements"]["lunar_electro"]["mechanics"].update(refresh_mode="independent"),
     "refresh_mode"),
    (lambda m: m["replacements"]["lunar_electro"]["mechanics"].update(tick_interval_seconds=.0000001),
     "time_precision"),
    (lambda m: m["replacements"]["lunar_crystallize"]["mechanics"].update(harmony_threshold=2),
     "mechanics_contract"),
])
def test_adversarial_models_fail_closed(mutation, code):
    value = model("lunar_electro", "lunar_crystallize")
    mutation(value)
    assert any(row["code"] == code for row in validate_special_reaction_model(value))


def test_capabilities_are_json_serializable_and_expose_profiles_without_mutable_leak():
    first = special_reaction_capabilities()
    json.dumps(first, ensure_ascii=False)
    assert first["routing"]["commit_after_route_selection"] is True
    assert first["profiles"]["stellar_swirl"]["parameters"]["upgrade_threshold"] == 3
    assert first["profiles"]["lunar_bloom"]["parameters"]["full_cap_queue_policy"] == "pending"
    assert "Hydro+Electro dual aura" in first["gauge"]["lunar_crystallize"]
    first["qualification"]["provider_ids_required"] = False
    assert special_reaction_capabilities()["qualification"]["provider_ids_required"] is True


def test_next_deadline_and_time_are_monotonic():
    rt = runtime("stellar_swirl")
    rt.replace(reaction("stellar_swirl"), time=0)
    assert rt.next_deadline() == 3_000_000
    with pytest.raises(ValueError, match="单调"):
        rt.advance(-1)
