"""Replay actual completed queues without granting unresolved game conditions."""
from __future__ import annotations

import copy

from .files import fingerprint
from .snapshot_capture_receipt import CHECK_APP_KEYS, EXECUTION_KEYS

CONTRACT = "automatic-energy-completion-v1"
FIELD = "automatic_energy_execution"
MESSAGE = "完整但待确认的扣能结果缺少一致的执行证明，请回App重新计算；未确认条件仍需处理。"


def needs_completion(check):
    termination = (check.get("automatic_energy_delivery") or {}).get("termination")
    return check.get("status") == "pending" and (
        not termination or isinstance(termination, dict) and termination.get("kind") == "cycle_proof")


def execution_inputs(report, check):
    return {"template": report["mechanics_input_template"],
            "rotation_model": check["resolved_rotation_model"],
            "mechanics_model": report["mechanics_model"], "matches": report["mechanics_matches"]}


def completion_proof(execution):
    return {"contract": CONTRACT, "execution_fingerprint": fingerprint(execution)}


def prepare_completion(report, result, session):
    """Bound before copying or hashing. Attach only after all preparation succeeds."""
    check = result["rotation_check"]
    if not needs_completion(check):
        if FIELD in result:
            raise ValueError(MESSAGE)
        return None
    execution = execution_inputs(report, check)
    session.scan_receipt_materials(inputs=(execution,))
    if fingerprint(execution) != check["input_fingerprint"]:
        raise ValueError(MESSAGE)
    return copy.deepcopy(execution)


def verify_completion(result, receipt, session):
    check = result["rotation_check"]
    proof = (receipt.get("action_resources") or {}).get("automatic_energy_delivery") or {}
    execution = result.get(FIELD)
    if not needs_completion(check):
        if FIELD in result or "completion" in proof:
            raise ValueError(MESSAGE)
        return None
    if (type(execution) is not dict or set(execution) != EXECUTION_KEYS
            or type(execution["matches"]) is not list
            or type(proof.get("completion")) is not dict
            or set(proof["completion"]) != {"contract", "execution_fingerprint"}
            or proof["completion"]["contract"] != CONTRACT):
        raise ValueError(MESSAGE)
    session.scan_receipt_materials(inputs=(execution,), outputs=(check,))
    if (proof["completion"] != completion_proof(execution)
            or fingerprint(execution) != check["input_fingerprint"]
            or fingerprint(execution["rotation_model"]) != fingerprint(check["resolved_rotation_model"])):
        raise ValueError(MESSAGE)
    replay = session.replay(execution, _result_fields=(),
                            _response_fields=("rotation_check", "status", "issues"))
    actual = {k: v for k, v in check.items() if k not in CHECK_APP_KEYS}
    expected = {k: v for k, v in replay.get("rotation_check", {}).items() if k not in CHECK_APP_KEYS}
    if (replay.get("result") is None or replay.get("resolved_template") is None
            or fingerprint(actual) != fingerprint(expected)
            or "mechanics_status" not in result or result["mechanics_status"] != replay["status"]
            or "mechanics_issues" not in result or result["mechanics_issues"] != replay.get("issues", [])):
        raise ValueError(MESSAGE)
    energy = check["energy_check"]
    checked, maximum = energy["checked_cycles"], execution["rotation_model"].get("max_cycles", 3)
    termination = check["automatic_energy_delivery"].get("termination")
    if (type(checked) is not int or type(maximum) is not int
            or not 1 <= checked == energy["completed_cycles"] <= maximum
            or energy.get("failed_cycle") is not None
            or not termination and checked != maximum):
        raise ValueError(MESSAGE)
    return checked


def verify_full_completion(result, report, session):
    if not needs_completion(result["rotation_check"]):
        return
    from genshin_app.rotation_check import resolve_rotation_model

    execution = result[FIELD]
    expected = execution_inputs(report, result["rotation_check"])
    expected["rotation_model"] = report["compiled_rotation_model"]
    session.scan_receipt_materials(inputs=(expected,))
    rotation, bindings = resolve_rotation_model(expected["template"], report["compiled_rotation_model"])
    expected["rotation_model"] = rotation
    if (fingerprint(execution) != fingerprint(expected)
            or fingerprint(bindings) != fingerprint(result["rotation_check"]["panel_bindings"])):
        raise ValueError(MESSAGE)
