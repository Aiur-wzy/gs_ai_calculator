"""Thin Host client for DSH's public Remote transport over an authenticated mux.

This client owns transport correlations and backpressure, never Session, Turn,
messages, questions, or domain permissions. The caller owns launch/authentication
and chooses when to close the connection. No request is automatically retried.
"""
from __future__ import annotations

import json
import struct
import threading
import uuid
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from .native_app_call_queue import APP_KINDS, AppCallDeferred, NativeAppCallQueue
from .native_dsh_logical_transport import (
    DIRECT_THRESHOLD_BYTES,
    LogicalBody,
    NativeDshLogicalTransport,
)
from .native_logical_stream import NativeLogicalStreamError
from .native_mux_protocol import MAX_MUX_PAYLOAD_BYTES

PRODUCTION_OWNER = False
CONTRACT = "genshin-native-dsh-mux-gateway-v1"
_PREFIX = struct.Struct(">I")
_MAX_METADATA_BYTES = 64 * 1024


class NativeDshMuxError(RuntimeError):
    def __init__(self, code: str, *, failure: dict | None = None, outcome_unknown=False):
        super().__init__(code)
        self.code = code
        self.failure = failure
        self.outcome_unknown = outcome_unknown


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def encode_dsh_fetch_payload(metadata: dict, body: bytes = b"") -> bytes:
    encoded = _json(metadata)
    if len(encoded) > _MAX_METADATA_BYTES:
        raise NativeDshMuxError("dsh_mux_metadata_size")
    if len(encoded) + 4 + len(body) > MAX_MUX_PAYLOAD_BYTES:
        raise NativeDshMuxError("dsh_mux_fetch_size")
    return _PREFIX.pack(len(encoded)) + encoded + body


def decode_dsh_fetch_payload(payload: bytes) -> tuple[dict, bytes]:
    if len(payload) < 4:
        raise NativeDshMuxError("dsh_mux_metadata_invalid")
    (size,) = _PREFIX.unpack_from(payload)
    if size > _MAX_METADATA_BYTES or size + 4 > len(payload):
        raise NativeDshMuxError("dsh_mux_metadata_invalid")
    try:
        metadata = json.loads(payload[4:4 + size])
    except (ValueError, UnicodeError):
        raise NativeDshMuxError("dsh_mux_metadata_invalid") from None
    if not isinstance(metadata, dict):
        raise NativeDshMuxError("dsh_mux_metadata_invalid")
    return metadata, payload[4 + size:]


@dataclass
class _Pending:
    event: threading.Event = field(default_factory=threading.Event)
    packet: Any = None
    error: NativeDshMuxError | None = None


@dataclass
class _Stream:
    sequence: int = 0
    values: deque = field(default_factory=deque)
    opened: bool = False
    terminal: bool = False
    cancelled: bool = False
    error: dict | None = None
    ack: int = 0
    ack_pending: int | None = None
    has_uplink: bool = False
    uplink_ended: bool = False
    credit: bool = False
    read_lock: Any = field(default_factory=threading.Lock)
    send_lock: Any = field(default_factory=threading.Lock)


class NativeDshMuxClient:
    """One reader, serialized writes, cancellable independent RPCs/streams.

    A single credit is issued by ``stream_next``. A consumer can wait for a long
    DSH operation without an arbitrary task timeout; close/cancel always wakes
    it. Physical framing retains the carrier's partial-frame/write deadlines.
    """

    def __init__(self, carrier, *, domain_handler=None, domain_workers=4, spool_root=None):
        self._carrier = carrier
        self._condition = threading.Condition(threading.RLock())
        self._writer = threading.Lock()
        self._pending: dict[str, _Pending] = {}
        self._streams: dict[str, _Stream] = {}
        self._failure: NativeDshMuxError | None = None
        self._closed = False
        self._cancelled_fetches: set[str] = set()
        self._cancelled_streams: set[str] = set()
        self._domain_handler = domain_handler
        self._domain_requests: set[str] = set()
        self._domain_retired: OrderedDict[str, None] = OrderedDict()
        self._active_domain_requests: set[str] = set()
        self._active_control_requests: dict[str, int] = {}
        self._domain_executor = ThreadPoolExecutor(max_workers=domain_workers,
                                                  thread_name_prefix="dsh-app-domain")
        self._control_executor = ThreadPoolExecutor(max_workers=1,
                                                   thread_name_prefix="dsh-app-control")
        # Only the trusted domain owner supplies candidate routing. Fixtures
        # without that port share one connection resource; never trust a
        # model-provided session/document ID as permission for another writer.
        self._app_queue = NativeAppCallQueue(
            executor=self._domain_executor, control_executor=self._control_executor,
            route_cached=getattr(domain_handler, "domain_route_cached", lambda _sid: "connection"),
            route_resolve=getattr(domain_handler, "domain_route_resolve", lambda _sid: "connection"),
            resource_snapshot=getattr(domain_handler, "domain_resource_snapshot", None),
            subscribe_resource=getattr(domain_handler, "subscribe_domain_resource", None),
            execute=self._execute_domain_request, reject=self._reject_domain_request,
            finished=self._domain_finished, cancelled=self._cancelled_domain_request,
            max_running=domain_workers, activity_changed=self._queue_activity_changed,
            fatal=lambda: self._fail("dsh_domain_dispatch_failed"),
            receipt_lookup=getattr(domain_handler, "domain_operation_receipt", None))
        self._logical = NativeDshLogicalTransport(send=self._send, on_artifact=self._logical_artifact,
            on_cancel=self._logical_cancelled,
            is_cancelled=lambda identity: identity in self._cancelled_fetches, spool_root=spool_root)
        self._reader = threading.Thread(target=self._read, name="dsh-mux-host-reader", daemon=True)
        self._reader.start()

    def _ensure_open(self):
        if self._failure is not None:
            raise self._failure
        if self._closed:
            raise NativeDshMuxError("dsh_mux_closed")

    def _send(self, lane, kind, payload, *, correlation, stream_id=None, sequence=None, logical=False):
        with self._writer:
            enqueue_failed = False
            with self._condition:
                self._ensure_open()
                if kind == "dsh_fetch" and correlation in self._cancelled_fetches:
                    raise NativeDshMuxError("dsh_mux_request_cancelled")
                if stream_id is not None and not logical:
                    stream = self._streams.get(stream_id)
                    if stream is None or stream.cancelled:
                        raise NativeDshMuxError("dsh_mux_stream_cancelled")
                    if kind == "dsh_stream_credit":
                        if stream.terminal:
                            return None
                        if stream.credit:
                            raise NativeDshMuxError("dsh_mux_stream_credit_violation")
                        stream.credit = True
                    if kind in {"dsh_stream_item", "dsh_stream_end"}:
                        if not stream.has_uplink or stream.uplink_ended or stream.terminal:
                            raise NativeDshMuxError("dsh_mux_stream_uplink_closed")
                        if stream.ack_pending is not None:
                            raise NativeDshMuxError("dsh_mux_stream_uplink_busy")
                    stream.sequence += 1
                    sequence = stream.sequence
                    if kind in {"dsh_stream_item", "dsh_stream_end"}:
                        stream.ack_pending = sequence
                        stream.uplink_ended = kind == "dsh_stream_end"
                # Assign per-stream sequence under the same writer lease as
                # enqueue, so concurrent uplink/pull cannot invert sequences.
                try:
                    self._carrier.enqueue(
                        lane=lane, payload_kind=kind, payload=payload,
                        message_id=str(uuid.uuid4()), correlation_id=correlation,
                        stream_id=stream_id, stream_seq=sequence,
                        trusted_class="control" if lane == "control" else "normal",
                    )
                except Exception:  # noqa: BLE001 - retire every waiter after carrier failure
                    enqueue_failed = True
            if enqueue_failed:
                self._fail("dsh_mux_write_failed")
                raise NativeDshMuxError("dsh_mux_write_failed") from None
            try:
                while self._carrier.flush_one() is not None:
                    pass
            except Exception:  # noqa: BLE001 - wake all waiters at the I/O boundary
                self._fail("dsh_mux_write_failed")
                raise NativeDshMuxError("dsh_mux_write_failed") from None
            return sequence

    def _fail(self, code):
        with self._condition:
            if self._failure is None:
                self._failure = NativeDshMuxError(code)
            for pending in self._pending.values():
                # A reply delivered before EOF remains authoritative even
                # when the waiting caller has not resumed yet.
                if not pending.event.is_set():
                    pending.error = self._failure
                    pending.event.set()
            self._condition.notify_all()
        self._carrier.close()
        self._logical.connection_lost()
        self._app_queue.close()
        self._domain_executor.shutdown(wait=False, cancel_futures=True)
        self._control_executor.shutdown(wait=False, cancel_futures=True)

    def _read(self):
        try:
            while True:
                delivery = self._carrier.receive_one()
                try:
                    self._dispatch(delivery.packet)
                    delivery.accept()
                except Exception:
                    if not delivery.settled:
                        delivery.outcome_unknown()
                    raise
        except Exception as error:  # noqa: BLE001 - reader failure must not strand callers
            self._fail(error.code if isinstance(error, NativeDshMuxError)
                       else "dsh_mux_connection_lost")

    def _dispatch(self, packet):
        header = packet.header
        kind = header["payload_kind"]
        if self._logical.accept(packet):
            return
        if kind == "dsh_app_call_cancel":
            if header.get("lane") != "control":
                raise NativeDshMuxError("dsh_domain_lane_invalid")
            request = json.loads(packet.payload)
            if type(request) is not dict or request.get("request_id") != header["correlation_id"]:
                raise NativeDshMuxError("dsh_domain_correlation_invalid")
            correlation = request["request_id"]
            with self._condition:
                self._active_control_requests[correlation] = self._active_control_requests.get(correlation, 0) + 1
            future = self._control_executor.submit(self._cancel_domain_request, request)
            future.add_done_callback(lambda _future: self._control_finished(correlation))
            return
        if kind in {"dsh_app_bind_request", "dsh_app_call_request", "dsh_research_register_request",
                    "dsh_research_settle_request", "dsh_budget_acquire_request", "dsh_budget_settle_request", "dsh_budget_tool_request"}:
            if header.get("lane") != "remote_unary":
                raise NativeDshMuxError("dsh_domain_lane_invalid")
            request = json.loads(packet.payload)
            correlation = header["correlation_id"]
            if (not isinstance(request, dict) or request.get("request_id") != correlation
                    or not isinstance(request.get("session_id"), str)):
                raise NativeDshMuxError("dsh_domain_correlation_invalid")
            with self._condition:
                if correlation in self._domain_requests:
                    raise NativeDshMuxError("dsh_domain_request_duplicate")
                self._domain_requests.add(correlation)
                self._active_domain_requests.add(correlation)
            try:
                if kind in APP_KINDS:
                    self._app_queue.submit(kind, request)
                else:
                    future = self._control_executor.submit(self._execute_domain_request, kind, request)
                    future.add_done_callback(lambda _future: self._domain_finished(correlation))
            except RuntimeError:
                self._domain_finished(correlation)
                raise NativeDshMuxError("dsh_domain_dispatch_closed") from None
            return

        with self._condition:
            if kind in {"dsh_fetch_result", "dsh_fetch_error", "dsh_fetch_cancelled",
                        "dsh_stream_cancelled", "dsh_boot_result", "dsh_bundle_result",
                        "dsh_asset_error", "dsh_runtime_control_result", "dsh_runtime_control_error"}:
                pending = self._pending.get(header["correlation_id"])
                # A cancelled caller may already have retired its correlation.
                if pending is not None:
                    pending.packet = packet
                    pending.event.set()
            else:
                stream = self._streams.get(header["stream_id"])
                if stream is None or stream.cancelled:
                    return
                body = json.loads(packet.payload)
                if kind == "dsh_stream_opened":
                    stream.opened = True
                elif kind == "dsh_stream_item":
                    # The peer may emit only one item per granted credit.
                    if stream.values or not stream.credit:
                        raise NativeDshMuxError("dsh_mux_stream_credit_violation")
                    stream.credit = False
                    stream.values.append(body["value"])
                elif kind == "dsh_stream_ack":
                    sequence = body.get("sequence")
                    if type(sequence) is not int or stream.ack_pending != sequence:
                        raise NativeDshMuxError("dsh_mux_stream_ack_invalid")
                    stream.ack = sequence
                    stream.ack_pending = None
                elif kind == "dsh_stream_end":
                    stream.credit = False
                    stream.terminal = True
                elif kind == "dsh_stream_error":
                    stream.credit = False
                    stream.error = body
                    stream.terminal = True
                else:
                    raise NativeDshMuxError("dsh_mux_message_unexpected")
            self._condition.notify_all()

    def _domain_finished(self, correlation):
        with self._condition:
            self._active_domain_requests.discard(correlation)
            self._domain_retired[correlation] = None
            while len(self._domain_retired) > self._app_queue.retained_receipts:
                expired, _ = self._domain_retired.popitem(last=False)
                self._domain_requests.discard(expired)
            self._condition.notify_all()

    def _queue_activity_changed(self):
        with self._condition:
            self._condition.notify_all()

    def _control_finished(self, correlation):
        with self._condition:
            remaining = self._active_control_requests.get(correlation, 1) - 1
            if remaining:
                self._active_control_requests[correlation] = remaining
            else:
                self._active_control_requests.pop(correlation, None)
            self._condition.notify_all()

    def wait_domain_idle(self):
        """Wait for actual submitted Host calls, never the replay-dedup set.

        No task wall timeout is imposed on a healthy engine/ledger operation.
        A failed pipe remains unknown and immediately wakes this barrier.
        """
        with self._condition:
            while (self._active_domain_requests or self._active_control_requests
                    or self._app_queue.snapshot()["resolving_routes"]):
                self._ensure_open()
                self._condition.wait()
            self._ensure_open()

    def _execute_domain_request(self, kind, request):
        response_kind = kind.replace("_request", "_result")
        try:
            if self._domain_handler is None:
                raise NativeDshMuxError("dsh_domain_unavailable")
            value = self._domain_handler(kind, request)
            if (not isinstance(value, dict) or value.get("request_id") != request["request_id"]
                    or value.get("session_id") != request["session_id"]):
                raise NativeDshMuxError("dsh_domain_result_identity", outcome_unknown=True)
        except AppCallDeferred:
            raise
        except Exception as error:  # noqa: BLE001 - never invent a settled tool result
            failure = error.failure if isinstance(error, NativeDshMuxError) else None
            details = failure.get("details", {}) if isinstance(failure, dict) else {}
            if (kind in APP_KINDS and getattr(error, "code", None) == "app_action_serial_gate_busy"
                    and details.get("app_call_admission") == "not_recorded"
                    and not error.outcome_unknown):
                # The exact Host proved no admission. The queue releases this
                # worker/locks and waits for the existing file resource lease.
                raise AppCallDeferred from None
            return self._reject_domain_request(kind, request, error)
        self._send_domain_response(response_kind, request, value)
        return value

    def _send_domain_response(self, response_kind, request, value):
        try:
            self._send("remote_unary", response_kind, _json(value), correlation=request["request_id"])
        except NativeDshMuxError:
            # Settlement stays durable after a lost response. Never replay it.
            pass

    def _reject_domain_request(self, kind, request, error):
        response_kind = kind.replace("_request", "_error")
        failure = getattr(error, "failure", None)
        value = {"request_id": request["request_id"], "session_id": request["session_id"],
                 "code": getattr(error, "code", "dsh_domain_execution_failed"),
                 "message": failure["message"] if failure else "App未能确认这次操作，请查看任务详情。",
                 "details": failure["details"] if failure else {},
                 "outcome_unknown": getattr(error, "outcome_unknown", True)}
        self._send_domain_response(response_kind, request, value)
        return value

    def _cancelled_domain_request(self, request):
        kind = "dsh_app_call_request" if "call_id" in request else "dsh_app_bind_request"
        return self._reject_domain_request(kind, request, NativeDshMuxError(
            "domain_call_cancelled", failure={"code": "domain_call_cancelled",
                "message": "App操作在准入前已取消，没有执行。",
                "details": {"app_call_admission": "not_recorded", "execution_state": "did_not_execute"}},
            outcome_unknown=False))

    def _cancel_domain_request(self, request):
        try:
            value = self._app_queue.cancel(request)
        except Exception as error:  # noqa: BLE001 - malformed control doesn't become an operation
            value = {"request_id": request.get("request_id"), "session_id": request.get("session_id"),
                "code": getattr(error, "code", "dsh_domain_cancel_invalid"),
                "message": "无法确认取消对象，请核对原操作状态。", "details": {}, "outcome_unknown": True}
        try:
            self._send("control", "dsh_app_call_cancel_result", _json(value), correlation=request["request_id"])
        except NativeDshMuxError:
            pass

    def _wait(self, predicate):
        with self._condition:
            while not predicate():
                self._ensure_open()
                self._condition.wait()
            self._ensure_open()

    def _logical_artifact(self, header, purpose, metadata, body):
        if purpose not in {"dsh_fetch_result", "dsh_bundle_result"} or metadata.get("result_kind") != purpose:
            body.release()
            with self._condition:
                pending = self._pending.get(header["correlation_id"])
                if pending is not None:
                    pending.error = NativeDshMuxError("dsh_mux_logical_purpose_invalid")
                    pending.event.set()
            return
        fetch_metadata = metadata.get("fetch_metadata")
        if not isinstance(fetch_metadata, dict):
            body.release()
            with self._condition:
                pending = self._pending.get(header["correlation_id"])
                if pending is not None:
                    pending.error = NativeDshMuxError("dsh_mux_logical_metadata_invalid")
                    pending.event.set()
            return
        with self._condition:
            pending = self._pending.get(header["correlation_id"])
            if pending is None or header["correlation_id"] in self._cancelled_fetches:
                body.release()
                return
            pending.packet = SimpleNamespace(header={"payload_kind": purpose}, payload=None,
                logical_metadata=fetch_metadata, logical_body=body)
            pending.event.set()

    def _logical_cancelled(self, identity):
        with self._condition:
            self._cancelled_fetches.add(identity)
            pending = self._pending.get(identity)
            if pending is not None:
                pending.error = NativeDshMuxError("dsh_mux_request_cancelled")
                pending.event.set()

    @staticmethod
    def _fetch_result(packet):
        if hasattr(packet, "logical_body"):
            return packet.logical_metadata, packet.logical_body
        return decode_dsh_fetch_payload(packet.payload)

    def fetch(self, metadata: dict, body: bytes = b"", *, request_id: str | None = None):
        """Return exact HTTP metadata/body; errors never trigger resubmission."""
        if isinstance(body, LogicalBody):
            return self.fetch_stream(metadata, body.chunks(), length=body.length, request_id=request_id)
        if len(body) > DIRECT_THRESHOLD_BYTES:
            return self.fetch_stream(metadata, (memoryview(body)[offset:offset + 8 * 1024 * 1024]
                for offset in range(0, len(body), 8 * 1024 * 1024)), length=len(body), request_id=request_id)
        packet = self._exchange("remote_unary", "dsh_fetch", encode_dsh_fetch_payload(metadata, body),
                                "dsh_fetch_result", request_id=request_id)
        return self._fetch_result(packet)

    def fetch_stream(self, metadata, source, *, length=None, request_id=None):
        try:
            packet = self._exchange("remote_unary", "dsh_fetch", None, "dsh_fetch_result",
                request_id=request_id, sender=lambda correlation: self._logical.send_source(
                    correlation=correlation, metadata=metadata, source=source, declared_length=length))
            return self._fetch_result(packet)
        except NativeLogicalStreamError as error:
            raise NativeDshMuxError(error.code) from None

    def boot(self):
        packet = self._exchange("bundle", "dsh_boot_request", b"{}", "dsh_boot_result")
        value = json.loads(packet.payload)
        if not isinstance(value, dict) or not isinstance(value.get("injections"), list):
            raise NativeDshMuxError("dsh_mux_boot_invalid")
        return value

    def control(self, operation: str, payload: dict | None = None):
        """Host-only control seam; no credential is part of an RPC URL/argv."""
        if operation not in {"dsh_secret_set", "dsh_secret_clear",
                             "dsh_runtime_status", "dsh_runtime_session_status", "dsh_runtime_drain", "dsh_runtime_shutdown"}:
            raise NativeDshMuxError("dsh_mux_control_operation_invalid")
        packet = self._exchange("control", operation, _json(payload or {}),
                                "dsh_runtime_control_result")
        value = json.loads(packet.payload)
        if (not isinstance(value, dict) or value.get("contract") != "genshin-native-dsh-runtime-control-v1"
                or value.get("operation") != operation or "result" not in value):
            raise NativeDshMuxError("dsh_mux_control_result_invalid")
        return value["result"]

    def bundle(self, url: str):
        packet = self._exchange("bundle", "dsh_bundle_request", _json({"url": url}),
                                "dsh_bundle_result")
        metadata, body = self._fetch_result(packet)
        content_type = next((value for key, value in metadata["headers"]
                             if key.lower() == "content-type"), None)
        return {"status": metadata["status"], "contentType": content_type, "body": body}

    def _exchange(self, lane, request_kind, payload, result_kind, *, request_id=None, sender=None):
        correlation = request_id or str(uuid.uuid4())
        pending = _Pending()
        with self._condition:
            self._ensure_open()
            if correlation in self._cancelled_fetches:
                raise NativeDshMuxError("dsh_mux_request_cancelled")
            if correlation in self._pending:
                raise NativeDshMuxError("dsh_mux_request_duplicate")
            self._pending[correlation] = pending
        try:
            if sender is None:
                self._send(lane, request_kind, payload, correlation=correlation)
            else:
                sender(correlation)
            pending.event.wait()
            if pending.error is not None:
                raise pending.error
            kind = pending.packet.header["payload_kind"]
            if kind in {"dsh_fetch_error", "dsh_asset_error", "dsh_runtime_control_error"}:
                failure = json.loads(pending.packet.payload)
                if (not isinstance(failure, dict) or type(failure.get("code")) is not str
                        or type(failure.get("message")) is not str
                        or not isinstance(failure.get("details"), dict)):
                    raise NativeDshMuxError("dsh_mux_fetch_error_invalid")
                raise NativeDshMuxError(failure["code"], failure=failure)
            if kind != result_kind:
                raise NativeDshMuxError("dsh_mux_fetch_failed")
            return pending.packet
        finally:
            with self._condition:
                self._pending.pop(correlation, None)

    def cancel_fetch(self, request_id: str):
        with self._condition:
            self._cancelled_fetches.add(request_id)
            pending = self._pending.get(request_id)
            if pending is not None:
                pending.error = NativeDshMuxError("dsh_mux_request_cancelled")
                pending.event.set()
        self._logical.cancel_correlation(request_id)
        self._send("control", "dsh_fetch_cancel", _json({"correlation_id": request_id}),
                   correlation=str(uuid.uuid4()))

    def _stream_send(self, stream_id, kind, value):
        return self._send("remote_stream", kind, _json(value), correlation=stream_id,
                          stream_id=stream_id)

    def _get_stream(self, stream_id):
        with self._condition:
            stream = self._streams.get(stream_id)
            if stream is None or stream.cancelled:
                raise NativeDshMuxError("dsh_mux_stream_cancelled")
            return stream

    def open_stream(self, endpoint: str, payload: Any, *, has_uplink=False,
                    stream_id: str | None = None) -> str:
        identity = stream_id or str(uuid.uuid4())
        with self._condition:
            self._ensure_open()
            if identity in self._cancelled_streams:
                raise NativeDshMuxError("dsh_mux_stream_cancelled")
            if identity in self._streams:
                raise NativeDshMuxError("dsh_mux_stream_duplicate")
            self._streams[identity] = _Stream(has_uplink=has_uplink)
        self._stream_send(identity, "dsh_stream_open", {
            "endpoint": endpoint, "payload": payload, "has_uplink": has_uplink,
        })
        # Opening an upstream stream can itself await its first uplink item.
        # Return its transport identity immediately, not a synthetic DSH result.
        return identity

    def stream_next(self, stream_id: str) -> dict:
        stream = self._get_stream(stream_id)
        with stream.read_lock:
            with self._condition:
                self._ensure_open()
                if stream.cancelled:
                    raise NativeDshMuxError("dsh_mux_stream_cancelled")
                terminal = stream.terminal
            if not terminal:
                self._stream_send(stream_id, "dsh_stream_credit", {"items": 1})
            self._wait(lambda: bool(stream.values) or stream.terminal or stream.cancelled)
            with self._condition:
                if stream.cancelled:
                    raise NativeDshMuxError("dsh_mux_stream_cancelled")
                if stream.values:
                    return {"done": False, "value": stream.values.popleft()}
                if stream.error is not None:
                    return {"done": True, "error": stream.error}
                return {"done": True}

    def stream_send(self, stream_id: str, value: Any = None, *, done=False):
        stream = self._get_stream(stream_id)
        with stream.send_lock:
            with self._condition:
                self._ensure_open()
                if stream.cancelled:
                    raise NativeDshMuxError("dsh_mux_stream_cancelled")
            sequence = self._stream_send(stream_id, "dsh_stream_end" if done
                                         else "dsh_stream_item", {} if done else {"value": value})
            self._wait(lambda: stream.ack >= sequence or stream.terminal or stream.cancelled)
            with self._condition:
                if stream.ack < sequence:
                    raise NativeDshMuxError("dsh_mux_stream_uplink_closed")
            return sequence

    def cancel_stream(self, stream_id: str):
        with self._condition:
            self._cancelled_streams.add(stream_id)
            stream = self._streams.get(stream_id)
            if stream is None or stream.cancelled:
                return
            stream.cancelled = True
            self._condition.notify_all()
        self._send("control", "dsh_stream_cancel", _json({"stream_id": stream_id}),
                   correlation=str(uuid.uuid4()))

    def release_stream(self, stream_id: str):
        try:
            self.cancel_stream(stream_id)
        finally:
            with self._condition:
                self._streams.pop(stream_id, None)

    def close(self):
        with self._condition:
            if self._closed:
                return
            self._closed = True
            for pending in self._pending.values():
                pending.error = NativeDshMuxError("dsh_mux_closed")
                pending.event.set()
            self._condition.notify_all()
        self._carrier.close()
        self._logical.close()
        self._app_queue.close()
        self._domain_executor.shutdown(wait=False, cancel_futures=True)
        self._control_executor.shutdown(wait=False, cancel_futures=True)

    def snapshot(self):
        with self._condition:
            return {"contract": CONTRACT, "production_owner": PRODUCTION_OWNER,
                    "closed": self._closed, "pending_requests": len(self._pending),
                    "streams": len(self._streams), "logical": self._logical.snapshot(),
                    "active_domain_requests": len(self._active_domain_requests),
                    "active_control_requests": sum(self._active_control_requests.values()),
                    "app_queue": self._app_queue.snapshot()}
