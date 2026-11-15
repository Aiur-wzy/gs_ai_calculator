"""Offline consistency proofs for source-bound particle delivery (not signatures)."""
from __future__ import annotations

import copy
import json
import math

from .energy_generation_schema import COMPILER, LIMITS, PREFIX
from .energy_policy import POLICY
from .files import fingerprint, input_view

MESSAGE = "产球来源、有限触发规则、实际资源流水或回执不一致，请重新计算。"

# Complete v1 compilations issued before the 7.1.53 runtime-source refresh.
#
# A historical delivery package is intentionally verifiable without opening the
# current database.  Re-running its source through today's v1 *reference data*
# is not a valid verification strategy: the compiler contract is unchanged but
# the admitted upstream snapshot and recipe have advanced.  These exact record
# fingerprints are the reviewed compatibility boundary.  They do not authorize
# a caller-created projection; every byte of the old compilation must match,
# and the normal acceptance, delivery, ledger, arithmetic and result-receipt
# checks below still run.
_HISTORICAL_COMPILATION_FINGERPRINTS = frozenset({
    "05b00284707f072359b1f00431599af33f18435bbbf982d6459f9c91f0437567",
    "fcb97eaf38649058d7bc523006f71fbe23f3600618dc5336dec1da849ffc56e5",
})


def energy_generation_summary(result, records=None):
    records = records if records is not None else result.get("energy_generation_compilations", [])
    summary = dict.fromkeys(("planned_parent_hits", "reported_parent_hits", "eligible_parent_hits", "missed_parent_hits",
        "offfield_parent_hits", "unverified_parent_hits", "unreported_parent_hits", "spawned_batches", "spawned_particles",
        "credited_batches", "credited_particles", "in_flight_particles"), 0)
    rows = {r["run_id"]: r for r in (result.get("rotation_check") or {}).get("event_log", [])}
    for record in records:
        for link in record["parent_links"]:
            summary["planned_parent_hits"] += 1
            parent = rows.get("0:" + link["hit_id"])
            if parent is None:
                summary["unreported_parent_hits"] += 1
                continue
            summary["reported_parent_hits"] += 1
            if parent["status"] == "missed":
                summary["missed_parent_hits"] += 1
            elif parent["status"] != "passed":
                summary["unverified_parent_hits"] += 1
            elif parent.get("frontline") != record["actor"]:
                summary["offfield_parent_hits"] += 1
            else:
                summary["eligible_parent_hits"] += 1
            rid = record["rules"][0]["id"]
            spawn_id = link["hit_id"] + "~" + rid + ":particles:spawn"
            spawn, credit = rows.get("0:" + spawn_id), rows.get("0:" + spawn_id + "~credit")
            for kind, row in (("spawned", spawn), ("credited", credit)):
                if row is not None and row["status"] == "passed":
                    summary[kind + "_batches"] += 1
                    summary[kind + "_particles"] += row["count"]
    summary["in_flight_particles"] = summary["spawned_particles"] - summary["credited_particles"]
    return summary


def make_energy_generation_receipt(plan, report):
    return {"compiler": COMPILER, "input_sources_fingerprint": fingerprint(plan.get("energy_generation_sources", [])),
        "compilations_fingerprint": fingerprint(report.get("energy_generation_compilations", [])),
        "compiled_mechanics_model_fingerprint": fingerprint(report["mechanics_model"]),
        "execution_rotation_model_fingerprint": fingerprint(report["action_event_execution_rotation_model"]),
        "summary_fingerprint": fingerprint(report["energy_generation_summary"]),
        "resource_ledger_fingerprint": report["energy_generation_resource_ledger_fingerprint"]}


def energy_generation_resource_ledger_fingerprint(result):
    """Bind actual sampled ER and resource facts without replaying D20 effects."""
    check = result["rotation_check"]
    model = check["resolved_rotation_model"]
    return fingerprint({"events": [r for r in check["event_log"] if r.get("type") in
        ("energy_spawn", "energy_credit", "energy_direct", "energy_spend") or "energy_admission" in r or "energy" in r],
        "initial_characters": model["characters"], "final_characters": check["characters"],
        "energy_recharge_windows": model.get("energy_recharge_windows", [])})


def _number(value, *, nonnegative=True):
    if type(value) not in (int, float) or not math.isfinite(value) or nonnegative and value < 0:
        raise ValueError(MESSAGE)
    return value


def _equal_number(a, b):
    if not math.isclose(_number(a), _number(b), rel_tol=1e-12, abs_tol=1e-8):
        raise ValueError(MESSAGE)


def _energy_arithmetic(result):
    """Check recorded arithmetic/history, not a second battle event executor.

    The independent receipt fingerprint binds sampled dynamic ER. Recomputing
    that stat from base ER alone would incorrectly discard valid D20 effects.
    """
    check = result["rotation_check"]
    characters = check["resolved_rotation_model"]["characters"]
    chars = {c["id"]: c for c in characters}
    if len(chars) != len(characters) or len(chars) not in (1, 2, 3, 4):
        raise ValueError(MESSAGE)
    energy = {cid: _number(c["initial_energy"]) for cid, c in chars.items()}
    off_field = {1: 1, 2: .8, 3: .7, 4: .6}[len(chars)]
    for row in check["event_log"]:
        admission = row.get("energy_admission")
        if admission is not None:
            cid = row["character"]
            _equal_number(energy[cid], admission["before"])
            _equal_number(admission["after"], max(0, energy[cid] - _number(admission["amount"])))
            energy[cid] = admission["after"]
        passed_credit = row["type"] == "energy_credit" and row["status"] == "passed"
        amounts = row.get("energy", [])
        if not isinstance(amounts, list) or len({v["character"] for v in amounts}) != len(amounts):
            raise ValueError(MESSAGE)
        if passed_credit and {v["character"] for v in amounts} != chars.keys():
            raise ValueError(MESSAGE)
        for value in amounts:
            cid = value["character"]
            if cid not in chars or type(value["on_field"]) is not bool or value["on_field"] != (cid == row["frontline"]):
                raise ValueError(MESSAGE)
            before, raw = _number(value["before"]), _number(value["raw"])
            _equal_number(energy[cid], before)
            if passed_credit:
                er = _number(value["energy_recharge"])
                count = _number(row["count"])
                if er <= 0 or row["particle_type"] not in ("particle", "orb"):
                    raise ValueError(MESSAGE)
                base = 2 if row["element"] == "clear" else 3 if row["element"] == chars[cid]["element"] else 1
                if row["particle_type"] == "orb":
                    base *= 3
                _equal_number(raw, count * base * (1 if value["on_field"] else off_field) * er)
            elif (row["type"] == "energy_direct" and row["status"] == "passed"
                    and check["resolved_rotation_model"].get("direct_energy_policy")
                    == POLICY):
                # This is a separate base-amount rule, not particle arithmetic:
                # the receiver's actual pre-credit ER applies exactly once.
                # Preserve recorded dynamic ER instead of substituting base ER.
                er = _number(value["energy_recharge"])
                base = _number(value["base_amount"])
                if er <= 0 or value["settlement_contract"] != POLICY:
                    raise ValueError(MESSAGE)
                _equal_number(value["theoretical_amount"], base * er)
                _equal_number(raw, base * er)
            elif value["energy_recharge"] is not None:
                raise ValueError(MESSAGE)
            gain = min(raw, max(0, _number(chars[cid]["energy_capacity"]) - before))
            _equal_number(value["gain"], gain)
            _equal_number(value["overflow"], raw - gain)
            _equal_number(value["after"], before + gain)
            energy[cid] = value["after"]
        if row["type"] == "energy_spend" and row["status"] == "passed" and not row.get("committed_at_action_start"):
            cid = row["character"]
            _equal_number(row["before"], energy[cid])
            _equal_number(row["after"], max(0, energy[cid] - _number(row["amount"])))
            energy[cid] = row["after"]
    final = check["characters"]
    if len(final) != len(chars) or {c["id"] for c in final} != chars.keys():
        raise ValueError(MESSAGE)
    for char in final:
        _equal_number(char["final_energy"], energy[char["id"]])


def _residue(result):
    rows = list((result.get("rotation_check") or {}).get("event_log", [])) + result.get("mechanics_trace", [])
    return any(PREFIX in str(row.get("id", "")) or str(row.get("rule_id", "")).startswith(PREFIX) for row in rows)


def _record(record):
    from genshin_engine.runtime_energy_trigger_compiler import (
        compile_energy_trigger_source,
    )

    from .energy_generation_sources import _delivery
    if record.get("status") != "ready" or record.get("compiler") != COMPILER or not record.get("parent_links"):
        raise ValueError(MESSAGE)
    projected = compile_energy_trigger_source(record["source_bundle"], parent_semantics=record["parent_semantics"], scenario=record["scenario"])
    if projected != record["projection"] or projected["status"] != "ready":
        if fingerprint(record) not in _HISTORICAL_COMPILATION_FINGERPRINTS:
            raise ValueError(MESSAGE)
        projected = record["projection"]
        if projected.get("status") != "ready":
            raise ValueError(MESSAGE)
    if (record["source_bundle"]["parent_identity"] != record["parent_identity"]
            or record["source_bundle"]["source_query"]["selection"]["origin_reference_id"] != record["origin_reference_id"]):
        raise ValueError(MESSAGE)
    for key in ("source_fingerprint", "source_status", "trigger_status", "trigger_fingerprint", "trigger_certificate", "resource_projection", "semantic_dependency"):
        if record.get(key) != projected.get(key):
            raise ValueError(MESSAGE)
    acceptance = fingerprint({"compiler": COMPILER, "trigger_fingerprint": projected["trigger_fingerprint"],
        "calibration": record["calibration"], "migration": record["migration"]})
    if (record["accepted_fingerprint"] != acceptance or record["acceptance_fingerprint"] != acceptance
            or record["required_confirmation"] != {"accepted_fingerprint": acceptance}
            or record["scope"] != {"actor": record["actor"], "parent_identity": record["parent_identity"],
                "parent_links": record["parent_links"], "migration": record["migration"],
                "controller_key": projected["trigger_certificate"]["controller_key"],
                "isolation": projected["trigger_certificate"]["isolation"]}):
        raise ValueError(MESSAGE)
    stage = _delivery({"id": record["id"], "calibration": record["calibration"], "scenario": record["scenario"]},
        record["actor"], record["parent_links"], projected["resource_projection"], record["migration"])
    if any(record.get(k) != v for k, v in stage.items()) or record["execution_fingerprint"] != fingerprint({"scope": record["scope"], "delivery": stage}):
        raise ValueError(MESSAGE)


def _ledger(result, records):
    from .energy_generation_sources import _ticks
    entries = (result.get("rotation_check") or {}).get("event_log", [])
    rows = {r["run_id"]: r for r in entries}
    positions = {r["run_id"]: i for i, r in enumerate(entries)}
    first_failure = next((i for i, r in enumerate(entries) if r.get("status") == "failed"), len(entries))
    if len(rows) != len(entries):
        raise ValueError(MESSAGE)
    check = result.get("rotation_check") or {}
    horizon = _ticks(check["resolved_rotation_model"]["period"]) * check["energy_check"]["checked_cycles"]
    allowed = set()
    for record in records:
        projection, calibration = record["resource_projection"], record["calibration"]
        for link in record["parent_links"]:
            parent = rows.get("0:" + link["hit_id"])
            if parent is None and link["absolute_tick"] < horizon:
                raise ValueError(MESSAGE)
            sid = link["hit_id"] + "~" + record["rules"][0]["id"] + ":particles:spawn"
            allowed.update((sid, sid + "~credit"))
            spawn, credit = rows.get("0:" + sid), rows.get("0:" + sid + "~credit")
            eligible = parent is not None and parent["status"] == "passed" and parent.get("frontline") == record["actor"] and link["landed"]
            if parent is not None and (_ticks(parent["t"]) != link["absolute_tick"] or parent["order"] != link["order"]
                    or parent.get("source_cycle") != 0 or parent.get("action_id") != link["action_id"]):
                raise ValueError(MESSAGE)
            spawn_delay = _ticks(calibration["spawn_delay_seconds"])
            credit_delay = spawn_delay + _ticks(calibration["travel_seconds"])
            for kind, row, delay in (("energy_spawn", spawn, spawn_delay), ("energy_credit", credit, credit_delay)):
                if eligible and link["absolute_tick"] + delay < horizon and row is None:
                    raise ValueError(MESSAGE)
                if (eligible and row is not None and positions[row["run_id"]] < first_failure
                        and row["status"] != "passed"):
                    raise ValueError(MESSAGE)
                if row is None or row["status"] != "passed":
                    continue
                if (parent is None or parent["status"] != "passed" or parent.get("frontline") != record["actor"]
                        or not link["landed"] or row["type"] != kind or row.get("source_cycle") != 0
                        or row.get("action_id") != link["action_id"] or row.get("order") != link["order"]
                        or _ticks(row["t"]) != link["absolute_tick"] + delay
                        or any(row.get(k) != projection[k] for k in ("count", "element", "particle_type"))):
                    raise ValueError(MESSAGE)
                if kind == "energy_credit" and (spawn is None or spawn["status"] != "passed" or row["spawn_id"] != sid):
                    raise ValueError(MESSAGE)
    if any(PREFIX in row["id"] and (row["id"] not in allowed or row["run_id"] != "0:" + row["id"]
                                  or row.get("source_cycle") != 0) for row in entries):
        raise ValueError(MESSAGE)


def validate_energy_generation_result_receipt(result, receipt):
    records, stored = result.get("energy_generation_compilations") or [], receipt.get("energy_generation")
    if not (records or stored or result.get("energy_generation_summary") or _residue(result)):
        return
    try:
        if (not records or not isinstance(stored, dict) or stored.get("compiler") != COMPILER
                or stored.get("compilations_fingerprint") != fingerprint(records)
                or receipt.get("result_fingerprint") != fingerprint(result)
                or len(records) > LIMITS["max_sources"]
                or len(json.dumps(records, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")) > LIMITS["max_receipt_bytes"]
                or len({r["id"] for r in records}) != len(records)):
            raise ValueError(MESSAGE)
        parent = receipt["action_events"]
        if any(stored[key] != parent[key] for key in ("compiled_mechanics_model_fingerprint", "execution_rotation_model_fingerprint")):
            raise ValueError(MESSAGE)
        if (stored["resource_ledger_fingerprint"] != energy_generation_resource_ledger_fingerprint(result)
                or stored["execution_rotation_model_fingerprint"] != fingerprint(result["rotation_check"]["resolved_rotation_model"])):
            raise ValueError(MESSAGE)
        for record in records:
            _record(record)
            events = [r for r in result.get("action_event_compilations", []) if r.get("profile_id") == record["action_event_profile_id"]]
            if (len(events) != 1 or events[0].get("status") != "ready"
                    or events[0].get("profile_fingerprint") != record["parent_semantics"]["profile_fingerprint"]
                    or events[0].get("source_dependency_fingerprint") != record["parent_semantics"]["source_dependency_fingerprint"]
                    or sorted(events[0].get("generated", []), key=lambda r: r["hit_id"]) != sorted([
                        {k: v for k, v in link.items() if k not in ("absolute_tick", "source_cycle", "pattern_fingerprint", "actor")}
                        for link in record["parent_links"]], key=lambda r: r["hit_id"])):
                raise ValueError(MESSAGE)
        _ledger(result, records)
        _energy_arithmetic(result)
        summary = energy_generation_summary(result)
        if result.get("energy_generation_summary") != summary or stored.get("summary_fingerprint") != fingerprint(summary):
            raise ValueError(MESSAGE)
    except (KeyError, TypeError, AttributeError, IndexError, RecursionError, ArithmeticError) as exc:
        raise ValueError(MESSAGE) from exc


def validate_energy_generation_receipt(plan, result, report, receipt):
    records = report.get("energy_generation_compilations") or []
    model = report.get("mechanics_model") or {}
    active = (plan.get("energy_generation_sources") or records or result.get("energy_generation_compilations")
              or receipt.get("energy_generation") or any(r["id"].startswith(PREFIX) for r in model.get("rules", [])) or _residue(result))
    if not active:
        return
    try:
        validate_energy_generation_result_receipt(result, receipt)
        if (not records or records != result.get("energy_generation_compilations")
                or receipt.get("input_fingerprint") != fingerprint(input_view(plan))
                or receipt.get("energy_generation") != make_energy_generation_receipt(plan, report)):
            raise ValueError(MESSAGE)
        replay = copy.deepcopy(plan)
        replay["mechanics_model"] = copy.deepcopy(model)
        replay["rotation_model"] = copy.deepcopy(report["action_event_execution_rotation_model"])
        replay["mechanics_model"]["rules"] = [r for r in model["rules"] if not r["id"].startswith(PREFIX)]
        replay["mechanics_model"]["initial_state"]["rules"] = [r for r in model["initial_state"]["rules"] if not r["rule_id"].startswith(PREFIX)]
        replay["mechanics_model"]["resource_ownership"] = [r for r in model.get("resource_ownership", []) if not r["rule_id"].startswith(PREFIX)]
        originals = {r["id"]: r for r in records}
        problems = []
        from .energy_generation_sources import compile_energy_generation_sources
        rebuilt, rows, _ = compile_energy_generation_sources(replay, report["mechanics_input_template"], None,
            action_event_compilations=report.get("action_event_compilations", []),
            attack_metadata_compilations=report.get("attack_metadata_compilations", []),
            parameter_compilations=report.get("hit_parameter_compilations", []),
            missing=lambda *args: problems.append(args),
            source_provider=lambda b, i: originals[b["id"]]["source_bundle"],
            projection_provider=lambda b, i: (
                originals[b["id"]]["projection"]
                if fingerprint(originals[b["id"]])
                in _HISTORICAL_COMPILATION_FINGERPRINTS
                else None
            ))
        # Later compilers may append independent rules after particle delivery.
        # Restore only our replayed definitions to their original slots; retain
        # the exact relative order and content of every other rule. Never sort
        # execution arrays to conceal a semantic ordering difference.
        if rebuilt is not None:
            def restore_slots(actual, expected, key):
                own = {row[key]: row for row in actual if row[key].startswith(PREFIX)}
                remaining = iter(row for row in actual if not row[key].startswith(PREFIX))
                expected_owned = [row[key] for row in expected if row[key].startswith(PREFIX)]
                if (len(actual) != len(expected) or len(own) != len(expected_owned)
                        or len({row[key] for row in actual}) != len(actual) or set(own) != set(expected_owned)):
                    raise ValueError(MESSAGE)
                return [own[row[key]] if row[key].startswith(PREFIX) else next(remaining) for row in expected]
            rebuilt["rules"] = restore_slots(rebuilt["rules"], model["rules"], "id")
            rebuilt["initial_state"]["rules"] = restore_slots(
                rebuilt["initial_state"]["rules"], model["initial_state"]["rules"], "rule_id")
        if problems or rows != records or rebuilt != model:
            raise ValueError(MESSAGE)
    except (KeyError, TypeError, AttributeError, IndexError, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc
