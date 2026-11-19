"""Read-only intervals from committed special-reaction business receipts.

This module never advances a world, guesses a layer, or evaluates a formula.
Internal per-field deadline assignments deliberately are not observations.
"""

from __future__ import annotations

import copy
import math
from collections import defaultdict
from functools import partial

from genshin_engine.rotation_port import TICKS
from genshin_engine.periodic_resource_timeline import project_resource_intervals

CONTRACT = "special-state-timeline-v1"
FAMILY = "stellar_superconduct"


def special_state_timeline_capabilities():
    return {"contract": CONTRACT, "version": 1, "result_field": "special_state_timeline",
            "kinds": ["field_accumulator", "settled_damage_bonus", "resource_inventory", "resource_mode"],
            "authority": "committed_business_trace_projection_only",
            "intervals": "half_open_with_ordered_boundaries",
            "notes": "累计层数与最近完成结算的伤害增益分开；不改变状态、公式或附着。"}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _boundary(row, index):
    tick = row.get("time_tick")
    if type(tick) is not int:
        tick = round(row["t"] * TICKS)
    return {"time_tick": tick, "trace_index": index,
            "event_id": row.get("event_id"), "order": row.get("order", index)}


def _state_record(instance, kind, layers, *, area, index, boundary, operation,
                  reference, expires_at=None, **extra):
    return {"id": f"{area}:{kind}:{index}", "kind": kind, "reaction_type": FAMILY,
            "area_id": area, "target_id": None, "scope": "area",
            "instance_id": instance, "status": "active", "knowledge": "known",
            "layers": layers, "start": boundary["time_tick"] / TICKS,
            "start_boundary": copy.deepcopy(boundary), "expires_at": expires_at,
            "cause": operation, "source_refs": [reference], **extra}


def _record_change(instance, kind, status, layers=None, *, output, area, index,
                   boundary, operation, reference, **extra):
    output.append({"id": f"{area}:{kind}:{index}:change", "kind": kind,
                   "reaction_type": FAMILY, "area_id": area,
                   "instance_id": instance, "t": boundary["time_tick"] / TICKS,
                   "boundary": copy.deepcopy(boundary), "status": status,
                   "layers": layers, "cause": operation,
                   "source_refs": [reference], **extra})


def build_special_state_timeline(trace, *, duration, execution_limit, execution_status):
    """Project actual observations; safe to reconstruct when verifying a file.

    ``execution_limit`` comes from the already validated execution-window
    receipt, not the last damage time. A historical trace with insufficient
    lineage is explicitly partial rather than reconstructed from final state.
    """
    if (not _finite(duration) or duration < 0 or not _finite(execution_limit)
            or not 0 <= execution_limit <= duration):
        raise ValueError("特殊状态时间线需要合法的有限执行窗口。")
    output = {"contract": CONTRACT, "version": 1, "duration": duration,
              "execution_limit": execution_limit, "execution_status": execution_status,
              "status": "complete", "states": [], "changes": [], "issues": [],
              "notes": ["累计层数不是本次伤害使用的结算层数；两条状态分别显示。",
                        "区间右端不包含，同刻先后由来源流水位置确定；查看不触发重算。"]}
    grouped = defaultdict(list)
    for index, row in enumerate(trace or []):
        if (not isinstance(row, dict) or row.get("type") != "special_reaction_state"
                or row.get("reaction_type") != FAMILY or row.get("status") != "executed"
                or not _finite(row.get("t")) or not 0 <= row["t"] <= execution_limit):
            continue
        # Inventory observations are not field layer observations. A resource
        # creation may also emit an actual field refresh; that row has no count.
        if row.get('resource_type') == 'periodic_resource' and 'count' in row:
            continue
        area = row.get("area_id")
        if not isinstance(area, str) or not area:
            output["issues"].append({"code": "state_area_missing", "trace_index": index})
            continue
        grouped[area].append((index, row))

    def issue(code, index, area):
        output["issues"].append({"code": code, "trace_index": index, "area_id": area})

    def close(state, boundary, reason):
        if state is None:
            return
        end_tick = min(boundary["time_tick"], round(execution_limit * TICKS))
        natural = state.get("expires_at")
        end_reason = reason
        if _finite(natural) and round(natural * TICKS) < end_tick:
            end_tick = round(natural * TICKS)
            end_reason = "declared_expiry_unobserved"
            issue(end_reason, state["source_refs"][0]["trace_index"], state["area_id"])
        end = max(state["start"], end_tick / TICKS)
        # Zero-duration transitions remain in changes with their ordered
        # boundary; inventing a visible-width interval would mislead users.
        if end > state["start"]:
            output["states"].append({**copy.deepcopy(state), "end": end,
                                     "end_boundary": {**boundary, "time_tick": end_tick},
                                     "end_reason": end_reason})

    for area in sorted(grouped):
        accumulator = bonus = None
        instance = None
        rows = sorted(grouped[area], key=lambda entry: (_boundary(entry[1], entry[0])["time_tick"], entry[0]))
        for index, row in rows:
            boundary = _boundary(row, index)
            operation = row.get("operation")
            reference = {"trace_index": index, "event_id": row.get("event_id")}

            context = {"area": area, "index": index, "boundary": boundary,
                       "operation": operation, "reference": reference}
            state = partial(_state_record, **context)
            change = partial(_record_change, output=output["changes"], **context)

            def unknown(previous, kind, *, boundary=boundary, state=state, change=change):
                """An incomplete committed observation ends previous certainty.

                Keep only separately proven lifecycle metadata. A later complete
                business receipt can restore a known value without inventing an
                intermediate layer count or continuing an old factor.
                """
                if previous is None:
                    return None
                close(previous, boundary, "state_observation_incomplete")
                value = state(previous["instance_id"], kind, None,
                              expires_at=previous.get("expires_at"), knowledge="unknown")
                if kind == "field_accumulator":
                    value["next_settlement_at"] = previous.get("next_settlement_at")
                change(previous["instance_id"], kind, "unknown")
                return value

            if operation == "establish_or_refresh_field":
                layers = row.get("current_layers")
                fresh = row.get("trigger_application_layer_eligibility") == "establishing_application_excluded"
                if fresh or instance is None or accumulator is None:
                    instance = row.get("event_id") or f"{area}:established:{index}"
                if type(layers) is not int or not 0 <= layers <= 12:
                    issue("state_layers_unknown", index, area)
                    close(accumulator, boundary, "state_observation_incomplete")
                    accumulator = state(instance, "field_accumulator", None,
                                        expires_at=row.get("expires_at"), knowledge="unknown",
                                        next_settlement_at=row.get("next_settlement_at"))
                    change(instance, "field_accumulator", "unknown")
                    continue
                close(accumulator, boundary, "refresh" if not fresh else "reestablished")
                accumulator = state(instance, "field_accumulator", layers, expires_at=row.get("expires_at"),
                                    next_settlement_at=row.get("next_settlement_at"))
                change(instance, "field_accumulator", "active", layers)
                if fresh and row.get("initial_grant_layers"):
                    initial = row["initial_grant_layers"]
                    factor = row.get("initial_grant_factor")
                    if type(initial) is not int or not 0 < initial <= 12 or not _finite(factor):
                        issue("state_initial_grant_incomplete", index, area)
                        bonus = unknown(bonus, "settled_damage_bonus")
                    else:
                        close(bonus, boundary, "initial_grant")
                        bonus = state(instance, "settled_damage_bonus", initial,
                                      expires_at=row.get("initial_grant_expires_at"),
                                      system_factor=factor,
                                      ordinary_cryo_electro_bonus=row.get("initial_grant_ordinary_bonus"))
                        change(instance, "settled_damage_bonus", "active", initial, system_factor=factor)
            elif operation == "count_successful_application":
                if accumulator is None:
                    if row.get("reason") == "field_inactive":
                        change(instance, "field_accumulator", "inactive")
                    elif row.get("counted"):
                        issue("state_establishment_missing", index, area)
                    continue
                layers = row.get("layers")
                if type(layers) is not int or not 0 <= layers <= 12:
                    issue("state_layers_unknown", index, area)
                    accumulator = unknown(accumulator, "field_accumulator")
                    continue
                if layers != accumulator["layers"]:
                    old = accumulator
                    close(old, boundary, "layer_change")
                    accumulator = state(instance, "field_accumulator", layers, expires_at=old["expires_at"],
                                        next_settlement_at=old.get("next_settlement_at"))
                    change(instance, "field_accumulator", "active", layers, reason=row.get("reason"))
            elif operation == "settle_four_second_window":
                layers, factor = row.get("layers"), row.get("system_factor")
                if instance is None or type(layers) is not int or not 0 <= layers <= 12 or not _finite(factor):
                    issue("state_settlement_incomplete", index, area)
                    accumulator = unknown(accumulator, "field_accumulator")
                    bonus = unknown(bonus, "settled_damage_bonus")
                    continue
                if accumulator is not None:
                    old = accumulator
                    close(old, boundary, "settlement_clear")
                    accumulator = state(instance, "field_accumulator", 0, expires_at=old["expires_at"],
                                        next_settlement_at=row.get("next_settlement_at"))
                    change(instance, "field_accumulator", "active", 0)
                close(bonus, boundary, "settlement")
                bonus = state(instance, "settled_damage_bonus", layers, expires_at=row.get("buff_expires_at"),
                              system_factor=factor,
                              ordinary_cryo_electro_bonus=row.get("ordinary_cryo_electro_bonus"))
                change(instance, "settled_damage_bonus", "active", layers, system_factor=factor)
            elif operation == "expire_field":
                close(accumulator, boundary, "field_expiry")
                accumulator = None
                change(instance, "field_accumulator", "expired")
            elif operation == "expire_settlement_buff":
                close(bonus, boundary, "buff_expiry")
                # An old settlement may outlive/refer to a previous field.
                source_instance = bonus["instance_id"] if bonus else None
                bonus = None
                change(source_instance, "settled_damage_bonus", "expired", source_instance_id=source_instance)
            else:
                issue("state_operation_unsupported", index, area)
                accumulator = unknown(accumulator, "field_accumulator")
                bonus = unknown(bonus, "settled_damage_bonus")

        boundary = {"time_tick": round(execution_limit * TICKS), "trace_index": None,
                    "event_id": None, "order": None}
        reason = "execution_limit" if execution_limit < duration else "window_end"
        close(accumulator, boundary, reason)
        close(bonus, boundary, reason)
    states, changes, issues = project_resource_intervals(trace, execution_limit)
    output['states'].extend(states)
    output['changes'].extend(changes)
    output['issues'].extend(issues)
    if output["issues"]:
        output["status"] = "partial"
    return output
