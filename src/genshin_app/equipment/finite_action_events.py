"""Compile one semantic action into a reviewed finite occurrence sequence."""
from __future__ import annotations

import copy
from collections import defaultdict

from genshin_app.task_capability_producer import emit_task_missing

from .files import fingerprint
from .finite_action_schema import COMPILER, KIND
from .periodic_events import _one, patterns_for_record, periodic_profile_scope
from .schema import PlanError


def _assign_global_orders(rotation, model, template, action, rows):
    """Map reviewed local topology order onto an unambiguous global queue order.

    A manifest step order only compares steps emitted by the same semantic
    action at the same offset.  It must never be reused as a scenario-global
    order because unrelated events may already occupy that integer.  At action
    start, generated contacts follow the action and its bound energy spend.
    At other offsets, any external same-microsecond event makes placement
    ambiguous and the whole finite profile remains pending.
    """
    from .action_events import (
        ORDER_MAX,
        ORDER_MIN,
        _action_start_post_order,
        _parent_action_start_events,
        _ticks,
        _time_events,
    )

    action_start = _ticks(action["start"])
    existing = _time_events(rotation, model, template)
    by_tick = defaultdict(list)
    for tick, order, event_id in existing:
        by_tick[tick].append((order, event_id))
    groups = defaultdict(list)
    for row in rows:
        groups[row["expected_time_ticks"]].append(row)
    for tick, group in groups.items():
        ordered = sorted(group, key=lambda row: (row["topology_order"], row["ordinal"]))
        if len({row["topology_order"] for row in ordered}) != len(ordered):
            raise ValueError("有限序列同一动作同一时点的topology order必须唯一")
        peers = by_tick[tick]
        if tick == action_start:
            spends = _parent_action_start_events(rotation, action)
            allowed = {action["id"], *(row["id"] for row in spends)}
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
        for rank, row in enumerate(ordered):
            row["order"] = first + rank
    return rows


def compile_finite_action_events(plan, template, *, parameter_compilations,
        elemental_compilations, reaction_compilations, periodic_compilations,
        missing):
    """Return finite-sequence patterns, atomically per full profile group.

    The database-owned component rows are unique.  The topology-owned steps
    are occurrences and may reference the same component repeatedly.
    """
    from .action_capture import prepare_action_captures, resolve_action_capture
    from .action_events import PREFIX, _prototype_references, _ticks

    profiles = [row for row in plan.get("action_event_profiles", [])
                if row.get("kind") == KIND]
    if not profiles:
        return None, [], []
    model, rotation = plan.get("mechanics_model"), plan.get("rotation_model")
    actions = {row["id"]: row for row in (rotation or {}).get("actions", [])}
    abilities = {row["id"]: row for row in (rotation or {}).get("abilities", [])}
    override_ids = {row["action_id"] for row in plan.get("action_event_overrides", [])}
    receipts, stages, sources = [], [], []
    for profile in profiles:
        pid = profile["id"]
        ability_ids = sorted(row["ability_id"] for row in plan["action_event_bindings"]
                             if row["profile_id"] == pid)
        selected = sorted((row for row in actions.values()
                           if row["ability_id"] in ability_ids),
                          key=lambda row: (_ticks(row["start"]), row["order"], row["id"]))
        record = {"profile_id": pid, "kind": KIND, "compiler": COMPILER,
            "actor": None, "ability_ids": ability_ids, "status": "pending",
            # These two fields describe the legacy aggregate action-event
            # receipt protocol.  Independent finite coverage is published by
            # ``kind``, ``compiler``, ``gameplay_interpretation`` and the
            # separate action-materialization receipt.
            "dispatch_mode": "calibrated", "skill_coverage": "partial",
            "resource_coverage": "not_generated",
            "gameplay_interpretation": "independent_manifest",
            "profile_fingerprint": None, "required_confirmation": None,
            "accepted_profile_fingerprint": profile["accepted_profile_fingerprint"],
            "source_dependency_fingerprint": None, "instance_fingerprint": None,
            "generated": [], "channels": copy.deepcopy(profile["channels"]),
            "steps": copy.deepcopy(profile["steps"]),
            "terminal_step_id": profile["terminal_step_id"],
            "interruption_policy": profile["interruption_policy"],
            "scope": None, "issues": [],
            "stats": {"matched_actions": len(selected),
                "planned_hits": len(selected) * len(profile["steps"]),
                "generated_hits": 0, "landed_hits": 0, "missed_hits": 0}}
        receipts.append(record)
        confirmation_only = False
        try:
            if not model or not rotation or not selected:
                raise ValueError("有限序列档案需要完整模型与实际动作")
            if profile["interruption_policy"] != "none":
                raise ValueError("本版仅执行已确认不会中断的有限序列；其他策略保持待处理")
            if any(row["id"] in override_ids for row in selected):
                raise ValueError("有限序列尚不接受逐动作例外；步骤顺序由独立清单完整提供")
            source = _one(periodic_compilations,
                lambda row, pid=pid: row["action_event_profile_id"] == pid,
                "有限序列数据库来源回执缺失")
            scope = periodic_profile_scope(profile, source, template, model,
                parameter_compilations, elemental_compilations, reaction_compilations)
            scope.update(interruption_policy=profile["interruption_policy"],
                steps=copy.deepcopy(profile["steps"]),
                terminal_step_id=profile["terminal_step_id"])
            captures = {}
            if any(row["self_sampling"] == "action_capture" for row in profile["channels"]):
                captures, scope["snapshot_capture_calibrations"] = prepare_action_captures(
                    selected, rotation, model)
            actor, selection = source["actor"], source["query"]["selection"]
            profile_fingerprint = fingerprint(scope)
            record.update(actor=actor, scope=scope,
                target_id=model["elemental_model"]["target"]["id"],
                profile_fingerprint=profile_fingerprint,
                source_dependency_fingerprint=fingerprint(scope["dependencies"]),
                required_confirmation={"accepted_profile_fingerprint": profile_fingerprint})
            channels = {row["channel_id"]: row for row in profile["channels"]}
            for channel in channels.values():
                raw = _one(plan["manual_template"]["hits"],
                    lambda row, channel=channel: row["id"] == channel["prototype_hit_id"],
                    "有限序列组件原型缺失")
                allowed = {"id", "damage_type_ids", "t", "character", "skill", "hit_name",
                           "description", "damage_system", "element", "scaling_stat", "count"}
                if (raw.keys() - allowed or raw["t"] != 0
                        or _prototype_references(plan, template, raw["id"])):
                    raise ValueError("有限序列原型须为t=0、无执行字段且不被手工选择器引用")
            for aid in ability_ids:
                ability = abilities[aid]
                ability_slot = {"action.normal_attack": "normal",
                    "action.charged_attack": "normal", "action.plunging_attack": "normal",
                    "action.elemental_skill": "skill", "action.elemental_burst": "burst"}.get(
                        ability["action_type_id"])
                if (ability["character"] != actor
                        or ability_slot not in selection.get("matching_slots", [])
                        or ability.get("variant") != profile["variant"]):
                    raise ValueError("有限序列绑定能力的角色、天赋槽或明确变体不符")
            if profile["accepted_profile_fingerprint"] != profile_fingerprint:
                confirmation_only = True
                raise ValueError("请复核有限序列完整组件、步骤、时点和终结段后接受档案指纹")
            planned = len(selected) * len(profile["steps"])
            if sum(len(row["generated"]) for row in stages) + planned > 40000:
                raise ValueError("有限序列展开总命中数超过40000")
            period = _ticks(rotation["period"])
            generated = []
            for action in selected:
                if action["repeat"] != {"mode": "once"}:
                    raise ValueError("有限序列只接受显式once动作；多轮须输入多轮动作")
                start = _ticks(action["start"])
                action_rows = []
                for step in profile["steps"]:
                    channel = channels[step["component_id"]]
                    offset = _ticks(step["offset_seconds"])
                    tick = start + offset
                    if tick >= period:
                        raise ValueError("有限序列步骤必须严格早于输入窗口右端")
                    landed = channel["default_landed"]
                    if landed is None:
                        raise ValueError("有限序列组件默认命中尚未确认")
                    ident = PREFIX + fingerprint([
                        KIND, action["id"], step["step_id"], step["ordinal"]])
                    event = {"action_id": action["id"], "ability_id": action["ability_id"],
                        "channel_id": channel["channel_id"],
                        "component_id": channel["channel_id"],
                        "occurrence_id": step["step_id"],
                        "step_id": step["step_id"], "ordinal": step["ordinal"],
                        "terminal": step["step_id"] == profile["terminal_step_id"],
                        "pattern_id": ident, "hit_id": ident + ":1",
                        "prototype_hit_id": channel["prototype_hit_id"],
                        "t": tick / 1_000_000,
                        "topology_order": step["order"],
                        "landed": landed,
                        "offset_ticks": offset, "expected_time_ticks": tick}
                    action_rows.append(event)
                _assign_global_orders(rotation, model, template, action, action_rows)
                for event in action_rows:
                    channel = channels[event["component_id"]]
                    if channel["self_sampling"] == "action_capture":
                        capture = resolve_action_capture(
                            action, event["expected_time_ticks"], event["order"], captures)
                        event.update(self_snapshot_event=capture["id"],
                            snapshot_capture_fingerprint=fingerprint(capture))
                generated.extend(action_rows)
            record["generated"] = generated
            record["instance_payload"] = {"profile_fingerprint": profile_fingerprint,
                "channels": copy.deepcopy(profile["channels"]),
                "steps": copy.deepcopy(profile["steps"]),
                "generated": copy.deepcopy(generated), "actions": copy.deepcopy(selected),
                "period": rotation["period"]}
            record["instance_fingerprint"] = fingerprint(record["instance_payload"])
            record["status"] = "ready"
            record["stats"].update(generated_hits=len(generated),
                landed_hits=sum(row["landed"] for row in generated),
                missed_hits=sum(not row["landed"] for row in generated))
            stages.append(record)
            sources.append({"kind": "action_event_profile", "profile_id": pid,
                "profile_fingerprint": profile_fingerprint,
                "source_dependency_fingerprint": record["source_dependency_fingerprint"]})
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            if isinstance(exc, PlanError):
                raise
            code = "action_event_confirmation" if confirmation_only else "finite_action_pending"
            issue = {"code": code, "path": "action_event_profiles." + pid,
                     "message": str(exc)[:1024]}
            record["issues"].append(issue)
            emit_task_missing(
                missing, code, issue["path"], issue["message"],
                "review_action_event",
                consumer="action_event_profile:" + pid,
                category="knowledge_missing",
                blocking_scope="agent_materialization",
                repair_hint="核对有限序列的段、偏移、次数、命中、附着和档案确认",
            )
    if len(stages) != len(receipts):
        return None, receipts, []
    compiled = copy.deepcopy(model)
    for record in stages:
        compiled["hit_patterns"].extend(patterns_for_record(record))
    return compiled, receipts, sources
