from __future__ import annotations

import copy

import pytest

from genshin_app.equipment import compose_equipment_plan, new_plan, run_plan_calc
from genshin_app.equipment.agent_execution_materialization import (
    lint_agent_execution_materialization,
    new_agent_execution_materialization,
)
from genshin_app.equipment.calculation_overrides import (
    acceptance_fingerprint,
    database_snapshot_fingerprint,
    fingerprint,
    materialize_calculation_overrides,
    validate_computed_value_result_receipt,
    validate_execution_bundle_receipt,
)
from genshin_app.equipment.event_topology import event_topology_fingerprint
from genshin_app.equipment.event_topology_binding import (
    lint_event_topology_bindings,
)
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.export import hit_rows, summary_rows
from genshin_db import queries

EVIDENCE = {"status": "assumed", "source": "覆盖执行独立工程样例"}
ONCE = {"mode": "once"}
ZERO = "0" * 64


def native_plan(*, dormant_hit: bool = False):
    hits = [{
        "id": "h0", "t": 0.2, "character": "a", "skill": "工程技能",
        "hit_name": "自动命中", "damage_type_ids": ["damage.elemental_skill"],
        "damage_system": "classic", "element": "geo", "multiplier": 1,
    }]
    if dormant_hit:
        hits.append({
            "id": "h1", "t": 0.3, "character": "a", "skill": "工程技能",
            "hit_name": "待接入命中", "damage_type_ids": ["damage.elemental_skill"],
            "damage_system": "classic", "element": "geo", "multiplier": 1,
        })
    template = {
        "version": 2, "form": "resolved", "id_registry_version": 1,
        "type_definitions": {"damage_types": []},
        "enemy": {"level": 90, "resistance": {"default": 0.1}},
        "loop_duration": 2,
        "characters": [{
            "id": "a", "name": "工程角色", "level": 90,
            "stats": {"atk": 100, "base_atk": 50, "em": 0, "crit_rate": 0,
                      "crit_dmg": 0.5, "energy_recharge": 1},
        }],
        "hits": hits, "buffs": [], "switches": [],
    }
    rotation = {
        "schema_version": 1, "period": 2, "max_cycles": 3,
        "initial_frontline": "a",
        "characters": [{
            "id": "a", "element": "geo", "energy_recharge": 1,
            "energy_capacity": 0, "initial_energy": 0, **EVIDENCE,
        }],
        "abilities": [{
            "id": "e", "character": "a",
            "action_type_id": "action.elemental_skill", "cooldown": None,
            "energy": None, "rules": [], **EVIDENCE,
        }],
        "actions": [{
            "id": "cast", "ability_id": "e", "start": 0,
            "occupation_end": 0.1, "order": 0, "repeat": ONCE, **EVIDENCE,
        }],
        "events": [], "initial_cooldowns": [],
    }
    mechanics = {
        "schema_version": 1, "rules": [],
        "hit_events": [{"hit_id": "h0", "action_id": "cast", "order": 10}],
        "hit_patterns": [], "condition_events": [], "initial_state": {"rules": []},
    }
    value = new_plan(template)
    value.update(plan_version=3, rotation_model=rotation, mechanics_model=mechanics)
    node_ids = [f"topology.{row['id']}" for row in hits]
    node_ids += [
        f"topology.mechanics.{row['hit_id']}" for row in mechanics["hit_events"]
    ]
    source = {
        "source_id": "source.engineering",
        "category": "other",
        "owner_ref": "a",
        "description": "覆盖执行工程样例",
        "disposition": "modeled",
        "node_ids": node_ids,
        "reason": None,
        "source_refs": [{
            "kind": "other", "reference": "engineering fixture",
            "fingerprint": None, "status": "declared",
        }],
    }
    nodes = [{
        "node_id": f"topology.{row['id']}",
        "kind": "damage", "status": "bound",
        "schedule": {
            "kind": "point",
            "anchor": {"kind": "action", "ref_id": "cast", "phase": "start"},
            "offset_seconds": row["t"], "order": 10 + index,
        },
        "source_ids": ["source.engineering"], "parameter_slots": [], "note": "",
    } for index, row in enumerate(hits)]
    nodes += [{
        "node_id": f"topology.mechanics.{row['hit_id']}",
        "kind": "damage", "status": "bound",
        "schedule": {
            "kind": "point",
            "anchor": {"kind": "action", "ref_id": row["action_id"], "phase": "start"},
            "offset_seconds": next(
                hit["t"] for hit in hits if hit["id"] == row["hit_id"]
            ),
            "order": row["order"],
        },
        "source_ids": ["source.engineering"], "parameter_slots": [], "note": "",
    } for row in mechanics["hit_events"]]
    topology = {
        "contract_id": "event-topology-v1", "schema_version": 1,
        "topology_id": "topology.engineering",
        "authority": "agent_declared_non_executable",
        "declaration": {
            "character_semantics": "agent_declared",
            "completeness": "declared_complete", "note": "工程测试拓扑",
        },
        "action_checkpoint": {
            "revision": 1, "fingerprint": "a" * 64, "action_ids": ["cast"],
        },
        "dependencies": [], "sources": [source], "nodes": nodes, "edges": [],
    }
    bindings = {
        "contract_id": "event-topology-execution-bindings-v1",
        "schema_version": 1, "topology_id": topology["topology_id"],
        "topology_fingerprint": event_topology_fingerprint(topology),
        "bindings": [{
            "binding_id": f"binding.{row['id']}",
            "node_id": f"topology.{row['id']}", "target_kind": "hit",
            "pointer": f"/manual_template/hits/{index}",
            "target_fingerprint": fingerprint(row), "note": "绑定工程命中",
        } for index, row in enumerate(hits)] + [{
            "binding_id": f"binding.mechanics.{row['hit_id']}",
            "node_id": f"topology.mechanics.{row['hit_id']}",
            "target_kind": "mechanics",
            "pointer": f"/mechanics_model/hit_events/{index}",
            "target_fingerprint": fingerprint(row), "note": "绑定工程命中派发",
        } for index, row in enumerate(mechanics["hit_events"])],
    }
    value["extensions"] = {
        "event_topology": topology,
        "event_topology_bindings": bindings,
    }
    return value


def target(domain: str, entity_id: str, scope_id: str, *, component_id=None):
    relation_prefix = "mechanics." if domain == "mechanics_hit_event" else ""
    return {
        "domain": domain, "entity_id": entity_id, "scope_id": scope_id,
        "topology_node_id": "topology." + relation_prefix + entity_id,
        "execution_binding_id": "binding." + relation_prefix + entity_id,
        "action_id": "cast", "component_id": component_id,
        "occurrence_id": None, "target_id": "enemy.0",
    }


def add_mechanics_hit_relation(plan, hit_id):
    event = next(
        row for row in plan["mechanics_model"]["hit_events"]
        if row["hit_id"] == hit_id
    )
    hit = next(row for row in plan["manual_template"]["hits"] if row["id"] == hit_id)
    topology = plan["extensions"]["event_topology"]
    topology["nodes"].append({
        "node_id": f"topology.mechanics.{hit_id}",
        "kind": "damage", "status": "bound",
        "schedule": {
            "kind": "point",
            "anchor": {"kind": "action", "ref_id": event["action_id"], "phase": "start"},
            "offset_seconds": hit["t"], "order": event["order"],
        },
        "source_ids": ["source.engineering"], "parameter_slots": [], "note": "",
    })
    topology["sources"][0]["node_ids"].append(f"topology.mechanics.{hit_id}")
    bindings = plan["extensions"]["event_topology_bindings"]
    bindings["topology_fingerprint"] = event_topology_fingerprint(topology)
    index = plan["mechanics_model"]["hit_events"].index(event)
    bindings["bindings"].append({
        "binding_id": f"binding.mechanics.{hit_id}",
        "node_id": f"topology.mechanics.{hit_id}", "target_kind": "mechanics",
        "pointer": f"/mechanics_model/hit_events/{index}",
        "target_fingerprint": fingerprint(event), "note": "绑定新增工程命中派发",
    })


def override_item(mode: str, selected_target: dict, payload: dict, *, suffix="one"):
    return {
        "override_id": "override." + suffix,
        "mode": mode,
        "target": selected_target,
        "coverage_key": "coverage." + suffix,
        "supersedes": [] if mode == "add_event" else ["automatic:placeholder"],
        "reason_code": "engine_disagreement",
        "reason": "工程测试确认自动执行图需要修订。",
        "source_refs": [{
            "source_id": "source." + suffix, "kind": "local_document",
            "locator": "tests/test_calculation_overrides_execution.py",
            "summary": "仅用于覆盖执行闭环测试", "fingerprint": None,
        }],
        "impact_domains": ["damage"], "payload": payload,
        "automatic_target_fingerprint": ZERO, "accepted_fingerprint": None,
        "applies_to_version": "engineering",
        "unknowns": [], "invalidation_conditions": ["自动基线变化"],
    }


def declaration(*items):
    return {
        "contract": "calculation-event-overrides-v1",
        "basis": {
            "plan_input_fingerprint": ZERO,
            "automatic_baseline_fingerprint": ZERO,
            "database_snapshot_fingerprint": ZERO,
            "engine_contract_fingerprint": ZERO,
            "topology_fingerprint": None,
            "execution_bindings_fingerprint": None,
        },
        "items": list(items),
    }


def accept_from_compose(plan, database):
    """Use the pending receipt exactly as an Agent/UI review round would."""

    report = compose_equipment_plan(plan, database)
    assert report["state"] == "pending"
    receipt = report["calculation_override_receipt"]
    candidates = {row["override_id"]: row for row in receipt["target_candidates"]}
    value = plan["calculation_event_overrides"]
    value["basis"] = copy.deepcopy(report["calculation_override_basis"])
    for item in value["items"]:
        candidate = candidates[item["override_id"]]
        item["automatic_target_fingerprint"] = candidate["automatic_target_fingerprint"]
        item["supersedes"] = (
            [] if candidate["automatic_owner_id"] is None
            else [candidate["automatic_owner_id"]]
        )
        item["accepted_fingerprint"] = acceptance_fingerprint(value["basis"], item)
    return plan


def test_ordinary_compose_publishes_automatic_baseline_without_override_receipt(tmp_path):
    report = compose_equipment_plan(native_plan(), tmp_path / "absent.db")
    assert report["state"] == "ready"
    assert report["automatic_baseline_fingerprint"] == (
        report["calculation_override_basis"]["automatic_baseline_fingerprint"]
    )
    assert "calculation_override_receipt" not in report


def test_override_declaration_participates_in_execution_input_fingerprint(tmp_path):
    database = tmp_path / "absent.db"
    plan = native_plan()
    plain = compose_equipment_plan(plan, database)
    replacement = copy.deepcopy(plan["manual_template"]["hits"][0])
    replacement["multiplier"] = 2
    plan["calculation_event_overrides"] = declaration(override_item(
        "replace_event", target("manual_hit", "h0", "manual_template.hits"),
        {"event": replacement},
    ))
    reviewed = compose_equipment_plan(plan, database)
    assert reviewed["input_fingerprint"] != plain["input_fingerprint"]


def test_database_snapshot_fingerprint_tracks_actual_legacy_file_bytes(
    tmp_path, monkeypatch
):
    database = tmp_path / "legacy.db"
    monkeypatch.setattr(queries, "get_meta", lambda _path: {"state": "legacy"})
    database.write_bytes(b"legacy-database-one")
    first = database_snapshot_fingerprint(database)
    database.write_bytes(b"legacy-database-two")
    second = database_snapshot_fingerprint(database)
    assert first != second


def test_replace_hit_multiplier_recalculates_and_receipt_survives_export(tmp_path):
    database = tmp_path / "absent.db"
    baseline = run_plan_calc(native_plan(), database)
    plan = native_plan()
    replacement = copy.deepcopy(plan["manual_template"]["hits"][0])
    replacement["multiplier"] = 2
    plan["calculation_event_overrides"] = declaration(override_item(
        "replace_event", target("manual_hit", "h0", "manual_template.hits"),
        {"event": replacement},
    ))
    accept_from_compose(plan, database)
    calculated = run_plan_calc(plan, database)
    assert calculated["result"]["summary"]["total_damage"] == pytest.approx(
        baseline["result"]["summary"]["total_damage"] * 2
    )
    proof = calculated["result"]["calculation_override_receipt"]
    assert proof["status"] == "applied"
    assert proof["coverage"][0]["status"] == "declaration_ready"
    assert proof["binding_overlay"]["rows"][0]["status"] == "effective"
    assert calculated["result_receipt"]["calculation_override_receipt"] == proof
    assert proof["released_owner_ids"] and proof["installed_owner_ids"]
    assert result_bytes(
        calculated["result"], calculated["result_receipt"], "json"
    )
    tampered = copy.deepcopy(calculated["result_receipt"])
    tampered["calculation_override_receipt"]["installed_owner_ids"] = []
    with pytest.raises(PlanError):
        result_bytes(calculated["result"], tampered, "json")


def test_execution_receipt_binds_mechanics_matches(tmp_path):
    database = tmp_path / "absent.db"
    plan = native_plan()
    replacement = copy.deepcopy(plan["manual_template"]["hits"][0])
    replacement["multiplier"] = 2
    plan["calculation_event_overrides"] = declaration(override_item(
        "replace_event", target("manual_hit", "h0", "manual_template.hits"),
        {"event": replacement},
    ))
    accept_from_compose(plan, database)
    report = compose_equipment_plan(plan, database)
    bundle = {
        "template": report["plan"]["resolved_template"],
        "rotation_model": report["compiled_rotation_model"],
        "mechanics_model": report["mechanics_model"],
        "mechanics_matches": report["mechanics_matches"],
    }
    validate_execution_bundle_receipt(
        report["calculation_override_receipt"], bundle
    )
    bundle["mechanics_matches"] = [{"unexpected": "different execution input"}]
    with pytest.raises(ValueError, match="执行图"):
        validate_execution_bundle_receipt(
            report["calculation_override_receipt"], bundle
        )


def test_acceptance_survives_document_revision_only_change(tmp_path):
    database = tmp_path / "absent.db"
    plan = native_plan()
    replacement = copy.deepcopy(plan["manual_template"]["hits"][0])
    replacement["multiplier"] = 2
    plan["calculation_event_overrides"] = declaration(override_item(
        "replace_event", target("manual_hit", "h0", "manual_template.hits"),
        {"event": replacement},
    ))
    accept_from_compose(plan, database)
    plan["revision"] += 1
    report = compose_equipment_plan(plan, database)
    assert report["state"] == "ready"
    assert report["calculation_override_receipt"]["status"] == "applied"


def test_acceptance_cannot_be_replayed_to_another_plan(tmp_path):
    database = tmp_path / "absent.db"
    plan = native_plan()
    replacement = copy.deepcopy(plan["manual_template"]["hits"][0])
    replacement["multiplier"] = 2
    plan["calculation_event_overrides"] = declaration(override_item(
        "replace_event", target("manual_hit", "h0", "manual_template.hits"),
        {"event": replacement},
    ))
    accept_from_compose(plan, database)
    copied = native_plan()
    copied["calculation_event_overrides"] = copy.deepcopy(
        plan["calculation_event_overrides"]
    )
    report = compose_equipment_plan(copied, database)
    assert report["state"] == "pending"
    assert "override_baseline_stale" in {
        row["code"] for row in report["missing_inputs"]
    }


def test_override_repairs_baseline_before_final_validation(tmp_path):
    database = tmp_path / "absent.db"
    plan = native_plan()
    plan["manual_template"]["hits"][0]["damage_type_ids"] = None
    plan["extensions"]["event_topology_bindings"]["bindings"][0][
        "target_fingerprint"
    ] = fingerprint(plan["manual_template"]["hits"][0])
    replacement = copy.deepcopy(plan["manual_template"]["hits"][0])
    replacement["damage_type_ids"] = ["damage.elemental_skill"]
    plan["calculation_event_overrides"] = declaration(override_item(
        "replace_event", target("manual_hit", "h0", "manual_template.hits"),
        {"event": replacement},
    ))
    accept_from_compose(plan, database)
    report = compose_equipment_plan(plan, database)
    assert report["state"] == "ready"
    assert report["plan"]["resolved_template"]["hits"][0][
        "damage_type_ids"
    ] == ["damage.elemental_skill"]


def test_suppress_hit_event_removes_damage_without_duplicate(tmp_path):
    database = tmp_path / "absent.db"
    plan = native_plan(dormant_hit=True)
    plan["mechanics_model"]["hit_events"].append(
        {"hit_id": "h1", "action_id": "cast", "order": 11}
    )
    add_mechanics_hit_relation(plan, "h1")
    plan["calculation_event_overrides"] = declaration(
        override_item(
            "suppress_event",
            target("mechanics_hit_event", "h1", "mechanics_model.hit_events"),
            {"reason": "该命中由自动派发错误生成"}, suffix="dispatch",
        ),
        override_item(
            "suppress_event",
            target("manual_hit", "h1", "manual_template.hits"),
            {"reason": "移除被排除派发所拥有的原型命中"}, suffix="prototype",
        ),
    )
    accept_from_compose(plan, database)
    calculated = run_plan_calc(plan, database)
    assert calculated["result"]["summary"]["total_damage"] == pytest.approx(45)
    assert [row["id"] for row in calculated["result"]["hits"]] == ["h0"]
    applied = calculated["result"]["calculation_override_receipt"]["applied"]
    assert [row["override_id"] for row in applied] == [
        "override.dispatch", "override.prototype",
    ]
    assert {
        row["status"]
        for row in calculated["result"]["calculation_override_receipt"][
            "binding_overlay"
        ]["rows"]
    } == {"suppressed"}


def test_add_hit_event_is_executed(tmp_path):
    database = tmp_path / "absent.db"
    plan = native_plan(dormant_hit=True)
    selected = target(
        "mechanics_hit_event", "h1", "mechanics_model.hit_events"
    )
    selected["topology_node_id"] = "topology.h1"
    selected["execution_binding_id"] = "binding.h1"
    plan["calculation_event_overrides"] = declaration(override_item(
        "add_event",
        selected,
        {"event": {"hit_id": "h1", "action_id": "cast", "order": 11}},
    ))
    accept_from_compose(plan, database)
    calculated = run_plan_calc(plan, database)
    assert calculated["result"]["summary"]["total_damage"] == pytest.approx(90)
    assert [row["id"] for row in calculated["result"]["hits"]] == ["h0", "h1"]
    overlay = calculated["result"]["calculation_override_receipt"][
        "binding_overlay"
    ]
    assert overlay["rows"][0]["projection"] == "manual_hit_dispatch_projection"


def test_reordered_array_is_still_replaced_by_stable_id(tmp_path):
    database = tmp_path / "absent.db"
    plan = native_plan(dormant_hit=True)
    plan["mechanics_model"]["hit_events"].append(
        {"hit_id": "h1", "action_id": "cast", "order": 11}
    )
    add_mechanics_hit_relation(plan, "h1")
    plan["manual_template"]["hits"].reverse()
    for binding in plan["extensions"]["event_topology_bindings"]["bindings"]:
        if binding["target_kind"] != "hit":
            continue
        hit_id = binding["binding_id"].removeprefix("binding.")
        binding["pointer"] = "/manual_template/hits/" + str(next(
            index for index, row in enumerate(plan["manual_template"]["hits"])
            if row["id"] == hit_id
        ))
    replacement = copy.deepcopy(next(
        row for row in plan["manual_template"]["hits"] if row["id"] == "h0"
    ))
    replacement["multiplier"] = 3
    plan["calculation_event_overrides"] = declaration(override_item(
        "replace_event", target("manual_hit", "h0", "manual_template.hits"),
        {"event": replacement},
    ))
    accept_from_compose(plan, database)
    report = compose_equipment_plan(plan, database)
    assert report["state"] == "ready"
    rows = {row["id"]: row for row in report["plan"]["resolved_template"]["hits"]}
    assert rows["h0"]["multiplier"] == 3
    assert rows["h1"]["multiplier"] == 1


def test_whole_event_replacement_cannot_change_stable_hit_binding(tmp_path):
    database = tmp_path / "absent.db"
    plan = native_plan()
    plan["calculation_event_overrides"] = declaration(override_item(
        "replace_event",
        target("mechanics_hit_event", "h0", "mechanics_model.hit_events"),
        {"event": {"hit_id": "h0", "action_id": "other", "order": 10}},
    ))
    accept_from_compose(plan, database)
    report = compose_equipment_plan(plan, database)
    assert report["state"] == "pending"
    assert report["plan"]["resolved_template"] is None
    assert "override_payload_identity_mismatch" in {
        row["code"] for row in report["missing_inputs"]
    }


def test_individually_valid_node_and_binding_must_belong_to_same_relation(tmp_path):
    plan = native_plan(dormant_hit=True)
    replacement = copy.deepcopy(plan["manual_template"]["hits"][0])
    replacement["multiplier"] = 2
    selected = target("manual_hit", "h0", "manual_template.hits")
    selected["execution_binding_id"] = "binding.h1"
    plan["calculation_event_overrides"] = declaration(override_item(
        "replace_event", selected, {"event": replacement},
    ))
    report = compose_equipment_plan(plan, tmp_path / "absent.db")
    assert report["state"] == "pending"
    assert "override_event_relation_mismatch" in {
        row["code"] for row in report["missing_inputs"]
    }


def test_mechanics_hit_override_requires_stable_action_identity(tmp_path):
    plan = native_plan()
    selected = target(
        "mechanics_hit_event", "h0", "mechanics_model.hit_events"
    )
    selected["action_id"] = None
    plan["calculation_event_overrides"] = declaration(override_item(
        "replace_event", selected,
        {"event": {"hit_id": "h0", "action_id": "cast", "order": 10}},
    ))
    report = compose_equipment_plan(plan, tmp_path / "absent.db")
    assert report["state"] == "pending"
    assert "override_event_action_required" in {
        row["code"] for row in report["missing_inputs"]
    }


def test_binding_to_another_valid_entity_cannot_take_over_target(tmp_path):
    plan = native_plan(dormant_hit=True)
    binding = next(
        row for row in plan["extensions"]["event_topology_bindings"]["bindings"]
        if row["binding_id"] == "binding.h0"
    )
    binding["pointer"] = "/manual_template/hits/1"
    binding["target_fingerprint"] = fingerprint(plan["manual_template"]["hits"][1])
    assert lint_event_topology_bindings(
        plan,
        plan["extensions"]["event_topology"],
        plan["extensions"]["event_topology_bindings"],
    )["status"] == "complete"
    replacement = copy.deepcopy(plan["manual_template"]["hits"][0])
    replacement["multiplier"] = 2
    plan["calculation_event_overrides"] = declaration(override_item(
        "replace_event", target("manual_hit", "h0", "manual_template.hits"),
        {"event": replacement},
    ))
    report = compose_equipment_plan(plan, tmp_path / "absent.db")
    assert report["state"] == "pending"
    assert "override_event_target_unproven" in {
        row["code"] for row in report["missing_inputs"]
    }


def test_stale_or_wrong_supersedes_fails_atomically(tmp_path):
    database = tmp_path / "absent.db"
    plan = native_plan(dormant_hit=True)
    replacements = []
    for hit_id, multiplier in (("h0", 2), ("h1", 4)):
        event = copy.deepcopy(next(
            row for row in plan["manual_template"]["hits"] if row["id"] == hit_id
        ))
        event["multiplier"] = multiplier
        replacements.append(override_item(
            "replace_event", target("manual_hit", hit_id, "manual_template.hits"),
            {"event": event}, suffix=hit_id,
        ))
    plan["calculation_event_overrides"] = declaration(*replacements)
    accept_from_compose(plan, database)

    stale = copy.deepcopy(plan)
    stale["manual_template"]["hits"][0]["multiplier"] = 1.5
    stale_report = compose_equipment_plan(stale, database)
    assert stale_report["state"] == "pending"
    assert "override_baseline_stale" in {
        row["code"] for row in stale_report["missing_inputs"]
    }
    assert stale_report["plan"]["resolved_template"] is None

    wrong = native_plan(dormant_hit=True)
    wrong_items = copy.deepcopy(replacements)
    for item in wrong_items:
        item["accepted_fingerprint"] = None
        item["automatic_target_fingerprint"] = ZERO
    wrong["calculation_event_overrides"] = declaration(*wrong_items)
    first_pass = materialize_calculation_overrides(
        wrong, wrong["manual_template"], wrong["rotation_model"],
        wrong["mechanics_model"], database_snapshot_fingerprint=ZERO,
        engine_contract_fingerprint=ZERO,
    )
    wrong["calculation_event_overrides"]["basis"] = copy.deepcopy(first_pass["basis"])
    candidates = {
        row["override_id"]: row
        for row in first_pass["receipt"]["target_candidates"]
    }
    for item in wrong["calculation_event_overrides"]["items"]:
        candidate = candidates[item["override_id"]]
        item["automatic_target_fingerprint"] = candidate[
            "automatic_target_fingerprint"
        ]
        item["supersedes"] = [candidate["automatic_owner_id"]]
        item["accepted_fingerprint"] = acceptance_fingerprint(
            wrong["calculation_event_overrides"]["basis"], item
        )
    second = wrong["calculation_event_overrides"]["items"][1]
    second["supersedes"] = ["automatic:wrong-owner"]
    second["accepted_fingerprint"] = acceptance_fingerprint(
        wrong["calculation_event_overrides"]["basis"], second
    )
    materialized = materialize_calculation_overrides(
        wrong, wrong["manual_template"], wrong["rotation_model"],
        wrong["mechanics_model"], database_snapshot_fingerprint=ZERO,
        engine_contract_fingerprint=ZERO,
    )
    assert materialized["status"] == "pending"
    assert materialized["effective"] == materialized["automatic"]
    assert "override_supersedes_mismatch" in {
        row["code"] for row in materialized["issues"]
    }


def test_replace_input_is_limited_to_explicit_non_identity_parameters(tmp_path):
    plan = native_plan()
    row = override_item(
        "replace_input",
        target(
            "parameter_input", "h0", "manual_template.hits", component_id="id"
        ),
        {"value": "hijacked"},
    )
    plan["calculation_event_overrides"] = declaration(row)
    report = compose_equipment_plan(plan, tmp_path / "absent.db")
    assert report["state"] == "pending"
    assert "override_parameter_unsupported" in {
        issue["code"] for issue in report["missing_inputs"]
    }


@pytest.mark.parametrize(
    ("entity_id", "scope_id", "component_id"),
    [
        ("h0", "manual_template.hits", "reaction"),
        ("h0", "manual_template.hits", "damage_type_ids"),
        ("cast", "rotation_model.actions", "repeat"),
        ("h0", "mechanics_model.hit_events", "application"),
    ],
)
def test_replace_input_rejects_relationship_rich_values(
    tmp_path, entity_id, scope_id, component_id
):
    plan = native_plan()
    selected_target = target(
        "parameter_input", entity_id, scope_id, component_id=component_id,
    )
    if entity_id == "cast":
        selected_target["topology_node_id"] = "topology.h0"
        selected_target["execution_binding_id"] = "binding.h0"
    plan["calculation_event_overrides"] = declaration(override_item(
        "replace_input",
        selected_target,
        {"value": {}},
    ))
    report = compose_equipment_plan(plan, tmp_path / "absent.db")
    assert report["state"] == "pending"
    assert "override_parameter_unsupported" in {
        issue["code"] for issue in report["missing_inputs"]
    }


def test_replace_input_updates_one_reviewed_parameter_and_recalculates(tmp_path):
    database = tmp_path / "absent.db"
    plan = native_plan()
    plan["calculation_event_overrides"] = declaration(override_item(
        "replace_input",
        target(
            "parameter_input", "h0", "manual_template.hits",
            component_id="multiplier",
        ),
        {"value": 2},
    ))
    accept_from_compose(plan, database)
    calculated = run_plan_calc(plan, database)
    assert calculated["result"]["summary"]["total_damage"] == pytest.approx(90)
    assert calculated["result"]["calculation_override_receipt"]["coverage"][0][
        "impact_domains"
    ] == ["damage"]


@pytest.mark.parametrize(
    ("remove", "expected"),
    [
        ("event_topology", "override_topology_unavailable"),
        ("event_topology_bindings", "override_execution_bindings_unavailable"),
    ],
)
def test_declared_graph_identity_fails_closed_when_document_is_missing(remove, expected):
    plan = native_plan()
    del plan["extensions"][remove]
    event = copy.deepcopy(plan["manual_template"]["hits"][0])
    event["multiplier"] = 2
    plan["calculation_event_overrides"] = declaration(override_item(
        "replace_event", target("manual_hit", "h0", "manual_template.hits"),
        {"event": event},
    ))
    materialized = materialize_calculation_overrides(
        plan, plan["manual_template"], plan["rotation_model"],
        plan["mechanics_model"], database_snapshot_fingerprint=ZERO,
        engine_contract_fingerprint=ZERO,
    )
    assert materialized["status"] == "pending"
    assert {row["code"] for row in materialized["issues"]} == {expected}


def test_declared_event_relation_fails_closed_when_bindings_are_empty():
    plan = native_plan()
    plan["extensions"]["event_topology_bindings"]["bindings"] = []
    event = copy.deepcopy(plan["manual_template"]["hits"][0])
    event["multiplier"] = 2
    plan["calculation_event_overrides"] = declaration(override_item(
        "replace_event", target("manual_hit", "h0", "manual_template.hits"),
        {"event": event},
    ))
    materialized = materialize_calculation_overrides(
        plan, plan["manual_template"], plan["rotation_model"],
        plan["mechanics_model"], database_snapshot_fingerprint=ZERO,
        engine_contract_fingerprint=ZERO,
    )
    assert materialized["status"] == "pending"
    assert {row["code"] for row in materialized["issues"]} == {
        "override_execution_bindings_unavailable"
    }


def test_computed_value_replaces_one_bound_event_and_rederives_aggregates(tmp_path):
    database = tmp_path / "absent.db"
    plan = native_plan()
    plan["calculation_event_overrides"] = declaration(override_item(
        "replace_computed_value",
        target("computed_event", "h0", "result.hits"),
        {"expected": 999, "precision": {
            "kind": "approximate", "method": "工程夹具单事件估算",
            "uncertainty": "仅验证覆盖传播，不代表游戏事实", "state_effects": "none",
        }},
    ))
    accept_from_compose(plan, database)
    calculated = run_plan_calc(plan, database)
    result = calculated["result"]
    assert result["hits"][0]["expected"] == 999
    assert result["hits"][0]["crit"] is None
    assert result["summary"]["total_damage"] == 999
    assert result["summary"]["dps"] == 499.5
    assert result["summary"]["precision"] == "approximate"
    assert result["summary"]["approximate_event_count"] == 1
    proof = result["calculation_computed_value_overrides"]
    assert proof == calculated["result_receipt"][
        "calculation_computed_value_overrides"
    ]
    assert proof["rows"][0]["state_effects"] == "none"
    assert ["结果精度", "近似"] in summary_rows(result)
    assert any(
        row and row[0] == "h0" and "approximate" in row
        for row in hit_rows(result)
    )
    assert b"calculation_computed_value_overrides" in result_bytes(
        result, calculated["result_receipt"], "json"
    )


@pytest.mark.parametrize(
    ("field", "tampered"),
    [
        ("total_damage", 1000.0),
        ("by_character", {"a": 1.0}),
        ("by_skill", {"工程技能": 1.0}),
        ("dps", 1.0),
        ("precision", "exact"),
        ("approximate_event_count", 2),
        ("approximate_by_character", ["other"]),
        ("approximate_by_skill", ["other"]),
        ("approximation_disclosures", []),
    ],
)
def test_computed_value_receipt_rejects_rehashed_aggregate_projection_tamper(
    tmp_path, field, tampered,
):
    database = tmp_path / "absent.db"
    plan = native_plan()
    plan["calculation_event_overrides"] = declaration(override_item(
        "replace_computed_value",
        target("computed_event", "h0", "result.hits"),
        {"expected": 999, "precision": {
            "kind": "approximate", "method": "工程夹具单事件估算",
            "uncertainty": "仅验证覆盖传播", "state_effects": "none",
        }},
    ))
    accept_from_compose(plan, database)
    calculated = run_plan_calc(plan, database)
    result = copy.deepcopy(calculated["result"])
    receipt = copy.deepcopy(calculated["result_receipt"])
    result["summary"][field] = copy.deepcopy(tampered)
    for proof in (
        result["calculation_computed_value_overrides"],
        receipt["calculation_computed_value_overrides"],
    ):
        proof["aggregation"][field] = copy.deepcopy(tampered)
        proof["proof_fingerprint"] = fingerprint({
            key: value for key, value in proof.items()
            if key != "proof_fingerprint"
        })
    with pytest.raises(ValueError):
        validate_computed_value_result_receipt(result, receipt)


def test_computed_value_receipt_rejects_rehashed_window_and_dps_tamper(tmp_path):
    database = tmp_path / "absent.db"
    plan = native_plan()
    plan["calculation_event_overrides"] = declaration(override_item(
        "replace_computed_value",
        target("computed_event", "h0", "result.hits"),
        {"expected": 999, "precision": {
            "kind": "exact", "method": "工程夹具独立公式",
            "uncertainty": "声明输入内无额外不确定性", "state_effects": "none",
        }},
    ))
    accept_from_compose(plan, database)
    calculated = run_plan_calc(plan, database)
    result = copy.deepcopy(calculated["result"])
    receipt = copy.deepcopy(calculated["result_receipt"])
    result["summary"]["dps"] = 999 / 4
    for proof in (
        result["calculation_computed_value_overrides"],
        receipt["calculation_computed_value_overrides"],
    ):
        proof["aggregation"]["window"]["seconds"] = 4
        proof["aggregation"]["dps"] = 999 / 4
        proof["proof_fingerprint"] = fingerprint({
            key: value for key, value in proof.items()
            if key != "proof_fingerprint"
        })
    with pytest.raises(ValueError):
        validate_computed_value_result_receipt(result, receipt)


def test_computed_value_requires_precision_and_cannot_write_aggregate():
    plan = native_plan()
    selected = target("computed_event", "h0", "result.hits")
    for payload in ({"expected": 999}, {"expected": 999, "total_damage": 999}):
        plan["calculation_event_overrides"] = declaration(override_item(
            "replace_computed_value", selected, payload,
        ))
        materialized = materialize_calculation_overrides(
            plan, plan["manual_template"], plan["rotation_model"],
            plan["mechanics_model"], database_snapshot_fingerprint=ZERO,
            engine_contract_fingerprint=ZERO,
        )
        assert materialized["status"] == "pending"
        assert materialized["issues"]


def test_state_transition_replaces_engine_input_before_replay(tmp_path):
    database = tmp_path / "absent.db"
    plan = native_plan()
    transition = {
        "id": "change", "t": 0.5, "order": 20,
        "condition": "engineering.flag", "value": True,
        "repeat": ONCE, **EVIDENCE,
    }
    plan["mechanics_model"]["condition_events"].append(transition)
    topology = plan["extensions"]["event_topology"]
    topology["nodes"].append({
        "node_id": "topology.change", "kind": "state", "status": "bound",
        "schedule": {
            "kind": "point",
            "anchor": {"kind": "action", "ref_id": "cast", "phase": "start"},
            "offset_seconds": 0.5, "order": 20,
        },
        "source_ids": ["source.engineering"], "parameter_slots": [], "note": "",
    })
    topology["sources"][0]["node_ids"].append("topology.change")
    bindings = plan["extensions"]["event_topology_bindings"]
    bindings["topology_fingerprint"] = event_topology_fingerprint(topology)
    bindings["bindings"].append({
        "binding_id": "binding.change", "node_id": "topology.change",
        "target_kind": "mechanics", "pointer": "/mechanics_model/condition_events/0",
        "target_fingerprint": fingerprint(transition), "note": "状态事件工程绑定",
    })
    replacement = {**transition, "value": False}
    plan["calculation_event_overrides"] = declaration(override_item(
        "replace_state_transition",
        target("state_transition", "change", "mechanics_model.condition_events"),
        {"transition": replacement},
    ))
    accept_from_compose(plan, database)
    report = compose_equipment_plan(plan, database)
    assert report["state"] == "ready"
    assert report["mechanics_model"]["condition_events"][0]["value"] is False
    assert report["calculation_override_receipt"]["status"] == "applied"


def test_computed_approximation_propagates_through_external_total(tmp_path):
    database = tmp_path / "absent.db"
    plan = native_plan()
    plan["calculation_event_overrides"] = declaration(override_item(
        "replace_computed_value",
        target("computed_event", "h0", "result.hits"),
        {"expected": 100, "precision": {
            "kind": "approximate", "method": "单事件工程估算",
            "uncertainty": "正负一个离散采样单位", "state_effects": "none",
        }},
    ))
    accept_from_compose(plan, database)
    plan["external_damage_supplements"] = [{
        "id": "external.one", "label": "独立额外事件", "character": "a",
        "skill": "外部工程伤害", "t": 1.5, "expected": 12,
        "action_id": None, "coverage_key": "external.coverage.one",
        "event_id": "external.event.one", "reason": "独立新增事件",
        "calculation_note": "工程常量", "non_overlap_note": "不替换h0",
        "source_refs": ["工程夹具"], "accepted_fingerprint": None,
        "precision": None,
    }]
    report = compose_equipment_plan(plan, database)
    item_review = report["external_damage_review"]["items"][0]
    plan["external_damage_supplements"][0].update(
        item_review["required_confirmation"]
    )
    result = run_plan_calc(plan, database)["result"]
    totals = result["calculation_totals"]
    assert totals["engine_total_damage"] == 100
    assert totals["external_total_damage"] == 12
    assert totals["total_damage"] == 112
    assert totals["precision"] == "approximate"
    assert totals["approximate_event_count"] == 1
    assert totals["approximate_by_character"] == ["a"]
    assert totals["approximate_by_skill"] == ["工程技能"]


def test_external_and_override_cannot_share_one_coverage_owner(tmp_path):
    database = tmp_path / "absent.db"
    plan = native_plan()
    plan["calculation_event_overrides"] = declaration(override_item(
        "replace_input",
        target(
            "parameter_input", "h0", "manual_template.hits",
            component_id="multiplier",
        ),
        {"value": 2},
    ))
    plan["external_damage_supplements"] = [{
        "id": "external.one", "label": "冲突范围", "character": "a",
        "skill": "外部工程伤害", "t": 1.5, "expected": 12,
        "action_id": None, "coverage_key": "coverage.one",
        "event_id": "external.event.one", "reason": "所有权冲突夹具",
        "calculation_note": "工程常量", "non_overlap_note": "故意冲突",
        "source_refs": ["工程夹具"], "accepted_fingerprint": None,
        "precision": None,
    }]
    accept_from_compose(plan, database)
    report = compose_equipment_plan(plan, database)
    assert report["state"] == "pending"
    assert "override_coverage_duplicate" in {
        issue["code"] for issue in report["missing_inputs"]
    }


def test_materialization_and_external_conflict_without_override_is_rejected():
    plan = native_plan()
    plan["external_damage_supplements"] = [{
        "id": "external.one", "label": "冲突范围", "character": "a",
        "skill": "外部工程伤害", "t": 1.5, "expected": 12,
        "action_id": None, "coverage_key": "coverage.one",
        "event_id": "external.event.one", "reason": "所有权冲突夹具",
        "calculation_note": "工程常量", "non_overlap_note": "故意冲突",
        "source_refs": ["工程夹具"], "accepted_fingerprint": None,
        "precision": None,
    }]
    materialized = materialize_calculation_overrides(
        plan, plan["manual_template"], plan["rotation_model"],
        plan["mechanics_model"], database_snapshot_fingerprint=ZERO,
        engine_contract_fingerprint=ZERO,
        additional_ownership_claims=[{
            "owner_id": "materialization:sequence.one",
            "owner_kind": "materialization",
            "coverage_key": "coverage.one",
            "target_identity": "agent_materialization|entry_id=sequence.one",
            "source_fingerprint": "f" * 64,
        }],
    )
    assert materialized["status"] == "pending"
    assert materialized["ownership_audit"] is None
    assert {row["code"] for row in materialized["issues"]} == {
        "override_coverage_duplicate"
    }


def test_no_override_still_publishes_verified_cross_lane_ownership_audit():
    plan = native_plan()
    materialized = materialize_calculation_overrides(
        plan, plan["manual_template"], plan["rotation_model"],
        plan["mechanics_model"], database_snapshot_fingerprint=ZERO,
        engine_contract_fingerprint=ZERO,
        additional_ownership_claims=[{
            "owner_id": "materialization:sequence.one",
            "owner_kind": "materialization",
            "coverage_key": "coverage.materialized",
            "target_identity": "agent_materialization|entry_id=sequence.one",
            "source_fingerprint": "f" * 64,
        }],
    )
    assert materialized["status"] == "not_declared"
    audit = materialized["ownership_audit"]
    assert audit["status"] == "verified"
    assert audit["claims"][0]["coverage_key"] == "coverage.materialized"
    assert audit["ownership_ledger_fingerprint"] == fingerprint(audit["claims"])


def test_agent_materialization_and_override_cannot_share_one_coverage_owner(
        tmp_path):
    database = tmp_path / "absent.db"
    plan = native_plan(dormant_hit=True)
    plan["calculation_event_overrides"] = declaration(override_item(
        "replace_input",
        target(
            "parameter_input", "h0", "manual_template.hits",
            component_id="multiplier",
        ),
        {"value": 2},
    ))
    topology = plan["extensions"]["event_topology"]
    topology["nodes"].append({
        "node_id": "topology.action.owner",
        "kind": "action_anchor",
        "status": "bound",
        "schedule": {
            "kind": "point",
            "anchor": {"kind": "action", "ref_id": "cast", "phase": "start"},
            "offset_seconds": 0,
            "order": 0,
        },
        "source_ids": ["source.engineering"],
        "parameter_slots": [],
        "note": "",
    })
    topology["nodes"].append({
        "node_id": "topology.sequence.owner",
        "kind": "finite_sequence",
        "status": "bound",
        "schedule": {
            "kind": "point",
            "anchor": {
                "kind": "node", "ref_id": "topology.action.owner",
                "phase": "point",
            },
            "offset_seconds": 0,
            "order": 1,
        },
        "source_ids": ["source.engineering"],
        "parameter_slots": [],
        "note": "",
    })
    topology["sources"][0]["node_ids"].extend([
        "topology.action.owner", "topology.sequence.owner",
    ])
    topology["edges"].append({
        "edge_id": "edge.sequence.owner",
        "kind": "causes",
        "from_node_id": "topology.sequence.owner",
        "to_node_id": "topology.h1",
        "status": "bound",
        "source_ids": ["source.engineering"],
        "note": "",
    })
    plan["extensions"]["event_topology_bindings"][
        "topology_fingerprint"] = event_topology_fingerprint(topology)
    document = new_agent_execution_materialization(
        plan, topology, "materialization.ownership.fixture")
    document["sources"] = [{
        "source_id": "source.engineering", "kind": "agent_research",
        "reference": "fixture://materialization-owner",
        "fingerprint": "c" * 64, "status": "verified", "note": "",
    }]
    document["entries"] = [{
        "entry_id": "materialized.owner", "family": "finite_sequence",
        "handler_version": 1, "node_ids": ["topology.sequence.owner"],
        "source_ids": ["source.engineering"],
        "coverage_keys": ["coverage.one"],
        "payload": {
            "sequence_id": "sequence.owner", "interrupt_policy": "none",
            "steps": [{
                "step_id": "step.owner", "offset_seconds": 0.2,
                "order": 1, "emits_node_ids": ["topology.h1"],
            }],
        },
        "note": "所有权冲突夹具",
    }]
    plan["extensions"]["agent_execution_materialization"] = document

    accept_from_compose(plan, database)
    assert lint_agent_execution_materialization(
        plan, topology, document)["status"] == "ready"
    report = compose_equipment_plan(plan, database)
    assert report["state"] == "pending"
    assert "override_coverage_duplicate" in {
        issue["code"] for issue in report["missing_inputs"]
    }


def test_review_identifies_manual_baseline_owner_before_override(tmp_path):
    database = tmp_path / "absent.db"
    plan = native_plan()
    plan["calculation_event_overrides"] = declaration(override_item(
        "replace_computed_value",
        target("computed_event", "h0", "result.hits"),
        {"expected": 123.0, "precision": {
            "kind": "exact", "method": "独立公式复核",
            "uncertainty": "声明输入范围内无额外不确定性",
            "state_effects": "none",
        }},
    ))
    candidate = compose_equipment_plan(plan, database)[
        "calculation_override_receipt"
    ]["target_candidates"][0]
    assert candidate["current_owner_kind"] == "manual"
    assert candidate["current_owner_id"].startswith("manual:")
    assert candidate["automatic_owner_id"] == candidate["current_owner_id"]
