"""Same-snapshot inherent-talent sources and explicit numerical gate checks.

This query never certifies a complete character build or executes a passive.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

from genshin_db.equipment_queries import borrow_read_cache
from genshin_db.runtime_catalog import (
    RuntimeConfigError,
    dumps,
    excel_key,
    fingerprint,
    resolve_pointer,
)
from genshin_runtime.passive_source_identity import (
    UNLOCK_ALGORITHM,
    passive_source_fingerprint,
    passive_unlock_fingerprint,
)

PASSIVE_LIMITS = {"max_bytes": 2 * 1024 * 1024, "max_openings": 16, "max_candidates": 64,
                  "max_promote_records": 32, "max_operations": 128, "max_abilities": 64,
                  "max_description_bytes": 32768, "max_issues": 256,
                  "max_nodes": 2000, "max_edges": 10000, "max_depth": 64}
TEXT_PATH = "TextMap/TextMap_MediumCHS.json"
_CACHE = "passive-source-v1"
_MAX_INT = 2**53 - 1


def passive_source_capabilities():
    return {"projection_version": 1, "bundle_version": 1, "limits": dict(PASSIVE_LIMITS),
            "selection": "character_id；多技能组明确skill_depot_id；proud_skill_id省略只发现，禁止自动选择",
            "progression": "选中后可提供character_level/ascension；只比较显式突破门槛及该阶段等级上限",
            "status": "ready只表示所选资料关系；unlock与descriptions分别回执，均不证明能力可执行",
            "source_kind": "inherent_proud_skill", "execution_support": "not_evaluated",
            "unlock_algorithm": UNLOCK_ALGORITHM,
            "complete_character_validity": "not_evaluated",
            "unchecked": ["minimum_character_level", "promotion_flow", "account_player_level", "current_client_unlock_state"],
            "fingerprints": {"source": "完整来源及规范选择，不含当前等级/突破输入或展示诊断",
                             "unlock": "来源指纹+两项输入+明确数值比较；不接受App确认声明"}}


def _validate(character_id, skill_depot_id=None, proud_skill_id=None, character_level=None, ascension=None):
    from genshin_db.runtime_queries import _integer

    _integer(character_id, "character_id")
    for value, name in ((skill_depot_id, "skill_depot_id"), (proud_skill_id, "proud_skill_id"),
                        (character_level, "character_level"), (ascension, "ascension")):
        if value is not None:
            _integer(value, name, 0 if name == "ascension" else 1)
    if proud_skill_id is None and (character_level is not None or ascension is not None):
        raise ValueError("提供等级或突破前须明确proud_skill_id")


def _issue(code, scope, *, blocks_source=False, **details):
    return {"code": code, "scope": scope, "blocks_source": blocks_source, **details}


def _bounded(value):
    if len(value.get("issues", [])) > PASSIVE_LIMITS["max_issues"]:
        raise RuntimeConfigError("固有天赋未决项超过上限", "response_too_large", 413)
    if len(dumps(value).encode("utf8")) > PASSIVE_LIMITS["max_bytes"]:
        raise RuntimeConfigError("固有天赋完整资料超过上限，未裁剪来源", "response_too_large", 413)
    return value


def _origin(record, field=None):
    value = {key: copy.deepcopy(record[key]) for key in ("definition", "source_path", "source_sha256", "json_pointer", "node_sha256") if key in record}
    if field is not None:
        present = field in record["record"]
        value.update(present=present, raw_pointer=record["json_pointer"] + "/" + field if present else None,
                     requested_pointer=record["json_pointer"] + "/" + field,
                     field_node_sha256=fingerprint(record["record"][field]) if present else None)
    return value


def _threshold(opening, proud):
    facts = {}
    for name, record, field in (("need_avatar_promote_level", opening, "needAvatarPromoteLevel"),
                                ("proud_break_level", proud, "breakLevel")):
        raw = record["record"].get(field)
        facts[name] = {"present": field in record["record"], "raw_value": copy.deepcopy(raw), "origin": _origin(record, field)}
    values = [r["raw_value"] for r in facts.values()]
    ready = all(type(v) is int and 0 <= v <= _MAX_INT for v in values) and values[0] == values[1]
    return {"status": "ready" if ready else "pending", "required_ascension": values[0] if ready else None, **facts}


def _empty_descriptions():
    return {"status": "pending", **{name: {"status": "pending", "text": None, "text_map_hash": None,
        "hash_origin": None, "text_origin": None, "requested_pointer": None, "issues": [{"code": "passive_not_selected"}]}
        for name in ("name", "description", "unlock_description")}}


@dataclass(frozen=True)
class PassiveSourceContext:
    """Private detached materialization. Never accepted by the public dispatcher."""
    source_view: dict


def get_passive_source_context(db_path, *, character_id, skill_depot_id=None, proud_skill_id=None,
                               version_line="release", snapshot_id=None):
    from genshin_db.runtime_queries import (
        _closure,
        _definition_summary,
        _lookup,
        _Nodes,
        _read,
        _row,
    )

    _validate(character_id, skill_depot_id, proud_skill_id)
    with _read(db_path, version_line, snapshot_id) as (conn, state):
        cache = borrow_read_cache(db_path, _CACHE)
        key = (state["snapshot_id"], character_id, skill_depot_id, proud_skill_id)
        if key in cache:
            return PassiveSourceContext(copy.deepcopy(cache[key]))
        nodes, issues = _Nodes(conn, version_line), []

        def add(code, scope, **details):
            issues.append(_issue(code, scope, **details))
            if len(issues) > PASSIVE_LIMITS["max_issues"]:
                raise RuntimeConfigError("固有天赋未决项超过上限", "response_too_large", 413)

        def read_record(row):
            fixed = cache.get((state["snapshot_id"], "fixed_table_source", row["source_path"]))
            if fixed is None:
                raw, origin = nodes.node(row)
            else:
                source, data = fixed
                try:
                    raw = resolve_pointer(data, row["json_pointer"])
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    raise RuntimeConfigError("固有天赋固定表原指针错误", "dataset_invalid", 500) from exc
                if fingerprint(raw) != row["node_sha256"]:
                    raise RuntimeConfigError("固有天赋固定表节点指纹错误", "dataset_invalid", 500)
                origin = {"source_path": row["source_path"], "json_pointer": row["json_pointer"], "source_sha256": source["source_sha256"]}
                # The generic closure reads its ProudSkill root through _Nodes.
                # Reuse the validated bytes there too, preserving the LRU limits.
                nodes.cache[row["source_path"]] = fixed
                nodes.cache.move_to_end(row["source_path"])
                while len(nodes.cache) > 1 and (len(nodes.cache) > 8 or sum(v[0]["source_size_bytes"] for v in nodes.cache.values()) > 64 * 1024 * 1024):
                    nodes.cache.popitem(last=False)
            if not isinstance(raw, dict):
                raise RuntimeConfigError("固有天赋来源表记录不是对象", "dataset_invalid", 500)
            return {"definition": _definition_summary(row), **origin, "node_sha256": row["node_sha256"], "record": copy.deepcopy(raw)}

        def exact(table, ident):
            path = f"ExcelBinOutput/{table}ExcelConfigData.json"
            rows = _lookup(conn, version_line, "excel_record", ident, path)
            if len(rows) != 1:
                raise RuntimeConfigError("固有天赋入口不存在或不唯一", "not_found" if not rows else "ambiguous_reference",
                                         404 if not rows else 409, {"table": table, "key": ident})
            return read_record(rows[0])

        def group_records(table, field, group):
            if type(group) is not int or not 1 <= group <= _MAX_INT:
                return []
            path = f"ExcelBinOutput/{table}ExcelConfigData.json"
            group_key = (state["snapshot_id"], "table_groups", path, field)
            if group_key not in cache:
                source, table_rows = nodes.source(path)
                if not isinstance(table_rows, list) or any(not isinstance(r, dict) for r in table_rows):
                    raise RuntimeConfigError("固有天赋固定表形状错误", "dataset_invalid", 500)
                groups = {}
                for offset, raw in enumerate(table_rows):
                    ident = raw.get(field)
                    if type(ident) is int and 1 <= ident <= _MAX_INT:
                        groups.setdefault(ident, []).append((offset, excel_key(path, raw, offset, indexer_version=state["indexer_version"])))
                # Pin just these two bounded source files in this transaction;
                # _Nodes' general LRU may evict them while traversing abilities.
                cache[(state["snapshot_id"], "fixed_table_source", path)] = (source, table_rows)
                cache[group_key] = groups
            return cache[group_key].get(group, [])

        def indexed_record(table, offset, ident):
            record = exact(table, ident)
            if record["json_pointer"] != f"/{offset}":
                raise RuntimeConfigError("固有天赋表索引与原节点位置不一致", "dataset_invalid", 500)
            return record

        text_cache = None

        def text(record, field):
            nonlocal text_cache
            raw_hash = record["record"].get(field)
            item = {"status": "pending", "text": None, "text_map_hash": copy.deepcopy(raw_hash),
                    "hash_origin": _origin(record, field), "text_origin": None, "requested_pointer": None, "issues": []}
            if type(raw_hash) is not int or not 1 <= raw_hash <= _MAX_INT:
                item["issues"].append({"code": "missing_text_hash" if field not in record["record"] or raw_hash == 0 else "invalid_text_hash"})
                return item
            if text_cache is None:
                text_cache = nodes.source(TEXT_PATH)
                if not isinstance(text_cache[1], dict):
                    raise RuntimeConfigError("中文TextMap形状错误", "dataset_invalid", 500)
            source, texts = text_cache
            item["requested_pointer"] = "/" + str(raw_hash)
            value = texts.get(str(raw_hash))
            if not isinstance(value, str) or not value:
                item["issues"].append({"code": "missing_description_text" if value is None or value == "" else "invalid_description_text"})
            elif len(value.encode("utf8")) > PASSIVE_LIMITS["max_description_bytes"]:
                raise RuntimeConfigError("固有天赋单项原文超过32KiB", "response_too_large", 413)
            else:
                item.update(status="ready", text=value, text_origin={"source_path": TEXT_PATH, "json_pointer": "/" + str(raw_hash),
                                                                    "source_sha256": source["source_sha256"], "node_sha256": fingerprint(value)})
            return item

        avatar = exact("Avatar", {"id": character_id})
        a = avatar["record"]
        candidates_depot = a.get("candSkillDepotIds", [])
        primary = a.get("skillDepotId", 0)
        if (type(primary) is not int or not 0 <= primary <= _MAX_INT or not isinstance(candidates_depot, list)
                or any(type(v) is not int or not 0 <= v <= _MAX_INT for v in candidates_depot)):
            raise RuntimeConfigError("角色技能组身份字段错误", "dataset_invalid", 500)
        depots = list(dict.fromkeys(v for v in [primary, *candidates_depot] if v))
        if skill_depot_id is None:
            if len(depots) != 1:
                raise RuntimeConfigError("角色技能组需明确选择", "skill_depot_selection_required", 409,
                                         {"candidate_skill_depot_ids": depots})
            skill_depot_id = depots[0]
        elif skill_depot_id not in depots:
            raise ValueError("skill_depot_id不属于角色明确列出的技能组")
        resolved_key = (state["snapshot_id"], character_id, skill_depot_id, proud_skill_id)
        if resolved_key in cache:
            cache[key] = cache[resolved_key]
            return PassiveSourceContext(copy.deepcopy(cache[resolved_key]))
        depot = exact("AvatarSkillDepot", {"id": skill_depot_id})
        raw_openings = depot["record"].get("inherentProudSkillOpens")
        if not isinstance(raw_openings, list):
            raw_openings = []
            add("inherent_openings_missing", "selection", blocks_source=True)
        if len(raw_openings) > PASSIVE_LIMITS["max_openings"]:
            raise RuntimeConfigError("固有天赋槽位数超过上限", "response_too_large", 413)
        openings, candidates, inventory = [], [], []
        for index, raw in enumerate(raw_openings):
            origin = {"source_path": depot["source_path"], "source_sha256": depot["source_sha256"],
                      "json_pointer": depot["json_pointer"] + f"/inherentProudSkillOpens/{index}", "node_sha256": fingerprint(raw)}
            item = {"index": index, "raw": copy.deepcopy(raw), "origin": origin, "state": "pending"}
            inventory.append(item)
            if raw == {}:
                item["state"] = "empty"
                continue
            if not isinstance(raw, dict) or type(raw.get("proudSkillGroupId")) is not int or not 1 <= raw["proudSkillGroupId"] <= _MAX_INT:
                add("invalid_inherent_opening", "other_candidate", opening_index=index)
                continue
            opening = {**origin, "record": copy.deepcopy(raw)}
            openings.append((index, opening))
            found = group_records("ProudSkill", "proudSkillGroupId", raw["proudSkillGroupId"])
            if len(candidates) + len(found) > PASSIVE_LIMITS["max_candidates"]:
                raise RuntimeConfigError("固有天赋候选数量超过上限", "response_too_large", 413)
            if not found:
                add("missing_parameter_record", "other_candidate", opening_index=index)
            for offset, ident in found:
                proud = indexed_record("ProudSkill", offset, ident)
                p = proud["record"]
                valid = type(p.get("proudSkillId")) is int and 1 <= p["proudSkillId"] <= _MAX_INT and type(p.get("level")) is int and 1 <= p["level"] <= _MAX_INT
                candidates.append({"proud_skill_id": p.get("proudSkillId"), "proud_skill_group_id": p.get("proudSkillGroupId"),
                    "proud_level": p.get("level"), "opening_index": index, "relation_status": "ready" if valid else "pending",
                    "opening_origin": _origin(opening), "proud_origin": _origin(proud),
                    "threshold": _threshold(opening, proud), "name": text(proud, "nameTextMapHash"),
                    "issues": [] if valid else [{"code": "invalid_proud_identity"}]})
            item["state"] = "candidate" if found else "pending"
        promote_pairs = group_records("AvatarPromote", "avatarPromoteId", a.get("avatarPromoteId"))
        if len(promote_pairs) > PASSIVE_LIMITS["max_promote_records"]:
            raise RuntimeConfigError("角色突破记录超过上限", "response_too_large", 413)
        promote_records = [indexed_record("AvatarPromote", i, k) for i, k in promote_pairs]
        selection = {"character_id": character_id, "skill_depot_id": skill_depot_id, "proud_skill_id": proud_skill_id,
                     "proud_skill_group_id": None, "proud_level": None, "opening_index": None}
        records = {"avatar": avatar, "depot": depot, "selected_opening": None, "promote_records": promote_records, "proud_skill": None}
        source_bundle, descriptions = None, _empty_descriptions()
        if proud_skill_id is None:
            add("passive_selection_required", "selection", blocks_source=True)
        else:
            proud = exact("ProudSkill", {"proudSkillId": proud_skill_id})
            group = proud["record"].get("proudSkillGroupId")
            if type(group) is not int or not 1 <= group <= _MAX_INT:
                raise RuntimeConfigError("选中固有天赋组身份无效", "dataset_invalid", 500)
            matched = [(i, o) for i, o in openings if o["record"]["proudSkillGroupId"] == group]
            if not matched:
                raise ValueError("proud_skill_id不属于所选角色技能组的固有天赋")
            if len(matched) != 1:
                raise RuntimeConfigError("固有天赋组对应多个原始槽位", "ambiguous_reference", 409)
            index, opening = matched[0]
            records.update(proud_skill=proud, selected_opening=opening)
            selection.update(proud_skill_group_id=proud["record"]["proudSkillGroupId"], proud_level=proud["record"].get("level"), opening_index=index)
            if type(selection["proud_level"]) is not int or not 1 <= selection["proud_level"] <= _MAX_INT:
                add("invalid_proud_level", "source", blocks_source=True)
            closure = _closure(conn, state, [proud["definition"]["definition_id"]], {})
            for row in closure["unresolved"]:
                add("unresolved_passive_reference", "source", blocks_source=True,
                    reference_id=row["reference_id"], resolution_status=row["resolution_status"])
            if closure["truncated"]:
                add("closure_truncated", "source", blocks_source=True, frontier=closure["frontier"])
            talents, abilities, operations = [], [], 0
            for definition in closure["definitions"]:
                kind = definition["definition_kind"]
                if kind not in ("talent_config", "ability"):
                    continue
                row = _row(conn, version_line, definition["definition_id"])
                payload, origin = nodes.node(row)
                node = {"definition_id": row["definition_id"], "node_sha256": row["node_sha256"], **origin}
                if kind == "talent_config":
                    if not isinstance(payload, list):
                        add("invalid_talent_operations", "source", blocks_source=True)
                    else:
                        operations += len(payload)
                    talents.append({**node, "operations": copy.deepcopy(payload)})
                else:
                    abilities.append({**node, "payload": copy.deepcopy(payload), "variant_key": row["variant_key"]})
            if operations > PASSIVE_LIMITS["max_operations"] or len(abilities) > PASSIVE_LIMITS["max_abilities"]:
                raise RuntimeConfigError("固有天赋来源操作/能力超过上限", "response_too_large", 413)
            if not isinstance(proud["record"].get("openConfig"), str) or not proud["record"].get("openConfig") or len(talents) != 1:
                add("talent_entry_not_unique", "source", blocks_source=True, count=len(talents))
            if not abilities:
                add("missing_passive_ability", "source", blocks_source=True)
            if not isinstance(proud["record"].get("paramList"), list):
                add("missing_parameter_values", "source", blocks_source=True)
            source_bundle = {"bundle_version": 1, "source_kind": "inherent_proud_skill", "snapshot": copy.deepcopy(state),
                "normalization": copy.deepcopy(state.get("normalization")),
                "source_identity": {"kind": "inherent_proud_skill", **selection, "version_line": version_line,
                                    "snapshot_id": state["snapshot_id"], "upstream_commit": state["upstream_commit"]},
                "parameter_record": copy.deepcopy(proud), "param_list": copy.deepcopy(proud["record"].get("paramList")),
                "talent": talents[0] if len(talents) == 1 else None, "talent_candidates": talents if len(talents) != 1 else [],
                "abilities": abilities, "definitions": closure["definitions"], "references": closure["references"],
                "reference_closed": closure["reference_closed"], "truncated": closure["truncated"], "frontier": closure["frontier"],
                "closure_fingerprint": closure["closure_fingerprint"], "execution_support": "not_evaluated"}
            descriptions = {name: text(proud, field) for name, field in (
                ("name", "nameTextMapHash"), ("description", "descTextMapHash"), ("unlock_description", "unlockDescTextMapHash"))}
            ready_texts = sum(item["status"] == "ready" for item in descriptions.values())
            for name, item in descriptions.items():
                for issue in item["issues"]:
                    add(issue["code"], "description", description_field=name)
            descriptions["status"] = "ready" if ready_texts == 3 else "partial" if ready_texts else "pending"
        status = "pending" if any(i["blocks_source"] for i in issues) else "ready"
        result = {"projection_version": 1, "bundle_version": 1, "status": status, "source_status": status,
            "mode": "discovery" if proud_skill_id is None else "selected", "snapshot": state, "selection": selection,
            "candidates": candidates, "opening_inventory": inventory, "entry_records": records,
            "source_bundle": source_bundle, "descriptions": descriptions, "execution_support": "not_evaluated",
            "gameplay_semantics": "not_evaluated", "issues": issues, "limits": dict(PASSIVE_LIMITS)}
        result["source_fingerprint"] = passive_source_fingerprint(result)
        _bounded(result)
        cache[key] = cache[resolved_key] = copy.deepcopy(result)
        return PassiveSourceContext(copy.deepcopy(result))


def project_passive_source(context, *, character_level=None, ascension=None):
    if not isinstance(context, PassiveSourceContext):
        raise TypeError("需要本次读取创建的内部PassiveSourceContext")
    result = copy.deepcopy(context.source_view)
    selection, records = result["selection"], result["entry_records"]
    _validate(selection["character_id"], selection["skill_depot_id"], selection["proud_skill_id"], character_level, ascension)
    unlock = {"status": "pending", "supplied": {"character_level": character_level, "ascension": ascension},
        "requirement": None, "selected_promote_record": None, "checks": [],
        "eligibility_scope": "explicit_ascension_threshold_and_current_stage_max_level",
        "complete_character_validity": "not_evaluated", "unchecked": passive_source_capabilities()["unchecked"], "issues": []}

    def pending(code, **details):
        unlock["issues"].append(_issue(code, "unlock", **details))

    if selection["proud_skill_id"] is None:
        unlock["status"] = "not_selected"
    elif result["source_status"] != "ready":
        pending("source_not_ready")
    else:
        requirement = _threshold(records["selected_opening"], records["proud_skill"])
        unlock["requirement"] = requirement
        if requirement["status"] != "ready":
            pending("passive_threshold_unresolved")
        if character_level is None:
            pending("character_level_required")
        if ascension is None:
            pending("ascension_required")
        stages = [r for r in records["promote_records"] if type(r["record"].get("promoteLevel")) is int
                  and r["record"]["promoteLevel"] == ascension]
        if ascension is not None and len(stages) != 1:
            pending("promote_stage_unresolved" if not stages else "ambiguous_promote_stage")
        if len(stages) == 1:
            record = stages[0]
            unlock["selected_promote_record"] = copy.deepcopy(record)
            maximum = record["record"].get("unlockMaxLevel")
            if type(maximum) is not int or not 1 <= maximum <= _MAX_INT:
                pending("promote_cap_unresolved")
            elif character_level is not None:
                within = character_level <= maximum
                unlock["checks"].append({"id": "current_stage_max_level", "status": "pass" if within else "fail",
                    "actual": character_level, "expected": {"maximum": maximum}, "origin": _origin(record, "unlockMaxLevel")})
                if not within:
                    unlock["status"] = "invalid"
                    pending("character_level_exceeds_stage_cap")
        if not unlock["issues"]:
            eligible = ascension >= requirement["required_ascension"]
            unlock["checks"].append({"id": "explicit_ascension_threshold", "status": "pass" if eligible else "fail",
                "actual": ascension, "expected": {"minimum": requirement["required_ascension"]},
                "origin": {key: requirement[key]["origin"] for key in ("need_avatar_promote_level", "proud_break_level")}})
            unlock["status"] = "eligible_under_declared_progression" if eligible else "locked"
    result["unlock"] = unlock
    result["issues"].extend(copy.deepcopy(unlock["issues"]))
    result["unlock_fingerprint"] = passive_unlock_fingerprint(result)
    result["content_fingerprint"] = fingerprint(result)
    return _bounded(result)


def get_runtime_passive_source_profile(db_path, *, character_id, skill_depot_id=None, proud_skill_id=None,
                                        character_level=None, ascension=None, version_line="release", snapshot_id=None):
    _validate(character_id, skill_depot_id, proud_skill_id, character_level, ascension)
    context = get_passive_source_context(db_path, character_id=character_id, skill_depot_id=skill_depot_id,
        proud_skill_id=proud_skill_id, version_line=version_line, snapshot_id=snapshot_id)
    return project_passive_source(context, character_level=character_level, ascension=ascension)
