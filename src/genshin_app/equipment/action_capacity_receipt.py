"""Typed historical capacity evidence and one bounded actual execution replay.

Offline verification proves saved material consistency; it does not inspect current
DB heads and never grants a current execution qualification.
"""
from __future__ import annotations

import copy

from genshin_runtime.action_capacity_model import (
    MAX_FACTS,
    PROFILE_CONTRACT,
    evidence_for,
    same,
    selection_matches,
    verify_material,
)
from genshin_runtime.action_root_semantics import bounded
from genshin_runtime.mechanism_semantics import (
    record_payload_fingerprint,
    record_review_fingerprint,
    validate_record,
)

from .action_capacities import (
    COMPILER,
    MARKER,
    pool_material,
    projection,
    verify_final_pools,
)
from .action_capacity_inputs import actual_selection
from .files import fingerprint, input_view
from .snapshot_capture_receipt import EXECUTION_KEYS, _ledger
from .verification_scope import verification_scope

CONTRACT = "action-capacity-receipt-v1"
FIELD = "action_capacity_execution"
MESSAGE = "受审最大次数、完整冷却池、实际消费或离线材料与回执不一致，请回App重新计算。"


def _equal(a, b):
    if not same(a, b):
        raise ValueError(MESSAGE)


def _head(record, receipt, snapshot):
    validate_record(record, "fact")
    if record["review"]["status"] != "approved" or type(receipt) is not dict:
        raise ValueError(MESSAGE)
    expected = {"version_line": snapshot["version_line"], "record_kind": "fact", "record_id": record["id"],
                "revision": record["revision"], "head_updated_at": receipt.get("head_updated_at"),
                "payload_fingerprint": record_payload_fingerprint(record, "fact"),
                "review_fingerprint": record_review_fingerprint(record, "fact")}
    if type(expected["head_updated_at"]) is not str or not 1 <= len(expected["head_updated_at"]) <= 80:
        raise ValueError(MESSAGE)
    _equal(receipt, expected)
    _equal(record["applicability"], {"version_line": snapshot["version_line"],
        "upstream_commit": snapshot["upstream_commit"], "normalization_fingerprint": fingerprint(snapshot["normalization"])})


def verify_saved_profile(profile):
    """Rebuild every current-at-save dependency/disposition, not copied ready flags."""
    from genshin_db.runtime_action_sources import project_delta_material
    bounded(profile)
    if profile["contract"] != PROFILE_CONTRACT or profile["status"] != "ready" or profile["reason"] is not None:
        raise ValueError(MESSAGE)
    _equal(sorted(profile), sorted(["contract", "status", "reason", "capacity", "selection", "context", "snapshot",
        "model_ref", "model_receipt", "model_record", "model_material", "delta_materials", "other_model_materials",
        "competitor_inventory", "native_dispatch", "execution_authority", "capacity_competitor_inventory_fingerprint", "material_fingerprint"]))
    model, material = profile["model_record"], profile["model_material"]
    claim, selected, snapshot = model["payload"]["claim"], profile["selection"], profile["snapshot"]
    _head(model, profile["model_receipt"], snapshot)
    _equal(model["payload"]["kind"], "action_capacity_model")
    _equal(profile["model_ref"], {"id": model["id"], "revision": model["revision"]})
    _equal(model["evidence"], evidence_for(claim))
    _equal(claim["snapshot"], snapshot)
    verify_material(claim, material)
    if not selection_matches(claim, selected, profile["context"]):
        raise ValueError(MESSAGE)
    inventory = profile["competitor_inventory"]
    entries = inventory["entries"]
    if type(entries) is not list or not 1 <= len(entries) <= MAX_FACTS:
        raise ValueError(MESSAGE)
    identities = [(x["id"], x["revision"]) for x in entries]
    if len({x[0] for x in identities}) != len(entries) or identities != sorted(identities):
        raise ValueError(MESSAGE)
    _equal(inventory, {"contract": "current-capacity-competitors-v1", "complete": True,
        "limit": MAX_FACTS, "selection": selected, "version_line": snapshot["version_line"], "entries": entries})
    _equal(profile["capacity_competitor_inventory_fingerprint"], fingerprint(inventory))
    deltas = {x["record"]["id"]: x for x in profile["delta_materials"]}
    others = {x["record"]["id"]: x for x in profile["other_model_materials"]}
    if len(deltas) != len(profile["delta_materials"]) or len(others) != len(profile["other_model_materials"]):
        raise ValueError(MESSAGE)
    required = {x["id"]: x["revision"] for x in claim["delta_refs"]}
    if not {model["id"], *required} <= {x[0] for x in identities}:
        raise ValueError(MESSAGE)
    delta_ids, other_ids, seen_ops, amounts = set(), set(), set(), []
    for entry in entries:
        record = entry["record"]
        _head(record, entry["receipt"], snapshot)
        _equal(record["subject"], model["subject"])
        _equal([entry["id"], entry["revision"]], [record["id"], record["revision"]])
        disposition, projected = "selected_model", None
        if entry["id"] == model["id"]:
            _equal(record, model)
            _equal(entry["receipt"], profile["model_receipt"])
        elif record["payload"]["kind"] == "action_capacity_delta":
            delta_ids.add(record["id"])
            row = deltas[record["id"]]
            _equal(sorted(row), sorted(["record", "inventory", "row", "declaration"]))
            _equal(row["record"], record)
            _equal(row["inventory"]["snapshot"], snapshot)
            projected = project_delta_material(row["inventory"], row["row"], row["declaration"], record, selected["constellation"])
            sc = record["payload"]["claim"]["source_claim"]
            target_same = sc["skill_depot_id"] == selected["skill_depot_id"] and sc["target_skill_id"] == selected["skill_id"]
            if record["id"] in required:
                if record["revision"] != required[record["id"]] or not target_same:
                    raise ValueError(MESSAGE)
                op = (sc["source_path"], sc["operation_pointer"], record["payload"]["claim"]["activation"]["rank"])
                if op in seen_ops:
                    raise ValueError(MESSAGE)
                seen_ops.add(op)
                amounts.append(projected["amount"])
                disposition = "model_dependency_active" if projected["amount"] else "model_dependency_inactive"
            elif not target_same:
                disposition = "different_target_verified"
            else:
                raise ValueError(MESSAGE)
        elif record["payload"]["kind"] == "action_capacity_model":
            other_ids.add(record["id"])
            row = others[record["id"]]
            _equal(sorted(row), ["material", "record"])
            _equal(row["record"], record)
            other = record["payload"]["claim"]
            _equal(other["snapshot"], snapshot)
            verify_material(other, row["material"])
            if other["target"]["skill_id"] == selected["skill_id"] and other["target"]["skill_depot_id"] == selected["skill_depot_id"]:
                raise ValueError(MESSAGE)
            disposition = "different_target_verified"
        else:
            raise ValueError(MESSAGE)
        _equal(entry, {"id": record["id"], "revision": record["revision"], "receipt": entry["receipt"],
            "status": "ready", "reason": None, "record": record, "projection": projected, "disposition": disposition})
    if delta_ids != set(deltas) or other_ids != set(others):
        raise ValueError(MESSAGE)
    maximum = claim["base"]["value"] + sum(amounts)
    reviewed = next(x["capacity"] for x in claim["reviewed_cases"] if x["constellation"] == selected["constellation"])
    if type(maximum) is not int or maximum != reviewed:
        raise ValueError(MESSAGE)
    _equal(profile["capacity"], maximum)
    _equal(profile["native_dispatch"], "unproven")
    _equal(profile["execution_authority"], False)
    _equal(profile["material_fingerprint"], fingerprint({k: v for k, v in profile.items() if k != "material_fingerprint"}))


def _binding(record):
    p = record["projection"]
    return {"id": record["id"], "actor": p["pool"]["actor"],
        "ability_ids": sorted(x["id"] for x in p["pool"]["members"]),
        "version_line": p["profile"]["snapshot"]["version_line"], "model_ref": p["profile"]["model_ref"],
        "target": p["pool"]["target"], "accepted_fingerprint": record["source_fingerprint"]}


def _verify_records(records, rotation):
    if type(records) is not list or not 1 <= len(records) <= 64 or len({x["id"] for x in records}) != len(records):
        raise ValueError(MESSAGE)
    owned = set()
    for record in records:
        p = record["projection"]
        verify_saved_profile(p["profile"])
        _equal(p, projection(p["profile"], p["actual"], p["pool"], p["weapon_stats"]))
        actual = p["actual"]
        _equal(actual["fingerprint"], fingerprint({k: v for k, v in actual.items() if k != "fingerprint"}))
        _equal(actual["selection"], p["profile"]["selection"])
        _equal(actual["context"], p["profile"]["context"])
        stats, selected = p["weapon_stats"], actual["selection"]
        _equal([stats["weapon_id"], stats["level"], stats["ascension"], stats["version_line"]],
               [selected["weapon"]["id"], selected["weapon"]["level"], actual["weapon_ascension"], p["profile"]["snapshot"]["version_line"]])
        binding = _binding(record)
        _equal(p["pool"]["actor"], actual["actor"])
        _equal(binding["target"], {k: selected[k] for k in ("character_id", "skill_depot_id", "skill_id", "variant")})
        from .action_capacity_schema import SLOTS
        presence = actual["presence"]
        if type(presence) is not dict or set(presence) != set(SLOTS) or not all(
                v == "empty" or type(v) is dict and v.get("state") == "empty" for v in presence.values()):
            raise ValueError(MESSAGE)
        native_rotation = copy.deepcopy(rotation)
        for ability in native_rotation["abilities"]:
            if ability["id"] in binding["ability_ids"]:
                for key in ("charges", "capacity_source"):
                    ability["cooldown"].pop(key, None)
        _equal(pool_material({"rotation_model": native_rotation}, binding), p["pool"])
        if owned.intersection(binding["ability_ids"]):
            raise ValueError(MESSAGE)
        owned.update(binding["ability_ids"])
        source_fp = fingerprint(p)
        values = {"charges": p["capacity"], "capacity_source": {"contract": MARKER, "binding_id": record["id"], "source_fingerprint": source_fp}}
        expected = {"id": record["id"], "compiler": COMPILER, "status": "ready", "issues": [], "projection": p,
                    "required_confirmation": {"accepted_fingerprint": source_fp}, "source_fingerprint": source_fp, "compiled_values": values}
        expected["compilation_fingerprint"] = fingerprint(expected)
        _equal(record, expected)
    markers = {a["id"] for a in rotation["abilities"] if "capacity_source" in (a.get("cooldown") or {})}
    if markers != owned:
        raise ValueError(MESSAGE)
    verify_final_pools({}, rotation, records)


def _has(result, receipt):
    check = result.get("rotation_check") or {}
    return bool(result.get("action_capacity_compilations") or FIELD in result or "action_capacities" in receipt
        or "action_capacity_consumption" in check or any("capacity_admission" in x for x in check.get("event_log", []))
        or any("capacity_source" in (x.get("cooldown") or {}) for x in (check.get("resolved_rotation_model") or {}).get("abilities", [])))


def _proof(result):
    return {"contract": CONTRACT, "approval_scope": "historical_material_only_recalculation_requires_fresh_database",
        "compilations_fingerprint": fingerprint(result["action_capacity_compilations"]),
        "execution_fingerprint": fingerprint(result[FIELD]), "ledger_fingerprint": fingerprint(_ledger(result))}


def make_action_capacity_receipt(plan, report, result, *, _session):
    records = report.get("action_capacity_compilations")
    if not records:
        return None
    execution = {"template": report["mechanics_input_template"],
        "rotation_model": result["rotation_check"]["resolved_rotation_model"],
        "mechanics_model": report["mechanics_model"], "matches": report["mechanics_matches"]}
    _session.scan_receipt_materials(inputs=(execution, records))
    result["action_capacity_compilations"] = copy.deepcopy(records)
    result[FIELD] = copy.deepcopy(execution)
    return _proof(result)


@verification_scope
def validate_action_capacity_result_receipt(result, receipt, *, _session=None):
    if not _has(result, receipt):
        return
    try:
        records, execution, check = result["action_capacity_compilations"], result[FIELD], result["rotation_check"]
        if type(execution) is not dict or set(execution) != EXECUTION_KEYS or type(execution["matches"]) is not list:
            raise ValueError(MESSAGE)
        _session.scan_receipt_materials(inputs=(records, execution), outputs=(check,))
        _verify_records(records, execution["rotation_model"])
        _equal(check["resolved_rotation_model"], execution["rotation_model"])
        _equal(check["input_fingerprint"], fingerprint(execution))
        _equal(receipt["action_capacities"], _proof(result))
        _equal(receipt["result_fingerprint"], fingerprint(result))
        replay = _session.replay(execution)
        expected = replay.get("result")
        if expected is None or replay.get("resolved_template") is None:
            raise ValueError(MESSAGE)
        if not expected.get("mechanics", {}).get("complete_first_cycle", False):
            expected["summary"]["dps"] = None
        expected.update(rotation_check=replay["rotation_check"], mechanics_trace=replay["mechanics_trace"],
                        sampling_trace=replay.get("sampling_trace", []), mechanics_status=replay["status"], mechanics_issues=replay.get("issues", []))
        from .replay_numeric_projection import replay_numeric_ledgers
        _equal(*replay_numeric_ledgers(_ledger(expected), _ledger(result), result, receipt))
        _equal(fingerprint(replay["resolved_template"]), receipt["output_fingerprint"])
    except (KeyError, TypeError, AttributeError, IndexError, ArithmeticError, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc


@verification_scope
def validate_action_capacity_receipt(plan, result, report, receipt, *, _session=None):
    if not (plan.get("action_capacity_sources") or report.get("action_capacity_compilations") or _has(result, receipt)):
        return
    try:
        from genshin_app.rotation_check import resolve_rotation_model

        from .action_capacity_schema import validate_capacity_bindings
        validate_capacity_bindings(plan)
        validate_action_capacity_result_receipt(result, receipt, _session=_session)
        records = result["action_capacity_compilations"]
        _equal(records, report["action_capacity_compilations"])
        _equal(records, plan["resolution"]["action_capacity_compilations"])
        bindings = plan["action_capacity_sources"]
        if len(bindings) != len(records):
            raise ValueError(MESSAGE)
        for binding, record in zip(bindings, records, strict=True):
            reconstructed = _binding(record)
            _equal({**binding, "ability_ids": sorted(binding["ability_ids"])}, reconstructed)
            selected, context, actual = actual_selection(plan, binding)
            _equal(actual, record["projection"]["actual"])
            _equal(selected, record["projection"]["profile"]["selection"])
            _equal(context, record["projection"]["profile"]["context"])
            _equal(pool_material(plan, binding), record["projection"]["pool"])
        verify_final_pools(plan, report["compiled_rotation_model"], records)
        _equal(report["compiled_rotation_model_fingerprint"], fingerprint(report["compiled_rotation_model"]))
        rotation, panels = resolve_rotation_model(report["mechanics_input_template"], report["compiled_rotation_model"])
        _equal(result[FIELD], {"template": report["mechanics_input_template"], "rotation_model": rotation,
                              "mechanics_model": report["mechanics_model"], "matches": report["mechanics_matches"]})
        _equal(panels, result["rotation_check"]["panel_bindings"])
        _equal(receipt["input_fingerprint"], fingerprint(input_view(plan)))
        _equal(input_view(plan), input_view(report["plan"]))
    except (KeyError, TypeError, AttributeError, IndexError, ArithmeticError, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc
