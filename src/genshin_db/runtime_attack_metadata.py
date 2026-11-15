"""Finite, source-bound attack metadata; never native action dispatch."""
from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass
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
from genshin_runtime.normalization import Profile

ATTACK_METADATA_LIMITS = {"max_bytes": 262144, "max_candidates": 32, "max_nodes": 8192,
                          "max_depth": 64, "max_issues": 128, "max_cached_selections": 32}
_CACHE = "attack-metadata-fresh-v1"
_ELEMENTS = {"Fire": "pyro", "Ice": "cryo", "Water": "hydro", "Wind": "anemo"}
_CONTROL = {"$type", "target", "targetType", "logic", "TargetAltitudeType", "triggerType",
            "canBeModifiedBy", "attackTag", "attackTags", "attenuationTag", "attackType", "strikeType", "key", "talentParam",
            "globalValueKey", "overrideMapKey", "abilityName"}


@dataclass(frozen=True)
class AttackMetadataContext:
    public_projection: dict
    parameter_context: dict
    attenuation_profiles: dict


@lru_cache(maxsize=1)
def _recipe():
    path = resources.files("genshin_runtime").joinpath("resources/attack_metadata_profiles.json")
    result = json.loads(path.read_text(encoding="utf8"))
    result["recipe_fingerprint"] = fingerprint(result)
    result["unit_fingerprint"] = fingerprint(result["unit_contract"])
    return result


def attack_metadata_capabilities():
    recipe = _recipe()
    return {"profile_version": 1, "recipe_id": recipe["recipe_id"], "recipe_version": recipe["recipe_version"],
            "recipe_fingerprint": recipe["recipe_fingerprint"], "limits": dict(ATTACK_METADATA_LIMITS),
            "selection": "角色、动作槽、命座与基础天赋等级均为精确选择；candidate_id省略用于发现，选择不会按值或名称猜测",
            "projection": "完整父链守卫的参数定位、元素、原始GU、衰减规则组及原始attenuationTag；缺失计数标签时明确待核实",
            "unit_contract": copy.deepcopy(recipe["unit_contract"]),
            "whole_source_status": "partial", "action_dispatch": "pending",
            "gameplay_interpretation": "declared_not_verified",
            "application_identity": "attenuationTag是计数标签证据；attackTag仅作攻击标签观察，绝不代替计数标签；同技能不同段、召唤物和命座追加攻击须逐组件核实",
            "unsupported": ["native_dispatch", "timing", "inferred_shared_identity", "contact_inference", "damage_extra",
                            "damage_percentage_ratio", "dynamic_parameter_writes", "unreviewed_control_tree"]}


def _bounded_walk(value):
    stack, count = [("", value, 0)], 0
    while stack:
        path, node, depth = stack.pop()
        count += 1
        if count > ATTACK_METADATA_LIMITS["max_nodes"] or depth > ATTACK_METADATA_LIMITS["max_depth"]:
            raise RuntimeConfigError("攻击元信息树预算超限", "response_too_large", 413)
        yield path, node
        children = node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else ()
        for key, child in children:
            token = str(key).replace("~", "~0").replace("/", "~1")
            stack.append((path + "/" + token, child, depth + 1))


def _shape(value, template, key=""):
    """Reviewed key/type/control tree, not sample IDs or whole-node SHA pins."""
    if isinstance(template, dict):
        if not isinstance(value, dict):
            return False
        # ``attenuationTag`` is a reviewed attack-info field, but the pinned
        # evidence set also contains older components where it is omitted.
        # Admit it only on an attack-info object; arbitrary sibling additions
        # remain rejected by the complete-shape guard.
        extra = value.keys() - template.keys()
        optional_tag = (extra == {"attenuationTag"}
                        and {"attackProperty", "attenuationGroup"} <= template.keys()
                        and isinstance(value.get("attenuationTag"), str)
                        and bool(value["attenuationTag"].strip()))
        if extra and not optional_tag:
            return False
        if template.keys() - value.keys():
            return False
        return all(_shape(value[k], v, k) for k, v in template.items())
    if isinstance(template, list):
        return isinstance(value, list) and len(value) == len(template) and all(
            _shape(a, b, key) for a, b in zip(value, template, strict=True))
    if type(template) is bool or template is None:
        return type(value) is type(template) and value == template
    if type(template) in (int, float):
        return type(value) in (int, float) and (type(value) is int or math.isfinite(value))
    if isinstance(template, str):
        return isinstance(value, str) and bool(value.strip()) and (key not in _CONTROL or value == template)
    return False


def _selection(selection, candidate_id):
    from genshin_db.runtime_queries import _identifier, _integer
    from genshin_db.validation import version_line

    allowed = {"character_id", "slot", "skill_id", "skill_depot_id", "talent_level", "constellation", "version_line", "snapshot_id"}
    if selection.keys() - allowed or "character_id" not in selection:
        raise QueryValidationError("攻击元信息选择字段未知或缺少character_id")
    _integer(selection["character_id"], "character_id")
    _integer(selection.get("constellation", 0), "constellation", 0, 6)
    if (selection.get("slot") is None) == (selection.get("skill_id") is None):
        raise QueryValidationError("slot与skill_id必须且只能提供一个")
    if "slot" in selection and selection["slot"] not in ("normal", "skill", "burst"):
        raise QueryValidationError("slot必须为normal/skill/burst")
    for key in ("skill_id", "skill_depot_id"):
        if key in selection:
            _integer(selection[key], key)
    if "talent_level" in selection:
        _integer(selection["talent_level"], "talent_level", 1, 15)
    line = version_line(selection.get("version_line", "release"))
    if "snapshot_id" in selection:
        _identifier(selection["snapshot_id"], "snapshot_id")
    if candidate_id is not None:
        _identifier(candidate_id, "candidate_id")
        if not candidate_id.strip():
            raise QueryValidationError("candidate_id不能空白")
    return {**selection, "version_line": line, "constellation": selection.get("constellation", 0)}


def _context_supported(view, recipe):
    """Recognize unrelated sibling structures; an unknown mixin is not harmless."""
    templates = recipe["context_templates"]
    allowed = set().union(*(t.keys() for t in templates))
    if view.keys() - allowed:
        return False
    for key, value in view.items():
        if key == "abilityName":
            if not isinstance(value, str) or not value.strip():
                return False
        elif key == "abilitySpecials":
            if not isinstance(value, dict) or len(value) > 64 or any(not _scalar_status(v) for v in value.values()):
                return False
        elif key == "modifiers":
            known = [m for t in templates for m in t.get("modifiers", {}).values()]
            if not isinstance(value, dict) or len(value) > 64 or any(not any(_shape(m, s) for s in known) for m in value.values()):
                return False
        elif not any(key in t and _shape(value, t[key], key) for t in templates):
            return False
    return True


def _semantic_snapshot(state):
    result = {k: copy.deepcopy(state[k]) for k in ("snapshot_id", "version_line", "upstream_commit", "indexer_version",
                                                  "schema_version", "normalization_version", "mapping_fingerprint") if k in state}
    if isinstance(state.get("normalization"), dict):
        result["normalization"] = {k: copy.deepcopy(state["normalization"][k]) for k in (
            "normalization_version", "mapping_fingerprint", "profile_id", "upstream_commit") if k in state["normalization"]}
    return result


def _semantic_projection(projection):
    result = copy.deepcopy(projection)
    source = result["attenuation"].get("profile")
    if source is not None:
        result["attenuation"]["profile"] = {key: source[key] for key in (
            "source", "raw_record", "projection", "content_fingerprint") if key in source}
        result["attenuation"]["profile"]["snapshot"] = _semantic_snapshot(source["snapshot"])
    descriptions = result["parameter"].get("parameter_descriptions")
    if descriptions is not None:
        descriptions.pop("issues", None)
    return result


def _origin(row, source, pointer, raw):
    return {"owner_definition_id": row["definition_id"], "source_path": row["source_path"],
            "source_sha256": source["source_sha256"], "json_pointer": row["json_pointer"] + pointer,
            "node_sha256": fingerprint(raw)}


def _scalar_status(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _empty_projection():
    return {"parameter": {"status": "pending", "param_index": None, "value": None},
            "element": {"status": "pending", "value": None},
            "gauge": {"status": "pending", "value": None, "raw_durability": None, "tax_applied": False},
            "attenuation": {"status": "pending", "group": None, "profile": None},
            "application_identity": {"status": "pending", "tag": None, "source": None,
                                     "evidence": "attenuation_tag_missing",
                                     "next_action": "核实该角色这一具体攻击段的attenuationTag；不得按普攻/战技/爆发类别猜测"},
            "attack_tag": {"status": "pending", "value": None, "source": None,
                           "application_identity": False}}


def _candidates(nodes, state, facts, recipe, local, parameter_context, attenuation_getter):
    from genshin_db.runtime_action_parameter import project_action_parameter
    from genshin_db.runtime_queries import _row

    candidates, graph, issues, abilities = [], [], [], {}
    scanned_nodes = graph_bytes = 0
    bindings = [b for b in facts["parameter_bindings"] if b["role"] == "skill_level"]
    for binding in bindings:
        target = binding["target"]
        if target["resolution_status"] != "resolved" or len(target["candidate_ids"]) != 1:
            issues.append({"code": "attack_ability_ambiguous", "operation_pointer": binding["operation_pointer"],
                           "scope": "other_candidate_discovery", "blocks_selected": False})
            continue
        abilities.setdefault(target["candidate_ids"][0], []).append(binding)
    for ident, ability_bindings in sorted(abilities.items()):
        row = _row(nodes.conn, state["version_line"], ident)
        raw, source = nodes.node(row)
        scanned_nodes += sum(1 for _ in _bounded_walk(raw))
        graph_bytes += len(dumps(raw).encode("utf8"))
        if scanned_nodes > ATTACK_METADATA_LIMITS["max_nodes"] or graph_bytes > ATTACK_METADATA_LIMITS["max_bytes"]:
            raise RuntimeConfigError("攻击元信息完整父链预算超限", "response_too_large", 413)
        origin = _origin(row, source, "", raw)
        try:
            view = local.normalize(raw, kind="ability")
        except ValueError:
            issues.append({"code": "attack_normalization_collision", "source": origin,
                           "scope": "other_candidate_discovery", "blocks_selected": False})
            continue
        graph_item = {"source": origin, "raw": copy.deepcopy(raw), "whole_source_status": "partial", "branches": []}
        graph.append(graph_item)
        groups = {}
        for path, node in _bounded_walk(view):
            if not isinstance(node, dict) or not path.endswith("/attackProperty"):
                continue
            tokens = path.split("/")
            if len(tokens) >= 4 and tokens[1] == "modifiers":
                parent = "/".join(tokens[:3])
            else:
                parent = "/".join(tokens[:3]) if len(tokens) >= 3 else path
            groups.setdefault(parent, []).append((path, node))
        for parent, leaves in sorted(groups.items()):
            if len(candidates) >= ATTACK_METADATA_LIMITS["max_candidates"]:
                raise RuntimeConfigError("攻击元信息候选数量超限", "response_too_large", 413)
            raw_parent = local.raw_pointer(raw, parent)
            parent_origin = _origin(row, source, raw_parent, resolve_pointer(raw, raw_parent))
            ident_data = {"source_path": row["source_path"], "ability_name": view.get("abilityName"),
                          "variant": row["variant_key"], "parent": parent, "branches": sorted(p for p, _ in leaves)}
            candidate = {"candidate_id": "attack." + fingerprint(ident_data), "status": "pending",
                "ability_name": view.get("abilityName"), "parent": parent_origin,
                "projection": _empty_projection(), "issues": [], "branches": [], "semantic_dependency": None}
            candidates.append(candidate)

            def issue(code, _candidate=candidate, **detail):
                _candidate["issues"].append({"code": code, **detail})

            for path, prop in leaves:
                rp = local.raw_pointer(raw, path)
                candidate["branches"].append({"source": _origin(row, source, rp, resolve_pointer(raw, rp)),
                                               "raw": copy.deepcopy(resolve_pointer(raw, rp))})
            graph_item["branches"].append({"parent": parent_origin, "candidate_id": candidate["candidate_id"],
                                           "execution": "not_evaluated"})
            parent_value = resolve_pointer(view, parent)
            matching = [t for t in recipe["guard_templates"] if parent.startswith("/modifiers/") and _shape(parent_value, t["modifier_template"])]
            if not matching:
                issue("attack_parent_shape_unsupported")
            if not _context_supported(view, recipe):
                issue("attack_context_structure_unsupported")
            if not row["source_path"].startswith("BinOutput/Ability/Temp/AvatarAbilities/"):
                issue("attack_owner_resource_unsupported")
            if len({b["target"]["ability_name"] for b in ability_bindings}) != 1 or view.get("abilityName") != ability_bindings[0]["target"]["ability_name"]:
                issue("attack_ability_identity_mismatch")
            # Preserve all leaves. Only the reviewed single/if-else modifier shapes may merge metadata.
            infos = [resolve_pointer(view, path.rsplit("/", 1)[0]) for path, _ in leaves]
            property_values = [prop for _, prop in leaves]
            metadata = [{"property": prop, "group": info.get("attenuationGroup"),
                         "attack_tag": info.get("attackTag"), "attenuation_tag": info.get("attenuationTag"),
                         "canBeModifiedBy": info.get("canBeModifiedBy")} for prop, info in zip(property_values, infos, strict=True)]
            if not metadata or any(item != metadata[0] for item in metadata[1:]):
                issue("attack_branch_metadata_not_equivalent")
            prop, info = property_values[0], infos[0]
            if any("damageExtra" in p or "damagePercentageRatio" in p for p in property_values):
                issue("attack_composite_damage_unsupported")
            element = prop.get("elementType")
            gauge = prop.get("elementDurability")
            def field_origin(field, _row=row, _source=source, _raw=raw, _path=leaves[0][0], _prop=prop):
                return _origin(_row, _source, local.raw_pointer(_raw, _path + "/" + field), _prop[field])
            def info_field_origin(field, _row=row, _source=source, _raw=raw, _path=leaves[0][0], _info=info):
                pointer = _path.rsplit("/", 1)[0] + "/" + field
                return _origin(_row, _source, local.raw_pointer(_raw, pointer), _info[field])
            if isinstance(element, str) and element in _ELEMENTS:
                candidate["projection"]["element"] = {"status": "ready", "value": _ELEMENTS[element], "source": field_origin("elementType")}
            else:
                issue("attack_element_unsupported")
            raw_gu = Decimal(str(gauge)) / 25 if type(gauge) in (int, float) and (type(gauge) is int or math.isfinite(gauge)) else None
            candidate["projection"]["gauge"].update(raw_durability=copy.deepcopy(gauge), unit_contract=copy.deepcopy(recipe["unit_contract"]),
                unit_fingerprint=recipe["unit_fingerprint"])
            if raw_gu in (Decimal(1), Decimal(2), Decimal(4)):
                candidate["projection"]["gauge"].update(status="ready", value=int(raw_gu), source=field_origin("elementDurability"))
            else:
                issue("attack_gauge_unknown_or_unsupported")
            symbol = prop.get("damagePercentage")
            matches = [b for b in ability_bindings if b["parameter_name"] == symbol]
            if not isinstance(symbol, str) or not symbol or len(matches) != 1:
                issue("attack_parameter_link_not_unique")
            else:
                binding = matches[0]
                talent = next((t for t in facts["talents"] if t["role"] == "skill_level"
                               and t["source_path"] == binding["source_path"]
                               and binding["operation_pointer"].startswith(t["json_pointer"] + "/")), None)
                op = None
                if talent:
                    suffix = binding["operation_pointer"][len(talent["json_pointer"]):]
                    op = resolve_pointer(talent["operations_normalized"], suffix)
                expected_keys = {"$type", "CLGIKOPIKBC", "abilityName", "paramDelta", "paramSpecial"}
                expression = binding["raw_expression"]
                match = re.fullmatch(r"%([1-9][0-9]?)", expression) if isinstance(expression, str) else None
                if (not isinstance(op, dict) or op.keys() != expected_keys or op.get("$type") != "ModifyAbility"
                        or op.get("CLGIKOPIKBC") is not True or not match
                        or op.get("abilityName") != view.get("abilityName") or op.get("paramSpecial") != symbol):
                    issue("attack_parameter_operation_unsupported")
                else:
                    index = int(match[1]) - 1
                    if index > 63:
                        issue("attack_parameter_index_unsupported")
                    elif not _scalar_status(binding.get("parameter_input")):
                        candidate["projection"]["parameter"].update(raw_input=binding["parameter_input"], source=copy.deepcopy(binding.get("parameter_input_origin")))
                        issue("attack_parameter_scalar_pending")
                    else:
                        projected = project_action_parameter(parameter_context, index)
                        value = projected["scalar_projection"]["value"]
                        operation_raw = resolve_pointer(talent["operations_raw"], suffix)
                        operation_source = {"source_path": talent["source_path"], "source_sha256": talent["source_sha256"],
                            "json_pointer": binding["operation_pointer"], "node_sha256": fingerprint(operation_raw)}
                        candidate["projection"]["parameter"] = {"status": projected["scalar_projection"]["status"],
                            "param_index": index, "value": value, "source": projected["source"],
                            "parameter_descriptions": projected["parameter_descriptions"], "entry_sources": projected["entry_sources"],
                            "level": projected["level"], "symbol": symbol, "expression": expression,
                            "operation": {"source": operation_source, "raw": copy.deepcopy(operation_raw), "execution": "not_evaluated"},
                            "gameplay_interpretation": "declared_not_verified"}
                        if projected["scalar_projection"]["status"] != "ready" or not _scalar_status(value):
                            candidate["projection"]["parameter"]["status"] = "pending"
                            issue("attack_parameter_scalar_pending")
                # A second symbol use outside defaults and selected attack leaves may be a dynamic write.
                allowed_paths = {p + "/damagePercentage" for p, _ in leaves}
                if any(v == symbol and p not in allowed_paths for p, v in _bounded_walk(view) if isinstance(v, str)):
                    issue("attack_parameter_other_use_unreviewed")
                specials = view.get("abilitySpecials")
                if not isinstance(specials, dict) or symbol not in specials or not _scalar_status(specials[symbol]):
                    issue("attack_parameter_default_unknown")
            group = info.get("attenuationGroup")
            candidate["projection"]["attenuation"]["group"] = group
            if "attenuationGroup" in info:
                group_pointer = local.raw_pointer(raw, leaves[0][0].rsplit("/", 1)[0] + "/attenuationGroup")
                candidate["projection"]["attenuation"]["source"] = _origin(row, source, group_pointer, group)
            # Do not obtain a ready downstream source for an unsupported attack parent or parameter.
            if not candidate["issues"] and isinstance(group, str) and group.strip():
                try:
                    attenuation = attenuation_getter(group)
                    candidate["projection"]["attenuation"].update(status=attenuation["execution"]["status"], profile=copy.deepcopy(attenuation))
                    if attenuation["execution"]["status"] != "ready":
                        issue("attack_attenuation_pending")
                except RuntimeConfigError as exc:
                    if exc.reason not in {"not_found", "unsupported_source", "ambiguous_reference"}:
                        raise
                    issue("attack_attenuation_unavailable", reason=exc.reason)
            else:
                issue("attack_attenuation_not_admitted")
            if not isinstance(group, str) or not group.strip():
                issue("attack_attenuation_group_missing")
            # ``attackTag`` classifies an attack for other game systems.  It
            # must never be substituted for the application counter identity.
            # Only the separately named upstream ``attenuationTag`` is
            # authoritative for that identity.
            attack_tag = info.get("attackTag")
            if isinstance(attack_tag, str) and attack_tag.strip():
                candidate["projection"]["attack_tag"] = {"status": "observed", "value": attack_tag,
                    "source": info_field_origin("attackTag"), "application_identity": False}
            attenuation_tag = info.get("attenuationTag")
            if isinstance(attenuation_tag, str) and attenuation_tag.strip():
                candidate["projection"]["application_identity"] = {"status": "ready", "tag": attenuation_tag,
                    "source": info_field_origin("attenuationTag"), "evidence": "upstream_attenuation_tag",
                    "next_action": None}
            elif "attenuationTag" in info:
                candidate["projection"]["application_identity"].update(
                    evidence="invalid_attenuation_tag",
                    next_action="上游attenuationTag为空或类型异常；需逐攻击段复核原始节点")
                issue("attack_attenuation_tag_invalid")
            candidate["status"] = "pending" if candidate["issues"] else "ready"
            candidate["semantic_dependency"] = {"recipe": {"id": recipe["recipe_id"], "version": recipe["recipe_version"],
                "fingerprint": recipe["recipe_fingerprint"], "mapping_fingerprint": local.mapping_fingerprint},
                "snapshot": _semantic_snapshot(state), "selection": copy.deepcopy(facts["selection"]), "level": copy.deepcopy(facts["level"]),
                "ability": origin, "parent_chain": [parent_origin], "branches": [b["source"] for b in candidate["branches"]],
                "projection": _semantic_projection(candidate["projection"])}
            candidate["source_fingerprint"] = fingerprint(candidate["semantic_dependency"])
    return candidates, graph, issues


def get_attack_metadata_context(db_path, *, candidate_id=None, **selection):
    from genshin_db.equipment_queries import borrow_read_cache
    from genshin_db.runtime_action_parameter import get_action_parameter_context
    from genshin_db.runtime_attenuation import get_runtime_attenuation_profile
    from genshin_db.runtime_queries import _Nodes, _read

    args = _selection(selection, candidate_id)
    recipe, prepared = _recipe(), {}
    with _read(db_path, args["version_line"], args.get("snapshot_id")) as (conn, state):
        cache = borrow_read_cache(db_path, _CACHE)
        # The locked transaction resolves an omitted snapshot to this exact one.
        # Retain every other request field: a slot request is not an ID request.
        key = ("parameters", fingerprint({**args, "snapshot_id": state["snapshot_id"]}))
        if key not in cache:
            if sum(k[0] == "parameters" for k in cache) >= ATTACK_METADATA_LIMITS["max_cached_selections"]:
                raise RuntimeConfigError("攻击元信息会话选择数量超限", "response_too_large", 413)
            cache[key] = copy.deepcopy(get_action_parameter_context(db_path, **args))
        context = copy.deepcopy(cache[key])
        facts = context["facts"]

        def attenuation(group):
            k = ("attenuation", args["version_line"], group)
            if k not in cache:
                cache[k] = copy.deepcopy(get_runtime_attenuation_profile(db_path, group=group, version_line=args["version_line"], snapshot_id=state["snapshot_id"]))
            prepared[group] = copy.deepcopy(cache[k])
            return prepared[group]

        issues, candidates, graph = [], [], []
        if (facts["level"].get("effective_level") not in range(1, 16)
                or not set(facts["selection"].get("matching_slots", ())) & {"normal", "skill", "burst"}):
            issues.append({"code": "attack_selection_scope_unsupported"})
        nodes = _Nodes(conn, args["version_line"])
        if state["upstream_commit"] != recipe["commit"] or nodes.normalization is None:
            issues.append({"code": "attack_mapping_unreviewed"})
        if not issues:
            local = Profile(recipe["recipe_id"], (recipe["commit"],),
                            {**nodes.normalization.fields, **recipe["fields"]}, {**nodes.normalization.types, **recipe["types"]})
            candidates, graph, issues = _candidates(nodes, state, facts, recipe, local, context, attenuation)
        result = {"profile_version": 1, "snapshot": copy.deepcopy(state), "selection": copy.deepcopy(facts["selection"]),
            "status": "pending", "whole_source_status": "partial", "candidates": candidates, "selected_candidate": None,
            "source_graph": graph, "semantic_dependency": None, "source_fingerprint": None, "issues": issues,
            "limits": dict(ATTACK_METADATA_LIMITS), "action_dispatch": "pending", "gameplay_interpretation": "declared_not_verified"}
        if len(issues) + sum(len(c["issues"]) for c in candidates) > ATTACK_METADATA_LIMITS["max_issues"]:
            raise RuntimeConfigError("攻击元信息诊断数量超限", "response_too_large", 413)
        internal = AttackMetadataContext(result, context, prepared)
        public = project_attack_metadata(internal, candidate_id)
        selected = public.get("selected_candidate")
        selected_group = selected["projection"]["attenuation"]["group"] if public["status"] == "ready" and selected else None
        return AttackMetadataContext(public, context, {selected_group: prepared[selected_group]} if selected_group in prepared else {})


def project_attack_metadata(context, candidate_id=None):
    if not isinstance(context, AttackMetadataContext):
        raise ValueError("攻击元信息投影需要本次内部上下文")  # noqa: TRY004 - shared validation contract
    result = copy.deepcopy(context.public_projection)
    result["issues"] = [i for i in result["issues"] if i["code"] != "attack_candidate_selection_required"]
    prior = result.get("selected_candidate")
    if candidate_id is not None:
        from genshin_db.runtime_queries import _identifier
        _identifier(candidate_id, "candidate_id")
        if prior is not None and candidate_id != prior["candidate_id"]:
            raise ValueError("不能把已选择的上下文重解释为其他候选")
        found = [c for c in result["candidates"] if c["candidate_id"] == candidate_id]
        if len(found) == 1:
            selected = found[0]
            result.update(selected_candidate=copy.deepcopy(selected), status=selected["status"],
                          semantic_dependency=copy.deepcopy(selected["semantic_dependency"]), source_fingerprint=selected["source_fingerprint"])
        else:
            result["issues"].append({"code": "attack_candidate_not_found"})
    elif prior is None:
        result["issues"].append({"code": "attack_candidate_selection_required"})
    if any(i.get("blocks_selected", True) for i in result["issues"]):
        result["status"] = "pending"
    if len(result["issues"]) + sum(len(c["issues"]) for c in result["candidates"]) > ATTACK_METADATA_LIMITS["max_issues"]:
        raise RuntimeConfigError("攻击元信息最终诊断数量超限", "response_too_large", 413)
    if len(dumps(result).encode("utf8")) > ATTACK_METADATA_LIMITS["max_bytes"]:
        raise RuntimeConfigError("攻击元信息响应字节超限，未裁掉未支持分支", "response_too_large", 413)
    return result


def get_runtime_attack_metadata_profile(db_path, *, candidate_id=None, **selection):
    return project_attack_metadata(get_attack_metadata_context(db_path, candidate_id=candidate_id, **selection))
