"""Review complete lifecycle graphs, then close finite resource dependencies.

No source is approved by its name or by a legacy rule-only compilation. An
excluded graph is still checked before pruning; native inputs are never edited.
"""
from __future__ import annotations

import copy

from genshin_engine.state_runtime_mechanics import validate_state_runtime_bindings

from .schema import PlanError


def prepare_state_runtime(model, rules, active, linked, excluded, missing, *, source_reviews,
                          context_fingerprint, pools, pool_adoptions, pool_disabled_rules,
                          rotation_model=None, character_ids=None):
    config = model.get("state_runtime")
    pool_records = copy.deepcopy(pool_adoptions)
    if config is None:
        return None, copy.deepcopy(pools), set(pool_disabled_rules), [], pool_records
    from .resource_lifecycle_sources import has_lifecycle_gates
    if config.get("schema_version") == 2 and (model.get("resource_threshold_triggers", {}).get("schema_version") == 3
                                             or has_lifecycle_gates(model)):
        from .state_runtime_v2 import prepare_positioned_state
        return prepare_positioned_state(model, rules, active, linked, excluded, missing, source_reviews=source_reviews,
            context_fingerprint=context_fingerprint, pools=pools, pool_adoptions=pool_adoptions,
            pool_disabled_rules=pool_disabled_rules, rotation_model=rotation_model, character_ids=character_ids)
    if config.get("schema_version") == 2:
        raise PlanError("App 的新生命周期合同必须与阈值 v3 显式共同声明", "invalid_state_runtime_source")
    authority = {}
    for rid, rule in rules.items():
        if rid not in linked: continue
        if rule["source_id"] in authority:
            raise PlanError("生命周期来源部件不能由多条权威规则交付", "duplicate_delivery")
        authority[rule["source_id"]] = rule

    # Use actual native identities, including action ends and expanded pattern
    # hits. Queue-specific ordering is checked again by the engine after expansion.
    actions = {r["id"] for r in (rotation_model or {}).get("actions", [])}
    if rotation_model is None:
        actions.update(r["action_id"] for r in model.get("hit_events", []) if "action_id" in r)
    event_ids = actions | {rid+":end" for rid in actions}
    for field, key in (("hit_events", "hit_id"), ("condition_events", "id"), ("application_events", "id")):
        event_ids.update(r[key] for r in model.get(field, []) if key in r)
    event_ids.update(f"{r['id']}:{i+1}" for r in model.get("hit_patterns", []) for i in range(len(r["offsets"])))
    from genshin_engine.registry import DAMAGE_TYPES
    from genshin_engine.rotation_schema import ACTION_TYPES
    # Evidence-pending rows are still structurally validated, not skipped by the
    # clock's early pending return. Their original status is handled below.
    checked = copy.deepcopy(model)
    for group in ("lifecycles", "timers"):
        for row in checked["state_runtime"][group]:
            if row.get("status") == "pending": row["status"] = "assumed"
    issues = validate_state_runtime_bindings(checked,
        character_ids=list(character_ids or {r["owner"] for r in rules.values()} or {r["owner"] for r in config["lifecycles"]}),
        event_ids=event_ids, action_ids=actions,
        conditions={r["condition"] for r in model.get("condition_events", []) if "condition" in r},
        action_type_ids=set(ACTION_TYPES), damage_type_ids=set(DAMAGE_TYPES))
    errors = [issue for issue in issues if issue["severity"] == "error"]
    if errors: raise PlanError("生命周期原图结构或目标所有权无效", "invalid_state_runtime_source", errors)
    for issue in issues:
        missing("state_reference_pending", issue["path"], issue["message"], "review_mechanics")
    pending = bool(issues)
    lives = {r["id"]: r for r in config["lifecycles"]}
    timers = {r["id"]: r for r in config["timers"]}
    groups = {lid: [] for lid in lives}
    for lid, life in lives.items():
        groups[lid].extend((h["kind"], h["ref_id"]) for hooks in life["hooks"].values() for h in hooks)
    for timer in timers.values():
        groups[timer["lifecycle_id"]].append((timer["payload"]["kind"], timer["payload"]["ref_id"]))
    records = []
    for kind, rows in (("lifecycles", lives), ("timers", timers)):
        for ident, row in rows.items():
            lid = ident if kind == "lifecycles" else row["lifecycle_id"]
            path = f"mechanics_model.state_runtime.{kind}.{ident}"
            source_rule = authority.get(row["source_id"])
            record = {"kind": kind, "id": ident, "lifecycle_id": lid, "source_id": row["source_id"],
                      "path": path, "own_source_status": "pending", "effective_status": "pending", "status": "pending",
                      "dependency_ids": [f"{k}:{ref}" for k, ref in sorted(groups[lid])]}
            records.append(record)
            if source_rule is None:
                missing("state_source_pending", path+".source_id", "生命周期需要唯一mechanics来源部件，来源字符串不构成批准", "review_mechanics")
                pending = True
                continue
            rid = source_rule["id"]
            record["authority_rule_id"] = rid
            if source_rule["owner"] != lives[lid]["owner"]:
                raise PlanError("生命周期与权威来源提供者不一致", "invalid_state_runtime_source", {"path": path, "rule_id": rid})
            if rid in excluded:
                record.update(own_source_status="excluded", effective_status="excluded", status="excluded", reason="source_part_excluded")
                continue
            review = source_reviews.get(row["source_id"]) or {}
            if (review.get("mode") != "manual_context" or review.get("context_fingerprint") != context_fingerprint
                    or not isinstance(review.get("note"), str) or not review["note"].strip()):
                pending = True
                missing("state_source_scope_unreviewed", path+".source_id", "完整生命周期子图需要当前manual_context及复核说明；source_only或旧自动规则不能批准新增时钟", "review_mechanics")
            elif rid not in active or row["status"] == "pending":
                pending = True
                missing("state_source_pending", path+".source_id", "生命周期来源或声明仍待核验，不能当作明确排除", "review_mechanics")
            else:
                record.update(own_source_status="ready", effective_status="ready", status="ready")

    original_pool = model.get("shared_pools", {"pools": [], "operations": [], "consumers": []})
    surviving = pools or {"pools": [], "operations": [], "consumers": []}
    removed = {kind: {r["id"] for r in original_pool[kind]} - {r["id"] for r in surviving[kind]}
               for kind in ("pools", "operations", "consumers")}
    disabled_rules = set(excluded) | set(pool_disabled_rules)
    disabled_lives = set()
    pool_pending = any("pending" in r.get("status", "") for r in pool_records)
    pool_rule_dependencies = {r.get("authority_rule_id") for r in records}
    pool_rule_dependencies.update(ref for deps in groups.values() for kind, ref in deps
                                  if kind == "dispatch_rule")
    if pool_pending and (pool_rule_dependencies & set(pool_disabled_rules)
                         or any(k == "resource_operation" and ref in removed["operations"]
                                for deps in groups.values() for k, ref in deps)):
        pending = True
        missing("state_dependency_pending", "mechanics_model.state_runtime", "生命周期依赖的共享池仍待核验，未交付时钟半图", "review_mechanics")
    unavailable_targets = {ref for deps in groups.values() for kind, ref in deps
                           if kind == "dispatch_rule" and ref not in active and ref not in disabled_rules}
    if unavailable_targets:
        pending = True
        missing("state_dependency_pending", "mechanics_model.state_runtime",
                "周期目标规则尚未交付，来源部件就绪不能代替目标资格：" + ", ".join(sorted(unavailable_targets)), "review_mechanics")
    if pending:
        disabled_lives.update(lives)
    bound = len(lives)+len(rules)+sum(len(original_pool[k]) for k in removed)+1
    for _ in range(bound):
        before = (len(disabled_rules), len(disabled_lives), *(len(removed[k]) for k in sorted(removed)))
        for lid, deps in groups.items():
            group_records = [r for r in records if r["lifecycle_id"] == lid]
            if (any(r.get("authority_rule_id") in disabled_rules for r in group_records)
                    or any(ref in (disabled_rules if kind == "dispatch_rule" else removed["operations"]) for kind, ref in deps)):
                disabled_lives.add(lid)
            if lid in disabled_lives:
                for kind, ref in deps:
                    (disabled_rules if kind == "dispatch_rule" else removed["operations"]).add(ref)
        for pool in original_pool["pools"]:
            source_rule = authority.get(pool["source_id"])
            if source_rule and source_rule["id"] in disabled_rules: removed["pools"].add(pool["id"])
        for consumer in original_pool["consumers"]:
            if consumer["pool_id"] in removed["pools"] or consumer["eligibility_rule_id"] in disabled_rules:
                removed["consumers"].add(consumer["id"])
                disabled_rules.add(consumer["eligibility_rule_id"])
        for operation in original_pool["operations"]:
            source_rule = authority.get(operation["source_id"])
            if operation["pool_id"] in removed["pools"] or source_rule and source_rule["id"] in disabled_rules:
                removed["operations"].add(operation["id"])
        after = (len(disabled_rules), len(disabled_lives), *(len(removed[k]) for k in sorted(removed)))
        if before == after: break
    else: raise PlanError("生命周期资源依赖闭包未在有限声明界内收敛", "invalid_state_runtime_source")
    state = copy.deepcopy(config)
    state["lifecycles"] = [r for r in state["lifecycles"] if r["id"] not in disabled_lives]
    state["timers"] = [r for r in state["timers"] if r["lifecycle_id"] not in disabled_lives]
    for record in records:
        if record["lifecycle_id"] not in disabled_lives: continue
        if record["own_source_status"] == "pending":
            status, reason = "pending", "state_dependency_pending"
        elif record["own_source_status"] == "excluded":
            status, reason = "excluded", "source_part_excluded"
        elif pending:
            status, reason = "dependency_pending", "state_dependency_pending"
        else:
            status, reason = "dependency_excluded", "lifecycle_group_dependency_excluded"
        record.update(effective_status=status, status=status, reason=reason)
    result_pools = copy.deepcopy(pools)
    if result_pools is not None:
        for kind, disabled_ids in removed.items():
            result_pools[kind] = [r for r in result_pools[kind] if r["id"] not in disabled_ids]
        if not result_pools["pools"]: result_pools = None
    for record in pool_records:
        record.setdefault("own_source_status", record["status"])
        if record["id"] in removed[record["kind"]] and record["status"] == "ready":
            record.update(status="dependency_pending" if pending else "dependency_excluded", reason="lifecycle_resource_dependency_unavailable")
        record["effective_status"] = record["status"]
    records.sort(key=lambda r: (r["kind"], r["id"]))
    pool_records.sort(key=lambda r: (r["kind"], r["id"]))
    return state if state["lifecycles"] else None, result_pools, disabled_rules, records, pool_records
