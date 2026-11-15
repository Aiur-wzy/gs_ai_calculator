"""Independent real-socket EC05 rejection tests; no production DB or business calls."""
from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from genshin_db import service
from genshin_db.annotation_store import create_annotation_tables


@pytest.fixture
def guarded_server(tmp_path, monkeypatch):
    create_annotation_tables(tmp_path / "genshin.db")
    called = []

    def forbidden(*args, **kwargs):
        called.append(True)
        raise AssertionError("rejected request entered JSON/auth/equipment logic")

    for name in ("_read_body", "_check_token", "_equipment_get"):
        monkeypatch.setattr(service._Handler, name, forbidden)
    server = service.GenshinHTTPServer(tmp_path, port=0)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    worker.start()
    try:
        yield server, called
    finally:
        server.shutdown()
        worker.join(2)
        server.server_close()


def receive(sock):
    output = b""
    while chunk := sock.recv(8192):
        output += chunk
    head, body = output.split(b"\r\n\r\n", 1)
    headers = {line.split(b":", 1)[0].lower(): line.split(b":", 1)[1].strip()
               for line in head.split(b"\r\n")[1:]}
    assert len(body) == int(headers[b"content-length"])
    return int(head.split(b" ")[1]), json.loads(body)


@pytest.mark.parametrize("path,status", [("/v1/weapons/11401", 405), ("/v1/absent", 404)])
def test_review_delayed_non_json_segments_never_enter_business(guarded_server, path, status):
    server, called = guarded_server
    with socket.create_connection(server.server_address, timeout=2) as sock:
        sock.sendall(f"POST {path} HTTP/1.1\r\nHost: localhost\r\nContent-Length: 6\r\n\r\n".encode())
        for chunk in (b"\xff\x00", b"[{", b"zz"):
            time.sleep(.025)
            sock.sendall(chunk)
        actual, body = receive(sock)
    assert actual == body["code"] == status
    assert called == []


@pytest.mark.parametrize("length,payload", [("9" * 5000, b""), ("0" * 5000 + "1", b"x")])
def test_review_decimal_length_conversion_never_overrides_route_status(guarded_server, length, payload):
    server, called = guarded_server
    with socket.create_connection(server.server_address, timeout=2) as sock:
        sock.sendall(f"POST /v1/weapons/11401 HTTP/1.1\r\nHost: localhost\r\nContent-Length: {length}\r\n\r\n".encode() + payload)
        actual, body = receive(sock)
    assert actual == body["code"] == 405
    assert called == []


def test_review_trickle_has_one_total_deadline_and_other_requests_continue(guarded_server, monkeypatch):
    server, called = guarded_server
    monkeypatch.setattr(service, "REJECTED_BODY_DRAIN_SECONDS", .15)
    with socket.create_connection(server.server_address, timeout=2) as slow:
        slow.sendall(b"POST /v1/weapons/11401 HTTP/1.1\r\nHost: localhost\r\nContent-Length: 100\r\n\r\n")
        started = time.monotonic()
        stop = threading.Event()

        def trickle():
            while not stop.wait(.03):
                try:
                    slow.sendall(b"x")
                except OSError:
                    break

        sender = threading.Thread(target=trickle, daemon=True)
        sender.start()
        try:
            with socket.create_connection(server.server_address, timeout=2) as fast:
                fast.sendall(b"GET /v1/meta/capabilities HTTP/1.1\r\nHost: localhost\r\n\r\n")
                actual, body = receive(fast)
            assert actual == 200 and body["code"] == 0
            assert time.monotonic() - started < .5
            actual, body = receive(slow)
            assert actual == body["code"] == 405
            assert time.monotonic() - started < .65
        finally:
            stop.set()
            sender.join(1)
    assert called == []
