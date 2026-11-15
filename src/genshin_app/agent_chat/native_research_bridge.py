"""App-side lifecycle adapter for the official DSH Web tools.

The Node runtime owns the upstream ``web_search``/``web_fetch`` tools.  This
module owns the durable side of their lifecycle: a call must be registered in
``NativeResearchLedger`` before Node may dispatch it, and every observed tool
terminal must match that immutable registration before it is settled.

The adapter deliberately has no network or model-facing API.  It is created
for one turn and workflow stage, so neither side of the pipe can substitute a
different call, tool, argument set, or research identity after registration.
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Mapping, Sequence
from typing import Any

from genshin_app.equipment.schema import PlanError

from .native_research_ledger import NativeResearchLedger

_TOOLS = frozenset({"web_search", "web_fetch"})
_TERMINALS = frozenset({"completed", "failed", "cancelled", "outcome_unknown"})
_TERMINAL_FIELDS = frozenset(
    {
        "research_id",
        "call_id",
        "tool",
        "arguments",
        "raw_arguments_hash",
        "status",
        "content_hash",
        "presentation_meta",
        "resource_ids",
    }
)


class NativeResearchBridgeError(RuntimeError):
    """Stable bridge failure that never retains Web content or provider errors."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise NativeResearchBridgeError(code)


def _copy_mapping(value: object, code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    try:
        return copy.deepcopy(dict(value))
    except (TypeError, ValueError, RecursionError):
        _fail(code)


class NativeResearchTurnBridge:
    """Serialize one turn's durable Web pre-dispatch and terminal callbacks."""

    def __init__(
        self,
        *,
        ledger: NativeResearchLedger,
        turn_id: str,
        epoch_id: str,
        workflow_stage: str,
    ) -> None:
        if not isinstance(ledger, NativeResearchLedger):
            raise TypeError("ledger must be NativeResearchLedger")
        snapshot = ledger.read()
        if not isinstance(turn_id, str) or not turn_id:
            _fail("research_turn_identity_invalid")
        if not isinstance(epoch_id, str) or not epoch_id:
            _fail("research_epoch_identity_invalid")
        if not isinstance(workflow_stage, str) or not workflow_stage:
            _fail("research_stage_identity_invalid")
        self._ledger = ledger
        self._turn_id = turn_id
        self._epoch_id = epoch_id
        self._workflow_stage = workflow_stage
        self._expected_revision = snapshot["revision"]
        self._lock = threading.RLock()
        self._registered: dict[str, str] = {}
        self._settled: set[str] = set()

    @property
    def expected_revision(self) -> int:
        with self._lock:
            return self._expected_revision

    @property
    def conversation_id(self) -> str:
        return self._ledger.conversation_id

    @property
    def turn_id(self) -> str:
        return self._turn_id

    @property
    def workflow_stage(self) -> str:
        return self._workflow_stage

    @property
    def epoch_id(self) -> str:
        return self._epoch_id

    def pre_register(
        self,
        *,
        call_id: str,
        tool: str,
        arguments: Mapping[str, Any],
        raw_arguments_hash: str,
    ) -> dict[str, Any]:
        """Persist the exact DSH call before allowing any outbound request."""

        if tool not in _TOOLS:
            _fail("research_tool_forbidden")
        checked_arguments = _copy_mapping(
            arguments, "research_arguments_invalid",
        )
        with self._lock:
            try:
                result = self._ledger.register(
                    expected_revision=self._expected_revision,
                    turn_id=self._turn_id,
                    epoch_id=self._epoch_id,
                    workflow_stage=self._workflow_stage,
                    call_id=call_id,
                    tool=tool,
                    arguments=checked_arguments,
                    raw_arguments_hash=raw_arguments_hash,
                )
            except PlanError as error:
                code = getattr(error, "reason", None) or "research_pre_registration_failed"
                raise NativeResearchBridgeError(str(code)) from None
            status = result.get("status")
            if status not in {"registered", "already_registered"}:
                _fail("research_pre_registration_ack_invalid")
            revision = result.get("research_ledger_revision")
            research_id = result.get("research_id")
            if type(revision) is not int or revision < 0 or not isinstance(
                research_id, str,
            ):
                _fail("research_pre_registration_ack_invalid")
            self._expected_revision = revision
            existing = self._registered.get(call_id)
            if existing is not None and existing != research_id:
                _fail("research_call_binding_conflict")
            self._registered[call_id] = research_id
            if status == "already_registered":
                ledger = self._ledger.read()
                row = next(
                    (
                        item
                        for item in ledger["entries"]
                        if item["research_id"] == research_id
                    ),
                    None,
                )
                if row is None:
                    _fail("research_call_binding_conflict")
                if row["status"] != "pending":
                    self._settled.add(research_id)
            return copy.deepcopy(result)

    def settle_terminal(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Verify one DSH result callback and durably settle its registration."""

        value = _copy_mapping(payload, "research_terminal_invalid")
        if set(value) != _TERMINAL_FIELDS:
            _fail("research_terminal_invalid")
        status = value.get("status")
        if status not in _TERMINALS:
            _fail("research_terminal_invalid")
        call_id = value.get("call_id")
        research_id = value.get("research_id")
        tool = value.get("tool")
        if (
            not isinstance(call_id, str)
            or not isinstance(research_id, str)
            or tool not in _TOOLS
        ):
            _fail("research_terminal_invalid")
        arguments = _copy_mapping(
            value.get("arguments"), "research_terminal_invalid",
        )
        raw_arguments_hash = value.get("raw_arguments_hash")
        if not isinstance(raw_arguments_hash, str):
            _fail("research_terminal_invalid")
        resource_ids = value.get("resource_ids")
        if (
            not isinstance(resource_ids, Sequence)
            or isinstance(resource_ids, (str, bytes))
        ):
            _fail("research_terminal_invalid")
        with self._lock:
            if self._registered.get(call_id) != research_id:
                _fail("research_terminal_binding_mismatch")
            ledger = self._ledger.read()
            row = next(
                (
                    item
                    for item in ledger["entries"]
                    if item["research_id"] == research_id
                ),
                None,
            )
            if row is None or (
                row["call_id"] != call_id
                or row["turn_id"] != self._turn_id
                or row["epoch_id"] != self._epoch_id
                or row["workflow_stage"] != self._workflow_stage
                or row["tool"] != tool
                or row["arguments"] != arguments
                or row["raw_arguments_hash"] != raw_arguments_hash
            ):
                _fail("research_terminal_binding_mismatch")
            try:
                result = self._ledger.settle(
                    research_id=research_id,
                    status=status,
                    content_hash=value.get("content_hash"),
                    presentation_meta=value.get("presentation_meta"),
                    resource_ids=list(resource_ids),
                )
            except PlanError as error:
                code = getattr(error, "reason", None) or "research_terminal_failed"
                raise NativeResearchBridgeError(str(code)) from None
            self._settled.add(research_id)
            return result

    def settle_unfinished_as_unknown(self) -> tuple[str, ...]:
        """Close registrations whose result callback cannot be proven delivered."""

        settled: list[str] = []
        with self._lock:
            for research_id in self._registered.values():
                if research_id in self._settled:
                    continue
                try:
                    self._ledger.settle(
                        research_id=research_id,
                        status="outcome_unknown",
                    )
                except PlanError as error:
                    code = getattr(error, "reason", None) or "research_terminal_failed"
                    raise NativeResearchBridgeError(str(code)) from None
                self._settled.add(research_id)
                settled.append(research_id)
        return tuple(settled)

    def public_status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "research_ledger_revision": self._expected_revision,
                "registered_count": len(self._registered),
                "settled_count": len(self._settled),
                "pending_count": len(self._registered) - len(self._settled),
            }


__all__ = ["NativeResearchBridgeError", "NativeResearchTurnBridge"]
