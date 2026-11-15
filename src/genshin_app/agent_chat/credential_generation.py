"""Versioned API-key mutations for the App-owned credential boundary.

The durable backend is deliberately injectable.  Production storage may use
Windows Credential Manager, while tests use an in-memory atomic backend.  The
controller never returns the secret from status or mutation operations; only
``resolve_for_provider`` crosses the explicit provider boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

MAX_CREDENTIAL_CHARS = 512
MAX_CREDENTIAL_GENERATION = (1 << 53) - 1


class CredentialGenerationError(RuntimeError):
    """Sanitized versioned-credential failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class CredentialGenerationConflict(CredentialGenerationError):
    """The caller observed an older credential generation."""

    def __init__(self, *, expected_generation: int, actual_generation: int):
        super().__init__("credential_generation_conflict")
        self.expected_generation = expected_generation
        self.actual_generation = actual_generation


@dataclass(frozen=True, repr=False)
class _CredentialRecord:
    generation: int
    secret: str | None


@dataclass(frozen=True, repr=False)
class _ReadFact:
    """Internal read result whose error branch contains no credential value."""

    available: bool
    record: _CredentialRecord | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class _MutationFact:
    """Credential-free mutation result safe to retain in an exception frame."""

    generation: int | None = None
    saved: bool | None = None
    error_code: str | None = None
    conflict_expected: int | None = None
    conflict_actual: int | None = None


class AtomicCredentialBackend(Protocol):
    """Minimal atomic storage contract required by the controller.

    ``compare_and_swap`` must compare and persist as one backend operation.
    Returning ``False`` means another controller committed first.
    """

    available: bool

    def read_versioned(self) -> tuple[int, str | None]: ...

    def compare_and_swap(
        self,
        *,
        expected_generation: int,
        next_generation: int,
        secret: str | None,
    ) -> bool: ...


def _generation(value: object, name: str = "credential_generation") -> int:
    if (isinstance(value, bool) or not isinstance(value, int) or value < 0
            or value > MAX_CREDENTIAL_GENERATION):
        raise CredentialGenerationError(f"{name}_invalid")
    return value


def _secret(value: object) -> str:
    if (not isinstance(value, str) or not value or len(value) > MAX_CREDENTIAL_CHARS
            or any(char.isspace() or ord(char) < 0x20 or ord(char) == 0x7f
                   for char in value)):
        raise CredentialGenerationError("credential_value_invalid")
    return value


class CredentialGenerationController:
    """Expose optimistic, generation-bound mutations over an atomic backend."""

    def __init__(self, backend: AtomicCredentialBackend):
        self._backend = backend

    def _read_fact(self) -> _ReadFact:
        """Read and validate the backend without exporting its exception graph.

        This worker never raises an ordinary exception.  Its frame may briefly
        contain the durable secret, but no public failure traceback can retain
        the frame because failures are reduced to a stable code before return.
        """

        try:
            available = bool(getattr(self._backend, "available", False))
        except Exception:  # noqa: BLE001 - sanitize an injected backend boundary
            return _ReadFact(available=False, error_code="credential_store_read_failed")
        if not available:
            return _ReadFact(
                available=False,
                error_code="credential_store_unavailable",
            )
        try:
            raw_generation, raw_secret = self._backend.read_versioned()
        except Exception:  # noqa: BLE001 - sanitize an injected backend boundary
            return _ReadFact(available=True, error_code="credential_store_read_failed")
        try:
            generation = _generation(raw_generation)
            if raw_secret is not None:
                raw_secret = _secret(raw_secret)
        except CredentialGenerationError as error:
            return _ReadFact(available=True, error_code=error.code)
        except Exception:  # noqa: BLE001 - reject hostile backend value objects
            return _ReadFact(available=True, error_code="credential_store_read_failed")
        return _ReadFact(
            available=True,
            record=_CredentialRecord(generation=generation, secret=raw_secret),
        )

    @staticmethod
    def _status(record: _CredentialRecord) -> dict:
        return {
            "available": True,
            "saved": record.secret is not None,
            "status": "saved" if record.secret is not None else "empty",
            "credential_generation": record.generation,
        }

    def status(self) -> dict:
        """Return renderer/session/log-safe metadata without the API key."""
        fact = None
        try:
            fact = self._read_fact()
            if fact.record is not None:
                return self._status(fact.record)
            if not fact.available and fact.error_code == "credential_store_unavailable":
                return {
                    "available": False,
                    "saved": False,
                    "status": "unavailable",
                    "credential_generation": None,
                }
            return {
                "available": fact.available,
                "saved": False,
                "status": "error",
                "credential_generation": None,
            }
        except Exception:  # noqa: BLE001 - status is a non-throwing public boundary
            return {
                "available": False,
                "saved": False,
                "status": "error",
                "credential_generation": None,
            }
        finally:
            # Do not retain a successful record's secret in an inspectable
            # status-frame local after projecting the public metadata.
            fact = None

    def resolve_for_provider(self) -> tuple[str | None, int]:
        """Resolve the secret only for the private App-to-provider handoff."""
        fact = None
        result = None
        error_code = None
        try:
            fact = self._read_fact()
            if fact.record is None:
                error_code = fact.error_code or "credential_store_read_failed"
            else:
                result = (fact.record.secret, fact.record.generation)
        except Exception:  # noqa: BLE001 - sanitize the complete provider boundary
            error_code = "credential_store_read_failed"
        finally:
            fact = None
        if error_code is not None:
            raise CredentialGenerationError(error_code)
        if result is None:  # Defensive: keep an injected/overridden worker fail-closed.
            raise CredentialGenerationError("credential_store_read_failed")
        return result

    def _mutation_fact(
        self,
        *,
        expected_generation: int,
        secret: str | None,
    ) -> _MutationFact:
        """Perform one mutation and return only credential-free outcome facts."""

        try:
            expected = _generation(
                expected_generation,
                "expected_credential_generation",
            )
        except CredentialGenerationError as error:
            return _MutationFact(error_code=error.code)
        except Exception:  # noqa: BLE001 - reject hostile integer-like objects
            return _MutationFact(error_code="expected_credential_generation_invalid")
        read = self._read_fact()
        if read.record is None:
            return _MutationFact(
                error_code=read.error_code or "credential_store_read_failed",
            )
        current = read.record
        if current.generation != expected:
            return _MutationFact(
                conflict_expected=expected,
                conflict_actual=current.generation,
            )
        if expected == MAX_CREDENTIAL_GENERATION:
            return _MutationFact(error_code="credential_generation_exhausted")
        next_generation = expected + 1
        try:
            committed = self._backend.compare_and_swap(
                expected_generation=expected,
                next_generation=next_generation,
                secret=secret,
            )
        except Exception:  # noqa: BLE001 - sanitize an injected backend boundary
            return _MutationFact(error_code="credential_store_write_failed")
        if type(committed) is not bool:
            return _MutationFact(error_code="credential_store_write_failed")
        if not committed:
            # Read after the failed CAS only to report a useful non-secret
            # generation.  A concurrently changing backend may move again;
            # either value still proves that the caller must refresh.
            refreshed = self._read_fact()
            if refreshed.record is None:
                return _MutationFact(
                    error_code=refreshed.error_code or "credential_store_read_failed",
                )
            return _MutationFact(
                conflict_expected=expected,
                conflict_actual=refreshed.record.generation,
            )
        return _MutationFact(
            generation=next_generation,
            saved=secret is not None,
        )

    @staticmethod
    def _finish_mutation(fact: _MutationFact | None) -> dict:
        """Materialize a result or new sanitized error from safe facts only."""

        if fact is None:
            raise CredentialGenerationError("credential_store_write_failed")
        if fact.conflict_expected is not None and fact.conflict_actual is not None:
            raise CredentialGenerationConflict(
                expected_generation=fact.conflict_expected,
                actual_generation=fact.conflict_actual,
            )
        if fact.error_code is not None:
            raise CredentialGenerationError(fact.error_code)
        if fact.generation is None or fact.saved is None:
            raise CredentialGenerationError("credential_store_write_failed")
        return {
            "available": True,
            "saved": fact.saved,
            "status": "saved" if fact.saved else "empty",
            "credential_generation": fact.generation,
        }

    def save(self, secret: str, *, expected_generation: int) -> dict:
        """Create or replace a key if the caller still owns the generation."""
        validated_secret = None
        fact = None
        try:
            try:
                validated_secret = _secret(secret)
            except CredentialGenerationError:
                fact = _MutationFact(error_code="credential_value_invalid")
            except Exception:  # noqa: BLE001 - reject hostile str subclasses
                fact = _MutationFact(error_code="credential_value_invalid")
            if fact is None:
                fact = self._mutation_fact(
                    expected_generation=expected_generation,
                    secret=validated_secret,
                )
        except Exception:  # noqa: BLE001 - sanitize the complete public boundary
            fact = _MutationFact(error_code="credential_store_write_failed")
        finally:
            # A public exception traceback includes this frame.  Overwrite both
            # references before constructing that exception so crash reporters
            # that capture locals cannot recover the submitted key.
            secret = ""
            validated_secret = None
        return self._finish_mutation(fact)

    def delete(self, *, expected_generation: int) -> dict:
        """Persist a tombstone so stale windows cannot recreate an old key."""
        fact = None
        try:
            fact = self._mutation_fact(
                expected_generation=expected_generation,
                secret=None,
            )
        except Exception:  # noqa: BLE001 - sanitize the complete public boundary
            fact = _MutationFact(error_code="credential_store_write_failed")
        return self._finish_mutation(fact)


__all__ = [
    "MAX_CREDENTIAL_CHARS",
    "MAX_CREDENTIAL_GENERATION",
    "AtomicCredentialBackend",
    "CredentialGenerationConflict",
    "CredentialGenerationController",
    "CredentialGenerationError",
]
