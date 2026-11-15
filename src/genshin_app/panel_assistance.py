"""Read-only panel comparisons automatically attached to ordinary reports.

Construction records are optional evidence. Their absence, contradiction or
failure cannot change the supplied panel or calculation admission.
"""
from __future__ import annotations

import copy

from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.panel_provenance import provenance_of
from genshin_engine.execution_control import ExecutionCancelled


def compare_plan_panels(plan, db_path):
    from genshin_app.equipment.panel_reference import reference_panel

    rows = []
    equipment = plan.get("equipment_inputs") or {}
    for actor in (plan.get("manual_template") or {}).get("characters", []):
        actor_id = actor.get("id")
        gear = equipment.get(actor_id, {})
        provenance = provenance_of(gear)
        row = {"character": actor_id, "origin": provenance["origin"],
               "entered_by": provenance["entered_by"], "state": "composition_unknown",
               "differences": [], "candidate_effect": "none", "acceptance_effect": "none"}
        request = provenance.get("reference_request")
        if not isinstance(request, dict):
            row["message"] = ("用户成品面板按原值使用，未反推词条或追加常驻属性。"
                              if provenance["origin"] == "user_panel" else
                              "未提供面板组成，不能据此核实白值、花羽、主属性和词条预算。")
            ref, weapon = gear.get("character_ref") or {}, gear.get("weapon") or {}
            if (ref.get("db_character_id") is not None and actor.get("level") in (90, 100)
                    and all(weapon.get(k) is not None for k in ("weapon_id", "level", "ascension"))):
                request = {"db_character_id": ref["db_character_id"], "level": actor["level"],
                           "weapon": {key: weapon[key] for key in ("weapon_id", "level", "ascension")},
                           "artifact_sets": [{"set_id": item["set_id"], "count": item["count"]}
                                             for item in gear.get("artifact_sets", []) if item.get("set_id") is not None]}
                basis_only = True
            else:
                rows.append(row)
                continue
        else:
            basis_only = False
        if ("weapon" in request and not isinstance(request["weapon"], dict)
                or "artifact_sets" in request and (
                    not isinstance(request["artifact_sets"], list)
                    or any(not isinstance(item, dict) for item in request["artifact_sets"]))):
            row.update(state="comparison_unavailable", code="invalid_panel_reference",
                       message="面板构造记录的武器或套装形状无效；请修正辅助记录，当前面板未被改动。")
            rows.append(row)
            continue
        request = copy.deepcopy(request)
        mismatches = []
        character_ref = gear.get("character_ref") or {}
        expected_id = character_ref.get("db_character_id")
        if expected_id is not None and request.get("db_character_id") != expected_id:
            mismatches.append("db_character_id")
        if request.get("level") != actor.get("level"):
            mismatches.append("level")
        weapon = gear.get("weapon") or {}
        for key in ("weapon_id", "level", "ascension", "refinement"):
            if basis_only and key == "refinement":
                continue
            if weapon.get(key) is not None and (request.get("weapon") or {}).get(key) != weapon[key]:
                mismatches.append("weapon." + key)
        if "artifact_sets" in gear:
            selected = sorted(((item.get("set_id"), item.get("count")) for item in gear["artifact_sets"]), key=repr)
            requested = sorted(((item.get("set_id"), item.get("count")) for item in request.get("artifact_sets", [])), key=repr)
            if selected != requested:
                mismatches.append("artifact_sets")
        if mismatches:
            row.update(state="construction_outdated", mismatched_fields=mismatches,
                       message="面板构造记录与当前等级或配装不同，请更新参考输入后再比较；当前面板未被改动。")
        else:
            request["current_panel"] = ( {key: value for key, value in actor.get("stats", {}).items()
                                          if key in ("base_atk", "base_hp", "base_def")}
                                       if basis_only else copy.deepcopy(actor.get("stats", {})))
            try:
                compared = reference_panel(db_path, request, character_ref.get("version_line", "release"))
                row.update(state="basis_compared" if basis_only else "compared", completeness=compared["completeness"],
                           differences=compared["differences"], budget=compared["budget"],
                           roll_feasibility=compared["roll_feasibility"],
                           warnings=compared["warnings"], missing_inputs=compared["missing_inputs"],
                           reference_panel=compared["reference_panel"],
                           weapon_basis=compared["weapon_basis"],
                           request_fingerprint=compared["request_fingerprint"],
                           database_source=compared["database_source"],
                           panel_fingerprint=fingerprint(actor.get("stats", {})))
            except ExecutionCancelled:
                raise
            except Exception as error:  # noqa: BLE001 - an optional advisor cannot revoke the main calculation
                # Advisory failures never revoke a valid main calculation.
                # Preserve the real error class without copying paths/secrets.
                row.update(state="comparison_unavailable", code=getattr(error, "reason", type(error).__name__),
                           message="面板辅助比较未完成；不影响主计算，参考面板工具可单独诊断。")
        rows.append(row)
    return rows
