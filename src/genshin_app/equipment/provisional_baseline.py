"""Typed loadout comparison over actual public-entity query evidence.

The wrapper is App metadata, never a claim that upstream stores the same keys.
Unknown values stay explicit; execution treatments remain Agent declarations.
"""
from __future__ import annotations

import copy

from genshin_app.equipment.schema import PlanError
from genshin_db import queries
from genshin_db.equipment import EquipmentError

FIELD = "app_loadout_comparison"
CONTRACT = "public-entity-loadout-baseline-projection-v1"


def loadout_comparison_selection(kind, replacement):
    """Only the exact queried states determine array-to-source comparison."""
    if kind == "character":
        return {"levels": [row["level"] for row in replacement["panel_basis"]]}
    if kind == "weapon":
        return {"states": [{key: row[key] for key in ("level", "ascension")} for row in replacement["panel_basis"]]}
    return {"thresholds": [row["required_count"] for row in replacement["thresholds"]]}


def typed_loadout_baseline(db_path, entity, detail):
    from .provisional_entities import (
        LOADOUT_VALUES_CONTRACT,
        validate_loadout_entity_values,
    )

    replacement = entity.get("values", {})
    if replacement.get("contract") != LOADOUT_VALUES_CONTRACT:
        return detail
    kind = entity["entity_type"]
    replacement = validate_loadout_entity_values(kind, replacement)
    if FIELD in detail:
        raise PlanError("数据库对象占用了App比较投影保留字段", "provisional_entity_patch_baseline_mismatch")
    raw = detail.get(kind) or {}
    values = {"contract": LOADOUT_VALUES_CONTRACT, "extensions": {}}
    sources = {"/contract": {"kind": "app_contract_metadata"}, "/extensions": {"kind": "app_contract_metadata"}}

    def missing(pointer, *, reason="source_missing"):
        return {"status": reason, "value": None, "source_pointer": pointer}

    def read(field, pointer):
        sources[field] = {"kind": "database_detail", "pointer": pointer}
        current = detail
        for key in pointer.split("/")[1:]:
            if not isinstance(current, dict) or key not in current or current[key] is None:
                return missing(pointer)
            current = current[key]
        return copy.deepcopy(current)

    if kind in {"character", "weapon"}:
        values["weapon_type"] = read("/weapon_type", f"/{kind}/weapon_type")
    if kind == "character":
        values["panel_basis"] = []
        for index, requested in enumerate(replacement["panel_basis"]):
            level = requested["level"]
            row = {"level": level}
            sources[f"/panel_basis/{index}/level"] = {"kind": "query_argument", "level": level}
            for stat in ("atk", "hp", "def"):
                row["base_" + stat] = read(f"/panel_basis/{index}/base_{stat}", f"/character/{stat}_{level}")
            values["panel_basis"].append(row)
    elif kind == "weapon":
        values["panel_basis"] = []
        for index, requested in enumerate(replacement["panel_basis"]):
            level, ascension = requested["level"], requested["ascension"]
            query = {"weapon_id": raw.get("id"), "level": level, "ascension": ascension,
                     "version_line": entity["version_claim"]["version_line"]}
            for field in ("level", "ascension"):
                sources[f"/panel_basis/{index}/{field}"] = {"kind": "query_argument", **query}
            try:
                response = queries.get_weapon_stats(db_path, query["weapon_id"], level, ascension, query["version_line"])
            except queries.NotFoundError:
                response = {"status": "source_unavailable", "reason": "entity_not_found"}
            except EquipmentError as error:
                # Dataset integrity/version/service failures are global. They
                # must not become an apparently missing scalar for approval.
                if error.reason not in {"invalid_equipment_state", "not_found"}:
                    raise
                response = {"status": "source_unavailable", "reason": error.reason}
            matches = [item for item in response.get("properties", [])
                       if item.get("prop_type") == "FIGHT_PROP_BASE_ATTACK"]
            value = matches[0].get("value") if len(matches) == 1 and matches[0].get("unit") == "flat" else None
            values["panel_basis"].append({"level": level, "ascension": ascension,
                "base_atk": value if value is not None else missing("/properties/FIGHT_PROP_BASE_ATTACK",
                    reason="source_ambiguous" if len(matches) > 1 else response.get("status", "source_missing"))})
            sources[f"/panel_basis/{index}/base_atk"] = {"kind": "database_weapon_stats", "query": query,
                "response": copy.deepcopy(response)}
        for field in ("passive_treatment", "passive_note"):
            values[field] = {"status": "agent_execution_policy_not_database_field", "database_value": None}
            sources["/" + field] = {"kind": "agent_execution_policy", "database_evidence_pointer": "/effects"}
    else:
        values["thresholds"] = []
        effects = detail.get("effects", [])
        for index, requested in enumerate(replacement["thresholds"]):
            count = requested["required_count"]
            matches = [row for row in effects if row.get("required_count") == count]
            values["thresholds"].append({"required_count": count if matches else missing("/effects/required_count"),
                "treatment": {"status": "agent_execution_policy_not_database_field", "database_value": None},
                "note": {"status": "agent_execution_policy_not_database_field", "database_value": None}})
            sources[f"/thresholds/{index}"] = {"kind": "database_set_effect", "required_count": count,
                                               "observed": copy.deepcopy(matches)}
    return {**copy.deepcopy(detail), FIELD: {"contract": CONTRACT, "entity_type": kind,
        "values": values, "field_sources": sources, "selection": loadout_comparison_selection(kind, replacement),
        "semantics": "App comparison projection; contract/extension wrappers and execution policy are not database facts. "
                     "Raw detail and queried numerical responses are retained separately; missing is not zero."}}
