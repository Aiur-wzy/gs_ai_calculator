"""One resolver contact over the existing target's aura state and receipt.

This object captures inputs and route-audit helpers. It owns no persistent
elemental state, world clock, queue, target registry or publication cursor.
"""

from __future__ import annotations

import copy
from typing import Any, Protocol


class MultiAuraResolverPort(Protocol):
    time: int
    multi_state: Any
    crystallize_state: Any

    def multi_reaction(self, *args, **kwargs): ...

    def multi_proposal(self, *args, **kwargs): ...

    def route_special_proposal(self, *args, **kwargs): ...

    def special_enabled(self, family): ...

    def require_crystallize_trigger_sample(self, row): ...

    def create_bloom_core(self, reaction, row): ...


class MultiAuraContact:
    def __init__(self, runtime: MultiAuraResolverPort, profile, row, *, epsilon,
                 reaction_table, pending_error):
        self.runtime = runtime
        self.element, self.gu = profile["element"], profile["gauge_units"]
        self.state, self.row = runtime.multi_state, row
        self.epsilon, self.reaction_table = epsilon, reaction_table
        self.pending_error = pending_error

    def validate(self):
        state, row, element = self.state, self.row, self.element
        if state.freezable and {"hydro", "cryo"}.issubset(state.auras):
            raise self.pending_error(
                "freezable_frozen_state_required",
                "可冻结对象已有无先后证据的水冰共存初态，无法重建Frozen量与底层余量。",
                row["event_id"], {"freeze_scenario": copy.deepcopy(state.freeze_contract)})
        if (not state.freezable and state.unfreezable_resolution == "mutual_cancel"
                and {"hydro", "cryo"}.issubset(state.auras)):
            raise self.pending_error(
                "freeze_resolution_conflict",
                "mutual_cancel场景出现了未经相消的水冰共存初态。",
                row["event_id"], {"freeze_scenario": copy.deepcopy(state.freeze_contract)})
        self.freeze_child = state.freeze_state
        frozen_active = bool(self.freeze_child and self.freeze_child.frozen)
        self.non_freeze_auras = [name for name in state.auras if name not in ("hydro", "cryo")]
        if frozen_active and (element not in ("hydro", "cryo", "anemo") or self.non_freeze_auras):
            raise self.pending_error(
                "unsupported_frozen_reaction",
                "Frozen与本次元素/共存底的交互尚未纳入v4；事件已原子回滚。",
                row["event_id"], {"trigger_element": element,
                                  "coexisting_auras": sorted(self.non_freeze_auras),
                                  "frozen": copy.deepcopy(self.freeze_child.frozen),
                                  "freeze_scenario": copy.deepcopy(state.freeze_contract)})

    def route(self, elements):
        selected, audit = self.state.reaction_route(self.element, elements)
        self.row["reaction_priority"] = audit
        if selected is None:
            raise self.pending_error(
                "reaction_priority_unknown",
                "该入射元素与多底组合没有已受审的固定反应优先路由。",
                self.row["event_id"], {"reaction_priority": copy.deepcopy(audit)})
        return selected

    def publish(self, reactions):
        self.row["reactions"] = reactions
        self.row["reaction"] = reactions[0] if reactions else None
        self.row["outcome"] = "reaction" if reactions else "aura_updated"

    def route_step(self, index, **values):
        candidates = self.row.get("reaction_priority", {}).get("ranked_candidates", [])
        if index < len(candidates):
            candidates[index].update(values)

    def skip_remaining(self, start, reason, trigger_gu):
        candidates = self.row.get("reaction_priority", {}).get("ranked_candidates", [])
        for index in range(start, len(candidates)):
            candidates[index].update(status="skipped", skip_reason=reason,
                                     trigger_gu_before=trigger_gu,
                                     trigger_gu_after=trigger_gu)
