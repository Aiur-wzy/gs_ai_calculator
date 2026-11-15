"""Frozen pre-handler packet oracle; never a production fallback."""

from __future__ import annotations

import copy
from types import FunctionType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from genshin_engine.rotation_port import (
        TICKS,  # noqa: TC004 - injected canonical globals
    )
    from genshin_engine.special_reactions import (  # noqa: TC004 - injected canonical globals
        REPLACEMENT_CONTRACTS,
        SpecialReactionPending,
        _reaction_elements,
        _seconds_to_ticks,
    )


def _canonical_replace_stellar_swirl_packet_v1(
        self,
        reactions: list[dict],
        *,
        time: int,
        area_id: str = "battlefield.primary",
        parent_application_id: str | None = None,
    ) -> dict:
        """Atomically route all Star-Swirl children of one application packet.

        The packet boundary is required for AoE: when the packet starts with
        no vortex, every child deals its immediate Anemo component but the
        whole packet creates only one level-one vortex.  When a vortex already
        exists, every child advances its count independently.
        """
        if type(time) is not int or time < self.time:
            raise ValueError("星扩散packet时间必须是单调非负整数微秒。")
        if not isinstance(reactions, list) or not reactions:
            raise ValueError("星扩散packet必须包含至少一个普通反应提案。")
        if not isinstance(area_id, str) or not area_id:
            raise ValueError("area_id必须是非空空间桶ID。")
        ids: list[str] = []
        inferred_parents: list[str] = []
        for index, reaction in enumerate(reactions):
            if (not isinstance(reaction, dict) or reaction.get("type") != "swirl"
                    or reaction.get("commit_state") != "proposal"):
                raise ValueError(
                    f"reactions[{index}]必须是未提交的普通扩散提案。")
            event_id = reaction.get("id") or reaction.get("event_id")
            if not isinstance(event_id, str) or not event_id:
                raise ValueError(f"reactions[{index}]缺少唯一事件ID。")
            ids.append(event_id)
            # Cross-target proposals keep their target-local package/event
            # identities.  ``atomic_parent_id`` is the explicit shared parent
            # selected by the world coordinator and therefore takes priority;
            # legacy direct callers may still use one common package/parent.
            packet_parent = (reaction.get("atomic_parent_id")
                             or reaction.get("package_id")
                             or reaction.get("parent_event_id"))
            if not isinstance(packet_parent, str) or not packet_parent:
                raise ValueError(f"reactions[{index}]缺少package_id或parent_event_id。")
            inferred_parents.append(packet_parent)
            declared_size = reaction.get("application_packet_size")
            if declared_size is not None and declared_size != len(reactions):
                raise ValueError("application_packet_size必须等于实际packet child数量。")
            if _reaction_elements(reaction) != REPLACEMENT_CONTRACTS["stellar_swirl"]["elements"]:
                raise ValueError("星扩散packet只能包含冰扩散。")
        if len(set(ids)) != len(ids):
            raise ValueError("星扩散packet内反应ID不得重复。")
        packet_parent = parent_application_id or inferred_parents[0]
        if any(value != packet_parent for value in inferred_parents):
            raise ValueError("星扩散packet的所有child必须属于同一父级附着。")

        snapshot = (
            self.time, copy.deepcopy(self.state_by_target),
            copy.deepcopy(self.party_state), copy.deepcopy(self.stellar_field_by_area),
            copy.deepcopy(self.vortex_by_area), copy.deepcopy(self.moondrift_by_area),
            copy.deepcopy(self.lunar_cloud_by_area), copy.deepcopy(self.scheduled),
        )
        trace_mark = len(self.trace)
        due = self.advance(time)
        if self._config("stellar_swirl") is None:
            decisions = [self._decision(row, None, "switch_disabled") for row in reactions]
            ordinary = [
                {**copy.deepcopy(row), "commit_state": "committed", "route": "ordinary",
                 "initial_gauge_commit_policy": "commit_elemental_proposal_once"}
                for row in reactions
            ]
            return {"decisions": decisions, "reactions": ordinary,
                    "generated_events": due, "state": self.state()}

        packet_event_id = packet_parent+":stellar-swirl-packet"
        try:
            config = self._require_ready("stellar_swirl", packet_event_id)
            qualification = config["qualification"]
            mechanics = config["mechanics"]
            profile = self._profile_audit("stellar_swirl")
            vortex = self._vortex_state(area_id)
            started_with_vortex = vortex["stellar_swirl_count"] > 0
            replaced_rows: list[dict] = []
            decisions: list[dict] = []
            generated: list[dict] = list(due)
            packet_roles: list[dict] = []
            for reaction, event_id in zip(reactions, ids):
                target_id = reaction.get("target_id")
                if not isinstance(target_id, str) or not target_id:
                    raise ValueError("星扩散child缺少target_id。")
                owner = reaction.get("owner")
                contributors = self._contributors(reaction)
                roles = self._stellar_swirl_roles(reaction, contributors)
                packet_roles.append(roles)
                replaced = {
                    **copy.deepcopy(reaction),
                    "id": event_id+":special:stellar_swirl",
                    "type": "stellar_swirl",
                    "replaces": "swirl",
                    "ordinary_reaction_id": event_id,
                    "ordinary_damage_suppressed": True,
                    "ordinary_side_effects_committed": False,
                    "commit_state": "committed",
                    "route": "special",
                    "initial_gauge_commit_policy": "commit_elemental_proposal_once",
                    "area_id": area_id,
                    "qualification": {
                        "provider_ids": copy.deepcopy(qualification["provider_ids"]),
                        "present_provider_ids": profile["proven_provider_ids"],
                        "capability_tag": qualification["capability_tag"],
                        "status": qualification["status"],
                        "source": qualification["source"],
                    },
                    "mechanics_source": mechanics["source"],
                    "role_provenance": copy.deepcopy(roles),
                    "profile": copy.deepcopy(profile),
                }
                replaced_rows.append(replaced)
                decisions.append(self._decision(
                    reaction, "stellar_swirl", "explicit_switch_packet_and_present_capability",
                    record=False,
                ))
                generated.append(self._damage_request(
                    event_id=replaced["id"]+":immediate:anemo",
                    reaction_type="stellar_swirl_anemo", target_id=target_id,
                    owner=owner, parent_reaction_id=replaced["id"], time_tick=time,
                    component="immediate_anemo", contributor_ids=contributors,
                    area_id=area_id, role_provenance=roles))

            if not started_with_vortex:
                first = replaced_rows[0]
                first_reaction = reactions[0]
                vortex.update(
                    stellar_swirl_count=1,
                    stellar_swirl_level=1,
                    stellar_swirl_detonate_at=time + _seconds_to_ticks(
                        mechanics["detonation_delay_seconds"], "detonation_delay_seconds"),
                    stellar_swirl_owner=first_reaction.get("owner"),
                    stellar_swirl_action_id=first_reaction.get("action_id"),
                    stellar_swirl_parent_reaction_id=first["id"],
                )
                for roles in packet_roles:
                    self._merge_stellar_swirl_roles(
                        vortex, roles, packet_event_id)
                generated.append({
                    "type": "special_reaction_state",
                    "event_id": packet_event_id+":create-vortex",
                    "t": time/TICKS, "time_tick": time, "target_id": None,
                    "area_id": area_id, "reaction_type": "stellar_swirl",
                    "operation": "create_vortex_from_packet",
                    "packet_size": len(reactions), "packet_started_with_vortex": False,
                    "counted_children": 1, "count": 1, "vortex_level": 1,
                    "detonate_at": vortex["stellar_swirl_detonate_at"]/TICKS,
                    "profile": copy.deepcopy(profile), "status": "executed",
                })
            else:
                detonated = False
                counted_children = 0
                for reaction, replaced, roles in zip(
                        reactions, replaced_rows, packet_roles):
                    if detonated:
                        continue
                    counted_children += 1
                    vortex["stellar_swirl_count"] += 1
                    vortex["stellar_swirl_level"] = (
                        2 if vortex["stellar_swirl_count"] >= mechanics["upgrade_threshold"] else 1)
                    vortex["stellar_swirl_owner"] = reaction.get("owner")
                    vortex["stellar_swirl_action_id"] = reaction.get("action_id")
                    self._merge_stellar_swirl_roles(
                        vortex, roles, replaced["id"])
                    generated.append({
                        "type": "special_reaction_state",
                        "event_id": replaced["id"]+":advance-vortex",
                        "t": time/TICKS, "time_tick": time, "target_id": None,
                        "area_id": area_id, "reaction_type": "stellar_swirl",
                        "operation": "advance_existing_vortex_from_packet_child",
                        "packet_started_with_vortex": True,
                        "count": vortex["stellar_swirl_count"],
                        "vortex_level": vortex["stellar_swirl_level"],
                        "profile": copy.deepcopy(profile), "status": "executed",
                    })
                    if vortex["stellar_swirl_count"] >= mechanics["early_detonation_threshold"]:
                        generated.extend(self._stellar_swirl_detonation(
                            area_id, vortex, time, "sixth_reaction_early"))
                        detonated = True
                if detonated and counted_children < len(reactions):
                    generated.append({
                        "type": "special_reaction_state",
                        "event_id": packet_event_id+":post-detonation",
                        "t": time/TICKS, "time_tick": time, "target_id": None,
                        "area_id": area_id, "reaction_type": "stellar_swirl",
                        "operation": "remaining_packet_children_do_not_reseed",
                        "remaining_children": len(reactions)-counted_children,
                        "interpretation_status": "community_interpretation_v1",
                        "profile": copy.deepcopy(profile), "status": "executed",
                    })
            self.trace.extend(copy.deepcopy(decisions))
            self.trace.extend(copy.deepcopy(generated[len(due):]))
            return {"decisions": decisions, "reactions": replaced_rows,
                    "generated_events": generated, "state": self.state()}
        except SpecialReactionPending as exc:
            (self.time, self.state_by_target, self.party_state,
             self.stellar_field_by_area, self.vortex_by_area,
             self.moondrift_by_area, self.lunar_cloud_by_area,
             self.scheduled) = snapshot
            del self.trace[trace_mark:]
            self.trace.append({
                "type": "special_reaction_decision",
                "event_id": packet_event_id+":decision", "t": time/TICKS,
                "time_tick": time, "ordinary_reaction_id": packet_parent,
                "ordinary_reaction_type": "swirl", "special_reaction_type": "stellar_swirl",
                "decision": "pending", "reason": exc.issue["code"],
                "issue": copy.deepcopy(exc.issue), "status": "pending",
            })
            raise
        except Exception:
            (self.time, self.state_by_target, self.party_state,
             self.stellar_field_by_area, self.vortex_by_area,
             self.moondrift_by_area, self.lunar_cloud_by_area,
             self.scheduled) = snapshot
            del self.trace[trace_mark:]
            raise


def build_reference_packet_v1(namespace: dict[str, Any]) -> FunctionType:
    source = _canonical_replace_stellar_swirl_packet_v1
    result = FunctionType(source.__code__, namespace, source.__name__, source.__defaults__, source.__closure__)
    result.__kwdefaults__ = copy.deepcopy(source.__kwdefaults__)
    result.__annotations__ = copy.deepcopy(source.__annotations__)
    return result

