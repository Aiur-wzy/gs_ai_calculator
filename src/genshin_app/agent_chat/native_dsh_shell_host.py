"""WebView Host projection of the DSH carrier (no chat/session reducers)."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from .native_dsh_logical_transport import LogicalBody
from .native_dsh_mux_client import NativeDshMuxClient, NativeDshMuxError
from .native_logical_stream import HARD_MAX_LOGICAL_STREAM_BYTES

RESOURCE_CHUNK_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = HARD_MAX_LOGICAL_STREAM_BYTES
PRODUCTION_OWNER = False


def _identity(value):
    if type(value) is not str:
        raise NativeDshMuxError("dsh_shell_identity_invalid")
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except ValueError:
        raise NativeDshMuxError("dsh_shell_identity_invalid") from None
    return value


def _endpoint(value):
    if (type(value) is not str or not value or value.startswith("/")
            or any(token in value for token in ("\\", "?", "#"))
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise NativeDshMuxError("dsh_shell_endpoint_invalid")
    return value


@dataclass
class _StreamRead:
    cursor: int = 0
    previous_cursor: int | None = None
    previous_result: dict | None = None
    lock: Any = field(default_factory=threading.Lock)
    send_lock: Any = field(default_factory=threading.Lock)
    send_sequence: int = -1
    has_uplink: bool = False
    send_ended: bool = False
    send_failed: bool = False
    cancelled: bool = False
    ended: bool = False


class NativeDshShellHost:
    """Thin, per-client Host methods shared by GUI and CLI projection tests.

    ``boot`` and ``bundle`` call Node's accepted client-module composition;
    they are injected transport functions, not filesystem/network fallbacks.
    Resources are connection-scoped, immutable and released after loading.
    """

    def __init__(self, client: NativeDshMuxClient, *, boot, bundle):
        self._client = client
        self._boot = boot
        self._bundle = bundle
        self._streams: dict[str, _StreamRead] = {}
        self._resources: dict[str, bytes] = {}
        self._lock = threading.RLock()
        self._closed = False
        self._retiring = False
        self._cancelled_requests: set[str] = set()
        self._cancelled_streams: set[str] = set()
        self._seen_requests: set[str] = set()

    def _ensure_open(self):
        if self._closed:
            raise NativeDshMuxError("dsh_shell_closed")

    def dsh_client_boot(self):
        with self._lock:
            self._ensure_open()
        value = self._boot()
        with self._lock:
            self._ensure_open()
        return value

    def dsh_client_rpc(self, request: dict):
        identity = _identity(request["request_id"])
        if request["channel"] != "/api":
            raise NativeDshMuxError("dsh_shell_channel_invalid")
        endpoint = _endpoint(request["endpoint"])
        body = json.dumps({"type": "client-request", "rpcId": identity,
                           "method": endpoint, "payload": request["payload"]},
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
        with self._lock:
            self._ensure_open()
            if identity in self._cancelled_requests:
                raise NativeDshMuxError("dsh_shell_request_cancelled")
            if identity in self._seen_requests:
                raise NativeDshMuxError("dsh_shell_request_duplicate")
            self._seen_requests.add(identity)
        try:
            metadata, result = self._client.fetch({"method": "POST", "path": f"/api/{endpoint}",
                                                  "headers": [["content-type", "application/json"]]},
                                                 body, request_id=identity)
        except NativeDshMuxError as error:
            if error.failure is None:
                raise
            with self._lock:
                self._ensure_open()
                if identity in self._cancelled_requests:
                    raise NativeDshMuxError("dsh_shell_request_cancelled") from None
            return {"ok": False, "error": copy.deepcopy(error.failure)}
        with self._lock:
            self._ensure_open()
            if identity in self._cancelled_requests:
                raise NativeDshMuxError("dsh_shell_request_cancelled")
        if metadata["status"] != 200:
            if isinstance(result, LogicalBody):
                result.release()
            return {"ok": False, "error": {"code": "dsh_shell_http_error",
                    "message": "本地 DSH 接口未完成请求。", "details": {"status": metadata["status"]}}}
        try:
            if isinstance(result, LogicalBody):
                try:
                    with result.open() as source:
                        envelope = json.load(source)
                finally:
                    result.release()
            else:
                envelope = json.loads(result)
            if envelope["rpcId"] != identity or envelope["type"] != "server-response":
                raise ValueError
            value = envelope["result"]
            if not isinstance(value, dict) or type(value.get("ok")) is not bool:
                raise ValueError
            return value
        except (ValueError, KeyError, TypeError):
            raise NativeDshMuxError("dsh_shell_rpc_envelope_invalid") from None

    def dsh_client_request_cancel(self, request: dict):
        identity = _identity(request["request_id"])
        with self._lock:
            already_cancelled = identity in self._cancelled_requests
            self._cancelled_requests.add(identity)
            closed = self._closed
        if not already_cancelled and not closed:
            self._client.cancel_fetch(identity)
        return {"cancelled": True}

    def dsh_client_stream_open(self, request: dict):
        identity = _identity(request["stream_id"])
        if request["channel"] != "/api" or type(request["has_uplink"]) is not bool:
            raise NativeDshMuxError("dsh_shell_stream_request_invalid")
        endpoint = _endpoint(request["endpoint"])
        with self._lock:
            self._ensure_open()
            if identity in self._cancelled_streams:
                raise NativeDshMuxError("dsh_shell_stream_cancelled")
            if identity in self._streams:
                raise NativeDshMuxError("dsh_shell_stream_duplicate")
            state = _StreamRead(has_uplink=request["has_uplink"])
            self._streams[identity] = state
        try:
            self._client.open_stream(endpoint, request["payload"],
                                     has_uplink=request["has_uplink"], stream_id=identity)
            with self._lock:
                self._ensure_open()
                if state.cancelled:
                    raise NativeDshMuxError("dsh_shell_stream_cancelled")
        except Exception:
            with self._lock:
                self._streams.pop(identity, None)
            raise
        return {"stream_id": identity, "cursor": 0}

    def _stream(self, request):
        identity = _identity(request["stream_id"])
        with self._lock:
            self._ensure_open()
            state = self._streams.get(identity)
        if state is None:
            raise NativeDshMuxError("dsh_shell_stream_unknown")
        return identity, state

    def _ensure_stream(self, state):
        with self._lock:
            self._ensure_open()
            if state.cancelled:
                raise NativeDshMuxError("dsh_shell_stream_cancelled")

    def dsh_client_stream_read(self, request: dict):
        identity = _identity(request["stream_id"])
        cursor = request["cursor"]
        if type(cursor) is not int or cursor < 0:
            raise NativeDshMuxError("dsh_shell_cursor_invalid")
        try:
            identity, state = self._stream(request)
            with state.lock:
                self._ensure_stream(state)
                if cursor == state.previous_cursor:
                    return copy.deepcopy(state.previous_result)
                if cursor != state.cursor or state.ended:
                    raise NativeDshMuxError("dsh_shell_cursor_stale")
                value = self._client.stream_next(identity)
                self._ensure_stream(state)
                state.cursor += 1
                result = {"items": [] if value["done"] else [value["value"]],
                          "cursor": state.cursor, "done": value["done"]}
                if "error" in value:
                    result["error"] = value["error"]
                state.ended = value["done"]
                state.previous_cursor = cursor
                state.previous_result = result
                return copy.deepcopy(result)
        except NativeDshMuxError as error:
            with self._lock:
                retired = identity in self._cancelled_streams
                shutting_down = self._retiring
            # Only this window's recorded cancellation/retirement is normal.
            # A live unknown stream, corrupt protocol or provider error remains
            # an error rather than being hidden behind a generic close result.
            normal_terminal = error.code in {"dsh_mux_stream_cancelled", "dsh_shell_stream_cancelled",
                "dsh_shell_closed", "dsh_mux_closed", "dsh_shell_stream_unknown"}
            # Shutdown closes the pipe after durable drain. An ordinary
            # per-stream cancel is not permission to hide a broken connection.
            owner_eof = shutting_down and error.code == "dsh_mux_connection_lost"
            if retired and (normal_terminal or owner_eof):
                return {"items": [], "cursor": cursor + 1, "done": True,
                        "error": {"code": "gateway/cancelled", "message": "窗口已停止读取此流。", "details": {}}}
            raise

    def dsh_client_stream_send(self, request: dict):
        identity, state = self._stream(request)
        sequence, items, done = request["sequence"], request["items"], request["done"]
        if (type(sequence) is not int or sequence < 0 or type(items) is not list
                or type(done) is not bool):
            raise NativeDshMuxError("dsh_shell_uplink_invalid")
        with state.send_lock:
            self._ensure_stream(state)
            if not state.has_uplink or state.send_ended or state.send_failed:
                raise NativeDshMuxError("dsh_shell_uplink_closed")
            if sequence != state.send_sequence + 1:
                raise NativeDshMuxError("dsh_shell_uplink_sequence")
            try:
                for item in items:
                    self._ensure_stream(state)
                    self._client.stream_send(identity, item)
                if done:
                    self._ensure_stream(state)
                    self._client.stream_send(identity, done=True)
                self._ensure_stream(state)
            except Exception:
                # A partially delivered batch must never be submitted again.
                state.send_failed = True
                raise
            state.send_sequence = sequence
            state.send_ended = done
            return {"sequence": sequence}

    def dsh_client_stream_cancel(self, request: dict):
        identity = _identity(request["stream_id"])
        with self._lock:
            already_cancelled = identity in self._cancelled_streams
            self._cancelled_streams.add(identity)
            state = self._streams.pop(identity, None)
            if state is not None:
                state.cancelled = True
            closed = self._closed
        if not already_cancelled and not closed:
            self._client.release_stream(identity)
        return {"cancelled": True}

    def dsh_client_bundle(self, request: dict):
        with self._lock:
            self._ensure_open()
        response = self._bundle(request["url"])
        if (not isinstance(response, dict) or type(response.get("status")) is not int
                or not 100 <= response["status"] <= 599
                or (response.get("contentType") is not None
                    and type(response.get("contentType")) is not str)):
            raise NativeDshMuxError("dsh_shell_bundle_invalid")
        result = {"status": response["status"], "contentType": response["contentType"]}
        if response["status"] == 200:
            raw = response["body"]
            if not isinstance(raw, (bytes, LogicalBody)) or len(raw) > MAX_BUNDLE_BYTES:
                raise NativeDshMuxError("dsh_shell_bundle_size")
            identity = str(uuid.uuid4())
            with self._lock:
                self._ensure_open()
                self._resources[identity] = raw
            result["resource"] = {"resource_id": identity, "byte_length": len(raw),
                                  "sha256": raw.sha256 if isinstance(raw, LogicalBody)
                                  else hashlib.sha256(raw).hexdigest()}
        elif isinstance(response.get("body"), LogicalBody):
            response["body"].release()
        return result

    def dsh_client_resource_read(self, request: dict):
        identity = _identity(request["resource_id"])
        offset, length = request["offset"], request["length"]
        if (type(offset) is not int or offset < 0 or type(length) is not int
                or not 1 <= length <= RESOURCE_CHUNK_BYTES):
            raise NativeDshMuxError("dsh_shell_resource_range")
        with self._lock:
            self._ensure_open()
            data = self._resources.get(identity)
        if data is None or offset > len(data):
            raise NativeDshMuxError("dsh_shell_resource_unknown")
        chunk = data.read_range(offset, length) if isinstance(data, LogicalBody) else data[offset:offset + length]
        return {"resource_id": identity, "offset": offset,
                "body_base64": base64.b64encode(chunk).decode("ascii"),
                "eof": offset + len(chunk) == len(data)}

    def dsh_client_resource_release(self, request: dict):
        with self._lock:
            data = self._resources.pop(_identity(request["resource_id"]), None)
        if isinstance(data, LogicalBody):
            data.release()
        return {"released": data is not None}

    def retire_stream_reads(self):
        """Mark this client's readers retired before the shared owner drains."""
        with self._lock:
            self._retiring = True
            self._cancelled_streams.update(self._streams)
            for state in self._streams.values():
                state.cancelled = True

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self.retire_stream_reads()
            self._streams.clear()
            resources = list(self._resources.values())
            self._resources.clear()
        for resource in resources:
            if isinstance(resource, LogicalBody):
                resource.release()
        self._client.close()
