"""Offline consistency proof for source-bound automatic energy admissions."""
from __future__ import annotations

import copy
from decimal import Decimal

from genshin_engine import automatic_energy as ae

from .files import fingerprint, input_view
from .verification_scope import verification_scope

MESSAGE = "自动扣能来源、动作接纳或历史审计与回执不一致，请回App重新计算。"


def _active(records):
    if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
        raise ValueError(MESSAGE)
    return [r for r in records if r.get("energy_delivery", {}).get("mode") == "action_start"]


def _audits(check):
    return [r for r in check.get("event_log", []) if r.get("delivery") == "automatic_action_start"]


def _has_execution(result):
    check = result.get("rotation_check") or {}
    return bool("automatic_energy_execution" in result or check.get("automatic_energy_delivery") or _audits(check)
                or any(ae.automatic(a) for a in (check.get("resolved_rotation_model") or {}).get("abilities", []))
                or any("energy_delivery" in r for r in check.get("event_log", [])))


def _admission_records(value):
    records = value.get("action_admission_compilations") or []
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ValueError(MESSAGE)
    return [row for row in records if row.get("status") == "ready"]


def _verify_legacy_links(records, check, admission_records):
    """Verify legacy source links when v2 owns the shared execution replay."""
    model = check["resolved_rotation_model"]
    abilities = {a["id"]: a for a in model["abilities"]}
    actions = {a["id"]: a for a in model["actions"]}
    legacy = {r["ability_id"] for r in _active(records)}
    admission = {r["ability_id"] for r in admission_records}
    if legacy & admission:
        raise ValueError(MESSAGE)
    automatic = {a["id"] for a in abilities.values() if ae.automatic(a)}
    if not legacy <= automatic or automatic != legacy | admission:
        raise ValueError(MESSAGE)
    for record in _active(records):
        aid = record["ability_id"]
        ability = abilities[aid]
        selected = sorted((a for a in actions.values() if a["ability_id"] == aid), key=lambda a: a["id"])
        expected = {"mode": "action_start", "contract": ae.CONTRACT, "phase": "action_admission",
                    "links": [{"action_id": a["id"], "ability_id": aid,
                               "receipt_id": ae.receipt_id(a["id"])} for a in selected]}
        if (not selected or record["energy_delivery"] != expected
                or ability["character"] != record["actor"]
                or ability["action_type_id"] != "action.elemental_burst"
                or ability["energy"] != record["compiled_values"]["energy"]
                or any(ability["cooldown"][key] != value
                       for key, value in record["compiled_values"]["cooldown"].items())
                or any(event["type"] == "energy_spend" and event.get("action_id") == action["id"]
                       for action in selected for event in model["events"])):
            raise ValueError(MESSAGE)


def _ticks(value):
    return int(Decimal(str(value)) * 1_000_000)


def _proof(records, check, completion=None):
    proof = {"contract": ae.CONTRACT,
            "links_fingerprint": fingerprint([{"ability_id": r["ability_id"], "delivery": r["energy_delivery"]}
                                               for r in sorted(_active(records), key=lambda r: r["ability_id"])]),
            "resolved_rotation_model_fingerprint": fingerprint(check["resolved_rotation_model"]),
            "audit_fingerprint": fingerprint(_audits(check)),
            "summary_fingerprint": fingerprint(check["automatic_energy_delivery"])}
    if completion is not None:
        from .energy_completion_receipt import completion_proof
        proof["completion"] = completion_proof(completion)
    return proof


@verification_scope
def make_automatic_energy_receipt(plan, report, result, *, _session=None):
    records = report.get("action_resource_compilations") or []
    if not _active(records):
        return None
    from .energy_completion_receipt import FIELD, prepare_completion
    completion = prepare_completion(report, result, _session)
    proof = _proof(records, result["rotation_check"], completion)
    if completion is not None:
        result[FIELD] = completion
    return proof


def _verify_records(records):
    from genshin_db.runtime_action_resource import RESOURCE_FIELDS, _resource_projection

    if len(records) > 64 or len({r["ability_id"] for r in records}) != len(records):
        raise ValueError(MESSAGE)
    for r in _active(records):
        selection = {k: v for k, v in r["resolved_selection"].items() if k != "slot"}
        projection = {k: _resource_projection(r["identity_sources"]["skill"], k, v) for k, v in RESOURCE_FIELDS.items()}
        raw_fp = fingerprint({"projection_version": 1, "snapshot": r["snapshot"], "selection": selection,
            "identity_sources": r["identity_sources"], "projection": projection, "descriptions": r["descriptions"]})
        cost, charges = projection["base_energy_cost"]["value"], projection["base_charge_count"]["value"]
        expected_values = {"cooldown": {"duration": projection["base_cooldown_seconds"]["value"], "charges": charges},
                           "energy": {"required": cost, "amount": cost, "delivery": "action_start"}}
        if (r["status"] != "ready" or r["projection"] != projection or r["resource_fingerprint"] != raw_fp
                or r["compiler"] != "database-action-resource-v1" or r["gameplay_interpretation"] != "declared_not_verified"
                or r["identity_coverage"] != "explicit_sources_only" or r["selection"]["constellation"] != 0
                or r["purpose"] != "ordinary_fixed_burst_base_resources" or r["interpretation_status"] not in ("assumed", "confirmed")
                or not r["interpretation_basis"].strip() or "burst" not in r["resolved_selection"]["matching_slots"]
                or r["descriptions"]["status"] != "ready" or any(p["status"] != "ready" for p in projection.values())
                or charges != 1 or cost is None or cost <= 0 or r["compiled_values"] != expected_values
                or r["action_spend_links"] != []
                or r["compilation_fingerprint"] != fingerprint({k: v for k, v in r.items() if k != "compilation_fingerprint"})):
            raise ValueError(MESSAGE)


def _verify_check(records, check, *, result=None, receipt=None, _session=None):
    model = check["resolved_rotation_model"]
    abilities = {a["id"]: a for a in model["abilities"]}
    actions = {a["id"]: a for a in model["actions"]}
    actual_ids = {a["id"] for a in abilities.values() if ae.automatic(a)}
    if actual_ids != {r["ability_id"] for r in _active(records)}:
        raise ValueError(MESSAGE)
    planned = {}
    for record in _active(records):
        aid = record["ability_id"]
        ability = abilities[aid]
        selected = sorted((a for a in actions.values() if a["ability_id"] == aid), key=lambda a: a["id"])
        expected = {"mode": "action_start", "contract": ae.CONTRACT, "phase": "action_admission",
                    "links": [{"action_id": a["id"], "ability_id": aid, "receipt_id": ae.receipt_id(a["id"])} for a in selected]}
        if (not selected or record["energy_delivery"] != expected or ability["character"] != record["actor"]
                or ability["action_type_id"] != "action.elemental_burst"
                or ability["energy"] != record["compiled_values"]["energy"]
                or any(ability["cooldown"][k] != v for k, v in record["compiled_values"]["cooldown"].items())):
            raise ValueError(MESSAGE)
        for action in selected:
            if any(e["type"] == "energy_spend" and e.get("action_id") == action["id"] for e in model["events"]):
                raise ValueError(MESSAGE)
            mode = action["repeat"]["mode"]
            origins = (range(model.get("max_cycles", 3)) if mode == "each_cycle" else
                       [0] if mode == "once" else action["repeat"]["cycles"])
            for origin in origins:
                tick = _ticks(action["start"]) + origin * _ticks(model["period"])
                if tick < model.get("max_cycles", 3) * _ticks(model["period"]):
                    planned[(action["id"], origin)] = (tick, 0, action["order"])
    rows, seen, expected_audits = check["event_log"], set(), []
    for i, parent in enumerate(rows):
        if parent.get("type") != "action":
            continue
        action = actions.get(parent["id"])
        if action is None or action["ability_id"] not in actual_ids:
            if "energy_delivery" in parent:
                raise ValueError(MESSAGE)
            continue
        origin = parent["source_cycle"]
        key = (action["id"], origin)
        ability = abilities[action["ability_id"]]
        delivery = parent["energy_delivery"]
        if (key not in planned or key in seen or parent["run_id"] != f"{origin}:{action['id']}"
                or parent["action_id"] != action["id"] or parent["ability_id"] != action["ability_id"]
                or parent["character"] != ability["character"] or delivery["mode"] != "action_start"
                or delivery["receipt_id"] != ae.receipt_id(action["id"])
                or _ticks(parent["t"]) != planned[key][0] or parent["order"] != action["order"]):
            raise ValueError(MESSAGE)
        seen.add(key)
        if delivery["status"] == "committed":
            admission = parent["energy_admission"]
            before = admission["before"]
            ev = {"id": action["id"], "run_id": parent["run_id"], "origin": origin,
                  "time": _ticks(parent["t"])}
            expected = ae.audit_row(ev, ability, parent["cycle"], before)
            if (before + 1e-8 < ability["energy"]["required"] or admission != {
                    "before": before, "after": expected["after"], "amount": expected["amount"]}
                    or i + 1 >= len(rows) or rows[i + 1] != expected):
                raise ValueError(MESSAGE)
            expected_audits.append(expected)
        elif delivery["status"] == "not_committed":
            if parent["status"] not in ("failed", "pending") or "energy_admission" in parent or not delivery.get("reason"):
                raise ValueError(MESSAGE)
        elif delivery["status"] == "not_verified":
            if parent["status"] != "not_verified" or "energy_admission" in parent:
                raise ValueError(MESSAGE)
        else:
            raise ValueError(MESSAGE)
    if _audits(check) != expected_audits:
        raise ValueError(MESSAGE)
    stats = check["automatic_energy_delivery"]
    termination = stats.get("termination", {})
    from .energy_completion_receipt import needs_completion, verify_completion
    completed = None
    if needs_completion(check):
        if result is None or receipt is None or _session is None:
            raise ValueError(MESSAGE)
        completed = verify_completion(result, receipt, _session)
    if check["energy_check"].get("proven_cycle") is not None:
        end = check["energy_check"]["checked_cycles"] * _ticks(model["period"])
        expected_parents = {key for key, pos in planned.items() if pos[0] < end}
    elif completed is not None:
        end = completed * _ticks(model["period"])
        expected_parents = {key for key, pos in planned.items() if pos[0] < end}
    elif check["status"] == "pending" and termination.get("code") != "automatic_energy_receipt_limit":
        if termination.get("kind") != "pending":
            raise ValueError(MESSAGE)
        stopped = next((r for r in rows if r.get("run_id") == termination["parent_run_id"]), None)
        if (stopped is None or stopped["status"] != "pending" or stopped["t"] != termination["t"]
                or stopped["order"] != termination["order"]):
            raise ValueError(MESSAGE)
        cutoff = (_ticks(termination["t"]), termination["system_priority"], termination["order"])
        expected_parents = {key for key, pos in planned.items() if pos <= cutoff}
    else:
        expected_parents = set(planned)
    if seen != expected_parents:
        raise ValueError(MESSAGE)
    ledger = ae.Ledger([], {}, 0)
    ledger.planned = len(planned)
    for row in expected_audits:
        ledger.commit(ledger.reserve(row))
    rebuilt = {"event_log": rows}
    ledger.finish(rebuilt, stats.get("termination"))
    if stats != rebuilt["automatic_energy_delivery"]:
        raise ValueError(MESSAGE)
    if stats["unreported_actions"] and not (check["energy_check"].get("proven_cycle") is not None or completed is not None
                                            or check["status"] == "pending" and stats.get("termination", {}).get("kind") == "pending"):
        raise ValueError(MESSAGE)
    # Reconcile admission history with energy credits; audit rows never alter it.
    energy = {c["id"]: c["initial_energy"] for c in model["characters"]}
    for row in rows:
        admission = row.get("energy_admission")
        if admission:
            cid = row["character"]
            if abs(energy[cid] - admission["before"]) > 1e-8:
                raise ValueError(MESSAGE)
            energy[cid] = admission["after"]
        for credit in row.get("energy", []):
            cid = credit["character"]
            if abs(energy[cid] - credit["before"]) > 1e-8:
                raise ValueError(MESSAGE)
            energy[cid] = credit["after"]
        if (row.get("type") == "energy_spend" and not row.get("committed_at_action_start")
                and row.get("status") == "passed"):
            if abs(energy[row["character"]] - row["before"]) > 1e-8:
                raise ValueError(MESSAGE)
            energy[row["character"]] = row["after"]
    if any(abs(energy[c["id"]] - c["final_energy"]) > 1e-8 for c in check["characters"]):
        raise ValueError(MESSAGE)


@verification_scope
def validate_automatic_energy_result_receipt(result, receipt, *, _session=None):
    records = result.get("action_resource_compilations") or []
    admission_records = _admission_records(result)
    check = result.get("rotation_check") or {}
    proof = (receipt.get("action_resources") or {}).get("automatic_energy_delivery")
    if not (_active(records) or admission_records or proof or _has_execution(result)):
        return
    try:
        if admission_records:
            # The v2 proof binds the reviewed static source, marker, parent
            # action, complete automatic-energy ledger, and one same-engine
            # replay.  Do not ask the legacy source schema to impersonate it.
            from .action_admission_receipt import (
                validate_action_admission_result_receipt,
            )
            validate_action_admission_result_receipt(result, receipt, _session=_session)
            if not _active(records):
                if proof is not None:
                    raise ValueError(MESSAGE)
                return
        from .energy_completion_receipt import (
            FIELD,
            needs_completion,
            verify_completion,
        )
        if not needs_completion(check):
            verify_completion(result, receipt, _session)
        _verify_records(records)
        if admission_records:
            _verify_legacy_links(records, check, admission_records)
        else:
            _verify_check(records, check, result=result, receipt=receipt, _session=_session)
        if (not _active(records) or proof != _proof(records, check, result.get(FIELD))
                or fingerprint(result) != receipt.get("result_fingerprint")
                or fingerprint(records) != receipt.get("action_resources", {}).get("compilations_fingerprint")):
            raise ValueError(MESSAGE)
    except (KeyError, TypeError, AttributeError, IndexError, ArithmeticError, ae.AuditLimit) as exc:
        raise ValueError(MESSAGE) from exc


@verification_scope
def validate_automatic_energy_receipt(plan, result, report, receipt, *, _session=None):
    records = report.get("action_resource_compilations") or []
    admission_records = [row for row in report.get("action_admission_compilations", [])
                         if row.get("status") == "ready"]
    if not (any(b.get("energy_delivery") == "action_start" for b in plan.get("action_resource_sources", []))
            or admission_records or _active(records)
            or (receipt.get("action_resources") or {}).get("automatic_energy_delivery")
            or _has_execution(result)):
        return
    try:
        validate_automatic_energy_result_receipt(result, receipt, _session=_session)
        if admission_records:
            from .action_admission_receipt import validate_action_admission_receipt
            validate_action_admission_receipt(plan, result, report, receipt, _session=_session)
            if not _active(records):
                return
        if (records != result["action_resource_compilations"] or fingerprint(input_view(plan)) != receipt["input_fingerprint"]
                or {b["ability_id"] for b in plan["action_resource_sources"]} != {r["ability_id"] for r in records}):
            raise ValueError(MESSAGE)
        from .action_resources import (
            COMPILER,
            CONFIRMATIONS,
            _identity_evidence,
            validate_action_resource_bindings,
        )
        validate_action_resource_bindings(plan)
        native = plan["rotation_model"]
        abilities = {a["id"]: a for a in native["abilities"]}
        identities, _ = _identity_evidence(plan, abilities)
        if admission_records:
            compiled = copy.deepcopy(report["compiled_rotation_model"])
        else:
            from .energy_policy import reconstruct_energy_policy_rotation
            compiled = reconstruct_energy_policy_rotation(native, result, receipt)
        bindings = {b["ability_id"]: b for b in plan["action_resource_sources"]}
        for record in records:
            aid = record["ability_id"]
            b = bindings[aid]
            if admission_records:
                target = next(a for a in compiled["abilities"] if a["id"] == aid)
                original = abilities[aid]
                target["cooldown"] = copy.deepcopy(original["cooldown"])
                target["energy"] = copy.deepcopy(original["energy"])
            semantic_binding = {k: v for k, v in b.items() if k not in CONFIRMATIONS}
            if semantic_binding.get("energy_delivery") == "existing_events":
                semantic_binding.pop("energy_delivery")
            scope = {"compiler": COMPILER, "binding": semantic_binding, "ability": abilities[aid],
                     "identity_evidence": identities.get(record["actor"], []), "resolved_selection": record["resolved_selection"],
                     "action_spend_links": record["action_spend_links"],
                     "known_aliases": [{**a, "ability": abilities[a["ability_id"]]} for a in record["known_aliases"]]}
            if "energy_delivery" in record:
                scope["energy_delivery"] = record["energy_delivery"]
            confirmations = {"expected_snapshot_id": record["snapshot"]["snapshot_id"],
                             "expected_resource_fingerprint": record["resource_fingerprint"],
                             "expected_scope_fingerprint": fingerprint(scope)}
            if (record["required_confirmation"] != confirmations or record["scope_fingerprint"] != fingerprint(scope)
                    or any(b[k] != v for k, v in confirmations.items())
                    or (b.get("energy_delivery") == "action_start") != ("energy_delivery" in record)):
                raise ValueError(MESSAGE)
            target = next(a for a in compiled["abilities"] if a["id"] == aid)
            if (set(target["energy"]) or {"duration", "charges"} & target["cooldown"].keys()):
                raise ValueError(MESSAGE)
            for key, value in record["compiled_values"].items():
                target[key].update(copy.deepcopy(value))
            for link in record["action_spend_links"]:
                event = next(e for e in compiled["events"] if e["id"] == link["spend_id"])
                if {"required", "amount"} & event.keys():
                    raise ValueError(MESSAGE)
                event.update(record["compiled_values"]["energy"])
        if (compiled != report["compiled_rotation_model"]
                or fingerprint(compiled) != report["compiled_rotation_model_fingerprint"]
                or fingerprint(compiled) != receipt["action_resources"]["compiled_rotation_model_fingerprint"]):
            raise ValueError(MESSAGE)
        for panel in result["rotation_check"]["panel_bindings"]:
            next(c for c in compiled["characters"] if c["id"] == panel["character"])["energy_recharge"] = panel["value"]
        if compiled != result["rotation_check"]["resolved_rotation_model"]:
            raise ValueError(MESSAGE)
        from .energy_completion_receipt import verify_full_completion
        verify_full_completion(result, report, _session)
    except (KeyError, TypeError, AttributeError, IndexError, StopIteration) as exc:
        raise ValueError(MESSAGE) from exc
