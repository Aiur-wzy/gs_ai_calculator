"""Opt-in, read-only maintenance diagnostics; never an execution authority.

No scope means no collection, material projection, hashing or clock reads.
Observer failures are isolated from the business result and its first error.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re
import time
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, fields, is_dataclass
from functools import wraps
from pathlib import Path

CONTRACT = "genshin-runtime-diagnostic-v1"
_CAPTURE: ContextVar[DiagnosticCapture | None] = ContextVar("genshin_runtime_diagnostics", default=None)
_CODE = re.compile(r"[A-Za-z0-9_./:-]{1,160}\Z")
_SENSITIVE = frozenset({"apikey", "authorization", "password", "secret", "credentialsecret", "accesstoken", "refreshtoken"})


def _material(value):
    if value is None or type(value) in (str, bool, int, float):
        return value
    if isinstance(value, Path):
        return {"path_identity": str(value)}  # Hashed only; paths never enter a record.
    if isinstance(value, bytes):
        return {"bytes": len(value), "content_sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, Mapping):
        return {str(key): "<redacted>" if str(key).replace("_", "").casefold() in _SENSITIVE
                else _material(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_material(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_material(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _material(getattr(value, field.name)) for field in fields(value)}
    if callable(value):
        # Ports are capabilities, not state snapshots or executable audit input.
        raise TypeError("diagnostic_callable_not_material")
    raise TypeError("diagnostic_opaque_material")


def _fingerprint(value):
    raw = json.dumps(_material(value), ensure_ascii=False, sort_keys=True,
                     allow_nan=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _code(value):
    return value if isinstance(value, str) and _CODE.fullmatch(value) is not None else None


@dataclass(frozen=True, slots=True)
class DiagnosticRecord:
    operation_id: str
    domain: str
    stage_id: str
    status: str
    input_fingerprints: tuple[tuple[str, str], ...]
    prestate_fingerprint: str | None
    output_fingerprints: tuple[tuple[str, str], ...]
    poststate_fingerprint: str | None
    changed_paths: tuple[str, ...]
    issue_codes: tuple[str, ...]
    cause_chain: tuple[tuple[str, str | None], ...]
    elapsed_ms: float
    handler_family: str | None = None
    contract_version: int | str | None = None
    source_commit: str | None = None
    component: str | None = None
    change_set_id: str | None = None
    event_id: str | None = None
    atomic_parent_id: str | None = None
    subject_operation_id: str | None = None
    board_id: str | None = None
    revision: int | None = None
    intent_fingerprint: str | None = None

    def as_dict(self):
        result = {field.name: getattr(self, field.name) for field in fields(self)}
        for key in ("input_fingerprints", "output_fingerprints"):
            result[key] = dict(result[key])
        result["cause_chain"] = [{"type": kind, "code": code} for kind, code in self.cause_chain]
        for key in ("changed_paths", "issue_codes"):
            result[key] = list(result[key])
        return {"contract": CONTRACT, **result}


class DiagnosticCapture:
    def __init__(self, *, operation_id=None, sink=None, retain=True):
        self.operation_id = operation_id or uuid.uuid4().hex
        if _code(self.operation_id) is None:
            raise ValueError("diagnostic_operation_id_invalid")
        self._sink, self._retain = sink, retain
        self._records: list[DiagnosticRecord] = []
        self.observer_failures = 0

    @property
    def records(self):
        return tuple(self._records)

    def emit(self, record):
        if self._retain:
            self._records.append(record)
        if self._sink is not None:
            try:
                self._sink(record.as_dict())
            except BaseException:  # noqa: BLE001 - observer aborts cannot replace business results/errors
                self.observer_failures += 1

    def write_jsonl(self, path):
        """Explicit maintenance output; never writes alongside a business file by default."""
        try:
            with Path(path).open("a", encoding="utf-8", newline="\n") as stream:
                stream.writelines(json.dumps(record.as_dict(), ensure_ascii=False, allow_nan=False) + "\n" for record in self._records)
            return {"status": "written", "records": len(self._records)}
        except BaseException:  # noqa: BLE001 - diagnostics have no authority over the completed operation
            self.observer_failures += 1
            return {"status": "failed", "code": "diagnostic_sink_failed"}


@contextmanager
def capture_runtime_diagnostics(*, operation_id=None, sink=None, retain=True):
    capture = DiagnosticCapture(operation_id=operation_id, sink=sink, retain=retain)
    token = _CAPTURE.set(capture)
    try:
        yield capture
    finally:
        _CAPTURE.reset(token)


def diagnostics_enabled():
    return _CAPTURE.get() is not None


class DiagnosticStage:
    def __init__(self, capture, domain, stage_id, inputs, prestate, metadata):
        if _code(domain) is None or _code(stage_id) is None:
            raise ValueError("diagnostic_stage_identity_invalid")
        self.capture, self.domain, self.stage_id = capture, domain, stage_id
        self.inputs = self._hashes(inputs)
        self.prestate = self._hash(prestate, resolve=True)
        self.metadata = {}
        self._metadata(metadata)
        self.started = time.perf_counter()
        self.outputs, self.poststate, self.changed, self.issues, self.status = (), None, (), (), "completed"

    def _metadata(self, metadata):
        for key, value in metadata.items():
            value = self._value(value)
            if value is None:
                continue
            if key in {"contract_version", "revision"} and type(value) is int:
                self.metadata[key] = value
            elif (plain := _code(value)) is not None:
                self.metadata[key] = plain
            else:
                self.capture.observer_failures += 1

    def _value(self, value):
        return value() if callable(value) else value

    def _hash(self, material, *, resolve=False):
        if material is None:
            return None
        try:
            return _fingerprint(self._value(material) if resolve else material)
        except BaseException:  # noqa: BLE001 - projection must not execute or invalidate business work
            self.capture.observer_failures += 1
            return None

    def _hashes(self, materials):
        if materials is None:
            return ()
        try:
            values = self._value(materials)
            return tuple((str(name), digest) for name, value in values.items()
                         if (digest := self._hash(value)) is not None)
        except BaseException:  # noqa: BLE001 - a diagnostic input is optional, never a calculation gate
            self.capture.observer_failures += 1
            return ()

    def result(self, *, outputs=None, poststate=None, changed_paths=(), issue_codes=(), status="completed", metadata=None):
        try:
            self.outputs, self.poststate = self._hashes(outputs), self._hash(poststate, resolve=True)
            self.changed = tuple(str(value) for value in changed_paths)
            self.issues = tuple(value for item in issue_codes if (value := _code(item)) is not None)
            self.status = status
            self._metadata(metadata or {})
        except BaseException:  # noqa: BLE001 - projection has no authority over the computed result
            self.capture.observer_failures += 1

    def finish(self, error):
        causes, seen = [], set()
        while error is not None and id(error) not in seen:
            seen.add(id(error))
            causes.append((type(error).__name__, _code(getattr(error, "reason", None) or getattr(error, "code", None))))
            error = error.__cause__
        record = DiagnosticRecord(
            self.capture.operation_id, self.domain, self.stage_id,
            "failed" if causes else self.status, self.inputs, self.prestate,
            self.outputs, self.poststate, self.changed, self.issues, tuple(causes),
            max(0.0, (time.perf_counter() - self.started) * 1000), **self.metadata,
        )
        self.capture.emit(record)


class _InactiveStage:
    @staticmethod
    def result(**_unused):
        pass


_INACTIVE = _InactiveStage()


@contextmanager
def diagnostic_stage(domain, stage_id, *, inputs=None, prestate=None, **metadata):
    capture = _CAPTURE.get()
    if capture is None:
        yield _INACTIVE
        return
    stage = None
    try:
        stage = DiagnosticStage(capture, domain, stage_id, inputs, prestate, metadata)
    except BaseException:  # noqa: BLE001 - invalid diagnostic metadata cannot reject business work
        capture.observer_failures += 1
    if stage is None:
        # Leave the observer's exception handler before entering business code;
        # otherwise Python would attach that observer error as __context__.
        yield _INACTIVE
        return
    try:
        yield stage
    except BaseException as error:
        try:
            stage.finish(error)
        except BaseException:  # noqa: BLE001 - preserve the original exception, including abort-like failures
            capture.observer_failures += 1
        raise
    else:
        try:
            stage.finish(None)
        except BaseException:  # noqa: BLE001 - optional observer failure cannot change success
            capture.observer_failures += 1


def diagnosed(domain, stage_id, *, exclude=("ports",), metadata=None, result_metadata=None):
    """Instrument a leaf without copying state or changing its public signature."""
    def decorate(operation):
        signature = inspect.signature(operation)

        @wraps(operation)
        def execute(*args, **kwargs):
            if _CAPTURE.get() is None:
                return operation(*args, **kwargs)

            def arguments():
                bound = signature.bind(*args, **kwargs)
                return {key: value for key, value in bound.arguments.items() if key not in exclude}

            try:
                details = metadata(arguments()) if callable(metadata) else metadata or {}
            except BaseException:  # noqa: BLE001 - optional metadata cannot change the original call's validation
                _CAPTURE.get().observer_failures += 1
                details = {}
            with diagnostic_stage(domain, stage_id, inputs=arguments, **details) as audit:
                result = operation(*args, **kwargs)
                try:
                    final_details = result_metadata(arguments(), result) if result_metadata is not None else None
                except BaseException:  # noqa: BLE001 - completed work is not invalidated by projection
                    _CAPTURE.get().observer_failures += 1
                    final_details = None
                audit.result(outputs={"result": result}, metadata=final_details)
                return result

        return execute
    return decorate


def project_reaction_diagnostics(rows, *, bindings=None):
    """Observe the one canonical trace; never re-execute a handler or mutate it."""
    capture = _CAPTURE.get()
    if capture is None:
        return
    try:
        bindings = bindings() if callable(bindings) else bindings
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            family = row.get("reaction_type") or row.get("replacement") or row.get("family")
            binding = (bindings or {}).get(family, {}) if isinstance(family, str) else {}
            with diagnostic_stage(
                "special-reaction", "committed-trace-projection-v1", inputs={"canonical_trace": row},
                handler_family=_code(family), contract_version=binding.get("contract_version"),
                event_id=_code(row.get("event_id") or row.get("id")),
                atomic_parent_id=_code(row.get("atomic_parent_id")),
            ) as audit:
                audit.result(outputs={"canonical_trace": row}, status="observed")
    except BaseException:  # noqa: BLE001 - malformed diagnostics cannot alter a completed calculation
        capture.observer_failures += 1
