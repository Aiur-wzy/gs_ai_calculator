"""One target's ICD counter kernel; no actor or action-category dispatch."""

from __future__ import annotations

import copy
from typing import Any, Protocol

from genshin_engine.rotation_port import TICKS


class ICDKernelPort(Protocol):
    time: int
    target: str
    icd_rules: dict[str, Any]
    icd: dict[tuple[str, str, str], Any]
    counter_bindings: dict[tuple[str, str, str], Any]

    def rule_signature(self, rule_group, icd): ...


def rule_signature(kernel: ICDKernelPort, rule_group, icd, *, signature):
    """Resolve a rule by its independent provenance identity.

    Normal plans are validated and fully indexed in ``__init__``.  The
    fallback preserves a small, long-standing test/debug affordance where
    callers replace a copied runtime profile after construction.
    """
    declared = signature(icd)
    known = kernel.icd_rules.get(rule_group)
    if known is None:
        kernel.icd_rules[rule_group] = declared
        return declared
    if known != declared:
        raise ValueError("同一 ICD 规则组在运行时出现了不同的规范化执行签名。")
    return known


def admit(kernel: ICDKernelPort, actor, profile, *, identity, pending_error):
    icd = profile["icd"]
    if icd["kind"] == "unknown":
        raise pending_error(
            "unknown_icd",
            "该具体攻击组件的附着 ICD 尚未确定；请核实counter_tag、rule_group与序列，不能按动作类别推断。",
            profile["id"])
    if icd["kind"] == "none":
        return True, "no_icd", {"mode": "none", "consumed": False,
                                 "counter_tag": None, "rule_group": None,
                                 "shared_key": None, "sequence_index": None,
                                 "attempt_number": None, "coefficient": 1}
    counter_tag, rule_group, legacy = identity(icd)
    reset, rule = kernel.rule_signature(rule_group, icd)
    timer_counter = bool(rule and rule[0] == "timer_counter")
    hit_interval = rule[1] if timer_counter else None
    key = (actor, counter_tag, kernel.target)
    binding = (reset, tuple(rule))
    existing_binding = kernel.counter_bindings.get(key)
    if existing_binding is None:
        existing_binding = {"signature": binding, "profile_id": profile["id"],
                            "rule_groups": [rule_group]}
        kernel.counter_bindings[key] = existing_binding
    elif existing_binding["signature"] != binding:
        raise ValueError(
            "同一角色、计数标签与目标绑定了不同的ICD执行规则；请逐攻击组件核实counter_tag和规则参数。")
    elif rule_group not in existing_binding["rule_groups"]:
        existing_binding["rule_groups"].append(rule_group)
    state = kernel.icd.get(key)
    previous = state
    if previous is None or kernel.time >= previous["anchor"] + reset:
        state = kernel.icd[key] = {"anchor": kernel.time, "hits": 1, "reset_ticks": reset,
                                "sequence_length": None if timer_counter else len(rule),
                                "hit_interval": hit_interval,
                                "rule_group": existing_binding["rule_groups"][0],
                                "rule_groups": copy.deepcopy(existing_binding["rule_groups"])}
        allowed = True if timer_counter else bool(rule[0])
        reason = "first_hit" if previous is None else "icd_timer_reset"
    else:
        state["rule_groups"] = copy.deepcopy(existing_binding["rule_groups"])
        state["hits"] += 1
        allowed = ((state["hits"] - 1) % hit_interval == 0 if timer_counter
                   else state["hits"] <= len(rule) and bool(rule[state["hits"] - 1]))
        reason = "icd_hit_counter" if timer_counter else "icd_hit_sequence"
    index = state["hits"] - 1
    audit = {"mode": "counter", "consumed": True, "legacy_group": legacy,
             "counter_tag": counter_tag, "rule_group": rule_group,
             "shared_key": [actor, counter_tag, kernel.target],
             "sequence_index": index, "attempt_number": state["hits"],
             "coefficient": int(allowed),
             "sequence_length": None if timer_counter else len(rule),
             "anchor_t": state["anchor"] / TICKS,
             "reset_at": (state["anchor"] + reset) / TICKS,
             "reset_seconds": reset / TICKS}
    if len(existing_binding["rule_groups"]) > 1:
        audit["shared_rule_groups"] = copy.deepcopy(existing_binding["rule_groups"])
    if timer_counter:
        audit.update(sequence_length=None, hit_interval=hit_interval, first_hit=1)
    return allowed, reason if allowed else "icd_blocked", audit


def observe_icd(kernel: ICDKernelPort, actor, profile, *, identity):
    """Describe an ICD binding without advancing its counter.

    Misses and explicit no-application hits still need an audit receipt so
    callers can prove that the event did not consume a sequence position.
    """
    icd = profile.get("icd")
    if not icd:
        return {"mode": "not_applicable", "consumed": False,
                "counter_tag": None, "rule_group": None, "shared_key": None,
                "sequence_index": None, "attempt_number": None, "coefficient": None}
    if icd["kind"] == "none":
        return {"mode": "none", "consumed": False,
                "counter_tag": None, "rule_group": None, "shared_key": None,
                "sequence_index": None, "attempt_number": None, "coefficient": None}
    if icd["kind"] == "unknown":
        return {"mode": "unknown", "consumed": False,
                "counter_tag": None, "rule_group": None, "shared_key": None,
                "sequence_index": None, "attempt_number": None, "coefficient": None}
    counter_tag, rule_group, legacy = identity(icd)
    reset, rule = kernel.rule_signature(rule_group, icd)
    timer_counter = bool(rule and rule[0] == "timer_counter")
    hit_interval = rule[1] if timer_counter else None
    key = (actor, counter_tag, kernel.target)
    state = kernel.icd.get(key)
    fresh = state is None or kernel.time >= state["anchor"] + reset
    audit = {"mode": "counter", "consumed": False, "legacy_group": legacy,
             "counter_tag": counter_tag, "rule_group": rule_group,
             "shared_key": [actor, counter_tag, kernel.target],
             "sequence_index": 0 if fresh else state["hits"],
             "attempt_number": 1 if fresh else state["hits"] + 1,
             "coefficient": None, "sequence_length": None if timer_counter else len(rule),
             "reset_seconds": reset / TICKS}
    if timer_counter:
        audit.update(hit_interval=hit_interval, first_hit=1)
    if state is not None and not fresh:
        audit.update(anchor_t=state["anchor"] / TICKS,
                     reset_at=(state["anchor"] + reset) / TICKS)
    return audit

