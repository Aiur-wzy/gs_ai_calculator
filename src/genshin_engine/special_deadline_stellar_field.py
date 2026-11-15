"""Incremental Star field deadline plans; Runtime commits each closed step.

Planning is incremental because standalone advancement has existing partial
commit semantics. A profile projection fault after a settlement must not undo
that settlement; World can still roll back the complete outer transaction.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from genshin_engine.rotation_port import TICKS
from genshin_engine.special_replacement_contracts import replacement_state_fingerprint


@dataclass(frozen=True, slots=True)
class StellarFieldDeadlineStep:
    operation: Literal["settled_layers", "factor", "settle", "schedule", "expire", "buff_expire"]
    expected_prestate_fingerprint: str
    layers: int
    factor: float
    bonus: float
    buff_expires_at: int | None
    next_settlement: int | None
    event: dict[str, Any] | None


def _field_step(operation, basis, state, event):
    return StellarFieldDeadlineStep(
        operation, basis, state["stellar_superconduct_settled_layers"],
        state["stellar_superconduct_factor"], state["stellar_superconduct_ordinary_bonus"],
        state["stellar_superconduct_buff_expires_at"],
        state["stellar_superconduct_next_settlement"], event)


def plan_stellar_field_deadline_steps(field: Mapping[str, Any], config, *, area_id, time,
                                     seconds_to_ticks, factor_for_layers, ordinary_bonus):
    state = copy.deepcopy(dict(field))
    if config and state["stellar_superconduct_active"]:
        next_tick = state["stellar_superconduct_next_settlement"]
        expires = state["stellar_superconduct_expires_at"]
        interval = seconds_to_ticks(
            config["mechanics"]["settlement_interval_seconds"], "settlement_interval_seconds")
        while next_tick is not None and next_tick <= time and next_tick <= expires:
            basis = replacement_state_fingerprint(state)
            layers = min(12, state["stellar_superconduct_layers"])
            state["stellar_superconduct_settled_layers"] = layers
            yield _field_step("settled_layers", basis, state, None)
            basis = replacement_state_fingerprint(state)
            state["stellar_superconduct_factor"] = factor_for_layers(layers)
            yield _field_step("factor", basis, state, None)
            basis = replacement_state_fingerprint(state)
            state["stellar_superconduct_ordinary_bonus"] = ordinary_bonus(layers)
            state["stellar_superconduct_buff_expires_at"] = next_tick + interval
            state["stellar_superconduct_layers"] = 0
            state["stellar_superconduct_atomic_parents"] = []
            yield _field_step("settle", basis, state, {
                "type": "special_reaction_state",
                "event_id": f"{area_id}:stellar-superconduct:settlement:{next_tick}",
                "t": next_tick/TICKS, "time_tick": next_tick, "target_id": None,
                "area_id": area_id,
                "reaction_type": "stellar_superconduct", "operation": "settle_four_second_window",
                "layers": layers, "system_factor": state["stellar_superconduct_factor"],
                "ordinary_cryo_electro_bonus": state["stellar_superconduct_ordinary_bonus"],
                "field_physical_res_delta": state[
                    "stellar_superconduct_physical_res_delta"],
                "buff_starts_at": next_tick/TICKS,
                "buff_expires_at": state["stellar_superconduct_buff_expires_at"]/TICKS,
                "next_settlement_at": (
                    (next_tick + interval)/TICKS if next_tick < expires else None),
                "cleared_layers": True,
                "profile": None,
                "status": "executed",
            })
            next_tick += interval
        basis = replacement_state_fingerprint(state)
        state["stellar_superconduct_next_settlement"] = next_tick
        yield _field_step("schedule", basis, state, None)
        if expires is not None and expires <= time:
            basis = replacement_state_fingerprint(state)
            state["stellar_superconduct_active"] = False
            state["stellar_superconduct_layers"] = 0
            state["stellar_superconduct_next_settlement"] = None
            state["stellar_superconduct_expires_at"] = None
            state["stellar_superconduct_origin_applications"] = []
            state["stellar_superconduct_atomic_parents"] = []
            state["stellar_superconduct_physical_res_delta"] = 0.0
            yield _field_step("expire", basis, state, {
                "type": "special_reaction_state",
                "event_id": f"{area_id}:stellar-superconduct:expire:{expires}",
                "t": expires/TICKS, "time_tick": expires, "target_id": None,
                "area_id": area_id,
                "reaction_type": "stellar_superconduct", "operation": "expire_field",
                "physical_res_delta": 0.0,
                "physical_res_effect_ended": True,
                "settled_buff_retained_until": (
                    state["stellar_superconduct_buff_expires_at"]/TICKS
                    if state["stellar_superconduct_buff_expires_at"] is not None else None),
                "profile": None,
                "status": "executed",
            })
    buff_expires = state["stellar_superconduct_buff_expires_at"]
    if buff_expires is not None and buff_expires <= time:
        basis = replacement_state_fingerprint(state)
        state["stellar_superconduct_settled_layers"] = 0
        state["stellar_superconduct_factor"] = 1.0
        state["stellar_superconduct_ordinary_bonus"] = 0.0
        state["stellar_superconduct_buff_expires_at"] = None
        yield _field_step("buff_expire", basis, state, {
            "type": "special_reaction_state",
            "event_id": f"{area_id}:stellar-superconduct:buff-expire:{buff_expires}",
            "t": buff_expires/TICKS, "time_tick": buff_expires,
            "target_id": None, "area_id": area_id,
            "reaction_type": "stellar_superconduct",
            "operation": "expire_settlement_buff",
            "profile": None,
            "status": "executed",
        })

