"""Native source assembly is fresh, reviewable and never trusted from cached output."""
from __future__ import annotations

import copy
import json

import pytest
from test_elemental_app import native
from test_runtime_attenuation import (  # noqa: F401
    attenuation_db,
    change,
    fake_repo,
    query,
    stub_fetcher,
)

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.files import read_plan
from genshin_app.equipment.schema import PLAN_SCHEMA, PlanError, validate_plan
from genshin_app.gui.api import GuiApi

# ruff: noqa: F811


def test_direct_elemental_source_schema_exposes_no_authority_research_report():
    source_schema = PLAN_SCHEMA["properties"]["elemental_sources"]["items"]
    report_schema = source_schema["properties"]["research_report"]
    assert report_schema["properties"]["execution_authority"] == {"enum": ["none"]}
    assert set(report_schema["required"]) >= {
        "report_version", "execution_authority", "actual_queries", "sources_checked",
        "adopted_findings", "rejected_findings", "remaining_unknowns", "database_patch_plan",
    }


def sourced(db, group="default", *, confirmed=True):
    plan = native()
    plan["mechanics_model"]["elemental_model"]["profiles"][0]["icd"] = {"kind": "unknown"}
    source = query(db, group)
    plan["elemental_sources"] = [{"profile_id": "pyro", "version_line": "release", "rule_group": group,
        "counter_tag": "declared-pyro-attacks", "application_mode": "sequence",
        "source": "工程夹具逐攻击段用途说明",
        "verification": {"status": "confirmed", "locator": "tests/test_elemental_sources_app.py sourced fixture",
            "version": "fixture-v1", "reviewed_at": "2026-10-18", "component_scope": "pyro exact application profile",
            "confirmed_fields": ["application_mode", "counter_tag", "shared_scope"]},
        "expected_snapshot_id": source["snapshot"]["snapshot_id"] if confirmed else None,
        "expected_node_sha256": source["source"]["node_sha256"] if confirmed else None}]
    return plan


def test_unconfirmed_source_preview_is_readable_and_blocks_calculation(attenuation_db):
    plan = sourced(attenuation_db, confirmed=False)
    preview = compose_equipment_plan(plan, attenuation_db)
    assert preview["state"] == "pending"
    item = preview["elemental_compilations"][0]
    assert item["required_confirmation"]["expected_node_sha256"]
    assert item["projection"]["durability_sequence"] == [1, 0, 0] * 8
    assert item["attack_mapping"] == "explicitly_declared_not_inferred"
    assert preview["plan"]["resolved_template"] is None
    with pytest.raises(PlanError) as error:
        run_plan_calc(plan, attenuation_db)
    assert error.value.details["elemental_compilations"][0]["required_confirmation"] == item["required_confirmation"]


def test_confirmed_source_recompiles_preserving_unknown_input_and_receipt(attenuation_db):
    plan = sourced(attenuation_db)
    before = copy.deepcopy(plan)
    result = run_plan_calc(plan, attenuation_db)
    assert plan == before
    assert result["plan"]["mechanics_model"]["elemental_model"]["profiles"][0]["icd"] == {"kind": "unknown"}
    assert result["report"]["mechanics_model"]["elemental_model"]["profiles"][0]["icd"]["kind"] == "sequence"
    assert [h["expected"] for h in result["result"]["hits"]] == [45, 67.5, 45, 45]
    source = result["result"]["elemental"]["source_compilations"][0]
    receipt = result["result_receipt"]["elemental_sources"][0]
    assert source["status"] == "ready" and source["compiler"] == "database-icd-v1"
    assert receipt["compilation_fingerprint"] == source["compilation_fingerprint"]
    assert receipt["source"]["node_sha256"] == before["elemental_sources"][0]["expected_node_sha256"]


def test_saved_native_and_gui_bridge_recompile_forged_resolution(attenuation_db):
    plan = run_plan_calc(sourced(attenuation_db), attenuation_db)["plan"]
    plan["resolution"] = {"state": "ready", "elemental_compilations": [{"status": "ready", "compiled_icd": {"kind": "none"}}]}
    plan["resolved_template"]["characters"][0]["stats"]["atk"] = 999999
    path = attenuation_db.parent / "native.json"
    path.write_text(json.dumps(plan), "utf8")
    result = run_plan_calc(read_plan(path)["plan"], attenuation_db)
    gui = GuiApi(attenuation_db.parent).run_plan_calc(plan)
    assert gui["code"] == 0
    assert gui["data"]["result"] == result["result"]
    assert result["result"]["hits"][0]["expected"] == 45
    assert result["report"]["elemental_compilations"][0]["compiled_icd"]["kind"] == "sequence"


@pytest.mark.parametrize("field", ["expected_snapshot_id", "expected_node_sha256"])
def test_stale_identity_never_uses_cached_output(attenuation_db, field):
    plan = run_plan_calc(sourced(attenuation_db), attenuation_db)["plan"]
    plan["elemental_sources"][0][field] = "stale"
    with pytest.raises(PlanError) as error:
        run_plan_calc(plan, attenuation_db)
    assert error.value.reason == "missing_input"
    assert any(row["code"] == "source_changed" for row in error.value.details["missing_inputs"])


def test_database_update_requires_new_review_and_changes_source_fingerprint(attenuation_db, fake_repo):
    plan = sourced(attenuation_db)
    old = run_plan_calc(plan, attenuation_db)
    change(fake_repo, attenuation_db, lambda rows: rows[0].update(durabilitySequence=[1]))
    pending = compose_equipment_plan(old["plan"], attenuation_db)
    assert pending["state"] == "pending"
    plan["elemental_sources"][0].update(pending["elemental_compilations"][0]["required_confirmation"])
    new = run_plan_calc(plan, attenuation_db)
    assert new["result_receipt"]["source_fingerprint"] != old["result_receipt"]["source_fingerprint"]
    assert new["result"]["elemental"]["source_compilations"][0]["compiled_icd"]["sequence"] == [1]


def test_nonbinary_source_is_pending_even_after_source_confirmation(attenuation_db):
    preview = compose_equipment_plan(sourced(attenuation_db, "nonbinary"), attenuation_db)
    assert preview["state"] == "pending"
    assert preview["elemental_compilations"][0]["execution"]["status"] == "pending"
    assert any(row["code"] == "unsupported_elemental_source" for row in preview["missing_inputs"])


def test_missing_group_is_diagnostic_not_silent_legacy_fallback(attenuation_db):
    plan = sourced(attenuation_db)
    plan["elemental_sources"][0]["rule_group"] = "absent"
    preview = compose_equipment_plan(plan, attenuation_db)
    assert preview["state"] == "pending"
    assert preview["elemental_compilations"][0]["issue"]["code"] == "not_found"


@pytest.mark.parametrize("icd", [{"kind": "none"}, {"kind": "standard", "group": "x"}])
def test_manual_and_database_icd_cannot_be_delivered_twice(attenuation_db, icd):
    plan = sourced(attenuation_db)
    plan["mechanics_model"]["elemental_model"]["profiles"][0]["icd"] = icd
    with pytest.raises(PlanError, match="不能同时交付"):
        compose_equipment_plan(plan, attenuation_db)


@pytest.mark.parametrize("mutation", [
    lambda p: p["elemental_sources"].append(copy.deepcopy(p["elemental_sources"][0])),
    lambda p: p["elemental_sources"][0].update(counter_tag=" "),
    lambda p: p["elemental_sources"][0].update(source=""),
])
def test_ambiguous_or_unevidenced_binding_rejected(attenuation_db, mutation):
    plan = sourced(attenuation_db)
    mutation(plan)
    with pytest.raises(PlanError):
        validate_plan(plan)


def test_shared_source_queried_once_but_never_reused_next_compose(attenuation_db, monkeypatch):
    from genshin_db import runtime_attenuation
    plan = sourced(attenuation_db)
    hydro = copy.deepcopy(plan["elemental_sources"][0])
    hydro.update(profile_id="hydro", counter_tag="separate-hydro")
    plan["elemental_sources"].append(hydro)
    plan["mechanics_model"]["elemental_model"]["profiles"][1]["icd"] = {"kind": "unknown"}
    original, calls = runtime_attenuation.get_runtime_attenuation_profile, []
    def counted(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(runtime_attenuation, "get_runtime_attenuation_profile", counted)
    compose_equipment_plan(plan, attenuation_db)
    assert len(calls) == 1
    compose_equipment_plan(plan, attenuation_db)
    assert len(calls) == 2


def _application_mode_plan(db, mode):
    plan = sourced(db)
    source = plan["elemental_sources"][0]
    source["application_mode"] = mode
    source.pop("rule_group", None)
    source.pop("counter_tag", None)
    source.update(expected_snapshot_id=None, expected_node_sha256=None,
                  source=f"逐攻击组件外部证据：application_mode={mode}")
    return plan


def test_explicit_no_icd_compiles_without_attenuation_lookup_and_allows_each_application(attenuation_db, monkeypatch):
    from genshin_db import runtime_attenuation

    plan = _application_mode_plan(attenuation_db, "no_icd")
    monkeypatch.setattr(runtime_attenuation, "get_runtime_attenuation_profile",
                        lambda *a, **kw: pytest.fail("no_icd must not query an attenuation rule"))
    output = run_plan_calc(plan, attenuation_db)
    record = output["report"]["elemental_compilations"][0]
    assert record["status"] == "ready" and record["application_mode"] == "no_icd"
    assert record["application_identity"]["status"] == "not_applicable"
    assert record["compiled_icd"] == {"kind": "none"}
    assert output["report"]["mechanics_model"]["elemental_model"]["profiles"][0]["icd"] == {"kind": "none"}
    pyro = [row for row in output["result"]["elemental"]["trace"] if row["profile_id"] == "pyro"]
    assert pyro and all(row["allowed"] for row in pyro)


def test_explicit_no_application_is_distinct_from_a_missing_profile(attenuation_db, monkeypatch):
    from genshin_db import runtime_attenuation

    plan = _application_mode_plan(attenuation_db, "no_application")
    profile = plan["mechanics_model"]["elemental_model"]["profiles"][0]
    profile["application"] = "unknown"
    profile.pop("gauge_units")
    profile.pop("icd")
    monkeypatch.setattr(runtime_attenuation, "get_runtime_attenuation_profile",
                        lambda *a, **kw: pytest.fail("no_application must not query an attenuation rule"))
    output = run_plan_calc(plan, attenuation_db)
    record = output["report"]["elemental_compilations"][0]
    assert record["status"] == "ready" and record["application_mode"] == "no_application"
    compiled = next(p for p in output["report"]["mechanics_model"]["elemental_model"]["profiles"] if p["id"] == "pyro")
    assert compiled["application"] == "none" and "gauge_units" not in compiled and "icd" not in compiled
    pyro = [row for row in output["result"]["elemental"]["trace"] if row["profile_id"] == "pyro"]
    assert pyro and all(row["reason"] == "no_application" for row in pyro)


@pytest.mark.parametrize("mode", ["sequence", "no_icd", "no_application"])
def test_free_text_never_promotes_direct_elemental_source_without_structured_evidence(attenuation_db, mode):
    plan = _application_mode_plan(attenuation_db, mode) if mode != "sequence" else sourced(attenuation_db)
    plan["elemental_sources"][0].pop("verification")
    plan["elemental_sources"][0]["source"] = "文字声称已核实，但没有逐攻击段结构化证据"
    if mode == "no_application":
        profile = plan["mechanics_model"]["elemental_model"]["profiles"][0]
        profile.update(application="unknown")
        profile.pop("gauge_units")
        profile.pop("icd")
    preview = compose_equipment_plan(plan, attenuation_db)
    record = preview["elemental_compilations"][0]
    assert preview["state"] == "pending" and record["status"] == "pending"
    assert "证据" in record["issue"]["message"]


def test_completed_research_report_is_preserved_but_never_grants_execution_authority(attenuation_db):
    plan = sourced(attenuation_db)
    source = plan["elemental_sources"][0]
    source.pop("verification")
    source["research_report"] = {
        "report_version": 1, "execution_authority": "none",
        "actual_queries": ["fixture exact attack component attenuationTag"],
        "sources_checked": [{"locator": "fixture://component", "version": "fixture-v1",
            "accessed_at": "2026-10-18", "supported_fields": ["counter_tag"],
            "decision": "inconclusive", "reason": "does not prove the concrete attack binding"}],
        "adopted_findings": [], "rejected_findings": ["action category inference"],
        "remaining_unknowns": ["concrete component shared scope"],
        "database_patch_plan": [{"target": "attack_component.fixture", "fields": ["counter_tag"],
            "evidence_locators": ["fixture://component"], "status": "deferred",
            "note": "wait for a source that proves this component"}],
    }
    preview = compose_equipment_plan(plan, attenuation_db)
    record = preview["elemental_compilations"][0]
    assert preview["state"] == "pending" and record["status"] == "pending"
    assert record["verification_task"]["research_report"] == source["research_report"]
    assert "structured_verification" in record["verification_task"]["missing_fields"]

    source["research_report"]["execution_authority"] = "verified"
    with pytest.raises(PlanError):
        validate_plan(plan)


def test_legacy_direct_source_fields_compile_but_missing_mode_is_deprecated_pending(attenuation_db):
    plan = sourced(attenuation_db)
    source = plan["elemental_sources"][0]
    source["attenuation_group"] = source.pop("rule_group")
    source["shared_group"] = source.pop("counter_tag")
    ready = run_plan_calc(plan, attenuation_db)["report"]["elemental_compilations"][0]
    assert ready["status"] == "ready"
    assert ready["deprecated_input_fields"] == ["shared_group", "attenuation_group"]

    source.pop("application_mode")
    preview = compose_equipment_plan(plan, attenuation_db)
    record = preview["elemental_compilations"][0]
    assert preview["state"] == "pending" and record["status"] == "pending"
    assert record["deprecated_application_mode"] == "missing"


@pytest.mark.parametrize(("mode", "deprecated"), [("pending", False), ("unknown", True)])
def test_pending_mode_blocks_deterministic_result_and_old_unknown_alias_is_disclosed(attenuation_db, mode, deprecated):
    plan = _application_mode_plan(attenuation_db, mode)
    preview = compose_equipment_plan(plan, attenuation_db)
    record = preview["elemental_compilations"][0]
    assert preview["state"] == "pending" and record["application_mode"] == "pending"
    assert record["application_identity"]["status"] == "pending"
    assert bool(record.get("deprecated_application_mode")) is deprecated
    assert any(issue["code"] == "elemental_application_mode_pending" for issue in preview["missing_inputs"])
    with pytest.raises(PlanError):
        run_plan_calc(plan, attenuation_db)


@pytest.mark.parametrize("mode", ["no_icd", "no_application", "pending", "unknown"])
def test_nonsequence_modes_reject_counter_or_rule_fields(attenuation_db, mode):
    plan = _application_mode_plan(attenuation_db, mode)
    plan["elemental_sources"][0]["counter_tag"] = "must-not-be-present"
    with pytest.raises(PlanError, match="不得同时声明"):
        validate_plan(plan)
