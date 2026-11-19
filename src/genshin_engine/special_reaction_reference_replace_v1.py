"""Pinned v1 canonical replacement coordinator used only by isolated oracles.

The body below is the accepted pre-handler implementation. Bind it to the
existing frozen special namespace so helper functions/configuration remain
independent of production handler registries. Production never calls this body.
"""

from __future__ import annotations

import copy
from types import FunctionType
from typing import TYPE_CHECKING

# Runtime bindings come exclusively from the injected frozen namespace. These
# imports describe its required symbols for static checking; moving them out
# of TYPE_CHECKING would form a cycle and reconnect the oracle to live globals.
if TYPE_CHECKING:
    from .special_reactions import (
        REPLACEMENT_CONTRACTS,  # noqa: TC004 - injected canonical globals
        TICKS,  # noqa: TC004 - injected canonical globals
        SpecialReactionPending,  # noqa: TC004 - injected canonical globals
        _reaction_elements,  # noqa: TC004 - injected canonical globals
        _seconds_to_ticks,  # noqa: TC004 - injected canonical globals
        stellar_superconduct_factor,  # noqa: TC004 - injected canonical globals
    )


def _canonical_replace_v1(self, reaction: dict, *, time: int, payload: dict | None = None) -> dict:
    """Route an uncommitted ordinary proposal, then commit exactly one path."""
    if type(time) is not int or time < self.time:
        raise ValueError("星月反应时间必须是单调非负整数微秒。")
    if not isinstance(reaction, dict) or not isinstance(reaction.get("type"), str):
        raise ValueError("普通反应回执缺少type。")  # noqa: TRY004 - public validation
    if reaction.get("commit_state") != "proposal":
        raise ValueError(
            "replace只接受commit_state='proposal'的普通反应提案；"
            "普通伤害、传播、晶片或减抗副作用必须在路由后再提交。")
    snapshot = (
        self.time, copy.deepcopy(self.state_by_target),
        copy.deepcopy(self.party_state), copy.deepcopy(self.stellar_field_by_area),
        copy.deepcopy(self.vortex_by_area), copy.deepcopy(self.moondrift_by_area),
        copy.deepcopy(self.lunar_cloud_by_area), copy.deepcopy(self.scheduled),
    )
    trace_mark = len(self.trace)
    ordinary_type = reaction["type"]
    event_id = reaction.get("id") or reaction.get("event_id") or "reaction"
    candidates = [name for name, contract in REPLACEMENT_CONTRACTS.items()
                  if contract["ordinary_type"] == ordinary_type and self._config(name)]
    if not candidates:
        due = self.advance(time)
        decision = self._decision(reaction, None, "switch_disabled")
        ordinary = {
            **copy.deepcopy(reaction), "commit_state": "committed", "route": "ordinary",
            "initial_gauge_commit_policy": "commit_elemental_proposal_once",
        }
        return {"decision": decision, "reaction": ordinary,
                "generated_events": due, "state": self.state()}
    elements = _reaction_elements(reaction)
    reaction_type = next((name for name in candidates
                          if REPLACEMENT_CONTRACTS[name]["elements"] == elements), None)
    if reaction_type is None:
        due = self.advance(time)
        decision = self._decision(reaction, None, "element_scope_not_matched")
        ordinary = {
            **copy.deepcopy(reaction), "commit_state": "committed", "route": "ordinary",
            "initial_gauge_commit_policy": "commit_elemental_proposal_once",
        }
        return {"decision": decision, "reaction": ordinary,
                "generated_events": due, "state": self.state()}
    if reaction_type == "stellar_swirl":
        packet_size = reaction.get("application_packet_size", 1)
        if type(packet_size) is not int or packet_size < 1:
            raise ValueError("application_packet_size必须是正整数。")
        if packet_size != 1:
            raise ValueError(
                "多child星扩散必须通过replace_stellar_swirl_packet原子提交，"
                "不能逐目标调用replace。")

    due = self.advance(time)
    try:
        config = self._require_ready(reaction_type, event_id)
        qualification, mechanics = config["qualification"], config["mechanics"]
        target_id = reaction.get("target_id")
        if not isinstance(target_id, str) or not target_id:
            raise ValueError("普通反应回执缺少target_id。")
        owner = reaction.get("owner")
        contributor_ids = self._contributors(reaction)
        stellar_swirl_roles = (
            self._stellar_swirl_roles(reaction, contributor_ids)
            if reaction_type == "stellar_swirl" else None)
        replaced = {
            **copy.deepcopy(reaction),
            "id": event_id+":special:"+reaction_type,
            "type": reaction_type,
            "replaces": ordinary_type,
            "ordinary_reaction_id": event_id,
            "ordinary_damage_suppressed": True,
            "ordinary_side_effects_committed": False,
            "commit_state": "committed",
            "route": "special",
            "initial_gauge_commit_policy": "commit_elemental_proposal_once",
            "qualification": {
                "provider_ids": copy.deepcopy(qualification["provider_ids"]),
                "present_provider_ids": sorted(
                    set(qualification["provider_ids"]) & set(self.party_member_ids or ())),
                "status": qualification["status"],
                "source": qualification["source"],
                "capability_tag": qualification["capability_tag"],
            },
            "mechanics_source": mechanics["source"],
            "profile": self._profile_audit(reaction_type),
        }
        # Lunar Crystallize replaces ordinary Hydro Crystallize damage,
        # shard creation and GCD, while still satisfying mechanics that
        # listen for the broader Crystallize family.  Stable trigger tags
        # expose that alias once without fabricating a second reaction.
        replaced["reaction_trigger_tags"] = (
            ["crystallize", "lunar_crystallize"]
            if reaction_type == "lunar_crystallize"
            else [reaction_type]
        )
        generated: list[dict] = list(due)
        if reaction_type == "stellar_superconduct":
            area_id = reaction.get("area_id") or (payload or {}).get("area_id") or "battlefield.primary"
            if self._resource_config():
                generated.extend(self._trigger_periodic_resource(time, area_id))
            else:
                field = self._stellar_field_state(area_id)
                duration = _seconds_to_ticks(mechanics.get("periodic_resource", {}).get("lifetime_seconds", mechanics["field_duration_seconds"]), "field_duration_seconds")
                interval = _seconds_to_ticks(mechanics["settlement_interval_seconds"], "settlement_interval_seconds")
                establishing = (reaction.get("atomic_parent_id")
                                or reaction.get("parent_event_id") or event_id)
                field_was_active = field["stellar_superconduct_active"]
                if not field_was_active:
                    field["stellar_superconduct_next_settlement"] = time + interval
                    field["stellar_superconduct_layers"] = 0
                    field["stellar_superconduct_atomic_parents"] = []
                field["stellar_superconduct_active"] = True
                field["stellar_superconduct_expires_at"] = time + duration
                field["stellar_superconduct_physical_res_delta"] = -.4
                # Only the application that first creates a Polestar Field is
                # excluded from layer recording.  A later Superconduct while
                # the field is already active both refreshes the field and is
                # still an eligible successful Cryo/Electro application.
                if not field_was_active:
                    field["stellar_superconduct_origin_applications"] = [establishing]
                    if mechanics.get("initial_grant_layers", 0):
                        initial = mechanics["initial_grant_layers"]
                        field["stellar_superconduct_settled_layers"] = initial
                        field["stellar_superconduct_factor"] = stellar_superconduct_factor(initial)
                        field["stellar_superconduct_ordinary_bonus"] = self._ordinary_bonus(initial)
                        field["stellar_superconduct_buff_expires_at"] = time + interval
                generated.append({
                    "type": "special_reaction_state", "event_id": replaced["id"]+":field",
                    "t": time/TICKS, "time_tick": time, "target_id": None,
                    "trigger_target_id": target_id, "area_id": area_id,
                    "reaction_type": reaction_type, "operation": "establish_or_refresh_field",
                    "initial_application_counted": False,
                    "trigger_application_layer_eligibility": (
                        "later_application" if field_was_active
                        else "establishing_application_excluded"),
                    "current_layers": field["stellar_superconduct_layers"],
                    "next_settlement_at": field["stellar_superconduct_next_settlement"]/TICKS,
                    "expires_at": field["stellar_superconduct_expires_at"]/TICKS,
                    "physical_res_delta": -.4, "physical_res_shred": .4,
                    "physical_res_effect_scope": "polestar_field_lifetime",
                    "damage_emitted": False, "damage_policy": "direct_stellar_hits_only",
                    "status": "executed",
                })
                if not field_was_active and mechanics.get("initial_grant_layers", 0):
                    generated[-1].update(
                        initial_grant_layers=mechanics["initial_grant_layers"],
                        initial_grant_factor=field["stellar_superconduct_factor"],
                        initial_grant_ordinary_bonus=field["stellar_superconduct_ordinary_bonus"],
                        initial_grant_expires_at=(time+interval)/TICKS)
        elif reaction_type == "stellar_swirl":
            replaced["role_provenance"] = copy.deepcopy(stellar_swirl_roles)
            area_id = reaction.get("area_id") or (payload or {}).get("area_id") or "battlefield.primary"
            vortex = self._vortex_state(area_id)
            new_vortex = vortex["stellar_swirl_count"] == 0
            vortex["stellar_swirl_count"] += 1
            vortex["stellar_swirl_level"] = (
                2 if vortex["stellar_swirl_count"] >= mechanics["upgrade_threshold"] else 1)
            vortex["stellar_swirl_owner"] = owner
            vortex["stellar_swirl_action_id"] = reaction.get("action_id")
            if new_vortex:
                initial = mechanics.get("initial_vortex_count", 1)
                if initial > 1 and time >= self.party_state.get("stellar_swirl_initial_upgrade_ready_at", 0):
                    vortex["stellar_swirl_count"] = initial
                    vortex["stellar_swirl_level"] = 2 if initial >= mechanics["upgrade_threshold"] else 1
                    ready = time + _seconds_to_ticks(mechanics.get("initial_upgrade_cooldown_seconds", 0), "initial_upgrade_cooldown_seconds")
                    self.party_state["stellar_swirl_initial_upgrade_ready_at"] = ready
                    vortex["stellar_swirl_initial_upgrade_ready_at"] = ready
                vortex["stellar_swirl_parent_reaction_id"] = replaced["id"]
            self._merge_stellar_swirl_roles(
                vortex, stellar_swirl_roles, event_id)
            if new_vortex:
                delay = _seconds_to_ticks(
                    mechanics["detonation_delay_seconds"], "detonation_delay_seconds")
                vortex["stellar_swirl_detonate_at"] = time + delay
            generated.append(self._damage_request(
                event_id=replaced["id"]+":immediate:anemo",
                reaction_type="stellar_swirl_anemo", target_id=target_id,
                owner=owner, parent_reaction_id=replaced["id"], time_tick=time,
                component="immediate_anemo", contributor_ids=contributor_ids,
                area_id=area_id, role_provenance=stellar_swirl_roles))
            generated.append({
                "type": "special_reaction_state", "event_id": replaced["id"]+":vortex",
                "t": time/TICKS, "time_tick": time, "target_id": None,
                "area_id": area_id, "reaction_type": reaction_type,
                "operation": "create_or_upgrade_vortex",
                "packet_size": 1, "packet_started_with_vortex": not new_vortex,
                "count": vortex["stellar_swirl_count"],
                "vortex_level": vortex["stellar_swirl_level"],
                "detonate_at": vortex["stellar_swirl_detonate_at"]/TICKS,
                "status": "executed",
            })
            if vortex["stellar_swirl_count"] >= mechanics["early_detonation_threshold"]:
                generated.extend(self._stellar_swirl_detonation(
                    area_id, vortex, time, "sixth_reaction_early"))
        elif reaction_type == "lunar_electro":
            area_id = (reaction.get("area_id") or (payload or {}).get("area_id")
                       or "battlefield.primary")
            cloud = self._lunar_cloud_state(area_id)
            first = _seconds_to_ticks(mechanics["first_tick_delay_seconds"], "first_tick_delay_seconds")
            interval = _seconds_to_ticks(mechanics["tick_interval_seconds"], "tick_interval_seconds")
            duration = _seconds_to_ticks(mechanics["duration_seconds"], "duration_seconds")
            was_active = cloud["lunar_electro_active"]
            previous_expires = cloud["lunar_electro_expires_at"]
            cloud["lunar_electro_active"] = True
            cloud["lunar_electro_expires_at"] = time+duration
            is_new_target = target_id not in cloud["lunar_electro_target_ids"]
            if is_new_target:
                cloud["lunar_electro_target_ids"].append(target_id)
                cloud["lunar_electro_phase_by_target"][target_id] = {
                    "first_attack_at": time+first,
                    "next_tick_index": 1,
                    "owner": owner,
                    "parent_reaction_id": replaced["id"],
                }
            scheduled_count = 0
            scheduled_times: list[int] = []
            for cloud_target in cloud["lunar_electro_target_ids"]:
                phase = cloud["lunar_electro_phase_by_target"][cloud_target]
                index = phase["next_tick_index"]
                tick_at = phase["first_attack_at"] + (index-1)*interval
                while tick_at <= cloud["lunar_electro_expires_at"]:
                    request = self._lunar_electro_tick_request(
                        area_id=area_id,
                        target_id=cloud_target,
                        phase=phase,
                        tick_index=index,
                        time_tick=tick_at,
                    )
                    self.scheduled.append(request)
                    scheduled_count += 1
                    scheduled_times.append(tick_at)
                    index += 1
                    tick_at += interval
                phase["next_tick_index"] = index
            generated.append({
                "type": "special_reaction_schedule", "event_id": replaced["id"]+":schedule",
                "t": time/TICKS, "time_tick": time, "target_id": target_id,
                "area_id": area_id, "cloud_scope": "area",
                "reaction_type": reaction_type,
                "operation": ("extend_cloud_without_phase_reset" if was_active
                              else "schedule_aura_checked_ticks"),
                "refresh_mode": "extend_without_phase_reset",
                "previous_expires_at": (
                    previous_expires/TICKS if previous_expires is not None else None),
                "new_target_added": is_new_target,
                "scheduled_target_ids": copy.deepcopy(
                    cloud["lunar_electro_target_ids"]),
                "scheduled_count": scheduled_count,
                "new_scheduled_times": [value/TICKS for value in scheduled_times],
                "first_tick_at": (
                    min(scheduled_times)/TICKS if scheduled_times else None),
                "last_tick_at": (
                    max(scheduled_times)/TICKS if scheduled_times else None),
                "expires_at": cloud["lunar_electro_expires_at"]/TICKS,
                "status": "executed",
            })
        elif reaction_type == "lunar_bloom":
            interval = _seconds_to_ticks(mechanics["dew_interval_seconds"], "dew_interval_seconds")
            atomic_parent = reaction.get("package_id") or reaction.get("parent_event_id") or event_id
            already_counted = atomic_parent in self.party_state["lunar_bloom_atomic_parents"]
            backlog_before = self.party_state["lunar_bloom_backlog"]
            unresolved_before = len(self.party_state["lunar_bloom_full_cap_unresolved"])
            backlog_added = 0
            queue_policy_status = "not_applicable"
            if not already_counted:
                self.party_state["lunar_bloom_atomic_parents"] = list(dict.fromkeys(
                    [*self.party_state["lunar_bloom_atomic_parents"], atomic_parent]))[-256:]
                if self.party_state["lunar_bloom_dew"] >= mechanics["dew_cap"]:
                    policy = mechanics["full_cap_queue_policy"]
                    if policy == "pending":
                        self.party_state["lunar_bloom_full_cap_unresolved"] = list(dict.fromkeys(
                            [*self.party_state["lunar_bloom_full_cap_unresolved"],
                             atomic_parent]))[-256:]
                        queue_policy_status = "pending_until_resource_is_consumed"
                    elif policy == "discard_new":
                        queue_policy_status = "assumed_discarded"
                    else:  # retain_active_fill_window, necessarily an assumed override
                        self.party_state["lunar_bloom_backlog"] = max(
                            1, self.party_state["lunar_bloom_backlog"])
                        self.party_state["lunar_bloom_backlog_sources"] = [atomic_parent]
                        queue_policy_status = "assumed_single_window_retained"
                else:
                    backlog_added = mechanics["backlog_per_reaction"]
                    self.party_state["lunar_bloom_backlog"] += backlog_added
                    self.party_state["lunar_bloom_backlog_sources"].append(atomic_parent)
                    queue_policy_status = "queued"
                    if backlog_before == 0:
                        self.party_state["lunar_bloom_next_dew_at"] = time + interval
            generated.append({
                "type": "special_reaction_core_request", "event_id": replaced["id"]+":core",
                "t": time/TICKS, "time_tick": time, "target_id": target_id,
                "reaction_type": reaction_type, "operation": "generate_ordinary_dendro_core",
                "ordinary_core_created": True, "lunar_damage_emitted": False,
                "status": "ready_for_core_runtime",
            })
            generated.append({
                "type": "special_reaction_state", "event_id": replaced["id"]+":backlog",
                "t": time/TICKS, "time_tick": time, "target_id": target_id,
                "reaction_type": reaction_type, "operation": "enqueue_party_dew_fill",
                "resource_scope": "party", "dew_added_immediately": 0,
                "lunar_damage_emitted": False,
                "atomic_parent_id": atomic_parent,
                "backlog_added": backlog_added,
                "backlog_before": backlog_before,
                "backlog_after": self.party_state["lunar_bloom_backlog"],
                "full_cap_queue_policy": mechanics["full_cap_queue_policy"],
                "queue_policy_status": (
                    "same_parent_already_counted" if already_counted else queue_policy_status),
                "unresolved_full_cap_before": unresolved_before,
                "unresolved_full_cap_after": len(
                    self.party_state["lunar_bloom_full_cap_unresolved"]),
                "next_dew_at": (
                    self.party_state["lunar_bloom_next_dew_at"]/TICKS
                    if self.party_state["lunar_bloom_next_dew_at"] is not None else None),
                "status": "executed",
            })
        else:  # lunar_crystallize
            duration = _seconds_to_ticks(mechanics["cage_duration_seconds"], "cage_duration_seconds")
            area_id = reaction.get("area_id") or (payload or {}).get("area_id") or "battlefield.primary"
            moondrift = self._moondrift_state(area_id)
            new_set = moondrift["lunar_crystallize_cages"] == 0
            if new_set:
                moondrift["lunar_crystallize_cages"] = mechanics["cages_per_reaction"]
                moondrift["lunar_crystallize_expires_at"] = time + duration
            ledger = self.party_state["lunar_crystallize_harmony_ledger"]
            cap = mechanics["harmony_counter_cap"]
            counter_capped = len(ledger) >= cap
            if not counter_capped:
                ledger.append({
                    "reaction_id": replaced["id"],
                    "area_id": area_id,
                    "contributor_ids": copy.deepcopy(contributor_ids),
                })
            count = min(cap, len(ledger))
            self.party_state["lunar_crystallize_harmony_count"] = count
            self.party_state["lunar_crystallize_contributor_ids"] = list(dict.fromkeys(
                contributor
                for entry in ledger
                for contributor in entry["contributor_ids"]
            ))[:4]
            state_event = {
                "type": "special_reaction_state", "event_id": replaced["id"]+":moon-cages",
                "t": time/TICKS, "time_tick": time, "target_id": None,
                "trigger_target_id": target_id, "area_id": area_id,
                "reaction_type": reaction_type,
                "operation": "create_moondrift_set" if new_set else "advance_harmony",
                "count": count, "cages": mechanics["cages_per_reaction"],
                "cages_created": mechanics["cages_per_reaction"] if new_set else 0,
                "ordinary_shard_created": False, "common_cooldown_seconds": 0,
                "harmony_scope": "party", "moondrift_scope": "area",
                "harmony_counter_cap": cap, "counter_capped": counter_capped,
                "contributor_ids": copy.deepcopy(
                    self.party_state["lunar_crystallize_contributor_ids"]),
                "expires_at": moondrift["lunar_crystallize_expires_at"]/TICKS,
                "harmony_dispatch": "not_ready",
                "status": "executed",
            }
            generated.append(state_event)
            threshold = mechanics["harmony_threshold"]
            if count >= threshold and new_set:
                # The creation reaction counts, but a newly created set may
                # not immediately spend historical overflow to attack.
                state_event["harmony_dispatch"] = "suppressed_on_initial_creation"
            elif count >= threshold:
                cooldown = mechanics.get("harmony_attack_cooldown_seconds")
                scope = mechanics.get("harmony_attack_cooldown_scope")
                if cooldown is None or scope is None:
                    raise SpecialReactionPending(
                        "lunar_crystallize_harmony_cooldown_pending",
                        "月笼谐奏攻击冷却的精确值与作用域尚未核实。",
                        event_id,
                        {"area_id": area_id, "harmony_count": count},
                    )
                cooldown_ticks = _seconds_to_ticks(
                    cooldown, "harmony_attack_cooldown_seconds")
                last_attack = (
                    self.party_state["lunar_crystallize_last_harmony_attack_at"]
                    if scope == "global"
                    else moondrift["lunar_crystallize_last_harmony_attack_at"]
                )
                if last_attack is not None and time-last_attack < cooldown_ticks:
                    state_event["harmony_dispatch"] = "blocked_by_assumed_attack_cooldown"
                    state_event["harmony_attack_cooldown_seconds"] = cooldown
                    state_event["harmony_attack_cooldown_scope"] = scope
                else:
                    cycle_entries = copy.deepcopy(ledger[:threshold])
                    harmony_contributors = list(dict.fromkeys(
                        contributor
                        for entry in cycle_entries
                        for contributor in entry["contributor_ids"]
                    ))[:4]
                    del ledger[:threshold]
                    self.party_state["lunar_crystallize_harmony_count"] = len(ledger)
                    self.party_state["lunar_crystallize_contributor_ids"] = list(dict.fromkeys(
                        contributor
                        for entry in ledger
                        for contributor in entry["contributor_ids"]
                    ))[:4]
                    if scope == "global":
                        self.party_state["lunar_crystallize_last_harmony_attack_at"] = time
                    else:
                        moondrift["lunar_crystallize_last_harmony_attack_at"] = time
                    state_event["harmony_dispatch"] = "executed"
                    state_event["harmony_count_after"] = len(ledger)
                    state_event["harmony_cycle_reaction_ids"] = [
                        entry["reaction_id"] for entry in cycle_entries]
                    state_event["harmony_attack_cooldown_seconds"] = cooldown
                    state_event["harmony_attack_cooldown_scope"] = scope
                # A Harmony attack refreshes inactivity lifetime only for
                # the Moondrifts that actually attacked in this area.
                    moondrift["lunar_crystallize_expires_at"] = time + duration
                    for cage in range(1, min(
                            moondrift["lunar_crystallize_cages"],
                            mechanics["cages_per_reaction"])+1):
                        generated.append(self._damage_request(
                            event_id=replaced["id"]+f":harmony:cage:{cage}",
                            reaction_type="lunar_crystallize", target_id=None,
                            owner=owner, parent_reaction_id=replaced["id"], time_tick=time,
                            component=f"moon_cage_harmony_{cage}",
                            contributor_ids=harmony_contributors, area_id=area_id,
                            sample_policy="harmony_cycle_contributors_event_time"))
                        generated[-1]["trigger_target_id"] = target_id
                        generated[-1]["target_selection_policy"] = (
                            "event_time_nearby_entities_in_area")
                        generated[-1]["damage_instance_index"] = cage
                        generated[-1]["independent_crit_instance"] = True
        profile = self._profile_audit(reaction_type)
        for row in generated[len(due):]:
            row.setdefault("profile", copy.deepcopy(profile))
        decision = self._decision(
            reaction, reaction_type,
            "explicit_switch_and_present_capability",
            record=False,
        )
        # The decision and replacement side effects become visible
        # together.  Exceptions above therefore cannot leave a false
        # successful route record in the audit stream.
        self.trace.append(copy.deepcopy(decision))
        self.trace.extend(copy.deepcopy(generated[len(due):]))
        return {"decision": decision, "reaction": replaced,
                "generated_events": generated, "state": self.state()}
    except SpecialReactionPending as exc:
        (self.time, self.state_by_target, self.party_state,
         self.stellar_field_by_area, self.vortex_by_area,
         self.moondrift_by_area, self.lunar_cloud_by_area,
         self.scheduled) = snapshot
        del self.trace[trace_mark:]
        pending = {
            "type": "special_reaction_decision", "event_id": event_id+":special-decision",
            "t": time/TICKS, "time_tick": time, "ordinary_reaction_id": event_id,
            "ordinary_reaction_type": ordinary_type, "special_reaction_type": reaction_type,
            "decision": "pending", "reason": exc.issue["code"],
            "issue": copy.deepcopy(exc.issue), "status": "pending",
        }
        self.trace.append(pending)
        raise
    except Exception:
        (self.time, self.state_by_target, self.party_state,
         self.stellar_field_by_area, self.vortex_by_area,
         self.moondrift_by_area, self.lunar_cloud_by_area,
         self.scheduled) = snapshot
        del self.trace[trace_mark:]
        raise


def build_reference_replace_v1(namespace: dict) -> FunctionType:
    reference = FunctionType(
        _canonical_replace_v1.__code__, namespace, name="replace",
        argdefs=copy.deepcopy(_canonical_replace_v1.__defaults__),
        closure=_canonical_replace_v1.__closure__,
    )
    reference.__kwdefaults__ = copy.deepcopy(_canonical_replace_v1.__kwdefaults__)
    reference.__annotations__ = copy.deepcopy(_canonical_replace_v1.__annotations__)
    reference.__module__ = namespace["__name__"]
    reference.__qualname__ = "SpecialReactionRuntime.replace"
    return reference


__all__ = ["build_reference_replace_v1"]

