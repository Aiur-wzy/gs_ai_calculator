"""Bounded offline replay of explicit capture inputs using the same engine.

This proves saved-input consistency, not game timing or cryptographic authenticity.
make_snapshot_capture_receipt attaches its private execution inputs to result before
the caller computes the final result fingerprint. No database access is performed.
"""
from __future__ import annotations

import copy
import json

from genshin_app.equipment.verification_scope import verification_scope

from .files import fingerprint, input_view

CONTRACT = "action-snapshot-capture-receipt-v1"
# A 121.61 s / seven-round real input needs 13,030,748 bytes before
# the proof envelope, mostly mandatory elemental/mechanics ledgers. Preserve
# all evidence and replay checks; leave room for conditional buff audits.
MAX_EVIDENCE_BYTES = 32 * 1024 * 1024
MESSAGE = "动作从属捕获的声明、真实父动作、取样引用或伤害流水不一致，请重新计算。"
EXECUTION_KEYS = {"template", "rotation_model", "mechanics_model", "matches"}
CHECK_APP_KEYS = {"input_fingerprint", "panel_bindings", "resolved_rotation_model", "damage_interpretation"}
TRACE_TYPES = {"snapshot_capture", "snapshot_capture_reference"}


def _same(a, b):
    if fingerprint(a) != fingerprint(b):
        raise ValueError(MESSAGE)


def _bounded(value):
    size = 0
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    for chunk in encoder.iterencode(value):
        size += len(chunk.encode("utf8"))
        if size > MAX_EVIDENCE_BYTES:
            from .schema import PlanError
            raise PlanError(
                f"捕获的完整离线证明超过字节预算 {MAX_EVIDENCE_BYTES}；"
                "未截断交付。请减少重复的输入记录或报告容量不足。",
                "response_budget_exceeded", {
                    "phase": "snapshot_capture_evidence",
                    "actual_bytes_at_least": size,
                    "max_bytes": MAX_EVIDENCE_BYTES,
                    "truncated": False,
                })


def _declared(model):
    return isinstance(model, dict) and bool(model.get("snapshot_captures"))


def _residue(result):
    if "snapshot_captures" in result or "snapshot_capture_execution" in result:
        return True
    if any(row.get("type") in TRACE_TYPES for row in result.get("mechanics_trace", [])):
        return True
    if any(row.get("type") == "mechanics_snapshot_capture" or "snapshot_capture" in row
           for row in (result.get("rotation_check") or {}).get("event_log", [])):
        return True
    return (_declared((result.get("mechanism_execution") or {}).get("model"))
            or _declared(((result.get("mechanism_replay_context") or {}).get("plan") or {}).get("mechanics_model")))


def _ledger(result):
    # Whole actual queues prevent a missing capture/consumer being disguised as
    # an unreported later window. App-only explanations are checked separately.
    check = result.get("rotation_check") or {}
    return {"rotation_check": {k: v for k, v in check.items() if k not in CHECK_APP_KEYS},
            "mechanics_trace": result.get("mechanics_trace"),
            "sampling_trace": result.get("sampling_trace"),
            "hits": result.get("hits"), "summary": result.get("summary"),
            "mechanics": result.get("mechanics"), "mechanics_status": result.get("mechanics_status"),
            "mechanics_issues": result.get("mechanics_issues")}


def _proof(result, input_fingerprint, output_fingerprint):
    return {"contract": CONTRACT, "input_fingerprint": input_fingerprint,
            "output_fingerprint": output_fingerprint,
            "execution_fingerprint": fingerprint(result["snapshot_capture_execution"]),
            "captures_fingerprint": fingerprint(result["snapshot_captures"]),
            "ledger_fingerprint": fingerprint(_ledger(result))}


def make_snapshot_capture_receipt(plan, report, result):
    """Attach a detached execution context and return proof, or None for old inputs.

    Call before setting receipt.result_fingerprint. This does not itself replay;
    the normal validation hook performs one bounded engine replay.
    """
    if not _declared(report.get("mechanics_model")):
        if _declared(plan.get("mechanics_model")) or _residue(result):
            raise ValueError(MESSAGE)
        return None
    execution = {"template": report["mechanics_input_template"],
                 "rotation_model": result["rotation_check"]["resolved_rotation_model"],
                 "mechanics_model": report["mechanics_model"],
                 "matches": report.get("mechanics_matches", [])}
    from .replay_numeric_projection import numeric_budget_result
    _bounded([execution, result.get("snapshot_captures"), numeric_budget_result(_ledger(result))])
    result["snapshot_capture_execution"] = copy.deepcopy(execution)
    return _proof(result, fingerprint(input_view(plan)), report["output_fingerprint"])


def _validate_result(result, receipt, _session):

    execution = result.get("snapshot_capture_execution")
    proof = receipt.get("snapshot_captures")
    if (type(execution) is not dict or set(execution) != EXECUTION_KEYS or type(proof) is not dict
            or not _declared(execution.get("mechanics_model")) or type(execution.get("matches")) is not list):
        raise ValueError(MESSAGE)
    from .replay_numeric_projection import numeric_budget_result
    _bounded([execution, result.get("snapshot_captures"), numeric_budget_result(_ledger(result)), proof])
    _same(proof, _proof(result, receipt.get("input_fingerprint"), receipt.get("output_fingerprint")))
    _same(receipt.get("result_fingerprint"), fingerprint(result))
    check = result["rotation_check"]
    _same(check["resolved_rotation_model"], execution["rotation_model"])
    _same(check["input_fingerprint"], fingerprint(execution))
    # One engine, no duplicated state machine, no database or source compiler.
    replay = _session.replay(execution)
    expected = replay.get("result")
    if expected is None or replay.get("resolved_template") is None:
        raise ValueError(MESSAGE)
    _same(expected.get("snapshot_captures"), result.get("snapshot_captures"))
    _same(fingerprint(replay["resolved_template"]), receipt["output_fingerprint"])
    # service preserves these actual engine rows and applies only this existing
    # failed-prefix DPS rule; presentation explanations never enter the proof.
    if not expected.get("mechanics", {}).get("complete_first_cycle", False):
        expected["summary"]["dps"] = None
    expected.update(rotation_check=replay["rotation_check"], mechanics_trace=replay["mechanics_trace"],
                    sampling_trace=replay.get("sampling_trace", []), mechanics_status=replay["status"],
                    mechanics_issues=replay.get("issues", []))
    from .replay_numeric_projection import replay_numeric_ledgers
    _same(*replay_numeric_ledgers(_ledger(expected), _ledger(result), result, receipt))
    for key in ("damage_reporting", "fixed_effect_sources", "target_condition_trace"):
        _same(expected.get(key), result.get(key))
    if "elemental" in expected:
        actual_elemental = result.get("elemental") or {}
        for key, value in expected["elemental"].items():
            _same(value, actual_elemental.get(key))


@verification_scope
def validate_snapshot_capture_result_receipt(result, receipt, *, _session=None):
    """Validate even manual captures; deletion of proof cannot hide residues."""
    try:
        if not (_residue(result) or "snapshot_captures" in receipt):
            return
        _validate_result(result, receipt, _session)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc


@verification_scope
def validate_snapshot_capture_receipt(plan, result, report, receipt, *, _session=None):
    """Full-package identity checks plus the same result-only replay."""
    try:
        if not (_declared(plan.get("mechanics_model")) or _declared(report.get("mechanics_model"))
                or _residue(result) or "snapshot_captures" in receipt):
            return
        validate_snapshot_capture_result_receipt(result, receipt, _session=_session)
        execution = result["snapshot_capture_execution"]
        _same(fingerprint(input_view(plan)), receipt["input_fingerprint"])
        _same(report["mechanics_model"], execution["mechanics_model"])
        _same(report["mechanics_input_template"], execution["template"])
        _same(report.get("mechanics_matches", []), execution["matches"])
        _same(report["output_fingerprint"], receipt["output_fingerprint"])
        _same(fingerprint(report["plan"]["resolved_template"]), receipt["output_fingerprint"])
        _same(input_view(report["plan"]), input_view(plan))
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError, RecursionError) as exc:
        raise ValueError(MESSAGE) from exc
