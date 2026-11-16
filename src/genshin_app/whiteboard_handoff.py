"""Agent handoff construction for validated whiteboard documents."""
from __future__ import annotations

import copy
import sqlite3

from genshin_app.equipment.panel_provenance import provenance_of
from genshin_app.equipment.schema import new_plan, validate_plan
from genshin_app.whiteboard_export_publisher import publish_whiteboard_handoff
from genshin_app.whiteboard_identity import board_fingerprint
from genshin_app.whiteboard_reference_gateway import WhiteboardReferenceGateway
from genshin_app.whiteboard_schema import (
    SCHEMA_VERSION,
    _fail,
    _whiteboard_ref,
    validate_whiteboard,
)
from genshin_runtime.diagnostics import diagnosed


def _context_state(intents):
    declared = sum(intent["context"] is not None for intent in intents)
    return ("none" if not intents else "unknown" if declared == 0
            else "declared" if declared == len(intents) else "mixed_pending")


def _prepare_action_admission_handoff(board, rotation, action_map, references):
    """Build non-authoritative proposals and explicit initial-state rows.

    A proposal deliberately omits ``accepted_fingerprint`` and the engine-private
    marker.  The Agent/App must still run the ordinary fresh preview and confirm
    that preview before the compiler can write an executable certificate.
    """
    intents = board["action_admission_intents"]
    if not intents:
        return []
    native_by_board = {row["board_action_id"]: row["native_action_id"] for row in action_map}
    native_actions = {row["id"]: row for row in rotation["actions"]}
    proposals = []
    for intent in intents:
        native_action_id = native_by_board[intent["action_id"]]
        ability_id = native_actions[native_action_id]["ability_id"]
        proposal = {
            "intent_id": intent["id"],
            "action_id": intent["action_id"],
            "native_action_id": native_action_id,
            "ability_id": ability_id,
            "binding": {
                "id": intent["id"],
                "ability_id": ability_id,
                "version_line": intent["version_line"],
                "selection": copy.deepcopy(intent["selection"]),
                "variant": intent["variant"],
                "model_ref": copy.deepcopy(intent["model_ref"]),
            },
            "status": "pending_database",
            "cooldown_group": None,
            "initial_cooldown": None,
        }
        if references.admission_available:
            try:
                profile = references.action_admission_profile(intent)
                if profile.get("status") == "ready":
                    group = profile["compiled_values"]["cooldown"]["group"]
                    proposal["cooldown_group"] = group
                    if intent["context"] is None:
                        proposal["status"] = "pending_context"
                    elif intent["initial_ready_in"] is None:
                        proposal["status"] = "pending_initial_state"
                    else:
                        proposal["status"] = "reviewed_reference"
                        initial = {"character": intent["actor"], "group": group,
                                   "ready_in": copy.deepcopy(intent["initial_ready_in"])}
                        if any(row.get("character") == initial["character"]
                               and row.get("group") == group
                               for row in rotation["initial_cooldowns"]):
                            _fail("多个动作接纳意图映射到同一初始冷却组；首片不支持能力别名",
                                  "action_admission_intents", "action_admission_alias")
                        rotation["initial_cooldowns"].append(initial)
                        proposal["initial_cooldown"] = copy.deepcopy(initial)
                else:
                    proposal["status"] = "pending_model"
            except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
                proposal["status"] = "pending_model"
        proposals.append(proposal)
    return proposals


@diagnosed('whiteboard', 'make-handoff-v1', metadata=lambda arguments: {
    "board_id": arguments["board"]["board_id"], "revision": arguments["board"]["revision"],
    "intent_fingerprint": board_fingerprint(arguments["board"])})
def make_handoff(board, db_path=None):
    validate_whiteboard(board)
    if not board["team"] or not board["actions"]:
        _fail("严格计算前请至少选择一名角色并添加一个动作；Agent可以继续在候选白板中补齐，无需切换聊天模式", reason="whiteboard_incomplete")
    references = WhiteboardReferenceGateway(db_path)
    plan = new_plan()
    plan.update(plan_version=3, mechanics_model=None)
    if 'calculation_options' in board:
        plan['calculation_options'] = copy.deepcopy(board['calculation_options'])
    # A whiteboard may persist plan-local proposals, but no App lookup,
    # attestation or user approval is reusable across the whiteboard→native
    # plan boundary.  Keep the sourced business declaration and require the
    # native session to run the ordinary D2 chain again.
    plan["provisional_entities"] = []
    for declared in board.get("provisional_entities", []):
        row = copy.deepcopy(declared)
        for key in ("database_lookup", "public_data_diff", "payload_fingerprint",
                    "attestation", "approval"):
            row[key] = None
        plan["provisional_entities"].append(row)
    template = plan["manual_template"]
    template.update(loop_duration=board["duration"], characters=[])
    template["meta"].update(name=board["title"], version_line=board["version_line"], assumptions=[
        "白板交接：固定面板按已包含武器和套装常驻属性处理，不重复加算；数值来源见各角色的面板来源记录。",
        "敌人默认90级、默认10%抗性仅为待核实场景占位，Agent须在交接说明中明确最终采用条件。"])
    rotation = {"schema_version": 1, "period": board["duration"], "characters": [],
                "abilities": [], "actions": [], "events": [], "initial_cooldowns": []}
    if "damage_report" in board:
        rotation["damage_report"] = copy.deepcopy(board["damage_report"])
    line = board["version_line"]
    intents = board["capacity_intents"]
    admission_intents = board["action_admission_intents"]
    metadata = {"schema_version": SCHEMA_VERSION, "equipment_input_contract": "equipment-presence-v1", "board_fingerprint": board_fingerprint(board), "board": copy.deepcopy(board),
                "capacity_intents": copy.deepcopy(intents), "capacity_context_state": _context_state(intents),
                "action_admission_intents": copy.deepcopy(admission_intents),
                "action_admission_context_state": _context_state(admission_intents),
                "action_admission_proposals": [],
                "action_map": [], "auxiliary_action_ids": [], "completion_notes": [],
                "constellations": {actor["id"]: actor["constellation"] for actor in board["team"]}}
    for actor in board["team"]:
        provenance = provenance_of(actor)
        stats = {key: value for key, value in actor["panel"].items() if key != "dmg_bonus" and value is not None}
        stats["dmg_bonus"] = {key: value for key, value in actor["panel"]["dmg_bonus"].items() if value is not None}
        template["characters"].append({"id": actor["id"], "name": actor["name"], "level": actor["level"], "stats": stats})
        weapon = actor["weapon"]
        character_entity_ref = _whiteboard_ref(
            actor.get("character_ref"), entity_type="character",
            database_id=actor.get("db_character_id"), version_line=line,
            path="team.character_ref",
            registry={(row["entity_type"], row["provisional_id"])
                      for row in board.get("provisional_entities", [])},
        )
        managed_weapon, version = None, None
        if weapon is not None:
            weapon_entity_ref = _whiteboard_ref(
                weapon.get("entity_ref"), entity_type="weapon",
                database_id=weapon.get("weapon_id"), version_line=line,
                path="team.weapon.entity_ref",
                registry={(row["entity_type"], row["provisional_id"])
                          for row in board.get("provisional_entities", [])},
            )
            managed_weapon = {"weapon_id": weapon["weapon_id"], "level": weapon["level"], "ascension": weapon["ascension"],
                              "entity_ref": weapon_entity_ref,
                              "refinements": [], "selected_by": provenance["entered_by"]}
            if weapon["refinement"] is not None:
                managed_weapon["refinement"] = weapon["refinement"]
            if (weapon_entity_ref["kind"] == "database"
                    and references.weapon_available):
                detail = references.weapon_detail(weapon["weapon_id"], line)
                managed_weapon["refinements"] = [{"source_index": effect["source_index"], "affix_group_id": effect["affix_group_id"],
                    "refinement": weapon["refinement"]} for effect in detail["effects"]]
                version = detail["data_version"]
        if (version is None and character_entity_ref["kind"] == "database"
                and references.admission_available):
            # A user-supplied panel with no weapon still uses the selected
            # catalog character. Stamp its actual source during conversion,
            # rather than generating a plan that immediately looks outdated.
            version = references.character_data_version(actor["db_character_id"], line)
        plan["equipment_inputs"][actor["id"]] = {"mode": "panel", "panel_policy": "static_included",
            "character_ref": ({"db_character_id": actor["db_character_id"], "version_line": line}
                              if character_entity_ref["kind"] == "database" else None),
            "character_entity_ref": character_entity_ref, "weapon": managed_weapon,
            "artifact_sets": [{
                "set_id": item["set_id"], "count": item["count"],
                "entity_ref": _whiteboard_ref(
                    item.get("entity_ref"), entity_type="artifact_set",
                    database_id=item.get("set_id"), version_line=line,
                    path="team.artifact_sets.entity_ref",
                    registry={(row["entity_type"], row["provisional_id"])
                              for row in board.get("provisional_entities", [])},
                ),
            } for item in actor["artifact_sets"]],
            "panel_provenance": copy.deepcopy(provenance),
            "source_refs": [{"kind": "whiteboard", "board_id": board["board_id"], "board_fingerprint": metadata["board_fingerprint"],
                             "note": "白板保存的固定面板；未声明来源时保持未知，未知数值不等于零"}], "data_version": version}
        gear = plan["equipment_inputs"][actor["id"]]
        gear["progression"] = {"ascension": actor.get("ascension"), "constellation": actor["constellation"],
            "status": "confirmed" if actor.get("ascension") is not None else "pending", "source": "白板角色成长输入"}
        if "talent_base_levels" in actor:
            gear["progression"]["talent_base_levels"] = copy.deepcopy(actor["talent_base_levels"])
        if "artifact_presence" in actor:
            gear["artifact_presence"] = copy.deepcopy(actor["artifact_presence"])
        if "panel_basis_mode" in actor:
            plan["equipment_inputs"][actor["id"]]["panel_basis_mode"] = actor["panel_basis_mode"]
        rotation["characters"].append({"id": actor["id"], "element": None, "energy_recharge": actor["panel"].get("energy_recharge"),
            "energy_capacity": None, "initial_energy": None, "status": "pending", "source": "用户白板，仅面板和动作意图明确"})
    for index, action in enumerate(board["actions"]):
        ability = {"id": "wb.ability." + action["id"], "character": action["character"], "action_type_id": action["action_type_id"],
                   "cooldown": None, "energy": None, "rules": [], "status": "pending", "source": "白板动作类别，游戏约束待核实"}
        if action["variant"]:
            ability["variant"] = action["variant"]
        rotation["abilities"].append(ability)
        native_action = {"id": action["id"], "ability_id": ability["id"], "start": action["start"], "order": index,
                         "occupation_end": None, "switch_allowed_at": None, "repeat": {"mode": "once"},
                         "status": "pending", "source": "当前白板指定时点与顺序；占用、冷却和能量待核实"}
        if action["action_type_id"] == "action.switch":
            native_action["switch_to"] = action["character"]
        rotation["actions"].append(native_action)
        metadata["action_map"].append({"board_action_id": action["id"], "native_action_id": action["id"]})
    metadata["action_admission_proposals"] = _prepare_action_admission_handoff(
        board, rotation, metadata["action_map"], references)
    if admission_intents and all(intent["context"] is not None for intent in admission_intents):
        from genshin_app.equipment.action_admission_schema import CONTEXT

        plan["action_admission_context"] = copy.deepcopy(CONTEXT)
    plan["rotation_model"] = rotation
    # New handoffs never grant database knowledge direct execution authority.
    # Existing native files without this marker remain readable through the
    # explicit legacy logic profile; there is no silent semantic migration.
    plan["extensions"] = {"governance_profile": "agent-supervised-v1",
                          "whiteboard_handoff": metadata}
    validate_plan(plan)
    return plan



@diagnosed('whiteboard', 'export-handoff-v1', metadata=lambda arguments: {
    "board_id": arguments["board"]["board_id"], "revision": arguments["board"]["revision"],
    "intent_fingerprint": board_fingerprint(arguments["board"])})
def export_handoff(board, output_dir, db_path=None):
    plan = make_handoff(board, db_path)
    return publish_whiteboard_handoff(board, plan, output_dir, db_path)
