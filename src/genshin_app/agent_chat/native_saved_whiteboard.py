"""User-requested reopening of an already committed App document.

No current Renderer document is inferred. Inspection is read-only. A restore
to an empty work area returns an explicit expected-absent creation contract;
the Renderer must confirm and CAS that absence before displaying the data.
This is document opening, not acceptance of a new Agent calculation.
"""
from __future__ import annotations

import copy

from .native_candidate_transaction_contracts import _application_payload_digest
from .native_dsh_mux_client import NativeDshMuxError


def saved_whiteboard(backend, scope, command, arguments):
    if type(arguments) is not dict:
        raise NativeDshMuxError("dsh_saved_whiteboard_request_invalid")
    store = backend._read_last_applied_whiteboard_projection(scope.session_root, scope.conversation_id)
    if command == "inspect" and not arguments and store is None:
        return {"status": "none"}
    if store is None:
        raise NativeDshMuxError("dsh_saved_whiteboard_not_found")
    row, board, delivery, result_path = backend._read_last_applied_whiteboard_projection_payload(
        scope.session_root, scope.conversation_id, store)
    proof = backend._application_proof(backend._read_applied_proofs(
        scope.session_root, scope.conversation_id)["records"], row["proof"],
        invalid_code="native_last_applied_whiteboard_projection_invalid")
    if proof != row["proof"]:
        raise NativeDshMuxError("dsh_saved_whiteboard_proof_invalid")
    metadata = {key: row[key] for key in ("board_id", "revision", "receipt_fingerprint",
        "document_digest", "application_payload_digest", "application_kind")}
    if command == "current_delivery":
        if set(arguments) != {"board", "calculated"} or delivery is None:
            raise NativeDshMuxError("dsh_saved_whiteboard_delivery_unavailable")
        current = {"kind": "whiteboard", **arguments}
        if _application_payload_digest("whiteboard", arguments["board"], current) != row["application_payload_digest"]:
            raise NativeDshMuxError("dsh_saved_whiteboard_stale")
        public = backend._register_whiteboard_delivery(
            conversation_id=scope.conversation_id, revision=row["revision"],
            receipt_fingerprint=row["receipt_fingerprint"], board=board, result_path=result_path,
            delivery=delivery, durable_projection=True)
        return {"status": "current_delivery", "revision": row["revision"], "receipt_fingerprint": row["receipt_fingerprint"],
                "delivery_capability": public["delivery_capability"]}
    preview = {"status": "available", **metadata, "board": copy.deepcopy(board),
        "calculated": copy.deepcopy(delivery.get("calculated")) if delivery is not None else None}
    if command == "inspect" and not arguments:
        return preview
    backend.assert_restore_writable(scope.conversation_id, operation="open_committed_whiteboard")
    intent = backend._read_application_intent(scope.session_root, scope.conversation_id)["intent"]
    if intent is not None and intent["state"] in {"prepared", "baseline_advanced"}:
        raise NativeDshMuxError("dsh_candidate_application_pending")
    if (command != "restore_new" or set(arguments) != {"expected_absent", "expected_revision", "expected_receipt"}
            or arguments["expected_absent"] is not True):
        raise NativeDshMuxError("dsh_saved_whiteboard_request_invalid")
    if (type(arguments["expected_revision"]) is not int or arguments["expected_revision"] != row["revision"]
            or arguments["expected_receipt"] != row["receipt_fingerprint"]):
        raise NativeDshMuxError("dsh_saved_whiteboard_stale")
    backend._expire_whiteboard_deliveries(scope.conversation_id)
    public_delivery = None if delivery is None else backend._register_whiteboard_delivery(
        conversation_id=scope.conversation_id, revision=row["revision"],
        receipt_fingerprint=row["receipt_fingerprint"], board=board, result_path=result_path,
        delivery=delivery, durable_projection=True)
    return {"status": "restore_new_ready", **metadata,
        "document_creation": {"kind": "whiteboard", "seed": copy.deepcopy(board), "expected_absent": True},
        "board": copy.deepcopy(board), "calculated_delivery": public_delivery}
