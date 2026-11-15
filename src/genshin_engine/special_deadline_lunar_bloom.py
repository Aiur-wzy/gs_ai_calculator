"""Incremental party Dew fill plans; no independent party resource store."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from genshin_engine.rotation_port import TICKS
from genshin_engine.special_replacement_contracts import replacement_state_fingerprint


@dataclass(frozen=True, slots=True)
class LunarBloomDeadlineStep:
    operation: Literal["fill", "full_cap", "next"]
    expected_prestate_fingerprint: str
    dew: int
    backlog: int
    next_dew_at: int | None
    sources: list[str]
    unresolved: list[str]
    full_cap_policy: str | None
    event: dict[str, Any] | None


def _bloom_step(operation, basis, party, event):
    return LunarBloomDeadlineStep(
        operation, basis, party["lunar_bloom_dew"], party["lunar_bloom_backlog"],
        party["lunar_bloom_next_dew_at"], copy.deepcopy(party["lunar_bloom_backlog_sources"]),
        copy.deepcopy(party["lunar_bloom_full_cap_unresolved"]),
        event.get("full_cap_queue_policy") if event is not None else None, event)


def plan_lunar_bloom_deadline_steps(party_state: Mapping[str, Any], config, *, time,
                                   seconds_to_ticks):
    party = copy.deepcopy(dict(party_state))
    if config and party["lunar_bloom_backlog"] > 0:
        next_dew = party["lunar_bloom_next_dew_at"]
        interval = seconds_to_ticks(
            config["mechanics"]["dew_interval_seconds"], "dew_interval_seconds")
        while (next_dew is not None and next_dew <= time
               and party["lunar_bloom_backlog"] > 0
               and party["lunar_bloom_dew"] < config["mechanics"]["dew_cap"]):
            basis = replacement_state_fingerprint(party)
            before = party["lunar_bloom_dew"]
            backlog_before = party["lunar_bloom_backlog"]
            party["lunar_bloom_dew"] = before+1
            party["lunar_bloom_backlog"] -= 1
            source_parent = (
                party["lunar_bloom_backlog_sources"].pop(0)
                if party["lunar_bloom_backlog_sources"] else None)
            yield _bloom_step("fill", basis, party, {
                "type": "special_reaction_resource",
                "event_id": f"party:lunar-bloom:dew:{next_dew}",
                "t": next_dew/TICKS, "time_tick": next_dew,
                "target_id": None, "resource_scope": "party",
                "reaction_type": "lunar_bloom", "operation": "periodic_dew_fill",
                "before": before, "after": party["lunar_bloom_dew"],
                "backlog_before": backlog_before,
                "backlog_after": party["lunar_bloom_backlog"],
                "source_atomic_parent_id": source_parent,
                "continuous_fill_rate_per_second": .4,
                "profile": None,
                "status": "executed",
            })
            if (party["lunar_bloom_backlog"] > 0
                    and party["lunar_bloom_dew"] >= config["mechanics"]["dew_cap"]):
                basis = replacement_state_fingerprint(party)
                policy = config["mechanics"]["full_cap_queue_policy"]
                overflow_sources = copy.deepcopy(
                    party["lunar_bloom_backlog_sources"])
                if policy == "pending":
                    party["lunar_bloom_full_cap_unresolved"] = list(dict.fromkeys(
                        [*party["lunar_bloom_full_cap_unresolved"],
                         *overflow_sources]))[-256:]
                    queue_status = "pending_until_resource_is_consumed"
                elif policy == "discard_new":
                    queue_status = "assumed_discarded"
                else:
                    overflow_sources = overflow_sources[-1:]
                    party["lunar_bloom_backlog"] = len(overflow_sources)
                    party["lunar_bloom_backlog_sources"] = overflow_sources
                    queue_status = "assumed_single_window_retained"
                if policy != "retain_active_fill_window":
                    party["lunar_bloom_backlog"] = 0
                    party["lunar_bloom_backlog_sources"] = []
                yield _bloom_step("full_cap", basis, party, {
                    "type": "special_reaction_resource",
                    "event_id": f"party:lunar-bloom:full-cap:{next_dew}",
                    "t": next_dew/TICKS, "time_tick": next_dew,
                    "target_id": None, "resource_scope": "party",
                    "reaction_type": "lunar_bloom",
                    "operation": "resolve_full_cap_queue_boundary",
                    "full_cap_queue_policy": policy,
                    "queue_policy_status": queue_status,
                    "affected_atomic_parent_ids": overflow_sources,
                    "profile": None,
                    "status": "pending" if policy == "pending" else "executed",
                })
                next_dew = None
            elif (party["lunar_bloom_backlog"] > 0
                    and party["lunar_bloom_dew"] < config["mechanics"]["dew_cap"]):
                next_dew += interval
            else:
                next_dew = None
        basis = replacement_state_fingerprint(party)
        party["lunar_bloom_next_dew_at"] = next_dew
        yield _bloom_step("next", basis, party, None)

