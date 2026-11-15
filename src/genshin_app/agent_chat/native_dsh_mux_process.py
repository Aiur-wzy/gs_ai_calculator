"""Process carrier for the production DSH shell and isolated qualification.

The caller owns release-manifest admission. This carrier owns only the pinned
Node child and authenticated private pipe; DSH owns Session and Turn state.
"""
from __future__ import annotations

import hmac
import json
import secrets
import subprocess
import threading
import time
import uuid
from pathlib import Path

from genshin_app.version import NATIVE_AGENT_NODE_VERSION

from .native_dsh_mux_client import NativeDshMuxClient, NativeDshMuxError
from .native_dsh_process_diagnostics import NativeDshProcessDiagnostics
from .native_mux_launch import encode_mux_launch_contract
from .native_mux_pipe_carrier import NativeMuxPipeCarrier
from .native_pipe import NativePipeServer, connect_timeout_milliseconds
from .native_pipe_multiplexer import NativeMuxReceiver, NativeMuxSendQueue
from .native_runtime_command import native_runtime_command
from .native_runtime_environment import build_native_runtime_environment
from .runtime_integrity import audit_node_executable

PRODUCTION_OWNER = False


class NativeDshMuxProcess:
    """Authenticated transport, with no Agent lifecycle or model request.

    ``connect_timeout_seconds`` is a grace for each startup phase: pipe accept
    and then the authenticated hello.  Their existing separate deadlines are
    preserved.  ``io_timeout_seconds`` independently bounds ordinary physical
    reads/writes; it is never replaced by the cold-start grace.
    """

    def __init__(self, *, node: Path, entry: Path, working: Path,
                 connect_timeout_seconds: float = 15.0, entry_args: tuple[str, ...] = (),
                 domain_handler=None, runtime_environment=None, diagnostics_root=None,
                 io_timeout_seconds: float = 15.0):
        if type(entry_args) is not tuple or any(type(value) is not str for value in entry_args):
            raise NativeDshMuxError("dsh_mux_entry_arguments_invalid")
        # Startup may wait for a large frozen dependency tree.  This does not
        # grant each later physical read/write the same multi-hour budget.
        connect_timeout_seconds = connect_timeout_milliseconds(connect_timeout_seconds) / 1000
        node = Path(audit_node_executable(node)["path"])
        self._process = None
        self._server = None
        self._carrier = None
        self.client = None
        self.diagnostics = NativeDshProcessDiagnostics(diagnostics_root)
        self._diagnostics_thread = None
        self._connecting = True
        nonce = secrets.token_urlsafe(48)
        identity = {"connection_generation": 1, "connection_id": str(uuid.uuid4()),
                    "runtime_incarnation": str(uuid.uuid4())}
        try:
            self._server = NativePipeServer(io_timeout=io_timeout_seconds)
            self._process = subprocess.Popen(
                list(native_runtime_command(node, entry, entry_args)), cwd=working,
                env=build_native_runtime_environment(runtime_environment), stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self._diagnostics_thread = threading.Thread(target=self.diagnostics.drain,
                args=(self._process.stderr, self._process), kwargs={"on_exit": self._startup_process_exited},
                name="dsh-process-diagnostics", daemon=True)
            self._diagnostics_thread.start()
            packet = encode_mux_launch_contract(pipe_name=self._server.name, nonce=nonce, **identity)
            stream = self._process.stdin
            try:
                written = 0
                while written < len(packet):
                    count = stream.write(packet[written:])
                    if not count:
                        raise NativeDshMuxError("dsh_mux_bootstrap_write")
                    written += count
                stream.flush()
            finally:
                stream.close()
            connection = self._server.accept(timeout=connect_timeout_seconds)
            self._carrier = NativeMuxPipeCarrier(
                connection, send_queue=NativeMuxSendQueue(sender_role="host", **identity),
                receiver=NativeMuxReceiver(sender_role="runtime", **identity),
            )
            deadline = time.monotonic() + connect_timeout_seconds
            delivery = self._carrier.receive_one(idle_deadline_provider=lambda: deadline)
            try:
                hello = json.loads(delivery.packet.payload)
                if (delivery.packet.header["lane"] != "control"
                        or delivery.packet.header["payload_kind"] != "dsh_mux_hello"
                        or type(hello) is not dict
                        or set(hello) != {"contract", "nonce", "nodeVersion"}
                        or hello["contract"] != "genshin-native-dsh-mux-hello-v1"
                        or type(hello["nonce"]) is not str
                        or not hmac.compare_digest(hello["nonce"], nonce)
                        or hello["nodeVersion"] != NATIVE_AGENT_NODE_VERSION):
                    raise NativeDshMuxError("dsh_mux_hello_invalid")
                delivery.accept()
            except Exception:
                if not delivery.settled:
                    delivery.outcome_unknown()
                raise
            self.client = NativeDshMuxClient(self._carrier, domain_handler=domain_handler)
            self._connecting = False
        except Exception:
            self.close()
            raise

    def _startup_process_exited(self, _code):
        # A long cold-start allowance is not a reason to wait for an already
        # dead child. After admission, mux/DSH own normal disconnection.
        if self._connecting and self._server is not None:
            self._server.close()

    def close(self):
        if self.client is not None:
            self.client.close()
        elif self._carrier is not None:
            self._carrier.close()
        if self._server is not None:
            self._server.close()
        if self._process is not None:
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
        if self._diagnostics_thread is not None:
            self._diagnostics_thread.join(timeout=2)
