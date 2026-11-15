"""Nonblocking admission to App-owned resources; DSH still schedules tools.

Waiting tickets never occupy an executor.  The unresolved-ingress watermark is
connection-wide: an unknown alias cannot be overtaken by a known alias of the
same resource. No candidate, file operation or tool ledger is owned here.
"""
from __future__ import annotations

import copy
import re
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass

from .native_app_call_limits import MAX_APP_CALLS

CANCEL_CONTRACT = "genshin-native-dsh-domain-cancel-v1"
CANCEL_RESULT_CONTRACT = "genshin-native-dsh-domain-cancel-result-v1"
APP_KINDS = frozenset({"dsh_app_call_request", "dsh_app_bind_request"})
_CANCEL_FIELDS = frozenset({"contract", "request_id", "session_id", "target_request_id",
                          "target_kind", "call_id", "raw_arguments_hash", "reason"})


class AppQueueProtocolError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code
        self.failure = {"code": code, "message": "App请求尚未执行；资源状态未能证明可以继续，请核对后重新请求。",
                        "details": {"app_call_admission": "not_recorded", "execution_state": "did_not_execute"}}
        self.outcome_unknown = False


class AppCallDeferred(Exception):
    """Only a proved pre-admission, temporary App resource occupation."""


@dataclass
class _Ticket:
    seq: int
    kind: str
    request: dict
    resource: object = None
    state: str = "resolving"
    result: dict | None = None
    route_error: Exception | None = None
    notified: bool = False
    deferred_generation: int | None = None
    started_generation: int | None = None


class NativeAppCallQueue:
    def __init__(self, *, executor, control_executor, route_cached, route_resolve,
                 execute, finished, cancelled, reject=None, resource_snapshot=None,
                 subscribe_resource=None, max_running=4, activity_changed=None, fatal=None,
                 retained_receipts=None, receipt_lookup=None):
        if retained_receipts is None:
            retained_receipts = MAX_APP_CALLS
        if type(retained_receipts) is not int or retained_receipts < 1:
            raise ValueError("retained_receipts must be a positive integer")
        self._executor, self._controls = executor, control_executor
        self._cached, self._resolve = route_cached, route_resolve
        self._execute, self._finished, self._cancelled = execute, finished, cancelled
        self._reject = reject
        self._resource_snapshot = resource_snapshot or (lambda _key: {"busy": False, "generation": 0})
        self._subscribe = subscribe_resource or (lambda _key, _notify: None)
        self._capacity = max_running
        self._activity_changed = activity_changed or (lambda: None)
        self._fatal = fatal or (lambda: None)
        self._retained_receipts = retained_receipts
        self._receipt_lookup = receipt_lookup or (lambda _request: None)
        self._condition = threading.Condition(threading.RLock())
        self._tickets: dict[str, _Ticket] = {}
        self._completed: OrderedDict[str, _Ticket] = OrderedDict()
        self._receive_seq = 0
        self._running: dict[object, _Ticket] = {}
        self._watchers: dict[object, object] = {}
        self._cancellations: OrderedDict[str, tuple[dict, dict]] = OrderedDict()
        self._tombstones: dict[str, dict] = {}
        self._closed = False
        self._resolutions = 0

    def submit(self, kind, request):
        value = copy.deepcopy(request)
        rid = value["request_id"]
        with self._condition:
            if self._closed:
                raise AppQueueProtocolError("dsh_domain_dispatch_closed")
            if rid in self._tickets or rid in self._completed:
                raise AppQueueProtocolError("dsh_domain_request_duplicate")
            self._receive_seq += 1
            ticket = _Ticket(self._receive_seq, kind, value)
            self._tickets[rid] = ticket
            tombstone = self._tombstones.pop(rid, None)
            if tombstone is not None:
                self._assert_target(tombstone, ticket)
                ticket.state = "cancelled"
            else:
                resource = self._cached(value["session_id"])
                if resource is not None:
                    ticket.resource, ticket.state = resource, "queued"
                else:
                    self._resolutions += 1
                    future = self._controls.submit(self._resolve_ticket, ticket)
                    future.add_done_callback(self._resolution_finished)
            self._pump_locked()
        if ticket.state == "cancelled":
            self._notify_cancelled(ticket)

    def _resolve_ticket(self, ticket):
        error = None
        try:
            resource = self._resolve(ticket.request["session_id"])
        except Exception as failure:  # noqa: BLE001 - domain rejection must reach its exact caller
            resource, error = None, failure
        with self._condition:
            if ticket.state != "resolving":
                return
            # This key grants no authority. The domain owner returns the exact
            # unbound/compatibility failure when the received request executes.
            ticket.resource = resource if resource is not None else ("unbound", ticket.request["session_id"])
            ticket.route_error = error
            ticket.state = "queued"
            self._pump_locked()

    def _resolution_finished(self, _future):
        with self._condition:
            self._resolutions -= 1
            self._condition.notify_all()
        self._activity_changed()

    def _pump_locked(self):
        if self._closed:
            return
        unresolved = [row.seq for row in self._tickets.values() if row.state == "resolving"]
        watermark = min(unresolved, default=float("inf"))
        considered = set()
        for ticket in self._tickets.values():
            if len(self._running) >= self._capacity:
                break
            if ticket.seq >= watermark:
                break
            if ticket.state not in {"queued", "waiting_resource"}:
                continue
            key = ticket.resource
            if key in considered or key in self._running:
                continue
            considered.add(key)
            if key not in self._watchers:
                dispose = self._subscribe(key, self.resource_changed)
                if dispose is not None:
                    self._watchers[key] = dispose
            # Subscribe before rechecking current state. Notifications and this
            # recheck share the queue condition; an earlier release isn't lost.
            state = self._resource_snapshot(key)
            if state["busy"]:
                ticket.state = "waiting_resource"
                continue
            if ticket.deferred_generation == state["generation"]:
                ticket.route_error = AppQueueProtocolError("app_resource_progress_unconfirmed")
            ticket.started_generation = state["generation"]
            ticket.state = "submitted"
            self._running[key] = ticket
            self._executor.submit(self._run, ticket)

    def resource_changed(self):
        with self._condition:
            self._pump_locked()
            self._condition.notify_all()

    def _run(self, ticket):
        with self._condition:
            if ticket.state == "cancelled":
                return
            ticket.state = "running"
        result = None
        try:
            if ticket.route_error is not None:
                result = (self._reject(ticket.kind, ticket.request, ticket.route_error)
                          if self._reject is not None else ticket.route_error.failure)
            else:
                result = self._execute(ticket.kind, ticket.request)
        except AppCallDeferred:
            with self._condition:
                self._running.pop(ticket.resource, None)
                ticket.state = "cancelled" if self._closed else "waiting_resource"
                # Re-run only after real resource progress. An owner claiming
                # busy while the snapshot stays available must not spin.
                ticket.deferred_generation = ticket.started_generation
                self._pump_locked()
            if ticket.state == "cancelled":
                self._notify_cancelled(ticket)
            return
        except Exception as error:  # noqa: BLE001 - do not strand drain after an executor failure
            try:
                if self._reject is not None:
                    result = self._reject(ticket.kind, ticket.request, error)
            except Exception:  # noqa: BLE001 - failed result delivery retires transport, never retries tool
                self._fatal()
        with self._condition:
            ticket.state, ticket.result = "settled", result
            self._running.pop(ticket.resource, None)
            self._pump_locked()
        try:
            self._finished(ticket.request["request_id"])
        finally:
            with self._condition:
                ticket.notified = True
                self._compact_locked(ticket)
                self._condition.notify_all()

    def _compact_locked(self, ticket):
        rid = ticket.request["request_id"]
        self._tickets.pop(rid, None)
        ticket.request = {key: ticket.request[key] for key in (
            "request_id", "session_id", "call_id", "raw_arguments_hash") if key in ticket.request}
        result = ticket.result or {}
        ticket.result = {"status": result.get("status"), "details": {
            "app_call_admission": result.get("details", {}).get("app_call_admission")}}
        ticket.route_error = None
        self._completed[rid] = ticket
        while len(self._completed) > self._retained_receipts:
            self._completed.popitem(last=False)
        if ticket.resource not in self._running and not any(
                row.resource == ticket.resource for row in self._tickets.values()):
            dispose = self._watchers.pop(ticket.resource, None)
            if dispose is not None:
                dispose()

    @staticmethod
    def _validate_cancel(value):
        if (type(value) is not dict or set(value) != _CANCEL_FIELDS
                or value["contract"] != CANCEL_CONTRACT or value["target_kind"] not in APP_KINDS
                or value["reason"] not in {"user_cancel", "scope_disposed", "runtime_drain"}
                or type(value["session_id"]) is not str or not value["session_id"]):
            raise AppQueueProtocolError("dsh_domain_cancel_invalid")
        try:
            uuid.UUID(value["request_id"])
            uuid.UUID(value["target_request_id"])
        except (ValueError, TypeError, AttributeError):
            raise AppQueueProtocolError("dsh_domain_cancel_invalid") from None
        if value["target_kind"] == "dsh_app_bind_request":
            valid = value["call_id"] is None and value["raw_arguments_hash"] is None
        else:
            valid = (type(value["call_id"]) is str and bool(value["call_id"])
                     and type(value["raw_arguments_hash"]) is str
                     and re.fullmatch("[0-9a-f]{64}", value["raw_arguments_hash"]) is not None)
        if not valid:
            raise AppQueueProtocolError("dsh_domain_cancel_invalid")

    @staticmethod
    def _assert_target(value, ticket):
        call = ticket.kind == "dsh_app_call_request"
        if (value["session_id"] != ticket.request["session_id"] or value["target_kind"] != ticket.kind
                or value["call_id"] != (ticket.request.get("call_id") if call else None)
                or value["raw_arguments_hash"] != (ticket.request.get("raw_arguments_hash") if call else None)):
            raise AppQueueProtocolError("dsh_domain_cancel_target_mismatch")

    def cancel(self, value):
        self._validate_cancel(value)
        with self._condition:
            prior = self._cancellations.get(value["request_id"])
            if prior is not None:
                if prior[0] != value:
                    raise AppQueueProtocolError("dsh_domain_cancel_conflict")
                return copy.deepcopy(prior[1])
            needs_receipt = (value["target_request_id"] not in self._tickets
                             and value["target_request_id"] not in self._completed)
        # Aged-out status comes from the App ledger, outside the queue lock.
        # No tool execution/activation is allowed through this callback.
        receipt = self._receipt_lookup(value) if needs_receipt else None
        cancelled = None
        with self._condition:
            prior = self._cancellations.get(value["request_id"])
            if prior is not None:
                if prior[0] != value:
                    raise AppQueueProtocolError("dsh_domain_cancel_conflict")
                return copy.deepcopy(prior[1])
            ticket = self._tickets.get(value["target_request_id"]) or self._completed.get(value["target_request_id"])
            disposition, admission, execution, unknown = "not_observed", "unknown", "unknown_after_interruption", True
            if ticket is None:
                prior_tombstone = self._tombstones.get(value["target_request_id"])
                if prior_tombstone is not None and any(prior_tombstone[key] != value[key]
                        for key in ("session_id", "target_kind", "call_id", "raw_arguments_hash")):
                    raise AppQueueProtocolError("dsh_domain_cancel_target_mismatch")
                if isinstance(receipt, dict):
                    status = receipt.get("state")
                    if status in {"settled_success", "settled_failure"}:
                        disposition, admission, execution, unknown = "already_settled", status, status, False
                if unknown:
                    # Only unresolved transport targets remain live. Settled
                    # history is looked up in its ledger, not kept as tombstones.
                    self._tombstones[value["target_request_id"]] = copy.deepcopy(value)
            else:
                self._assert_target(value, ticket)
                if ticket.state in {"queued", "resolving", "waiting_resource", "submitted", "cancelled"}:
                    if ticket.state != "cancelled":
                        ticket.state, cancelled = "cancelled", ticket
                        if self._running.get(ticket.resource) is ticket:
                            self._running.pop(ticket.resource, None)
                    disposition, admission, execution, unknown = "cancelled_before_admission", "not_recorded", "did_not_execute", False
                elif ticket.state == "running":
                    disposition, admission, execution = "running", "unknown", "running"
                else:
                    result = ticket.result or {}
                    status = result.get("status")
                    if status in {"settled_success", "settled_failure"}:
                        disposition, admission, execution, unknown = "already_settled", status, status, False
                    elif (result.get("details", {}).get("app_call_admission") == "not_recorded"
                            or ticket.kind == "dsh_app_bind_request"):
                        disposition, admission, execution, unknown = "already_settled", "not_recorded", "did_not_execute", False
            answer = {"contract": CANCEL_RESULT_CONTRACT,
                **{key: value[key] for key in ("request_id", "session_id", "target_request_id", "target_kind", "call_id")},
                "disposition": disposition, "app_call_admission": admission, "execution_state": execution,
                "outcome_unknown": unknown,
                "settlement_ref": receipt.get("settlement_ref") if isinstance(receipt, dict) else None,
                "diagnostic_id": None}
            self._cancellations[value["request_id"]] = copy.deepcopy(value), copy.deepcopy(answer)
            while len(self._cancellations) > self._retained_receipts:
                self._cancellations.popitem(last=False)
            self._pump_locked()
        if cancelled is not None:
            self._notify_cancelled(cancelled)
        return answer

    def _notify_cancelled(self, ticket):
        try:
            self._cancelled(ticket.request)
        finally:
            try:
                self._finished(ticket.request["request_id"])
            finally:
                with self._condition:
                    ticket.notified = True
                    self._compact_locked(ticket)
                    self._condition.notify_all()

    def wait_idle(self):
        with self._condition:
            while self._resolutions or any(not row.notified for row in self._tickets.values()):
                self._condition.wait()

    def close(self):
        with self._condition:
            if self._closed:
                return
            self._closed = True
            waiting = [row for row in self._tickets.values()
                       if row.state in {"queued", "resolving", "waiting_resource", "submitted"}]
            for ticket in waiting:
                ticket.state = "cancelled"
                if self._running.get(ticket.resource) is ticket:
                    self._running.pop(ticket.resource, None)
            watchers = list(self._watchers.values())
            self._watchers.clear()
            self._tombstones.clear()
            self._cancellations.clear()
        for dispose in watchers:
            dispose()
        for ticket in waiting:
            self._notify_cancelled(ticket)

    def snapshot(self):
        with self._condition:
            return {"running": len(self._running),
                    "waiting": sum(row.state in {"queued", "waiting_resource", "resolving"}
                                   for row in self._tickets.values()),
                    "resolving_routes": self._resolutions,
                    "active_tickets": len(self._tickets), "retained_receipts": len(self._completed),
                    "retained_cancellations": len(self._cancellations),
                    "unmatched_cancellations": len(self._tombstones),
                    "receive_seq": self._receive_seq, "closed": self._closed}

    @property
    def retained_receipts(self):
        return self._retained_receipts
