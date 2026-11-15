"""Typed, read-only projection port for the active App candidate.

The candidate session owned by the active App binding remains the only source
of candidate and baseline facts.  This module does not persist a second
summary, revision or review state. Read-only access borrows the exact owner,
locks its candidate for a coherent page, then rechecks the binding on return.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable, Mapping
from typing import Any, Protocol, runtime_checkable

from .native_domain_session_registry import (
    NativeDomainSessionRegistry,
    NativeDomainSessionRegistryError,
)
from .native_error_codes import reviewed_native_error_code

_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class NativeCandidateProjectionError(RuntimeError):
    """Stable, renderer-safe failure from candidate projection."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise NativeCandidateProjectionError(code)


def _candidate(binding: object) -> object:
    candidate = getattr(binding, "candidate_session", None)
    if candidate is None or candidate is not getattr(
        binding, "candidate_session", None
    ):
        _fail("native_app_binding_invalid")
    # Keep the domain shape exact even though this port is read-only.
    # A half-formed candidate must not appear ready
    # to review while the application path would reject it.
    for name in ("summary", "read_candidate_view", "accept"):
        if not callable(getattr(candidate, name, None)):
            _fail("native_candidate_not_ready")
    return candidate


def _candidate_lock(candidate: object) -> Any:
    lock = getattr(candidate, "_lock", None)
    if (
        lock is None
        or not hasattr(lock, "__enter__")
        or not hasattr(lock, "__exit__")
    ):
        _fail("native_candidate_not_ready")
    return lock


def _checkpoint(binding: object) -> dict[str, Any]:
    checkpoint = getattr(binding, "domain_checkpoint", None)
    if not callable(checkpoint):
        _fail("native_candidate_checkpoint_invalid")
    value = checkpoint()
    if not isinstance(value, Mapping) or set(value) != {
        "document_id",
        "candidate_revision",
        "stage_revision",
        "database_fingerprint",
        "receipt_fingerprint",
    }:
        _fail("native_candidate_checkpoint_invalid")
    result = copy.deepcopy(dict(value))
    document_id = result["document_id"]
    receipt = result["receipt_fingerprint"]
    if (
        not isinstance(document_id, str)
        or _IDENTITY.fullmatch(document_id) is None
        or type(result["candidate_revision"]) is not int
        or not 0 <= result["candidate_revision"] <= 2**53 - 1
        or type(result["stage_revision"]) is not int
        or not 0 <= result["stage_revision"] <= 2**53 - 1
        or not isinstance(result["database_fingerprint"], str)
        or _DIGEST.fullmatch(result["database_fingerprint"]) is None
        or not (
            receipt is None
            or isinstance(receipt, str)
            and _DIGEST.fullmatch(receipt) is not None
        )
    ):
        _fail("native_candidate_checkpoint_invalid")
    return result


def _summary(value: object, *, checkpoint: Mapping[str, Any]) -> dict[str, Any]:
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
            or isinstance(receipt, str)
            and _DIGEST.fullmatch(receipt) is not None
        )
        or receipt != checkpoint["receipt_fingerprint"]
    ):
        _fail("native_candidate_summary_invalid")
    return result


def _safe_error(error: object) -> NativeCandidateProjectionError:
    return NativeCandidateProjectionError(
        reviewed_native_error_code(error, fallback="native_service_failed")
    )


def _resource_identity(candidate: object, checkpoint: Mapping[str, Any], resource: str) -> str:
    from genshin_app.equipment.files import fingerprint

    baseline_digest = None
    if resource == "baseline":
        baseline = getattr(candidate, "baseline", None)
        cached = getattr(candidate, "_projection_baseline", None)
        if cached is not None and cached[0] is baseline:
            baseline_digest = cached[1]
        else:
            baseline_digest = fingerprint(baseline)
            # All candidate transitions replace the baseline document under
            # the candidate lock. Never cache the mutable candidate/report.
            if hasattr(candidate, "__dict__"):
                candidate._projection_baseline = (baseline, baseline_digest)
    return fingerprint({"resource": resource, "document": checkpoint["document_id"],
                        "database": checkpoint["database_fingerprint"],
                        "revision": checkpoint["candidate_revision"] if resource != "baseline" else None,
                        "receipt": checkpoint["receipt_fingerprint"] if resource != "baseline" else None,
                        "baseline": baseline_digest})


@runtime_checkable
class NativeCandidateProjectionPortProtocol(Protocol):
    def summary(self, conversation_id: str) -> dict[str, Any]: ...

    def read(
        self,
        conversation_id: str,
        resource: str,
        pointer: str = "",
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]: ...


class NativeCandidateProjectionPort(NativeCandidateProjectionPortProtocol):
    """Project one exact active candidate without becoming its state owner."""

    def __init__(
        self,
        registry: NativeDomainSessionRegistry,
        *,
        error_factory: Callable[[str], BaseException] | None = None,
    ) -> None:
        if not isinstance(registry, NativeDomainSessionRegistry):
            raise TypeError("native_domain_session_registry_invalid")
        self._registry = registry
        self._error_factory = error_factory or NativeCandidateProjectionError

    def _project(self, operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return operation()
        except (
            NativeCandidateProjectionError,
            NativeDomainSessionRegistryError,
        ) as error:
            projected = self._error_factory(error.code)
            if not isinstance(projected, BaseException):
                raise TypeError("native_candidate_projection_error_factory_invalid")
            raise projected from None

    def summary(self, conversation_id: str) -> dict[str, Any]:
        return self._project(lambda: self._summary(conversation_id))

    def _summary(self, conversation_id: str) -> dict[str, Any]:
        with self._registry.read_scope(conversation_id) as handle:
            candidate = _candidate(handle.binding)
            try:
                with _candidate_lock(candidate):
                    before = _checkpoint(handle.binding)
                    result = candidate.summary()
                    after = _checkpoint(handle.binding)
                    identities = {kind: _resource_identity(candidate, after, kind)
                                  for kind in ("candidate", "baseline", "calculated")}
            except NativeCandidateProjectionError:
                raise
            except Exception as error:  # noqa: BLE001 - renderer-safe boundary
                raise _safe_error(error) from None
            if before != after:
                _fail("native_candidate_changed")
            return {**_summary(result, checkpoint=after), "resource_identities": identities}

    def read(
        self,
        conversation_id: str,
        resource: str,
        pointer: str = "",
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        return self._project(
            lambda: self._read(
                conversation_id,
                resource,
                pointer,
                offset,
                limit,
            )
        )

    def _read(
        self,
        conversation_id: str,
        resource: str,
        pointer: str,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        if resource not in {"candidate", "baseline", "calculated"}:
            _fail("agent_resource_missing")
        if (
            not isinstance(pointer, str)
            or len(pointer) > 4096
            or type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 200
        ):
            _fail("agent_tool_input")
        with self._registry.read_scope(conversation_id) as handle:
            candidate = _candidate(handle.binding)
            try:
                with _candidate_lock(candidate):
                    before = _checkpoint(handle.binding)
                    response = candidate.read_candidate_view(resource, pointer, offset, limit)
                    after = _checkpoint(handle.binding)
                    identity = _resource_identity(candidate, after, resource)
            except NativeCandidateProjectionError:
                raise
            except Exception as error:  # noqa: BLE001 - renderer-safe boundary
                raise _safe_error(error) from None
            if before != after:
                _fail("native_candidate_changed")
            if not isinstance(response, Mapping):
                _fail("native_candidate_read_invalid")
            return {**copy.deepcopy(dict(response)), "resource_identity": identity}


__all__ = [
    "NativeCandidateProjectionError",
    "NativeCandidateProjectionPort",
    "NativeCandidateProjectionPortProtocol",
]
