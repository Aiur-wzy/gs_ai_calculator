"""Per-component application adapters remain bounded, local, and auditable."""
from __future__ import annotations

import copy

import pytest
from test_action_dispatch_sources import (
    _install,
    _multi_plan,
    _multi_profile,
    _single_plan,
    _single_profile,
    _verification,
)
from test_periodic_actions import confirm_periodic, periodic_plan
from test_periodic_external_refresh import _refresh, _trigger_action
from test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from test_runtime_attack_metadata import SOURCE, change, metadata_db  # noqa: F401
from test_runtime_periodic_attack import periodic_db  # noqa: F401

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.periodic_receipt import validate_periodic_receipt
from genshin_app.equipment.schema import PlanError, validate_plan
from genshin_app.formula_workbook import FormulaWorkbookError, build_formula_workbook
from genshin_db.runtime_catalog import fingerprint

# ruff: noqa: F811


def _emission(profile, component="slice"):
    return next(row for row in profile["emissions"] if row["component_id"] == component)


def _adapter(profile, component="slice", *, mode="inherit", counter_tag=None,
             gate="inherit", period_hits=None, first_attempt_ordinal=None,
             status="confirmed", accepted_base_fingerprint=None, scope=None,
             icd_hit_interval=None, accepted_base_rule_fingerprint=None,
             icd_reset_seconds=None):
    base = _emission(profile, component)["application"]
    if mode == "sequence" and counter_tag is None:
        counter_tag = base.get("counter_tag") or "Elemental_Burst"
    verification = None
    changes_icd = icd_hit_interval is not None
    if mode not in ("inherit", "pending") or gate != "inherit" or changes_icd:
        verification = _verification(component, sequence=mode == "sequence")
        if mode in ("inherit", "pending"):
            verification["confirmed_fields"] = []
            if gate != "inherit":
                verification["confirmed_fields"].append("attempt_gate")
            if changes_icd:
                verification["confirmed_fields"].append("icd_rule")
        elif gate != "inherit":
            verification["confirmed_fields"].append("attempt_gate")
        if changes_icd and "icd_rule" not in verification["confirmed_fields"]:
            verification["confirmed_fields"].append("icd_rule")
        verification["component_scope"] = scope or component
    result = {
        "accepted_base_fingerprint": accepted_base_fingerprint or fingerprint(base),
        "mode": mode,
        "counter_tag": counter_tag,
        "attempt_gate": {"mode": gate, "period_hits": period_hits,
                         "first_attempt_ordinal": first_attempt_ordinal},
        "status": status,
        "source": "fixture://application-adapter/" + component,
        "verification": verification,
    }
    if changes_icd:
        result["icd_rule"] = {
            "mode": "timer_counter",
            "accepted_base_rule_fingerprint": accepted_base_rule_fingerprint,
            "hit_interval": icd_hit_interval,
            "reset_seconds": icd_reset_seconds,
        }
    return result


def _base_icd_fingerprint(output, component):
    audit = next(row for row in output["report"]["action_dispatch_compilations"][0][
        "application_rule_audit"] if row["component_id"] == component)
    return audit["base_icd_rule_fingerprint"]


def _run(periodic_db, monkeypatch, profile, adapter, component="slice"):
    _install(monkeypatch, profile)
    plan = _multi_plan(periodic_db, profile)
    plan["action_dispatch_sources"][0]["scenario_calibrations"] = [{
        "component_id": component, "application_adapter": adapter}]
    return plan, run_plan_calc(plan, periodic_db)


def test_first_unadapted_compile_exposes_copyable_base_rule_fingerprints(
        periodic_db, monkeypatch):
    profile = _multi_profile(periodic_db)
    _install(monkeypatch, profile)
    plan = _multi_plan(periodic_db, profile)

    output = run_plan_calc(plan, periodic_db)

    assert output["report"]["state"] == "ready", output["report"]["missing_inputs"]
    audits = {row["component_id"]: row for row in output["report"][
        "action_dispatch_compilations"][0]["application_rule_audit"]}
    assert set(audits) == {"slice", "blast"}
    for component, audit in audits.items():
        assert audit["base_application_fingerprint"] == fingerprint(
            _emission(profile, component)["application"])
        assert audit["base_rule"] == audit["effective_rule"]
        assert audit["adapter_fingerprint"] is None
        assert audit["base_icd_rule_fingerprint"] == fingerprint(audit["base_icd_rule"])
        assert audit["effective_icd_rule"]["kind"] == "sequence"
        assert audit["effective_icd_rule"]["rule_group"] == audit["base_icd_rule"][
            "rule_group"]
    mappings = output["report"]["action_dispatch_materialization"][
        "periodic_attack_sources"][0]["channels"]
    assert all("application_rule_audit" not in row and "application_adapter" not in row
               for row in mappings)
    validate_periodic_receipt(
        plan, output["result"], output["report"], output["result_receipt"])


def test_legacy_periodic_receipt_without_adapter_audit_remains_valid(periodic_db):
    plan = confirm_periodic(periodic_plan(periodic_db), periodic_db)

    output = run_plan_calc(plan, periodic_db)

    channels = output["report"]["periodic_attack_compilations"][0]["channels"]
    assert all("application_adapter" not in row and "application_rule_audit" not in row
               and "database_application_identity" not in row
               and "application_identity_divergence" not in row for row in channels)
    validate_periodic_receipt(
        plan, output["result"], output["report"], output["result_receipt"])


def test_periodic_components_share_one_evidence_derived_timer_counter_rule(
        periodic_db, monkeypatch):
    profile = _multi_profile(periodic_db)
    _install(monkeypatch, profile)
    baseline_plan = _multi_plan(periodic_db, profile)
    baseline = run_plan_calc(baseline_plan, periodic_db)
    assert baseline["report"]["state"] == "ready", baseline["report"]["missing_inputs"]

    plan = _multi_plan(periodic_db, profile)
    plan["action_dispatch_sources"][0]["scenario_calibrations"] = [
        {"component_id": component, "application_adapter": _adapter(
            profile, component, icd_hit_interval=2,
            accepted_base_rule_fingerprint=_base_icd_fingerprint(baseline, component))}
        for component in ("slice", "blast")]
    output = run_plan_calc(plan, periodic_db)

    assert output["report"]["state"] == "ready", output["report"]["missing_inputs"]
    dispatch = output["report"]["action_dispatch_compilations"][0]
    audits = {row["component_id"]: row for row in dispatch["application_rule_audit"]}
    effective = [audits[name]["effective_icd_rule"] for name in ("slice", "blast")]
    assert all(rule["kind"] == "timer_counter" and rule["hit_interval"] == 2
               and rule["first_hit"] == 1 and rule["sequence"] is None for rule in effective)
    assert effective[0]["rule_group"] == effective[1]["rule_group"]
    assert audits["slice"]["adapter_fingerprint"] != audits["blast"]["adapter_fingerprint"]
    assert audits["slice"]["base_icd_rule"]["rule_group"] == audits[
        "blast"]["base_icd_rule"]["rule_group"]

    channels = output["report"]["periodic_attack_compilations"][0]["channels"]
    assert {row["compiled_fields"]["rule_group"] for row in channels} == {
        effective[0]["rule_group"]}
    assert all(row["compiled_fields"]["database_rule_group"] ==
               audits[row["channel_id"]]["base_icd_rule"]["rule_group"] for row in channels)
    for channel in channels:
        elemental = next(row for row in output["report"]["elemental_compilations"]
                         if row["profile_id"] == channel["application_profile_id"])
        assert elemental["compiled_icd"] == {
            "kind": "timer_counter",
            "counter_tag": channel["compiled_fields"]["counter_tag"],
            "rule_group": channel["compiled_fields"]["rule_group"],
            "reset_seconds": audits[channel["channel_id"]]["effective_icd_rule"][
                "reset_seconds"],
            "hit_interval": 2,
        }
    generated = output["report"]["action_event_compilations"][0]["generated"]
    assert generated and all(row.get("application_attempted", True) for row in generated)
    validate_periodic_receipt(
        plan, output["result"], output["report"], output["result_receipt"])


def test_periodic_timer_counter_and_attempt_gate_are_independent_layers(
        periodic_db, monkeypatch):
    profile = _multi_profile(periodic_db)
    _install(monkeypatch, profile)
    baseline = run_plan_calc(_multi_plan(periodic_db, profile), periodic_db)
    plan = _multi_plan(periodic_db, profile)
    plan["action_dispatch_sources"][0]["scenario_calibrations"] = [
        {"component_id": "slice", "application_adapter": _adapter(
            profile, "slice", gate="every_n_landed_hits", period_hits=2,
            first_attempt_ordinal=1, icd_hit_interval=2,
            accepted_base_rule_fingerprint=_base_icd_fingerprint(baseline, "slice"))},
        {"component_id": "blast", "application_adapter": _adapter(
            profile, "blast", icd_hit_interval=2,
            accepted_base_rule_fingerprint=_base_icd_fingerprint(baseline, "blast"))},
    ]

    output = run_plan_calc(plan, periodic_db)

    assert output["report"]["state"] == "ready", output["report"]["missing_inputs"]
    generated = [row for row in output["report"]["action_event_compilations"][0]["generated"]
                 if row["channel_id"] == "slice"]
    for action_id in {row["action_id"] for row in generated}:
        rows = sorted((row for row in generated if row["action_id"] == action_id),
                      key=lambda row: row["ordinal"])
        assert [row["application_attempted"] for row in rows] == [True, False, True, False]
    channel = next(row for row in output["report"]["periodic_attack_compilations"][0][
        "channels"] if row["channel_id"] == "slice")
    elemental = next(row for row in output["report"]["elemental_compilations"]
                     if row["profile_id"] == channel["application_profile_id"])
    assert elemental["compiled_icd"]["kind"] == "timer_counter"
    assert elemental["compiled_icd"]["hit_interval"] == 2
    assert channel["application_rule_audit"]["effective_rule"]["attempt_gate"]["mode"] == (
        "every_n_landed_hits")


def test_timer_counter_reset_accepts_exact_microseconds(periodic_db, monkeypatch):
    profile = _multi_profile(periodic_db)
    _install(monkeypatch, profile)
    baseline = run_plan_calc(_multi_plan(periodic_db, profile), periodic_db)
    plan = _multi_plan(periodic_db, profile)
    plan["action_dispatch_sources"][0]["scenario_calibrations"] = [
        {"component_id": component, "application_adapter": _adapter(
            profile, component, icd_hit_interval=2, icd_reset_seconds=.000123,
            accepted_base_rule_fingerprint=_base_icd_fingerprint(baseline, component))}
        for component in ("slice", "blast")]

    output = run_plan_calc(plan, periodic_db)

    assert output["report"]["state"] == "ready", output["report"]["missing_inputs"]
    audits = output["report"]["action_dispatch_compilations"][0]["application_rule_audit"]
    assert all(row["effective_icd_rule"]["reset_seconds"] == .000123 for row in audits)


@pytest.mark.parametrize("reset", [.0000001, float("nan"), float("inf"), True])
def test_timer_counter_reset_rejects_sub_microsecond_or_nonfinite_values(
        periodic_db, monkeypatch, reset):
    profile = _multi_profile(periodic_db)
    _install(monkeypatch, profile)
    baseline = run_plan_calc(_multi_plan(periodic_db, profile), periodic_db)
    plan = _multi_plan(periodic_db, profile)
    plan["action_dispatch_sources"][0]["scenario_calibrations"] = [{
        "component_id": "slice", "application_adapter": _adapter(
            profile, "slice", icd_hit_interval=2, icd_reset_seconds=reset,
            accepted_base_rule_fingerprint=_base_icd_fingerprint(baseline, "slice"))}]

    with pytest.raises(PlanError):
        validate_plan(plan)


def test_timer_counter_continues_across_external_refresh_generation(
        periodic_db, monkeypatch):
    profile = _multi_profile(periodic_db)
    profile["lifecycle"].update(
        no_refresh_replacement_or_early_despawn=False,
        replacement_policy="replace_previous",
        no_early_despawn=True,
    )
    _install(monkeypatch, profile)
    baseline = run_plan_calc(_multi_plan(periodic_db, profile), periodic_db)
    plan = _multi_plan(periodic_db, profile)
    _trigger_action(plan)
    _refresh(plan, dispatch_ability_id="e")
    plan["action_dispatch_sources"][0]["scenario_calibrations"] = [
        {"component_id": component, "application_adapter": _adapter(
            profile, component, icd_hit_interval=3,
            accepted_base_rule_fingerprint=_base_icd_fingerprint(baseline, component))}
        for component in ("slice", "blast")]

    output = run_plan_calc(plan, periodic_db)

    assert output["report"]["state"] == "ready", output["report"]["missing_inputs"]
    channel = next(row for row in output["report"]["periodic_attack_compilations"][0]["channels"]
                   if row["channel_id"] == "slice")
    applications = [row for row in output["result"]["elemental"]["trace"]
                    if row["type"] == "elemental_application"
                    and row["profile_id"] == channel["application_profile_id"]
                    and row["action_id"] == "q0"]
    rows = [next(row for row in applications if row["t"] == time)
            for time in (.2, .45, .7, .95)]
    assert [row["application_attempted"] for row in rows] == [True] * 4
    assert [row["icd_audit"]["attempt_number"] for row in rows] == [1, 2, 3, 4]
    assert [row["allowed"] for row in rows] == [True, False, False, True]
    assert {row["icd_audit"]["anchor_t"] for row in rows} == {.2}
    generated = output["report"]["action_event_compilations"][0]["generated"]
    assert all(row.get("application_attempted", True) for row in generated)


@pytest.mark.parametrize("forbidden", ["element", "gauge_units", "sequence", "rule_group"])
def test_adapter_schema_does_not_accept_inline_game_rules(periodic_db, forbidden):
    profile = _multi_profile(periodic_db)
    plan = _multi_plan(periodic_db, profile)
    adapter = _adapter(profile)
    adapter[forbidden] = "forbidden"
    plan["action_dispatch_sources"][0]["scenario_calibrations"] = [{
        "component_id": "slice", "application_adapter": adapter}]

    with pytest.raises(PlanError, match="结构不合法"):
        validate_plan(plan)


def test_gate_override_requires_component_scoped_structured_evidence(periodic_db, monkeypatch):
    profile = _multi_profile(periodic_db)
    adapter = _adapter(
        profile, gate="every_n_landed_hits", period_hits=2, first_attempt_ordinal=1)
    adapter["verification"] = None
    plan = _multi_plan(periodic_db, profile)
    plan["action_dispatch_sources"][0]["scenario_calibrations"] = [{
        "component_id": "slice", "application_adapter": adapter}]
    with pytest.raises(PlanError, match="结构化证据"):
        validate_plan(plan)

    adapter = _adapter(profile, gate="every_n_landed_hits", period_hits=2,
                       first_attempt_ordinal=1, scope="another-component")
    plan["action_dispatch_sources"][0]["scenario_calibrations"] = [{
        "component_id": "slice", "application_adapter": adapter}]
    _install(monkeypatch, profile)
    report = compose_equipment_plan(plan, periodic_db)
    assert report["state"] == "pending"
    assert "action_dispatch_adapter_verification_required" in report[
        "action_dispatch_compilations"][0]["issues"][0]["message"]


def test_timer_counter_override_requires_fresh_base_and_exact_component_evidence(
        periodic_db, monkeypatch):
    profile = _multi_profile(periodic_db)
    _install(monkeypatch, profile)
    baseline = run_plan_calc(_multi_plan(periodic_db, profile), periodic_db)
    base_rule_fingerprint = _base_icd_fingerprint(baseline, "slice")

    missing_evidence = _adapter(
        profile, icd_hit_interval=2,
        accepted_base_rule_fingerprint=base_rule_fingerprint)
    missing_evidence["verification"] = None
    plan = _multi_plan(periodic_db, profile)
    plan["action_dispatch_sources"][0]["scenario_calibrations"] = [{
        "component_id": "slice", "application_adapter": missing_evidence}]
    with pytest.raises(PlanError, match="结构化证据"):
        validate_plan(plan)

    stale = _adapter(profile, icd_hit_interval=2,
                     accepted_base_rule_fingerprint="0" * 64)
    plan["action_dispatch_sources"][0]["scenario_calibrations"] = [{
        "component_id": "slice", "application_adapter": stale}]
    report = compose_equipment_plan(plan, periodic_db)
    assert report["state"] == "pending"
    assert "action_dispatch_materialization" not in report
    assert "action_dispatch_adapter_icd_base_stale" in report[
        "action_dispatch_compilations"][0]["issues"][0]["message"]

    wrong_scope = _adapter(
        profile, icd_hit_interval=2,
        accepted_base_rule_fingerprint=base_rule_fingerprint,
        scope="blast")
    plan["action_dispatch_sources"][0]["scenario_calibrations"] = [{
        "component_id": "slice", "application_adapter": wrong_scope}]
    report = compose_equipment_plan(plan, periodic_db)
    assert report["state"] == "pending"
    assert "action_dispatch_adapter_verification_required" in report[
        "action_dispatch_compilations"][0]["issues"][0]["message"]


def test_inherit_mode_can_add_local_landed_hit_gate_and_offline_receipt(
        periodic_db, monkeypatch):
    profile = _multi_profile(periodic_db)
    _plan, output = _run(periodic_db, monkeypatch, profile, _adapter(
        profile, gate="every_n_landed_hits", period_hits=2, first_attempt_ordinal=1))

    assert output["report"]["state"] == "ready", output["report"]["missing_inputs"]
    dispatch = output["report"]["action_dispatch_compilations"][0]
    audit = next(row for row in dispatch["application_rule_audit"]
                 if row["component_id"] == "slice")
    assert audit["base_application_fingerprint"] == fingerprint(
        _emission(profile)["application"])
    assert audit["base_rule"]["mode"] == audit["effective_rule"]["mode"] == "sequence"
    assert audit["effective_rule"]["attempt_gate"] == {
        "mode": "every_n_landed_hits", "period_hits": 2, "first_attempt_ordinal": 1}
    manifest_mapping = output["report"]["action_dispatch_materialization"][
        "periodic_attack_sources"][0]["channels"]
    slice_mapping = next(row for row in manifest_mapping if row["channel_id"] == "slice")
    blast_mapping = next(row for row in manifest_mapping if row["channel_id"] == "blast")
    assert slice_mapping["application_adapter"]["adapter_fingerprint"] == audit["adapter_fingerprint"]
    assert slice_mapping["application_rule_audit"] == audit
    assert "application_adapter" not in blast_mapping
    assert blast_mapping["application_mode"] == _emission(profile, "blast")["application"]["mode"]

    generated = [row for record in output["report"]["action_event_compilations"]
                 for row in record.get("generated", []) if row.get("channel_id") == "slice"]
    for action_id in {row["action_id"] for row in generated}:
        rows = sorted((row for row in generated if row["action_id"] == action_id),
                      key=lambda row: row["ordinal"])
        assert [row["application_attempted"] for row in rows] == [True, False, True, False]
    validate_periodic_receipt(
        output["plan"], output["result"], output["report"], output["result_receipt"])


def test_sequence_can_be_locally_overridden_to_no_icd_without_losing_damage(
        periodic_db, monkeypatch):
    profile = _multi_profile(periodic_db)
    plan, output = _run(periodic_db, monkeypatch, profile, _adapter(profile, mode="no_icd"))

    assert output["report"]["state"] == "ready", output["report"]["missing_inputs"]
    record = output["report"]["periodic_attack_compilations"][0]
    channel = next(row for row in record["channels"] if row["channel_id"] == "slice")
    assert channel["compiled_fields"]["application_mode"] == "no_icd"
    assert channel["application_identity_divergence"] is True
    assert channel["application_rule_audit"]["base_rule"]["mode"] == "sequence"
    assert channel["application_rule_audit"]["effective_rule"]["mode"] == "no_icd"
    assert output["result"]["hits"]
    assert any(row["channel_id"] == "slice" for row in
               output["report"]["action_event_compilations"][0]["generated"])
    validate_periodic_receipt(
        plan, output["result"], output["report"], output["result_receipt"])


def test_no_application_adapter_keeps_damage_and_does_not_touch_peer_or_catalog(
        periodic_db, monkeypatch):
    profile = _multi_profile(periodic_db)
    original = copy.deepcopy(profile)
    plan, output = _run(periodic_db, monkeypatch, profile, _adapter(
        profile, mode="no_application"))

    assert output["report"]["state"] == "ready", output["report"]["missing_inputs"]
    assert len(output["result"]["hits"]) == 15
    generated = output["report"]["action_event_compilations"][0]["generated"]
    assert any(row["channel_id"] == "slice" for row in generated)
    applications = [row for row in output["result"]["elemental"]["trace"]
                    if row["type"] == "elemental_application"]
    slice_applications = [row for row in applications
                          if row["profile_id"].endswith(".slice.application")]
    blast_applications = [row for row in applications
                          if row["profile_id"].endswith(".blast.application")]
    assert slice_applications and all(
        not row["allowed"] and row["reason"] == "no_application"
        for row in slice_applications)
    assert blast_applications and any(row["reason"] != "no_application"
                                      for row in blast_applications)
    assert profile == original
    validate_periodic_receipt(
        plan, output["result"], output["report"], output["result_receipt"])


def test_no_icd_baseline_can_adapt_to_sequence_with_component_evidence(
        periodic_db, monkeypatch):
    profile = _multi_profile(periodic_db)
    application = _emission(profile)["application"]
    database_tag = application["counter_tag"]
    application.update(mode="no_icd", counter_tag=None,
                       verification=_verification("slice", sequence=False))
    plan, output = _run(periodic_db, monkeypatch, profile, _adapter(
        profile, mode="sequence", counter_tag=database_tag))

    assert output["report"]["state"] == "ready", output["report"]["missing_inputs"]
    dispatch = output["report"]["action_dispatch_compilations"][0]
    assert dispatch["source_verification"]["status"] == "ready"
    audit = next(row for row in dispatch["application_rule_audit"]
                 if row["component_id"] == "slice")
    assert audit["base_rule"]["mode"] == "no_icd"
    assert audit["effective_rule"]["mode"] == "sequence"
    assert audit["identity_divergence"] is False
    validate_periodic_receipt(
        plan, output["result"], output["report"], output["result_receipt"])


def test_confirmed_component_adapter_may_override_counter_tag_but_not_db_rule_group(
        periodic_db, monkeypatch):
    profile = _multi_profile(periodic_db)
    database_tag = _emission(profile)["application"]["counter_tag"]
    effective_tag = "Agent_Special_Burst"
    plan, output = _run(periodic_db, monkeypatch, profile, _adapter(
        profile, mode="sequence", counter_tag=effective_tag))

    assert output["report"]["state"] == "ready", output["report"]["missing_inputs"]
    channel = next(row for row in output["report"]["periodic_attack_compilations"][0]["channels"]
                   if row["channel_id"] == "slice")
    assert channel["compiled_fields"]["counter_tag"] == effective_tag
    assert channel["compiled_fields"]["rule_group"]
    assert channel["application_identity"] == {
        "status": "adapter_override", "tag": effective_tag, "database_tag": database_tag,
        "source": channel["application_adapter"], "evidence": "confirmed_component_adapter",
        "next_action": None}
    assert channel["database_application_identity"]["tag"] == database_tag
    assert channel["application_identity_divergence"] is True
    assert channel["application_rule_audit"]["base_rule"]["counter_tag"] == database_tag
    assert channel["application_rule_audit"]["effective_rule"]["counter_tag"] == effective_tag
    semantic = next(row for row in output["report"]["periodic_attack_compilations"][0][
        "semantic_dependency"]["channels"] if row["candidate_id"] == channel["candidate_id"])
    assert semantic["application_adapter"]["adapter_fingerprint"] == channel[
        "application_adapter"]["adapter_fingerprint"]
    assert semantic["application_rule_audit"] == channel["application_rule_audit"]
    assert semantic["identity_divergence"] is True
    validate_periodic_receipt(
        plan, output["result"], output["report"], output["result_receipt"])


@pytest.mark.parametrize(("mutation", "expected"), [
    ("stale", "action_dispatch_adapter_base_stale"),
    ("pending", "action_dispatch_adapter_pending"),
    ("fixed_gate", "action_dispatch_adapter_fixed_gate_unsupported"),
    ("wrong_scope", "action_dispatch_adapter_verification_required"),
])
def test_stale_pending_fixed_gate_and_cross_component_evidence_fail_atomically(
        periodic_db, monkeypatch, mutation, expected):
    profile = _multi_profile(periodic_db)
    component = "blast" if mutation == "fixed_gate" else "slice"
    kwargs = {}
    if mutation == "stale":
        kwargs["accepted_base_fingerprint"] = "0" * 64
    elif mutation == "pending":
        kwargs["status"] = "pending"
    elif mutation == "fixed_gate":
        kwargs.update(gate="every_n_landed_hits", period_hits=2,
                      first_attempt_ordinal=1)
    elif mutation == "wrong_scope":
        kwargs.update(mode="no_icd", scope="another-component")
    adapter = _adapter(profile, component, **kwargs)
    _install(monkeypatch, profile)
    plan = _multi_plan(periodic_db, profile)
    plan["action_dispatch_sources"][0]["scenario_calibrations"] = [{
        "component_id": component, "application_adapter": adapter}]

    report = compose_equipment_plan(plan, periodic_db)

    assert report["state"] == "pending"
    assert "action_dispatch_materialization" not in report
    assert not report.get("action_event_compilations")
    assert expected in report["action_dispatch_compilations"][0]["issues"][0]["message"]
    if mutation == "stale":
        record = report["action_dispatch_compilations"][0]
        assert record["application_adapter_audit"][0]["accepted_base_fingerprint"] == "0" * 64
        assert record["application_rule_audit"][0]["base_application_fingerprint"] == fingerprint(
            _emission(profile)["application"])


def test_offline_receipt_rejects_adapter_audit_tamper(periodic_db, monkeypatch):
    profile = _multi_profile(periodic_db)
    plan, output = _run(periodic_db, monkeypatch, profile, _adapter(
        profile, gate="every_n_landed_hits", period_hits=2, first_attempt_ordinal=1))
    report = copy.deepcopy(output["report"])
    report["periodic_attack_compilations"][0]["channels"][0][
        "application_rule_audit"]["identity_divergence"] = True

    with pytest.raises(ValueError):
        validate_periodic_receipt(plan, output["result"], report, output["result_receipt"])


def test_offline_receipt_rejects_timer_counter_rule_tamper(periodic_db, monkeypatch):
    profile = _multi_profile(periodic_db)
    _install(monkeypatch, profile)
    baseline = run_plan_calc(_multi_plan(periodic_db, profile), periodic_db)
    plan = _multi_plan(periodic_db, profile)
    plan["action_dispatch_sources"][0]["scenario_calibrations"] = [
        {"component_id": component, "application_adapter": _adapter(
            profile, component, icd_hit_interval=2,
            accepted_base_rule_fingerprint=_base_icd_fingerprint(baseline, component))}
        for component in ("slice", "blast")]
    output = run_plan_calc(plan, periodic_db)
    report = copy.deepcopy(output["report"])
    report["periodic_attack_compilations"][0]["channels"][0][
        "application_rule_audit"]["effective_icd_rule"]["hit_interval"] = 3

    with pytest.raises(ValueError):
        validate_periodic_receipt(plan, output["result"], report, output["result_receipt"])


def test_fixed_periodic_channel_no_application_is_local_and_keeps_all_damage(
        periodic_db, monkeypatch):
    profile = _multi_profile(periodic_db)
    original = copy.deepcopy(profile)
    plan, output = _run(periodic_db, monkeypatch, profile, _adapter(
        profile, "blast", mode="no_application"), component="blast")

    assert output["report"]["state"] == "ready", output["report"]["missing_inputs"]
    assert len(output["result"]["hits"]) == 15
    trace = [row for row in output["result"]["elemental"]["trace"]
             if row["type"] == "elemental_application"]
    blast = [row for row in trace if row["profile_id"].endswith(".blast.application")]
    slices = [row for row in trace if row["profile_id"].endswith(".slice.application")]
    assert blast and all(not row["allowed"] and row["reason"] == "no_application"
                         for row in blast)
    assert slices and any(row["reason"] != "no_application" for row in slices)
    channels = output["report"]["periodic_attack_compilations"][0]["channels"]
    blast_channel = next(row for row in channels if row["channel_id"] == "blast")
    slice_channel = next(row for row in channels if row["channel_id"] == "slice")
    assert blast_channel["compiled_fields"]["application_mode"] == "no_application"
    assert "application_adapter" not in slice_channel
    assert profile == original
    validate_periodic_receipt(
        plan, output["result"], output["report"], output["result_receipt"])


def test_fixed_attack_metadata_sequence_override_uses_database_rule_group(
        metadata_db, fake_repo, monkeypatch):
    def add_tag(rows):
        info = rows[0]["Default"]["GPPBBDEICKP"]["Icespine_Strike_Delay"][
            "HHDOFNHCIAE"][0]["IDCIIIFMDCM"]["LGHDGGJIJOK"]
        info["FHNDLIJJDEA"] = "Fixture_Icespine_Counter"

    change(metadata_db, fake_repo, SOURCE, add_tag)
    profile = _single_profile(metadata_db)
    _install(monkeypatch, profile)
    plan = _single_plan(metadata_db, profile)
    plan["action_dispatch_sources"][0]["scenario_calibrations"] = [{
        "component_id": "primary", "application_adapter": _adapter(
            profile, "primary", mode="sequence", counter_tag="Agent_Special_Skill")}]

    output = run_plan_calc(plan, metadata_db)

    assert output["report"]["state"] == "ready", output["report"]["missing_inputs"]
    record = output["report"]["attack_metadata_compilations"][0]
    assert record["compiled_fields"]["counter_tag"] == "Agent_Special_Skill"
    assert record["compiled_fields"]["rule_group"] == "default"
    assert record["application_identity"]["status"] == "adapter_override"
    assert record["application_identity"]["database_tag"] == "Fixture_Icespine_Counter"
    assert record["database_application_identity"] == {
        "status": "ready", "counter_tag": "Fixture_Icespine_Counter"}
    assert record["application_identity_divergence"] is True
    assert record["semantic_dependency"]["interpretation"][
        "application_adapter"]["adapter_fingerprint"] == record[
            "application_adapter"]["adapter_fingerprint"]
    assert len(output["result"]["hits"]) == 3


def test_fixed_attack_metadata_accepts_reviewed_timer_counter_overlay(
        metadata_db, monkeypatch):
    profile = _single_profile(metadata_db)
    _install(monkeypatch, profile)
    baseline = run_plan_calc(_single_plan(metadata_db, profile), metadata_db)
    plan = _single_plan(metadata_db, profile)
    plan["action_dispatch_sources"][0]["scenario_calibrations"] = [{
        "component_id": "primary", "application_adapter": _adapter(
            profile, "primary", icd_hit_interval=2,
            accepted_base_rule_fingerprint=_base_icd_fingerprint(baseline, "primary"))}]

    output = run_plan_calc(plan, metadata_db)

    assert output["report"]["state"] == "ready", output["report"]["missing_inputs"]
    assert len(output["result"]["hits"]) == 3
    record = output["report"]["attack_metadata_compilations"][0]
    audit = record["application_rule_audit"]
    effective = audit["effective_icd_rule"]
    assert record["compiled_fields"]["database_rule_group"] == audit[
        "base_icd_rule"]["rule_group"]
    assert record["compiled_fields"]["rule_group"] == effective["rule_group"]
    assert effective["kind"] == "timer_counter" and effective["hit_interval"] == 2
    elemental = next(row for row in output["report"]["elemental_compilations"]
                     if row["profile_id"] == record["application_profile_id"])
    assert elemental["compiled_icd"] == {
        "kind": "timer_counter", "counter_tag": record["compiled_fields"]["counter_tag"],
        "rule_group": effective["rule_group"],
        "reset_seconds": effective["reset_seconds"], "hit_interval": 2}
    assert build_formula_workbook(output).startswith(b"PK")

    tampered = copy.deepcopy(output)
    tampered["report"]["attack_metadata_compilations"][0]["application_rule_audit"][
        "effective_icd_rule"]["hit_interval"] = 3
    with pytest.raises(FormulaWorkbookError):
        build_formula_workbook(tampered)


def test_fixed_attack_metadata_no_application_preserves_hits_and_no_global_state(
        metadata_db, monkeypatch):
    profile = _single_profile(metadata_db)
    original = copy.deepcopy(profile)
    _install(monkeypatch, profile)
    plan = _single_plan(metadata_db, profile)
    plan["action_dispatch_sources"][0]["scenario_calibrations"] = [{
        "component_id": "primary", "application_adapter": _adapter(
            profile, "primary", mode="no_application")}]

    adapted = run_plan_calc(plan, metadata_db)

    assert adapted["report"]["state"] == "ready", adapted["report"]["missing_inputs"]
    assert len(adapted["result"]["hits"]) == 3
    trace = [row for row in adapted["result"]["elemental"]["trace"]
             if row["type"] == "elemental_application"]
    assert trace and all(not row["allowed"] and row["reason"] == "no_application"
                         for row in trace)
    assert adapted["report"]["attack_metadata_compilations"][0][
        "compiled_fields"]["application_mode"] == "no_application"
    assert profile == original

    plain = run_plan_calc(_single_plan(metadata_db, profile), metadata_db)
    assert plain["report"]["state"] == "ready", plain["report"]["missing_inputs"]
    assert "application_adapter" not in plain["report"]["attack_metadata_compilations"][0]
    plain_trace = [row for row in plain["result"]["elemental"]["trace"]
                   if row["type"] == "elemental_application"]
    assert any(row["allowed"] for row in plain_trace)
