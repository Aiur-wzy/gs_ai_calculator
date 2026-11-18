"""BM01 review queue for external facts; this module never accesses the network.

Model surface: submit(...) and poll(...). Bind run_id and actual_call_id in trusted
host code, never in the model tool schema. All host_* methods are reviewer-only.
The reviewer fetches pages separately and registers exact raw text. Semantic
review is a human/high-capability-reviewer responsibility: offsets and hashes
prove excerpt fidelity, not that a passage is free of benchmark answers.

Offsets count Python Unicode code points; end is exclusive. SHA-256 hashes the
exact raw text encoded as UTF-8, preserving whitespace and newlines.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

_WORKSPACE = Path(__file__).resolve().parents[3]
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z")
_REQUEST_ID = re.compile(r"req_[0-9a-f]{32}\Z")
_RAW_ID = re.compile(r"raw_[0-9a-f]{32}\Z")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)),
             *(f"LPT{i}" for i in range(10))}
_METADATA = {"title", "url", "retrieved_at", "source_date", "version"}


class BrokerError(ValueError):
    def __init__(self, reason: str, message: str):
        self.reason = reason
        super().__init__(message)


def _fail(reason: str, message: str) -> None:
    raise BrokerError(reason, message)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _text(value: object, label: str, limit: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        _fail("invalid_input", f"Invalid {label}")
    if "\x00" in value:
        _fail("invalid_input", f"Invalid {label}")
    return value


def _no_links(path: Path) -> None:
    for item in reversed((path, *path.parents)):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            _fail("path_denied", "Links and reparse points are not allowed")
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            _fail("path_denied", "Hard links are not allowed")


class LookupBroker:
    """Private queue scoped to one run. Constructor paths are HOST ONLY.

    A single broker root must not be registered in the model's file tool. Model
    log records are stored separately from raw/reviewer evidence under this root.
    Separate processes can open the same run root; a short exclusive lock guards
    complete JSON reads/writes and audit appends. A stale lock fails closed.
    """

    def __init__(self, private_root: Path, run_id: str):
        if not isinstance(run_id, str) or not _ID.fullmatch(run_id) or run_id.upper() in _RESERVED:
            _fail("invalid_run_id", "Invalid run identifier")
        requested = Path(private_root).absolute()
        _no_links(requested)
        root = requested.resolve()
        if not root.is_relative_to(_WORKSPACE.resolve()) or root == _WORKSPACE.resolve():
            _fail("path_denied", "Private queue must stay below the workspace")
        self.run_id = run_id
        self.root = root / run_id
        _no_links(self.root)
        for name in ("requests", "raw", "reviews"):
            self._path(name).mkdir(parents=True, exist_ok=True)

    def _path(self, *parts: str) -> Path:
        path = self.root.joinpath(*parts)
        _no_links(path)
        if not path.resolve().is_relative_to(self.root.resolve()):
            _fail("path_denied", "Path leaves the private queue")
        return path

    @contextmanager
    def _lock(self):
        path = self._path(".queue-lock")
        deadline = time.monotonic() + 2.0
        while True:
            try:
                handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(handle)
                break
            except FileExistsError:
                if time.monotonic() >= deadline:
                    _fail("queue_busy", "Queue is busy; retry later")
                time.sleep(0.025)
        try:
            yield
        finally:
            self._path(".queue-lock").unlink()

    def _write(self, path: Path, value: object) -> None:
        self._path(*path.relative_to(self.root).parts)
        if path.exists():
            _fail("immutable_record", "Queue records cannot be replaced")
        data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        temp = self._path(path.parent.name, ".pending-" + uuid.uuid4().hex)
        try:
            with temp.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()

    def _read(self, path: Path) -> dict:
        self._path(*path.relative_to(self.root).parts)
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            _fail("not_found", "Queue record not found for this run")
        except (ValueError, UnicodeError):
            _fail("corrupt_record", "Queue record is not valid JSON")
        if not isinstance(result, dict) or result.get("run_id") != self.run_id:
            _fail("corrupt_record", "Queue record has an invalid run identity")
        return result

    def _log(self, name: str, entry: dict) -> None:
        path = self._path(name)
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps({"at": _now(), "run_id": self.run_id, **entry}, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _request(self, request_id: str) -> dict:
        if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
            _fail("invalid_request_id", "Invalid request identifier")
        result = self._read(self._path("requests", request_id + ".json"))
        if result.get("request_id") != request_id:
            _fail("corrupt_record", "Queue request identity does not match")
        return result

    def model_dispatch(self, tool: str, arguments: dict, actual_call_id: str) -> dict:
        """Trusted runner adapter; refuses host methods and identity overrides."""
        if not isinstance(arguments, dict):
            _fail("invalid_input", "Lookup tool arguments must be an object")
        if tool == "web_lookup_submit":
            allowed = {"query", "local_reason", "local_checks", "intent", "requested_version"}
            required = allowed - {"requested_version"}
            if not required <= set(arguments) or not set(arguments) <= allowed:
                _fail("invalid_input", "Lookup request fields do not match the model contract")
            return self.submit(**arguments, actual_call_id=actual_call_id)
        if tool == "web_lookup_poll":
            if not {"request_id"} <= set(arguments) or not set(arguments) <= {"request_id", "wait_ms"}:
                _fail("invalid_input", "Lookup poll fields do not match the model contract")
            return self.poll(**arguments, actual_call_id=actual_call_id)
        _fail("unregistered_tool", "Only lookup submit and poll are model-callable")

    def submit(self, *, query: str, local_reason: str, local_checks: list[str],
               intent: str, actual_call_id: str, requested_version: str | None = None) -> dict:
        """MODEL: request a specific mechanism/base fact; returns no web content."""
        _text(actual_call_id, "host call identifier", 256)
        _text(query, "query", 2000)
        _text(local_reason, "local lookup reason", 4000)
        if intent not in ("mechanism", "base_fact"):
            _fail("invalid_input", "Lookup intent must be mechanism or base_fact")
        if not isinstance(local_checks, list) or not 1 <= len(local_checks) <= 20:
            _fail("invalid_input", "Record between 1 and 20 completed local checks")
        for value in local_checks:
            _text(value, "local check", 2000)
        if requested_version is not None:
            _text(requested_version, "requested version", 100)
        request_id = "req_" + uuid.uuid4().hex
        request = {"run_id": self.run_id, "request_id": request_id, "actual_call_id": actual_call_id,
                   "query": query, "local_reason": local_reason, "local_checks": list(local_checks),
                   "intent": intent, "requested_version": requested_version, "created_at": _now()}
        result = {"request_id": request_id, "status": "pending", "retry_after_ms": 1000}
        with self._lock():
            self._write(self._path("requests", request_id + ".json"), request)
            self._log("host-audit.jsonl", {"event": "submitted", "request": request})
            self._log("model-visible.jsonl", {"event": "submit", "actual_call_id": actual_call_id,
                                             "request": request, "response": result})
        return result


    def poll(self, request_id: str, *, actual_call_id: str, wait_ms: int = 0) -> dict:
        """MODEL: poll nonblocking by default, or wait at most 30 seconds.

        Pending/denied/empty replies contain no source title, URL, snippet, or raw
        evidence. The host call ID comes from the trusted runner, not model JSON.
        """
        _text(actual_call_id, "host call identifier", 256)
        if isinstance(wait_ms, bool) or not isinstance(wait_ms, int) or not 0 <= wait_ms <= 30000:
            _fail("invalid_input", "wait_ms must be between 0 and 30000")
        deadline = time.monotonic() + wait_ms / 1000
        while True:
            with self._lock():
                self._request(request_id)
                path = self._path("reviews", request_id + ".json")
                result = {"request_id": request_id, "status": "pending", "retry_after_ms": 1000}
                if path.exists():
                    review = self._read(path)
                    # Rebuild, including integrity checks, instead of trusting a
                    # stored arbitrary model-visible payload.
                    result = self._render_review(request_id, review)
                if result["status"] != "pending" or time.monotonic() >= deadline:
                    self._log("model-visible.jsonl", {"event": "poll", "actual_call_id": actual_call_id,
                                                      "request_id": request_id, "response": result})
                    return result
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))

    def host_pending(self) -> list[dict]:
        """HOST ONLY: outstanding requests, including the model's local checks."""
        with self._lock():
            result = []
            for path in sorted(self._path("requests").glob("req_*.json")):
                request = self._request(path.stem)
                if not self._path("reviews", path.name).exists():
                    result.append(request)
            return result

    def host_register_raw(self, request_id: str, *, text: str, title: str, url: str,
                          retrieved_at: str, source_date: str | None, version: str | None,
                          kind: str = "page") -> dict:
        """HOST ONLY: register exact fetched text; accepts no filesystem path."""
        _text(text, "raw text", 2_000_000)
        _text(title, "raw title", 3000, empty=True)
        _text(url, "source URL", 4000)
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            _fail("invalid_source", "Source must have an HTTP(S) URL without credentials")
        _text(retrieved_at, "retrieval date", 100)
        for label, value in (("source date", source_date), ("source version", version)):
            if value is not None:
                _text(value, label, 100)
        if kind not in ("page", "search_result", "video_transcript"):
            _fail("invalid_source", "Unsupported source kind")
        raw_id = "raw_" + uuid.uuid4().hex
        raw = {"run_id": self.run_id, "request_id": request_id, "raw_id": raw_id,
               "text": text, "text_sha256": _sha(text), "text_length": len(text), "title": title,
               "url": url, "retrieved_at": retrieved_at, "source_date": source_date,
               "version": version, "kind": kind}
        with self._lock():
            self._request(request_id)
            if self._path("reviews", request_id + ".json").exists():
                _fail("already_reviewed", "Request is already reviewed")
            self._write(self._path("raw", raw_id + ".json"), raw)
            self._log("host-audit.jsonl", {"event": "raw_registered", "request_id": request_id,
                                           "raw_id": raw_id, "text_sha256": raw["text_sha256"]})
        return {key: raw[key] for key in ("raw_id", "text_sha256", "text_length")}

    def host_raw(self, request_id: str, raw_id: str) -> dict:
        """HOST ONLY: read a registered raw record by opaque ID, never a path."""
        with self._lock():
            self._request(request_id)
            return self._raw(request_id, raw_id)

    def _raw(self, request_id: str, raw_id: str) -> dict:
        if not isinstance(raw_id, str) or not _RAW_ID.fullmatch(raw_id):
            _fail("invalid_raw_id", "Invalid raw identifier")
        raw = self._read(self._path("raw", raw_id + ".json"))
        if raw.get("request_id") != request_id or raw.get("raw_id") != raw_id:
            _fail("source_mismatch", "Source does not belong to this request")
        if not isinstance(raw.get("text"), str) or _sha(raw["text"]) != raw.get("text_sha256"):
            _fail("source_integrity", "Registered raw text failed integrity verification")
        return raw

    def _render_review(self, request_id: str, review: dict) -> dict:
        decision, outcome = review.get("decision"), review.get("outcome")
        if review.get("request_id") != request_id:
            _fail("corrupt_record", "Review request identity does not match")
        valid = {"allow": {"found", "conflict"}, "deny": {"denied"}, "empty": {"not_found", "conflict"}}
        if decision not in valid or outcome not in valid[decision]:
            _fail("invalid_review", "Review decision and outcome do not match")
        excerpts = review.get("excerpts")
        if not isinstance(excerpts, list) or len(excerpts) > 20:
            _fail("invalid_review", "Invalid excerpt list")
        result = {"request_id": request_id, "status": "reviewed", "decision": decision, "outcome": outcome}
        if decision != "allow":
            if excerpts:
                _fail("invalid_review", "Denied or empty reviews cannot expose excerpts")
            return result
        if not excerpts:
            _fail("invalid_review", "Allow requires at least one verified excerpt")
        visible = []
        for item in excerpts:
            if not isinstance(item, dict) or set(item) != {"raw_id", "raw_sha256", "start", "end", "excerpt", "approved_metadata"}:
                _fail("invalid_review", "Excerpt fields do not match the review contract")
            raw = self._raw(request_id, item["raw_id"])
            if item["raw_sha256"] != raw["text_sha256"]:
                _fail("source_integrity", "Review SHA does not match registered raw text")
            start, end = item["start"], item["end"]
            if any(isinstance(n, bool) or not isinstance(n, int) for n in (start, end)) or not 0 <= start < end <= len(raw["text"]):
                _fail("excerpt_bounds", "Excerpt offsets are outside the registered raw text")
            if end - start > 16000 or item["excerpt"] != raw["text"][start:end]:
                _fail("excerpt_mismatch", "Excerpt must exactly match the registered raw text slice")
            metadata = item["approved_metadata"]
            if not isinstance(metadata, dict) or not set(metadata) <= _METADATA or not {"url", "retrieved_at", "source_date", "version"} <= set(metadata):
                _fail("invalid_review", "Explicit approval of URL, dates and version is required")
            if any(metadata[key] != raw[key] for key in metadata):
                _fail("metadata_mismatch", "Approved metadata must match the registered source")
            visible.append({"raw_id": item["raw_id"], "raw_sha256": item["raw_sha256"],
                            "start": start, "end": end, "excerpt": item["excerpt"], **metadata})
        result["sources"] = visible
        return result

    def host_review(self, request_id: str, *, decision: str, outcome: str,
                    excerpts: list[dict], reviewer_id: str, review_note: str) -> dict:
        """HOST ONLY: approve literal excerpts after semantic answer screening.

        Review note and reviewer identity stay private. Title is optional in each
        approved_metadata, allowing a safe passage from a mixed page while hiding
        its answer-bearing title. All metadata must be separately reviewed.
        """
        _text(reviewer_id, "reviewer identifier", 256)
        _text(review_note, "private review note", 8000)
        review = {"run_id": self.run_id, "request_id": request_id, "decision": decision,
                  "outcome": outcome, "excerpts": excerpts, "reviewer_id": reviewer_id,
                  "review_note": review_note, "reviewed_at": _now(),
                  "semantic_review_required": True}
        with self._lock():
            self._request(request_id)
            result = self._render_review(request_id, review)
            self._write(self._path("reviews", request_id + ".json"), review)
            self._log("host-audit.jsonl", {"event": "reviewed", "review": review})
        return result


def model_tool_specs() -> list[dict]:
    """Explicit app-server dynamic tools; host review functions are excluded."""
    return [
        {"type": "function", "name": "web_lookup_submit",
         "description": "Request a specific mechanism or base fact after recording completed local checks. The host reviews sources for benchmark-answer leakage before releasing any content. Returns an opaque pending request ID, never raw search results. Do not request a completed team rotation, computed benchmark answer or observed score.",
         "inputSchema": {"type": "object", "additionalProperties": False,
                         "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 2000},
                                        "local_reason": {"type": "string", "minLength": 1, "maxLength": 4000},
                                        "local_checks": {"type": "array", "minItems": 1, "maxItems": 20, "items": {"type": "string", "minLength": 1, "maxLength": 2000}},
                                        "intent": {"type": "string", "enum": ["mechanism", "base_fact"]},
                                        "requested_version": {"type": ["string", "null"], "maxLength": 100}},
                         "required": ["query", "local_reason", "local_checks", "intent"]}},
        {"type": "function", "name": "web_lookup_poll",
         "description": "Poll one of this run's lookup IDs. Pending means continue independent work and poll later. Only an explicit allow decision exposes reviewed literal excerpts and approved source metadata; denied or empty responses carry no source content. Default wait is zero; maximum wait is 30 seconds.",
         "inputSchema": {"type": "object", "additionalProperties": False,
                         "properties": {"request_id": {"type": "string", "pattern": "^req_[0-9a-f]{32}$"},
                                        "wait_ms": {"type": "integer", "minimum": 0, "maximum": 30000}},
                         "required": ["request_id"]}},
    ]
