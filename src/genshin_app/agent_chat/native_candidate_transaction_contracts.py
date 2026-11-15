"""Pure candidate application identities and persisted transaction contracts.

Extracted unchanged from the legacy service; these contain no DSH lifecycle.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from genshin_app.equipment.files import fingerprint, input_view

APPLIED_PROOFS_CONTRACT = "genshin-native-applied-proofs-v1"

APPLIED_PROOFS_FILE = "native-applied-proofs-v1.json"

MAX_APPLIED_PROOFS_BYTES = 64 * 1024

MAX_APPLIED_PROOF_RECORDS = 64

APPLICATION_INTENT_CONTRACT = "genshin-native-application-intent-v1"

APPLICATION_INTENT_FILE = "native-application-intent-v1.json"

MAX_APPLICATION_INTENT_BYTES = 16 * 1024

LAST_APPLIED_WHITEBOARD_PROJECTION_CONTRACT = (
    "genshin-native-last-applied-whiteboard-projection-v1"
)

LAST_APPLIED_WHITEBOARD_PROJECTION_FILE = (
    "native-last-applied-whiteboard-projection-v1.json"
)

MAX_LAST_APPLIED_WHITEBOARD_PROJECTION_BYTES = 32 * 1024

_LAST_APPLIED_WHITEBOARD_PROJECTION_STATES = frozenset({
    "none", "result_only", "restore_unsaved_target", "conflict",
})

LAST_APPLIED_WHITEBOARD_BOARD_PREFIX = "native-last-applied-whiteboard-board-"

LAST_APPLIED_WHITEBOARD_CALCULATED_PREFIX = (
    "native-last-applied-whiteboard-calculated-"
)

_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")

_DIGEST = re.compile(r"[0-9a-f]{64}\Z")

_APPLICATION_INTENT_STATES = frozenset({
    "prepared",
    "baseline_advanced",
    "committed",
    "aborted",
})

_APPLICATION_IDENTITY_FIELDS = (
    "kind",
    "revision",
    "receipt_fingerprint",
    "document_digest",
    "application_payload_digest",
)

class NativeServiceV2Error(RuntimeError):
    """Stable renderer-safe service failure."""

    def __init__(self, code: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.details = copy.deepcopy(dict(details or {}))

def _stamp() -> str:
    return datetime.now(UTC).isoformat()

def _fail(code: str, *, details: Mapping[str, Any] | None = None) -> None:
    raise NativeServiceV2Error(code, details=details)

def _identity(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        _fail(f"native_{field_name}_invalid")
    return value

def _timestamp(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        _fail("native_conversation_metadata_invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _fail("native_conversation_metadata_invalid")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail("native_conversation_metadata_invalid")
    return value

def _unique_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("native_conversation_metadata_invalid")
        result[key] = value
    return result

def _unique_application_intent_pairs(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("native_application_intent_invalid")
        result[key] = value
    return result

def _application_document(kind: str, document: Mapping[str, Any]) -> dict[str, Any]:
    """Project the exact App document shape used by candidate baselines.

    Native calculated plans carry engine-owned ``resolution`` fields that the
    Renderer deliberately removes from its editable document.  CandidateSession
    uses ``input_view`` for the same boundary, so applied-proof identity must do
    likewise.  Whiteboards are already the exact editable document.
    """

    detached = copy.deepcopy(dict(document))
    return input_view(detached) if kind == "native" else detached

def _application_document_digest(kind: str, document: Mapping[str, Any]) -> str:
    return fingerprint(_application_document(kind, document))

def _application_document_id(kind: str, document: Mapping[str, Any]) -> str:
    key = "plan_id" if kind == "native" else "board_id"
    value = _application_document(kind, document).get(key)
    if not isinstance(value, str) or not value:
        _fail("native_candidate_application_recovery_unsafe")
    return value

def _application_payload(
    kind: str,
    document: Mapping[str, Any],
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the exact Renderer state covered by an application receipt.

    The editable document remains the candidate baseline identity.  This
    payload additionally binds engine-owned native resolution data or the
    whiteboard calculation projection that can change while the editable input
    stays byte-for-byte identical.
    """

    if kind not in {"native", "whiteboard"} or not isinstance(document, Mapping):
        _fail("native_candidate_application_payload_invalid")
    application_document = _application_document(kind, document)
    if payload is None:
        if kind == "native":
            result: dict[str, Any] = {
                "kind": "native",
                "plan": copy.deepcopy(dict(document)),
            }
        else:
            result = {
                "kind": "whiteboard",
                "board": copy.deepcopy(application_document),
                "calculated": None,
            }
    else:
        if not isinstance(payload, Mapping):
            _fail("native_candidate_application_payload_invalid")
        result = copy.deepcopy(dict(payload))
    if kind == "native":
        if (
            set(result) != {"kind", "plan"}
            or result.get("kind") != "native"
            or not isinstance(result.get("plan"), Mapping)
            or input_view(dict(result["plan"])) != application_document
        ):
            _fail("native_candidate_application_payload_invalid")
        result["plan"] = copy.deepcopy(dict(result["plan"]))
    elif (
        set(result) != {"kind", "board", "calculated"}
        or result.get("kind") != "whiteboard"
        or not isinstance(result.get("board"), Mapping)
        or dict(result["board"]) != application_document
        or result.get("calculated") is not None
        and not isinstance(result.get("calculated"), Mapping)
    ):
        _fail("native_candidate_application_payload_invalid")
    else:
        result["board"] = copy.deepcopy(dict(result["board"]))
        if isinstance(result["calculated"], Mapping):
            result["calculated"] = copy.deepcopy(dict(result["calculated"]))
    try:
        # Reject non-JSON values and NaN/Infinity at the trust boundary.
        fingerprint(result)
    except (TypeError, ValueError, OverflowError):
        _fail("native_candidate_application_payload_invalid")
    return result

def _application_payload_digest(
    kind: str,
    document: Mapping[str, Any],
    payload: Mapping[str, Any] | None = None,
) -> str:
    return fingerprint(_application_payload(kind, document, payload))

def _validate_applied_proofs(
    value: object,
    *,
    conversation_id: str,
) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != {"contract", "conversation_id", "records"}
        or value.get("contract") != APPLIED_PROOFS_CONTRACT
        or value.get("conversation_id") != conversation_id
        or not isinstance(value.get("records"), list)
        or len(value["records"]) > MAX_APPLIED_PROOF_RECORDS
    ):
        _fail("native_applied_proof_invalid")
    records = []
    for row in value["records"]:
        if (
            not isinstance(row, dict)
            or set(row) != {
                "kind",
                "revision",
                "receipt_fingerprint",
                "document_digest",
                "application_payload_digest",
                "applied_at",
            }
            or row.get("kind") not in {"native", "whiteboard"}
            or type(row.get("revision")) is not int
            or not 0 <= row["revision"] <= 2**53 - 1
            or not isinstance(row.get("receipt_fingerprint"), str)
            or _DIGEST.fullmatch(row["receipt_fingerprint"]) is None
            or not isinstance(row.get("document_digest"), str)
            or _DIGEST.fullmatch(row["document_digest"]) is None
            or not isinstance(row.get("application_payload_digest"), str)
            or _DIGEST.fullmatch(row["application_payload_digest"]) is None
        ):
            _fail("native_applied_proof_invalid")
        checked = copy.deepcopy(row)
        checked["applied_at"] = _timestamp(row.get("applied_at"))
        records.append(checked)
    candidate_keys = {
        (row["kind"], row["revision"], row["receipt_fingerprint"])
        for row in records
    }
    if len(candidate_keys) != len(records):
        _fail("native_applied_proof_invalid")
    return {
        "contract": APPLIED_PROOFS_CONTRACT,
        "conversation_id": conversation_id,
        "records": records,
    }

def _validate_application_intent(
    value: object,
    *,
    conversation_id: str,
) -> dict[str, Any]:
    """Validate the one bounded write-ahead candidate application record."""

    if (
        not isinstance(value, dict)
        or set(value) != {"contract", "conversation_id", "intent"}
        or value.get("contract") != APPLICATION_INTENT_CONTRACT
        or value.get("conversation_id") != conversation_id
        or value.get("intent") is not None
        and not isinstance(value.get("intent"), dict)
    ):
        _fail("native_application_intent_invalid")
    row = value.get("intent")
    if row is None:
        return {
            "contract": APPLICATION_INTENT_CONTRACT,
            "conversation_id": conversation_id,
            "intent": None,
        }
    if (
        set(row) != {
            "kind",
            "revision",
            "receipt_fingerprint",
            "baseline_document_digest",
            "baseline_payload_digest",
            "document_digest",
            "application_payload_digest",
            "state",
            "created_at",
            "updated_at",
        }
        or row.get("kind") not in {"native", "whiteboard"}
        or type(row.get("revision")) is not int
        or not 0 <= row["revision"] <= 2**53 - 1
        or not isinstance(row.get("receipt_fingerprint"), str)
        or _DIGEST.fullmatch(row["receipt_fingerprint"]) is None
        or not isinstance(row.get("baseline_document_digest"), str)
        or _DIGEST.fullmatch(row["baseline_document_digest"]) is None
        or not isinstance(row.get("baseline_payload_digest"), str)
        or _DIGEST.fullmatch(row["baseline_payload_digest"]) is None
        or not isinstance(row.get("document_digest"), str)
        or _DIGEST.fullmatch(row["document_digest"]) is None
        or not isinstance(row.get("application_payload_digest"), str)
        or _DIGEST.fullmatch(row["application_payload_digest"]) is None
        or row.get("state") not in _APPLICATION_INTENT_STATES
    ):
        _fail("native_application_intent_invalid")
    checked = copy.deepcopy(row)
    try:
        checked["created_at"] = _timestamp(row.get("created_at"))
        checked["updated_at"] = _timestamp(row.get("updated_at"))
    except NativeServiceV2Error:
        _fail("native_application_intent_invalid")
    return {
        "contract": APPLICATION_INTENT_CONTRACT,
        "conversation_id": conversation_id,
        "intent": checked,
    }

def _last_applied_whiteboard_artifact_name(
    value: object,
    *,
    prefix: str,
    digest: str,
) -> str:
    expected = f"{prefix}{digest}.json"
    if value != expected:
        _fail("native_last_applied_whiteboard_projection_invalid")
    return expected

def _validate_last_applied_whiteboard_projection(
    value: object,
    *,
    conversation_id: str,
) -> dict[str, Any]:
    """Validate the small durable pointer to one immutable App projection."""

    if (
        not isinstance(value, dict)
        or set(value) != {"contract", "conversation_id", "projection"}
        or value.get("contract") != LAST_APPLIED_WHITEBOARD_PROJECTION_CONTRACT
        or value.get("conversation_id") != conversation_id
        or not isinstance(value.get("projection"), dict)
    ):
        _fail("native_last_applied_whiteboard_projection_invalid")
    row = value["projection"]
    if (
        set(row) != {
            "board_id",
            "revision",
            "receipt_fingerprint",
            "baseline_document_digest",
            "baseline_payload_digest",
            "document_digest",
            "application_payload_digest",
            "application_kind",
            "board_artifact",
            "calculated_artifact",
            "calculated_file_sha",
            "proof",
            "committed_at",
        }
        or not isinstance(row.get("board_id"), str)
        or not row["board_id"]
        or type(row.get("revision")) is not int
        or not 0 <= row["revision"] <= 2**53 - 1
        or not isinstance(row.get("receipt_fingerprint"), str)
        or _DIGEST.fullmatch(row["receipt_fingerprint"]) is None
        or not isinstance(row.get("baseline_document_digest"), str)
        or _DIGEST.fullmatch(row["baseline_document_digest"]) is None
        or not isinstance(row.get("baseline_payload_digest"), str)
        or _DIGEST.fullmatch(row["baseline_payload_digest"]) is None
        or not isinstance(row.get("document_digest"), str)
        or _DIGEST.fullmatch(row["document_digest"]) is None
        or not isinstance(row.get("application_payload_digest"), str)
        or _DIGEST.fullmatch(row["application_payload_digest"]) is None
        or row.get("application_kind")
        not in {"whiteboard_draft", "whiteboard_calculated"}
        or not isinstance(row.get("proof"), dict)
    ):
        _fail("native_last_applied_whiteboard_projection_invalid")
    proof = row["proof"]
    if (
        set(proof) != {
            "kind",
            "revision",
            "receipt_fingerprint",
            "document_digest",
            "application_payload_digest",
            "applied_at",
        }
        or proof.get("kind") != "whiteboard"
        or proof.get("revision") != row["revision"]
        or proof.get("receipt_fingerprint") != row["receipt_fingerprint"]
        or proof.get("document_digest") != row["document_digest"]
        or proof.get("application_payload_digest")
        != row["application_payload_digest"]
    ):
        _fail("native_last_applied_whiteboard_projection_invalid")
    board_artifact = _last_applied_whiteboard_artifact_name(
        row.get("board_artifact"),
        prefix=LAST_APPLIED_WHITEBOARD_BOARD_PREFIX,
        digest=row["document_digest"],
    )
    if row["application_kind"] == "whiteboard_draft":
        if row.get("calculated_artifact") is not None or row.get(
            "calculated_file_sha"
        ) is not None:
            _fail("native_last_applied_whiteboard_projection_invalid")
        calculated_artifact = None
        calculated_file_sha = None
    else:
        calculated_artifact = _last_applied_whiteboard_artifact_name(
            row.get("calculated_artifact"),
            prefix=LAST_APPLIED_WHITEBOARD_CALCULATED_PREFIX,
            digest=row["application_payload_digest"],
        )
        calculated_file_sha = row.get("calculated_file_sha")
        if (
            not isinstance(calculated_file_sha, str)
            or _DIGEST.fullmatch(calculated_file_sha) is None
        ):
            _fail("native_last_applied_whiteboard_projection_invalid")
    checked = copy.deepcopy(row)
    checked["board_artifact"] = board_artifact
    checked["calculated_artifact"] = calculated_artifact
    checked["calculated_file_sha"] = calculated_file_sha
    try:
        checked["proof"]["applied_at"] = _timestamp(proof.get("applied_at"))
        checked["committed_at"] = _timestamp(row.get("committed_at"))
    except NativeServiceV2Error:
        _fail("native_last_applied_whiteboard_projection_invalid")
    return {
        "contract": LAST_APPLIED_WHITEBOARD_PROJECTION_CONTRACT,
        "conversation_id": conversation_id,
        "projection": checked,
    }
