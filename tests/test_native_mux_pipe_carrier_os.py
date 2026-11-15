from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from genshin_app.agent_chat.native_mux_pipe_carrier import NativeMuxPipeCarrier
from genshin_app.agent_chat.native_pipe import NativePipeServer
from genshin_app.agent_chat.native_pipe_multiplexer import (
    NativeMuxReceiver,
    NativeMuxSendQueue,
)
from genshin_app.agent_chat.runtime_integrity import audit_node_executable
from genshin_app.version import (
    NATIVE_AGENT_NODE_BINARY_SHA256,
    NATIVE_AGENT_NODE_VERSION,
)

_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = _ROOT / "agent-runtime" / "src" / "native-mux-pipe-cross-language-fixture.mjs"
_SMOKE = (
    os.name == "nt"
    and os.environ.get("GENSHIN_NATIVE_MUX_PIPE_OS_SMOKE") == "1"
)


@pytest.mark.skipif(
    not _SMOKE,
    reason="requires explicit unsandboxed Python/Node named-pipe smoke",
)
def test_python_and_node_mux_carriers_exchange_multi_chunk_frames():
    node_text = os.environ.get("GENSHIN_NATIVE_NODE", "")
    node = Path(node_text).absolute() if node_text else Path("missing-node")
    assert node.is_file(), "GENSHIN_NATIVE_NODE must name the pinned Node executable"
    node_audit = audit_node_executable(node)
    assert node_audit["node_binary_sha256"] == NATIVE_AGENT_NODE_BINARY_SHA256
    node = Path(node_audit["path"])
    version = subprocess.run(
        [str(node), "--version"],
        cwd=_ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=5.0,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert version.returncode == 0, version.stderr
    assert version.stdout.strip() == f"v{NATIVE_AGENT_NODE_VERSION}"

    with NativePipeServer(io_timeout=10.0) as server:
        process: subprocess.Popen[str] | None = None
        connection = None
        carrier = None
        try:
            process = subprocess.Popen(
                [str(node), str(_FIXTURE), server.name],
                cwd=_ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            connection = server.accept(timeout=10.0)
            carrier = NativeMuxPipeCarrier(
                connection,
                send_queue=NativeMuxSendQueue(
                    sender_role="host",
                    connection_generation=1,
                    connection_id="cross-language-connection",
                    runtime_incarnation="cross-language-runtime",
                ),
                receiver=NativeMuxReceiver(
                    sender_role="runtime",
                    connection_generation=1,
                    connection_id="cross-language-connection",
                    runtime_incarnation="cross-language-runtime",
                ),
            )
            inbound = carrier.receive_one()
            assert inbound.packet.header["message_id"] == "node-to-python"
            assert len(inbound.packet.payload) == 2 * 1024 * 1024 + 17
            expected_node = b"N" * (2 * 1024 * 1024 + 17)
            assert hashlib.sha256(inbound.packet.payload).digest() == hashlib.sha256(
                expected_node
            ).digest()
            inbound.accept()

            outbound = b"H" * (2 * 1024 * 1024 + 31)
            carrier.enqueue(
                lane="legacy_turn",
                payload_kind="legacy_frame",
                payload=outbound,
                message_id="python-to-node",
            )
            assert carrier.flush_one() is not None

            stdout, stderr = process.communicate(timeout=15.0)
            assert process.returncode == 0, stderr
            report = json.loads(stdout)
            assert report == {
                "contract": "genshin-native-mux-pipe-cross-language-fixture-v1",
                "status": "passed",
                "productionOwner": False,
                "sentFrames": 1,
                "acceptedFrames": 1,
                "outboundSha256": hashlib.sha256(expected_node).hexdigest(),
            }
        finally:
            if carrier is not None:
                carrier.close()
            elif connection is not None:
                connection.close()
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5.0)
