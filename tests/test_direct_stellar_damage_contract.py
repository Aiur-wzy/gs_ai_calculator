from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from test_elemental_multiaura_v4 import aura
from test_special_reaction_mechanics_projection import _application_fixture, _enable
from test_special_reactions import direct_lunar_bloom_profile, reaction, runtime

from genshin_engine import special_reaction_profiles
from genshin_engine.constants import stellar_superconduct_factor
from genshin_engine.mechanics import _Mechanics, execute_mechanics
from genshin_engine.special_reactions import (
    REQUIRED_CAPABILITY_TAGS,
    SpecialReactionPending,
    direct_stellar_damage_profile_content_hash,
    load_reviewed_direct_special_damage_profiles,
    validate_direct_stellar_damage_profile,
)


def direct_stellar_profile(
    *,
    character_id: str = "qualification.provider",
    ability_id: str = "ability.star",
    component: str = "stellar_component",
    variant_id: str = "tap",
    effective_level: int = 10,
    status: str = "assumed",
) -> dict:
    value = {
        "profile_id": "ability.test.direct-stellar-superconduct.v2",
        "profile_revision": 2,
        "profile_content_hash": "0" * 64,
        "authority": ("reviewed_database_projection" if status == "confirmed"
                      else "explicit_assumption"),
        "status": status,
        "source": ("reviewed direct Stellar fixture" if status == "confirmed"
                   else "explicit direct Stellar fixture"),
        "source_refs": [{"kind": "test_database", "id": "ability.star"}],
        "parameters": {
            "reaction_type": "stellar_superconduct",
            "element": "cryo",
            "multiplier": 2.4,
            "scaling_stat": "atk",
            "count": 2,
            "component": component,
        },
        "ability_binding": {
            "character_id": character_id,
            "ability_id": ability_id,
            "component_id": component,
            "variant_id": variant_id,
            "effective_level": effective_level,
            "database_fingerprint": "1" * 64,
        },
    }
    value["profile_content_hash"] = direct_stellar_damage_profile_content_hash(value)
    return value


def _established_runtime(*, layers: int = 1):
    rt = runtime("stellar_superconduct")
    rt.party_capability_tags = {
        **rt.party_capability_tags,
        "qualification.provider": (
            REQUIRED_CAPABILITY_TAGS["stellar_superconduct"],),
    }
    rt.replace(reaction("stellar_superconduct"), time=0)
    for index in range(layers):
        rt.record_stellar_superconduct_application(
            event_id=f"layer.{index}", target_id="enemy",
            actor="qualification.provider", element="cryo", time=index + 1,
            application_allowed=True, atomic_parent_id=f"layer-parent.{index}",
        )
    return rt


def test_direct_stellar_profile_rejects_external_layers_factor_and_hash_tamper():
    profile = direct_stellar_profile()
    assert validate_direct_stellar_damage_profile(profile) == []

    for field in ("layers", "factor", "system_factor"):
        tampered = copy.deepcopy(profile)
        tampered["parameters"][field] = 12
        tampered["profile_content_hash"] = direct_stellar_damage_profile_content_hash(
            tampered)
        issues = validate_direct_stellar_damage_profile(tampered)
        assert any(row["code"] == "fields" for row in issues)

    tampered = copy.deepcopy(profile)
    tampered["parameters"]["multiplier"] = 999
    assert [row["code"] for row in validate_direct_stellar_damage_profile(tampered)] == [
        "hash"]

    unregistered = direct_stellar_profile(status="confirmed")
    assert "reviewed_profile" in {
        row["code"] for row in validate_direct_stellar_damage_profile(unregistered)}


def test_public_reviewed_catalog_cannot_be_poisoned_to_confirm_a_profile():
    profile = direct_stellar_profile(status="confirmed")
    catalog = load_reviewed_direct_special_damage_profiles()
    catalog[(profile["profile_id"], profile["profile_revision"])] = profile

    assert load_reviewed_direct_special_damage_profiles() == {}
    assert "reviewed_profile" in {
        row["code"] for row in validate_direct_stellar_damage_profile(profile)}


def test_public_reviewed_catalog_returns_detached_nested_rows(monkeypatch):
    profile = direct_stellar_profile(status="confirmed")
    key = (profile["profile_id"], profile["profile_revision"])
    canonical = {key: profile}
    monkeypatch.setattr(
        special_reaction_profiles,
        "_load_reviewed_direct_special_damage_profiles",
        lambda: canonical,
    )

    detached = load_reviewed_direct_special_damage_profiles()
    detached[key]["parameters"]["multiplier"] = 999

    assert canonical[key]["parameters"]["multiplier"] == 2.4
    assert load_reviewed_direct_special_damage_profiles() == canonical


def test_direct_stellar_samples_latest_completed_window_at_exact_boundary():
    rt = _established_runtime(layers=1)
    receipt = rt.request_direct_stellar_damage(
        event_id="direct.star", target_id="enemy", area_id="battlefield.primary",
        actor="qualification.provider", action_id="action.star",
        ability_id="ability.star", ability_variant="tap",
        ability_effective_level=10, time=4_000_000,
        ability_profile=direct_stellar_profile(),
    )

    request = receipt["damage_event"]
    assert request["layers"] == 1
    assert request["system_factor"] == pytest.approx(1.45)
    assert request["system_factor"] == stellar_superconduct_factor(request["layers"])
    assert request["application"] is None
    assert request["contributor_ids"] == []
    assert [row.get("operation") for row in receipt["generated_events"][:-1]] == [
        "settle_four_second_window"]


def test_direct_stellar_zero_and_twelve_layers_are_runtime_owned():
    for layers, expected in ((0, 1.0), (12, 2.0)):
        rt = _established_runtime(layers=layers)
        request = rt.request_direct_stellar_damage(
            event_id=f"direct.star.{layers}", target_id="enemy",
            area_id="battlefield.primary", actor="qualification.provider",
            action_id=f"action.star.{layers}", ability_id="ability.star",
            ability_variant="tap", ability_effective_level=10,
            time=4_000_000, ability_profile=direct_stellar_profile(),
        )["damage_event"]
        assert request["layers"] == layers
        assert request["system_factor"] == pytest.approx(expected)


def test_direct_stellar_reads_previous_window_before_boundary_and_buff_lifetime_after_field():
    rt = _established_runtime(layers=1)
    before = rt.request_direct_stellar_damage(
        event_id="direct.before", target_id="enemy", area_id="battlefield.primary",
        actor="qualification.provider", action_id="action.before",
        ability_id="ability.star", ability_variant="tap",
        ability_effective_level=10, time=3_999_999,
        ability_profile=direct_stellar_profile(),
    )["damage_event"]
    assert before["layers"] == 0

    during_tail = rt.request_direct_stellar_damage(
        event_id="direct.field-expiry", target_id="enemy",
        area_id="battlefield.primary", actor="qualification.provider",
        action_id="action.field-expiry", ability_id="ability.star",
        ability_variant="tap", ability_effective_level=10,
        time=6_000_000, ability_profile=direct_stellar_profile(),
    )["damage_event"]
    assert during_tail["settled_snapshot"]["active"] is False
    assert during_tail["settled_snapshot"]["settled_buff_active"] is True
    assert during_tail["layers"] == 1

    expired = rt.request_direct_stellar_damage(
        event_id="direct.buff-expiry", target_id="enemy",
        area_id="battlefield.primary", actor="qualification.provider",
        action_id="action.buff-expiry", ability_id="ability.star",
        ability_variant="tap", ability_effective_level=10,
        time=8_000_000, ability_profile=direct_stellar_profile(),
    )["damage_event"]
    assert expired["settled_snapshot"]["settled_buff_active"] is False
    assert expired["layers"] == 0
    assert expired["system_factor"] == 1


def test_direct_stellar_unknown_area_fails_without_lazy_state_creation():
    rt = _established_runtime()
    before = copy.deepcopy(rt.state())
    with pytest.raises(SpecialReactionPending) as error:
        rt.request_direct_stellar_damage(
            event_id="direct.bad-area", target_id="enemy", area_id="typo.area",
            actor="qualification.provider", action_id="action.star",
            ability_id="ability.star", ability_variant="tap",
            ability_effective_level=10, time=4_000_000,
            ability_profile=direct_stellar_profile(),
        )
    assert error.value.issue["code"] == "direct_stellar_area_unbound"
    assert rt.state() == before
    assert "typo.area" not in rt.stellar_field_by_area


def test_snapshot_and_ordinary_bonus_probe_cannot_precreate_direct_stellar_area():
    rt = runtime("stellar_superconduct")
    rt.party_capability_tags = {
        **rt.party_capability_tags,
        "qualification.provider": (
            REQUIRED_CAPABILITY_TAGS["stellar_superconduct"],),
    }
    assert rt.stellar_superconduct_snapshot("arena.unestablished")["layers"] == 0
    assert rt.stellar_field_by_area == {}

    runner = object.__new__(_Mechanics)
    runner.elemental = SimpleNamespace(special_runtime=rt)
    assert runner._stellar_superconduct_effects({
        "id": "ordinary.cryo", "damage_system": "classic",
        "element": "cryo", "extensions": {
            "special_reaction": {"area_id": "arena.unestablished"}},
    }, {"origin": 0}) == []
    assert rt.stellar_field_by_area == {}

    with pytest.raises(SpecialReactionPending) as error:
        rt.request_direct_stellar_damage(
            event_id="direct.precreated", target_id="enemy",
            area_id="arena.unestablished", actor="qualification.provider",
            action_id="action.star", ability_id="ability.star",
            ability_variant="tap", ability_effective_level=10, time=0,
            ability_profile=direct_stellar_profile(),
        )
    assert error.value.issue["code"] == "direct_stellar_area_unbound"
    assert rt.stellar_field_by_area == {}


def test_direct_delivery_identity_rejects_same_component_relabelled_event():
    rt = _established_runtime()
    kwargs = {
        "target_id": "enemy", "area_id": "battlefield.primary",
        "actor": "qualification.provider", "action_id": "action.star",
        "ability_id": "ability.star", "ability_variant": "tap",
        "ability_effective_level": 10,
        "time": 4_000_000,
        "ability_profile": direct_stellar_profile(),
    }
    rt.request_direct_stellar_damage(event_id="direct.first", **kwargs)
    before = copy.deepcopy(rt.state())
    with pytest.raises(SpecialReactionPending) as error:
        rt.request_direct_stellar_damage(event_id="direct.second", **kwargs)
    assert error.value.issue["code"] == "direct_special_delivery_duplicate"
    assert rt.state() == before


def test_direct_stellar_variant_mismatch_fails_without_state_change():
    rt = _established_runtime(layers=1)
    before = copy.deepcopy(rt.__dict__)

    with pytest.raises(SpecialReactionPending) as error:
        rt.request_direct_stellar_damage(
            event_id="direct.variant-mismatch", target_id="enemy",
            area_id="battlefield.primary", actor="qualification.provider",
            action_id="action.star", ability_id="ability.star",
            ability_variant="hold", ability_effective_level=10,
            time=4_000_000,
            ability_profile=direct_stellar_profile(variant_id="tap"),
        )

    assert error.value.issue["code"] == "direct_special_ability_binding_mismatch"
    assert error.value.issue["ability_variant"] == "hold"
    assert rt.__dict__ == before


def test_direct_stellar_effective_level_mismatch_fails_without_state_change():
    rt = _established_runtime(layers=1)
    before = copy.deepcopy(rt.__dict__)

    with pytest.raises(SpecialReactionPending) as error:
        rt.request_direct_stellar_damage(
            event_id="direct.level-mismatch", target_id="enemy",
            area_id="battlefield.primary", actor="qualification.provider",
            action_id="action.star", ability_id="ability.star",
            ability_variant="tap", ability_effective_level=9,
            time=4_000_000,
            ability_profile=direct_stellar_profile(effective_level=10),
        )

    assert error.value.issue["code"] == "direct_special_ability_binding_mismatch"
    assert error.value.issue["ability_effective_level"] == 9
    assert rt.__dict__ == before


def test_direct_stellar_rejects_id_created_by_same_boundary_deadline_atomically():
    rt = _established_runtime(layers=1)
    before = copy.deepcopy(rt.__dict__)
    event_id = "battlefield.primary:stellar-superconduct:settlement:4000000"

    with pytest.raises(SpecialReactionPending) as error:
        rt.request_direct_stellar_damage(
            event_id=event_id, target_id="enemy", area_id="battlefield.primary",
            actor="qualification.provider", action_id="action.star",
            ability_id="ability.star", ability_variant="tap",
            ability_effective_level=10, time=4_000_000,
            ability_profile=direct_stellar_profile(),
        )

    assert error.value.issue["code"] == "direct_special_event_duplicate"
    assert rt.__dict__ == before


def test_invalid_direct_lunar_request_does_not_advance_due_deadline():
    rt = runtime("lunar_bloom")
    rt.replace(reaction("lunar_bloom"), time=0)
    profile = direct_lunar_bloom_profile()
    profile["parameters"]["multiplier"] = 999
    before = copy.deepcopy(rt.__dict__)

    with pytest.raises(SpecialReactionPending) as error:
        rt.request_direct_lunar_damage(
            event_id="bad.lunar", target_id="enemy", area_id="battlefield.primary",
            actor="consumer", action_id="consumer.skill", time=2_500_000,
            ability_variant="default", ability_profile=profile,
        )

    assert error.value.issue["code"] == "direct_lunar_ability_profile_invalid"
    assert rt.__dict__ == before


def test_public_direct_stellar_operation_projects_one_unweighted_hit():
    template, rotation, mechanics = _application_fixture(
        "electro", initial=[aura("cryo", .8, "a")], times=(.2, 1.0))
    template["characters"][0]["stats"]["atk"] = 1000
    rotation["abilities"][0]["variant"] = "tap"
    rotation["abilities"][0]["effective_level"] = 10
    _enable(mechanics, "stellar_superconduct")
    # A later successful Cryo application contributes one layer to the first
    # four-second window.  The direct event itself has no application.
    template["hits"][1]["element"] = "cryo"
    mechanics["hit_events"][1]["application"] = {
        "profile_id": "cryo", "target_id": "enemy", "landed": True,
        "area_id": "battlefield.primary",
    }
    profile = direct_stellar_profile(character_id="a", ability_id="e")
    mechanics["special_reaction_events"] = [{
        "id": "direct-stellar", "kind": "direct_stellar_damage",
        "action_id": "cast", "target_id": "enemy",
        # The field is established by the 0.2s hit, so its exact settlement
        # boundary is 4.2s.
        "area_id": "battlefield.primary", "t": 4.2, "order": 20,
        "ability_profile": profile, "status": profile["status"],
        "source": "explicit direct Stellar operation fixture",
    }]

    out = execute_mechanics(template, rotation, mechanics)

    assert out["status"] in ("passed", "unproven"), out["issues"]
    hits = [row for row in out["resolved_template"]["hits"]
            if row.get("extensions", {}).get("special_reaction", {}).get(
                "request_id") == "0:direct-stellar"]
    assert len(hits) == 1
    hit = hits[0]
    assert hit["reaction"] == {"type": "stellar_superconduct", "layers": 1}
    assert hit["damage_system"] == "stellar"
    assert hit["description"].startswith("由显式假设能力profile生成；")
    assert "result_weight" not in hit
    assert hit["extensions"]["special_reaction"]["contributor_weighting"] == (
        "not_applicable")
    projections = [row for row in out["special_reaction_trace"]
                   if row.get("type") == "special_reaction_damage_projection"
                   and row.get("request_id") == "0:direct-stellar"]
    assert len(projections) == 1
    assert projections[0]["settled_layers"] == 1


def test_public_direct_stellar_operation_preserves_non_default_area_through_hit_sampling():
    template, rotation, mechanics = _application_fixture(
        "electro", initial=[aura("cryo", .8, "a")], times=(.2, 1.0))
    template["characters"][0]["stats"]["atk"] = 1000
    rotation["abilities"][0]["variant"] = "tap"
    rotation["abilities"][0]["effective_level"] = 10
    _enable(mechanics, "stellar_superconduct")
    for application in mechanics["hit_events"]:
        if application.get("application") is not None:
            application["application"]["area_id"] = "arena.side"
    template["hits"][1]["element"] = "cryo"
    mechanics["hit_events"][1]["application"] = {
        "profile_id": "cryo", "target_id": "enemy", "landed": True,
        "area_id": "arena.side",
    }
    mechanics["special_reaction_events"] = [{
        "id": "direct-stellar-side", "kind": "direct_stellar_damage",
        "action_id": "cast", "target_id": "enemy", "area_id": "arena.side",
        "t": 4.2, "order": 20,
        "ability_profile": direct_stellar_profile(character_id="a", ability_id="e"),
        "status": "assumed", "source": "explicit non-default-area fixture",
    }]

    out = execute_mechanics(template, rotation, mechanics)

    assert out["status"] in ("passed", "unproven"), out["issues"]
    hit = next(row for row in out["resolved_template"]["hits"]
               if row.get("extensions", {}).get("special_reaction", {}).get(
                   "request_id") == "0:direct-stellar-side")
    special = hit["extensions"]["special_reaction"]
    assert special["area_id"] == "arena.side"
    assert special["settled_snapshot"]["layers"] == 1
    assert hit["reaction"] == {"type": "stellar_superconduct", "layers": 1}
    samples = [row for row in out["mechanics_trace"]
               if row.get("type") == "special_reaction_stat_sample"
               and row.get("event_id") == hit["id"]]
    assert len(samples) == 1 and samples[0]["area_id"] == "arena.side"
