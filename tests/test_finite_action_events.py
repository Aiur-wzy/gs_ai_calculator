from __future__ import annotations

import copy

import pytest

from genshin_app.equipment.action_dispatch_materialization import (
    compile_action_dispatch_inputs,
)
from genshin_app.equipment.action_event_schema import _check
from genshin_app.equipment.action_events import compile_action_events
from genshin_app.equipment.finite_action_schema import PROFILE as FINITE_PROFILE
from genshin_app.equipment.schema import PlanError
from genshin_db.runtime_action_dispatch import resolve_action_dispatch_profile
from genshin_db.runtime_catalog import fingerprint
from tests.test_runtime_action_dispatch import (
    H2,
    _bundle,
    _emission,
    _profile,
    _snapshot,
    _source_receipts,
)


def _field_evidence():
    return {"status": "confirmed", "category": "calibration_required", "source_refs": [{
        "kind": "calibration_record", "reference": "fixture://finite/topology",
        "fingerprint": H2,
    }]}


def _finite_manifest(profile, *, interruption="none"):
    components = [{"component_id": row["component_id"],
        "critical_domains": ["damage", "application"],
        "evidence": _field_evidence()} for row in profile["emissions"]]
    steps = [
        {"step_id": "loop.1", "ordinal": 1, "component_id": "loop",
         "offset_seconds": .1, "order": 0},
        {"step_id": "loop.2", "ordinal": 2, "component_id": "loop",
         "offset_seconds": .3, "order": 1},
        {"step_id": "loop.3", "ordinal": 3, "component_id": "loop",
         "offset_seconds": .3, "order": 2},
        {"step_id": "finale", "ordinal": 4, "component_id": "finale",
         "offset_seconds": .8, "order": 0},
    ]
    return {"manifest_id": "fixture.finite.sequence", "status": "confirmed",
        "selector": copy.deepcopy(profile["selector"]), "components": components,
        "topology": {"kind": "finite_sequence", "steps": steps,
            "terminal_step_id": "finale", "interruption_policy": interruption,
            "evidence": _field_evidence()},
        "dataset": copy.deepcopy(profile["dataset"]),
        "review": {"status": "reviewed", "date": "2026-10-21", "reviewer": "test",
                   "notes": "Synthetic character-agnostic finite topology."}}


def _coverage_bundle(manifest):
    return {"schema_version": 1, "contract_id": "gameplay-action-manifest-v1",
            "manifests": [manifest]}


def _finite_resolution(*, interruption="none"):
    loop = _emission("loop", source_kind="periodic_attack")
    finale = _emission("finale", source_kind="periodic_attack")
    loop["schedule"]["offset_seconds"] = 0
    finale["schedule"]["offset_seconds"] = 0
    profile = _profile(loop, finale)
    manifest = _finite_manifest(profile, interruption=interruption)
    resolution = resolve_action_dispatch_profile(_bundle(profile), selector=profile["selector"],
        snapshot=_snapshot(), source_receipts=_source_receipts(profile),
        coverage_bundle=_coverage_bundle(manifest))
    return profile, manifest, resolution


def _compile(resolution):
    selector = resolution["profile"]["selector"]
    return compile_action_dispatch_inputs(resolution, ability_id="ability.fixture",
        actor="fixture.actor", actor_character_id=selector["character_id"],
        ability_action_type_id="action.elemental_skill", ability_variant="default")


def test_dispatch_projects_three_reused_steps_and_terminal_without_duplicate_sources():
    _, manifest, resolution = _finite_resolution()
    compiled = _compile(resolution)
    assert compiled["status"] == "ready", compiled["issues"]
    assert compiled["target_contract"] == "calibrated_finite_application_v1"
    native = compiled["fragments"]
    finite = native["action_event_profiles"][0]
    assert not _check(finite, FINITE_PROFILE, "finite")
    assert [row["component_id"] for row in finite["steps"]] == [
        "loop", "loop", "loop", "finale"]
    assert [(row["offset_seconds"], row["order"]) for row in finite["steps"]][1:3] == [
        (.3, 1), (.3, 2)]
    assert finite["terminal_step_id"] == "finale"
    assert len(native["prototype_hits"]) == 2
    assert len(native["periodic_attack_sources"][0]["channels"]) == 2
    assert compiled["coverage_manifest_fingerprint"] == fingerprint(manifest)


def test_component_declaration_order_does_not_change_topology_acceptance():
    profile, manifest, _ = _finite_resolution()
    manifest["components"].reverse()
    resolution = resolve_action_dispatch_profile(
        _bundle(profile), selector=profile["selector"], snapshot=_snapshot(),
        source_receipts=_source_receipts(profile),
        coverage_bundle=_coverage_bundle(manifest))
    compiled = _compile(resolution)
    assert resolution["status"] == "ready", resolution["issues"]
    assert compiled["status"] == "ready", compiled["issues"]
    assert [row["component_id"] for row in compiled["fragments"][
        "action_event_profiles"][0]["steps"]] == ["loop", "loop", "loop", "finale"]


def test_missing_coverage_catalog_is_unknown_and_does_not_block_legacy_dispatch():
    profile = _profile(_emission("loop", source_kind="periodic_attack"))
    resolution = resolve_action_dispatch_profile(
        _bundle(profile), selector=profile["selector"], snapshot=_snapshot(),
        source_receipts=_source_receipts(profile),
        coverage_bundle={"schema_version": 1,
            "contract_id": "gameplay-action-manifest-v1", "manifests": []})
    assert resolution["status"] == "ready", resolution["issues"]
    assert resolution["coverage_status"] == "unknown"
    assert resolution["coverage_manifest"] is None


def test_one_step_metadata_finite_topology_maps_to_single_with_occurrence_proof():
    profile = _profile(_emission("primary", source_kind="attack_metadata"))
    profile["emissions"][0]["schedule"]["offset_seconds"] = 0
    manifest = {"manifest_id": "fixture.finite.single", "status": "confirmed",
        "selector": copy.deepcopy(profile["selector"]),
        "components": [{"component_id": "primary",
            "critical_domains": ["damage", "application"],
            "evidence": _field_evidence()}],
        "topology": {"kind": "finite_sequence", "steps": [{
            "step_id": "primary.1", "ordinal": 1, "component_id": "primary",
            "offset_seconds": .125, "order": 17}],
            "terminal_step_id": "primary.1", "interruption_policy": "none",
            "evidence": _field_evidence()},
        "dataset": copy.deepcopy(profile["dataset"]),
        "review": {"status": "reviewed", "date": "2026-10-21",
            "reviewer": "test", "notes": "Synthetic one-step finite topology."}}
    resolution = resolve_action_dispatch_profile(
        _bundle(profile), selector=profile["selector"], snapshot=_snapshot(),
        source_receipts=_source_receipts(profile),
        coverage_bundle=_coverage_bundle(manifest))
    compiled = _compile(resolution)
    assert compiled["status"] == "ready", compiled["issues"]
    assert compiled["target_contract"] == "calibrated_single_application_v2"
    native = compiled["fragments"]["action_event_profiles"][0]
    assert native["calibration"]["offset_seconds"] == .125
    assert native["topology_occurrence"] == {
        "contract_id": "finite-sequence-step-v1", "occurrence_id": "primary.1",
        "component_id": "primary", "ordinal": 1, "topology_order": 17,
        "terminal": True}


def test_unsupported_finite_interruption_is_fail_closed_without_fragments():
    _, _, resolution = _finite_resolution(interruption="cancel_with_source_action")
    compiled = _compile(resolution)
    assert compiled["status"] == "pending"
    assert compiled["fragments"] is None
    assert "action_dispatch_finite_interruption_pending" in {
        row["code"] for row in compiled["issues"]}


def test_finite_component_with_nonzero_dispatch_offset_is_rejected():
    loop = _emission("loop", source_kind="periodic_attack")
    finale = _emission("finale", source_kind="periodic_attack")
    loop["schedule"]["offset_seconds"] = 0
    finale["schedule"]["offset_seconds"] = .25
    profile = _profile(loop, finale)
    manifest = _finite_manifest(profile)
    resolution = resolve_action_dispatch_profile(
        _bundle(profile), selector=profile["selector"], snapshot=_snapshot(),
        source_receipts=_source_receipts(profile),
        coverage_bundle=_coverage_bundle(manifest))
    assert resolution["status"] == "pending"
    assert resolution["coverage_status"] == "incomplete"
    assert "action_dispatch_finite_component_timing_conflict" in {
        row["code"] for row in resolution["coverage_issues"]}


def _native_plan(compiled):
    fragments = compiled["fragments"]
    profile = fragments["action_event_profiles"][0]
    raw_hits = copy.deepcopy(fragments["prototype_hits"])
    resolved_hits = []
    parameters = []
    elemental_profiles = []
    source_channels = []
    for index, hit in enumerate(raw_hits, 1):
        resolved = copy.deepcopy(hit)
        resolved.update(element="cryo", multiplier=.5 + index / 10)
        resolved_hits.append(resolved)
        parameters.append({"hit_id": hit["id"], "status": "ready"})
        channel = next(row for row in profile["channels"]
                       if row["prototype_hit_id"] == hit["id"])
        elemental_profiles.append({"id": channel["application_profile_id"]})
        source_channels.append({"channel_id": channel["channel_id"]})
    plan = {"plan_version": 3,
        "action_event_profiles": copy.deepcopy(fragments["action_event_profiles"]),
        "action_event_bindings": copy.deepcopy(fragments["action_event_bindings"]),
        "action_event_overrides": [],
        "manual_template": {"characters": [{"id": "fixture.actor"}], "hits": raw_hits},
        "mechanics_model": {"hit_events": [], "hit_patterns": [], "condition_events": [],
            "application_events": [], "elemental_model": {"target": {"id": "target"},
                "profiles": elemental_profiles}},
        "rotation_model": {"period": 3.0,
            "abilities": [{"id": "ability.fixture", "character": "fixture.actor",
                "action_type_id": "action.elemental_skill", "variant": "default"}],
            "actions": [{"id": "cast", "ability_id": "ability.fixture", "start": .5,
                "occupation_end": .7, "order": 0, "repeat": {"mode": "once"}}],
            "events": [], "action_hit_links": []}}
    source = {"action_event_profile_id": profile["id"], "status": "ready",
        "actor": "fixture.actor", "query": {"selection": {"matching_slots": ["skill"]}},
        "semantic_dependency": {}, "periodic_dependency_fingerprint": fingerprint({}),
        "channels": source_channels}
    return plan, {"hits": resolved_hits}, parameters, source


def test_native_finite_compiler_preserves_same_time_order_and_component_reuse(monkeypatch):
    _, _, resolution = _finite_resolution()
    compiled = _compile(resolution)
    plan, template, parameters, source = _native_plan(compiled)

    def scope(profile, source_row, *_args):
        assert source_row is source
        return {"compiler": "calibrated-finite-actions-v1", "kind": profile["kind"],
                "variant": profile["variant"], "channels": [], "dependencies": {}}

    monkeypatch.setattr("genshin_app.equipment.finite_action_events.periodic_profile_scope", scope)
    missing = []
    kwargs = {"parameter_compilations": parameters, "elemental_compilations": [],
        "reaction_compilations": [], "periodic_compilations": [source],
        "missing": lambda *args: missing.append(args)}
    model, first, _ = compile_action_events(plan, template, **kwargs)
    assert model is None
    accepted = first[0]["required_confirmation"]["accepted_profile_fingerprint"]
    plan["action_event_profiles"][0]["accepted_profile_fingerprint"] = accepted
    missing.clear()
    model, records, _ = compile_action_events(plan, template, **kwargs)
    assert not missing
    assert model is not None and records[0]["status"] == "ready"
    generated = records[0]["generated"]
    assert [row["component_id"] for row in generated] == ["loop", "loop", "loop", "finale"]
    assert [(row["t"], row["order"]) for row in generated][1:3] == [(.8, 0), (.8, 1)]
    assert [row["topology_order"] for row in generated][1:3] == [1, 2]
    assert [row["step_id"] for row in generated] == ["loop.1", "loop.2", "loop.3", "finale"]
    assert generated[-1]["terminal"] is True
    assert len(model["hit_patterns"]) == 4


def _accept_and_compile(plan, template, parameters, source, monkeypatch):
    monkeypatch.setattr("genshin_app.equipment.finite_action_events.periodic_profile_scope",
        lambda profile, source_row, *_args: {
            "compiler": "calibrated-finite-actions-v1", "kind": profile["kind"],
            "variant": profile["variant"], "channels": [], "dependencies": {}})
    missing = []
    kwargs = {"parameter_compilations": parameters, "elemental_compilations": [],
        "reaction_compilations": [], "periodic_compilations": [source],
        "missing": lambda *args: missing.append(args)}
    _, preview, _ = compile_action_events(plan, template, **kwargs)
    plan["action_event_profiles"][0]["accepted_profile_fingerprint"] = preview[0][
        "required_confirmation"]["accepted_profile_fingerprint"]
    missing.clear()
    compiled = compile_action_events(plan, template, **kwargs)
    return compiled, missing, kwargs


def test_zero_offset_local_order_is_mapped_after_action_and_energy_spend(monkeypatch):
    _, _, resolution = _finite_resolution()
    plan, template, parameters, source = _native_plan(_compile(resolution))
    steps = plan["action_event_profiles"][0]["steps"]
    steps[0].update(offset_seconds=0, order=-10)
    steps[1].update(offset_seconds=0, order=25)
    plan["rotation_model"]["events"] = [{"id": "spend", "type": "energy_spend",
        "action_id": "cast", "character": "fixture.actor", "t": .5, "order": 3}]
    (model, records, _), missing, _ = _accept_and_compile(
        plan, template, parameters, source, monkeypatch)
    assert model is not None and not missing
    generated = records[0]["generated"]
    zero = [row for row in generated if row["offset_ticks"] == 0]
    assert [row["topology_order"] for row in zero] == [-10, 25]
    assert [row["order"] for row in zero] == [4, 5]


def test_zero_offset_group_overflow_is_atomically_pending(monkeypatch):
    _, _, resolution = _finite_resolution()
    plan, template, parameters, source = _native_plan(_compile(resolution))
    steps = plan["action_event_profiles"][0]["steps"]
    steps[0].update(offset_seconds=0, order=-10)
    steps[1].update(offset_seconds=0, order=25)
    plan["rotation_model"]["actions"][0]["order"] = 10**9

    (model, records, sources), missing, _ = _accept_and_compile(
        plan, template, parameters, source, monkeypatch)

    assert model is None and sources == [] and missing
    assert records[0]["status"] == "pending" and records[0]["generated"] == []
    assert any("order" in row["message"] and "\u8fb9\u754c" in row["message"]
               for row in records[0]["issues"])


def test_zero_offset_group_maps_safely_from_lower_order_boundary(monkeypatch):
    _, _, resolution = _finite_resolution()
    plan, template, parameters, source = _native_plan(_compile(resolution))
    steps = plan["action_event_profiles"][0]["steps"]
    steps[0].update(offset_seconds=0, order=-10)
    steps[1].update(offset_seconds=0, order=25)
    plan["rotation_model"]["actions"][0]["order"] = -(10**9)

    (model, records, _), missing, _ = _accept_and_compile(
        plan, template, parameters, source, monkeypatch)

    assert model is not None and not missing
    zero = [row for row in records[0]["generated"] if row["offset_ticks"] == 0]
    assert [row["order"] for row in zero] == [-(10**9) + 1, -(10**9) + 2]


def test_external_same_tick_event_makes_finite_sequence_atomically_pending(monkeypatch):
    _, _, resolution = _finite_resolution()
    plan, template, parameters, source = _native_plan(_compile(resolution))
    (model, records, _), missing, kwargs = _accept_and_compile(
        plan, template, parameters, source, monkeypatch)
    assert model is not None and not missing and records[0]["status"] == "ready"
    plan["rotation_model"]["events"] = [{"id": "external", "type": "energy_direct",
        "character": "fixture.actor", "t": .8, "order": 9}]
    missing.clear()
    model, records, sources = compile_action_events(plan, template, **kwargs)
    assert model is None and sources == []
    assert records[0]["status"] == "pending" and records[0]["generated"] == []
    assert any("外部" in issue["message"] or "同微秒" in issue["message"]
               for issue in records[0]["issues"])


def test_reused_profile_across_actions_keeps_local_order_and_unique_hits(monkeypatch):
    _, _, resolution = _finite_resolution()
    plan, template, parameters, source = _native_plan(_compile(resolution))
    plan["rotation_model"]["period"] = 4.0
    plan["rotation_model"]["actions"].append({"id": "cast.2",
        "ability_id": "ability.fixture", "start": 1.5, "occupation_end": 1.7,
        "order": 0, "repeat": {"mode": "once"}})
    (model, records, _), missing, _ = _accept_and_compile(
        plan, template, parameters, source, monkeypatch)
    assert model is not None and not missing
    generated = records[0]["generated"]
    assert len(generated) == 8 and len({row["hit_id"] for row in generated}) == 8
    for action_id in ("cast", "cast.2"):
        same_tick = [row for row in generated
                     if row["action_id"] == action_id and row["offset_ticks"] == 300000]
        assert [row["topology_order"] for row in same_tick] == [1, 2]
        assert [row["order"] for row in same_tick] == [0, 1]
        assert [row["occurrence_id"] for row in same_tick] == ["loop.2", "loop.3"]


def test_one_unsupported_finite_peer_rolls_back_all_generated_patterns(monkeypatch):
    _, _, resolution = _finite_resolution()
    compiled = _compile(resolution)
    plan, template, parameters, source = _native_plan(compiled)
    monkeypatch.setattr("genshin_app.equipment.finite_action_events.periodic_profile_scope",
        lambda profile, source_row, *_args: {
            "compiler": "calibrated-finite-actions-v1", "kind": profile["kind"],
            "variant": profile["variant"], "channels": [], "dependencies": {}})
    missing = []
    kwargs = {"parameter_compilations": parameters, "elemental_compilations": [],
        "reaction_compilations": [], "periodic_compilations": [source],
        "missing": lambda *args: missing.append(args)}
    _, first, _ = compile_action_events(plan, template, **kwargs)
    plan["action_event_profiles"][0]["accepted_profile_fingerprint"] = first[0][
        "required_confirmation"]["accepted_profile_fingerprint"]

    peer = copy.deepcopy(plan["action_event_profiles"][0])
    peer["id"] += ".peer"
    peer["interruption_policy"] = "scenario_required"
    for channel in peer["channels"]:
        old = channel["channel_id"]
        channel["channel_id"] += ".peer"
        channel["prototype_hit_id"] += ".peer"
        channel["application_profile_id"] += ".peer"
        for step in peer["steps"]:
            if step["component_id"] == old:
                step["component_id"] = channel["channel_id"]
    plan["action_event_profiles"].append(peer)
    plan["action_event_bindings"].append({"ability_id": "ability.peer", "profile_id": peer["id"]})
    plan["rotation_model"]["abilities"].append({"id": "ability.peer",
        "character": "fixture.actor", "action_type_id": "action.elemental_skill",
        "variant": "default"})
    plan["rotation_model"]["actions"].append({"id": "cast.peer", "ability_id": "ability.peer",
        "start": 1.5, "occupation_end": 1.7, "order": 0, "repeat": {"mode": "once"}})
    peer_source = copy.deepcopy(source)
    peer_source["action_event_profile_id"] = peer["id"]
    for channel, peer_channel in zip(source["channels"], peer["channels"], strict=True):
        peer_source["channels"][source["channels"].index(channel)]["channel_id"] = peer_channel[
            "channel_id"]
    for raw, resolved, parameter in zip(copy.deepcopy(plan["manual_template"]["hits"]),
                                        copy.deepcopy(template["hits"]),
                                        copy.deepcopy(parameters), strict=True):
        raw["id"] += ".peer"
        resolved["id"] += ".peer"
        parameter["hit_id"] += ".peer"
        plan["manual_template"]["hits"].append(raw)
        template["hits"].append(resolved)
        parameters.append(parameter)
    kwargs["periodic_compilations"].append(peer_source)
    missing.clear()
    model, records, sources = compile_action_events(plan, template, **kwargs)
    assert model is None and sources == []
    assert {row["status"] for row in records} == {"pending"}
    assert all(row["generated"] == [] for row in records)
    assert any(issue["code"] == "periodic_action_group_pending"
               for row in records for issue in row["issues"])


def test_native_schema_rejects_nonterminal_terminal_and_duplicate_position():
    _, _, resolution = _finite_resolution()
    profile = _compile(resolution)["fragments"]["action_event_profiles"][0]
    plan = {"plan_version": 3, "action_event_profiles": [profile],
            "action_event_bindings": [], "action_event_overrides": []}
    profile["terminal_step_id"] = "loop.2"
    with pytest.raises(PlanError, match="最后一个"):
        from genshin_app.equipment.action_event_schema import (
            validate_action_event_fields,
        )
        validate_action_event_fields(plan)

    profile["terminal_step_id"] = "finale"
    profile["steps"][2].update(offset_seconds=.3, order=1)
    with pytest.raises(PlanError, match="不同order"):
        validate_action_event_fields(plan)


def test_periodic_manifest_pause_or_off_field_stop_is_not_executable():
    emission = _emission("storm", kind="periodic", source_kind="periodic_attack")
    profile = _profile(emission)
    manifest = _finite_manifest(_profile(
        _emission("loop", source_kind="periodic_attack"),
        _emission("finale", source_kind="periodic_attack")))
    manifest["selector"] = copy.deepcopy(profile["selector"])
    manifest["components"] = [{"component_id": "storm",
        "critical_domains": ["damage", "application"], "evidence": _field_evidence()}]
    manifest["topology"] = {"kind": "periodic_channels", "channels": [{
        "channel_id": "storm", "component_id": "storm", "schedule": {
            "kind": "periodic", "first_offset_seconds": .5,
            "interval_seconds": 1.0, "count": 3}}],
        "lifecycle": {"establishment_offset_seconds": .1,
            "instance_end_offset_seconds": 4.0, "refresh_policy": "none",
            "pause_resume_policy": "pause_preserve_phase", "off_field_policy": "stop",
            "evidence": _field_evidence()}, "evidence": _field_evidence()}
    manifest["dataset"] = copy.deepcopy(profile["dataset"])
    resolved = resolve_action_dispatch_profile(_bundle(profile), selector=profile["selector"],
        snapshot=_snapshot(), source_receipts=_source_receipts(profile),
        coverage_bundle=_coverage_bundle(manifest))
    assert resolved["status"] == "pending"
    assert resolved["coverage_status"] == "incomplete"
    assert "action_dispatch_coverage_lifecycle_unsupported" in {
        row["code"] for row in resolved["coverage_issues"]}


def test_finite_occurrence_budget_is_rejected_before_event_materialization(monkeypatch):
    _, _, resolution = _finite_resolution()
    plan, template, parameters, source = _native_plan(_compile(resolution))
    plan["rotation_model"]["period"] = 20_000.0
    prototype = plan["rotation_model"]["actions"][0]
    plan["rotation_model"]["actions"] = [
        {**copy.deepcopy(prototype), "id": f"cast.{index}",
         "start": float(index), "occupation_end": float(index) + .1}
        for index in range(10_001)
    ]
    original = __import__(
        "genshin_app.equipment.finite_action_events", fromlist=["_assign_global_orders"]
    )._assign_global_orders
    called = False

    def observe(*args, **kwargs):
        nonlocal called
        called = True
        return original(*args, **kwargs)

    monkeypatch.setattr(
        "genshin_app.equipment.finite_action_events._assign_global_orders", observe)
    (model, records, sources), missing, _ = _accept_and_compile(
        plan, template, parameters, source, monkeypatch)
    assert model is None and sources == [] and not called
    assert records[0]["status"] == "pending" and records[0]["generated"] == []
    assert any("40000" in issue["message"] for issue in records[0]["issues"])
    assert any("40000" in item[2] for item in missing)
