"""Real socket regression for route errors with a separately sent request body."""
from __future__ import annotations

import io
import json
import socket
import sqlite3
import threading
import time
from email.message import Message
from types import SimpleNamespace

import pytest

from genshin_db import service


@pytest.fixture
def server(tmp_path):
    connection = sqlite3.connect(tmp_path / "genshin.db")
    connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    connection.commit()
    connection.close()
    srv = service.GenshinHTTPServer(tmp_path, port=0)
    worker = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    worker.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        worker.join(3)
        srv.server_close()


def response(sock):
    chunks = []
    while block := sock.recv(8192):
        chunks.append(block)
    head, body = b"".join(chunks).split(b"\r\n\r\n", 1)
    size = next(int(line.split(b":", 1)[1]) for line in head.split(b"\r\n") if line.lower().startswith(b"content-length:"))
    assert size == len(body)
    return head, json.loads(body)


@pytest.mark.parametrize("path,status", [("/v1/weapons/11401", 405), ("/v1/no-such-route", 404)])
@pytest.mark.parametrize("method,body", [("POST", b"{}"), ("PROPFIND", b"not JSON")])
def test_delayed_body_preserves_complete_route_error(server, path, status, method, body):
    # The old server replied/closed after headers; delayed payload then caused
    # Windows to discard the unread response (Win10053). This must not retry.
    for _ in range(3):
        with socket.create_connection(server.server_address, timeout=2) as sock:
            sock.sendall(f"{method} {path} HTTP/1.1\r\nHost: localhost\r\nContent-Length: {len(body)}\r\n\r\n".encode())
            time.sleep(.02)
            sock.sendall(body)
            head, payload = response(sock)
        assert head.startswith(f"HTTP/1.1 {status}".encode())
        assert payload["code"] == status
        assert b"Allow: GET" in head if status == 405 else b"Allow:" not in head


def test_missing_rejected_body_has_a_bounded_wait(server, monkeypatch):
    monkeypatch.setattr(service, "REJECTED_BODY_DRAIN_SECONDS", .05)
    started = time.monotonic()
    with socket.create_connection(server.server_address, timeout=2) as sock:
        sock.sendall(b"POST /v1/weapons/11401 HTTP/1.1\r\nHost: localhost\r\nContent-Length: 2\r\n\r\n")
        head, payload = response(sock)
    assert time.monotonic() - started < 2
    assert head.startswith(b"HTTP/1.1 405") and payload["code"] == 405


@pytest.mark.parametrize("values,encoding", [([], None), (["0"], None), (["-1"], None),
    ([str(service.MAX_BODY_BYTES + 1)], None), (["9"*5000], None), (["2", "2"], None),
    (["2"], "chunked"), (["2"], ""), (["²"], None)])
def test_invalid_unbounded_or_ambiguous_framing_is_not_consumed(values, encoding):
    headers = Message()
    for value in values:
        headers.add_header("Content-Length", value)
    if encoding is not None:
        headers.add_header("Transfer-Encoding", encoding)
    class Unreadable:
        def read1(self, _):
            raise AssertionError("must not drain ambiguous or unbounded data")
    handler = SimpleNamespace(headers=headers, rfile=Unreadable())
    service._Handler._discard_rejected_body(handler)


def test_discard_reads_only_framed_bytes_and_restores_socket_timeout():
    headers = Message()
    headers["Content-Length"] = "2"
    timeouts = []
    handler = SimpleNamespace(headers=headers, rfile=io.BytesIO(b"{}extra"),
        connection=SimpleNamespace(gettimeout=lambda: 7, settimeout=timeouts.append))
    service._Handler._discard_rejected_body(handler)
    assert handler.rfile.read() == b"extra"
    assert timeouts[-1] == 7 and 0 < timeouts[0] <= 1
