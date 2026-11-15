"""Native plan schema and structural validation share one JSON Schema definition."""
from __future__ import annotations

import copy
import json
import math
import re
import uuid

from genshin_engine.attribute_budget import (
    OPTIONS_SCHEMA,
    only_attribute_capacity_issues,
)
from genshin_engine.execution_control import ExecutionCancelled

from .action_admission_schema import CONTEXT_SCHEMA as ADMISSION_CONTEXT_SCHEMA
from .action_admission_schema import SOURCES_SCHEMA as ADMISSION_SOURCES_SCHEMA
from .action_capacity_schema import CONTEXT_SCHEMA as CAPACITY_CONTEXT_SCHEMA
from .action_capacity_schema import PRESENCE_SCHEMA, PROGRESSION_ADDITIONS
from .action_capacity_schema import SOURCES_SCHEMA as CAPACITY_SOURCES_SCHEMA
from .action_dispatch_source_schema import (
    SOURCES_SCHEMA as ACTION_DISPATCH_SOURCES_SCHEMA,
)
from .action_event_schema import FIELD_SCHEMAS
from .application_evidence import APPLICATION_RULE_AUDIT
from .application_evidence import RESEARCH_REPORT as APPLICATION_RESEARCH_REPORT
from .attack_metadata_schema import SOURCES_SCHEMA as ATTACK_METADATA_SOURCES_SCHEMA
from .attack_metadata_schema import VERIFICATION as APPLICATION_VERIFICATION
from .calculation_overrides.schema import OVERRIDES_SCHEMA
from .energy_generation_schema import SOURCES_SCHEMA as ENERGY_GENERATION_SOURCES_SCHEMA
from .external_damage_schema import SUPPLEMENTS_SCHEMA
from .limited_hit_benefit_schema import (
    SOURCES_SCHEMA as LIMITED_HIT_BENEFIT_SOURCES_SCHEMA,
)
from .mechanism_source_schema import SOURCES_SCHEMA as MECHANISM_SOURCES_SCHEMA
from .panel_provenance import SCHEMA as PANEL_PROVENANCE_SCHEMA
from .passive_sources import PASSIVE_SOURCE_SCHEMA, PROGRESSION_SCHEMA
from .periodic_schema import REFRESH_TRIGGERS_SCHEMA as PERIODIC_REFRESH_TRIGGERS_SCHEMA
from .periodic_schema import SOURCES_SCHEMA as PERIODIC_SOURCES_SCHEMA
from .provisional_entities import ENTITY_REF_SCHEMA, PROVISIONAL_ENTITIES_SCHEMA

SLOTS = ("EQUIP_BRACER", "EQUIP_NECKLACE", "EQUIP_SHOES", "EQUIP_RING", "EQUIP_DRESS")
STATS = ("atk", "hp", "def", "crit_rate", "crit_dmg", "em", "energy_recharge")
ELEMENTS = ("all", "physical", "pyro", "hydro", "electro", "cryo", "anemo", "geo", "dendro")
PANEL_STATS = (*STATS, *(f"dmg_bonus:{e}" for e in ELEMENTS))
COMPOSER_VERSION = "1.33.0"


class PlanError(ValueError):
    def __init__(self, message, reason="invalid_plan", details=None):
        super().__init__(message)
        self.reason = reason
        self.details = details if details is not None else []


def obj(properties, required=None, extra=False):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": extra}


def arr(items, minimum=0):
    return {"type": "array", "items": items, "minItems": minimum}


def enum(*values):
    return {"enum": list(values)}


def nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


TEXT = {"type": "string", "minLength": 1}
NUMBER = {"type": "number"}
POS_INT = {"type": "integer", "minimum": 1}
NONNEG_INT = {"type": "integer", "minimum": 0}
SOURCE = {"type": "object", "minProperties": 1}
SOURCES = arr(SOURCE, 1)
EXTENSIONS = {"type": "object"}
DATA_VERSION = obj({"version_line": enum("release", "beta"), "upstream_commit": TEXT,
                    "equipment_schema_version": POS_INT, "built_at": TEXT})
CONTRIBUTION = obj({"contribution_id": TEXT, "stat": enum(*PANEL_STATS),
                    "operation": enum("flat", "percent"), "value": nullable(NUMBER),
                    "source_refs": SOURCES})
BASIS = obj({"mode": enum("database", "manual"),
             **{f"character_base_{s}": nullable({"type": "number", "minimum": 0})
                for s in ("atk", "hp", "def")},
             **{s: nullable(NUMBER) for s in ("crit_rate", "crit_dmg", "energy_recharge", "em")},
             "dmg_bonus": obj({e: NUMBER for e in ELEMENTS}, []),
             "ascension_contributions": arr(CONTRIBUTION), "source_refs": SOURCES})
WEAPON = obj({"weapon_id": nullable(POS_INT), "level": nullable(POS_INT),
              "ascension": nullable(NONNEG_INT), "entity_ref": nullable(ENTITY_REF_SCHEMA),
              "refinements": arr(obj({"source_index": NONNEG_INT, "affix_group_id": POS_INT,
                                       "refinement": nullable(POS_INT)})), "selected_by": enum("agent", "user", "unknown")},
             ["weapon_id", "level", "ascension", "refinements", "selected_by"])
ARTIFACT = {"anyOf": [obj({"state": enum("empty"), "reason": TEXT}), obj({
    "state": enum("equipped"), "artifact_id": POS_INT, "level": nullable(NONNEG_INT),
    "main_prop_id": nullable(POS_INT), "substats": nullable(arr(obj({
        "prop_type": TEXT, "value": NUMBER, "source": SOURCE}))),
    "selected_by": enum("agent", "user")})]}
LEGACY_EQUIPMENT_INPUT = obj({"character_ref": nullable(obj({"db_character_id": POS_INT,
    "version_line": enum("release", "beta"), "skill_depot_id": POS_INT},
    ["db_character_id", "version_line"])), "character_entity_ref": nullable(ENTITY_REF_SCHEMA),
    "level": POS_INT, "basis": BASIS,
    "weapon": nullable(WEAPON), "artifacts": obj({s: nullable(ARTIFACT) for s in SLOTS}),
    "manual_contributions": arr(CONTRIBUTION), "data_version": nullable(DATA_VERSION)},
    ["character_ref", "level", "basis", "weapon", "artifacts",
     "manual_contributions", "data_version"])
WEAPON["properties"]["refinement"] = {"type": "integer", "minimum": 1, "maximum": 5}
PANEL_WEAPON = obj(WEAPON["properties"], ["weapon_id", "refinements", "selected_by"])
PANEL_INPUT = obj({"mode": enum("panel"), "panel_policy": enum("static_included"),
    "panel_basis_mode": enum("database", "manual"),
    "character_ref": LEGACY_EQUIPMENT_INPUT["properties"]["character_ref"],
    "character_entity_ref": nullable(ENTITY_REF_SCHEMA),
    "weapon": nullable(PANEL_WEAPON),
    "artifact_sets": arr(obj({"set_id": nullable(POS_INT), "count": {"type": "integer", "minimum": 1, "maximum": 5},
                              "entity_ref": nullable(ENTITY_REF_SCHEMA)}, ["set_id", "count"])),
    "source_refs": SOURCES, "data_version": nullable(DATA_VERSION)},
    ["mode", "panel_policy", "weapon", "artifact_sets", "source_refs", "data_version"])
PANEL_INPUT["properties"]["artifact_presence"] = copy.deepcopy(PRESENCE_SCHEMA)
EQUIPMENT_INPUT = {"anyOf": [PANEL_INPUT, LEGACY_EQUIPMENT_INPUT]}
for _equipment in (PANEL_INPUT, LEGACY_EQUIPMENT_INPUT):
    _equipment["properties"]["panel_provenance"] = copy.deepcopy(PANEL_PROVENANCE_SCHEMA)
    _equipment["properties"]["progression"] = copy.deepcopy(PROGRESSION_SCHEMA)
    _equipment["properties"]["progression"]["properties"].update(copy.deepcopy(PROGRESSION_ADDITIONS))
APPLIES_TO = {"anyOf": [obj({"mode": enum("all", "none")}),
    obj({"mode": enum("types"), "damage_type_ids": arr(TEXT, 1)}),
    obj({"mode": enum("hits"), "hit_ids": arr(TEXT, 1)})]}
DISPLAY_TEXT = {"name": {"type": "string"}, "description": {"type": "string"}}
EFFECT = obj({**DISPLAY_TEXT, "id": TEXT, "benefit_type_id": TEXT, "zone": TEXT, "stat": TEXT,
              "value": NUMBER, "applies_to": nullable(APPLIES_TO), "extensions": EXTENSIONS},
             ["id", "benefit_type_id", "zone", "stat", "value", "applies_to"])
HIT = obj({"id": TEXT, "damage_type_ids": nullable(arr(TEXT, 1)), "t": NUMBER,
    "character": TEXT, "skill": TEXT, "hit_name": {"type": "string"}, "description": {"type": "string"},
    "damage_system": enum("classic", "transformative", "lunar", "stellar"),
    "element": enum(*ELEMENTS[1:]), "multiplier": NUMBER, "scaling_stat": enum("atk", "hp", "def", "em"),
    "count": POS_INT, "cast_t": NUMBER, "snapshot": {"type": "boolean"},
    "reaction_base": {"type": "boolean"}, "reaction": obj({"type": TEXT,
        "factor": NUMBER, "layers": NONNEG_INT}, ["type"]), "extensions": EXTENSIONS},
    ["id", "damage_type_ids", "t", "character", "skill", "damage_system", "element"])
BUFF = obj({**DISPLAY_TEXT, "id": TEXT, "source": {"type": "string"}, "start": NUMBER, "end": NUMBER,
    "scope": enum("team", "frontline"), "target": nullable(TEXT), "effects": arr(EFFECT, 1),
    "extensions": EXTENSIONS}, ["id", "start", "end", "effects"])
TEMPLATE = obj({"version": enum(2), "form": enum("resolved"), "id_registry_version": enum(1),
    "type_definitions": obj({"damage_types": arr(obj({"id": TEXT, "label": TEXT,
        "description": TEXT, "source_refs": SOURCES}))}),
    "meta": obj({"assumptions": arr(TEXT)}, [], True), "enemy": obj({"level": POS_INT,
        "resistance": {"type": "object", "additionalProperties": NUMBER},
        "defense_reduction": NUMBER, "defense_ignore": NUMBER}, ["level"]),
    "characters": arr(obj({**DISPLAY_TEXT, "id": TEXT, "level": POS_INT,
        "stats": obj({**{s: {"type": "number", "minimum": 0} for s in (*STATS, "base_atk", "base_hp", "base_def")},
                      "dmg_bonus": obj({e: NUMBER for e in ELEMENTS}, [])}, []),
        "extensions": EXTENSIONS}, ["id", "level", "stats"])),
    "switches": arr(obj({"t": NUMBER, "character": TEXT})), "buffs": arr(BUFF), "hits": arr(HIT),
    "loop_duration": NUMBER, "extensions": EXTENSIONS},
    ["version", "form", "id_registry_version", "type_definitions", "enemy", "characters", "hits"])
NATIVE_TEMPLATE = copy.deepcopy(TEMPLATE)
NATIVE_TEMPLATE["properties"]["hits"]["items"]["required"].remove("element")
NATIVE_TEMPLATE["properties"]["hits"]["items"]["x-element-ownership"] = "只有attack_metadata_sources唯一接管的原型可省略element；运行时严格核对"
# Runtime contribution ranking annotates its own resolved hits.  This is an
# execution-output field, not another author-controlled damage multiplier.
# Keep HIT/NATIVE_TEMPLATE (including explicit_hit rules) unchanged.
EXECUTED_TEMPLATE = copy.deepcopy(TEMPLATE)
EXECUTED_TEMPLATE["properties"]["hits"]["items"]["properties"]["result_weight"] = {
    "type": "number", "exclusiveMinimum": 0, "maximum": 1,
}
RULE = {"anyOf": [obj({"kind": enum("constant"), "value": NUMBER}), obj({
    "kind": enum("stat_scale"), "from": obj({"character_id": TEXT,
        "stage": enum("base", "static"), "stat": enum(*STATS)}),
    "factor": NUMBER, "offset": NUMBER, "min": NUMBER, "max": NUMBER}, ["kind", "from", "factor"]),
    obj({"kind": enum("explicit_hit"), "hit": HIT}),
    obj({"kind": enum("mechanics"), "rule_id": TEXT}),
    obj({"kind": enum("fixed_effect_source"), "fixed_source_id": TEXT})]}
DELIVERY = {"anyOf": [obj({"kind": enum("panel"), "target_character_id": TEXT,
    "stat": enum(*PANEL_STATS), "operation": enum("flat", "percent")}), obj({"kind": enum("hit")}),
    obj({"kind": enum("mechanics")}), obj({"kind": enum("fixed_effect_source")}),
    obj({"kind": enum("buff"), "start": NUMBER, "end": NUMBER, "scope": enum("team", "frontline"),
        "target": nullable(TEXT), "effect": obj({k: v for k, v in EFFECT["properties"].items()
                                                if k != "value"},
                                                ["id", "benefit_type_id", "zone", "stat", "applies_to"]),
        "source": {"type": "string"}},
        ["kind", "start", "end", "scope", "target", "effect"])]}
CONDITION = obj({"condition_id": TEXT, "description": TEXT, "state": enum("confirmed", "pending"),
    "scope": enum("always", "intervals"), "intervals": arr(obj({"start": NUMBER, "end": NUMBER})),
    "evidence": SOURCES})
PART = obj({"part_id": TEXT, "label": TEXT, "state": enum("resolved", "pending", "excluded"),
    "rule": nullable(RULE), "delivery": nullable(DELIVERY), "conditions": arr(CONDITION),
    "dependencies": obj({"mode": enum("source_only", "stat_rule", "manual_context"),
                         "context_fingerprint": nullable(TEXT), "note": TEXT}),
    "reason": nullable(TEXT), "extensions": EXTENSIONS},
    ["part_id", "label", "state", "rule", "delivery", "conditions", "dependencies", "reason"])
OWNER = {"anyOf": [
    obj({"kind": enum("weapon"), "weapon_id": POS_INT, "source_index": NONNEG_INT, "affix_id": POS_INT}),
    obj({"kind": enum("artifact_set"), "set_id": POS_INT, "required_count": POS_INT, "affix_id": nullable(POS_INT)}),
    obj({"kind": enum("character_skill"), "character_id": POS_INT, "version_line": enum("release", "beta"),
         "skill_id": POS_INT, "talent_id": POS_INT, "proud_skill_group_id": POS_INT},
        ["kind", "character_id", "version_line"]),
    obj({"kind": enum("manual"), "note": TEXT})]}
BINDING = obj({"binding_id": TEXT, "provider_character_id": TEXT, "owner": OWNER,
    "source_refs": SOURCES, "source_fingerprint": nullable(TEXT), "coverage": obj({
        "state": enum("unreviewed", "complete", "projection"), "reviewed_by": nullable(TEXT),
        "source_fingerprint": nullable(TEXT), "note": nullable(TEXT)}), "parts": arr(PART),
    "extensions": EXTENSIONS},
    ["binding_id", "provider_character_id", "owner", "source_refs", "source_fingerprint", "coverage", "parts"])
# The compiler owns scenario semantics. This native envelope remains versioned and
# does not accept client supplied compiled rules or coverage certificates.
BINDING["properties"]["upstream_recipe"] = obj({
    "schema_version": enum(1), "scenario": {"type": "object"},
    "ability_refs": arr(obj({"ability_id": TEXT, "skill_id": POS_INT})),
}, ["schema_version", "scenario"])
MATCH_COMMON = {"id": TEXT, "effect_ref": {"anyOf": [obj({"buff_id": TEXT, "effect_id": TEXT}),
    obj({"fixed_source_id": TEXT, "effect_id": TEXT})]},
    "reason": TEXT, "source_refs": SOURCES, "state": enum("confirmed", "pending"),
    "basis_fingerprint": nullable(TEXT)}
from genshin_engine.runtime_event_match import RUNTIME_EVENT_REF_SCHEMA

MATCH = {"anyOf": [obj({**MATCH_COMMON, "mode": enum("runtime_adjust"),
                        "runtime_events": arr(obj({"event_ref": RUNTIME_EVENT_REF_SCHEMA, "operation": enum("include", "exclude")}))}),
                    obj({**MATCH_COMMON, "mode": enum("adjust"),
                        "include_hit_ids": arr(TEXT), "exclude_hit_ids": arr(TEXT)}),
                    obj({**MATCH_COMMON, "mode": enum("replace"), "hit_ids": arr(TEXT)})]}
MANUAL_DEPENDENCY = obj({"effect_ref": obj({"buff_id": TEXT, "effect_id": TEXT}),
    "mode": enum("unreviewed", "source_only", "manual_context"),
    "context_fingerprint": nullable(TEXT), "note": {"type": "string"}})
ELEMENTAL_SOURCE = obj({"profile_id": TEXT, "version_line": enum("release", "beta"),
    "rule_group": TEXT, "counter_tag": TEXT, "attenuation_group": TEXT, "shared_group": TEXT,
    "verification": APPLICATION_VERIFICATION, "research_report": APPLICATION_RESEARCH_REPORT, "source": TEXT,
    "application_rule_audit": APPLICATION_RULE_AUDIT,
    "application_mode": enum("sequence", "no_icd", "no_application", "pending", "unknown"),
    "expected_snapshot_id": nullable(TEXT), "expected_node_sha256": nullable(TEXT)},
    ["profile_id", "version_line", "source", "expected_snapshot_id", "expected_node_sha256"])
REACTION_DAMAGE_SOURCE = obj({"reaction_type": enum("swirl"), "version_line": enum("release", "beta"),
    "source": TEXT, "expected_snapshot_id": nullable(TEXT), "expected_profile_fingerprint": nullable(TEXT)})
LEGACY_HIT_PARAMETER_SOURCE = obj({"hit_id": TEXT, "version_line": enum("release", "beta"),
    "selection": obj({"character_id": POS_INT, "slot": enum("normal", "skill", "burst"), "skill_id": POS_INT,
        "skill_depot_id": POS_INT, "constellation": {"type": "integer", "minimum": 0, "maximum": 6},
        "talent_level": {"type": "integer", "minimum": 1, "maximum": 15}}, ["character_id", "constellation"]),
    "param_index": {"type": "integer", "minimum": 0, "maximum": 63},
    "scaling_stat": enum("atk", "hp", "def", "em"), "purpose": enum("single_stat_damage_multiplier"),
    "status": enum("confirmed", "assumed", "pending"), "source": TEXT,
    "expected_snapshot_id": nullable(TEXT), "expected_node_sha256": nullable(TEXT)})
LEVEL_HIT_PARAMETER_SOURCE = copy.deepcopy(LEGACY_HIT_PARAMETER_SOURCE)
LEVEL_HIT_PARAMETER_SOURCE['properties'].update(parameter_contract=enum('level-bound-parameter-v1'),
    expected_level_fingerprint=nullable(TEXT))
LEVEL_HIT_PARAMETER_SOURCE['required'] += ['parameter_contract','expected_level_fingerprint']
LEVEL_HIT_PARAMETER_SOURCE['properties']['selection']['properties'].pop('talent_level')
LEVEL_HIT_PARAMETER_SOURCE['properties']['selection']['properties']['base_level'] = {'type':'integer','minimum':1,'maximum':10}
HIT_PARAMETER_SOURCE = {'anyOf':[LEGACY_HIT_PARAMETER_SOURCE, LEVEL_HIT_PARAMETER_SOURCE]}
ACTION_RESOURCE_SOURCE = obj({"ability_id": TEXT, "version_line": enum("release", "beta"),
    "selection": obj({"character_id": POS_INT, "slot": enum("normal", "skill", "burst"), "skill_id": POS_INT,
        "skill_depot_id": POS_INT, "constellation": {"type": "integer", "minimum": 0, "maximum": 6}},
        ["character_id", "constellation"]),
    "purpose": enum("ordinary_fixed_burst_base_resources", "unresolved_resource_mode"),
    "variant": TEXT, "status": enum("confirmed", "assumed", "pending"), "source": TEXT,
    "expected_snapshot_id": nullable(TEXT), "expected_resource_fingerprint": nullable(TEXT),
    "expected_scope_fingerprint": nullable(TEXT)})
ACTION_RESOURCE_SOURCE["properties"]["energy_delivery"] = enum("existing_events", "action_start")
PLAN_SCHEMA = {"$schema": "https://json-schema.org/draft/2020-12/schema", **obj({
    'direct_energy_policy': {'const': 'energy-direct-base-er-v1'},
    "calculation_options": OPTIONS_SCHEMA,
    "kind": enum("genshin-plan"), "plan_version": enum(1, 2, 3), "plan_id": TEXT, "revision": NONNEG_INT,
    "manual_template": NATIVE_TEMPLATE, "equipment_inputs": {"type": "object", "additionalProperties": EQUIPMENT_INPUT},
    "effect_bindings": arr(BINDING), "rotation_tuning": obj({"buff_hit_matches": arr(MATCH)}),
    "manual_effect_dependencies": arr(MANUAL_DEPENDENCY),
    "rotation_model": nullable({"type": "object"}),
    "mechanics_model": nullable({"type": "object"}),
    "attribute_review": obj({"mode": enum("unreviewed", "manual_context"),
                             "context_fingerprint": nullable(TEXT), "note": TEXT}),
    "elemental_sources": arr(ELEMENTAL_SOURCE),
    "reaction_damage_sources": arr(REACTION_DAMAGE_SOURCE),
    "hit_parameter_sources": arr(HIT_PARAMETER_SOURCE),
    "action_resource_sources": {**arr(ACTION_RESOURCE_SOURCE), "maxItems": 64},
    "action_dispatch_sources": ACTION_DISPATCH_SOURCES_SCHEMA,
    "action_admission_sources": ADMISSION_SOURCES_SCHEMA,
    "action_admission_context": nullable(ADMISSION_CONTEXT_SCHEMA),
    "action_capacity_sources": CAPACITY_SOURCES_SCHEMA,
    "action_capacity_context": CAPACITY_CONTEXT_SCHEMA,
    "attack_metadata_sources": ATTACK_METADATA_SOURCES_SCHEMA,
    "periodic_attack_sources": PERIODIC_SOURCES_SCHEMA,
    "periodic_refresh_triggers": PERIODIC_REFRESH_TRIGGERS_SCHEMA,
    "passive_sources": {**arr(PASSIVE_SOURCE_SCHEMA), "maxItems": 32},
    "energy_generation_sources": ENERGY_GENERATION_SOURCES_SCHEMA,
    "limited_hit_benefit_sources": LIMITED_HIT_BENEFIT_SOURCES_SCHEMA,
    "mechanism_sources": MECHANISM_SOURCES_SCHEMA,
    "provisional_entities": PROVISIONAL_ENTITIES_SCHEMA,
    "external_damage_supplements": SUPPLEMENTS_SCHEMA,
    "calculation_event_overrides": OVERRIDES_SCHEMA,
    **FIELD_SCHEMAS,
    "resolution": {"type": "object"}, "resolved_template": nullable(EXECUTED_TEMPLATE), "extensions": EXTENSIONS},
    ["kind", "plan_version", "plan_id", "revision", "manual_template", "equipment_inputs", "effect_bindings",
     "resolution", "resolved_template"])}


def _check(value, schema, path, errors):
    valid = {"object": lambda: isinstance(value, dict), "array": lambda: isinstance(value, list),
        "number": lambda: isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
        "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
        "string": lambda: isinstance(value, str), "null": lambda: value is None,
        "boolean": lambda: isinstance(value, bool)}
    if "anyOf" in schema:
        attempts = []
        for option in schema["anyOf"]:
            attempt = []
            _check(value, option, path, attempt)
            if not attempt:
                return
            kind = option.get("type")
            # A malformed object is still an object.  Selecting a shorter
            # nullable/type-mismatch error hides the actual field to repair.
            type_rank = 1 if kind is None else 0 if valid[kind]() else 2
            attempts.append((type_rank, attempt))
        errors.extend(min(attempts, key=lambda item: (item[0], len(item[1])))[1])
        return
    kind = schema.get("type")
    if kind and not valid[kind]():
        errors.append({"path": path, "message": f"需要 {kind}"})
        return
    if "enum" in schema and (value not in schema["enum"] or
                              isinstance(value, bool) and not any(isinstance(v, bool) for v in schema["enum"])):
        errors.append({"path": path, "message": f"允许值 {schema['enum']}"})
    if (isinstance(value, (int, float)) and not isinstance(value, bool)
            and value < schema.get("minimum", -math.inf)):
        errors.append({"path": path, "message": f"数值低于允许范围：实际{value}，最小{schema['minimum']}",
                       "actual": value, **{key: schema[key] for key in ("minimum", "maximum", "default") if key in schema},
                       **({"repair_hint": schema["description"]} if "description" in schema else {})})
    if (isinstance(value, (int, float)) and not isinstance(value, bool)
            and value > schema.get("maximum", math.inf)):
        errors.append({"path": path, "message": f"数值高于允许范围：实际{value}，最大{schema['maximum']}",
                       "actual": value, **{key: schema[key] for key in ("minimum", "maximum", "default") if key in schema},
                       **({"repair_hint": schema["description"]} if "description" in schema else {})})
    if (isinstance(value, (int, float)) and not isinstance(value, bool)
            and "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]):
        errors.append({"path": path, "message": f"数值必须大于 {schema['exclusiveMinimum']}",
                       "actual": value, "exclusiveMinimum": schema["exclusiveMinimum"]})
    if isinstance(value, str):
        if len(value.strip()) < schema.get("minLength", 0):
            errors.append({"path": path, "message": "需要非空文本"})
        if len(value) > schema.get("maxLength", math.inf):
            errors.append({"path": path, "message": "文本超过允许长度"})
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            errors.append({"path": path, "message": "文本格式不符"})
    if isinstance(value, dict):
        if len(value) < schema.get("minProperties", 0):
            errors.append({"path": path, "message": "需要来源依据"})
        for key in schema.get("required", []):
            if key not in value:
                errors.append({"path": f"{path}.{key}", "message": "缺少必需字段"})
        for key, item in value.items():
            sub = schema.get("properties", {}).get(key, schema.get("additionalProperties", True))
            if sub is False:
                errors.append({"path": f"{path}.{key}", "message": "未知字段；展示扩展请放 extensions"})
            elif isinstance(sub, dict):
                _check(item, sub, f"{path}.{key}", errors)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append({"path": path, "message": "列表不能为空"})
        if len(value) > schema.get("maxItems", math.inf):
            errors.append({"path": path, "message": "列表超过允许条目数"})
        for i, item in enumerate(value):
            _check(item, schema.get("items", {}), f"{path}[{i}]", errors)


def _unique(items, key, path):
    ids = [item[key] for item in items]
    if len(set(ids)) != len(ids):
        raise PlanError(f"{path}: {key} 重复")


def _validate_loadout_entity_refs(plan):
    """Keep legacy database IDs and new discriminated refs mutually honest."""

    from .provisional_entities import validate_entity_ref, validate_registry

    registry = {
        (row["entity_type"], row["provisional_id"])
        for row in validate_registry(plan)
    }
    provisional_used = False

    def checked_ref(ref, expected_type, path, legacy_id=None, line=None):
        nonlocal provisional_used
        if ref is None:
            if legacy_id is None:
                raise PlanError("公共实体既没有数据库身份，也没有临时实体引用",
                                "entity_ref_required", {"path": path})
            return
        try:
            checked = validate_entity_ref(ref, expected_type=expected_type)
        except Exception as exc:
            raise PlanError(str(exc), getattr(exc, "reason", "entity_ref_invalid"),
                            getattr(exc, "details", {"path": path})) from exc
        if checked["kind"] == "database":
            if legacy_id != checked["database_id"]:
                raise PlanError("数据库实体引用与兼容ID不一致", "entity_ref_database_mismatch",
                                {"path": path})
            if line is not None and checked["version_line"] != line:
                raise PlanError("数据库实体引用资料线与配装不一致", "entity_ref_version_mismatch",
                                {"path": path})
            return
        provisional_used = True
        if legacy_id is not None:
            raise PlanError("临时实体不得占用或伪造数据库ID", "entity_ref_fake_database_id",
                            {"path": path})
        identity = (expected_type, checked["provisional_id"])
        if identity not in registry:
            raise PlanError("临时实体引用不属于当前方案登记表", "entity_ref_registry_missing",
                            {"path": path})

    default_line = plan.get("manual_template", {}).get("meta", {}).get(
        "version_line", "release")
    for character_id, equipment in plan.get("equipment_inputs", {}).items():
        ref = equipment.get("character_ref")
        line = (ref or equipment.get("data_version") or {}).get(
            "version_line", default_line)
        if equipment.get("character_entity_ref") is not None:
            checked_ref(
                equipment.get("character_entity_ref"), "character",
                f"equipment_inputs.{character_id}.character_entity_ref",
                (ref or {}).get("db_character_id"), line,
            )
        # A user-supplied finished panel may intentionally have no public
        # character identity at all.  ``entity_ref`` governs database and D2
        # public entities; it must not turn a private/manual panel into a
        # provisional public entity or force it through the D2 authority
        # chain.  Once either public identity field is present, the checks
        # above remain fail-closed.
        weapon = equipment.get("weapon")
        if weapon is not None:
            checked_ref(
                weapon.get("entity_ref"), "weapon",
                f"equipment_inputs.{character_id}.weapon.entity_ref",
                weapon.get("weapon_id"), line,
            )
        seen_sets = set()
        for index, selected in enumerate(equipment.get("artifact_sets", [])):
            checked_ref(
                selected.get("entity_ref"), "artifact_set",
                f"equipment_inputs.{character_id}.artifact_sets.{index}.entity_ref",
                selected.get("set_id"), line,
            )
            identity = (selected.get("set_id"), json.dumps(
                selected.get("entity_ref"), sort_keys=True, ensure_ascii=False))
            if identity in seen_sets:
                raise PlanError("套装实体引用重复", "entity_ref_duplicate", {
                    "path": f"equipment_inputs.{character_id}.artifact_sets.{index}"
                })
            seen_sets.add(identity)
    if provisional_used and plan.get("plan_version") != 3:
        raise PlanError("当前方案临时实体配装需要原生方案版本3",
                        "provisional_entity_plan_version")


def _json_guard(value, path="plan", parents=None):
    """Bound every JSON branch, including open metadata, before copying or hashing it."""
    parents = set() if parents is None else parents
    if isinstance(value, (dict, list)):
        if id(value) in parents or len(parents) >= 100:
            raise PlanError(f"{path}: JSON 循环引用或嵌套深度超过 100")
        parents.add(id(value))
        children = value.items() if isinstance(value, dict) else enumerate(value)
        for key, child in children:
            if isinstance(value, dict) and not isinstance(key, str):
                raise PlanError(f"{path}: JSON 对象键必须为字符串")
            _json_guard(child, f"{path}.{key}", parents)
        parents.remove(id(value))
    elif isinstance(value, bool) or value is None or isinstance(value, str):
        return
    elif isinstance(value, (int, float)):
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise PlanError(f"{path}: 数值必须有限，不能溢出")
    else:
        raise PlanError(f"{path}: 必须为 JSON 兼容值")


def validate_executed_plan(plan):
    """Validate an internal expanded plan, without granting result authority.

    Author game inputs retain the native 8 MiB budget. Generated caches use
    the existing calculated-artifact budget; the complete calculated package
    still has its own independent check in the delivery reader.
    """
    return validate_plan(plan, _executed=True)


def validate_plan(plan, *, _executed=False):
    try:
        # Reject circular, non-JSON, excessive depth and non-finite values even in extension metadata.
        _json_guard(plan)
        source = plan
        if _executed and isinstance(plan, dict):
            from .files import input_view
            source = input_view(plan)
        serialized = json.dumps(source, allow_nan=False, ensure_ascii=False).encode("utf-8")
        if len(serialized) > 8 * 1024 * 1024:
            raise ValueError("方案超过 8 MiB")
        if _executed:
            from genshin_app.calculation_delivery_files import LIMITS
            if len(json.dumps(plan, allow_nan=False, ensure_ascii=False).encode("utf-8")) > LIMITS["calculated_bytes"]:
                maximum = LIMITS["calculated_bytes"] // (1024 * 1024)
                raise ValueError(f"执行方案超过 {maximum} MiB；完整计算包仍须另行通过总容量校验")
        if isinstance(plan, dict) and plan.get("plan_version") not in (1, 2, 3):
            raise PlanError("不支持的原生方案版本", "unsupported_plan_version")
        # All shape checks precede consumers which dereference nested records.
        # PLAN_SCHEMA deliberately keeps the two engine models as open objects;
        # use their authoritative schemas here, not an App-side approximation.
        errors = []
        _check(plan, PLAN_SCHEMA, "plan", errors)
        # Preserve these standalone aggregate contracts' stable diagnostics
        # after the outer shape pass, before surfacing a generic schema error.
        if isinstance(plan, dict) and "external_damage_supplements" in plan:
            from .external_damage_schema import validate_items
            validate_items(plan["external_damage_supplements"])
        if isinstance(plan, dict) and "calculation_event_overrides" in plan:
            from .calculation_overrides.schema import validate_declarations
            validate_declarations(
                plan["calculation_event_overrides"], plan_version=plan.get("plan_version")
            )
        if errors:
            raise PlanError("方案结构不合法", details=errors)
        from genshin_engine.rotation import validate_rotation_model
        if plan.get("rotation_model") is not None:
            validation = validate_rotation_model(plan["rotation_model"])
            if validation["status"] == "invalid":
                raise PlanError("动作与能量模型结构不合法", details=validation["issues"])
        if plan.get("mechanics_model") is not None:
            from genshin_engine.mechanics_schema import MECHANICS_SCHEMA
            from genshin_engine.rotation_port import collect_schema_errors
            issues = []
            from genshin_engine.attribute_contract import attribute_model_schema
            author_schema = copy.deepcopy(MECHANICS_SCHEMA)
            from genshin_engine.attribute_budget import validate_calculation_options
            attribute_limits = validate_calculation_options(plan.get('calculation_options', {'schema_version':1}))['effective']
            author_schema['properties']['attribute_model'] = attribute_model_schema(author=True, limits=attribute_limits)
            if 'execution_options' in plan['mechanics_model']:
                raise PlanError('原生方案的执行配置请放在calculation_options，不可重复交付', details=[{
                    'path':'mechanics_model.execution_options','message':'请使用calculation_options'}])
            collect_schema_errors(plan["mechanics_model"], author_schema,
                                  "mechanics_model", issues)
            if issues:
                reason = 'attribute_declaration_limit' if only_attribute_capacity_issues(issues) else 'invalid_plan'
                message = '属性声明超出本次计算预算，请调整预算后重新计算' if reason=='attribute_declaration_limit' else '机制模型结构不合法'
                raise PlanError(message, reason, details=issues)
        extensions = plan.get("extensions") if isinstance(plan, dict) else None
        if isinstance(extensions, dict) and "agent_execution_materialization" in extensions:
            if plan.get("plan_version") != 3:
                raise PlanError("Agent执行材料化需要原生方案版本3",
                                "agent_materialization_plan_version")
            if not isinstance(extensions.get("event_topology"), dict):
                raise PlanError("Agent执行材料化必须绑定当前事件拓扑",
                                "agent_materialization_topology_required")
            from .agent_execution_materialization import (
                validate_agent_execution_materialization,
            )
            validate_agent_execution_materialization(
                extensions["agent_execution_materialization"]
            )
        from .action_resources import validate_action_resource_bindings
        validate_action_resource_bindings(plan)
        from .action_admission_schema import validate_action_admission_bindings
        validate_action_admission_bindings(plan)
        from .action_capacity_schema import (
            validate_capacity_bindings,
            validate_capacity_equipment,
        )
        validate_capacity_bindings(plan)
        for gear in plan["equipment_inputs"].values():
            validate_capacity_equipment(gear)
        from .action_dispatch_source_schema import validate_action_dispatch_sources
        from .action_event_schema import validate_action_event_fields
        validate_action_dispatch_sources(plan)
        validate_action_event_fields(plan)
        from .attack_metadata_schema import validate_attack_metadata_sources
        from .periodic_schema import validate_periodic_sources
        periodic_owned = validate_periodic_sources(plan)
        validate_attack_metadata_sources(plan, additional_owned_hits=periodic_owned)
        from .passive_sources import validate_passive_sources
        validate_passive_sources(plan)
        from .energy_generation_schema import validate_energy_generation_sources
        validate_energy_generation_sources(plan)
        from .limited_hit_benefit_schema import validate_limited_hit_benefit_sources
        validate_limited_hit_benefit_sources(plan)
        from .mechanism_source_schema import validate_mechanism_sources
        validate_mechanism_sources(plan)
        if "provisional_entities" in plan:
            from .provisional_entities import validate_registry
            validate_registry(plan)
        _validate_loadout_entity_refs(plan)
        if plan["plan_version"] == 1 and "rotation_model" in plan:
            raise PlanError("动作与能量模型需要原生方案版本 2，旧版本不允许忽略该字段")
        if plan["plan_version"] >= 2:
            if "rotation_model" not in plan:
                raise PlanError("原生方案版本 2 必须明确 rotation_model（null 表示未检查）")
            if plan["rotation_model"] is not None:
                from genshin_engine.rotation import validate_rotation_model
                validation = validate_rotation_model(plan["rotation_model"])
                if validation["status"] == "invalid":
                    raise PlanError("动作与能量模型结构不合法", details=validation["issues"])
        if plan["plan_version"] < 3 and "mechanics_model" in plan:
            raise PlanError("声明式机制模型需要原生方案版本3，旧版本不得忽略新字段")
        if "reaction_damage_sources" in plan:
            if plan["plan_version"] != 3:
                raise PlanError("数据库反应伤害来源需要原生方案版本3")
            refs = plan["reaction_damage_sources"]
            if len(refs) > 1:
                raise PlanError("当前普通扩散只接受一项全局伤害来源，不按动作重开计数")
            if any(not item["source"].strip() for item in refs):
                raise PlanError("扩散伤害来源的适用依据不能为空白")
        if "hit_parameter_sources" in plan:
            if plan["plan_version"] != 3:
                raise PlanError("技能倍率来源需要原生方案版本3")
            refs = plan["hit_parameter_sources"]
            if len(refs) > 256 or len({b["hit_id"] for b in refs}) != len(refs):
                raise PlanError("命中倍率来源不能重复且最多256项")
            for binding in refs:
                selection = binding["selection"]
                if ("slot" in selection) == ("skill_id" in selection):
                    raise PlanError("技能倍率来源须明确slot或skill_id，且只能选择一种")
        if "elemental_sources" in plan:
            if plan["plan_version"] != 3:
                raise PlanError("数据库附着规则来源需要原生方案版本3")
            ids = [s["profile_id"] for s in plan["elemental_sources"]]
            if len(ids) > 256 or len(set(ids)) != len(ids):
                raise PlanError("附着规则来源的档案ID不能重复且最多256条")
            for source in plan["elemental_sources"]:
                if any(not source[key].strip() for key in ("profile_id", "source")):
                    raise PlanError("附着来源档案及其依据不能为空")
                raw_mode = source.get("application_mode")
                mode = "pending" if raw_mode in (None, "unknown") else raw_mode
                if source.get("rule_group") and source.get("attenuation_group"):
                    raise PlanError("附着规则组不得同时使用rule_group与旧attenuation_group字段")
                if source.get("counter_tag") and source.get("shared_group"):
                    raise PlanError("附着计数标签不得同时使用counter_tag与旧shared_group字段")
                rule_group = source.get("rule_group") or source.get("attenuation_group")
                counter_tag = source.get("counter_tag") or source.get("shared_group")
                if mode == "sequence" and any(not (value or "").strip()
                                               for value in (rule_group, counter_tag)):
                    raise PlanError("序列附着模式必须声明附着规则组和附着计数标签（ICD Tag）")
                if mode != "sequence" and raw_mode is not None and (rule_group or counter_tag):
                    raise PlanError("非序列附着模式不得同时声明规则组或计数标签")
                verification = source.get("verification")
                if verification and len(set(verification["confirmed_fields"])) != len(verification["confirmed_fields"]):
                    raise PlanError("逐段证据confirmed_fields不得重复")
                rule_audit = source.get("application_rule_audit")
                if rule_audit is not None:
                    if mode != "sequence" or rule_audit.get("effective_icd_rule") is None:
                        raise PlanError("局部ICD规则审计只能投影到sequence附着来源")
                    if rule_audit["base_icd_rule"]["rule_group"] != rule_group:
                        raise PlanError("局部ICD规则审计的数据库基线与附着来源不一致")
                    owners = []
                    profiles = {row["id"]: row for row in plan.get("action_event_profiles", [])}
                    for attack in plan.get("attack_metadata_sources", []):
                        profile = profiles.get(attack.get("action_event_profile_id")) or {}
                        if (profile.get("application_profile_id") == source["profile_id"]
                                and attack.get("application_rule_audit") == rule_audit):
                            owners.append(("attack", attack.get("action_event_profile_id")))
                    for periodic in plan.get("periodic_attack_sources", []):
                        profile = profiles.get(periodic.get("action_event_profile_id")) or {}
                        channels = {row.get("channel_id"): row for row in profile.get("channels", [])}
                        for mapping in periodic.get("channels", []):
                            channel = channels.get(mapping.get("channel_id")) or {}
                            if (channel.get("application_profile_id") == source["profile_id"]
                                    and mapping.get("application_rule_audit") == rule_audit):
                                owners.append(("periodic", mapping.get("channel_id")))
                    if len(owners) != 1:
                        raise PlanError("局部ICD规则审计必须来自唯一动作派发组件")
        if plan["plan_version"] == 3:
            if "mechanics_model" not in plan:
                raise PlanError("原生方案版本3必须明确mechanics_model（null为待补草稿）")
            if plan["mechanics_model"] is not None:
                from genshin_engine.mechanics import validate_mechanics_model
                validation = validate_mechanics_model(plan["mechanics_model"], attribute_author=True,
                    attribute_limits=validate_calculation_options(plan.get('calculation_options', {'schema_version':1}))['effective'])
                if validation["status"] == "invalid":
                    reason = ('attribute_declaration_limit' if only_attribute_capacity_issues(validation['issues'])
                              else 'invalid_plan')
                    message = '属性声明超出本次计算预算，请调整预算后重新计算' if reason=='attribute_declaration_limit' else '机制模型结构不合法'
                    raise PlanError(message, reason, details=validation["issues"])
        uuid.UUID(plan["plan_id"])
    except (ExecutionCancelled, PlanError):
        raise
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise PlanError(str(exc)) from exc
    template = plan["manual_template"]
    chars = {c["id"] for c in template["characters"]}
    for collection in ("characters", "hits", "buffs"):
        _unique(template.get(collection, []), "id", f"manual_template.{collection}")
    for buff in template.get("buffs", []):
        _unique(buff["effects"], "id", f"buff.{buff['id']}.effects")
    _unique(plan["effect_bindings"], "binding_id", "effect_bindings")
    for cid, equipment in plan["equipment_inputs"].items():
        if cid not in chars:
            raise PlanError(f"equipment_inputs.{cid}: 未知方案角色")
        if equipment["weapon"]:
            _unique(equipment["weapon"]["refinements"], "source_index", f"equipment_inputs.{cid}.weapon")
        if equipment.get("mode") == "panel":
            # Database IDs are nullable for current-plan provisional sets.
            # _validate_loadout_entity_refs already enforces uniqueness on the
            # full discriminated identity without conflating distinct null IDs.
            if sum(item["count"] for item in equipment["artifact_sets"]) > len(SLOTS):
                raise PlanError(f"equipment_inputs.{cid}.artifact_sets: 套装总件数不能超过五件")
            continue
        all_contributions = equipment["basis"]["ascension_contributions"] + equipment["manual_contributions"]
        _unique(all_contributions, "contribution_id", f"equipment_inputs.{cid}.contributions")
        for contribution in all_contributions:
            if contribution["operation"] == "percent" and contribution["stat"] not in ("atk", "hp", "def"):
                raise PlanError("percent 贡献仅允许 atk/hp/def")
        for slot, item in equipment["artifacts"].items():
            if item and item["state"] == "equipped" and item["substats"] is not None:
                _unique(item["substats"], "prop_type", f"equipment_inputs.{cid}.{slot}.substats")
    for binding in plan["effect_bindings"]:
        if binding["provider_character_id"] not in chars:
            raise PlanError("效果提供者引用未知角色")
        if "upstream_recipe" in binding:
            if plan["plan_version"] != 3:
                raise PlanError("上游自动来源需要原生方案版本3，旧版不允许忽略配方")
            if binding["owner"]["kind"] not in ("weapon", "artifact_set"):
                raise PlanError("当前自动来源仅支持实际装备效果")
            if binding["parts"]:
                raise PlanError("自动来源的parts必须为空；生成分项仅在计算报告中查看")
            _unique(binding["upstream_recipe"].get("ability_refs", []), "ability_id", "upstream_recipe.ability_refs")
        _unique(binding["parts"], "part_id", f"binding.{binding['binding_id']}")
        for part in binding["parts"]:
            if part["state"] == "excluded" and not part["reason"]:
                raise PlanError("excluded 部件必须有 reason")
            if part["state"] == "resolved" and (part["rule"] is None or part["delivery"] is None):
                raise PlanError("resolved 部件必须提供 rule/delivery")
            rule, delivery = part["rule"], part["delivery"]
            if rule and rule["kind"] in ("mechanics", "fixed_effect_source") and plan["plan_version"] != 3:
                raise PlanError("声明式规则交付需要原生方案版本3")
            if rule and rule["kind"] == "stat_scale":
                if rule["from"]["character_id"] not in chars or rule.get("min", -math.inf) > rule.get("max", math.inf):
                    raise PlanError("stat_scale 引用或截断范围非法")
                if part["dependencies"]["mode"] != "stat_rule":
                    raise PlanError("stat_scale 必须使用 stat_rule 依赖")
            if delivery:
                target = delivery.get("target_character_id", delivery.get("target"))
                if target is not None and target not in chars:
                    raise PlanError("delivery 引用未知角色")
                if delivery["kind"] == "panel" and delivery["operation"] == "percent" and delivery["stat"] not in ("atk", "hp", "def"):
                    raise PlanError("percent 面板交付仅允许 atk/hp/def")
                if rule and (rule["kind"] == "explicit_hit") != (delivery["kind"] == "hit"):
                    raise PlanError("explicit_hit 必须且只能交付 hit")
                if (rule is not None and (rule["kind"] == "mechanics") != (delivery["kind"] == "mechanics")):
                    raise PlanError("mechanics规则必须且只能交付mechanics")
                if (rule is not None and (rule["kind"] == "fixed_effect_source") != (delivery["kind"] == "fixed_effect_source")):
                    raise PlanError("固定条件来源必须且只能交付fixed_effect_source")
            _unique(part["conditions"], "condition_id", "conditions")
            for condition in part["conditions"]:
                intervals = condition["intervals"]
                if (condition["scope"] == "always" and intervals or
                    condition["scope"] == "intervals" and not intervals or
                    any(i["start"] < 0 or i["end"] <= i["start"] for i in intervals)):
                    raise PlanError("conditions 生效区间非法")
    matches = plan.get("rotation_tuning", {}).get("buff_hit_matches", [])
    _unique(matches, "id", "rotation_tuning.buff_hit_matches")
    refs = [("fixed_source_id" if "fixed_source_id" in m["effect_ref"] else "buff_id",
             m["effect_ref"].get("fixed_source_id", m["effect_ref"].get("buff_id")),
             m["effect_ref"]["effect_id"]) for m in matches]
    if len(set(refs)) != len(refs):
        raise PlanError("同一收益只能有一条轴微调")
    for match in matches:
        if match['mode'] == 'runtime_adjust' and not match['runtime_events']:
            raise PlanError('runtime_adjust 必须声明有限运行期语义引用')
        if "fixed_source_id" in match["effect_ref"] and plan["plan_version"] != 3:
            raise PlanError("固定条件来源微调需要原生方案版本3")
        for key in ("hit_ids", "include_hit_ids", "exclude_hit_ids"):
            values = match.get(key, [])
            if len(set(values)) != len(values):
                raise PlanError(f"{match['id']}.{key}: 引用重复")
        if match["mode"] == "adjust":
            inc, exc = set(match["include_hit_ids"]), set(match["exclude_hit_ids"])
            if not (inc or exc) or inc & exc:
                raise PlanError("adjust 增补/排除必须有变化且不能相交")
    return {"valid": True, "plan_version": plan["plan_version"], "plan_id": plan["plan_id"], "revision": plan["revision"]}


def new_plan(template=None):
    if template is not None:
        _json_guard(template, "template")
        if not isinstance(template, dict):
            raise PlanError("template: 模板必须为 JSON 对象")
    template = copy.deepcopy(template) if template is not None else {
        "version": 2, "form": "resolved", "meta": {"name": "新装备方案", "version_line": "release"},
        "enemy": {"level": 90, "resistance": {"default": 0.1}},
        "characters": [], "switches": [], "buffs": [], "hits": [], "loop_duration": 20}
    if template.get("version") not in (1, 2):
        raise PlanError("不支持的模板版本", "unsupported_template_version")
    if template["version"] == 1:
        from genshin_engine.template import validate_template
        validate_template(template)
        template["version"] = 2
        for hit in template["hits"]:
            hit["id"] = "hit." + uuid.uuid4().hex
            hit["damage_type_ids"] = None
        for buff in template.get("buffs", []):
            for effect in buff["effects"]:
                effect.update(id="effect." + uuid.uuid4().hex,
                              benefit_type_id=f"benefit.{effect['zone']}.{effect['stat']}",
                              applies_to={"mode": "all"})
    template.setdefault("id_registry_version", 1)
    template.setdefault("type_definitions", {"damage_types": []})
    plan = {"kind": "genshin-plan", "plan_version": 1, "plan_id": str(uuid.uuid4()), "revision": 0,
            'direct_energy_policy': 'energy-direct-base-er-v1',
            "manual_template": template, "equipment_inputs": {}, "effect_bindings": [],
            "manual_effect_dependencies": [{"effect_ref": {"buff_id": b["id"], "effect_id": e["id"]},
                "mode": "unreviewed", "context_fingerprint": None, "note": ""}
                for b in template.get("buffs", []) for e in b["effects"]],
            "rotation_tuning": {"buff_hit_matches": []}, "resolution": {"state": "pending"},
            "resolved_template": None}
    validate_plan(plan)
    return plan
