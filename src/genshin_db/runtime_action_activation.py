"""B1 fresh same-snapshot root discovery; never an action execution grant.

This isolated candidate reuses the production transaction, definition integrity,
static level reader, passive unlock projector and exact reference closure.
"""
from __future__ import annotations

import copy

from genshin_db.runtime_capacity_delta import Budget, CapacityError, _integer
from genshin_db.runtime_catalog import RuntimeConfigError, fingerprint
from genshin_db.runtime_passive_source import (
    _threshold,
    get_runtime_passive_source_profile,
)
from genshin_db.runtime_queries import (
    _closure,
    _definition_summary,
    _lookup,
    _Nodes,
    _read,
    _row,
)
from genshin_db.runtime_talent_level import get_runtime_talent_level

CONTRACT = "fresh-activation-root-directory-candidate-v1"
ROOT_LIMITS = {"roots": 128, "definitions": 512, "edges": 2048, "depth": 64}
CATALOG_BUDGET = {"work": 2000000, "bytes": 32 * 1024 * 1024}
CONTAINERS = ("skills", "subSkills", "energySkill", "talents", "inherentProudSkillOpens",
              "specialProudSkillOpens", "extraAbilities", "attackModeSkill", "leaderTalent")
REQUIRED_CONTAINERS = CONTAINERS[:7]


def opening_shape(value, family):
    """Only schema interpretation; no text-to-rule activation guess."""
    if type(value) is dict and not value:
        return "empty", []
    if type(value) is not dict or not _integer(value.get("proudSkillGroupId"), minimum=1):
        return "pending", ["invalid_opening"]
    allowed = {"proudSkillGroupId", "needAvatarPromoteLevel"}
    if family == "inherent" and not value.keys() - allowed:
        return "candidate", []
    return "pending", ["special_activation_uninterpreted" if family == "special" else "unknown_opening_fields"]


def declared_progression(profile, character_level, ascension):
    """Separate original numeric unlock checks from unrelated graph closure.

    This is the existing explicit threshold/stage-cap scope, never full client
    activation. The original full passive profile remains beside this result.
    """
    records = profile["entry_records"]
    opening, proud = records.get("selected_opening"), records.get("proud_skill")
    result = {"status": "pending", "scope": "explicit_ascension_threshold_and_current_stage_max_level",
              "complete_client_activation": False, "issues": [], "requirement": None, "stage": None}
    if not opening or not proud:
        result["issues"].append({"code": "selected_passive_material_missing"})
        return result
    requirement = _threshold(opening, proud)
    result["requirement"] = requirement
    if requirement["status"] != "ready":
        result["issues"].append({"code": "passive_threshold_unresolved"})
    if not _integer(character_level, minimum=1) or not _integer(ascension):
        result["issues"].append({"code": "explicit_progression_required"})
    stages = [r for r in records.get("promote_records", [])
              if type(r["record"].get("promoteLevel")) is int and type(ascension) is int and r["record"]["promoteLevel"] == ascension]
    if len(stages) != 1:
        result["issues"].append({"code": "promote_stage_unresolved_or_ambiguous"})
    else:
        result["stage"] = copy.deepcopy(stages[0])
        maximum = stages[0]["record"].get("unlockMaxLevel")
        if not _integer(maximum, minimum=1):
            result["issues"].append({"code": "promote_cap_unresolved"})
        elif _integer(character_level, minimum=1) and character_level > maximum:
            result["issues"].append({"code": "character_level_exceeds_stage_cap"})
            result["status"] = "invalid"
    if not result["issues"]:
        result["status"] = "active" if ascension >= requirement["required_ascension"] else "inactive"
    return result


def validate_catalog_selection(character_id, skill_depot_id, constellation, base_level, character_level, ascension):
    for name, value, low, high in (("character_id", character_id, 1, 2**53-1),
                                  ("constellation", constellation, 0, 6), ("base_level", base_level, 1, 10)):
        if not _integer(value, minimum=low, maximum=high):
            raise ValueError(name + " requires strict bounded integer")
    for name, value in (("skill_depot_id", skill_depot_id), ("character_level", character_level), ("ascension", ascension)):
        if value is not None and not _integer(value, minimum=0 if name == "ascension" else 1):
            raise ValueError(name + " requires strict bounded integer")


def read_activation_catalog(db_path, *, character_id, skill_depot_id=None, constellation,
                            base_level=10, character_level=None, ascension=None,
                            version_line="release", snapshot_id=None):
    validate_catalog_selection(character_id, skill_depot_id, constellation, base_level, character_level, ascension)
    with _read(db_path, version_line, snapshot_id) as (conn, state):
        return collect_activation_catalog(conn, state, db_path=db_path, character_id=character_id,
            skill_depot_id=skill_depot_id, constellation=constellation, base_level=base_level,
            character_level=character_level, ascension=ascension)


def collect_activation_catalog(conn, state, *, db_path, character_id, skill_depot_id=None,
                               constellation, base_level=10, character_level=None, ascension=None):
    """Collect within the established shared read session; nested readers reuse this connection."""
    _require_shared_session(conn, db_path)
    return _collect_catalog(conn, state, db_path=db_path, character_id=character_id,
                            skill_depot_id=skill_depot_id, constellation=constellation,
                            base_level=base_level, character_level=character_level, ascension=ascension)


def _require_shared_session(conn, db_path):
    from genshin_db.equipment_queries import borrow_read_connection
    borrowed = borrow_read_connection(db_path)
    # Borrowing returns a fresh non-owning facade each time. Compare the actual
    # SQLite handle, not facade identity; still reject a separate transaction.
    if (not conn.in_transaction or borrowed is None
            or getattr(conn, "connection", conn) is not borrowed.connection):
        raise CapacityError("capacity_transaction", "目录必须在同一共享只读事务内读取")


def _collect_catalog(conn, state, *, db_path, character_id, skill_depot_id=None,
                     constellation, base_level=10, character_level=None, ascension=None):
    version_line = state["version_line"]
    validate_catalog_selection(character_id, skill_depot_id, constellation, base_level, character_level, ascension)
    nodes, budget = _Nodes(conn, version_line), Budget(limits=CATALOG_BUDGET)
    materials, roots, issues, profiles, source_files = {}, [], [], {}, {}

    def issue(code, **details):
        if len(issues) >= 512:
            raise CapacityError("capacity_budget", "root issue budget exceeded")
        issues.append({"code": code, **details})

    def material(row):
        ident = row["definition_id"]
        if ident not in materials:
            if len(materials) >= ROOT_LIMITS["definitions"]:
                raise CapacityError("capacity_budget", "definition budget exceeded before load")
            budget.charge(1)
            raw, origin = nodes.node(row)
            value = {"definition": _definition_summary(row), **origin, "record": raw}
            budget.scan(value)
            materials[ident] = copy.deepcopy(value)
            source_files[origin["source_path"]] = origin["source_sha256"]
        return materials[ident]

    def exact(table, key, required=False):
        budget.charge(1)
        path = f"ExcelBinOutput/{table}ExcelConfigData.json"
        rows = _lookup(conn, version_line, "excel_record", key, path)
        budget.charge(len(rows))
        if len(rows) != 1:
            if required:
                raise RuntimeConfigError("root identity missing or ambiguous", "not_found" if not rows else "ambiguous_reference", 409)
            issue("missing_record" if not rows else "ambiguous_record", table=table, key=key)
            return None
        return material(rows[0])

    avatar = exact("Avatar", {"id": character_id}, True)
    av = avatar["record"]
    candidates = av.get("candSkillDepotIds", [])
    if type(candidates) is not list or any(not _integer(v) for v in candidates) or not _integer(av.get("skillDepotId")):
        raise RuntimeConfigError("avatar depot relation invalid", "dataset_invalid", 500)
    depot_ids = list(dict.fromkeys(v for v in [av["skillDepotId"], *candidates] if v))
    if skill_depot_id is None:
        if len(depot_ids) != 1:
            raise RuntimeConfigError("explicit skill depot required", "skill_depot_selection_required", 409)
        skill_depot_id = depot_ids[0]
    if skill_depot_id not in depot_ids:
        raise ValueError("skill_depot_id not in avatar")
    depot = exact("AvatarSkillDepot", {"id": skill_depot_id}, True)
    dep = depot["record"]
    containers = {k: {"present": k in dep, "raw": copy.deepcopy(dep.get(k)),
                       "pointer": depot["json_pointer"] + "/" + k} for k in CONTAINERS}
    budget.scan(containers)

    def root(kind, pointer, raw, activation="pending", reasons=(), owner=None):
        if len(roots) >= ROOT_LIMITS["roots"]:
            raise CapacityError("capacity_budget", "root budget exceeded before append")
        budget.scan(raw)
        value = {"kind": kind, "occurrence_pointer": pointer, "raw": copy.deepcopy(raw),
                 "enumeration_status": "enumerated", "activation_status": activation,
                 "material_status": "pending", "capacity_footprint_status": "unreviewed",
                 "definition_ids": [], "issues": [{"code": c} for c in reasons]}
        owner = owner or depot
        value["origin"] = {"source_path": owner["source_path"], "source_sha256": owner["source_sha256"],
                           "json_pointer": pointer, "node_sha256": fingerprint(raw)}
        value["occurrence_identity"] = fingerprint({"snapshot": state["snapshot_id"], "kind": kind,
                                                    "origin": value["origin"]})
        roots.append(value)
        return value

    def attach(item, record):
        if record:
            ident = record["definition"]["definition_id"]
            if ident not in item["definition_ids"]:
                item["definition_ids"].append(ident)
            item["material_status"] = "ready"

    def empty(item):
        item.update(activation_status="not_applicable", material_status="not_applicable",
                    capacity_footprint_status="not_applicable")

    for field in REQUIRED_CONTAINERS:
        if field not in dep:
            issue("root_container_missing", path=containers[field]["pointer"])
            root("missing_container", containers[field]["pointer"], None, reasons=("root_container_missing",))

    proud_path = "ExcelBinOutput/ProudSkillExcelConfigData.json"
    _, proud_records = nodes.source(proud_path)
    if type(proud_records) is not list:
        raise RuntimeConfigError("ProudSkill source invalid", "dataset_invalid", 500)
    proud_groups = {}
    for raw in proud_records:
        budget.charge(1)
        if type(raw) is dict and _integer(raw.get("proudSkillGroupId"), minimum=1):
            proud_groups.setdefault(raw["proudSkillGroupId"], []).append(raw)

    # The existing static-level projector resolves each ordinary slot from its
    # own actual target operations. Its pending result is retained in full.
    for slot in ("normal", "skill", "burst"):
        try:
            value = get_runtime_talent_level(db_path, character_id=character_id, skill_depot_id=skill_depot_id,
                slot=slot, constellation=constellation, base_level=base_level,
                version_line=version_line, snapshot_id=state["snapshot_id"])
            budget.scan(value)
            profiles[slot] = copy.deepcopy(value)
        except CapacityError:
            raise
        except (ValueError, RuntimeConfigError) as exc:
            profiles[slot] = {"status": "pending", "issues": [{"code": getattr(exc, "code", "invalid_selection"), "message": str(exc)}]}

    ordinary_ids = {p.get("selection", {}).get("skill_id"): p for p in profiles.values() if "selection" in p}
    for field in ("skills", "subSkills", "energySkill", "attackModeSkill"):
        if field not in dep:
            continue
        value = dep[field]
        values = value if field in ("skills", "subSkills") and type(value) is list else [value]
        if field in ("skills", "subSkills") and type(value) is not list:
            issue("invalid_skill_container", field=field)
        for i, ident in enumerate(values):
            pointer = containers[field]["pointer"] + ("/" + str(i) if field in ("skills", "subSkills") else "")
            item = root("skill_record", pointer, ident, "available_skill_definition")
            if type(ident) is int and ident == 0:
                empty(item)
                continue
            if not _integer(ident, minimum=1):
                item["issues"].append({"code": "invalid_skill_id"})
                continue
            record = exact("AvatarSkill", {"id": ident})
            attach(item, record)
            profile = ordinary_ids.get(ident)
            if profile:
                item["level_profile_slot"] = profile["selection"]["slot"]
                parameter = profile.get("records", {}).get("proud_skill")
                if profile["status"] == "ready" and parameter:
                    attach(item, exact("ProudSkill", {"proudSkillId": parameter["record"]["proudSkillId"]}))
                else:
                    item["issues"].append({"code": "effective_parameter_unresolved"})
                    group = record["record"].get("proudSkillGroupId") if record else None
                    alternatives = proud_groups.get(group, []) if _integer(group, minimum=1) else []
                    item["parameter_selection"] = "all_exact_group_candidates_no_selected_effective_level"
                    item["candidate_parameter_levels"] = []
                    for parameter_candidate in alternatives:
                        item["candidate_parameter_levels"].append(parameter_candidate.get("level"))
                        attach(item, exact("ProudSkill", {"proudSkillId": parameter_candidate.get("proudSkillId")}))
            elif record and record["record"].get("proudSkillGroupId"):
                item["issues"].append({"code": "nonordinary_skill_parameter_level_unresolved"})
                group = record["record"]["proudSkillGroupId"]
                alternatives = proud_groups.get(group, []) if _integer(group, minimum=1) else []
                item["parameter_selection"] = "all_exact_group_candidates_no_selected_effective_level"
                item["candidate_parameter_levels"] = []
                for parameter_candidate in alternatives:
                    item["candidate_parameter_levels"].append(parameter_candidate.get("level"))
                    attach(item, exact("ProudSkill", {"proudSkillId": parameter_candidate.get("proudSkillId")}))

    values = dep.get("talents")
    if type(values) is list:
        for rank, ident in enumerate(values, 1):
            item = root("constellation", containers["talents"]["pointer"] + "/" + str(rank-1), ident,
                        "active" if rank <= constellation else "inactive")
            item["activation_rule"] = {"kind": "declared_constellation_rank", "rank": rank, "constellation": constellation}
            if _integer(ident, minimum=1):
                attach(item, exact("AvatarTalent", {"talentId": ident}))
            else:
                item["issues"].append({"code": "invalid_constellation_id"})
        if len(values) != 6 or len({str(v) for v in values}) != len(values):
            issue("constellation_directory_incomplete_or_duplicate")
    else:
        issue("invalid_constellation_container")

    for field, family in (("inherentProudSkillOpens", "inherent"), ("specialProudSkillOpens", "special")):
        values = dep.get(field)
        if type(values) is not list:
            issue("invalid_opening_container", field=field)
            continue
        for i, raw in enumerate(values):
            shape, reasons = opening_shape(raw, family)
            item = root(family + "_opening", containers[field]["pointer"] + "/" + str(i), raw, reasons=reasons)
            if shape == "empty":
                empty(item)
                continue
            group = raw.get("proudSkillGroupId") if type(raw) is dict else None
            matches = proud_groups.get(group, []) if _integer(group, minimum=1) else []
            if not matches:
                item["issues"].append({"code": "missing_opening_proud_records"})
            for candidate in matches:
                record = exact("ProudSkill", {"proudSkillId": candidate.get("proudSkillId")})
                attach(item, record)
            if family == "inherent" and shape == "candidate" and len(matches) == 1:
                try:
                    view = get_runtime_passive_source_profile(db_path, character_id=character_id,
                        skill_depot_id=skill_depot_id, proud_skill_id=matches[0]["proudSkillId"],
                        character_level=character_level, ascension=ascension,
                        version_line=version_line, snapshot_id=state["snapshot_id"])
                    budget.scan(view)
                    item["passive_profile"] = view
                    progression = declared_progression(view, character_level, ascension)
                    budget.scan(progression)
                    item["declared_progression"] = progression
                    item["activation_status"] = progression["status"]
                    item["activation_scope"] = progression["scope"]
                    item["issues"].extend(copy.deepcopy(progression["issues"]))
                except CapacityError:
                    raise
                except (ValueError, RuntimeConfigError) as exc:
                    item["issues"].append({"code": "passive_query_pending", "message": str(exc)})
            elif len(matches) > 1:
                item["issues"].append({"code": "opening_parameter_selection_ambiguous"})

    values = dep.get("extraAbilities")
    if type(values) is list:
        for i, name in enumerate(values):
            item = root("extra_ability", containers["extraAbilities"]["pointer"] + "/" + str(i), name,
                        "declared_extra_binding")
            if type(name) is str and name == "":
                empty(item)
                continue
            if type(name) is not str:
                item["issues"].append({"code": "invalid_extra_ability"})
                continue
            matches = _lookup(conn, version_line, "ability", name)
            if len(matches) != 1:
                item["issues"].append({"code": "extra_ability_missing_or_ambiguous"})
            for row in matches:
                attach(item, material(row))
    else:
        issue("invalid_extra_ability_container")

    # These fields are discovered without pretending a numeric hash is an
    # already reviewed resource mount. Keep precise missing bindings visible.
    if "leaderTalent" in dep:
        item = root("leader_talent", containers["leaderTalent"]["pointer"], dep["leaderTalent"],
                    reasons=("leader_talent_binding_semantics_unreviewed",))
    root("avatar_base_resource", avatar["json_pointer"] + "/combatConfigHash", av.get("combatConfigHash"),
         reasons=("exact_avatar_resource_hash_binding_required",), owner=avatar)

    identities = list(dict.fromkeys(v for item in roots for v in item["definition_ids"]))
    if len(identities) > 128:
        raise CapacityError("capacity_budget", "closure root budget exceeded")
    graph = _closure(conn, state, identities, {"max_nodes": 512, "max_edges": 2048, "max_depth": 64, "max_bytes": 2*1024*1024}) if identities else None
    if graph:
        budget.scan(graph)
        for definition in graph["definitions"]:
            material(_row(conn, version_line, definition["definition_id"]))
        if graph["truncated"]:
            issue("reference_graph_truncated", frontier=graph["frontier"])
        for ref in graph["unresolved"]:
            issue("unresolved_reference", reference_id=ref["reference_id"], status=ref["resolution_status"])
    directory_invalid = {"root_container_missing", "invalid_skill_container", "invalid_constellation_container",
                         "constellation_directory_incomplete_or_duplicate", "invalid_opening_container", "invalid_extra_ability_container"}
    root_invalid = {"invalid_skill_id", "invalid_constellation_id", "invalid_opening", "invalid_extra_ability"}
    directory_pending = any(i["code"] in directory_invalid for i in issues) or any(
        i["code"] in root_invalid for r in roots for i in r["issues"])
    result = {"contract": CONTRACT, "snapshot": state,
              "selection": {"character_id": character_id, "skill_depot_id": skill_depot_id,
                            "constellation": constellation, "base_level": base_level,
                            "character_level": character_level, "ascension": ascension},
              "normalization": {"mapping_fingerprint": nodes.normalization.mapping_fingerprint if nodes.normalization else None},
              "containers": containers, "root_occurrences": roots, "materials": materials,
              "level_profiles": profiles, "reference_graph": graph, "source_files": source_files,
              "root_directory_status": "pending" if directory_pending else "enumerated",
              "root_directory_scope": "listed_depot_containers_and_required_avatar_resource_slot_only",
              "unclassified_depot_fields": sorted(set(dep) - set(CONTAINERS) - {"id"}),
              "activation_closure_status": "pending", "capacity_closure_status": "unreviewed",
              "action_dispatch": "pending", "whole_skill_coverage": "partial", "issues": issues,
              "execution_authority": False, "limits": {**ROOT_LIMITS, "material_bytes": CATALOG_BUDGET["bytes"], "work": CATALOG_BUDGET["work"]},
              "load_scope": "existing_source_reader_64MiB_decode_and_cache_limits_separate_from_retained_material"}
    # Retained/copy material and serialization output are separate buffers.
    # Rewalking the same graph consumes work again, not another retained copy.
    output_budget = Budget(limits=CATALOG_BUDGET)
    output_budget.work = budget.work
    output_budget.scan(result)
    usage = {"work": output_budget.work, "retained_scan_safe_bytes": budget.bytes,
             "output_safe_encoding_bytes": output_budget.bytes,
             "root_occurrences": len(roots), "material_definitions": len(materials)}
    output_budget.scan({"usage": usage, "content_fingerprint": "0" * 64})
    result["usage"] = {"work": output_budget.work, "retained_scan_safe_bytes": budget.bytes,
                       "output_safe_encoding_bytes": output_budget.bytes,
                       "root_occurrences": len(roots), "material_definitions": len(materials)}
    result["content_fingerprint"] = fingerprint(result)
    return result
