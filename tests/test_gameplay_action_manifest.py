import copy

import pytest

from genshin_db.gameplay_action_manifest import (
    GameplayActionManifestError,
    validate_bundle,
)
from genshin_db.runtime_gameplay_action_manifest import (
    materialization_expectation,
    resolve_gameplay_action_manifest,
)


def _evidence():
    return {"status": "confirmed", "category": "calibration_required", "source_refs": [{
        "kind": "calibration_record", "reference": "synthetic topology fixture",
        "fingerprint": "1" * 64,
    }]}


def _selector():
    return {"version_line": "release", "character_id": 1001, "skill_depot_id": 2001,
        "skill_id": 3001, "slot": "skill", "effective_level": 10,
        "constellation": 0, "variant": "default", "cast_stage": None}


def _dataset():
    return {"version_line": "release", "upstream_commit": "fixture-commit",
        "normalization_profile_id": "fixture.normalization", "snapshot_id": None}


def _snapshot():
    return {"version_line": "release", "snapshot_id": "snapshot-1",
        "upstream_commit": "fixture-commit", "normalization": {
            "profile_id": "fixture.normalization"}}


def _finite_manifest():
    evidence = _evidence()
    return {"manifest_id": "fixture.finite", "status": "confirmed", "selector": _selector(),
        "components": [
            {"component_id": "loop", "critical_domains": ["damage", "application"],
             "evidence": copy.deepcopy(evidence)},
            {"component_id": "finale", "critical_domains": ["damage"],
             "evidence": copy.deepcopy(evidence)},
        ],
        "topology": {"kind": "finite_sequence", "steps": [
            {"step_id": "loop.1", "ordinal": 1, "component_id": "loop",
             "offset_seconds": .1, "order": 0},
            {"step_id": "loop.2", "ordinal": 2, "component_id": "loop",
             "offset_seconds": .3, "order": 0},
            {"step_id": "loop.3", "ordinal": 3, "component_id": "loop",
             "offset_seconds": .5, "order": 0},
            {"step_id": "finale", "ordinal": 4, "component_id": "finale",
             "offset_seconds": .8, "order": 0},
        ], "terminal_step_id": "finale", "interruption_policy": "none",
            "evidence": copy.deepcopy(evidence)},
        "dataset": _dataset(),
        "review": {"status": "reviewed", "date": "2026-10-21",
                   "reviewer": "test", "notes": "Synthetic independent topology."}}


def _periodic_manifest():
    manifest = _finite_manifest()
    manifest["manifest_id"] = "fixture.periodic"
    manifest["components"] = [
        {"component_id": "opening", "critical_domains": ["damage"],
         "evidence": _evidence()},
        {"component_id": "storm", "critical_domains": ["damage", "application"],
         "evidence": _evidence()},
    ]
    manifest["topology"] = {"kind": "periodic_channels", "channels": [
        {"channel_id": "opening", "component_id": "opening",
         "schedule": {"kind": "fixed", "offset_seconds": .2}},
        {"channel_id": "storm", "component_id": "storm",
         "schedule": {"kind": "periodic", "first_offset_seconds": .25,
                      "interval_seconds": .5, "count": None,
                      "derive_count": "through_instance_end_inclusive"}},
    ], "lifecycle": {"establishment_offset_seconds": .1,
        "instance_end_offset_seconds": 1.25, "refresh_policy": "replace_previous",
        "pause_resume_policy": "none", "off_field_policy": "continue",
        "evidence": _evidence()}, "evidence": _evidence()}
    return manifest


def _bundle(*manifests):
    return {"schema_version": 1, "contract_id": "gameplay-action-manifest-v1",
            "manifests": list(manifests)}


def test_finite_manifest_separates_unique_components_from_occurrences():
    manifest = _finite_manifest()
    validate_bundle(_bundle(manifest))
    expected = materialization_expectation(manifest)
    assert expected["status"] == "ready"
    assert [row["component_id"] for row in expected["components"]] == ["loop", "finale"]
    assert [row["occurrence_id"] for row in expected["occurrences"]] == [
        "loop.1", "loop.2", "loop.3", "finale"]
    assert expected["occurrences"][-1]["terminal"] is True


def test_finite_manifest_rejects_bad_terminal_duplicate_position_and_unused_component():
    manifest = _finite_manifest()
    manifest["topology"]["terminal_step_id"] = "loop.2"
    with pytest.raises(GameplayActionManifestError, match="last ordered step"):
        validate_bundle(_bundle(manifest))

    manifest = _finite_manifest()
    manifest["topology"]["steps"][1].update(offset_seconds=.1, order=0)
    with pytest.raises(GameplayActionManifestError, match="distinct order"):
        validate_bundle(_bundle(manifest))

    manifest = _finite_manifest()
    manifest["topology"]["steps"] = manifest["topology"]["steps"][:-1]
    manifest["topology"]["terminal_step_id"] = "loop.3"
    with pytest.raises(GameplayActionManifestError, match="every declared component"):
        validate_bundle(_bundle(manifest))

    manifest = _finite_manifest()
    manifest["topology"]["steps"][0]["ordinal"] = 2
    manifest["topology"]["steps"][1]["ordinal"] = 1
    with pytest.raises(GameplayActionManifestError, match="chronological"):
        validate_bundle(_bundle(manifest))


def test_periodic_manifest_derives_count_and_preserves_lifecycle_policies():
    manifest = _periodic_manifest()
    validate_bundle(_bundle(manifest))
    expected = materialization_expectation(manifest)
    assert [row["expected_occurrences"] for row in expected["channels"]] == [1, 3]
    assert len(expected["occurrences"]) == 4
    assert expected["lifecycle"]["refresh_policy"] == "replace_previous"
    assert expected["lifecycle"]["off_field_policy"] == "continue"


def test_non_integral_derived_periodic_count_is_rejected_not_rounded():
    manifest = _periodic_manifest()
    manifest["topology"]["lifecycle"]["instance_end_offset_seconds"] = 1.2
    with pytest.raises(GameplayActionManifestError, match="close exactly"):
        validate_bundle(_bundle(manifest))


@pytest.mark.parametrize("field", [
    "refresh_policy", "pause_resume_policy", "off_field_policy",
])
def test_pending_lifecycle_policy_cannot_become_ready(field):
    manifest = _periodic_manifest()
    manifest["topology"]["lifecycle"][field] = "pending"
    validate_bundle(_bundle(manifest))
    expected = materialization_expectation(manifest)
    assert expected["status"] == "pending"
    assert field in expected["issue"]


def test_derived_periodic_count_is_bounded():
    manifest = _periodic_manifest()
    schedule = manifest["topology"]["channels"][1]["schedule"]
    schedule["interval_seconds"] = .001
    manifest["topology"]["lifecycle"]["instance_end_offset_seconds"] = 1.25
    with pytest.raises(GameplayActionManifestError, match="exceeds 256"):
        validate_bundle(_bundle(manifest))


def test_explicit_unequal_offsets_and_closed_deliveries_reach_runtime_expectation():
    manifest = _periodic_manifest()
    storm = manifest["topology"]["channels"][1]
    storm["schedule"] = {"kind": "explicit_offsets", "offsets_seconds": [.25, .6, 1.25]}
    storm["deliveries"] = [{"kind": "damage"}, {"kind": "application"}]
    validate_bundle(_bundle(manifest))
    expected = materialization_expectation(manifest)
    assert [row["offset_ticks"] for row in expected["occurrences"]
            if row["channel_id"] == "storm"] == [250000, 600000, 1250000]
    assert expected["channels"][1]["deliveries"] == [
        {"kind": "damage"}, {"kind": "application"}]


def test_resource_delivery_requires_component_domain_and_closed_rotation_event_reference():
    manifest = _periodic_manifest()
    opening = manifest["topology"]["channels"][0]
    opening["deliveries"] = [{"kind": "resource", "contract": "rotation-resource-event-v1",
        "event": {"type": "energy_spawn", "template_id": "particle.template"}}]
    with pytest.raises(GameplayActionManifestError, match="critical domain"):
        validate_bundle(_bundle(manifest))
    manifest["components"][0]["critical_domains"].append("resource")
    validate_bundle(_bundle(manifest))
    opening["deliveries"][0]["contract"] = "invented"
    with pytest.raises(GameplayActionManifestError, match="closed rotation"):
        validate_bundle(_bundle(manifest))


def test_periodic_schedule_must_stay_inside_lifecycle():
    manifest = _periodic_manifest()
    manifest["topology"]["channels"][0]["schedule"]["offset_seconds"] = .05
    with pytest.raises(GameplayActionManifestError, match="within the lifecycle"):
        validate_bundle(_bundle(manifest))

    manifest = _periodic_manifest()
    schedule = manifest["topology"]["channels"][1]["schedule"]
    schedule.update(count=4)
    schedule.pop("derive_count")
    with pytest.raises(GameplayActionManifestError, match="within the lifecycle"):
        validate_bundle(_bundle(manifest))


def test_confirmed_manifest_requires_confirmed_nested_evidence():
    manifest = _finite_manifest()
    manifest["components"][0]["evidence"] = {
        "status": "pending", "category": "calibration_required", "source_refs": []}
    with pytest.raises(GameplayActionManifestError, match="confirmed component"):
        validate_bundle(_bundle(manifest))


def test_exact_resolution_is_reviewed_and_dataset_bound():
    manifest = _finite_manifest()
    ready = resolve_gameplay_action_manifest(
        _bundle(manifest), selector=_selector(), snapshot=_snapshot())
    assert ready["status"] == "ready"
    assert ready["manifest"]["manifest_id"] == "fixture.finite"
    assert len(ready["manifest_fingerprint"]) == 64

    stale = _snapshot()
    stale["upstream_commit"] = "other"
    pending = resolve_gameplay_action_manifest(
        _bundle(manifest), selector=_selector(), snapshot=stale)
    assert pending["status"] == "pending"
    assert pending["issues"][0]["code"] == "gameplay_action_manifest_stale"


def test_bundle_rejects_duplicate_exact_selectors_even_with_different_ids():
    first = _finite_manifest()
    second = copy.deepcopy(first)
    second["manifest_id"] = "fixture.other"
    with pytest.raises(GameplayActionManifestError, match="exact selectors"):
        validate_bundle(_bundle(first, second))
