"""Tie resource components to existing reviewed mechanics source parts."""
from __future__ import annotations

import copy

from .schema import PlanError


def prepare_shared_pools(model, rules, active, linked, excluded, missing, *, source_reviews, context_fingerprint):
    config = model.get("shared_pools")
    if config is None:
        return None, set(), []
    authority = {}
    for rid, rule in rules.items():
        if rid not in linked:
            continue
        if rule["source_id"] in authority:
            raise PlanError("共享池的来源部件不能由多条权威规则重复交付", "duplicate_delivery")
        authority[rule["source_id"]] = rule
    pools = {row["id"]: row for row in config["pools"]}
    records, pending = [], False
    for kind in ("pools", "operations", "consumers"):
        for row in config[kind]:
            path = f"mechanics_model.shared_pools.{kind}.{row['id']}"
            source_rule = authority.get(row["source_id"])
            pool = row if kind == "pools" else pools.get(row["pool_id"])
            if pool is None:
                raise PlanError("共享资源项引用不存在的池", details={"path": path})
            record = {"kind": kind, "id": row["id"], "source_id": row["source_id"],
                      "pool_id": pool["id"], "status": "pending", "path": path}
            records.append(record)
            if source_rule is None:
                pending = True
                missing("pool_source_pending", path + ".source_id", "共享资源需要已登记的mechanics来源部件，不能只填来源字符串", "edit_mechanics")
                continue
            rid = source_rule["id"]
            record["authority_rule_id"] = rid
            if source_rule["owner"] != pool["owner"]:
                raise PlanError("共享资源与来源规则的提供者不一致", details={"path": path, "rule_id": rid})
            if kind == "consumers" and (row["eligibility_rule_id"] != rid
                    or source_rule.get("resource_gate") != {"consumer_id": row["id"]}):
                raise PlanError("共享消费必须与门控收益规则一一对应且同源", details={"path": path})
            if rid in excluded:
                record.update(status="excluded", reason="source_part_excluded")
                continue
            dependency = source_reviews.get(row["source_id"], {})
            if (dependency.get("mode") != "manual_context"
                    or dependency.get("context_fingerprint") != context_fingerprint
                    or not str(dependency.get("note", "")).strip()):
                pending = True
                missing("pool_source_scope_unreviewed", path + ".source_id",
                        "资源子图需要显式mechanics来源部件的当前manual_context确认；旧source_only或自动规则身份不能批准新增资源", "review_mechanics")
            elif rid in active:
                record["status"] = "ready"
            else:
                pending = True
                missing("pool_source_pending", path + ".source_id", "共享资源的来源尚未就绪；未就绪不等于明确排除", "edit_mechanics")
    if pending:
        # Compose remains pending. Do not project an unqualified partial graph,
        # and do not leave a formerly gated benefit live without its gate.
        for record in records:
            if record["status"] == "ready":
                record.update(status="pending", reason="shared_resource_dependency_pending")
        return None, {rid for rid, rule in rules.items() if "resource_gate" in rule}, records

    disabled_rules = set(excluded)
    disabled_pools, disabled_consumers = set(), set()
    # A component excluded by a dependency can itself own another pool. Close
    # the finite dependency graph before changing the execution-only model.
    while True:
        previous = (len(disabled_rules), len(disabled_pools), len(disabled_consumers))
        disabled_pools.update(row["id"] for row in config["pools"]
                              if authority[row["source_id"]]["id"] in disabled_rules)
        for row in config["consumers"]:
            if row["pool_id"] in disabled_pools or row["eligibility_rule_id"] in disabled_rules:
                disabled_consumers.add(row["id"])
                disabled_rules.add(row["eligibility_rule_id"])
        if previous == (len(disabled_rules), len(disabled_pools), len(disabled_consumers)):
            break
    result = copy.deepcopy(config)
    for kind in ("pools", "operations", "consumers"):
        kept = []
        for row in result[kind]:
            pool_id = row["id"] if kind == "pools" else row["pool_id"]
            disabled = (pool_id in disabled_pools or authority[row["source_id"]]["id"] in disabled_rules
                        or kind == "consumers" and row["id"] in disabled_consumers)
            if disabled:
                record = next(r for r in records if r["kind"] == kind and r["id"] == row["id"])
                if record["status"] != "excluded":
                    record.update(status="dependency_excluded", reason="resource_authority_excluded")
            else:
                kept.append(row)
        result[kind] = kept
    return (result if result["pools"] else None), disabled_rules - set(excluded), records
