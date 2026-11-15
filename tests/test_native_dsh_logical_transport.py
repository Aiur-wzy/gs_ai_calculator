"""Real DSH public streaming Fetch through inherited private mux, no network."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from genshin_app.agent_chat.native_dsh_logical_transport import LogicalBody
from genshin_app.agent_chat.native_dsh_mux_client import NativeDshMuxError
from genshin_app.agent_chat.native_dsh_mux_process import NativeDshMuxProcess
from genshin_app.agent_chat.native_dsh_shell_host import NativeDshShellHost
from genshin_app.agent_chat.native_logical_stream import (
    HARD_MAX_LOGICAL_STREAM_BYTES,
    LOGICAL_STREAM_CONTRACT,
    LogicalStreamAssembler,
)
from tests.dsh_fixture_environment import isolated_runtime_environment

ROOT = Path(__file__).resolve().parents[1]
OS = pytest.mark.skipif(os.name != "nt" or os.environ.get("GENSHIN_NATIVE_DSH_MUX_OS_SMOKE") != "1",
                        reason="explicit private Windows pipe stress, no real GUI or provider")


@pytest.fixture
def process(tmp_path):
    spool = tmp_path / "runtime-spool"
    spool.mkdir()
    value = NativeDshMuxProcess(node=Path(os.environ["GENSHIN_NATIVE_NODE"]),
        entry=ROOT / "agent-runtime/src/native-dsh-logical-pipe-fixture.mjs", working=ROOT,
        entry_args=(str(spool),))
    yield value, spool
    value.close()


def metadata(path, method="GET"):
    return {"method": method, "path": path, "headers": []}


def test_explicit_four_gib_ceiling_is_streaming_declaration_not_allocation(tmp_path):
    receiver = LogicalStreamAssembler(spool_root=tmp_path,
        allowed_purposes=frozenset({"dsh_fetch_request"}),
        maximum_bytes=HARD_MAX_LOGICAL_STREAM_BYTES,
        aggregate_spool_bytes=HARD_MAX_LOGICAL_STREAM_BYTES * 2)
    receipt = receiver.open({"contract": LOGICAL_STREAM_CONTRACT, "transfer_id": "four-gib-declaration",
        "purpose": "dsh_fetch_request", "media_type": "application/octet-stream", "content_encoding": "identity",
        "maximum_bytes": HARD_MAX_LOGICAL_STREAM_BYTES, "declared_length": HARD_MAX_LOGICAL_STREAM_BYTES,
        "declared_sha256": None, "chunk_bytes": 8 * 1024 * 1024, "metadata": {}})
    assert receipt["grant_bytes"] == 16 * 1024 * 1024
    assert receiver.aggregate_spool_bytes == 0
    assert sum(path.stat().st_size for path in tmp_path.iterdir()) == 0
    receiver.cancel(transfer_id="four-gib-declaration")
    assert list(tmp_path.iterdir()) == []


@OS
def test_public_fetch_streams_beyond_physical_frame_and_releases_spools(process):
    runtime, node_spool = process
    size = 132 * 1024 * 1024 + 19
    block = b"O" * (1024 * 1024)

    def source():
        remaining = size
        while remaining:
            length = min(len(block), remaining)
            yield memoryview(block)[:length]
            remaining -= length

    expected = hashlib.sha256()
    for chunk in source():
        expected.update(chunk)
    response, body = runtime.client.fetch_stream(metadata("/api/probe/blob", "POST"), source(), length=size)
    assert response["status"] == 200
    assert isinstance(body, LogicalBody)
    assert body.length == size
    assert body.sha256 == expected.hexdigest()
    digest = hashlib.sha256()
    for chunk in body.chunks():
        assert len(chunk) <= 8 * 1024 * 1024
        digest.update(chunk)
    assert digest.hexdigest() == body.sha256
    body.release()
    _, counters = runtime.client.fetch(metadata("/api/probe/metrics"))
    assert json.loads(counters)["uploadBytes"] == size
    assert runtime.client.snapshot()["logical"]["bytes"] == 0
    until = time.monotonic() + 2
    while list(node_spool.iterdir()) and time.monotonic() < until:
        time.sleep(.01)
    assert list(node_spool.iterdir()) == []


@OS
def test_bundle_keeps_spool_until_gui_resource_release(process, tmp_path):
    runtime, _ = process
    shell = NativeDshShellHost(runtime.client, boot=runtime.client.boot, bundle=runtime.client.bundle)
    size = 2 * 1024 * 1024 + 31
    result = shell.dsh_client_bundle({"url": f"/bundle?bytes={size}"})
    resource = result["resource"]
    assert resource["byte_length"] == size
    assert runtime.client.snapshot()["logical"]["bytes"] == size
    digest = hashlib.sha256()
    for offset in range(0, size, 1024 * 1024):
        part = shell.dsh_client_resource_read({"resource_id": resource["resource_id"],
            "offset": offset, "length": 1024 * 1024})
        digest.update(base64.b64decode(part["body_base64"], validate=True))
    assert digest.hexdigest() == resource["sha256"]
    assert shell.dsh_client_resource_release({"resource_id": resource["resource_id"]})["released"]
    assert runtime.client.snapshot()["logical"]["bytes"] == 0
    shell.close()


@OS
def test_cancel_large_downlink_does_not_poison_next_public_request(process):
    runtime, _ = process
    identity = str(uuid.uuid4())
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(runtime.client.fetch,
            metadata("/api/probe/generated?bytes=20000000&slow=1"), request_id=identity)
        until = time.monotonic() + 5
        while runtime.client.snapshot()["logical"]["bytes"] == 0 and time.monotonic() < until:
            time.sleep(.02)
        runtime.client.cancel_fetch(identity)
        with pytest.raises(NativeDshMuxError, match="cancelled"):
            pending.result(timeout=5)
    _, counters = runtime.client.fetch(metadata("/api/probe/metrics"))
    assert json.loads(counters)["requests"] >= 1
    until = time.monotonic() + 5
    while runtime.client.snapshot()["logical"]["bytes"] and time.monotonic() < until:
        time.sleep(.02)
    assert runtime.client.snapshot()["logical"]["bytes"] == 0


@OS
def test_wrong_commit_sha_never_dispatches_and_same_connection_recovers(process):
    runtime, node_spool = process
    original = runtime.client._carrier.enqueue
    corrupted = False

    def tamper(**packet):
        nonlocal corrupted
        if packet["payload_kind"] == "logical_stream.commit" and not corrupted:
            value = json.loads(packet["payload"])
            value["sha256"] = "0" * 64
            packet["payload"] = json.dumps(value).encode()
            corrupted = True
        return original(**packet)

    runtime.client._carrier.enqueue = tamper
    with pytest.raises(NativeDshMuxError, match="sha256_mismatch"):
        runtime.client.fetch_stream(metadata("/api/probe/blob", "POST"), [b"wrong-hash"], length=10)
    runtime.client._carrier.enqueue = original
    _, counters = runtime.client.fetch(metadata("/api/probe/metrics"))
    assert json.loads(counters)["requests"] == 0
    assert list(node_spool.iterdir()) == []


@OS
def test_verified_body_with_invalid_route_returns_error_without_hanging(process):
    runtime, _ = process
    # Public DSH failure mapping intentionally hides internal carrier errors.
    with pytest.raises(NativeDshMuxError, match="gateway/internal"):
        runtime.client.fetch_stream(metadata("/outside-app", "POST"), [b"payload"], length=7)
    response, counters = runtime.client.fetch(metadata("/api/probe/metrics"))
    assert response["status"] == 200
    assert json.loads(counters)["requests"] == 0


@OS
def test_disconnect_wakes_large_transfer_and_releases_owned_spool(process):
    runtime, _ = process
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(runtime.client.fetch,
            metadata("/api/probe/generated?bytes=20000000&slow=1"))
        until = time.monotonic() + 5
        while runtime.client.snapshot()["logical"]["bytes"] == 0 and time.monotonic() < until:
            time.sleep(.02)
        assert runtime.client.snapshot()["logical"]["bytes"] > 0
        runtime.client.close()
        with pytest.raises(NativeDshMuxError):
            pending.result(timeout=5)
    assert runtime.client.snapshot()["logical"]["bytes"] == 0


@OS
def test_public_binary_upload_returns_actual_dsh_durable_attachment_receipt(tmp_path):
    profile, mirror = tmp_path / "profile", tmp_path / "media"
    profile.mkdir()
    mirror.mkdir()
    runtime = NativeDshMuxProcess(node=Path(os.environ["GENSHIN_NATIVE_NODE"]),
        entry=ROOT / "agent-runtime/src/native-dsh-session-pipe-fixture.mjs", working=ROOT,
        runtime_environment=isolated_runtime_environment(profile),
        entry_args=(str(profile), str(mirror)))
    try:
        session = "upload-" + str(uuid.uuid4())
        request = {"type": "client-request", "rpcId": str(uuid.uuid4()), "method": "session/create",
                   "payload": {"args": {"request": {"sessionId": session}}}}
        _, created = runtime.client.fetch({**metadata("/api/session/create", "POST"),
            "headers": [["content-type", "application/json"]]}, json.dumps(request).encode())
        assert json.loads(created)["result"]["ok"]
        data = b"A" * (2 * 1024 * 1024 + 17)
        _, receipt = runtime.client.fetch_stream({**metadata(
            f"/api/session/uploadFileBinary?sessionId={session}&name=source.txt", "POST"),
            "headers": [["content-type", "application/octet-stream"]]}, [data], length=len(data))
        value = json.loads(receipt)
        assert value["ok"], value
        assert str(uuid.UUID(value["value"]["receiptId"])) == value["value"]["receiptId"]
        file = value["value"]["file"]
        assert file["attachmentId"] == "sha256:" + hashlib.sha256(data).hexdigest()
        assert file["bytes"] == len(data)
        # A transport commit alone was not sufficient: the public DSH upload
        # has now returned its own receipt and an independently stored object.
        stored = [path for path in profile.rglob("*") if path.is_file() and path.stat().st_size == len(data)]
        assert stored
        assert all(hashlib.sha256(path.read_bytes()).hexdigest() == file["attachmentId"][7:] for path in stored)
        assert list((profile / "logical-spool").iterdir()) == []
    finally:
        runtime.close()
