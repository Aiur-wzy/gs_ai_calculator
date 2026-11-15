"""App-owned candidate NativeCandidateApplicationBackend; no Session or Turn state."""
from __future__ import annotations

import copy
import hashlib
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from genshin_app.equipment.files import fingerprint

from .native_candidate_application_storage import NativeCandidateApplicationStorage
from .native_candidate_delivery_repository import NativeCandidateDeliveryRepository
from .native_candidate_transaction_contracts import (
    _APPLICATION_IDENTITY_FIELDS,
    _DIGEST,
    NativeServiceV2Error,
    _application_document_digest,
    _application_payload_digest,
    _fail,
    _identity,
)
from .native_document_restore_repository import NativeDocumentRestoreRepository
from .native_error_codes import reviewed_native_error_code
from .native_incompatible_application_retirements import (
    NativeIncompatibleApplicationRetirements,
)
from .native_whiteboard_delivery_consumers import NativeWhiteboardDeliveryConsumers
from .native_whiteboard_projection_recovery import NativeWhiteboardProjectionRecovery
from .sessions import safe_path


class NativeCandidateApplicationBackend(NativeCandidateApplicationStorage, NativeCandidateDeliveryRepository,
                                      NativeWhiteboardDeliveryConsumers, NativeWhiteboardProjectionRecovery):
    def __init__(self, root: Path, *, resolve_scope, lock=None, workspace_validator=None):
        self.root = Path(root).absolute()
        self._lock = lock or threading.RLock()
        self._resolve_scope = resolve_scope
        self._whiteboard_deliveries = {}
        self._document_restore_repository = NativeDocumentRestoreRepository(self.root)
        self.incompatible_applications_repository = NativeIncompatibleApplicationRetirements(
            self.root, intent_reader=self._read_application_intent,
            workspace_validator=workspace_validator, lock=self._lock)

    @staticmethod
    def _stable_error(error):
        return reviewed_native_error_code(error, fallback="native_service_failed")

    def _candidate_review_target(self, conversation_id):
        scope = self._resolve_scope(conversation_id)
        return scope, scope.candidate_session

    def _document_restore_store(self, conversation_id):
        scope, _ = self._candidate_review_target(conversation_id)
        domain = self._candidate_review_checkpoint(scope)
        return self._document_restore_repository.store(domain["document_id"]), domain

    def assert_restore_writable(self, conversation_id, *, operation):
        store, _ = self._document_restore_store(conversation_id)
        if store.path.exists() and store.write_gate()["writes_frozen"]:
            _fail("native_document_restore_in_progress", details={"operation": operation})

    def _assert_document_restore_writable(self, conversation_id, *, operation):
        self.assert_restore_writable(conversation_id, operation=operation)

    def _pending_application_owner(self):
        """The one Renderer effect is serialized independently of DSH Turns."""
        owners = []
        if not self.root.is_dir():
            return None
        for path in self.root.iterdir():
            # The new registry owns deterministic UUID workspace directories;
            # delivery and document-restore repositories are separate resources.
            if len(path.name) != 36 or not safe_path(path, "dsh-workspace.json").is_file():
                continue
            if path.is_symlink() or not path.is_dir():
                _fail("native_application_intent_invalid")
            intent = self._read_application_intent(path, path.name)["intent"]
            if intent is not None and intent["state"] in {"prepared", "baseline_advanced"}:
                if self.incompatible_applications_repository.is_retired(path, intent):
                    continue
                owners.append((path.name, intent["state"]))
        if len(owners) > 1:
            _fail("native_candidate_application_global_invalid")
        return owners[0] if owners else None

    @staticmethod
    def _same_application_identity(
        left: Mapping[str, Any],
        right: Mapping[str, Any],
    ) -> bool:
        return all(left.get(key) == right.get(key) for key in _APPLICATION_IDENTITY_FIELDS)

    @staticmethod
    def _application_proof(
        records: list[dict[str, Any]],
        identity: Mapping[str, Any],
        *,
        invalid_code: str,
    ) -> dict[str, Any] | None:
        """Resolve one proof without tolerating an identity/digest collision."""

        same_receipt = [
            row
            for row in records
            if (
                row["kind"] == identity["kind"]
                and row["revision"] == identity["revision"]
                and row["receipt_fingerprint"] == identity["receipt_fingerprint"]
            )
        ]
        if any(
            row["document_digest"] != identity["document_digest"]
            for row in same_receipt
        ):
            _fail(invalid_code)
        payload_digest = identity.get("application_payload_digest")
        if payload_digest is not None and any(
            row["application_payload_digest"] != payload_digest
            for row in same_receipt
        ):
            _fail(invalid_code)
        exact = same_receipt
        if payload_digest is not None:
            exact = [
                row
                for row in same_receipt
                if row["application_payload_digest"] == payload_digest
            ]
        if len(exact) > 1:
            _fail(invalid_code)
        return None if not exact else copy.deepcopy(exact[0])

    @staticmethod
    def _candidate_application_identity(
        candidate: object,
        summary: Mapping[str, Any],
        *,
        invalid_code: str,
        current_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Bind a review identity to the candidate's actual applied baseline."""

        document = getattr(candidate, "document", None)
        if not isinstance(document, Mapping):
            _fail(invalid_code)
        try:
            document_digest = _application_document_digest(summary["kind"], document)
        except Exception:  # noqa: BLE001 - candidate storage is an integrity boundary
            _fail(invalid_code)
        result = {
            "kind": summary["kind"],
            "revision": summary["revision"],
            "receipt_fingerprint": summary["receipt_fingerprint"],
            "document_digest": document_digest,
        }
        if current_payload is not None:
            try:
                result["application_payload_digest"] = _application_payload_digest(
                    summary["kind"],
                    document,
                    current_payload,
                )
            except NativeServiceV2Error:
                raise
            except Exception:  # noqa: BLE001 - renderer payload is untrusted
                _fail(invalid_code)
        return result

    def _validate_application_intent_for_binding(
        self,
        root: Path,
        conversation_id: str,
        binding: object,
        store: Mapping[str, Any],
    ) -> None:
        """Bind a durable intent to the exact candidate reopened by the Host."""

        intent = store.get("intent")
        candidate = getattr(binding, "candidate_session", None)
        if candidate is None or not callable(getattr(candidate, "summary", None)):
            _fail("native_application_intent_invalid")
        try:
            checkpoint = self._candidate_review_checkpoint_for_binding(binding)
            summary = self._validate_candidate_summary(
                candidate.summary(),
                checkpoint=checkpoint,
            )
        except NativeServiceV2Error:
            raise
        except Exception:  # noqa: BLE001 - reopen is a storage trust boundary
            _fail("native_application_intent_invalid")
        current_identity = self._candidate_application_identity(
            candidate,
            summary,
            invalid_code="native_applied_proof_invalid",
        )
        records = self._read_applied_proofs(root, conversation_id)["records"]
        # Resolve the proof against the actual reopened baseline, rather than
        # revision/receipt alone.  This also rejects conflicting or duplicate
        # proof rows even after a terminal intent has been cleared.
        self._application_proof(
            records,
            current_identity,
            invalid_code="native_applied_proof_invalid",
        )
        if intent is None:
            return
        if (
            intent["kind"] != summary["kind"]
            or intent["revision"] != summary["revision"]
            or intent["receipt_fingerprint"] != summary["receipt_fingerprint"]
        ):
            _fail("native_application_intent_invalid")
        proof = self._application_proof(
            records,
            intent,
            invalid_code="native_application_intent_invalid",
        )
        state = intent["state"]
        actual_digest = current_identity["document_digest"]
        baseline_digest = intent["baseline_document_digest"]
        target_digest = intent["document_digest"]
        if (
            state == "prepared"
            and (actual_digest not in {baseline_digest, target_digest} or proof is not None)
            or state == "baseline_advanced"
            and actual_digest != target_digest
            or state == "committed"
            and (actual_digest != target_digest or proof is None)
            or state == "aborted"
            and (actual_digest != baseline_digest or proof is not None)
        ):
            _fail("native_application_intent_invalid")

    @staticmethod
    def _candidate_review_checkpoint_for_binding(
        binding: object,
    ) -> dict[str, Any]:
        """Validate a binding checkpoint before it is published as active."""

        value = binding.domain_checkpoint()
        if not isinstance(value, Mapping) or set(value) != {
            "document_id",
            "candidate_revision",
            "stage_revision",
            "database_fingerprint",
            "receipt_fingerprint",
        }:
            _fail("native_candidate_checkpoint_invalid")
        result = copy.deepcopy(dict(value))
        _identity(result["document_id"], "document_id")
        if (
            type(result["candidate_revision"]) is not int
            or not 0 <= result["candidate_revision"] <= 2**53 - 1
            or type(result["stage_revision"]) is not int
            or not 0 <= result["stage_revision"] <= 2**53 - 1
            or not isinstance(result["database_fingerprint"], str)
            or _DIGEST.fullmatch(result["database_fingerprint"]) is None
            or not (
                result["receipt_fingerprint"] is None
                or (
                    isinstance(result["receipt_fingerprint"], str)
                    and _DIGEST.fullmatch(result["receipt_fingerprint"])
                    is not None
                )
            )
        ):
            _fail("native_candidate_checkpoint_invalid")
        return result

    @staticmethod
    def _validate_candidate_summary(
        value: object,
        *,
        checkpoint: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            _fail("native_candidate_summary_invalid")
        result = copy.deepcopy(dict(value))
        kind = result.get("kind")
        revision = result.get("revision")
        receipt = result.get("receipt_fingerprint")
        if (
            kind not in {"native", "whiteboard"}
            or type(revision) is not int
            or revision != checkpoint["candidate_revision"]
            or not (
                receipt is None
                or (
                    isinstance(receipt, str)
                    and _DIGEST.fullmatch(receipt) is not None
                )
            )
            or receipt != checkpoint["receipt_fingerprint"]
        ):
            _fail("native_candidate_summary_invalid")
        return result

    @staticmethod
    def _document_restore_kind(kind: str) -> str:
        if kind == "native":
            return "native_plan"
        if kind == "whiteboard":
            return "whiteboard"
        _fail("native_document_restore_kind_invalid")

    @staticmethod
    def _document_restore_application_payload(
        kind: str,
        document: Mapping[str, Any],
        payload: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if payload is not None:
            if not isinstance(payload, Mapping):
                _fail("native_candidate_document_invalid")
            return copy.deepcopy(dict(payload))
        if kind == "native":
            return {"kind": "native", "plan": copy.deepcopy(dict(document))}
        if kind == "whiteboard":
            return {
                "kind": "whiteboard",
                "board": copy.deepcopy(dict(document)),
                "calculated": None,
            }
        _fail("native_document_restore_kind_invalid")

    def _document_restore_fingerprints(
        self,
        bundle: object,
        *,
        kind: str,
    ) -> dict[str, str | None]:
        domain = self._candidate_review_checkpoint(bundle)
        database = domain.get("database_fingerprint")
        if database is not None and (
            not isinstance(database, str) or _DIGEST.fullmatch(database) is None
        ):
            _fail("native_candidate_checkpoint_invalid")
        engine = bundle.binding.tool_manifest_hash()
        if not isinstance(engine, str) or _DIGEST.fullmatch(engine) is None:
            _fail("native_tool_manifest_changed")
        schema = hashlib.sha256(
            f"genshin-app-document-schema-v1:{kind}".encode()
        ).hexdigest()
        return {"database": database, "engine": engine, "schema": schema}

    def synchronize_document_restore_baseline(
        self,
        conversation_id: str,
        *,
        kind: str,
        document: Mapping[str, Any],
        application_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Create or monotonically advance the restore store App baseline."""

        if not isinstance(document, Mapping) or not isinstance(
            application_payload, Mapping
        ):
            _fail("native_candidate_document_invalid")
        checked_document = copy.deepcopy(dict(document))
        checked_payload = copy.deepcopy(dict(application_payload))
        internal_kind = self._document_restore_kind(kind)
        with self._lock:
            bundle, _candidate = self._candidate_review_target(conversation_id)
            store, _record = self._document_restore_store(conversation_id)
            domain = self._candidate_review_checkpoint(bundle)
            document_id = _identity(domain["document_id"], "document_id")
            fingerprints = self._document_restore_fingerprints(bundle, kind=kind)
            if not store.path.exists():
                return store.initialize(
                    document_kind=internal_kind,
                    document=checked_document,
                    application_payload=checked_payload,
                    document_lineage_epoch=0,
                    revision=0,
                    fingerprints=fingerprints,
                )
            current_store = store.read()
            if current_store["document_id"] != document_id:
                _fail("native_workbench_document_binding_invalid")
            current = current_store["current"]
            if (
                current["document_kind"] == internal_kind
                and current["document_artifact"]["sha256"]
                == fingerprint(checked_document)
                and current["application_payload_artifact"]["sha256"]
                == fingerprint(checked_payload)
                and current["fingerprints"] == fingerprints
            ):
                return {
                    "status": "already_current",
                    "store_revision": current_store["store_revision"],
                    "current": copy.deepcopy(current),
                }
            return store.record_document_revision(
                expected_store_revision=current_store["store_revision"],
                expected_lineage_epoch=current["document_lineage_epoch"],
                expected_document_revision=current["revision"],
                revision=current["revision"] + 1,
                document=checked_document,
                application_payload=checked_payload,
                fingerprints=fingerprints,
            )

    def _resume_document_unchecked(
        self,
        conversation_id: str,
        document: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Advance the candidate baseline inside the application transaction."""

        current_document = copy.deepcopy(dict(document))
        with self._lock:
            bundle, candidate = self._candidate_review_target(conversation_id)
            resume = getattr(candidate, "resume_document", None)
            boundary = getattr(bundle.binding, "domain_write_boundary", None)
            if not callable(resume):
                _fail("native_candidate_not_ready")
            if not callable(boundary):
                _fail("native_app_binding_invalid")
            try:
                with boundary():
                    checkpoint = self._candidate_review_checkpoint(bundle)
                    tool_manifest = bundle.binding.tool_manifest_hash()
                    result = resume(current_document)
                    after = self._candidate_review_checkpoint(bundle)
                    after_tool_manifest = bundle.binding.tool_manifest_hash()
            except NativeServiceV2Error:
                raise
            except Exception as error:  # noqa: BLE001 - renderer-safe boundary
                raise NativeServiceV2Error(self._stable_error(error)) from None
            if after != checkpoint:
                _fail("native_candidate_changed")
            if after_tool_manifest != tool_manifest:
                _fail("native_tool_manifest_changed")
            return self._validate_candidate_summary(result, checkpoint=after)

    @staticmethod
    def _candidate_review_checkpoint(
        bundle: object,
    ) -> dict[str, Any]:
        value = bundle.binding.domain_checkpoint()
        if not isinstance(value, Mapping) or set(value) != {
            "document_id",
            "candidate_revision",
            "stage_revision",
            "database_fingerprint",
            "receipt_fingerprint",
        }:
            _fail("native_candidate_checkpoint_invalid")
        result = copy.deepcopy(dict(value))
        _identity(result["document_id"], "document_id")
        if (
            type(result["candidate_revision"]) is not int
            or not 0 <= result["candidate_revision"] <= 2**53 - 1
            or type(result["stage_revision"]) is not int
            or not 0 <= result["stage_revision"] <= 2**53 - 1
            or not isinstance(result["database_fingerprint"], str)
            or _DIGEST.fullmatch(result["database_fingerprint"]) is None
            or not (
                result["receipt_fingerprint"] is None
                or (
                    isinstance(result["receipt_fingerprint"], str)
                    and _DIGEST.fullmatch(result["receipt_fingerprint"])
                    is not None
                )
            )
        ):
            _fail("native_candidate_checkpoint_invalid")
        return result
