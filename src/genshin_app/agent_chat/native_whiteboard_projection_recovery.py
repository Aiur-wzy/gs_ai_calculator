"""Recovery of committed App whiteboards, independent of DSH chat recovery."""
from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from genshin_app.equipment.files import fingerprint

from .native_candidate_transaction_contracts import (
    _LAST_APPLIED_WHITEBOARD_PROJECTION_STATES,
    _application_document_digest,
    _application_document_id,
    _application_payload,
    _fail,
)


class NativeWhiteboardProjectionRecovery:
    def query_last_applied_whiteboard_projection(
        self,
        conversation_id: str,
        current_document: Mapping[str, Any],
        *,
        current_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Classify durable whiteboard projection recovery without authority.

        The query never returns the saved board, calculated result, filesystem
        locator or a delivery capability.  It only tells the Router whether the
        current Renderer needs a result-only replay, may explicitly restore an
        unsaved applied target, is already current, or conflicts by identity.
        """

        if not isinstance(current_document, Mapping):
            _fail("native_candidate_document_invalid")
        detached = copy.deepcopy(dict(current_document))
        with self._lock:
            bundle, _candidate = self._candidate_review_target(conversation_id)
            root = bundle.session_root
            intent_store = self._read_application_intent(root, conversation_id)
            self._validate_application_intent_for_binding(
                root,
                conversation_id,
                bundle.binding,
                intent_store,
            )
            active_intent = intent_store["intent"]
            if (
                isinstance(active_intent, Mapping)
                and active_intent["state"] in {"prepared", "baseline_advanced"}
            ):
                _fail("native_candidate_application_busy")
            store = self._read_last_applied_whiteboard_projection(
                root,
                conversation_id,
            )
            if store is None:
                return {
                    "conversation_id": conversation_id,
                    "status": "none",
                    "reason": "no_last_applied_projection",
                    "projection": None,
                }
            row = store["projection"]
            proof = self._application_proof(
                self._read_applied_proofs(root, conversation_id)["records"],
                row["proof"],
                invalid_code="native_last_applied_whiteboard_projection_invalid",
            )
            if proof is None or proof != row["proof"]:
                _fail("native_last_applied_whiteboard_projection_invalid")
            if isinstance(active_intent, Mapping) and (
                active_intent["state"] != "committed"
                or active_intent["kind"] != "whiteboard"
                or not self._same_application_identity(active_intent, row["proof"])
            ):
                _fail("native_candidate_application_recovery_unsafe")
            current_board_id = _application_document_id("whiteboard", detached)
            current = _application_payload(
                "whiteboard",
                detached,
                current_payload,
            )
            current_document_digest = _application_document_digest(
                "whiteboard",
                detached,
            )
            current_payload_digest = fingerprint(current)
            if current_board_id != row["board_id"]:
                status = "conflict"
                reason = "document_identity_changed"
            elif (
                current_document_digest == row["document_digest"]
                and current_payload_digest == row["application_payload_digest"]
            ):
                status = "none"
                reason = "projection_already_current"
            elif (
                current_document_digest == row["document_digest"]
                and
                row["application_kind"] == "whiteboard_calculated"
                and current.get("calculated") is None
            ):
                status = "result_only"
                reason = "calculated_projection_missing"
            elif (
                current_document_digest == row["baseline_document_digest"]
                and current_payload_digest == row["baseline_payload_digest"]
            ):
                status = "restore_unsaved_target"
                reason = "applied_target_not_saved"
            else:
                status = "conflict"
                reason = "calculated_projection_changed"
            if status not in _LAST_APPLIED_WHITEBOARD_PROJECTION_STATES:
                _fail("native_last_applied_whiteboard_projection_invalid")
            return {
                "conversation_id": conversation_id,
                "status": status,
                "reason": reason,
                "projection": {
                    "board_id": row["board_id"],
                    "revision": row["revision"],
                    "receipt_fingerprint": row["receipt_fingerprint"],
                    "document_digest": row["document_digest"],
                    "application_payload_digest": row[
                        "application_payload_digest"
                    ],
                    "application_kind": row["application_kind"],
                    "has_calculated": row["application_kind"]
                    == "whiteboard_calculated",
                },
            }


    def recover_last_applied_whiteboard_projection(
        self,
        conversation_id: str,
        current_document: Mapping[str, Any],
        *,
        current_payload: Mapping[str, Any] | None = None,
        allow_unsaved_target: bool = False,
    ) -> dict[str, Any]:
        """Return one path-free durable projection and a fresh capability."""

        self._assert_document_restore_writable(
            conversation_id,
            operation="whiteboard_projection_recover",
        )

        if type(allow_unsaved_target) is not bool:
            _fail("native_candidate_application_recovery_unsafe")
        with self._lock:
            state = self.query_last_applied_whiteboard_projection(
                conversation_id,
                current_document,
                current_payload=current_payload,
            )
            recovery_mode = state["status"]
            if recovery_mode == "restore_unsaved_target":
                if not allow_unsaved_target:
                    _fail("native_candidate_application_recovery_confirmation_required")
            elif recovery_mode != "result_only":
                _fail("native_candidate_application_recovery_unsafe")
            bundle, _candidate = self._candidate_review_target(conversation_id)
            if bundle.action_controller.execution_pending():
                _fail("native_app_action_busy")
            store = self._read_last_applied_whiteboard_projection(
                bundle.session_root,
                conversation_id,
            )
            if store is None:
                _fail("native_candidate_application_recovery_unsafe")
            row, board, delivery, result_path = (
                self._read_last_applied_whiteboard_projection_payload(
                    bundle.session_root,
                    conversation_id,
                    store,
                )
            )
            queried_projection = state.get("projection")
            if not isinstance(queried_projection, Mapping) or any(
                row[key] != queried_projection.get(key)
                for key in (
                    "board_id",
                    "revision",
                    "receipt_fingerprint",
                    "document_digest",
                    "application_payload_digest",
                    "application_kind",
                )
            ):
                _fail("native_last_applied_whiteboard_projection_invalid")
            self._expire_whiteboard_deliveries(conversation_id)
            public_delivery = None
            if isinstance(delivery, Mapping):
                if result_path is None:
                    _fail("native_last_applied_whiteboard_projection_invalid")
                public_delivery = self._register_whiteboard_delivery(
                    conversation_id=conversation_id,
                    revision=row["revision"],
                    receipt_fingerprint=row["receipt_fingerprint"],
                    board=board,
                    result_path=result_path,
                    delivery=delivery,
                    durable_projection=True,
                )
            result = {
                "conversation_id": conversation_id,
                "status": "recovered",
                "recovery_mode": recovery_mode,
                "application_kind": row["application_kind"],
                "revision": row["revision"],
                "receipt_fingerprint": row["receipt_fingerprint"],
                "board": copy.deepcopy(board),
                "calculated_delivery": public_delivery,
            }
            record_timeline = getattr(self, "_record_domain_timeline_node", None)
            if callable(record_timeline):
                record_timeline(
                    conversation_id,
                    kind="candidate",
                    category="candidate",
                    label="已恢复上次应用的白板投影",
                    state="recovered",
                    operation_id=(
                        f"candidate:whiteboard:{row['revision']}:"
                        f"{row['receipt_fingerprint'][:16]}"
                    ),
                    identity=(
                        "whiteboard-projection-recover",
                        row["revision"],
                        row["receipt_fingerprint"],
                        recovery_mode,
                    ),
                )
            return result


