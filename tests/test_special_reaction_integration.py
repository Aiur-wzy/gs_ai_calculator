from __future__ import annotations

import copy

from genshin_engine.rotation import _schema_errors
from genshin_engine.special_reaction_capabilities import (
    SPECIAL_CAPABILITY_CATALOG_TRACK,
    SPECIAL_CAPABILITY_SNAPSHOT_MATCH,
    load_reviewed_special_reaction_capabilities,
    project_special_reaction_party_capabilities,
    special_reaction_capability_entry,
)
from genshin_engine.special_reaction_integration import (
    build_special_reaction_runtime,
    validate_special_reaction_bindings,
)
from genshin_engine.special_reaction_schema import SPECIAL_REACTION_EVENT_SCHEMA
from genshin_engine.special_reactions import (
    REQUIRED_CAPABILITY_TAGS,
    REVIEWED_SPECIAL_REACTION_PROFILES,
    direct_lunar_damage_profile_content_hash,
)

PROVIDER_DB_IDS = {
    "stellar_superconduct": 10000133,
    "stellar_swirl": 10000150,
    "lunar_electro": 10000116,
    "lunar_bloom": 10000119,
    "lunar_crystallize": 10000126,
}


def equipment_snapshot(character_id: int, *, skill_depot_id: int | None = None,
                       version_line: str | None = None,
                       upstream_commit: str | None = None) -> dict:
    catalog = load_reviewed_special_reaction_capabilities()
    line = catalog["version_line"] if version_line is None else version_line
    reference = {"db_character_id": character_id, "version_line": line}
    if skill_depot_id is not None:
        reference["skill_depot_id"] = skill_depot_id
    return {
        "character_ref": reference,
        "data_version": {
            "version_line": line,
            "upstream_commit": (
                catalog["source_upstream_commit"]
                if upstream_commit is None else upstream_commit),
        },
    }


def replacement(reaction_type: str) -> dict:
    reviewed = REVIEWED_SPECIAL_REACTION_PROFILES[reaction_type]
    parameters = copy.deepcopy(reviewed["parameters"])
    status = "confirmed"
    row = {
        "status": status,
        "source": "reviewed integration fixture",
        "profile_id": reviewed["profile_id"],
        "profile_revision": reviewed["profile_revision"],
        "profile_content_hash": reviewed["profile_content_hash"],
        **parameters,
    }
    if not reviewed["confirmed_ready"]:
        row["status"] = "assumed"
        row["override_reason"] = "unresolved Harmony cooldown fixed by scenario"
        if reaction_type == "lunar_crystallize":
            row["harmony_attack_cooldown_seconds"] = .2
            row["harmony_attack_cooldown_scope"] = "area"
    return {
        "enabled": True,
        "qualification": {
            "status": "confirmed",
            "provider_ids": ["provider"],
            "capability_tag": REQUIRED_CAPABILITY_TAGS[reaction_type],
            "source": "database capability fixture",
        },
        "mechanics": row,
    }


def mechanics(reaction_type="lunar_bloom") -> dict:
    catalog = load_reviewed_special_reaction_capabilities()
    entry = special_reaction_capability_entry(PROVIDER_DB_IDS[reaction_type])
    return {
        "schema_version": 1,
        "rules": [],
        "hit_events": [],
        "hit_patterns": [],
        "condition_events": [],
        "initial_state": {"rules": []},
        "elemental_model": {
            "ruleset": "gauge-multi-aura-v4",
            "target": {"id": "enemy"},
        },
        "special_reaction_model": {
            "schema_version": 1,
            "replacements": {reaction_type: replacement(reaction_type)},
        },
        "special_reaction_party_capabilities": [{
            "character_id": "provider",
            "db_character_id": PROVIDER_DB_IDS[reaction_type],
            "skill_depot_id": entry["required_skill_depot_id"],
            "capability_tags": [REQUIRED_CAPABILITY_TAGS[reaction_type]],
            "runtime_profile_id": entry["runtime_profile_id"],
            "catalog_entry_fingerprint": entry["entry_fingerprint"],
            "version_line": catalog["version_line"],
            "catalog_id": catalog["collection_id"],
            "catalog_revision": catalog["revision"],
            "catalog_content_hash": catalog["content_hash"],
            "catalog_track": SPECIAL_CAPABILITY_CATALOG_TRACK,
            "catalog_source_upstream_commit": catalog["source_upstream_commit"],
            "equipment_version_line": catalog["version_line"],
            "equipment_upstream_commit": catalog["source_upstream_commit"],
            "projection_reason_code": SPECIAL_CAPABILITY_SNAPSHOT_MATCH,
            "status": "confirmed",
            "source": "database capability fixture",
        }],
        "special_reaction_events": [],
    }


def codes(value: dict, *, roster=("provider", "consumer"), actions=("cast",)) -> set[str]:
    return {row["code"] for row in validate_special_reaction_bindings(
        value, character_ids=roster, action_ids=actions,
        action_bindings={
            "cast": {
                "character_id": "provider",
                "ability_id": "provider.skill",
                "variant": "default",
                "effective_level": 10,
            },
        })}


def direct_lunar_profile(reaction_type="lunar_bloom", *, status="assumed") -> dict:
    elements = {
        "lunar_electro": "electro",
        "lunar_bloom": "dendro",
        "lunar_crystallize": "geo",
    }
    value = {
        "profile_id": f"ability.integration.{reaction_type}.v2",
        "profile_revision": 1,
        "profile_content_hash": "0"*64,
        "authority": ("reviewed_database_projection" if status == "confirmed"
                      else "explicit_assumption"),
        "status": status,
        "source": ("reviewed integration ability profile"
                   if status == "confirmed"
                   else "explicit integration ability profile assumption"),
        "source_refs": [{"kind": "test_database", "id": reaction_type}],
        "parameters": {
            "reaction_type": reaction_type,
            "element": elements[reaction_type],
            "multiplier": 2.4,
            "scaling_stat": "em",
            "count": 2,
            "component": f"integration_direct_{reaction_type}",
        },
    }
    value["ability_binding"] = {
        "character_id": "provider",
        "ability_id": "provider.skill",
        "component_id": value["parameters"]["component"],
        "variant_id": "default",
        "effective_level": 10,
        "database_fingerprint": "1" * 64,
    }
    value["profile_content_hash"] = direct_lunar_damage_profile_content_hash(value)
    return value


def direct_lunar_event(reaction_type="lunar_bloom", *, status="assumed") -> dict:
    return {
        "id": "direct-lunar", "kind": "direct_lunar_damage", "action_id": "cast",
        "target_id": "enemy", "area_id": "battlefield.primary", "t": 2,
        "order": 1, "ability_profile": direct_lunar_profile(
            reaction_type, status=status),
        "status": status, "source": "reviewed action occurrence fixture",
    }


def event_schema_issues(event: dict) -> list[dict]:
    issues: list[dict] = []
    _schema_errors(event, SPECIAL_REACTION_EVENT_SCHEMA, "event", issues)
    return issues


def test_switch_requires_real_roster_member_and_database_capability_projection():
    value = mechanics("lunar_electro")
    assert not codes(value)
    assert "special_provider_absent" in codes(value, roster=("consumer",))
    value["special_reaction_party_capabilities"][0]["capability_tags"] = [
        REQUIRED_CAPABILITY_TAGS["lunar_bloom"]]
    assert "special_capability_unproven" in codes(value)


def test_pending_capability_fails_closed_in_binding_layer():
    value = mechanics("stellar_swirl")
    value["special_reaction_party_capabilities"][0]["status"] = "pending"
    assert {"special_capability_pending", "special_capability_unproven"} <= codes(value)


def test_capability_row_cannot_forge_character_tag_or_catalog_snapshot():
    value = mechanics("lunar_electro")
    value["special_reaction_party_capabilities"][0]["db_character_id"] = 10000133
    assert {"special_capability_forged", "special_capability_unproven"} <= codes(value)
    value = mechanics("lunar_electro")
    value["special_reaction_party_capabilities"][0]["catalog_content_hash"] = "0" * 64
    assert {"special_capability_catalog_mismatch", "special_capability_unproven"} <= codes(value)


def test_resource_variant_requires_explicit_resource_configuration():
    value = mechanics("stellar_superconduct")
    entry = special_reaction_capability_entry(10000136)
    row = value["special_reaction_party_capabilities"][0]
    row.update(
        db_character_id=entry["character_id"],
        skill_depot_id=entry["required_skill_depot_id"],
        capability_tags=entry["capability_tags"],
        runtime_profile_id=entry["runtime_profile_id"],
        catalog_entry_fingerprint=entry["entry_fingerprint"],
    )
    assert "special_capability_resource_profile_required" in codes(value)


def test_special_reactions_require_multi_aura_transaction_entry():
    value = mechanics()
    value["elemental_model"]["ruleset"] = "gauge-reaction-v1"
    assert "special_reaction_ruleset" in codes(value)


def test_lunar_bloom_consumer_is_bounded_and_references_action_and_target():
    value = mechanics()
    value["special_reaction_events"] = [{
        "id": "consume", "kind": "lunar_bloom_consume", "action_id": "cast",
        "target_id": "enemy", "area_id": "battlefield.primary", "t": 2,
        "order": 1, "dew": 1, "status": "confirmed", "source": "skill profile fixture",
    }]
    assert not codes(value)
    value["special_reaction_events"][0]["action_id"] = "missing"
    assert "reference" in codes(value)


def test_direct_lunar_damage_schema_and_binding_accept_each_supported_type():
    for reaction_type in ("lunar_electro", "lunar_bloom", "lunar_crystallize"):
        value = mechanics(reaction_type)
        event = direct_lunar_event(reaction_type)
        assert event_schema_issues(event) == []
        value["special_reaction_events"] = [event]
        assert not codes(value)


def test_direct_lunar_legacy_profile_is_schema_readable_but_binding_pending():
    event = direct_lunar_event("lunar_bloom")
    event["ability_profile"].pop("ability_binding")
    event["ability_profile"]["profile_content_hash"] = (
        direct_lunar_damage_profile_content_hash(event["ability_profile"]))
    # Schema keeps the historical shape readable so callers receive one clear
    # migration diagnostic; the integration gate still prevents execution.
    assert event_schema_issues(event) == []

    value = mechanics("lunar_bloom")
    value["special_reaction_events"] = [event]
    issues = validate_special_reaction_bindings(
        value,
        character_ids=("provider", "consumer"),
        action_ids=("cast",),
        action_bindings={"cast": {
            "character_id": "provider", "ability_id": "provider.skill",
            "variant": "default", "effective_level": 10,
        }},
    )
    migration = next(row for row in issues
                     if row["code"] == "direct_lunar_profile_ability_binding_required")
    assert migration["severity"] == "pending"


def test_direct_lunar_damage_binding_rejects_tamper_disabled_switch_and_hidden_assumption():
    value = mechanics("lunar_bloom")
    event = direct_lunar_event("lunar_bloom")
    event["ability_profile"]["parameters"]["multiplier"] = 999
    value["special_reaction_events"] = [event]
    assert "direct_lunar_profile_hash" in codes(value)

    value = mechanics("lunar_bloom")
    value["special_reaction_events"] = [direct_lunar_event("lunar_electro")]
    assert "special_switch_disabled" in codes(value)

    value = mechanics("lunar_bloom")
    event = direct_lunar_event("lunar_bloom", status="assumed")
    event["status"] = "confirmed"
    value["special_reaction_events"] = [event]
    assert "direct_lunar_status_mismatch" in codes(value)


def test_direct_lunar_damage_schema_rejects_type_element_mismatch_and_external_factor():
    event = direct_lunar_event("lunar_electro")
    event["ability_profile"]["parameters"]["element"] = "geo"
    value = mechanics("lunar_electro")
    event["ability_profile"]["profile_content_hash"] = (
        direct_lunar_damage_profile_content_hash(event["ability_profile"]))
    value["special_reaction_events"] = [event]
    assert "direct_lunar_profile_value" in codes(value)

    event = direct_lunar_event("lunar_electro")
    event["ability_profile"]["parameters"]["system_factor"] = 999
    assert event_schema_issues(event)


def test_runtime_binding_passes_only_reviewed_or_assumed_capability_rows():
    value = mechanics("lunar_electro")
    runtime = build_special_reaction_runtime(value, ["provider", "consumer"])
    assert runtime.party_capability_tags["provider"] == (
        REQUIRED_CAPABILITY_TAGS["lunar_electro"],)
    value["special_reaction_party_capabilities"][0]["status"] = "pending"
    runtime = build_special_reaction_runtime(value, ["provider", "consumer"])
    assert runtime.party_capability_tags["provider"] == ()


def test_reviewed_catalog_is_complete_for_each_native_reaction_and_detached():
    catalog = load_reviewed_special_reaction_capabilities()
    tags = {
        tag for row in catalog["entries"] for tag in row["capability_tags"]
    }
    assert tags == set(REQUIRED_CAPABILITY_TAGS.values())
    assert special_reaction_capability_entry(10000150)["capability_tags"] == [
        REQUIRED_CAPABILITY_TAGS["stellar_superconduct"],
        REQUIRED_CAPABILITY_TAGS["stellar_swirl"],
    ]
    catalog["entries"].clear()
    assert load_reviewed_special_reaction_capabilities()["entries"]


def test_catalog_preserves_supported_resource_profile_identity():
    sandrone = special_reaction_capability_entry(10000133)
    assert sandrone["capability_tags"] == [
        REQUIRED_CAPABILITY_TAGS["stellar_superconduct"],
        REQUIRED_CAPABILITY_TAGS["stellar_swirl"],
    ]
    mitya = special_reaction_capability_entry(10000136)
    assert mitya["runtime_support_status"] == "supported"
    assert mitya["runtime_profile_id"] == "stellar_conduct.gradient_beacon_v1"


def test_app_projection_derives_tags_from_stable_database_character_id():
    rows = project_special_reaction_party_capabilities({
        "provider": equipment_snapshot(10000150),
        "ordinary": equipment_snapshot(10000016),
    })
    assert len(rows) == 1
    assert rows[0]["character_id"] == "provider"
    assert rows[0]["capability_tags"] == [
        REQUIRED_CAPABILITY_TAGS["stellar_superconduct"],
        REQUIRED_CAPABILITY_TAGS["stellar_swirl"],
    ]
    value = mechanics("stellar_swirl")
    value["special_reaction_party_capabilities"] = rows
    assert not codes(value)


def test_anastasya_capability_projection_requires_real_identity_and_current_catalog():
    from genshin_engine.special_reaction_capabilities import verified_capability_tags

    rows = project_special_reaction_party_capabilities({
        "anastasya": equipment_snapshot(10000149, skill_depot_id=14901),
        "wrong_form": equipment_snapshot(10000149, skill_depot_id=13301),
        "ordinary": equipment_snapshot(10000016),
    })
    assert [row["character_id"] for row in rows] == ["anastasya"]
    assert set(verified_capability_tags(rows[0])) == {
        REQUIRED_CAPABILITY_TAGS["stellar_superconduct"],
        REQUIRED_CAPABILITY_TAGS["stellar_swirl"],
    }
    assert special_reaction_capability_entry(10000149)["evidence"] == {
        "proud_skill_group_id": 14923,
        "proud_skill_id": 1492301,
        "desc_text_map_hash": "3047748675",
    }
    forged = copy.deepcopy(rows[0])
    forged["db_character_id"] = 10000016
    assert not verified_capability_tags(forged)
    stale = copy.deepcopy(rows[0])
    stale["catalog_revision"] -= 1
    assert not verified_capability_tags(stale)


def test_app_projection_requires_exact_traveler_form_and_includes_resource_variant():
    rows = project_special_reaction_party_capabilities({
        "wrong_form": equipment_snapshot(10000005, skill_depot_id=501),
        "cryo_form": equipment_snapshot(10000005, skill_depot_id=505),
        "resource_variant": equipment_snapshot(10000136),
    })
    assert [row["character_id"] for row in rows] == ["cryo_form", "resource_variant"]


def test_app_projection_fails_closed_with_auditable_pending_snapshot_reasons():
    catalog = load_reviewed_special_reaction_capabilities()
    missing = equipment_snapshot(10000150)
    missing.pop("data_version")
    wrong_line = equipment_snapshot(10000150)
    wrong_line["data_version"]["version_line"] = "beta"
    stale = equipment_snapshot(10000150, upstream_commit="0" * 40)

    rows = project_special_reaction_party_capabilities({
        "missing": missing, "wrong_line": wrong_line, "stale": stale,
    })

    assert {row["character_id"]: row["projection_reason_code"] for row in rows} == {
        "missing": "equipment_data_version_missing",
        "stale": "equipment_upstream_commit_mismatch",
        "wrong_line": "equipment_version_line_mismatch",
    }
    assert all(row["status"] == "pending" for row in rows)
    assert all(row["catalog_track"] == "beta" for row in rows)
    assert all(row["catalog_source_upstream_commit"] == catalog["source_upstream_commit"]
               for row in rows)
    value = mechanics("stellar_swirl")
    value["special_reaction_party_capabilities"] = rows
    assert {"special_capability_pending", "special_capability_unproven"} <= codes(
        value, roster=("missing", "wrong_line", "stale"))
