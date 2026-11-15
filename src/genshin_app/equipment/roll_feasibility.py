"""Necessary aggregate artifact-roll bounds; never an item allocator or gate."""
from __future__ import annotations

import math
from collections import deque

SUBSTATS = frozenset({
    "FIGHT_PROP_HP", "FIGHT_PROP_ATTACK", "FIGHT_PROP_DEFENSE",
    "FIGHT_PROP_HP_PERCENT", "FIGHT_PROP_ATTACK_PERCENT", "FIGHT_PROP_DEFENSE_PERCENT",
    "FIGHT_PROP_ELEMENT_MASTERY", "FIGHT_PROP_CHARGE_EFFICIENCY",
    "FIGHT_PROP_CRITICAL", "FIGHT_PROP_CRITICAL_HURT",
})
EPS = 1e-8


def aggregate_roll_feasibility(artifacts, rolls):
    """Test upper bounds only, permitting explicitly authorized fractional rolls.

    A feasible flow is not a proof of actual initial lines, upgrade outcomes or
    five realizable items. Missing rarity rules remain unknown, never guessed.
    """
    result = {"contract": "aggregate-roll-feasibility-v1", "authority": "advisory_only",
              "status": "unknown", "proof_scope": "necessary_aggregate_bounds_not_item_allocation",
              "issues": []}
    if not artifacts or artifacts.get("rarity") != 5:
        result["reason"] = "需要五星主属性模板；其他稀有度的容量规则未在本检查中证明。"
        return result
    level = artifacts["level"]
    upgrades = level // 4
    per_stat, per_piece = 1 + upgrades, 4 + upgrades
    mains = ["FIGHT_PROP_HP", "FIGHT_PROP_ATTACK",
             artifacts["sands"], artifacts["goblet"], artifacts["circlet"]]
    demand = {}
    for row in rolls:
        count = row["count"]
        if count > 0:
            demand[row["prop_type"]] = math.fsum([demand.get(row["prop_type"], 0), count])
    for prop, count in demand.items():
        available = [slot for slot, main in zip(("flower", "feather", "sands", "goblet", "circlet"), mains, strict=True)
                     if main != prop]
        maximum = len(available) * per_stat if prop in SUBSTATS else 0
        if count > maximum + EPS:
            result["issues"].append({"code": "substat_capacity_exceeded", "prop_type": prop,
                "requested": count, "maximum": maximum, "available_slots": available,
                "message": "主副属性不能相同，单项词条还受可出现部位和升级次数限制。"})
    total = math.fsum(demand.values())
    result.update(requested_total=total, maximum_total=5 * per_piece,
                  maximum_per_stat_per_piece=per_stat, initial_lines_upper_bound_per_piece=4,
                  upgrades_per_piece=upgrades)
    if total > 5 * per_piece + EPS:
        result["issues"].append({"code": "aggregate_capacity_exceeded", "requested": total,
                                "maximum": 5 * per_piece,
                                "message": "聚合词条数超过五件主属性模板的最大初始及升级容量。"})
    # One bipartite flow additionally catches simultaneous demands competing
    # for the same eligible pieces; free strings cannot create new roll types.
    props = sorted(demand)
    sink, size = 1 + len(props) + 5, 2 + len(props) + 5
    residual = [[0.0] * size for _ in range(size)]
    for index, prop in enumerate(props, 1):
        residual[0][index] = demand[prop]
        for slot, main in enumerate(mains, 1 + len(props)):
            if prop in SUBSTATS and prop != main:
                residual[index][slot] = float(per_stat)
    slot_bounds = []
    for index, slot in enumerate(range(1 + len(props), sink)):
        eligible_types = sum(prop in SUBSTATS and prop != mains[index] for prop in props)
        # Unrequested initial lines cannot turn into additional upgrades of a
        # requested stat. At most min(4,k) initial requested lines plus upgrades.
        bound = min(4, eligible_types) + upgrades if eligible_types else 0
        residual[slot][sink] = float(bound)
        slot_bounds.append(bound)
    result["requested_type_slot_upper_bounds"] = slot_bounds
    delivered = 0.0
    while delivered + EPS < total:
        parent, queue = {0: None}, deque([0])
        while queue and sink not in parent:
            node = queue.popleft()
            for other, capacity in enumerate(residual[node]):
                if capacity > EPS and other not in parent:
                    parent[other] = node
                    queue.append(other)
        if sink not in parent:
            break
        amount, node = total - delivered, sink
        while parent[node] is not None:
            amount = min(amount, residual[parent[node]][node])
            node = parent[node]
        node = sink
        while parent[node] is not None:
            before = parent[node]
            residual[before][node] -= amount
            residual[node][before] += amount
            node = before
        delivered += amount
    if delivered + EPS < total and not result["issues"]:
        result["issues"].append({"code": "shared_slot_capacity_exceeded", "requested": total,
            "maximum_allocatable_upper_bound": delivered,
            "message": "多个属性争用相同可用部位，无法在聚合升级容量内同时放入。"})
    result["status"] = "infeasible" if result["issues"] else "bounds_passed"
    result["reason"] = ("仅提示；不修改面板，不阻止直接给定成品面板。" if result["issues"] else
                        "未违反已检查的必要上界；不代表已生成或证明五件真实圣遗物。")
    return result
