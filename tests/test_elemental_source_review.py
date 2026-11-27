"""Independent EC-02 review against the optional, read-only project snapshot.

No database update or fixture-generated copy is used. Installations without the
project data snapshot skip these integration cases; generic contract tests live
in the other elemental test modules.
"""
from __future__ import annotations

import copy
import hashlib
import json

import pytest
from test_elemental_app import native
from test_elemental_execution import attempt, model

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.schema import PlanError
from genshin_db.queries import equipment_read_session
from genshin_db.runtime_queries import query_runtime
from genshin_engine.elemental import ElementalRuntime, validate_elemental_model

SOURCE_PATH = "ExcelBinOutput/AttackAttenuationExcelConfigData.json"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@pytest.fixture(scope="module")
def live_sources(accepted_source_database):
    if not accepted_source_database.is_file():
        pytest.skip("optional project database is unavailable")
    before = sha(accepted_source_database)
    with equipment_read_session(accepted_source_database):
        sources = {group: query_runtime(accepted_source_database, {"action": "attenuation_profile", "group": group})
                   for group in ("default", "Diluc", "Shenhe_ElementalArt_L_Bullet", "Airborne_ChestAtk")}
        yield sources
    assert sha(accepted_source_database) == before, "read-only audit changed the project database"


@pytest.fixture(scope="module")
def exact_source_bytes(live_sources, exact_upstream_source):
    sample = live_sources["default"]
    commit = sample["snapshot"]["upstream_commit"]
    expected = sample["source"]["source_sha256"]
    return exact_upstream_source(commit, SOURCE_PATH, expected)


def bind(plan, sources, profile="pyro", group="default", counter_tag="review-pyro"):
    source = sources[group]
    entry = next(p for p in plan["mechanics_model"]["elemental_model"]["profiles"] if p["id"] == profile)
    entry["icd"] = {"kind": "unknown"}
    plan.setdefault("elemental_sources", []).append({
        "profile_id": profile, "version_line": "release", "rule_group": group,
        "counter_tag": counter_tag, "application_mode": "sequence",
        "verification": {"status": "confirmed",
            "locator": "tests/test_elemental_source_review.py canonical binding fixture",
            "version": "fixture-v1", "reviewed_at": "2026-10-18",
            "component_scope": f"exact {profile} test component and its declared shared counter",
            "confirmed_fields": ["application_mode", "counter_tag", "shared_scope"]},
        "source": "controlled review; exact component-to-counter binding is fixture evidence",
        "expected_snapshot_id": source["snapshot"]["snapshot_id"],
        "expected_node_sha256": source["source"]["node_sha256"],
    })
    return plan


@pytest.mark.parametrize("group", ["default", "Diluc", "Shenhe_ElementalArt_L_Bullet", "Airborne_ChestAtk"])
def test_review_source_bytes_pointer_node_digest_and_field_projection(live_sources, exact_source_bytes, group):
    result = live_sources[group]
    origin = result["source"]
    assert origin["source_path"] == SOURCE_PATH
    assert hashlib.sha256(exact_source_bytes).hexdigest() == origin["source_sha256"]
    rows = json.loads(exact_source_bytes)
    record = rows[int(origin["json_pointer"].removeprefix("/"))]
    independent_node_bytes = json.dumps(record, sort_keys=True, separators=(",", ":"),
                                        ensure_ascii=False, allow_nan=False).encode("utf-8")
    assert record["group"] == group and record == result["raw_record"]
    assert hashlib.sha256(independent_node_bytes).hexdigest() == origin["node_sha256"]
    assert origin["field_pointers"]["resetCycle"] == origin["json_pointer"] + "/resetCycle"
    assert result["execution"]["attack_binding"] == "not_evaluated"


def test_review_real_custom_sequence_resets_on_five_seconds_not_last_application(live_sources):
    source = live_sources["Diluc"]["projection"]
    assert source["durability_sequence"] == [1, 0, 0, 0, 0] * 6
    assert source["reset_cycle_ticks"] == 5_000_000
    value = model()
    value["profiles"][0]["icd"] = {"kind": "sequence", "group": "review-diluc",
        "reset_seconds": source["reset_cycle_seconds_rounded"], "sequence": source["durability_sequence"]}
    assert validate_elemental_model(value) == []
    runtime = ElementalRuntime(value)
    assert [attempt(runtime, "pyro", t)["allowed"] for t in (0, .1, .2, .3, .4, 4.9, 5, 5.1)] == [
        True, False, False, False, False, True, True, False]


def test_review_real_rounding_retains_exact_original_and_discloses_error(live_sources):
    source = live_sources["Shenhe_ElementalArt_L_Bullet"]
    projection = source["projection"]
    assert source["raw_record"]["resetCycle"] == 0.10000000149011612
    assert projection["reset_cycle_ticks"] == 100_000
    assert projection["reset_cycle_seconds_rounded"] == .1
    assert projection["rounding_error_seconds"] == pytest.approx(-1.49011612e-9, abs=1e-18)


def test_review_real_default_binding_matches_legacy_standard_and_preserves_input(live_sources, accepted_source_database):
    source_plan = bind(native(), live_sources)
    legacy_plan = native()
    legacy_plan["mechanics_model"]["elemental_model"]["profiles"][0]["icd"] = {
        "kind": "standard", "group": "review-pyro"}
    original = copy.deepcopy(source_plan)
    actual, expected = run_plan_calc(source_plan, accepted_source_database), run_plan_calc(legacy_plan, accepted_source_database)
    assert actual["result"]["summary"] == expected["result"]["summary"]
    assert [hit["expected"] for hit in actual["result"]["hits"]] == pytest.approx([45, 67.5, 45, 45])
    assert [r["allowed"] for r in actual["result"]["elemental"]["trace"]] == [
        r["allowed"] for r in expected["result"]["elemental"]["trace"]]
    assert source_plan == original
    assert actual["plan"]["mechanics_model"]["elemental_model"]["profiles"][0]["icd"] == {"kind": "unknown"}
    compiled = actual["report"]["mechanics_model"]["elemental_model"]["profiles"][0]["icd"]
    assert compiled == {"kind": "sequence", "counter_tag": "review-pyro", "rule_group": "default",
                        "reset_seconds": 2.5, "sequence": [1, 0, 0] * 8}
    assert actual["result_receipt"]["elemental_sources"][0]["source"]["node_sha256"] == live_sources["default"]["source"]["node_sha256"]


def test_review_conflicting_database_profiles_cannot_be_composed_as_ready(live_sources, accepted_source_database):
    plan = bind(native(), live_sources, group="default", counter_tag="conflict")
    bind(plan, live_sources, profile="hydro", group="Diluc", counter_tag="conflict")
    report = compose_equipment_plan(plan, accepted_source_database)
    assert report["state"] == "pending"
    assert any("icd_group_conflict" in json.dumps(issue, ensure_ascii=False) for issue in report["missing_inputs"])
    task = report["elemental_counter_verification_tasks"][0]
    assert task["kind"] == "elemental_counter_binding_conflict"
    assert task["attack_identity"]["actor"] == "a"
    assert task["known_fields"] == {"counter_tag": "conflict", "rule_groups": ["Diluc", "default"]}
    assert {row["application_profile_id"] for row in task["uses"]} == {"pyro", "hydro"}
    assert task["local_checks"][0]["kind"] == "actor_counter_rule_consistency"
    assert "attenuationTag" in task["keywords"] and "attenuationGroup" in task["keywords"]
    assert task["database_patch_targets"] == ["attack_component.counter_tag", "attack_component.rule_group",
        "runtime_attack_metadata.application_identity", "runtime_periodic_attack.application_identity"]
    assert task["research_report_template"]["execution_authority"] == "none"
    assert len(task["targeted_research_questions"]) == 2
    assert {row["profile_id"] for row in task["signature_bindings"]} == {"pyro", "hydro"}
    assert {row["signature"]["reset_microseconds"] for row in task["signature_bindings"]} == {
        2_500_000, 5_000_000}
    assert all(row["profile_path"] in row["source_paths"] and row["usage_paths"]
               for row in task["signature_bindings"])
    with pytest.raises(PlanError):
        run_plan_calc(plan, accepted_source_database)


def test_review_same_named_rule_signature_conflict_has_top_level_audit_work_order(live_sources, accepted_source_database):
    plan = native()
    pyro = next(profile for profile in plan["mechanics_model"]["elemental_model"]["profiles"]
                if profile["id"] == "pyro")
    pyro["icd"] = {"kind": "sequence", "counter_tag": "manual-pyro", "rule_group": "default",
                   "reset_seconds": 5, "sequence": [1, 0, 0, 0, 0] * 6}
    bind(plan, live_sources, profile="hydro", group="default", counter_tag="database-hydro")

    report = compose_equipment_plan(plan, accepted_source_database)

    assert report["state"] == "pending"
    tasks = [task for task in report["elemental_counter_verification_tasks"]
             if task["kind"] == "elemental_rule_signature_conflict"]
    assert len(tasks) == 1
    task = tasks[0]
    assert task["known_fields"] == {"counter_tag": None, "rule_groups": ["default"]}
    assert {row["profile_id"] for row in task["signature_bindings"]} == {"pyro", "hydro"}
    assert {row["signature"]["reset_microseconds"] for row in task["signature_bindings"]} == {
        2_500_000, 5_000_000}
    assert {tuple(row["signature"]["sequence"]) for row in task["signature_bindings"]} == {
        tuple([1, 0, 0] * 8), tuple([1, 0, 0, 0, 0] * 6)}
    assert all(row["profile_path"] in row["source_paths"] for row in task["signature_bindings"])
    hydro_binding = next(row for row in task["signature_bindings"] if row["profile_id"] == "hydro")
    assert hydro_binding["database_sources"][0]["json_pointer"]
    assert "elemental_sources.hydro" in hydro_binding["source_paths"]
    assert task["local_checks"][0]["kind"] == "rule_group_signature_consistency"
    assert "完整签名" in task["next_action"]


def test_preexisting_named_rule_conflict_remains_schema_rejected_with_auditable_details(accepted_source_database):
    plan = native()
    profiles = plan["mechanics_model"]["elemental_model"]["profiles"]
    for profile, reset, sequence in zip(profiles[:2], (2.5, 5),
                                        ([1, 0, 0] * 8, [1, 0, 0, 0, 0] * 6), strict=True):
        profile["icd"] = {"kind": "sequence", "counter_tag": f"tag.{profile['id']}",
                          "rule_group": "conflicting-manual-rule", "reset_seconds": reset,
                          "sequence": sequence}

    with pytest.raises(PlanError) as error:
        compose_equipment_plan(plan, accepted_source_database)

    issue = next(row for row in error.value.details if row["code"] == "icd_rule_group_conflict")
    assert {row["profile_id"] for row in issue["bindings"]} == {"pyro", "hydro"}
    assert {row["signature"]["reset_microseconds"] for row in issue["bindings"]} == {
        2_500_000, 5_000_000}
    assert issue["source_paths"] == [
        "elemental_model.profiles.pyro", "elemental_model.profiles.hydro"]


@pytest.mark.parametrize("field", ["expected_snapshot_id", "expected_node_sha256"])
def test_review_stale_confirmation_and_forged_cached_success_cannot_bypass_recompile(live_sources, accepted_source_database, field):
    plan = bind(native(), live_sources)
    valid = compose_equipment_plan(plan, accepted_source_database)
    plan["resolution"] = copy.deepcopy(valid["plan"]["resolution"])
    plan["resolved_template"] = copy.deepcopy(valid["plan"]["resolved_template"])
    plan["elemental_sources"][0][field] = "0" * 64
    original = copy.deepcopy(plan)
    stale = compose_equipment_plan(plan, accepted_source_database)
    assert stale["state"] == "pending" and stale["plan"]["resolved_template"] is None
    assert any(issue["code"] == "source_changed" for issue in stale["missing_inputs"])
    assert stale["elemental_compilations"][0]["required_confirmation"][field] == live_sources["default"][
        "snapshot" if field == "expected_snapshot_id" else "source"]["snapshot_id" if field == "expected_snapshot_id" else "node_sha256"]
    with pytest.raises(PlanError):
        run_plan_calc(plan, accepted_source_database)
    assert plan == original


def test_review_nonbinary_real_source_remains_queryable_but_blocks_native_calc(live_sources, accepted_source_database):
    source = live_sources["Airborne_ChestAtk"]
    assert source["status"] == "ready" and source["execution"]["status"] == "pending"
    assert any(value not in (0, 1) for value in source["raw_record"]["durabilitySequence"])
    plan = bind(native(), live_sources, group="Airborne_ChestAtk")
    report = compose_equipment_plan(plan, accepted_source_database)
    assert report["state"] == "pending"
    assert any(issue["code"] == "unsupported_elemental_source" for issue in report["missing_inputs"])
    with pytest.raises(PlanError):
        run_plan_calc(plan, accepted_source_database)


def test_review_manual_icd_cannot_duplicate_database_delivery(live_sources, accepted_source_database):
    plan = bind(native(), live_sources)
    plan["mechanics_model"]["elemental_model"]["profiles"][0]["icd"] = {"kind": "standard", "group": "review-pyro"}
    with pytest.raises(PlanError) as error:
        compose_equipment_plan(plan, accepted_source_database)
    assert error.value.reason == "duplicate_delivery"


@pytest.mark.parametrize("kind", ["sequence", "standard"])
def test_review_blank_shared_identity_is_not_a_valid_group(kind):
    value = model()
    icd = {"kind": kind, "group": "  \t "}
    if kind == "sequence":
        icd.update(reset_seconds=2.5, sequence=[1, 0, 0] * 8)
    value["profiles"][0]["icd"] = icd
    assert any(issue["code"] == "icd_group" for issue in validate_elemental_model(value))


@pytest.mark.parametrize(("reset", "valid"), [(10**9, True), (10**9 + 1, False), (1e308, False)])
def test_review_reset_clock_has_same_upper_bound_as_database_projection(reset, valid):
    value = model()
    value["profiles"][0]["icd"] = {
        "kind": "sequence", "group": "bounded", "reset_seconds": reset, "sequence": [1]}
    issues = validate_elemental_model(value)
    assert (not issues) == valid
