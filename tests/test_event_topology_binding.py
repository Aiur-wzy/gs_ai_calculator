from __future__ import annotations

import copy

from genshin_app.equipment.event_topology import AUTHORITY
from genshin_app.equipment.event_topology import (
    CONTRACT_ID as TOPOLOGY_CONTRACT,
)
from genshin_app.equipment.event_topology_binding import (
    lint_event_topology_bindings,
    new_event_topology_bindings,
)
from genshin_app.equipment.files import fingerprint


def _topology():
    checkpoint = {"revision": 1, "fingerprint": "a" * 64, "action_ids": ["action.skill"]}
    source = {
        "source_id": "source.skill",
        "category": "talent",
        "owner_ref": "character-1",
        "description": "Agent声明的技能事件",
        "disposition": "modeled",
        "node_ids": ["node.action", "node.damage"],
        "reason": None,
        "source_refs": [{
            "kind": "agent_research",
            "reference": "reviewed fixture declaration",
            "fingerprint": None,
            "status": "declared",
        }],
    }
    action = {
        "node_id": "node.action",
        "kind": "action_anchor",
        "status": "bound",
        "schedule": {
            "kind": "point",
            "anchor": {"kind": "action", "ref_id": "action.skill", "phase": "start"},
            "offset_seconds": 0,
            "order": 0,
        },
        "source_ids": ["source.skill"],
        "parameter_slots": [],
        "note": "",
    }
    damage = {
        "node_id": "node.damage",
        "kind": "damage",
        "status": "bound",
        "schedule": {
            "kind": "point",
            "anchor": {"kind": "node", "ref_id": "node.action", "phase": "point"},
            "offset_seconds": 0.2,
            "order": 1,
        },
        "source_ids": ["source.skill"],
        "parameter_slots": [],
        "note": "",
    }
    return {
        "contract_id": TOPOLOGY_CONTRACT,
        "schema_version": 1,
        "topology_id": "topology.fixture",
        "authority": AUTHORITY,
        "declaration": {
            "character_semantics": "agent_declared",
            "completeness": "declared_complete",
            "note": "事件已经由Agent盘点",
        },
        "action_checkpoint": checkpoint,
        "dependencies": [],
        "sources": [source],
        "nodes": [action, damage],
        "edges": [],
    }


def _plan():
    return {
        "kind": "genshin-plan",
        "plan_id": "plan.fixture",
        "revision": 1,
        "manual_template": {
            "meta": {"name": "fixture"},
            "hits": [{"id": "hit.skill", "name": "技能伤害", "multiplier": 2.0}],
            "buffs": [],
        },
        "rotation_model": {"events": []},
        "mechanics_model": {"schema_version": 1, "rules": [], "hit_events": []},
        "extensions": {},
        "resolution": {"state": "pending"},
        "resolved_template": None,
    }


def _binding(topology, *, plan=None, pointer="/manual_template/hits/0", target_kind="hit"):
    plan = plan or _plan()
    target_fingerprint = "0" * 64
    if pointer == "/manual_template/hits/0":
        target_fingerprint = fingerprint(plan["manual_template"]["hits"][0])
    document = new_event_topology_bindings(topology)
    document["bindings"].append({
        "binding_id": "binding.damage",
        "node_id": "node.damage",
        "target_kind": target_kind,
        "pointer": pointer,
        "target_fingerprint": target_fingerprint,
        "note": "映射到当前方案的技能伤害输入",
    })
    return document


def test_complete_receipt_requires_strong_typed_executable_object():
    topology, plan = _topology(), _plan()
    receipt = lint_event_topology_bindings(plan, topology, _binding(topology, plan=plan))
    assert receipt["status"] == "complete"
    assert receipt["counts"] == {
        "expected_nodes": 1,
        "bound_nodes": 1,
        "bindings": 1,
        "resolved_bindings": 1,
    }
    assert receipt["coverage"]["missing_node_ids"] == []
    assert receipt["character_semantics"] == "agent_declared_unverified"


def test_missing_and_stale_topology_never_produce_complete_receipt():
    topology, plan = _topology(), _plan()
    empty = new_event_topology_bindings(topology)
    receipt = lint_event_topology_bindings(plan, topology, empty)
    assert receipt["status"] == "incomplete"
    assert receipt["coverage"]["missing_node_ids"] == ["node.damage"]

    binding = _binding(topology, plan=plan)
    changed = copy.deepcopy(topology)
    changed["declaration"]["note"] = "Agent重新审查了事件"
    receipt = lint_event_topology_bindings(plan, changed, binding)
    assert receipt["status"] == "incomplete"
    assert "event_topology_binding_fingerprint_stale" in {
        issue["code"] for issue in receipt["issues"]
    }


def test_legacy_binding_without_saved_target_identity_fails_closed():
    topology, plan = _topology(), _plan()
    binding = _binding(topology, plan=plan)
    binding["bindings"][0].pop("target_fingerprint")
    receipt = lint_event_topology_bindings(plan, topology, binding)
    assert receipt["status"] == "invalid"
    assert receipt["issues"][0]["code"] == "invalid_event_topology_bindings"


def test_leaf_metadata_and_forbidden_result_cannot_fake_coverage():
    topology, plan = _topology(), _plan()
    cases = (
        ("hit", "/manual_template/hits/0/name", "event_topology_binding_target_shape_invalid"),
        ("hit", "/manual_template/meta", "event_topology_binding_root_mismatch"),
        ("hit", "/result", "event_topology_binding_target_forbidden"),
        ("hit", "/extensions/event_topology", "event_topology_binding_target_forbidden"),
    )
    plan["result"] = {"damage": 1}
    plan["extensions"]["event_topology"] = topology
    for target_kind, pointer, expected_code in cases:
        receipt = lint_event_topology_bindings(
            plan, topology, _binding(topology, pointer=pointer, target_kind=target_kind))
        assert receipt["status"] == "incomplete"
        assert expected_code in {issue["code"] for issue in receipt["issues"]}


def test_replaced_target_requires_explicit_rebinding_before_coverage_recovers():
    topology, plan = _topology(), _plan()
    binding = _binding(topology, plan=plan)
    before = lint_event_topology_bindings(plan, topology, binding)
    replacement = {**plan["manual_template"]["hits"][0], "multiplier": 3.0}
    plan["manual_template"]["hits"][0] = replacement
    after = lint_event_topology_bindings(plan, topology, binding)
    assert before["status"] == "complete"
    assert after["status"] == "incomplete"
    stale = next(issue for issue in after["issues"]
                 if issue["code"] == "event_topology_binding_target_stale")
    assert stale["expected_target_fingerprint"] == binding["bindings"][0]["target_fingerprint"]
    assert stale["current_target_fingerprint"] == fingerprint(replacement)
    assert after["counts"]["bound_nodes"] == 0
    assert after["coverage"]["missing_node_ids"] == ["node.damage"]
    assert before["target_projection_fingerprint"] != after["target_projection_fingerprint"]
    assert before["receipt_fingerprint"] != after["receipt_fingerprint"]

    binding["bindings"][0]["target_fingerprint"] = fingerprint(replacement)
    rebound = lint_event_topology_bindings(plan, topology, binding)
    assert rebound["status"] == "complete"
    assert rebound["counts"]["bound_nodes"] == 1
    assert rebound["coverage"]["missing_node_ids"] == []


def test_shared_target_is_explicitly_reported_for_review():
    topology, plan = _topology(), _plan()
    application = copy.deepcopy(topology["nodes"][1])
    application.update(node_id="node.application", kind="application")
    topology["nodes"].append(application)
    topology["sources"][0]["node_ids"].append("node.application")
    binding = _binding(topology, plan=plan)
    binding["bindings"].append({
        "binding_id": "binding.application",
        "node_id": "node.application",
        "target_kind": "hit",
        "pointer": "/manual_template/hits/0",
        "target_fingerprint": fingerprint(plan["manual_template"]["hits"][0]),
        "note": "同一命中对象承载附着",
    })
    receipt = lint_event_topology_bindings(plan, topology, binding)
    assert receipt["status"] == "complete"
    assert receipt["coverage"]["shared_targets"] == [{
        "target_kind": "hit",
        "pointer": "/manual_template/hits/0",
        "node_ids": ["node.application", "node.damage"],
        "node_ids_truncated": False,
    }]


def test_invalid_non_json_binding_degrades_to_bounded_invalid_receipt():
    topology = _topology()
    recursive = {}
    recursive["self"] = recursive
    receipt = lint_event_topology_bindings(_plan(), topology, recursive)
    assert receipt["status"] == "invalid"
    assert receipt["bindings_fingerprint"] is None
    assert receipt["receipt_fingerprint"]


def test_external_supplement_is_not_an_executable_binding_target():
    topology, plan = _topology(), _plan()
    plan["external_damage_supplements"] = [{"id": "external-1", "damage": 999}]
    document = _binding(topology, pointer="/external_damage_supplements/0", target_kind="hit")
    receipt = lint_event_topology_bindings(plan, topology, document)
    assert receipt["status"] == "incomplete"
    assert "event_topology_binding_root_mismatch" in {
        issue["code"] for issue in receipt["issues"]
    }
