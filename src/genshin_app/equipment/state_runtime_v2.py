"""Per-lifecycle source seeds for the explicit clock2/threshold3 contract.

This preparation does not publish a calculation. The shared finite dependency
closure must still remove typed endpoints and unavailable downstream groups.
"""
from __future__ import annotations

import copy

from genshin_engine.registry import DAMAGE_TYPES
from genshin_engine.rotation_schema import ACTION_TYPES
from genshin_engine.state_runtime_mechanics import validate_state_runtime_bindings

from .schema import PlanError


def verify_positioned_delivery(original, resolved, character_ids):
    """A ready subgraph may omit nodes, but cannot rewrite an approved node."""
    from genshin_engine.mechanics import validate_mechanics_model
    from genshin_engine.resource_threshold_dispatch import ThresholdTriggerProgram
    from genshin_engine.shared_pools import SharedPoolError
    from genshin_engine.state_runtime import StateRuntime, StateRuntimeError

    collections = {"shared_pools": ("pools", "operations", "consumers"), "state_runtime": ("lifecycles", "timers"),
        "resource_counters": ("counters",), "resource_counter_views": ("views",),
        "resource_threshold_observers": ("observers",), "resource_threshold_triggers": ("bindings",)}
    for field, groups in collections.items():
        if field not in resolved: continue
        before, after = original.get(field), resolved[field]
        if before is None or {k: v for k, v in before.items() if k not in groups} != {k: v for k, v in after.items() if k not in groups}:
            raise PlanError("实际资源合同偏离已审核图", "invalid_state_runtime_delivery", field)
        for group in groups:
            rows = {row["id"]: row for row in before[group]}
            if len({r["id"] for r in after[group]}) != len(after[group]) or any(rows.get(r["id"]) != r for r in after[group]):
                raise PlanError("实际资源条目被改写或混入未经复核节点", "invalid_state_runtime_delivery", field+"."+group)
    rules = {r["id"]: r for r in original["rules"]}
    if any(rules.get(r["id"]) != r for r in resolved["rules"]):
        raise PlanError("实际收益规则偏离已核完整来源", "invalid_state_runtime_delivery")
    validation = validate_mechanics_model(resolved)
    if validation["status"] != "valid":
        raise PlanError("最终生命周期执行图没有通过完整复核", "invalid_state_runtime_delivery", validation["issues"])
    try:
        if "resource_threshold_triggers" in resolved:
            ThresholdTriggerProgram(resolved, character_ids=sorted(character_ids))
        if "state_runtime" in resolved:
            StateRuntime(resolved["state_runtime"], character_ids=sorted(character_ids), emit_ordered_steps=True,
                rule_ids=[r["id"] for r in resolved["rules"]],
                operation_ids=[r["id"] for r in resolved.get("shared_pools", {}).get("operations", [])])
    except (SharedPoolError, StateRuntimeError) as exc:
        raise PlanError("最终计量、阈值及定时器索引不能构成完整执行图", "invalid_state_runtime_delivery", exc.issue) from exc


def prepare_positioned_state(model, rules, active, linked, excluded, missing, *, source_reviews,
                             context_fingerprint, pools, pool_adoptions, pool_disabled_rules,
                             rotation_model=None, character_ids=None):
    config = model["state_runtime"]
    lives = {row["id"]: row for row in config["lifecycles"]}
    timers = {row["id"]: row for row in config["timers"]}
    groups = {lid: [(p["kind"], p["ref_id"]) for hooks in life["hooks"].values() for p in hooks]
              for lid, life in lives.items()}
    for timer in timers.values():
        groups[timer["lifecycle_id"]].append((timer["payload"]["kind"], timer["payload"]["ref_id"]))
    authority = {}
    for rid, rule in rules.items():
        if rid not in linked: continue
        if rule["source_id"] in authority:
            raise PlanError("生命周期来源部件不能由多条权威规则交付", "duplicate_delivery")
        authority[rule["source_id"]] = rule
    actions = {r["id"] for r in (rotation_model or {}).get("actions", [])}
    if rotation_model is None: actions.update(r["action_id"] for r in model.get("hit_events", []) if "action_id" in r)
    events = actions | {aid+":end" for aid in actions}
    for field, key in (("hit_events", "hit_id"), ("condition_events", "id"), ("application_events", "id")):
        events.update(r[key] for r in model.get(field, []) if key in r)
    events.update(f"{r['id']}:{i+1}" for r in model.get("hit_patterns", []) for i in range(len(r["offsets"])))
    checked = copy.deepcopy(model)
    for group in ("lifecycles", "timers"):
        for row in checked["state_runtime"][group]:
            if row["status"] == "pending": row["status"] = "assumed"
    issues = validate_state_runtime_bindings(checked, character_ids=sorted(character_ids or {r["owner"] for r in lives.values()}),
        event_ids=events, action_ids=actions, conditions={r["condition"] for r in model.get("condition_events", [])},
        action_type_ids=set(ACTION_TYPES), damage_type_ids=set(DAMAGE_TYPES))
    if any(row["severity"] == "error" for row in issues):
        raise PlanError("生命周期完整原图无效，排除不能隐藏错误", "invalid_state_runtime_source", issues)
    group_status = {lid: 0 for lid in lives}
    for issue in issues:
        path = issue["path"]
        # IDs may themselves contain dots. Match the validator's full known
        # diagnostic locations; text prefixes do not identify graph nodes.
        affected = {lid for lid in lives if path in {
            "state_runtime.lifecycles."+lid+".start.condition",
            "state_runtime.lifecycles."+lid+".end_trigger.condition"}}
        affected.update(lid for lid, deps in groups.items() for kind, ident in deps
                        if path == ("rules." if kind == "dispatch_rule" else "shared_pools.operations.")+ident+".trigger.condition")
        if not affected:
            raise PlanError("待核引用不能归属具体生命周期，拒绝发布部分图", "invalid_state_runtime_source", issue)
        for lid in affected: group_status[lid] = 2
        missing("state_reference_pending", path, issue["message"], "review_mechanics")
    records = []
    for kind, rows in (("lifecycles", lives), ("timers", timers)):
        for ident, row in rows.items():
            lid = ident if kind == "lifecycles" else row["lifecycle_id"]
            path = f"mechanics_model.state_runtime.{kind}.{ident}"
            parent = authority.get(row["source_id"])
            own = "pending"
            if parent and parent["owner"] != lives[lid]["owner"]:
                raise PlanError("生命周期来源提供者与原图不一致", "invalid_state_runtime_source", path)
            review = source_reviews.get(row["source_id"]) or {}
            if parent and parent["id"] in excluded:
                own = "excluded"
            elif (parent and parent["id"] in active and parent.get("status") != "pending"
                    and bool(parent.get("source", "").strip()) and row["status"] != "pending"
                    and review.get("mode") == "manual_context" and review.get("context_fingerprint") == context_fingerprint
                    and isinstance(review.get("note"), str) and review["note"].strip()):
                own = "ready"
            if own == "pending":
                missing("state_source_pending", path, "当前完整生命周期来源尚未确认", "review_mechanics")
            group_status[lid] = max(group_status[lid], {"ready": 0, "excluded": 1, "pending": 2}[own])
            record = {"kind": kind, "id": ident, "lifecycle_id": lid, "source_id": row["source_id"], "path": path,
                "own_source_status": own, "dependency_ids": [f"{k}:{ref}" for k, ref in sorted(groups[lid])]}
            if parent: record["authority_rule_id"] = parent["id"]
            records.append(record)
    pool_records = copy.deepcopy(pool_adoptions)
    approvals = {(r["kind"], r["id"]): r for r in pool_records}
    operations = {r["id"]: r for r in (pools or {}).get("operations", [])}
    for lid, deps in groups.items():
        for kind, ident in deps:
            if kind == "dispatch_rule":
                row = rules[ident]
                status = 1 if ident in excluded else 0 if ident in active and row.get("status") != "pending" and row.get("source", "").strip() else 2
            else:
                record = approvals.get(("operations", ident), {})
                value = record.get("effective_status", record.get("status", "pending"))
                status = 2 if "pending" in value else 1 if "excluded" in value else 0 if value == "ready" and ident in operations else 2
            group_status[lid] = max(group_status[lid], status)
    unavailable = {lid for lid, status in group_status.items() if status}
    disabled_rules = set(pool_disabled_rules) | set(excluded)
    removed_operations = set()
    for lid in unavailable:
        for kind, ident in groups[lid]:
            (disabled_rules if kind == "dispatch_rule" else removed_operations).add(ident)
    for record in records:
        value, own = group_status[record["lifecycle_id"]], record["own_source_status"]
        group_name = ("ready", "excluded", "pending")[value]
        effective = "ready" if value == 0 else own if own == group_name else "dependency_"+group_name
        record.update(status=effective, effective_status=effective,
            reason="current_context_reviewed" if value == 0 else "state_dependency_"+group_name)
    state = copy.deepcopy(config)
    state["lifecycles"] = [r for r in state["lifecycles"] if r["id"] not in unavailable]
    state["timers"] = [r for r in state["timers"] if r["lifecycle_id"] not in unavailable]
    actual_pools = copy.deepcopy(pools)
    if actual_pools is not None:
        actual_pools["operations"] = [r for r in actual_pools["operations"] if r["id"] not in removed_operations]
    for record in pool_records:
        if record["kind"] == "operations" and record["id"] in removed_operations:
            owners = [lid for lid, deps in groups.items() if ("resource_operation", record["id"]) in deps]
            status = max(group_status[lid] for lid in owners)
            record.setdefault("own_source_status", record["status"])
            record.update(status="dependency_"+("excluded" if status == 1 else "pending"),
                          effective_status="dependency_"+("excluded" if status == 1 else "pending"))
    return (state if state["lifecycles"] else None), actual_pools, disabled_rules, sorted(records, key=lambda r: (r["kind"], r["id"])), pool_records
