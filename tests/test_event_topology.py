import copy
import json

import pytest

from genshin_app.equipment.event_topology import (
    AUTHORITY,
    CONTRACT_ID,
    MAX_NODES,
    MAX_SOURCES,
    EventTopologyError,
    _graph_cycles,
    event_topology_capabilities,
    event_topology_fingerprint,
    lint_event_topology,
    new_event_topology,
    validate_event_topology,
)


def _source(node_ids, *, disposition="modeled", reference="private://skill-evidence"):
    return {
        "source_id": "source.skill",
        "category": "talent",
        "owner_ref": "character.fixture",
        "description": "Agent-declared skill source.",
        "disposition": disposition,
        "node_ids": list(node_ids),
        "reason": None,
        "source_refs": [{
            "kind": "agent_research",
            "reference": reference,
            "fingerprint": "b" * 64,
            "status": "verified",
        }],
    }


def _point(anchor_kind, ref_id, *, offset=0.0):
    return {"kind": "point", "anchor": {"kind": anchor_kind, "ref_id": ref_id,
        "phase": "start" if anchor_kind == "action" else "point"},
        "offset_seconds": offset, "order": 0}


def _slot(*, status="bound", required_for="calculation", value=1.234567):
    return {"slot_id": "slot.multiplier", "semantic": "damage.multiplier",
        "value_type": "number", "required_for": required_for, "status": status,
        "value": value if status == "bound" else None, "unit": "ratio",
        "source_ids": ["source.skill"] if status == "bound" else [], "note": ""}


def _topology(*, completeness="declared_complete", slot=None):
    node_ids = ["node.action", "node.hit"]
    return {
        "contract_id": CONTRACT_ID,
        "schema_version": 1,
        "topology_id": "topology.fixture",
        "authority": AUTHORITY,
        "declaration": {"character_semantics": "agent_declared",
            "completeness": completeness, "note": "Fixture declaration."},
        "action_checkpoint": {"revision": 3, "fingerprint": "a" * 64,
            "action_ids": ["action.skill"]},
        "dependencies": [{"dependency_id": "dependency.database", "kind": "database_snapshot",
            "fingerprint": "c" * 64, "required": True}],
        "sources": [_source(node_ids)],
        "nodes": [
            {"node_id": "node.action", "kind": "action_anchor", "status": "bound",
             "schedule": _point("action", "action.skill"),
             "source_ids": ["source.skill"], "parameter_slots": [], "note": ""},
            {"node_id": "node.hit", "kind": "damage", "status": "bound",
             "schedule": _point("node", "node.action", offset=.5),
             "source_ids": ["source.skill"], "parameter_slots": [slot or _slot()], "note": ""},
        ],
        "edges": [{"edge_id": "edge.action-hit", "kind": "causes",
            "from_node_id": "node.action", "to_node_id": "node.hit", "status": "bound",
            "source_ids": ["source.skill"], "note": ""}],
    }


def test_complete_topology_is_numeric_ready_but_never_executable():
    topology = _topology()
    assert validate_event_topology(topology) is topology
    receipt = lint_event_topology(topology)
    assert receipt["status"] == "numeric_ready"
    assert receipt["stages"] == {
        "topology": {"status": "passed", "blocking_issue_count": 0},
        "numeric_binding": {"status": "passed", "blocking_issue_count": 0},
    }
    assert receipt["authority"] == "diagnostic_only"
    assert receipt["character_semantics"] == "agent_declared_unverified"
    assert receipt["active_node_kinds"] == ["action_anchor", "damage"]
    assert receipt["active_node_kind_counts"] == {"action_anchor": 1, "damage": 1}
    assert "can_execute" not in receipt and "result" not in receipt
    assert len(receipt["receipt_fingerprint"]) == 64


def test_calculation_parameter_can_remain_pending_after_topology_closes():
    topology = _topology(slot=_slot(status="pending", value=None))
    receipt = lint_event_topology(topology)
    assert receipt["status"] == "topology_ready"
    assert receipt["stages"]["topology"]["status"] == "passed"
    assert receipt["stages"]["numeric_binding"]["status"] == "blocked"
    assert [(row["code"], row["stage"]) for row in receipt["issues"]] == [
        ("event_parameter_pending", "calculation")]


def test_topology_parameter_blocks_declared_topology_but_warns_in_draft():
    complete = _topology(slot=_slot(status="pending", required_for="topology", value=None))
    receipt = lint_event_topology(complete)
    assert receipt["status"] == "draft"
    assert receipt["issues"][0]["severity"] == "error"

    draft = _topology(completeness="draft", slot=_slot(
        status="pending", required_for="topology", value=None))
    receipt = lint_event_topology(draft)
    parameter = next(row for row in receipt["issues"] if row["code"] == "event_parameter_pending")
    assert parameter["severity"] == "warning"
    assert any(row["code"] == "event_topology_declaration_draft" for row in receipt["issues"])


def test_result_or_execution_fields_are_rejected_at_any_depth():
    for key, value in (("dps", 999), ("result_receipt", {}), ("execution_authority", "agent")):
        topology = _topology()
        topology["nodes"][1]["parameter_slots"][0][key] = value
        with pytest.raises(EventTopologyError) as error:
            validate_event_topology(topology)
        assert error.value.code == "event_topology_execution_payload"

    topology = _topology()
    topology["authority"] = "engine_executable"
    with pytest.raises(EventTopologyError) as error:
        validate_event_topology(topology)
    assert error.value.code == "event_topology_execution_authority"

    topology = _topology()
    topology["nodes"][1]["parameter_slots"][0]["semantic"] = "calculation.dps"
    with pytest.raises(EventTopologyError) as error:
        validate_event_topology(topology)
    assert error.value.code == "event_topology_execution_payload"


def test_source_receipt_metadata_is_narrow_and_cannot_grant_execution():
    topology = _topology()
    ref = topology["sources"][0]["source_refs"][0]
    ref.update(kind="database_appendix", status="verified", fingerprint="a" * 64,
               integrity_status="verified", knowledge_review_status="approved", execution_authority="none")
    validate_event_topology(topology)

    ref["execution_authority"] = "engine_executable"
    with pytest.raises(EventTopologyError) as error:
        validate_event_topology(topology)
    assert error.value.code == "event_topology_execution_payload"

    ref["execution_authority"] = "none"
    topology["nodes"][0]["execution_authority"] = "none"
    with pytest.raises(EventTopologyError) as error:
        validate_event_topology(topology)
    assert error.value.code == "event_topology_execution_payload"


def test_receipt_discloses_coverage_but_not_sources_or_parameter_values():
    topology = _topology(slot=_slot(value=98765.4321))
    topology["sources"][0]["source_refs"][0]["reference"] = "secret research transcript"
    receipt_text = json.dumps(lint_event_topology(topology), ensure_ascii=False)
    assert "secret research transcript" not in receipt_text
    assert "98765.4321" not in receipt_text
    assert '"parameter_slots": 1' in receipt_text
    assert "角色特有事实由Agent声明" in receipt_text


def test_active_kind_summary_is_bounded_and_excludes_inactive_nodes():
    topology = _topology()
    excluded = copy.deepcopy(topology["nodes"][1])
    excluded.update(node_id="node.excluded", kind="reaction_candidate", status="excluded",
                    note="本场景明确不适用。")
    topology["nodes"].append(excluded)
    topology["sources"][0]["node_ids"].append("node.excluded")
    receipt = lint_event_topology(topology)
    assert receipt["active_node_kinds"] == ["action_anchor", "damage"]
    assert receipt["active_node_kind_counts"] == {"action_anchor": 1, "damage": 1}
    assert len(receipt["active_node_kinds"]) <= len(event_topology_capabilities()["node_kinds"])


def test_source_and_node_ownership_must_be_bidirectional_and_closed():
    topology = _topology()
    topology["sources"][0]["node_ids"].remove("node.hit")
    with pytest.raises(EventTopologyError, match="bidirectional"):
        validate_event_topology(topology)

    topology = _topology()
    topology["nodes"][1]["source_ids"] = ["source.unknown"]
    with pytest.raises(EventTopologyError, match="unknown source"):
        validate_event_topology(topology)


def test_bound_relationships_and_modeled_sources_need_declared_provenance():
    topology = _topology()
    topology["edges"][0]["source_ids"] = []
    receipt = lint_event_topology(topology)
    assert any(row["code"] == "event_edge_missing_source" for row in receipt["issues"])

    topology = _topology()
    topology["sources"][0]["source_refs"] = []
    receipt = lint_event_topology(topology)
    assert any(row["code"] == "event_source_evidence_missing" for row in receipt["issues"])

    topology = _topology()
    topology["sources"][0]["source_refs"] = [{"kind": "agent_research",
        "reference": "query still pending", "fingerprint": None, "status": "pending"}]
    receipt = lint_event_topology(topology)
    assert any(row["code"] == "event_source_evidence_pending" for row in receipt["issues"])


def test_action_anchor_coverage_is_a_stage_gate_not_character_inference():
    topology = _topology()
    topology["nodes"][0]["kind"] = "state"
    receipt = lint_event_topology(topology)
    assert receipt["status"] == "draft"
    missing = next(row for row in receipt["issues"] if row["code"] == "action_anchor_missing")
    assert missing["action_id"] == "action.skill"
    assert not any("skill" in row["code"] or "character" in row["code"] for row in receipt["issues"])


def test_periodic_schedule_is_structural_and_pending_fields_remain_visible():
    topology = _topology()
    topology["nodes"][1]["kind"] = "periodic_channel"
    topology["nodes"][1]["schedule"] = {
        "kind": "periodic",
        "anchor": {"kind": "node", "ref_id": "node.action", "phase": "point"},
        "first_offset_seconds": .25,
        "interval_seconds": None,
        "count": None,
        "duration_seconds": 8.0,
        "refresh_policy": "pending",
        "off_field_policy": "continue",
        "order": 4,
    }
    receipt = lint_event_topology(topology)
    assert receipt["status"] == "draft"
    paths = {row["path"] for row in receipt["issues"] if row["code"] == "event_schedule_pending"}
    assert "topology.nodes[1].schedule.interval_seconds" in paths
    assert "topology.nodes[1].schedule.refresh_policy" in paths


def test_explicit_occurrences_have_stable_identity_and_unique_positions():
    topology = _topology()
    topology["nodes"][1]["kind"] = "finite_sequence"
    topology["nodes"][1]["schedule"] = {"kind": "explicit_offsets",
        "anchor": {"kind": "node", "ref_id": "node.action", "phase": "point"},
        "occurrences": [
            {"occurrence_id": "occurrence.1", "offset_seconds": .2, "order": 0},
            {"occurrence_id": "occurrence.2", "offset_seconds": .4, "order": 0},
        ]}
    assert lint_event_topology(topology)["status"] == "numeric_ready"
    topology["nodes"][1]["schedule"]["occurrences"][1].update(
        occurrence_id="occurrence.1", offset_seconds=.2)
    with pytest.raises(EventTopologyError):
        validate_event_topology(topology)


def test_duplicate_relations_and_causal_cycles_are_reported_without_execution():
    topology = _topology()
    topology["edges"].append({"edge_id": "edge.duplicate", "kind": "causes",
        "from_node_id": "node.action", "to_node_id": "node.hit", "status": "bound",
        "source_ids": ["source.skill"], "note": ""})
    receipt = lint_event_topology(topology)
    assert any(row["code"] == "event_edge_duplicate_binding" for row in receipt["issues"])

    topology = _topology()
    topology["edges"].append({"edge_id": "edge.reverse", "kind": "triggers",
        "from_node_id": "node.hit", "to_node_id": "node.action", "status": "bound",
        "source_ids": ["source.skill"], "note": ""})
    receipt = lint_event_topology(topology)
    assert any(row["code"] == "event_topology_cycle" for row in receipt["issues"])


def test_fingerprint_binds_actions_dependencies_and_every_declared_change():
    topology = _topology()
    original = event_topology_fingerprint(topology)
    changed = copy.deepcopy(topology)
    changed["dependencies"][0]["fingerprint"] = "d" * 64
    assert event_topology_fingerprint(changed) != original
    changed = copy.deepcopy(topology)
    changed["action_checkpoint"]["fingerprint"] = "e" * 64
    assert event_topology_fingerprint(changed) != original


def test_new_topology_is_a_valid_non_executable_draft():
    topology = new_event_topology("topology.empty", action_checkpoint={
        "revision": 0, "fingerprint": "a" * 64, "action_ids": ["action.one"]})
    assert validate_event_topology(topology) is topology
    receipt = lint_event_topology(topology)
    assert receipt["status"] == "draft"
    assert receipt["coverage"]["actions"]["missing_action_ids"] == ["action.one"]


def test_invalid_document_gets_bounded_diagnostic_receipt():
    topology = _topology()
    topology["nodes"][1]["schedule"]["anchor"]["ref_id"] = "node.missing"
    receipt = lint_event_topology(topology)
    assert receipt["status"] == "invalid"
    assert receipt["issues"][0]["code"] == "invalid_event_topology"
    assert receipt["topology_fingerprint"]


def test_capabilities_make_the_verification_boundary_explicit():
    capabilities = event_topology_capabilities()
    assert capabilities["authority"] == "agent_declared_non_executable"
    assert "character_specific_game_truth" in capabilities["excluded_scope"]
    assert "declared_parameter_slots" in capabilities["validation_scope"]
    assert capabilities["stage_disclosure"].startswith("coverage receipt omits")


def test_capabilities_publish_closed_bounded_component_schemas_and_legal_templates():
    capabilities = event_topology_capabilities()
    schema = capabilities["component_json_schema"]
    definitions = schema["$defs"]
    contracts = capabilities["component_contracts"]

    assert schema["$schema"].endswith("2020-12/schema")
    assert set(contracts) == {"source", "node", "schedule", "parameter_slot", "edge"}
    for name, contract in contracts.items():
        assert contract["schema_ref"] == f"#/$defs/{name}"
        if name != "schedule":
            assert definitions[name]["additionalProperties"] is False
    assert len(definitions["schedule"]["oneOf"]) == 4
    assert definitions["source"]["properties"]["node_ids"]["maxItems"] == MAX_NODES
    assert definitions["node"]["properties"]["source_ids"]["maxItems"] == MAX_SOURCES
    assert definitions["edge"]["properties"]["source_ids"]["uniqueItems"] is True

    # Each advertised template can be copied into a document without guessing
    # missing fields.  Cross-document references are rebound to fixture ids.
    source = copy.deepcopy(contracts["source"]["minimal_legal_template"])
    topology = _topology()
    topology["sources"].append(source)
    validate_event_topology(topology)

    node = copy.deepcopy(contracts["node"]["minimal_legal_template"])
    topology = _topology()
    topology["nodes"].append(node)
    validate_event_topology(topology)

    schedule = copy.deepcopy(contracts["schedule"]["minimal_legal_template"])
    topology = _topology()
    topology["nodes"][1]["schedule"] = schedule
    validate_event_topology(topology)

    slot = copy.deepcopy(contracts["parameter_slot"]["minimal_legal_template"])
    topology = _topology()
    topology["nodes"][1]["parameter_slots"].append(slot)
    validate_event_topology(topology)

    edge = copy.deepcopy(contracts["edge"]["minimal_legal_template"])
    edge.update(from_node_id="node.action", to_node_id="node.hit")
    topology = _topology()
    topology["edges"].append(edge)
    validate_event_topology(topology)

    variants = contracts["schedule"]["variant_templates"]
    assert set(variants) == {"point", "periodic", "explicit_offsets", "pending"}
    for variant in variants.values():
        topology = _topology()
        schedule = copy.deepcopy(variant)
        if schedule.get("anchor") is not None:
            schedule["anchor"]["ref_id"] = "action.skill"
        topology["nodes"][1]["schedule"] = schedule
        validate_event_topology(topology)


def test_capability_templates_are_isolated_between_calls():
    first = event_topology_capabilities()
    first["component_contracts"]["node"]["minimal_legal_template"]["node_id"] = "mutated"
    second = event_topology_capabilities()
    assert second["component_contracts"]["node"]["minimal_legal_template"][
        "node_id"] == "node.pending"


def test_cycle_detection_handles_a_maximum_depth_graph_without_recursion():
    nodes = []
    for index in range(MAX_NODES):
        node_id = f"node.{index:04d}"
        if index == 0:
            schedule = _point("action", "action.skill")
        else:
            schedule = _point("node", f"node.{index - 1:04d}")
        nodes.append({"node_id": node_id, "kind": "other", "status": "bound",
                      "schedule": schedule})
    edges = [{"edge_id": "edge.close", "kind": "causes", "status": "bound",
              "from_node_id": f"node.{MAX_NODES - 1:04d}", "to_node_id": "node.0000"}]

    cycles = _graph_cycles(nodes, edges)
    assert len(cycles) == 1
    assert cycles[0][0] == cycles[0][-1]
    assert len(cycles[0]) == MAX_NODES + 1

    assert _graph_cycles(nodes, []) == []


def test_maximum_source_index_validates_bidirectional_references():
    topology = new_event_topology("topology.source-index", action_checkpoint={
        "revision": 1, "fingerprint": "a" * 64, "action_ids": ["action.skill"]})
    for source_index in range(MAX_SOURCES):
        source_id = f"source.{source_index:04d}"
        node_ids = [f"node.{source_index:04d}.{offset}" for offset in range(4)]
        topology["sources"].append({
            "source_id": source_id, "category": "other", "owner_ref": None,
            "description": "Indexed source.", "disposition": "modeled",
            "node_ids": node_ids, "reason": None, "source_refs": [],
        })
        for node_id in node_ids:
            topology["nodes"].append({
                "node_id": node_id, "kind": "other", "status": "bound",
                "schedule": {"kind": "pending", "anchor": None,
                             "reason": "Pending schedule."},
                "source_ids": [source_id], "parameter_slots": [], "note": "",
            })

    assert len(topology["nodes"]) == MAX_NODES
    assert validate_event_topology(topology) is topology

    topology["sources"][-1]["node_ids"].pop()
    with pytest.raises(EventTopologyError, match="bidirectional"):
        validate_event_topology(topology)


def test_large_diagnostic_receipt_is_bounded_without_losing_blocker_counts():
    topology = new_event_topology("topology.large-receipt", action_checkpoint={
        "revision": 1, "fingerprint": "a" * 64, "action_ids": ["action.one"]})
    source = {"source_id": "source.large", "category": "other", "owner_ref": None,
              "description": "large diagnostic fixture", "disposition": "modeled",
              "node_ids": [], "reason": None,
              "source_refs": [{"kind": "agent_research", "reference": "fixture",
                               "fingerprint": None, "status": "declared"}]}
    topology["sources"] = [source]
    for index in range(700):
        node_id = "node.large." + str(index)
        source["node_ids"].append(node_id)
        topology["nodes"].append({
            "node_id": node_id,
            "kind": "action_anchor" if index == 0 else "damage",
            "status": "identified",
            "schedule": {"kind": "point", "anchor": {
                "kind": "action", "ref_id": "action.one", "phase": "start"},
                "offset_seconds": index / 1000, "order": index},
            "source_ids": ["source.large"], "parameter_slots": [], "note": "",
        })
    topology["declaration"]["completeness"] = "declared_complete"
    receipt = lint_event_topology(topology)
    assert receipt["status"] == "draft"
    assert len(receipt["issues"]) == 512
    assert receipt["issues_total"] >= 700
    assert receipt["issues_truncated"] is True
    assert receipt["stages"]["topology"]["blocking_issue_count"] >= 700
