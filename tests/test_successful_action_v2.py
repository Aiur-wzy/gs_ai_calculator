"""successful_action_v2: strict qualification, committed admission and App replay."""
from __future__ import annotations

import copy

import pytest

from genshin_app.equipment.agent_execution_materialization import (
    lint_agent_execution_materialization,
    materialize_agent_execution,
    new_agent_execution_materialization,
)
from genshin_app.equipment.agent_execution_runtime import adapt_agent_execution_runtime
from genshin_app.equipment.event_topology import AUTHORITY, CONTRACT_ID
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.mechanism_sources import compile_mechanism_sources
from genshin_engine import action_admission_audit as admission
from genshin_engine.mechanics import execute_mechanics, validate_mechanics_model
from genshin_runtime.mechanism_qualification import validate_execution_qualification
from tests.test_action_admission_execution import admission_fixture
from tests.test_mechanics_execution import add, fixture, rule
from tests.test_mechanism_cast_graph import identity
from tests.test_mechanism_cast_integration import scene as v1_scene
from tests.test_mechanism_cast_qualification import cast_case, qualification
from tests.test_mechanism_qualification import refresh


def _v2_qualification():
    descriptor, args = cast_case(True, "post")
    claim = qualification(args)
    claim["parent"].update(
        profile_kind="successful_action_v2",
        required_receipts=["cast_identity", "action_admission"],
    )
    refresh(descriptor, args)
    return descriptor, args


def test_v2_qualification_requires_exact_ordered_receipts_and_v1_is_unchanged():
    _, args = _v2_qualification()
    claim = qualification(args)
    assert validate_execution_qualification(claim) == claim

    for receipts in (
        ["cast_identity"],
        ["action_admission", "cast_identity"],
        ["cast_identity", "action_admission", "hit_parameter"],
    ):
        changed = copy.deepcopy(claim)
        changed["parent"]["required_receipts"] = receipts
        with pytest.raises(ValueError):
            validate_execution_qualification(changed)

    _, old_args = cast_case(True, "post")
    old = qualification(old_args)
    assert old["parent"]["profile_kind"] == "successful_action_v1"
    assert old["parent"]["required_receipts"] == ["cast_identity"]
    assert validate_execution_qualification(old) == old


def _admission_trigger():
    return {
        "event": "action_start",
        "phase": "pre",
        "action_ids": ["cast"],
        "action_admission": {"contract": admission.CONTRACT},
    }


def test_v2_engine_rule_only_observes_committed_action_admission():
    template, rotation, mechanics = admission_fixture()
    add(mechanics, rule(trigger=_admission_trigger()))
    assert validate_mechanics_model(mechanics)["status"] == "valid"
    output = execute_mechanics(template, rotation, mechanics)
    parent = next(row for row in output["rotation_check"]["event_log"]
                  if row.get("action_id") == "cast" and "action_admission" in row)
    activation = next(row for row in output["mechanics_trace"] if row["type"] == "activation")
    assert parent["action_admission"]["status"] == "committed"
    assert activation["action_admission_certificate"] == parent["action_admission"]["certificate"]

    # A legacy action with the same identity cannot satisfy the private filter.
    legacy_template, legacy_rotation, legacy_mechanics = fixture((0.2,))
    add(legacy_mechanics, rule(trigger=_admission_trigger()))
    legacy = execute_mechanics(legacy_template, legacy_rotation, legacy_mechanics)
    assert not any(row["type"] == "activation" for row in legacy["mechanics_trace"])

    # A marked action that fails an actual game gate also cannot activate it.
    failed_template, failed_rotation, failed_mechanics = admission_fixture(initial_energy=0)
    add(failed_mechanics, rule(trigger=_admission_trigger()))
    failed = execute_mechanics(failed_template, failed_rotation, failed_mechanics)
    rejected = next(row for row in failed["rotation_check"]["event_log"]
                    if row.get("action_id") == "cast" and "action_admission" in row)
    assert rejected["action_admission"]["status"] == "rejected"
    assert not any(row["type"] == "activation" for row in failed["mechanics_trace"])


def _admission_compilation(plan, snapshot):
    ability = plan["rotation_model"]["abilities"][0]
    selection = {
        "character_id": 71,
        "skill_depot_id": 81,
        "skill_id": 93,
        "slot": "burst",
        "constellation": 0,
    }
    model_ref = {"id": "fixture.action-admission", "revision": 1}
    profile_source_fingerprint = "c" * 64
    context = {"fixture": True}
    binding = {
        "id": "admission.q",
        "ability_id": "e",
        "version_line": "release",
        "selection": copy.deepcopy(selection),
        "variant": "default",
        "model_ref": copy.deepcopy(model_ref),
        "accepted_fingerprint": None,
    }
    plan["action_admission_context"] = copy.deepcopy(context)
    plan["action_admission_sources"] = [binding]
    compiled_values = {
        "cooldown": copy.deepcopy(ability["cooldown"]),
        "energy": copy.deepcopy(ability["energy"]),
    }
    initial = {
        "character": "a",
        "group": "skill:81:93",
        "ready_in": [],
    }
    scope = {
        "compiler": "reviewed-action-admission-compiler-v1",
        "binding": {key: copy.deepcopy(value) for key, value in binding.items()
                    if key != "accepted_fingerprint"},
        "ability": {key: copy.deepcopy(ability.get(key)) for key in
                    ("id", "character", "action_type_id", "variant", "status", "source")},
        "action_ids": ["cast"],
        "initial_cooldown": copy.deepcopy(initial),
        "context": copy.deepcopy(context),
        "aliases": [],
        "capacity_dependencies": [],
        "profile_source_fingerprint": profile_source_fingerprint,
    }
    scope_fingerprint = fingerprint(scope)
    source_fingerprint = fingerprint({
        "profile_source_fingerprint": profile_source_fingerprint,
        "scope_fingerprint": scope_fingerprint,
    })
    binding["accepted_fingerprint"] = source_fingerprint
    model_receipt = {"fixture": True}
    resource_fingerprint = "e" * 64
    certificate = {
        "contract": "static-action-admission-certificate-v1",
        "binding_id": "admission.q",
        "model_ref": copy.deepcopy(model_ref),
        "model_receipt": copy.deepcopy(model_receipt),
        "snapshot_id": snapshot["snapshot_id"],
        "resource_fingerprint": resource_fingerprint,
        "source_fingerprint": source_fingerprint,
        "scope_fingerprint": scope_fingerprint,
        "selection": copy.deepcopy(selection),
        "variant": "default",
        "action_type_id": "action.elemental_burst",
        "field_authority": {},
        "compiled_values": copy.deepcopy(compiled_values),
        "declared_not_verified": [],
        "ability_id": "e",
        "action_ids": ["cast"],
        "initial_cooldown": copy.deepcopy(initial),
        "context": copy.deepcopy(context),
        "aliases": [],
        "capacity_dependencies": [],
    }
    certificate["static_certificate_fingerprint"] = fingerprint(certificate)
    marker = {
        "contract": "static-action-admission-source-v1",
        "binding_id": "admission.q",
        "model_ref": copy.deepcopy(model_ref),
        "source_fingerprint": source_fingerprint,
        "static_certificate_fingerprint": certificate["static_certificate_fingerprint"],
    }
    ability["action_admission_source"] = copy.deepcopy(marker)
    record = {
        "id": "admission.q",
        "ability_id": "e",
        "actor": "a",
        "compiler": "reviewed-action-admission-compiler-v1",
        "status": "ready",
        "selection": selection,
        "variant": "default",
        "action_type_id": "action.elemental_burst",
        "model_ref": model_ref,
        "context": copy.deepcopy(context),
        "issues": [],
        "profile": {
            "status": "ready",
            "snapshot": copy.deepcopy(snapshot),
            "selection": copy.deepcopy(selection),
            "variant": "default",
            "action_type_id": "action.elemental_burst",
            "model_ref": copy.deepcopy(model_ref),
            "model_receipt": copy.deepcopy(model_receipt),
            "resource_profile": {"resource_fingerprint": resource_fingerprint},
            "field_authority": {},
            "declared_not_verified": [],
            "compiled_values": copy.deepcopy(compiled_values),
            "source_fingerprint": profile_source_fingerprint,
        },
        "required_confirmation": {"accepted_fingerprint": source_fingerprint},
        "source_fingerprint": source_fingerprint,
        "scope_fingerprint": scope_fingerprint,
        "static_certificate": certificate,
        "compiled_values": {**compiled_values, "action_admission_source": marker},
    }
    record["compilation_fingerprint"] = fingerprint(record)
    return record


def _v2_scene():
    plan, template, material, requirements = v1_scene()
    fact = next(row for row in material["facts"] if row["kind"] == "execution_qualification")
    fact["claim"]["parent"].update(
        profile_kind="successful_action_v2",
        required_receipts=["cast_identity", "action_admission"],
    )
    refresh(material["descriptor"], {
        "source_proof": material["source_proof"],
        "facts": material["facts"],
        "parameters": material["resolved_parameters"],
    })

    rotation = plan["rotation_model"]
    rotation["characters"][0].update(element="cryo", energy_capacity=10, initial_energy=10)
    ability = rotation["abilities"][0]
    ability.update(
        variant="default",
        cooldown={"group": "skill:81:93", "duration": 15, "charges": 1,
                  "start": "action_start", "recovery": "parallel"},
        energy={"required": 10, "amount": 10, "delivery": "action_start"},
    )
    rotation["events"] = []
    rotation["initial_cooldowns"] = [
        {"character": "a", "group": "skill:81:93", "ready_in": []}
    ]
    record = _admission_compilation(plan, material["snapshot"])
    return plan, template, material, requirements, record


def _compose_v2(plan, template, material, requirements, admission_record):
    issues = []
    admission_records = admission_record if isinstance(admission_record, list) else [admission_record]
    model, records, sources = compile_mechanism_sources(
        plan,
        template,
        None,
        action_admission_compilations=admission_records,
        equipment_requirements=requirements,
        missing=lambda *args: issues.append(args),
        source_provider=lambda binding: copy.deepcopy(material),
        cast_identity_provider=identity,
    )
    return model, records, sources, issues


def _agent_materialized_v2():
    plan, template, material, requirements, admission_record = _v2_scene()
    model, records, _, issues = _compose_v2(
        plan, template, material, requirements, admission_record)
    candidate = records[0]
    assert model is None and issues
    assert candidate["execution_authority"] == "none"
    assert candidate["required_confirmation"] is None
    assert candidate["required_materialization"]["contract"] == (
        "mechanism-materialization-candidate-v1")

    execution_plan = {
        "kind": "genshin-plan",
        "plan_version": 3,
        "plan_id": "plan.successful-action-v2",
        "revision": 1,
        "manual_template": copy.deepcopy(template),
        "equipment_inputs": copy.deepcopy(plan["equipment_inputs"]),
        "effect_bindings": copy.deepcopy(plan["effect_bindings"]),
        "rotation_model": copy.deepcopy(plan["rotation_model"]),
        "mechanics_model": copy.deepcopy(plan["mechanics_model"]),
        "extensions": {},
        "resolution": {"state": "pending"},
        "resolved_template": None,
    }
    topology = {
        "contract_id": CONTRACT_ID,
        "schema_version": 1,
        "topology_id": "topology.successful-action-v2",
        "authority": AUTHORITY,
        "declaration": {
            "character_semantics": "agent_declared",
            "completeness": "declared_complete",
            "note": "通用动作接纳触发工程夹具",
        },
        "action_checkpoint": {
            "revision": 1,
            "fingerprint": "a" * 64,
            "action_ids": ["cast"],
        },
        "dependencies": [],
        "sources": [{
            "source_id": "source.agent-v2",
            "category": "artifact",
            "owner_ref": "a",
            "description": "Agent核实后的通用动作接纳触发",
            "disposition": "modeled",
            "node_ids": ["node.action", "node.rule"],
            "reason": None,
            "source_refs": [{
                "kind": "agent_research",
                "reference": "fixture://successful-action-v2",
                "fingerprint": candidate["candidate_fingerprint"],
                "status": "verified",
            }],
        }],
        "nodes": [{
            "node_id": "node.action",
            "kind": "action_anchor",
            "status": "bound",
            "schedule": {
                "kind": "point",
                "anchor": {"kind": "action", "ref_id": "cast", "phase": "start"},
                "offset_seconds": 0.0,
                "order": 0,
            },
            "source_ids": ["source.agent-v2"],
            "parameter_slots": [],
            "note": "",
        }, {
            "node_id": "node.rule",
            "kind": "buff_interval",
            "status": "bound",
            "schedule": {
                "kind": "point",
                "anchor": {"kind": "node", "ref_id": "node.action", "phase": "point"},
                "offset_seconds": 0.05,
                "order": 0,
            },
            "source_ids": ["source.agent-v2"],
            "parameter_slots": [],
            "note": "",
        }],
        "edges": [],
    }
    document = new_agent_execution_materialization(
        execution_plan, topology, "materialization.successful-action-v2")
    document["sources"] = [{
        "source_id": "source.agent-v2",
        "kind": "agent_research",
        "reference": "fixture://successful-action-v2",
        "fingerprint": candidate["candidate_fingerprint"],
        "status": "verified",
        "note": "知识候选仅作为Agent核实材料，不取得执行权",
    }]
    rule = copy.deepcopy(candidate["preview_rules"][0])
    rule.update(source_id="part.agent-v2", status="assumed",
                source="Agent自包含强类型材料化夹具")
    document["entries"] = [{
        "entry_id": "rules",
        "family": "mechanics_rule_bundle",
        "handler_version": 1,
        "node_ids": ["node.rule"],
        "source_ids": ["source.agent-v2"],
        "coverage_keys": ["coverage.successful-action-v2"],
        "payload": {
            "bundle_id": "bundle.successful-action-v2",
            "rules": [rule],
            "initial_state": {"rules": [{
                "rule_id": rule["id"],
                "cooldown_remaining": 0,
                "instances": [],
            }]},
            "resource_ownership": [],
            "parts": [{
                "part_id": "part.agent-v2",
                "rule_id": rule["id"],
                "node_ids": ["node.rule"],
                "source_ids": ["source.agent-v2"],
                "note": "Agent明确发布的动作接纳触发规则",
            }],
        },
        "note": "",
    }]
    materialization_receipt = lint_agent_execution_materialization(
        execution_plan, topology, document)
    assert materialization_receipt["status"] == "ready", materialization_receipt["issues"]
    effective = materialize_agent_execution(execution_plan, topology, document)
    runtime = adapt_agent_execution_runtime(
        execution_plan,
        topology,
        effective,
        template,
        execution_plan["rotation_model"],
        execution_plan["mechanics_model"],
    )
    assert runtime["status"] == "complete", runtime["receipt"]
    return plan, template, admission_record, candidate, runtime


def test_knowledge_candidate_and_agent_bundle_preserve_private_admission_filter():
    _, _, _, candidate, runtime = _agent_materialized_v2()
    assert candidate["parent_graph"]["verification_scope"] == (
        "cast_and_action_admission_graph")
    assert candidate["scope"]["identity"]["action_admission"] == [
        candidate["parent_graph"]["dependencies"][0]["action_admission"]
    ]
    assert candidate["preview_rules"][0]["trigger"]["action_admission"] == {
        "contract": admission.CONTRACT
    }
    installed = runtime["mechanics_model"]["rules"][-1]
    assert installed["trigger"]["action_admission"] == {
        "contract": admission.CONTRACT
    }
    receipt = runtime["receipt"]["entries"][0]
    assert receipt["family"] == "mechanics_rule_bundle"
    assert receipt["status"] == "installed"


@pytest.mark.parametrize("fault", ["missing", "stale_initial", "wrong_compiler"])
def test_app_materializer_keeps_unverifiable_v2_admission_pending(fault):
    plan, template, material, requirements, admission_record = _v2_scene()
    records = [admission_record]
    if fault == "missing":
        records = []
    elif fault == "stale_initial":
        plan["rotation_model"]["initial_cooldowns"][0]["ready_in"] = [1]
    else:
        admission_record["compiler"] = "untrusted-compiler"
        admission_record["compilation_fingerprint"] = fingerprint({
            key: value for key, value in admission_record.items()
            if key != "compilation_fingerprint"
        })
    model, compiled, _, issues = _compose_v2(
        plan, template, material, requirements, records)
    assert model is None and issues
    assert compiled[0]["status"] == "pending"


def test_agent_materialized_v2_rule_only_observes_committed_action_admission():
    _, template, _, _, runtime = _agent_materialized_v2()
    executed = execute_mechanics(
        template, runtime["rotation_model"], runtime["mechanics_model"])
    assert executed["status"] in {"passed", "unproven"}
    activation = next(row for row in executed["mechanics_trace"]
                      if row["type"] == "activation")
    assert activation["action_admission_certificate"]

    rejected_rotation = copy.deepcopy(runtime["rotation_model"])
    rejected_rotation["characters"][0]["initial_energy"] = 0
    rejected = execute_mechanics(
        template, rejected_rotation, runtime["mechanics_model"])
    admission_row = next(
        row for row in rejected["rotation_check"]["event_log"]
        if row.get("action_id") == "cast" and "action_admission" in row)
    assert admission_row["action_admission"]["status"] == "rejected"
    assert not any(row["type"] == "activation"
                   for row in rejected["mechanics_trace"])
