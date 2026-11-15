"""Bounded base-skill resource facts; no variant or gameplay execution inference."""
from __future__ import annotations

import copy
import json
import math
from decimal import Decimal
from functools import lru_cache
from importlib import resources

from genshin_db.runtime_catalog import (
    RuntimeConfigError,
    dumps,
    fingerprint,
    resolve_pointer,
)
from genshin_db.validation import QueryValidationError

RESOURCE_LIMITS = {"max_bytes": 65536, "max_description_bytes": 32768,
                   "max_seconds": 1_000_000_000, "max_energy": 1_000_000_000,
                   "max_charges": 100}
TEXT_MAP_PATH = "TextMap/TextMap_MediumCHS.json"
RESOURCE_FIELDS = {
    "base_cooldown_seconds": "cdTime", "base_charge_count": "maxChargeNum",
    "base_energy_cost": "costElemVal", "cost_element": "costElemType",
}
ELEMENT_NAMES = {"Fire": "pyro", "Water": "hydro", "Ice": "cryo", "Electric": "electro",
                 "Wind": "anemo", "Rock": "geo", "Grass": "dendro"}


def action_resource_capabilities():
    return {"projection_version": 1, "scope": "base_skill_record", "limits": dict(RESOURCE_LIMITS),
            "selection": "character_id；slot(normal/skill/burst)或skill_id二选一；多depot必须显式选择",
            "independent_of": ["talent_level", "constellation"],
            "fields": dict(RESOURCE_FIELDS), "descriptions": "同一快照的简体中文技能名称/完整描述，附原始hash及节点指纹",
            "missing": "逐字段pending，不补0或1；已知数值不因缺文而消失",
            "time": "仅接受可精确表示为微秒的基础CD；不隐式量化",
            "cast_sequence": "命中受审资料时返回通用阶段、续段窗口及冷却起点；未命中时省略，不从技能名或数值猜测",
            "variant_applicability": "not_evaluated", "execution_support": "not_evaluated",
            "notes": ["ready只表示所请求基础资料完整，不证明press/hold或当前动作适用",
                      "基础次数不是连段次数；多段施放必须使用cast_sequence，不能改写成charges",
                      "不解释命座、减CD、额外次数、退款、动作派发或能量门槛与消耗是否相等"]}


@lru_cache(maxsize=1)
def _cast_sequence_recipes():
    """Load small, reviewed semantic overlays; raw upstream remains the evidence source."""
    value = json.loads(resources.files("genshin_runtime").joinpath(
        "resources/cast_sequence_profiles.json").read_text("utf8"))
    if (not isinstance(value, dict) or value.get("schema_version") != 1
            or value.get("profile_version") != 2 or not isinstance(value.get("profiles"), list)):
        raise RuntimeError("cast sequence profile bundle is invalid")
    for profile in value["profiles"]:
        required = {"id", "version_line", "upstream_commit", "normalization_profile_id", "character_id",
                    "skill_depot_id", "group", "cooldown_start", "cooldown_charges",
                    "cooldown_recovery", "max_stages",
                    "skill_source", "ability_source", "stages", "review"}
        if not isinstance(profile, dict) or set(profile) != required:
            raise RuntimeError("cast sequence profile fields are invalid")
        stages = profile["stages"]
        if (profile["cooldown_start"] != "sequence_start" or profile["cooldown_charges"] != 1
                or profile["cooldown_recovery"] != "sequential" or type(profile["max_stages"]) is not int
                or not isinstance(stages, list) or len(stages) != profile["max_stages"]
                or [item.get("stage") for item in stages] != list(range(1, len(stages) + 1))
                or len({item.get("skill_id") for item in stages}) != len(stages)):
            raise RuntimeError("cast sequence profile stage model is invalid")
        nonterminal = stages[:-1]
        terminal = stages[-1]
        if (any(type(item.get("continuation_window")) not in (int, float)
                or item["continuation_window"] <= 0
                or "continuation_modifier_pointer" not in item
                or "terminal_modifier_pointer" in item for item in nonterminal)
                or terminal.get("continuation_window") is not None
                or "terminal_modifier_pointer" not in terminal
                or "continuation_modifier_pointer" in terminal):
            raise RuntimeError("cast sequence continuation/terminal model is invalid")
    return value


def _cast_sequence_projection(nodes, state, entry):
    """Return an exact reviewed sequence overlay for this skill, or None.

    This is data-driven.  Character IDs select a reviewed row; they never alter
    the state-machine algorithm.  Every projected scalar is re-read from the
    current validated snapshot before the row is exposed.
    """
    selection = entry["selection"]
    matches = [profile for profile in _cast_sequence_recipes()["profiles"]
               if profile["version_line"] == state["version_line"]
               and profile["upstream_commit"] == state["upstream_commit"]
               and profile["normalization_profile_id"] == state["normalization"]["profile_id"]
               and profile["character_id"] == selection["character_id"]
               and profile["skill_depot_id"] == selection["skill_depot_id"]
               and selection["skill_id"] in {stage["skill_id"] for stage in profile["stages"]}]
    if not matches:
        return None
    if len(matches) != 1:
        raise RuntimeConfigError("连段技能受审资料不唯一", "ambiguous_reference", 409)
    profile = matches[0]
    skill_origin, skill_rows = nodes.source(profile["skill_source"]["source_path"])
    ability_origin, ability_root = nodes.source(profile["ability_source"]["source_path"])
    if (skill_origin["source_sha256"] != profile["skill_source"]["source_sha256"]
            or ability_origin["source_sha256"] != profile["ability_source"]["source_sha256"]):
        raise RuntimeConfigError("连段技能受审资料与当前源文件不符", "dataset_invalid", 500)
    evidence, stage_rows = [], []
    duration_field = profile["ability_source"]["duration_field"]
    for stage in profile["stages"]:
        try:
            skill = resolve_pointer(skill_rows, stage["skill_pointer"])
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeConfigError("连段技能阶段记录不存在", "dataset_invalid", 500) from exc
        if (not isinstance(skill, dict) or skill.get("id") != stage["skill_id"]
                or skill.get("abilityName") != stage["ability_name"]
                or skill.get("cdTime") != stage["raw_cooldown_seconds"]):
            raise RuntimeConfigError("连段技能阶段记录与受审资料不符", "dataset_invalid", 500)
        if stage["stage"] == 1 and skill.get("maxChargeNum") != profile["cooldown_charges"]:
            raise RuntimeConfigError("连段技能基础次数与受审资料不符", "dataset_invalid", 500)
        skill_evidence = {"kind": "skill_record", "stage": stage["stage"],
                          "source_path": skill_origin["source_path"],
                          "source_sha256": skill_origin["source_sha256"],
                          "json_pointer": stage["skill_pointer"], "node_sha256": fingerprint(skill)}
        evidence.append(skill_evidence)
        stage_row = {"stage": stage["stage"], "skill_id": stage["skill_id"],
                     "ability_name": stage["ability_name"],
                     "raw_cooldown_seconds": stage["raw_cooldown_seconds"],
                     "cooldown_role": "sequence_start" if stage["stage"] == 1 else "continuation_bypasses_active_cooldown",
                     "source": skill_evidence}
        pointer = stage.get("continuation_modifier_pointer") or stage.get("terminal_modifier_pointer")
        try:
            modifier = resolve_pointer(ability_root, pointer)
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeConfigError("连段技能阶段modifier不存在", "dataset_invalid", 500) from exc
        if not isinstance(modifier, dict):
            raise RuntimeConfigError("连段技能阶段modifier格式错误", "dataset_invalid", 500)
        modifier_evidence = {"kind": "continuation_modifier" if stage["stage"] < profile["max_stages"] else "terminal_modifier",
                             "stage": stage["stage"], "source_path": ability_origin["source_path"],
                             "source_sha256": ability_origin["source_sha256"], "json_pointer": pointer,
                             "node_sha256": fingerprint(modifier)}
        evidence.append(modifier_evidence)
        stage_row["transition_source"] = modifier_evidence
        if stage["stage"] < profile["max_stages"]:
            if modifier.get(duration_field) != stage["continuation_window"]:
                raise RuntimeConfigError("连段技能续段窗口与受审资料不符", "dataset_invalid", 500)
            stage_row["continuation_window"] = stage["continuation_window"]
        else:
            stage_row["continuation_window"] = None
        stage_rows.append(stage_row)
    selected = next(stage for stage in stage_rows if stage["skill_id"] == selection["skill_id"])
    value = {"group": profile["group"], "stage": selected["stage"],
             "max_stages": profile["max_stages"], "continuation_window": selected["continuation_window"],
             "cooldown_start": profile["cooldown_start"]}
    result = {"contract": "cast-sequence-source-v1", "status": "ready", "profile_id": profile["id"],
              "value": value, "stages": stage_rows,
              "resource_policy": {"sequence_cooldown_owner_stage": 1,
                  "sequence_cooldown_seconds": stage_rows[0]["raw_cooldown_seconds"],
                  "sequence_cooldown_charges": profile["cooldown_charges"],
                  "sequence_cooldown_recovery": profile["cooldown_recovery"],
                  "continuations_bypass_active_cooldown": True,
                  "continuation_deadline_semantics": "half_open_[action_start,action_start_plus_window)",
                  "continuation_window_is_latest_deadline_not_minimum_wait": True,
                  "terminal_stage_restarts_cooldown": False,
                  "raw_followup_cdtime_is_not_an_independent_cast_cooldown": True,
                  "raw_followup_cdtime_is_not_a_minimum_stage_delay": True},
              "review": copy.deepcopy(profile["review"]), "evidence": evidence,
              "execution_authority": "cast_sequence_fields_only"}
    result["content_fingerprint"] = fingerprint(result)
    return result


def validate_selection(*, character_id, slot=None, skill_id=None, skill_depot_id=None):
    """Reject malformed identities before potentially expensive database validation."""
    from genshin_db.runtime_queries import _integer
    _integer(character_id, "character_id")
    if (slot is None) == (skill_id is None):
        raise QueryValidationError("slot与skill_id必须且只能提供一个")
    if slot is not None and (not isinstance(slot, str) or slot not in ("normal", "skill", "burst")):
        raise QueryValidationError("slot只允许normal/skill/burst")
    for value, name in ((skill_id, "skill_id"), (skill_depot_id, "skill_depot_id")):
        if value is not None:
            _integer(value, name)


def resolve_action_resource_entry(conn, state, *, character_id, slot=None, skill_id=None, skill_depot_id=None):
    """Reusable exact identity resolver on the caller's read transaction.

    It deliberately does not visit ProudSkill, Talent or dispatch resources.
    Future shared-resolver refactors must preserve existing action_facts output.
    """
    from genshin_db.runtime_queries import _definition_summary, _lookup, _Nodes

    validate_selection(character_id=character_id, slot=slot, skill_id=skill_id, skill_depot_id=skill_depot_id)
    nodes, records = _Nodes(conn, state["version_line"]), {}

    def exact(table, key, name, fields):
        rows = _lookup(conn, state["version_line"], "excel_record", key,
                       f"ExcelBinOutput/{table}ExcelConfigData.json")
        if len(rows) != 1:
            raise RuntimeConfigError("动作资料入口不存在或不唯一", "not_found" if not rows else "ambiguous_reference",
                                     404 if not rows else 409, {"table": table, "key": key})
        row = rows[0]
        payload, origin = nodes.node(row)
        if not isinstance(payload, dict):
            raise RuntimeConfigError("动作身份记录不是对象", "dataset_invalid", 500)
        records[name] = {"definition": _definition_summary(row), **origin, "node_sha256": row["node_sha256"],
                         "fields": {key: copy.deepcopy(payload[key]) for key in fields if key in payload},
                         "field_pointers": {key: row["json_pointer"]+"/"+key for key in fields if key in payload}}
        return payload

    return {**_resolve_action_identity(exact, character_id=character_id, slot=slot, skill_id=skill_id,
                                      skill_depot_id=skill_depot_id), "identity_sources": records}, nodes


def _resolve_action_identity(exact, *, character_id, slot=None, skill_id=None, skill_depot_id=None):
    """Shared traversal for fresh records and bounded saved full-node replay."""
    validate_selection(character_id=character_id, slot=slot, skill_id=skill_id, skill_depot_id=skill_depot_id)
    def id_list(payload, field):
        value = payload.get(field, [])
        if not isinstance(value, list) or any(type(item) is not int or not 0 <= item <= 2**53-1 for item in value):
            raise RuntimeConfigError("技能身份列表格式错误", "dataset_invalid", 500, {"field": field})
        return value

    avatar = exact("Avatar", {"id": character_id}, "avatar", ("id", "skillDepotId", "candSkillDepotIds"))
    base = avatar.get("skillDepotId", 0)
    if type(base) is not int or not 0 <= base <= 2**53-1:
        raise RuntimeConfigError("角色基础技能组ID格式错误", "dataset_invalid", 500)
    candidates = list(dict.fromkeys(value for value in [base, *id_list(avatar, "candSkillDepotIds")] if value))
    if skill_depot_id is None:
        if len(candidates) != 1:
            raise RuntimeConfigError("角色技能组需明确选择", "skill_depot_selection_required", 409,
                                     {"candidate_skill_depot_ids": candidates})
        skill_depot_id = candidates[0]
    elif skill_depot_id not in candidates:
        raise QueryValidationError("skill_depot_id不属于角色明确列出的技能组")
    depot = exact("AvatarSkillDepot", {"id": skill_depot_id}, "depot", ("id", "skills", "subSkills", "energySkill"))
    skills, subskills = id_list(depot, "skills"), id_list(depot, "subSkills")
    burst = depot.get("energySkill", 0)
    if type(burst) is not int or not 0 <= burst <= 2**53-1:
        raise RuntimeConfigError("爆发技能ID格式错误", "dataset_invalid", 500)
    slot_ids = {"normal": skills[0] if skills else 0, "skill": skills[1] if len(skills) > 1 else 0, "burst": burst}
    if slot is not None:
        skill_id = slot_ids[slot]
        if not skill_id:
            raise RuntimeConfigError("所选技能槽为空", "empty_skill_slot", 409, {"slot": slot})
    elif skill_id not in [*skills, *subskills, burst]:
        raise QueryValidationError("skill_id不属于所选技能组")
    exact("AvatarSkill", {"id": skill_id}, "skill", ("id", *RESOURCE_FIELDS.values(), "costStamina", "nameTextMapHash", "descTextMapHash"))
    return {"selection": {"character_id": character_id, "skill_depot_id": skill_depot_id, "skill_id": skill_id,
                           "slot": slot, "matching_slots": [k for k, value in slot_ids.items() if value == skill_id]}}


def _field_origin(record, field):
    present = field in record["fields"]
    return {key: record[key] for key in ("source_path", "source_sha256", "node_sha256", "json_pointer")} | {
        "raw_pointer": record["field_pointers"].get(field), "requested_pointer": record["json_pointer"]+"/"+field,
        "present": present, "field_node_sha256": fingerprint(record["fields"][field]) if present else None}


def _resource_projection(record, output, field):
    origin = _field_origin(record, field)
    raw = record["fields"].get(field)
    result = {"status": "pending", "value": None, "raw_value": copy.deepcopy(raw), "source_field": field,
              "origin": origin, "issues": []}
    code = None
    if not origin["present"]:
        code = "missing_resource_field"
    elif output == "cost_element":
        if not isinstance(raw, str) or raw not in ELEMENT_NAMES:
            code = "unsupported_cost_element"
        else:
            result["value"] = ELEMENT_NAMES[raw]
    elif type(raw) not in (int, float) or (type(raw) is float and not math.isfinite(raw)):
        code = "invalid_resource_number"
    elif output == "base_charge_count":
        if type(raw) is not int or not 1 <= raw <= RESOURCE_LIMITS["max_charges"]:
            code = "invalid_resource_charges"
        else:
            result["value"] = raw
    elif raw < 0 or raw > RESOURCE_LIMITS["max_seconds" if output == "base_cooldown_seconds" else "max_energy"]:
        code = "resource_value_out_of_range"
    elif output == "base_cooldown_seconds" and Decimal(str(raw))*1_000_000 != (Decimal(str(raw))*1_000_000).to_integral_value():
        code = "resource_time_precision"
    else:
        result["value"] = raw
    if code:
        result["issues"].append({"code": code, "field": field})
    else:
        result["status"] = "ready"
    return result


def _descriptions(nodes, skill):
    source, texts = nodes.source(TEXT_MAP_PATH)
    if not isinstance(texts, dict):
        raise RuntimeConfigError("中文TextMap根形状错误", "dataset_invalid", 500)
    results = {}
    for name, field in (("name", "nameTextMapHash"), ("description", "descTextMapHash")):
        origin, text_hash = _field_origin(skill, field), skill["fields"].get(field)
        item = {"status": "pending", "text": None, "text_map_hash": text_hash, "hash_origin": origin,
                "text_origin": None, "issues": []}
        if type(text_hash) is not int or text_hash <= 0:
            item["issues"].append({"code": "missing_text_hash" if not origin["present"] or text_hash == 0 else "invalid_text_hash"})
        else:
            text = texts.get(str(text_hash))
            if not isinstance(text, str) or not text:
                item["issues"].append({"code": "missing_description_text" if text is None or text == "" else "invalid_description_text"})
            elif len(text.encode("utf8")) > RESOURCE_LIMITS["max_description_bytes"]:
                raise RuntimeConfigError("动作资源中文原文超过上限", "response_too_large", 413)
            else:
                item.update(status="ready", text=text, text_origin={"source_path": TEXT_MAP_PATH,
                    "json_pointer": "/"+str(text_hash), "source_sha256": source["source_sha256"], "node_sha256": fingerprint(text)})
        results[name] = item
    results["status"] = "ready" if all(results[key]["status"] == "ready" for key in ("name", "description")) else "partial"
    return results


def read_action_resource_profile(conn, state, *, character_id, slot=None, skill_id=None, skill_depot_id=None):
    """Build a detached profile inside the caller's existing read transaction."""
    validate_selection(character_id=character_id, slot=slot, skill_id=skill_id, skill_depot_id=skill_depot_id)
    if not conn.in_transaction:
        raise RuntimeConfigError("动作资源资料须在调用者读取事务内装配", "transaction_required", 500)
    entry, nodes = resolve_action_resource_entry(conn, state, character_id=character_id, slot=slot,
                                                 skill_id=skill_id, skill_depot_id=skill_depot_id)
    skill = entry["identity_sources"]["skill"]
    projection = {name: _resource_projection(skill, name, field) for name, field in RESOURCE_FIELDS.items()}
    descriptions = _descriptions(nodes, skill)
    cast_sequence = _cast_sequence_projection(nodes, state, entry)
    statuses = [item["status"] for item in projection.values()]
    status = "ready" if all(value == "ready" for value in statuses) and descriptions["status"] == "ready" else "partial" if "ready" in statuses else "pending"
    result = {"projection_version": 1, "status": status, "scope": "base_skill_record", "snapshot": state,
              **entry, "projection": projection, "descriptions": descriptions,
              "variant_applicability": {"status": "not_evaluated"}, "execution_support": "not_evaluated",
              "action_dispatch": {"status": "pending"}, "gameplay_interpretation": "not_evaluated",
              "limits": dict(RESOURCE_LIMITS), "notes": action_resource_capabilities()["notes"]}
    if cast_sequence is not None:
        result["cast_sequence"] = cast_sequence
    canonical_selection = {key: value for key, value in entry["selection"].items() if key != "slot"}
    resource_payload = {"projection_version": 1, "snapshot": state,
        "selection": canonical_selection, "identity_sources": entry["identity_sources"],
        "projection": projection, "descriptions": descriptions}
    if cast_sequence is not None:
        resource_payload["cast_sequence"] = cast_sequence
    result["resource_fingerprint"] = fingerprint(resource_payload)
    result["content_fingerprint"] = fingerprint(result)
    if len(dumps(result).encode("utf8")) > RESOURCE_LIMITS["max_bytes"]:
        raise RuntimeConfigError("动作资源响应字节数超限", "response_too_large", 413)
    # Snapshot metadata may contain nested structures borrowed from the session
    # validation cache. A caller must not mutate the next query's evidence.
    return copy.deepcopy(result)


def get_runtime_action_resource_profile(db_path, *, character_id, slot=None, skill_id=None, skill_depot_id=None,
                                        version_line="release", snapshot_id=None):
    from genshin_db.runtime_queries import _read

    validate_selection(character_id=character_id, slot=slot, skill_id=skill_id, skill_depot_id=skill_depot_id)
    with _read(db_path, version_line, snapshot_id) as (conn, state):
        return read_action_resource_profile(conn, state, character_id=character_id, slot=slot,
                                            skill_id=skill_id, skill_depot_id=skill_depot_id)
