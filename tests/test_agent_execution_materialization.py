from __future__ import annotations

import copy
import io
import json
import zipfile

import pytest

import genshin_app.equipment.agent_execution_materialization as materialization_module
import genshin_app.equipment.agent_execution_runtime as runtime_module
from genshin_app.agent_chat.calculation_coverage import qualify_calculation_coverage
from genshin_app.equipment.agent_execution_materialization import (
    AgentExecutionMaterializationError,
    agent_execution_materialization_capabilities,
    lint_agent_execution_materialization,
    materialize_agent_execution,
    new_agent_execution_materialization,
    validate_agent_execution_materialization,
)
from genshin_app.equipment.agent_execution_runtime import (
    adapt_agent_execution_runtime,
)
from genshin_app.equipment.composer import compose_equipment_plan
from genshin_app.equipment.event_topology import AUTHORITY, CONTRACT_ID
from genshin_app.equipment.event_topology_binding import (
    lint_event_topology_bindings,
    new_event_topology_bindings,
)
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.equipment.schema import PlanError, new_plan, validate_plan
from genshin_app.equipment.service import result_bytes, run_plan_calc
from genshin_app.export import export_result
from genshin_app.formula_workbook import (
    FormulaWorkbookError,
    build_formula_workbook,
)
from genshin_engine import validate_template
from genshin_engine.mechanics import execute_mechanics, validate_mechanics_model


def _resign_visible_agent_execution_fingerprints(calculated):
    """Recompute every public SHA to prove version checks are semantic."""
    result = calculated["result"]
    receipt = calculated["result_receipt"]
    materialization = result["agent_execution_materialization_receipt"]
    materialization["receipt_fingerprint"] = fingerprint({
        key: value for key, value in materialization.items()
        if key != "receipt_fingerprint"
    })
    effective = result["agent_execution_effective_input"]
    effective["materialization_receipt_fingerprint"] = materialization[
        "receipt_fingerprint"]
    effective["bundle_fingerprint"] = fingerprint({
        key: value for key, value in effective.items()
        if key != "bundle_fingerprint"
    })
    runtime = result["agent_execution_runtime_receipt"]
    runtime["effective_input_fingerprint"] = effective["bundle_fingerprint"]
    for row in runtime["entries"]:
        row["adapter_fingerprint"] = fingerprint({
            key: value for key, value in row.items()
            if key != "adapter_fingerprint"
        })
    runtime["receipt_fingerprint"] = fingerprint({
        key: value for key, value in runtime.items()
        if key != "receipt_fingerprint"
    })
    proof = receipt["agent_execution_proof"]
    proof["materialization_receipt_fingerprint"] = fingerprint(materialization)
    proof["effective_input_fingerprint"] = fingerprint(effective)
    proof["runtime_receipt_fingerprint"] = fingerprint(runtime)
    runtime_by_id = {row["entry_id"]: row for row in runtime["entries"]}
    for row in proof["entries"]:
        installed = runtime_by_id[row["entry_id"]]
        row["adapter_id"] = installed["adapter_id"]
        row["adapter_version"] = installed["adapter_version"]
    proof["proof_fingerprint"] = fingerprint({
        key: value for key, value in proof.items()
        if key != "proof_fingerprint"
    })
    receipt["result_fingerprint"] = fingerprint(result)


def _point(ref_id: str, offset: float = 0.0, order: int = 0):
    return {"kind": "point", "anchor": {"kind": "node", "ref_id": ref_id,
            "phase": "point"}, "offset_seconds": offset, "order": order}


def _node(node_id: str, kind: str, schedule: dict):
    return {"node_id": node_id, "kind": kind, "status": "bound",
            "schedule": schedule, "source_ids": ["source.skill"],
            "parameter_slots": [], "note": ""}


def _periodic_payload(*, channel_id="channel.fixture", first=0.2, interval=1.0,
                      count=2, duration=1.2, emits=("node.damage",),
                      owner_node_id="node.periodic", refresh="reset_duration"):
    return {
        "profile_id": "profile." + channel_id,
        "channel_id": channel_id,
        "schedule": {"kind": "equal_interval", "schedule_id": "schedule." + channel_id,
            "first_offset_seconds": first, "interval_seconds": interval,
            "count": count, "derive_count": None, "order": 1},
        "lifecycle": {"duration_seconds": duration, "refresh_policy": refresh,
            "off_field_policy": "continue", "pause_resume_policy": "none"},
        "establishments": [{"establishment_id": "establishment." + channel_id,
            "anchor_node_id": owner_node_id, "order": 0}],
        "controls": [], "on_field_intervals": [],
        "dispatches": [{"dispatch_id": "dispatch." + channel_id,
            "emits_node_ids": list(emits)}],
    }


def _topology():
    node_ids = [
        "node.action", "node.sequence", "node.periodic", "node.application",
        "node.buff", "node.resource", "node.state", "node.damage",
    ]
    nodes = [
        {"node_id": "node.action", "kind": "action_anchor", "status": "bound",
         "schedule": {"kind": "point", "anchor": {"kind": "action",
             "ref_id": "action.skill", "phase": "start"},
             "offset_seconds": 0.0, "order": 0},
         "source_ids": ["source.skill"], "parameter_slots": [], "note": ""},
        _node("node.sequence", "finite_sequence", _point("node.action", 0.1)),
        _node("node.periodic", "periodic_channel", {
            "kind": "periodic", "anchor": {"kind": "node", "ref_id": "node.action",
                "phase": "point"}, "first_offset_seconds": 0.2,
            "interval_seconds": 1.0, "count": 2, "duration_seconds": 1.2,
            "refresh_policy": "restart", "off_field_policy": "continue", "order": 1}),
        _node("node.application", "application", _point("node.sequence", 0.2)),
        _node("node.buff", "buff_interval", _point("node.action", 0.0, 2)),
        _node("node.resource", "resource", _point("node.action", 0.3, 3)),
        _node("node.state", "state", _point("node.action", 0.0, 4)),
        _node("node.damage", "damage", _point("node.sequence", 0.2, 5)),
    ]
    return {
        "contract_id": CONTRACT_ID,
        "schema_version": 1,
        "topology_id": "topology.materialization",
        "authority": AUTHORITY,
        "declaration": {"character_semantics": "agent_declared",
                        "completeness": "declared_complete", "note": "fixture"},
        "action_checkpoint": {"revision": 1, "fingerprint": "a" * 64,
                              "action_ids": ["action.skill"]},
        "dependencies": [{"dependency_id": "dependency.database",
            "kind": "database_snapshot", "fingerprint": "b" * 64, "required": True}],
        "sources": [{"source_id": "source.skill", "category": "talent",
            "owner_ref": "character.fixture", "description": "fixture source",
            "disposition": "modeled", "node_ids": node_ids, "reason": None,
            "source_refs": [{"kind": "agent_research", "reference": "fixture://skill",
                "fingerprint": "c" * 64, "status": "verified"}]}],
        "nodes": nodes,
        "edges": [
            {"edge_id": "edge.sequence-application", "kind": "contains",
             "from_node_id": "node.sequence", "to_node_id": "node.application",
             "status": "bound", "source_ids": ["source.skill"], "note": ""},
            {"edge_id": "edge.sequence-damage", "kind": "causes",
             "from_node_id": "node.sequence", "to_node_id": "node.damage",
             "status": "bound", "source_ids": ["source.skill"], "note": ""},
            {"edge_id": "edge.periodic-damage", "kind": "triggers",
             "from_node_id": "node.periodic", "to_node_id": "node.damage",
             "status": "bound", "source_ids": ["source.skill"], "note": ""},
        ],
    }


def _plan():
    return {"kind": "genshin-plan", "plan_version": 3, "plan_id": "plan.fixture",
            "revision": 4, "manual_template": {"meta": {"name": "fixture"}},
            "equipment_inputs": {}, "effect_bindings": [], "rotation_model": {},
            "mechanics_model": {}, "extensions": {}, "resolution": {"state": "pending"},
            "resolved_template": None}


def _document(plan=None, topology=None):
    plan, topology = plan or _plan(), topology or _topology()
    document = new_agent_execution_materialization(plan, topology, "materialization.fixture")
    document["sources"] = [{"source_id": "source.skill", "kind": "agent_research",
        "reference": "fixture://skill", "fingerprint": "c" * 64,
        "status": "verified", "note": "fixture"}]

    def entry(entry_id, family, node_id, payload):
        return {"entry_id": entry_id, "family": family,
                "handler_version": 2 if family in {"application", "periodic_channel"} else 1,
                "node_ids": [node_id], "source_ids": ["source.skill"],
                "coverage_keys": ["coverage." + entry_id], "payload": payload, "note": ""}

    document["entries"] = [
        entry("sequence", "finite_sequence", "node.sequence", {
            "sequence_id": "sequence.fixture", "steps": [
                {"step_id": "step.1", "offset_seconds": 0.2, "order": 0,
                 "emits_node_ids": ["node.application", "node.damage"]}],
            "interrupt_policy": "none"}),
        entry("periodic", "periodic_channel", "node.periodic", _periodic_payload()),
        entry("application", "application", "node.application", {
            "element": "cryo", "gauge_units": 1.0,
            "application_mode": "timer_counter", "counter_tag": "ordinary",
            "rule_group": "ordinary.3hit.2_5s", "timer_seconds": 2.5,
            "hit_count": 3, "sequence": None, "target_ref": "enemy",
            "landed": True, "attempted": True}),
        entry("buff", "buff", "node.buff", {
            "effect_id": "effect.fixture", "target_scope": "team",
            "start_offset_seconds": 0.0, "duration_seconds": 10.0,
            "refresh_policy": "restart", "operations": [{"stat": "dmg_bonus.cryo",
                "operation": "add_ratio", "value": 0.1, "target_ref": "team"}]}),
        entry("resource", "resource", "node.resource", {
            "resource_id": "energy", "operation": "add", "amount": 3.0,
            "receiver_ref": "actor.fixture", "delivery": "immediate",
            "travel_seconds": None}),
        entry("state", "state", "node.state", {
            "state_key": "enemy.frozen", "operation": "set", "value_type": "boolean",
            "value": True, "target_ref": "enemy", "duration_seconds": 2.0,
            "refresh_policy": "restart"}),
    ]
    return document


def test_all_first_slice_families_materialize_without_database_or_role_dispatch():
    plan, topology = _plan(), _topology()
    document = _document(plan, topology)
    assert validate_agent_execution_materialization(document) is document
    receipt = lint_agent_execution_materialization(plan, topology, document)
    assert receipt["status"] == "ready"
    assert receipt["database_access"] == "none"
    assert set(receipt["coverage"]) == {
        "finite_sequence", "periodic_channel", "application", "buff", "resource", "state"}
    assert all(receipt["coverage"][family] == {"declared": 1, "covered": 1}
               for family in (
                   "finite_sequence", "periodic_channel", "application", "buff",
                   "resource", "state",
               ))

    bundle = materialize_agent_execution(plan, topology, document)
    assert bundle["authority"] == "agent_materialized_generic_handler"
    assert [row["handler"]["handler_id"] for row in bundle["entries"]] == [
        "generic.finite_sequence", "generic.periodic_channel", "generic.application",
        "generic.buff", "generic.resource", "generic.state"]
    assert "result" not in bundle and "dps" not in bundle
    assert len(bundle["bundle_fingerprint"]) == 64


def test_handler_selection_is_independent_of_character_identity():
    topology = _topology()
    first = _plan()
    first["manual_template"]["characters"] = [{"id": "actor.a"}]
    second = copy.deepcopy(first)
    second["manual_template"]["characters"] = [{"id": "actor.completely.different"}]
    first_bundle = materialize_agent_execution(first, topology, _document(first, topology))
    second_bundle = materialize_agent_execution(second, topology, _document(second, topology))
    assert first_bundle["entries"] == second_bundle["entries"]
    assert first_bundle["handler_set_fingerprint"] == second_bundle["handler_set_fingerprint"]
    assert first_bundle["plan_input_fingerprint"] != second_bundle["plan_input_fingerprint"]


def test_unused_handler_addition_does_not_stale_but_consumed_version_change_does(monkeypatch):
    plan, topology = _plan(), _topology()
    document = _document(plan, topology)
    before = lint_agent_execution_materialization(plan, topology, document)
    assert before["status"] == "ready"

    unused = materialization_module.HandlerSpec(
        "future_generic_family", "other", 1, lambda payload, path: payload)
    monkeypatch.setitem(
        materialization_module.HANDLER_REGISTRY, unused.family, unused)
    after_addition = lint_agent_execution_materialization(plan, topology, document)
    assert after_addition == before

    current = materialization_module.HANDLER_REGISTRY["finite_sequence"]
    monkeypatch.setitem(
        materialization_module.HANDLER_REGISTRY,
        "finite_sequence",
        materialization_module.HandlerSpec(
            current.family, current.node_kind, current.version + 1,
            current.validate_payload, current.additional_node_kinds),
    )
    stale = lint_agent_execution_materialization(plan, topology, document)
    assert stale["status"] == "stale"
    assert stale["issues"][0]["code"] == "agent_materialization_handler_stale"


def test_plan_or_topology_change_makes_materialization_stale():
    plan, topology = _plan(), _topology()
    document = _document(plan, topology)
    changed_plan = copy.deepcopy(plan)
    changed_plan["manual_template"]["meta"]["name"] = "changed"
    assert lint_agent_execution_materialization(changed_plan, topology, document)["status"] == "stale"

    changed_topology = copy.deepcopy(topology)
    changed_topology["declaration"]["note"] = "changed"
    assert lint_agent_execution_materialization(plan, changed_topology, document)["status"] == "stale"


def test_storage_revision_increment_does_not_stale_an_atomic_materialization_patch():
    plan, topology = _plan(), _topology()
    document = _document(plan, topology)
    committed = copy.deepcopy(plan)
    committed["revision"] += 1
    committed.setdefault("extensions", {})["agent_execution_materialization"] = document
    assert lint_agent_execution_materialization(committed, topology, document)["status"] == "ready"


def test_automatic_owned_node_need_not_be_redeclared_but_schedule_mismatch_is_partial():
    plan, topology = _plan(), _topology()
    plan["mechanics_model"] = {
        "schema_version": 1, "rules": [], "hit_events": [], "hit_patterns": [],
        "condition_events": [{"id": "state.auto", "t": 0, "order": 4,
            "condition": "enemy.frozen", "value": True,
            "repeat": {"mode": "each_cycle"},
            "status": "assumed", "source": "automatic baseline fixture"}],
        "initial_state": {"rules": []},
    }
    bindings = new_event_topology_bindings(topology)
    bindings["bindings"] = [{"binding_id": "binding.state.auto",
        "node_id": "node.state", "target_kind": "mechanics",
        "pointer": "/mechanics_model/condition_events/0",
        "target_fingerprint": fingerprint(plan["mechanics_model"]["condition_events"][0]),
        "note": "该状态已经由自动基线拥有"}]
    plan["extensions"]["event_topology"] = topology
    plan["extensions"]["event_topology_bindings"] = bindings
    document = _document(plan, topology)
    document["entries"] = [row for row in document["entries"] if row["family"] != "state"]
    receipt = lint_agent_execution_materialization(plan, topology, document)
    assert receipt["status"] == "ready"
    assert receipt["uncovered_supported_node_ids"] == []

    document = _document(plan, topology)
    document["entries"][1]["payload"]["schedule"]["first_offset_seconds"] = 0.1
    receipt = lint_agent_execution_materialization(plan, topology, document)
    assert receipt["status"] == "partial"
    assert "agent_materialization_schedule_mismatch" in {row["code"] for row in receipt["issues"]}


def test_duplicate_ownership_unknown_handler_and_result_payload_fail_closed():
    plan, topology = _plan(), _topology()
    document = _document(plan, topology)
    document["entries"][1]["coverage_keys"] = document["entries"][0]["coverage_keys"]
    with pytest.raises(AgentExecutionMaterializationError) as error:
        validate_agent_execution_materialization(document)
    assert error.value.code == "agent_materialization_duplicate_identity"

    document = _document(plan, topology)
    document["entries"][0]["family"] = "character_specific_magic"
    with pytest.raises(AgentExecutionMaterializationError) as error:
        validate_agent_execution_materialization(document)
    assert error.value.code == "agent_materialization_handler_unsupported"

    document = _document(plan, topology)
    document["entries"][0]["payload"]["total_damage"] = 999999
    with pytest.raises(AgentExecutionMaterializationError) as error:
        validate_agent_execution_materialization(document)
    assert error.value.code == "agent_materialization_result_payload"


@pytest.mark.parametrize("gauge_units", [0.5, 1.5])
def test_application_gauge_never_silently_truncates_fractional_units(gauge_units):
    plan, topology = _plan(), _topology()
    document = _document(plan, topology)
    application = next(row for row in document["entries"]
                       if row["family"] == "application")
    application["payload"]["gauge_units"] = gauge_units
    with pytest.raises(AgentExecutionMaterializationError) as error:
        validate_agent_execution_materialization(document)
    assert error.value.path.endswith("gauge_units")


def test_capabilities_are_versioned_bounded_and_only_publish_generic_families():
    capabilities = agent_execution_materialization_capabilities()
    assert capabilities["contract_id"] == "agent-execution-materialization-v1"
    assert capabilities["database_access"] == "none"
    assert set(capabilities["families"]) == {
        "finite_sequence", "periodic_channel", "application", "buff", "resource", "state",
        "mechanics_rule_bundle", "resource_operation"}
    assert all(row["handler_id"].startswith("generic.")
               for row in capabilities["families"].values())
    periodic = capabilities["families"]["periodic_channel"]
    assert periodic["handler_version"] == 2
    assert periodic["capability_matrix"]["automatic_consumption"] is True
    assert periodic["capability_matrix"]["manual_feature_toggle"] is False
    assert periodic["capability_matrix"]["schedule"] == {
        "equal_interval": "supported",
        "explicit_offsets": "supported",
        "explicit_count": "supported",
        "inclusive_duration_count_derivation": "supported",
        "while_active_inclusive_floor_derivation": "supported",
    }
    assert periodic["capability_matrix"]["lifecycle"] == {
        "single_establishment": "supported",
        "multiple_establishments": "supported",
        "replace_previous": "supported",
        "reset_duration": "supported",
        "extend_duration": "supported",
        "ignore_new": "supported",
        "early_cancel": "supported",
        "off_field_continue": "supported",
        "off_field_stop": "supported",
        "pause_preserve_phase": "supported",
        "pause_reset_phase": "supported",
        "finite_window_active_or_paused_boundary": "supported_with_finite_proof_only",
    }
    assert periodic["capability_matrix"]["dispatch"]["group_semantics"] == (
        "all_dispatches_each_occurrence")
    assert periodic["capability_matrix"]["limits"] == {
        "occurrences_per_channel_phase": 256,
    }
    assert capabilities["limits"]["periodic_occurrences"] == 256

    minimal = copy.deepcopy(periodic["minimal_payload"])
    assert minimal["lifecycle"]["duration_seconds"] == 1.0
    assert materialization_module.HANDLER_REGISTRY[
        "periodic_channel"].validate_payload(minimal, "payload") is minimal


def test_periodic_occurrence_limit_matches_the_shared_projector():
    handler = materialization_module.HANDLER_REGISTRY["periodic_channel"]
    payload = copy.deepcopy(
        agent_execution_materialization_capabilities()["families"][
            "periodic_channel"]["minimal_payload"])
    payload["schedule"]["count"] = 256
    payload["lifecycle"]["duration_seconds"] = 255.0
    assert handler.validate_payload(payload, "payload") is payload

    payload["schedule"].update(count=None,
                               derive_count="through_instance_end_inclusive")
    payload["lifecycle"]["duration_seconds"] = 256.0
    with pytest.raises(AgentExecutionMaterializationError) as error:
        handler.validate_payload(payload, "payload")
    assert "supported bound" in error.value.message


def test_native_plan_schema_validates_materialization_extension_and_requires_v3_topology():
    plan = enable_mechanics(new_plan())
    topology = _topology()
    plan.setdefault("extensions", {})["event_topology"] = topology
    plan["extensions"]["agent_execution_materialization"] = _document(plan, topology)
    assert validate_plan(plan)["valid"] is True

    legacy = new_plan()
    legacy.setdefault("extensions", {})["event_topology"] = topology
    legacy["extensions"]["agent_execution_materialization"] = _document(legacy, topology)
    with pytest.raises(Exception) as error:
        validate_plan(legacy)
    assert getattr(error.value, "reason", None) == "agent_materialization_plan_version"


def test_composer_preserves_effective_bundle_and_reports_uninstallable_entries(tmp_path):
    plan = enable_mechanics(new_plan())
    topology = _topology()
    plan.setdefault("extensions", {})["event_topology"] = topology
    plan["extensions"]["agent_execution_materialization"] = _document(plan, topology)
    report = compose_equipment_plan(plan, tmp_path / "missing.db")
    resolution = report["plan"]["resolution"]
    assert resolution["agent_execution_materialization_receipt"]["status"] == "ready"
    assert resolution["agent_execution_effective_input"]["authority"] == (
        "agent_materialized_generic_handler")
    runtime_issues = [row for row in report["missing_inputs"]
                      if row["code"] == "agent_materialization_runtime_model_missing"]
    assert len(runtime_issues) == 6
    assert all(row["blocking_scope"] == "agent_materialization"
               for row in runtime_issues)
    assert resolution["agent_execution_runtime_receipt"]["status"] == "partial"
    assert report["state"] == "pending"


def _runtime_base():
    evidence = {"status": "assumed", "source": "materialization runtime fixture"}
    template = {
        "version": 2, "form": "resolved", "id_registry_version": 1,
        "type_definitions": {"damage_types": []},
        "meta": {"assumptions": []},
        "enemy": {"level": 90, "resistance": {"default": 0.1}},
        "characters": [{"id": "actor", "level": 90, "stats": {
            "atk": 100, "base_atk": 50, "em": 0,
            "crit_rate": 0, "crit_dmg": 0.5, "energy_recharge": 1,
        }}],
        "hits": [{"id": "hit.fixture", "damage_type_ids": ["damage.elemental_skill"],
            "t": 0.2, "character": "actor", "skill": "fixture",
            "damage_system": "classic", "element": "cryo", "multiplier": 1,
            "count": 1}],
        "buffs": [], "switches": [], "loop_duration": 10,
    }
    rotation = {
        "schema_version": 1, "period": 10, "max_cycles": 3,
        "initial_frontline": "actor",
        "characters": [{"id": "actor", "element": "cryo", "energy_recharge": 1,
            "energy_capacity": 60, "initial_energy": 0, **evidence}],
        "abilities": [{"id": "ability", "character": "actor",
            "action_type_id": "action.elemental_skill", "cooldown": None,
            "energy": None, **evidence}],
        "actions": [{"id": "action.skill", "ability_id": "ability", "start": 0,
            "occupation_end": 0.1, "order": 0, "repeat": {"mode": "each_cycle"},
            **evidence}],
        "events": [], "initial_cooldowns": [],
    }
    mechanics = {
        "schema_version": 1, "rules": [], "hit_events": [], "hit_patterns": [],
        "condition_events": [], "initial_state": {"rules": []},
        "elemental_model": {
            "schema_version": 1, "ruleset": "gauge-basic-v1",
            "target": {"id": "enemy", "kind": "ordinary", "knowledge": "known",
                "initial_aura": None, **evidence},
            "profiles": [],
        },
    }
    plan = {
        "kind": "genshin-plan", "plan_version": 3, "plan_id": "plan.runtime",
        "revision": 1, "manual_template": copy.deepcopy(template),
        "equipment_inputs": {}, "effect_bindings": [],
        "rotation_model": copy.deepcopy(rotation), "mechanics_model": copy.deepcopy(mechanics),
        "extensions": {}, "resolution": {"state": "pending"}, "resolved_template": None,
    }
    return plan, template, rotation, mechanics


def _runtime_topology(*, periodic=False):
    source_nodes = ["node.action", "node.damage"]
    nodes = [{"node_id": "node.action", "kind": "action_anchor", "status": "bound",
        "schedule": {"kind": "point", "anchor": {"kind": "action",
            "ref_id": "action.skill", "phase": "start"},
            "offset_seconds": 0.0, "order": 0},
        "source_ids": ["source.skill"], "parameter_slots": [], "note": ""}]
    if periodic:
        source_nodes.append("node.periodic")
        nodes.append(_node("node.periodic", "periodic_channel", {
            "kind": "periodic", "anchor": {"kind": "node", "ref_id": "node.action",
                "phase": "point"}, "first_offset_seconds": 0.2,
            "interval_seconds": 0.5, "count": 3, "duration_seconds": 1.2,
            "refresh_policy": "restart", "off_field_policy": "continue", "order": 1}))
        nodes.append(_node("node.damage", "damage", _point("node.periodic", 0.0, 2)))
        edges = [{"edge_id": "edge.periodic-damage", "kind": "causes",
            "from_node_id": "node.periodic", "to_node_id": "node.damage",
            "status": "bound", "source_ids": ["source.skill"], "note": ""}]
    else:
        source_nodes.extend(["node.sequence", "node.application", "node.resource"])
        nodes.extend([
            _node("node.sequence", "finite_sequence", _point("node.action", 0.0, 1)),
            _node("node.damage", "damage", _point("node.sequence", 0.2, 2)),
            _node("node.application", "application", _point("node.sequence", 0.2, 3)),
            _node("node.resource", "resource", _point("node.action", 0.1, 4)),
        ])
        edges = [
            {"edge_id": "edge.sequence-damage", "kind": "causes",
             "from_node_id": "node.sequence", "to_node_id": "node.damage",
             "status": "bound", "source_ids": ["source.skill"], "note": ""},
            {"edge_id": "edge.sequence-application", "kind": "contains",
             "from_node_id": "node.sequence", "to_node_id": "node.application",
             "status": "bound", "source_ids": ["source.skill"], "note": ""},
        ]
    return {
        "contract_id": CONTRACT_ID, "schema_version": 1,
        "topology_id": "topology.runtime.periodic" if periodic else "topology.runtime.finite",
        "authority": AUTHORITY,
        "declaration": {"character_semantics": "agent_declared",
            "completeness": "declared_complete", "note": "fixture"},
        "action_checkpoint": {"revision": 1, "fingerprint": "a" * 64,
            "action_ids": ["action.skill"]},
        "dependencies": [],
        "sources": [{"source_id": "source.skill", "category": "talent",
            "owner_ref": "actor", "description": "fixture", "disposition": "modeled",
            "node_ids": source_nodes, "reason": None,
            "source_refs": [{"kind": "agent_research", "reference": "fixture",
                "fingerprint": "c" * 64, "status": "verified"}]}],
        "nodes": nodes, "edges": edges,
    }


def _runtime_document(plan, topology, *, periodic=False):
    bindings = new_event_topology_bindings(topology)
    bindings["bindings"] = [{"binding_id": "binding.damage", "node_id": "node.damage",
        "target_kind": "hit", "pointer": "/manual_template/hits/0",
        "target_fingerprint": fingerprint(plan["manual_template"]["hits"][0]), "note": ""}]
    plan["extensions"] = {"event_topology": topology,
                          "event_topology_bindings": bindings}
    document = new_agent_execution_materialization(plan, topology, "materialization.runtime")
    document["sources"] = [{"source_id": "source.skill", "kind": "agent_research",
        "reference": "fixture", "fingerprint": "c" * 64,
        "status": "verified", "note": ""}]
    if periodic:
        document["entries"] = [{"entry_id": "periodic", "family": "periodic_channel",
            "handler_version": 2, "node_ids": ["node.periodic"],
            "source_ids": ["source.skill"], "coverage_keys": ["coverage.periodic"],
            "payload": _periodic_payload(interval=0.5, count=3), "note": ""}]
    else:
        document["entries"] = [
            {"entry_id": "sequence", "family": "finite_sequence", "handler_version": 1,
             "node_ids": ["node.sequence"], "source_ids": ["source.skill"],
             "coverage_keys": ["coverage.sequence"], "payload": {
                 "sequence_id": "sequence.fixture", "steps": [{"step_id": "step.1",
                     "offset_seconds": 0.2, "order": 2,
                     "emits_node_ids": ["node.damage", "node.application"]}],
                 "interrupt_policy": "none"}, "note": ""},
            {"entry_id": "application", "family": "application", "handler_version": 2,
             "node_ids": ["node.application"], "source_ids": ["source.skill"],
             "coverage_keys": ["coverage.application"], "payload": {
                 "element": "cryo", "gauge_units": 1, "application_mode": "timer_counter",
                 "counter_tag": "ordinary", "rule_group": "ordinary.3hit.2_5s",
                 "timer_seconds": 2.5, "hit_count": 3, "sequence": None,
                 "target_ref": "enemy", "landed": True, "attempted": True}, "note": ""},
            {"entry_id": "resource", "family": "resource", "handler_version": 1,
             "node_ids": ["node.resource"], "source_ids": ["source.skill"],
             "coverage_keys": ["coverage.resource"], "payload": {
                 "resource_id": "energy", "operation": "add", "amount": 3,
                 "receiver_ref": "actor", "delivery": "immediate", "travel_seconds": None},
             "note": ""},
        ]
    return document


def _add_runtime_action_anchor(plan, rotation, topology, *, node_id, action_id,
                               start, order=0, relation="triggers"):
    action = copy.deepcopy(rotation["actions"][0])
    action.update(id=action_id, start=start, occupation_end=start + 0.1,
                  order=order)
    rotation["actions"].append(action)
    plan["rotation_model"] = copy.deepcopy(rotation)
    topology["nodes"].append({
        "node_id": node_id, "kind": "action_anchor", "status": "bound",
        "schedule": {"kind": "point", "anchor": {
            "kind": "action", "ref_id": action_id, "phase": "start",
        }, "offset_seconds": 0.0, "order": order},
        "source_ids": ["source.skill"], "parameter_slots": [], "note": "",
    })
    topology["sources"][0]["node_ids"].append(node_id)
    topology["action_checkpoint"]["action_ids"].append(action_id)
    topology["edges"].append({
        "edge_id": f"edge.{node_id}-periodic", "kind": relation,
        "from_node_id": node_id, "to_node_id": "node.periodic",
        "status": "bound", "source_ids": ["source.skill"], "note": "",
    })


def _rule_bundle_payload():
    return {
        "bundle_id": "bundle.fixture",
        "rules": [{
            "id": "rule.fixture", "name_zh": "通用动作增伤",
            "owner": "actor", "source_id": "part.fixture",
            "trigger": {"event": "action_start", "phase": "pre",
                        "action_ids": ["action.skill"]},
            "beneficiaries": {"mode": "self", "scope": "locked"},
            "timing": {"delay": 0, "duration": 1, "cooldown": 0,
                       "cooldown_anchor": "success"},
            "stacking": {"max": 1, "add": 1, "policy": "refresh"},
            "effects": [{"id": "effect.fixture",
                "benefit_type_id": "benefit.dmg_bonus.all", "value": 1,
                "domain": "self", "filter": {"event": "hit", "phase": "pre",
                    "event_ids": ["hit.fixture"]}}],
            "status": "assumed", "source": "Agent材料化工程夹具",
        }],
        "initial_state": {"rules": [{
            "rule_id": "rule.fixture", "cooldown_remaining": 0,
            "instances": [],
        }]},
        "resource_ownership": [],
        "parts": [{
            "part_id": "part.fixture", "rule_id": "rule.fixture",
            "node_ids": ["node.rule"], "source_ids": ["source.skill"],
            "note": "通用D20输入",
        }],
    }


def _add_rule_bundle(topology, document):
    topology["nodes"].append(_node(
        "node.rule", "buff_interval", _point("node.action", 0, 8)))
    topology["sources"][0]["node_ids"].append("node.rule")
    # Rebind after changing topology: both the document envelope and existing
    # execution-binding document belong to the old graph.
    document = copy.deepcopy(document)
    document["topology_binding"]["topology_fingerprint"] = fingerprint(topology)
    document["handler_set_fingerprint"] = agent_execution_materialization_capabilities()[
        "handler_set_fingerprint"]
    document["entries"].append({
        "entry_id": "rules", "family": "mechanics_rule_bundle",
        "handler_version": 1, "node_ids": ["node.rule"],
        "source_ids": ["source.skill"], "coverage_keys": ["coverage.rules"],
        "payload": _rule_bundle_payload(), "note": "",
    })
    return document


def test_runtime_adapters_install_finite_application_and_resource_into_engine_graph():
    plan, template, rotation, mechanics = _runtime_base()
    topology = _runtime_topology()
    document = _runtime_document(plan, topology)
    effective = materialize_agent_execution(plan, topology, document)
    runtime = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)
    assert runtime["status"] == "complete"
    assert [row["status"] for row in runtime["receipt"]["entries"]] == [
        "installed", "installed", "installed"]
    assert runtime["mechanics_model"]["hit_events"][0]["application"]["target_id"] == "enemy"
    application_row = next(
        row for row in runtime["receipt"]["entries"]
        if row["family"] == "application")
    assert application_row["installed"][0]["delivery"] == "dispatch_descriptor"
    assert runtime["mechanics_model"].get("application_events", []) == []
    assert runtime["mechanics_model"]["elemental_model"]["profiles"][0]["icd"] == {
        "kind": "timer_counter", "counter_tag": "ordinary",
        "rule_group": "ordinary.3hit.2_5s", "reset_seconds": 2.5,
        "hit_interval": 3,
    }
    assert runtime["rotation_model"]["events"][0]["amounts"] == {"actor": 3}
    assert validate_mechanics_model(runtime["mechanics_model"])["status"] == "valid"
    result = execute_mechanics(runtime["template"], runtime["rotation_model"],
                               runtime["mechanics_model"], [])
    assert result["status"] in {"passed", "unproven"}
    assert result["result"]["hits"]


def test_finite_dispatch_installs_immediate_energy_as_a_child_occurrence():
    plan, template, rotation, mechanics = _runtime_base()
    topology = _runtime_topology()
    topology["edges"].append({
        "edge_id": "edge.sequence-resource", "kind": "contains",
        "from_node_id": "node.sequence", "to_node_id": "node.resource",
        "status": "bound", "source_ids": ["source.skill"], "note": "",
    })
    document = _runtime_document(plan, topology)
    sequence = next(row for row in document["entries"]
                    if row["family"] == "finite_sequence")
    sequence["payload"]["steps"][0]["emits_node_ids"].append("node.resource")

    effective = materialize_agent_execution(plan, topology, document)
    runtime = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)
    by_family = {row["family"]: row for row in runtime["receipt"]["entries"]}

    assert runtime["status"] == "complete"
    assert by_family["resource"]["installed"] == [{
        "target_identity": "rotation.resource_dispatch:node.resource",
        "delivery": "dispatch_descriptor",
    }]
    events = runtime["rotation_model"]["events"]
    assert len(events) == 1
    assert (events[0]["t"], events[0]["amounts"]) == (0.2, {"actor": 3})


def test_registering_an_unused_runtime_adapter_does_not_change_existing_execution(
        monkeypatch):
    plan, template, rotation, mechanics = _runtime_base()
    topology = _runtime_topology()
    document = _runtime_document(plan, topology)
    effective = materialize_agent_execution(plan, topology, document)
    before = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)

    monkeypatch.setitem(
        runtime_module.RUNTIME_ADAPTER_REGISTRY,
        "future_unrelated_family",
        runtime_module.RuntimeAdapterSpec(
            "future_unrelated_family", "future_unrelated_capability", 999,
            unsupported_reason="not used by this document",
        ),
    )
    after = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)
    assert after == before


def test_composer_and_service_consume_installed_runtime_graph(tmp_path):
    source_plan, _, rotation, mechanics = _runtime_base()
    plan = enable_mechanics(new_plan())
    plan["manual_template"] = source_plan["manual_template"]
    plan["rotation_model"] = rotation
    plan["mechanics_model"] = mechanics
    topology = _runtime_topology()
    document = _runtime_document(plan, topology)
    plan["extensions"]["agent_execution_materialization"] = document

    output = run_plan_calc(plan, tmp_path / "missing.db")
    runtime_receipt = output["report"]["agent_execution_runtime_receipt"]
    assert runtime_receipt["status"] == "complete"
    assert runtime_receipt["coverage"]["pending_keys"] == []
    assert output["result"]["hits"]
    direct = [row for row in output["result"]["rotation_check"]["event_log"]
              if row["type"] == "energy_direct"]
    assert direct and direct[0]["energy"][0]["gain"] == 3


def test_failed_finite_dispatch_cannot_install_its_application_standalone():
    plan, template, rotation, mechanics = _runtime_base()
    topology = _runtime_topology()
    topology["edges"].append({
        "edge_id": "edge.sequence-resource", "kind": "contains",
        "from_node_id": "node.sequence", "to_node_id": "node.resource",
        "status": "bound", "source_ids": ["source.skill"], "note": "",
    })
    document = _runtime_document(plan, topology)
    sequence = next(row for row in document["entries"]
                    if row["family"] == "finite_sequence")
    sequence["payload"]["interrupt_policy"] = "cancel_remaining"
    sequence["payload"]["steps"][0]["emits_node_ids"].append("node.resource")

    effective = materialize_agent_execution(plan, topology, document)
    runtime = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)
    by_family = {row["family"]: row for row in runtime["receipt"]["entries"]}

    assert by_family["finite_sequence"]["status"] == "partial"
    for family in ("application", "resource"):
        assert by_family[family]["status"] == "partial"
        assert by_family[family]["installed"] == []
        assert by_family[family]["issues"][0]["code"] == (
            "agent_materialization_parent_dispatch_uninstalled")
        assert by_family[family]["issues"][0]["dependency_status"] == "blocked"
        assert by_family[family]["issues"][0]["parent_dispatches"] == [{
            "entry_id": "sequence", "status": "partial",
        }]
    assert runtime["mechanics_model"]["hit_events"] == []
    assert runtime["mechanics_model"].get("application_events", []) == []
    assert runtime["mechanics_model"]["elemental_model"]["profiles"] == []


@pytest.mark.parametrize("unsupported_family", ["finite_sequence", "resource"])
def test_payload_specific_runtime_gap_cannot_grant_topology_coverage(
        unsupported_family):
    plan, _, _, _ = _runtime_base()
    topology = _runtime_topology()
    document = _runtime_document(plan, topology)
    entry = next(row for row in document["entries"]
                 if row["family"] == unsupported_family)
    if unsupported_family == "finite_sequence":
        entry["payload"]["interrupt_policy"] = "cancel_remaining"
    else:
        entry["payload"].update(delivery="projectile", travel_seconds=0.5)
    plan["extensions"]["agent_execution_materialization"] = document

    receipt = lint_event_topology_bindings(
        plan, topology, plan["extensions"]["event_topology_bindings"])

    assert receipt["status"] == "incomplete"
    issue = next(row for row in receipt["issues"]
                 if row["code"] == "event_topology_materialization_runtime_unsupported")
    assert issue["family"] == unsupported_family
    assert issue["handler_version"] == entry["handler_version"]


def test_finite_dispatch_with_unsupported_resource_is_atomic_for_all_children():
    plan, template, rotation, mechanics = _runtime_base()
    topology = _runtime_topology()
    topology["edges"].append({
        "edge_id": "edge.sequence-resource", "kind": "contains",
        "from_node_id": "node.sequence", "to_node_id": "node.resource",
        "status": "bound", "source_ids": ["source.skill"], "note": "",
    })
    document = _runtime_document(plan, topology)
    sequence = next(row for row in document["entries"]
                    if row["family"] == "finite_sequence")
    sequence["payload"]["steps"][0]["emits_node_ids"].append("node.resource")
    resource = next(row for row in document["entries"]
                    if row["family"] == "resource")
    resource["payload"].update(delivery="projectile", travel_seconds=0.5)

    effective = materialize_agent_execution(plan, topology, document)
    runtime = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)
    by_family = {row["family"]: row for row in runtime["receipt"]["entries"]}

    assert by_family["finite_sequence"]["status"] == "unsupported"
    assert by_family["finite_sequence"]["issues"][0]["code"] == (
        "agent_materialization_dispatch_resource_unsupported")
    assert by_family["finite_sequence"]["issues"][0]["child_issue_codes"] == [
        "agent_materialization_resource_semantics_unsupported"]
    assert by_family["application"]["status"] == "partial"
    assert by_family["application"]["installed"] == []
    assert by_family["application"]["issues"][0]["code"] == (
        "agent_materialization_parent_dispatch_uninstalled")
    assert by_family["resource"]["status"] == "unsupported"
    assert by_family["resource"]["installed"] == []
    assert by_family["resource"]["issues"][0]["code"] == (
        "agent_materialization_resource_semantics_unsupported")
    assert runtime["mechanics_model"]["hit_events"] == []
    assert runtime["mechanics_model"].get("application_events", []) == []
    assert runtime["rotation_model"]["events"] == []


def test_runtime_adapter_installs_bounded_nonoverlapping_periodic_pattern():
    plan, template, rotation, mechanics = _runtime_base()
    mechanics.pop("elemental_model")
    plan["mechanics_model"].pop("elemental_model")
    topology = _runtime_topology(periodic=True)
    document = _runtime_document(plan, topology, periodic=True)
    effective = materialize_agent_execution(plan, topology, document)
    runtime = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)
    assert runtime["status"] == "complete"
    pattern = runtime["mechanics_model"]["hit_patterns"][0]
    assert pattern["offsets"] == [0.2, 0.7, 1.2]
    result = execute_mechanics(runtime["template"], runtime["rotation_model"],
                               runtime["mechanics_model"], [])
    assert [row["id"] for row in result["result"]["hits"][:3]] == [
        pattern["id"] + ":1", pattern["id"] + ":2", pattern["id"] + ":3"]


def test_periodic_v2_projects_equal_interval_pulse_field_with_derived_count():
    """Synthetic fixture A: a regular field, unrelated to any game identity."""
    plan, template, rotation, mechanics = _runtime_base()
    mechanics.pop("elemental_model")
    plan["mechanics_model"].pop("elemental_model")
    topology = _runtime_topology(periodic=True)
    owner = next(row for row in topology["nodes"] if row["node_id"] == "node.periodic")
    owner["schedule"].update(count=None, duration_seconds=1.2)
    document = _runtime_document(plan, topology, periodic=True)
    document["entries"][0]["payload"] = _periodic_payload(
        channel_id="channel.pulse-field", interval=0.5, count=None,
        duration=1.2, refresh="reset_duration")
    document["entries"][0]["payload"]["schedule"]["derive_count"] = (
        "through_instance_end_inclusive")

    effective = materialize_agent_execution(plan, topology, document)
    runtime = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)

    assert runtime["status"] == "complete"
    row = runtime["receipt"]["entries"][0]
    lifecycle = row["installed"][0]
    assert lifecycle["projection_contract"] == "periodic-lifecycle-projection-v1"
    assert lifecycle["summary"]["materialized_occurrences"] == 3
    assert lifecycle["refresh_policy_state"] == "executed_by_shared_projector"
    assert {item["generation"] for item in lifecycle["occurrences"]} == {1}
    assert runtime["mechanics_model"]["hit_patterns"][0]["offsets"] == [0.2, 0.7, 1.2]


def test_periodic_v2_projects_explicit_orbit_offsets_without_equal_interval_guess():
    """Synthetic fixture B: a finite irregular orbit, not a role-specific branch."""
    plan, template, rotation, mechanics = _runtime_base()
    mechanics.pop("elemental_model")
    plan["mechanics_model"].pop("elemental_model")
    topology = _runtime_topology(periodic=True)
    owner = next(row for row in topology["nodes"] if row["node_id"] == "node.periodic")
    owner["schedule"] = {
        "kind": "explicit_offsets",
        "anchor": {"kind": "node", "ref_id": "node.action", "phase": "point"},
        "occurrences": [
            {"occurrence_id": "orbit.contact.1", "offset_seconds": 0.15, "order": 1},
            {"occurrence_id": "orbit.contact.2", "offset_seconds": 0.55, "order": 2},
            {"occurrence_id": "orbit.contact.3", "offset_seconds": 1.4, "order": 3},
        ],
    }
    document = _runtime_document(plan, topology, periodic=True)
    document["entries"][0]["payload"] = {
        "profile_id": "profile.orbit-projectile",
        "channel_id": "channel.orbit-projectile",
        "schedule": {"kind": "explicit_offsets", "schedule_id": "schedule.orbit",
            "occurrences": copy.deepcopy(owner["schedule"]["occurrences"])},
        "lifecycle": {"duration_seconds": 1.4, "refresh_policy": "none",
            "off_field_policy": "continue", "pause_resume_policy": "none"},
        "establishments": [{"establishment_id": "establishment.orbit",
            "anchor_node_id": "node.periodic", "order": 0}],
        "controls": [], "on_field_intervals": [],
        "dispatches": [{"dispatch_id": "dispatch.orbit-contact",
            "emits_node_ids": ["node.damage"]}],
    }

    effective = materialize_agent_execution(plan, topology, document)
    runtime = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)

    assert runtime["status"] == "complete"
    lifecycle = runtime["receipt"]["entries"][0]["installed"][0]
    assert lifecycle["schedule_kind"] == "explicit_offsets"
    assert [row["order"] for row in lifecycle["occurrences"]] == [1, 2, 3]
    assert runtime["mechanics_model"]["hit_patterns"][0]["offsets"] == [0.15, 0.55, 1.4]


def test_periodic_v2_replace_control_uses_its_own_root_action():
    plan, template, rotation, mechanics = _runtime_base()
    mechanics.pop("elemental_model")
    plan["mechanics_model"].pop("elemental_model")
    topology = _runtime_topology(periodic=True)
    owner = next(row for row in topology["nodes"]
                 if row["node_id"] == "node.periodic")
    owner["schedule"]["refresh_policy"] = "replace"
    _add_runtime_action_anchor(
        plan, rotation, topology, node_id="node.refresh-action",
        action_id="action.refresh", start=0.8, relation="replaces")
    document = _runtime_document(plan, topology, periodic=True)
    payload = document["entries"][0]["payload"]
    payload["lifecycle"]["refresh_policy"] = "replace_previous"
    payload["controls"] = [{
        "control_id": "control.refresh", "operation": "replace",
        "anchor_node_id": "node.refresh-action", "order": 0,
        "duration_seconds": None,
    }]

    effective = materialize_agent_execution(plan, topology, document)
    runtime = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)

    assert runtime["status"] == "complete"
    installed = runtime["receipt"]["entries"][0]["installed"]
    lifecycle = next(row for row in installed
                     if row.get("projection_contract"))
    assert [(row["generation"], row["root_action_id"], row["time_ticks"])
            for row in lifecycle["occurrences"]] == [
        (1, "action.skill", 200000),
        (1, "action.skill", 700000),
        (2, "action.refresh", 1000000),
        (2, "action.refresh", 1500000),
        (2, "action.refresh", 2000000),
    ]
    patterns = {row["action_id"]: row
                for row in runtime["mechanics_model"]["hit_patterns"]}
    assert patterns["action.skill"]["offsets"] == [0.2, 0.7]
    assert patterns["action.refresh"]["offsets"] == [0.2, 0.7, 1.2]
    assert len(runtime["template"]["hits"]) == 2
    assert validate_template(runtime["template"]) is None


def test_periodic_v2_ignore_new_keeps_the_original_root_generation():
    plan, template, rotation, mechanics = _runtime_base()
    mechanics.pop("elemental_model")
    plan["mechanics_model"].pop("elemental_model")
    topology = _runtime_topology(periodic=True)
    owner = next(row for row in topology["nodes"]
                 if row["node_id"] == "node.periodic")
    owner["schedule"]["refresh_policy"] = "ignore"
    _add_runtime_action_anchor(
        plan, rotation, topology, node_id="node.repeat-action",
        action_id="action.repeat", start=0.8)
    document = _runtime_document(plan, topology, periodic=True)
    payload = document["entries"][0]["payload"]
    payload["lifecycle"]["refresh_policy"] = "ignore_new"
    payload["establishments"].append({
        "establishment_id": "establishment.ignored",
        "anchor_node_id": "node.repeat-action", "order": 0,
    })

    effective = materialize_agent_execution(plan, topology, document)
    runtime = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)

    lifecycle = runtime["receipt"]["entries"][0]["installed"][0]
    assert lifecycle["summary"]["generations"] == 1
    assert {row["root_action_id"] for row in lifecycle["occurrences"]} == {
        "action.skill"}
    assert {row["action_id"] for row in runtime["mechanics_model"]["hit_patterns"]} == {
        "action.skill"}


@pytest.mark.parametrize(("refresh", "topology_refresh", "operation", "duration"), [
    ("reset_duration", "restart", "reset_duration", None),
    ("extend_duration", "extend", "extend_duration", 0.5),
])
def test_periodic_v2_executes_reset_and_extend_controls(
        refresh, topology_refresh, operation, duration):
    plan, template, rotation, mechanics = _runtime_base()
    mechanics.pop("elemental_model")
    plan["mechanics_model"].pop("elemental_model")
    topology = _runtime_topology(periodic=True)
    owner = next(row for row in topology["nodes"]
                 if row["node_id"] == "node.periodic")
    owner["schedule"].update(count=None, refresh_policy=topology_refresh)
    topology["nodes"].append(_node(
        "node.lifecycle-control", "state", _point("node.action", 0.5, 9)))
    topology["sources"][0]["node_ids"].append("node.lifecycle-control")
    topology["edges"].append({
        "edge_id": "edge.lifecycle-control-periodic", "kind": "refreshes",
        "from_node_id": "node.lifecycle-control", "to_node_id": "node.periodic",
        "status": "bound", "source_ids": ["source.skill"], "note": "",
    })
    document = _runtime_document(plan, topology, periodic=True)
    payload = document["entries"][0]["payload"]
    payload["schedule"].update(count=None,
        derive_count="through_instance_end_inclusive")
    payload["lifecycle"]["refresh_policy"] = refresh
    payload["controls"] = [{
        "control_id": "control.lifecycle", "operation": operation,
        "anchor_node_id": "node.lifecycle-control", "order": 9,
        "duration_seconds": duration,
    }]

    effective = materialize_agent_execution(plan, topology, document)
    runtime = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)

    assert runtime["status"] == "complete"
    pattern = runtime["mechanics_model"]["hit_patterns"][0]
    assert pattern["offsets"] == [0.2, 0.7, 1.2, 1.7]


def test_periodic_v2_executes_pause_resume_and_off_field_stop_boundaries():
    plan, template, rotation, mechanics = _runtime_base()
    mechanics.pop("elemental_model")
    plan["mechanics_model"].pop("elemental_model")
    topology = _runtime_topology(periodic=True)
    for node_id, offset, order in (
            ("node.pause", 0.4, 7), ("node.resume", 0.8, 8)):
        topology["nodes"].append(_node(
            node_id, "state", _point("node.action", offset, order)))
        topology["sources"][0]["node_ids"].append(node_id)
        topology["edges"].append({
            "edge_id": f"edge.{node_id}-periodic", "kind": "triggers",
            "from_node_id": node_id, "to_node_id": "node.periodic",
            "status": "bound", "source_ids": ["source.skill"], "note": "",
        })
    document = _runtime_document(plan, topology, periodic=True)
    payload = document["entries"][0]["payload"]
    payload["lifecycle"]["pause_resume_policy"] = "pause_preserve_phase"
    payload["controls"] = [
        {"control_id": "control.pause", "operation": "pause",
         "anchor_node_id": "node.pause", "order": 7,
         "duration_seconds": None},
        {"control_id": "control.resume", "operation": "resume",
         "anchor_node_id": "node.resume", "order": 8,
         "duration_seconds": None},
    ]

    effective = materialize_agent_execution(plan, topology, document)
    runtime = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)
    assert runtime["mechanics_model"]["hit_patterns"][0]["offsets"] == [
        0.2, 1.1, 1.6]

    plan, template, rotation, mechanics = _runtime_base()
    mechanics.pop("elemental_model")
    plan["mechanics_model"].pop("elemental_model")
    topology = _runtime_topology(periodic=True)
    owner = next(row for row in topology["nodes"]
                 if row["node_id"] == "node.periodic")
    owner["schedule"]["off_field_policy"] = "stop"
    for node_id, offset, order in (
            ("node.frontline-start", 0.0, -1),
            ("node.frontline-end", 0.8, 6)):
        topology["nodes"].append(_node(
            node_id, "other", _point("node.action", offset, order)))
        topology["sources"][0]["node_ids"].append(node_id)
    document = _runtime_document(plan, topology, periodic=True)
    payload = document["entries"][0]["payload"]
    payload["lifecycle"]["off_field_policy"] = "stop"
    payload["on_field_intervals"] = [{
        "interval_id": "interval.frontline",
        "start_node_id": "node.frontline-start",
        "end_node_id": "node.frontline-end",
    }]

    effective = materialize_agent_execution(plan, topology, document)
    runtime = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)
    assert runtime["mechanics_model"]["hit_patterns"][0]["offsets"] == [0.2, 0.7]
    lifecycle = runtime["receipt"]["entries"][0]["installed"][0]
    assert lifecycle["generations"][0]["status"] == "stopped_off_field"


def test_periodic_v2_dispatch_groups_all_fire_on_every_occurrence():
    plan, template, rotation, mechanics = _runtime_base()
    mechanics.pop("elemental_model")
    plan["mechanics_model"].pop("elemental_model")
    second_hit = copy.deepcopy(template["hits"][0])
    second_hit["id"] = "hit.fixture.second"
    template["hits"].append(second_hit)
    plan["manual_template"]["hits"].append(copy.deepcopy(second_hit))

    topology = _runtime_topology(periodic=True)
    topology["nodes"].append(_node(
        "node.damage.second", "damage", _point("node.periodic", 0, 3)))
    topology["sources"][0]["node_ids"].append("node.damage.second")
    topology["edges"].append({
        "edge_id": "edge.periodic-damage-second", "kind": "causes",
        "from_node_id": "node.periodic", "to_node_id": "node.damage.second",
        "status": "bound", "source_ids": ["source.skill"], "note": "",
    })
    document = _runtime_document(plan, topology, periodic=True)
    bindings = plan["extensions"]["event_topology_bindings"]
    bindings["bindings"].append({
        "binding_id": "binding.damage.second", "node_id": "node.damage.second",
        "target_kind": "hit", "pointer": "/manual_template/hits/1",
        "target_fingerprint": fingerprint(plan["manual_template"]["hits"][1]),
        "note": "",
    })
    document["plan_binding"]["input_fingerprint"] = (
        new_agent_execution_materialization(
            plan, topology, "rebind")["plan_binding"]["input_fingerprint"])
    document["entries"][0]["payload"]["dispatches"].append({
        "dispatch_id": "dispatch.second",
        "emits_node_ids": ["node.damage.second"],
    })

    effective = materialize_agent_execution(plan, topology, document)
    runtime = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)

    assert runtime["status"] == "complete"
    lifecycle = runtime["receipt"]["entries"][0]["installed"][0]
    assert lifecycle["dispatch_group_semantics"] == (
        "all_dispatches_each_occurrence")
    patterns = {row["prototype_hit_id"]: row
                for row in runtime["mechanics_model"]["hit_patterns"]}
    assert set(patterns) == {"hit.fixture", "hit.fixture.second"}
    assert all(row["offsets"] == [0.2, 0.7, 1.2]
               for row in patterns.values())


def test_on_field_boundaries_are_observations_not_fake_control_edges():
    plan, _, _, _ = _runtime_base()
    topology = _runtime_topology(periodic=True)
    owner = next(row for row in topology["nodes"]
                 if row["node_id"] == "node.periodic")
    owner["schedule"]["off_field_policy"] = "stop"
    for node_id, offset, order in (
            ("node.frontline.start", 0.0, 8),
            ("node.frontline.end", 1.2, 9)):
        topology["nodes"].append(_node(
            node_id, "other", _point("node.action", offset, order)))
        topology["sources"][0]["node_ids"].append(node_id)
    document = _runtime_document(plan, topology, periodic=True)
    payload = document["entries"][0]["payload"]
    payload["lifecycle"]["off_field_policy"] = "stop"
    payload["on_field_intervals"] = [{
        "interval_id": "frontline.interval.1",
        "start_node_id": "node.frontline.start",
        "end_node_id": "node.frontline.end",
    }]

    receipt = lint_agent_execution_materialization(plan, topology, document)
    assert receipt["status"] == "ready"
    assert not any(issue["code"] == "agent_periodic_lifecycle_anchor_unbound"
                   for issue in receipt["issues"])


def test_periodic_v2_executes_explicit_cancel_through_shared_projector():
    plan, template, rotation, mechanics = _runtime_base()
    mechanics.pop("elemental_model")
    plan["mechanics_model"].pop("elemental_model")
    topology = _runtime_topology(periodic=True)
    topology["nodes"].append(_node(
        "node.cancel", "state", _point("node.action", 0.8, 9)))
    topology["sources"][0]["node_ids"].append("node.cancel")
    topology["edges"].append({
        "edge_id": "edge.cancel-periodic", "kind": "triggers",
        "from_node_id": "node.cancel", "to_node_id": "node.periodic",
        "status": "bound", "source_ids": ["source.skill"], "note": "",
    })
    document = _runtime_document(plan, topology, periodic=True)
    document["entries"][0]["payload"]["controls"] = [{
        "control_id": "control.cancel", "operation": "cancel",
        "anchor_node_id": "node.cancel", "order": 9, "duration_seconds": None,
    }]

    effective = materialize_agent_execution(plan, topology, document)
    runtime = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)

    assert runtime["status"] == "complete"
    row = runtime["receipt"]["entries"][0]
    assert row["status"] == "installed"
    lifecycle = row["installed"][0]
    assert lifecycle["summary"]["materialized_occurrences"] == 2
    assert lifecycle["summary"]["cancelled_occurrences"] == 1
    assert lifecycle["generations"][0]["status"] == "cancelled"
    assert runtime["mechanics_model"]["hit_patterns"][0]["offsets"] == [0.2, 0.7]


def test_failed_periodic_dispatch_cannot_install_its_application_standalone():
    plan, template, rotation, mechanics = _runtime_base()
    mechanics["hit_events"] = [{
        "hit_id": "hit.fixture", "action_id": "action.skill", "order": 2,
    }]
    topology = _runtime_topology(periodic=True)
    topology["nodes"].extend([
        _node("node.application", "application",
              _point("node.periodic", 0, 3)),
        _node("node.cancel", "state", _point("node.action", 0.8, 9)),
    ])
    topology["sources"][0]["node_ids"].extend([
        "node.application", "node.cancel",
    ])
    topology["edges"].extend([
        {"edge_id": "edge.periodic-application", "kind": "contains",
         "from_node_id": "node.periodic", "to_node_id": "node.application",
         "status": "bound", "source_ids": ["source.skill"], "note": ""},
        {"edge_id": "edge.cancel-periodic", "kind": "triggers",
         "from_node_id": "node.cancel", "to_node_id": "node.periodic",
         "status": "bound", "source_ids": ["source.skill"], "note": ""},
    ])
    document = _runtime_document(plan, topology, periodic=True)
    periodic = document["entries"][0]
    periodic["payload"]["dispatches"][0]["emits_node_ids"].append(
        "node.application")
    periodic["payload"]["controls"] = [{
        "control_id": "control.cancel", "operation": "cancel",
        "anchor_node_id": "node.cancel", "order": 9,
        "duration_seconds": None,
    }]
    document["entries"].append({
        "entry_id": "application", "family": "application",
        "handler_version": 2, "node_ids": ["node.application"],
        "source_ids": ["source.skill"],
        "coverage_keys": ["coverage.application"], "payload": {
            "element": "cryo", "gauge_units": 1,
            "application_mode": "timer_counter", "counter_tag": "ordinary",
            "rule_group": "ordinary.3hit.2_5s", "timer_seconds": 2.5,
            "hit_count": 3, "sequence": None, "target_ref": "enemy",
            "landed": True, "attempted": True,
        }, "note": "",
    })

    effective = materialize_agent_execution(plan, topology, document)
    runtime = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)
    by_family = {row["family"]: row for row in runtime["receipt"]["entries"]}

    assert by_family["periodic_channel"]["status"] == "partial"
    assert by_family["periodic_channel"]["issues"][0]["code"] == (
        "agent_materialization_runtime_adapter_partial")
    assert by_family["application"]["status"] == "partial"
    assert by_family["application"]["installed"] == []
    assert by_family["application"]["issues"][0]["code"] == (
        "agent_materialization_parent_dispatch_uninstalled")
    assert runtime["mechanics_model"].get("application_events", []) == []
    assert runtime["mechanics_model"]["elemental_model"]["profiles"] == []
    assert runtime["mechanics_model"]["hit_patterns"] == []


def test_periodic_v2_installs_immediate_energy_for_every_projected_occurrence():
    plan, template, rotation, mechanics = _runtime_base()
    mechanics.pop("elemental_model")
    plan["mechanics_model"].pop("elemental_model")
    topology = _runtime_topology(periodic=True)
    topology["nodes"].append(_node(
        "node.resource-tick", "resource", _point("node.periodic", 0, 7)))
    topology["sources"][0]["node_ids"].append("node.resource-tick")
    topology["edges"].append({
        "edge_id": "edge.periodic-resource", "kind": "contains",
        "from_node_id": "node.periodic", "to_node_id": "node.resource-tick",
        "status": "bound", "source_ids": ["source.skill"], "note": "",
    })
    document = _runtime_document(plan, topology, periodic=True)
    document["entries"][0]["payload"]["dispatches"].append({
        "dispatch_id": "dispatch.resource", "emits_node_ids": ["node.resource-tick"],
    })
    document["entries"].append({
        "entry_id": "resource", "family": "resource", "handler_version": 1,
        "node_ids": ["node.resource-tick"], "source_ids": ["source.skill"],
        "coverage_keys": ["coverage.resource"], "payload": {
            "resource_id": "energy", "operation": "add", "amount": 3,
            "receiver_ref": "actor", "delivery": "immediate",
            "travel_seconds": None,
        }, "note": "",
    })

    effective = materialize_agent_execution(plan, topology, document)
    runtime = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)

    row, resource = runtime["receipt"]["entries"]
    assert row["status"] == "installed"
    assert resource["status"] == "installed"
    assert resource["installed"] == [{
        "target_identity": "rotation.resource_dispatch:node.resource-tick",
        "delivery": "dispatch_descriptor",
    }]
    events = runtime["rotation_model"]["events"]
    assert [(event["t"], event["amounts"]) for event in events] == [
        (0.2, {"actor": 3}), (0.7, {"actor": 3}), (1.2, {"actor": 3})]
    assert len({event["id"] for event in events}) == 3


@pytest.mark.parametrize("periodic", [False, True])
def test_dispatch_families_reject_multiple_owner_nodes(periodic):
    plan, _, _, _ = _runtime_base()
    topology = _runtime_topology(periodic=periodic)
    document = _runtime_document(plan, topology, periodic=periodic)
    dispatch = next(
        row for row in document["entries"]
        if row["family"] in {"finite_sequence", "periodic_channel"}
    )
    dispatch["node_ids"].append("node.unexecuted-owner")

    with pytest.raises(AgentExecutionMaterializationError) as error:
        validate_agent_execution_materialization(document)
    assert error.value.code == "agent_materialization_owner_node_count"


def test_self_contained_d20_rule_bundle_installs_executes_and_receipts_exact_parts():
    plan, template, rotation, mechanics = _runtime_base()
    topology = _runtime_topology()
    document = _runtime_document(plan, topology)
    document = _add_rule_bundle(topology, document)
    plan["extensions"]["event_topology"] = topology
    bindings = new_event_topology_bindings(topology)
    bindings["bindings"] = [{"binding_id": "binding.damage", "node_id": "node.damage",
        "target_kind": "hit", "pointer": "/manual_template/hits/0",
        "target_fingerprint": fingerprint(plan["manual_template"]["hits"][0]), "note": ""}]
    plan["extensions"]["event_topology_bindings"] = bindings
    document["plan_binding"]["input_fingerprint"] = new_agent_execution_materialization(
        plan, topology, "rebind")["plan_binding"]["input_fingerprint"]

    effective = materialize_agent_execution(plan, topology, document)
    runtime = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)
    by_family = {row["family"]: row for row in runtime["receipt"]["entries"]}
    rules = by_family["mechanics_rule_bundle"]
    assert rules["status"] == "installed"
    assert rules["installed"] == [{
        "target_identity": "mechanics.rule:rule.fixture",
        "rule_id": "rule.fixture",
        "rule_fingerprint": fingerprint(_rule_bundle_payload()["rules"][0]),
        "initial_state_fingerprint": fingerprint(
            _rule_bundle_payload()["initial_state"]["rules"][0]),
        "part_id": "part.fixture", "node_ids": ["node.rule"],
        "source_ids": ["source.skill"],
    }]
    result = execute_mechanics(
        runtime["template"], runtime["rotation_model"],
        runtime["mechanics_model"], [])
    hit = result["result"]["hits"][0]
    assert hit["expected"] == pytest.approx(90)
    assert hit["zones"]["dmg_bonus"] == pytest.approx(2)
    assert hit["matched_effect_refs"][0]["buff_id"] == "mechanics:hit.fixture"


def test_rule_bundle_rejects_output_trace_and_requires_exact_part_scope():
    plan, topology = _plan(), _topology()
    topology["nodes"].append(_node(
        "node.rule", "buff_interval", _point("node.action", 0, 8)))
    topology["sources"][0]["node_ids"].append("node.rule")
    document = new_agent_execution_materialization(plan, topology, "materialization.rules")
    document["sources"] = [{"source_id": "source.skill", "kind": "agent_research",
        "reference": "fixture", "fingerprint": "c" * 64,
        "status": "verified", "note": ""}]
    document["entries"] = [{
        "entry_id": "rules", "family": "mechanics_rule_bundle",
        "handler_version": 1, "node_ids": ["node.rule"],
        "source_ids": ["source.skill"], "coverage_keys": ["coverage.rules"],
        "payload": _rule_bundle_payload(), "note": "",
    }]
    assert lint_agent_execution_materialization(plan, topology, document)["status"] == "ready"

    tampered = copy.deepcopy(document)
    tampered["entries"][0]["payload"]["trace"] = []
    with pytest.raises(AgentExecutionMaterializationError) as error:
        validate_agent_execution_materialization(tampered)
    assert error.value.code == "agent_materialization_result_payload"

    tampered = copy.deepcopy(document)
    tampered["entries"][0]["payload"]["parts"][0]["node_ids"] = ["node.buff"]
    receipt = lint_agent_execution_materialization(plan, topology, tampered)
    assert receipt["status"] == "partial"
    assert "agent_materialization_rule_part_scope_mismatch" in {
        row["code"] for row in receipt["issues"]}

    missing_initial = copy.deepcopy(document)
    missing_initial["entries"][0]["payload"]["initial_state"]["rules"] = []
    with pytest.raises(AgentExecutionMaterializationError) as error:
        validate_agent_execution_materialization(missing_initial)
    assert error.value.code == "agent_materialization_d20_invalid"


def test_composer_service_and_export_use_the_same_materialized_d20_rule(tmp_path):
    source_plan, _, rotation, mechanics = _runtime_base()
    plan = enable_mechanics(new_plan())
    plan["manual_template"] = source_plan["manual_template"]
    plan["rotation_model"] = rotation
    plan["mechanics_model"] = mechanics
    topology = _runtime_topology()
    document = _runtime_document(plan, topology)
    document = _add_rule_bundle(topology, document)
    distribution = [
        {"count": 1, "probability": 2 / 3},
        {"count": 2, "probability": 1 / 3},
    ]
    bundle = document["entries"][-1]["payload"]
    bundle["rules"][0]["resources"] = [{
        "id": "particle.fixture", "kind": "particles", "delay": 0.01,
        "travel_time": 0.1, "count": 4 / 3, "count_mode": "average",
        "count_distribution": distribution, "particle_type": "particle",
        "element": "cryo", "source_part": "part.fixture",
    }]
    bundle["resource_ownership"] = [{
        "rule_id": "rule.fixture", "action_id": "action.skill",
        "source_part": "part.fixture", "replaces_event_ids": [],
    }]
    plan["extensions"]["event_topology"] = topology
    bindings = new_event_topology_bindings(topology)
    bindings["bindings"] = [{"binding_id": "binding.damage", "node_id": "node.damage",
        "target_kind": "hit", "pointer": "/manual_template/hits/0",
        "target_fingerprint": fingerprint(plan["manual_template"]["hits"][0]), "note": ""}]
    plan["extensions"]["event_topology_bindings"] = bindings
    document["plan_binding"]["input_fingerprint"] = new_agent_execution_materialization(
        plan, topology, "rebind")["plan_binding"]["input_fingerprint"]
    plan["extensions"]["agent_execution_materialization"] = document

    output = run_plan_calc(plan, tmp_path / "missing.db")
    rules = {row["family"]: row for row in output["report"][
        "agent_execution_runtime_receipt"]["entries"]}["mechanics_rule_bundle"]
    assert rules["status"] == "installed"
    hit = output["result"]["hits"][0]
    assert hit["zones"]["dmg_bonus"] == pytest.approx(2)
    rotation_check = output["result"]["rotation_check"]
    spawn, credit = [
        row for row in rotation_check["event_log"]
        if row["source_cycle"] == 0 and row["type"] in {"energy_spawn", "energy_credit"}
    ]
    assert spawn["t"] == pytest.approx(0.01)
    assert spawn["count_distribution"] == distribution
    assert credit["t"] == pytest.approx(0.11)
    assert credit["spawn_id"] == spawn["id"]
    assert credit["count"] == spawn["count"] == 4 / 3
    assert credit["resource_cause"]["source_part"] == "part.fixture"
    assert credit["energy"][0]["gain"] == 4
    assert rotation_check["energy_check"]["particle_quantity"]["count_mode"] == "average"
    assert {
        "agent_execution_materialization_document",
        "agent_execution_materialization_receipt",
        "agent_execution_effective_input",
        "agent_execution_runtime_receipt",
    } <= set(output["result"])
    proof = output["result_receipt"]["agent_execution_proof"]
    assert proof["installed_entry_count"] == 4
    assert proof["database_access"] == "none"
    assert {row["family"] for row in proof["entries"]} == {
        "finite_sequence", "application", "resource", "mechanics_rule_bundle",
    }
    coverage = qualify_calculation_coverage(output)
    assert coverage["agent_materialized"] == 4
    assert "agent_execution_proof_invalid" not in {
        row["code"] for row in coverage["issues"]
    }
    exported = json.loads(result_bytes(
        output["result"], output["result_receipt"], "json"))
    assert exported["result"] == output["result"]
    assert exported["result_receipt"] == output["result_receipt"]
    direct = json.loads(export_result(
        output["result"], "json", tmp_path / "formal.json",
        receipt=output["result_receipt"],
    ).read_text(encoding="utf-8"))
    assert direct["result_receipt"] == output["result_receipt"]
    csv_text = result_bytes(
        output["result"], output["result_receipt"], "csv",
    ).decode("utf-8-sig")
    assert "formal_calculation_audit_json" in csv_text
    assert "calculation-coverage-ownership-proof-v1" in csv_text
    assert "agent-execution-result-proof-v1" in csv_text
    for workbook in (
        result_bytes(output["result"], output["result_receipt"], "xlsx"),
        export_result(
            output["result"], "xlsx", tmp_path / "formal.xlsx",
            receipt=output["result_receipt"],
        ).read_bytes(),
        build_formula_workbook(output),
    ):
        with zipfile.ZipFile(io.BytesIO(workbook)) as archive:
            text = "".join(
                archive.read(name).decode("utf-8")
                for name in archive.namelist() if name.endswith(".xml")
            )
        assert "计算审计" in text
        assert "calculation-coverage-ownership-proof-v1" in text
        assert "agent-execution-result-proof-v1" in text

    tampered = copy.deepcopy(output)
    del tampered["result"]["agent_execution_runtime_receipt"]
    with pytest.raises(PlanError) as error:
        result_bytes(tampered["result"], tampered["result_receipt"], "json")
    assert error.value.reason == "source_changed"
    with pytest.raises((TypeError, ValueError)):
        export_result(
            tampered["result"], "json", tmp_path / "tampered.json",
            receipt=tampered["result_receipt"],
        )
    with pytest.raises(FormulaWorkbookError):
        build_formula_workbook(tampered)

    envelope_paths = (
        ("result", "agent_execution_materialization_receipt"),
        ("result", "agent_execution_effective_input"),
        ("result", "agent_execution_runtime_receipt"),
        ("result_receipt", "agent_execution_proof"),
    )
    for parent_key, field in envelope_paths:
        tampered = copy.deepcopy(output)
        tampered[parent_key][field]["unknown_injected"] = {"trusted": True}
        _resign_visible_agent_execution_fingerprints(tampered)
        with pytest.raises(PlanError) as error:
            result_bytes(tampered["result"], tampered["result_receipt"], "json")
        assert error.value.reason == "source_changed"

    tampered = copy.deepcopy(output)
    tampered["result_receipt"]["agent_execution_proof"][
        "materialization_id"] = "materialization.bogus"
    _resign_visible_agent_execution_fingerprints(tampered)
    with pytest.raises(PlanError) as error:
        result_bytes(tampered["result"], tampered["result_receipt"], "json")
    assert error.value.reason == "source_changed"

    tampered = copy.deepcopy(output)
    tampered["result"]["agent_execution_materialization_receipt"][
        "materialization_id"] = "materialization.one"
    tampered["result"]["agent_execution_effective_input"][
        "materialization_id"] = "materialization.two"
    tampered["result_receipt"]["agent_execution_proof"][
        "materialization_id"] = "materialization.three"
    _resign_visible_agent_execution_fingerprints(tampered)
    with pytest.raises(PlanError) as error:
        result_bytes(tampered["result"], tampered["result_receipt"], "json")
    assert error.value.reason == "source_changed"

    tampered = copy.deepcopy(output)
    tampered["result_receipt"]["agent_execution_proof"][
        "materialization_document_fingerprint"] = "f" * 64
    _resign_visible_agent_execution_fingerprints(tampered)
    with pytest.raises(PlanError) as error:
        result_bytes(tampered["result"], tampered["result_receipt"], "json")
    assert error.value.reason == "source_changed"

    tampered = copy.deepcopy(output)
    tampered["result"]["agent_execution_effective_input"]["entries"][0][
        "payload"]["tampered"] = True
    with pytest.raises(PlanError) as error:
        result_bytes(tampered["result"], tampered["result_receipt"], "json")
    assert error.value.reason == "source_changed"

    # A valid in-schema payload substitution must still fail after every
    # public SHA and the proof's payload projection are recomputed.  The
    # original materialization document is the sole authoring surface.
    tampered = copy.deepcopy(output)
    effective_entry = next(
        row for row in tampered["result"]["agent_execution_effective_input"][
            "entries"]
        if row["family"] == "mechanics_rule_bundle"
    )
    effective_entry["payload"]["rules"][0]["effects"][0]["value"] = 9.0
    _resign_visible_agent_execution_fingerprints(tampered)
    proof_entry = next(
        row for row in tampered["result_receipt"]["agent_execution_proof"][
            "entries"]
        if row["family"] == "mechanics_rule_bundle"
    )
    proof_entry["payload_fingerprint"] = fingerprint(
        effective_entry["payload"])
    proof = tampered["result_receipt"]["agent_execution_proof"]
    proof["proof_fingerprint"] = fingerprint({
        key: value for key, value in proof.items()
        if key != "proof_fingerprint"
    })
    with pytest.raises(PlanError) as error:
        result_bytes(tampered["result"], tampered["result_receipt"], "json")
    assert error.value.reason == "source_changed"

    tampered = copy.deepcopy(output)
    runtime = tampered["result"]["agent_execution_runtime_receipt"]
    runtime["entries"][0]["coverage_keys"] = ["coverage.forged"]
    runtime["coverage"]["installed_keys"][0] = "coverage.forged"
    _resign_visible_agent_execution_fingerprints(tampered)
    with pytest.raises(PlanError) as error:
        result_bytes(tampered["result"], tampered["result_receipt"], "json")
    assert error.value.reason == "source_changed"

    tampered = copy.deepcopy(output)
    tampered["result"]["agent_execution_runtime_receipt"]["entries"][0][
        "status"] = "partial"
    with pytest.raises(PlanError) as error:
        result_bytes(tampered["result"], tampered["result_receipt"], "json")
    assert error.value.reason == "source_changed"
    invalid_coverage = qualify_calculation_coverage(tampered)
    assert invalid_coverage["agent_materialized"] == 0
    assert "agent_execution_proof_invalid" in {
        row["code"] for row in invalid_coverage["issues"]
    }

    tampered = copy.deepcopy(output)
    tampered["result"]["agent_execution_materialization_receipt"][
        "schema_version"] = 999
    _resign_visible_agent_execution_fingerprints(tampered)
    with pytest.raises(PlanError) as error:
        result_bytes(tampered["result"], tampered["result_receipt"], "json")
    assert error.value.reason == "source_changed"

    tampered = copy.deepcopy(output)
    runtime = tampered["result"]["agent_execution_runtime_receipt"]
    runtime["adapter_version"] = 999
    for row in runtime["entries"]:
        row["adapter_version"] = 999
    _resign_visible_agent_execution_fingerprints(tampered)
    with pytest.raises(PlanError) as error:
        result_bytes(tampered["result"], tampered["result_receipt"], "json")
    assert error.value.reason == "source_changed"


def test_rule_bundle_cannot_take_over_an_existing_rule_or_owner_source_delivery():
    plan, template, rotation, mechanics = _runtime_base()
    payload = _rule_bundle_payload()
    mechanics["rules"] = copy.deepcopy(payload["rules"])
    mechanics["initial_state"] = copy.deepcopy(payload["initial_state"])
    plan["mechanics_model"] = copy.deepcopy(mechanics)
    topology = _runtime_topology()
    topology["nodes"].append(_node(
        "node.rule", "buff_interval", _point("node.action", 0, 8)))
    topology["sources"][0]["node_ids"].append("node.rule")
    document = new_agent_execution_materialization(plan, topology, "materialization.rules")
    document["sources"] = [{"source_id": "source.skill", "kind": "agent_research",
        "reference": "fixture", "fingerprint": "c" * 64,
        "status": "verified", "note": ""}]
    document["entries"] = [{
        "entry_id": "rules", "family": "mechanics_rule_bundle",
        "handler_version": 1, "node_ids": ["node.rule"],
        "source_ids": ["source.skill"], "coverage_keys": ["coverage.rules"],
        "payload": payload, "note": "",
    }]
    effective = materialize_agent_execution(plan, topology, document)
    runtime = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)
    receipt = runtime["receipt"]["entries"][0]
    assert receipt["status"] == "partial"
    assert "collides" in receipt["issues"][0]["message"]
    assert runtime["mechanics_model"] == mechanics


def test_binding_receipt_accepts_new_materialized_objects_without_double_owning_automatic_nodes():
    plan, _, _, _ = _runtime_base()
    topology = _runtime_topology()
    document = _runtime_document(plan, topology)
    document = _add_rule_bundle(topology, document)
    plan["extensions"]["event_topology"] = topology
    bindings = new_event_topology_bindings(topology)
    bindings["bindings"] = [{"binding_id": "binding.damage", "node_id": "node.damage",
        "target_kind": "hit", "pointer": "/manual_template/hits/0",
        "target_fingerprint": fingerprint(plan["manual_template"]["hits"][0]), "note": ""}]
    plan["extensions"]["event_topology_bindings"] = bindings
    document["plan_binding"]["input_fingerprint"] = new_agent_execution_materialization(
        plan, topology, "rebind")["plan_binding"]["input_fingerprint"]
    plan["extensions"]["agent_execution_materialization"] = document

    receipt = lint_event_topology_bindings(plan, topology, bindings)
    assert receipt["status"] == "complete"
    assert receipt["coverage"]["missing_node_ids"] == []
    assert receipt["coverage"]["materialized_node_ids"] == [
        "node.application", "node.resource", "node.rule", "node.sequence"]
    assert receipt["coverage"]["target_kinds"] == {
        "hit": 1, "agent_materialization": 4}

    conflicted = copy.deepcopy(bindings)
    conflicted["bindings"].append({
        "binding_id": "binding.sequence", "node_id": "node.sequence",
        "target_kind": "hit", "pointer": "/manual_template/hits/0",
        "target_fingerprint": fingerprint(plan["manual_template"]["hits"][0]),
        "note": "故意制造双重所有权",
    })
    conflict = lint_event_topology_bindings(plan, topology, conflicted)
    assert conflict["status"] == "incomplete"
    assert "event_topology_execution_owner_conflict" in {
        row["code"] for row in conflict["issues"]}
