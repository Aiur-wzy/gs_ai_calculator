"""Finite calibrated periodic contacts reuse the ordinary mechanics queue."""
from __future__ import annotations

import copy
from bisect import bisect_right
from collections import Counter, defaultdict

from genshin_app.task_capability_producer import emit_task_missing
from genshin_engine.elemental import validate_elemental_model
from genshin_engine.elemental_schema import (
    DYNAMIC_SWIRL_RULESET,
    FREEZE_RULESET,
    REFERENCE_CONTRACT,
)

from .files import fingerprint
from .finite_action_schema import KIND as FINITE_APPLICATION_KIND
from .periodic_schema import (
    APPLICATION_CONTRACT,
    APPLICATION_KIND,
    DERIVED_COUNT_POLICY,
    KIND,
    MULTI_KINDS,
    REFRESH_OPERATION,
    REFRESH_SNAPSHOT_POLICY,
    REPLACEMENT_POLICY,
)
from .schema import PlanError


def _one(rows, predicate, message):
    found = [row for row in rows if predicate(row)]
    if len(found) != 1:
        raise ValueError(message)
    return found[0]


def _refresh_declared_target(declaration):
    """Return the public target identity without exposing private profile IDs."""
    if "action_event_profile_id" in declaration:
        return {"kind": "action_event_profile",
                "action_event_profile_id": declaration["action_event_profile_id"]}
    return {"kind": "action_dispatch_ability",
            "action_dispatch_ability_id": declaration["action_dispatch_ability_id"]}


def _refresh_targets_profile(declaration, profile_id, ability_ids):
    if "action_event_profile_id" in declaration:
        return declaration["action_event_profile_id"] == profile_id
    return declaration["action_dispatch_ability_id"] in ability_ids


def periodic_profile_scope(profile, source, template, model, parameters, elemental_sources, reactions):
    """Portable paired semantic units; raw IDs never substitute for their content."""
    from .action_events import _source_identity, _ticks, _without_id

    if source.get("status") != "ready" or source.get("periodic_dependency_fingerprint") != fingerprint(source.get("semantic_dependency")):
        raise ValueError("周期来源尚未完整装配或依赖指纹不符")
    elemental = model.get("elemental_model") or {}
    finite = profile["kind"] == FINITE_APPLICATION_KIND
    dynamic = profile["kind"] in (APPLICATION_KIND, FINITE_APPLICATION_KIND)
    expected_ruleset = DYNAMIC_SWIRL_RULESET if dynamic else FREEZE_RULESET
    if elemental.get("ruleset") != expected_ruleset or validate_elemental_model(elemental):
        raise ValueError("周期派发的元素模型与档案类别不匹配或尚未通过校验")
    if not finite:
        if profile["lifecycle"]["status"] == "pending" or profile["lifecycle"]["precision_seconds"] is None:
            raise ValueError("生命周期校准或记录精度未知")
        lifecycle = profile["lifecycle"]
        replacement = lifecycle.get("replacement_policy")
        if any(lifecycle[k] is not True for k in ("owner_and_target_survive", "no_interrupt")):
            raise ValueError("存活/无中断场景未成立")
        if replacement is None:
            if lifecycle["no_refresh_replacement_or_early_despawn"] is not True:
                raise ValueError("无刷新替换或提前销毁场景未成立")
        elif (replacement != REPLACEMENT_POLICY
              or lifecycle["no_refresh_replacement_or_early_despawn"] is not False
              or lifecycle.get("no_early_despawn") is not True):
            raise ValueError("刷新替换策略或独立的无提前消失证据未成立")
        if not 0 <= _ticks(profile["lifecycle"]["establishment_offset_seconds"]) <= _ticks(profile["lifecycle"]["instance_end_offset_seconds"]):
            raise ValueError("生命周期建立与结束时序无效")
    units, actors, skill_keys = [], set(), set()
    for channel in profile["channels"]:
        cid, proto = channel["channel_id"], channel["prototype_hit_id"]
        row = _one(source["channels"], lambda r, cid=cid: r["channel_id"] == cid, "周期来源通道缺失")
        hit = _one(template["hits"], lambda h, proto=proto: h["id"] == proto, "周期原型缺失")
        param = _one(parameters, lambda p, proto=proto: p["hit_id"] == proto, "周期倍率回执不唯一")
        app = _one(elemental["profiles"], lambda a, channel=channel: a["id"] == channel["application_profile_id"], "周期附着档案不唯一")
        contact = None if dynamic else _one(
            elemental.get("contact_profiles", []),
            lambda c, channel=channel: c["id"] == channel["contact_profile_id"],
            "周期接触档案不唯一")
        fields = row["compiled_fields"]
        mode = fields.get("application_mode")
        matching_sources = [e for e in elemental_sources if e.get("profile_id") == app["id"]]
        e_source = matching_sources[0] if len(matching_sources) == 1 else None
        classification = row["projection"]["classification"]["value"]
        if (param.get("status") != "ready" or hit.get("damage_type_ids") != [classification]
                or hit.get("damage_system") != "classic" or hit.get("scaling_stat") != "atk" or hit.get("count", 1) != 1
                or mode not in ("sequence", "no_icd", "no_application")
                or app["status"] == "pending"
                or (not dynamic and (contact["status"] == "pending"
                                     or contact["freeze_interaction"] != "none"))):
            raise ValueError("周期原型、倍率、附着或档案类别依赖未满足当前范围")
        if (row["prototype_hit_id"] != proto or row["application_profile_id"] != app["id"]
                or hit["element"] != fields["element"] or hit["multiplier"] != fields["multiplier"]
                or app["element"] != fields["element"]):
            raise ValueError("实际物化值与周期来源不一致")
        if mode == "sequence":
            source_counter_tag = (e_source or {}).get("counter_tag", (e_source or {}).get("shared_group"))
            if (len(matching_sources) != 1 or e_source.get("status") != "ready"
                    or app.get("application") != "apply" or app.get("gauge_units") != fields.get("gauge_units")
                    or app.get("icd") != e_source.get("compiled_icd")
                    or source_counter_tag != fields.get("counter_tag")
                    or fields.get("rule_group") is None):
                raise ValueError("周期序列附着与计数标签/规则组来源不一致")
        elif mode == "no_icd":
            if (matching_sources or app.get("application") != "apply"
                    or app.get("gauge_units") != fields.get("gauge_units")
                    or app.get("icd") != {"kind": "none"}
                    or fields.get("counter_tag") is not None or fields.get("rule_group") is not None):
                raise ValueError("周期无ICD附着不得创建计数器或规则组")
        elif (matching_sources or app.get("application") != "none"
                or "gauge_units" in app or "icd" in app
                or fields.get("counter_tag") is not None or fields.get("rule_group") is not None):
            raise ValueError("周期无附着攻击不得消费GU或创建ICD状态")
        selection = param["resolved_selection"]
        level = param["level"].get("effective_level")
        if (selection != source["query"]["selection"]
                or (dynamic and level not in range(1, 16))
                or (not dynamic and (selection["constellation"] != 0 or level != 10))
                or {"damage.normal_attack": "normal", "damage.charged_attack": "normal",
                    "damage.plunging_attack": "normal", "damage.elemental_skill": "skill",
                    "damage.elemental_burst": "burst"}.get(classification)
                    not in selection.get("matching_slots", [])):
            raise ValueError("周期通道不是同一精确天赋参数上下文或档案版本范围")
        actors.add(hit["character"])
        skill_keys.add((selection["character_id"], selection["skill_depot_id"], selection["skill_id"]))
        schedule = channel.get("schedule")
        if channel["default_landed"] is None:
            raise ValueError("逐组件默认命中尚未确认")
        if not finite:
            if schedule["status"] == "pending" or schedule["precision_seconds"] is None:
                raise ValueError("逐通道调度或记录精度尚未确认")
            if schedule["kind"] == "periodic":
                timer = row["projection"]["timer"]
                count = schedule["count"]
                if (row["channel_kind"] != "periodic_callback_attack"
                        or (count is not None and (type(count) is not int or not 1 <= count <= 256))
                        or (count is None and schedule.get("derive_count") != DERIVED_COUNT_POLICY)
                        or timer.get("status") != "ready" or type(timer.get("ticks")) is not int
                        or not 0 < timer["ticks"] <= 10**15 or _ticks(timer["seconds"]) != timer["ticks"]):
                    raise ValueError("周期次数或精确来源timer无效")
                _ticks(schedule["first_offset_seconds"])
            elif schedule["kind"] == "explicit_offsets":
                offsets = schedule.get("offsets_seconds")
                if (row["channel_kind"] != "periodic_callback_attack"
                        or not isinstance(offsets, list) or not 1 <= len(offsets) <= 256):
                    raise ValueError("不等间隔序列需要1–256个已核实的周期来源偏移")
                ticks = [_ticks(value) for value in offsets]
                if ticks != sorted(ticks) or len(ticks) != len(set(ticks)):
                    raise ValueError("不等间隔序列偏移须严格递增且微秒位置唯一")
            elif row["channel_kind"] != "ability_start_attack":
                raise ValueError("single不能覆盖周期来源类型")
            else:
                _ticks(schedule["offset_seconds"])
        semantic_hit = {k: hit.get(k, 1 if k == "count" else None) for k in
                        ("damage_type_ids", "damage_system", "element", "scaling_stat", "count", "multiplier")}
        candidate_dependency = _one(source["query"]["semantic_dependency"]["selected_candidates"],
            lambda d, row=row: d["candidate_id"] == row["candidate_id"], "周期语义候选缺失")
        unit = {"candidate_id": row["candidate_id"], "projection_fingerprint": candidate_dependency["projection_fingerprint"],
            "prototype": semantic_hit, "application": _without_id(app),
            "parameter": {**_source_identity(param), "selection": copy.deepcopy(selection), "level": level,
                "param_index": param["param_index"], "purpose": param["declared_purpose"],
                "interpretation_status": param["interpretation_status"], "interpretation_basis": param["interpretation_basis"]},
            "default_landed": channel["default_landed"], "self_sampling": channel["self_sampling"]}
        if schedule is not None:
            unit["schedule"] = copy.deepcopy(schedule)
        cadence = channel.get("application_cadence")
        if cadence is not None:
            if (cadence.get("kind") != "every_n_landed_hits"
                    or cadence.get("scope") != "channel_generation"
                    or cadence.get("status") == "pending"
                    or type(cadence.get("period_hits")) is not int
                    or type(cadence.get("first_attempt_ordinal")) is not int
                    or not 1 <= cadence["first_attempt_ordinal"] <= cadence["period_hits"] <= 256
                    or schedule["kind"] != "periodic"):
                raise ValueError("逐通道附着节律尚未形成可执行的已核实有限规则")
            unit["application_cadence"] = copy.deepcopy(cadence)
        if not dynamic:
            unit["contact"] = _without_id(contact)
        units.append(unit)
    if len(actors) != 1 or len(skill_keys) != 1 or next(iter(actors)) != source["actor"]:
        raise ValueError("周期档案跨角色或技能身份")
    reaction_sources = [{"version_line": r["version_line"], "snapshot_id": (r.get("snapshot") or {}).get("snapshot_id"),
        "profiles": [q["content_fingerprint"] for q in r.get("profiles", [])], "compiled_icd": r.get("compiled_icd"),
        "binding_basis": r.get("binding_basis")} for r in reactions]
    result = {"compiler": ("calibrated-finite-actions-v1" if finite else
            "calibrated-periodic-actions-v2" if dynamic else "calibrated-periodic-actions-v1"),
        "kind": profile["kind"], "variant": profile["variant"],
        "channels": sorted(units, key=lambda r: r["candidate_id"]),
        "dependencies": copy.deepcopy(source["semantic_dependency"]), "ruleset": expected_ruleset,
        "reaction_sources": reaction_sources}
    if not finite:
        result["lifecycle"] = copy.deepcopy(profile["lifecycle"])
    if dynamic:
        result["application_contract"] = copy.deepcopy(APPLICATION_CONTRACT)
    else:
        result["reference_fingerprint"] = REFERENCE_CONTRACT["contract_fingerprint"]
    return result


def patterns_for_record(record):
    """Reconstruct exact delivered patterns without a DB or gameplay inference."""
    channels = {c["channel_id"]: c for c in record["channels"]}
    result = []
    for event in record["generated"]:
        channel = channels[event["channel_id"]]
        pattern = {"id": event["pattern_id"], "prototype_hit_id": event["prototype_hit_id"], "action_id": event["action_id"],
            "offsets": [event["offset_ticks"] / 1000000], "orders": [event["order"]],
            "application": {"profile_id": channel["application_profile_id"], "target_id": record["target_id"], "landed": event["landed"]}}
        if "application_attempted" in event:
            pattern["application"]["attempted"] = event["application_attempted"]
        if record["kind"] == KIND:
            pattern["contact"] = {"kind": "profile", "profile_id": channel["contact_profile_id"]}
        if channel["self_sampling"] == "action_start":
            pattern["self_snapshot_event"] = event["action_id"]
        elif channel["self_sampling"] == "action_capture":
            pattern["self_snapshot_event"] = event["self_snapshot_event"]
        result.append(pattern)
    return result


def periodic_instance_payload(*, profile, record, selected, overrides, period,
                              refresh_declarations=()):
    """Build the closed, replayable payload for one materialized lifecycle.

    Both the compiler and downstream contact-graph verifier use this helper so
    newly added lifecycle fields cannot silently split their receipt contracts.
    Values derived by the lifecycle projector remain part of the signed
    payload, while plan-owned actions, channels, overrides, refresh
    declarations and the rotation window are supplied afresh by the caller.
    """
    payload = {
        "profile_fingerprint": record["profile_fingerprint"],
        "channels": copy.deepcopy(profile["channels"]),
        "generated": copy.deepcopy(record["generated"]),
        "actions": copy.deepcopy(selected),
        "overrides": [copy.deepcopy(overrides[action["id"]])
                      for action in selected if action["id"] in overrides],
        "period": period,
        "lifecycle_projection": copy.deepcopy(record["lifecycle_projection"]),
    }
    declarations = list(refresh_declarations)
    if declarations:
        payload.update(
            refresh_declarations=copy.deepcopy(declarations),
            refresh_triggers=copy.deepcopy(record["refresh_triggers"]),
            refresh_contract=copy.deepcopy(record["refresh_contract"]),
        )
    if profile["lifecycle"].get("replacement_policy") == REPLACEMENT_POLICY:
        payload.update(
            replacements=copy.deepcopy(record["replacements"]),
            replacement_contract=copy.deepcopy(record["replacement_contract"]),
        )
    return payload


def compile_periodic_events(plan, template, *, parameter_compilations, elemental_compilations,
        reaction_compilations, periodic_compilations, resource_compilations=(), missing):
    from .action_events import (
        PREFIX,
        _action_start_post_order,
        _prototype_references,
        _ticks,
    )

    profiles = [p for p in plan["action_event_profiles"] if p["kind"] in MULTI_KINDS]
    if not profiles:
        return None, [], []
    model, rotation = plan.get("mechanics_model"), plan.get("rotation_model")
    receipts, sources, stages = [], [], []
    actions = {a["id"]: a for a in (rotation or {}).get("actions", [])}
    abilities = {a["id"]: a for a in (rotation or {}).get("abilities", [])}
    overrides = {o["action_id"]: o for o in plan["action_event_overrides"]}
    all_known = {}
    # Explicit source identities only; unrelated manual E/Q need no invented identity.
    for rec in resource_compilations:
        if rec.get("resolved_selection"):
            all_known[rec["ability_id"]] = rec["resolved_selection"]
    for source in periodic_compilations:
        for bind in plan["action_event_bindings"]:
            if bind["profile_id"] == source["action_event_profile_id"] and (source.get("query") or {}).get("selection"):
                all_known[bind["ability_id"]] = source["query"]["selection"]
    for binding in plan.get("effect_bindings", []):
        for ref in (binding.get("upstream_recipe") or {}).get("ability_refs", []):
            all_known.setdefault(ref["ability_id"], {"skill_id": ref["skill_id"]})
    for profile in profiles:
        pid = profile["id"]
        ability_ids = sorted(b["ability_id"] for b in plan["action_event_bindings"] if b["profile_id"] == pid)
        selected = sorted((a for a in actions.values() if a["ability_id"] in ability_ids),
                          key=lambda a: (_ticks(a["start"]), a["order"], a["id"]))
        refresh_declarations = [copy.deepcopy(row) for row in plan.get("periodic_refresh_triggers", [])
                                if _refresh_targets_profile(row, pid, ability_ids)]
        dynamic = profile["kind"] == APPLICATION_KIND
        record = {"profile_id": pid, "kind": profile["kind"],
            "compiler": "calibrated-action-events-v2" if dynamic else "calibrated-action-events-v1", "actor": None,
            "ability_ids": ability_ids, "status": "pending", "dispatch_mode": "calibrated", "skill_coverage": "partial",
            "resource_coverage": "not_generated", "gameplay_interpretation": "declared_not_verified",
            "action_creation_association": "unproven", "dispatch_semantics_status": "not_proven",
            "profile_fingerprint": None, "required_confirmation": None, "accepted_profile_fingerprint": profile["accepted_profile_fingerprint"],
            "source_dependency_fingerprint": None, "instance_fingerprint": None, "generated": [], "channels": copy.deepcopy(profile["channels"]),
            "lifecycle": copy.deepcopy(profile["lifecycle"]), "scope": None, "issues": [],
            "stats": {"matched_actions": len(selected), "planned_hits": 0, "generated_hits": 0, "landed_hits": 0, "missed_hits": 0}}
        receipts.append(record)
        confirmation_only = False
        try:
            if not model or not rotation or not selected:
                raise ValueError("周期档案需要完整模型与实际动作，不能只执行孤立原型")
            source = _one(periodic_compilations, lambda r, pid=pid: r["action_event_profile_id"] == pid, "周期来源回执缺失")
            scope = periodic_profile_scope(profile, source, template, model, parameter_compilations, elemental_compilations, reaction_compilations)
            captures = {}
            if any(c["self_sampling"] == "action_capture" for c in profile["channels"]):
                from .action_capture import prepare_action_captures
                captures, scope["snapshot_capture_calibrations"] = prepare_action_captures(selected, rotation, model)
            actor, selection = source["actor"], source["query"]["selection"]
            record.update(actor=actor, scope=scope, target_id=model["elemental_model"]["target"]["id"],
                profile_fingerprint=fingerprint(scope), source_dependency_fingerprint=fingerprint(scope["dependencies"]),
                required_confirmation={"accepted_profile_fingerprint": fingerprint(scope)})
            for channel in profile["channels"]:
                raw = _one(plan["manual_template"]["hits"], lambda h, channel=channel: h["id"] == channel["prototype_hit_id"], "原型缺失")
                allowed = {"id", "damage_type_ids", "t", "character", "skill", "hit_name", "description", "damage_system", "element", "scaling_stat", "count"}
                if raw.keys() - allowed or raw["t"] != 0 or _prototype_references(plan, template, raw["id"]):
                    raise ValueError("周期原型须为t=0、无额外反应/快照字段且不被手工选择器引用")
            for aid in ability_ids:
                ability = abilities[aid]
                ability_slot = {"action.normal_attack": "normal", "action.charged_attack": "normal",
                    "action.plunging_attack": "normal", "action.elemental_skill": "skill",
                    "action.elemental_burst": "burst"}.get(ability["action_type_id"])
                if (ability["character"] != actor or ability_slot not in selection.get("matching_slots", [])
                        or ability.get("variant") != profile["variant"]):
                    raise ValueError("周期绑定能力的角色/天赋槽/明确变体不符")
                for resource in plan.get("action_resource_sources", []):
                    if resource["ability_id"] == aid and (resource["version_line"] != source["version_line"]
                            or resource["selection"]["character_id"] != selection["character_id"]
                            or resource["selection"]["constellation"] != selection["constellation"]):
                        raise ValueError("周期来源与显式资源角色/命座/版本线冲突")
            # Preflight all count/tick products before constructing any per-hit rows.
            establish = _ticks(profile["lifecycle"]["establishment_offset_seconds"])
            end_offset = _ticks(profile["lifecycle"]["instance_end_offset_seconds"])
            specs = []
            source_channels = {c["channel_id"]: c for c in source["channels"]}
            for channel in profile["channels"]:
                schedule = channel["schedule"]
                periodic = schedule["kind"] == "periodic"
                explicit = schedule["kind"] == "explicit_offsets"
                offsets = ([_ticks(value) for value in schedule["offsets_seconds"]]
                           if explicit else None)
                first = (offsets[0] if explicit else
                         _ticks(schedule["first_offset_seconds"] if periodic
                                else schedule["offset_seconds"]))
                interval = (source_channels[channel["channel_id"]]["projection"]["timer"]["ticks"]
                            if periodic else 0)
                count = len(offsets) if explicit else schedule["count"] if periodic else 1
                if periodic and count is None:
                    if schedule.get("derive_count") != DERIVED_COUNT_POLICY or first > end_offset:
                        raise ValueError("按实例结束派生次数的策略或首击时点无效")
                    span = end_offset - first
                    if span % interval:
                        raise ValueError("实例结束、首击与周期间隔不能整除，拒绝猜测末次命中")
                    count = span // interval + 1
                    if not 1 <= count <= 256:
                        raise ValueError("按实例结束派生的周期次数超出1–256")
                specs.append((channel, first, count, interval, offsets))
            period = _ticks(rotation["period"])
            switches = sorted((_ticks(a["start"]), a["order"]) for a in actions.values() if abilities[a["ability_id"]]["action_type_id"] == "action.switch"
                and (a["repeat"]["mode"] != "cycles" or 0 in a["repeat"].get("cycles", [])))
            replacement_policy = profile["lifecycle"].get("replacement_policy")
            has_application_cadence = any(
                channel.get("application_cadence") is not None for channel in profile["channels"])
            selected_ids = {action["id"] for action in selected}
            replacement_cutoffs = {}
            replacements = []
            for action in selected:
                start, occupation = _ticks(action["start"]), _ticks(action["occupation_end"])
                if action["repeat"] != {"mode": "once"} or start + establish > occupation or start + end_offset >= period:
                    raise ValueError("周期动作必须once，建立不晚于占用结束，实例绝对结束严格早于输入窗口")
                index = bisect_right(switches, (start, action["order"]))
                if index < len(switches) and switches[index][0] < occupation:
                    raise ValueError("周期建立动作存在实际提前切人/取消")
                for other in actions.values():
                    if other["id"] == action["id"] or abilities[other["ability_id"]]["character"] != actor:
                        continue
                    identity = all_known.get(other["ability_id"], {})
                    other_start = _ticks(other["start"])
                    if other["id"] in selected_ids:
                        other_start += establish
                    if identity.get("skill_id") == selection["skill_id"] and start + establish <= other_start <= start + end_offset:
                        if replacement_policy == REPLACEMENT_POLICY and other["id"] in selected_ids:
                            continue
                        raise ValueError("同角色同精确技能的另一次动作与实例生命周期重叠，含结束同刻")
                for _, first, n, interval, offsets in specs:
                    last = offsets[-1] if offsets is not None else first + (n - 1) * interval
                    if first < establish or last > end_offset:
                        raise ValueError("计划接触超出显式建立/结束区间")
            generation_inputs = selected
            refresh_resolutions = []
            if not refresh_declarations:
                for index, action in enumerate(selected[:-1]):
                    next_action = selected[index + 1]
                    current_start = _ticks(action["start"])
                    next_establish = _ticks(next_action["start"]) + establish
                    if next_establish <= current_start + end_offset:
                        if replacement_policy != REPLACEMENT_POLICY:
                            raise ValueError("同一周期档案的动作实例生命周期重叠")
                        if next_establish <= current_start + establish:
                            raise ValueError("刷新实例的建立时点必须严格晚于被替换实例")
                        replacement_cutoffs[action["id"]] = (next_establish, next_action["order"])
                        replacement = {"replaced_action_id": action["id"],
                            "replacing_action_id": next_action["id"],
                            "replaced_generation": index + 1, "replacing_generation": index + 2,
                            "replacement_time_ticks": next_establish,
                            "replacement_order": next_action["order"], "cancelled_hits": 0}
                        if has_application_cadence:
                            replacement["cancelled_application_attempts"] = 0
                        replacements.append(replacement)
            else:
                if replacement_policy != REPLACEMENT_POLICY:
                    raise ValueError("外部刷新需要replace_previous生命周期")
                if any("landed" in contact
                       for action in selected for contact in overrides.get(action["id"], {}).get("contacts", [])):
                    raise ValueError("外部刷新代尚不接受逐contact命中例外；例外缺少generation身份")
                trigger_rows = []
                for action in selected:
                    start = _ticks(action["start"])
                    trigger_rows.append({"kind": "action_establishment", "trigger_id": action["id"],
                        "time_ticks": start + establish, "order": action["order"],
                        "root_action": action, "source_event": None})
                for declaration in refresh_declarations:
                    if (declaration["operation"] != REFRESH_OPERATION
                            or declaration["snapshot_policy"] != REFRESH_SNAPSHOT_POLICY
                            or declaration["status"] != "confirmed"):
                        raise ValueError("外部刷新操作、快照策略或依据未确认")
                    trigger = declaration["trigger"]
                    source_action = actions.get(trigger["action_id"])
                    if source_action is None or source_action["repeat"] != {"mode": "once"}:
                        raise ValueError("外部刷新须引用本窗口唯一once动作")
                    source_ability = abilities[source_action["ability_id"]]
                    if source_action["id"] in selected_ids:
                        raise ValueError("周期档案的隐式建立动作不能同时声明为显式刷新触发")
                    if source_action.get("status") != "confirmed" or source_ability.get("status") != "confirmed":
                        raise ValueError("外部刷新引用的动作与能力须为confirmed")
                    trigger_time = _ticks(source_action[
                        "start" if trigger["kind"] == "action_start" else "occupation_end"])
                    if trigger_time >= period:
                        raise ValueError("外部刷新触发越过输入窗口")
                    if trigger["kind"] == "action_start":
                        trigger_order = (source_action["order"] if trigger["phase"] == "pre"
                                         else _action_start_post_order(rotation, source_action))
                    else:
                        trigger_order = trigger["order"]
                        if trigger_order <= source_action["order"]:
                            raise ValueError("action_end刷新post order须严格晚于动作结束事件")
                    source_event = {"kind": trigger["kind"], "action_id": source_action["id"],
                        "ability_id": source_action["ability_id"], "time_ticks": trigger_time,
                        "native_order": source_action["order"], "order": trigger_order,
                        "phase": trigger["phase"],
                        "repeat": copy.deepcopy(source_action["repeat"]),
                        "evidence": {
                            "action": {"status": source_action["status"],
                                       "source": source_action["source"]},
                            "ability": {"status": source_ability["status"],
                                        "source": source_ability["source"]},
                        }}
                    trigger_rows.append({"kind": "external_refresh", "trigger_id": declaration["id"],
                        "time_ticks": trigger_time, "order": trigger_order,
                        "declaration": declaration, "source_event": source_event,
                        "declared_target": _refresh_declared_target(declaration)})
                trigger_rows.sort(key=lambda row: (row["time_ticks"], row["order"], row["trigger_id"]))
                positions = [(row["time_ticks"], row["order"]) for row in trigger_rows]
                if len(set(positions)) != len(positions):
                    raise ValueError("周期实例建立与刷新触发不能占用同一时点和order")
                generation_inputs, current = [], None
                duration = end_offset - establish
                for trigger in trigger_rows:
                    position = (trigger["time_ticks"], trigger["order"])
                    if trigger["kind"] == "external_refresh" and (
                            current is None or trigger["time_ticks"] > current["_end_ticks"]):
                        refresh_resolutions.append({"trigger_id": trigger["trigger_id"],
                            "profile_id": pid, "operation": REFRESH_OPERATION,
                            "declared_target": copy.deepcopy(trigger["declared_target"]),
                            "resolved_profile_id": pid,
                            "source_event": copy.deepcopy(trigger["source_event"]),
                            "source_event_fingerprint": fingerprint(trigger["source_event"]),
                            "snapshot_policy": REFRESH_SNAPSHOT_POLICY,
                            "outcome": "inactive_noop"})
                        continue
                    if (current is not None and trigger["time_ticks"] <= current["_end_ticks"]
                            and position <= current["_establish_position"]):
                        raise ValueError("刷新实例的建立位置必须严格晚于被替换实例")
                    if trigger["kind"] == "action_establishment":
                        root_action = trigger["root_action"]
                        base_start = _ticks(root_action["start"])
                        end_ticks = base_start + end_offset
                    else:
                        root_action = current["_root_action"]
                        base_start = trigger["time_ticks"] - establish
                        end_ticks = trigger["time_ticks"] + duration
                        same_tick_channels = [channel["channel_id"] for channel, first, _, _, _ in specs
                                              if first == establish]
                        if same_tick_channels:
                            order_overrides = {(row["channel_id"], row["ordinal"]): row.get("order")
                                for row in overrides.get(root_action["id"], {}).get("contacts", [])}
                            same_tick_orders = [order_overrides.get((channel_id, 1))
                                                for channel_id in same_tick_channels]
                            if (any(order is None or order <= trigger["order"] for order in same_tick_orders)
                                    or len(set(same_tick_orders)) != len(same_tick_orders)):
                                raise ValueError("外部刷新同刻首击须逐通道声明严格晚于触发且互异的order")
                        if end_ticks >= period:
                            raise ValueError("外部刷新后的实例结束越过输入窗口")
                    instance = {**copy.deepcopy(root_action),
                        "_instance_key": trigger["trigger_id"], "_root_action": root_action,
                        "_base_start_ticks": base_start, "_establish_ticks": trigger["time_ticks"],
                        "_establish_position": position, "_end_ticks": end_ticks,
                        "_trigger_kind": trigger["kind"], "_source_event": trigger["source_event"]}
                    if current is not None and trigger["time_ticks"] <= current["_end_ticks"]:
                        replacement_cutoffs[current["_instance_key"]] = position
                        replacement = {"replaced_action_id": current["_root_action"]["id"],
                            "replacing_action_id": root_action["id"],
                            "replaced_trigger_id": current["_instance_key"],
                            "replacing_trigger_id": trigger["trigger_id"],
                            "replacing_trigger_kind": trigger["kind"],
                            "replaced_generation": len(generation_inputs),
                            "replacing_generation": len(generation_inputs) + 1,
                            "replacement_time_ticks": trigger["time_ticks"],
                            "replacement_order": trigger["order"], "cancelled_hits": 0}
                        if has_application_cadence:
                            replacement["cancelled_application_attempts"] = 0
                        replacements.append(replacement)
                    generation_inputs.append(instance)
                    current = instance
                    if trigger["kind"] == "external_refresh":
                        refresh_resolutions.append({"trigger_id": trigger["trigger_id"],
                            "profile_id": pid, "operation": REFRESH_OPERATION,
                            "declared_target": copy.deepcopy(trigger["declared_target"]),
                            "resolved_profile_id": pid,
                            "source_event": copy.deepcopy(trigger["source_event"]),
                            "source_event_fingerprint": fingerprint(trigger["source_event"]),
                            "snapshot_policy": REFRESH_SNAPSHOT_POLICY,
                            "outcome": "replaced_active", "generation": len(generation_inputs),
                            "root_action_id": root_action["id"]})
            count = sum(s[2] for s in specs) * len(generation_inputs)
            record["stats"]["planned_hits"] = count
            if sum(r["stats"]["planned_hits"] for r in receipts) > 40000:
                raise ValueError("周期展开总命中数超过40000")
            if profile["accepted_profile_fingerprint"] != record["profile_fingerprint"]:
                confirmation_only = True
                raise ValueError("请复核当前完整来源、成对通道语义和校准生命周期后接受档案指纹")
            # The generic lifecycle projector is the authority for generation,
            # cancellation, refresh boundaries, and exact occurrence positions.
            # The legacy materializer below still attaches damage/application
            # semantics, but is rejected atomically if its event set diverges.
            from .periodic_lifecycle import project_periodic_lifecycle

            projected_channels = []
            for channel, first, n, interval, offsets in specs:
                if offsets is not None:
                    projected_schedule = {"kind": "explicit_offsets",
                        "offset_ticks": [value - establish for value in offsets]}
                elif interval and n > 1:
                    projected_schedule = {"kind": "equal_interval",
                        "first_offset_ticks": first - establish,
                        "interval_ticks": interval, "count": n}
                else:
                    projected_schedule = {"kind": "fixed",
                        "offset_ticks": first - establish}
                projected_channels.append({"channel_id": channel["channel_id"],
                    "schedule": projected_schedule,
                    # This projection is the authoritative schedule/lifecycle
                    # proof.  Landed state, compact cadence and
                    # no_application are resolved later per generated contact,
                    # so claiming static delivery kinds here would overstate
                    # what actually happened.
                    "deliveries": []})
            establishments = [{"trigger_id": action["id"], "root_action_id": action["id"],
                "time_ticks": _ticks(action["start"]) + establish, "order": action["order"],
                "status": "confirmed"} for action in selected]
            lifecycle_controls = [{"control_id": row["trigger_id"], "operation": "replace",
                "time_ticks": row["source_event"]["time_ticks"],
                "order": row["source_event"]["order"], "status": "confirmed"}
                for row in refresh_resolutions if row.get("outcome") == "replaced_active"]
            occurrence_orders = []
            for instance in generation_inputs:
                instance_key = instance.get("_instance_key", instance["id"])
                root_action = instance.get("_root_action", instance)
                contact_overrides = {(row["channel_id"], row["ordinal"]): row
                    for row in overrides.get(root_action["id"], {}).get("contacts", [])}
                for channel, first, n, interval, offsets in specs:
                    for ordinal in range(1, n + 1):
                        offset = (offsets[ordinal - 1] if offsets is not None
                                  else first + (ordinal - 1) * interval)
                        occurrence_orders.append({"trigger_id": instance_key, "phase": 1,
                            "channel_id": channel["channel_id"], "ordinal": ordinal,
                            "order": contact_overrides.get(
                                (channel["channel_id"], ordinal), {}).get(
                                    "order", _action_start_post_order(rotation, root_action)
                                    if offset == establish else 0)})
            lifecycle_projection = project_periodic_lifecycle(
                channels=projected_channels,
                lifecycle={"duration_ticks": end_offset - establish,
                    "refresh_policy": replacement_policy or "none",
                    "pause_resume_policy": "none", "off_field_policy": "continue"},
                establishments=establishments, controls=lifecycle_controls,
                occurrence_orders=occurrence_orders, window_end_ticks=period)
            expected_generation_ids = [
                action.get("_instance_key", action["id"]) for action in generation_inputs]
            if ([row["trigger_id"] for row in lifecycle_projection["generations"]]
                    != expected_generation_ids):
                raise ValueError("统一生命周期投影与周期实例代次不一致")
            generated = []
            planned_application_attempts = 0
            generation_by_instance = {
                action.get("_instance_key", action["id"]): index + 1
                for index, action in enumerate(generation_inputs)}
            for action in generation_inputs:
                instance_key = action.get("_instance_key", action["id"])
                root_action = action.get("_root_action", action)
                override = overrides.get(root_action["id"], {})
                if override and override.get("kind") != profile["kind"]:
                    raise ValueError("多通道动作必须使用逐contact例外")
                contact_overrides = {(o["channel_id"], o["ordinal"]): o for o in override.get("contacts", [])}
                if len(contact_overrides) != len(override.get("contacts", [])):
                    raise ValueError("逐contact例外身份重复")
                expected_keys = {(c["channel_id"], i) for c, _, n, _, _ in specs
                                 for i in range(1, n + 1)}
                if contact_overrides.keys() - expected_keys:
                    raise ValueError("逐contact例外引用不存在的channel/ordinal")
                for channel, first, n, interval, offsets in specs:
                    landed_ordinal = 0
                    for ordinal in range(1, n + 1):
                        extra = contact_overrides.get((channel["channel_id"], ordinal), {})
                        landed = extra.get("landed", channel["default_landed"])
                        if extra.get("status") == "pending" or landed is None:
                            raise ValueError("逐contact命中例外待确认")
                        if landed:
                            landed_ordinal += 1
                        cadence = channel.get("application_cadence")
                        application_attempted = None
                        if cadence is not None:
                            first_attempt = cadence["first_attempt_ordinal"]
                            application_attempted = bool(
                                landed and landed_ordinal >= first_attempt
                                and (landed_ordinal - first_attempt) % cadence["period_hits"] == 0)
                        planned_attempt = landed if cadence is None else application_attempted
                        planned_application_attempts += int(planned_attempt)
                        offset = (offsets[ordinal - 1] if offsets is not None
                                  else first + (ordinal - 1) * interval)
                        tick = action.get("_base_start_ticks", _ticks(root_action["start"])) + offset
                        event_order = extra.get(
                            "order", _action_start_post_order(rotation, root_action)
                            if offset == establish else 0)
                        cutoff = replacement_cutoffs.get(instance_key)
                        cancelled = (cutoff is not None and (
                            (tick, event_order) >= cutoff if isinstance(cutoff, tuple) else tick >= cutoff))
                        if cancelled:
                            if extra and (not refresh_declarations or "landed" in extra):
                                raise ValueError("逐contact例外指向已被刷新替换取消的旧实例命中")
                            replacement = next(item for item in replacements
                                if (item.get("replaced_trigger_id", item["replaced_action_id"])
                                    == instance_key))
                            replacement["cancelled_hits"] += 1
                            if has_application_cadence:
                                replacement["cancelled_application_attempts"] += int(planned_attempt)
                            continue
                        time = tick / 1000000
                        resolved_offset = tick - _ticks(root_action["start"])
                        if (_ticks(time) != tick or _ticks(offset / 1000000) != offset
                                or resolved_offset < 0):
                            raise ValueError("整数时点不能无损表示到现引擎微秒输入")
                        ident = PREFIX + fingerprint([profile["kind"], instance_key, channel["channel_id"], ordinal])
                        generated.append({"action_id": root_action["id"], "ability_id": root_action["ability_id"], "channel_id": channel["channel_id"],
                            "ordinal": ordinal, "pattern_id": ident, "hit_id": ident + ":1", "prototype_hit_id": channel["prototype_hit_id"],
                            "t": time, "order": event_order,
                            "order_explicit": "order" in extra, "landed": landed,
                            "offset_ticks": resolved_offset, "expected_time_ticks": tick})
                        if refresh_declarations:
                            generated[-1]["schedule_offset_ticks"] = offset
                        if cadence is not None:
                            generated[-1].update(application_attempted=application_attempted,
                                application_cadence_position=landed_ordinal if landed else None)
                        if replacement_policy == REPLACEMENT_POLICY:
                            generated[-1].update(instance_generation=generation_by_instance[instance_key],
                                instance_id=f"{pid}:{instance_key}:{generation_by_instance[instance_key]}")
                        if refresh_declarations:
                            generated[-1].update(instance_trigger_id=instance_key,
                                instance_trigger_kind=action["_trigger_kind"])
                        if channel["self_sampling"] == "action_capture":
                            from .action_capture import resolve_action_capture
                            capture = resolve_action_capture(root_action, tick, generated[-1]["order"], captures)
                            generated[-1].update(self_snapshot_event=capture["id"], snapshot_capture_fingerprint=fingerprint(capture))
            record["generated"] = generated
            projected_events = {(row["trigger_id"], row["channel_id"], row["ordinal"]): row
                                for row in lifecycle_projection["occurrences"]}
            generated_events = {(row.get("instance_trigger_id", row["action_id"]),
                row["channel_id"], row["ordinal"]): row for row in generated}
            if projected_events.keys() != generated_events.keys():
                raise ValueError("统一生命周期投影与周期语义物化的事件集合不一致")
            for identity, event in generated_events.items():
                projected = projected_events[identity]
                if (event["expected_time_ticks"] != projected["time_ticks"]
                        or event["order"] != projected["order"]):
                    raise ValueError("统一生命周期投影与周期语义物化的时点/order不一致")
            record["lifecycle_projection"] = copy.deepcopy(lifecycle_projection)
            if replacement_policy == REPLACEMENT_POLICY:
                record["replacement_policy"] = replacement_policy
                record["replacement_contract"] = {"trigger": "new_instance_establishment",
                    "old_instance_end": "inclusive", "cancel_old_events": "at_or_after_trigger",
                    "past_events": "preserved", "execution": "compile_time_planned"}
                if refresh_declarations:
                    record["replacement_contract"].update(
                        position_ordering="integer_microseconds_then_order",
                        inactive_refresh="no_op",
                        snapshot_policy=REFRESH_SNAPSHOT_POLICY,
                        state_runtime_integration="not_implemented")
                record["replacements"] = replacements
                record["stats"]["cancelled_hits"] = sum(item["cancelled_hits"] for item in replacements)
            if has_application_cadence:
                cancelled_attempts = sum(
                    item.get("cancelled_application_attempts", 0) for item in replacements)
                record["stats"].update(
                    planned_application_attempts=planned_application_attempts,
                    generated_application_attempts=sum(
                        event.get("application_attempted", event["landed"]) for event in generated),
                    cancelled_application_attempts=cancelled_attempts,
                    application_gated_hits=sum(
                        event["landed"] and event.get("application_attempted") is False for event in generated),
                )
            if refresh_declarations:
                record["refresh_contract"] = {"operation": REFRESH_OPERATION,
                    "profile_scope": "all_channels", "execution": "compile_time_planned",
                    "target_resolution": "public_profile_or_dispatch_ability_to_unique_materialized_profile",
                    "supported_triggers": ["action_start", "action_end"],
                    "ordering": "integer_microseconds_then_order",
                    "snapshot_policy": REFRESH_SNAPSHOT_POLICY,
                    "generation_application_cadence": "reset",
                    "shared_application_counter": "preserved"}
                record["refresh_triggers"] = copy.deepcopy(refresh_resolutions)
                record["stats"].update(
                    refresh_triggers=len(refresh_declarations),
                    refreshes_applied=sum(row["outcome"] == "replaced_active"
                                          for row in refresh_resolutions),
                    refreshes_inactive_noop=sum(row["outcome"] == "inactive_noop"
                                                for row in refresh_resolutions),
                    generations_created=len(generation_inputs),
                    instances_replaced=len(replacements))
            record["instance_payload"] = periodic_instance_payload(
                profile=profile, record=record, selected=selected,
                overrides=overrides, period=rotation["period"],
                refresh_declarations=refresh_declarations)
            record["instance_fingerprint"] = fingerprint(record["instance_payload"])
            record["status"] = "ready"
            record["stats"].update(generated_hits=len(generated), landed_hits=sum(g["landed"] for g in generated), missed_hits=sum(not g["landed"] for g in generated))
            stages.append(record)
            sources.append({"kind": "action_event_profile", "profile_id": pid, "profile_fingerprint": record["profile_fingerprint"], "source_dependency_fingerprint": record["source_dependency_fingerprint"]})
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            if isinstance(exc, PlanError):
                raise
            code = "action_event_confirmation" if confirmation_only else "periodic_action_pending"
            message = str(exc)[:1024]
            record["issues"].append({"code": code, "path": "action_event_profiles." + pid, "message": message})
            emit_task_missing(
                missing, code, "action_event_profiles." + pid, message,
                "review_action_event",
                consumer="action_event_profile:" + pid,
                category="knowledge_missing",
                blocking_scope="agent_materialization",
                repair_hint="核对周期档案的生命周期、各通道、刷新、时序、命中和确认",
            )
    if len(stages) != len(receipts):
        return None, receipts, []
    compiled = copy.deepcopy(model)
    for record in stages:
        compiled["hit_patterns"].extend(patterns_for_record(record))
    return compiled, receipts, sources


def validate_combined_times(rotation, original_model, template, records, overrides):
    from .action_events import (
        ORDER_MAX,
        ORDER_MIN,
        _action_start_post_order,
        _is_unambiguous_action_start,
        _parent_action_start_events,
        _ticks,
        _time_events,
    )

    generated = [g for r in records for g in r["generated"]]
    existing = _time_events(rotation, original_model, template)
    times = [*existing, *((g["expected_time_ticks"], g["order"], g["hit_id"]) for g in generated)]
    if len(times) > 40000:
        raise ValueError("动作派发后事件总数超过40000")
    by_time, by_order = Counter(t for t, _, _ in times), Counter((t, o) for t, o, _ in times)
    actions = {action["id"]: action for action in rotation["actions"]}
    legacy = {o["action_id"]: o for o in overrides if o.get("kind") not in MULTI_KINDS}
    finite_groups = defaultdict(list)
    for row in generated:
        if "topology_order" in row:
            finite_groups[(row["action_id"], row["expected_time_ticks"])].append(row)
    for (action_id, tick), group in finite_groups.items():
        action = actions[action_id]
        ordered = sorted(group, key=lambda row: (row["topology_order"], row["ordinal"]))
        if len({row["topology_order"] for row in ordered}) != len(ordered):
            raise ValueError("有限序列同一动作同一时点的局部拓扑顺序重复")
        group_ids = {row["hit_id"] for row in group}
        peers = [(order, event_id) for peer_tick, order, event_id in times
                 if peer_tick == tick and event_id not in group_ids]
        if tick == _ticks(action["start"]):
            spends = _parent_action_start_events(rotation, action)
            allowed = {action_id, *(row["id"] for row in spends)}
            if {event_id for _, event_id in peers} != allowed:
                raise ValueError("有限序列零偏移段与外部同刻事件关系不明确")
            first = _action_start_post_order(rotation, action)
        else:
            if peers:
                raise ValueError("有限序列派生段与外部事件同微秒，无法确定全局顺序")
            first = 0
        last = first + len(ordered) - 1
        if not ORDER_MIN <= first <= last <= ORDER_MAX:
            raise ValueError("有限序列派生段的全局order超出受支持整数边界")
        if [row["order"] for row in ordered] != list(range(first, first + len(ordered))):
            raise ValueError("有限序列局部拓扑顺序未正确映射到全局事件队列")
    for record in records:
        for refresh in record.get("refresh_triggers", []):
            source = refresh["source_event"]
            position = source["time_ticks"], source["order"]
            allowed = ({source["action_id"]} if source["kind"] == "action_start"
                       and source["phase"] == "pre" and source["order"] == source["native_order"]
                       else set())
            peers = {event_id for tick, order, event_id in times if (tick, order) == position}
            if peers - allowed or not peers <= allowed:
                raise ValueError("周期刷新控制位置与同刻已有事件或派生命中order冲突")
        for g in record["generated"]:
            if "topology_order" in g:
                continue
            explicit = (g.get("order_explicit", False) if "order_explicit" in g
                        else "order" in legacy.get(g["action_id"], {}))
            action = actions[g["action_id"]]
            same_tick_peers = [event_id for tick, _, event_id in times
                               if tick == g["expected_time_ticks"] and event_id != g["hit_id"]]
            implicit_action_start = _is_unambiguous_action_start(
                rotation, action, g, same_tick_peers)
            if by_time[g["expected_time_ticks"]] > 1 and not explicit and not implicit_action_start:
                raise ValueError("同微秒接触需显式逐项order，不由通道数组顺序猜测")
            if by_order[(g["expected_time_ticks"], g["order"])] > 1:
                raise ValueError("派生命中与已有事件的同刻order冲突")
