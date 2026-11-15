"""Materialize calibrated contacts, not native game-script dispatch."""
from __future__ import annotations

import copy
import json
from bisect import bisect_right
from collections import Counter
from decimal import Decimal, InvalidOperation

from genshin_app.task_capability_producer import emit_task_missing
from genshin_engine.elemental import validate_elemental_model
from genshin_engine.elemental_schema import (
    DYNAMIC_SWIRL_RULESET,
    FREEZE_RULESET,
    REFERENCE_CONTRACT,
)

from .action_event_schema import (
    APPLICATION_COMPILER,
    APPLICATION_CONTRACT,
    APPLICATION_KIND,
    COMPILER,
    FIELDS,
    KIND,
    MAX_RECEIPT_BYTES,
    action_event_capabilities,
    validate_action_event_fields,
)
from .files import fingerprint
from .schema import PlanError, _json_guard

__all__ = ["action_event_capabilities", "compile_action_events", "validate_action_event_fields"]
PREFIX = "auto.action."
EMISSION = "primary-contact"
ORDER_MIN = -10**9
ORDER_MAX = 10**9


class _Pending(Exception):
    pass


def _ticks(value):
    if type(value) not in (int, float):
        raise ValueError("时间必须是已知数值")
    scaled = Decimal(str(value)) * 1000000
    if not scaled.is_finite() or scaled != scaled.to_integral_value() or not 0 <= scaled <= 10**15:
        raise ValueError("时间必须为有限精确微秒且不超过10亿秒")
    return int(scaled)


def _walk(value, path=""):
    yield path, value
    children = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else ()
    for key, child in children:
        yield from _walk(child, path + "/" + str(key))


def _source_identity(record):
    """Database identities only; legacy compilation hashes contain local aliases."""
    source = record.get("source") or {}
    snapshot = record.get("snapshot") or {}
    identity = {"source": {key: copy.deepcopy(source[key]) for key in (
        "source_path", "source_sha256", "json_pointer", "parameter_pointer", "node_sha256", "definition_id") if key in source},
        "snapshot": {key: copy.deepcopy(snapshot[key]) for key in (
            "snapshot_id", "version_line", "upstream_commit", "indexer_version", "normalization_version", "mapping_fingerprint") if key in snapshot}}
    definition = source.get("definition") or {}
    identity["source"]["definition"] = {key: copy.deepcopy(definition[key])
        for key in ("definition_id", "node_sha256") if key in definition}
    normalization = snapshot.get("normalization")
    if isinstance(normalization, dict):
        identity["snapshot"]["normalization"] = {key: copy.deepcopy(normalization[key]) for key in (
            "mapping_fingerprint", "normalization_version", "profile_id", "upstream_commit") if key in normalization}
    return identity


def _without_id(value):
    return {k: copy.deepcopy(v) for k, v in value.items() if k != "id"}


def _prototype_references(plan, template, prototype):
    """Only actual selectors, never arbitrary strings in descriptions."""
    paths = []
    for root, value in (("template", template), ("mechanics_model", plan.get("mechanics_model") or {}),
                        ("rotation_tuning", plan.get("rotation_tuning") or {})):
        for path, obj in _walk(value, root):
            if not isinstance(obj, dict):
                continue
            for key in ("hit_ids", "include_hit_ids", "exclude_hit_ids", "event_ids"):
                if isinstance(obj.get(key), list) and prototype in obj[key]:
                    paths.append(path + "/" + key)
            if obj.get("mode") == "hits" and prototype in obj.get("hit_ids", []):
                paths.append(path)
            if obj.get("sample") == "event" and obj.get("event_id") == prototype:
                paths.append(path + "/event_id")
            if obj.get("self_snapshot_event") == prototype:
                paths.append(path + "/self_snapshot_event")
    return sorted(set(paths))


def _time_events(rotation, model, template):
    events = []
    actions = {a["id"]: a for a in rotation["actions"]}
    for action in actions.values():
        events.extend([(_ticks(action.get("start")), action["order"], action["id"]),
                       (_ticks(action.get("occupation_end")), action["order"], action["id"] + ":end")])
    for event in rotation["events"]:
        events.append((_ticks(event.get("credit_at") if event["type"] == "energy_credit" else event.get("t")), event["order"], event["id"]))
    for event in model.get("condition_events", []) + model.get("application_events", []):
        events.append((_ticks(event.get("t")), event["order"], event["id"]))
    hits = {h["id"]: h for h in template["hits"]}
    for event in model["hit_events"]:
        events.append((_ticks(hits[event["hit_id"]]["t"]), event["order"], event["hit_id"]))
    for pattern in model["hit_patterns"]:
        action = actions[pattern["action_id"]]
        for index, (offset, order) in enumerate(zip(pattern["offsets"], pattern["orders"], strict=True), 1):
            events.append((_ticks(round(action["start"] + offset, 6)), order, f"{pattern['id']}:{index}"))
    return events


def _parent_action_start_events(rotation, action):
    start = _ticks(action["start"])
    return [event for event in rotation.get("events", [])
            if event.get("type") == "energy_spend"
            and event.get("action_id") == action["id"]
            and _ticks(event.get("t")) == start]


def _action_start_post_order(rotation, action):
    parent_orders = [action["order"],
                     *(event["order"] for event in _parent_action_start_events(rotation, action))]
    if any(type(order) is not int or not ORDER_MIN <= order <= ORDER_MAX
           for order in parent_orders):
        raise ValueError("动作起点父事件order超出受支持整数边界")
    result = max(parent_orders) + 1
    if not ORDER_MIN <= result <= ORDER_MAX:
        raise ValueError("动作起点后的派生事件order超出受支持整数边界")
    return result


def _is_unambiguous_action_start(rotation, action, generated, peer_ids):
    allowed = {action["id"],
               *(event["id"] for event in _parent_action_start_events(rotation, action))}
    return (generated["offset_ticks"] == 0
            and generated["order"] == _action_start_post_order(rotation, action)
            and len(peer_ids) == len(allowed)
            and set(peer_ids) == allowed)


def _profile_record(profile, ability_ids, count):
    compiler = APPLICATION_COMPILER if profile['kind'] == APPLICATION_KIND else COMPILER
    return {"profile_id": profile["id"], "kind": profile["kind"], "compiler": compiler, "actor": None,
        "ability_ids": ability_ids, "prototype_hit_id": profile["prototype_hit_id"], "status": "pending", "dispatch_mode": "calibrated",
        "skill_coverage": "partial", "resource_coverage": "not_generated", "gameplay_interpretation": "declared_not_verified",
        "profile_fingerprint": None, "required_confirmation": None,
        "accepted_profile_fingerprint": profile["accepted_profile_fingerprint"], "instance_fingerprint": None,
        "source_dependency_fingerprint": None, "generated": [], "stats": {"matched_actions": count,
            "planned_hits": count, "generated_hits": 0, "landed_hits": 0, "missed_hits": 0}, "issues": []}


def _bound_pending_records(records):
    if len(json.dumps(records, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()) > MAX_RECEIPT_BYTES:
        for record in records:
            record["actor"] = None
            record["issues"] = [{"code": "action_event_receipt_limit", "path": "action_event_profiles." + record["profile_id"],
                "message": "待处理档案回执超过2MiB；已省略过大的角色身份及逐项诊断，原始输入保留"}]


def build_single_action_scope(profile, *, raw_hit, hit, parameter, declaration,
                              application, contact, metadata, elemental_record,
                              reaction_compilations, selected_actions, rotation, model):
    """Build the original portable acceptance payload, without dispatch or DB.

    Both fresh compilation and contact-proof replay use this exact construction.
    Local actor/action/profile aliases stay outside semantic acceptance; capture
    calibrations remain part of it. Callers verify the supplied materialization.
    """
    if profile['kind'] not in (KIND, APPLICATION_KIND):
        raise ValueError('单次动作scope不支持此档案kind')
    dynamic = profile['kind'] == APPLICATION_KIND
    if dynamic and (contact is not None or 'contact_profile_id' in profile):
        raise ValueError('普通附着v2禁止freeze contact字段或材料')
    if dynamic and ((model.get('elemental_model') or {}).get('ruleset') != DYNAMIC_SWIRL_RULESET
                    or validate_elemental_model(model['elemental_model'])):
        raise ValueError('普通附着v2 scope需要当前合法v3元素模型')
    param, app, selected = parameter, application, selected_actions
    selection = param["resolved_selection"]
    owned_metadata = metadata is not None
    # Whitelist semantic source data; compilation fingerprints in the old
    # APIs contain local actor/hit/profile names and are not portable.
    dependencies = {"parameter": {**_source_identity(param),
        "selection": {k: selection[k] for k in ("character_id", "skill_depot_id", "skill_id", "constellation")},
        "effective_level": param["level"]["effective_level"], "param_index": declaration["param_index"], "scaling_stat": "atk",
        "purpose": declaration["purpose"], "status": declaration["status"], "interpretation_source": declaration["source"],
        "value": hit["multiplier"]}, "application": _without_id(app)}
    if dynamic:
        dependencies.update(ruleset=DYNAMIC_SWIRL_RULESET, application_contract=copy.deepcopy(APPLICATION_CONTRACT))
    else:
        dependencies.update(contact=_without_id(contact), ruleset=FREEZE_RULESET,
                            reference_fingerprint=REFERENCE_CONTRACT['contract_fingerprint'])
    dependencies["parameter"]["entries"] = {name: _source_identity({"source": source})["source"]
        for name, source in sorted(param.get("entry_sources", {}).items())}
    if owned_metadata:
        dependencies["attack_metadata"] = copy.deepcopy(metadata["semantic_dependency"])
    if elemental_record:
        counter_tag = elemental_record.get("counter_tag", elemental_record.get("shared_group"))
        rule_group = elemental_record.get("rule_group", elemental_record.get("attenuation_group"))
        dependencies["elemental_source"] = {**_source_identity(elemental_record),
            "version_line": elemental_record["version_line"],
            "counter_tag": copy.deepcopy(counter_tag), "rule_group": copy.deepcopy(rule_group),
            "binding_basis": copy.deepcopy(elemental_record["binding_basis"]),
            "compiled_icd": copy.deepcopy(elemental_record["compiled_icd"]),
            **({"legacy_source_fields": [name for name in ("shared_group", "attenuation_group")
                                          if name in elemental_record]}
               if "shared_group" in elemental_record or "attenuation_group" in elemental_record else {})}
    dependencies["reaction_sources"] = [{"version_line": r["version_line"],
        "snapshot_id": (r.get("snapshot") or {}).get("snapshot_id"),
        "profiles": [q["content_fingerprint"] for q in r.get("profiles", [])],
        "compiled_icd": r.get("compiled_icd"), "binding_basis": r.get("binding_basis")} for r in reaction_compilations]
    semantic_hit = {key: raw_hit.get(key, 1 if key == "count" else None) for key in ("damage_type_ids", "damage_system", "element", "scaling_stat", "count")}
    # Registry IDs determine behavior; labels/descriptions do not.
    scope = {"compiler": APPLICATION_COMPILER if dynamic else COMPILER, "kind": profile["kind"], "variant": profile["variant"],
        "calibration": profile["calibration"], "default_landed": profile["default_landed"],
        "self_sampling": profile["self_sampling"], "hit": semantic_hit, "dependencies": dependencies}
    if "topology_occurrence" in profile:
        scope["topology_occurrence"] = copy.deepcopy(profile["topology_occurrence"])
    captures = {}
    if profile["self_sampling"] == "action_capture":
        from .action_capture import prepare_action_captures
        captures, scope["snapshot_capture_calibrations"] = prepare_action_captures(selected, rotation, model)
    return scope, captures


def _compile_single_action_events(plan, template, *, parameter_compilations, elemental_compilations,
                          reaction_compilations, missing, attack_metadata_compilations=()):
    """Return (whole working mechanics model | None, profile records, sources).

    No database access or input mutation. The caller atomically installs the
    returned model only when all records are ready; source evidence is deduped.
    """
    validate_action_event_fields(plan)
    if not any(plan.get(name) for name in FIELDS):
        return None, [], []
    _json_guard(plan)
    _json_guard(template)
    profiles, bindings, overrides = (plan[name] for name in FIELDS)
    model, rotation = plan.get("mechanics_model"), plan.get("rotation_model")
    if not isinstance(model, dict) or not isinstance(rotation, dict):
        issues = []
        for field, value in (("rotation_model", rotation), ("mechanics_model", model)):
            if isinstance(value, dict):
                continue
            hint = ("读取mechanics_capabilities的/empty_model，使用gs_patch_candidate补入/mechanics_model"
                    if field == "mechanics_model" else "补入当前方案的/rotation_model动作输入；不要重建已存在的机制模型")
            issue = {"code": "action_event_model_missing", "path": field,
                     "message": "动作档案缺少" + field, "repair_hint": hint}
            issues.append(issue)
            emit_task_missing(missing, issue["code"], field, issue["message"],
                "review_action_event", consumer="action_event:batch", category="request_error", repair_hint=hint)
        records = [_profile_record(p, sorted(b["ability_id"] for b in bindings if b["profile_id"] == p["id"]), 0) for p in profiles]
        for record in records:
            record["issues"].extend(copy.deepcopy(issues))
        return None, records, []
    profile_ids = {p["id"] for p in profiles}
    if any(b["profile_id"] not in profile_ids for b in bindings):
        raise PlanError("动作绑定引用不存在的档案", "reference")
    if len({p["prototype_hit_id"] for p in profiles}) != len(profiles):
        raise PlanError("每个命中原型只能属于一个动作档案", "duplicate_delivery")
    ids = set()
    for root in (template, rotation, model):
        for _, item in _walk(root):
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                if item["id"].startswith(PREFIX):
                    raise PlanError("原生输入不得占用自动动作事件ID空间", "duplicate_delivery")
                ids.add(item["id"])
    actions = {a["id"]: a for a in rotation["actions"]}
    abilities = {a["id"]: a for a in rotation["abilities"]}
    if any(b["ability_id"] not in abilities for b in bindings):
        raise PlanError("档案绑定能力不存在", "reference")
    bound_abilities = {b["ability_id"] for b in bindings}
    bound_actions = {a["id"] for a in actions.values() if a["ability_id"] in bound_abilities}
    if any(o["action_id"] not in bound_actions for o in overrides):
        raise PlanError("动作命中例外须引用实际绑定动作", "reference")
    for collection in (model["hit_events"], model["hit_patterns"], model.get("application_events", []), rotation.get("action_hit_links", [])):
        if any(row.get("action_id") in bound_actions for row in collection):
            raise PlanError("绑定动作不能同时交付手工命中/附着/动作命中关联", "duplicate_delivery")
    owned = {p["prototype_hit_id"] for p in profiles}
    if any(e["hit_id"] in owned for e in model["hit_events"]) or any(p["prototype_hit_id"] in owned for p in model["hit_patterns"]):
        raise PlanError("动作档案原型不能被手工命中或模式复用", "duplicate_delivery")
    receipts, stages, sources = [], [], []
    override_by_id = {o["action_id"]: o for o in overrides}
    elemental = model.get("elemental_model") or {}

    for profile in profiles:
        dynamic = profile['kind'] == APPLICATION_KIND
        pid, proto = profile["id"], profile["prototype_hit_id"]
        ability_ids = sorted(b["ability_id"] for b in bindings if b["profile_id"] == pid)
        selected = sorted((a for a in actions.values() if a["ability_id"] in ability_ids), key=lambda a: a["id"])
        record = _profile_record(profile, ability_ids, len(selected))
        receipts.append(record)

        def pending(code, message, path=None, *, _pid=pid, _record=record):
            path = path or "action_event_profiles." + _pid
            if len(path) > 1024:
                path = path[:1000] + "…（路径已截断）"
            issue = {"code": code, "path": path, "message": message[:1024]}
            _record["issues"].append(issue)
            emit_task_missing(
                missing, code, path, issue["message"], "review_action_event",
                consumer="action_event_profile:" + _pid,
                category="knowledge_missing",
                blocking_scope="agent_materialization",
                repair_hint="核对该档案的动作实例、倍率、附着、时序、命中和确认指纹",
            )
            raise _Pending

        try:
            if not ability_ids or not selected:
                pending("action_event_no_actions", "档案没有实际动作；不能让孤立原型作为真实命中计算")
            metadata = [r for r in attack_metadata_compilations if r["action_event_profile_id"] == pid]
            owned_metadata = any(s["action_event_profile_id"] == pid for s in plan.get("attack_metadata_sources", []))
            if owned_metadata:
                if len(metadata) != 1 or metadata[0]["status"] != "ready":
                    pending("action_event_metadata_pending", "攻击元信息尚未完整装配；不能用缓存或手工缺省绕过来源")
                materialized = metadata[0]
                if (materialized["prototype_hit_id"] != proto
                        or materialized["application_profile_id"] != profile["application_profile_id"]
                        or materialized["metadata_dependency_fingerprint"] != fingerprint(materialized["semantic_dependency"])):
                    pending("action_event_metadata_identity", "攻击元信息目标或语义依赖指纹不一致")
                record.update(metadata_source_fingerprint=materialized["source_fingerprint"],
                              metadata_dependency_fingerprint=materialized["metadata_dependency_fingerprint"])
            hit_matches = [h for h in template["hits"] if h["id"] == proto]
            raw_matches = [h for h in plan["manual_template"]["hits"] if h["id"] == proto]
            params = [r for r in parameter_compilations if r["hit_id"] == proto]
            declarations = [r for r in plan.get("hit_parameter_sources", []) if r["hit_id"] == proto]
            if any(len(x) != 1 for x in (hit_matches, raw_matches, params, declarations)):
                pending("action_event_parameter_missing", "需要唯一的现有原型和数据库倍率来源")
            hit, raw_hit, param, declaration = hit_matches[0], raw_matches[0], params[0], declarations[0]
            actor = raw_hit["character"]
            record["actor"] = actor
            allowed_hit = {"id", "damage_type_ids", "t", "character", "skill", "hit_name", "description", "damage_system", "element", "scaling_stat", "count"}
            if raw_hit.keys() - allowed_hit or raw_hit["t"] != 0 or raw_hit.get("count", 1) != 1 or raw_hit["damage_system"] != "classic" or raw_hit.get("scaling_stat") != "atk":
                pending("action_event_prototype_scope", "原型须为t=0、单ATK/classic/单命中，省略倍率/反应/旧快照及额外执行字段")
            refs = _prototype_references(plan, template, proto)
            if refs:
                pending("action_event_prototype_selector", "旧Buff/match/filter仍引用模板原型，改用伤害类别或受支持的实际命中选择器：" + refs[0], refs[0])
            if param.get("status") != "ready":
                pending("action_event_parameter_pending", "原型的数据库倍率尚未就绪")
            selection = param["resolved_selection"]
            slot_types = {"normal": "action.normal_attack", "skill": "action.elemental_skill",
                          "burst": "action.elemental_burst"}
            matching_slots = set(selection.get("matching_slots", [])) & set(slot_types)
            level = param["level"].get("effective_level")
            if dynamic:
                if (level not in range(1, 16) or len(matching_slots) != 1
                        or declaration["scaling_stat"] != "atk"):
                    pending("action_event_skill_scope", "普通附着档案需要精确且唯一的动作槽、1–15级有效天赋与单ATK倍率")
                expected_action_type = slot_types[next(iter(matching_slots))]
                equipment = (plan.get("equipment_inputs", {}).get(actor) or {}).get("progression") or {}
                # Progression records predate the optional constellation field.
                # An omitted value is unknown, not evidence that contradicts the
                # typed database selection.  Only an explicit declaration can
                # establish a mismatch (the action-capacity path uses the same
                # compatibility rule).
                if ("constellation" in equipment
                        and equipment["constellation"] != selection["constellation"]):
                    pending("action_event_progression_mismatch", "动作来源命座与角色装备方案进度不一致")
            else:
                expected_action_type = "action.elemental_skill"
                if (selection["constellation"] != 0 or level != 10 or matching_slots != {"skill"}
                        or declaration["scaling_stat"] != "atk"):
                    pending("action_event_skill_scope", "冻结接触首片只接C0有效10级E槽的单ATK倍率")
            for aid in ability_ids:
                ability = abilities[aid]
                if (ability["character"] != actor or ability["action_type_id"] != expected_action_type
                        or ability.get("variant", "default") != profile["variant"]):
                    pending("action_event_ability_scope", "能力角色、动作槽或明确变体与原型档案不一致")
                for resource in plan.get("action_resource_sources", []):
                    if resource["ability_id"] == aid and (resource["version_line"] != declaration["version_line"] or resource["selection"]["character_id"] != selection["character_id"]):
                        pending("action_event_identity", "动作资源与命中来源身份不一致")
            expected_ruleset = DYNAMIC_SWIRL_RULESET if dynamic else FREEZE_RULESET
            if elemental.get("ruleset") != expected_ruleset or validate_elemental_model(elemental):
                pending("action_event_elemental_scope", "普通附着v2需要合法的gauge-swirl-dynamic-v3模型" if dynamic
                        else "需要已确认且合法的普通冻结实验参考模型")
            apps = [p for p in elemental["profiles"] if p["id"] == profile["application_profile_id"]]
            contacts = [] if dynamic else [p for p in elemental.get("contact_profiles", []) if p["id"] == profile["contact_profile_id"]]
            if len(apps) != 1 or (not dynamic and len(contacts) != 1):
                pending("action_event_application_missing", "附着/接触档案引用缺失或不唯一")
            app, contact = apps[0], None if dynamic else contacts[0]
            if (app["element"] != hit["element"] or app["application"] == "unknown" or app["status"] == "pending"
                    or (not dynamic and (contact["status"] == "pending" or contact["freeze_interaction"] != "none"))):
                pending("action_event_contact_scope", "元素不一致或附着/接触语义未核实")
            elemental_record = next((r for r in elemental_compilations if r["profile_id"] == app["id"]), None)
            if elemental_record and elemental_record["status"] != "ready":
                pending("action_event_application_pending", "被引用的数据库附着来源尚未就绪")
            scope, captures = build_single_action_scope(profile, raw_hit=raw_hit, hit=hit,
                parameter=param, declaration=declaration, application=app, contact=contact,
                metadata=metadata[0] if owned_metadata else None, elemental_record=elemental_record,
                reaction_compilations=reaction_compilations, selected_actions=selected,
                rotation=rotation, model=model)
            dependencies = scope["dependencies"]
            fp = fingerprint(scope)
            record.update(profile_fingerprint=fp, source_dependency_fingerprint=fingerprint(dependencies),
                          required_confirmation={"accepted_profile_fingerprint": fp})
            if profile["calibration"]["status"] == "pending" or profile["default_landed"] is None:
                pending("action_event_calibration_pending", "时序用途或默认命中尚未确认")
            if profile["accepted_profile_fingerprint"] != fp:
                pending("action_event_confirmation", "请核对当前档案、实际引用资料和实验参考范围，再接受档案指纹")
            offset, period = _ticks(profile["calibration"]["offset_seconds"]), _ticks(rotation.get("period"))
            switch_times = sorted((_ticks(other.get("start")), other["order"]) for other in actions.values()
                if abilities[other["ability_id"]]["action_type_id"] == "action.switch"
                and (other["repeat"]["mode"] != "cycles" or 0 in other["repeat"].get("cycles", [])))
            local = []
            for action in selected:
                aid = action["id"]
                if action["repeat"] != {"mode": "once"}:
                    pending("action_event_repeat", "绑定动作只接受once；多轮请显式输入多轮动作", "rotation_model.actions." + aid)
                start, end = _ticks(action.get("start")), _ticks(action.get("occupation_end"))
                tick = start + offset
                time = round(action["start"] + profile["calibration"]["offset_seconds"], 6)
                if _ticks(time) != tick or not start <= tick <= end or tick >= period:
                    pending("action_event_time_scope", "发射必须与实际投影微秒一致，位于占用期内且严格早于窗口右端", "rotation_model.actions." + aid)
                next_switch = bisect_right(switch_times, (start, action["order"]))
                if next_switch < len(switch_times) and switch_times[next_switch][0] < end:
                    pending("action_event_cancel", "首片不支持绑定动作的实际提前切人/取消", "rotation_model.actions." + aid)
                override = override_by_id.get(aid, {})
                landed = override.get("landed", profile["default_landed"])
                if override.get("status") == "pending" or landed is None:
                    pending("action_event_override_pending", "本轴命中例外尚未确认", "action_event_overrides." + aid)
                pattern_id = PREFIX + fingerprint([profile["kind"], aid, 'primary-application' if dynamic else EMISSION])
                hit_id = pattern_id + ":1"
                if pattern_id in ids or hit_id in ids:
                    raise PlanError("派生命中ID冲突", "duplicate_delivery")
                ids.update((pattern_id, hit_id))
                generated = {"action_id": aid, "ability_id": action["ability_id"], "pattern_id": pattern_id,
                    "hit_id": hit_id, "prototype_hit_id": proto, "t": time,
                    "order": override.get(
                        "order", _action_start_post_order(rotation, action) if offset == 0 else 0),
                    "landed": landed, "offset_ticks": offset, "expected_time_ticks": tick}
                occurrence = profile.get("topology_occurrence")
                if occurrence is not None:
                    generated.update(
                        occurrence_id=occurrence["occurrence_id"],
                        step_id=occurrence["occurrence_id"],
                        component_id=occurrence["component_id"],
                        ordinal=occurrence["ordinal"],
                        topology_order=occurrence["topology_order"],
                        terminal=occurrence["terminal"],
                    )
                if override:
                    generated["override"] = copy.deepcopy(override)
                pattern = {"id": pattern_id, "prototype_hit_id": proto, "action_id": aid,
                    "offsets": [profile["calibration"]["offset_seconds"]], "orders": [generated["order"]],
                    "application": {"profile_id": app["id"], "target_id": elemental["target"]["id"], "landed": landed}}
                if not dynamic:
                    pattern['contact'] = {'kind': 'profile', 'profile_id': contact['id']}
                if profile["self_sampling"] == "action_start":
                    pattern["self_snapshot_event"] = aid
                elif profile["self_sampling"] == "action_capture":
                    from .action_capture import resolve_action_capture
                    capture = resolve_action_capture(action, tick, generated["order"], captures)
                    pattern["self_snapshot_event"] = capture["id"]
                    generated.update(self_snapshot_event=capture["id"], snapshot_capture_fingerprint=fingerprint(capture))
                local.append((generated, pattern))
            stages.append((record, local))
        except _Pending:
            pass
        except (ValueError, TypeError, KeyError, IndexError, InvalidOperation) as exc:
            if isinstance(exc, PlanError):
                raise
            try:
                pending("action_event_incomplete", str(exc))
            except _Pending:
                pass
    if len(stages) == len(receipts):
        try:
            existing = _time_events(rotation, model, template)
            generated_all = [g for _, local in stages for g, _ in local]
            all_times = [*existing, *((g["expected_time_ticks"], g["order"], g["hit_id"]) for g in generated_all)]
            if len(all_times) > 40000:
                raise ValueError("动作派发后事件数超过40000")
            by_time = Counter(tick for tick, _, _ in all_times)
            by_time_order = Counter((tick, order) for tick, order, _ in all_times)
            for record, local in stages:
                for g, _ in local:
                    action = actions[g["action_id"]]
                    same_tick_peers = [event_id for tick, _, event_id in all_times
                                       if tick == g["expected_time_ticks"] and event_id != g["hit_id"]]
                    implicit_action_start = _is_unambiguous_action_start(
                        rotation, action, g, same_tick_peers)
                    if (by_time[g["expected_time_ticks"]] > 1
                            and "order" not in override_by_id.get(g["action_id"], {})
                            and not implicit_action_start):
                        raise ValueError("同微秒存在其他事件；需要逐动作order例外：" + g["action_id"])
                    if by_time_order[(g["expected_time_ticks"], g["order"])] > 1:
                        raise ValueError("派生命中与已有事件同微秒order冲突：" + g["action_id"])
        except (ValueError, KeyError, TypeError) as exc:
            issue = {"code": "action_event_order_or_limit", "path": "action_event_bindings", "message": str(exc)[:1024]}
            receipts[0]["issues"].append(issue)
            emit_task_missing(
                missing, issue["code"], issue["path"], issue["message"],
                "review_action_event", consumer="action_event:batch",
                category="engine_capability_missing", impact="high",
                blocking_scope="engine_capability",
                repair_hint="解决同微秒顺序冲突或缩小当前动作事件批次",
            )
    if any(r["issues"] for r in receipts):
        _bound_pending_records(receipts)
        return None, receipts, []
    compiled = copy.deepcopy(model)
    for record, local in stages:
        record["generated"] = [g for g, _ in local]
        record["stats"].update(generated_hits=len(local), landed_hits=sum(g["landed"] for g, _ in local),
                               missed_hits=sum(not g["landed"] for g, _ in local))
        record["status"] = "ready"
        record["instance_fingerprint"] = fingerprint({"profile_fingerprint": record["profile_fingerprint"],
            "generated": record["generated"], "actions": [actions[g["action_id"]] for g, _ in local], "period": rotation["period"]})
        compiled["hit_patterns"].extend(pattern for _, pattern in local)
        sources.append({"kind": "action_event_profile", "profile_id": record["profile_id"],
            "profile_fingerprint": record["profile_fingerprint"], "source_dependency_fingerprint": record["source_dependency_fingerprint"]})
    if len(json.dumps([receipts, sources], ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()) > MAX_RECEIPT_BYTES:
        for record in receipts:
            record.update(status="pending", generated=[], instance_fingerprint=None)
            record["stats"].update(generated_hits=0, landed_hits=0, missed_hits=0)
        issue = {"code": "action_event_receipt_limit", "path": "action_event_profiles", "message": "动作派发回执超过2MiB；未交付部分模型"}
        receipts[0]["issues"].append(issue)
        emit_task_missing(
            missing, issue["code"], issue["path"], issue["message"],
            "review_action_event", consumer="action_event:batch",
            category="engine_capability_missing", impact="critical",
            blocking_scope="engine_capability", retryable=False,
            repair_hint="缩小动作事件批次或扩展受审回执承载能力",
        )
        _bound_pending_records(receipts)
        return None, receipts, []
    return compiled, receipts, sources


def compile_action_events(plan, template, *, parameter_compilations, elemental_compilations,
                          reaction_compilations, missing, attack_metadata_compilations=(),
                          periodic_compilations=(), resource_compilations=()):
    from .finite_action_events import compile_finite_action_events
    from .finite_action_schema import KIND as FINITE_APPLICATION_KIND
    from .periodic_events import compile_periodic_events, validate_combined_times
    from .periodic_schema import MULTI_KINDS, profile_channels

    kwargs = {"parameter_compilations": parameter_compilations, "elemental_compilations": elemental_compilations,
              "reaction_compilations": reaction_compilations, "missing": missing}
    grouped_kinds = (*MULTI_KINDS, FINITE_APPLICATION_KIND)
    if not any(p["kind"] in grouped_kinds for p in plan.get("action_event_profiles", [])):
        return _compile_single_action_events(plan, template, attack_metadata_compilations=attack_metadata_compilations, **kwargs)
    validate_action_event_fields(plan)
    profiles = plan["action_event_profiles"]
    model, rotation = plan.get("mechanics_model") or {}, plan.get("rotation_model") or {}
    protos = [c["prototype_hit_id"] for p in profiles for c in profile_channels(p)]
    apps = [c["application_profile_id"] for p in profiles for c in profile_channels(p)]
    if len(set(protos)) != len(protos) or len(set(apps)) != len(apps):
        raise PlanError("动作档案原型/附着槽位须唯一所有者", "duplicate_delivery")
    for root in (template, model, rotation):
        if any(isinstance(v, dict) and isinstance(v.get("id"), str) and v["id"].startswith(PREFIX) for _, v in _walk(root)):
            raise PlanError("原生不得占用自动事件ID空间", "duplicate_delivery")
    abilities = {a["id"] for a in rotation.get("abilities", [])}
    bound = {b["ability_id"] for b in plan["action_event_bindings"]}
    if bound - abilities or any(b["profile_id"] not in {p["id"] for p in profiles} for b in plan["action_event_bindings"]):
        raise PlanError("动作档案绑定引用不存在", "reference")
    actual = {a["id"] for a in rotation.get("actions", []) if a["ability_id"] in bound}
    if any(o["action_id"] not in actual for o in plan["action_event_overrides"]):
        raise PlanError("命中例外须引用实际绑定动作", "reference")
    for collection in (model.get("hit_events", []), model.get("hit_patterns", []), model.get("application_events", []), rotation.get("action_hit_links", [])):
        if any(e.get("action_id") in actual for e in collection):
            raise PlanError("已绑定动作不能重复交付手工命中或附着", "duplicate_delivery")
    if (any(e["hit_id"] in protos for e in model.get("hit_events", []))
            or any(e["prototype_hit_id"] in protos for e in model.get("hit_patterns", []))):
        raise PlanError("共享原型不能成为独立事件或手工pattern", "duplicate_delivery")
    single = copy.deepcopy(plan)
    single["action_event_profiles"] = [p for p in profiles if p["kind"] not in grouped_kinds]
    single_ids = {p["id"] for p in single["action_event_profiles"]}
    single["action_event_bindings"] = [b for b in plan["action_event_bindings"] if b["profile_id"] in single_ids]
    single_bound = {b["ability_id"] for b in single["action_event_bindings"]}
    single_actions = {a["id"] for a in rotation.get("actions", []) if a["ability_id"] in single_bound}
    single["action_event_overrides"] = [o for o in plan["action_event_overrides"] if o["action_id"] in single_actions]
    s_model, s_records, s_sources = _compile_single_action_events(single, template, attack_metadata_compilations=attack_metadata_compilations, **kwargs)
    p_model, p_records, p_sources = compile_periodic_events(plan, template, periodic_compilations=periodic_compilations,
                                                          resource_compilations=resource_compilations, **kwargs)
    f_model, f_records, f_sources = compile_finite_action_events(
        plan, template, periodic_compilations=periodic_compilations, **kwargs)
    records = s_records + p_records + f_records
    sources = s_sources + p_sources + f_sources
    try:
        if any(r["status"] != "ready" for r in records):
            raise ValueError("本次动作档案仍有待处理项，所有自动生成事件原子撤回")
        validate_combined_times(rotation, model, template, records, plan["action_event_overrides"])
        if len(json.dumps([records, sources], ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()) > MAX_RECEIPT_BYTES:
            raise ValueError("全部动作派发回执超过2MiB")
    except ValueError as exc:
        for record in records:
            record.update(status="pending", generated=[], instance_fingerprint=None)
            record.pop("instance_payload", None)
            record["stats"].update(generated_hits=0, landed_hits=0, missed_hits=0)
        issue = {"code": "periodic_action_group_pending", "path": "action_event_profiles", "message": str(exc)}
        records[0]["issues"].append(issue)
        emit_task_missing(
            missing, issue["code"], issue["path"], issue["message"],
            "review_action_event", consumer="action_event:batch",
            category="knowledge_missing", blocking_scope="agent_materialization",
            repair_hint="修复各档案缺项与组合时序后重新原子展开整组动作事件",
        )
        if len(json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode()) > MAX_RECEIPT_BYTES:
            for record in records:
                record.pop("scope", None)
        _bound_pending_records(records)
        return None, records, []
    compiled = copy.deepcopy(model)
    original_count = len(model["hit_patterns"])
    if s_model:
        compiled["hit_patterns"].extend(s_model["hit_patterns"][original_count:])
    if p_model:
        compiled["hit_patterns"].extend(p_model["hit_patterns"][original_count:])
    if f_model:
        compiled["hit_patterns"].extend(f_model["hit_patterns"][original_count:])
    return compiled, records, sources
