from __future__ import annotations

import copy

import pytest
from test_action_events import confirm_action_events
from test_attack_metadata_app import metadata_native
from test_elemental_swirl import swirl_model
from test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from test_runtime_attack_metadata import candidate, metadata_db  # noqa: F401

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.action_dispatch_materialization import (
    action_dispatch_materialization_capabilities,
    compile_action_dispatch_inputs,
)
from genshin_app.equipment.action_event_schema import (
    APPLICATION_PROFILE,
    _check,
)
from genshin_app.equipment.attack_metadata_schema import (
    SOURCE_SCHEMA as ATTACK_SOURCE_SCHEMA,
)
from genshin_app.equipment.periodic_schema import (
    APPLICATION_PROFILE as MULTI_APPLICATION_PROFILE,
)
from genshin_app.equipment.periodic_schema import (
    SOURCE_SCHEMA as PERIODIC_SOURCE_SCHEMA,
)
from genshin_db.action_dispatch_schema import (
    ActionDispatchProfileError,
    validate_bundle,
)
from genshin_db.runtime_action_dispatch import (
    action_dispatch_capabilities,
    load_action_dispatch_bundle,
    resolve_action_dispatch_profile,
)
from genshin_db.runtime_queries import query_runtime, runtime_capabilities

# ruff: noqa: F811

H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64


def _ref(kind="database_pointer"):
    return {"kind": kind, "reference": "fixture://reviewed/source", "fingerprint": H2}


def _evidence(category="database_proven", status="confirmed"):
    return {"status": status, "category": category, "source_refs": [_ref()] if status == "confirmed" else []}


def _selector(**updates):
    value = {"version_line": "release", "character_id": 10000001, "skill_depot_id": 101,
        "skill_id": 1001, "slot": "skill", "effective_level": 10, "constellation": 0,
        "variant": "default", "cast_stage": None}
    value.update(updates)
    return value


def _snapshot(**updates):
    value = {"version_line": "release", "snapshot_id": "fixture-snapshot", "upstream_commit": H1,
             "manifest_fingerprint": H3, "indexer_version": 1,
             "normalization": {"profile_id": "fixture-normalization"}}
    value.update(updates)
    return value


def _emission(component="primary", *, kind="fixed", source_kind="attack_metadata",
              landed_policy="default", application="sequence"):
    schedule = ({"offset_seconds": 0.25, "precision_seconds": 0.001} if kind == "fixed" else
                {"first_offset_seconds": 0.5, "interval_seconds": 1.0, "count": 3,
                 "precision_seconds": 0.001})
    return {"component_id": component, "kind": kind,
        "attack_source": {"kind": source_kind, "candidate_id": "attack." + component,
            "origin_reference_id": H3 if source_kind == "periodic_attack" else None,
            "source_fingerprint": H2},
        "prototype": {"damage_type_ids": ["damage.elemental_skill"], "damage_system": "classic",
            "scaling_stat": "atk", "count": 1, "skill_label": "夹具技能",
            "hit_name": component, "description": "通用合成夹具"},
        "application": {"mode": application,
            "counter_tag": "icd." + component if application == "sequence" else None,
            "verification": {"status": "confirmed", "locator": "fixture://application/" + component,
                "version": H1, "reviewed_at": "2026-10-19", "component_scope": component,
                "confirmed_fields": (["application_mode", "counter_tag", "shared_scope"]
                    if application == "sequence" else ["application_mode", "shared_scope"])}},
        "schedule": schedule,
        "landed": {"policy": landed_policy, "default": True if landed_policy == "default" else None},
        "self_sampling": "action_start",
        "evidence": {name: _evidence("calibration_required" if name == "timing" else "database_proven")
                     for name in ("dispatch", "timing", "damage", "application")}}


def _profile(*emissions, selector=None, status="confirmed"):
    return {"profile_id": "fixture.dispatch", "status": status, "selector": selector or _selector(),
        "ruleset": "gauge-swirl-dynamic-v3", "emissions": list(emissions) or [_emission()],
        "lifecycle": {"establishment_offset_seconds": 0.1, "instance_end_offset_seconds": 4.0,
            "precision_seconds": 0.001, "owner_and_target_survive": True, "no_interrupt": True,
            "no_refresh_replacement_or_early_despawn": True, "evidence": _evidence("calibration_required")},
        "dataset": {"version_line": "release", "upstream_commit": H1,
            "normalization_profile_id": "fixture-normalization", "snapshot_id": None},
        "review": {"status": "reviewed" if status == "confirmed" else "pending", "date": "2026-10-19",
            "reviewer": "generic-fixture", "notes": "Synthetic profile; no character gameplay claim."}}


def _bundle(*profiles):
    return {"schema_version": 1, "profile_version": 1, "contract_id": "action-dispatch-profile-v1",
            "profiles": list(profiles)}


def _coverage_bundle(manifest):
    return {"schema_version": 1, "contract_id": "gameplay-action-manifest-v1",
            "manifests": [manifest]}


def _periodic_manifest(profile, component, schedule, *, deliveries=None):
    evidence = _evidence("calibration_required")
    return {"manifest_id": "fixture.periodic.topology", "status": "confirmed",
        "selector": copy.deepcopy(profile["selector"]),
        "components": [{"component_id": component,
            "critical_domains": ["damage", "application"],
            "evidence": copy.deepcopy(evidence)}],
        "topology": {"kind": "periodic_channels", "channels": [{
            "channel_id": component, "component_id": component,
            "schedule": copy.deepcopy(schedule),
            **({"deliveries": copy.deepcopy(deliveries)} if deliveries is not None else {}),
        }], "lifecycle": {"establishment_offset_seconds": .1,
            "instance_end_offset_seconds": 4.0, "refresh_policy": "none",
            "pause_resume_policy": "none", "off_field_policy": "continue",
            "evidence": copy.deepcopy(evidence)}, "evidence": copy.deepcopy(evidence)},
        "dataset": copy.deepcopy(profile["dataset"]),
        "review": {"status": "reviewed", "date": "2026-10-21",
            "reviewer": "generic-fixture", "notes": "Synthetic topology."}}


def _source_receipts(profile):
    return [{"component_id": emission["component_id"], "kind": emission["attack_source"]["kind"],
        "candidate_id": emission["attack_source"]["candidate_id"],
        "origin_reference_id": emission["attack_source"]["origin_reference_id"],
        "source_fingerprint": emission["attack_source"]["source_fingerprint"], "status": "ready",
        "application_identity_status": "ready" if emission["application"]["mode"] == "sequence" else "pending",
        "application_identity_tag": emission["application"]["counter_tag"],
        "interval_seconds": emission["schedule"].get("interval_seconds"),
        "classification": emission["prototype"]["damage_type_ids"][0]}
        for emission in profile["emissions"]]


def _resolved(profile):
    return resolve_action_dispatch_profile(_bundle(profile), selector=profile["selector"], snapshot=_snapshot(),
                                           source_receipts=_source_receipts(profile))


def _compile(resolution, **kwargs):
    selector = resolution["profile"]["selector"]
    values = {"ability_id": "ability.fixture", "actor": "fixture.actor",
        "actor_character_id": selector["character_id"],
        "ability_action_type_id": {"normal": "action.normal_attack", "skill": "action.elemental_skill",
                                   "burst": "action.elemental_burst"}[selector["slot"]],
        "ability_variant": selector["variant"]}
    values.update(kwargs)
    return compile_action_dispatch_inputs(resolution, **values)


def test_packaged_catalog_is_empty_until_reviewed_game_rows_are_added():
    bundle = load_action_dispatch_bundle()
    assert bundle == _bundle()
    capabilities = action_dispatch_capabilities()
    assert capabilities["safety"]["missing"] == "pending"
    assert capabilities["safety"]["execution_authority"] is False
    assert "mapping_targets" not in capabilities and "application_adapter" not in capabilities
    assert action_dispatch_materialization_capabilities()["safety"]["partial_materialization"] is False
    public = runtime_capabilities()
    assert "action_dispatch_profile" in public["actions"]
    assert public["action_dispatch_profile"]["safety"]["selection"] == "exact_only"
    assert "action_dispatch_profile" in public["batch"]["actions"]


def test_exact_reviewed_profile_resolves_and_neighbor_does_not_fallback():
    profile = _profile(_emission())
    ready = _resolved(profile)
    assert ready["status"] == "ready"
    assert ready["profile"]["profile_id"] == "fixture.dispatch"

    neighboring = copy.deepcopy(profile["selector"])
    neighboring["effective_level"] = 11
    missing = resolve_action_dispatch_profile(_bundle(profile), selector=neighboring, snapshot=_snapshot())
    assert missing["status"] == "pending"
    assert [issue["code"] for issue in missing["issues"]] == ["action_dispatch_profile_missing"]


def test_stale_or_ambiguous_profile_is_pending_without_a_selected_row():
    profile = _profile(_emission())
    stale = resolve_action_dispatch_profile(_bundle(profile), selector=profile["selector"],
                                             snapshot=_snapshot(upstream_commit="9" * 64))
    assert stale["status"] == "pending"
    assert stale["profile"] is None
    assert stale["issues"][0]["code"] == "action_dispatch_profile_stale"

    second = copy.deepcopy(profile)
    second["profile_id"] = "fixture.dispatch.duplicate"
    ambiguous = resolve_action_dispatch_profile(_bundle(profile, second), selector=profile["selector"],
                                                 snapshot=_snapshot())
    assert ambiguous["status"] == "pending"
    assert ambiguous["profile"] is None
    assert ambiguous["issues"][0]["code"] == "action_dispatch_profile_ambiguous"


def test_single_fixed_emission_maps_to_existing_v2_inputs():
    compiled = _compile(_resolved(_profile(_emission())))
    assert compiled["status"] == "ready"
    assert compiled["target_contract"] == "calibrated_single_application_v2"
    fragments = compiled["fragments"]
    assert not _check(fragments["action_event_profiles"][0], APPLICATION_PROFILE, "profile")
    assert not _check(fragments["attack_metadata_sources"][0], ATTACK_SOURCE_SCHEMA, "source")
    assert fragments["periodic_attack_sources"] == []
    assert fragments["prototype_requirements"][0]["owned_fields"] == ["element", "multiplier"]
    assert fragments["prototype_hits"] == [{"id": "dispatch.fixture.dispatch.primary.hit",
        "damage_type_ids": ["damage.elemental_skill"], "t": 0, "character": "fixture.actor",
        "skill": "夹具技能", "hit_name": "primary", "description": "通用合成夹具",
        "damage_system": "classic", "scaling_stat": "atk", "count": 1}]
    assert fragments["attack_metadata_sources"][0]["verification"]["status"] == "confirmed"


def test_zero_schedule_offsets_flow_through_dispatch_and_native_event_schemas():
    fixed = _emission()
    fixed["schedule"]["offset_seconds"] = 0
    fixed_profile = _profile(fixed)
    validate_bundle(_bundle(fixed_profile))
    fixed_compiled = _compile(_resolved(fixed_profile))
    assert fixed_compiled["status"] == "ready"
    assert not _check(
        fixed_compiled["fragments"]["action_event_profiles"][0], APPLICATION_PROFILE, "profile")
    assert fixed_compiled["fragments"]["action_event_profiles"][0]["calibration"][
        "offset_seconds"] == 0

    periodic = _emission("ticks", kind="periodic", source_kind="periodic_attack")
    periodic["schedule"]["first_offset_seconds"] = 0
    periodic["prototype"]["damage_type_ids"] = ["damage.elemental_burst"]
    periodic_profile = _profile(periodic, selector=_selector(slot="burst"))
    periodic_profile["lifecycle"]["establishment_offset_seconds"] = 0
    validate_bundle(_bundle(periodic_profile))
    periodic_compiled = _compile(_resolved(periodic_profile))
    assert periodic_compiled["status"] == "ready"
    assert not _check(
        periodic_compiled["fragments"]["action_event_profiles"][0],
        MULTI_APPLICATION_PROFILE,
        "profile",
    )
    assert periodic_compiled["fragments"]["action_event_profiles"][0]["channels"][0][
        "schedule"]["first_offset_seconds"] == 0


@pytest.mark.parametrize(
    ("kind", "field", "value"),
    [("fixed", "offset_seconds", -0.000001),
     ("periodic", "first_offset_seconds", -0.000001),
     ("periodic", "interval_seconds", 0)],
)
def test_negative_schedule_offsets_and_zero_period_are_rejected(kind, field, value):
    emission = _emission(
        "component", kind=kind,
        source_kind="periodic_attack" if kind == "periodic" else "attack_metadata",
    )
    emission["schedule"][field] = value
    with pytest.raises(ActionDispatchProfileError):
        validate_bundle(_bundle(_profile(emission)))


def test_zero_establishment_is_valid_but_negative_establishment_and_zero_end_are_rejected():
    profile = _profile(_emission())
    profile["lifecycle"]["establishment_offset_seconds"] = 0
    validate_bundle(_bundle(profile))

    profile["lifecycle"]["establishment_offset_seconds"] = -0.000001
    with pytest.raises(ActionDispatchProfileError):
        validate_bundle(_bundle(profile))

    profile["lifecycle"]["establishment_offset_seconds"] = 0
    profile["lifecycle"]["instance_end_offset_seconds"] = 0
    with pytest.raises(ActionDispatchProfileError):
        validate_bundle(_bundle(profile))


@pytest.mark.parametrize("mode", ["no_icd", "no_application"])
def test_non_sequence_application_modes_keep_native_positive_verification(mode):
    compiled = _compile(_resolved(_profile(_emission(application=mode))))
    assert compiled["status"] == "ready"
    source = compiled["fragments"]["attack_metadata_sources"][0]
    assert source["application_mode"] == mode
    assert source["verification"]["status"] == "confirmed"
    assert set(source["verification"]["confirmed_fields"]) >= {
        "application_mode", "shared_scope"}


def test_fixed_and_periodic_channels_map_atomically_to_existing_multi_v2_inputs():
    fixed = _emission("opening", source_kind="periodic_attack")
    periodic = _emission("ticks", kind="periodic", source_kind="periodic_attack", application="no_icd")
    for emission in (fixed, periodic):
        emission["prototype"]["damage_type_ids"] = ["damage.elemental_burst"]
    compiled = _compile(_resolved(_profile(fixed, periodic, selector=_selector(slot="burst"))))
    assert compiled["status"] == "ready"
    assert compiled["target_contract"] == "calibrated_multi_application_v2"
    fragments = compiled["fragments"]
    assert not _check(fragments["action_event_profiles"][0], MULTI_APPLICATION_PROFILE, "profile")
    assert not _check(fragments["periodic_attack_sources"][0], PERIODIC_SOURCE_SCHEMA, "source")
    channels = fragments["action_event_profiles"][0]["channels"]
    assert [row["schedule"]["kind"] for row in channels] == ["single", "periodic"]
    assert fragments["prototype_requirements"][1]["expected_interval_seconds"] == 1.0
    assert fragments["source_expectations"][1]["interval_seconds"] == 1.0
    assert fragments["periodic_attack_sources"][0]["channels"][1]["verification"]["status"] == "confirmed"


def test_explicit_offsets_use_fixed_value_prototype_and_reach_native_periodic_input():
    emission = _emission("storm", source_kind="periodic_attack")
    emission["schedule"]["offset_seconds"] = 0
    profile = _profile(emission)
    manifest = _periodic_manifest(profile, "storm", {
        "kind": "explicit_offsets", "offsets_seconds": [.1, .55, 1.4, 4.0]})
    resolution = resolve_action_dispatch_profile(
        _bundle(profile), selector=profile["selector"], snapshot=_snapshot(),
        source_receipts=_source_receipts(profile), coverage_bundle=_coverage_bundle(manifest))
    assert resolution["status"] == "ready", resolution["issues"] + resolution["coverage_issues"]
    compiled = _compile(resolution)
    assert compiled["status"] == "ready", compiled["issues"]
    channel_row = compiled["fragments"]["action_event_profiles"][0]["channels"][0]
    assert channel_row["schedule"] == {
        "kind": "explicit_offsets", "offsets_seconds": [.1, .55, 1.4, 4.0],
        "precision_seconds": .001, "status": "confirmed",
        "source": "gameplay_action_manifest:fixture.periodic.topology"}
    assert "interval_seconds" not in compiled["fragments"]["source_expectations"][0]
    assert not _check(compiled["fragments"]["action_event_profiles"][0],
                      MULTI_APPLICATION_PROFILE, "profile")


def test_resource_delivery_reference_stays_pending_without_closed_app_consumer():
    emission = _emission("storm", source_kind="periodic_attack")
    emission["schedule"]["offset_seconds"] = 0
    profile = _profile(emission)
    manifest = _periodic_manifest(profile, "storm", {
        "kind": "explicit_offsets", "offsets_seconds": [.1, 4.0]}, deliveries=[
            {"kind": "damage"}, {"kind": "application"},
            {"kind": "resource", "contract": "rotation-resource-event-v1",
             "event": {"type": "energy_spawn", "template_id": "particle.template"}},
        ])
    manifest["components"][0]["critical_domains"].append("resource")
    resolution = resolve_action_dispatch_profile(
        _bundle(profile), selector=profile["selector"], snapshot=_snapshot(),
        source_receipts=_source_receipts(profile), coverage_bundle=_coverage_bundle(manifest))
    assert resolution["status"] == "pending"
    assert resolution["coverage_status"] == "incomplete"
    assert {row["code"] for row in resolution["coverage_issues"]} == {
        "action_dispatch_coverage_resource_delivery_unsupported"}


def test_skill_periodic_profile_maps_without_burst_or_character_special_case():
    emission = _emission("storm", kind="periodic", source_kind="periodic_attack")
    profile = _profile(emission, selector=_selector(slot="skill"))
    compiled = _compile(_resolved(profile))
    assert compiled["status"] == "ready", compiled["issues"]
    native = compiled["fragments"]
    assert native["prototype_hits"][0]["damage_type_ids"] == ["damage.elemental_skill"]
    assert native["action_event_profiles"][0]["kind"] == "calibrated_multi_application_v2"


def test_periodic_count_may_be_derived_only_by_explicit_inclusive_end_policy():
    emission = _emission("storm", kind="periodic", source_kind="periodic_attack")
    emission["schedule"].update(count=None, derive_count="through_instance_end_inclusive")
    profile = _profile(emission)
    validate_bundle(_bundle(profile))
    compiled = _compile(_resolved(profile))
    assert compiled["status"] == "ready"
    schedule = compiled["fragments"]["action_event_profiles"][0]["channels"][0]["schedule"]
    assert schedule["count"] is None
    assert schedule["derive_count"] == "through_instance_end_inclusive"

    emission = _emission("storm", kind="periodic", source_kind="periodic_attack")
    emission["schedule"]["count"] = None
    with pytest.raises(ActionDispatchProfileError, match="derive_count"):
        validate_bundle(_bundle(_profile(emission)))


def test_reviewed_replacement_policy_is_preserved_in_native_lifecycle():
    emission = _emission("storm", kind="periodic", source_kind="periodic_attack")
    profile = _profile(emission)
    profile["lifecycle"].update(no_refresh_replacement_or_early_despawn=False,
        replacement_policy="replace_previous", no_early_despawn=True)
    validate_bundle(_bundle(profile))
    compiled = _compile(_resolved(profile))
    assert compiled["status"] == "ready"
    lifecycle = compiled["fragments"]["action_event_profiles"][0]["lifecycle"]
    assert lifecycle["replacement_policy"] == "replace_previous"
    assert lifecycle["no_early_despawn"] is True

    profile["lifecycle"]["no_early_despawn"] = False
    with pytest.raises(ActionDispatchProfileError, match="replace_previous"):
        validate_bundle(_bundle(profile))


def test_scenario_required_landed_blocks_all_fragments_until_supplied():
    profile = _profile(_emission(landed_policy="scenario_required"))
    pending = _compile(_resolved(profile))
    assert pending["status"] == "pending"
    assert pending["fragments"] is None
    assert {issue["code"] for issue in pending["issues"]} == {"action_dispatch_scenario_required"}

    ready = _compile(_resolved(profile), scenario_calibration={"primary": {"landed": False, "order": 7}})
    assert ready["status"] == "pending"
    assert ready["fragments"] is None
    assert "action_dispatch_order_binding_required" in {issue["code"] for issue in ready["issues"]}

    ready = _compile(_resolved(profile), scenario_calibration={"primary": {"landed": False}})
    assert ready["status"] == "pending"
    assert ready["fragments"] is None
    assert "action_dispatch_landed_binding_required" in {issue["code"] for issue in ready["issues"]}


def test_pending_evidence_and_unrepresentable_mixed_sources_never_emit_a_partial_prefix():
    pending_profile = _profile(_emission())
    pending_profile["emissions"][0]["evidence"]["dispatch"] = _evidence("calibration_required", "pending")
    pending = _compile(_resolved(pending_profile))
    assert pending["status"] == "pending"
    assert pending["fragments"] is None

    mixed = _profile(_emission("one"), _emission("two", source_kind="periodic_attack"))
    blocked = _compile(_resolved(mixed))
    assert blocked["status"] == "pending"
    assert blocked["fragments"] is None
    assert "action_dispatch_target_contract_unavailable" in {issue["code"] for issue in blocked["issues"]}

    multiple_fixed = _profile(_emission("one"), _emission("two"))
    blocked = _compile(_resolved(multiple_fixed))
    assert blocked["status"] == "pending"
    assert blocked["fragments"] is None
    assert "action_dispatch_target_contract_unavailable" in {issue["code"] for issue in blocked["issues"]}


def test_stage_specific_profile_waits_for_a_stage_aware_native_binding():
    profile = _profile(_emission(), selector=_selector(cast_stage=2))
    compiled = _compile(_resolved(profile))
    assert compiled["status"] == "pending"
    assert compiled["fragments"] is None
    assert "action_dispatch_cast_stage_unrepresentable" in {issue["code"] for issue in compiled["issues"]}


def test_fresh_source_fingerprint_and_periodic_interval_are_execution_gates():
    profile = _profile(_emission("ticks", kind="periodic", source_kind="periodic_attack"))
    receipts = _source_receipts(profile)
    receipts[0]["source_fingerprint"] = "9" * 64
    mismatch = resolve_action_dispatch_profile(_bundle(profile), selector=profile["selector"], snapshot=_snapshot(),
                                               source_receipts=receipts)
    assert mismatch["status"] == "pending"
    assert "action_dispatch_source_fingerprint_mismatch" in {row["code"] for row in mismatch["issues"]}

    receipts = _source_receipts(profile)
    receipts[0]["interval_seconds"] = 0.5
    mismatch = resolve_action_dispatch_profile(_bundle(profile), selector=profile["selector"], snapshot=_snapshot(),
                                               source_receipts=receipts)
    assert mismatch["status"] == "pending"
    assert "action_dispatch_interval_mismatch" in {row["code"] for row in mismatch["issues"]}


def test_fresh_source_classification_and_plan_actor_identity_are_execution_gates():
    profile = _profile(_emission())
    receipts = _source_receipts(profile)
    receipts[0]["classification"] = "damage.elemental_burst"
    mismatch = resolve_action_dispatch_profile(_bundle(profile), selector=profile["selector"], snapshot=_snapshot(),
                                               source_receipts=receipts)
    assert mismatch["status"] == "pending"
    assert "action_dispatch_classification_mismatch" in {row["code"] for row in mismatch["issues"]}

    compiled = _compile(_resolved(profile), actor_character_id=10000002)
    assert compiled["status"] == "pending"
    assert compiled["fragments"] is None
    assert "action_dispatch_actor_identity_mismatch" in {row["code"] for row in compiled["issues"]}


def test_positive_no_icd_verification_cannot_override_a_fresh_counter_identity():
    profile = _profile(_emission(application="no_icd"))
    receipts = _source_receipts(profile)
    receipts[0].update(application_identity_status="ready", application_identity_tag="fresh.counter")
    mismatch = resolve_action_dispatch_profile(_bundle(profile), selector=profile["selector"], snapshot=_snapshot(),
                                               source_receipts=receipts)
    assert mismatch["status"] == "pending"
    assert "action_dispatch_application_identity_conflict" in {row["code"] for row in mismatch["issues"]}


def test_default_landed_cannot_be_overridden_without_a_concrete_action_binding():
    profile = _profile(_emission())
    compiled = _compile(_resolved(profile), scenario_calibration={"primary": {"landed": False}})
    assert compiled["status"] == "pending" and compiled["fragments"] is None
    assert "action_dispatch_landed_override_binding_required" in {row["code"] for row in compiled["issues"]}


def test_mapped_single_profile_reaches_real_composer_and_elemental_runtime(metadata_db):
    plan = metadata_native(metadata_db)
    existing_source = plan["attack_metadata_sources"][0]
    found = candidate(metadata_db)
    from genshin_db.runtime_attack_metadata import get_runtime_attack_metadata_profile

    query = get_runtime_attack_metadata_profile(metadata_db, character_id=90000001, slot="skill",
        constellation=0, talent_level=10, candidate_id=found["candidate_id"])
    selected = query["selected_candidate"]
    selection = query["selection"]
    selector = _selector(character_id=selection["character_id"], skill_depot_id=selection["skill_depot_id"],
        skill_id=selection["skill_id"], slot="skill", effective_level=10,
        constellation=selection["constellation"], variant=plan["rotation_model"]["abilities"][0]["variant"])
    emission = _emission()
    emission["attack_source"].update(candidate_id=found["candidate_id"],
                                     source_fingerprint=query["source_fingerprint"])
    emission["application"].update(counter_tag=existing_source["counter_tag"],
                                    verification=copy.deepcopy(existing_source["verification"]))
    profile = _profile(emission, selector=selector)
    profile["dataset"] = {"version_line": "release", "upstream_commit": query["snapshot"]["upstream_commit"],
        "normalization_profile_id": query["snapshot"]["normalization"]["profile_id"], "snapshot_id": None}
    identity = selected["projection"]["application_identity"]
    receipt = {"component_id": "primary", "kind": "attack_metadata", "candidate_id": found["candidate_id"],
        "origin_reference_id": None, "source_fingerprint": query["source_fingerprint"], "status": query["status"],
        "application_identity_status": identity["status"], "application_identity_tag": identity.get("tag"),
        "interval_seconds": None, "classification": "damage.elemental_skill"}
    resolution = resolve_action_dispatch_profile(_bundle(profile), selector=selector, snapshot=query["snapshot"],
                                                 source_receipts=[receipt])
    ability = plan["rotation_model"]["abilities"][0]
    compiled = compile_action_dispatch_inputs(resolution, ability_id=ability["id"], actor=ability["character"],
        actor_character_id=selector["character_id"], ability_action_type_id=ability["action_type_id"],
        ability_variant=ability["variant"])
    assert compiled["status"] == "ready", compiled["issues"]
    fragments = compiled["fragments"]
    plan["manual_template"]["hits"] = fragments["prototype_hits"]
    for key in ("action_event_profiles", "action_event_bindings", "attack_metadata_sources"):
        plan[key] = fragments[key]
    plan["action_event_overrides"] = []
    elemental = swirl_model("cryo")
    elemental["ruleset"] = "gauge-swirl-dynamic-v3"
    elemental["profiles"] = []
    plan["mechanics_model"]["elemental_model"] = elemental
    preview = compose_equipment_plan(plan, metadata_db)
    assert preview["attack_metadata_compilations"][0]["status"] == "ready"
    assert preview["action_event_compilations"][0]["status"] == "pending"
    confirm_action_events(plan, metadata_db)
    result = run_plan_calc(plan, metadata_db)
    assert result["report"]["state"] == "ready"
    assert len(result["result"]["hits"]) == 3
    attempts = [row for row in result["result"]["elemental"]["trace"]
                if row["type"] == "elemental_application"]
    assert len(attempts) == 3


def test_production_query_derives_single_classification_from_fresh_attack_tag(metadata_db, monkeypatch):
    import genshin_db.runtime_action_dispatch as dispatch
    from genshin_db.runtime_attack_metadata import get_runtime_attack_metadata_profile

    plan = metadata_native(metadata_db)
    found = candidate(metadata_db)
    discovery = get_runtime_attack_metadata_profile(metadata_db, character_id=90000001, slot="skill",
        constellation=0, talent_level=10, candidate_id=found["candidate_id"])
    selection = discovery["selection"]
    query = get_runtime_attack_metadata_profile(metadata_db, character_id=selection["character_id"],
        skill_depot_id=selection["skill_depot_id"], skill_id=selection["skill_id"], constellation=0,
        talent_level=10, candidate_id=found["candidate_id"])
    selector = _selector(character_id=selection["character_id"], skill_depot_id=selection["skill_depot_id"],
        skill_id=selection["skill_id"], slot="skill", effective_level=10,
        constellation=selection["constellation"], variant=plan["rotation_model"]["abilities"][0]["variant"])
    emission = _emission()
    emission["attack_source"].update(candidate_id=found["candidate_id"],
                                     source_fingerprint=query["source_fingerprint"])
    native_source = plan["attack_metadata_sources"][0]
    emission["application"].update(counter_tag=native_source["counter_tag"],
                                   verification=copy.deepcopy(native_source["verification"]))
    profile = _profile(emission, selector=selector)
    profile["dataset"] = {"version_line": "release", "upstream_commit": query["snapshot"]["upstream_commit"],
        "normalization_profile_id": query["snapshot"]["normalization"]["profile_id"], "snapshot_id": None}
    monkeypatch.setattr(dispatch, "load_action_dispatch_bundle", lambda: _bundle(profile))

    resolved = dispatch.get_runtime_action_dispatch_profile(metadata_db, character_id=selector["character_id"],
        skill_depot_id=selector["skill_depot_id"], skill_id=selector["skill_id"], slot=selector["slot"],
        effective_level=selector["effective_level"], constellation=selector["constellation"],
        variant=selector["variant"])
    assert resolved["status"] == "ready", resolved["issues"]
    assert resolved["source_verification"]["receipts"][0]["classification"] == "damage.elemental_skill"


def test_public_runtime_query_and_batch_report_missing_reviewed_catalog_row(metadata_db):
    metadata = query_runtime(metadata_db, {"action": "attack_metadata_profile", "character_id": 90000001,
        "slot": "skill", "constellation": 0, "talent_level": 10})
    selection = metadata["selection"]
    request = {"action": "action_dispatch_profile", "character_id": selection["character_id"],
        "skill_depot_id": selection["skill_depot_id"], "skill_id": selection["skill_id"], "slot": "skill",
        "effective_level": 10, "constellation": 0, "variant": "tap"}
    direct = query_runtime(metadata_db, request)
    assert direct["status"] == "pending"
    assert direct["issues"][0]["code"] == "action_dispatch_profile_missing"
    assert direct["snapshot"]["snapshot_id"] == metadata["snapshot"]["snapshot_id"]

    batched = query_runtime(metadata_db, {"action": "batch", "items": [{"id": "dispatch", "request": request}]})
    assert batched["status"] == "complete"
    assert batched["items"][0]["data"]["status"] == "pending"


def test_bundle_validation_is_closed_and_rejects_unproved_sequence_identity():
    bundle = _bundle(_profile(_emission()))
    bundle["profiles"][0]["unexpected"] = True
    with pytest.raises(ActionDispatchProfileError, match="unknown fields"):
        validate_bundle(bundle)

    broken = _bundle(_profile(_emission(application="sequence")))
    broken["profiles"][0]["emissions"][0]["application"]["counter_tag"] = None
    with pytest.raises(ActionDispatchProfileError, match="requires a counter_tag"):
        validate_bundle(broken)

    scenario_as_stored_fact = _bundle(_profile(_emission()))
    scenario_as_stored_fact["profiles"][0]["emissions"][0]["evidence"]["timing"][
        "category"] = "scenario_required"
    with pytest.raises(ActionDispatchProfileError, match="cannot be confirmed in a stored profile"):
        validate_bundle(scenario_as_stored_fact)
