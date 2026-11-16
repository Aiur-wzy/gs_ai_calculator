"""Window-scoped carrier view; it never owns the shared DSH process."""
from __future__ import annotations

import threading
import uuid

from .native_dsh_mux_client import NativeDshMuxError


class NativeDshPeerClient:
    def __init__(self, client, peer_id, *, on_close=None):
        self._client = client
        self.peer_id = peer_id
        self._on_close = on_close
        self._lock = threading.RLock()
        self._closed = False
        self._requests = set()
        self._streams = set()

    def _ensure_open(self):
        if self._closed:
            raise NativeDshMuxError("dsh_shell_closed")

    def boot(self):
        with self._lock:
            self._ensure_open()
        return self._client.boot()

    def bundle(self, url):
        with self._lock:
            self._ensure_open()
        return self._client.bundle(url)

    def fetch(self, metadata, body=b"", *, request_id=None):
        identity = request_id or str(uuid.uuid4())
        with self._lock:
            self._ensure_open()
            self._requests.add(identity)
        try:
            return self._client.fetch({**metadata, "peer_id": self.peer_id}, body, request_id=identity)
        finally:
            with self._lock:
                self._requests.discard(identity)

    def cancel_fetch(self, identity):
        with self._lock:
            if identity not in self._requests:
                return
        self._client.cancel_fetch(identity)

    def open_stream(self, endpoint, payload, *, has_uplink=False, stream_id=None):
        identity = stream_id or str(uuid.uuid4())
        with self._lock:
            self._ensure_open()
            self._streams.add(identity)
            try:
                return self._client.open_stream(endpoint, payload, has_uplink=has_uplink,
                    stream_id=identity, peer_id=self.peer_id)
            except Exception:
                self._streams.discard(identity)
                raise

    def _check_stream(self, identity):
        with self._lock:
            self._ensure_open()
            if identity not in self._streams:
                raise NativeDshMuxError("dsh_shell_stream_unknown")

    def stream_next(self, identity):
        self._check_stream(identity)
        return self._client.stream_next(identity)

    def stream_send(self, identity, value=None, *, done=False):
        self._check_stream(identity)
        return self._client.stream_send(identity, value, done=done)

    def release_stream(self, identity):
        with self._lock:
            if identity not in self._streams:
                return
            self._streams.remove(identity)
        self._client.release_stream(identity)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            streams, requests = list(self._streams), list(self._requests)
            self._streams.clear()
            self._requests.clear()
        failures = []
        try:
            for identity in requests:
                try:
                    self._client.cancel_fetch(identity)
                except Exception as error:  # noqa: BLE001 - clean every resource, then report the first failure
                    failures.append(error)
            for identity in streams:
                try:
                    self._client.release_stream(identity)
                except Exception as error:  # noqa: BLE001 - clean every resource, then report the first failure
                    failures.append(error)
            try:
                self._client.control("dsh_runtime_client_close", {"peer_id": self.peer_id})
            except Exception as error:  # noqa: BLE001 - owner retirement must still run after transport failure
                failures.append(error)
        finally:
            if self._on_close is not None:
                self._on_close()
        if failures:
            raise failures[0]
