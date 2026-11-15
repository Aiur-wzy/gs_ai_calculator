"""Streaming mux adapter for the existing logical byte-stream assembler.

Only opaque local artifacts cross this seam. No wire value is a file path,
attachment receipt, Session record or domain-operation completion claim.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import uuid
from pathlib import Path

from .native_logical_stream import (
    DEFAULT_DIRECT_PAYLOAD_THRESHOLD_BYTES,
    DEFAULT_LOGICAL_STREAM_CHUNK_BYTES,
    DEFAULT_LOGICAL_STREAM_CREDIT_BYTES,
    HARD_MAX_LOGICAL_STREAM_BYTES,
    LOGICAL_STREAM_CONTRACT,
    LogicalStreamAssembler,
    LogicalStreamCreditTracker,
    NativeLogicalStreamError,
)

_KINDS = frozenset({"logical_stream.open", "logical_stream.chunk", "logical_stream.commit",
                    "logical_stream.accept", "logical_stream.credit", "logical_stream.committed",
                    "logical_stream.cancel", "logical_stream.cancelled", "logical_stream.error"})


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


class LogicalBody:
    """Verified immutable spool body; released by its final Host consumer."""

    def __init__(self, assembler, artifact, on_release=lambda _body: None):
        self._assembler = assembler
        self._artifact = artifact
        self.length = artifact.total_length
        self.sha256 = artifact.sha256
        self._released = False
        self._lock = threading.RLock()
        self._readers = set()
        self._on_release = on_release
        stat = artifact.path.stat()
        self._identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)

    def __len__(self):
        return self.length

    def open(self):
        with self._lock:
            if self._released:
                raise NativeLogicalStreamError("dsh_logical_body_released")
            stream = self._artifact.path.open("rb")
            stat = os.fstat(stream.fileno())
            if (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns) != self._identity:
                stream.close()
                raise NativeLogicalStreamError("dsh_logical_body_changed")
            self._readers = {reader for reader in self._readers if not reader.closed}
            self._readers.add(stream)
            return stream

    def read_range(self, offset, length):
        with self.open() as stream:
            stream.seek(offset)
            return stream.read(length)

    def chunks(self, chunk_bytes=DEFAULT_LOGICAL_STREAM_CHUNK_BYTES):
        with self.open() as stream:
            while chunk := stream.read(chunk_bytes):
                yield chunk

    def release(self):
        with self._lock:
            if self._released:
                return
            for stream in self._readers:
                stream.close()
            self._readers.clear()
            self._assembler.release_artifact(transfer_id=self._artifact.transfer_id)
            self._released = True
            self._on_release(self)


class NativeDshLogicalTransport:
    """Transport correlations and spool credit, never DSH business state."""

    def __init__(self, *, send, on_artifact, on_cancel=lambda _identity: None,
                 is_cancelled=lambda _identity: False, spool_root=None,
                 maximum_bytes=HARD_MAX_LOGICAL_STREAM_BYTES, aggregate_spool_bytes=None):
        self._temporary = spool_root is None
        self._root = None if spool_root is None else Path(spool_root)
        self._assembler = None
        self._aggregate = aggregate_spool_bytes
        self._maximum = maximum_bytes
        self._send = send
        self._on_artifact = on_artifact
        self._on_cancel = on_cancel
        self._is_cancelled = is_cancelled
        self._incoming = {}
        self._outgoing = {}
        self._retired = set()
        self._sequences = {}
        self._bodies = set()
        self._condition = threading.Condition(threading.RLock())
        self._receiver_lock = threading.RLock()
        self._closed = False

    def _ensure_assembler(self):
        if self._assembler is not None:
            return
        if self._root is None:
            self._root = Path(tempfile.mkdtemp(prefix="genshin-dsh-logical-"))
        self._root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(self._root).free
        quota = min(self._aggregate or self._maximum * 2, max(1, free - 512 * 1024 * 1024))
        purposes = frozenset({"dsh_fetch_result", "dsh_bundle_result"})
        self._assembler = LogicalStreamAssembler(spool_root=self._root, allowed_purposes=purposes,
            metadata_keys_by_purpose={key: frozenset({"fetch_metadata", "result_kind"}) for key in purposes},
            maximum_bytes=self._maximum, aggregate_spool_bytes=quota)

    def _emit(self, header, kind, value):
        control = kind == "logical_stream.cancel"
        identity = header["stream_id"]
        with self._condition:
            sequence = self._sequences.get(identity, 0) + 1
            if not control:
                self._sequences[identity] = sequence
        self._send("control" if control else "remote_stream", kind,
                   value if isinstance(value, bytes) else _json(value),
                   correlation=header["correlation_id"],
                   stream_id=None if control else identity,
                   sequence=None if control else sequence, logical=True)

    def accept(self, packet):
        with self._receiver_lock:
            if self._closed:
                return packet.header["payload_kind"] in _KINDS
            return self._accept(packet)

    def _accept(self, packet):
        header = packet.header
        kind = header["payload_kind"]
        if kind not in _KINDS:
            return False
        value = None if kind == "logical_stream.chunk" else json.loads(packet.payload)
        identity = value["transfer_id"] if kind == "logical_stream.cancel" else header["stream_id"]
        if not isinstance(identity, str) or not isinstance(header["correlation_id"], str):
            raise NativeLogicalStreamError("dsh_logical_identity_invalid")
        if kind in {"logical_stream.accept", "logical_stream.credit", "logical_stream.committed",
                    "logical_stream.cancelled", "logical_stream.error"}:
            with self._condition:
                state = self._outgoing.get(identity)
                if state is None:
                    if identity in self._retired:
                        return True
                    raise NativeLogicalStreamError("dsh_logical_response_unknown")
                if state["header"]["correlation_id"] != header["correlation_id"] or value["transfer_id"] != identity:
                    raise NativeLogicalStreamError("dsh_logical_correlation_invalid")
                if kind in {"logical_stream.error", "logical_stream.cancelled"}:
                    state["error"] = NativeLogicalStreamError(value.get("code", "dsh_logical_cancelled"))
                elif kind == "logical_stream.committed":
                    if value != state.get("commit"):
                        raise NativeLogicalStreamError("dsh_logical_receipt_invalid")
                    state["committed"] = True
                else:
                    state["credit"].apply_grant(transfer_id=identity,
                        accepted_through_seq=value["accepted_through_seq"], grant_bytes=value["grant_bytes"])
                self._condition.notify_all()
            return True
        if kind == "logical_stream.cancel":
            original = self._incoming.get(identity)
            if original is not None and original["correlation_id"] != header["correlation_id"]:
                raise NativeLogicalStreamError("dsh_logical_correlation_invalid")
            self._retired.add(identity)
            self._on_cancel(header["correlation_id"])
            if identity in self._incoming:
                self._assembler.cancel(transfer_id=identity)
                self._incoming.pop(identity, None)
            self._emit({**header, "stream_id": identity}, "logical_stream.cancelled",
                       {"transfer_id": identity, "reason": "cancelled"})
            return True
        if identity in self._retired:
            return True
        try:
            if kind == "logical_stream.open":
                if (identity in self._incoming or header["stream_seq"] != 1
                        or value.get("transfer_id") != identity or self._is_cancelled(header["correlation_id"])):
                    raise NativeLogicalStreamError("dsh_logical_open_rejected")
                self._incoming[identity] = header
                self._ensure_assembler()
                receipt = self._assembler.open(value)
                self._emit(header, "logical_stream.accept", receipt)
            else:
                original = self._incoming.get(identity)
                if original is None or original["correlation_id"] != header["correlation_id"]:
                    raise NativeLogicalStreamError("dsh_logical_correlation_invalid")
                if kind == "logical_stream.chunk":
                    receipt = self._assembler.append_chunk(transfer_id=identity,
                        stream_seq=header["stream_seq"], payload=packet.payload)
                    self._emit(header, "logical_stream.credit", receipt)
                else:
                    artifact = self._assembler.commit(transfer_id=identity,
                        stream_seq=header["stream_seq"], message=value)
                    body = LogicalBody(self._assembler, artifact, self._bodies.discard)
                    self._bodies.add(body)
                    self._retired.add(identity)
                    self._incoming.pop(identity)
                    self._emit(header, "logical_stream.committed", value)
                    self._on_artifact(header, artifact.purpose, json.loads(artifact.metadata_json), body)
        except Exception as error:  # noqa: BLE001 - retire failed transfer and wake only its sender
            self._retired.add(identity)
            if identity in self._incoming:
                self._incoming.pop(identity, None)
                try:
                    self._assembler.cancel(transfer_id=identity)
                except NativeLogicalStreamError:
                    pass
            self._emit(header, "logical_stream.error", {"transfer_id": identity,
                "code": error.code if isinstance(error, NativeLogicalStreamError) else "dsh_logical_transfer_failed"})
        return True

    def _wait(self, state, predicate):
        with self._condition:
            while not predicate():
                if state.get("error"):
                    raise state["error"]
                if self._closed:
                    raise NativeLogicalStreamError("dsh_logical_closed")
                self._condition.wait()
            if state.get("error"):
                raise state["error"]

    def send_source(self, *, correlation, metadata, source, declared_length=None):
        identity = str(uuid.uuid4())
        header = {"correlation_id": correlation, "stream_id": identity}
        state = {"header": header, "credit": LogicalStreamCreditTracker(transfer_id=identity,
                 maximum_credit_bytes=DEFAULT_LOGICAL_STREAM_CREDIT_BYTES), "committed": False}
        with self._condition:
            if self._closed or self._is_cancelled(correlation):
                raise NativeLogicalStreamError("dsh_logical_cancelled")
            self._outgoing[identity] = state
        try:
            self._emit(header, "logical_stream.open", {"contract": LOGICAL_STREAM_CONTRACT,
                "transfer_id": identity, "purpose": "dsh_fetch_request", "media_type": "application/octet-stream",
                "content_encoding": "identity", "maximum_bytes": self._maximum, "declared_length": declared_length,
                "declared_sha256": None, "chunk_bytes": DEFAULT_LOGICAL_STREAM_CHUNK_BYTES,
                "metadata": {"fetch_metadata": metadata, "result_kind": "dsh_fetch"}})
            self._wait(state, lambda: state["credit"].accepted_through_seq >= 1)
            sequence, total, count = 1, 0, 0
            digest = hashlib.sha256()
            buffer = bytearray()

            def flush():
                nonlocal sequence, total, count
                if not buffer:
                    return
                total += len(buffer)
                if total > self._maximum:
                    raise NativeLogicalStreamError("dsh_logical_length_exceeded")
                self._wait(state, lambda: state["credit"].available_bytes >= len(buffer))
                state["credit"].reserve(len(buffer))
                raw = bytes(buffer)
                digest.update(raw)
                self._emit(header, "logical_stream.chunk", raw)
                sequence += 1
                self._wait(state, lambda: state["credit"].accepted_through_seq >= sequence)
                count += 1
                buffer.clear()

            for part in source:
                view = memoryview(part)
                for offset in range(0, len(view), DEFAULT_LOGICAL_STREAM_CHUNK_BYTES):
                    data = view[offset:offset + DEFAULT_LOGICAL_STREAM_CHUNK_BYTES]
                    while data:
                        take = min(DEFAULT_LOGICAL_STREAM_CHUNK_BYTES - len(buffer), len(data))
                        buffer.extend(data[:take])
                        data = data[take:]
                        if len(buffer) == DEFAULT_LOGICAL_STREAM_CHUNK_BYTES:
                            flush()
            flush()
            state["commit"] = {"transfer_id": identity, "chunk_count": count, "total_length": total,
                               "sha256": digest.hexdigest(), "terminal": True}
            self._emit(header, "logical_stream.commit", state["commit"])
            self._wait(state, lambda: state["committed"])
        except Exception:
            if not self._closed:
                self._emit(header, "logical_stream.cancel", {"transfer_id": identity, "reason": "transfer_failed"})
            raise
        finally:
            with self._condition:
                self._outgoing.pop(identity, None)
                self._retired.add(identity)

    def cancel_correlation(self, correlation):
        with self._condition:
            states = [(identity, state) for identity, state in self._outgoing.items()
                      if state["header"]["correlation_id"] == correlation]
            for _identity, state in states:
                state["error"] = NativeLogicalStreamError("dsh_logical_cancelled")
            self._condition.notify_all()
        for identity, state in states:
            self._emit(state["header"], "logical_stream.cancel", {"transfer_id": identity, "reason": "cancelled"})

    def close(self):
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._condition.notify_all()
        with self._receiver_lock:
            if self._assembler is None:
                return
            self._assembler.abort_all()
            for body in list(self._bodies):
                body.release()
            self._bodies.clear()
            if self._temporary:
                # Root was created here; paths are never accepted from the peer.
                self._root.rmdir()

    def connection_lost(self):
        with self._condition:
            for state in self._outgoing.values():
                state["error"] = NativeLogicalStreamError("dsh_logical_connection_lost")
            self._condition.notify_all()

    def snapshot(self):
        return {"active": self._assembler.active_transfers if self._assembler else 0,
                "bytes": self._assembler.aggregate_spool_bytes if self._assembler else 0,
                "incoming": len(self._incoming), "outgoing": len(self._outgoing)}


DIRECT_THRESHOLD_BYTES = DEFAULT_DIRECT_PAYLOAD_THRESHOLD_BYTES
