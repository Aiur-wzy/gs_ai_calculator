"""Install reviewed Agent materialization into the existing generic runtime graph.

This module is intentionally a narrow adapter layer.  It does not query the
database, infer character mechanics, or branch on character/skill/equipment
identity.  Every installed value comes from a validated materialization
payload, an Agent-authored topology schedule, or an exact topology binding to
an already-present execution object.

The runtime supports bounded finite/periodic dispatch, elemental application,
direct energy delivery and self-contained D20 rule/state bundles.  Legacy
free-form buff/state payloads remain storable but deliberately non-executable;
only their closed handler-v2 counterparts can enter the effective graph.
"""
from __future__ import annotations

import copy
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from genshin_engine import validate_template
from genshin_engine.mechanics import validate_mechanics_model
from genshin_engine.rotation import validate_rotation_model

from .event_topology import event_topology_fingerprint, validate_event_topology
from .event_topology_binding import validate_event_topology_bindings
from .files import fingerprint
from .periodic_lifecycle import (
    PeriodicLifecycleError,
    periodic_lifecycle_capabilities,
    project_periodic_lifecycle,
)

CONTRACT_ID = "agent-execution-runtime-adapter-receipt-v1"
SCHEMA_VERSION = 1
ADAPTER_VERSION = 1


class PeriodicRuntimeUnsupported(ValueError):
    """A valid periodic declaration exceeds the currently wired App subset."""

    def __init__(self, issues: list[dict]):
        self.issues = issues
        super().__init__(issues[0]["message"] if issues else "periodic runtime unsupported")


def _issue(code: str, path: str, message: str, **identity: Any) -> dict:
    return {"code": code, "path": path, "message": message, **identity}


def _pointer_tokens(pointer: str) -> list[str]:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("invalid JSON pointer")
    result = []
    for raw in pointer[1:].split("/"):
        if "~" in raw:
            raw = raw.replace("~1", "/").replace("~0", "~")
        result.append(raw)
    return result


def _resolve(value: Any, pointer: str) -> Any:
    for token in _pointer_tokens(pointer):
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif (isinstance(value, list) and token.isdigit()
              and int(token) < len(value)):
            value = value[int(token)]
        else:
            raise ValueError("binding pointer does not resolve")
    return value


def _binding_index(plan: dict, topology: dict) -> tuple[dict[str, list[dict]], list[dict]]:
    extensions = plan.get("extensions") if isinstance(plan.get("extensions"), dict) else {}
    document = extensions.get("event_topology_bindings")
    if not isinstance(document, dict):
        return {}, [_issue(
            "agent_materialization_execution_bindings_missing",
            "extensions.event_topology_bindings",
            "有限/周期派发需要事件节点到具体执行对象的精确绑定。",
        )]
    try:
        validate_event_topology_bindings(document)
    except (TypeError, ValueError, KeyError) as exc:
        return {}, [_issue(
            "agent_materialization_execution_bindings_invalid",
            "extensions.event_topology_bindings", str(exc)[:512],
        )]
    if (document["topology_id"] != topology["topology_id"]
            or document["topology_fingerprint"] != event_topology_fingerprint(topology)):
        return {}, [_issue(
            "agent_materialization_execution_bindings_stale",
            "extensions.event_topology_bindings",
            "执行绑定不属于当前事件拓扑。",
        )]
    rows: dict[str, list[dict]] = defaultdict(list)
    issues = []
    for index, binding in enumerate(document["bindings"]):
        path = f"extensions.event_topology_bindings.bindings[{index}]"
        try:
            target = _resolve(plan, binding["pointer"])
        except ValueError as exc:
            issues.append(_issue(
                "agent_materialization_binding_target_missing", path + ".pointer",
                str(exc), node_id=binding["node_id"],
            ))
            continue
        if fingerprint(target) != binding["target_fingerprint"]:
            issues.append(_issue(
                "agent_materialization_binding_target_stale", path + ".target_fingerprint",
                "执行绑定目标已经变化。", node_id=binding["node_id"],
            ))
            continue
        rows[binding["node_id"]].append({**copy.deepcopy(binding), "target": copy.deepcopy(target)})
    return dict(rows), issues


def _node_map(topology: dict) -> dict[str, dict]:
    return {row["node_id"]: row for row in topology["nodes"]}


def _action_map(rotation: dict | None) -> dict[str, dict]:
    return {row["id"]: row for row in (rotation or {}).get("actions", [])}


def _anchor_to_action(topology: dict, rotation: dict | None, node_id: str,
                      *, periodic_base: bool = False) -> tuple[str, float]:
    """Return source action and absolute anchor time for a supported schedule.

    Point chains and the anchor of a periodic schedule are sufficient for the
    bounded adapters.  End anchors, explicit-offset parents and nested
    periodic parents remain unsupported instead of receiving guessed timing.
    """
    nodes = _node_map(topology)
    actions = _action_map(rotation)
    visiting: set[str] = set()

    def resolve(current: str, include_schedule_offset: bool = True) -> tuple[str, float]:
        if current in visiting:
            raise ValueError("topology schedule contains a cycle")
        visiting.add(current)
        try:
            node = nodes.get(current)
            if node is None:
                raise ValueError("topology schedule references a missing node")
            schedule = node["schedule"]
            kind = schedule["kind"]
            if kind not in {"point", "periodic"}:
                raise ValueError("adapter supports only point/periodic schedule chains")
            anchor = schedule["anchor"]
            if anchor["phase"] == "end":
                raise ValueError("end-anchored materialization is not supported by this adapter")
            if anchor["kind"] == "action":
                action = actions.get(anchor["ref_id"])
                if action is None or action.get("start") is None:
                    raise ValueError("source action or its start time is missing")
                action_id, base = action["id"], float(action["start"])
            else:
                action_id, base = resolve(anchor["ref_id"])
            if include_schedule_offset:
                if kind == "point":
                    offset = schedule.get("offset_seconds")
                else:
                    offset = schedule.get("first_offset_seconds")
                if offset is None:
                    raise ValueError("schedule offset is pending")
                base += float(offset)
            return action_id, round(base, 6)
        finally:
            visiting.remove(current)

    if periodic_base:
        node = nodes.get(node_id)
        if node is None or node["schedule"]["kind"] not in {
                "periodic", "explicit_offsets"}:
            raise ValueError(
                "periodic adapter requires a periodic or explicit-offset topology schedule")
        schedule = node["schedule"]
        anchor = schedule["anchor"]
        if anchor["phase"] == "end":
            raise ValueError("end-anchored periodic channels are unsupported")
        if anchor["kind"] == "action":
            action = actions.get(anchor["ref_id"])
            if action is None or action.get("start") is None:
                raise ValueError("source action or its start time is missing")
            return action["id"], float(action["start"])
        return resolve(anchor["ref_id"])
    return resolve(node_id)


def _target_hit_ids(bindings: dict[str, list[dict]], node_id: str) -> list[str]:
    result = []
    for binding in bindings.get(node_id, []):
        target = binding["target"]
        if binding["target_kind"] == "hit" and isinstance(target, dict):
            hit_id = target.get("id")
        elif binding["target_kind"] == "mechanics" and isinstance(target, dict):
            hit_id = target.get("hit_id") or target.get("prototype_hit_id")
        else:
            hit_id = None
        if isinstance(hit_id, str) and hit_id not in result:
            result.append(hit_id)
    return result


def _occupied_positions(rotation: dict, mechanics: dict, template: dict) -> set[tuple[int, int]]:
    rows: set[tuple[int, int]] = set()
    for action in rotation.get("actions", []):
        if action.get("start") is not None:
            rows.add((round(action["start"] * 1_000_000), action["order"]))
    for event in rotation.get("events", []):
        time = event.get("credit_at") if event.get("type") == "energy_credit" else event.get("t")
        if time is not None:
            rows.add((round(time * 1_000_000), event["order"]))
    for event in mechanics.get("condition_events", []):
        rows.add((round(event["t"] * 1_000_000), event["order"]))
    for event in mechanics.get("application_events", []):
        rows.add((round(event["t"] * 1_000_000), event["order"]))
    for hit in template.get("hits", []):
        if hit.get("t") is not None:
            rows.add((round(hit["t"] * 1_000_000), 0))
    return rows


def _allocate_order(time: float, requested: int, occupied: set[tuple[int, int]],
                    *, after: int | None = None) -> int:
    tick = round(time * 1_000_000)
    order = max(requested, (after + 1) if after is not None else requested)
    while (tick, order) in occupied:
        order += 1
        if order > 10**9:
            raise ValueError("cannot allocate a bounded event order")
    occupied.add((tick, order))
    return order


def _profile_from_payload(entry: dict, node_id: str) -> tuple[str, dict]:
    payload = entry["payload"]
    profile_id = "agent.mat.app." + fingerprint({
        "entry_id": entry["entry_id"], "node_id": node_id,
        "payload": payload,
    })[:40]
    profile = {
        "id": profile_id,
        "element": payload["element"],
        "application": "none" if payload["application_mode"] == "no_application" else "apply",
        "status": "assumed",
        "source": "agent-execution-materialization:" + entry["entry_id"],
    }
    mode = payload["application_mode"]
    if mode != "no_application":
        profile["gauge_units"] = payload["gauge_units"]
    if mode == "no_icd":
        profile["icd"] = {"kind": "none"}
    elif mode == "timer_counter":
        profile["icd"] = {
            "kind": "timer_counter", "counter_tag": payload["counter_tag"],
            "rule_group": payload["rule_group"],
            "reset_seconds": payload["timer_seconds"],
            "hit_interval": payload["hit_count"],
        }
    elif mode == "sequence":
        profile["icd"] = {
            "kind": "sequence", "counter_tag": payload["counter_tag"],
            "rule_group": payload["rule_group"],
            "reset_seconds": payload["timer_seconds"],
            "sequence": [int(value) for value in payload["sequence"]],
        }
    application = {
        "profile_id": profile_id,
        "target_id": payload["target_ref"],
        "landed": payload["landed"],
        "attempted": payload["attempted"],
    }
    return profile_id, {"profile": profile, "application": application}


def _existing_dispatch_hits(mechanics: dict) -> set[str]:
    result = {row["hit_id"] for row in mechanics.get("hit_events", [])}
    result.update(row["prototype_hit_id"] for row in mechanics.get("hit_patterns", []))
    return result


def _periodic_unsupported(entry: dict, code: str, field: str,
                          message: str, **details: Any) -> dict:
    return _issue(
        code, f"entries.{entry['entry_id']}.payload.{field}", message, **details)


def _periodic_dispatch_steps(entry: dict, topology: dict,
                             rotation: dict) -> tuple[list[dict], dict]:
    """Project the complete bounded handler-v2 lifecycle through one engine.

    Every establishment, control, and frontline boundary is first resolved to
    a confirmed topology position.  The shared integer-microsecond projector
    remains the sole authority for generations, refresh, pause, cancellation,
    and off-field stopping; this adapter only translates its occurrences into
    the existing execution graph.
    """
    payload = entry["payload"]
    lifecycle = payload["lifecycle"]
    nodes = _node_map(topology)
    unsupported: list[dict] = []
    emitted = [node_id for dispatch in payload["dispatches"]
               for node_id in dispatch["emits_node_ids"]]
    emitted_kinds = {node_id: nodes.get(node_id, {}).get("kind") for node_id in emitted}
    unsupported_nodes = sorted(
        node_id for node_id, kind in emitted_kinds.items()
        if kind not in {"contact", "damage", "application", "resource"})
    if unsupported_nodes:
        unsupported.append(_periodic_unsupported(
            entry, "agent_periodic_event_kind_unsupported", "dispatches",
            "每跳派发包含当前周期适配器尚未执行的事件种类。",
            nodes=[{"node_id": node_id, "kind": emitted_kinds[node_id]}
                   for node_id in unsupported_nodes],
        ))
    if unsupported:
        raise PeriodicRuntimeUnsupported(unsupported)

    actions = _action_map(rotation)
    period = rotation.get("period")
    if type(period) not in (int, float) or period <= 0:
        raise ValueError("periodic lifecycle projection requires a finite rotation period")

    schedule = payload["schedule"]
    if schedule["kind"] == "equal_interval":
        projector_schedule = {
            "kind": "equal_interval",
            "first_offset_ticks": round(schedule["first_offset_seconds"] * 1_000_000),
            "interval_ticks": round(schedule["interval_seconds"] * 1_000_000),
            "count": schedule["count"],
            "order": schedule["order"],
        }
        if schedule["count"] is None:
            projector_schedule["derive_count"] = schedule["derive_count"]
    else:
        projector_schedule = {
            "kind": "explicit_offsets",
            "offset_ticks": [round(row["offset_seconds"] * 1_000_000)
                             for row in schedule["occurrences"]],
            "orders": [row["order"] for row in schedule["occurrences"]],
        }

    owner_node = entry["node_ids"][0]

    def resolve_anchor(node_id: str, *, primary: bool = False) -> tuple[str, float]:
        return _anchor_to_action(
            topology, rotation, node_id,
            periodic_base=primary and node_id == owner_node,
        )

    establishments = []
    for index, establishment in enumerate(payload["establishments"]):
        action_id, time = resolve_anchor(
            establishment["anchor_node_id"], primary=index == 0)
        establishments.append({
            "trigger_id": establishment["establishment_id"],
            "root_action_id": action_id,
            "time_ticks": round(time * 1_000_000),
            "order": establishment["order"],
            "status": "confirmed",
        })

    controls = []
    for control in payload["controls"]:
        action_id, time = resolve_anchor(control["anchor_node_id"])
        projected = {
            "control_id": control["control_id"],
            "operation": control["operation"],
            "root_action_id": action_id,
            "time_ticks": round(time * 1_000_000),
            "order": control["order"],
            "status": "confirmed",
        }
        if control["duration_seconds"] is not None:
            projected["duration_ticks"] = round(
                control["duration_seconds"] * 1_000_000)
        controls.append(projected)

    on_field_intervals = []
    for interval in payload["on_field_intervals"]:
        _, start = resolve_anchor(interval["start_node_id"])
        _, end = resolve_anchor(interval["end_node_id"])
        start_node = nodes[interval["start_node_id"]]
        end_node = nodes[interval["end_node_id"]]
        on_field_intervals.append({
            "start_ticks": round(start * 1_000_000),
            "start_order": start_node["schedule"].get("order", 0),
            "end_ticks": round(end * 1_000_000),
            "end_order": end_node["schedule"].get("order", 0),
            "source_event_id": interval["interval_id"],
            "status": "confirmed",
        })

    try:
        projection = project_periodic_lifecycle(
            channels=[{
                "channel_id": payload["channel_id"],
                "schedule": projector_schedule,
                "deliveries": [],
            }],
            lifecycle={
                "duration_ticks": round(lifecycle["duration_seconds"] * 1_000_000),
                "refresh_policy": lifecycle["refresh_policy"],
                "pause_resume_policy": lifecycle["pause_resume_policy"],
                "off_field_policy": lifecycle["off_field_policy"],
            },
            establishments=establishments,
            controls=controls,
            on_field_intervals=on_field_intervals,
            window_end_ticks=round(period * 1_000_000),
            projection_scope="finite_prefix",
        )
    except PeriodicLifecycleError as exc:
        raise ValueError(str(exc)) from exc

    dispatch_node_ids = emitted
    steps = []
    for row in projection["occurrences"]:
        action_id = row["root_action_id"]
        action = actions.get(action_id)
        if action is None or action.get("start") is None:
            raise ValueError("periodic occurrence root action is absent")
        base_ticks = round(float(action["start"]) * 1_000_000)
        steps.append({
            "step_id": row["occurrence_id"],
            "action_id": action_id,
            "absolute_time": row["time_ticks"] / 1_000_000,
            "offset_seconds": (row["time_ticks"] - base_ticks) / 1_000_000,
            "order": row["order"],
            "emits_node_ids": dispatch_node_ids,
            "generation": row["generation"],
            "generation_trigger_id": row["trigger_id"],
            "occurrence_id": row["occurrence_id"],
        })
    projection_receipt = {
        "target_identity": "periodic_lifecycle:" + payload["profile_id"],
        "projection_contract": projection["contract"],
        "schedule_kind": schedule["kind"],
        "declared_refresh_policy": lifecycle["refresh_policy"],
        "refresh_policy_state": "executed_by_shared_projector",
        "summary": copy.deepcopy(projection["summary"]),
        "generations": copy.deepcopy(projection["generations"]),
        "boundary": copy.deepcopy(projection["boundary"]),
        "projection_scope": "finite_prefix",
        "projection_topology": copy.deepcopy(topology),
        "projection_rotation": copy.deepcopy(rotation),
        "projection_inputs": {"channels": [{"channel_id": payload["channel_id"],
            "schedule": copy.deepcopy(projector_schedule), "deliveries": []}],
            "lifecycle": copy.deepcopy(projection['lifecycle']),
            "establishments": copy.deepcopy(establishments), "controls": copy.deepcopy(controls),
            "on_field_intervals": copy.deepcopy(on_field_intervals),
            "window_end_ticks": round(period * 1_000_000), "projection_scope": "finite_prefix"},
        "occurrences": [{
            "occurrence_id": row["occurrence_id"],
            "generation": row["generation"],
            "trigger_id": row["trigger_id"],
            "root_action_id": row["root_action_id"],
            "time_ticks": row["time_ticks"],
            "order": row["order"],
        } for row in projection["occurrences"]],
        "dispatch_ids": [row["dispatch_id"] for row in payload["dispatches"]],
        "dispatch_group_semantics": "all_dispatches_each_occurrence",
        "emits_node_ids": copy.deepcopy(dispatch_node_ids),
        "source_evidence": {
            "status": "validated_executable",
            "fingerprints": copy.deepcopy(entry["source_fingerprints"]),
        },
    }
    return steps, projection_receipt


def _dispatch_plan(entry: dict, topology: dict, bindings: dict[str, list[dict]],
                   template: dict, rotation: dict, mechanics: dict,
                   applications: dict[str, dict], resources: dict[str, dict],
                   resource_issues: dict[str, list[dict]], *,
                   periodic: bool,
                   occupied: set[tuple[int, int]], claimed_hits: set[str]) -> dict:
    payload = entry["payload"]
    owner_node = entry["node_ids"][0]
    actions = _action_map(rotation)
    periodic_projection = None
    if periodic:
        steps, periodic_projection = _periodic_dispatch_steps(
            entry, topology, rotation)
    else:
        if payload["interrupt_policy"] != "none":
            raise ValueError("cancel_remaining requires an interruption lifecycle runtime")
        action_id, sequence_time = _anchor_to_action(topology, rotation, owner_node)
        action = actions[action_id]
        base = sequence_time - float(action["start"])
        steps = [{**row, "action_id": action_id,
                  "absolute_time": round(
                      float(action["start"]) + base + row["offset_seconds"], 6),
                  "offset_seconds": round(base + row["offset_seconds"], 6)}
                 for row in payload["steps"]]

    nodes = _node_map(topology)
    emitted_node_ids = {
        node_id for step in steps for node_id in step["emits_node_ids"]
    }
    if periodic_projection is not None and not steps:
        emitted_node_ids.update(periodic_projection["emits_node_ids"])
    unsupported_emissions = sorted(
        node_id for node_id in emitted_node_ids
        if nodes.get(node_id, {}).get("kind") not in {
            "damage", "contact", "application", "resource",
        }
    )
    if unsupported_emissions:
        kinds = [
            {"node_id": node_id, "kind": nodes.get(node_id, {}).get("kind")}
            for node_id in unsupported_emissions
        ]
        raise ValueError(
            "dispatch contains event kinds without an executable delivery: "
            + repr(kinds)
        )

    hits = {row["id"]: row for row in template.get("hits", [])}
    occurrences: dict[tuple[str, str], list[dict]] = defaultdict(list)
    application_by_hit: dict[tuple[str, str], list[dict | None]] = defaultdict(list)
    resource_occurrences: list[tuple[str, dict]] = []
    consumed_dispatch_nodes: set[str] = set()
    declared_hit_ids: set[str] = set()
    # An actual lifecycle can have no in-window pulse (a future first pulse,
    # or cancellation before it). Still validate and claim its typed delivery
    # templates so child applications/resources cannot become standalone
    # events. This local shape is never queued or saved as an occurrence.
    dispatch_steps = steps or [{"emits_node_ids": sorted(emitted_node_ids),
                                "validation_only": True}]
    for step in dispatch_steps:
        damage_nodes = [node_id for node_id in step["emits_node_ids"]
                        if nodes.get(node_id, {}).get("kind") in {"damage", "contact"}]
        step_hits = []
        for node_id in damage_nodes:
            for hit_id in _target_hit_ids(bindings, node_id):
                if hit_id not in step_hits:
                    step_hits.append(hit_id)
        declared_app_nodes = [
            node_id for node_id in step["emits_node_ids"]
            if nodes.get(node_id, {}).get("kind") == "application"]
        missing_app_nodes = [
            node_id for node_id in declared_app_nodes if node_id not in applications]
        if missing_app_nodes:
            raise ValueError(
                "application dispatch lacks a materialized application payload: "
                + ", ".join(sorted(missing_app_nodes)))
        app_nodes = [node_id for node_id in declared_app_nodes if node_id in applications]
        if len(app_nodes) > 1:
            raise ValueError("one occurrence cannot carry multiple elemental application payloads")
        app = applications[app_nodes[0]]["application"] if app_nodes else None
        declared_resource_nodes = [
            node_id for node_id in step["emits_node_ids"]
            if nodes.get(node_id, {}).get("kind") == "resource"]
        missing_resource_nodes = [
            node_id for node_id in declared_resource_nodes if node_id not in resources]
        if missing_resource_nodes:
            issues = []
            for node_id in sorted(missing_resource_nodes):
                child_issues = resource_issues.get(node_id, [])
                issues.append(_issue(
                    "agent_materialization_dispatch_resource_unsupported",
                    f"entries.{entry['entry_id']}.payload",
                    "派发引用的资源事件没有可执行的立即直接能量表示；父派发保持原子拒绝。",
                    node_id=node_id,
                    child_issue_codes=[row["code"] for row in child_issues],
                ))
            raise PeriodicRuntimeUnsupported(issues)
        if not step_hits and not declared_resource_nodes:
            raise ValueError(
                "each dispatch occurrence needs a concrete hit or supported resource delivery")
        consumed_dispatch_nodes.update(damage_nodes)
        consumed_dispatch_nodes.update(app_nodes)
        consumed_dispatch_nodes.update(declared_resource_nodes)
        for hit_id in step_hits:
            if hit_id not in hits:
                raise ValueError(f"bound hit {hit_id} is absent from the effective template")
        declared_hit_ids.update(step_hits)
        if step.get("validation_only"):
            continue
        resource_occurrences.extend(
            (node_id, step) for node_id in declared_resource_nodes)
        for hit_id in step_hits:
            key = (hit_id, step["action_id"])
            occurrences[key].append(step)
            application_by_hit[key].append(app)

    already = _existing_dispatch_hits(mechanics)
    original_hit_ids = declared_hit_ids
    collision = (original_hit_ids & already) | (original_hit_ids & claimed_hits)
    if collision:
        raise ValueError("dispatch ownership collides with an existing descriptor: "
                         + ", ".join(sorted(collision)))
    if periodic_projection is not None and not steps:
        # These are authoring prototypes, not events. Keep them in the native
        # source document; the effective numerical template contains only
        # prototypes with an actual occurrence or another real consumer.
        template["hits"] = [hit for hit in template["hits"]
                            if hit["id"] not in original_hit_ids]
    generated = []
    grouped_by_hit: dict[str, list[tuple[str, list[dict]]]] = defaultdict(list)
    for (hit_id, action_id), rows in occurrences.items():
        grouped_by_hit[hit_id].append((action_id, rows))
    for hit_id in sorted(grouped_by_hit):
        groups = sorted(
            grouped_by_hit[hit_id],
            key=lambda item: (
                min(row["absolute_time"] for row in item[1]), item[0]),
        )
        for group_index, (action_id, rows) in enumerate(groups):
            action = actions[action_id]
            key = (hit_id, action_id)
            app_rows = application_by_hit[key]
            app_fingerprints = {
                fingerprint(row) if row is not None else None for row in app_rows}
            if len(app_fingerprints) > 1:
                raise ValueError(
                    "one repeated prototype cannot vary application semantics by occurrence")
            application = copy.deepcopy(app_rows[0]) if app_rows else None
            effective_orders = []
            for row in rows:
                absolute = row["absolute_time"]
                after = action["order"] if absolute == action["start"] else None
                effective_orders.append(_allocate_order(
                    absolute, row["order"], occupied, after=after))
            prototype_id = hit_id
            if group_index:
                prototype_id = "agent.mat.hit." + fingerprint({
                    "entry_id": entry["entry_id"], "hit_id": hit_id,
                    "action_id": action_id,
                })[:40]
                if prototype_id in hits:
                    raise ValueError("derived periodic hit identity collides")
                prototype = copy.deepcopy(hits[hit_id])
                prototype["id"] = prototype_id
                template.setdefault("hits", []).append(prototype)
                hits[prototype_id] = prototype
            prototype = hits[prototype_id]
            prototype["t"] = rows[0]["absolute_time"]
            prototype["count"] = 1
            common = {"action_id": action_id}
            if application is not None:
                common["application"] = application
            if len(rows) == 1:
                descriptor = {"hit_id": prototype_id,
                              "order": effective_orders[0], **common}
                mechanics.setdefault("hit_events", []).append(descriptor)
                identity = "mechanics.hit_event:" + prototype_id
            else:
                pattern_id = "agent.mat.pattern." + fingerprint({
                    "entry_id": entry["entry_id"], "hit_id": hit_id,
                    "action_id": action_id,
                })[:40]
                descriptor = {"id": pattern_id,
                              "prototype_hit_id": prototype_id,
                              "offsets": [row["offset_seconds"] for row in rows],
                              "orders": effective_orders, **common}
                mechanics.setdefault("hit_patterns", []).append(descriptor)
                identity = "mechanics.hit_pattern:" + pattern_id
            generated.append({"target_identity": identity,
                              "hit_id": prototype_id,
                              "source_hit_id": hit_id,
                              "action_id": action_id,
                              "occurrences": len(rows),
                              "effective_orders": effective_orders,
                              **({"generation_ids": sorted({
                                  row["generation"] for row in rows})}
                                 if periodic else {})})
    claimed_hits.update(original_hit_ids)

    for node_id, step in resource_occurrences:
        prepared = resources[node_id]
        resource = prepared["payload"]
        action_id = step["action_id"]
        action = actions[action_id]
        absolute = step["absolute_time"]
        order = _allocate_order(
            absolute, step["order"], occupied,
            after=action["order"] if absolute == action["start"] else None)
        event_id = "agent.mat.resource." + fingerprint({
            "parent_entry_id": entry["entry_id"],
            "resource_entry_id": prepared["entry_id"],
            "node_id": node_id, "occurrence_id": step["step_id"],
        })[:40]
        rotation.setdefault("events", []).append({
            "id": event_id, "type": "energy_direct", "t": absolute,
            "order": order, "repeat": copy.deepcopy(action["repeat"]),
            "action_id": action_id,
            "amounts": {resource["receiver_ref"]: resource["amount"]},
            "status": "assumed",
            "source": "agent-execution-materialization:" + prepared["entry_id"],
        })
        generated.append({
            "target_identity": "rotation.event:" + event_id,
            "resource_entry_id": prepared["entry_id"],
            "action_id": action_id, "t": absolute, "order": order,
            **({"generation": step["generation"]} if periodic else {}),
        })
    if periodic_projection is not None:
        generated.insert(0, periodic_projection)
    return {"installed": generated,
            "consumed_dispatch_node_ids": sorted(consumed_dispatch_nodes)}


def _standalone_application(entry: dict, node_id: str, topology: dict,
                            rotation: dict, mechanics: dict,
                            application: dict, occupied: set[tuple[int, int]]) -> dict:
    action_id, time = _anchor_to_action(topology, rotation, node_id)
    action = _action_map(rotation)[action_id]
    node = _node_map(topology)[node_id]
    requested = node["schedule"].get("order", 0)
    order = _allocate_order(time, requested, occupied,
                            after=action["order"] if time == action["start"] else None)
    event_id = "agent.mat.application." + fingerprint({
        "entry_id": entry["entry_id"], "node_id": node_id,
    })[:40]
    mechanics.setdefault("application_events", []).append({
        "id": event_id, "action_id": action_id, "t": time, "order": order,
        "application": copy.deepcopy(application),
    })
    return {"target_identity": "mechanics.application_event:" + event_id,
            "action_id": action_id, "t": time, "order": order}


def _install_resource(entry: dict, topology: dict, rotation: dict,
                      occupied: set[tuple[int, int]]) -> list[dict]:
    payload = entry["payload"]
    if (payload["resource_id"] != "energy" or payload["operation"] != "add"
            or payload["delivery"] != "immediate"):
        raise ValueError("current resource adapter supports only immediate direct energy addition")
    characters = {row["id"] for row in rotation.get("characters", [])}
    if payload["receiver_ref"] not in characters:
        raise ValueError("energy receiver is not a current rotation character")
    installed = []
    for node_id in entry["node_ids"]:
        action_id, time = _anchor_to_action(topology, rotation, node_id)
        action = _action_map(rotation)[action_id]
        node = _node_map(topology)[node_id]
        requested = node["schedule"].get("order", 0)
        order = _allocate_order(time, requested, occupied,
                                after=action["order"] if time == action["start"] else None)
        event_id = "agent.mat.resource." + fingerprint({
            "entry_id": entry["entry_id"], "node_id": node_id,
        })[:40]
        rotation.setdefault("events", []).append({
            "id": event_id, "type": "energy_direct", "t": time, "order": order,
            "repeat": copy.deepcopy(action["repeat"]), "action_id": action_id,
            "amounts": {payload["receiver_ref"]: payload["amount"]},
            "status": "assumed",
            "source": "agent-execution-materialization:" + entry["entry_id"],
        })
        installed.append({"target_identity": "rotation.event:" + event_id,
                          "action_id": action_id, "t": time, "order": order})
    return installed


def _rule_selector_action_ids(rule: dict) -> set[str]:
    selectors = []
    trigger = rule.get("trigger")
    if isinstance(trigger, dict) and "event" in trigger:
        selectors.append(trigger)
    quota = rule.get("quota")
    if isinstance(quota, dict) and isinstance(quota.get("consume"), dict):
        selectors.append(quota["consume"])
    for effect in rule.get("effects", []):
        if isinstance(effect, dict) and isinstance(effect.get("filter"), dict):
            selectors.append(effect["filter"])
    return {action_id for selector in selectors
            for action_id in selector.get("action_ids", [])}


def _install_mechanics_rule_bundle(entry: dict, rotation: dict,
                                   mechanics: dict) -> list[dict]:
    """Merge one complete D20 rule bundle without deriving gameplay facts."""
    payload = entry["payload"]
    action_ids = set(_action_map(rotation))
    character_ids = {row["id"] for row in rotation.get("characters", [])}
    existing_rule_ids = {row["id"] for row in mechanics.get("rules", [])}
    incoming_rule_ids = {row["id"] for row in payload["rules"]}
    collision = existing_rule_ids & incoming_rule_ids
    if collision:
        raise ValueError("D20 rule identity collides with the effective graph: "
                         + ", ".join(sorted(collision)))
    existing_delivery = {(row["owner"], row["source_id"])
                         for row in mechanics.get("rules", [])}
    incoming_delivery = {(row["owner"], row["source_id"])
                         for row in payload["rules"]}
    collision_delivery = existing_delivery & incoming_delivery
    if collision_delivery:
        raise ValueError("D20 owner/source delivery is already owned by the effective graph")
    for rule in payload["rules"]:
        unknown_actions = _rule_selector_action_ids(rule) - action_ids
        if unknown_actions:
            raise ValueError("D20 rule references unknown actions: "
                             + ", ".join(sorted(unknown_actions)))
        beneficiaries = rule.get("beneficiaries", {})
        unknown_characters = set(beneficiaries.get("characters", [])) - character_ids
        if unknown_characters:
            raise ValueError("D20 rule references unknown beneficiaries: "
                             + ", ".join(sorted(unknown_characters)))
        if rule.get("owner") not in character_ids:
            raise ValueError("D20 rule owner is not a current rotation character")
    for owner in payload["resource_ownership"]:
        if 'action_id' in owner and owner["action_id"] not in action_ids:
            raise ValueError("D20 resource ownership references an unknown action")
        if owner["rule_id"] not in incoming_rule_ids:
            raise ValueError("D20 resource ownership references a rule outside its bundle")

    mechanics.setdefault("rules", []).extend(copy.deepcopy(payload["rules"]))
    mechanics.setdefault("initial_state", {}).setdefault("rules", []).extend(
        copy.deepcopy(payload["initial_state"]["rules"]))
    if payload["resource_ownership"]:
        mechanics.setdefault("resource_ownership", []).extend(
            copy.deepcopy(payload["resource_ownership"]))
    part_by_rule = {part["rule_id"]: part for part in payload["parts"]}
    return [{
        "target_identity": "mechanics.rule:" + rule["id"],
        "rule_id": rule["id"],
        "rule_fingerprint": fingerprint(rule),
        "initial_state_fingerprint": fingerprint(next(
            initial for initial in payload["initial_state"]["rules"]
            if initial["rule_id"] == rule["id"])),
        "part_id": part_by_rule[rule["id"]]["part_id"],
        "node_ids": copy.deepcopy(part_by_rule[rule["id"]]["node_ids"]),
        "source_ids": copy.deepcopy(part_by_rule[rule["id"]]["source_ids"]),
    } for rule in payload["rules"]]


def _adapter_row(entry: dict, status: str, *, installed=(), issues=()) -> dict:
    row = {
        "entry_id": entry["entry_id"], "family": entry["family"],
        "adapter_id": "generic-runtime." + entry["family"],
        "adapter_version": ADAPTER_VERSION,
        "status": status,
        "coverage_keys": copy.deepcopy(entry["coverage_keys"]),
        "installed": copy.deepcopy(list(installed)),
        "issues": copy.deepcopy(list(issues)),
    }
    row["adapter_fingerprint"] = fingerprint(row)
    return row


@dataclass
class _RuntimeContext:
    plan: dict
    topology: dict
    bindings: dict[str, list[dict]]
    binding_issues: list[dict]
    template: dict
    rotation: dict
    mechanics: dict
    occupied: set[tuple[int, int]]
    claimed_hits: set[str] = field(default_factory=set)
    rows_by_id: dict[str, dict] = field(default_factory=dict)
    application_entries: dict[str, dict] = field(default_factory=dict)
    application_nodes: dict[str, dict] = field(default_factory=dict)
    resource_entries: dict[str, dict] = field(default_factory=dict)
    resource_nodes: dict[str, dict] = field(default_factory=dict)
    resource_node_issues: dict[str, list[dict]] = field(default_factory=dict)
    dispatch_consumers: dict[str, set[str]] = field(default_factory=dict)
    consumed_dispatch_nodes: set[str] = field(default_factory=set)
    consumed_dispatch_by_parent: dict[str, set[str]] = field(default_factory=dict)


def _dispatch_consumer_index(entries: list[dict]) -> dict[str, set[str]]:
    """Index child nodes declared by every generic dispatch parent."""
    result: dict[str, set[str]] = defaultdict(set)
    for entry in entries:
        payload = entry.get("payload", {})
        if entry.get("family") == "finite_sequence":
            groups = payload.get("steps", [])
        elif entry.get("family") == "periodic_channel":
            groups = payload.get("dispatches", [])
        else:
            continue
        for group in groups:
            for node_id in group.get("emits_node_ids", []):
                result[node_id].add(entry["entry_id"])
    return dict(result)


def _parent_dispatch_issues(context: _RuntimeContext, entry: dict) -> list[dict]:
    """Prevent a declared dispatch child from falling back to standalone use."""
    issues = []
    for node_id in entry["node_ids"]:
        parent_ids = sorted(context.dispatch_consumers.get(node_id, ()))
        if not parent_ids:
            continue
        parent_states = [{
            "entry_id": parent_id,
            "status": context.rows_by_id.get(parent_id, {}).get(
                "status", "not_processed"),
        } for parent_id in parent_ids]
        if all(
            row["status"] == "installed"
            and node_id in context.consumed_dispatch_by_parent.get(
                row["entry_id"], set())
            for row in parent_states
        ):
            continue
        issues.append(_issue(
            "agent_materialization_parent_dispatch_uninstalled",
            f"entries.{entry['entry_id']}.node_ids",
            "该事件已经声明为派发子节点，但父派发未安装；不会降级为独立事件。",
            node_id=node_id,
            dependency_status="blocked",
            parent_dispatches=parent_states,
        ))
    return issues


@dataclass(frozen=True)
class RuntimeAdapterSpec:
    """One statically reviewed generic-family adapter registration."""

    family: str
    capability: str
    priority: int
    prepare: Callable[[_RuntimeContext, list[dict]], None] | None = None
    install: Callable[[_RuntimeContext, dict], None] | None = None
    finalize: Callable[[_RuntimeContext, list[dict]], None] | None = None
    unsupported_reason: str | None = None


def _prepare_application_entries(context: _RuntimeContext,
                                 entries: list[dict]) -> None:
    elemental = context.mechanics.get("elemental_model")
    for entry in entries:
        issues = []
        if not isinstance(elemental, dict):
            issues.append(_issue(
                "agent_materialization_elemental_runtime_missing",
                f"entries.{entry['entry_id']}",
                "附着材料化需要当前机制模型已经声明目标与元素规则集。",
            ))
        else:
            target = elemental.get("target", {}).get("id")
            if target != entry["payload"]["target_ref"]:
                issues.append(_issue(
                    "agent_materialization_application_target_mismatch",
                    f"entries.{entry['entry_id']}.payload.target_ref",
                    "附着目标与当前元素模型目标不一致。",
                    expected_target=target,
                ))
        if issues:
            context.rows_by_id[entry["entry_id"]] = _adapter_row(
                entry, "partial", issues=issues)
            continue
        per_node = {}
        profiles = []
        for node_id in entry["node_ids"]:
            profile_id, compiled = _profile_from_payload(entry, node_id)
            if any(row.get("id") == profile_id
                   for row in elemental.get("profiles", [])):
                issues.append(_issue(
                    "agent_materialization_profile_collision",
                    f"entries.{entry['entry_id']}",
                    "材料化附着档案身份与现有档案冲突。",
                    profile_id=profile_id,
                ))
                break
            profiles.append(compiled["profile"])
            per_node[node_id] = compiled
        if issues:
            context.rows_by_id[entry["entry_id"]] = _adapter_row(
                entry, "partial", issues=issues)
            continue
        context.application_entries[entry["entry_id"]] = {
            "entry": entry, "profiles": profiles, "nodes": per_node,
        }
        context.application_nodes.update(per_node)


def _install_dispatch_entry(context: _RuntimeContext, entry: dict,
                            *, periodic: bool) -> None:
    work_template = copy.deepcopy(context.template)
    work_rotation = copy.deepcopy(context.rotation)
    work_mechanics = copy.deepcopy(context.mechanics)
    work_occupied = set(context.occupied)
    work_claimed = set(context.claimed_hits)
    try:
        installed = _dispatch_plan(
            entry, context.topology, context.bindings, work_template,
            work_rotation, work_mechanics, context.application_nodes,
            context.resource_nodes, context.resource_node_issues,
            periodic=periodic, occupied=work_occupied,
            claimed_hits=work_claimed,
        )
        if periodic and not work_template.get("hits"):
            raise ValueError(
                "周期在本次窗口内没有命中，且没有其他可验证伤害或直接命中锚点；"
                "不能将空周期作为零伤计算接纳。")
        validate_template(work_template)
        validation = validate_mechanics_model(work_mechanics)
        if validation["status"] == "invalid":
            raise ValueError(validation["issues"][0]["message"])
        rotation_validation = validate_rotation_model(work_rotation)
        if rotation_validation["status"] == "invalid":
            raise ValueError(rotation_validation["issues"][0]["message"])
    except PeriodicRuntimeUnsupported as exc:
        issues = copy.deepcopy(exc.issues)
        issues.extend(context.binding_issues[:8])
        context.rows_by_id[entry["entry_id"]] = _adapter_row(
            entry, "unsupported", issues=issues)
        return
    except (KeyError, TypeError, ValueError) as exc:
        issues = [_issue(
            "agent_materialization_runtime_adapter_partial",
            f"entries.{entry['entry_id']}", str(exc)[:512],
        )]
        issues.extend(context.binding_issues[:8])
        context.rows_by_id[entry["entry_id"]] = _adapter_row(
            entry, "partial", issues=issues)
        return
    context.template, context.rotation, context.mechanics = (
        work_template, work_rotation, work_mechanics)
    context.occupied, context.claimed_hits = work_occupied, work_claimed
    consumed = set(installed["consumed_dispatch_node_ids"])
    context.consumed_dispatch_nodes.update(consumed)
    context.consumed_dispatch_by_parent[entry["entry_id"]] = consumed
    context.rows_by_id[entry["entry_id"]] = _adapter_row(
        entry, "installed", installed=installed["installed"])


def _install_finite_sequence(context: _RuntimeContext, entry: dict) -> None:
    _install_dispatch_entry(context, entry, periodic=False)


def _install_periodic_channel(context: _RuntimeContext, entry: dict) -> None:
    _install_dispatch_entry(context, entry, periodic=True)


def _finalize_application_entries(context: _RuntimeContext,
                                  entries: list[dict]) -> None:
    del entries
    for entry_id, prepared in context.application_entries.items():
        entry = prepared["entry"]
        dependency_issues = _parent_dispatch_issues(context, entry)
        if dependency_issues:
            context.rows_by_id[entry_id] = _adapter_row(
                entry, "partial", issues=dependency_issues)
            continue
        work_mechanics = copy.deepcopy(context.mechanics)
        work_occupied = set(context.occupied)
        installed = []
        issues = []
        try:
            work_mechanics["elemental_model"].setdefault("profiles", []).extend(
                copy.deepcopy(prepared["profiles"]))
            for node_id, compiled in prepared["nodes"].items():
                if node_id in context.consumed_dispatch_nodes:
                    installed.append({
                        "target_identity": "mechanics.application_profile:"
                        + compiled["profile"]["id"],
                        "delivery": "dispatch_descriptor",
                    })
                else:
                    installed.append(_standalone_application(
                        entry, node_id, context.topology, context.rotation,
                        work_mechanics, compiled["application"], work_occupied))
            validation = validate_mechanics_model(work_mechanics)
            if validation["status"] == "invalid":
                raise ValueError(validation["issues"][0]["message"])
        except (KeyError, TypeError, ValueError) as exc:
            issues.append(_issue(
                "agent_materialization_runtime_adapter_partial",
                f"entries.{entry_id}", str(exc)[:512],
            ))
            context.rows_by_id[entry_id] = _adapter_row(
                entry, "partial", issues=issues)
            continue
        context.mechanics, context.occupied = work_mechanics, work_occupied
        context.rows_by_id[entry_id] = _adapter_row(
            entry, "installed", installed=installed)


def _prepare_resource_entries(context: _RuntimeContext,
                              entries: list[dict]) -> None:
    """Validate the closed resource subset before any parent dispatch mutates.

    This mirrors application preparation: a periodic/finite parent can consume
    the node only when the child has one faithful executable representation.
    Unsupported consume/set/projectile/foreign-resource declarations remain
    visible on the child and cannot be silently projected as energy gain.
    """

    characters = {row["id"] for row in context.rotation.get("characters", [])}
    for entry in entries:
        payload = entry["payload"]
        issues = []
        if (payload["resource_id"] != "energy"
                or payload["operation"] != "add"
                or payload["delivery"] != "immediate"):
            issues.append(_issue(
                "agent_materialization_resource_semantics_unsupported",
                f"entries.{entry['entry_id']}.payload",
                "当前强类型资源适配器只执行立即直接能量增加；其他声明未被改写。",
                resource_id=payload["resource_id"],
                operation=payload["operation"],
                delivery=payload["delivery"],
            ))
        elif payload["receiver_ref"] not in characters:
            issues.append(_issue(
                "agent_materialization_resource_receiver_missing",
                f"entries.{entry['entry_id']}.payload.receiver_ref",
                "资源接收者不是当前动作模型角色。",
                receiver_ref=payload["receiver_ref"],
            ))
        if issues:
            status = ("unsupported" if issues[0]["code"].endswith("unsupported")
                      else "partial")
            context.rows_by_id[entry["entry_id"]] = _adapter_row(
                entry, status, issues=issues)
            for node_id in entry["node_ids"]:
                context.resource_node_issues[node_id] = copy.deepcopy(issues)
            continue
        prepared = {
            "entry": entry, "entry_id": entry["entry_id"],
            "payload": copy.deepcopy(payload),
        }
        context.resource_entries[entry["entry_id"]] = prepared
        registered = []
        for node_id in entry["node_ids"]:
            if node_id in context.resource_nodes:
                collision_issues = [_issue(
                        "agent_materialization_resource_node_collision",
                        f"entries.{entry['entry_id']}.node_ids",
                        "资源节点被多个材料化条目重复占用。",
                        node_id=node_id,
                    )]
                context.rows_by_id[entry["entry_id"]] = _adapter_row(
                    entry, "partial", issues=collision_issues)
                context.resource_entries.pop(entry["entry_id"], None)
                for registered_node_id in registered:
                    context.resource_nodes.pop(registered_node_id, None)
                    context.resource_node_issues[registered_node_id] = copy.deepcopy(
                        collision_issues)
                context.resource_node_issues[node_id] = copy.deepcopy(
                    collision_issues)
                break
            context.resource_nodes[node_id] = prepared
            registered.append(node_id)


def _finalize_resource_entries(context: _RuntimeContext,
                               entries: list[dict]) -> None:
    del entries
    for entry_id, prepared in context.resource_entries.items():
        entry = prepared["entry"]
        dependency_issues = _parent_dispatch_issues(context, entry)
        if dependency_issues:
            context.rows_by_id[entry_id] = _adapter_row(
                entry, "partial", issues=dependency_issues)
            continue
        dispatched = [node_id for node_id in entry["node_ids"]
                      if node_id in context.consumed_dispatch_nodes]
        standalone = [node_id for node_id in entry["node_ids"]
                      if node_id not in context.consumed_dispatch_nodes]
        work_rotation = copy.deepcopy(context.rotation)
        work_occupied = set(context.occupied)
        installed = [{
            "target_identity": "rotation.resource_dispatch:" + node_id,
            "delivery": "dispatch_descriptor",
        } for node_id in dispatched]
        try:
            if standalone:
                standalone_entry = {**entry, "node_ids": standalone}
                installed.extend(_install_resource(
                    standalone_entry, context.topology, work_rotation,
                    work_occupied))
            validation = validate_rotation_model(work_rotation)
            if validation["status"] == "invalid":
                raise ValueError(validation["issues"][0]["message"])
        except (KeyError, TypeError, ValueError) as exc:
            context.rows_by_id[entry_id] = _adapter_row(
                entry, "partial", issues=[_issue(
                    "agent_materialization_runtime_adapter_partial",
                    f"entries.{entry_id}", str(exc)[:512],
                )])
            continue
        context.rotation, context.occupied = work_rotation, work_occupied
        context.rows_by_id[entry_id] = _adapter_row(
            entry, "installed", installed=installed)


def _install_rule_bundle_entry(context: _RuntimeContext, entry: dict) -> None:
    work_mechanics = copy.deepcopy(context.mechanics)
    try:
        installed = _install_mechanics_rule_bundle(
            entry, context.rotation, work_mechanics)
        validation = validate_mechanics_model(work_mechanics)
        if validation["status"] == "invalid":
            raise ValueError(validation["issues"][0]["message"])
    except (KeyError, StopIteration, TypeError, ValueError) as exc:
        context.rows_by_id[entry["entry_id"]] = _adapter_row(
            entry, "partial", issues=[_issue(
                "agent_materialization_runtime_adapter_partial",
                f"entries.{entry['entry_id']}", str(exc)[:512],
            )])
        return
    context.mechanics = work_mechanics
    context.rows_by_id[entry["entry_id"]] = _adapter_row(
        entry, "installed", installed=installed)


def _install_resource_operation_entry(context: _RuntimeContext, entry: dict) -> None:
    work = copy.deepcopy(context.mechanics)
    incoming = copy.deepcopy(entry['payload']['shared_pools'])
    try:
        current = work.get('shared_pools')
        if current is None:
            work['shared_pools'] = incoming
        else:
            if current['schema_version'] != incoming['schema_version']:
                raise ValueError('resource-operation unit contract differs from current graph')
            for field in ('pools', 'operations'):
                existing = {r['id'] for r in current[field]}
                if existing & {r['id'] for r in incoming[field]}:
                    raise ValueError('resource-operation identity already has an owner')
                current[field].extend(incoming[field])
        from genshin_engine.shared_pools import validate_shared_pools
        errors = validate_shared_pools(work['shared_pools'],
            character_ids=[c['id'] for c in context.rotation['characters']],
            rule_ids=[r['id'] for r in work.get('rules', [])])
        if errors:
            raise ValueError(errors[0]['message'])
        validation = validate_mechanics_model(work)
        if validation['status'] == 'invalid':
            raise ValueError(validation['issues'][0]['message'])
    except (KeyError, TypeError, ValueError) as exc:
        context.rows_by_id[entry['entry_id']] = _adapter_row(entry, 'partial', issues=[_issue(
            'agent_resource_operation_invalid', 'entries.' + entry['entry_id'], str(exc)[:512])])
        return
    context.mechanics = work
    context.rows_by_id[entry['entry_id']] = _adapter_row(entry, 'installed', installed=[{
        'target_identity': 'mechanics.shared_pools.operation:' + op['id'],
        'operation_id': op['id'], 'operation_fingerprint': fingerprint(op)} for op in incoming['operations']])


def _handler_version(entry: dict) -> int | None:
    handler = entry.get("handler")
    version = handler.get("version") if isinstance(handler, dict) else None
    return version if type(version) is int else None


def _unsupported_handler_version(context: _RuntimeContext, entry: dict,
                                 *, supported: tuple[int, ...]) -> None:
    version = _handler_version(entry)
    context.rows_by_id[entry["entry_id"]] = _adapter_row(
        entry,
        "unsupported",
        issues=[_issue(
            "agent_materialization_runtime_handler_version_unsupported",
            f"entries.{entry['entry_id']}.handler.version",
            "该处理器版本只允许兼容存储，当前运行时不会执行。",
            handler_version=version,
            executable_versions=list(supported),
        )],
    )


def _install_typed_buff_entry(context: _RuntimeContext, entry: dict) -> None:
    """Install only the closed handler-v2 D20 benefit bundle."""

    if _handler_version(entry) != 2:
        _unsupported_handler_version(context, entry, supported=(2,))
        return
    _install_rule_bundle_entry(context, entry)


def _state_reference_catalogue(
        context: _RuntimeContext, mechanics: dict | None = None) -> dict[str, set[str]]:
    """Build the same conservative identity catalogue used by App review.

    This does not infer a gameplay event.  It only enumerates identities that
    already exist in the current typed rotation/mechanics/template graph so the
    finite clock cannot reference an invented action, event, condition or type.
    The engine repeats this check after its full runtime expansion.
    """

    from genshin_engine.registry import DAMAGE_TYPES
    from genshin_engine.rotation_schema import ACTION_TYPES

    mechanics = context.mechanics if mechanics is None else mechanics
    action_ids = {row["id"] for row in context.rotation.get("actions", [])}
    event_ids = set(action_ids) | {action_id + ":end" for action_id in action_ids}
    event_ids.update(
        row["id"] for row in context.rotation.get("events", [])
        if isinstance(row.get("id"), str)
    )
    for collection, key in (
        ("hit_events", "hit_id"),
        ("condition_events", "id"),
        ("application_events", "id"),
    ):
        event_ids.update(
            row[key] for row in mechanics.get(collection, [])
            if isinstance(row.get(key), str)
        )
    event_ids.update(
        f"{row['id']}:{index + 1}"
        for row in mechanics.get("hit_patterns", [])
        if isinstance(row.get("id"), str)
        for index in range(len(row.get("offsets", [])))
    )
    event_ids.update(
        row["id"] for row in context.template.get("hits", [])
        if isinstance(row.get("id"), str)
    )
    conditions = {
        row["condition"] for row in mechanics.get("condition_events", [])
        if isinstance(row.get("condition"), str)
    }
    custom_damage_types = {
        row["id"]
        for row in context.template.get("type_definitions", {}).get(
            "damage_types", [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    return {
        "character_ids": {
            row["id"] for row in context.rotation.get("characters", [])
            if isinstance(row.get("id"), str)
        },
        "event_ids": event_ids,
        "action_ids": action_ids,
        "conditions": conditions,
        "damage_type_ids": set(DAMAGE_TYPES) | custom_damage_types,
        "action_type_ids": set(ACTION_TYPES),
    }


def _install_typed_state_entry(context: _RuntimeContext, entry: dict) -> None:
    """Atomically merge one closed finite state clock and its D20 targets."""

    if _handler_version(entry) != 2:
        _unsupported_handler_version(context, entry, supported=(2,))
        return

    from genshin_engine.state_runtime_mechanics import (
        validate_state_runtime_bindings,
    )

    work_mechanics = copy.deepcopy(context.mechanics)
    try:
        installed = _install_mechanics_rule_bundle(
            entry, context.rotation, work_mechanics)
        incoming = copy.deepcopy(entry["payload"]["state_runtime"])
        current = work_mechanics.get("state_runtime")
        if current is None:
            work_mechanics["state_runtime"] = incoming
        else:
            if current.get("schema_version") != incoming.get("schema_version"):
                raise ValueError(
                    "state-runtime schema version differs from the effective graph")
            for field in ("lifecycles", "timers"):
                existing_ids = {row["id"] for row in current.get(field, [])}
                incoming_ids = {row["id"] for row in incoming.get(field, [])}
                collision = existing_ids & incoming_ids
                if collision:
                    raise ValueError(
                        "state-runtime identity collides with the effective graph: "
                        + ", ".join(sorted(collision)))
                current.setdefault(field, []).extend(
                    copy.deepcopy(incoming.get(field, [])))

        validation = validate_mechanics_model(work_mechanics)
        if validation["status"] == "invalid":
            raise ValueError(validation["issues"][0]["message"])
        reference_catalogue = _state_reference_catalogue(
            context, work_mechanics)
        binding_issues = validate_state_runtime_bindings(
            work_mechanics, **reference_catalogue)
        if binding_issues:
            first = binding_issues[0]
            raise ValueError(first.get("message", "state-runtime binding is invalid"))
    except (KeyError, StopIteration, TypeError, ValueError) as exc:
        context.rows_by_id[entry["entry_id"]] = _adapter_row(
            entry, "partial", issues=[_issue(
                "agent_materialization_runtime_adapter_partial",
                f"entries.{entry['entry_id']}", str(exc)[:512],
            )])
        return

    for lifecycle in incoming.get("lifecycles", []):
        installed.append({
            "target_identity": "mechanics.state_runtime.lifecycle:" + lifecycle["id"],
            "lifecycle_id": lifecycle["id"],
            "state_runtime_fingerprint": fingerprint(lifecycle),
        })
    for timer in incoming.get("timers", []):
        installed.append({
            "target_identity": "mechanics.state_runtime.timer:" + timer["id"],
            "timer_id": timer["id"],
            "state_runtime_fingerprint": fingerprint(timer),
        })
    context.mechanics = work_mechanics
    context.rows_by_id[entry["entry_id"]] = _adapter_row(
        entry, "installed", installed=installed)


_RUNTIME_ADAPTERS = (
    RuntimeAdapterSpec('resource_operation', 'typed_shared_pool_operations', 45,
        install=_install_resource_operation_entry),
    RuntimeAdapterSpec(
        "application", "elemental_profile_and_application_event", 10,
        prepare=_prepare_application_entries,
        finalize=_finalize_application_entries,
    ),
    RuntimeAdapterSpec(
        "finite_sequence", "bounded_hit_event_or_pattern", 20,
        install=_install_finite_sequence,
    ),
    RuntimeAdapterSpec(
        "periodic_channel", "versioned_bounded_periodic_dispatch_v2", 30,
        install=_install_periodic_channel,
    ),
    RuntimeAdapterSpec(
        "resource", "immediate_direct_energy", 40,
        prepare=_prepare_resource_entries,
        finalize=_finalize_resource_entries,
    ),
    RuntimeAdapterSpec(
        "mechanics_rule_bundle", "self_contained_d20_rule_bundle", 50,
        install=_install_rule_bundle_entry,
    ),
    RuntimeAdapterSpec(
        "buff", "typed_d20_benefit_bundle_v2", 60,
        install=_install_typed_buff_entry,
    ),
    RuntimeAdapterSpec(
        "state", "typed_finite_state_runtime_v2", 70,
        install=_install_typed_state_entry,
    ),
)
RUNTIME_ADAPTER_REGISTRY = {
    adapter.family: adapter for adapter in _RUNTIME_ADAPTERS
}

_RUNTIME_HANDLER_CAPABILITIES = {
    'resource_operation': {1: {'status': 'supported', 'capability': 'typed_shared_pool_operations',
        'limitations': ['source adoption and actual reaction/lifecycle routing remain separately authenticated']}},
    "finite_sequence": {
        1: {"status": "partial", "capability": "bounded_hit_event_or_pattern",
            "limitations": ["cancel_remaining requires an explicit interruption runtime"]},
    },
    "periodic_channel": {
        2: {"status": "supported", "capability": "versioned_bounded_periodic_dispatch_v2",
            "limitations": []},
    },
    "application": {
        2: {"status": "supported", "capability": "elemental_profile_and_application_event",
            "limitations": []},
    },
    "buff": {
        1: {"status": "unsupported", "capability": "legacy_free_form_storage_only",
            "limitations": ["legacy free-form buff has no execution authority"]},
        2: {"status": "supported", "capability": "typed_d20_benefit_bundle_v2",
            "limitations": []},
    },
    "resource": {
        1: {"status": "partial", "capability": "immediate_direct_energy",
            "limitations": ["only immediate direct energy addition is executable"]},
    },
    "state": {
        1: {"status": "unsupported", "capability": "legacy_free_form_storage_only",
            "limitations": ["legacy key/value state has no execution authority"]},
        2: {"status": "supported", "capability": "typed_finite_state_runtime_v2",
            "limitations": [
                "finite state-runtime v1 clock only",
                "dispatch_rule hooks only; resource_operation hooks remain unsupported",
            ]},
    },
    "mechanics_rule_bundle": {
        1: {"status": "supported", "capability": "self_contained_d20_rule_bundle",
            "limitations": []},
    },
}


def runtime_handler_capability(family: str, version: int) -> dict:
    """Return exact executable support for one frozen handler version."""

    if type(version) is not int:
        return {
            "status": "unsupported",
            "capability": "invalid_handler_version_type",
            "limitations": ["handler version must be an integer"],
        }
    result = _RUNTIME_HANDLER_CAPABILITIES.get(family, {}).get(version)
    if result is None:
        return {
            "status": "unsupported",
            "capability": "unregistered_handler_version",
            "limitations": ["the exact family/version pair is not registered"],
        }
    return copy.deepcopy(result)


def runtime_entry_capability(entry: dict) -> dict:
    """Refine version support for the closed payload subsets already known.

    This remains a side-effect-free admission hint.  The runtime adapter is
    still authoritative and validates the complete effective graph atomically.
    """

    family = entry.get("family")
    version = entry.get("handler_version")
    if version is None and isinstance(entry.get("handler"), dict):
        version = entry["handler"].get("version")
    result = runtime_handler_capability(family, version)
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    if result["status"] == "unsupported":
        return result
    if family == "finite_sequence" and version == 1:
        if payload.get("interrupt_policy") != "none":
            return {
                "status": "unsupported",
                "capability": "finite_interruption_runtime_missing",
                "limitations": ["cancel_remaining requires an explicit interruption runtime"],
            }
        result["status"] = "supported"
    elif family == "resource" and version == 1:
        if not (
            payload.get("resource_id") == "energy"
            and payload.get("operation") == "add"
            and payload.get("delivery") == "immediate"
        ):
            return {
                "status": "unsupported",
                "capability": "resource_payload_semantics_unsupported",
                "limitations": ["only immediate direct energy addition is executable"],
            }
        result["status"] = "supported"
    return result


_DISPATCH_FAMILIES = frozenset({"finite_sequence", "periodic_channel"})


def _dispatch_install_order(entries: list[dict]) -> list[dict]:
    """Return a stable parent-before-child order for generic dispatch entries.

    A dispatch node may itself be emitted by another finite/periodic entry.
    The currently shipped runtime deliberately rejects that payload shape, but
    preserving a dependency-aware order keeps the adapter contract extensible:
    a future generic nested-dispatch implementation cannot accidentally install
    a child before its parent has atomically consumed the child node.
    Cycles remain in source order and are rejected by the dependency guard.
    """

    dispatch_entries = [
        entry for entry in entries if entry.get("family") in _DISPATCH_FAMILIES
    ]
    source_order = {
        entry["entry_id"]: index for index, entry in enumerate(dispatch_entries)
    }
    by_id = {entry["entry_id"]: entry for entry in dispatch_entries}
    node_owner = {
        node_id: entry["entry_id"]
        for entry in dispatch_entries
        for node_id in entry.get("node_ids", [])
    }
    outgoing: dict[str, set[str]] = defaultdict(set)
    indegree = {entry_id: 0 for entry_id in by_id}
    for parent in dispatch_entries:
        payload = parent.get("payload", {})
        groups = (
            payload.get("steps", [])
            if parent.get("family") == "finite_sequence"
            else payload.get("dispatches", [])
        )
        for group in groups:
            for node_id in group.get("emits_node_ids", []):
                child_id = node_owner.get(node_id)
                if child_id is None or child_id in outgoing[parent["entry_id"]]:
                    continue
                outgoing[parent["entry_id"]].add(child_id)
                indegree[child_id] += 1

    ready = sorted(
        (entry_id for entry_id, degree in indegree.items() if degree == 0),
        key=source_order.__getitem__,
    )
    ordered_ids: list[str] = []
    while ready:
        current = ready.pop(0)
        ordered_ids.append(current)
        for child_id in sorted(
            outgoing.get(current, ()), key=source_order.__getitem__
        ):
            indegree[child_id] -= 1
            if indegree[child_id] == 0:
                ready.append(child_id)
                ready.sort(key=source_order.__getitem__)
    ordered_ids.extend(
        entry_id
        for entry_id in sorted(by_id, key=source_order.__getitem__)
        if entry_id not in ordered_ids
    )
    return [by_id[entry_id] for entry_id in ordered_ids]


def _install_with_parent_guard(
        context: _RuntimeContext, entry: dict,
        installer: Callable[[_RuntimeContext, dict], None]) -> None:
    """Install one entry only when every declared dispatch parent succeeded."""

    dependency_issues = _parent_dispatch_issues(context, entry)
    if dependency_issues:
        context.rows_by_id[entry["entry_id"]] = _adapter_row(
            entry, "partial", issues=dependency_issues)
        return
    installer(context, entry)


def adapt_agent_execution_runtime(plan: dict, topology: dict, effective_input: dict,
                                  template: dict, rotation_model: dict | None,
                                  mechanics_model: dict | None) -> dict:
    """Install the supported subset and return an auditable effective graph.

    Unsupported or unrepresentable entries are left untouched and reported as
    partial/unsupported.  Supported entries are committed one at a time only
    after their complete mutation validates against the existing engine schema.
    """
    validate_event_topology(topology)
    template = copy.deepcopy(template)
    rotation = copy.deepcopy(rotation_model)
    mechanics = copy.deepcopy(mechanics_model)
    entries = effective_input.get("entries", [])
    if not isinstance(rotation, dict) or not isinstance(mechanics, dict):
        rows = [_adapter_row(entry, "partial", issues=[_issue(
            "agent_materialization_runtime_model_missing",
            f"entries.{entry['entry_id']}",
            "动作模型或机制模型尚未建立，无法安装材料化输入。",
        )]) for entry in entries]
        return _result(effective_input, template, rotation, mechanics, rows, [])

    bindings, binding_issues = _binding_index(plan, topology)
    context = _RuntimeContext(
        plan=plan,
        topology=topology,
        bindings=bindings,
        binding_issues=binding_issues,
        template=template,
        rotation=rotation,
        mechanics=mechanics,
        occupied=_occupied_positions(rotation, mechanics, template),
        dispatch_consumers=_dispatch_consumer_index(entries),
    )
    by_family: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        by_family[entry["family"]].append(entry)
    adapters = sorted(RUNTIME_ADAPTER_REGISTRY.values(), key=lambda row: row.priority)

    for adapter in adapters:
        family_entries = by_family.get(adapter.family, [])
        if family_entries and adapter.prepare is not None:
            adapter.prepare(context, family_entries)
    # Dispatch parents are dependency-ordered independently of their registry
    # priority.  All other direct installers run only after dispatch has
    # settled, so a failed parent cannot leave a rule/buff/state child active.
    for entry in _dispatch_install_order(entries):
        adapter = RUNTIME_ADAPTER_REGISTRY[entry["family"]]
        if adapter.install is not None:
            _install_with_parent_guard(context, entry, adapter.install)
    for adapter in adapters:
        if adapter.install is None or adapter.family in _DISPATCH_FAMILIES:
            continue
        for entry in by_family.get(adapter.family, []):
            _install_with_parent_guard(context, entry, adapter.install)
    for adapter in adapters:
        family_entries = by_family.get(adapter.family, [])
        if family_entries and adapter.finalize is not None:
            adapter.finalize(context, family_entries)

    for entry in entries:
        if entry["entry_id"] in context.rows_by_id:
            continue
        adapter = RUNTIME_ADAPTER_REGISTRY.get(entry["family"])
        reason = (adapter.unsupported_reason if adapter is not None
                  else "未安装该通用机制族的运行时适配器。")
        context.rows_by_id[entry["entry_id"]] = _adapter_row(
            entry, "unsupported", issues=[_issue(
                "agent_materialization_runtime_adapter_unsupported",
                f"entries.{entry['entry_id']}", reason,
            )])
    rows = [context.rows_by_id[entry["entry_id"]] for entry in entries]
    return _result(
        effective_input, context.template, context.rotation,
        context.mechanics, rows, binding_issues,
    )


def _result(effective_input: dict, template: dict, rotation: dict | None,
            mechanics: dict | None, rows: list[dict], binding_issues: list[dict]) -> dict:
    installed_keys = [key for row in rows if row["status"] == "installed"
                      for key in row["coverage_keys"]]
    pending_keys = [key for row in rows if row["status"] != "installed"
                    for key in row["coverage_keys"]]
    status = "complete" if rows and not pending_keys else (
        "partial" if rows else "empty")
    receipt = {
        "contract_id": CONTRACT_ID, "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION, "status": status,
        "effective_input_fingerprint": effective_input.get("bundle_fingerprint"),
        "entries": rows,
        "coverage": {"installed_keys": installed_keys,
                     "pending_keys": pending_keys},
        "binding_diagnostics": copy.deepcopy(binding_issues[:32]),
        "effective_graph": {
            "template_fingerprint": fingerprint(template),
            "rotation_model_fingerprint": fingerprint(rotation),
            "mechanics_model_fingerprint": fingerprint(mechanics),
        },
        "database_access": "none",
        "character_identity_dispatch": "forbidden",
    }
    receipt["receipt_fingerprint"] = fingerprint(receipt)
    return {"status": status, "template": template,
            "rotation_model": rotation, "mechanics_model": mechanics,
            "receipt": receipt}


def agent_execution_runtime_capabilities() -> dict:
    capabilities = {
        "contract_id": CONTRACT_ID, "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "families": {
            adapter.family: adapter.capability
            for adapter in sorted(
                RUNTIME_ADAPTER_REGISTRY.values(), key=lambda row: row.priority)
        },
        "database_access": "none",
        "identity_dispatch": "none",
        "selection": "static_per_family_registry",
        "family_versions": {
            family: {str(version): copy.deepcopy(capability)
                     for version, capability in sorted(versions.items())}
            for family, versions in sorted(_RUNTIME_HANDLER_CAPABILITIES.items())
        },
    }
    capabilities["periodic_channel_v2"] = {
        "automatic_consumption": True,
        "manual_feature_toggle": False,
        "schedule": {
            "equal_interval": "supported",
            "explicit_offsets": "supported",
            "explicit_count": "supported",
            "inclusive_duration_count_derivation": "supported",
            "while_active_inclusive_floor_derivation": "supported",
        },
        "lifecycle": {
            "finite_window_active_or_paused_boundary": "supported_with_finite_proof_only",
            "single_establishment": "supported",
            "multiple_establishments": "supported",
            "replace_previous": "supported",
            "reset_duration": "supported",
            "extend_duration": "supported",
            "ignore_new": "supported",
            "early_cancel": "supported",
            "off_field_continue": "supported",
            "off_field_stop": "supported",
            "pause_preserve_phase": "supported",
            "pause_reset_phase": "supported",
        },
        "dispatch": {
            "group_semantics": "all_dispatches_each_occurrence",
            "damage_or_contact_binding": "supported",
            "application_and_icd_binding": "supported",
            "resource_binding": "immediate_direct_energy_supported",
            "buff_or_state_binding": "declared_unsupported",
        },
        "limits": {
            "occurrences_per_channel_phase": periodic_lifecycle_capabilities()[
                "limits"]["occurrences_per_channel_phase"],
        },
        "projection": periodic_lifecycle_capabilities()["contract"],
    }
    return capabilities


__all__ = [
    "CONTRACT_ID", "adapt_agent_execution_runtime",
    "agent_execution_runtime_capabilities", "runtime_entry_capability",
    "runtime_handler_capability",
]
