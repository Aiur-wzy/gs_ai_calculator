"""Lossless native-owned editing with a derived, tolerant whiteboard view."""
from __future__ import annotations

import copy

from genshin_app.equipment.schema import PlanError
from genshin_app.whiteboard_identity import board_fingerprint

CONTRACT = "native-game-input-v1"


def _object(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def action_view(plan):
    """A draft can be read even before its ability/actor references close."""
    from genshin_app.equipment.action_authoring_projection import native_action_board
    return native_action_board(plan, allow_incomplete=True)


def project_board(plan, board):
    """Project representable fields; the complete native input remains untouched."""
    from genshin_app.whiteboard_schema import default_panel

    result = copy.deepcopy(board)
    template = _object(plan.get("manual_template"))
    rotation = _object(plan.get("rotation_model"))
    if "provisional_entities" in plan:
        result["provisional_entities"] = copy.deepcopy(plan["provisional_entities"])
    if "calculation_options" in plan:
        result["calculation_options"] = copy.deepcopy(plan["calculation_options"])
    else:
        result.pop("calculation_options", None)
    result["title"] = _object(template.get("meta")).get("name", result.get("title", "原生方案"))
    if rotation.get("period") is not None:
        result["duration"] = rotation["period"]
    elif template.get("loop_duration") is not None:
        result["duration"] = template["loop_duration"]
    if rotation.get("damage_report") is not None:
        result["damage_report"] = copy.deepcopy(rotation["damage_report"])
    old_actors = {row["id"]: row for row in _rows(result.get("team")) if isinstance(row.get("id"), str)}
    team = []
    for row in _rows(template.get("characters")):
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        actor = copy.deepcopy(old_actors.get(row["id"]))
        equipment = _object(_object(plan.get("equipment_inputs")).get(row["id"]))
        if actor is None:
            ref = _object(equipment.get("character_ref"))
            # Unknown public identities stay native-only; they are not invented.
            if not ref.get("db_character_id"):
                continue
            actor = {"id": row["id"], "db_character_id": ref["db_character_id"],
                "name": row.get("name", row["id"]), "level": row.get("level", 90),
                "constellation": 0, "weapon": None, "artifact_sets": [],
                "panel": default_panel(), "panel_basis_mode": "manual", "notes": "原生输入投影"}
        actor.update(name=row.get("name", actor["name"]), level=row.get("level", actor["level"]))
        panel = default_panel()
        stats = row.get("stats") if isinstance(row.get("stats"), dict) else {}
        for key in panel:
            if key == "dmg_bonus":
                panel[key].update(_object(stats.get(key)))
            elif key in stats:
                panel[key] = stats[key]
        actor["panel"] = panel
        progression = _object(equipment.get("progression"))
        if progression.get("constellation") is not None:
            actor["constellation"] = progression["constellation"]
        for key in ("ascension", "talent_base_levels"):
            if progression.get(key) is not None:
                actor[key] = copy.deepcopy(progression[key])
        if _object(equipment.get("character_ref")).get("db_character_id"):
            actor["db_character_id"] = equipment["character_ref"]["db_character_id"]
        if equipment.get("character_entity_ref") is not None:
            actor["character_ref"] = copy.deepcopy(equipment["character_entity_ref"])
        if equipment.get("panel_basis_mode") is not None:
            actor["panel_basis_mode"] = equipment["panel_basis_mode"]
        if equipment.get("artifact_presence") is not None:
            actor["artifact_presence"] = copy.deepcopy(equipment["artifact_presence"])
        weapon = equipment.get("weapon")
        if weapon is None:
            actor["weapon"] = None
        elif isinstance(weapon, dict):
            old = _object(actor.get("weapon"))
            actor["weapon"] = {"name": old.get("name", "原生武器"),
                **{key: weapon.get(key) for key in ("weapon_id", "level", "ascension", "refinement")}}
            if weapon.get("entity_ref") is not None:
                actor["weapon"]["entity_ref"] = copy.deepcopy(weapon["entity_ref"])
        if "artifact_sets" in equipment:
            old_sets = {r.get("set_id"): r for r in _rows(actor.get("artifact_sets"))}
            actor["artifact_sets"] = [{**copy.deepcopy(r), "name": old_sets.get(r.get("set_id"), {}).get("name", "原生套装")}
                for r in _rows(equipment["artifact_sets"])]
        team.append(actor)
    result["team"] = team
    old_handoff = _object(_object(plan.get("extensions")).get("whiteboard_handoff"))
    aliases = {row["native_action_id"]: row["board_action_id"] for row in _rows(old_handoff.get("action_map"))
               if isinstance(row.get("native_action_id"), str) and isinstance(row.get("board_action_id"), str)}
    old_actions = {row["id"]: row for row in _rows(result.get("actions")) if isinstance(row.get("id"), str)}
    known_actors = {row["id"] for row in team}
    actions, mapping, auxiliary = [], [], []
    for row in action_view(plan)["actions"]:
        if not isinstance(row["id"], str) or not isinstance(row["character"], str) or row["character"] not in known_actors or row["action_type_id"] is None:
            continue
        if row["action_type_id"] in {"action.switch", "action.wait"} and row["id"] not in aliases:
            auxiliary.append(row["id"])
            continue
        bid = aliases.get(row["id"], row["id"])
        original = old_actions.get(bid, {})
        actions.append({"id": bid, "character": row["character"], "action_type_id": row["action_type_id"],
            "label": original.get("label", bid), "start": row["start"], "variant": row["variant"],
            "notes": original.get("notes", "原生输入投影")})
        mapping.append({"board_action_id": bid, "native_action_id": row["id"]})
    result["actions"] = actions
    return result, mapping, auxiliary


def synchronize_native_projection(plan, previous=None):
    handoff = plan.get("extensions", {}).get("whiteboard_handoff")
    if not isinstance(handoff, dict) or not isinstance(handoff.get("board"), dict):
        return plan
    board, mapping, auxiliary = project_board(plan, handoff["board"])
    old_board = (previous if isinstance(previous, dict) and previous.get("kind") == "genshin-whiteboard" else
                 _object(_object(_object(previous).get("extensions")).get("whiteboard_handoff")).get("board"))
    if isinstance(old_board, dict):
        for key in ("kind", "schema_version", "board_id", "version_line"):
            board[key] = old_board[key]
        old_content = {key: value for key, value in old_board.items() if key != "revision"}
        content = {key: value for key, value in board.items() if key != "revision"}
        board["revision"] = old_board["revision"] + (old_content != content)
    handoff.update(input_owner=CONTRACT, board=board, action_map=mapping, auxiliary_action_ids=auxiliary,
        board_fingerprint=board_fingerprint(board), constellations={row["id"]: row["constellation"] for row in board["team"]})
    from genshin_app.whiteboard_handoff import _context_state
    for key, state in (("capacity_intents", "capacity_context_state"), ("action_admission_intents", "action_admission_context_state")):
        handoff[key] = copy.deepcopy(board.get(key, []))
        handoff[state] = _context_state(handoff[key])
    if previous is not None and previous != plan:
        # These proposals/confirmations describe a previous input, not the new view.
        handoff["action_admission_proposals"] = []
    return plan


def canonical_projection_valid(board, plan):
    """Recompute the projection; a caller-provided marker alone grants nothing."""
    handoff = plan.get("extensions", {}).get("whiteboard_handoff", {})
    expected, mapping, auxiliary = project_board(plan, board)
    if expected != board or mapping != handoff.get("action_map") or auxiliary != handoff.get("auxiliary_action_ids"):
        raise PlanError("白板投影不是当前原生输入，请重新生成投影", "whiteboard_intent_mismatch")
    if board_fingerprint(board) != handoff.get("board_fingerprint"):
        raise PlanError("白板投影身份不一致", "whiteboard_intent_mismatch")
    return {"board_fingerprint": board_fingerprint(board), "user_action_count": len(mapping), "auxiliary_action_count": len(auxiliary)}
