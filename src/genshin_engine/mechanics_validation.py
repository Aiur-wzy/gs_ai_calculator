"""Pure validation and selector helpers for the mechanics runtime.

The execution session imports this module as a narrow validation port.  It has
no scheduler, event queue, database, or report-projection ownership.
"""

from __future__ import annotations

import copy
import json
from decimal import Decimal

from genshin_engine import attribute_callback_runtime as callback_runtime
from genshin_engine import resource_counter_view_runtime as counter_view_runtime
from genshin_engine import resource_reaction_runtime as reaction_resource_runtime
from genshin_engine import shared_pool_runtime as pool_runtime
from genshin_engine import snapshot_capture as capture_contract
from genshin_engine.beneficiary_selection import qualification_errors
from genshin_engine.constants import ENEMY_ZONES
from genshin_engine.elemental import (
    _icd_identity,
    _icd_signature,
    _icd_signature_audit,
    validate_elemental_model,
)
from genshin_engine.elemental_schema import (
    FREEZE_RULESET,
    MULTI_AURA_RULESET,
    SWIRL_RULESETS,
)
from genshin_engine.execution_control import ExecutionCancelled
from genshin_engine.mechanics_schema import MECHANICS_SCHEMA
from genshin_engine.particle_quantities import (
    PARTICLE_MODE_FIELDS,
    quantity_model_issue,
)
from genshin_engine.reaction_event_capabilities import (
    reaction_event_available,
    reaction_listener_phase_supported,
)
from genshin_engine.registry import BENEFIT_TYPES
from genshin_engine.rotation_port import TICKS
from genshin_engine.rotation_port import collect_schema_errors as _schema_errors
from genshin_engine.rotation_port import rotation_issue as _issue
from genshin_engine.special_reaction_port import validate_special_reaction_model
from genshin_engine.target_state import predicate_bounds_valid


def _swirl_v2(model):
    return model.get("elemental_model", {}).get("ruleset") in SWIRL_RULESETS


def _threshold_classic_rule_ids(model):
    rules = {r["id"] for r in model["rules"] if r["trigger"].get("kind") == "resource_threshold"}
    contract = model.get("resource_threshold_triggers", {})
    if contract.get("schema_version") == 3:
        lives = {b["payload"]["ref_id"] for b in contract["bindings"]
                 if b["payload"]["kind"] in ("start_lifecycle", "end_lifecycle")}
        rules.update(t["payload"]["ref_id"] for t in model.get("state_runtime", {}).get("timers", [])
                     if t["lifecycle_id"] in lives and t["payload"]["kind"] == "dispatch_rule")
    return rules


def _freeze_reference(model):
    return model.get("elemental_model", {}).get("ruleset") == FREEZE_RULESET


def _multi_aura_v4(model):
    return model.get("elemental_model", {}).get("ruleset") == MULTI_AURA_RULESET


def _tick(value):
    scaled = Decimal(str(value)) * TICKS
    if scaled != scaled.to_integral_value() or abs(scaled) > 10**15:
        raise ValueError("时间须精确到微秒且不超过 10 亿秒。")
    return int(scaled)


def validate_mechanics_model(model, *, attribute_author=False, attribute_limits=None):
    issues = []
    try:
        json.dumps(model, allow_nan=False)
        from genshin_engine.attribute_budget import validate_calculation_options
        from genshin_engine.attribute_contract import attribute_model_schema
        effective = (attribute_limits if attribute_limits is not None else
                     validate_calculation_options(model.get('execution_options', {'schema_version':1}))['effective'])
        schema = copy.deepcopy(MECHANICS_SCHEMA)
        schema['properties']['attribute_model'] = attribute_model_schema(author=attribute_author, limits=effective)
        _schema_errors(model, schema, "mechanics_model", issues)
    except ExecutionCancelled:
        raise
    except (TypeError, ValueError, OverflowError, RecursionError):
        issues.append(_issue("schema", "输入不是有限 JSON。", "mechanics_model"))
    if not issues:
        from genshin_engine.health_mechanics import validate_health_declarations
        issues.extend(validate_health_declarations(model))
        from genshin_engine.target_status import validate_status_declarations
        issues.extend(validate_status_declarations(model))
        issues.extend(pool_runtime.validate_bindings(model))
        issues.extend(reaction_resource_runtime.validate_declaration(model))
        issues.extend(counter_view_runtime.validate_bindings(model))
        issues.extend(callback_runtime.validate_bindings(model))
        from genshin_engine.special_damage_echo import (
            validate_bindings as validate_echo_bindings,
        )
        issues.extend(validate_echo_bindings(model))
        if "elemental_model" in model:
            issues.extend(validate_elemental_model(model["elemental_model"]))
        elif (model.get("application_events") or model.get("elemental_state_events")
              or any("application" in h for h in [*model["hit_events"], *model["hit_patterns"]])):
            issues.append(_issue("missing_information", "元素附着或状态操作需要启用 elemental_model。", "mechanics_model"))
        if "special_reaction_model" in model:
            issues.extend(
                _issue(
                    row["code"], row["message"], "mechanics_model." + row["path"],
                    **{key: copy.deepcopy(value) for key, value in row.items()
                       if key not in {"code", "message", "path"}},
                )
                for row in validate_special_reaction_model(model["special_reaction_model"])
            )
        descriptors = [*model["hit_events"], *model["hit_patterns"], *model.get("application_events", [])]
        contact_ids = {p["id"] for p in model.get("elemental_model", {}).get("contact_profiles", [])}
        for descriptor in descriptors:
            if descriptor.get('target_id') is not None:
                application_target = descriptor.get('application', {}).get('target_id')
                runtime_target = model.get('elemental_model', {}).get('target', {}).get('id')
                if any(target is not None and target != descriptor['target_id']
                       for target in (application_target, runtime_target)):
                    issues.append(_issue('target_identity_conflict', '命中目标与附着或元素模型的目标不一致。',
                        'mechanics_model.hit_events.target_id',
                        event_id=descriptor.get('hit_id', descriptor.get('id')),
                        target_id=descriptor['target_id'], application_target=application_target,
                        runtime_target=runtime_target))
            contact = descriptor.get("contact")
            if contact is not None:
                if not _freeze_reference(model):
                    issues.append(_issue("unused_field", "接触字段只用于冻结参考模型。", "mechanics_model.contact"))
                elif contact["kind"] == "profile" and contact["profile_id"] not in contact_ids:
                    issues.append(_issue("reference", "接触档案不存在。", "mechanics_model.contact"))
        rules = model["rules"]
        captures = model.get("snapshot_captures", [])
        if len(captures) > capture_contract.MAX_SNAPSHOT_CAPTURES or len(capture_contract.canonical(captures)) > capture_contract.MAX_SNAPSHOT_CAPTURE_DECLARATION_BYTES:
            issues.append(_issue("snapshot_capture_limit", "捕获声明超过条数或字节限制。", "mechanics_model.snapshot_captures"))
        if len({c["id"] for c in captures}) != len(captures) or len({c["action_id"] for c in captures}) != len(captures):
            issues.append(_issue("duplicate_id", "捕获ID不可重复，每个动作最多一项捕获。", "mechanics_model.snapshot_captures"))
        for capture in captures:
            for key in ("offset", "precision"):
                try:
                    _tick(capture[key])
                except ValueError as exc:
                    issues.append(_issue("time_precision", str(exc), capture["id"]+"."+key))
            if capture["precision"] <= 0:
                issues.append(_issue("time_precision", "捕获记录精度必须大于零。", capture["id"]+".precision"))
        ids = [r["id"] for r in rules]
        if len(ids) > 128 or len(set(ids)) != len(ids):
            issues.append(_issue("duplicate_id", "规则 ID 重复或超过 128 条。", "mechanics_model.rules"))
        sources = [(r["owner"], r["source_id"]) for r in rules]
        if len(set(sources)) != len(sources):
            issues.append(_issue("duplicate_delivery", "同一提供者的来源部件只能由一条规则交付；多收益放入同一 effects。", "mechanics_model.rules"))
        fixed = model.get("fixed_effect_sources", [])
        fixed_ids = [s["id"] for s in fixed]
        if len(fixed) > 64 or len(set(fixed_ids)) != len(fixed_ids) or set(fixed_ids) & set(ids):
            issues.append(_issue("duplicate_id", "固定来源最多64项，且不可与其他固定来源或普通规则共用ID。", "mechanics_model.fixed_effect_sources"))
        all_sources = sources + [(s["owner"], s["source_id"]) for s in fixed]
        if len(set(all_sources)) != len(all_sources):
            issues.append(_issue("duplicate_delivery", "固定来源与其他执行来源不可重复交付同一提供者的同一部件。", "mechanics_model.fixed_effect_sources"))
        for source in fixed:
            effect_ids = [e["id"] for e in source["effects"]]
            if not 1 <= len(effect_ids) <= 8 or len(set(effect_ids)) != len(effect_ids):
                issues.append(_issue("limit", "固定来源须有1至8项收益，收益ID不可重复。", source["id"]))
            for effect in source["effects"]:
                info = BENEFIT_TYPES.get(effect["benefit_type_id"], {})
                if (info.get("zone") in ENEMY_ZONES) != (effect["domain"] == "enemy"):
                    issues.append(_issue("domain", "敌方收益必须使用enemy域，角色收益使用self域。", source["id"]+":"+effect["id"]))
                if not predicate_bounds_valid(effect["target_predicate"]):
                    issues.append(_issue("limit", "条件树最多4层32节点，分支1至8项，元素非空且不重复。", source["id"]+":"+effect["id"]))
                values = effect["filter"].get("elements", [])
                if "elements" in effect["filter"] and (not values or len(set(values)) != len(values)):
                    issues.append(_issue("reference", "固定收益元素筛选不可为空或重复。", source["id"]+":"+effect["id"]))
        initial_ids = [x["rule_id"] for x in model["initial_state"]["rules"]]
        if len(set(initial_ids)) != len(initial_ids) or set(initial_ids) != set(ids):
            issues.append(_issue("initial_state", "每条规则须恰好声明一次初态（允许空实例）。", "mechanics_model.initial_state"))
        for initial in model["initial_state"]["rules"]:
            rule = next((r for r in rules if r["id"] == initial["rule_id"]), None)
            if rule is None:
                continue
            for inst in initial["instances"]:
                if len(inst["remaining"]) > rule["stacking"]["max"] or not inst["remaining"] or not inst["beneficiaries"]:
                    issues.append(_issue("initial_state", "初始实例需要受益者和不超过上限的活跃层。", initial["rule_id"]))
                quota = rule.get("quota")
                if quota:
                    expected = set(inst["beneficiaries"]) if quota["scope"] == "per_beneficiary" else {"team"}
                    if set(inst.get("quota", {})) != expected or any(type(x) is not int or x > quota["capacity"] for x in inst.get("quota", {}).values()):
                        issues.append(_issue("initial_state", "初始次数必须逐池明确，且为不超过容量的整数。", initial["rule_id"]))
                if any(isinstance(e["value"], dict) and e["value"]["sample"] == "trigger" for e in rule["effects"]):
                    issues.append(_issue("unsupported", "一期不接受已有 trigger 冻结值的初始活跃实例。", initial["rule_id"]))
        for rule in rules:
            path = f"mechanics_model.rules.{rule['id']}"
            selectors = [*([rule["trigger"]] if "event" in rule["trigger"] else []), *([rule["quota"]["consume"]] if rule.get("quota") else []),
                         *[e["filter"] for e in rule["effects"] if "filter" in e]]
            for selector in selectors:
                if "action_admission" in selector and (selector is not rule["trigger"]
                        or selector.get("event") != "action_start" or not selector.get("action_ids")):
                    issues.append(_issue("action_admission_scope",
                        "动作接纳筛选只允许用于含明确动作ID的action_start规则触发。", path))
                if selector["event"] == "elemental_reaction":
                    if not reaction_event_available(model, selector["event"]) or not reaction_listener_phase_supported(selector):
                        issues.append(_issue("unsupported", "自动反应触发需要元素模拟，且只支持反应后置相位。", path))
                elif selector["event"] == "reaction_damage":
                    if not reaction_event_available(model, selector["event"]):
                        issues.append(_issue("unsupported", "reaction_damage需要普通扩散v2。", path))
                elif "reaction_types" in selector:
                    issues.append(_issue("unused_field", "reaction_types 只能筛选自动反应事件。", path))
                if "reaction_elements" in selector:
                    values = selector["reaction_elements"]
                    if not _swirl_v2(model) or selector["event"] not in ("elemental_reaction", "reaction_damage"):
                        issues.append(_issue("unused_field", "reaction_elements仅用于v2反应或反应伤害。", path))
                    if not values or len(set(values)) != len(values):
                        issues.append(_issue("reference", "reaction_elements必须非空且不重复。", path))
                if "owner_presence" in selector and (not _swirl_v2(model) or selector is not rule["trigger"]):
                    issues.append(_issue("unused_field", "owner_presence仅用于v2规则触发，不撤销已激活收益。", path))
                if "freeze" in selector.get("reaction_types", []) and not _freeze_reference(model):
                    issues.append(_issue("unsupported", "冻结筛选需要冻结参考模型。", path))
                if "swirl" in selector.get("reaction_types", []) and not _swirl_v2(model):
                    issues.append(_issue("unsupported", "扩散筛选需要普通扩散v2。", path))
            if rule["trigger"].get("event") == "reaction_damage" and not reaction_listener_phase_supported(rule["trigger"]):
                issues.append(_issue("unsupported", "反应伤害触发仅支持post相位。", path))
            for code, message in qualification_errors(rule):
                issues.append(_issue(code, message, path + ".beneficiaries"))
            if rule["beneficiaries"]["mode"] == "characters" and not rule["beneficiaries"].get("characters"):
                issues.append(_issue("missing_information", "明确角色模式需要非空角色列表。", path))
            if rule["timing"]["duration"] <= 0 and rule["effects"]:
                issues.append(_issue("value", "有收益的规则持续时间必须大于 0。", path))
            if rule["stacking"]["add"] > rule["stacking"]["max"]:
                issues.append(_issue("value", "新增层数不可超过上限。", path))
            effect_ids = [e["id"] for e in rule["effects"]]
            if len(set(effect_ids)) != len(effect_ids) or len(effect_ids) > 64:
                issues.append(_issue("duplicate_id", "收益 ID 在同一规则内不可重复，且最多 64 项。", path))
            resource_ids = [r["id"] for r in rule.get("resources", [])]
            if len(set(resource_ids)) != len(resource_ids) or len(resource_ids) > 16:
                issues.append(_issue("duplicate_id", "资源 ID 在同一规则内不可重复，且最多 16 项。", path))
            if rule.get("quota") and (rule["quota"]["consume"]["event"] not in ("hit", "reaction_damage") or rule["quota"]["consume"]["phase"] != "pre"):
                issues.append(_issue("unsupported", "次数消费使用单目标命中的 pre 阶段，以本次消费回执决定收益。", path + ".quota.consume"))
            if rule["beneficiaries"]["mode"] != "characters" and "characters" in rule["beneficiaries"]:
                issues.append(_issue("unused_field", "只有 characters 模式可以填写角色列表。", path + ".beneficiaries"))
            for key in ("delay", "duration", "cooldown"):
                try:
                    _tick(rule["timing"][key])
                except ValueError as exc:
                    issues.append(_issue("time_precision", str(exc), path+".timing."+key))
            for effect in rule["effects"]:
                info = BENEFIT_TYPES.get(effect["benefit_type_id"], {})
                if effect["benefit_type_id"] == "benefit.energy_recharge.all" and (rule.get("quota") or effect.get("filter")):
                    issues.append(_issue("unsupported", "临时充能一期不与次数消费或伤害选择器混用。", path))
                if effect.get("filter") and (effect["filter"]["event"] not in (("hit", "reaction_damage") if _swirl_v2(model) else ("hit",)) or effect["filter"]["phase"] != "pre"):
                    issues.append(_issue("unsupported", "收益筛选须为hit/pre，或v2专用reaction_damage/pre；触发相位写在trigger。", path))
                if (info.get("zone") in ENEMY_ZONES) != (effect["domain"] == "enemy"):
                    issues.append(_issue("domain", "敌方收益必须使用 enemy 域，角色收益不可使用 enemy 域。", path))
                value = effect["value"]
                if isinstance(value, dict):
                    stat_v3 = model.get("attribute_model", {}).get("contract") == "attribute-ledger-v3"
                    stat_v4 = model.get("attribute_model", {}).get("contract") == "attribute-ledger-v4"
                    allowed_stats = (("em", "atk", "hp", "def", "energy_recharge", "base_atk", "base_hp", "base_def")
                                     if stat_v4 else ("em", "atk", "base_atk") if stat_v3 else ("em",))
                    if "basis" in value and ("attribute_model" not in model or
                            value.get("stat") not in allowed_stats):
                        issues.append(_issue("attribute_basis_scope", "basis必须属于已启用属性账的读取范围。", path))
                    if stat_v3 and value.get("stat") == "base_atk" and value.get("basis", "base") != "base":
                        issues.append(_issue("attribute_basis_scope", "base_atk仅映射不可变基础攻击。", path))
                    if stat_v4 and value.get("stat", "").startswith("base_") and value.get("basis", "base") != "base":
                        issues.append(_issue("attribute_basis_scope", "base属性仅映射不可变基础面板。", path))
                    if value["sample"] == "event" and not value.get("event_id"):
                        issues.append(_issue("reference", "事件采样必须指定 event_id。", path))
                    if value["sample"] != "event" and "event_id" in value:
                        issues.append(_issue("unused_field", "只有 event 采样可以指定 event_id。", path))
                    if value.get("min", float("-inf")) > value.get("max", float("inf")):
                        issues.append(_issue("value", "表达式下限不可大于上限。", path))
            for resource in rule.get("resources", []):
                required = ["amounts"] if resource["kind"] == "energy_direct" else ["count", "particle_type", "element", "travel_time"]
                if any(k not in resource for k in required):
                    issues.append(_issue("missing_information", "资源声明缺少 " + ",".join(required), path))
                if resource["kind"] == "energy_direct" and not resource.get("amounts"):
                    issues.append(_issue("missing_information", "固定回能需要明确目标和数值。", path))
                inapplicable = (set(resource) & {"count", "element", "particle_type", "travel_time", *PARTICLE_MODE_FIELDS}) if resource["kind"] == "energy_direct" else (set(resource) & {"amounts"})
                if inapplicable:
                    issues.append(_issue("unused_field", "资源声明包含不适用于本类型的字段。", path, fields=sorted(inapplicable)))
                if resource["kind"] == "particles" and (problem := quantity_model_issue(resource)):
                    issues.append(_issue(*problem, path))
                try:
                    _tick(resource["delay"])
                    if "travel_time" in resource:
                        _tick(resource["travel_time"])
                except ValueError as exc:
                    issues.append(_issue("time_precision", str(exc), path))
        for pattern in model["hit_patterns"]:
            try:
                for offset in pattern["offsets"]:
                    _tick(offset)
            except ValueError as exc:
                issues.append(_issue("time_precision", str(exc), pattern["id"]))
        for attempt in model.get("application_events", []):
            try:
                _tick(attempt["t"])
            except ValueError as exc:
                issues.append(_issue("time_precision", str(exc), attempt["id"]))
        for event in model.get("special_reaction_events", []):
            try:
                _tick(event["t"])
                if event["kind"] == "lunar_bloom_reserve_grant":
                    _tick(event["duration_seconds"])
            except ValueError as exc:
                issues.append(_issue("time_precision", str(exc), event["id"]))
        for event in model.get("elemental_state_events", []):
            try:
                _tick(event["t"])
                if event["kind"] == "bloom_core_conversion_open":
                    _tick(event["duration_seconds"])
                    _tick(event["replacement_lifetime_seconds"])
            except ValueError as exc:
                issues.append(_issue("time_precision", str(exc), event["id"]))
            repeat = event["repeat"]
            cycles = repeat.get("cycles", [])
            if ((repeat["mode"] == "cycles" and (not cycles or len(cycles) != len(set(cycles))))
                    or (repeat["mode"] != "cycles" and "cycles" in repeat)):
                issues.append(_issue(
                    "repeat", "指定轮次必须非空且不重复；其他重复模式不能包含 cycles。",
                    event["id"]))
            if event["status"] == "pending":
                issues.append(_issue(
                    "elemental_state_operation_pending",
                    "元素状态操作来源仍待核实，不能进入执行队列。",
                    event["id"], severity="pending"))
            if event["kind"] == "crystallize_shard_pickup" and not _multi_aura_v4(model):
                issues.append(_issue(
                    "crystallize_pickup_ruleset",
                    "结晶晶片拾取需要gauge-multi-aura-v4普通结晶状态机。",
                    event["id"]))
            if event["kind"].startswith("bloom_core_") and not _multi_aura_v4(model):
                issues.append(_issue("bloom_conversion_ruleset", "草原核转化与吸收需要gauge-multi-aura-v4。", event["id"]))
            elif event["kind"].startswith("bloom_core_") and not (
                model.get("special_reaction_model", {}).get("replacements", {}).get("lunar_bloom") or {}
            ).get("enabled"):
                issues.append(_issue("special_switch_disabled", "草原核转化与吸收需要启用月绽放的草原核运行时。", event["id"]))
        for event in model["condition_events"]:
            if ("value" in event) == ("dew_consumption" in event):
                issues.append(_issue("condition_value_source", "条件须提供 value 或引用实际扣露结果，二者选一。", event["id"]))
            try:
                _tick(event["t"])
            except ValueError as exc:
                issues.append(_issue("time_precision", str(exc), event["id"]))
            repeat = event["repeat"]
            cycles = repeat.get("cycles", [])
            if (repeat["mode"] == "cycles" and (not cycles or len(cycles) != len(set(cycles)))) or (repeat["mode"] != "cycles" and "cycles" in repeat):
                issues.append(_issue("repeat", "指定轮次必须非空且不重复；其他重复模式不能包含 cycles。", event["id"]))
        # Legacy conversions retain their constant-only dependency guard. Only
        # explicit graph v2 EM-to-EM closures move to immutable graph evaluation.
        conversions = []
        for rule in rules:
            for effect in rule["effects"]:
                info = BENEFIT_TYPES.get(effect["benefit_type_id"], {})
                if isinstance(effect["value"], dict) and effect["value"].get("kind") == "stat" and (info.get("zone") in ("stat_add", "stat_pct", "em") or effect["benefit_type_id"] == "benefit.energy_recharge.all"):
                    stat = "energy_recharge" if effect["benefit_type_id"] == "benefit.energy_recharge.all" else "em" if info["zone"] == "em" else info["stat"]
                    conversions.append((rule["id"], stat, effect["value"]["stat"] == "em" and stat == "em"))
        for rule in rules:
            for effect in rule["effects"]:
                value = effect["value"]
                if isinstance(value, dict) and value.get("kind") == "stat":
                    for dependency, stat, graph_supported in conversions:
                        if value["stat"] == stat:
                            if "attribute_model" in model and stat == "em" and value.get("basis") == "base":
                                continue
                            contract = model.get("attribute_model", {}).get("contract")
                            if contract == "attribute-ledger-v2" and graph_supported:
                                continue
                            if contract == "attribute-ledger-v3" and stat in ("em", "atk"):
                                continue
                            if contract == "attribute-ledger-v4" and stat in ("em", "atk", "hp", "def", "energy_recharge"):
                                continue
                            issues.append(_issue("unsupported_dependency", "旧常量面板路径不求解该属性转换闭包；此潜在依赖并不证明实际存在循环。请按实际来源建立显式属性图。", rule["id"],
                                                 dependency_path=[rule["id"], dependency, stat],
                                                 dependency_kind="unsupported_conversion_closure", known_cycle=False,
                                                 required_contract=("attribute-ledger-v3" if stat == "atk" else "attribute-ledger-v2"),
                                                 recovery="prepare_explicit_attribute_graph"))
    return {"status": ("invalid" if any(i["severity"] == "error" for i in issues) else "pending") if issues else "valid", "issues": issues}


_STELLAR_SWIRL_DAMAGE_COMPONENTS = frozenset({
    "stellar_swirl_anemo", "stellar_swirl_cryo_lv2", "stellar_swirl_cryo_lv3",
})


def _selector(selector, event):
    if selector.get("event") != event["event"]:
        return False
    if 'event_refs' in selector:
        from genshin_engine.runtime_event_match import reference_matches
        if not any(reference_matches(ref, event) for ref in selector['event_refs']):
            return False
    admission = selector.get("action_admission")
    if admission is not None:
        from genshin_engine.action_admission_audit import (
            CONTRACT as ACTION_ADMISSION_CONTRACT,
        )
        from genshin_engine.action_admission_audit import (
            certificate_id as action_admission_certificate_id,
        )

        reference = event.get("action_admission_certificate")
        expected_id = action_admission_certificate_id(event.get("action_id"))
        if (admission != {"contract": ACTION_ADMISSION_CONTRACT}
                or type(reference) is not dict
                or set(reference) != {"contract", "certificate_id", "run_id"}
                or reference.get("contract") != ACTION_ADMISSION_CONTRACT
                or reference.get("certificate_id") != expected_id
                or reference.get("run_id") != f"{event.get('origin')}:{expected_id}"):
            return False
    for field, value in (("action_ids", event.get("action_id")), ("event_ids", event["id"]),
                         ("characters", event.get("character")), ("action_type_ids", event.get("action_type_id")),
                         ("elements", event.get("element")),
                         ("reaction_elements", event.get("reaction_element"))):
        if field in selector and value not in selector[field]:
            return False
    if "reaction_types" in selector:
        if event["event"] == "elemental_reaction":
            stable_tags = event.get("reaction_trigger_tags")
            if not isinstance(stable_tags, list) or not stable_tags:
                stable_tags = [event.get("reaction_type")]
            if not set(selector["reaction_types"]) & set(stable_tags):
                return False
        elif event.get("reaction_type") not in selector["reaction_types"]:
            # The public filter names the reaction family, while the damage
            # dispatcher preserves its immediate/delayed component identity.
            # Only these supported components belong to that family: do not
            # accept arbitrary prefixes or change elemental trigger semantics.
            if not (event["event"] == "reaction_damage"
                    and "stellar_swirl" in selector["reaction_types"]
                    and event.get("reaction_type") in _STELLAR_SWIRL_DAMAGE_COMPONENTS):
                return False
    if "damage_type_ids" in selector and not set(selector["damage_type_ids"]) & set(event.get("damage_type_ids", [])):
        return False
    return not ("condition" in selector and event.get("conditions", {}).get(selector["condition"]) is not True)


def validate_elemental_counter_bindings(template, rotation, mechanics):
    """Validate rule consistency at the actor-local counter boundary.

    Elemental profiles intentionally do not own an actor.  The same upstream
    counter tag may therefore use different rule groups for different actors;
    only concrete action-to-profile usage supplies enough identity to reject a
    conflict.  This helper is shared by native composition and execution so a
    freshly compiled database profile cannot bypass the check.
    """
    elemental = mechanics.get("elemental_model") if isinstance(mechanics, dict) else None
    if not elemental:
        return []
    profiles = {p.get("id"): p for p in elemental.get("profiles", []) if isinstance(p, dict)}
    actions = {a.get("id"): a for a in rotation.get("actions", []) if isinstance(a, dict)}
    abilities = {a.get("id"): a for a in rotation.get("abilities", []) if isinstance(a, dict)}
    target_id = (elemental.get("target") or {}).get("id")
    bindings, issues = {}, []

    def actor_for(action_id):
        action = actions.get(action_id)
        ability = abilities.get(action.get("ability_id")) if action else None
        return ability.get("character") if ability else None

    def bind(application, path, actor):
        if not isinstance(application, dict) or actor is None:
            return
        profile = profiles.get(application.get("profile_id"))
        if profile is None or application.get("target_id") != target_id:
            return
        icd = profile.get("icd", {})
        if icd.get("kind") not in ("standard", "sequence", "timer_counter"):
            return
        try:
            counter_tag, rule_group, _ = _icd_identity(icd)
            signature = _icd_signature(icd)
        except (KeyError, TypeError, ValueError, ArithmeticError):
            # Structural validation owns malformed profiles.  This pass only
            # compares well-formed concrete bindings.
            return
        if not counter_tag or not rule_group or signature is None:
            return
        key = (actor, counter_tag, target_id)
        bucket = bindings.setdefault(key, {})
        audit_key = (profile["id"], rule_group, signature)
        entry = bucket.setdefault(audit_key, {
            "profile_id": profile["id"],
            "profile_path": f"mechanics_model.elemental_model.profiles.{profile['id']}",
            "usage_paths": [],
            "rule_group": rule_group,
            "signature": _icd_signature_audit(signature),
            "declared_source": profile.get("source"),
        })
        if path not in entry["usage_paths"]:
            entry["usage_paths"].append(path)

    for descriptor in mechanics.get("hit_events", []):
        bind(descriptor.get("application"), descriptor.get("hit_id", "mechanics_model.hit_events"),
             actor_for(descriptor.get("action_id")))
    for pattern in mechanics.get("hit_patterns", []):
        bind(pattern.get("application"), pattern.get("id", "mechanics_model.hit_patterns"),
             actor_for(pattern.get("action_id")))
    for event in mechanics.get("application_events", []):
        bind(event.get("application"), event.get("id", "mechanics_model.application_events"),
             actor_for(event.get("action_id")))
    for (actor, counter_tag, bound_target), bucket in bindings.items():
        audit_bindings = list(bucket.values())
        distinct = {(binding["signature"]["reset_microseconds"],
                     binding["signature"].get("kind"),
                     tuple(binding["signature"].get("sequence", [])),
                     binding["signature"].get("hit_interval")) for binding in audit_bindings}
        if len(distinct) <= 1:
            continue
        first = audit_bindings[0]
        conflicting = next(binding for binding in audit_bindings
                           if binding["signature"] != first["signature"])
        path = conflicting["usage_paths"][0]
        conflicting_path = first["usage_paths"][0]
        issues.append(_issue(
            "icd_group_conflict",
            "同一角色、计数标签与目标引用了不同ICD执行签名。", path,
            actor=actor, counter_tag=counter_tag, target_id=bound_target,
            rule_group=conflicting["rule_group"], conflicting_rule_group=first["rule_group"],
            profile_id=conflicting["profile_id"], conflicting_profile_id=first["profile_id"],
            signature=copy.deepcopy(conflicting["signature"]),
            conflicting_signature=copy.deepcopy(first["signature"]),
            conflicting_path=conflicting_path, bindings=copy.deepcopy(audit_bindings),
            source_paths=[path for binding in audit_bindings
                          for path in [binding["profile_path"], *binding["usage_paths"]]]))
    return issues

__all__ = [
    "_freeze_reference",
    "_multi_aura_v4",
    "_selector",
    "_swirl_v2",
    "_threshold_classic_rule_ids",
    "_tick",
    "validate_elemental_counter_bindings",
    "validate_mechanics_model",
]
