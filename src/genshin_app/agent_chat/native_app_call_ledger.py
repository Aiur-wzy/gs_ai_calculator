"""Durable exactly-once ledger for all V2 App-owned tool calls."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_db.locking import file_lock

from .bridge_protocol import JSON_SAFE_INTEGER_MAX
from .sessions import safe_path, write_owned

CONTRACT = "genshin-native-app-call-ledger-v1"
CHECKPOINT_CONTRACT = "genshin-native-app-call-checkpoint-v1"
LEDGER_FILE = "native-app-call-ledger.json"
LOCK_FILE = ".native-app-call-ledger.lock"
MAX_LEDGER_BYTES = 512 * 1024 * 1024
MAX_RESULT_BYTES = 4 * 1024 * 1024
MAX_ENTRIES = 262_144
# Protocol safety capacity.  The lower, user-adjustable product budget is
# frozen by native_turn_budget and enforced before a physical tool dispatch.
MAX_CALLS_PER_TURN = 16_384

INFLIGHT = "inflight"
SETTLED_SUCCESS = "settled_success"
SETTLED_FAILURE = "settled_failure"
UNKNOWN = "unknown_after_interruption"
TERMINAL = frozenset({SETTLED_SUCCESS, SETTLED_FAILURE, UNKNOWN})
RECOVERY_APPLIED = "applied"
RECOVERY_NOT_APPLIED = "not_applied"
RECOVERY_CONFLICT = "conflict"

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_CHECKPOINT_REF = re.compile(r"appcalls\.v1\.(0|[1-9][0-9]*)\.([0-9a-f]{64})\Z")
_IDENTITY_KEYS = frozenset({
    "conversation_id", "turn_id", "step_id", "epoch_id", "document_id",
    "call_id", "tool_name", "raw_arguments_hash", "runtime_generation",
    "candidate_revision", "stage_revision", "database_fingerprint",
    "tool_manifest_hash", "catalog_hash", "writer_fence",
})
_ENTRY_KEYS = frozenset({
    "identity", "effect", "state", "started_at", "settled_at",
    "before_state_hash", "after_state_hash", "result_json", "result_hash",
    "terminal_reason", "stage_transition",
})
_ROOT_KEYS = frozenset({"contract", "conversation_id", "revision", "entries"})


def _fail(message: str, reason: str = "native_app_call_ledger_invalid", details=None) -> None:
    raise PlanError(message, reason, details)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _identity(value: object, name: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        _fail(f"{name}不符合原生调用身份语法")
    lowered = value.lower()
    if lowered.startswith(("sk-", "bearer:", "authorization:", "api_key:", "secret:")):
        _fail(f"{name}不能承载凭据")
    return value


def _hash(value: object, name: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        _fail(f"{name}必须是小写SHA-256")
    return value


def _revision(value: object, name: str) -> int:
    if type(value) is not int or not 0 <= value <= JSON_SAFE_INTEGER_MAX:
        _fail(f"{name}必须是非负安全整数")
    return value


def _strict_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _portable(value: object, *, depth: int = 0) -> None:
    if depth > 64:
        _fail("工具结果JSON嵌套超过上限")
    if value is None or isinstance(value, (str, bool)):
        return
    if type(value) is int:
        if abs(value) > JSON_SAFE_INTEGER_MAX:
            _fail("工具结果整数超过JavaScript安全范围")
        return
    if isinstance(value, float):
        if not math.isfinite(value) or value == 0.0 and math.copysign(1.0, value) < 0:
            _fail("工具结果包含非安全数值")
        return
    if isinstance(value, list):
        for item in value:
            _portable(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("工具结果字段名必须是字符串")
            _portable(item, depth=depth + 1)
        return
    _fail("工具结果不是可移植JSON")


def _result(value: Mapping[str, Any]) -> tuple[str, str]:
    if not isinstance(value, Mapping):
        _fail("工具结果必须是JSON对象")
    detached = copy.deepcopy(dict(value))
    _portable(detached)
    try:
        encoded = json.dumps(
            detached,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError, OverflowError) as error:
        raise PlanError("工具结果不是规范JSON", "native_app_call_ledger_invalid") from error
    if not encoded or len(encoded) > MAX_RESULT_BYTES:
        _fail("工具结果超过保存上限", "native_app_tool_result_limit")
    return encoded.decode("utf-8"), hashlib.sha256(encoded).hexdigest()


def _stage_transition(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    keys = {
        "contract", "kind", "from_stage", "to_stage",
        "pre_stage_revision", "post_stage_revision",
        "pre_tool_manifest_hash", "post_tool_manifest_hash",
    }
    if not isinstance(value, Mapping) or set(value) != keys:
        _fail("阶段切换证明字段不合法")
    row = copy.deepcopy(dict(value))
    if row["contract"] != "genshin-native-stage-transition-v1":
        _fail("阶段切换证明合同不合法")
    if row["kind"] not in {
        "explicit_forward", "explicit_backward", "automatic_invalidation",
        "artifact_focus", "artifact_preparation", "artifact_representation",
    }:
        _fail("阶段切换证明类型不合法")
    _identity(row["from_stage"], "from_stage")
    _identity(row["to_stage"], "to_stage")
    _revision(row["pre_stage_revision"], "pre_stage_revision")
    _revision(row["post_stage_revision"], "post_stage_revision")
    _hash(row["pre_tool_manifest_hash"], "pre_tool_manifest_hash")
    _hash(row["post_tool_manifest_hash"], "post_tool_manifest_hash")
    return row


def make_app_call_identity(
    *,
    conversation_id: str,
    turn_id: str,
    step_id: str,
    epoch_id: str,
    document_id: str,
    call_id: str,
    tool_name: str,
    raw_arguments_hash: str,
    runtime_generation: str,
    candidate_revision: int,
    stage_revision: int,
    database_fingerprint: str,
    tool_manifest_hash: str,
    catalog_hash: str,
    writer_fence: str,
) -> dict[str, Any]:
    return _validate_identity(locals())


def _validate_identity(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _IDENTITY_KEYS:
        _fail("原生App调用身份字段不合法")
    row = copy.deepcopy(dict(value))
    for name in (
        "conversation_id", "turn_id", "step_id", "epoch_id", "document_id",
        "call_id", "tool_name", "runtime_generation", "writer_fence",
    ):
        _identity(row.get(name), name)
    for name in (
        "raw_arguments_hash", "database_fingerprint", "tool_manifest_hash",
        "catalog_hash",
    ):
        _hash(row.get(name), name)
    _revision(row.get("candidate_revision"), "candidate_revision")
    _revision(row.get("stage_revision"), "stage_revision")
    return row


def _empty(conversation_id: str) -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "conversation_id": conversation_id,
        "revision": 0,
        "entries": {},
    }


def _validate_entry(call_id: str, value: object, conversation_id: str) -> dict[str, Any]:
    _identity(call_id, "call_id")
    if not isinstance(value, Mapping) or set(value) != _ENTRY_KEYS:
        _fail("原生App调用记录字段不合法")
    row = copy.deepcopy(dict(value))
    identity = _validate_identity(row.get("identity"))
    if identity["call_id"] != call_id or identity["conversation_id"] != conversation_id:
        _fail("原生App调用记录索引不一致")
    effect = _identity(row.get("effect"), "effect")
    row["stage_transition"] = _stage_transition(row.get("stage_transition"))
    transition = row["stage_transition"]
    if transition is not None:
        artifact = transition["kind"] in {"artifact_focus", "artifact_preparation", "artifact_representation"}
        increment = 0 if transition["kind"] == "artifact_focus" else 1
        if (
            transition["pre_stage_revision"] != identity["stage_revision"]
            or transition["post_stage_revision"]
            != identity["stage_revision"] + increment
            or transition["pre_tool_manifest_hash"]
            != identity["tool_manifest_hash"]
            or (not artifact and transition["from_stage"] == transition["to_stage"])
        ):
            _fail("阶段切换证明与调用pre身份不一致")
        if artifact:
            representation = transition["kind"] == "artifact_representation"
            if representation and (effect != "candidate" or identity["tool_name"] != "gs_patch_whiteboard"
                    or transition["post_tool_manifest_hash"] == transition["pre_tool_manifest_hash"]):
                _fail("工件输入表示回执与执行权限不一致")
            if not representation and effect != "stage_transition":
                _fail("工件输入表示回执与执行权限不一致")
            if (transition["from_stage"] != "artifact"
                    or transition["to_stage"] != "artifact"
                    or transition["kind"] == "artifact_focus" and
                        transition["post_tool_manifest_hash"] != transition["pre_tool_manifest_hash"]):
                _fail("工件工作重点或准备回执与执行权限不一致")
        elif effect == "stage_transition":
            if transition["kind"] not in {
                "explicit_forward", "explicit_backward",
            }:
                _fail("显式阶段工具的切换证明类型不合法")
        elif effect in {"candidate", "candidate_and_derived"}:
            if transition["kind"] != "automatic_invalidation":
                _fail("候选修改的阶段切换必须是自动失效回退")
        else:
            _fail("当前工具效果不能携带阶段切换证明")
    state = row.get("state")
    if state not in {INFLIGHT, *TERMINAL}:
        _fail("原生App调用状态不合法")
    if not isinstance(row.get("started_at"), str) or not row["started_at"]:
        _fail("原生App调用开始时间不合法")
    _hash(row.get("before_state_hash"), "before_state_hash")
    if state == INFLIGHT:
        if any(row.get(name) is not None for name in (
            "settled_at", "after_state_hash", "result_json", "result_hash",
            "terminal_reason",
                "stage_transition",
        )):
            _fail("在途原生App调用包含终态字段")
        return row
    if not isinstance(row.get("settled_at"), str) or not row["settled_at"]:
        _fail("原生App调用结算时间不合法")
    _hash(row.get("after_state_hash"), "after_state_hash")
    if state == UNKNOWN:
        if row.get("result_json") is not None or row.get("result_hash") is not None:
            _fail("未知结果不能保存工具载荷")
        _identity(row.get("terminal_reason"), "terminal_reason")
        return row
    if state == SETTLED_SUCCESS and effect == "stage_transition" and transition is None:
        _fail("成功阶段工具缺少阶段切换证明")
    if row.get("terminal_reason") is not None:
        _identity(row["terminal_reason"], "terminal_reason")
    raw = row.get("result_json")
    if not isinstance(raw, str):
        _fail("已结算调用缺少结果")
    try:
        decoded = json.loads(raw, object_pairs_hook=_strict_object)
    except (ValueError, RecursionError):
        _fail("已结算调用结果损坏", "agent_checkpoint_invalid")
    encoded, digest = _result(decoded)
    if encoded != raw or digest != row.get("result_hash"):
        _fail("已结算调用结果哈希不匹配", "agent_checkpoint_invalid")
    return row


def _validate_ledger(value: object, conversation_id: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _ROOT_KEYS:
        _fail("原生App调用账字段不合法")
    row = copy.deepcopy(dict(value))
    if row.get("contract") != CONTRACT or row.get("conversation_id") != conversation_id:
        _fail("原生App调用账身份不匹配")
    _revision(row.get("revision"), "revision")
    entries = row.get("entries")
    if not isinstance(entries, dict) or len(entries) > MAX_ENTRIES:
        _fail("原生App调用账条目不合法")
    inflight = 0
    for call_id, entry in entries.items():
        checked = _validate_entry(call_id, entry, conversation_id)
        inflight += checked["state"] == INFLIGHT
    if inflight > 1:
        _fail("同一对话存在多个在途App调用")
    if len(json.dumps(row, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_LEDGER_BYTES:
        _fail("原生App调用账超过保存上限", "agent_size_limit")
    return row


class NativeAppCallLedger:
    """Atomic call admission, exact replay and crash recovery."""

    def __init__(
        self,
        root: str | Path,
        *,
        conversation_id: str,
        clock: Callable[[], str] = _now,
    ) -> None:
        self.root = Path(root).absolute()
        self.conversation_id = _identity(conversation_id, "conversation_id")
        self.path = safe_path(self.root, LEDGER_FILE)
        self.lock_path = safe_path(self.root, LOCK_FILE)
        self._clock = clock

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty(self.conversation_id)
        if self.path.is_symlink() or self.path.stat().st_size > MAX_LEDGER_BYTES:
            _fail("原生App调用账不可读取", "agent_storage")
        try:
            value = json.loads(
                self.path.read_text(encoding="utf-8"),
                object_pairs_hook=_strict_object,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
        except (OSError, UnicodeError, ValueError, RecursionError):
            _fail("原生App调用账不是严格JSON", "agent_checkpoint_invalid")
        return _validate_ledger(value, self.conversation_id)

    def _write_unlocked(self, value: Mapping[str, Any]) -> None:
        checked = _validate_ledger(value, self.conversation_id)
        self.root.mkdir(parents=True, exist_ok=True)
        write_owned(self.root, LEDGER_FILE, checked, MAX_LEDGER_BYTES)

    def read(self) -> dict[str, Any]:
        with file_lock(self.lock_path):
            return copy.deepcopy(self._read_unlocked())

    @staticmethod
    def _checkpoint_projection(ledger: Mapping[str, Any]) -> dict[str, Any]:
        checked = _validate_ledger(ledger, ledger.get("conversation_id"))
        digest = fingerprint(checked)
        revision = checked["revision"]
        return {
            "contract": CHECKPOINT_CONTRACT,
            "conversation_id": checked["conversation_id"],
            "ledger_revision": revision,
            "ledger_hash": digest,
            "session_ref": f"appcalls.v1.{revision}.{digest}",
        }

    def checkpoint_projection(self) -> dict[str, Any]:
        """Return the path-free integrity anchor saved by the main session."""

        with file_lock(self.lock_path):
            return self._checkpoint_projection(self._read_unlocked())

    def verify_checkpoint_projection(self, value: Mapping[str, Any]) -> None:
        """Reject a stale, tampered, cross-conversation or missing ledger."""

        if not isinstance(value, Mapping) or set(value) != {
            "contract", "conversation_id", "ledger_revision", "ledger_hash",
            "session_ref",
        }:
            _fail("原生App调用账检查点字段不合法", "agent_checkpoint_invalid")
        supplied = copy.deepcopy(dict(value))
        if supplied.get("contract") != CHECKPOINT_CONTRACT:
            _fail("原生App调用账检查点合同不匹配", "agent_checkpoint_invalid")
        _identity(supplied.get("conversation_id"), "conversation_id")
        _revision(supplied.get("ledger_revision"), "ledger_revision")
        _hash(supplied.get("ledger_hash"), "ledger_hash")
        reference = supplied.get("session_ref")
        matched = _CHECKPOINT_REF.fullmatch(reference) if isinstance(reference, str) else None
        if matched is None:
            _fail("原生App调用账session_ref不合法", "agent_checkpoint_invalid")
        if (
            int(matched.group(1)) != supplied["ledger_revision"]
            or matched.group(2) != supplied["ledger_hash"]
        ):
            _fail("原生App调用账session_ref与投影不一致", "agent_checkpoint_invalid")
        with file_lock(self.lock_path):
            current = self._checkpoint_projection(self._read_unlocked())
        if supplied != current:
            _fail("原生App调用账检查点与持久账不匹配", "stale_context")

    def verify_checkpoint_ref(self, reference: str) -> None:
        """Verify the compact identity-safe reference used by session refs."""

        if not isinstance(reference, str):
            _fail("原生App调用账引用不合法", "agent_checkpoint_invalid")
        matched = _CHECKPOINT_REF.fullmatch(reference)
        if matched is None:
            _fail("原生App调用账引用不合法", "agent_checkpoint_invalid")
        projection = self.checkpoint_projection()
        if projection["session_ref"] != reference:
            _fail("原生App调用账引用与持久账不匹配", "stale_context")

    def lookup(self, *, identity: Mapping[str, Any]) -> dict[str, Any] | None:
        """Return an exact prior call before consulting the now-current snapshot.

        This is the lost-ACK path for writes: the current candidate/stage may
        already have advanced, so requiring today's state vector first would
        incorrectly reject an exact replay as stale.
        """

        checked_identity = _validate_identity(identity)
        with file_lock(self.lock_path):
            ledger = self._read_unlocked()
            existing = ledger["entries"].get(checked_identity["call_id"])
            if existing is None:
                return None
            if existing["identity"] != checked_identity:
                _fail("相同call_id绑定了不同调用", "call_id_conflict")
            response = {
                "disposition": "inflight" if existing["state"] == INFLIGHT else "replay",
                "entry": copy.deepcopy(existing),
            }
            if existing["result_json"] is not None:
                response["result"] = json.loads(existing["result_json"])
            return response

    def begin(
        self,
        *,
        identity: Mapping[str, Any],
        effect: str,
        before_state_hash: str,
    ) -> dict[str, Any]:
        checked_identity = _validate_identity(identity)
        checked_effect = _identity(effect, "effect")
        before = _hash(before_state_hash, "before_state_hash")
        with file_lock(self.lock_path):
            ledger = self._read_unlocked()
            if checked_identity["conversation_id"] != self.conversation_id:
                _fail("调用不属于当前对话", "stale_context")
            call_id = checked_identity["call_id"]
            existing = ledger["entries"].get(call_id)
            if existing is not None:
                if (
                    existing["identity"] != checked_identity
                    or existing["effect"] != checked_effect
                    or existing["before_state_hash"] != before
                ):
                    _fail("相同call_id绑定了不同调用", "call_id_conflict")
                response = {
                    "disposition": "inflight" if existing["state"] == INFLIGHT else "replay",
                    "entry": copy.deepcopy(existing),
                }
                if existing["result_json"] is not None:
                    response["result"] = json.loads(existing["result_json"])
                return response
            turn_entries = [
                row for row in ledger["entries"].values()
                if row["identity"]["turn_id"] == checked_identity["turn_id"]
            ]
            if len(turn_entries) >= MAX_CALLS_PER_TURN:
                _fail(
                    f"当前用户轮App工具调用已达传输安全容量{MAX_CALLS_PER_TURN}次",
                    "native_app_call_budget_exhausted",
                )
            if any(row["state"] == INFLIGHT for row in ledger["entries"].values()):
                _fail("同一时刻只允许一个App工具调用", "native_app_call_concurrency")
            if len(ledger["entries"]) >= MAX_ENTRIES:
                _fail("原生App调用账已满，请新建对话", "agent_size_limit")
            entry = {
                "identity": checked_identity,
                "effect": checked_effect,
                "state": INFLIGHT,
                "started_at": self._clock(),
                "settled_at": None,
                "before_state_hash": before,
                "after_state_hash": None,
                "result_json": None,
                "result_hash": None,
                "terminal_reason": None,
                "stage_transition": None,
            }
            ledger["entries"][call_id] = entry
            ledger["revision"] += 1
            self._write_unlocked(ledger)
            return {"disposition": "execute", "entry": copy.deepcopy(entry)}

    def settle(
        self,
        *,
        identity: Mapping[str, Any],
        effect: str,
        state: str,
        after_state_hash: str,
        result: Mapping[str, Any] | None = None,
        terminal_reason: str | None = None,
        stage_transition: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        checked_identity = _validate_identity(identity)
        checked_effect = _identity(effect, "effect")
        after = _hash(after_state_hash, "after_state_hash")
        if state not in TERMINAL:
            _fail("原生App调用终态不合法")
        result_json = result_hash = None
        if state == UNKNOWN:
            if result is not None or terminal_reason is None:
                _fail("未知终态必须只有机器原因")
        else:
            if result is None:
                _fail("成功或失败终态必须保存工具结果")
            result_json, result_hash = _result(result)
        reason = _identity(terminal_reason, "terminal_reason") if terminal_reason else None
        transition = _stage_transition(stage_transition)
        with file_lock(self.lock_path):
            ledger = self._read_unlocked()
            call_id = checked_identity["call_id"]
            existing = ledger["entries"].get(call_id)
            if existing is None:
                _fail("结算没有对应在途调用", "unknown_settlement")
            if existing["identity"] != checked_identity or existing["effect"] != checked_effect:
                _fail("相同call_id绑定了不同调用", "call_id_conflict")
            proposed = copy.deepcopy(existing)
            proposed.update(
                state=state,
                settled_at=existing["settled_at"] or self._clock(),
                after_state_hash=after,
                result_json=result_json,
                result_hash=result_hash,
                terminal_reason=reason,
                stage_transition=transition,
            )
            proposed = _validate_entry(call_id, proposed, self.conversation_id)
            if existing["state"] != INFLIGHT:
                if proposed != existing:
                    _fail("原生App调用终态冲突", "settlement_conflict")
                response = {"status": "replay", "entry": copy.deepcopy(existing)}
                if existing["result_json"] is not None:
                    response["result"] = json.loads(existing["result_json"])
                return response
            ledger["entries"][call_id] = proposed
            ledger["revision"] += 1
            self._write_unlocked(ledger)
            response = {"status": "settled", "entry": copy.deepcopy(proposed)}
            if result_json is not None:
                response["result"] = json.loads(result_json)
            return response

    def recover_inflight(
        self,
        *,
        after_state_hash: str,
        terminal_reason: str = "process_interrupted",
    ) -> list[str]:
        """Close persisted in-flight calls as unknown without re-executing."""

        reason = _identity(terminal_reason, "terminal_reason")
        after = _hash(after_state_hash, "after_state_hash")
        recovered: list[str] = []
        with file_lock(self.lock_path):
            ledger = self._read_unlocked()
            for call_id, entry in ledger["entries"].items():
                if entry["state"] != INFLIGHT:
                    continue
                entry.update(
                    state=UNKNOWN,
                    settled_at=self._clock(),
                    after_state_hash=after,
                    terminal_reason=reason,
                )
                _validate_entry(call_id, entry, self.conversation_id)
                recovered.append(call_id)
            if recovered:
                ledger["revision"] += 1
                self._write_unlocked(ledger)
        return recovered

    def classify_recovery(
        self,
        *,
        call_id: str,
        observed_state_hash: str,
    ) -> dict[str, Any]:
        """Classify a terminal against an authoritative live state hash.

        This method never replays a call and never edits the ledger.  In
        particular, an entry closed by ``recover_inflight`` remains a conflict:
        that method samples state only *after* the interruption and therefore
        cannot prove which side of the App mutation boundary was durable.
        """

        call_id = _identity(call_id, "call_id")
        observed = _hash(observed_state_hash, "observed_state_hash")
        with file_lock(self.lock_path):
            ledger = self._read_unlocked()
            entry = ledger["entries"].get(call_id)
            if entry is None:
                _fail("恢复目标没有对应App调用", "unknown_settlement")
            row = copy.deepcopy(entry)
        if row["state"] == INFLIGHT:
            classification = RECOVERY_CONFLICT
            reason = "call_still_inflight"
        elif row["state"] == UNKNOWN:
            # These reasons are written synchronously after the call and the
            # after-state observation. process_interrupted is not such proof.
            if (
                row["terminal_reason"] in {
                    "effect_mask_violation", "failed_tool_changed_state",
                }
                and observed == row["after_state_hash"]
            ):
                classification = (
                    RECOVERY_NOT_APPLIED
                    if row["before_state_hash"] == row["after_state_hash"]
                    else RECOVERY_APPLIED
                )
                reason = "synchronous_post_state_evidence"
            else:
                classification = RECOVERY_CONFLICT
                reason = "interruption_has_no_commit_proof"
        elif row["state"] == SETTLED_FAILURE:
            if (
                row["before_state_hash"] == row["after_state_hash"]
                and observed == row["before_state_hash"]
            ):
                classification = RECOVERY_NOT_APPLIED
                reason = "settled_failure_unchanged"
            else:
                classification = RECOVERY_CONFLICT
                reason = "failure_state_mismatch"
        elif observed == row["after_state_hash"]:
            classification = RECOVERY_APPLIED
            reason = "settled_success_post_state"
        else:
            classification = RECOVERY_CONFLICT
            reason = "live_state_does_not_match_terminal"
        return {
            "classification": classification,
            "reason": reason,
            "entry": row,
        }


__all__ = [
    "CHECKPOINT_CONTRACT",
    "INFLIGHT",
    "MAX_CALLS_PER_TURN",
    "RECOVERY_APPLIED",
    "RECOVERY_CONFLICT",
    "RECOVERY_NOT_APPLIED",
    "SETTLED_FAILURE",
    "SETTLED_SUCCESS",
    "TERMINAL",
    "UNKNOWN",
    "NativeAppCallLedger",
    "make_app_call_identity",
]
