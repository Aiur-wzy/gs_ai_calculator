"""Exact user abandonment of old App Renderer recovery rights, not outcomes.

No intent is rewritten or acknowledged. A separate decision can release only
the exact incompatible workspace/intent shown to the user. It grants no DSH,
candidate, public-data or filesystem capability.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from genshin_app.equipment.files import fingerprint
from genshin_db.locking import file_lock

from .native_candidate_transaction_contracts import (
    APPLICATION_INTENT_FILE,
    MAX_APPLICATION_INTENT_BYTES,
)
from .native_dsh_mux_client import NativeDshMuxError
from .native_dsh_workspace_compatibility import (
    compatibility_issue,
    current_workspace_identity,
)
from .sessions import safe_path, write_owned

CONTRACT = "genshin-native-incompatible-application-retirements-v1"
_FILE = "decisions.json"
_MAX_BYTES = 4 * 1024 * 1024  # Bounded local decision journal, not a model budget.
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_RECORD_KEYS = {"conversation_id", "workspace_sha256", "intent_sha256", "current_identity_fingerprint",
    "expected_fingerprint", "decision", "decided_at", "state"}


def _fail(code):
    raise NativeDshMuxError("native_incompatible_application_" + code)


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("decision_journal_invalid")
        result[key] = value
    return result


class NativeIncompatibleApplicationRetirements:
    def __init__(self, root, *, intent_reader, workspace_validator=None, lock=None):
        self.root = Path(root).absolute()
        self.journal_root = safe_path(self.root, "incompatible-application-decisions")
        self._intent_reader = intent_reader
        self._workspace_validator = workspace_validator
        self._lock = lock if lock is not None else threading.RLock()

    def _path(self, conversation_id):
        if type(conversation_id) is not str:
            _fail("identity_invalid")
        try:
            if str(uuid.UUID(conversation_id)) != conversation_id:
                raise ValueError
        except ValueError:
            _fail("identity_invalid")
        return safe_path(self.root, conversation_id)

    @contextmanager
    def _journal_lock(self, *, create=False):
        # A read of an unused repository creates no directory. Once present,
        # reads share the writer lock (also important for Windows replace).
        if create:
            self.journal_root.mkdir(parents=True, exist_ok=True)
        if self.journal_root.exists():
            with file_lock(safe_path(self.journal_root, ".decisions.lock")):
                yield
        else:
            yield

    def _read(self):
        if not safe_path(self.journal_root, _FILE).exists():
            return {"contract": CONTRACT, "records": {}}
        try:
            row = json.loads(self._raw(self.journal_root, _FILE, _MAX_BYTES).decode("utf-8"),
                object_pairs_hook=_unique, parse_constant=lambda _: _fail("decision_journal_invalid"))
        except (UnicodeError, ValueError, RecursionError):
            _fail("decision_journal_invalid")
        if type(row) is not dict or set(row) != {"contract", "records"} or row["contract"] != CONTRACT or type(row["records"]) is not dict:
            _fail("decision_journal_invalid")
        for key, record in row["records"].items():
            if (type(record) is not dict or set(record) != _RECORD_KEYS or record["decision"] != "abandon_recovery"
                    or record["state"] != "result_unknown" or type(record["decided_at"]) is not str
                    or not record["decided_at"].endswith("+00:00")
                    or any(type(record[name]) is not str or _HASH.fullmatch(record[name]) is None
                        for name in ("workspace_sha256", "intent_sha256", "current_identity_fingerprint", "expected_fingerprint"))):
                _fail("decision_journal_invalid")
            self._path(record["conversation_id"])
            if key != record["expected_fingerprint"] or key != self._fingerprint(record):
                _fail("decision_journal_invalid")
        return row

    @staticmethod
    def _fingerprint(record):
        return fingerprint({"contract": CONTRACT, **{key: record[key] for key in (
            "conversation_id", "workspace_sha256", "intent_sha256", "current_identity_fingerprint")}})

    @staticmethod
    def _raw(root, name, maximum):
        try:
            with safe_path(root, name).open("rb") as stream:
                raw = stream.read(maximum + 1)
        except OSError:
            raise NativeDshMuxError("native_incompatible_application_source_invalid") from None
        if len(raw) > maximum:
            _fail("source_invalid")
        return raw

    def _evidence(self, path, intent=None):
        if self._workspace_validator is None:
            return None
        path = Path(path).absolute()
        if path != self._path(path.name):
            _fail("identity_invalid")
        # The injected validator checks the complete known workspace schema,
        # Session/path identity and compatibility without activating it. Normal
        # current workspaces do not reread/hash their intent for this feature.
        try:
            self._workspace_validator(path)
        except NativeDshMuxError as error:
            if compatibility_issue(error) is None:
                raise
        else:
            return None  # Current compatible records must use normal recovery.
        workspace_raw = self._raw(path, "dsh-workspace.json", 64 * 1024)
        intent_raw = self._raw(path, APPLICATION_INTENT_FILE, MAX_APPLICATION_INTENT_BYTES)
        # Validate the captured version as well, so replacement between the
        # cheap eligibility check and byte capture cannot gain abandonment.
        try:
            self._workspace_validator(path)
        except NativeDshMuxError as error:
            if compatibility_issue(error) is None:
                raise
        else:
            return None
        checked = self._intent_reader(path, path.name)["intent"]
        if checked is None or checked["state"] not in {"prepared", "baseline_advanced"}:
            return None
        if intent is not None and fingerprint(intent) != fingerprint(checked):
            return None
        if (self._raw(path, "dsh-workspace.json", 64 * 1024) != workspace_raw
                or self._raw(path, APPLICATION_INTENT_FILE, MAX_APPLICATION_INTENT_BYTES) != intent_raw):
            _fail("stale")
        record = {"conversation_id": path.name, "workspace_sha256": _hash(workspace_raw),
            "intent_sha256": _hash(intent_raw), "current_identity_fingerprint": fingerprint(current_workspace_identity())}
        record["expected_fingerprint"] = self._fingerprint(record)
        return record

    def is_retired(self, path, intent):
        with self._lock, self._journal_lock():
            evidence = self._evidence(path, intent)
            return evidence is not None and self._prior(self._read()["records"], evidence) is not None

    @staticmethod
    def _prior(records, evidence):
        # The user abandoned this exact old recovery once. Later App versions
        # must not revive it; current identity belongs to the click CAS/audit.
        return next((record for record in records.values() if all(record[key] == evidence[key]
            for key in ("conversation_id", "workspace_sha256", "intent_sha256"))), None)

    def list_pending(self):
        with self._lock, self._journal_lock():
            if self._workspace_validator is None or not self.root.is_dir():
                return {"items": []}
            decisions = self._read()["records"]
            items = []
            for path in sorted(self.root.iterdir()):
                if len(path.name) != 36 or not safe_path(path, "dsh-workspace.json").is_file():
                    continue
                if not safe_path(path, APPLICATION_INTENT_FILE).is_file():
                    continue
                evidence = self._evidence(path)
                if evidence is None or self._prior(decisions, evidence) is not None:
                    continue
                items.append({"conversation_id": path.name, "expected_fingerprint": evidence["expected_fingerprint"],
                    "state": "result_unknown", "display_name": "旧版本未确认的同步",
                    "message": "旧版本曾准备同步，但结果尚未确认。放弃恢复只解除占用，不代表旧同步成功，也不会还原已经发生的修改。"})
            return {"items": items}

    def abandon(self, conversation_id, expected_fingerprint, decision):
        if decision != "abandon_recovery":
            _fail("decision_invalid")
        if type(expected_fingerprint) is not str or _HASH.fullmatch(expected_fingerprint) is None:
            _fail("fingerprint_invalid")
        with self._lock:
            path = self._path(conversation_id)
            evidence = self._evidence(path)
            if evidence is None:
                _fail("not_eligible")
            if evidence["expected_fingerprint"] != expected_fingerprint:
                _fail("stale")
            with self._journal_lock(create=True):
                records = self._read()
                prior = self._prior(records["records"], evidence)
                if prior is None:
                    records["records"][expected_fingerprint] = {**evidence, "decision": decision,
                        "state": "result_unknown", "decided_at": datetime.now(UTC).isoformat()}
                    write_owned(self.journal_root, _FILE, records, _MAX_BYTES)
                return {"conversation_id": conversation_id, "status": "recovery_abandoned", "state": "result_unknown",
                    "decision_receipt": expected_fingerprint if prior is None else prior["expected_fingerprint"],
                    "disposition": "recorded" if prior is None else "replay"}
