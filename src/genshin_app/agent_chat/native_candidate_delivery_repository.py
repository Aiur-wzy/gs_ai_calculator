"""App-owned candidate NativeCandidateDeliveryRepository; no Session or Turn state."""
from __future__ import annotations

import copy
import hashlib
import os
import secrets
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from genshin_app import whiteboard
from genshin_app.equipment.files import fingerprint

from .native_candidate_transaction_contracts import (
    _DIGEST,
    LAST_APPLIED_WHITEBOARD_BOARD_PREFIX,
    LAST_APPLIED_WHITEBOARD_CALCULATED_PREFIX,
    LAST_APPLIED_WHITEBOARD_PROJECTION_CONTRACT,
    NativeServiceV2Error,
    _application_document,
    _application_document_digest,
    _application_document_id,
    _application_payload,
    _fail,
    _stamp,
    _validate_last_applied_whiteboard_projection,
)
from .sessions import MAX_CALCULATED, MAX_NATIVE, read_owned, safe_path


class NativeCandidateDeliveryRepository:
    def _register_whiteboard_delivery(
        self,
        *,
        conversation_id: str,
        revision: int,
        receipt_fingerprint: str,
        board: Mapping[str, Any],
        result_path: Path,
        delivery: Mapping[str, Any],
        durable_projection: bool = False,
    ) -> dict[str, Any]:
        public_delivery = copy.deepcopy(dict(delivery))
        if "path" not in public_delivery or "file_sha" not in public_delivery:
            _fail("native_candidate_acceptance_invalid")
        capability = "wdc_v1_" + secrets.token_urlsafe(32)
        capability_digest = hashlib.sha256(capability.encode("ascii")).hexdigest()
        self._whiteboard_deliveries[capability_digest] = {
            "conversation_id": conversation_id,
            "candidate_revision": revision,
            "receipt_fingerprint": receipt_fingerprint,
            "board_digest": _application_document_digest("whiteboard", board),
            "board": copy.deepcopy(dict(board)),
            "result_path": result_path.absolute(),
            "file_sha": public_delivery["file_sha"],
            "durable_projection": durable_projection,
        }
        public_delivery.pop("path", None)
        public_delivery.pop("file_sha", None)
        public_delivery["delivery_capability"] = capability
        return public_delivery

    def _promote_exact_whiteboard_deliveries(
        self,
        *,
        conversation_id: str,
        row: Mapping[str, Any],
        board: Mapping[str, Any],
        delivery: Mapping[str, Any] | None,
        result_path: Path | None,
    ) -> None:
        """Re-anchor exact live capabilities to one persisted projection.

        This is deliberately an in-memory promotion performed only after the
        proof and projection pointer are durable.  A promoted capability still
        re-reads that pointer, proof and immutable package on every use; a
        service restart therefore continues to invalidate all capabilities.
        """

        if row.get("application_kind") != "whiteboard_calculated":
            return
        if not isinstance(delivery, Mapping) or not isinstance(result_path, Path):
            _fail("native_last_applied_whiteboard_projection_invalid")
        calculated = delivery.get("calculated")
        calculated_file_sha = row.get("calculated_file_sha")
        if (
            not isinstance(calculated, Mapping)
            or not isinstance(calculated_file_sha, str)
            or _DIGEST.fullmatch(calculated_file_sha) is None
            or delivery.get("file_sha") != calculated_file_sha
            or _application_document_digest("whiteboard", board)
            != row.get("document_digest")
            or fingerprint(
                {
                    "kind": "whiteboard",
                    "board": copy.deepcopy(dict(board)),
                    "calculated": copy.deepcopy(dict(calculated)),
                }
            )
            != row.get("application_payload_digest")
        ):
            _fail("native_last_applied_whiteboard_projection_invalid")

        pinned_path = result_path.absolute()
        for capability_digest, record in tuple(self._whiteboard_deliveries.items()):
            if (
                record.get("conversation_id") != conversation_id
                or record.get("candidate_revision") != row.get("revision")
                or record.get("receipt_fingerprint")
                != row.get("receipt_fingerprint")
                or record.get("board_digest") != row.get("document_digest")
            ):
                continue
            record_board = record.get("board")
            source_path = record.get("result_path")
            if not isinstance(record_board, Mapping) or not isinstance(
                source_path, Path
            ):
                continue
            try:
                if (
                    _application_document_digest("whiteboard", record_board)
                    != row["document_digest"]
                ):
                    continue
                source_delivery = whiteboard.read_whiteboard_result(
                    record_board,
                    source_path,
                )
                source_package = read_owned(
                    source_path.parent,
                    source_path.name,
                    MAX_CALCULATED,
                )
            except Exception:  # noqa: BLE001,S112 - inexact token stays non-durable
                continue
            if (
                not isinstance(source_package, Mapping)
                or source_delivery.get("file_sha") != record.get("file_sha")
                or source_delivery.get("calculated") != calculated
                or fingerprint(source_package) != calculated_file_sha
            ):
                continue
            # Replace the record in one assignment while the service lock is
            # held, so observers never see a durable token still pointing at
            # the candidate's transient calculation package.
            self._whiteboard_deliveries[capability_digest] = {
                **record,
                "board": copy.deepcopy(dict(board)),
                "result_path": pinned_path,
                "file_sha": calculated_file_sha,
                "durable_projection": True,
            }

    def _read_last_applied_whiteboard_projection_payload(
        self,
        root: Path,
        conversation_id: str,
        store: Mapping[str, Any],
        *,
        trusted_file_sha=None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None, Path | None]:
        """Resolve and revalidate one durable projection without issuing authority."""

        try:
            checked_store = _validate_last_applied_whiteboard_projection(
                copy.deepcopy(dict(store)),
                conversation_id=conversation_id,
            )
            row = checked_store["projection"]
            proof = self._application_proof(
                self._read_applied_proofs(root, conversation_id)["records"],
                {
                    "kind": "whiteboard",
                    "revision": row["revision"],
                    "receipt_fingerprint": row["receipt_fingerprint"],
                    "document_digest": row["document_digest"],
                    "application_payload_digest": row[
                        "application_payload_digest"
                    ],
                },
                invalid_code="native_last_applied_whiteboard_projection_invalid",
            )
            if proof is None or proof != row["proof"]:
                _fail("native_last_applied_whiteboard_projection_invalid")
            board_value = read_owned(
                root,
                row["board_artifact"],
                MAX_NATIVE,
            )
            if not isinstance(board_value, Mapping):
                _fail("native_last_applied_whiteboard_projection_invalid")
            board = copy.deepcopy(dict(board_value))
            if (
                _application_document_digest("whiteboard", board)
                != row["document_digest"]
                or _application_document_id("whiteboard", board) != row["board_id"]
            ):
                _fail("native_last_applied_whiteboard_projection_invalid")
            calculated_delivery: dict[str, Any] | None = None
            result_path: Path | None = None
            if row["application_kind"] == "whiteboard_calculated":
                result_path = safe_path(root, row["calculated_artifact"])
                if trusted_file_sha is None:
                    calculation = whiteboard.read_whiteboard_result(board, result_path)
                else:
                    # The live capability was promoted only after full validation of the exact
                    # package. Reuse that verification exclusively for the same private bytes.
                    from genshin_app.equipment.calculation_wire import (
                        PACKAGE_KIND,
                        PACKAGE_SCHEMA,
                    )
                    from genshin_app.whiteboard_store import _read

                    package, digest, _ = _read(result_path, MAX_CALCULATED)
                    if (digest != trusted_file_sha or digest != row["calculated_file_sha"]
                            or not isinstance(package, dict) or package.get("kind") != PACKAGE_KIND
                            or package.get("package_schema") != PACKAGE_SCHEMA):
                        _fail("native_whiteboard_delivery_integrity_changed")
                    calculation = {"file_sha": digest, "calculated": package.get("calculated")}
                calculated_delivery = copy.deepcopy(dict(calculation))
                if calculated_delivery.get("file_sha") != row["calculated_file_sha"]:
                    _fail("native_last_applied_whiteboard_projection_invalid")
                calculated = calculated_delivery.get("calculated")
                if not isinstance(calculated, Mapping):
                    _fail("native_last_applied_whiteboard_projection_invalid")
            else:
                calculated = None
            payload = {
                "kind": "whiteboard",
                "board": copy.deepcopy(board),
                "calculated": (
                    copy.deepcopy(dict(calculated))
                    if isinstance(calculated, Mapping)
                    else None
                ),
            }
            if fingerprint(payload) != row["application_payload_digest"]:
                _fail("native_last_applied_whiteboard_projection_invalid")
            return row, board, calculated_delivery, result_path
        except NativeServiceV2Error:
            raise
        except Exception:  # noqa: BLE001 - normalize all artifact integrity failures
            _fail("native_last_applied_whiteboard_projection_invalid")

    def _persist_last_applied_whiteboard_projection(
        self,
        root: Path,
        conversation_id: str,
        *,
        bundle: object,
        candidate: object,
        intent: Mapping[str, Any],
        proof: Mapping[str, Any],
        board: Mapping[str, Any],
        payload: Mapping[str, Any] | None,
    ) -> None:
        """Pin the exact applied board/package before an intent becomes terminal."""

        normalized_payload = _application_payload("whiteboard", board, payload)
        try:
            board_id = _application_document_id("whiteboard", board)
        except NativeServiceV2Error:
            # Keep the pre-existing application transaction compatible with
            # legacy whiteboard-like test/adaptor payloads.  Only a real
            # board identity is eligible for durable projection recovery.
            return
        identity = {
            "kind": "whiteboard",
            "revision": proof.get("revision"),
            "receipt_fingerprint": proof.get("receipt_fingerprint"),
            "document_digest": proof.get("document_digest"),
            "application_payload_digest": proof.get(
                "application_payload_digest"
            ),
        }
        if (
            intent.get("state") not in {"baseline_advanced", "committed"}
            or not self._same_application_identity(intent, identity)
            or not isinstance(intent.get("baseline_document_digest"), str)
            or _DIGEST.fullmatch(intent["baseline_document_digest"]) is None
            or not isinstance(intent.get("baseline_payload_digest"), str)
            or _DIGEST.fullmatch(intent["baseline_payload_digest"]) is None
            or
            proof.get("kind") != "whiteboard"
            or _application_document_digest("whiteboard", board)
            != identity["document_digest"]
            or fingerprint(normalized_payload)
            != identity["application_payload_digest"]
        ):
            _fail("native_last_applied_whiteboard_projection_invalid")
        previous = self._read_last_applied_whiteboard_projection(
            root,
            conversation_id,
        )
        if previous is not None and self._same_application_identity(
            previous["projection"]["proof"],
            identity,
        ):
            row, saved_board, saved_delivery, saved_path = (
                self._read_last_applied_whiteboard_projection_payload(
                    root,
                    conversation_id,
                    previous,
                )
            )
            saved_payload = {
                "kind": "whiteboard",
                "board": saved_board,
                "calculated": (
                    saved_delivery.get("calculated")
                    if isinstance(saved_delivery, Mapping)
                    else None
                ),
            }
            if (
                row["proof"] != proof
                or row["baseline_document_digest"]
                != intent["baseline_document_digest"]
                or row["baseline_payload_digest"]
                != intent["baseline_payload_digest"]
                or fingerprint(saved_payload)
                != identity["application_payload_digest"]
            ):
                _fail("native_last_applied_whiteboard_projection_invalid")
            self._promote_exact_whiteboard_deliveries(
                conversation_id=conversation_id,
                row=row,
                board=saved_board,
                delivery=saved_delivery,
                result_path=saved_path,
            )
            return

        board_name = (
            f"{LAST_APPLIED_WHITEBOARD_BOARD_PREFIX}"
            f"{identity['document_digest']}.json"
        )
        calculated = normalized_payload["calculated"]
        calculated_name = None
        calculated_file_sha = None
        package: Mapping[str, Any] | None = None
        if isinstance(calculated, Mapping):
            matching: list[tuple[Path, dict[str, Any]]] = []
            for delivery_record in self._whiteboard_deliveries.values():
                if (
                    delivery_record.get("conversation_id") != conversation_id
                    or delivery_record.get("candidate_revision")
                    != identity["revision"]
                    or delivery_record.get("receipt_fingerprint")
                    != identity["receipt_fingerprint"]
                    or delivery_record.get("board_digest")
                    != identity["document_digest"]
                ):
                    continue
                source_path = delivery_record.get("result_path")
                if not isinstance(source_path, Path):
                    _fail("native_last_applied_whiteboard_projection_invalid")
                delivery = whiteboard.read_whiteboard_result(board, source_path)
                if (
                    delivery.get("file_sha") != delivery_record.get("file_sha")
                    or delivery.get("calculated") != calculated
                ):
                    continue
                matching.append((source_path, dict(delivery)))
            if not matching:
                boundary = getattr(bundle.binding, "domain_write_boundary", None)
                if not callable(boundary):
                    _fail("native_app_binding_invalid")
                try:
                    with boundary():
                        checkpoint = self._candidate_review_checkpoint(bundle)
                        accepted = candidate.accept(
                            "whiteboard",
                            copy.deepcopy(dict(board)),
                            identity["revision"],
                            identity["receipt_fingerprint"],
                        )
                        self._validate_candidate_acceptance(
                            accepted,
                            kind="whiteboard",
                            expected_revision=identity["revision"],
                            expected_receipt=identity["receipt_fingerprint"],
                            conversation_id=conversation_id,
                        )
                        after = self._candidate_review_checkpoint(bundle)
                except NativeServiceV2Error:
                    raise
                except Exception as error:  # noqa: BLE001 - stable Host boundary
                    raise NativeServiceV2Error(self._stable_error(error)) from None
                if after != checkpoint:
                    _fail("native_candidate_changed")
                for delivery_record in self._whiteboard_deliveries.values():
                    if (
                        delivery_record.get("conversation_id") == conversation_id
                        and delivery_record.get("candidate_revision")
                        == identity["revision"]
                        and delivery_record.get("receipt_fingerprint")
                        == identity["receipt_fingerprint"]
                        and delivery_record.get("board_digest")
                        == identity["document_digest"]
                    ):
                        source_path = delivery_record.get("result_path")
                        if not isinstance(source_path, Path):
                            _fail(
                                "native_last_applied_whiteboard_projection_invalid"
                            )
                        delivery = whiteboard.read_whiteboard_result(
                            board,
                            source_path,
                        )
                        if (
                            delivery.get("file_sha")
                            == delivery_record.get("file_sha")
                            and delivery.get("calculated") == calculated
                        ):
                            matching.append((source_path, dict(delivery)))
            if not matching:
                # Legacy callers could bind an arbitrary Renderer payload to a
                # whiteboard-draft acceptance.  Preserve that transaction API,
                # but never promote such an unverified value into the durable
                # projection recovery channel.
                return
            source_path, _delivery = matching[0]
            package = read_owned(
                source_path.parent,
                source_path.name,
                MAX_CALCULATED,
            )
            if not isinstance(package, Mapping):
                _fail("native_last_applied_whiteboard_projection_invalid")
            calculated_name = (
                f"{LAST_APPLIED_WHITEBOARD_CALCULATED_PREFIX}"
                f"{identity['application_payload_digest']}.json"
            )
            calculated_file_sha = fingerprint(package)

        self._write_immutable_projection_artifact(
            root,
            board_name,
            _application_document("whiteboard", board),
            MAX_NATIVE,
        )
        pinned_delivery = None
        pinned_path = None
        if package is not None and calculated_name is not None:
            self._write_immutable_projection_artifact(
                root,
                calculated_name,
                package,
                MAX_CALCULATED,
            )
            pinned_path = safe_path(root, calculated_name)
            pinned_delivery = whiteboard.read_whiteboard_result(
                board,
                pinned_path,
            )
            if (
                pinned_delivery.get("file_sha") != calculated_file_sha
                or pinned_delivery.get("calculated") != calculated
            ):
                _fail("native_last_applied_whiteboard_projection_invalid")

        now = _stamp()
        pointer = {
            "contract": LAST_APPLIED_WHITEBOARD_PROJECTION_CONTRACT,
            "conversation_id": conversation_id,
            "projection": {
                "board_id": board_id,
                "revision": identity["revision"],
                "receipt_fingerprint": identity["receipt_fingerprint"],
                "baseline_document_digest": intent["baseline_document_digest"],
                "baseline_payload_digest": intent["baseline_payload_digest"],
                "document_digest": identity["document_digest"],
                "application_payload_digest": identity[
                    "application_payload_digest"
                ],
                "application_kind": (
                    "whiteboard_calculated"
                    if calculated is not None
                    else "whiteboard_draft"
                ),
                "board_artifact": board_name,
                "calculated_artifact": calculated_name,
                "calculated_file_sha": calculated_file_sha,
                "proof": copy.deepcopy(dict(proof)),
                "committed_at": now,
            },
        }
        self._write_last_applied_whiteboard_projection(root, pointer)
        self._promote_exact_whiteboard_deliveries(
            conversation_id=conversation_id,
            row=pointer["projection"],
            board=board,
            delivery=pinned_delivery,
            result_path=pinned_path,
        )
        # The pointer is already durable.  Old immutable artifacts are now
        # unreachable and may be removed best-effort; failure only leaks space.
        if previous is not None:
            old = previous["projection"]
            for name in (old["board_artifact"], old["calculated_artifact"]):
                if name is None or name in {board_name, calculated_name}:
                    continue
                try:
                    safe_path(root, name).unlink(missing_ok=True)
                except (OSError, RuntimeError):
                    pass

    def _validate_candidate_acceptance(
        self,
        value: object,
        *,
        kind: str,
        expected_revision: int,
        expected_receipt: str,
        conversation_id: str,
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            _fail("native_candidate_acceptance_invalid")
        document_key = "plan" if kind == "native" else "board"
        result = dict(value)
        document = result.get(document_key)
        if (
            result.get("kind") != kind
            or result.get("revision") != expected_revision
            or result.get("receipt_fingerprint") != expected_receipt
            or not isinstance(document, Mapping)
        ):
            _fail("native_candidate_acceptance_invalid")
        # Do not surface candidate delivery paths or synthesize an
        # ``applied`` marker.  The renderer receives only the reviewed value
        # and performs its own current-document CAS before applying it.
        public_fields = {
            "kind",
            "workflow",
            "application_kind",
            "revision",
            "receipt_fingerprint",
            document_key,
        }
        projection = {
            key: copy.deepcopy(item)
            for key, item in result.items()
            if key in public_fields
        }
        projection[document_key] = copy.deepcopy(dict(document))
        result_path = result.get("result_path")
        if kind == "native":
            if result_path is not None:
                _fail("native_candidate_acceptance_invalid")
            return projection
        if result_path is None:
            if projection.get("application_kind") != "whiteboard_draft":
                _fail("native_candidate_acceptance_invalid")
            return projection
        if not isinstance(result_path, (str, os.PathLike)):
            _fail("native_candidate_acceptance_invalid")
        if projection.get("application_kind") not in {
            None,
            "whiteboard_calculated",
        }:
            _fail("native_candidate_acceptance_invalid")
        delivery = whiteboard.read_whiteboard_result(
            projection["board"],
            result_path,
        )
        if not isinstance(delivery, Mapping):
            _fail("native_candidate_acceptance_invalid")
        public_delivery = self._register_whiteboard_delivery(
            conversation_id=conversation_id,
            revision=expected_revision,
            receipt_fingerprint=expected_receipt,
            board=projection["board"],
            result_path=Path(delivery["path"]),
            delivery=delivery,
        )
        projection.setdefault("application_kind", "whiteboard_calculated")
        projection["calculated_delivery"] = public_delivery
        return projection

    def _expire_whiteboard_deliveries(self, conversation_id: str) -> None:
        for digest, record in tuple(self._whiteboard_deliveries.items()):
            if record.get("conversation_id") == conversation_id:
                self._whiteboard_deliveries.pop(digest, None)
