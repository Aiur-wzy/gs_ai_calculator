"""Real GuiApi -> production runner -> DSH cancel -> durable usage barrier."""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest

from genshin_app.agent_chat.credential_generation import CredentialGenerationController
from genshin_app.agent_chat.native_dsh_application import NativeDshApplication
from genshin_app.agent_chat.native_dsh_host_requests import NativeDshHostRequests
from genshin_app.agent_chat.native_dsh_mux_process import NativeDshMuxProcess
from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry
from genshin_app.gui.api import GuiApi
from tests.dsh_fixture_environment import isolated_runtime_environment
from tests.test_native_dsh_application import Store

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "nt" or os.environ.get("GENSHIN_NATIVE_DSH_MUX_OS_SMOKE") != "1",
    reason="explicit offline production-runner pipe test; no true GUI or paid provider")


def test_shutdown_cancels_active_provider_and_persists_lease_before_closing_pipe(tmp_path):
    profile, mirror = tmp_path / "profile", tmp_path / "media"
    profile.mkdir()
    mirror.mkdir()
    workspaces = NativeDshWorkspaceRegistry(tmp_path / "workspaces", database_path=tmp_path / "absent.db")
    host = NativeDshHostRequests(workspaces, data_dir=tmp_path)
    process = NativeDshMuxProcess(node=Path(os.environ["GENSHIN_NATIVE_NODE"]),
        entry=ROOT / "agent-runtime/src/native-dsh-runtime-pipe-fixture.mjs", working=ROOT,
        runtime_environment=isolated_runtime_environment(profile),
        entry_args=(str(profile), str(mirror), str(ROOT / "frontend")), domain_handler=host)
    credentials = CredentialGenerationController(Store())
    credentials.save("offline-drain-sentinel", expected_generation=0)
    application = NativeDshApplication(process, credentials=credentials)
    api = GuiApi(tmp_path, dsh_shell_factory=lambda _: application)
    sid = "drain-" + str(uuid.uuid4())

    def rpc(endpoint, request):
        result = api.dsh_client_rpc({"request_id": str(uuid.uuid4()), "channel": "/api", "endpoint": endpoint,
                                    "payload": {"args": {"request": request}}})
        assert result["ok"], result
        return result["value"]

    try:
        rpc("session/create", {"sessionId": sid})
        initial = application.session_status(sid)
        assert initial["last_turn"] is None
        assert initial["agent_running"] is False
        assert initial["admission"]["state"] == "idle"
        rpc("session/prompt", {"sessionId": sid, "requestId": str(uuid.uuid4()), "mode": "queue",
            "content": [{"type": "text", "text": "phase5b-provider-idle-heartbeat-fixture"}]})
        until = time.monotonic() + 5
        while not host.budgets._read()["leases"] and time.monotonic() < until:
            time.sleep(.02)
        before = host.budgets._read()["leases"]
        assert len(before) == 1 and next(iter(before.values()))["settled_at"] is None
        active = application.session_status(sid)
        assert active["agent_running"] is True
        assert active["admission"]["state"] == "active"
        assert active["last_turn"] is None  # A live physical request is not a terminal task fact.
        started = time.monotonic()
        api._close_dsh_shell()
        assert time.monotonic() - started < 5  # The fixture otherwise waits 9.5s.
        assert application.shutdown_diagnostic() == {"state": "drained_and_stopped"}
        after = host.budgets._read()["leases"]
        assert len(after) == 1
        lease = next(iter(after.values()))
        assert lease["settled_at"] is not None
        assert lease["finish"] == "aborted"
        assert process.client.snapshot()["active_domain_requests"] == 0
    finally:
        application.close()
        workspaces.close()
