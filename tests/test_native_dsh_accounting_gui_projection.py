"""Offline paid-work/research ledgers through the real GuiApi/DSH pipe."""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from genshin_app.agent_chat.native_dsh_host_requests import NativeDshHostRequests
from genshin_app.agent_chat.native_dsh_mux_process import NativeDshMuxProcess
from genshin_app.agent_chat.native_dsh_shell_host import NativeDshShellHost
from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry
from genshin_app.gui.api import GuiApi
from tests.dsh_fixture_environment import isolated_runtime_environment
from tests.test_queries import db_path as db_path  # noqa: PLC0414

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "nt" or os.environ.get("GENSHIN_NATIVE_DSH_MUX_OS_SMOKE") != "1",
    reason="explicit Windows private-pipe accounting qualification; no paid network")


def test_gui_dsh_web_tool_registers_and_settles_actual_host_ledgers(tmp_path, db_path):
    registry = NativeDshWorkspaceRegistry(tmp_path / "domain", database_path=db_path)
    requests = NativeDshHostRequests(registry, data_dir=tmp_path)
    profile, mirror = tmp_path / "profile", tmp_path / "media"
    profile.mkdir()
    mirror.mkdir()
    script = {"accounting": True, "calls": [{"tool": "web_search", "arguments": {"queries": ["工程角色技能周期资料"]}}]}
    process = NativeDshMuxProcess(node=Path(os.environ["GENSHIN_NATIVE_NODE"]),
        entry=ROOT / "agent-runtime/src/native-dsh-domain-pipe-fixture.mjs", working=ROOT,
        runtime_environment=isolated_runtime_environment(profile),
        entry_args=(str(profile), str(mirror), json.dumps(script, ensure_ascii=True)), domain_handler=requests)
    shell = NativeDshShellHost(process.client, boot=process.client.boot, bundle=process.client.bundle)
    api = GuiApi(tmp_path, dsh_shell_factory=lambda _: shell, dsh_workspace_registry=registry)
    def rpc(endpoint, args):
        response = api.dsh_client_rpc({"request_id": str(uuid.uuid4()), "channel": "/api",
                                      "endpoint": endpoint, "payload": {"args": args}})
        assert response["ok"], response
        return response["value"]
    stream = str(uuid.uuid4())
    try:
        rpc("session/create", {"request": {"sessionId": "research-session"}})
        api.dsh_client_stream_open({"stream_id": stream, "channel": "/api", "endpoint": "session/follow",
            "payload": {"args": {"request": {"address": {"kind": "session", "sessionId": "research-session"},
                                                "assistantStream": True}}}, "has_uplink": False})
        cursor = api.dsh_client_stream_read({"stream_id": stream, "cursor": 0})["cursor"]
        rpc("session/prompt", {"request": {"requestId": str(uuid.uuid4()), "sessionId": "research-session",
            "mode": "queue", "content": [{"type": "text", "text": "请核实技能周期并说明来源。"}]}})
        events = []
        while True:
            batch = api.dsh_client_stream_read({"stream_id": stream, "cursor": cursor})
            assert "error" not in batch
            cursor = batch["cursor"]
            events.extend(item["event"] for item in batch["items"] if item.get("type") == "event")
            if any(event["type"] == "turn/end" for event in events):
                break
        assert any(event["type"] == "assistant/message" for event in events)
        entries = requests._research_ledger("research-session").read()["entries"]
        assert len(entries) == 1 and entries[0]["status"] == "completed"
        assert entries[0]["turn_id"] == "dsh-turn-1"
        leases = requests.budgets._read()["leases"]
        assert len(leases) == 2
        assert all(row["request"]["session_id"] == "research-session" and row["settled_at"] is not None
                   and row["usage"] is not None for row in leases.values())
        assert not registry._workspaces  # Unbound research never invents a candidate.
        api.dsh_client_stream_cancel({"stream_id": stream})
        process.client._send("control", "fixture_shutdown", b"{}", correlation=str(uuid.uuid4()))
        assert process._process.wait(timeout=10) == 0
    finally:
        process.close()
        registry.close()
