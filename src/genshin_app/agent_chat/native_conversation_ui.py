"""Small Host-owned navigation metadata for native Agent conversations.

The conversation directory remains the lifecycle authority.  This sidecar only
stores user-interface facts which do not belong in the model/session record:
an explicit title override, archive state and a durable read cursor.  Renderer
storage is never consulted.

All Renderer mutations use revision CAS plus a bounded, durable idempotency
ledger.  Host activity additionally records a bounded high-water mark for
each durable source, so an old fact cannot become unread again after its
mutation id has aged out of that ledger.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from genshin_app.equipment.files import json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_db.locking import file_lock

from .native_session_store import redact_visible_text
from .sessions import safe_path, write_owned

CONTRACT = "genshin-native-conversation-ui-v1"
STORE_FILE = "native-conversation-ui-v1.json"
LOCK_FILE = ".native-conversation-ui-v1.lock"
MAX_STORE_BYTES = 128 * 1024
MAX_MUTATIONS = 512
MAX_ACTIVITY_SOURCES = 16
MAX_TITLE_CHARS = 256
MAX_SAFE_INTEGER = 2**53 - 1

_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_OPERATIONS = frozenset({"rename", "archive", "mark_read", "visible_activity"})


class NativeConversationUiError(RuntimeError):
    """Stable error code emitted by the UI metadata boundary."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise NativeConversationUiError(code)


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _identity(value: object, field: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        _fail(f"native_session_{field}_invalid")
    return value


def _counter(value: object, field: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SAFE_INTEGER:
        _fail(f"native_session_{field}_invalid")
    return value


def _title(value: object) -> str:
    if not isinstance(value, str):
        _fail("native_session_title_invalid")
    result = " ".join(value.strip().split())
    if not result or len(result) > MAX_TITLE_CHARS:
        _fail("native_session_title_invalid")
    # Titles are copied into the global session navigation projection.  Keep
    # them under the same credential/path boundary as visible messages rather
    # than allowing a rename to become a side channel into every conversation
    # list and search result.
    return redact_visible_text(result)


def _digest_payload(operation: str, payload: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            [operation, dict(payload)],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        _fail("native_session_mutation_invalid")
    return hashlib.sha256(encoded).hexdigest()


def _default(conversation_id: str) -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "conversation_id": conversation_id,
        "revision": 0,
        "title": None,
        "archived": False,
        "activity_revision": 0,
        "read_activity_revision": 0,
        "activity_sources": {},
        "mutations": [],
    }


def _normalize(value: object, *, conversation_id: str) -> dict[str, Any]:
    legacy_fields = {
        "contract",
        "conversation_id",
        "revision",
        "title",
        "archived",
        "activity_revision",
        "read_activity_revision",
        "mutations",
    }
    current_fields = legacy_fields | {"activity_sources"}
    if not isinstance(value, Mapping) or frozenset(value) not in {
        frozenset(legacy_fields),
        frozenset(current_fields),
    }:
        _fail("native_session_metadata_invalid")
    if value.get("contract") != CONTRACT:
        _fail("native_session_metadata_invalid")
    if value.get("conversation_id") != conversation_id:
        _fail("native_session_metadata_invalid")
    revision = _counter(value.get("revision"), "metadata_revision")
    title = value.get("title")
    if title is not None:
        title = _title(title)
    if type(value.get("archived")) is not bool:
        _fail("native_session_metadata_invalid")
    activity_revision = _counter(
        value.get("activity_revision"), "activity_revision"
    )
    read_revision = _counter(
        value.get("read_activity_revision"), "read_activity_revision"
    )
    if read_revision > activity_revision:
        _fail("native_session_metadata_invalid")
    sources = value.get("activity_sources", {})
    if not isinstance(sources, Mapping) or len(sources) > MAX_ACTIVITY_SOURCES:
        _fail("native_session_metadata_invalid")
    checked_sources: dict[str, dict[str, Any]] = {}
    for source, raw in sources.items():
        source_id = _identity(source, "activity_source")
        if (
            not isinstance(raw, Mapping)
            or set(raw) != {"revision", "fact_digest"}
        ):
            _fail("native_session_metadata_invalid")
        source_revision = _counter(
            raw.get("revision"), "activity_source_revision"
        )
        fact_digest = raw.get("fact_digest")
        if not isinstance(fact_digest, str) or _DIGEST.fullmatch(fact_digest) is None:
            _fail("native_session_metadata_invalid")
        checked_sources[source_id] = {
            "revision": source_revision,
            "fact_digest": fact_digest,
        }
    mutations = value.get("mutations")
    if not isinstance(mutations, list) or len(mutations) > MAX_MUTATIONS:
        _fail("native_session_metadata_invalid")
    checked_mutations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in mutations:
        if not isinstance(raw, Mapping) or set(raw) != {
            "mutation_id", "operation", "digest", "resulting_revision"
        }:
            _fail("native_session_metadata_invalid")
        mutation_id = _identity(raw.get("mutation_id"), "mutation_id")
        if mutation_id in seen or raw.get("operation") not in _OPERATIONS:
            _fail("native_session_metadata_invalid")
        digest = raw.get("digest")
        if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
            _fail("native_session_metadata_invalid")
        resulting_revision = _counter(
            raw.get("resulting_revision"), "metadata_revision"
        )
        if not 1 <= resulting_revision <= revision:
            _fail("native_session_metadata_invalid")
        seen.add(mutation_id)
        checked_mutations.append({
            "mutation_id": mutation_id,
            "operation": raw["operation"],
            "digest": digest,
            "resulting_revision": resulting_revision,
        })
    result = {
        "contract": CONTRACT,
        "conversation_id": conversation_id,
        "revision": revision,
        "title": title,
        "archived": value["archived"],
        "activity_revision": activity_revision,
        "read_activity_revision": read_revision,
        "activity_sources": checked_sources,
        "mutations": checked_mutations,
    }
    try:
        if len(json_bytes(result)) > MAX_STORE_BYTES:
            _fail("native_session_metadata_invalid")
    except (TypeError, ValueError, OverflowError, RecursionError):
        _fail("native_session_metadata_invalid")
    return result


def _projection(value: Mapping[str, Any], *, status: str = "current") -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "conversation_id": value["conversation_id"],
        "title": value["title"],
        "archived": value["archived"],
        "unread": value["activity_revision"] > value["read_activity_revision"],
        "metadata_revision": value["revision"],
        "activity_revision": value["activity_revision"],
        "read_activity_revision": value["read_activity_revision"],
        "status": status,
    }


class NativeConversationUiStore:
    """One conversation's strictly versioned navigation state."""

    def __init__(self, root: str | Path, *, conversation_id: str) -> None:
        self.root = Path(root).absolute()
        self.conversation_id = _identity(conversation_id, "id")
        try:
            self.path = safe_path(self.root, STORE_FILE)
            self.lock_path = safe_path(self.root, LOCK_FILE)
        except (OSError, PlanError):
            _fail("native_session_metadata_storage_invalid")

    def _checked_path(self, name: str) -> Path:
        try:
            return safe_path(self.root, name)
        except (OSError, PlanError):
            _fail("native_session_metadata_storage_invalid")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        try:
            lock_path = self._checked_path(LOCK_FILE)
            self._checked_path(STORE_FILE)
            with file_lock(lock_path):
                if self._checked_path(LOCK_FILE) != lock_path:
                    _fail("native_session_metadata_storage_invalid")
                if self._checked_path(STORE_FILE) != self.path:
                    _fail("native_session_metadata_storage_invalid")
                yield
        except NativeConversationUiError:
            raise
        except (OSError, PlanError):
            _fail("native_session_metadata_storage_invalid")

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return _default(self.conversation_id)
        try:
            if self.path.is_symlink() or self.path.stat().st_size > MAX_STORE_BYTES:
                _fail("native_session_metadata_storage_invalid")
            with self.path.open("rb") as stream:
                raw = stream.read(MAX_STORE_BYTES + 1)
            if len(raw) > MAX_STORE_BYTES:
                _fail("native_session_metadata_storage_invalid")
            value = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_strict_object,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
        except NativeConversationUiError:
            raise
        except (OSError, UnicodeError, ValueError, RecursionError):
            _fail("native_session_metadata_storage_invalid")
        return _normalize(value, conversation_id=self.conversation_id)

    def _write_unlocked(self, value: Mapping[str, Any]) -> dict[str, Any]:
        checked = _normalize(value, conversation_id=self.conversation_id)
        try:
            write_owned(self.root, STORE_FILE, checked, MAX_STORE_BYTES)
        except PlanError:
            _fail("native_session_metadata_storage_invalid")
        return checked

    def read(self) -> dict[str, Any]:
        """Read without creating a sidecar for an untouched old conversation."""

        with self._locked():
            return _projection(self._read_unlocked())

    def _mutate(
        self,
        *,
        operation: str,
        payload: Mapping[str, Any],
        mutation_id: str,
        expected_revision: int | None,
    ) -> dict[str, Any]:
        if operation not in _OPERATIONS:
            _fail("native_session_mutation_invalid")
        mutation_id = _identity(mutation_id, "mutation_id")
        if expected_revision is not None:
            expected_revision = _counter(expected_revision, "metadata_revision")
        digest = _digest_payload(operation, payload)
        with self._locked():
            current = self._read_unlocked()
            replay = next(
                (row for row in current["mutations"] if row["mutation_id"] == mutation_id),
                None,
            )
            if replay is not None:
                if replay["operation"] != operation or replay["digest"] != digest:
                    _fail("native_session_mutation_conflict")
                return _projection(current, status="already_applied")
            if expected_revision is not None and current["revision"] != expected_revision:
                _fail("native_session_metadata_stale")
            updated = copy.deepcopy(current)
            if operation == "rename":
                updated["title"] = _title(payload.get("title"))
            elif operation == "archive":
                archived = payload.get("archived")
                if type(archived) is not bool or set(payload) != {"archived"}:
                    _fail("native_session_mutation_invalid")
                updated["archived"] = archived
            elif operation == "mark_read":
                if payload:
                    _fail("native_session_mutation_invalid")
                updated["read_activity_revision"] = updated["activity_revision"]
            else:
                if set(payload) != {"fact_id", "source", "source_revision"}:
                    _fail("native_session_mutation_invalid")
                fact_id = _identity(payload.get("fact_id"), "activity_id")
                source = _identity(payload.get("source"), "activity_source")
                source_revision = _counter(
                    payload.get("source_revision"), "activity_source_revision"
                )
                fact_digest = hashlib.sha256(fact_id.encode("utf-8")).hexdigest()
                high_water = updated["activity_sources"].get(source)
                if high_water is not None:
                    if source_revision < high_water["revision"]:
                        return _projection(current, status="already_applied")
                    if source_revision == high_water["revision"]:
                        if fact_digest != high_water["fact_digest"]:
                            _fail("native_session_activity_conflict")
                        return _projection(current, status="already_applied")
                elif len(updated["activity_sources"]) >= MAX_ACTIVITY_SOURCES:
                    _fail("native_session_activity_sources_exhausted")
                if updated["activity_revision"] >= MAX_SAFE_INTEGER:
                    _fail("native_session_metadata_exhausted")
                updated["activity_revision"] += 1
                updated["activity_sources"][source] = {
                    "revision": source_revision,
                    "fact_digest": fact_digest,
                }
            if updated["revision"] >= MAX_SAFE_INTEGER:
                _fail("native_session_metadata_exhausted")
            updated["revision"] += 1
            updated["mutations"].append({
                "mutation_id": mutation_id,
                "operation": operation,
                "digest": digest,
                "resulting_revision": updated["revision"],
            })
            updated["mutations"] = updated["mutations"][-MAX_MUTATIONS:]
            return _projection(self._write_unlocked(updated), status="applied")

    def rename(
        self, *, title: str, expected_revision: int, mutation_id: str
    ) -> dict[str, Any]:
        return self._mutate(
            operation="rename",
            payload={"title": _title(title)},
            expected_revision=expected_revision,
            mutation_id=mutation_id,
        )

    def set_archived(
        self, *, archived: bool, expected_revision: int, mutation_id: str
    ) -> dict[str, Any]:
        return self._mutate(
            operation="archive",
            payload={"archived": archived},
            expected_revision=expected_revision,
            mutation_id=mutation_id,
        )

    def mark_read(
        self, *, expected_revision: int, mutation_id: str
    ) -> dict[str, Any]:
        return self._mutate(
            operation="mark_read",
            payload={},
            expected_revision=expected_revision,
            mutation_id=mutation_id,
        )

    def note_visible_activity(
        self, *, fact_id: str, source: str, source_revision: int
    ) -> dict[str, Any]:
        fact_id = _identity(fact_id, "activity_id")
        source = _identity(source, "activity_source")
        source_revision = _counter(source_revision, "activity_source_revision")
        digest = hashlib.sha256(
            f"{source}\0{source_revision}\0{fact_id}".encode()
        ).hexdigest()[:40]
        return self._mutate(
            operation="visible_activity",
            payload={
                "fact_id": fact_id,
                "source": source,
                "source_revision": source_revision,
            },
            expected_revision=None,
            mutation_id=f"activity-{digest}",
        )


__all__ = [
    "CONTRACT",
    "LOCK_FILE",
    "MAX_STORE_BYTES",
    "STORE_FILE",
    "NativeConversationUiError",
    "NativeConversationUiStore",
]
