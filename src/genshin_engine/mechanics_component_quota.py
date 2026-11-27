"""Spend a damage component's team quotas once for each concrete target."""

from __future__ import annotations

from genshin_engine.beneficiary_selection import effective_recipient
from genshin_engine.elemental import ElementalPending
from genshin_engine.reaction_event_capabilities import STELLAR_SWIRL_DAMAGE_COMPONENTS
from genshin_engine.rotation_port import TICKS

UNIT = "damage_component_target"
STELLAR_SWIRL_COMPONENTS = ("stellar_swirl", *STELLAR_SWIRL_DAMAGE_COMPONENTS)


def component_rules(runtime, request):
    if request["reaction_type"] not in STELLAR_SWIRL_COMPONENTS:
        return ()
    return runtime.component_quota_rules


def component_identity(runtime, request, cycle):
    target = request.get("target_id")
    event_id = request["event_id"]
    if target is None and request.get("target_selection_policy") == "event_time_entities_in_area":
        # Existing single-target plans have one concrete entity at detonation.
        # Multi-target callers pass the explicitly resolved child target.
        target = getattr(runtime.elemental, "target", None)
    if not isinstance(target, str) or not target:
        raise ElementalPending(
            "component_quota_target_missing",
            "星扩散扣层需要当前伤害分项的实际目标。请核对命中目标及风旋覆盖范围。",
            event_id, {"area_id": request.get("area_id")},
        )
    return cycle, event_id, target


def plan_component_quota(runtime, request, events, rules, target):
    """Share eligibility by hit ID and stage balance changes until projection succeeds."""
    event_id = request["event_id"]
    frontline = runtime.frontline
    if frontline not in runtime.data["chars"]:
        raise ElementalPending(
            "component_quota_frontline_missing",
            "星扩散扣层需要结算当时的前台角色。请补齐前台与切人记录。",
            event_id, {"target_id": target},
        )

    receipts, planned = {}, []
    for rule in rules:
        rid = rule["id"]
        consume = rule["quota"]["consume"]
        # Rule-independent filters still determine whether this component
        # participates. Determine per-contributor eligibility only once.
        effect = rule["effects"][0]
        for instance in runtime.state[rid]["instances"]:
            eligible = []
            for event in events:
                if (not effective_recipient(rule, instance, event["character"], frontline)
                        or not runtime.filter(consume, event, rid)):
                    continue
                override = runtime.match_effect(rid, effect["id"], event["id"])
                if (override == "exclude" or override != "include" and effect.get("filter")
                        and not runtime.filter(effect["filter"], event, rid)):
                    continue
                eligible.append(event)
            before = instance["quota"]["team"]
            consumed = bool(eligible and before > 0)
            instance_id = instance["instance_id"]
            for event in eligible:
                receipts.setdefault(event["id"], {})[(rid, instance_id)] = consumed
            planned.append((instance["quota"], {
                "type": "quota", "unit": UNIT, "t": runtime.time / TICKS,
                "cycle": events[0]["origin"], "event_id": event_id, "rule_id": rid,
                "source_id": rule["source_id"], "instance_id": instance_id, "pool": "team",
                "target_id": target, "reaction_type": request["reaction_type"], "frontline": frontline,
                **{key: request[key] for key in ("selection_audit_id", "target_order")
                   if key in request},
                "position": ("frontline" if rule["beneficiaries"]["scope"] == "current_frontline" else "offfield"),
                "candidates": sorted(event["character"] for event in events),
                "beneficiaries": sorted(event["character"] for event in eligible) if consumed else [],
                "hit_ids": sorted(event["id"] for event in eligible),
                "component_hit_ids": sorted(event["id"] for event in events),
                "before": before, "after": before - int(consumed), "consumed": consumed,
                "reason": "consumed" if consumed else ("empty" if eligible else "no_eligible_contributor"),
                "reported_damage": True,
            }))
    return receipts, planned


def commit_component_quota(runtime, identity, planned, trace_start):
    """Publish the group only after all its contributors projected successfully."""
    if any(balance["team"] != row["before"] for balance, row in planned):
        row = planned[0][1]
        raise ElementalPending(
            "component_quota_balance_changed", "星扩散分项投影期间次数池发生了未声明的变化。",
            row["event_id"], {"target_id": row["target_id"]},
        )
    for balance, row in planned:
        balance["team"] = row["after"]
    runtime.committed_component_ids.add(identity)
    runtime.trace[trace_start:trace_start] = [row for _, row in planned]
