"""Natural target-aura advancement on the sole target runtime clock."""

from __future__ import annotations

import copy
from typing import Any, Protocol

from genshin_engine.elemental_schema import FREEZE_RULESET, MULTI_AURA_RULESET
from genshin_engine.rotation_port import TICKS


class AuraKernelPort(Protocol):
    time: int
    target: str
    aura: dict[str, Any] | None
    trace: list[dict[str, Any]]
    recent_elemental_applications: list[dict[str, Any]]
    freeze_state: Any
    multi_state: Any
    crystallize_state: Any

    def prune_recent_elemental_applications(self): ...


def advance_elemental_state(kernel: AuraKernelPort, time, *, epsilon):
    """Advance only aura/freeze/crystallize state to one queue boundary."""
    if kernel.freeze_state:
        for transition in kernel.freeze_state.advance(time):
            kernel.trace.append({**transition, "target_id": kernel.target, "ruleset": FREEZE_RULESET,
                               "event_id": f"{kernel.target}:state:{transition['reason']}:{transition['t']}",
                               "source": "实验参考模型自然状态推进"})
        kernel.time = time
        kernel.prune_recent_elemental_applications()
        return
    if kernel.multi_state:
        for transition in kernel.multi_state.advance(time):
            kernel.trace.append({**transition, "target_id": kernel.target,
                               "ruleset": MULTI_AURA_RULESET,
                               "event_id": f"{kernel.target}:state:{transition['reason']}:{transition['t']}",
                               "source": "reviewed-freeze-state-v1",
                               "freeze_scenario": copy.deepcopy(kernel.multi_state.freeze_contract)})
        for transition in kernel.crystallize_state.advance(time):
            kernel.trace.append({**transition, "ruleset": MULTI_AURA_RULESET,
                               "event_id": (f"{transition.get('target_id', kernel.target)}:crystallize-state:"
                                            f"{transition['type']}:{transition['t']}:"
                                            f"{transition.get('shard_id', transition.get('source_reaction_id', 'state'))}"),
                               "source": "ordinary-crystallize-v1"})
        kernel.time = time
        kernel.prune_recent_elemental_applications()
        return
    if kernel.aura:
        kernel.aura["remaining_gu"] -= (time - kernel.time) / TICKS * kernel.aura["decay_gu_per_second"]
        if kernel.aura["remaining_gu"] <= epsilon:
            kernel.aura = None
    kernel.time = time
    kernel.prune_recent_elemental_applications()


def prune_recent_elemental_applications(kernel: AuraKernelPort):
    cutoff = kernel.time - 5*TICKS
    kernel.recent_elemental_applications = [
        row for row in kernel.recent_elemental_applications
        if row["time_tick"] >= cutoff
    ][-256:]


def record_recent_elemental_application(kernel: AuraKernelPort, row):
    kernel.recent_elemental_applications.append({
        "time_tick": kernel.time,
        "element": row["element"],
        "character": row["character"],
        "target_id": kernel.target,
        "area_id": row["area_id"],
        "source": row["event_id"],
    })
    kernel.prune_recent_elemental_applications()

