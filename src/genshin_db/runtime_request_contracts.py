"""Single runtime request contract producer for validation and Agent discovery.

This module describes transport/input shape only. Semantic/DB validators remain
in the actual runtime handlers; no knowledge gains execution authority.
"""
from __future__ import annotations

import copy
import math

CONTRACT = "genshin-runtime-request-contracts-v1"
ACTION_FIELDS = {'action_activation_catalog': {'ascension',
                               'catalog_view', 'page', 'page_size',
                               'base_level',
                               'character_id',
                               'character_level',
                               'constellation',
                               'fact_refs',
                               'skill_depot_id'},
 'action_admission_model_candidates': {'request'},
 'action_admission_profile': {'selection', 'model_ref', 'action_type_id', 'variant'},
 'action_capacity_model_candidates': {'request'},
 'action_capacity_profile': {'selection', 'model_ref', 'context'},
 'action_capacity_source_profile': {'constellation', 'fact_refs', 'skill_depot_id', 'character_id'},
 'action_dispatch_profile': {'cast_stage',
                             'character_id',
                             'constellation',
                             'effective_level',
                             'skill_depot_id',
                             'skill_id',
                             'slot',
                             'variant'},
 'action_facts': {'character_id', 'talent_level', 'constellation', 'skill_id', 'skill_depot_id', 'slot'},
 'action_level_context': {'base_level',
                          'character_id',
                          'constellation',
                          'skill_depot_id',
                          'skill_id',
                          'slot'},
 'action_parameter': {'character_id',
                      'constellation',
                      'param_index',
                      'skill_depot_id',
                      'skill_id',
                      'slot',
                      'talent_level'},
 'action_parameter_at_level': {'base_level',
                               'character_id',
                               'constellation',
                               'param_index',
                               'skill_depot_id',
                               'skill_id',
                               'slot'},
 'action_resource_profile': {'skill_id', 'skill_depot_id', 'slot', 'character_id'},
 'assemble': {'affix_id'},
 'attack_metadata_profile': {'candidate_id',
                             'character_id',
                             'constellation',
                             'skill_depot_id',
                             'skill_id',
                             'slot',
                             'talent_level'},
 'attenuation_profile': {'group', 'channel'},
 'batch': {'max_bytes', 'items'},
 'closure': {'max_depth', 'max_bytes', 'roots', 'max_edges', 'max_nodes'},
 'definition': {'sections', 'definition_id'},
 'energy_generation_profile': {'origin_reference_id'},
 'fragment': {'max_bytes', 'relative_pointer', 'definition_id'},
 'gadget_source_profile': {'gadget_id', 'origin_reference_id'},
 'limited_hit_benefit_profile': {'character_id',
                                 'constellation',
                                 'param_index',
                                 'skill_depot_id',
                                 'skill_id',
                                 'slot',
                                 'talent_level'},
 'mechanism_catalog': {'page_size', 'target_type', 'page', 'target_id'},
 'mechanism_profile': {'revision', 'mechanism_id'},
 'passive_source_profile': {'ascension',
                            'character_id',
                            'character_level',
                            'proud_skill_id',
                            'skill_depot_id'},
 'periodic_attack_profile': {'candidate_ids',
                             'character_id',
                             'constellation',
                             'origin_reference_id',
                             'skill_depot_id',
                             'skill_id',
                             'slot',
                             'talent_level'},
 'public_root_catalog': {'max_bytes'},
 'reaction_damage_profile': {'reaction_type', 'element'},
 'roots': {'characters', 'artifact_sets', 'affix_ids', 'weapons'},
 'status': set(),
 'talent_level_profile': {'base_level',
                          'character_id',
                          'constellation',
                          'skill_depot_id',
                          'skill_id',
                          'slot'}}

REQUIRED = {'action_activation_catalog': ['character_id', 'constellation'],
 'action_admission_model_candidates': ['request'],
 'action_admission_profile': ['selection', 'variant', 'action_type_id', 'model_ref'],
 'action_capacity_model_candidates': ['request'],
 'action_capacity_profile': ['selection', 'context', 'model_ref'],
 'action_capacity_source_profile': ['character_id', 'constellation'],
 'action_dispatch_profile': ['character_id',
                             'skill_depot_id',
                             'skill_id',
                             'slot',
                             'effective_level',
                             'constellation',
                             'variant'],
 'action_facts': ['character_id'],
 'action_level_context': ['character_id'],
 'action_parameter': ['character_id', 'param_index'],
 'action_parameter_at_level': ['character_id', 'param_index'],
 'action_resource_profile': ['character_id'],
 'assemble': ['affix_id'],
 'attack_metadata_profile': ['character_id'],
 'attenuation_profile': ['group'],
 'batch': ['items'],
 'closure': ['roots'],
 'definition': ['definition_id'],
 'energy_generation_profile': ['origin_reference_id'],
 'fragment': ['definition_id'],
 'gadget_source_profile': ['gadget_id'],
 'limited_hit_benefit_profile': ['character_id', 'param_index'],
 'mechanism_profile': ['mechanism_id'],
 'passive_source_profile': ['character_id'],
 'periodic_attack_profile': ['character_id'],
 'reaction_damage_profile': ['element'],
 'talent_level_profile': ['character_id']}

# Dispatch profiles deliberately require both identities. Other action reads
# resolve by exactly one selector; keep the distinction in the public schema.
EXCLUSIVE_SKILL_SELECTORS = frozenset({
    "action_facts", "action_parameter", "action_parameter_at_level",
    "action_resource_profile", "action_level_context", "talent_level_profile",
    "attack_metadata_profile", "periodic_attack_profile", "limited_hit_benefit_profile",
})


class RuntimeRequestError(ValueError):
    reason = "runtime_request_invalid"
    code = reason

    def __init__(self, message, path, action=None, **constraints):
        super().__init__(message)
        self.details = {
            "path": path, "category": "request_error",
            "contract_ref": {"resource": "runtime_contracts", "pointer":
                "/actions/" + action if action in ACTION_FIELDS else "/actions"},
            "recovery": {"action": "correct_arguments", "retry_same_request": False},
            **constraints,
        }


def _object(fields, required=()):
    return {"type": "object", "properties": fields, "required": list(required),
            "additionalProperties": False}


def _catalog_schema(action):
    # These are the actual handler-owned constants, not another catalog list.
    from . import runtime_action_admission as admission
    from . import runtime_action_capacity as capacity
    mod = admission if action == "action_admission_model_candidates" else capacity
    integer = {"type": "integer", "minimum": 1, "maximum": 2**53 - 1}
    if mod is admission:
        observed = _object({
            "target": _object({"skill_depot_id": integer, "skill_id": integer,
                               "slot": {"const": "burst"}}),
            "domain": _object({"constellation": {"type": "integer", "minimum": 0, "maximum": 6},
                "variant": {"const": "default"}, "action_type_id": {"const": "action.elemental_burst"}}),
        })
    else:
        observed = _object({
            "target": _object({"skill_depot_id": integer, "skill_id": integer,
                               "variant": {"enum": ["default", "tap", "hold"]}}),
            "progression": _object({"character_level": {"type": "integer", "minimum": 1, "maximum": 100},
                "ascension": {"type": "integer", "minimum": 0, "maximum": 6},
                "constellation": {"type": "integer", "minimum": 0, "maximum": 6},
                "base_level": {"type": "integer", "minimum": 1, "maximum": 10}}),
            "weapon": _object({"id": integer, "level": {"type": "integer", "minimum": 1, "maximum": 100},
                               "refinement": {"type": "integer", "minimum": 1, "maximum": 5}}),
            "artifacts": {"enum": ["all_empty", "not_all_empty"]},
            "team": {"enum": ["self_only", "not_self_only"]}, "context": {"type": "object"},
        })
    fields = {"contract": {"const": mod.CATALOG_QUERY_CONTRACT},
        "version_line": {"type": "string"}, "character_id": integer,
        "observed": observed, "unknown_paths": {"type": "array", "items": {"enum": list(mod._CATALOG_PATHS)}},
        "view": {"enum": ["all", *admission._CATALOG_GROUPS] if mod is admission else list(capacity.CATALOG_VIEWS)},
        "page_size": {"type": "integer", "minimum": 1, "maximum": mod.MAX_CATALOG_PAGE_SIZE},
        "cursor": {"anyOf": [{"type": "string", "minLength": 1,
                              "maxLength": mod.MAX_CATALOG_CURSOR_BYTES}, {"type": "null"}]} }
    if mod is admission:
        fields["order"] = {"const": mod.CATALOG_ORDER}
        fields["version_line"] = {"const": "release"}
    return _object(fields, fields)


def _field_schema(name):
    if name in {"definition_id", "origin_reference_id", "candidate_id", "mechanism_id"}:
        return {"type": "string"}
    if name in {"selection", "context", "model_ref", "request"}:
        return {"type": "object"}
    if name in {"characters", "weapons", "artifact_sets", "affix_ids", "roots", "items", "sections", "candidate_ids", "fact_refs"}:
        return {"type": "array"}
    if name.endswith("_id") or name in {
        "level", "base_level", "effective_level", "talent_level", "constellation", "ascension", "revision",
        "param_index", "cast_stage", "character_level", "max_nodes", "max_edges", "max_depth", "max_bytes", "page", "page_size"}:
        return {"type": "integer"}
    return {"type": "string"}


def runtime_action_schema(action):
    if action not in ACTION_FIELDS:
        raise RuntimeRequestError("未知runtime action或请求字段", "/action", action)
    fields = {name: _field_schema(name) for name in sorted(ACTION_FIELDS[action])}
    if "slot" in fields:
        fields["slot"] = {"enum": ["normal", "skill", "burst"]}
    if action == "mechanism_catalog":
        from .runtime_mechanism import CATALOG_PAGINATION
        fields.update({
            "page": {"type": "integer", "minimum": 1,
                "maximum": CATALOG_PAGINATION["max_page"], "default": CATALOG_PAGINATION["default_page"]},
            "page_size": {"type": "integer", "minimum": 1,
                "maximum": CATALOG_PAGINATION["max_page_size"], "default": CATALOG_PAGINATION["default_page_size"]},
            "target_type": {"enum": ["character", "weapon", "artifact_set"]},
            "target_id": {"type": "integer", "minimum": 1, "maximum": 2**53 - 1},
        })
    if action == "action_activation_catalog":
        from .runtime_catalog_projection import VIEWS
        fields.update(catalog_view={"enum": list(VIEWS), "default": "all"},
                      page={"type": "integer", "minimum": 1, "default": 1},
                      page_size={"type": "integer", "minimum": 1, "maximum": 50, "default": 20})
    if action in {"action_admission_model_candidates", "action_capacity_model_candidates"}:
        fields["request"] = _catalog_schema(action)
    fields.update({"action": {"const": action}, "version_line": {"enum": ["release", "beta"]},
                   "snapshot_id": {"type": "string"}})
    schema = _object(fields, ["action", *REQUIRED.get(action, [])])
    if action in EXCLUSIVE_SKILL_SELECTORS:
        schema["oneOf"] = [{"required": ["slot"]}, {"required": ["skill_id"]}]
        schema["description"] = "slot与skill_id必须且只能提供一个；slot选择动作槽，skill_id选择精确技能。"
    return schema


def runtime_request_schema():
    """Small request envelope; exact per-action contracts are read on demand."""
    return {"type": "object", "properties": {"action": {"type": "string", "enum": sorted(ACTION_FIELDS)}},
            "required": ["action"], "additionalProperties": True,
            "description": "先按需读取runtime_contracts的/actions/<action>；GUI方法名不是runtime action。"}


def _shape(value, schema, path, action):
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                raise RuntimeRequestError("缺少runtime必填字段：" + name, path + "/" + name, action)
    if "anyOf" in schema:
        for branch in schema["anyOf"]:
            try:
                _shape(value, branch, path, action)
                return
            except RuntimeRequestError:
                pass
        raise RuntimeRequestError("字段不符合允许的类型", path, action)
    kind = schema.get("type")
    matched = {"object": type(value) is dict, "array": type(value) is list,
               "integer": type(value) is int, "string": type(value) is str,
               "null": value is None, "boolean": type(value) is bool,
               "number": type(value) is int or type(value) is float and math.isfinite(value)}
    if kind in matched and not matched[kind]:
        raise RuntimeRequestError("runtime字段类型错误", path, action)
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        raise RuntimeRequestError("runtime字段值不符合合同", path, action)
    if "enum" in schema and value not in schema["enum"]:
        raise RuntimeRequestError("runtime字段值不在允许范围", path, action)
    if type(value) in (int, float) and (
            "minimum" in schema and value < schema["minimum"] or "maximum" in schema and value > schema["maximum"]):
        bounds = {key: schema[key] for key in ("minimum", "maximum", "default") if key in schema}
        raise RuntimeRequestError(
            f"runtime数值字段越界：实际{value}，允许{schema.get('minimum', '不限')}..{schema.get('maximum', '不限')}",
            path, action, actual=value, **bounds)
    if type(value) is str and (len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", len(value))):
        raise RuntimeRequestError("runtime文本长度越界", path, action)
    if kind == "object":
        for name in schema.get("required", []):
            if name not in value:
                raise RuntimeRequestError("缺少runtime必填字段：" + name, path + "/" + name, action)
        for name, child in value.items():
            fields = schema.get("properties", {})
            if name not in fields:
                if schema.get("additionalProperties") is False:
                    raise RuntimeRequestError("未知runtime action或请求字段", path + "/" + name, action)
            else:
                _shape(child, fields[name], path + "/" + name, action)
    if kind == "array" and "items" in schema:
        for index, child in enumerate(value):
            _shape(child, schema["items"], path + "/" + str(index), action)
    if "oneOf" in schema:
        matched = 0
        for branch in schema["oneOf"]:
            try:
                _shape(value, branch, path, action)
                matched += 1
            except RuntimeRequestError:
                pass
        if matched != 1:
            fields = [key for branch in schema["oneOf"] for key in branch.get("required", [])]
            raise RuntimeRequestError("slot与skill_id必须且只能提供一个；省略另一字段", path + "/slot", action,
                                      selection_fields=fields, actual_fields=[key for key in fields if key in value])


def validate_runtime_request(request):
    if type(request) is not dict:
        raise RuntimeRequestError("runtime请求必须为JSON对象", "")
    action = request.get("action")
    if type(action) is not str or action not in ACTION_FIELDS:
        raise RuntimeRequestError("未知runtime action或请求字段", "/action")
    if any(v is None for v in request.values()):
        raise RuntimeRequestError("省略可选字段，不允许显式null", "/" + next(k for k,v in request.items() if v is None), action)
    try:
        _shape(request, runtime_action_schema(action), "", action)
        if action == "mechanism_catalog" and (("target_type" in request) != ("target_id" in request)):
            raise RuntimeRequestError("target_type与target_id必须成对提供或同时省略", "/target_id", action)
    except RuntimeRequestError as error:
        # Catalog consumers already distinguish these typed query failures.
        # Preflight adds field guidance without replacing their public error
        # family with a less specific generic request error.
        path = error.details["path"]
        if "request" in request and (path == "/request" or path.startswith("/request/")):
            if action == "action_capacity_model_candidates":
                from .runtime_capacity_delta import CapacityError

                failure = CapacityError("capacity_catalog_query", str(error))
            elif action == "action_admission_model_candidates":
                from .runtime_action_admission import ActionAdmissionError

                failure = ActionAdmissionError("action_admission_catalog_query", str(error))
            else:
                raise
            failure.reason = failure.code
            failure.details = error.details
            raise failure from None
        raise
    return copy.deepcopy(request)


def runtime_contracts():
    actions = {}
    for name in ACTION_FIELDS:
        schema = runtime_action_schema(name)
        value = {"contract_version": 1, "owner": "genshin_db", "callable_surface": "native_runtime",
            "input_schema": schema, "effect": "read_fixed_database", "execution_authority": "none",
            "semantic_validation": "实际处理器继续核实实体、来源、条件与嵌套语义；schema通过不等于资料可执行。",
            "recovery": {"action": "correct_arguments", "resource": "runtime_contracts", "pointer": "/actions/" + name}}
        if name == "action_level_context":
            value["example"] = {"action": name, "character_id": 10000043, "slot": "burst"}
        if name.endswith("_model_candidates"):
            sub = schema["properties"]["request"]
            value["example"] = {"action": name, "request": {
                "contract": sub["properties"]["contract"]["const"], "version_line": "release", "character_id": 10000043,
                "observed": {}, "unknown_paths": sorted(sub["properties"]["unknown_paths"]["items"]["enum"]),
                "view": "all", "page_size": 16, "cursor": None}}
            if "order" in sub["properties"]:
                value["example"]["request"]["order"] = sub["properties"]["order"]["const"]
        actions[name] = value
    return {"contract": CONTRACT, "actions": actions,
            "gui_aliases": {"whiteboard_action_admission_model_candidates": {
                "callable_surface": "gui_only", "native_tool": "gs_query_runtime",
                "action": "action_admission_model_candidates", "request_shape": "nested_request"},
                "whiteboard_action_capacity_model_candidates": {
                "callable_surface": "gui_only", "native_tool": "gs_query_runtime",
                "action": "action_capacity_model_candidates", "request_shape": "nested_request"}}}
