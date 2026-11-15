"""Actual GuiApi/DSH private pipe keeps ordinary chat after domain refusal."""
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
from tests.test_calculation_delivery import delivery_plan
from tests.test_queries import db_path as db_path  # noqa: PLC0414

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "nt" or os.environ.get("GENSHIN_NATIVE_DSH_MUX_OS_SMOKE") != "1",
    reason="explicit real private-pipe domain recovery qualification, no GUI window")


def test_legacy_domain_refusal_still_finishes_official_chat_on_same_session(tmp_path, db_path):
    registry = NativeDshWorkspaceRegistry(tmp_path / "domain", database_path=db_path)
    original = delivery_plan()
    registry.prepare_document("old-session", "native", original)
    path = registry._root("old-session") / "dsh-workspace.json"
    legacy = json.loads(path.read_bytes())
    legacy["contract"] = "genshin-native-dsh-workspace-binding-v1"
    del legacy["compatibility_identity"]
    path.write_text(json.dumps(legacy), encoding="utf-8")
    before = path.read_bytes()
    profile, mirror = tmp_path / "profile", tmp_path / "mirror"
    profile.mkdir()
    mirror.mkdir()
    process = NativeDshMuxProcess(node=Path(os.environ["GENSHIN_NATIVE_NODE"]),
        entry=ROOT / "agent-runtime/src/native-dsh-domain-pipe-fixture.mjs", working=ROOT,
        runtime_environment=isolated_runtime_environment(profile),
        entry_args=(str(profile), str(mirror), json.dumps({"calls": [], "repeatCalls": False})),
        domain_handler=NativeDshHostRequests(registry, data_dir=tmp_path))
    shell = NativeDshShellHost(process.client, boot=process.client.boot, bundle=process.client.bundle)
    api = GuiApi(tmp_path, database_path=db_path, dsh_workspace_registry=registry, dsh_shell_factory=lambda _: shell)

    def rpc(endpoint, request):
        value = api.dsh_client_rpc({"request_id": str(uuid.uuid4()), "channel": "/api", "endpoint": endpoint,
            "payload": {"args": {"request": request}}})
        assert value["ok"], value
        return value["value"]

    stream = str(uuid.uuid4())
    try:
        rpc("session/create", {"sessionId": "old-session"})
        refused = api.dsh_client_bind_document({"dsh_session_id": "old-session",
            "document_kind": "native", "document": original})
        assert refused["reason"] == "native_app_binding_compatibility_missing"
        api.dsh_client_stream_open({"stream_id": stream, "channel": "/api", "endpoint": "session/follow",
            "payload": {"args": {"request": {"address": {"kind": "session", "sessionId": "old-session"},
                "assistantStream": True}}}, "has_uplink": False})
        cursor = api.dsh_client_stream_read({"stream_id": stream, "cursor": 0})["cursor"]
        assert rpc("session/prompt", {"requestId": str(uuid.uuid4()), "sessionId": "old-session",
            "mode": "queue", "content": [{"type": "text", "text": "旧候选不兼容，先解释如何在新会话适配，不要计算或修改。"}]})["accepted"]
        events = []
        while True:
            batch = api.dsh_client_stream_read({"stream_id": stream, "cursor": cursor})
            assert "error" not in batch, batch
            cursor = batch["cursor"]
            events.extend(row["event"] for row in batch["items"] if row.get("type") == "event")
            end = next((event["data"] for event in events if event["type"] == "turn/end"), None)
            if end is not None:
                break
            assert not batch.get("done"), batch
        assert end["reason"]["kind"] == "completed", end
        assert any(event["type"] == "assistant/message" for event in events)
        assert not registry._workspaces  # No candidate activation/write lease/model App tool.
        assert path.read_bytes() == before
        assert not list((tmp_path / "domain").rglob("checkpoint.json"))
        assert api.dsh_client_domain_binding({"dsh_session_id": "old-session"})["reason"] == refused["reason"]
        api.dsh_client_stream_cancel({"stream_id": stream})
    finally:
        process.close()
        registry.close()
