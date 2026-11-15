"""Existing App whiteboard delivery consumers, independent of DSH lifecycle."""
from __future__ import annotations

import copy
import hashlib
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from genshin_app import whiteboard

from .native_candidate_transaction_contracts import (
    _DIGEST,
    NativeServiceV2Error,
    _fail,
    _identity,
)
from .sessions import MAX_CALCULATED


class NativeWhiteboardDeliveryConsumers:
    def apply_whiteboard_equipment_delivery(
        self,
        conversation_id: str,
        delivery_capability: str,
        board: Mapping[str, Any],
        *,
        db_path: str | Path,
    ) -> dict[str, Any]:
        """Recalculate from one accepted package without exposing its path.

        The capability is usable only by its original open conversation.  A
        normal candidate delivery remains bound to the current revision; a
        recovered last-applied delivery instead remains bound to its durable
        pointer, immutable package and exact applied proof.  The complete
        package is re-read and checked on every use before the existing
        equipment recalculator sees it.  Capabilities expire when their anchor
        changes, on conversation deletion or service restart, or on any
        integrity mismatch.
        """

        conversation_id = _identity(conversation_id, "conversation_id")
        self._assert_document_restore_writable(
            conversation_id,
            operation="whiteboard_equipment_apply",
        )
        if (
            not isinstance(delivery_capability, str)
            or re.fullmatch(r"wdc_v1_[A-Za-z0-9_-]{43}", delivery_capability)
            is None
            or not isinstance(board, Mapping)
        ):
            _fail("native_whiteboard_delivery_capability_invalid")
        capability_digest = hashlib.sha256(
            delivery_capability.encode("ascii", errors="strict")
        ).hexdigest()
        current_board = copy.deepcopy(dict(board))
        with self._lock:
            record = self._whiteboard_deliveries.get(capability_digest)
            if record is None:
                _fail("native_whiteboard_delivery_capability_expired")
            if record["conversation_id"] != conversation_id:
                _fail("native_whiteboard_delivery_capability_mismatch")
            bundle, candidate = self._candidate_review_target(conversation_id)
            checkpoint = self._candidate_review_checkpoint(bundle)
            summary = self._validate_candidate_summary(
                candidate.summary(), checkpoint=checkpoint
            )
            if summary["kind"] != "whiteboard":
                self._whiteboard_deliveries.pop(capability_digest, None)
                _fail("native_whiteboard_delivery_capability_expired")
            if record.get("durable_projection"):
                try:
                    projection_store = (
                        self._read_last_applied_whiteboard_projection(
                            bundle.session_root,
                            conversation_id,
                        )
                    )
                    if projection_store is None:
                        self._whiteboard_deliveries.pop(
                            capability_digest,
                            None,
                        )
                        _fail("native_whiteboard_delivery_capability_expired")
                    row, _saved_board, _delivery, saved_path = (
                        self._read_last_applied_whiteboard_projection_payload(
                            bundle.session_root,
                            conversation_id,
                            projection_store,
                        )
                    )
                except NativeServiceV2Error:
                    # A durable capability is authority derived from this
                    # exact pointer/proof/artifact chain.  Once that chain is
                    # observed invalid, consume the in-memory authority before
                    # surfacing the storage error.  Repairing files later must
                    # not resurrect a capability whose integrity check failed.
                    self._whiteboard_deliveries.pop(capability_digest, None)
                    raise
                intent_store = self._read_application_intent(
                    bundle.session_root,
                    conversation_id,
                )
                self._validate_application_intent_for_binding(
                    bundle.session_root,
                    conversation_id,
                    bundle.binding,
                    intent_store,
                )
                intent = intent_store["intent"]
                if isinstance(intent, Mapping):
                    same_application = self._same_application_identity(
                        intent,
                        row["proof"],
                    )
                    if intent["state"] != "committed" or not same_application:
                        # A failed terminal-intent write leaves the exact ACK in
                        # ``baseline_advanced`` after its proof and projection
                        # are already durable.  Reject recalculation until the
                        # ACK retry commits, but preserve that exact capability
                        # so the retry can restore normal editing without a
                        # service restart.  Every other intent invalidates it.
                        if not (
                            intent["state"] == "baseline_advanced"
                            and same_application
                        ):
                            self._whiteboard_deliveries.pop(
                                capability_digest,
                                None,
                            )
                        _fail("native_whiteboard_delivery_not_applied")
                if (
                    row["revision"] != record["candidate_revision"]
                    or row["receipt_fingerprint"]
                    != record["receipt_fingerprint"]
                    or row["document_digest"] != record["board_digest"]
                    or saved_path is None
                    or saved_path != record["result_path"]
                ):
                    self._whiteboard_deliveries.pop(capability_digest, None)
                    _fail("native_whiteboard_delivery_capability_expired")
                proof = row["proof"]
            else:
                if (
                    summary["revision"] != record["candidate_revision"]
                    or summary["receipt_fingerprint"]
                    != record["receipt_fingerprint"]
                ):
                    self._whiteboard_deliveries.pop(capability_digest, None)
                    _fail("native_whiteboard_delivery_capability_expired")
                proof = self._candidate_application_port.applied_candidate_proof(
                    conversation_id
                ).get("proof")
            if (
                not isinstance(proof, Mapping)
                or proof.get("kind") != "whiteboard"
                or proof.get("revision") != record["candidate_revision"]
                or proof.get("receipt_fingerprint") != record["receipt_fingerprint"]
                or proof.get("document_digest") != record["board_digest"]
            ):
                if record.get("durable_projection"):
                    self._whiteboard_deliveries.pop(capability_digest, None)
                _fail("native_whiteboard_delivery_not_applied")
            try:
                verified = whiteboard.read_whiteboard_result(
                    record["board"], record["result_path"]
                )
            except NativeServiceV2Error:
                self._whiteboard_deliveries.pop(capability_digest, None)
                raise
            except Exception as error:  # noqa: BLE001 - trusted artifact boundary
                self._whiteboard_deliveries.pop(capability_digest, None)
                raise NativeServiceV2Error(self._stable_error(error)) from None
            if verified.get("file_sha") != record["file_sha"]:
                self._whiteboard_deliveries.pop(capability_digest, None)
                _fail("native_whiteboard_delivery_integrity_changed")
            try:
                response = whiteboard.apply_whiteboard_equipment(
                    current_board,
                    record["board"],
                    record["result_path"],
                    db_path=Path(db_path),
                )
            except NativeServiceV2Error:
                raise
            except Exception as error:  # noqa: BLE001 - Host calculation boundary
                raise NativeServiceV2Error(self._stable_error(error)) from None
            source = response.get("source_result")
            if isinstance(source, Mapping):
                if source.get("file_sha") != record["file_sha"]:
                    self._whiteboard_deliveries.pop(capability_digest, None)
                    _fail("native_whiteboard_delivery_integrity_changed")
                # Filesystem identities remain inside this service.  The
                # Renderer needs only the validation result and its existing
                # opaque handle for another cumulative edit.
                response["source_result"] = {
                    "validation": "host_verified_delivery_capability"
                }
            return copy.deepcopy(response)

    def publish_committed_whiteboard_result(
        self,
        conversation_id: str,
        delivery_capability: str,
        output_path: str | Path,
        expected_revision: int | None = None,
        expected_receipt: str | None = None,
        *,
        format: str = "json",
        expected_file_sha: str | None = None,
        expected_result_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        """Publish the exact calculated package or its verified report tables.

        This is a trusted Host export seam for the headless GUI acceptance
        runner.  It does not accept a caller-supplied calculation object and
        it is intentionally unavailable before the Renderer-equivalent
        ``record_candidate_applied`` acknowledgement has durably committed the
        board/package pair.  The opaque delivery capability, applied proof,
        durable projection and requested candidate identity must all agree.
        """

        conversation_id = _identity(conversation_id, "conversation_id")
        table_export = format in ("xlsx", "csv")
        if (
            not isinstance(delivery_capability, str)
            or re.fullmatch(r"wdc_v1_[A-Za-z0-9_-]{43}", delivery_capability)
            is None
            or not isinstance(output_path, (str, os.PathLike))
            or format not in ("json", "xlsx", "csv")
            or (expected_file_sha is not None and (not isinstance(expected_file_sha, str)
                                                 or _DIGEST.fullmatch(expected_file_sha) is None))
        ):
            _fail("native_whiteboard_delivery_export_invalid")
        destination = Path(output_path).resolve()
        if table_export:
            if destination.suffix.lower() != f".{format}":
                _fail("native_whiteboard_delivery_export_format")
            if not isinstance(expected_result_fingerprint, str) or _DIGEST.fullmatch(expected_result_fingerprint) is None:
                _fail("native_whiteboard_delivery_export_invalid")
        elif (type(expected_revision) is not int or not 0 <= expected_revision <= 2**53 - 1
              or not isinstance(expected_receipt, str) or _DIGEST.fullmatch(expected_receipt) is None):
            _fail("native_whiteboard_delivery_export_invalid")
        capability_digest = hashlib.sha256(
            delivery_capability.encode("ascii", errors="strict")
        ).hexdigest()

        with self._lock:
            record = self._whiteboard_deliveries.get(capability_digest)
            if record is None:
                _fail("native_whiteboard_delivery_capability_expired")
            if record.get("conversation_id") != conversation_id:
                _fail("native_whiteboard_delivery_capability_mismatch")
            if table_export:
                expected_revision, expected_receipt = record.get("candidate_revision"), record.get("receipt_fingerprint")
            if (
                record.get("candidate_revision") != expected_revision
                or record.get("receipt_fingerprint") != expected_receipt
            ):
                _fail("native_whiteboard_delivery_capability_mismatch")
            if record.get("durable_projection") is not True:
                _fail("native_whiteboard_delivery_not_applied")

            bundle, _candidate = self._candidate_review_target(conversation_id)
            store = self._read_last_applied_whiteboard_projection(
                bundle.session_root,
                conversation_id,
            )
            if store is None:
                _fail("native_whiteboard_delivery_not_applied")
            row, board, delivery, result_path = (
                self._read_last_applied_whiteboard_projection_payload(
                    bundle.session_root,
                    conversation_id,
                    store,
                    **({"trusted_file_sha": record["file_sha"]} if table_export else {}),
                )
            )
            if (
                row.get("application_kind") != "whiteboard_calculated"
                or row.get("revision") != expected_revision
                or row.get("receipt_fingerprint") != expected_receipt
                or not isinstance(delivery, Mapping)
                or not isinstance(result_path, Path)
                or result_path.absolute() != record.get("result_path")
                or row.get("document_digest") != record.get("board_digest")
                or delivery.get("file_sha") != record.get("file_sha")
            ):
                _fail("native_whiteboard_delivery_capability_expired")

            intent_store = self._read_application_intent(
                bundle.session_root,
                conversation_id,
            )
            self._validate_application_intent_for_binding(
                bundle.session_root,
                conversation_id,
                bundle.binding,
                intent_store,
            )
            intent = intent_store.get("intent")
            if (
                not isinstance(intent, Mapping)
                or intent.get("state") != "committed"
                or not self._same_application_identity(intent, row["proof"])
            ):
                _fail("native_whiteboard_delivery_not_applied")

            source = result_path.resolve()
            try:
                destination.relative_to(bundle.session_root.resolve())
            except ValueError:
                pass
            else:
                _fail("native_whiteboard_delivery_export_path_invalid")
            if destination == source:
                _fail("native_whiteboard_delivery_export_path_invalid")
            try:
                size = source.stat().st_size
                if not 0 < size <= MAX_CALCULATED:
                    _fail("native_whiteboard_delivery_integrity_changed")
                with source.open("rb") as stream:
                    content = stream.read(MAX_CALCULATED + 1)
            except NativeServiceV2Error:
                raise
            except OSError:
                _fail("native_whiteboard_delivery_integrity_changed")
            if len(content) > MAX_CALCULATED or hashlib.sha256(content).hexdigest() != delivery["file_sha"]:
                self._whiteboard_deliveries.pop(capability_digest, None)
                _fail("native_whiteboard_delivery_integrity_changed")

            from genshin_app.output_policy import publish_ordinary_files
            if table_export:
                from genshin_app.equipment.provisional_entities import (
                    validate_current_session_usage_receipt,
                )
                from genshin_app.equipment.service import (
                    _encode_validated_result,
                    display_names,
                )
                from genshin_app.export import _formal_audit_from_verified_result

                calculated = delivery["calculated"]
                result, receipt = calculated["result"], calculated["result_receipt"]
                if receipt["result_fingerprint"] != expected_result_fingerprint:
                    _fail("native_whiteboard_delivery_capability_mismatch")
                context = calculated.get("display_context")
                names = display_names(calculated["plan"], context["catalog"]) if context else None
                candidate_session = bundle.binding.candidate_session
                validate_current_session_usage_receipt(result, receipt, candidate_session)
                content = _encode_validated_result(result, receipt, format, names, calculated.get("equipment_summary"),
                    formal_audit=_formal_audit_from_verified_result(result, receipt))
                published = publish_ordinary_files([(destination, content)], candidate_session.db_path,
                    expected_shas={destination: expected_file_sha}, input_paths=[source, candidate_session.db_path])
                return {"path": str(destination), "file_sha": published["file_shas"][str(destination)],
                        "revision": expected_revision, "receipt_fingerprint": expected_receipt,
                        "board_id": board.get("board_id"), "format": format,
                        "result_fingerprint": receipt["result_fingerprint"],
                        "validation": "committed_gui_agent_report_export",
                        "published_paths": published["published_paths"]}
            published = publish_ordinary_files([(destination, content)], bundle.binding.candidate_session.db_path,
                                      input_paths=[source, bundle.binding.candidate_session.db_path])
            try:
                exported = whiteboard.read_whiteboard_result(board, destination)
            except Exception as error:  # noqa: BLE001 - trusted export verification
                # Publication already happened. A failed readback is not a
                # rollback, and an external editor may now own newer bytes.
                _fail("native_whiteboard_delivery_export_verify_failed", details={
                    "published_paths": published["published_paths"], "path": str(destination),
                    "publication_state": "committed_verification_failed", "requires_user_inspection": True,
                    "cause_code": self._stable_error(error),
                })
            if (
                exported.get("file_sha") != delivery["file_sha"]
                or exported.get("calculated") != delivery.get("calculated")
            ):
                _fail("native_whiteboard_delivery_export_verify_failed", details={
                    "published_paths": published["published_paths"], "path": str(destination),
                    "publication_state": "committed_verification_failed", "requires_user_inspection": True,
                })
            return {
                "path": str(destination),
                "file_sha": exported["file_sha"],
                "revision": expected_revision,
                "receipt_fingerprint": expected_receipt,
                "board_id": board.get("board_id"),
                "validation": "committed_gui_agent_delivery_reloaded",
                "published_paths": published["published_paths"],
            }
