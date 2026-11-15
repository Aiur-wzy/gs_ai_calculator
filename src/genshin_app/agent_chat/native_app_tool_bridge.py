"""Strict bridge projection for unified App-owned native-Agent tools.

The domain host returns a detached terminal projection.  This module turns
that projection into the one wire settlement accepted by the reviewed Node
dispatcher and validates the acknowledgement before the provider loop may
continue.  It owns no candidate state and never executes a tool.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from typing import Any

from .bridge_protocol import JSON_SAFE_INTEGER_MAX, make_frame, validate_frame
from .native_app_tool_catalog import validate_native_app_tool_snapshot
from .native_app_tool_effects import (
    ALL_EFFECTS,
    REQUIRES_CHANGE,
    allowed_changes_for_transition,
    stage_transition_proof,
)

SETTLEMENT_CONTRACT = "genshin-native-app-tool-settlement-v1"
SETTLEMENT_ACK_CONTRACT = "genshin-native-app-tool-settlement-ack-v1"
MAX_RESULT_BYTES = 256 * 1024

_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_TERMINAL = frozenset({
    "settled_success",
    "settled_failure",
    "unknown_after_interruption",
})
_STATUSES = _TERMINAL | {"replay"}
_STATE_COMPONENTS = frozenset({
    "workflow",
    "workflow_stage",
    "document_id",
    "candidate_revision",
    "stage_revision",
    "database_fingerprint",
    "tool_manifest_hash",
    "runtime_generation",
    "writer_fence",
    "candidate_hash",
    "resources_hash",
    "findings_hash",
    "derived_hash",
    "stage_state_hash",
    "durable_checkpoint_hash",
})
_PAYLOAD_KEYS = frozenset({
    "contract",
    "tool",
    "status",
    "terminal_state",
    "effect",
    "result_json",
    "result_hash",
    "before_state_hash",
    "after_state_hash",
    "terminal_reason",
    "state_changes",
    "disallowed_state_changes",
    "stage_transition",
    "next_snapshot",
    "runtime_generation",
    "writer_fence",
    "catalog_hash",
})
_ACK_KEYS = frozenset({
    "contract",
    "disposition",
    "settlement_status",
    "terminal_state",
    "result_hash",
    "runtime_generation",
    "writer_fence",
    "catalog_hash",
    "next_catalog_hash",
    "rollover_required",
})
_FRAME_OPTIONALS = frozenset({
    "request_id",
    "call_id",
    "document_id",
    "candidate_revision",
    "stage_revision",
    "database_fingerprint",
    "tool_manifest_hash",
    "canonical_args_hash",
})
_FRAME_REQUIRED = frozenset({
    "protocol",
    "version",
    "message_id",
    "kind",
    "runtime_incarnation",
    "conversation_id",
    "turn_id",
    "step_id",
    "epoch_id",
    "event_seq",
    "type",
    "payload",
    "payload_hash",
})


class NativeAppToolBridgeError(ValueError):
    """Stable error raised for a malformed unified tool round trip."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise NativeAppToolBridgeError(code)


def _identity(value: object, field: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        _fail(f"native_app_{field}_invalid")
    return value


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        _fail(f"native_app_{field}_invalid")
    return value


def _revision(value: object, field: str) -> int:
    if type(value) is not int or not 0 <= value <= JSON_SAFE_INTEGER_MAX:
        _fail(f"native_app_{field}_invalid")
    return value


def _canonical_result(value: Mapping[str, Any]) -> tuple[str, str]:
    if not isinstance(value, Mapping):
        _fail("native_app_settlement_result_invalid")
    try:
        raw = json.dumps(
            copy.deepcopy(dict(value)),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        encoded = raw.encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        _fail("native_app_settlement_result_invalid")
    if not encoded or len(encoded) > MAX_RESULT_BYTES:
        _fail("native_app_settlement_result_size")
    return raw, hashlib.sha256(encoded).hexdigest()


def _changes(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or len(value) > len(_STATE_COMPONENTS):
        _fail(f"native_app_{field}_invalid")
    checked = [_identity(item, field) for item in value]
    if checked != sorted(set(checked)) or any(
        item not in _STATE_COMPONENTS for item in checked
    ):
        _fail(f"native_app_{field}_invalid")
    return checked


def _settlement_transition(
    effect: str,
    current: Mapping[str, Any],
    next_snapshot: Mapping[str, Any],
    *,
    tool_name: str,
    terminal_state: str,
    claimed: object,
    mismatch_reason: str = "native_app_settlement_stage_transition_mismatch",
) -> tuple[dict[str, Any] | None, bool]:
    """Select one proof/mask policy for both first settlement and lost-ACK replay.

    A public catalog proves an import's expected consequence, not that the
    Host's private checks passed. Success requires its exact proof; a strict
    UNKNOWN rejection keeps no proof and the base mask, including on replay.
    The boolean permits only that same conservative UNKNOWN replay. It never
    supplies a result, calculation acceptance or permission transition.
    """
    expected = stage_transition_proof(effect, current, next_snapshot, tool_name=tool_name)
    if expected is None or expected["kind"] != "artifact_representation":
        return expected, False
    if terminal_state == "unknown_after_interruption" and claimed is None:
        return None, True
    if claimed != expected:
        _fail(mismatch_reason)
    return expected, False


def build_native_app_tool_settlement(
    terminal: Mapping[str, Any],
    *,
    tool_name: str,
    current_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the exact Node settlement payload from one durable host terminal."""

    if not isinstance(terminal, Mapping):
        _fail("native_app_terminal_invalid")
    current = validate_native_app_tool_snapshot(current_snapshot)
    next_snapshot = validate_native_app_tool_snapshot(
        terminal.get("next_snapshot")
    )
    tool_name = _identity(tool_name, "tool_name")
    if terminal.get("tool_name") != tool_name:
        _fail("native_app_terminal_tool_mismatch")
    if tool_name not in current["policies"]:
        _fail("native_app_terminal_tool_forbidden")
    status = terminal.get("status")
    terminal_state = terminal.get("terminal_state")
    if status not in _STATUSES or terminal_state not in _TERMINAL:
        _fail("native_app_terminal_state_invalid")
    if status != "replay" and status != terminal_state:
        _fail("native_app_terminal_state_invalid")
    effect = _identity(terminal.get("effect"), "effect")
    if effect != current["policies"][tool_name]["effect"]:
        _fail("native_app_terminal_effect_mismatch")
    before_hash = _digest(terminal.get("before_state_hash"), "before_state_hash")
    after_hash = _digest(terminal.get("after_state_hash"), "after_state_hash")
    terminal_reason = terminal.get("terminal_reason")
    state_changes = _changes(terminal.get("state_changes", []), "state_changes")
    disallowed = _changes(
        terminal.get("disallowed_state_changes", []),
        "disallowed_state_changes",
    )
    if status == "replay" and (state_changes or disallowed):
        _fail("native_app_terminal_replay_changes")

    result_json: str | None
    result_hash: str | None
    if terminal_state == "unknown_after_interruption":
        if "result" in terminal or terminal.get("result_hash") is not None:
            _fail("native_app_terminal_unknown_result")
        result_json = None
        result_hash = None
        terminal_reason = _identity(terminal_reason, "terminal_reason")
    else:
        if not isinstance(terminal.get("result"), Mapping):
            _fail("native_app_terminal_result_missing")
        result_json, calculated_hash = _canonical_result(terminal["result"])
        result_hash = _digest(terminal.get("result_hash"), "result_hash")
        if result_hash != calculated_hash:
            _fail("native_app_terminal_result_hash_mismatch")
        if terminal_state == "settled_success":
            if terminal_reason is not None:
                _fail("native_app_terminal_reason_invalid")
        else:
            terminal_reason = _identity(terminal_reason, "terminal_reason")

    transition, _ = _settlement_transition(effect, current, next_snapshot, tool_name=tool_name,
        terminal_state=terminal_state, claimed=terminal.get("stage_transition"),
        mismatch_reason="native_app_terminal_stage_transition_mismatch")
    if terminal.get("stage_transition") not in (None, transition):
        _fail("native_app_terminal_stage_transition_mismatch")
    payload = {
        "contract": SETTLEMENT_CONTRACT,
        "tool": tool_name,
        "status": status,
        "terminal_state": terminal_state,
        "effect": effect,
        "result_json": result_json,
        "result_hash": result_hash,
        "before_state_hash": before_hash,
        "after_state_hash": after_hash,
        "terminal_reason": terminal_reason,
        "state_changes": state_changes,
        "disallowed_state_changes": disallowed,
        "stage_transition": transition,
        "next_snapshot": next_snapshot,
        "runtime_generation": current["runtime_generation"],
        "writer_fence": current["writer_fence"],
        "catalog_hash": current["catalog_hash"],
    }
    return validate_native_app_tool_settlement(payload, current_snapshot=current)


def validate_native_app_tool_settlement(
    value: Mapping[str, Any],
    *,
    current_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the host-to-Node payload without re-executing domain logic."""

    if not isinstance(value, Mapping) or set(value) != _PAYLOAD_KEYS:
        _fail("native_app_settlement_shape")
    payload = copy.deepcopy(dict(value))
    current = validate_native_app_tool_snapshot(current_snapshot)
    if payload.get("contract") != SETTLEMENT_CONTRACT:
        _fail("native_app_settlement_contract")
    tool_name = _identity(payload.get("tool"), "tool_name")
    if tool_name not in current["policies"]:
        _fail("native_app_settlement_tool_forbidden")
    if payload.get("runtime_generation") != current["runtime_generation"]:
        _fail("native_app_settlement_stale")
    if payload.get("writer_fence") != current["writer_fence"]:
        _fail("native_app_settlement_stale")
    if payload.get("catalog_hash") != current["catalog_hash"]:
        _fail("native_app_settlement_stale")
    if payload.get("effect") != current["policies"][tool_name]["effect"]:
        _fail("native_app_settlement_effect_mismatch")
    effect = payload["effect"]
    if effect not in ALL_EFFECTS:
        _fail("native_app_settlement_effect_mismatch")
    status = payload.get("status")
    terminal_state = payload.get("terminal_state")
    if status not in _STATUSES or terminal_state not in _TERMINAL:
        _fail("native_app_settlement_state_invalid")
    if status != "replay" and status != terminal_state:
        _fail("native_app_settlement_state_invalid")
    _digest(payload.get("before_state_hash"), "before_state_hash")
    _digest(payload.get("after_state_hash"), "after_state_hash")
    state_changes = _changes(payload.get("state_changes"), "state_changes")
    disallowed = _changes(
        payload.get("disallowed_state_changes"),
        "disallowed_state_changes",
    )
    if any(item not in state_changes for item in disallowed):
        _fail("native_app_settlement_effect_mask")
    next_snapshot = validate_native_app_tool_snapshot(payload.get("next_snapshot"))
    expected_transition, conservative_import_unknown = _settlement_transition(
        effect, current, next_snapshot, tool_name=tool_name,
        terminal_state=terminal_state, claimed=payload.get("stage_transition"))
    if payload.get("stage_transition") != expected_transition:
        _fail("native_app_settlement_stage_transition_mismatch")
    if (
        effect == "stage_transition"
        and terminal_state == "settled_success"
        and expected_transition is None
    ):
        _fail("native_app_settlement_stage_transition_missing")
    allowed = allowed_changes_for_transition(effect, expected_transition)
    expected_disallowed = sorted(set(state_changes) - allowed)
    if disallowed != expected_disallowed:
        _fail("native_app_settlement_effect_mask")
    if disallowed and terminal_state != "unknown_after_interruption":
        _fail("native_app_settlement_effect_mask")
    if status == "replay" and (state_changes or disallowed):
        _fail("native_app_terminal_replay_changes")
    if (
        status != "replay"
        and terminal_state == "settled_success"
        and effect in REQUIRES_CHANGE
        and not state_changes
    ):
        _fail("native_app_settlement_missing_effect")
    if terminal_state == "unknown_after_interruption":
        if payload.get("result_json") is not None or payload.get("result_hash") is not None:
            _fail("native_app_settlement_unknown_result")
        _identity(payload.get("terminal_reason"), "terminal_reason")
    else:
        raw = payload.get("result_json")
        if not isinstance(raw, str):
            _fail("native_app_settlement_result_invalid")
        try:
            parsed = json.loads(raw)
        except (ValueError, RecursionError):
            _fail("native_app_settlement_result_invalid")
        canonical, calculated = _canonical_result(parsed)
        if raw != canonical or payload.get("result_hash") != calculated:
            _fail("native_app_settlement_result_hash_mismatch")
        reports_success = (
            "code" not in parsed
            or parsed.get("code") is None
            or parsed.get("code") == 0
            or parsed.get("code") is False
        )
        if terminal_state == "settled_success" and not reports_success:
            _fail("native_app_settlement_result_code")
        if terminal_state == "settled_failure" and reports_success:
            _fail("native_app_settlement_result_code")
        if terminal_state == 'settled_failure' and parsed.get('reason') == 'calculation_verification_incomplete':
            from .native_app_tool_effects import allows_settled_failure_commit
            if (not allows_settled_failure_commit(tool_name, effect, parsed)
                    or parsed['details']['revision'] != next_snapshot['candidate_revision']):
                _fail('native_app_settlement_verification_evidence')
        diagnostic = (parsed.get('details') or {}).get('diagnostic') if isinstance(parsed.get('details'), dict) else None
        diagnostic_body = diagnostic.get('diagnostic') if isinstance(diagnostic, dict) else None
        output_validation_failure = (
            isinstance(parsed.get('system_fault'), dict)
            and parsed['system_fault'].get('operation') == 'gs_calculate.validate_generated_result'
        ) or (isinstance(diagnostic_body, dict)
              and diagnostic_body.get('contract') == 'calculation-output-validation-failure-v1')
        if terminal_state == 'settled_failure' and output_validation_failure:
            from .native_app_tool_effects import allows_settled_failure_commit
            if (not allows_settled_failure_commit(tool_name, effect, parsed)
                    or parsed['details']['revision'] != next_snapshot['candidate_revision']):
                _fail('native_app_settlement_calculation_failure_evidence')
        if terminal_state == "settled_success":
            if payload.get("terminal_reason") is not None:
                _fail("native_app_terminal_reason_invalid")
        else:
            _identity(payload.get("terminal_reason"), "terminal_reason")
    for field in (
        "document_id",
        "database_fingerprint",
        "runtime_generation",
        "writer_fence",
    ):
        if next_snapshot[field] != current[field]:
            _fail("native_app_settlement_next_snapshot_stale")
    if status != "replay":
        for snapshot_field, state_field in (
            ("workflow", "workflow"),
            ("workflow_stage", "workflow_stage"),
            ("candidate_revision", "candidate_revision"),
            ("stage_revision", "stage_revision"),
        ):
            changed = next_snapshot[snapshot_field] != current[snapshot_field]
            if changed != (state_field in state_changes):
                _fail("native_app_settlement_state_projection")
        manifest_changed = (
            next_snapshot["tool_manifest_hash"] != current["tool_manifest_hash"]
        )
        if manifest_changed != ("tool_manifest_hash" in state_changes):
            _fail("native_app_settlement_state_projection")
    else:
        replay_stage_identity_changed = any(
            next_snapshot[field] != current[field]
            for field in (
                "workflow_stage", "stage_revision", "tool_manifest_hash",
            )
        )
        if replay_stage_identity_changed and expected_transition is None and not conservative_import_unknown:
            _fail("native_app_settlement_stage_transition_mismatch")
    return payload


def make_native_app_tool_settle_request(
    *,
    settlement: Mapping[str, Any],
    current_snapshot: Mapping[str, Any],
    runtime_incarnation: str,
    conversation_id: str,
    turn_id: str,
    step_id: str,
    epoch_id: str,
    request_id: str,
    event_seq: int,
    call_id: str,
    canonical_args_hash: str,
) -> dict[str, Any]:
    """Create one fully bound ``tool.settle`` request."""

    snapshot = validate_native_app_tool_snapshot(current_snapshot)
    payload = validate_native_app_tool_settlement(
        settlement,
        current_snapshot=snapshot,
    )
    return make_frame(
        kind="request",
        frame_type="tool.settle",
        message_id=str(uuid.uuid4()),
        runtime_incarnation=_identity(runtime_incarnation, "runtime_incarnation"),
        conversation_id=_identity(conversation_id, "conversation_id"),
        turn_id=_identity(turn_id, "turn_id"),
        step_id=_identity(step_id, "step_id"),
        epoch_id=_identity(epoch_id, "epoch_id"),
        event_seq=_revision(event_seq, "event_seq"),
        payload=payload,
        request_id=_identity(request_id, "request_id"),
        call_id=_identity(call_id, "call_id"),
        document_id=snapshot["document_id"],
        candidate_revision=snapshot["candidate_revision"],
        stage_revision=snapshot["stage_revision"],
        database_fingerprint=snapshot["database_fingerprint"],
        tool_manifest_hash=snapshot["tool_manifest_hash"],
        canonical_args_hash=_digest(canonical_args_hash, "canonical_args_hash"),
    )


def validate_native_app_tool_settle_ack(
    value: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the Node acknowledgement against the exact request."""

    request_frame = validate_frame(request)
    response = validate_frame(value)
    if request_frame["kind"] != "request" or request_frame["type"] != "tool.settle":
        _fail("native_app_settlement_request_invalid")
    if response["kind"] != "response" or response["type"] != "tool.settle.ack":
        _fail("native_app_settlement_ack_invalid")
    if set(request_frame) - _FRAME_REQUIRED != _FRAME_OPTIONALS:
        _fail("native_app_settlement_request_shape")
    if set(response) - _FRAME_REQUIRED != _FRAME_OPTIONALS:
        _fail("native_app_settlement_ack_shape")
    for field in (
        "runtime_incarnation",
        "conversation_id",
        "turn_id",
        "step_id",
        "epoch_id",
        "request_id",
        "call_id",
        "document_id",
        "candidate_revision",
        "stage_revision",
        "database_fingerprint",
        "tool_manifest_hash",
        "canonical_args_hash",
    ):
        if response.get(field) != request_frame.get(field):
            _fail("native_app_settlement_ack_binding")
    payload = response.get("payload")
    if not isinstance(payload, dict) or set(payload) != _ACK_KEYS:
        _fail("native_app_settlement_ack_shape")
    settlement = request_frame["payload"]
    next_snapshot = settlement["next_snapshot"]
    expected = {
        "contract": SETTLEMENT_ACK_CONTRACT,
        "disposition": "accepted",
        "settlement_status": settlement["status"],
        "terminal_state": settlement["terminal_state"],
        "result_hash": settlement["result_hash"],
        "runtime_generation": settlement["runtime_generation"],
        "writer_fence": settlement["writer_fence"],
        "catalog_hash": settlement["catalog_hash"],
        "next_catalog_hash": next_snapshot["catalog_hash"],
        "rollover_required": next_snapshot["catalog_hash"]
        != settlement["catalog_hash"],
    }
    if payload != expected:
        _fail("native_app_settlement_ack_mismatch")
    return response


__all__ = [
    "MAX_RESULT_BYTES",
    "SETTLEMENT_ACK_CONTRACT",
    "SETTLEMENT_CONTRACT",
    "NativeAppToolBridgeError",
    "build_native_app_tool_settlement",
    "make_native_app_tool_settle_request",
    "validate_native_app_tool_settle_ack",
    "validate_native_app_tool_settlement",
]
