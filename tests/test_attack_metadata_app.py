"""EC10 materialization uses fixture source bytes and one parent acceptance."""
from __future__ import annotations

import copy

import pytest
from test_action_events import action_event_plan, confirm_action_events
from test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from test_runtime_attack_metadata import (  # noqa: F401
    SOURCE,
    candidate,
    change,
    metadata_db,
)

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.attack_metadata_schema import attack_metadata_capabilities
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError

# ruff: noqa: F811


def test_attack_metadata_capabilities_expose_saved_research_report_without_execution_authority():
    capabilities = attack_metadata_capabilities()
    report_schema = capabilities["schema"]["items"]["properties"]["research_report"]
    assert "research_report" in capabilities["verification_task"]
    assert "research_report_template" in capabilities["verification_task"]
    assert capabilities["research_report_contract"]["saved_field"] == "attack_metadata_sources[].research_report"
    assert capabilities["research_report_contract"]["execution_authority"] == "none"
    assert report_schema["properties"]["execution_authority"] == {"enum": ["none"]}


def metadata_native(db, ability="Avatar_Ayaka_Icespine_Strike_New"):
    plan = action_event_plan(db)
    found = candidate(db, ability)
    plan.pop("hit_parameter_sources")
    plan["manual_template"]["hits"][0].pop("element")
    profile = plan["action_event_profiles"][0]
    profile["application_profile_id"] = "from.metadata"
    plan["mechanics_model"]["elemental_model"]["profiles"] = []
    plan["attack_metadata_sources"] = [{"action_event_profile_id": profile["id"], "version_line": "release",
        "selection": {"character_id": 90000001, "slot": "skill", "constellation": 0},
        "candidate_id": found["candidate_id"], "counter_tag": "declared.metadata.shared",
        "application_mode": "sequence",
        "purpose": "single_stat_damage_multiplier", "status": "confirmed",
        "source": "工程夹具逐攻击段用途说明；不认定完整游戏派发",
        "verification": {"status": "confirmed", "locator": "tests/test_runtime_attack_metadata.py fixture node",
            "version": "commit 342084292ba4c3c6ec2bc34c045de4de72ae8d53", "reviewed_at": "2026-10-18",
            "component_scope": "selected candidate exact attack component and declared shared counter",
            "confirmed_fields": ["application_mode", "counter_tag", "shared_scope"]}}]
    return plan


def test_fresh_metadata_one_acceptance_runs_three_actions_without_native_numbers(metadata_db):
    plan = metadata_native(metadata_db)
    before = copy.deepcopy(plan)
    preview = compose_equipment_plan(plan, metadata_db)
    assert preview["state"] == "pending"
    record = preview["attack_metadata_compilations"][0]
    assert record["status"] == "ready", record
    assert record["compiled_fields"] == {"element": "cryo", "param_index": 0, "gauge_units": 2,
        "application_mode": "sequence",
        "counter_tag": "declared.metadata.shared", "rule_group": "default",
        "application_identity_status": "declared", "tax_applied": False}
    assert record["application_identity"]["status"] == "declared"
    assert "具体攻击段" in record["application_identity"]["next_action"]
    assert record["source_fingerprint"] == fingerprint(record["query"]["semantic_dependency"])
    assert record["metadata_dependency_fingerprint"] == fingerprint(record["semantic_dependency"])
    assert "expected_source_fingerprint" not in plan["attack_metadata_sources"][0]
    assert plan == before
    confirm_action_events(plan, metadata_db)
    output = run_plan_calc(plan, metadata_db)
    assert [h["expected"] for h in output["result"]["hits"]] == pytest.approx([45 * 4.305600166320801] * 3)
    native = output["plan"]
    assert "hit_parameter_sources" not in native and "elemental_sources" not in native
    assert native["mechanics_model"]["elemental_model"]["profiles"] == []
    assert native["mechanics_model"]["hit_patterns"] == []
    assert not ({"element", "multiplier"} & native["manual_template"]["hits"][0].keys())
    trace = [r for r in output["result"]["elemental"]["trace"] if r["type"] == "elemental_application"]
    assert [r["allowed"] for r in trace] == [True, False, True]
    assert trace[0]["after"]["ordinary_aura"]["remaining_gu"] == pytest.approx(1.6)
    ec09 = output["report"]["action_event_compilations"][0]
    assert ec09["metadata_source_fingerprint"] == record["source_fingerprint"]
    assert ec09["metadata_dependency_fingerprint"] == record["metadata_dependency_fingerprint"]


def _dynamic_c2_metadata_plan(metadata_db, fake_repo):
    from tests.test_elemental_swirl import swirl_model

    def add_level(rows):
        level = copy.deepcopy(rows[0])
        level["proudSkillId"] = 99213
        level["level"] = 13
        rows.append(level)

    change(metadata_db, fake_repo, "ExcelBinOutput/ProudSkillExcelConfigData.json", add_level)

    def set_pyro(rows):
        prop = rows[0]["Default"]["GPPBBDEICKP"]["Icespine_Strike_Delay"]["HHDOFNHCIAE"][0][
            "IDCIIIFMDCM"]["LGHDGGJIJOK"]["EHKJIDFINEK"]
        prop["EPPIDAILPBN"] = "Fire"

    change(metadata_db, fake_repo, SOURCE, set_pyro)
    plan = metadata_native(metadata_db)
    source = plan["attack_metadata_sources"][0]
    source["selection"].update(constellation=2, talent_level=13)
    profile = plan["action_event_profiles"][0]
    profile["kind"] = "calibrated_single_application_v2"
    profile.pop("contact_profile_id")
    elemental = swirl_model(None)
    elemental["ruleset"] = "gauge-swirl-dynamic-v3"
    elemental["profiles"] = []
    plan["mechanics_model"]["elemental_model"] = elemental
    return plan


def _add_progression_without_constellation(plan):
    plan["equipment_inputs"]["a"] = {
        "mode": "panel", "panel_policy": "static_included", "weapon": None,
        "artifact_sets": [], "source_refs": [{"kind": "manual", "note": "工程命座兼容夹具"}],
        "data_version": None,
        "progression": {"ascension": 6, "status": "confirmed", "source": "工程成长声明"},
    }


def test_dynamic_metadata_accepts_pyro_and_explicit_constellation_level(
        metadata_db, fake_repo):
    plan = _dynamic_c2_metadata_plan(metadata_db, fake_repo)

    preview = compose_equipment_plan(plan, metadata_db)
    record = preview["attack_metadata_compilations"][0]
    assert record["status"] == "ready", record["issues"]
    assert record["compiled_fields"]["element"] == "pyro"
    assert record["query"]["selected_candidate"]["projection"]["parameter"]["level"][
        "effective_level"] == 13
    output = run_plan_calc(confirm_action_events(plan, metadata_db), metadata_db)
    assert {hit["element"] for hit in output["plan"]["resolved_template"]["hits"]} == {"pyro"}
    assert len(output["result"]["hits"]) == 3


def test_dynamic_c2_source_accepts_progression_with_omitted_constellation(
        metadata_db, fake_repo):
    plan = _dynamic_c2_metadata_plan(metadata_db, fake_repo)
    _add_progression_without_constellation(plan)
    assert "constellation" not in plan["equipment_inputs"]["a"]["progression"]

    first = compose_equipment_plan(plan, metadata_db)
    metadata = first["attack_metadata_compilations"][0]
    event = first["action_event_compilations"][0]
    assert metadata["status"] == "ready", metadata["issues"]
    assert metadata["query"]["selection"]["constellation"] == 2
    assert not any(row["code"] == "action_event_progression_mismatch"
                   for row in first["missing_inputs"])
    assert event["required_confirmation"]

    plan["action_event_profiles"][0].update(event["required_confirmation"])
    accepted = compose_equipment_plan(plan, metadata_db)
    event = accepted["action_event_compilations"][0]
    assert event["status"] == "ready"
    assert event["generated"]
    assert not any(row["code"] == "action_event_progression_mismatch"
                   for row in accepted["missing_inputs"])


def test_dynamic_c2_source_explicit_c0_conflict_withdraws_accepted_generation(
        metadata_db, fake_repo):
    plan = _dynamic_c2_metadata_plan(metadata_db, fake_repo)
    _add_progression_without_constellation(plan)
    first = compose_equipment_plan(plan, metadata_db)
    plan["action_event_profiles"][0].update(
        first["action_event_compilations"][0]["required_confirmation"])
    accepted = compose_equipment_plan(plan, metadata_db)
    assert accepted["action_event_compilations"][0]["status"] == "ready"

    plan["equipment_inputs"]["a"]["progression"]["constellation"] = 0
    pending = compose_equipment_plan(plan, metadata_db)
    event = pending["action_event_compilations"][0]
    assert pending["state"] == "pending"
    assert any(row["code"] == "action_event_progression_mismatch"
               for row in pending["missing_inputs"])
    assert event["status"] == "pending"
    assert event["required_confirmation"] is None
    assert event["generated"] == []


def test_database_confirmed_attenuation_tag_must_match_plan_counter_tag(metadata_db, fake_repo):
    def add_tag(rows):
        info = rows[0]["Default"]["GPPBBDEICKP"]["Icespine_Strike_Delay"]["HHDOFNHCIAE"][0][
            "IDCIIIFMDCM"]["LGHDGGJIJOK"]
        info["FHNDLIJJDEA"] = "Fixture_Icespine_Counter"

    change(metadata_db, fake_repo, SOURCE, add_tag)
    plan = metadata_native(metadata_db)
    plan["attack_metadata_sources"][0]["counter_tag"] = "Fixture_Icespine_Counter"
    plan["attack_metadata_sources"][0]["status"] = "assumed"
    plan["attack_metadata_sources"][0].pop("verification")
    report = compose_equipment_plan(plan, metadata_db)
    record = report["attack_metadata_compilations"][0]
    assert record["status"] == "ready"
    assert record["application_identity"]["status"] == "database_confirmed"
    assert record["compiled_fields"]["counter_tag"] == "Fixture_Icespine_Counter"
    assert record["compiled_fields"]["rule_group"] == "default"
    elemental_record = report["elemental_compilations"][0]
    assert elemental_record["application_identity"]["status"] == "database_confirmed"
    assert elemental_record["application_identity"]["tag"] == "Fixture_Icespine_Counter"

    plan["attack_metadata_sources"][0]["counter_tag"] = "Elemental_Art"
    pending = compose_equipment_plan(plan, metadata_db)
    rejected = pending["attack_metadata_compilations"][0]
    assert rejected["status"] == "pending" and rejected["compiled_fields"] == {}
    assert "attenuationTag不一致" in rejected["issues"][0]["message"]


def test_same_selection_different_candidates_reuse_fresh_reads_and_never_client_cache(metadata_db, monkeypatch):
    from genshin_db import runtime_action_parameter, runtime_attenuation

    plan = metadata_native(metadata_db)
    other = metadata_native(metadata_db, "Avatar_Kazuha_ElementalArt_FlyAway")
    original_profile = plan["action_event_profiles"][0]
    second_profile = copy.deepcopy(original_profile)
    second_profile.update(id="hold", prototype_hit_id="hold.proto", application_profile_id="hold.app", variant="hold")
    other_source = copy.deepcopy(other["attack_metadata_sources"][0])
    other_source["action_event_profile_id"] = "hold"
    prototype = copy.deepcopy(plan["manual_template"]["hits"][0])
    prototype["id"] = "hold.proto"
    plan["manual_template"]["hits"].append(prototype)
    plan["action_event_profiles"].append(second_profile)
    plan["attack_metadata_sources"].append(other_source)
    ability = copy.deepcopy(plan["rotation_model"]["abilities"][0])
    ability.update(id="hold.e", variant="hold")
    plan["rotation_model"]["abilities"].append(ability)
    plan["rotation_model"]["actions"][1]["ability_id"] = "hold.e"
    plan["action_event_bindings"].append({"ability_id": "hold.e", "profile_id": "hold"})
    calls = {"parameters": 0, "attenuation": 0}
    parameter_getter, attenuation_getter = runtime_action_parameter.get_action_parameter_context, runtime_attenuation.get_runtime_attenuation_profile

    def parameter(*args, **kwargs):
        calls["parameters"] += 1
        return parameter_getter(*args, **kwargs)

    def attenuation(*args, **kwargs):
        calls["attenuation"] += 1
        return attenuation_getter(*args, **kwargs)

    monkeypatch.setattr(runtime_action_parameter, "get_action_parameter_context", parameter)
    monkeypatch.setattr(runtime_attenuation, "get_runtime_attenuation_profile", attenuation)
    report = compose_equipment_plan(plan, metadata_db)
    assert calls == {"parameters": 1, "attenuation": 1}
    assert all(r["status"] == "ready" for r in report["attack_metadata_compilations"]), report["missing_inputs"]
    plan = confirm_action_events(plan, metadata_db)
    first = run_plan_calc(plan, metadata_db)
    plan["resolution"] = {"attack_metadata_compilations": [{"status": "ready", "multiplier": 999999}]}
    plan["resolved_template"] = copy.deepcopy(first["plan"]["resolved_template"])
    plan["resolved_template"]["hits"][0]["multiplier"] = 999999
    second = run_plan_calc(plan, metadata_db)
    assert second["result"] == first["result"]


@pytest.mark.parametrize("change", ["source", "counter_tag", "status", "candidate"])
def test_changed_metadata_semantics_never_reuse_old_profile_acceptance(metadata_db, change):
    plan = confirm_action_events(metadata_native(metadata_db), metadata_db)
    source = plan["attack_metadata_sources"][0]
    if change == "source":
        source["source"] += " 修订用途"
    elif change == "counter_tag":
        source["counter_tag"] = "different.shared"
    elif change == "status":
        source["status"] = "pending"
    else:
        source["candidate_id"] = "unknown-candidate"
    with pytest.raises(PlanError):
        run_plan_calc(plan, metadata_db)


def test_unknown_candidate_keeps_complete_native_draft_and_does_not_fill_fields(metadata_db):
    plan = metadata_native(metadata_db)
    plan["attack_metadata_sources"][0]["candidate_id"] = None
    report = compose_equipment_plan(plan, metadata_db)
    assert report["state"] == "pending"
    assert report["attack_metadata_compilations"][0]["query"]["candidates"]
    assert report["action_event_compilations"][0]["generated"] == []
    assert "element" not in report["plan"]["manual_template"]["hits"][0]


def _another_source(plan, suffix, *, candidate_id=None):
    profile = copy.deepcopy(plan["action_event_profiles"][0])
    profile.update(id="profile." + suffix, prototype_hit_id="proto." + suffix,
                   application_profile_id="app." + suffix)
    hit = copy.deepcopy(plan["manual_template"]["hits"][0])
    hit["id"] = profile["prototype_hit_id"]
    binding = copy.deepcopy(plan["attack_metadata_sources"][0])
    binding.update(action_event_profile_id=profile["id"], candidate_id=candidate_id)
    plan["action_event_profiles"].append(profile)
    plan["manual_template"]["hits"].append(hit)
    plan["attack_metadata_sources"].append(binding)


def test_pending_peer_never_partially_mutates_private_work_plan(metadata_db):
    from genshin_app.equipment.attack_metadata import prepare_attack_metadata

    plan = metadata_native(metadata_db)
    _another_source(plan, "pending")
    template = copy.deepcopy(plan["manual_template"])
    before = copy.deepcopy(plan), copy.deepcopy(template)
    issues = []
    prepared = prepare_attack_metadata(plan, template, metadata_db, lambda *args: issues.append(args))
    assert (plan, template) == before
    assert prepared.sources == [] and prepared.parameter_contexts == {} and prepared.attenuation_profiles == {}
    assert prepared.application_identities == {}
    assert len(prepared.records) == 2
    assert all(r["status"] == "pending" and r["compiled_fields"] == {} for r in prepared.records)
    assert prepared.records[0]["issues"][0]["code"] == "attack_metadata_peer_pending"
    assert issues


def test_missing_database_counter_tag_never_promotes_an_assumed_override_without_component_evidence(metadata_db):
    plan = metadata_native(metadata_db)
    source = plan["attack_metadata_sources"][0]
    source.pop("verification")
    source.update(status="assumed", source="仅为待核实假设，不是逐攻击段正向证据")
    report = compose_equipment_plan(plan, metadata_db)
    record = report["attack_metadata_compilations"][0]
    assert report["state"] == "pending" and record["status"] == "pending"
    assert record["compiled_fields"] == {}
    assert "assumed声明不能升格" in record["issues"][0]["message"]
    task = record["verification_task"]
    assert task["attack_identity"]["candidate_id"] == source["candidate_id"]
    assert "counter_tag" in task["missing_fields"]
    assert "attenuationTag" in task["keywords"]
    assert "动作类别" in task["acceptance_required"]


def test_free_text_confirmed_source_without_structured_evidence_stays_pending(metadata_db):
    plan = metadata_native(metadata_db)
    source = plan["attack_metadata_sources"][0]
    source.pop("verification")
    source.update(status="confirmed", source="我确认这个标签正确")
    record = compose_equipment_plan(plan, metadata_db)["attack_metadata_compilations"][0]
    assert record["status"] == "pending" and record["compiled_fields"] == {}
    assert "结构化逐段证据" in record["issues"][0]["message"]


def test_legacy_counter_field_is_read_only_compatible_but_missing_mode_migrates_to_pending(metadata_db):
    plan = metadata_native(metadata_db)
    source = plan["attack_metadata_sources"][0]
    source["shared_group"] = source.pop("counter_tag")
    ready = compose_equipment_plan(plan, metadata_db)["attack_metadata_compilations"][0]
    assert ready["status"] == "ready" and ready["compiled_fields"]["counter_tag"] == "declared.metadata.shared"
    assert ready["semantic_dependency"]["interpretation"]["legacy_counter_tag_field"] == "shared_group"

    source.pop("application_mode")
    pending = compose_equipment_plan(plan, metadata_db)["attack_metadata_compilations"][0]
    assert pending["status"] == "pending"
    assert pending["application_mode"]["deprecated_alias"] == "missing"


def test_receipt_budget_keeps_tail_status_without_query_or_materialization(metadata_db, monkeypatch):
    from genshin_app.equipment import attack_metadata
    from genshin_db import runtime_attack_metadata

    plan = metadata_native(metadata_db)
    _another_source(plan, "unqueried", candidate_id="unknown-tail-candidate")
    template = copy.deepcopy(plan["manual_template"])
    before = copy.deepcopy(plan), copy.deepcopy(template)
    calls, real_getter = [], runtime_attack_metadata.get_attack_metadata_context

    def getter(*args, **kwargs):
        calls.append(kwargs["candidate_id"])
        return real_getter(*args, **kwargs)

    # Deliberately tiny threshold forces the production budget branch with the
    # normal bounded fixture response; production retains the full 2 MiB cap.
    monkeypatch.setattr(attack_metadata, "MAX_RECEIPT_BYTES", 1024)
    monkeypatch.setattr(runtime_attack_metadata, "get_attack_metadata_context", getter)
    prepared = attack_metadata.prepare_attack_metadata(plan, template, metadata_db, lambda *args: None)
    assert len(calls) == 1
    assert (plan, template) == before
    assert len(prepared.records) == 2 and all(r["status"] == "pending" for r in prepared.records)
    assert prepared.records[-1]["issues"][0]["code"] == "attack_metadata_skipped"
    assert all("query" not in r for r in prepared.records)
    assert prepared.sources == [] and prepared.parameter_contexts == {} and prepared.attenuation_profiles == {}
    assert prepared.application_identities == {}


def test_inconsistent_server_projection_does_not_hide_behind_unchanged_source_fingerprint(metadata_db, monkeypatch):
    from genshin_db import runtime_attack_metadata

    plan = metadata_native(metadata_db)
    real_getter = runtime_attack_metadata.get_attack_metadata_context

    def getter(*args, **kwargs):
        context = real_getter(*args, **kwargs)
        context.public_projection["selected_candidate"]["projection"]["gauge"]["value"] = 4
        return context

    monkeypatch.setattr(runtime_attack_metadata, "get_attack_metadata_context", getter)
    report = compose_equipment_plan(plan, metadata_db)
    assert report["state"] == "pending"
    row = report["attack_metadata_compilations"][0]
    assert row["status"] == "pending" and row["compiled_fields"] == {}
    assert "确认载荷不一致" in row["issues"][0]["message"]
    assert report["action_event_compilations"][0]["generated"] == []


@pytest.mark.parametrize(("mode", "application", "icd"), [
    ("no_icd", "apply", {"kind": "none"}),
    ("no_application", "none", None),
])
def test_attack_metadata_can_declare_nonsequence_application_modes(metadata_db, mode, application, icd):
    plan = metadata_native(metadata_db)
    source = plan["attack_metadata_sources"][0]
    source["application_mode"] = mode
    source.pop("counter_tag")
    source["source"] = f"逐攻击组件外部证据：application_mode={mode}"
    report = compose_equipment_plan(plan, metadata_db)
    record = report["attack_metadata_compilations"][0]
    assert record["status"] == "ready" and record["application_mode"]["value"] == mode
    assert record["application_identity"]["status"] == "not_applicable"
    assert record["compiled_fields"]["application_mode"] == mode
    assert record["compiled_fields"]["counter_tag"] is None
    assert record["compiled_fields"]["rule_group"] is None
    materialized = next(p for p in report["mechanics_model"]["elemental_model"]["profiles"]
                        if p["id"] == record["application_profile_id"])
    assert materialized["application"] == application
    assert materialized.get("icd") == icd


def test_component_evidence_is_independent_from_whole_source_interpretation_status(metadata_db):
    plan = metadata_native(metadata_db)
    source = plan["attack_metadata_sources"][0]
    source.update(application_mode="no_icd", status="assumed")
    source.pop("counter_tag")
    report = compose_equipment_plan(plan, metadata_db)
    record = report["attack_metadata_compilations"][0]
    assert record["status"] == "ready"
    assert record["compiled_fields"]["application_mode"] == "no_icd"
    assert record["application_identity"]["status"] == "not_applicable"


@pytest.mark.parametrize(("mode", "deprecated"), [("pending", False), ("unknown", True)])
def test_attack_metadata_pending_mode_is_canonical_and_never_materializes(metadata_db, mode, deprecated):
    plan = metadata_native(metadata_db)
    source = plan["attack_metadata_sources"][0]
    source.update(application_mode=mode, status="pending")
    source.pop("counter_tag")
    report = compose_equipment_plan(plan, metadata_db)
    record = report["attack_metadata_compilations"][0]
    assert report["state"] == "pending" and record["status"] == "pending"
    assert record["application_mode"]["value"] == "pending"
    assert bool(record["application_mode"].get("deprecated_alias")) is deprecated
    assert record["compiled_fields"] == {}
    assert not any(p["id"] == record["application_profile_id"]
                   for p in report["mechanics_model"]["elemental_model"]["profiles"])


@pytest.mark.parametrize("mode", ["no_icd", "no_application", "pending", "unknown"])
def test_attack_metadata_nonsequence_modes_reject_counter_tag(metadata_db, mode):
    plan = metadata_native(metadata_db)
    plan["attack_metadata_sources"][0]["application_mode"] = mode
    with pytest.raises(PlanError, match="不得再声明计数标签"):
        compose_equipment_plan(plan, metadata_db)


@pytest.mark.parametrize("mode", ["no_icd", "no_application"])
def test_nonsequence_metadata_does_not_require_an_attenuation_rule(metadata_db, monkeypatch, mode):
    from genshin_db import runtime_attenuation
    from genshin_db.runtime_catalog import RuntimeConfigError

    plan = metadata_native(metadata_db)
    source = plan["attack_metadata_sources"][0]
    source.update(application_mode=mode, source=f"逐攻击组件外部证据：application_mode={mode}")
    source.pop("counter_tag")

    def missing_rule(*args, **kwargs):
        raise RuntimeConfigError("工程夹具中没有规则模板", "not_found", 404)

    monkeypatch.setattr(runtime_attenuation, "get_runtime_attenuation_profile", missing_rule)
    report = compose_equipment_plan(plan, metadata_db)
    record = report["attack_metadata_compilations"][0]
    assert record["status"] == "ready"
    assert record["application_mode"]["value"] == mode
    assert record["projection"]["attenuation"]["status"] == "pending"


def test_no_application_can_ignore_unknown_gauge_but_no_icd_cannot(metadata_db, fake_repo):
    plan = metadata_native(metadata_db)

    def unsupported_gauge(rows):
        prop = rows[0]["Default"]["GPPBBDEICKP"]["Icespine_Strike_Delay"]["HHDOFNHCIAE"][0][
            "IDCIIIFMDCM"]["LGHDGGJIJOK"]["EHKJIDFINEK"]
        prop["DBGOKAKAHFP"] = 75

    change(metadata_db, fake_repo, SOURCE, unsupported_gauge)
    source = plan["attack_metadata_sources"][0]
    source.update(application_mode="no_application", source="逐攻击组件外部证据：明确无附着")
    source.pop("counter_tag")
    ready_report = compose_equipment_plan(plan, metadata_db)
    ready = ready_report["attack_metadata_compilations"][0]
    assert ready["status"] == "ready"
    assert ready["compiled_fields"]["application_mode"] == "no_application"
    assert "gauge_units" not in ready["compiled_fields"]
    optional = [
        row for row in ready_report["task_capability"]["degraded"]
        if row["consumer"].startswith("attack_metadata:")
        and ":optional:" in row["consumer"]
    ]
    assert optional
    assert all(not row["required"] and not row["consumed"] for row in optional)

    source["application_mode"] = "no_icd"
    pending = compose_equipment_plan(plan, metadata_db)["attack_metadata_compilations"][0]
    assert pending["status"] == "pending"
    assert "parameter/element/gauge" in pending["issues"][0]["message"]
