from __future__ import annotations

import copy
import json

import pytest
from test_agent_execution_materialization import (
    _document,
    _plan,
    _rule_bundle_payload,
    _runtime_base,
    _runtime_document,
    _runtime_topology,
    _topology,
)

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
    agent_execution_runtime_capabilities,
)
from genshin_app.equipment.event_topology import AUTHORITY as TOPOLOGY_AUTHORITY
from genshin_app.equipment.event_topology import CONTRACT_ID as TOPOLOGY_CONTRACT_ID
from genshin_app.equipment.event_topology_binding import (
    lint_event_topology_bindings,
    new_event_topology_bindings,
)
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.equipment.schema import new_plan
from genshin_app.equipment.service import result_bytes, run_plan_calc
from genshin_engine.mechanics import execute_mechanics
from genshin_engine.state_runtime_mechanics import route_event_id


def _source() -> dict:
    return {
        "source_id": "source.skill",
        "kind": "agent_research",
        "reference": "fixture://typed-family",
        "fingerprint": "c" * 64,
        "status": "verified",
        "note": "通用强类型机制夹具",
    }


def _buff_payload() -> dict:
    return {
        "bundle_id": "bundle.buff.fixture",
        "rules": [{
            "id": "rule.buff.fixture",
            "name_zh": "通用团队增伤",
            "owner": "actor.fixture",
            "source_id": "part.buff.fixture",
            "trigger": {
                "event": "action_start",
                "phase": "pre",
                "action_ids": ["action.skill"],
            },
            "beneficiaries": {"mode": "team", "scope": "locked"},
            "timing": {
                "delay": 0,
                "duration": 10,
                "cooldown": 0,
                "cooldown_anchor": "success",
            },
            "stacking": {"max": 1, "add": 1, "policy": "refresh"},
            "effects": [{
                "id": "effect.buff.fixture",
                "benefit_type_id": "benefit.dmg_bonus.cryo",
                "value": 0.2,
                "domain": "self",
            }],
            "status": "assumed",
            "source": "Agent材料化工程夹具",
        }],
        "initial_state": {"rules": [{
            "rule_id": "rule.buff.fixture",
            "cooldown_remaining": 0,
            "instances": [],
        }]},
        "resource_ownership": [],
        "parts": [{
            "part_id": "part.buff.fixture",
            "rule_id": "rule.buff.fixture",
            "node_ids": ["node.buff"],
            "source_ids": ["source.skill"],
            "note": "强类型收益规则",
        }],
    }


def _state_payload() -> dict:
    rule_id = "rule.state.fixture"
    return {
        "bundle_id": "bundle.state.fixture",
        "rules": [{
            "id": rule_id,
            "name_zh": "有限状态建立收益",
            "owner": "actor.fixture",
            "source_id": "part.state.fixture",
            "trigger": {
                "event": "condition",
                "phase": "pre",
                "event_ids": [route_event_id("dispatch_rule", rule_id)],
            },
            "beneficiaries": {"mode": "self", "scope": "locked"},
            "timing": {
                "delay": 0,
                "duration": 1,
                "cooldown": 0,
                "cooldown_anchor": "success",
            },
            "stacking": {"max": 1, "add": 1, "policy": "refresh"},
            "effects": [{
                "id": "effect.state.fixture",
                "benefit_type_id": "benefit.dmg_bonus.all",
                "value": 0.1,
                "domain": "self",
            }],
            "status": "assumed",
            "source": "Agent材料化工程夹具",
        }],
        "initial_state": {"rules": [{
            "rule_id": rule_id,
            "cooldown_remaining": 0,
            "instances": [],
        }]},
        "resource_ownership": [],
        "parts": [{
            "part_id": "part.state.fixture",
            "rule_id": rule_id,
            "node_ids": ["node.state"],
            "source_ids": ["source.skill"],
            "note": "有限生命周期规则",
        }],
        "state_runtime": {
            "schema_version": 1,
            "lifecycles": [{
                "id": "lifecycle.state.fixture",
                "name_zh": "有限状态",
                "owner": "actor.fixture",
                "source_id": "part.state.fixture",
                "start": {
                    "event": "action_start",
                    "phase": "pre",
                    "action_ids": ["action.skill"],
                },
                "start_order": 1,
                "establishment_order": 2,
                "end_order": 3,
                "establishment_delay_us": 0,
                "duration_us": 1_000_000,
                "restart": "replace",
                "pending_cancel": "never_after_success",
                "hooks": {
                    "create": [],
                    "established": [{
                        "kind": "dispatch_rule",
                        "ref_id": rule_id,
                        "order": 4,
                    }],
                    "end": [],
                },
                "status": "assumed",
                "source": "Agent材料化工程夹具",
            }],
            "timers": [],
        },
    }


def _typed_document() -> tuple[dict, dict, dict]:
    plan, topology = _plan(), _topology()
    document = new_agent_execution_materialization(
        plan, topology, "materialization.typed.fixture")
    document["sources"] = [_source()]
    document["entries"] = [
        {
            "entry_id": "buff.typed",
            "family": "buff",
            "handler_version": 2,
            "node_ids": ["node.buff"],
            "source_ids": ["source.skill"],
            "coverage_keys": ["coverage.buff.typed"],
            "payload": _buff_payload(),
            "note": "",
        },
        {
            "entry_id": "state.typed",
            "family": "state",
            "handler_version": 2,
            "node_ids": ["node.state"],
            "source_ids": ["source.skill"],
            "coverage_keys": ["coverage.state.typed"],
            "payload": _state_payload(),
            "note": "",
        },
    ]
    return plan, topology, document


def _runtime_typed_case(family: str, *, handler_version: int = 2):
    plan, template, rotation, mechanics = _runtime_base()
    node_kind = "buff_interval" if family == "buff" else "state"
    node_id = "node." + family
    topology = {
        "contract_id": TOPOLOGY_CONTRACT_ID,
        "schema_version": 1,
        "topology_id": "topology.typed." + family,
        "authority": TOPOLOGY_AUTHORITY,
        "declaration": {
            "character_semantics": "agent_declared",
            "completeness": "declared_complete",
            "note": "typed runtime fixture",
        },
        "action_checkpoint": {
            "revision": 1,
            "fingerprint": "a" * 64,
            "action_ids": ["action.skill"],
        },
        "dependencies": [],
        "sources": [{
            "source_id": "source.skill",
            "category": "talent",
            "owner_ref": "actor",
            "description": "typed runtime fixture",
            "disposition": "modeled",
            "node_ids": ["node.action", node_id],
            "reason": None,
            "source_refs": [{
                "kind": "agent_research",
                "reference": "fixture",
                "fingerprint": "c" * 64,
                "status": "verified",
            }],
        }],
        "nodes": [{
            "node_id": "node.action",
            "kind": "action_anchor",
            "status": "bound",
            "schedule": {
                "kind": "point",
                "anchor": {"kind": "action", "ref_id": "action.skill", "phase": "start"},
                "offset_seconds": 0.0,
                "order": 0,
            },
            "source_ids": ["source.skill"],
            "parameter_slots": [],
            "note": "",
        }, {
            "node_id": node_id,
            "kind": node_kind,
            "status": "bound",
            "schedule": {
                "kind": "point",
                "anchor": {"kind": "node", "ref_id": "node.action", "phase": "point"},
                "offset_seconds": 0.0,
                "order": 1,
            },
            "source_ids": ["source.skill"],
            "parameter_slots": [],
            "note": "",
        }],
        "edges": [{
            "edge_id": "edge.action-" + family,
            "kind": "triggers",
            "from_node_id": "node.action",
            "to_node_id": node_id,
            "status": "bound",
            "source_ids": ["source.skill"],
            "note": "",
        }],
    }
    bindings = new_event_topology_bindings(topology)
    plan["extensions"] = {
        "event_topology": topology,
        "event_topology_bindings": bindings,
    }
    document = new_agent_execution_materialization(
        plan, topology, "materialization.typed." + family)
    document["sources"] = [_source()]
    if handler_version == 2:
        payload = _buff_payload() if family == "buff" else _state_payload()
        payload["rules"][0]["owner"] = "actor"
        if family == "state":
            payload["state_runtime"]["lifecycles"][0]["owner"] = "actor"
    elif family == "buff":
        payload = {
            "effect_id": "legacy.buff",
            "target_scope": "owner",
            "start_offset_seconds": 0.0,
            "duration_seconds": 1.0,
            "refresh_policy": "replace",
            "operations": [{
                "stat": "dmg_bonus",
                "operation": "add_ratio",
                "value": 0.1,
                "target_ref": "actor",
            }],
        }
    else:
        payload = {
            "state_key": "legacy.state",
            "operation": "set",
            "value_type": "boolean",
            "value": True,
            "target_ref": "actor",
            "duration_seconds": 1.0,
            "refresh_policy": "replace",
        }
    document["entries"] = [{
        "entry_id": family + ".typed",
        "family": family,
        "handler_version": handler_version,
        "node_ids": [node_id],
        "source_ids": ["source.skill"],
        "coverage_keys": ["coverage." + family],
        "payload": payload,
        "note": "",
    }]
    return plan, topology, bindings, document, template, rotation, mechanics


def _add_second_state_entry(plan, topology, document, *, collide_lifecycle: bool):
    topology["nodes"].append({
        "node_id": "node.state.2",
        "kind": "state",
        "status": "bound",
        "schedule": {
            "kind": "point",
            "anchor": {"kind": "node", "ref_id": "node.action", "phase": "point"},
            "offset_seconds": 0.1,
            "order": 2,
        },
        "source_ids": ["source.skill"],
        "parameter_slots": [],
        "note": "",
    })
    topology["sources"][0]["node_ids"].append("node.state.2")
    topology["edges"].append({
        "edge_id": "edge.action-state-2",
        "kind": "triggers",
        "from_node_id": "node.action",
        "to_node_id": "node.state.2",
        "status": "bound",
        "source_ids": ["source.skill"],
        "note": "",
    })
    plan["extensions"]["event_topology"] = topology
    plan["extensions"]["event_topology_bindings"] = new_event_topology_bindings(topology)
    rebuilt = new_agent_execution_materialization(
        plan, topology, "materialization.typed.state.two")
    rebuilt["sources"] = copy.deepcopy(document["sources"])
    first = copy.deepcopy(document["entries"][0])
    second = copy.deepcopy(first)
    second.update(
        entry_id="state.typed.2",
        node_ids=["node.state.2"],
        coverage_keys=["coverage.state.2"],
    )
    payload = second["payload"]
    payload["bundle_id"] = "bundle.state.fixture.2"
    rule = payload["rules"][0]
    rule["id"] = "rule.state.fixture.2"
    rule["source_id"] = "part.state.fixture.2"
    rule["trigger"]["event_ids"] = [
        route_event_id("dispatch_rule", rule["id"])]
    payload["initial_state"]["rules"][0]["rule_id"] = rule["id"]
    part = payload["parts"][0]
    part.update(
        part_id="part.state.fixture.2",
        rule_id=rule["id"],
        node_ids=["node.state.2"],
    )
    lifecycle = payload["state_runtime"]["lifecycles"][0]
    if not collide_lifecycle:
        lifecycle["id"] = "lifecycle.state.fixture.2"
    lifecycle["source_id"] = part["part_id"]
    lifecycle.update(start_order=11, establishment_order=12, end_order=13)
    lifecycle["hooks"]["established"][0]["order"] = 14
    lifecycle["hooks"]["established"][0]["ref_id"] = rule["id"]
    rebuilt["entries"] = [first, second]
    return rebuilt


def test_typed_buff_and_state_v2_materialize_without_database_or_role_dispatch():
    plan, topology, document = _typed_document()
    assert validate_agent_execution_materialization(document) is document
    receipt = lint_agent_execution_materialization(plan, topology, document)
    assert receipt["status"] == "ready"
    assert receipt["database_access"] == "none"

    effective = materialize_agent_execution(plan, topology, document)
    assert [(row["family"], row["handler"]["version"])
            for row in effective["entries"]] == [("buff", 2), ("state", 2)]
    assert "result" not in effective and "dps" not in effective


@pytest.mark.parametrize("invalid_version", [True, 1.0])
def test_handler_version_requires_an_exact_json_integer(invalid_version):
    _, _, document = _typed_document()
    document["entries"][0]["handler_version"] = invalid_version

    with pytest.raises(AgentExecutionMaterializationError) as error:
        validate_agent_execution_materialization(document)

    assert error.value.code == "agent_materialization_handler_version_invalid"


def _dispatch_child_case(parent_family: str, child_family: str):
    periodic = parent_family == "periodic_channel"
    plan, template, rotation, mechanics = _runtime_base()
    topology = _runtime_topology(periodic=periodic)
    parent_node = "node.periodic" if periodic else "node.sequence"
    child_node = (
        "node.rule" if child_family == "mechanics_rule_bundle"
        else "node." + child_family
    )
    child_kind = "state" if child_family == "state" else "buff_interval"
    topology["nodes"].append({
        "node_id": child_node,
        "kind": child_kind,
        "status": "bound",
        "schedule": {
            "kind": "point",
            "anchor": {"kind": "node", "ref_id": parent_node, "phase": "point"},
            "offset_seconds": 0.0,
            "order": 9,
        },
        "source_ids": ["source.skill"],
        "parameter_slots": [],
        "note": "",
    })
    topology["sources"][0]["node_ids"].append(child_node)
    topology["edges"].append({
        "edge_id": f"edge.{parent_family}-{child_family}",
        "kind": "causes",
        "from_node_id": parent_node,
        "to_node_id": child_node,
        "status": "bound",
        "source_ids": ["source.skill"],
        "note": "",
    })
    document = _runtime_document(plan, topology, periodic=periodic)
    parent = next(
        row for row in document["entries"] if row["family"] == parent_family)
    groups = (
        parent["payload"]["dispatches"]
        if periodic else parent["payload"]["steps"]
    )
    groups[0]["emits_node_ids"].append(child_node)
    if child_family == "buff":
        payload = _buff_payload()
        payload["rules"][0]["owner"] = "actor"
        entry_version = 2
    elif child_family == "state":
        payload = _state_payload()
        payload["rules"][0]["owner"] = "actor"
        payload["state_runtime"]["lifecycles"][0]["owner"] = "actor"
        entry_version = 2
    else:
        payload = _rule_bundle_payload()
        entry_version = 1
    document["entries"].append({
        "entry_id": child_family + ".child",
        "family": child_family,
        "handler_version": entry_version,
        "node_ids": [child_node],
        "source_ids": ["source.skill"],
        "coverage_keys": ["coverage." + child_family + ".child"],
        "payload": payload,
        "note": "",
    })
    return plan, topology, document, template, rotation, mechanics


@pytest.mark.parametrize("parent_family", ["finite_sequence", "periodic_channel"])
@pytest.mark.parametrize("child_family", ["buff", "state", "mechanics_rule_bundle"])
def test_failed_dispatch_parent_atomically_blocks_every_typed_child(
        parent_family, child_family):
    case = _dispatch_child_case(parent_family, child_family)
    plan, topology, document, template, rotation, mechanics = case
    effective = materialize_agent_execution(plan, topology, document)

    runtime = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)
    by_id = {row["entry_id"]: row for row in runtime["receipt"]["entries"]}

    assert by_id[parent_family.removesuffix("_channel")
                 if parent_family == "periodic_channel" else "sequence"]["status"] in {
        "partial", "unsupported",
    }
    child = by_id[child_family + ".child"]
    assert child["status"] == "partial"
    assert child["installed"] == []
    assert child["issues"][0]["code"] == (
        "agent_materialization_parent_dispatch_uninstalled")
    assert runtime["mechanics_model"].get("rules", []) == []
    assert runtime["mechanics_model"].get("state_runtime") is None


def test_legacy_v1_buff_and_state_remain_storable_but_versions_are_explicit():
    plan, topology = _plan(), _topology()
    legacy = _document(plan, topology)
    assert validate_agent_execution_materialization(legacy) is legacy

    capabilities = agent_execution_materialization_capabilities()["families"]
    assert capabilities["buff"]["handler_version"] == 2
    assert capabilities["buff"]["supported_handler_versions"] == [1, 2]
    assert capabilities["state"]["handler_version"] == 2
    assert capabilities["state"]["supported_handler_versions"] == [1, 2]

    runtime = agent_execution_runtime_capabilities()["family_versions"]
    assert runtime["buff"]["1"]["status"] == "unsupported"
    assert runtime["buff"]["2"]["status"] == "supported"
    assert runtime["state"]["1"]["status"] == "unsupported"
    assert runtime["state"]["2"]["status"] == "supported"


@pytest.mark.parametrize("family", ["buff", "state"])
def test_typed_family_v2_installs_atomically_in_generic_runtime(family):
    plan, topology, _, document, template, rotation, mechanics = _runtime_typed_case(family)
    effective = materialize_agent_execution(plan, topology, document)

    result = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)

    assert result["status"] == "complete"
    assert result["receipt"]["entries"][0]["status"] == "installed"
    assert result["mechanics_model"]["rules"][0]["owner"] == "actor"
    if family == "buff":
        assert "state_runtime" not in result["mechanics_model"]
    else:
        assert result["mechanics_model"]["state_runtime"]["lifecycles"][0][
            "id"] == "lifecycle.state.fixture"


@pytest.mark.parametrize("family", ["buff", "state"])
def test_typed_family_v2_executes_in_the_real_mechanics_queue(family):
    plan, topology, _, document, template, rotation, mechanics = _runtime_typed_case(family)
    mechanics.pop("elemental_model", None)
    mechanics["hit_events"] = [{
        "hit_id": "hit.fixture",
        "action_id": "action.skill",
        "order": 10,
    }]
    effective = materialize_agent_execution(plan, topology, document)
    runtime = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)

    calculated = execute_mechanics(
        runtime["template"], runtime["rotation_model"],
        runtime["mechanics_model"], [])

    assert calculated["status"] == "passed"
    assert calculated["result"]["hits"][0]["zones"]["dmg_bonus"] > 1
    if family == "state":
        assert calculated["result"]["state_runtime"]["status"] == "active"
        assert calculated["result"]["state_runtime"]["execution_trace"]


@pytest.mark.parametrize("family", ["buff", "state"])
def test_typed_family_v2_survives_composer_service_and_json_export(
        family, tmp_path):
    _, topology, _, document, template, rotation, mechanics = _runtime_typed_case(family)
    mechanics.pop("elemental_model", None)
    mechanics["hit_events"] = [{
        "hit_id": "hit.fixture",
        "action_id": "action.skill",
        "order": 10,
    }]
    plan = enable_mechanics(new_plan())
    plan["manual_template"] = template
    plan["rotation_model"] = rotation
    plan["mechanics_model"] = mechanics
    plan["extensions"] = {
        "event_topology": topology,
        "event_topology_bindings": new_event_topology_bindings(topology),
    }
    rebound = new_agent_execution_materialization(
        plan, topology, "materialization.typed.service." + family)
    rebound["sources"] = copy.deepcopy(document["sources"])
    rebound["entries"] = copy.deepcopy(document["entries"])
    plan["extensions"]["agent_execution_materialization"] = rebound

    output = run_plan_calc(plan, tmp_path / "missing.db")

    runtime_entry = output["report"]["agent_execution_runtime_receipt"][
        "entries"][0]
    assert runtime_entry["status"] == "installed"
    proof = output["result_receipt"]["agent_execution_proof"]
    assert proof["entries"][0]["family"] == family
    assert len(proof["entries"][0]["installed_fingerprint"]) == 64
    exported = json.loads(result_bytes(
        output["result"], output["result_receipt"], "json"))
    assert exported["result"]["agent_execution_runtime_receipt"] == output[
        "result"]["agent_execution_runtime_receipt"]
    if family == "state":
        assert exported["result"]["state_runtime"]["status"] == "active"


def test_state_v2_rejects_a_rule_outside_the_executable_clock_subset_early():
    _, _, document = _typed_document()
    state = next(row for row in document["entries"] if row["family"] == "state")
    state["payload"]["rules"][0]["timing"]["delay"] = 0.1

    with pytest.raises(AgentExecutionMaterializationError) as error:
        validate_agent_execution_materialization(document)

    assert error.value.code == "agent_materialization_state_rule_scope_unsupported"


def test_multiple_state_v2_entries_merge_only_when_ids_are_disjoint():
    plan, topology, _, document, template, rotation, mechanics = _runtime_typed_case("state")
    document = _add_second_state_entry(
        plan, topology, document, collide_lifecycle=False)
    effective = materialize_agent_execution(plan, topology, document)

    runtime = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)

    assert runtime["status"] == "complete"
    assert [row["status"] for row in runtime["receipt"]["entries"]] == [
        "installed", "installed"]
    assert {row["id"] for row in runtime["mechanics_model"]["state_runtime"][
        "lifecycles"]} == {"lifecycle.state.fixture", "lifecycle.state.fixture.2"}


def test_state_v2_collision_is_atomic_for_the_failing_entry():
    plan, topology, _, document, template, rotation, mechanics = _runtime_typed_case("state")
    document = _add_second_state_entry(
        plan, topology, document, collide_lifecycle=True)
    effective = materialize_agent_execution(plan, topology, document)

    runtime = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)

    assert runtime["status"] == "partial"
    assert [row["status"] for row in runtime["receipt"]["entries"]] == [
        "installed", "partial"]
    assert [row["id"] for row in runtime["mechanics_model"]["rules"]] == [
        "rule.state.fixture"]
    assert [row["id"] for row in runtime["mechanics_model"]["state_runtime"][
        "lifecycles"]] == ["lifecycle.state.fixture"]


@pytest.mark.parametrize("family", ["buff", "state"])
def test_legacy_typed_family_is_storage_only_and_cannot_cover_topology(family):
    plan, topology, bindings, document, template, rotation, mechanics = _runtime_typed_case(
        family, handler_version=1)
    plan["extensions"]["agent_execution_materialization"] = document
    effective = materialize_agent_execution(plan, topology, document)

    runtime = adapt_agent_execution_runtime(
        plan, topology, effective, template, rotation, mechanics)
    assert runtime["status"] == "partial"
    assert runtime["receipt"]["entries"][0]["status"] == "unsupported"
    assert runtime["receipt"]["entries"][0]["issues"][0]["code"] == (
        "agent_materialization_runtime_handler_version_unsupported")

    coverage = lint_event_topology_bindings(plan, topology, bindings)
    assert coverage["status"] == "incomplete"
    assert any(issue["code"] == "event_topology_materialization_runtime_unsupported"
               for issue in coverage["issues"])


@pytest.mark.parametrize("family", ["buff", "state"])
def test_typed_families_reject_open_payload_and_calculated_output(family):
    _, _, document = _typed_document()
    entry = next(row for row in document["entries"] if row["family"] == family)

    open_payload = copy.deepcopy(document)
    next(row for row in open_payload["entries"] if row["family"] == family)[
        "payload"]["unknown"] = True
    with pytest.raises(AgentExecutionMaterializationError):
        validate_agent_execution_materialization(open_payload)

    calculated = copy.deepcopy(document)
    next(row for row in calculated["entries"] if row["family"] == family)[
        "payload"]["total_damage"] = 1
    with pytest.raises(AgentExecutionMaterializationError) as error:
        validate_agent_execution_materialization(calculated)
    assert error.value.code == "agent_materialization_result_payload"
    assert entry["handler_version"] == 2
