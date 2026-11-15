"""Real GuiApi/private-pipe/DSH Controller test with an offline SSE provider."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import uuid
from pathlib import Path

import pytest

from genshin_app.agent_chat.native_dsh_mux_process import NativeDshMuxProcess
from genshin_app.agent_chat.native_dsh_shell_host import NativeDshShellHost
from genshin_app.gui.api import GuiApi
from tests.dsh_fixture_environment import isolated_runtime_environment

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("GENSHIN_NATIVE_DSH_MUX_OS_SMOKE") != "1",
    reason="explicit real Windows GuiApi/DSH Session Controller qualification",
)


def test_gui_projection_visible_turn_and_cold_reopen_use_dsh_session(tmp_path):
    profile, mirror = tmp_path / "profile", tmp_path / "media"
    profile.mkdir()
    mirror.mkdir()
    session = f"session-{uuid.uuid4()}"
    request_id = str(uuid.uuid4())
    prompt = "此条用户消息必须和最终回复、推理一起通过原生 Session 恢复。"

    def start():
        process = NativeDshMuxProcess(node=Path(os.environ["GENSHIN_NATIVE_NODE"]),
            entry=ROOT / "agent-runtime/src/native-dsh-session-pipe-fixture.mjs", working=ROOT,
            runtime_environment=isolated_runtime_environment(profile),
            entry_args=(str(profile), str(mirror)))
        shell = NativeDshShellHost(process.client, boot=process.client.boot, bundle=process.client.bundle)
        api = GuiApi(tmp_path, dsh_shell_factory=lambda _: shell)
        return process, api

    def rpc(api, endpoint, args):
        result = api.dsh_client_rpc({"request_id": str(uuid.uuid4()), "channel": "/api",
                                    "endpoint": endpoint, "payload": {"args": args}})
        assert result["ok"], result
        return result["value"]

    def follow(api):
        identity = str(uuid.uuid4())
        api.dsh_client_stream_open({"stream_id": identity, "channel": "/api",
            "endpoint": "session/follow", "payload": {"args": {"request": {
                "address": {"kind": "session", "sessionId": session}, "assistantStream": True,
            }}}, "has_uplink": False})
        first = api.dsh_client_stream_read({"stream_id": identity, "cursor": 0})
        assert first["items"][0]["type"] == "snapshot"
        return identity, first

    process, api = start()
    try:
        boot = api.dsh_client_boot()
        graph = next(row["value"] for row in boot["injections"]
                     if row["kind"] == "global" and row["name"] == "__DSH_BOOT__")
        chat = next(entry for entry in graph["entries"]
                    if entry["id"] == "@deepseek-ai/dsh-client-ui-chat")
        asset = api.dsh_client_bundle({"url": chat["url"]})
        assert asset["status"] == 200
        resource = asset["resource"]
        loaded = bytearray()
        while len(loaded) < resource["byte_length"]:
            part = api.dsh_client_resource_read({"resource_id": resource["resource_id"],
                "offset": len(loaded), "length": 1024 * 1024})
            loaded.extend(base64.b64decode(part["body_base64"], validate=True))
        assert hashlib.sha256(loaded).hexdigest() == resource["sha256"]
        assert b"sourceMappingURL" in loaded
        assert api.dsh_client_resource_release({"resource_id": resource["resource_id"]})["released"]
        assert rpc(api, "session/list", {"_request": {}})["items"] == []
        assert rpc(api, "session/create", {"request": {"sessionId": session}})["sessionId"] == session
        stream, first = follow(api)
        accepted = rpc(api, "session/prompt", {"request": {
            "requestId": request_id, "sessionId": session, "mode": "queue",
            "content": [{"type": "text", "text": prompt}], "clientTimeZone": "Asia/Shanghai",
        }})
        assert accepted["accepted"]
        frames = []
        cursor = first["cursor"]
        while True:
            batch = api.dsh_client_stream_read({"stream_id": stream, "cursor": cursor})
            assert "error" not in batch
            frames.extend(batch["items"])
            cursor = batch["cursor"]
            if any(item.get("type") == "event" and item["event"]["type"] == "turn/end"
                   for item in batch["items"]):
                break
        events = [frame["event"] for frame in frames if frame["type"] == "event"]
        user = next(event for event in events if event["type"] == "user/message")
        assert user["data"]["source"]["rpcId"] == request_id
        assert user["data"]["content"][0]["text"] == prompt
        answer = next(event for event in events if event["type"] == "assistant/message")
        blocks = answer["data"]["message"]["content"]
        assert {block["type"] for block in blocks} >= {"text", "reasoning"}
        assert any(frame["type"] == "assistant-stream" for frame in frames)
        api.dsh_client_stream_cancel({"stream_id": stream})
        process.client._send("control", "fixture_shutdown", b"{}", correlation=str(uuid.uuid4()))
        assert process._process.wait(timeout=10) == 0
    finally:
        process.close()

    process, api = start()
    try:
        listed = rpc(api, "session/list", {"_request": {}})
        assert len(listed["items"]) == 1
        assert listed["items"][0]["sessionId"] == session
        assert listed["items"][0]["agentAvailable"] is False
        stream, first = follow(api)
        page = rpc(api, "session/page", {"request": {
            "address": {"kind": "session", "sessionId": session},
            "throughSeq": first["items"][0]["cursor"],
        }})
        history = json.dumps(page["records"], ensure_ascii=False)
        assert prompt in history
        assert "phase5b official answer" in history
        assert "phase5b offline reasoning" in history
        api.dsh_client_stream_cancel({"stream_id": stream})
        process.client._send("control", "fixture_shutdown", b"{}", correlation=str(uuid.uuid4()))
        assert process._process.wait(timeout=10) == 0
    finally:
        process.close()
