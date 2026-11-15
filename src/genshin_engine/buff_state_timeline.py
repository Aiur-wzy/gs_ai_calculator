"""Read-only finite Buff state receipts, independent of per-hit projections.

Intervals are half-open and describe declared rule availability, not a promise
that every attack benefits.  In particular, snapshot sampling, selectors and
frontline constraints are deliberately retained rather than guessed away.
"""
from __future__ import annotations

import copy
import json
import math
from bisect import bisect_right
from collections import defaultdict
from itertools import pairwise

from genshin_engine.constants import ENEMY_ZONES

CONTRACT = "buff-state-timeline-v1"


def buff_state_timeline_capabilities():
    return {"contract": CONTRACT, "version": 1, "result_field": "buff_state_timeline",
            "interval": "[start,end)", "slice_statuses": ["active", "exhausted"],
            "origins": ["mechanics_rule", "declared_interval"],
            "description": "明确持续规则和输入区间的只读状态；保留刷新、层数、次数耗尽与执行截断，不承诺每击受益。",
            "excluded": "无明确期限的固定条件收益、逐命中数值投影和没有收益的资源规则不推断持续区间。"}


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _identity(source, *, fixed=False):
    return {"fixed_source_id" if fixed else "rule_id": source.get("id"),
            "source_id": source.get("source_id"), "owner": source.get("owner"),
            "name_zh": source.get("name_zh", "机制增益"), "source": source.get("source", ""),
            "evidence_status": source.get("status")}


def _state_id(origin, instance, beneficiary):
    # Tuple encoding avoids collisions even when user-authored IDs contain ':'.
    return json.dumps([origin, instance, beneficiary], ensure_ascii=False, separators=(",", ":"))


def _effect_applicability(effects, beneficiaries, characters, scope, *, quota=False, manual=False):
    output = []
    for effect in effects:
        domain = ("enemy" if effect.get("zone") in ENEMY_ZONES else "self") if manual else effect.get("domain")
        enemy = domain == "enemy"
        possible = characters if enemy and not quota else beneficiaries
        frontline = not enemy and scope in ("current_frontline", "frontline")
        message = ("敌方收益不受实例角色或前台范围限制；队友命中同目标也可能受益。" if enemy and not quota else
                   "敌方收益仍需该次命中的次数消费回执；不能扩展实例之外角色的次数池。" if enemy else
                   "角色收益受声明角色范围约束；实际命中还须通过收益筛选和取样。")
        if frontline:
            message += "此项另受当前前台限制。"
        output.append({"effect_id": effect.get("id"), "domain": domain,
            "recipient_mode": "enemy_shared" if enemy else "declared_recipients" if manual else "instance_beneficiaries",
            "possible_characters": copy.deepcopy(possible), "requires_quota_receipt": quota,
            "frontline_required": frontline, "message": message})
    return output


def _resource_gate(rule, model):
    """Return self-contained display metadata for an accepted shared-pool gate.

    The damage runtime remains authoritative for each consume decision.  This
    projection only lets saved results explain why an otherwise-active rule can
    still be unavailable for a particular hit.
    """
    gate = rule.get("resource_gate")
    if not gate:
        return None
    config = model.get("shared_pools", {})
    consumer = next((row for row in config.get("consumers", [])
                     if row.get("id") == gate.get("consumer_id")), None)
    if not consumer:
        return {"consumer_id": gate.get("consumer_id"), "status": "unresolved",
                "receipt_field": "shared_pools.trace"}
    pool = next((row for row in config.get("pools", [])
                 if row.get("id") == consumer.get("pool_id")), None)
    return {"consumer_id": consumer.get("id"), "pool_id": consumer.get("pool_id"),
            "actor_relation": consumer.get("actor_relation"), "amount": consumer.get("amount"),
            "filter": copy.deepcopy(consumer.get("filter", {})),
            "status": consumer.get("status"), "source_id": consumer.get("source_id"),
            "pool": copy.deepcopy(pool) if pool else None,
            "receipt_field": "shared_pools.trace"}


def _resource_gate_recipients(rule, recipients, gate):
    """Narrow possible recipients by explicit consumer identity constraints."""
    if not gate or gate.get("status") == "unresolved":
        return list(recipients)
    relation, owner = gate.get("actor_relation"), rule.get("owner")
    allowed = list(recipients)
    if relation == "owner":
        allowed = [recipient for recipient in allowed if recipient == owner]
    elif relation == "other_frontline":
        allowed = [recipient for recipient in allowed if recipient != owner]
    characters = gate.get("filter", {}).get("characters")
    if isinstance(characters, list) and characters:
        allowed = [recipient for recipient in allowed if recipient in characters]
    return allowed


def _resource_balance_projection(gate, recipient, shared_pool_trace, start, end):
    """Project post-event pool availability inside one declared rule interval.

    The shared-pool runtime remains authoritative for the hit at a boundary.
    Slices describe the balance after all retained receipts at that timestamp;
    their point-event ordering remains available in ``shared_pools.trace``.
    ``None`` trace is the legacy call contract and deliberately makes no claim
    about resource availability.
    """
    if shared_pool_trace is None or not gate or gate.get("status") == "unresolved":
        return None
    pool = gate.get("pool")
    amount = gate.get("amount")
    if not isinstance(pool, dict) or not _finite(amount) or amount <= 0:
        return None
    key = "team" if pool.get("scope") == "team" else recipient
    initial = pool.get("initial", {})
    if not isinstance(initial, dict) or not _finite(initial.get(key)):
        return None
    if not isinstance(shared_pool_trace, (list, tuple)):
        raise TypeError("共享资源流水必须为数组。")
    rows = []
    for trace_index, row in enumerate(shared_pool_trace):
        if not isinstance(row, dict) or row.get("pool_id") != pool.get("id") or row.get("key") != key:
            continue
        kind = row.get("type", row.get("kind"))
        if kind not in ("operation", "consume"):
            continue
        time = row.get("t")
        if not _finite(time):
            time_us = row.get("t_us")
            time = time_us / 1_000_000 if type(time_us) is int else None
        after = row.get("after")
        if not _finite(time) or not _finite(after) or after < 0:
            raise ValueError("共享资源流水包含无效的时间或余额。")
        rows.append({"t": time, "after": after, "trace_index": trace_index})
    rows.sort(key=lambda row: (row["t"], row["trace_index"]))
    # Collapse same-timestamp receipts to their final post-event balance.  The
    # exact pre/post order still lives in the point ledger and decides the hit.
    grouped = []
    for row in rows:
        if grouped and grouped[-1]["t"] == row["t"]:
            grouped[-1]["after"] = row["after"]
            grouped[-1]["trace_indices"].append(row["trace_index"])
        else:
            grouped.append({"t": row["t"], "after": row["after"],
                            "trace_indices": [row["trace_index"]]})
    balance = initial[key]
    for row in grouped:
        if row["t"] <= start:
            balance = row["after"]
    changes = []
    current = balance
    for row in grouped:
        if not start < row["t"] < end:
            continue
        if row["after"] != current:
            changes.append(copy.deepcopy(row))
        current = row["after"]
    metadata = {"pool_id": pool.get("id"), "pool_key": key, "required": amount,
                "unit_scale": pool.get("unit_scale", 1),
                "quantity_unit": "atoms" if pool.get("unit_scale", 1) != 1 else "count",
                "receipt_field": gate.get("receipt_field", "shared_pools.trace")}
    return metadata, balance, changes


def _execution_limit(rotation, trace, duration, status):
    log = rotation.get("event_log", [])
    failures = [r["t"] for r in log if r.get("status") in ("failed", "not_verified", "pending")
                and _finite(r.get("t"))]
    if failures:
        return max(0, min(duration, *failures))
    # Some legacy pending exceptions have no retained rotation prefix.  They
    # cannot certify the rest of the window.  A complete checked input window
    # remains usable even when later cyclic/energy proof is unproven.
    if (status in ("invalid", "pending", "failed")
            and not rotation.get("energy_check", {}).get("completed_cycles", 0)):
        observed = [r["t"] for r in [*log, *trace] if _finite(r.get("t"))]
        return max(0, min(duration, max(observed, default=0)))
    return duration


def _end_reason(end, natural, limit, duration, *, next_time=None, expiry_time=None):
    if end == limit and limit < duration:
        return "execution_limit"
    if end == natural or end == expiry_time:
        return "natural_expiry"
    if end == next_time:
        return "refresh"
    return "window_end"


def build_buff_state_timeline(mechanics_model, mechanics_trace, rotation_check, *, duration,
                             execution_status, template=None, shared_pool_trace=None,
                             include_resource_gate=True):
    """Build a deterministic receipt from already validated execution inputs.

    ``template`` is the pre-projection execution input.  Passing the resolved
    per-hit template cannot recover original manual/composed Buff intervals.
    This pure builder is also used to validate saved calculation packages.
    ``include_resource_gate=False`` is reserved for strict reconstruction of
    receipts written before resource-gate metadata joined this v1 contract.
    """
    if not _finite(duration) or duration < 0:
        raise ValueError("Buff 状态窗口必须为有限非负数。")
    model, rotation = mechanics_model or {}, rotation_check or {}
    characters = [c["id"] for c in (template or {}).get("characters", rotation.get("characters", []))]
    trace = [dict(row, trace_index=i) for i, row in enumerate(mechanics_trace or [])]
    limit = _execution_limit(rotation, trace, duration, execution_status)
    output = {"contract": CONTRACT, "version": 1, "duration": duration,
              "execution_limit": limit, "execution_status": execution_status,
              "states": [], "excluded": [], "notes": [
                  "持续条表示声明规则或明确输入区间的状态；筛选、前台限制、目标条件和快照取样仍以逐击计算为准。",
                  "区间右端不包含；同刻事件的先后与该次命中收益请查看计算流水。"]}
    if limit < duration:
        output["notes"].append("执行在输入窗口内中断，持续状态仅保留已执行前缀；中断之后不推测有效。")
    rules = {r["id"]: r for r in model.get("rules", [])}
    activations, expiry, quota_rows = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in trace:
        if not _finite(row.get("t")) or not 0 <= row["t"] <= duration or not row.get("instance_id"):
            continue
        if row.get("type") == "activation" and row["t"] < limit:
            activations[row["instance_id"]].append(row)
        elif row.get("type") == "expiry" and row.get("layers_after") == 0:
            expiry[row["instance_id"]].append(row)
        elif row.get("type") == "quota":
            quota_rows[(row["instance_id"], row.get("pool"))].append(row)
    initial_count = 0
    for ri, state in enumerate(model.get("initial_state", {}).get("rules", [])):
        for ii, inst in enumerate(state.get("instances", [])):
            instance = f"{state['rule_id']}:initial:{ii}"
            if limit > 0:
                activations[instance].append({"type": "activation", "rule_id": state["rule_id"],
                    "instance_id": instance, "t": 0, "trace_index": -(ri * 10000 + ii + 1),
                    "origin": "initial_state", "beneficiaries": inst.get("beneficiaries", []),
                    "expires_at": inst.get("remaining", []), "quota": inst.get("quota", {})})
                initial_count += 1
    if initial_count:
        output["notes"].append("起点活跃状态来自显式初态，不推断窗口之前的施放或触发。")
    represented = set()
    resource_projected = False
    states = {}
    quota_indices = {key: [r["trace_index"] for r in rows] for key, rows in quota_rows.items()}
    for instance, rows in activations.items():
        rows.sort(key=lambda r: (r["t"], r["trace_index"]))
        for ai, activation in enumerate(rows):
            rid = activation["rule_id"]
            rule = rules.get(rid)
            if not rule or not rule.get("effects"):
                continue
            expires = sorted(t for t in activation.get("expires_at", []) if _finite(t) and t > activation["t"])
            if not expires:
                continue
            natural = expires[-1]
            next_row = rows[ai + 1] if ai + 1 < len(rows) else None
            next_time = next_row["t"] if next_row else None
            actual_expiries = [r["t"] for r in expiry[instance]
                               if r["trace_index"] > activation["trace_index"] and r["t"] >= activation["t"]]
            expiry_time = min(actual_expiries) if actual_expiries else None
            end = min(natural, limit, duration, next_time if next_time is not None else duration,
                      expiry_time if expiry_time is not None else duration)
            if end <= activation["t"]:
                continue
            ending = _end_reason(end, natural, limit, duration, next_time=next_time, expiry_time=expiry_time)
            resource_gate = _resource_gate(rule, model) if include_resource_gate else None
            beneficiaries = _resource_gate_recipients(rule, activation.get("beneficiaries", []), resource_gate)
            for recipient in beneficiaries:
                quota = activation.get("quota", {})
                pool = "team" if "team" in quota else recipient
                limited = pool in quota
                key = (instance, pool)
                indices, all_changes = quota_indices.get(key, []), quota_rows[key]
                lo = bisect_right(indices, activation["trace_index"])
                hi = bisect_right(indices, next_row["trace_index"]) if next_row else len(indices)
                changes = [r for r in all_changes[lo:hi] if activation["t"] <= r["t"] < end]
                changes.sort(key=lambda r: (r["t"], r["trace_index"]))
                resource = _resource_balance_projection(resource_gate, recipient, shared_pool_trace,
                                                        activation["t"], end)
                resource_changes = resource[2] if resource else []
                resource_projected = resource_projected or resource is not None
                cuts = sorted({activation["t"], end, *[t for t in expires if t < end],
                               *[r["t"] for r in changes], *[r["t"] for r in resource_changes]})
                sid = _state_id("mechanics_rule", instance, recipient)
                state = states.setdefault(sid, {**_identity(rule), "id": sid, "origin": "mechanics_rule",
                    "instance_id": instance, "beneficiary": recipient,
                    "beneficiaries": copy.deepcopy(beneficiaries),
                    "beneficiaries_scope": copy.deepcopy(rule.get("beneficiaries", {})),
                    "trigger": copy.deepcopy(rule.get("trigger", {})),
                    **({"resource_gate": copy.deepcopy(resource_gate)} if resource_gate else {}),
                    "effects": copy.deepcopy(rule["effects"]),
                    "effect_applicability": _effect_applicability(rule["effects"], beneficiaries, characters,
                        rule.get("beneficiaries", {}).get("scope"),
                        quota=bool(rule.get("quota") or resource_gate)),
                    "filters": [{"effect_id": e["id"], "filter": copy.deepcopy(e["filter"])}
                                for e in rule["effects"] if "filter" in e],
                    "start": activation["t"], "end": end, "end_reason": ending, "slices": []})
                remaining, ci = quota.get(pool) if limited else None, 0
                resource_remaining, resource_ci = (resource[1], 0) if resource else (None, 0)
                quota_times = {r["t"] for r in changes}
                resource_times = {r["t"] for r in resource_changes}
                expiry_times = set(expires)
                for start, stop in pairwise(cuts):
                    while ci < len(changes) and changes[ci]["t"] <= start:
                        remaining = changes[ci]["after"] if limited else None
                        ci += 1
                    while resource_ci < len(resource_changes) and resource_changes[resource_ci]["t"] <= start:
                        resource_remaining = resource_changes[resource_ci]["after"]
                        resource_ci += 1
                    layers = len(expires) - bisect_right(expires, start)
                    reason = (ending if stop == end else "layer_expiry" if stop in expiry_times else
                              "resource_change" if stop in resource_times else "quota_change")
                    resource_balance = ({**resource[0], "remaining": resource_remaining,
                        "status": "available" if resource_remaining >= resource[0]["required"] else "exhausted"}
                        if resource else None)
                    exhausted = ((limited and remaining <= 0)
                                 or (resource_balance is not None and resource_balance["status"] == "exhausted"))
                    state["slices"].append({"start": start, "end": stop,
                        "status": "exhausted" if exhausted else "active", "layers": layers,
                        "remaining_quota": remaining, "quota_pool": pool if limited else None,
                        **({"resource_balance": resource_balance} if resource_balance is not None else {}),
                        "activation_time": activation["t"], "activation_trace_index": activation["trace_index"],
                        "activation_event_id": activation.get("event_id"),
                        "origin": activation.get("origin", "activation"),
                        "rule_expires_at": natural, "end_reason": reason,
                        "quota_changed_at_end": stop in quota_times,
                        **({"resource_changed_at_end": stop in resource_times} if resource else {}),
                        **({"beneficiary_selection": copy.deepcopy(activation["beneficiary_selection"])}
                           if "eligibility" in rule.get("beneficiaries", {})
                           and "beneficiary_selection" in activation else {})})
                state.update(end=end, end_reason=ending)
                represented.add(rid)
    output["states"] = list(states.values())
    if resource_projected:
        output["notes"].append(
            "受共享资源门控的持续条仍保留规则期限；active/exhausted仅表示事件后余额是否足够下一次消费，"
            "同刻命中是否实际受益以共享资源消费回执为准。")
    for rule in rules.values():
        if rule["id"] in represented:
            continue
        code, message = ("no_effects", "规则仅有资源等事件，没有持续数值收益。") if not rule.get("effects") else (
            "no_finite_interval", "输入窗口已执行部分没有可展示的有限激活区间；不会根据逐击收益猜测持续时间。")
        output["excluded"].append({**_identity(rule), "code": code, "message": message})
    for source in model.get("fixed_effect_sources", []):
        output["excluded"].append({**_identity(source, fixed=True), "code": "fixed_condition_without_duration",
            "message": "固定条件收益没有显式持续期限；按命中前目标条件检查，不绘制全轴持续条。"})
    _declared_intervals(output, template or {})
    output["states"].sort(key=lambda s: (s["start"], s["id"]))
    return output


def _declared_intervals(output, template):
    chars = [c["id"] for c in template.get("characters", [])]
    limit, duration = output["execution_limit"], output["duration"]
    for buff in template.get("buffs", []):
        bid = buff.get("id", "")
        if (bid.startswith("mechanics:") or buff.get("source") == "声明式机制逐事件投影"
                or buff.get("name", "").startswith("机制派生收益")):
            continue
        start, natural = buff.get("start"), buff.get("end")
        if not _finite(start) or not _finite(natural) or natural <= start:
            continue
        end = min(natural, limit, duration)
        if end <= start:
            continue
        enemy = any(e.get("zone") in ENEMY_ZONES for e in buff.get("effects", []))
        declared = [buff["target"]] if buff.get("target") else chars
        beneficiaries = chars if enemy else declared
        extension = buff.get("extensions", {})
        for recipient in beneficiaries:
            instance = "declared:" + bid
            sid = _state_id("declared_interval", instance, recipient)
            reason = _end_reason(end, natural, limit, duration)
            output["states"].append({"id": sid, "origin": "declared_interval", "buff_id": bid,
                "rule_id": None, "source_id": extension.get("source_id", bid),
                "owner": extension.get("owner", buff.get("target")), "name_zh": buff.get("name") or buff.get("source") or "输入增益",
                "source": buff.get("source", ""), "evidence_status": "declared", "instance_id": instance,
                "beneficiary": recipient, "beneficiaries": copy.deepcopy(beneficiaries),
                "beneficiaries_scope": {"scope": buff.get("scope", "team"), "target": buff.get("target"),
                                         "enemy_effects_ignore_scope": enemy},
                "trigger": None, "effects": copy.deepcopy(buff.get("effects", [])),
                "effect_applicability": _effect_applicability(buff.get("effects", []), declared, chars,
                    buff.get("scope", "team"), manual=True),
                "filters": [{"effect_id": e.get("id"), "filter": copy.deepcopy(e["applies_to"])}
                                               for e in buff.get("effects", []) if "applies_to" in e],
                "buff": copy.deepcopy(buff), "start": start, "end": end, "end_reason": reason,
                "slices": [{"start": start, "end": end, "status": "active", "layers": 1,
                    "remaining_quota": None, "quota_pool": None, "activation_time": start,
                    "activation_trace_index": None, "activation_event_id": None, "origin": "declared_interval",
                    "rule_expires_at": natural, "end_reason": reason, "quota_changed_at_end": False}]})


__all__ = ["CONTRACT", "buff_state_timeline_capabilities", "build_buff_state_timeline"]
