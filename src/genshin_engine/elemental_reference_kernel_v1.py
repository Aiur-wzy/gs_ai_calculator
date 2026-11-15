"""Version-pinned target kernel reference; no production execution fallback."""

from __future__ import annotations

import copy
from types import FunctionType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from genshin_engine.elemental import (  # noqa: TC004 - injected canonical globals
        _EPS,
        ElementalPending,
        _icd_identity,
        _icd_signature,
    )
    from genshin_engine.elemental_schema import (  # noqa: TC004 - injected canonical globals
        FREEZE_RULESET,
        MULTI_AURA_RULESET,
    )
    from genshin_engine.rotation_port import (
        TICKS,  # noqa: TC004 - injected canonical globals
    )


def _canonical_advance_elemental_state_v1(self, time):
        """Advance only aura/freeze/crystallize state to one queue boundary."""
        if self.freeze_state:
            for transition in self.freeze_state.advance(time):
                self.trace.append({**transition, "target_id": self.target, "ruleset": FREEZE_RULESET,
                                   "event_id": f"{self.target}:state:{transition['reason']}:{transition['t']}",
                                   "source": "实验参考模型自然状态推进"})
            self.time = time
            self._prune_recent_elemental_applications()
            return
        if self.multi_state:
            for transition in self.multi_state.advance(time):
                self.trace.append({**transition, "target_id": self.target,
                                   "ruleset": MULTI_AURA_RULESET,
                                   "event_id": f"{self.target}:state:{transition['reason']}:{transition['t']}",
                                   "source": "reviewed-freeze-state-v1",
                                   "freeze_scenario": copy.deepcopy(self.multi_state.freeze_contract)})
            for transition in self.crystallize_state.advance(time):
                self.trace.append({**transition, "ruleset": MULTI_AURA_RULESET,
                                   "event_id": (f"{transition.get('target_id', self.target)}:crystallize-state:"
                                                f"{transition['type']}:{transition['t']}:"
                                                f"{transition.get('shard_id', transition.get('source_reaction_id', 'state'))}"),
                                   "source": "ordinary-crystallize-v1"})
            self.time = time
            self._prune_recent_elemental_applications()
            return
        if self.aura:
            self.aura["remaining_gu"] -= (time - self.time) / TICKS * self.aura["decay_gu_per_second"]
            if self.aura["remaining_gu"] <= _EPS:
                self.aura = None
        self.time = time
        self._prune_recent_elemental_applications()


def _canonical_prune_recent_elemental_applications_v1(self):
        cutoff = self.time - 5*TICKS
        self.recent_elemental_applications = [
            row for row in self.recent_elemental_applications
            if row["time_tick"] >= cutoff
        ][-256:]


def _canonical_record_recent_elemental_application_v1(self, row):
        self.recent_elemental_applications.append({
            "time_tick": self.time,
            "element": row["element"],
            "character": row["character"],
            "target_id": self.target,
            "area_id": row["area_id"],
            "source": row["event_id"],
        })
        self._prune_recent_elemental_applications()


def _canonical_rule_signature_v1(self, rule_group, icd):
        """Resolve a rule by its independent provenance identity.

        Normal plans are validated and fully indexed in ``__init__``.  The
        fallback preserves a small, long-standing test/debug affordance where
        callers replace a copied runtime profile after construction.
        """
        declared = _icd_signature(icd)
        known = self.icd_rules.get(rule_group)
        if known is None:
            self.icd_rules[rule_group] = declared
            return declared
        if known != declared:
            raise ValueError("同一 ICD 规则组在运行时出现了不同的规范化执行签名。")
        return known


def _canonical_admit_v1(self, actor, profile):
        icd = profile["icd"]
        if icd["kind"] == "unknown":
            raise ElementalPending(
                "unknown_icd",
                "该具体攻击组件的附着 ICD 尚未确定；请核实counter_tag、rule_group与序列，不能按动作类别推断。",
                profile["id"])
        if icd["kind"] == "none":
            return True, "no_icd", {"mode": "none", "consumed": False,
                                     "counter_tag": None, "rule_group": None,
                                     "shared_key": None, "sequence_index": None,
                                     "attempt_number": None, "coefficient": 1}
        counter_tag, rule_group, legacy = _icd_identity(icd)
        reset, rule = self._rule_signature(rule_group, icd)
        timer_counter = bool(rule and rule[0] == "timer_counter")
        hit_interval = rule[1] if timer_counter else None
        key = (actor, counter_tag, self.target)
        binding = (reset, tuple(rule))
        existing_binding = self.counter_bindings.get(key)
        if existing_binding is None:
            existing_binding = {"signature": binding, "profile_id": profile["id"],
                                "rule_groups": [rule_group]}
            self.counter_bindings[key] = existing_binding
        elif existing_binding["signature"] != binding:
            raise ValueError(
                "同一角色、计数标签与目标绑定了不同的ICD执行规则；请逐攻击组件核实counter_tag和规则参数。")
        elif rule_group not in existing_binding["rule_groups"]:
            existing_binding["rule_groups"].append(rule_group)
        state = self.icd.get(key)
        previous = state
        if previous is None or self.time >= previous["anchor"] + reset:
            state = self.icd[key] = {"anchor": self.time, "hits": 1, "reset_ticks": reset,
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
                 "shared_key": [actor, counter_tag, self.target],
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


def _canonical_observe_icd_v1(self, actor, profile):
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
        counter_tag, rule_group, legacy = _icd_identity(icd)
        reset, rule = self._rule_signature(rule_group, icd)
        timer_counter = bool(rule and rule[0] == "timer_counter")
        hit_interval = rule[1] if timer_counter else None
        key = (actor, counter_tag, self.target)
        state = self.icd.get(key)
        fresh = state is None or self.time >= state["anchor"] + reset
        audit = {"mode": "counter", "consumed": False, "legacy_group": legacy,
                 "counter_tag": counter_tag, "rule_group": rule_group,
                 "shared_key": [actor, counter_tag, self.target],
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


_REFERENCE_METHODS = {
    "_advance_elemental_state": _canonical_advance_elemental_state_v1,
    "_prune_recent_elemental_applications": _canonical_prune_recent_elemental_applications_v1,
    "_record_recent_elemental_application": _canonical_record_recent_elemental_application_v1,
    "_rule_signature": _canonical_rule_signature_v1,
    "_admit": _canonical_admit_v1,
    "_observe_icd": _canonical_observe_icd_v1,
}


def build_reference_elemental_kernel_v1(namespace: dict[str, Any]) -> dict[str, FunctionType]:
    result = {}
    for name, source in _REFERENCE_METHODS.items():
        clone = FunctionType(source.__code__, namespace, name, source.__defaults__, source.__closure__)
        clone.__kwdefaults__ = copy.deepcopy(source.__kwdefaults__)
        clone.__annotations__ = copy.deepcopy(source.__annotations__)
        result[name] = clone
    return result

