"""One-step finite coverage survives compose, execution, receipt, and export."""
from __future__ import annotations

import copy
import io
import zipfile

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.service import result_bytes
from genshin_app.formula_workbook import build_formula_workbook
from genshin_db.runtime_catalog import fingerprint
from tests.test_action_dispatch_sources import (
    _bundle,
    _multi_plan,
    _multi_profile,
    _single_plan,
    _single_profile,
)
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_runtime_attack_metadata import metadata_db  # noqa: F401
from tests.test_runtime_periodic_attack import SOURCE, change, periodic_db  # noqa: F401

# ruff: noqa: F811


def _evidence():
    return {"status": "confirmed", "category": "calibration_required",
        "source_refs": [{"kind": "calibration_record",
            "reference": "fixture://finite/single/integration",
            "fingerprint": "2" * 64}]}


def _manifest(profile):
    component = profile["emissions"][0]["component_id"]
    return {"manifest_id": "fixture.finite.single.integration", "status": "confirmed",
        "selector": copy.deepcopy(profile["selector"]),
        "components": [{"component_id": component,
            "critical_domains": ["damage", "application"], "evidence": _evidence()}],
        "topology": {"kind": "finite_sequence", "steps": [{
            "step_id": component + ".1", "ordinal": 1, "component_id": component,
            "offset_seconds": .2, "order": 17}],
            "terminal_step_id": component + ".1", "interruption_policy": "none",
            "evidence": _evidence()},
        "dataset": copy.deepcopy(profile["dataset"]),
        "review": {"status": "reviewed", "date": "2026-10-21",
            "reviewer": "integration-test",
            "notes": "Synthetic character-agnostic single finite topology."}}


def _formula_sources_text(content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return archive.read("xl/worksheets/sheet4.xml").decode("utf8")


def test_single_finite_manifest_is_complete_through_compose_run_and_json_export(
        metadata_db, monkeypatch):
    import genshin_db.runtime_action_dispatch as dispatch_runtime
    import genshin_db.runtime_gameplay_action_manifest as manifest_runtime

    profile = _single_profile(metadata_db)
    # Under a finite topology the dispatch row owns source identity only; all
    # actual occurrence timing comes from the independent manifest.
    profile["emissions"][0]["schedule"]["offset_seconds"] = 0
    manifest = _manifest(profile)
    manifest_bundle = {"schema_version": 1,
        "contract_id": "gameplay-action-manifest-v1", "manifests": [manifest]}
    monkeypatch.setattr(dispatch_runtime, "load_action_dispatch_bundle",
        lambda: copy.deepcopy(_bundle(profile)))
    monkeypatch.setattr(manifest_runtime, "load_gameplay_action_manifest_bundle",
        lambda: copy.deepcopy(manifest_bundle))
    plan = _single_plan(metadata_db, profile)
    declaration = plan["action_dispatch_sources"][0]
    declaration.update(coverage_manifest_id=manifest["manifest_id"],
        expected_coverage_manifest_fingerprint=fingerprint(manifest))

    output = run_plan_calc(plan, metadata_db)

    assert output["report"]["state"] == "ready", output["report"]["missing_inputs"]
    dispatch = output["report"]["action_dispatch_compilations"][0]
    assert dispatch["status"] == "ready"
    assert dispatch["target_contract"] == "calibrated_single_application_v2"
    event = output["report"]["action_event_compilations"][0]
    assert event["status"] == "ready"
    assert all(row["occurrence_id"] == "primary.1"
               and row["component_id"] == "primary"
               and row["ordinal"] == 1
               and row["topology_order"] == 17
               and row["terminal"] is True
               for row in event["generated"])
    receipt = output["result"]["action_materialization_receipt"]
    relevant = [row for row in receipt["actions"]
                if row["ability_id"] == declaration["ability_id"]]
    assert relevant and {row["coverage_status"] for row in relevant} == {"complete"}
    assert all(not row["unresolved_occurrences"] and not row["unexpected_occurrences"]
               for row in relevant)
    exported = result_bytes(output["result"], output["result_receipt"], "json")
    assert b"action_materialization_receipt" in exported
    assert result_bytes(output["result"], output["result_receipt"], "xlsx").startswith(b"PK")
    formula = build_formula_workbook(output)
    assert formula.startswith(b"PK")
    source_text = _formula_sources_text(formula)
    assert "动作物化状态" in source_text
    assert "完整" in source_text
    assert declaration["ability_id"] in source_text


def test_finite_capabilities_name_the_actual_compiler_and_source_schema():
    from genshin_app.equipment.action_event_schema import action_event_capabilities
    from genshin_app.equipment.finite_action_schema import COMPILER, KIND
    from genshin_app.equipment.periodic_schema import periodic_attack_capabilities
    from genshin_app.equipment.service import plan_capabilities

    action = action_event_capabilities()
    periodic = periodic_attack_capabilities()
    assert action["profile_contracts"][KIND]["compiler"] == COMPILER
    assert action["profile_contracts"][KIND]["receipt_compiler"] == "calibrated-action-events-v2"
    assert KIND in periodic["profile_kinds"]
    assert KIND in periodic["source_backed_profile_kinds"]
    assert KIND in periodic["profile_schemas"]
    assert periodic["per_profile_kind_limits"][KIND] == {
        "max_channels": 64, "max_occurrences": 256}
    assert periodic["profile_contracts"][KIND]["timing_authority"] == "reviewed_steps"
    assert periodic["profile_contracts"][KIND]["lifecycle"].startswith("not_applicable")
    public = plan_capabilities()["finite_action_contract"]
    assert public["profile_kind"] == KIND
    assert public["compiler"] == COMPILER
    assert public["supported_interruption_policy"] == "none"
    lifecycle = plan_capabilities()["periodic_lifecycle_contract"]
    assert lifecycle["contract"] == "periodic-lifecycle-projection-v1"
    assert lifecycle["app_execution_subset"]["schedule_kinds"] == [
        "fixed", "equal_interval", "explicit_offsets"]
    assert lifecycle["resource_delivery"]["status"] == "unavailable"
    assert lifecycle["resource_delivery"]["materialization_complete"] is False


def test_multi_component_native_finite_survives_formula_workbook(
        periodic_db, monkeypatch):
    import genshin_db.runtime_action_dispatch as dispatch_runtime
    import genshin_db.runtime_gameplay_action_manifest as manifest_runtime

    profile = _multi_profile(periodic_db)
    for emission in profile["emissions"]:
        emission["kind"] = "fixed"
        emission["schedule"] = {"offset_seconds": 0, "precision_seconds": .01}
    first, second = (row["component_id"] for row in profile["emissions"])
    manifest = {"manifest_id": "fixture.finite.multi.integration",
        "status": "confirmed", "selector": copy.deepcopy(profile["selector"]),
        "components": [{"component_id": component,
            "critical_domains": ["damage", "application"], "evidence": _evidence()}
            for component in (first, second)],
        "topology": {"kind": "finite_sequence", "steps": [
            {"step_id": first + ".1", "ordinal": 1, "component_id": first,
             "offset_seconds": .2, "order": 3},
            {"step_id": first + ".2", "ordinal": 2, "component_id": first,
             "offset_seconds": .45, "order": 4},
            {"step_id": second + ".3", "ordinal": 3, "component_id": second,
             "offset_seconds": 1.1, "order": 5}],
            "terminal_step_id": second + ".3", "interruption_policy": "none",
            "evidence": _evidence()},
        "dataset": copy.deepcopy(profile["dataset"]),
        "review": {"status": "reviewed", "date": "2026-10-21",
            "reviewer": "integration-test",
            "notes": "Synthetic multi-component finite topology."}}
    monkeypatch.setattr(dispatch_runtime, "load_action_dispatch_bundle",
        lambda: copy.deepcopy(_bundle(profile)))
    monkeypatch.setattr(manifest_runtime, "load_gameplay_action_manifest_bundle",
        lambda: copy.deepcopy({"schema_version": 1,
            "contract_id": "gameplay-action-manifest-v1", "manifests": [manifest]}))
    plan = _multi_plan(periodic_db, profile)
    declaration = plan["action_dispatch_sources"][0]
    declaration.update(coverage_manifest_id=manifest["manifest_id"],
        expected_coverage_manifest_fingerprint=fingerprint(manifest))

    output = run_plan_calc(plan, periodic_db)

    assert output["report"]["state"] == "ready", output["report"]["missing_inputs"]
    event = output["report"]["action_event_compilations"][0]
    assert event["kind"] == "calibrated_finite_application_v1"
    assert event["compiler"] == "calibrated-finite-actions-v1"
    assert event["status"] == "ready"
    assert {row["component_id"] for row in event["generated"]} == {first, second}
    assert result_bytes(
        output["result"], output["result_receipt"], "xlsx").startswith(b"PK")
    assert build_formula_workbook(output).startswith(b"PK")


def test_finite_reviewed_steps_allow_timer_only_source_issue_through_offline_receipt(
        periodic_db, fake_repo, monkeypatch):
    import genshin_db.runtime_action_dispatch as dispatch_runtime
    import genshin_db.runtime_gameplay_action_manifest as manifest_runtime
    from genshin_app.equipment.periodic_receipt import _sources

    def make_timer_inexact(rows):
        rows[2]["Default"]["GPPBBDEICKP"]["FrozenWindmill_Damage"][
            "JFLGPLCIOEC"] = 0.25000001

    change(periodic_db, fake_repo, SOURCE, make_timer_inexact)
    profile = _multi_profile(periodic_db)
    for emission in profile["emissions"]:
        emission["kind"] = "fixed"
        emission["schedule"] = {"offset_seconds": 0, "precision_seconds": .01}
    components = [row["component_id"] for row in profile["emissions"]]
    manifest = {"manifest_id": "fixture.finite.timer-independent.integration",
        "status": "confirmed", "selector": copy.deepcopy(profile["selector"]),
        "components": [{"component_id": component,
            "critical_domains": ["damage", "application"], "evidence": _evidence()}
            for component in components],
        "topology": {"kind": "finite_sequence", "steps": [
            {"step_id": component + ".1", "ordinal": ordinal,
             "component_id": component, "offset_seconds": offset, "order": ordinal}
            for ordinal, (component, offset) in enumerate(
                zip(components, (.2, .6), strict=True), start=1)],
            "terminal_step_id": components[-1] + ".1",
            "interruption_policy": "none", "evidence": _evidence()},
        "dataset": copy.deepcopy(profile["dataset"]),
        "review": {"status": "reviewed", "date": "2026-10-21",
            "reviewer": "integration-test",
            "notes": "Finite steps are the sole timing authority for this fixture."}}
    monkeypatch.setattr(dispatch_runtime, "load_action_dispatch_bundle",
        lambda: copy.deepcopy(_bundle(profile)))
    monkeypatch.setattr(manifest_runtime, "load_gameplay_action_manifest_bundle",
        lambda: copy.deepcopy({"schema_version": 1,
            "contract_id": "gameplay-action-manifest-v1", "manifests": [manifest]}))
    plan = _multi_plan(periodic_db, profile)
    declaration = plan["action_dispatch_sources"][0]
    declaration.update(coverage_manifest_id=manifest["manifest_id"],
        expected_coverage_manifest_fingerprint=fingerprint(manifest))

    output = run_plan_calc(plan, periodic_db)

    assert output["report"]["state"] == "ready", output["report"]["missing_inputs"]
    event = output["report"]["action_event_compilations"][0]
    assert event["kind"] == "calibrated_finite_application_v1"
    records = output["result"]["periodic_attack_compilations"]
    assert any(issue["code"] == "periodic_timer_not_exact"
               for candidate in records[0]["query"]["selected_candidates"]
               for issue in candidate.get("issues", []))
    receipts = output["report"]["action_dispatch_compilations"][0][
        "source_verification"]["receipts"]
    assert next(row for row in receipts if row["component_id"] == "slice")[
        "source_issue_codes"] == ["periodic_timer_not_exact"]
    assert next(row for row in receipts if row["component_id"] == "blast")["status"] == "ready"
    with pytest.raises(ValueError):
        _sources(records)
    _sources(records, finite_profile_ids={event["profile_id"]})
    assert result_bytes(
        output["result"], output["result_receipt"], "json").startswith(b"{")


def test_explicit_offsets_survive_dispatch_source_event_and_lifecycle_projection(
        periodic_db, monkeypatch):
    import genshin_db.runtime_action_dispatch as dispatch_runtime
    import genshin_db.runtime_gameplay_action_manifest as manifest_runtime
    from genshin_db.runtime_queries import query_runtime

    profile = _multi_profile(periodic_db)
    emission = next(row for row in profile["emissions"]
                    if row["component_id"] == "slice")
    profile["emissions"] = [emission]
    emission["kind"] = "fixed"
    emission["schedule"] = {"offset_seconds": 0, "precision_seconds": .01}
    selector = profile["selector"]
    fresh = query_runtime(periodic_db, {"action": "periodic_attack_profile",
        "character_id": selector["character_id"],
        "skill_depot_id": selector["skill_depot_id"],
        "skill_id": selector["skill_id"],
        "constellation": selector["constellation"],
        "talent_level": selector["effective_level"],
        "origin_reference_id": emission["attack_source"]["origin_reference_id"],
        "candidate_ids": [emission["attack_source"]["candidate_id"]]})
    emission["attack_source"]["source_fingerprint"] = fresh["source_fingerprint"]
    manifest = {"manifest_id": "fixture.periodic.explicit.integration",
        "status": "confirmed", "selector": copy.deepcopy(selector),
        "components": [{"component_id": "slice",
            "critical_domains": ["damage", "application"],
            "evidence": _evidence()}],
        "topology": {"kind": "periodic_channels", "channels": [{
            "channel_id": "slice", "component_id": "slice",
            "schedule": {"kind": "explicit_offsets",
                         "offsets_seconds": [.2, .55, 1.2]},
            "deliveries": [{"kind": "damage"}, {"kind": "application"}],
        }], "lifecycle": {"establishment_offset_seconds": .1,
            "instance_end_offset_seconds": 1.2, "refresh_policy": "none",
            "pause_resume_policy": "none", "off_field_policy": "continue",
            "evidence": _evidence()}, "evidence": _evidence()},
        "dataset": copy.deepcopy(profile["dataset"]),
        "review": {"status": "reviewed", "date": "2026-10-21",
            "reviewer": "integration-test", "notes": "Explicit offsets fixture."}}
    monkeypatch.setattr(dispatch_runtime, "load_action_dispatch_bundle",
        lambda: copy.deepcopy(_bundle(profile)))
    monkeypatch.setattr(manifest_runtime, "load_gameplay_action_manifest_bundle",
        lambda: copy.deepcopy({"schema_version": 1,
            "contract_id": "gameplay-action-manifest-v1", "manifests": [manifest]}))
    plan = _multi_plan(periodic_db, profile)
    declaration = plan["action_dispatch_sources"][0]
    declaration.update(coverage_manifest_id=manifest["manifest_id"],
        expected_coverage_manifest_fingerprint=fingerprint(manifest))

    output = run_plan_calc(plan, periodic_db)

    assert output["report"]["state"] == "ready", output["report"]["missing_inputs"]
    event = output["report"]["action_event_compilations"][0]
    assert {row["offset_ticks"] for row in event["generated"]} == {
        200000, 550000, 1200000}
    assert all(not row["deliveries"]
               for row in event["lifecycle_projection"]["occurrences"])
    relevant = [row for row in output["result"]["action_materialization_receipt"]["actions"]
                if row["ability_id"] == declaration["ability_id"]]
    assert relevant and {row["coverage_status"] for row in relevant} == {"complete"}


def test_single_finite_zero_offset_order_overflow_fails_closed(metadata_db, monkeypatch):
    import genshin_db.runtime_action_dispatch as dispatch_runtime
    import genshin_db.runtime_gameplay_action_manifest as manifest_runtime

    profile = _single_profile(metadata_db)
    profile["emissions"][0]["schedule"]["offset_seconds"] = 0
    manifest = _manifest(profile)
    manifest["topology"]["steps"][0]["offset_seconds"] = 0
    manifest_bundle = {"schema_version": 1,
        "contract_id": "gameplay-action-manifest-v1", "manifests": [manifest]}
    monkeypatch.setattr(dispatch_runtime, "load_action_dispatch_bundle",
        lambda: copy.deepcopy(_bundle(profile)))
    monkeypatch.setattr(manifest_runtime, "load_gameplay_action_manifest_bundle",
        lambda: copy.deepcopy(manifest_bundle))
    plan = _single_plan(metadata_db, profile)
    declaration = plan["action_dispatch_sources"][0]
    declaration.update(coverage_manifest_id=manifest["manifest_id"],
        expected_coverage_manifest_fingerprint=fingerprint(manifest))
    action = next(row for row in plan["rotation_model"]["actions"]
                  if row["ability_id"] == declaration["ability_id"])
    action["order"] = 10**9

    report = compose_equipment_plan(plan, metadata_db)

    assert report["state"] == "pending"
    records = report["action_event_compilations"]
    assert records and records[0]["status"] == "pending"
    assert records[0]["generated"] == []
    assert any("order" in row["message"] and "\u8fb9\u754c" in row["message"]
               for row in records[0]["issues"])
