"""Finite source graph closure shared by current reads and resource triggers."""
from __future__ import annotations

import copy

from genshin_engine.resource_counter_view_runtime import is_counter_view

from .resource_counter_views import validate_original_counter_views
from .resource_thresholds import inspect_original_resource_thresholds
from .schema import PlanError


def prepare_resource_source_graph(model, rules, source_active, active, linked, excluded, missing, *,
                                   source_reviews, context_fingerprint, pools, state, counters,
                                   pool_adoptions, state_adoptions, counter_adoptions, character_ids):
    """Close dependencies once, using frozen seeds from the original approvals.

    ``source_active`` is the set before resource pruning; ``active`` is the actual
    legacy delivery. The lattice is ready < excluded < pending. A mixed excluded
    and pending dependency remains pending, while its own source status is kept.
    """
    validate_original_counter_views(model, character_ids)
    threshold_fields = ('resource_threshold_observers', 'resource_threshold_triggers')
    has_threshold = any(field in model for field in threshold_fields)
    threshold_graph = inspect_original_resource_thresholds(model, character_ids)
    timer_thresholds = any(origin['kind'] == 'ordinary_timer' for origin in threshold_graph['operation_origins'].values())
    for issue in threshold_graph['issues']:
        missing('threshold_scope_pending', issue['path'], issue['message'], 'review_mechanics')
    authority = {}
    for rid, rule in rules.items():
        if rid not in linked: continue
        if rule["source_id"] in authority:
            raise PlanError("资源读数来源由多条权威规则交付", "duplicate_delivery")
        authority[rule["source_id"]] = rule
    records = [copy.deepcopy(pool_adoptions), copy.deepcopy(state_adoptions), copy.deepcopy(counter_adoptions), []]
    if has_threshold: records.extend([[], []])
    previous = {(r["kind"], r["id"]): r for group in records[:3] for r in group}
    original, delivered = {}, {}
    collections = (("shared_pools", pools, ("pools", "operations", "consumers")),
                   ("state_runtime", state, ("lifecycles", "timers")),
                   ("resource_counters", counters, ("counters",)),
                   ("resource_counter_views", model.get("resource_counter_views"), ("views",)))
    if has_threshold:
        collections += (('resource_threshold_observers', model.get('resource_threshold_observers'), ('observers',)),
                        ('resource_threshold_triggers', model.get('resource_threshold_triggers'), ('bindings',)))
    for field, actual, groups in collections:
        for group in groups:
            original.update({(group, r["id"]): r for r in (model.get(field) or {}).get(group, [])})
            delivered.update({(group, r["id"]): r for r in (actual or {}).get(group, [])})
    seeds, dependencies, owners, source_states = {}, {}, {}, {}

    def normalized(status):
        return 2 if "pending" in status else 1 if "excluded" in status else 0 if status == "ready" else 2

    def source_status(row, owner, path):
        parent = authority.get(row["source_id"])
        if parent and parent["owner"] != owner:
            raise PlanError("资源子图与权威规则的提供者不一致", "invalid_counter_view_source", {"path": path})
        if parent and parent["id"] in excluded: return "excluded"
        review = source_reviews.get(row["source_id"]) or {}
        if (parent is None or parent["id"] not in source_active or row.get("status") == "pending"
                or path.split('.')[1] in ('observers', 'bindings') and not row.get('source', '').strip()
                or review.get("mode") != "manual_context" or review.get("context_fingerprint") != context_fingerprint
                or not isinstance(review.get("note"), str) or not review["note"].strip()):
            return "pending"
        return "ready"

    def node(key, seed):
        seeds[key] = max(seeds.get(key, 0), seed)
        dependencies.setdefault(key, set())

    def edge(key, target):
        if target not in seeds:
            raise PlanError("资源读数依赖引用未登记节点", "invalid_counter_view_source", {"node": key, "target": target})
        dependencies[key].add(target)

    for rid, rule in rules.items():
        # Part applicability does not confirm the native rule's own evidence.
        # Keep this as a dependency failure, separate from the view's review.
        ready = rid in source_active and rule.get("status") != "pending" and bool(rule.get("source", "").strip())
        node(("rules", rid), 1 if rid in excluded else 0 if ready else 2)
    for key, row in sorted(original.items()):
        group, ident = key
        owner = (row["owner"] if group in ("pools", "lifecycles", "counters", "views", 'observers', 'bindings') else
                 original[("lifecycles", row["lifecycle_id"])]["owner"] if group == "timers" else
                 original[("pools", row["pool_id"])]["owner"])
        owners[key] = owner
        path = f"mechanics_model.{group}.{ident}"
        own = source_status(row, owner, path)
        source_states[key] = own
        if group in ('views', 'observers', 'bindings'):
            field, index = {'views': ('resource_counter_views', 3),
                            'observers': ('resource_threshold_observers', 4),
                            'bindings': ('resource_threshold_triggers', 5)}[group]
            record = {"kind": group, "id": ident, "source_id": row["source_id"], "owner": owner,
                      "path": f"mechanics_model.{field}.{group}.{ident}", "own_source_status": own}
            if group != 'views': record['name_zh'] = row['name_zh']
            parent = authority.get(row["source_id"])
            if parent: record["authority_rule_id"] = parent["id"]
            records[index].append(record)
            previous[key] = record
            seed = normalized(own)
            if own == "pending":
                missing('counter_view_source_scope_unreviewed' if group == 'views' else 'threshold_source_scope_unreviewed',
                        record['path'], ('资源读数需要唯一同提供者mechanics来源和当前完整manual_context确认' if group == 'views'
                                         else '资源阈值需要唯一同提供者mechanics来源和当前完整manual_context确认'), 'review_mechanics')
        else:
            record = previous.get(key)
            seed = normalized(record.get("effective_status", record.get("status", "pending"))) if record else 2
            # Missing execution copies must not be re-created from raw input.
            if seed == 0 and delivered.get(key) != row: seed = 2
            seed = max(seed, normalized(own))
        canonical = ("lifecycles", row["lifecycle_id"]) if group == "timers" else key
        node(canonical, seed)
        if timer_thresholds and group == 'timers':
            # Preserve the concrete timer identity in threshold dependencies,
            # while legacy timer/lifecycle pruning remains one atomic group.
            node(key, seed)

    for key, row in sorted(original.items()):
        group, ident = key
        canonical = ("lifecycles", row["lifecycle_id"]) if group == "timers" else key
        parent = authority.get(row["source_id"])
        if parent: edge(canonical, ("rules", parent["id"]))
        if timer_thresholds and group == 'timers': edge(key, canonical)
        if group in ("operations", "consumers", "counters"):
            edge(canonical, ("pools", row["pool_id"]))
        if group == "consumers":
            edge(canonical, ("rules", row["eligibility_rule_id"]))
        elif group == "counters":
            edge(canonical, ("lifecycles", row["lifecycle_id"]))
            for operation in row["operation_ids"]: edge(canonical, ("operations", operation))
        elif group == "views":
            edge(canonical, ("counters", row["counter_id"]))
        elif group == 'observers':
            if row['input']['kind'] == 'counter_value_key':
                edge(canonical, ('counters', row['input']['counter_id']))
            else:
                edge(canonical, ('pools', row['input']['pool_id']))
            for field, target_group in (('operation_ids', 'operations'), ('consumer_ids', 'consumers')):
                for ref in row['causes'][field]: edge(canonical, (target_group, ref))
        elif group == 'bindings':
            edge(canonical, ('observers', row['observer_id']))
            edge(canonical, ('rules' if row['payload']['kind'] == 'activate_rule' else 'lifecycles', row['payload']['ref_id']))
            observer = original[('observers', row['observer_id'])]
            for operation_id in observer['causes']['operation_ids']:
                origin = threshold_graph['operation_origins'].get(operation_id, {})
                if origin.get('kind') == 'ordinary_timer':
                    edge(canonical, ('timers', origin['timer_id']))
                    edge(canonical, ('lifecycles', origin['lifecycle_id']))
        if group in ("lifecycles", "timers"):
            if group == "lifecycles":
                for field in ("start", "end_trigger"):
                    selector = row.get(field, {})
                    if selector.get("kind") == "resource_threshold":
                        edge(canonical, ("bindings", selector["binding_id"]))
            payloads = [p for hooks in row["hooks"].values() for p in hooks] if group == "lifecycles" else [row["payload"]]
            for payload in payloads:
                target = ("rules" if payload["kind"] == "dispatch_rule" else "operations", payload["ref_id"])
                edge(canonical, target)
                edge(target, canonical)
    for rid, rule in rules.items():
        key = ("rules", rid)
        if rule.get("resource_gate"): edge(key, ("consumers", rule["resource_gate"]["consumer_id"]))
        if rule['trigger'].get('kind') == 'resource_threshold':
            edge(key, ('bindings', rule['trigger']['binding_id']))
        for effect in rule["effects"]:
            if is_counter_view(effect["value"]): edge(key, ("views", effect["value"]["view_id"]))
    # The validated original gate is a dependency, never an optional selector
    # that can disappear while its operation remains executable.
    for gate in model.get('resource_reaction_contract', {}).get('lifecycle_gates', []):
        edge(('operations', gate['operation_id']), ('lifecycles', gate['lifecycle_id']))
    # A ready source removed by an earlier helper is not revived. Use the
    # helper's immediate dependency reason where available, otherwise pending.
    for rid in source_active-set(active)-set(excluded):
        key = ("rules", rid)
        reason = max((seeds[d] for d in dependencies[key]), default=0)
        seeds[key] = max(seeds[key], reason or 2)
    states = dict(seeds)
    for _ in range(2*len(states)+1):
        next_states = {key: max([states[key], *(states[d] for d in dependencies[key])]) for key in sorted(states)}
        if next_states == states: break
        states = next_states
    else:
        raise PlanError("资源读数依赖闭包未在有限节点内收敛", "invalid_counter_view_source")
    status_name = {0: "ready", 1: "excluded", 2: "pending"}
    for group in records:
        for record in group:
            key = (record["kind"], record["id"])
            row = original[key]
            canonical = ("lifecycles", row["lifecycle_id"]) if key[0] == "timers" else key
            own, state_value = source_states[key], states[canonical]
            effective = ("ready" if state_value == 0 else own if normalized(own) == state_value
                         else "dependency_"+status_name[state_value])
            reason_prefix = 'resource_threshold_dependency_' if key[0] in ('observers', 'bindings') else 'resource_view_dependency_'
            record.update(own_source_status=own, effective_status=effective, status=effective,
                reason="current_context_reviewed" if state_value == 0 else reason_prefix+status_name[state_value],
                dependencies=[{"kind": d[0], "id": d[1], "status": status_name[states[d]]} for d in sorted(dependencies[canonical])])
        group.sort(key=lambda r: (r["kind"], r["id"]))
    for record in records[3]:
        if normalized(record["effective_status"]) == 2:
            missing("counter_view_dependency_pending", record["path"], "资源读数的来源或依赖仍待核验，已关闭其规则读者", "review_mechanics")
    for group in records[4:]:
        for record in group:
            if normalized(record['effective_status']) == 2:
                missing('threshold_dependency_pending', record['path'],
                        '阈值的来源或完整依赖仍待核验，不能按缩小后的场景计算', 'review_mechanics')

    def prune(field, actual, groups):
        if actual is None: return None
        result = copy.deepcopy(actual)
        for group in groups:
            result[group] = [r for r in result[group] if states[("lifecycles", r["lifecycle_id"]) if group == "timers" else (group, r["id"])] == 0]
        return result if result[groups[0]] else None
    result = {"pools": prune("shared_pools", pools, ("pools", "operations", "consumers")),
            "state": prune("state_runtime", state, ("lifecycles", "timers")),
            "counters": prune("resource_counters", counters, ("counters",)),
            "views": prune("resource_counter_views", model.get("resource_counter_views"), ("views",)),
            "disabled_rules": {rid for rid in rules if states[("rules", rid)] != 0},
            "pool_adoptions": records[0], "state_adoptions": records[1],
            "counter_adoptions": records[2], "view_adoptions": records[3]}
    if has_threshold:
        result.update(observers=prune('resource_threshold_observers', model.get('resource_threshold_observers'), ('observers',)),
                      triggers=prune('resource_threshold_triggers', model.get('resource_threshold_triggers'), ('bindings',)),
                      observer_adoptions=records[4], trigger_adoptions=records[5])
    if (model.get('resource_counter_views') or {}).get('schema_version') == 2:
        for adoption in result['view_adoptions']:
            adoption['sampling_contract'] = 'counter-self-frame-v2'
            adoption['readers'] = sorted([
                {'rule_id': rid, 'effect_id': effect['id'], 'domain': effect['domain'],
                 'sample': effect['value']['sample']}
                for rid, rule in rules.items() if states[('rules', rid)] == 0
                for effect in rule['effects'] if is_counter_view(effect['value'])
                and effect['value']['view_id'] == adoption['id']],
                key=lambda row: (row['rule_id'], row['effect_id']))
    return result
