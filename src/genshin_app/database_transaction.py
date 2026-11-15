"""One reentrant cross-process writer fence for a profile's release identity.

Uses the existing database update lock.  Its implementation is reentrant in
the owning thread; database migration may therefore retain its own fence.
No caller-owned boolean or token grants authority to skip an OS lock.
"""
from __future__ import annotations

import functools
import inspect
import json
import os
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from genshin_db.locking import file_lock

ACTIVATION_JOURNAL = "database-activation-journal.json"
_activation_owner: ContextVar[str | None] = ContextVar("database_activation_owner", default=None)


class ActivationJournalError(ValueError):
    reason = "database_activation_journal_invalid"

    def __init__(self):
        super().__init__("数据库恢复记录不完整或不可读，请保留诊断与原文件后处理。")


@contextmanager
def profile_writer(data_dir: Path, *, timeout: float | None = 0):
    with file_lock(Path(data_dir).resolve() / ".update.lock", timeout=timeout):
        yield


def serialized_profile_writer(argument="data_dir"):
    """Fence all writes by an existing entry point, including final markers."""
    def decorate(function):
        signature = inspect.signature(function)

        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            values = signature.bind(*args, **kwargs)
            values.apply_defaults()
            directory = values.arguments[argument]
            if directory is None:
                from genshin_app.paths import default_data_dir
                directory = default_data_dir()
            with profile_writer(directory):
                return function(*args, **kwargs)
        return wrapped
    return decorate


@contextmanager
def activation_owner(data_dir: Path):
    token = _activation_owner.set(str(Path(data_dir).resolve()))
    try:
        yield
    finally:
        _activation_owner.reset(token)


def owns_activation(data_dir: Path) -> bool:
    return _activation_owner.get() == str(Path(data_dir).resolve())


def atomic_json(path: Path, value: Mapping):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(dict(value), stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_activation_journal(data_dir: Path) -> dict | None:
    path = Path(data_dir) / ACTIVATION_JOURNAL
    if not path.exists():
        return None
    try:
        # Three existing 4 MiB control-file limits, JSON escaping and envelope.
        if not 2 <= path.stat().st_size <= 80 * 1024 * 1024:
            raise ValueError("journal size")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ActivationJournalError() from error
    if (type(value) is not dict or value.get("contract") != "genshin-database-activation-journal-v1"
            or value.get("state") not in {"prepared", "database_installing", "database_ready",
                                           "pair_committed", "complete", "cancelled", "failed", "unknown"}):
        raise ActivationJournalError()
    before = value.get("before")
    pins = (value.get("operation_id"), value.get("target_digest"))
    if (not all(type(pin) is str and len(pin) == size and all(c in "0123456789abcdef" for c in pin)
                for pin, size in zip(pins, (32, 64), strict=True))
            or type(before) is not dict or type(before.get("controls")) is not dict
            or set(before["controls"]) != {"database-live-manifest.json", "database-live-receipt.json", "release-pair-live-manifest.json"}
            or any(raw is not None and type(raw) is not str for raw in before["controls"].values())):
        raise ActivationJournalError()
    sha = before.get("database_sha256")
    if sha is not None and (type(sha) is not str or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha)):
        raise ActivationJournalError()
    return value


def journal_pending(value: dict | None) -> bool:
    return value is not None and value["state"] not in {"complete", "cancelled", "failed"}


def write_activation_journal(data_dir: Path, value: dict, state: str, **details):
    value.update(state=state, updated_at=datetime.now(UTC).isoformat(), **details)
    atomic_json(Path(data_dir) / ACTIVATION_JOURNAL, value)


def write_startup_diagnostic(data_dir: Path, *, attempt_id: str, stage: str,
                             status: str, reason: str | None = None,
                             details: Mapping | None = None) -> dict:
    """Bounded, public release facts only; failure to log is never authority."""
    from genshin_app.version import APP_RELEASE_ID, DATABASE_RELEASE_ID, RELEASE_PAIR_ID
    payload = {"contract": "genshin-database-startup-diagnostic-v1",
               "attempt_id": attempt_id, "recorded_at": datetime.now(UTC).isoformat(),
               "stage": stage, "status": status, "reason": reason,
               "app_release_id": APP_RELEASE_ID, "database_release_id": DATABASE_RELEASE_ID,
               "release_pair_id": RELEASE_PAIR_ID, "data_dir": str(Path(data_dir).resolve())}
    allowed = {"current_database_release_id", "target_database_release_id", "current_pair_id",
               "target_pair_id", "confirmation_digest", "activation_state", "migration_reason"}
    payload["details"] = {k: v for k, v in (details or {}).items() if k in allowed
                          and (v is None or isinstance(v, (str, bool, int)))}
    path = Path(data_dir) / "logs" / "database-startup-last.json"
    try:
        atomic_json(path, payload)
        # Preserve earlier causes when a later inspection/repair updates the
        # latest summary. Only a random, process-created attempt ID is used.
        if len(attempt_id) != 32 or any(c not in "0123456789abcdef" for c in attempt_id):
            raise ValueError("invalid startup attempt id")
        trace = path.with_name(f"database-startup-{attempt_id}.jsonl")
        with trace.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        payload["diagnostic_path"] = str(trace)
        payload["latest_diagnostic_path"] = str(path)
    except (OSError, ValueError, TypeError):
        payload["diagnostic_write_failed"] = True
    return payload
