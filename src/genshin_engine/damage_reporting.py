"""Bounded proof of a complete, explicitly missed damage input window.

Candidate assembly is a pure projection of existing descriptors. It does not
run actions, elemental state, energy, effects, or a second event simulator.
"""
from __future__ import annotations

import json
from decimal import Decimal

CONTRACT = "verified-empty-damage-v1"
MAX_DAMAGE_REPORTING_CONTACTS = 40000
MAX_DAMAGE_REPORTING_BYTES = 8 * 1024 * 1024
TICKS = 1_000_000
_FIELDS = {"event_id", "run_id", "action_id", "tick", "order", "report", "landed"}


class DamageReportingLimit(ValueError):
    def __init__(self, *, attempted_bytes=None, contacts=None):
        self.issue = {"code": "damage_reporting_limit", "severity": "pending", "path": "damage_reporting",
                      "message": "零伤害覆盖证明超过有界预算，未发布完整零伤害结果。",
                      "max_contacts": MAX_DAMAGE_REPORTING_CONTACTS, "max_bytes": MAX_DAMAGE_REPORTING_BYTES}
        if attempted_bytes is not None:
            self.issue["attempted_bytes"] = attempted_bytes
        if contacts is not None:
            self.issue["contacts"] = contacts
        super().__init__(self.issue["message"])


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf8")


def _ticks(value):
    if type(value) not in (int, float):
        raise ValueError("Coverage time must be a finite number, not a boolean")
    scaled = Decimal(str(value)) * TICKS
    if not scaled.is_finite() or scaled != scaled.to_integral_value():
        raise ValueError("Coverage time must have exact microsecond precision")
    return int(scaled)


def _header(period_ticks, count):
    return {"contract": CONTRACT, "status": "verified_zero", "reason": "all_declared_contacts_missed",
            "scope": {"origin": 0, "start_tick": 0, "end_tick": period_ticks, "tick_unit": "microsecond"},
            "planned_contacts": count, "missed_contacts": count, "reported_damage_events": 0, "contacts": []}


def _count_budget(count):
    if count > MAX_DAMAGE_REPORTING_CONTACTS:
        raise DamageReportingLimit(contacts=count)


def _append_budget(used, row, *, previous):
    attempted = used + len(_canonical({**row, "outcome": "missed"})) + int(previous)
    if attempted > MAX_DAMAGE_REPORTING_BYTES:
        raise DamageReportingLimit(attempted_bytes=attempted)
    return attempted


def _in_window(event, period_ticks):
    return (event["kind"] == "mechanics_hit" and type(event["origin"]) is int and event["origin"] == 0
            and type(event["time"]) is int and 0 <= event["time"] < period_ticks)


def candidate_sources_known(events, mechanics_model, period_ticks):
    """A miss cannot turn explicitly unknown source/contact evidence into proof."""
    model = mechanics_model.get("elemental_model")
    if not model or mechanics_model.get("application_events"):
        return False
    target = model["target"]
    if target.get("knowledge") != "known" or target.get("status") not in ("assumed", "confirmed") or not target.get("source", "").strip():
        return False
    profiles = {p["id"]: p for p in model["profiles"]}
    contacts = {p["id"]: p for p in model.get("contact_profiles", [])}
    for event in events:
        if not _in_window(event, period_ticks):
            continue
        descriptor = event["data"]["descriptor"]
        application = descriptor.get("application") or {}
        profile = profiles.get(application.get("profile_id")) or {}
        if (profile.get("status") not in ("assumed", "confirmed") or not profile.get("source", "").strip()
                or profile.get("application") not in ("none", "apply")
                or (profile.get("application") == "apply" and profile.get("icd", {}).get("kind") == "unknown")):
            return False
        if model.get("ruleset") == "gauge-freeze-reference-v1":
            contact = descriptor.get("contact") or {}
            if contact.get("kind") == "profile":
                evidence = contacts.get(contact.get("profile_id")) or {}
                safe = evidence.get("freeze_interaction") == "none"
            else:
                evidence = contact
                safe = contact.get("kind") == "none"
            if not safe or evidence.get("status") not in ("assumed", "confirmed") or not evidence.get("source", "").strip():
                return False
    return True


def collect_damage_candidates(events, period_ticks):
    """Read already expanded queue identities; budget before appending any row."""
    if type(period_ticks) is not int or period_ticks <= 0:
        raise ValueError("Coverage window must have positive integer microseconds")
    count = sum(_in_window(event, period_ticks) for event in events)
    _count_budget(count)
    used = len(_canonical(_header(period_ticks, count)))
    rows = []
    for event in events:
        if not _in_window(event, period_ticks):
            continue
        descriptor = event["data"]["descriptor"]
        row = {"event_id": event["id"], "run_id": event["run_id"], "action_id": event["data"]["action_id"],
               "tick": event["time"], "order": event["order"], "report": descriptor.get("report", True),
               "landed": (descriptor.get("application") or {}).get("landed")}
        used = _append_budget(used, row, previous=bool(rows))
        rows.append(row)
    rows.sort(key=lambda row: (row["tick"], row["order"]))
    return rows


def assemble_damage_candidates(template, rotation_model, mechanics_model):
    """Pure offline origin-zero projection, never execute the mechanics queue.

    Inputs are the execution template/model, not an unresolved native plan.
    Explicit unknown source/contact evidence is rejected even on a missed hit.
    """
    from .mechanics_projection import project_mechanics_inputs
    from .mechanics_validation import validate_mechanics_model
    from .rotation_port import repeat_origins as _origins
    from .rotation_port import validate_rotation_model
    from .template import validate_template

    validate_template(template)
    if validate_mechanics_model(mechanics_model)["issues"] or validate_rotation_model(rotation_model)["issues"]:
        raise ValueError("Coverage requires valid execution models")
    issues = []
    _, _, runtime = project_mechanics_inputs(
        template, rotation_model, mechanics_model, issues
    )
    if issues:
        raise ValueError("Coverage source projection is incomplete")
    period = _ticks(rotation_model["period"])
    events = [{**event, "origin": 0, "run_id": "0:" + event["id"]} for event in runtime
              if 0 in _origins(event["data"]["repeat"], rotation_model.get("max_cycles", 3))]
    if not candidate_sources_known(events, mechanics_model, period):
        raise ValueError("Coverage source/contact evidence is not fully known")
    return collect_damage_candidates(events, period)


def classify_verified_empty_damage(candidates, rotation_check, *, period_ticks, mechanics_model,
                                   issues=(), reported_damage_events=0):
    """Return immutable coverage facts only for complete all-missed execution.

    Result-only receipt checks may pass a model with only application_events;
    complete receipts additionally reconstruct candidates from the actual model.
    No caller objects are changed. Malformed evidence returns None; budget errors
    raise DamageReportingLimit so execution cannot turn truncation into success.
    """
    if (type(rotation_check) is not dict or type(mechanics_model) is not dict
            or type(period_ticks) is not int or period_ticks <= 0 or type(reported_damage_events) is not int
            or reported_damage_events != 0 or issues or mechanics_model.get("application_events")
            or rotation_check.get("status") not in ("passed", "unproven")
            or rotation_check.get("issues") or type(candidates) is not list or not candidates):
        return None
    energy = rotation_check.get("energy_check") or {}
    if (type(energy) is not dict or type(energy.get("completed_cycles")) is not int or energy["completed_cycles"] < 1
            or type(energy.get("checked_cycles")) is not int or energy["checked_cycles"] != energy["completed_cycles"]
            or energy.get("status") not in ("passed", "unproven")
            or energy.get("cycle_status") not in ("passed", "unproven") or energy.get("opening_status") != "passed"
            or type(rotation_check.get("action_check")) is not dict
            or rotation_check.get("action_check", {}).get("status") != "passed"):
        return None
    cycles = rotation_check.get("cycles")
    if (type(cycles) is not list or len(cycles) != energy["completed_cycles"]
            or any(type(row) is not dict or type(row.get("cycle")) is not int or row["cycle"] != i
                   or row.get("status") != "passed" or row.get("incomplete")
                   for i, row in enumerate(cycles))):
        return None
    _count_budget(len(candidates))
    header = _header(period_ticks, len(candidates))
    used = len(_canonical(header))
    by_run, ordered = {}, []
    for row in candidates:
        if (type(row) is not dict or row.keys() != _FIELDS
                or any(type(row[key]) is not str or not row[key] for key in ("event_id", "run_id", "action_id"))
                or type(row["tick"]) is not int or not 0 <= row["tick"] < period_ticks
                or type(row["order"]) is not int or row["report"] is not True or row["landed"] is not False
                or row["run_id"] != "0:" + row["event_id"] or row["run_id"] in by_run):
            return None
        used = _append_budget(used, row, previous=bool(ordered))
        by_run[row["run_id"]] = row
        ordered.append({**row, "outcome": "missed"})
    actual = set()
    log = rotation_check.get("event_log")
    if type(log) is not list:
        return None
    for row in log:
        if type(row) is not dict or row.get("status") in ("failed", "pending", "not_verified"):
            return None
        if row.get("type") == "elemental_application":
            return None
        if row.get("type") != "mechanics_hit":
            continue
        try:
            tick = _ticks(row["t"])
        except (KeyError, ValueError, ArithmeticError):
            return None
        if type(row.get("source_cycle")) is not int:
            return None
        if row["source_cycle"] != 0 or not 0 <= tick < period_ticks:
            continue
        run_id = row.get("run_id")
        candidate = by_run.get(run_id) if type(run_id) is str else None
        if (candidate is None or run_id in actual or row.get("status") != "missed"
                or type(row.get("order")) is not int or row["order"] != candidate["order"]
                or row.get("id") != candidate["event_id"] or row.get("action_id") != candidate["action_id"]
                or type(row.get("cycle")) is not int or row["cycle"] != 0 or tick != candidate["tick"]):
            return None
        actual.add(run_id)
    if actual != by_run.keys() or ordered != sorted(ordered, key=lambda row: (row["tick"], row["order"])):
        return None
    header["contacts"] = ordered
    return header
