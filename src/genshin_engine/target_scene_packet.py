"""Scheduled, explicitly authored scene contact groups."""

from __future__ import annotations

import copy
from collections import Counter, defaultdict

from genshin_engine.elemental import ElementalPending
from genshin_engine.rotation_port import rotation_issue


def application_for(event):
    payload = event["data"]
    return payload.get("descriptor", payload).get("application") or {}


def annotate_contact_packets(events, profiles, issues):
    groups = defaultdict(list)
    for event in events:
        application = application_for(event)
        parent = application.get("atomic_parent_id")
        if parent is not None and event["kind"] in ("mechanics_hit", "elemental_application"):
            groups[event["time"], parent].append(event)
    parent_counts = Counter(parent for _, parent in groups)
    claimed_parents = set(parent_counts)
    for (_, parent), members in groups.items():
        if len(members) < 2 or not any(
                profiles.get(application_for(row).get("profile_id"), {}).get("element") == "anemo"
                for row in members):
            continue
        members.sort(key=lambda row: row["order"])
        scopes = {(row["data"]["action_id"], application_for(row).get("area_id"),
                   application_for(row).get("profile_id")) for row in members}
        targets = {application_for(row)["target_id"] for row in members}
        ids = tuple(row["id"] for row in members)
        if len(scopes) != 1 or len(targets) != len(members) or not parent.strip():
            issues.append(rotation_issue(
                "target_scene_atomic_contact_invalid",
                "同父风命中须来自同一动作与附着档案，登记在同一区域，且目标互不重复。",
                members[0]["id"], atomic_parent_id=parent, child_ids=list(ids)))
            continue
        # Pattern descriptors can reuse the same authored label at several
        # offsets. A stable expanded child identifies each original group.
        world_parent = parent
        if parent_counts[parent] > 1:
            world_parent = f"@scene-contact:{ids[0]}"
            while world_parent in claimed_parents:
                world_parent = "@" + world_parent
            claimed_parents.add(world_parent)
        for row in members:
            row["scene_packet_ids"] = ids
            row["scene_packet_world_parent"] = world_parent


def remaining_packet_members(first, expanded, cursor):
    ids = first["scene_packet_ids"]
    members = [first, *expanded[cursor:cursor+len(ids)-1]]
    if tuple(row["id"] for row in members) != ids or any(
            row["time"] != first["time"] or row["origin"] != first["origin"]
            or row.get("scene_packet_ids") != ids
            or row.get("scene_packet_world_parent") != first["scene_packet_world_parent"] for row in members):
        raise ElementalPending(
            "target_scene_atomic_contact_interleaved",
            "同父风命中的 order 范围包含其他事件；请将组内命中放在相邻的执行位置。",
            first["id"], {"atomic_parent_id": application_for(first)["atomic_parent_id"],
                          "child_ids": list(ids),
                          "observed_ids": [row["id"] for row in members]})
    return members


class ActiveContactPacket:
    def __init__(self, members):
        self.members = members
        self.prepared = False
        self.receipts = None
        self.immediate = defaultdict(list)
        self.after_members = []

    def prepare(self, runtime):
        self.prepared = True
        arguments = []
        for member in self.members:
            application = application_for(member)
            action_id = member["data"]["action_id"]
            ability = runtime.data["abilities"][runtime.data["actions"][action_id]["ability_id"]]
            hit = member["data"].get("hit")
            event = {"action_id": action_id, "character": ability["character"],
                     "element": hit["element"] if hit is not None else None}
            arguments.append(runtime._contact_arguments(member, event, application))
        first = self.members[0]
        self.receipts = runtime.target_scene.attempt_packet(
            arguments, first["id"], first["origin"], first["scene_packet_world_parent"])
        if self.receipts is None:
            return
        for row in runtime.target_scene.drain_generated_events():
            if row.get("type") == "special_reaction_damage_request" and row.get("component") == "immediate_anemo":
                self.immediate[row["target_id"]].append(row)
            else:
                self.after_members.append(row)

    def drain_for(self, runtime, cycle, parent):
        rows = self.immediate.pop(application_for(parent)["target_id"], [])
        runtime.elemental_bridge.drain_generated(cycle, parent, rows, sync=False)


def run_contact_packet(runtime, members, rows, frontline, energy):
    # Stage a branch before any phase. Both schedules share authored data;
    # only the generated-event registry needs a staged copy.
    previous = runtime.__dict__
    data = runtime.data
    staged_data = {**data, "events": dict(data["events"])}
    memo = {id(runtime): runtime, id(data): staged_data, id(data["events"]): staged_data["events"]}
    runtime.__dict__ = copy.deepcopy(previous, memo)
    packet = ActiveContactPacket(members)
    runtime._active_contact_packet = packet
    derived = []
    try:
        for member, row in zip(members, rows, strict=True):
            derived.extend(runtime._run_observed_event(member, row, frontline, energy))
        if packet.receipts is not None:
            first = members[0]
            if packet.receipts or packet.immediate:
                raise ElementalPending("target_scene_atomic_contact_incomplete",
                    "同父风命中缺少子事件交付；该组未结算。", first["id"])
            runtime.event_emitter.begin_batch()
            runtime.elemental_bridge.drain_generated(first["origin"], first, packet.after_members)
            derived.extend(runtime.queue)
        deadline = runtime.schedule_deadline(members[0]["origin"])
        if deadline:
            derived.append(deadline)
        runtime._active_contact_packet = None
        data["events"].update(staged_data["events"])
        runtime.data = data
        return derived
    except BaseException:
        runtime.__dict__ = previous
        raise
