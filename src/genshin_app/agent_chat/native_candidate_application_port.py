"""Typed transaction port for reviewed candidate application.

The active App binding, candidate session, intent/proof stores and document
restore repository remain the only state owners.  This port owns the command
ordering from candidate review through Renderer acknowledgement, cancellation
and crash recovery while the ``NativeDomainSessionRegistry`` holds the Service
authority lock.  It never persists a parallel transaction projection.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Mapping
from typing import Any, Protocol, runtime_checkable

from genshin_app.equipment.files import fingerprint, input_view

from .native_domain_session_registry import (
    NativeDomainSessionHandle,
    NativeDomainSessionRegistry,
    NativeDomainSessionRegistryError,
)
from .native_error_codes import reviewed_native_error_code

_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_APPLICATION_IDENTITY_FIELDS = (
    "kind",
    "revision",
    "receipt_fingerprint",
    "document_digest",
    "application_payload_digest",
)


class NativeCandidateApplicationError(RuntimeError):
    """Stable, renderer-safe failure from the application transaction port."""

    def __init__(
        self,
        code: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.details = copy.deepcopy(dict(details or {}))


def _fail(
    code: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> None:
    raise NativeCandidateApplicationError(code, details=details)


def _require_settled_file_actions(controller):
    if controller.execution_pending():
        pending = next((card for card in controller.list_cards()
                        if card["status"] == "claimed_for_external_execution"), None)
        _fail("native_app_action_busy", details={"action_id": pending["action_id"]} if pending else None)


def _application_document(kind: str, document: Mapping[str, Any]) -> dict[str, Any]:
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
    if kind not in {"native", "whiteboard"} or not isinstance(document, Mapping):
        _fail("native_candidate_application_payload_invalid")
    projected = _application_document(kind, document)
    if payload is None:
        result: dict[str, Any] = (
            {"kind": "native", "plan": copy.deepcopy(dict(document))}
            if kind == "native"
            else {
                "kind": "whiteboard",
                "board": copy.deepcopy(projected),
                "calculated": None,
            }
        )
    elif isinstance(payload, Mapping):
        result = copy.deepcopy(dict(payload))
    else:
        _fail("native_candidate_application_payload_invalid")
    if kind == "native":
        if (
            set(result) != {"kind", "plan"}
            or result.get("kind") != "native"
            or not isinstance(result.get("plan"), Mapping)
            or input_view(dict(result["plan"])) != projected
        ):
            _fail("native_candidate_application_payload_invalid")
        result["plan"] = copy.deepcopy(dict(result["plan"]))
    elif (
        set(result) != {"kind", "board", "calculated"}
        or result.get("kind") != "whiteboard"
        or not isinstance(result.get("board"), Mapping)
        or dict(result["board"]) != projected
        or result.get("calculated") is not None
        and not isinstance(result.get("calculated"), Mapping)
    ):
        _fail("native_candidate_application_payload_invalid")
    else:
        result["board"] = copy.deepcopy(dict(result["board"]))
        if isinstance(result["calculated"], Mapping):
            result["calculated"] = copy.deepcopy(dict(result["calculated"]))
    try:
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


def _candidate(handle: NativeDomainSessionHandle) -> object:
    binding = handle.binding
    candidate = getattr(binding, "candidate_session", None)
    if candidate is None or candidate is not getattr(binding, "candidate_session", None):
        _fail("native_app_binding_invalid")
    for name in ("summary", "call_tool", "accept"):
        if not callable(getattr(candidate, name, None)):
            _fail("native_candidate_not_ready")
    return candidate


def _target_payload(
    kind: str,
    accepted: Mapping[str, Any],
) -> dict[str, Any]:
    key = "plan" if kind == "native" else "board"
    document = accepted.get(key)
    if not isinstance(document, Mapping):
        _fail("native_candidate_acceptance_invalid")
    if kind == "native":
        return _application_payload(kind, document, {"kind": kind, "plan": document})
    delivery = accepted.get("calculated_delivery")
    calculated = delivery.get("calculated") if isinstance(delivery, Mapping) else None
    return _application_payload(
        kind,
        document,
        {"kind": kind, "board": document, "calculated": calculated},
    )


@runtime_checkable
class NativeCandidateApplicationPortProtocol(Protocol):
    def accept(
        self,
        conversation_id: str,
        kind: str,
        document: Mapping[str, Any],
        expected_revision: int,
        expected_receipt: str,
    ) -> dict[str, Any]: ...

    def prepare_candidate_application(
        self,
        conversation_id: str,
        kind: str,
        document: Mapping[str, Any],
        expected_revision: int,
        expected_receipt: str,
        *,
        baseline_payload: Mapping[str, Any] | None = None,
        target_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def record_candidate_applied(
        self,
        conversation_id: str,
        kind: str,
        document: Mapping[str, Any],
        expected_revision: int,
        expected_receipt: str,
        *,
        actual_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def abort_candidate_application(
        self,
        conversation_id: str,
        kind: str,
        current_document: Mapping[str, Any],
        expected_revision: int,
        expected_receipt: str,
        *,
        current_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def recover_candidate_application(
        self,
        conversation_id: str,
        current_document: Mapping[str, Any],
        *,
        current_payload: Mapping[str, Any] | None = None,
        discard_current: bool = False,
    ) -> dict[str, Any]: ...

    def candidate_application_intent(
        self, conversation_id: str
    ) -> dict[str, Any]: ...

    def applied_candidate_proof(
        self,
        conversation_id: str,
        *,
        current_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class NativeCandidateApplicationPort(NativeCandidateApplicationPortProtocol):
    """Run the complete candidate application state machine over existing owners."""

    def __init__(
        self,
        registry: NativeDomainSessionRegistry,
        *,
        backend: object,
        restore_guard: Callable[..., None],
        timeline_sink: Callable[..., None],
        error_factory: Callable[..., BaseException] | None = None,
    ) -> None:
        if not isinstance(registry, NativeDomainSessionRegistry):
            raise TypeError("native_domain_session_registry_invalid")
        if backend is None or not callable(restore_guard) or not callable(timeline_sink):
            raise TypeError("native_candidate_application_port_dependency_invalid")
        self._registry = registry
        self._backend = backend
        self._restore_guard = restore_guard
        self._timeline_sink = timeline_sink
        self._error_factory = error_factory or NativeCandidateApplicationError

    def _project_error(self, error: object) -> BaseException:
        code = reviewed_native_error_code(error, fallback="native_service_failed")
        details = getattr(error, "details", None)
        try:
            projected = self._error_factory(code, details=details)
        except TypeError:
            projected = self._error_factory(code)
        if not isinstance(projected, BaseException):
            raise TypeError("native_candidate_application_error_factory_invalid")
        return projected

    def _run(self, operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return operation()
        except (NativeCandidateApplicationError, NativeDomainSessionRegistryError) as error:
            raise self._project_error(error) from None

    def _checkpoint(self, handle: NativeDomainSessionHandle) -> dict[str, Any]:
        return self._backend._candidate_review_checkpoint_for_binding(handle.binding)

    def _summary(
        self,
        handle: NativeDomainSessionHandle,
        candidate: object,
    ) -> dict[str, Any]:
        return self._backend._validate_candidate_summary(
            candidate.summary(), checkpoint=self._checkpoint(handle)
        )

    @staticmethod
    def _validate_identity(kind: str, revision: int, receipt: str) -> None:
        if kind not in {"native", "whiteboard"}:
            _fail("native_candidate_kind_invalid")
        if type(revision) is not int or not 0 <= revision <= 2**53 - 1:
            _fail("native_candidate_revision_invalid")
        if not isinstance(receipt, str) or _DIGEST.fullmatch(receipt) is None:
            _fail("native_candidate_receipt_invalid")

    @staticmethod
    def _same_identity(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
        return all(left.get(key) == right.get(key) for key in _APPLICATION_IDENTITY_FIELDS)

    def _proof(
        self,
        records: list[dict[str, Any]],
        identity: Mapping[str, Any],
        *,
        invalid_code: str,
    ) -> dict[str, Any] | None:
        return self._backend._application_proof(
            records, identity, invalid_code=invalid_code
        )

    def _timeline(
        self,
        conversation_id: str,
        *,
        label: str,
        state: str,
        operation: str,
        kind: str,
        revision: int,
        receipt: str,
        extra: tuple[Any, ...] = (),
    ) -> None:
        self._timeline_sink(
            conversation_id,
            kind="candidate",
            category="candidate",
            label=label,
            state=state,
            operation_id=f"candidate:{kind}:{revision}:{receipt[:16]}",
            identity=(operation, kind, revision, receipt, *extra),
        )

    def accept(
        self,
        conversation_id: str,
        kind: str,
        document: Mapping[str, Any],
        expected_revision: int,
        expected_receipt: str,
    ) -> dict[str, Any]:
        self._restore_guard(conversation_id, operation="candidate_accept")
        return self._run(
            lambda: self._accept(
                conversation_id,
                kind,
                document,
                expected_revision,
                expected_receipt,
            )
        )

    def _accept(
        self,
        conversation_id: str,
        kind: str,
        document: Mapping[str, Any],
        expected_revision: int,
        expected_receipt: str,
    ) -> dict[str, Any]:
        self._validate_identity(kind, expected_revision, expected_receipt)
        if not isinstance(document, Mapping):
            _fail("native_candidate_document_invalid")
        current_document = copy.deepcopy(dict(document))
        with self._registry.action_scope(conversation_id) as handle:
            candidate = _candidate(handle)
            boundary = getattr(handle.binding, "domain_write_boundary", None)
            if not callable(boundary):
                _fail("native_app_binding_invalid")
            try:
                with boundary():
                    checkpoint = self._checkpoint(handle)
                    summary = self._backend._validate_candidate_summary(
                        candidate.summary(), checkpoint=checkpoint
                    )
                    if kind != summary["kind"]:
                        _fail("agent_document_changed")
                    if expected_revision != summary["revision"]:
                        _fail("agent_stale_revision")
                    if expected_receipt != summary["receipt_fingerprint"]:
                        _fail("agent_stale_receipt")
                    self._backend._expire_whiteboard_deliveries(conversation_id)
                    accepted = candidate.accept(
                        kind,
                        current_document,
                        expected_revision,
                        expected_receipt,
                    )
                    projected = self._backend._validate_candidate_acceptance(
                        accepted,
                        kind=kind,
                        expected_revision=expected_revision,
                        expected_receipt=expected_receipt,
                        conversation_id=conversation_id,
                    )
                    after = self._checkpoint(handle)
            except NativeCandidateApplicationError:
                raise
            except Exception as error:  # noqa: BLE001 - renderer-safe boundary
                raise self._project_error(error) from None
            if after != checkpoint:
                _fail("native_candidate_changed")
            projected["app_actions"] = copy.deepcopy(
                handle.action_controller.list_cards(include_terminal=True)
            )
            self._timeline(
                conversation_id,
                label="候选已通过审核",
                state="ready_for_application",
                operation="candidate-accept",
                kind=kind,
                revision=expected_revision,
                receipt=expected_receipt,
            )
            return projected

    def prepare_candidate_application(
        self,
        conversation_id: str,
        kind: str,
        document: Mapping[str, Any],
        expected_revision: int,
        expected_receipt: str,
        *,
        baseline_payload: Mapping[str, Any] | None = None,
        target_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._restore_guard(
            conversation_id, operation="candidate_application_prepare"
        )
        return self._run(
            lambda: self._prepare(
                conversation_id,
                kind,
                document,
                expected_revision,
                expected_receipt,
                baseline_payload=baseline_payload,
                target_payload=target_payload,
            )
        )

    def _prepare(
        self,
        conversation_id: str,
        kind: str,
        document: Mapping[str, Any],
        expected_revision: int,
        expected_receipt: str,
        *,
        baseline_payload: Mapping[str, Any] | None,
        target_payload: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        self._validate_identity(kind, expected_revision, expected_receipt)
        if not isinstance(document, Mapping):
            _fail("native_candidate_document_invalid")
        requested = {
            "kind": kind,
            "revision": expected_revision,
            "receipt_fingerprint": expected_receipt,
            "document_digest": _application_document_digest(kind, document),
            "application_payload_digest": _application_payload_digest(
                kind, document, target_payload
            ),
        }
        with self._registry.action_scope(conversation_id) as handle:
            pending_owner = self._backend._pending_application_owner()
            if pending_owner is not None and pending_owner[0] != conversation_id:
                _fail(
                    "native_candidate_application_global_busy",
                    details={"conversation_id": pending_owner[0]},
                )
            candidate = _candidate(handle)
            _require_settled_file_actions(handle.action_controller)
            current = self._summary(handle, candidate)
            if current["kind"] != kind:
                _fail("agent_document_changed")
            if current["revision"] != expected_revision:
                _fail("agent_stale_revision")
            if current["receipt_fingerprint"] != expected_receipt:
                _fail("agent_stale_receipt")
            baseline_document = getattr(candidate, "document", None)
            if not isinstance(baseline_document, Mapping):
                _fail("native_candidate_checkpoint_invalid")
            baseline_document_digest = _application_document_digest(
                kind, baseline_document
            )
            baseline_payload_digest = _application_payload_digest(
                kind, baseline_document, baseline_payload
            )
            root = handle.session_root
            store = self._backend._read_application_intent(root, conversation_id)
            intent = store["intent"]
            proofs = self._backend._read_applied_proofs(
                root, conversation_id
            )["records"]
            proof = self._proof(
                proofs, requested, invalid_code="native_applied_proof_invalid"
            )
            if intent is not None:
                if intent["state"] == "aborted":
                    if proof is not None:
                        _fail("native_application_intent_invalid")
                    intent = None
                elif self._same_identity(intent, requested):
                    if intent["state"] == "committed" and proof is None:
                        _fail("native_application_intent_invalid")
                    result = {
                        "conversation_id": conversation_id,
                        "status": intent["state"],
                        "intent": copy.deepcopy(intent),
                    }
                    self._timeline(
                        conversation_id,
                        label="候选应用已准备",
                        state=result["status"],
                        operation="candidate-prepare",
                        kind=kind,
                        revision=expected_revision,
                        receipt=expected_receipt,
                        extra=(requested["application_payload_digest"],),
                    )
                    return result
                else:
                    same_candidate_key = all(
                        intent[key] == requested[key]
                        for key in ("kind", "revision", "receipt_fingerprint")
                    )
                    if intent["state"] != "committed" or same_candidate_key:
                        _fail("native_candidate_application_conflict")
                    if self._proof(
                        proofs,
                        intent,
                        invalid_code="native_application_intent_invalid",
                    ) is None:
                        _fail("native_application_intent_invalid")
            now = self._backend._application_timestamp()
            prepared = {
                **requested,
                "baseline_document_digest": baseline_document_digest,
                "baseline_payload_digest": baseline_payload_digest,
                "state": "committed" if proof is not None else "prepared",
                "created_at": now,
                "updated_at": now,
            }
            store["intent"] = prepared
            self._backend._write_application_intent(root, store)
            result = {
                "conversation_id": conversation_id,
                "status": prepared["state"],
                "intent": copy.deepcopy(prepared),
            }
            self._timeline(
                conversation_id,
                label="候选应用已准备",
                state=result["status"],
                operation="candidate-prepare",
                kind=kind,
                revision=expected_revision,
                receipt=expected_receipt,
                extra=(requested["application_payload_digest"],),
            )
            return result

    def record_candidate_applied(
        self,
        conversation_id: str,
        kind: str,
        document: Mapping[str, Any],
        expected_revision: int,
        expected_receipt: str,
        *,
        actual_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._restore_guard(conversation_id, operation="candidate_application_ack")
        return self._run(
            lambda: self._record_applied(
                conversation_id,
                kind,
                document,
                expected_revision,
                expected_receipt,
                actual_payload=actual_payload,
            )
        )

    def _record_applied(
        self,
        conversation_id: str,
        kind: str,
        document: Mapping[str, Any],
        expected_revision: int,
        expected_receipt: str,
        *,
        actual_payload: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        self._validate_identity(kind, expected_revision, expected_receipt)
        if not isinstance(document, Mapping):
            _fail("native_candidate_document_invalid")
        detached = copy.deepcopy(dict(document))
        payload_digest = _application_payload_digest(kind, detached, actual_payload)
        requested = {
            "kind": kind,
            "revision": expected_revision,
            "receipt_fingerprint": expected_receipt,
            "document_digest": _application_document_digest(kind, detached),
            "application_payload_digest": payload_digest,
        }
        with self._registry.action_scope(conversation_id) as handle:
            pending_owner = self._backend._pending_application_owner()
            if pending_owner is not None and pending_owner[0] != conversation_id:
                _fail(
                    "native_candidate_application_global_busy",
                    details={"conversation_id": pending_owner[0]},
                )
            candidate = _candidate(handle)
            _require_settled_file_actions(handle.action_controller)
            current = self._summary(handle, candidate)
            if current["kind"] != kind:
                _fail("agent_document_changed")
            if current["revision"] != expected_revision:
                _fail("agent_stale_revision")
            if current["receipt_fingerprint"] != expected_receipt:
                _fail("agent_stale_receipt")
            root = handle.session_root
            intent_store = self._backend._read_application_intent(
                root, conversation_id
            )
            intent = intent_store["intent"]
            proof_store = self._backend._read_applied_proofs(root, conversation_id)
            proof = self._proof(
                proof_store["records"],
                requested,
                invalid_code="native_applied_proof_invalid",
            )
            if intent is None:
                _fail("native_candidate_application_not_prepared")
            if not self._same_identity(intent, requested):
                _fail("native_candidate_application_conflict")
            if intent["state"] == "aborted":
                _fail("native_candidate_application_not_prepared")
            if intent["state"] == "committed":
                if proof is None:
                    _fail("native_application_intent_invalid")
                if kind == "whiteboard":
                    self._backend._persist_last_applied_whiteboard_projection(
                        root,
                        conversation_id,
                        bundle=handle,
                        candidate=candidate,
                        intent=intent,
                        proof=proof,
                        board=detached,
                        payload=actual_payload,
                    )
                self._sync_restore(
                    conversation_id, kind, detached, actual_payload
                )
                result = {
                    "status": "applied",
                    "proof": copy.deepcopy(proof),
                    "summary": copy.deepcopy(current),
                }
                self._timeline(
                    conversation_id,
                    label="候选已应用到文档",
                    state="applied",
                    operation="candidate-applied",
                    kind=kind,
                    revision=expected_revision,
                    receipt=expected_receipt,
                    extra=(payload_digest,),
                )
                return result

            summary = self._backend._resume_document_unchecked(
                conversation_id, detached
            )
            if (
                summary.get("kind") != kind
                or summary.get("revision") != expected_revision
                or summary.get("receipt_fingerprint") != expected_receipt
            ):
                _fail("native_candidate_resume_invalid")
            if intent["state"] == "prepared":
                intent = {
                    **intent,
                    "state": "baseline_advanced",
                    "updated_at": self._backend._application_timestamp(),
                }
                intent_store["intent"] = intent
                self._backend._write_application_intent(root, intent_store)
            if proof is None:
                proof = {**requested, "applied_at": self._backend._application_timestamp()}
                proof_store["records"] = [
                    *proof_store["records"],
                    proof,
                ][-self._backend._max_applied_proof_records():]
                self._backend._write_applied_proofs(root, proof_store)
            if kind == "whiteboard":
                self._backend._persist_last_applied_whiteboard_projection(
                    root,
                    conversation_id,
                    bundle=handle,
                    candidate=candidate,
                    intent=intent,
                    proof=proof,
                    board=detached,
                    payload=actual_payload,
                )
            intent = {
                **intent,
                "state": "committed",
                "updated_at": self._backend._application_timestamp(),
            }
            intent_store["intent"] = intent
            self._backend._write_application_intent(root, intent_store)
            self._sync_restore(conversation_id, kind, detached, actual_payload)
            result = {
                "status": "applied",
                "proof": copy.deepcopy(proof),
                "summary": copy.deepcopy(summary),
            }
            self._timeline(
                conversation_id,
                label="候选已应用到文档",
                state="applied",
                operation="candidate-applied",
                kind=kind,
                revision=expected_revision,
                receipt=expected_receipt,
                extra=(payload_digest,),
            )
            return result

    def _sync_restore(
        self,
        conversation_id: str,
        kind: str,
        document: Mapping[str, Any],
        payload: Mapping[str, Any] | None,
    ) -> None:
        self._backend.synchronize_document_restore_baseline(
            conversation_id,
            kind=kind,
            document=document,
            application_payload=self._backend._document_restore_application_payload(
                kind, document, payload
            ),
        )

    def abort_candidate_application(
        self,
        conversation_id: str,
        kind: str,
        current_document: Mapping[str, Any],
        expected_revision: int,
        expected_receipt: str,
        *,
        current_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._restore_guard(conversation_id, operation="candidate_application_abort")
        return self._run(
            lambda: self._abort(
                conversation_id,
                kind,
                current_document,
                expected_revision,
                expected_receipt,
                current_payload=current_payload,
            )
        )

    def _abort(
        self,
        conversation_id: str,
        kind: str,
        current_document: Mapping[str, Any],
        expected_revision: int,
        expected_receipt: str,
        *,
        current_payload: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        self._validate_identity(kind, expected_revision, expected_receipt)
        if not isinstance(current_document, Mapping):
            _fail("native_candidate_document_invalid")
        with self._registry.action_scope(conversation_id) as handle:
            candidate = _candidate(handle)
            _require_settled_file_actions(handle.action_controller)
            current = self._summary(handle, candidate)
            if current["kind"] != kind:
                _fail("agent_document_changed")
            if current["revision"] != expected_revision:
                _fail("agent_stale_revision")
            if current["receipt_fingerprint"] != expected_receipt:
                _fail("agent_stale_receipt")
            root = handle.session_root
            store = self._backend._read_application_intent(root, conversation_id)
            intent = store["intent"]
            if intent is None:
                _fail("native_candidate_application_not_prepared")
            if (
                intent["kind"] != kind
                or intent["revision"] != expected_revision
                or intent["receipt_fingerprint"] != expected_receipt
            ):
                _fail("native_candidate_application_conflict")
            baseline = getattr(candidate, "document", None)
            if not isinstance(baseline, Mapping):
                _fail("native_candidate_application_abort_unsafe")
            baseline_digest = _application_document_digest(kind, baseline)
            if (
                _application_document_digest(kind, current_document) != baseline_digest
                or baseline_digest != intent["baseline_document_digest"]
                or _application_payload_digest(
                    kind, current_document, current_payload
                )
                != intent["baseline_payload_digest"]
            ):
                _fail("native_candidate_application_abort_unsafe")
            proof = self._proof(
                self._backend._read_applied_proofs(root, conversation_id)["records"],
                intent,
                invalid_code="native_application_intent_invalid",
            )
            if intent["state"] == "aborted":
                if proof is not None:
                    _fail("native_application_intent_invalid")
                result = {
                    "conversation_id": conversation_id,
                    "status": "aborted",
                    "intent": copy.deepcopy(intent),
                }
            else:
                if intent["state"] != "prepared" or proof is not None:
                    _fail("native_candidate_application_abort_unsafe")
                store["intent"] = {
                    **intent,
                    "state": "aborted",
                    "updated_at": self._backend._application_timestamp(),
                }
                self._backend._write_application_intent(root, store)
                result = {
                    "conversation_id": conversation_id,
                    "status": "aborted",
                    "intent": copy.deepcopy(store["intent"]),
                }
            self._timeline(
                conversation_id,
                label="候选应用已安全取消",
                state="aborted",
                operation="candidate-abort",
                kind=kind,
                revision=expected_revision,
                receipt=expected_receipt,
            )
            return result

    def recover_candidate_application(
        self,
        conversation_id: str,
        current_document: Mapping[str, Any],
        *,
        current_payload: Mapping[str, Any] | None = None,
        discard_current: bool = False,
    ) -> dict[str, Any]:
        self._restore_guard(conversation_id, operation="candidate_application_recover")
        return self._run(
            lambda: self._recover(
                conversation_id,
                current_document,
                current_payload=current_payload,
                discard_current=discard_current,
            )
        )

    def _recover(
        self,
        conversation_id: str,
        current_document: Mapping[str, Any],
        *,
        current_payload: Mapping[str, Any] | None,
        discard_current: bool,
    ) -> dict[str, Any]:
        if not isinstance(current_document, Mapping):
            _fail("native_candidate_document_invalid")
        if type(discard_current) is not bool or (
            discard_current and not isinstance(current_payload, Mapping)
        ):
            _fail("native_candidate_application_recovery_unsafe")
        detached = copy.deepcopy(dict(current_document))
        with self._registry.action_scope(conversation_id) as handle:
            pending_owner = self._backend._pending_application_owner()
            if pending_owner is not None and pending_owner[0] != conversation_id:
                _fail(
                    "native_candidate_application_global_busy",
                    details={"conversation_id": pending_owner[0]},
                )
            candidate = _candidate(handle)
            _require_settled_file_actions(handle.action_controller)
            root = handle.session_root
            intent_store = self._backend._read_application_intent(
                root, conversation_id
            )
            self._backend._validate_application_intent_for_binding(
                root, conversation_id, handle.binding, intent_store
            )
            intent = intent_store["intent"]
            if not isinstance(intent, Mapping):
                _fail("native_candidate_application_not_prepared")
            state = intent.get("state")
            if state in {"prepared", "baseline_advanced"}:
                if pending_owner is None or pending_owner[0] != conversation_id:
                    _fail("native_application_intent_invalid")
            elif state == "committed" and intent.get("kind") == "whiteboard":
                if pending_owner is not None:
                    _fail("native_application_intent_invalid")
            else:
                _fail("native_candidate_application_not_prepared")
            kind = intent["kind"]
            if state in {"prepared", "baseline_advanced"}:
                if discard_current:
                    _application_payload(kind, detached, current_payload)
                elif (
                    _application_document_digest(kind, detached)
                    != intent["baseline_document_digest"]
                    or _application_payload_digest(kind, detached, current_payload)
                    != intent["baseline_payload_digest"]
                ):
                    _fail("native_candidate_application_recovery_unsafe")
            else:
                if discard_current:
                    _fail("native_candidate_application_recovery_unsafe")
                empty_payload = {
                    "kind": "whiteboard",
                    "board": _application_document("whiteboard", detached),
                    "calculated": None,
                }
                if (
                    _application_document_digest(kind, detached)
                    != intent["document_digest"]
                    or _application_payload(kind, detached, current_payload)
                    != empty_payload
                    or fingerprint(empty_payload)
                    == intent["application_payload_digest"]
                ):
                    _fail("native_candidate_application_recovery_unsafe")
                if self._proof(
                    self._backend._read_applied_proofs(root, conversation_id)[
                        "records"
                    ],
                    intent,
                    invalid_code="native_application_intent_invalid",
                ) is None:
                    _fail("native_application_intent_invalid")
            boundary = getattr(handle.binding, "domain_write_boundary", None)
            if not callable(boundary):
                _fail("native_app_binding_invalid")
            try:
                with boundary():
                    checkpoint = self._checkpoint(handle)
                    summary = self._backend._validate_candidate_summary(
                        candidate.summary(), checkpoint=checkpoint
                    )
                    if (
                        summary["kind"] != kind
                        or summary["revision"] != intent["revision"]
                        or summary["receipt_fingerprint"]
                        != intent["receipt_fingerprint"]
                    ):
                        _fail("native_application_intent_invalid")
                    candidate_document = getattr(candidate, "document", None)
                    if not isinstance(candidate_document, Mapping):
                        _fail("native_candidate_checkpoint_invalid")
                    if discard_current and (
                        _application_document_id(kind, detached)
                        != _application_document_id(kind, candidate_document)
                        or _application_document_id(kind, candidate_document)
                        != checkpoint["document_id"]
                    ):
                        _fail("native_candidate_application_recovery_unsafe")
                    self._backend._expire_whiteboard_deliveries(conversation_id)
                    draft_payload = (
                        {
                            "kind": "whiteboard",
                            "board": copy.deepcopy(dict(candidate_document)),
                            "calculated": None,
                        }
                        if kind == "whiteboard"
                        else None
                    )
                    if (
                        state == "baseline_advanced"
                        and draft_payload is not None
                        and _application_document_digest(kind, candidate_document)
                        == intent["document_digest"]
                        and fingerprint(draft_payload)
                        == intent["application_payload_digest"]
                    ):
                        accepted = {
                            "kind": "whiteboard",
                            "workflow": summary.get("workflow"),
                            "application_kind": "whiteboard_draft",
                            "revision": intent["revision"],
                            "receipt_fingerprint": intent["receipt_fingerprint"],
                            "board": copy.deepcopy(dict(candidate_document)),
                        }
                    else:
                        accepted = candidate.accept(
                            kind,
                            copy.deepcopy(dict(candidate_document)),
                            intent["revision"],
                            intent["receipt_fingerprint"],
                        )
                    projected = self._backend._validate_candidate_acceptance(
                        accepted,
                        kind=kind,
                        expected_revision=intent["revision"],
                        expected_receipt=intent["receipt_fingerprint"],
                        conversation_id=conversation_id,
                    )
                    after = self._checkpoint(handle)
            except NativeCandidateApplicationError:
                self._backend._expire_whiteboard_deliveries(conversation_id)
                raise
            except Exception as error:  # noqa: BLE001 - stable Host boundary
                self._backend._expire_whiteboard_deliveries(conversation_id)
                raise self._project_error(error) from None
            if after != checkpoint:
                self._backend._expire_whiteboard_deliveries(conversation_id)
                _fail("native_candidate_changed")
            key = "plan" if kind == "native" else "board"
            target_document = projected.get(key)
            if not isinstance(target_document, Mapping):
                self._backend._expire_whiteboard_deliveries(conversation_id)
                _fail("native_candidate_acceptance_invalid")
            if discard_current and (
                _application_document_id(kind, target_document)
                != _application_document_id(kind, detached)
            ):
                self._backend._expire_whiteboard_deliveries(conversation_id)
                _fail("native_candidate_application_recovery_unsafe")
            target_payload = _target_payload(kind, projected)
            if (
                _application_document_digest(kind, target_document)
                != intent["document_digest"]
                or _application_payload_digest(kind, target_document, target_payload)
                != intent["application_payload_digest"]
            ):
                self._backend._expire_whiteboard_deliveries(conversation_id)
                _fail("native_candidate_application_recovery_unsafe")
            if isinstance(summary.get("workflow"), str):
                projected.setdefault("workflow", summary["workflow"])
            projected["issues"] = copy.deepcopy(summary.get("issues", []))
            projected["issues_total"] = summary.get(
                "issues_total", len(projected["issues"])
            )
            projected["issues_truncated"] = bool(
                summary.get("issues_truncated", False)
            )
            projected["conversation_id"] = conversation_id
            projected["status"] = intent["state"]
            projected["application_intent"] = copy.deepcopy(dict(intent))
            projected["app_actions"] = copy.deepcopy(
                handle.action_controller.list_cards(include_terminal=True)
            )
            self._timeline(
                conversation_id,
                label="候选应用目标已恢复",
                state=intent["state"],
                operation="candidate-recover",
                kind=kind,
                revision=intent["revision"],
                receipt=intent["receipt_fingerprint"],
                extra=(intent["application_payload_digest"],),
            )
            return projected

    def candidate_application_intent(
        self, conversation_id: str
    ) -> dict[str, Any]:
        return self._run(lambda: self._intent(conversation_id))

    def _intent(self, conversation_id: str) -> dict[str, Any]:
        with self._registry.action_scope(conversation_id) as handle:
            _candidate(handle)
            store = self._backend._read_application_intent(
                handle.session_root, conversation_id
            )
            self._backend._validate_application_intent_for_binding(
                handle.session_root,
                conversation_id,
                handle.binding,
                store,
            )
            intent = store["intent"]
            return {
                "conversation_id": conversation_id,
                "status": "none" if intent is None else intent["state"],
                "intent": copy.deepcopy(intent),
            }

    def applied_candidate_proof(
        self,
        conversation_id: str,
        *,
        current_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._run(
            lambda: self._applied_proof(
                conversation_id, current_payload=current_payload
            )
        )

    def _applied_proof(
        self,
        conversation_id: str,
        *,
        current_payload: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        with self._registry.action_scope(conversation_id) as handle:
            candidate = _candidate(handle)
            summary = self._summary(handle, candidate)
            records = self._backend._read_applied_proofs(
                handle.session_root, conversation_id
            )["records"]
            identity = self._backend._candidate_application_identity(
                candidate,
                summary,
                invalid_code="native_applied_proof_invalid",
                current_payload=current_payload,
            )
            proof = self._proof(
                records, identity, invalid_code="native_applied_proof_invalid"
            )
            return {
                "status": "applied" if proof is not None else "missing",
                "proof": copy.deepcopy(proof),
            }


__all__ = [
    "NativeCandidateApplicationError",
    "NativeCandidateApplicationPort",
    "NativeCandidateApplicationPortProtocol",
]
