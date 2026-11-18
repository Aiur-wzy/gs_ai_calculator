"""Version-pinned original deadline and detonation oracles; no fallback."""

from __future__ import annotations

import copy
from types import FunctionType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from genshin_engine.constants import (
        stellar_superconduct_factor,  # noqa: TC004 - injected canonical globals
    )
    from genshin_engine.rotation_port import (
        TICKS,  # noqa: TC004 - injected canonical globals
    )
    from genshin_engine.special_reactions import (
        _seconds_to_ticks,  # noqa: TC004 - injected canonical globals
    )


def _canonical_stellar_swirl_detonation_v1(self, area_id: str, state: dict, time_tick: int,
                                   reason: str) -> list[dict]:
        mechanics = self._config("stellar_swirl")["mechanics"]
        profile = self._profile_audit("stellar_swirl")
        level = max(1, min(state["stellar_swirl_level"], 2))
        factor_level = 2 if level == 1 else 3
        parent = state["stellar_swirl_parent_reaction_id"] or f"{area_id}:stellar-swirl"
        owner = state["stellar_swirl_owner"]
        action_id = state["stellar_swirl_action_id"]
        contributors = copy.deepcopy(state["stellar_swirl_contributor_ids"])
        damage = self._damage_request(
            event_id=parent+f":detonation:cryo:lv{factor_level}",
            reaction_type=f"stellar_swirl_cryo_lv{factor_level}", target_id=None,
            owner=owner, parent_reaction_id=parent, time_tick=time_tick,
            component=f"cryo_vortex_level_{level}_detonation", contributor_ids=contributors,
            area_id=area_id,
            role_provenance=self._stellar_swirl_vortex_provenance(state))
        damage["vortex_level"] = level
        damage["base_factor"] = factor_level
        application = {
            "type": "special_reaction_application_request",
            "event_id": damage["event_id"]+":application",
            "t": time_tick/TICKS,
            "time_tick": time_tick,
            "target_id": None,
            "area_id": area_id,
            "target_selection_policy": "event_time_entities_in_area",
            "parent_damage_event_id": damage["event_id"],
            "owner": owner,
            "action_id": action_id,
            "contributor_ids": contributors,
            "reaction_type": "stellar_swirl",
            "element": "cryo",
            "gauge_units": mechanics["ice_application_gu"],
            "application_order": "after_damage",
            "icd": {"kind": "none"},
            "direct_hit": False,
            "counts_toward_stellar_superconduct": True,
            "source": mechanics["source"],
            "profile": copy.deepcopy(profile),
            "status": "ready_for_elemental_runtime",
        }
        audit = {
            "type": "special_reaction_state",
            "event_id": parent+":detonation",
            "t": time_tick/TICKS,
            "time_tick": time_tick,
            "target_id": None,
            "area_id": area_id,
            "reaction_type": "stellar_swirl",
            "operation": "detonate_vortex",
            "reason": reason,
            "count": state["stellar_swirl_count"],
            "vortex_level": level,
            "ice_factor": 2 if level == 1 else 3,
            "profile": copy.deepcopy(profile),
            "status": "executed",
        }
        state.update(
            stellar_swirl_count=0, stellar_swirl_level=0,
            stellar_swirl_detonate_at=None,
            stellar_swirl_owner=None, stellar_swirl_action_id=None,
            stellar_swirl_parent_reaction_id=None,
            stellar_swirl_contributor_ids=[],
            stellar_swirl_anemo_trigger_ids=[],
            stellar_swirl_cryo_applier_ids=[])
        return [audit, damage, application]


def _canonical_advance_deadlines_v1(
        self,
        time: int,
        *,
        defer_world_lunar_electro_expiry: bool,
    ) -> list[dict]:
        """Advance once, optionally reserving Lunar-Charged expiry for world commit."""
        if type(time) is not int or time < self.time:
            raise ValueError("星月反应时间必须是单调非负整数微秒。")
        if self._pending_world_lunar_electro_expiry is not None:
            raise RuntimeError("上一个世界月感电截止批次尚未完成。")
        due: list[dict] = []
        deferred_lunar_electro_expiries: list[dict] = []
        for area_id in sorted(self.stellar_field_by_area):
            state = self.stellar_field_by_area[area_id]
            config = self._config("stellar_superconduct")
            if config and state["stellar_superconduct_active"]:
                next_tick = state["stellar_superconduct_next_settlement"]
                expires = state["stellar_superconduct_expires_at"]
                interval = _seconds_to_ticks(
                    config["mechanics"]["settlement_interval_seconds"], "settlement_interval_seconds")
                while next_tick is not None and next_tick <= time and next_tick <= expires:
                    layers = min(12, state["stellar_superconduct_layers"] + config["mechanics"].get("settlement_bonus_layers", 0))
                    state["stellar_superconduct_settled_layers"] = layers
                    state["stellar_superconduct_factor"] = stellar_superconduct_factor(layers)
                    state["stellar_superconduct_ordinary_bonus"] = self._ordinary_bonus(layers)
                    state["stellar_superconduct_buff_expires_at"] = next_tick + interval
                    state["stellar_superconduct_layers"] = 0
                    state["stellar_superconduct_atomic_parents"] = []
                    due.append({
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
                        "cleared_layers": True,
                        "profile": self._profile_audit("stellar_superconduct"),
                        "status": "executed",
                    })
                    next_tick += interval
                state["stellar_superconduct_next_settlement"] = next_tick
                if expires is not None and expires <= time:
                    state["stellar_superconduct_active"] = False
                    state["stellar_superconduct_layers"] = 0
                    state["stellar_superconduct_next_settlement"] = None
                    state["stellar_superconduct_expires_at"] = None
                    state["stellar_superconduct_origin_applications"] = []
                    state["stellar_superconduct_atomic_parents"] = []
                    state["stellar_superconduct_physical_res_delta"] = 0.0
                    due.append({
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
                        "profile": self._profile_audit("stellar_superconduct"),
                        "status": "executed",
                    })
            buff_expires = state["stellar_superconduct_buff_expires_at"]
            if buff_expires is not None and buff_expires <= time:
                state["stellar_superconduct_settled_layers"] = 0
                state["stellar_superconduct_factor"] = 1.0
                state["stellar_superconduct_ordinary_bonus"] = 0.0
                state["stellar_superconduct_buff_expires_at"] = None
                due.append({
                    "type": "special_reaction_state",
                    "event_id": f"{area_id}:stellar-superconduct:buff-expire:{buff_expires}",
                    "t": buff_expires/TICKS, "time_tick": buff_expires,
                    "target_id": None, "area_id": area_id,
                    "reaction_type": "stellar_superconduct",
                    "operation": "expire_settlement_buff",
                    "profile": self._profile_audit("stellar_superconduct"),
                    "status": "executed",
                })
        for area_id in sorted(self.vortex_by_area):
            vortex = self.vortex_by_area[area_id]
            detonate = vortex["stellar_swirl_detonate_at"]
            if detonate is not None and detonate <= time:
                due.extend(self._stellar_swirl_detonation(
                    area_id, vortex, detonate, "three_second_timeout"))
        for area_id in sorted(self.lunar_cloud_by_area):
            cloud = self.lunar_cloud_by_area[area_id]
            expires = cloud["lunar_electro_expires_at"]
            if expires is not None and expires <= time:
                old_targets = copy.deepcopy(cloud["lunar_electro_target_ids"])
                expiry_event = {
                    "type": "special_reaction_state",
                    "event_id": f"{area_id}:lunar-electro:expire:{expires}",
                    "t": expires/TICKS, "time_tick": expires,
                    "target_id": None, "area_id": area_id,
                    "reaction_type": "lunar_electro",
                    "operation": "expire_area_thundercloud",
                    "previous_target_ids": old_targets,
                    "profile": self._profile_audit("lunar_electro"),
                    "status": "executed",
                }
                if defer_world_lunar_electro_expiry:
                    deferred_lunar_electro_expiries.append({
                        "area_id": area_id,
                        "expires_at": expires,
                        "target_ids": old_targets,
                        "phase_by_target": copy.deepcopy(
                            cloud["lunar_electro_phase_by_target"]),
                        "event": expiry_event,
                    })
                else:
                    cloud.update(
                        lunar_electro_active=False,
                        lunar_electro_expires_at=None,
                        lunar_electro_target_ids=[],
                        lunar_electro_last_attack_by_target={},
                        lunar_electro_phase_by_target={},
                    )
                    due.append(expiry_event)
        for area_id in sorted(self.moondrift_by_area):
            moondrift = self.moondrift_by_area[area_id]
            expires = moondrift["lunar_crystallize_expires_at"]
            if expires is not None and expires <= time:
                moondrift["lunar_crystallize_cages"] = 0
                moondrift["lunar_crystallize_expires_at"] = None
                due.append({
                    "type": "special_reaction_state",
                    "event_id": f"{area_id}:lunar-crystallize:expire:{expires}",
                    "t": expires/TICKS, "time_tick": expires, "target_id": None,
                    "area_id": area_id, "reaction_type": "lunar_crystallize",
                    "operation": "expire_moondrift_set",
                    "profile": self._profile_audit("lunar_crystallize"),
                    "status": "executed",
                })
        config = self._config("lunar_bloom")
        if config and self.party_state["lunar_bloom_backlog"] > 0:
            next_dew = self.party_state["lunar_bloom_next_dew_at"]
            interval = _seconds_to_ticks(
                config["mechanics"]["dew_interval_seconds"], "dew_interval_seconds")
            while (next_dew is not None and next_dew <= time
                   and self.party_state["lunar_bloom_backlog"] > 0
                   and self.party_state["lunar_bloom_dew"] < config["mechanics"]["dew_cap"]):
                before = self.party_state["lunar_bloom_dew"]
                backlog_before = self.party_state["lunar_bloom_backlog"]
                self.party_state["lunar_bloom_dew"] = before+1
                self.party_state["lunar_bloom_backlog"] -= 1
                source_parent = (
                    self.party_state["lunar_bloom_backlog_sources"].pop(0)
                    if self.party_state["lunar_bloom_backlog_sources"] else None)
                due.append({
                    "type": "special_reaction_resource",
                    "event_id": f"party:lunar-bloom:dew:{next_dew}",
                    "t": next_dew/TICKS, "time_tick": next_dew,
                    "target_id": None, "resource_scope": "party",
                    "reaction_type": "lunar_bloom", "operation": "periodic_dew_fill",
                    "before": before, "after": self.party_state["lunar_bloom_dew"],
                    "backlog_before": backlog_before,
                    "backlog_after": self.party_state["lunar_bloom_backlog"],
                    "source_atomic_parent_id": source_parent,
                    "continuous_fill_rate_per_second": .4,
                    "profile": self._profile_audit("lunar_bloom"),
                    "status": "executed",
                })
                if (self.party_state["lunar_bloom_backlog"] > 0
                        and self.party_state["lunar_bloom_dew"] >= config["mechanics"]["dew_cap"]):
                    policy = config["mechanics"]["full_cap_queue_policy"]
                    overflow_sources = copy.deepcopy(
                        self.party_state["lunar_bloom_backlog_sources"])
                    if policy == "pending":
                        self.party_state["lunar_bloom_full_cap_unresolved"] = list(dict.fromkeys(
                            [*self.party_state["lunar_bloom_full_cap_unresolved"],
                             *overflow_sources]))[-256:]
                        queue_status = "pending_until_resource_is_consumed"
                    elif policy == "discard_new":
                        queue_status = "assumed_discarded"
                    else:
                        overflow_sources = overflow_sources[-1:]
                        self.party_state["lunar_bloom_backlog"] = len(overflow_sources)
                        self.party_state["lunar_bloom_backlog_sources"] = overflow_sources
                        queue_status = "assumed_single_window_retained"
                    if policy != "retain_active_fill_window":
                        self.party_state["lunar_bloom_backlog"] = 0
                        self.party_state["lunar_bloom_backlog_sources"] = []
                    due.append({
                        "type": "special_reaction_resource",
                        "event_id": f"party:lunar-bloom:full-cap:{next_dew}",
                        "t": next_dew/TICKS, "time_tick": next_dew,
                        "target_id": None, "resource_scope": "party",
                        "reaction_type": "lunar_bloom",
                        "operation": "resolve_full_cap_queue_boundary",
                        "full_cap_queue_policy": policy,
                        "queue_policy_status": queue_status,
                        "affected_atomic_parent_ids": overflow_sources,
                        "profile": self._profile_audit("lunar_bloom"),
                        "status": "pending" if policy == "pending" else "executed",
                    })
                    next_dew = None
                elif (self.party_state["lunar_bloom_backlog"] > 0
                        and self.party_state["lunar_bloom_dew"] < config["mechanics"]["dew_cap"]):
                    next_dew += interval
                else:
                    next_dew = None
            self.party_state["lunar_bloom_next_dew_at"] = next_dew
        scheduled = [row for row in self.scheduled if row["time_tick"] <= time]
        self.scheduled = [row for row in self.scheduled if row["time_tick"] > time]
        due.extend(scheduled)
        due.sort(key=lambda row: (row["time_tick"], row["event_id"]))
        self.time = time
        if defer_world_lunar_electro_expiry:
            self._pending_world_lunar_electro_expiry = {
                "time_tick": time,
                "entries": deferred_lunar_electro_expiries,
            }
        cleaned = copy.deepcopy(due)
        for row in cleaned:
            row.pop("schedule_key", None)
        self.trace.extend(copy.deepcopy(cleaned))
        return cleaned


_REFERENCE_METHODS = {
    "_advance_deadlines": _canonical_advance_deadlines_v1,
    "_stellar_swirl_detonation": _canonical_stellar_swirl_detonation_v1,
}


def build_reference_special_deadlines_v1(namespace: dict[str, Any]) -> dict[str, FunctionType]:
    result = {}
    for name, source in _REFERENCE_METHODS.items():
        clone = FunctionType(source.__code__, namespace, name, source.__defaults__, source.__closure__)
        clone.__kwdefaults__ = copy.deepcopy(source.__kwdefaults__)
        clone.__annotations__ = copy.deepcopy(source.__annotations__)
        result[name] = clone
    return result

