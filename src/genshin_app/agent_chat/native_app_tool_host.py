"""Unified, sequential Python host for DSH V2 App-owned tools."""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from typing import Any

from genshin_app.app_fault_store import fallback_fault_scope
from genshin_app.domain_errors import classify_domain_failure, tool_failure_guidance
from genshin_app.equipment.files import fingerprint, json_bytes
from genshin_app.equipment.schema import PlanError

from . import tools
from .bridge_protocol import BridgeProtocolError, decode_json_object, raw_json_hash
from .native_app_call_ledger import (
    INFLIGHT,
    SETTLED_FAILURE,
    SETTLED_SUCCESS,
    UNKNOWN,
    NativeAppCallLedger,
    make_app_call_identity,
)
from .native_app_tool_catalog import (
    build_native_app_tool_snapshot,
    validate_native_app_tool_snapshot,
)
from .native_app_tool_effects import (
    DOMAIN_EFFECTS,
    REQUIRES_CHANGE,
    WRITE_EFFECTS,
    allowed_changes_for_transition,
    allows_settled_failure_commit,
    artifact_representation_state_matches,
    stage_transition_proof,
)
from .native_research_ledger import NativeResearchLedger
from .native_research_tools import call as call_research_tool
from .native_tool_receipt import sanitize_native_response


def _fail(message: str, reason: str = "native_app_tool_host_invalid", details=None) -> None:
    raise PlanError(message, reason, details)


@dataclass(frozen=True)
class NativeAppStateVector:
    workflow: str
    workflow_stage: str
    document_id: str
    candidate_revision: int
    stage_revision: int
    database_fingerprint: str
    tool_manifest_hash: str
    runtime_generation: str
    writer_fence: str
    candidate_hash: str
    resources_hash: str
    findings_hash: str
    derived_hash: str
    stage_state_hash: str
    durable_checkpoint_hash: str

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.__dict__)

    @property
    def state_hash(self) -> str:
        return fingerprint(self.as_dict())


def native_app_state_vector(
    session: object,
    snapshot: Mapping[str, Any],
) -> NativeAppStateVector:
    """Project the complete App-owned state used by the generic call ledger.

    This is deliberately a pure projection.  The caller owns the candidate
    session lock and supplies the exact live binding (including writer fence),
    so cold recovery can compare the same state vector that the tool Host
    recorded before and after execution without creating a second Host.
    """

    stage_state = getattr(session, "stage_state", {})
    return NativeAppStateVector(
        workflow=snapshot["workflow"],
        workflow_stage=snapshot["workflow_stage"],
        document_id=snapshot["document_id"],
        candidate_revision=snapshot["candidate_revision"],
        stage_revision=snapshot["stage_revision"],
        database_fingerprint=snapshot["database_fingerprint"],
        tool_manifest_hash=snapshot["tool_manifest_hash"],
        runtime_generation=snapshot["runtime_generation"],
        writer_fence=snapshot["writer_fence"],
        candidate_hash=fingerprint(getattr(session, "candidate", None)),
        resources_hash=fingerprint(getattr(session, "resources", {})),
        findings_hash=fingerprint(getattr(session, "findings", [])),
        derived_hash=fingerprint(_derived_view(session)),
        stage_state_hash=fingerprint(stage_state),
        durable_checkpoint_hash=fingerprint(_durable_candidate_view(session)),
    )


def native_app_state_hash(session: object, snapshot: Mapping[str, Any]) -> str:
    """Return the ledger-compatible hash for one locked live App snapshot."""

    return native_app_state_vector(session, snapshot).state_hash


def _derived_view(session: object) -> dict[str, Any]:
    return {
        "report": getattr(session, "report", None),
        "calculated": getattr(session, "calculated", None),
        "diagnostic": getattr(session, "_diagnostic_cache", None),
        "review_projection_records": getattr(session, "_review_projection_records", None),
        "review_projection_orphans": getattr(session, "_review_projection_orphans", None),
    }


def _durable_candidate_view(session: object) -> dict[str, Any]:
    """Return a canonical in-memory mirror of CandidateSession.save().

    The generic cold-recovery hash must cover more than the fields that are
    currently interesting to the effect mask.  In particular, stage and D2
    operations can change the baseline, provisional authority and audit
    journal without changing the candidate JSON itself.  Keeping the
    fine-grained hashes alongside this aggregate preserves precise effect
    checks while making an omitted durable field fail closed during recovery.
    """

    return {
        "session_id": getattr(session, "session_id", None),
        "kind": getattr(session, "kind", None),
        "workflow": getattr(session, "workflow", None),
        "document": getattr(session, "document", None),
        "document_fingerprint": getattr(session, "document_fingerprint", None),
        "candidate": getattr(session, "candidate", None),
        "baseline": getattr(session, "baseline", None),
        "database_snapshot": getattr(session, "database_snapshot", None),
        "revision": getattr(session, "revision", None),
        "changes": getattr(session, "changes", None),
        "provisional_authority": getattr(session, "provisional_authority", None),
        "findings": getattr(session, "findings", None),
        "resources": getattr(session, "resources", None),
        "stage_state": getattr(session, "stage_state", None),
        "calculated": getattr(session, "calculated", None),
        "report": getattr(session, "report", None),
        "review_projection_records": getattr(
            session, "_review_projection_records", None,
        ),
        "review_projection_orphans": getattr(
            session, "_review_projection_orphans", None,
        ),
    }


def _changes(before: NativeAppStateVector, after: NativeAppStateVector) -> list[str]:
    left, right = before.as_dict(), after.as_dict()
    return sorted(name for name in left if left[name] != right[name])


def _failure(error: BaseException, *, operation: str | None = None) -> dict[str, Any]:
    from genshin_app.app_system_fault import is_domain_exception, system_failure
    if isinstance(error, Exception) and not is_domain_exception(error):
        return sanitize_native_response(system_failure(error,
            component="python_engine_runtime" if isinstance(error, ImportError) else "app_domain_tools",
            operation=operation or "native_app_tool"))
    reason = getattr(error, "reason", None) or getattr(error, "code", None)
    if not isinstance(reason, str) or not reason or len(reason) > 128:
        reason = "native_app_tool_error"
    message = str(error)
    if not message or len(message) > 4096:
        message = reason
    result: dict[str, Any] = {
        "code": 1,
        "reason": reason,
        "message": message[:4096],
    }
    raw_details = getattr(error, "details", None)
    if raw_details not in (None, [], {}):
        try:
            details = sanitize_native_response(raw_details)
            if len(json_bytes(details)) <= 16 * 1024:
                result["details"] = details
            else:
                details = None
        except Exception:  # noqa: BLE001 - failure projection must stay safe
            details = None

        first = details if isinstance(details, Mapping) else (
            details[0] if isinstance(details, list) and details
            and isinstance(details[0], Mapping) else None
        )
        if first is not None:
            field = first.get("field") or first.get("path")
            path = first.get("path") or first.get("field")
            repair_hint = first.get("repair_hint") or first.get("message")
            if isinstance(field, str) and field:
                result["field"] = field[:512]
            if isinstance(path, str) and path:
                result["path"] = path[:512]
            if isinstance(repair_hint, str) and repair_hint:
                result["repair_hint"] = repair_hint[:2000]
            retryable = first.get("retryable")
            result["retryable"] = retryable if type(retryable) is bool else True
    classification = classify_domain_failure(reason, operation=operation, details=raw_details)
    for key, value in classification.items():
        result.setdefault(key, value)
    result.update(tool_failure_guidance(reason, operation=operation, details=raw_details))
    return sanitize_native_response(result)


class NativeAppToolHost:
    """Sequential App-domain execution boundary shared across DSH Turns.

    Candidate/session mutations are never implemented here.  Domain writes go
    through the injected executor, normally the existing App session's
    authoritative ``call_tool`` path.  The host only admits, serializes,
    records and verifies their declared effects.  The ledger owns only a
    protocol safety capacity; the frozen per-turn policy owns consumption.
    """

    def __init__(
        self,
        session: object,
        *,
        conversation_id: str,
        document_id: str,
        database_fingerprint: str,
        tool_manifest_hash: str,
        runtime_generation: str,
        writer_fence: str,
        ledger: NativeAppCallLedger,
        domain_write_executor: Callable[[object, str, Mapping[str, Any]], Mapping[str, Any]],
        domain_read_executor: Callable[[object, str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
        domain_write_boundary: Callable[[], AbstractContextManager[None]] | None = None,
        live_binding_reader: Callable[[], Mapping[str, Any]] | None = None,
        development_allow_static_binding: bool = False,
        research_ledger: NativeResearchLedger | None = None,
        app_action_host: object | None = None,
        resource_admission: Callable[[Mapping[str, Any], str], Any] | None = None,
        diagnostic_data_dir=None,
    ) -> None:
        if not isinstance(ledger, NativeAppCallLedger):
            raise TypeError("ledger must be NativeAppCallLedger")
        if not callable(domain_write_executor):
            raise TypeError("domain_write_executor must be callable")
        if domain_read_executor is not None and not callable(domain_read_executor):
            raise TypeError("domain_read_executor must be callable")
        if domain_write_boundary is not None and not callable(domain_write_boundary):
            raise TypeError("domain_write_boundary must be callable")
        if live_binding_reader is not None and not callable(live_binding_reader):
            raise TypeError("live_binding_reader must be callable")
        if live_binding_reader is None and not development_allow_static_binding:
            _fail("生产Host必须提供实时工具绑定读取器", "native_app_live_binding_required")
        if domain_write_boundary is None and not development_allow_static_binding:
            _fail("生产Host必须提供领域写原子边界", "native_app_write_boundary_required")
        self.session = session
        self.conversation_id = conversation_id
        self.ledger = ledger
        self._domain_write_executor = domain_write_executor
        self._domain_write_boundary = domain_write_boundary
        self._domain_read_executor = domain_read_executor or (
            lambda current, name, arguments: tools.call_tool(current, name, arguments)
        )
        self._live_binding_reader = live_binding_reader
        self._research_ledger = research_ledger
        self._app_action_executor = self._action_executor(app_action_host)
        self._resource_admission = resource_admission
        self.diagnostic_data_dir = diagnostic_data_dir
        self._execution_lock = threading.Lock()
        self._fixed_binding = {
            "document_id": document_id,
            "database_fingerprint": database_fingerprint,
            "tool_manifest_hash": tool_manifest_hash,
            "runtime_generation": runtime_generation,
            "writer_fence": writer_fence,
        }
        self.snapshot = build_native_app_tool_snapshot(
            session,
            **self._fixed_binding,
        )
        if ledger.conversation_id != conversation_id:
            _fail("工具账与Host对话身份不一致", "stale_context")

    def _execution_boundary(self, effect: str) -> AbstractContextManager[None]:
        if effect not in WRITE_EFFECTS or self._domain_write_boundary is None:
            return nullcontext()
        boundary = self._domain_write_boundary()
        if not hasattr(boundary, "__enter__") or not hasattr(boundary, "__exit__"):
            _fail("领域写边界不是上下文管理器", "native_app_write_boundary_required")
        return boundary

    @staticmethod
    def _action_executor(
        value: object | None,
    ) -> Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None:
        """Adapt either the production controller or the legacy raw host.

        ``NativeAppActionController.call_model_tool`` is deliberately preferred
        over a generic ``call`` method.  The controller enforces the complete
        Agent-turn/file-UI gate and maintains its companion checkpoint/events;
        production composition must never silently bypass that layer.
        """

        if value is None:
            return None
        model_call = getattr(value, "call_model_tool", None)
        if callable(model_call):
            return model_call
        raw_call = getattr(value, "call", None)
        if callable(raw_call):
            return raw_call
        if callable(value):
            return value
        raise TypeError(
            "app_action_host must provide call_model_tool/call or be callable"
        )

    def _binding(self) -> dict[str, Any]:
        current = copy.deepcopy(self._fixed_binding)
        if self._live_binding_reader is not None:
            raw = self._live_binding_reader()
            if not isinstance(raw, Mapping):
                _fail("实时工具绑定读取失败", "stale_context")
            raw = dict(raw)
            if set(raw) != set(current):
                _fail("实时工具绑定字段不合法", "stale_context")
            current = copy.deepcopy(raw)
        return current

    def _current_snapshot(self) -> dict[str, Any]:
        current = build_native_app_tool_snapshot(self.session, **self._binding())
        return validate_native_app_tool_snapshot(current)

    def current_snapshot(self) -> dict[str, Any]:
        """Return the fresh, detached catalog used to start the next turn.

        ``snapshot`` is retained for diagnostics and for the in-flight
        settlement checks, but it may describe the catalogue from the end of
        the previous turn.  The production turn port must bind every new
        provider turn to the live candidate/stage revisions instead of
        reusing that cached value.
        """

        with self._execution_lock:
            current = self._current_snapshot()
            self.snapshot = copy.deepcopy(current)
            return copy.deepcopy(current)

    def _state(self, snapshot: Mapping[str, Any]) -> NativeAppStateVector:
        return native_app_state_vector(self.session, snapshot)

    def _assert_request_binding(self, values: Mapping[str, Any], snapshot: Mapping[str, Any]) -> None:
        expected = {
            "document_id": snapshot["document_id"],
            "candidate_revision": snapshot["candidate_revision"],
            "stage_revision": snapshot["stage_revision"],
            "database_fingerprint": snapshot["database_fingerprint"],
            "tool_manifest_hash": snapshot["tool_manifest_hash"],
            "runtime_generation": snapshot["runtime_generation"],
            "writer_fence": snapshot["writer_fence"],
            "catalog_hash": snapshot["catalog_hash"],
        }
        if any(values.get(name) != value for name, value in expected.items()):
            _fail("工具调用绑定已陈旧", "stale_tool_snapshot")

    def _route(self, name: str, arguments: dict[str, Any], effect: str, turn_id: str) -> dict[str, Any]:
        if effect in DOMAIN_EFFECTS:
            tools.validate_tool_arguments_for_session(self.session, name, arguments)
            executor = (
                self._domain_read_executor if effect == "none" else self._domain_write_executor
            )
            value = executor(self.session, name, copy.deepcopy(arguments))
        elif effect == "external_research_ledger":
            if self._research_ledger is None:
                _fail("研究工具账未连接", "native_app_tool_unavailable")
            value = call_research_tool(
                name,
                arguments,
                ledger=self._research_ledger,
                turn_id=turn_id,
            )
        elif effect == "external_action_ledger":
            if self._app_action_executor is None:
                _fail("App操作卡Host未连接", "native_app_tool_unavailable")
            value = self._app_action_executor(name, arguments)
        else:
            _fail("工具效果分类不受支持", "agent_tool_rejected")
        if not isinstance(value, Mapping):
            _fail("工具执行器没有返回JSON对象", "invalid_tool_result")
        return sanitize_native_response(value, tool=name, arguments=arguments)

    def execute(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        step_id: str,
        epoch_id: str,
        call_id: str,
        tool_name: str,
        arguments_json: str,
        raw_arguments_hash: str,
        document_id: str,
        candidate_revision: int,
        stage_revision: int,
        database_fingerprint: str,
        tool_manifest_hash: str,
        runtime_generation: str,
        writer_fence: str,
        catalog_hash: str,
    ) -> dict[str, Any]:
        if not self._execution_lock.acquire(blocking=False):
            _fail("同一时刻只允许一个App工具调用", "native_app_call_concurrency")
        try:
            if conversation_id != self.conversation_id:
                _fail("工具调用不属于当前对话", "stale_context")
            try:
                arguments = decode_json_object(arguments_json)
                actual_raw_hash = raw_json_hash(arguments_json)
            except BridgeProtocolError as error:
                _fail("工具参数不是严格JSON对象", error.code)  # type: ignore[attr-defined]
            if actual_raw_hash != raw_arguments_hash:
                _fail("工具原始参数哈希不匹配", "canonical_args_hash_mismatch")
            identity = make_app_call_identity(
                conversation_id=self.conversation_id,
                turn_id=turn_id,
                step_id=step_id,
                epoch_id=epoch_id,
                document_id=document_id,
                call_id=call_id,
                tool_name=tool_name,
                raw_arguments_hash=raw_arguments_hash,
                runtime_generation=runtime_generation,
                candidate_revision=candidate_revision,
                stage_revision=stage_revision,
                database_fingerprint=database_fingerprint,
                tool_manifest_hash=tool_manifest_hash,
                catalog_hash=catalog_hash,
                writer_fence=writer_fence,
            )
            request_binding = {
                "document_id": document_id,
                "candidate_revision": candidate_revision,
                "stage_revision": stage_revision,
                "database_fingerprint": database_fingerprint,
                "tool_manifest_hash": tool_manifest_hash,
                "runtime_generation": runtime_generation,
                "writer_fence": writer_fence,
                "catalog_hash": catalog_hash,
            }

            # The first lookup is only a hint needed to select the write
            # boundary for a lost-ACK replay.  A write is looked up again and
            # verified while its writer authority is held.
            prior_hint = self.ledger.lookup(identity=identity)
            if prior_hint is not None:
                effect = prior_hint["entry"]["effect"]
                with self._execution_boundary(effect):
                    prior = self.ledger.lookup(identity=identity)
                    if prior is None:
                        _fail("原生App调用账在回放期间发生变化", "stale_context")
                    return self._project_prior(
                        prior,
                        call_id=call_id,
                        tool_name=tool_name,
                    )

            preview = self._current_snapshot()
            self.snapshot = preview
            self._assert_request_binding(request_binding, preview)
            definitions = {row["name"]: row for row in preview["tools"]}
            if tool_name not in definitions:
                _fail("工具不在当前阶段目录", "agent_tool_forbidden")
            effect = preview["policies"][tool_name]["effect"]

            with self._execution_boundary(effect):
                # A second Host may have admitted this identity while this
                # call waited for writer authority.  Recheck before execution.
                prior = self.ledger.lookup(identity=identity)
                if prior is not None:
                    return self._project_prior(
                        prior,
                        call_id=call_id,
                        tool_name=tool_name,
                    )

                snapshot = self._current_snapshot()
                self.snapshot = snapshot
                self._assert_request_binding(request_binding, snapshot)
                definitions = {row["name"]: row for row in snapshot["tools"]}
                if tool_name not in definitions:
                    _fail("工具不在当前阶段目录", "agent_tool_forbidden")
                current_effect = snapshot["policies"][tool_name]["effect"]
                if current_effect != effect:
                    _fail("工具效果目录在调用期间变化", "stale_tool_snapshot")
                before = self._state(snapshot)
                before_stage_state = copy.deepcopy(getattr(self.session, "stage_state", None))
                before_candidate_kind = getattr(self.session, "candidate", {}).get("kind")
                before_document = fingerprint(getattr(self.session, "document", None))
                before_baseline = fingerprint(getattr(self.session, "baseline", None))
                if self._resource_admission is not None:
                    self._resource_admission(identity, effect)
                admitted = self.ledger.begin(
                    identity=identity,
                    effect=effect,
                    before_state_hash=before.state_hash,
                )
                if admitted["disposition"] != "execute":
                    return self._project_prior(
                        admitted,
                        call_id=call_id,
                        tool_name=tool_name,
                    )

                result: dict[str, Any]
                failed = False
                database = getattr(self.session, "db_path", None)
                from pathlib import Path
                with fallback_fault_scope(self.diagnostic_data_dir if self.diagnostic_data_dir is not None else
                        Path(database).parent if database is not None else self.ledger.root,
                        conversation_id=self.conversation_id, turn_id=turn_id, step_id=step_id,
                        call_id=call_id, document_id=document_id, candidate_revision=candidate_revision,
                        stage_revision=stage_revision):
                    try:
                        result = self._route(tool_name, arguments, effect, turn_id)
                        failed = result.get("code") not in (None, 0)
                    except Exception as error:  # noqa: BLE001 - sanitize executor boundary
                        result = _failure(error, operation=tool_name)
                        failed = True

                after_snapshot = self._current_snapshot()
                after = self._state(after_snapshot)
                changed = _changes(before, after)
                transition = stage_transition_proof(
                    effect,
                    {**before.as_dict(), "tools": snapshot["tools"]},
                    {**after.as_dict(), "tools": after_snapshot["tools"]},
                    tool_name=tool_name,
                )
                if transition is not None and transition["kind"] == "artifact_representation" and not (
                    before_candidate_kind == "genshin-whiteboard"
                    and getattr(self.session, "candidate", {}).get("kind") == "genshin-plan"
                    and before_document == fingerprint(getattr(self.session, "document", None))
                    and before_baseline == fingerprint(getattr(self.session, "baseline", None))
                    and artifact_representation_state_matches(before_stage_state, getattr(self.session, "stage_state", None))
                ):
                    transition = None
                invalid_stage_transition = (
                    effect == "stage_transition"
                    and transition is None
                    and not failed
                )
                if invalid_stage_transition:
                    # A successful phase tool must actually publish one
                    # reviewed phase/manifest transition.  Treat a partial
                    # mutation as outcome-unknown rather than blessing its
                    # unrelated candidate changes as a phase change.
                    result = {
                        "code": 1,
                        "reason": "native_app_stage_transition_missing",
                        "message": "阶段工具没有形成完整的阶段与工具目录切换。",
                    }
                    failed = True
                allowed = allowed_changes_for_transition(effect, transition)
                disallowed = sorted(set(changed) - allowed)
                retained_diagnostic = (
                    failed
                    and bool(changed)
                    and not disallowed
                    and allows_settled_failure_commit(tool_name, effect, result,
                        diagnostic_cache=getattr(self.session, '_diagnostic_cache', None),
                        require_cached_verification=True,
                        candidate_state={'revision': getattr(self.session, 'revision', None),
                            'fingerprint': fingerprint(getattr(self.session, 'candidate', None))})
                )
                if disallowed or failed and changed and not retained_diagnostic:
                    reason = (
                        "native_app_stage_transition_missing"
                        if invalid_stage_transition
                        else "effect_mask_violation"
                        if disallowed
                        else "failed_tool_changed_state"
                    )
                    settled = self.ledger.settle(
                        identity=identity,
                        effect=effect,
                        state=UNKNOWN,
                        after_state_hash=after.state_hash,
                        terminal_reason=reason,
                        stage_transition=transition,
                    )
                    self.snapshot = after_snapshot
                    return {
                        **self._project_terminal(settled["entry"], None),
                        "state_changes": changed,
                        "disallowed_state_changes": disallowed,
                        "stage_transition": copy.deepcopy(transition),
                        "next_snapshot": copy.deepcopy(after_snapshot),
                    }
                if not failed and effect in REQUIRES_CHANGE and not changed:
                    result = {
                        "code": 1,
                        "reason": "native_app_tool_success_without_effect",
                        "message": "工具报告成功但没有产生声明的状态变化。",
                    }
                    failed = True
                state = SETTLED_FAILURE if failed else SETTLED_SUCCESS
                reason = result.get("reason") if failed else None
                if failed and (not isinstance(reason, str) or not reason):
                    reason = "native_app_tool_error"
                settled = self.ledger.settle(
                    identity=identity,
                    effect=effect,
                    state=state,
                    after_state_hash=after.state_hash,
                    result=result,
                    terminal_reason=reason,
                    stage_transition=transition,
                )
                self.snapshot = after_snapshot
                return {
                    **self._project_terminal(settled["entry"], settled.get("result")),
                    "state_changes": changed,
                    "disallowed_state_changes": [],
                    "stage_transition": copy.deepcopy(transition),
                    "next_snapshot": copy.deepcopy(after_snapshot),
                }
        finally:
            self._execution_lock.release()

    def _project_prior(
        self,
        prior: Mapping[str, Any],
        *,
        call_id: str,
        tool_name: str,
    ) -> dict[str, Any]:
        if prior["disposition"] != "replay":
            return {
                "status": INFLIGHT,
                "call_id": call_id,
                "tool_name": tool_name,
                "effect": prior["entry"]["effect"],
                "replayable": False,
            }
        current_snapshot = self._current_snapshot()
        current_state = self._state(current_snapshot)
        if current_state.state_hash != prior["entry"]["after_state_hash"]:
            _fail(
                "已结算工具对应的领域状态不再匹配，不能回放旧结果",
                "native_app_terminal_state_mismatch",
            )
        return {
            **self._project_terminal(
                prior["entry"], prior.get("result"), replay=True,
            ),
            # A settlement request can be lost before Node observes the fresh
            # catalog.  Exact replay therefore carries the post-call snapshot.
            "next_snapshot": copy.deepcopy(current_snapshot),
        }

    @staticmethod
    def _project_terminal(
        entry: Mapping[str, Any],
        result: Mapping[str, Any] | None,
        *,
        replay: bool = False,
    ) -> dict[str, Any]:
        projected = {
            "status": "replay" if replay else entry["state"],
            "terminal_state": entry["state"],
            "call_id": entry["identity"]["call_id"],
            "tool_name": entry["identity"]["tool_name"],
            "effect": entry["effect"],
            "result_hash": entry["result_hash"],
            "before_state_hash": entry["before_state_hash"],
            "after_state_hash": entry["after_state_hash"],
            "stage_transition": copy.deepcopy(entry["stage_transition"]),
        }
        if result is not None:
            projected["result"] = copy.deepcopy(dict(result))
        if entry["terminal_reason"] is not None:
            projected["terminal_reason"] = entry["terminal_reason"]
        return projected

    def recover_interrupted_calls(self) -> list[str]:
        # Recovery chooses UNKNOWN from the current durable domain state.  In
        # production take the writer boundary unconditionally so another Host
        # cannot admit a write between an unlocked ledger peek and recovery.
        with self._execution_boundary("candidate"):
            snapshot = self._current_snapshot()
            return self.ledger.recover_inflight(
                after_state_hash=self._state(snapshot).state_hash,
            )


__all__ = [
    "NativeAppStateVector",
    "NativeAppToolHost",
]
