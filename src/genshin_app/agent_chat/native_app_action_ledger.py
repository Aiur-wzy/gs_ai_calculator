"""Durable one-shot App operation cards for the native V2 Agent.

The model can request a card but never supplies or receives a filesystem path.
Only the renderer may present the existing App file UI, and only an App result
for the same immutable card can complete it. Each operation binds the formal
document, captured input snapshot or exact applied result that it consumes.
Unrelated candidate edits and reading-focus changes do not expire file cards.
"""

from __future__ import annotations

import copy
import json
import re
import secrets
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from genshin_app.equipment.files import json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_db.locking import file_lock

from .native_session_store import redact_visible_text
from .sessions import safe_path, write_owned

CONTRACT = "genshin-native-app-action-ledger-v2"
LEDGER_FILE = "native-app-action-ledger-v2.json"
LOCK_FILE = ".native-app-action-ledger-v2.lock"
LEGACY_CONTRACT = "genshin-native-app-action-ledger-v1"
LEGACY_LEDGER_FILE = "native-app-action-ledger-v1.json"
MAX_LEDGER_BYTES = 2 * 1024 * 1024
MAX_CARDS = 256

ACTION_TYPES = frozenset(
    {
        "save_current",
        "save_as",
        "open_document",
        "export_result",
        "export_candidate_input_draft",
        "navigate_rotation",
        "navigate_result",
        "navigate_whiteboard",
    }
)
FILE_ACTIONS = frozenset(
    {"save_current", "save_as", "open_document", "export_result", "export_candidate_input_draft"}
)
ACCEPTED_CANDIDATE_ACTIONS = frozenset(
    {"export_result"}
)
_PENDING = frozenset({"queued", "presented"})
_TERMINAL = frozenset({"completed", "cancelled", "failed", "expired"})
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_CARD_ID = re.compile(r"action_[A-Za-z0-9_-]{32}\Z")
_UI_RECEIPT = re.compile(r"ui_[A-Za-z0-9_-]{43}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z"
)
_RESERVED_BASENAME = re.compile(
    r"(?i)(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z"
)
_ILLEGAL_BASENAME = re.compile(r"[\\/:*?\"<>|\x00-\x1f]")
_SENSITIVE_PREFIXES = (
    "access-token:",
    "api-key:",
    "api_key:",
    "apikey:",
    "authorization:",
    "basic:",
    "bearer:",
    "cookie:",
    "credential:",
    "password:",
    "proxy-authorization:",
    "secret:",
    "sk-",
    "token:",
    "wf_",
    "x-api-key:",
)


class NativeAppActionError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise NativeAppActionError(code)


def _identity(value: object, field: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        _fail(f"app_action_{field}_invalid")
    if value.lower().startswith(_SENSITIVE_PREFIXES):
        _fail(f"app_action_{field}_invalid")
    return value


def _counter(value: object, field: str) -> int:
    if type(value) is not int or not 0 <= value <= 2**53 - 1:
        _fail(f"app_action_{field}_invalid")
    return value


def _optional_hash(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        _fail(f"app_action_{field}_invalid")
    return value


def _timestamp(value: object, field: str) -> tuple[str, datetime]:
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        _fail(f"app_action_{field}_invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except (OverflowError, ValueError):
        _fail(f"app_action_{field}_invalid")
    if parsed.tzinfo is None:
        _fail(f"app_action_{field}_invalid")
    return value, parsed.astimezone(UTC)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _new_action_id() -> str:
    return "action_" + secrets.token_urlsafe(24)


def _new_ui_receipt() -> str:
    return "ui_" + secrets.token_urlsafe(32)


def sanitize_suggested_basename(value: object) -> str | None:
    """Return a path-free Windows-safe suggestion, never a directory or path."""

    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 1024:
        _fail("app_action_suggested_name_invalid")
    name = _ILLEGAL_BASENAME.sub("_", value).strip().rstrip(". ")
    while "__" in name:
        name = name.replace("__", "_")
    if not name:
        name = "方案"
    device_stem = name.split(".", 1)[0].rstrip(" .")
    if _RESERVED_BASENAME.fullmatch(name) or _RESERVED_BASENAME.fullmatch(
        device_stem,
    ):
        name = f"_{name}"
    if len(name) > 128:
        name = name[:128].rstrip(". ") or "方案"
    name = redact_visible_text(name)
    # Windows filenames are measured in UTF-16 code units.  Keep the same
    # public 128-character ceiling while preventing an astral-only suggestion
    # from exceeding that boundary after the App adds an extension.
    try:
        while len(name.encode("utf-16-le")) // 2 > 128:
            name = name[:-1]
    except UnicodeEncodeError:
        _fail("app_action_suggested_name_invalid")
    if not name:
        name = "方案"
    return name


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _binding(value: object) -> dict[str, Any]:
    required = {
        "document_id",
        "candidate_revision",
        "stage_revision",
        "receipt_fingerprint",
        "applied_receipt_fingerprint",
    }
    if (not isinstance(value, Mapping) or not required <= set(value)
            or set(value) - required - {"result_payload_digest"}):
        _fail("app_action_binding_invalid")
    result = {
        "document_id": _identity(value.get("document_id"), "document_id"),
        "candidate_revision": _counter(
            value.get("candidate_revision"), "candidate_revision",
        ),
        "stage_revision": _counter(value.get("stage_revision"), "stage_revision"),
        "receipt_fingerprint": _optional_hash(
            value.get("receipt_fingerprint"), "receipt_fingerprint",
        ),
        "applied_receipt_fingerprint": _optional_hash(
            value.get("applied_receipt_fingerprint"),
            "applied_receipt_fingerprint",
        ),
    }
    if "result_payload_digest" in value:
        result["result_payload_digest"] = _optional_hash(value["result_payload_digest"], "result_payload_digest")
    return result


def _workspace(value: object) -> dict[str, Any]:
    fields = {
        "document_kind",
        "display_name",
        "current_view",
        "dirty",
        "has_path",
        "can_save",
        "can_open",
        "can_export",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail("app_action_workspace_invalid")
    kind = value.get("document_kind")
    display_name = value.get("display_name")
    current_view = value.get("current_view")
    if kind not in {"whiteboard", "native"}:
        _fail("app_action_workspace_invalid")
    if (
        not isinstance(display_name, str)
        or not display_name
        or len(display_name) > 256
        or sanitize_suggested_basename(display_name) != display_name
        or redact_visible_text(display_name) != display_name
    ):
        _fail("app_action_workspace_invalid")
    if (
        not isinstance(current_view, str)
        or not current_view
        or len(current_view) > 64
        or _ILLEGAL_BASENAME.search(current_view) is not None
        or redact_visible_text(current_view) != current_view
    ):
        _fail("app_action_workspace_invalid")
    result = {
        "document_kind": kind,
        "display_name": display_name,
        "current_view": current_view,
    }
    for field in fields - {"document_kind", "display_name", "current_view"}:
        item = value.get(field)
        if type(item) is not bool:
            _fail("app_action_workspace_invalid")
        result[field] = item
    return result


def normalize_action_binding(value: object) -> dict[str, Any]:
    """Return a detached, strict operation-card binding."""

    return _binding(value)


def action_binding_matches(card: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    """Compare only the domain object consumed by this particular operation.

    A formal-document save does not consume the isolated candidate. A draft
    export consumes the immutable target captured in its card, whose contents
    the snapshot store verifies separately. Neither should expire merely
    because the Agent continues editing or changes its reading focus.
    Result export still consumes an exact applied-result receipt.
    """
    previous = card["binding"]
    if card["action"] == "export_result":
        if previous.get("result_payload_digest") is not None:
            return (previous["document_id"] == current["document_id"]
                    and current["applied_receipt_fingerprint"] is not None
                    and previous["result_payload_digest"] == current.get("result_payload_digest"))
        return (previous["document_id"] == current["document_id"]
                and previous["applied_receipt_fingerprint"] is not None
                and previous["applied_receipt_fingerprint"] == current["applied_receipt_fingerprint"])
    return previous["document_id"] == current["document_id"]


def normalize_action_workspace(value: object) -> dict[str, Any]:
    """Return a detached, path-free renderer workspace projection."""

    return _workspace(value)


def normalize_action_target(action, binding, value=None):
    """A closed, path-free target; callers cannot make Save mean a candidate."""
    if action == "export_candidate_input_draft":
        fields = {"kind", "workspace_id", "artifact_id", "candidate_revision", "stage_revision",
                  "candidate_fingerprint", "input_snapshot_fingerprint", "compatibility_fingerprint", "database_fingerprint"}
        if type(value) is not dict or set(value) != fields or value.get("kind") != "candidate_input_draft":
            _fail("app_action_target_invalid")
        for key in fields - {"kind", "workspace_id", "artifact_id", "candidate_revision", "stage_revision"}:
            if _optional_hash(value[key], key) is None:
                _fail("app_action_target_invalid")
        for key in ("workspace_id", "artifact_id"):
            _identity(value[key], key)
        for key in ("candidate_revision", "stage_revision"):
            _counter(value[key], key)
        return copy.deepcopy(value)
    expected = {"kind": "accepted_result" if action == "export_result" else
                "navigation" if action.startswith("navigate_") else "formal_document",
                "document_id": binding["document_id"]}
    if action == "export_result":
        expected.update(candidate_revision=binding["candidate_revision"], receipt_fingerprint=binding["receipt_fingerprint"])
        if binding.get("result_payload_digest") is not None:
            expected["result_payload_digest"] = binding["result_payload_digest"]
    if action.startswith("navigate_"):
        expected["view"] = action.removeprefix("navigate_")
    if value is not None and value != expected:
        _fail("app_action_target_mismatch")
    return expected


def _card(value: object, *, legacy=False) -> dict[str, Any]:
    fields = {
        "action_id",
        "conversation_id",
        "action",
        "reason",
        "suggested_name",
        "status",
        "binding",
        "workspace",
        "created_at",
        "presented_at",
        "settled_at",
        "result_message",
        "ui_receipt",
        "dirty_open_guard_required",
    }
    if not legacy:
        fields.add("target")
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail("app_action_card_invalid")
    action_id = value.get("action_id")
    if not isinstance(action_id, str) or _CARD_ID.fullmatch(action_id) is None:
        _fail("app_action_card_invalid")
    action = value.get("action")
    status = value.get("status")
    reason = value.get("reason")
    if action not in ACTION_TYPES or status not in _PENDING | _TERMINAL:
        _fail("app_action_card_invalid")
    if legacy and action == "export_candidate_input_draft":
        _fail("app_action_card_invalid")
    if (
        not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > 1000
        or redact_visible_text(reason) != reason
    ):
        _fail("app_action_card_invalid")
    suggested = value.get("suggested_name")
    if sanitize_suggested_basename(suggested) != suggested:
        _fail("app_action_card_invalid")
    created, created_time = _timestamp(value.get("created_at"), "created_at")
    presented = value.get("presented_at")
    settled = value.get("settled_at")
    presented_time = None
    settled_time = None
    if presented is not None:
        presented, presented_time = _timestamp(presented, "presented_at")
    if settled is not None:
        settled, settled_time = _timestamp(settled, "settled_at")
    if presented_time is not None and presented_time < created_time:
        _fail("app_action_card_invalid")
    prior_time = presented_time or created_time
    if settled_time is not None and settled_time < prior_time:
        _fail("app_action_card_invalid")
    if status == "queued" and (presented is not None or settled is not None):
        _fail("app_action_card_invalid")
    if status == "presented" and (presented is None or settled is not None):
        _fail("app_action_card_invalid")
    if status in _TERMINAL and settled is None:
        _fail("app_action_card_invalid")
    if status in {"completed", "cancelled", "failed"} and presented is None:
        _fail("app_action_card_invalid")
    ui_receipt = value.get("ui_receipt")
    dirty_guard = value.get("dirty_open_guard_required")
    if ui_receipt is not None and (
        not isinstance(ui_receipt, str)
        or _UI_RECEIPT.fullmatch(ui_receipt) is None
    ):
        _fail("app_action_card_invalid")
    if dirty_guard is not None and type(dirty_guard) is not bool:
        _fail("app_action_card_invalid")
    if status == "queued" and (ui_receipt is not None or dirty_guard is not None):
        _fail("app_action_card_invalid")
    if status in {"presented", "completed", "cancelled", "failed"} and (
        ui_receipt is None or dirty_guard is None
    ):
        _fail("app_action_card_invalid")
    if status == "expired" and (
        (presented is None and (ui_receipt is not None or dirty_guard is not None))
        or (presented is not None and (ui_receipt is None or dirty_guard is None))
    ):
        _fail("app_action_card_invalid")
    if dirty_guard is True and action != "open_document":
        _fail("app_action_card_invalid")
    message = value.get("result_message")
    if (
        not isinstance(message, str)
        or len(message) > 1000
        or redact_visible_text(message) != message
    ):
        _fail("app_action_card_invalid")
    if status in _PENDING and message:
        _fail("app_action_card_invalid")
    binding = _binding(value.get("binding"))
    workspace = _workspace(value.get("workspace"))
    capability = _capability(action)
    if capability is not None and workspace[capability] is not True:
        _fail("app_action_card_invalid")
    if action == "save_current" and workspace["has_path"] is not True:
        _fail("app_action_card_invalid")
    if (
        action in ACCEPTED_CANDIDATE_ACTIONS
        and (
            binding["receipt_fingerprint"] is None
            or binding["applied_receipt_fingerprint"]
            != binding["receipt_fingerprint"]
        )
    ):
        _fail("app_action_card_invalid")
    result = {
        "action_id": action_id,
        "conversation_id": _identity(value.get("conversation_id"), "conversation_id"),
        "action": action,
        "reason": reason.strip(),
        "suggested_name": suggested,
        "status": status,
        "binding": binding,
        "workspace": workspace,
        "created_at": created,
        "presented_at": presented,
        "settled_at": settled,
        "result_message": message,
        "ui_receipt": ui_receipt,
        "dirty_open_guard_required": dirty_guard,
    }
    if not legacy:
        result["target"] = normalize_action_target(action, binding, value["target"])
    return result


def _ledger(value: object, *, conversation_id: str, legacy=False) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "contract", "conversation_id", "revision", "cards",
    }:
        _fail("app_action_ledger_invalid")
    contract = LEGACY_CONTRACT if legacy else CONTRACT
    if value.get("contract") != contract:
        _fail("app_action_ledger_invalid")
    if value.get("conversation_id") != conversation_id:
        _fail("app_action_ledger_identity_mismatch")
    revision = _counter(value.get("revision"), "ledger_revision")
    cards = value.get("cards")
    if not isinstance(cards, list) or len(cards) > MAX_CARDS:
        _fail("app_action_ledger_invalid")
    checked = [_card(item, legacy=legacy) for item in cards]
    if any(item["conversation_id"] != conversation_id for item in checked):
        _fail("app_action_ledger_identity_mismatch")
    identities = [item["action_id"] for item in checked]
    if len(set(identities)) != len(identities):
        _fail("app_action_ledger_invalid")
    result = {
        "contract": contract,
        "conversation_id": conversation_id,
        "revision": revision,
        "cards": checked,
    }
    if len(json_bytes(result)) > MAX_LEDGER_BYTES:
        _fail("app_action_ledger_too_large")
    return result


def _public(card: Mapping[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(dict(card))


def _capability(action: str) -> str | None:
    if action in {"save_current", "save_as"}:
        return "can_save"
    if action == "open_document":
        return "can_open"
    if action == "export_result":
        return "can_export"
    return None


class NativeAppActionLedger:
    """One conversation's persistent, path-free App operation-card ledger."""

    def __init__(
        self,
        root: str | Path,
        *,
        conversation_id: str,
        clock: Callable[[], str] = _now,
        id_factory: Callable[[], str] = _new_action_id,
        ui_receipt_factory: Callable[[], str] = _new_ui_receipt,
        legacy_recovery: bool = False,
    ) -> None:
        self.root = Path(root).absolute()
        self.conversation_id = _identity(conversation_id, "conversation_id")
        self.legacy_recovery = legacy_recovery
        self.file_name = LEGACY_LEDGER_FILE if legacy_recovery else LEDGER_FILE
        self.lock_name = "." + self.file_name.removesuffix(".json") + ".lock"
        try:
            self.path = safe_path(self.root, self.file_name)
            self.lock_path = safe_path(self.root, self.lock_name)
        except (OSError, PlanError):
            _fail("app_action_storage_invalid")
        self._clock = clock
        self._id_factory = id_factory
        self._ui_receipt_factory = ui_receipt_factory

    def _checked_path(self, name: str) -> Path:
        try:
            return safe_path(self.root, name)
        except (OSError, PlanError):
            _fail("app_action_storage_invalid")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Revalidate fixed storage paths before and after taking the lock.

        ``safe_path`` rejects directory links, reparse points and hard-linked
        files.  Repeating it here prevents a ledger or lock path swapped after
        construction from silently redirecting locking or persistence.
        """

        lock_path = self._checked_path(self.lock_name)
        self._checked_path(self.file_name)
        with file_lock(lock_path):
            if self._checked_path(self.lock_name) != lock_path:
                _fail("app_action_storage_invalid")
            if self._checked_path(self.file_name) != self.path:
                _fail("app_action_storage_invalid")
            yield

    def _read_unlocked(self) -> dict[str, Any]:
        if self._checked_path(self.file_name) != self.path:
            _fail("app_action_storage_invalid")
        try:
            if not self.path.exists():
                return {
                    "contract": LEGACY_CONTRACT if self.legacy_recovery else CONTRACT,
                    "conversation_id": self.conversation_id,
                    "revision": 0,
                    "cards": [],
                }
            if self.path.is_symlink() or self.path.stat().st_size > MAX_LEDGER_BYTES:
                _fail("app_action_storage_invalid")
            value = json.loads(
                self.path.read_text(encoding="utf-8"),
                object_pairs_hook=_strict_object,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
        except (OSError, UnicodeError, ValueError, RecursionError):
            _fail("app_action_storage_invalid")
        return _ledger(value, conversation_id=self.conversation_id, legacy=self.legacy_recovery)

    def _write_unlocked(self, value: Mapping[str, Any]) -> None:
        checked = _ledger(value, conversation_id=self.conversation_id, legacy=self.legacy_recovery)
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            write_owned(self.root, self.file_name, checked, MAX_LEDGER_BYTES)
        except PlanError:
            _fail("app_action_storage_invalid")

    def read(self) -> dict[str, Any]:
        with self._locked():
            return copy.deepcopy(self._read_unlocked())

    def request(
        self,
        *,
        expected_revision: int,
        action: str,
        reason: str,
        suggested_name: str | None,
        binding: Mapping[str, Any],
        workspace: Mapping[str, Any],
        target: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.legacy_recovery:
            _fail("app_action_legacy_read_only")
        _counter(expected_revision, "expected_revision")
        if action not in ACTION_TYPES:
            _fail("app_action_type_invalid")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            _fail("app_action_reason_invalid")
        safe_reason = redact_visible_text(reason.strip())
        if not safe_reason:
            _fail("app_action_reason_invalid")
        checked_binding = _binding(binding)
        checked_workspace = _workspace(workspace)
        suggested = sanitize_suggested_basename(suggested_name)
        notice_code = None
        if action == "save_current" and checked_workspace["has_path"] is not True:
            # A path-free document cannot satisfy save_current.  Normalize the
            # request before it enters the durable ledger so every downstream
            # consumer sees the same user-mediated Save As operation instead
            # of a generic tool failure.
            action = "save_as"
            notice_code = "save_as_required"
        capability = _capability(action)
        checked_target = normalize_action_target(action, checked_binding, target)
        if capability is not None and checked_workspace[capability] is not True:
            _fail("app_action_workspace_capability_unavailable")
        if (
            action in ACCEPTED_CANDIDATE_ACTIONS
            and (
                checked_binding["receipt_fingerprint"] is None
                or checked_binding["applied_receipt_fingerprint"]
                != checked_binding["receipt_fingerprint"]
            )
        ):
            _fail("app_action_candidate_not_accepted")
        with self._locked():
            ledger = self._read_unlocked()
            duplicate = next(
                (
                    item
                    for item in ledger["cards"]
                    if item["status"] in _PENDING
                    and item["action"] == action
                    and item["suggested_name"] == suggested
                    and action_binding_matches(item, checked_binding)
                    and item["target"] == checked_target
                ),
                None,
            )
            if duplicate is not None:
                return {
                    "status": (
                        "already_queued"
                        if duplicate["status"] == "queued"
                        else "already_presented"
                    ),
                    "ledger_revision": ledger["revision"],
                    "card": _public(duplicate),
                    **({"notice_code": notice_code} if notice_code else {}),
                }
            if ledger["revision"] != expected_revision:
                _fail("app_action_stale_revision")
            if len(ledger["cards"]) >= MAX_CARDS:
                removable = next(
                    (index for index, item in enumerate(ledger["cards"])
                     if item["status"] in _TERMINAL),
                    None,
                )
                if removable is None:
                    _fail("app_action_queue_full")
                ledger["cards"].pop(removable)
            action_id = self._id_factory()
            if not isinstance(action_id, str) or _CARD_ID.fullmatch(action_id) is None:
                _fail("app_action_id_generation_failed")
            if any(item["action_id"] == action_id for item in ledger["cards"]):
                _fail("app_action_id_generation_failed")
            card = _card(
                {
                    "action_id": action_id,
                    "conversation_id": self.conversation_id,
                    "action": action,
                    "reason": safe_reason,
                    "suggested_name": suggested,
                    "status": "queued",
                    "binding": checked_binding,
                    "target": checked_target,
                    "workspace": checked_workspace,
                    "created_at": self._clock(),
                    "presented_at": None,
                    "settled_at": None,
                    "result_message": "",
                    "ui_receipt": None,
                    "dirty_open_guard_required": None,
                }
            )
            ledger["cards"].append(card)
            ledger["revision"] += 1
            self._write_unlocked(ledger)
            return {
                "status": "queued_for_user",
                "ledger_revision": ledger["revision"],
                "card": _public(card),
                **({"notice_code": notice_code} if notice_code else {}),
            }

    def refresh_binding(
        self,
        *,
        current_binding: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """Expire every pending card whose immutable domain binding changed."""

        binding = _binding(current_binding)
        if self.legacy_recovery:
            return []
        with self._locked():
            ledger = self._read_unlocked()
            expired: list[dict[str, Any]] = []
            for card in ledger["cards"]:
                if card["status"] not in _PENDING or action_binding_matches(card, binding):
                    continue
                card["status"] = "expired"
                card["settled_at"] = self._clock()
                card["result_message"] = "文档、候选、阶段或接纳回执已经变化。"
                expired.append(_public(card))
            if expired:
                ledger["revision"] += 1
                self._write_unlocked(ledger)
            return expired

    def expire_pending(
        self,
        *,
        result_message: str,
        exclude_action_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Expire pending cards after the Renderer leaves this document.

        A successful native ``open_document`` cannot safely prove the newly
        opened document through the old conversation binding.  The old
        conversation therefore retires every other pending card explicitly.
        """

        if exclude_action_id is not None and (
            not isinstance(exclude_action_id, str)
            or _CARD_ID.fullmatch(exclude_action_id) is None
        ):
            _fail("app_action_id_invalid")
        if not isinstance(result_message, str) or len(result_message) > 1000:
            _fail("app_action_result_message_invalid")
        safe_message = redact_visible_text(result_message)
        with self._locked():
            ledger = self._read_unlocked()
            expired: list[dict[str, Any]] = []
            for card in ledger["cards"]:
                if (
                    card["status"] not in _PENDING
                    or card["action_id"] == exclude_action_id
                ):
                    continue
                card["status"] = "expired"
                card["settled_at"] = self._clock()
                card["result_message"] = safe_message
                expired.append(_public(card))
            if expired:
                ledger["revision"] += 1
                self._write_unlocked(ledger)
            return expired

    def present(
        self,
        *,
        action_id: str,
        agent_turn_active: bool,
        current_binding: Mapping[str, Any],
        current_workspace: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self.legacy_recovery:
            _fail("app_action_legacy_read_only")
        if not isinstance(action_id, str) or _CARD_ID.fullmatch(action_id) is None:
            _fail("app_action_id_invalid")
        if type(agent_turn_active) is not bool:
            _fail("app_action_turn_state_invalid")
        if agent_turn_active:
            _fail("app_action_turn_active")
        binding = _binding(current_binding)
        workspace = _workspace(current_workspace)
        with self._locked():
            ledger = self._read_unlocked()
            card = next(
                (item for item in ledger["cards"] if item["action_id"] == action_id),
                None,
            )
            if card is None:
                _fail("app_action_not_presentable")
            if card["status"] == "presented":
                if not action_binding_matches(card, binding):
                    card["status"] = "expired"
                    card["settled_at"] = self._clock()
                    card["result_message"] = "操作卡绑定已经变化。"
                    ledger["revision"] += 1
                    self._write_unlocked(ledger)
                    _fail("app_action_expired")
                return {
                    "status": "already_presented",
                    "ledger_revision": ledger["revision"],
                    "card": _public(card),
                    "workspace": copy.deepcopy(workspace),
                }
            if card["status"] in _TERMINAL:
                return {
                    "status": "already_settled",
                    "ledger_revision": ledger["revision"],
                    "card": _public(card),
                    "workspace": copy.deepcopy(workspace),
                }
            if card["status"] != "queued":  # pragma: no cover - validator guard
                _fail("app_action_not_presentable")
            if not action_binding_matches(card, binding):
                card["status"] = "expired"
                card["settled_at"] = self._clock()
                card["result_message"] = "操作卡绑定已经变化。"
                ledger["revision"] += 1
                self._write_unlocked(ledger)
                _fail("app_action_expired")
            capability = _capability(card["action"])
            if capability is not None and workspace[capability] is not True:
                card["status"] = "expired"
                card["settled_at"] = self._clock()
                card["result_message"] = "当前工作区已不允许该操作。"
                ledger["revision"] += 1
                self._write_unlocked(ledger)
                _fail("app_action_expired")
            if card["action"] == "save_current" and workspace["has_path"] is not True:
                card["status"] = "expired"
                card["settled_at"] = self._clock()
                card["result_message"] = "当前文档已没有可覆盖的保存路径。"
                ledger["revision"] += 1
                self._write_unlocked(ledger)
                _fail("app_action_expired")
            card["status"] = "presented"
            card["presented_at"] = self._clock()
            ui_receipt = self._ui_receipt_factory()
            if (
                not isinstance(ui_receipt, str)
                or _UI_RECEIPT.fullmatch(ui_receipt) is None
            ):
                _fail("app_action_ui_receipt_generation_failed")
            card["ui_receipt"] = ui_receipt
            card["dirty_open_guard_required"] = bool(
                card["action"] == "open_document" and workspace["dirty"]
            )
            ledger["revision"] += 1
            self._write_unlocked(ledger)
            return {
                "status": "presented_for_user",
                "ledger_revision": ledger["revision"],
                "card": _public(card),
                "workspace": copy.deepcopy(workspace),
            }

    def resolve(
        self,
        *,
        action_id: str,
        status: str,
        result_message: str,
        current_binding: Mapping[str, Any],
        agent_turn_active: bool,
        ui_receipt: str,
        dirty_open_guard_passed: bool,
    ) -> dict[str, Any]:
        if self.legacy_recovery and status != "failed":
            _fail("app_action_legacy_read_only")
        if not isinstance(action_id, str) or _CARD_ID.fullmatch(action_id) is None:
            _fail("app_action_id_invalid")
        if status not in {"completed", "cancelled", "failed"}:
            _fail("app_action_terminal_invalid")
        if not isinstance(result_message, str) or len(result_message) > 1000:
            _fail("app_action_result_message_invalid")
        if type(agent_turn_active) is not bool:
            _fail("app_action_turn_state_invalid")
        if not isinstance(ui_receipt, str) or _UI_RECEIPT.fullmatch(ui_receipt) is None:
            _fail("app_action_ui_receipt_invalid")
        if type(dirty_open_guard_passed) is not bool:
            _fail("app_action_dirty_guard_invalid")
        safe_message = redact_visible_text(result_message)
        binding = _binding(current_binding)
        with self._locked():
            ledger = self._read_unlocked()
            card = next(
                (item for item in ledger["cards"] if item["action_id"] == action_id),
                None,
            )
            if card is None:
                _fail("app_action_not_resolvable")
            if card["status"] in {"completed", "cancelled", "failed"}:
                if (
                    card["status"] != status
                    or card["result_message"] != safe_message
                    or card["ui_receipt"] != ui_receipt
                ):
                    _fail("app_action_settlement_conflict")
                return {
                    "status": "already_resolved",
                    "ledger_revision": ledger["revision"],
                    "card": _public(card),
                }
            if card["status"] != "presented":
                _fail("app_action_not_resolvable")
            if agent_turn_active:
                _fail("app_action_turn_active")
            if card["ui_receipt"] != ui_receipt:
                _fail("app_action_ui_receipt_mismatch")
            if (
                status == "completed"
                and card["dirty_open_guard_required"] is True
                and not dirty_open_guard_passed
            ):
                _fail("app_action_dirty_guard_required")
            # A successful open intentionally replaces the document and thus
            # its binding before the UI callback arrives.  Presentation has
            # already proven the immutable pre-operation binding; every other
            # outcome must still match it exactly.
            intentional_open_transition = (
                card["action"] == "open_document" and status == "completed"
            )
            if not action_binding_matches(card, binding) and not intentional_open_transition:
                card["status"] = "expired"
                card["settled_at"] = self._clock()
                card["result_message"] = "操作卡绑定已经变化。"
                ledger["revision"] += 1
                self._write_unlocked(ledger)
                _fail("app_action_expired")
            card["status"] = status
            card["settled_at"] = self._clock()
            card["result_message"] = safe_message
            ledger["revision"] += 1
            self._write_unlocked(ledger)
            return {
                "status": "resolved",
                "ledger_revision": ledger["revision"],
                "card": _public(card),
            }


__all__ = [
    "ACCEPTED_CANDIDATE_ACTIONS",
    "ACTION_TYPES",
    "CONTRACT",
    "FILE_ACTIONS",
    "NativeAppActionError",
    "NativeAppActionLedger",
    "normalize_action_binding",
    "normalize_action_workspace",
    "sanitize_suggested_basename",
]
