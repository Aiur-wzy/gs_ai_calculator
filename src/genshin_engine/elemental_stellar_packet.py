"""Atomic multi-target packet consumer for the reviewed Star-Swirl route.

This module is intentionally narrow.  It does not discover targets, infer
collision order, or approximate space.  A caller supplies the exact children
of one landed parent application and an explicit deterministic child order;
the world registry only proves that those targets belong to the declared area.
"""
from __future__ import annotations

import copy
from collections.abc import Iterable

from genshin_engine.elemental_world import ElementalWorldPending, ElementalWorldRuntime
from genshin_engine.special_reaction_reference import (
    copy_runtime as _copy_special_runtime_for_reference,
)
from genshin_engine.special_reaction_reference import (
    replace_stellar_swirl_packet as _REFERENCE_REPLACE_STELLAR_SWIRL_PACKET,
)

PACKET_CONTRACT_ID = "stellar-swirl-world-packet-v1"
_MAX_PACKET_CHILDREN = 256
_ROUTING_FIELDS = frozenset((
    "route",
    "initial_gauge_commit_policy",
))


def _nonempty(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}必须是非空字符串。")
    return value


def _ordinary_reaction_as_proposal(reaction: dict) -> dict:
    """Recover the complete pre-routing proposal from an ordinary preview."""
    if (not isinstance(reaction, dict)
            or reaction.get("commit_state") != "committed"
            or reaction.get("route") != "ordinary"
            or reaction.get("initial_gauge_commit_policy")
            != "commit_elemental_proposal_once"):
        raise ElementalWorldPending(
            "stellar_swirl_preview_not_ordinary_proposal",
            "星扩散packet预览没有得到可逆的普通反应提案。",
            {"reaction": copy.deepcopy(reaction)},
        )
    proposal = {
        key: copy.deepcopy(value)
        for key, value in reaction.items()
        if key not in _ROUTING_FIELDS
    }
    proposal["commit_state"] = "proposal"
    return proposal


def _receipt_without_world_routing(receipt: dict) -> dict:
    """Return fields whose semantics must not change during pre-routing."""
    value = copy.deepcopy(receipt)
    for key in (
        "reaction",
        "reactions",
        "special_reaction_decisions",
        "stellar_superconduct_application",
    ):
        value.pop(key, None)
    return value


def _validate_children(children: Iterable[dict]) -> tuple[list[dict], tuple[str, int, str]]:
    if isinstance(children, (str, bytes, dict)):
        raise TypeError("children必须是星扩散packet child对象集合。")
    rows = [copy.deepcopy(row) for row in children]
    if not rows or len(rows) > _MAX_PACKET_CHILDREN:
        raise ValueError("星扩散packet必须包含1至256个child。")

    ids: set[str] = set()
    stable_ids: set[str] = set()
    targets: set[str] = set()
    orders: set[int] = set()
    keys: set[tuple[str, int, str]] = set()
    actor_action_profiles: set[tuple[str, str, str]] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise TypeError(f"children[{index}]必须是对象。")
        event_id = _nonempty(row.get("event_id"), f"children[{index}].event_id")
        stable_id = _nonempty(
            row.get("stable_event_id"), f"children[{index}].stable_event_id")
        action_id = _nonempty(row.get("action_id"), f"children[{index}].action_id")
        actor = _nonempty(row.get("actor"), f"children[{index}].actor")
        time_tick = row.get("time_tick")
        order = row.get("packet_child_order")
        if type(time_tick) is not int or time_tick < 0:
            raise ValueError(f"children[{index}].time_tick必须是非负整数微秒。")
        if type(order) is not int or order < 0:
            raise ValueError(f"children[{index}].packet_child_order必须是非负整数。")
        application = row.get("application")
        if not isinstance(application, dict):
            raise TypeError(f"children[{index}].application必须是对象。")
        target_id = _nonempty(
            application.get("target_id"), f"children[{index}].application.target_id")
        area_id = _nonempty(
            application.get("area_id"), f"children[{index}].application.area_id")
        parent_id = _nonempty(
            application.get("atomic_parent_id"),
            f"children[{index}].application.atomic_parent_id")
        profile_id = _nonempty(
            application.get("profile_id"), f"children[{index}].application.profile_id")
        if application.get("landed") is not True:
            raise ValueError("星扩散packet child必须是已经命中的附着接触。")
        if application.get("attempted", True) is not True:
            raise ValueError("星扩散packet child不能声明跳过元素附着尝试。")
        packet_size = application.get("application_packet_size")
        if (type(packet_size) is not int or packet_size < 1
                or packet_size != len(rows)):
            raise ValueError("每个child的application_packet_size必须等于实际child数量。")
        if row.get("damage_element") not in (None, "anemo"):
            raise ValueError("星扩散packet child的命中元素只能是风元素或省略。")
        if event_id in ids or stable_id in stable_ids or target_id in targets:
            raise ValueError("packet内event_id、stable_event_id与target_id必须分别唯一。")
        if order in orders:
            raise ValueError("packet_child_order不得重复。")
        ids.add(event_id)
        stable_ids.add(stable_id)
        targets.add(target_id)
        orders.add(order)
        keys.add((parent_id, time_tick, area_id))
        actor_action_profiles.add((actor, action_id, profile_id))

    if len(keys) != 1:
        raise ValueError("所有child必须具有相同atomic_parent_id、time_tick与area_id。")
    if actor_action_profiles and len(actor_action_profiles) != 1:
        raise ValueError("同一原子父附着的actor、action_id与profile_id必须一致。")
    if orders != set(range(len(rows))):
        raise ValueError("packet_child_order必须从0开始连续覆盖全部child。")
    rows.sort(key=lambda row: row["packet_child_order"])
    return rows, next(iter(keys))


def attempt_stellar_swirl_packet(
    world: ElementalWorldRuntime,
    children: Iterable[dict],
) -> dict:
    """Execute one exact Star-Swirl parent packet as a world transaction.

    The caller must first advance the world to the packet timestamp.  This
    keeps time observation separate from packet admission and guarantees that
    any child, replacement or publication failure restores every target and
    the shared special state to the same timestamp.
    """
    if not isinstance(world, ElementalWorldRuntime):
        raise TypeError("world必须是ElementalWorldRuntime。")
    rows, (parent_id, time_tick, area_id) = _validate_children(children)
    if time_tick != world.time:
        raise ElementalWorldPending(
            "stellar_swirl_packet_time_not_aligned",
            "请先把世界运行时推进到packet时间，再提交同父星扩散。",
            {"packet_time_tick": time_tick, "world_time_tick": world.time},
        )

    def commit(value: ElementalWorldRuntime) -> dict:
        if value.special_runtime is None:
            raise ElementalWorldPending(
                "stellar_swirl_special_runtime_missing",
                "世界运行时没有绑定星月反应状态机。",
                {"atomic_parent_id": parent_id},
            )
        pending_deadline = value.special_runtime.next_deadline()
        if pending_deadline is not None and pending_deadline <= value.time:
            raise ElementalWorldPending(
                "stellar_swirl_packet_deadline_not_consumed",
                "星扩散packet预览前仍有当前或过去的世界截止事件；请先推进世界。",
                {"packet_time_tick": time_tick,
                 "world_time_tick": value.time,
                 "next_deadline_tick": pending_deadline},
            )
        area_targets = set(value.targets_in_area(area_id))
        input_targets = {
            row["application"]["target_id"] for row in rows
        }
        missing = sorted(input_targets-area_targets)
        if missing:
            raise ElementalWorldPending(
                "stellar_swirl_packet_target_outside_area",
                "packet child目标未登记在声明区域中。",
                {"area_id": area_id, "target_ids": missing},
            )

        previews: list[dict] = []
        router_proposals: list[dict] = []
        for row in rows:
            target_id = row["application"]["target_id"]
            runtime = value.get_target(target_id)
            target_state_before = value._target_commit_snapshot(runtime)
            target_identity_before = value._target_identity_snapshot(runtime)
            if runtime.time != time_tick:
                raise ElementalWorldPending(
                    "stellar_swirl_target_time_not_aligned",
                    "packet目标运行时没有与世界时间对齐。",
                    {"target_id": target_id, "target_time_tick": runtime.time,
                     "world_time_tick": time_tick},
                )
            if runtime.generated_events:
                raise ElementalWorldPending(
                    "stellar_swirl_target_events_not_collected",
                    "目标运行时仍有未归集的派生事件，不能开始新的世界packet。",
                    {"target_id": target_id,
                     "event_ids": [event.get("event_id")
                                   for event in runtime.generated_events]},
                )
            reference_preview = value._reference_target_copy(
                runtime,
                preserve_trace=False,
            )
            reference_receipt = reference_preview.attempt(
                event_id=row["event_id"],
                stable_event_id=row["stable_event_id"],
                action_id=row["action_id"],
                actor=row["actor"],
                time=time_tick,
                application=copy.deepcopy(row["application"]),
                damage_element=row.get("damage_element"),
                contact=copy.deepcopy(row.get("contact")),
                trigger_stat_sample=copy.deepcopy(
                    row.get("trigger_stat_sample")),
            )
            preview = runtime.preview_copy()
            receipt = preview.attempt(
                event_id=row["event_id"],
                stable_event_id=row["stable_event_id"],
                action_id=row["action_id"],
                actor=row["actor"],
                time=time_tick,
                application=copy.deepcopy(row["application"]),
                damage_element=row.get("damage_element"),
                contact=copy.deepcopy(row.get("contact")),
                trigger_stat_sample=copy.deepcopy(
                    row.get("trigger_stat_sample")),
            )
            if (receipt != reference_receipt
                    or value._target_commit_snapshot(preview)
                    != value._target_commit_snapshot(reference_preview)
                    or value._target_commit_snapshot(runtime)
                    != target_state_before):
                raise ElementalWorldPending(
                    "stellar_swirl_packet_target_preview_mismatch",
                    "packet child普通预演回执或完整后态与隔离参考执行不一致。",
                    {"target_id": target_id,
                     "event_id": row["event_id"]},
                )
            reactions = reference_receipt.get("reactions")
            if (not isinstance(reactions, list) or len(reactions) != 1
                    or reactions[0].get("type") != "swirl"
                    or reactions[0].get("aura_element") != "cryo"
                    or reactions[0].get("trigger_element") != "anemo"):
                raise ElementalWorldPending(
                    "stellar_swirl_packet_child_not_unique_cryo_swirl",
                    "首片要求每个child恰好产生一个冰扩散提案；额外或缺失反应已整包拒绝。",
                    {"target_id": target_id,
                     "reaction_summaries": [
                         {"id": item.get("id"), "type": item.get("type"),
                          "aura_element": item.get("aura_element"),
                          "trigger_element": item.get("trigger_element")}
                         for item in reactions or []]},
                )
            if preview.generated_events:
                raise ElementalWorldPending(
                    "stellar_swirl_packet_child_side_effects_unexpected",
                    "child预览在packet路由前产生了额外派生事件，已停止提交。",
                    {"target_id": target_id,
                     "event_ids": [event.get("event_id")
                                   for event in preview.generated_events]},
                )
            expected = _ordinary_reaction_as_proposal(reactions[0])
            routed = {
                **copy.deepcopy(expected),
                "atomic_parent_id": parent_id,
                "area_id": area_id,
                "application_packet_size": len(rows),
                "time_tick": time_tick,
                "packet_child_order": row["packet_child_order"],
            }
            previews.append({
                "input": row,
                "expected_proposal": expected,
                "preview_receipt": reference_receipt,
                # A proposal records the reaction semantics, not every
                # irrelevant amount of aura above the consumed threshold.
                # Keep the complete target pre-state as an optimistic commit
                # guard so any mutation between preview and commit still
                # rejects the whole world transaction.
                "target_state_before": target_state_before,
                "target_identity_before": target_identity_before,
            })
            router_proposals.append(routed)

        producer_candidate = copy.deepcopy(value.special_runtime)
        reference_candidate = _copy_special_runtime_for_reference(
            value.special_runtime)
        routed_packet = producer_candidate.replace_stellar_swirl_packet(
            router_proposals,
            time=time_tick,
            area_id=area_id,
            parent_application_id=parent_id,
        )
        reference_packet = _REFERENCE_REPLACE_STELLAR_SWIRL_PACKET(
            reference_candidate,
            router_proposals,
            time=time_tick,
            area_id=area_id,
            parent_application_id=parent_id,
        )
        reactions = routed_packet.get("reactions")
        decisions = routed_packet.get("decisions")
        if (not isinstance(reactions, list) or len(reactions) != len(rows)
                or not isinstance(decisions, list) or len(decisions) != len(rows)):
            raise ElementalWorldPending(
                "stellar_swirl_packet_router_cardinality_mismatch",
                "星扩散packet路由结果与child数量不一致。",
                {"child_count": len(rows),
                 "reaction_count": len(reactions or []),
                 "decision_count": len(decisions or [])},
            )
        for reaction, decision in zip(reactions, decisions, strict=True):
            ordinary_id = reaction.get("ordinary_reaction_id")
            if (not isinstance(decision, dict)
                    or decision.get("ordinary_reaction_id") != ordinary_id
                    or decision.get("ordinary_reaction_type") != "swirl"
                    or decision.get("special_reaction_type") != "stellar_swirl"
                    or decision.get("decision") != "replace"
                    or decision.get("status") != "executed"
                    or decision.get("time_tick") != time_tick
                    or decision.get("event_id")
                    != str(ordinary_id)+":special-decision"):
                raise ElementalWorldPending(
                    "stellar_swirl_packet_decision_identity_mismatch",
                    "星扩散packet的审计决策与对应普通提案或替换结果串线。",
                    {"ordinary_reaction_id": ordinary_id,
                     "decision": copy.deepcopy(decision)},
                )
        replacement_by_ordinary_id = {
            reaction.get("ordinary_reaction_id"): (reaction, decision)
            for reaction, decision in zip(reactions, decisions, strict=True)
        }
        if (len(replacement_by_ordinary_id) != len(rows)
                or None in replacement_by_ordinary_id):
            raise ElementalWorldPending(
                "stellar_swirl_packet_router_identity_mismatch",
                "星扩散packet路由没有为每个普通提案返回唯一替换身份。",
                {"ordinary_reaction_ids": sorted(
                    str(key) for key in replacement_by_ordinary_id)},
            )
        if (routed_packet != reference_packet
                or value._special_commit_snapshot(producer_candidate)
                != value._special_commit_snapshot(reference_candidate)):
            raise ElementalWorldPending(
                "stellar_swirl_packet_producer_mismatch",
                "星扩散packet生产器的完整回执或后态与隔离参考执行不一致。",
                {"atomic_parent_id": parent_id, "area_id": area_id},
            )

        committed_receipts: list[dict] = []
        for preview in previews:
            row = preview["input"]
            expected = preview["expected_proposal"]
            pair = replacement_by_ordinary_id.get(expected["id"])
            if pair is None:
                raise ElementalWorldPending(
                    "stellar_swirl_packet_router_identity_mismatch",
                    "星扩散packet缺少当前child的替换结果。",
                    {"proposal_id": expected["id"]},
                )
            replacement, decision = pair
            target = value.get_target(row["application"]["target_id"])
            if (value._target_commit_snapshot(target)
                    != preview["target_state_before"]
                    or value._target_identity_changes(
                        target, preview["target_identity_before"])):
                raise ElementalWorldPending(
                    "stellar_swirl_target_state_changed_before_commit",
                    "目标状态在packet预览与提交之间发生变化，已整包回滚。",
                    {"target_id": target.target,
                     "proposal_id": expected["id"]},
                )
            route_binding = [{
                "expected_proposal": expected,
                "replacement": replacement,
                "decision": decision,
            }]
            attempt = {
                "event_id": row["event_id"],
                "stable_event_id": row["stable_event_id"],
                "action_id": row["action_id"],
                "actor": row["actor"],
                "time": time_tick,
                "application": copy.deepcopy(row["application"]),
                "damage_element": row.get("damage_element"),
                "contact": copy.deepcopy(row.get("contact")),
                "trigger_stat_sample": copy.deepcopy(
                    row.get("trigger_stat_sample")),
            }
            reference_routed_preview = value._reference_target_copy(target)
            profiles_before_route = copy.deepcopy(
                reference_routed_preview.profiles)
            reference_receipt = (
                reference_routed_preview._attempt_with_world_reaction_replacements(
                    reaction_replacements=route_binding,
                    **copy.deepcopy(attempt),
                ))
            expected_generated = copy.deepcopy(
                reference_routed_preview.generated_events)
            expected_target_after = value._target_commit_snapshot(
                reference_routed_preview)

            routed_preview = target.preview_copy()
            routed_preview.trace = copy.deepcopy(target.trace)
            routed_preview_receipt = (
                routed_preview._attempt_with_world_reaction_replacements(
                    reaction_replacements=route_binding,
                    **copy.deepcopy(attempt),
                ))
            routed_preview_generated = copy.deepcopy(
                routed_preview.generated_events)
            if (reference_receipt
                    != reference_routed_preview.trace[-1]
                    or reference_receipt.get("after")
                    != reference_routed_preview.state()
                    or reference_routed_preview.trace
                    != [*target.trace, reference_receipt]):
                raise ElementalWorldPending(
                    "stellar_swirl_packet_target_reference_invalid",
                    "packet child规范路由预演的回执、轨迹或后态不一致。",
                    {"target_id": target.target,
                     "proposal_id": expected["id"]},
                )
            if (routed_preview_receipt != reference_receipt
                    or routed_preview_generated != expected_generated
                    or value._target_commit_snapshot(routed_preview)
                    != expected_target_after
                    or value._target_commit_snapshot(target)
                    != preview["target_state_before"]
                    or expected_generated
                    or routed_preview.profiles != profiles_before_route
                    or routed_preview_receipt.get("after")
                    != routed_preview.state()
                    or routed_preview.trace
                    != [*target.trace, routed_preview_receipt]):
                raise ElementalWorldPending(
                    "stellar_swirl_packet_target_preview_mismatch",
                    "packet child最终路由预览改写了档案、后态或产生了额外轨迹。",
                    {"target_id": target.target,
                     "proposal_id": expected["id"]},
                )
            expected_receipt = reference_receipt
            if (_receipt_without_world_routing(expected_receipt)
                    != _receipt_without_world_routing(
                        preview["preview_receipt"])):
                raise ElementalWorldPending(
                    "stellar_swirl_packet_target_preview_mismatch",
                    "packet child预路由改写了反应路由之外的命中回执字段。",
                    {"target_id": target.target,
                     "proposal_id": expected["id"]},
                )
            receipt = target._attempt_with_world_reaction_replacements(
                reaction_replacements=route_binding,
                **attempt,
            )
            if target.generated_events != expected_generated:
                raise ElementalWorldPending(
                    "stellar_swirl_packet_commit_side_effects_unexpected",
                    "child提交产生了packet路由之外的派生事件，已整包回滚。",
                    {"target_id": target.target,
                     "event_ids": [event.get("event_id")
                                   for event in target.generated_events]},
                )
            if receipt != expected_receipt:
                raise ElementalWorldPending(
                    "stellar_swirl_packet_target_commit_mismatch",
                    "packet child正式提交回执与最终路由预览不一致。",
                    {"target_id": target.target,
                     "proposal_id": expected["id"]},
                )
            if (target.profiles != profiles_before_route
                    or receipt.get("after") != target.state()
                    or target.trace
                    != [*preview["target_state_before"]["trace"], receipt]
                    or value._target_commit_snapshot(target)
                    != expected_target_after
                    or value._target_identity_changes(
                        target, preview["target_identity_before"])):
                raise ElementalWorldPending(
                    "stellar_swirl_packet_target_commit_mismatch",
                    "packet child正式提交的后态、档案、轨迹或共享对象拓扑与预览不一致。",
                    {"target_id": target.target,
                     "proposal_id": expected["id"],
                     "identity_changed": value._target_identity_changes(
                         target, preview["target_identity_before"])},
                )
            committed_receipts.append(receipt)

        restored = value._restore_object(
            value.special_runtime, producer_candidate)
        if restored is not value.special_runtime:
            raise RuntimeError("星扩散packet提交破坏了共享特殊状态对象身份。")
        raw_generated = copy.deepcopy(routed_packet.get("generated_events", []))
        (generated, lunar_batch_count, stellar_batch_count,
         expiry_rows) = value._consume_special_generated_batch(
             raw_generated,
             time_tick=time_tick,
             source="reaction_packet",
         )
        if lunar_batch_count or expiry_rows:
            raise ElementalWorldPending(
                "world_special_packet_contains_deadline",
                "星扩散packet批次不得夹带月感电截止提交。",
                {"time_tick": time_tick},
            )
        cursor = (value.publish_generated_events(generated) if generated
                  else value.drain_generated_events()["cursor"])
        return {
            "contract_id": PACKET_CONTRACT_ID,
            "packet_id": parent_id+":stellar-swirl-packet",
            "group_key": {
                "atomic_parent_id": parent_id,
                "time_tick": time_tick,
                "area_id": area_id,
            },
            "child_count": len(rows),
            "target_ids": [row["application"]["target_id"] for row in rows],
            "application_receipts": committed_receipts,
            "reactions": copy.deepcopy(reactions),
            "decisions": copy.deepcopy(decisions),
            "generated_events": generated,
            "stellar_swirl_batch_count": stellar_batch_count,
            "cursor": cursor,
            "status": "executed",
        }

    return world.execute_atomic(commit)
