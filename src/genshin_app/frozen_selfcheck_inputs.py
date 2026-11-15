"""Fixed synthetic inputs for packaged end-to-end qualification.

The DB supplies real public identities only. All timings, energy and damage
are explicitly engineering inputs, never asserted as those characters' skills.
The real App must still compile, execute, accept and publish them independently.
"""
from __future__ import annotations

import copy
import uuid

SOURCE = "固定离线工程输入，不代表对应数据库角色的真实技能机制。"
EVIDENCE = {"status": "assumed", "source": SOURCE}
EXPECTED_DAMAGE = 594.0  # Cryo finite 2×54 + Pyro melt 216 + two nonreacting bursts 2×135.


def verify_fixed_world():
    """Actually instantiate and advance the formerly missing world module.

    This separate world proof is not mislabeled as the App's single-target
    coordinator. Both are mandatory parts of the one packaged qualification.
    """
    from genshin_engine.elemental_world import ElementalWorldRuntime
    world = ElementalWorldRuntime()
    model = {"schema_version": 1, "ruleset": "gauge-multi-aura-v4",
             "target": {"id": "engineering.enemy", "kind": "ordinary", "knowledge": "known", "initial_auras": [],
                        "freeze": {"freezable": True, "resistance": 0, **EVIDENCE}, **EVIDENCE},
             "profiles": [{"id": element, "element": element, "application": "apply", "gauge_units": 1,
                           "icd": {"kind": "none"}, **EVIDENCE} for element in ("cryo", "pyro")],
             "swirl_damage": {"icd": {"kind": "sequence", "reset_seconds": .5, "sequence": [1, 1, 0]}, **EVIDENCE}}
    target = world.register_target(model, area_ids=("engineering.area",))
    applications = []
    for tick, element, actor in ((1_000_000, "cryo", "a"), (1_200_000, "pyro", "b")):
        world.advance(tick)
        applications.append(target.attempt(event_id=f"engineering.{element}", action_id="engineering.action", actor=actor,
            time=tick, application={"profile_id": element, "target_id": "engineering.enemy", "landed": True}))
    if not any((row.get("reaction") or {}).get("type") == "melt" for row in applications):
        raise RuntimeError("selfcheck_world_melt_missing")
    return {"coordinator": "ElementalWorldRuntime", "ruleset": model["ruleset"], "applications": applications,
            "world_time_tick": world.time, "target_ids": world.targets_in_area("engineering.area")}


def build_fixed_inputs(root, database):
    from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
    from genshin_app.equipment.event_topology import AUTHORITY, CONTRACT_ID
    from genshin_app.equipment.event_topology_binding import new_event_topology_bindings
    from genshin_app.equipment.files import fingerprint
    from genshin_app.frozen_selfcheck_calculation import FLOW_ATTRIBUTE_LIMITS
    from genshin_app.whiteboard import default_panel, new_whiteboard
    from genshin_db.queries import list_characters

    rows = list_characters(database, page_size=2)["items"]
    if len(rows) != 2:
        raise RuntimeError("selfcheck_requires_two_public_character_identities")
    board = new_whiteboard()
    board.update(title="离线完整链工程样例（非角色机制实验）", duration=6, notes=SOURCE)
    panel = default_panel()
    for key in panel:
        panel[key] = {element: 0 for element in panel[key]} if isinstance(panel[key], dict) else 0
    panel.update(hp=12000, atk=100, crit_rate=0, crit_dmg=.5, em=0, energy_recharge=1)
    panel["def"] = 500
    for identity, row in zip(("a", "b"), rows, strict=True):
        board["team"].append({"id": identity, "db_character_id": row["id"], "name": row["name"],
            "level": 90, "constellation": 0, "weapon": None, "artifact_sets": [],
            "panel_basis_mode": "manual", "panel": copy.deepcopy(panel), "notes": SOURCE})
    specs = [("e.a", "a", "action.elemental_skill", 1),
             ("switch.b", "b", "action.switch", 1.8),
             ("e.b", "b", "action.elemental_skill", 2.2),
             ("switch.a", "a", "action.switch", 3),
             ("q.a.first", "a", "action.elemental_burst", 3.2),
             ("q.a.second", "a", "action.elemental_burst", 5.2)]
    board["actions"] = [{"id": identity, "character": actor, "action_type_id": kind,
                          "label": identity, "start": start, "variant": "tap", "notes": SOURCE}
                         for identity, actor, kind, start in specs]
    seed = copy.deepcopy(board)
    seed.update(team=[], actions=[])
    dry = ArtifactCandidateSession(uuid.uuid4().hex, root / "fixed-input-preflight", database, "whiteboard", seed)
    create = {"tool": "gs_patch_whiteboard", "arguments": {"expected_revision": 0, "reason": SOURCE,
        "operations": [{"op": "replace", "path": "/team", "value": board["team"]},
                       {"op": "replace", "path": "/actions", "value": board["actions"]}]}}
    reply = dry.call_tool(create["tool"], create["arguments"])
    if reply.get("code") != 0:
        raise RuntimeError("selfcheck_empty_board_population_failed:" + str(reply))
    prepare = {"tool": "gs_transition_phase", "arguments": {"target_phase": "numeric_model", "expected_revision": 1,
        "expected_stage_revision": 0, "reason": SOURCE, "prepare_calculation": True, "energy_intent": "single_window_legal"}}
    reply = dry.call_tool(prepare["tool"], prepare["arguments"])
    if reply.get("code") != 0:
        raise RuntimeError("selfcheck_input_preparation_failed:" + str(reply))
    plan = dry.candidate
    rotation = copy.deepcopy(plan["rotation_model"])
    rotation["initial_frontline"] = "a"
    for row in rotation["characters"]:
        row.update(element="geo", energy_capacity=10, initial_energy=10, **EVIDENCE)
    for index, ability in enumerate(rotation["abilities"]):
        ability.update(cooldown=None, energy=None, **EVIDENCE)
        if specs[index][2] == "action.switch":
            ability["character"] = "a" if specs[index][1] == "b" else "b"
        if specs[index][2] == "action.elemental_burst":
            ability["energy"] = {"required": 10, "amount": 10}
    for row in rotation["actions"]:
        duration = .1 if row["id"].startswith("switch") else .3
        row.update(occupation_end=round(row["start"] + duration, 6), switch_allowed_at=round(row["start"] + duration, 6), **EVIDENCE)
    rotation["events"] = [
        {"id": "spend.first", "type": "energy_spend", "t": 3.2, "order": 20, "repeat": {"mode": "once"},
         "action_id": "q.a.first", "character": "a", "required": 10, "amount": 10, **EVIDENCE},
        {"id": "restore", "type": "energy_direct", "t": 4, "order": 20, "repeat": {"mode": "once"},
         "amounts": {"a": 10}, **EVIDENCE},
        {"id": "spend.second", "type": "energy_spend", "t": 5.2, "order": 20, "repeat": {"mode": "once"},
         "action_id": "q.a.second", "character": "a", "required": 10, "amount": 10, **EVIDENCE},
    ]
    attacks = [("e.a", "a", 1.2, 1, "damage.elemental_skill"), ("e.b", "b", 2.4, 2, "damage.elemental_skill"),
               ("q.a.first", "a", 3.4, 3, "damage.elemental_burst"), ("q.a.second", "a", 5.4, 3, "damage.elemental_burst")]
    hits = [{"id": "hit." + action, "t": time, "character": actor, "skill": action, "hit_name": "工程命中 " + action,
             "damage_type_ids": [kind], "damage_system": "classic", "element": "cryo" if index == 0 else "pyro", "multiplier": multiplier}
            for index, (action, actor, time, multiplier, kind) in enumerate(attacks)]
    pattern = {"id": "finite.e.a", "prototype_hit_id": hits[0]["id"], "action_id": "e.a",
               "offsets": [.2, .4], "orders": [30, 31],
               "application": {"profile_id": "cryo", "target_id": "enemy", "landed": True}}
    descriptors = [{"hit_id": hit["id"], "action_id": attacks[index][0], "order": 30,
                    "application": {"profile_id": hit["element"], "target_id": "enemy", "landed": True}}
                   for index, hit in enumerate(hits) if index != 0]
    # These engineering components are all cast-phase direct hits. Preserve
    # the execution horizon while exercising the current automatic report
    # contract instead of inheriting a missing anchor from an empty board.
    rotation['damage_report'] = {'contract': 'damage-report-window-v1', 'mode': 'last_active_damage',
        'active_hit_ids': [*[f"{pattern['id']}:{index + 1}" for index in range(len(pattern['offsets']))],
                           *[hit['id'] for hit in hits[1:]]]}
    mechanics = {"schema_version": 1, "rules": [], "hit_events": descriptors, "hit_patterns": [pattern],
        "condition_events": [], "initial_state": {"rules": []},
        "elemental_model": {"schema_version": 1, "ruleset": "gauge-basic-v1",
            "target": {"id": "enemy", "kind": "ordinary", "knowledge": "known", "initial_aura": None, **EVIDENCE},
            "profiles": [{"id": element, "element": element, "application": "apply", "gauge_units": 1,
                          "icd": {"kind": "none"}, **EVIDENCE} for element in ("cryo", "pyro")]}}
    # Exercise the published authoring contract and per-request execution
    # budget through DSH, App compilation, result proof and fresh file reload.
    # This graph adds no stat or damage and cannot change the 594 oracle.
    mechanics["attribute_model"] = {"contract": "attribute-ledger-v2", "initial_basis": [
        {"actor": actor, "included_components": []} for actor in ("a", "b")], "effect_classifications": []}
    buff = {"id": "engineering.team.bonus", "name": "工程持续增伤", "source": SOURCE,
            "start": 1, "end": 3, "scope": "team", "extensions": {"owner": "a", "source_id": "source.engineering"}, "effects": [
                {"id": "engineering.bonus", "zone": "dmg_bonus", "stat": "all", "value": .2, "benefit_type_id": "benefit.dmg_bonus.all", "applies_to": {"mode": "all"}}]}
    topology = {"contract_id": CONTRACT_ID, "schema_version": 1, "topology_id": "topology.selfcheck", "authority": AUTHORITY,
        "declaration": {"character_semantics": "agent_declared", "completeness": "declared_complete", "note": SOURCE},
        "action_checkpoint": dry._current_action_checkpoint(),
        "dependencies": [{"dependency_id": "database_snapshot", "kind": "database_snapshot", "fingerprint": fingerprint(dry.database_snapshot), "required": True}],
        "sources": [], "nodes": [], "edges": []}
    def node(identity, kind, action, offset=0):
        return {"node_id": identity, "kind": kind, "status": "bound", "source_ids": ["source.engineering"],
                "schedule": {"kind": "point", "anchor": {"kind": "action", "ref_id": action, "phase": "start"}, "offset_seconds": offset, "order": 30},
                "parameter_slots": [], "note": SOURCE}
    topology["nodes"] = [node("anchor." + row[0], "action_anchor", row[0]) for row in specs]
    for row in topology["nodes"]:
        row["schedule"]["order"] = 0
    topology["nodes"] += [node("damage." + action, "damage", action, round(time - next(x[3] for x in specs if x[0] == action), 6))
                           for action, _, time, _, _ in attacks]
    topology["nodes"][len(specs)]["kind"] = "finite_sequence"
    topology["nodes"][len(specs)]["schedule"] = {"kind": "explicit_offsets", "anchor": {"kind": "action", "ref_id": "e.a", "phase": "start"},
        "occurrences": [{"occurrence_id": "tick.1", "offset_seconds": .2, "order": 30},
                        {"occurrence_id": "tick.2", "offset_seconds": .4, "order": 31}]}
    topology["nodes"].append(node("buff", "buff_interval", "e.a"))
    topology["nodes"] += [node("resource.first", "resource", "q.a.first"), node("resource.restore", "resource", "q.a.first", .8),
                           node("resource.second", "resource", "q.a.second")]
    topology["sources"] = [{"source_id": "source.engineering", "category": "scenario", "owner_ref": None, "description": SOURCE,
        "disposition": "modeled", "node_ids": [row["node_id"] for row in topology["nodes"]], "reason": None,
        "source_refs": [{"kind": "agent_research", "reference": SOURCE, "fingerprint": None, "status": "declared"}]}]
    bindings = new_event_topology_bindings(topology)
    for identity, kind, pointer, value in [
        ("damage.e.a", "mechanics", "/mechanics_model/hit_patterns/0", pattern),
        *[("damage." + action, "hit", f"/manual_template/hits/{index}", hits[index]) for index, (action, *_rest) in enumerate(attacks) if index != 0],
        ("buff", "buff", "/manual_template/buffs/0", buff),
        *[(identity, "rotation_event", f"/rotation_model/events/{index}", rotation["events"][index])
          for index, identity in enumerate(("resource.first", "resource.restore", "resource.second"))],
    ]:
        bindings["bindings"].append({"binding_id": "binding." + identity, "node_id": identity,
            "target_kind": kind, "pointer": pointer, "target_fingerprint": fingerprint(value), "note": SOURCE})
    patch = {"tool": "gs_patch_candidate", "arguments": {"expected_revision": 2, "reason": SOURCE, "operations": [
        {"op": "add", "path": "/calculation_options", "value": {"schema_version": 1, "attribute_limits": copy.deepcopy(FLOW_ATTRIBUTE_LIMITS)}},
        {"op": "replace", "path": "/manual_template/enemy", "value": {"level": 90, "resistance": {"default": .1}}},
        {"op": "replace", "path": "/manual_template/hits", "value": hits},
        {"op": "replace", "path": "/manual_template/buffs", "value": [buff]},
        {"op": "replace", "path": "/manual_effect_dependencies", "value": [{"effect_ref": {"buff_id": buff["id"], "effect_id": "engineering.bonus"},
            "mode": "source_only", "context_fingerprint": None, "note": "固定工程常量不依赖配装或角色机制。"}]},
        {"op": "replace", "path": "/rotation_model", "value": rotation},
        {"op": "replace", "path": "/mechanics_model", "value": mechanics},
        {"op": "replace", "path": "/extensions/whiteboard_handoff/completion_notes", "value": [SOURCE]},
        {"op": "replace", "path": "/extensions/event_topology", "value": topology},
        {"op": "replace", "path": "/extensions/event_topology_bindings", "value": bindings},
    ]}}
    refresh = {"tool": "gs_refresh_data_refs", "arguments": {"expected_revision": 3, "reason": SOURCE}}
    adopt = {"tool": "gs_adopt_attributes", "arguments": {"expected_revision": 4, "note": SOURCE}}
    calculate = {"tool": "gs_calculate", "arguments": {"expected_revision": 5}}
    # Use the existing App itself to validate only the fixed fixture inputs.
    # The DSH case receives these inputs, never this dry result/receipt.
    for call in (patch, refresh, adopt, calculate):
        reply = dry.call_tool(call["tool"], call["arguments"])
        if reply.get("code") != 0:
            raise RuntimeError("selfcheck_fixed_input_invalid:" + call["tool"] + ":" + str(reply))
    total = dry.calculated["result"]["summary"]["total_damage"]
    if abs(total - EXPECTED_DAMAGE) > 1e-8:
        raise RuntimeError(f"selfcheck_independent_arithmetic_mismatch:{total}")
    from genshin_app.frozen_selfcheck_calculation import (
        verify_appendix_index_response,
        verify_flow_budget,
        verify_flow_damage_explanations,
    )
    verify_flow_budget(dry.calculated)
    verify_flow_damage_explanations(dry.calculated)
    if dry.calculation_acceptance()["status"] != "accepted":
        raise RuntimeError("selfcheck_fixed_input_not_accepted:" + str(dry.calculation_acceptance()))
    reads = [
        {"tool": "gs_get_character_appendix_index", "arguments": {"db_character_id": board["team"][0]["db_character_id"]}},
        {"tool": "gs_read_context", "arguments": {"resource": "special_reaction_capabilities"}},
        {"tool": "gs_read_context", "arguments": {"resource": "mechanics_capabilities"}},
        {"tool": "gs_read_context", "arguments": {"resource": "mechanics_capabilities"}},
        {"tool": "gs_read_context", "arguments": {"resource": "special_reaction_capabilities", "pointer": "/input_contract/schemas/direct_stellar_damage_profile"}},
        {"tool": "gs_read_context", "arguments": {"resource": "capabilities", "pointer": "/schema/properties/mechanics_model"}},
        {"tool": "gs_read_context", "arguments": {"resource": "capabilities", "pointer": "/semantic_constraints"}},
    ]
    navigation = reads[0]
    reply = dry.call_tool(navigation["tool"], navigation["arguments"])
    verify_appendix_index_response(reply, board["team"][0]["db_character_id"])
    return seed, [create, prepare, *reads, patch, refresh, adopt, calculate]
