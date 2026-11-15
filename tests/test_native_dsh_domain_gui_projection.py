"""Real scoped App tools, engine and durable ledger over the GuiApi pipe path."""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from genshin_app.agent_chat.native_app_action_controller import (
    NativeAppActionController,
)
from genshin_app.agent_chat.native_app_action_ledger import NativeAppActionLedger
from genshin_app.agent_chat.native_app_binding_provider import (
    CandidateNativeAppBindingProviderV2,
)
from genshin_app.agent_chat.native_app_call_ledger import NativeAppCallLedger
from genshin_app.agent_chat.native_app_tool_composition import (
    create_native_app_tool_host,
)
from genshin_app.agent_chat.native_dsh_domain_calls import (
    DshDomainTarget,
    NativeDshDomainCalls,
)
from genshin_app.agent_chat.native_dsh_mux_process import NativeDshMuxProcess
from genshin_app.agent_chat.native_dsh_shell_host import NativeDshShellHost
from genshin_app.agent_chat.native_research_ledger import NativeResearchLedger
from genshin_app.equipment.delivery import validate_delivery_calculated
from genshin_app.gui.api import GuiApi
from tests.dsh_fixture_environment import isolated_runtime_environment
from tests.test_calculation_delivery import delivery_plan
from tests.test_queries import db_path as db_path  # noqa: PLC0414

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("GENSHIN_NATIVE_DSH_MUX_OS_SMOKE") != "1",
    reason="explicit real Windows GuiApi/DSH/App-domain qualification",
)


def test_gui_dsh_tool_loop_edits_real_candidate_and_publishes_calculated_file(tmp_path, db_path):
    session_id = "dsh-domain-one"
    conversation = "conversation-domain-one"
    domain_root = tmp_path / conversation
    domain_root.mkdir()
    original = delivery_plan()
    provider = CandidateNativeAppBindingProviderV2()
    provider.prepare_create(conversation_id=conversation, workflow="calculate", document_kind="native",
        document_seed=original, database_path=db_path, delivery_root=tmp_path / "deliveries")
    binding = provider.create(conversation_id=conversation, session_root=domain_root, workflow="calculate")
    binding.install_writer_fence("dsh-domain-writer")

    def workspace():
        checkpoint = binding.domain_checkpoint()
        return {"binding": {**{key: checkpoint[key] for key in (
            "document_id", "candidate_revision", "stage_revision", "receipt_fingerprint")},
            "applied_receipt_fingerprint": None}, "workspace": {
            "document_kind": "native", "display_name": "领域工具真实管道夹具", "current_view": "advanced",
            "dirty": True, "has_path": False, "can_save": True, "can_open": True,
            "can_export": binding.candidate_session.calculated is not None,
        }}

    action = NativeAppActionController(ledger=NativeAppActionLedger(domain_root, conversation_id=conversation),
        context_reader=workspace, callbacks={}, event_callback=lambda _: None)
    scope = SimpleNamespace(conversation_id=conversation, binding=binding,
        candidate_session=binding.candidate_session,
        research_ledger=NativeResearchLedger(domain_root, conversation_id=conversation),
        action_controller=action, app_call_ledger=NativeAppCallLedger(domain_root, conversation_id=conversation))
    tool_host = create_native_app_tool_host(scope)
    target = DshDomainTarget(session_id, conversation, tool_host, execution_boundary=action.agent_call)
    domain = NativeDshDomainCalls(lambda sid: target if sid == session_id else None)
    profile, mirror = tmp_path / "profile", tmp_path / "media"
    profile.mkdir()
    mirror.mkdir()
    script = {"calls": [
        {"tool": "gs_app_get_workspace", "arguments": {}},
        {"tool": "gs_patch_candidate", "arguments": {"expected_revision": 0,
            "reason": "真实DSH领域工具链定向验收", "operations": [{"op": "replace",
            "path": "/manual_template/meta/name", "value": "DSH public scoped candidate"}]}},
        {"tool": "gs_calculate", "arguments": {"expected_revision": 1}},
        {"tool": "gs_review_result", "arguments": {"expected_revision": 1, "request": {"view": "summary"}}},
    ]}
    process = NativeDshMuxProcess(node=Path(os.environ["GENSHIN_NATIVE_NODE"]),
        entry=ROOT / "agent-runtime/src/native-dsh-domain-pipe-fixture.mjs", working=ROOT,
        runtime_environment=isolated_runtime_environment(profile),
        entry_args=(str(profile), str(mirror), json.dumps(script, ensure_ascii=True)), domain_handler=domain)
    shell = NativeDshShellHost(process.client, boot=process.client.boot, bundle=process.client.bundle)
    api = GuiApi(tmp_path, dsh_shell_factory=lambda _: shell)

    def rpc(endpoint, args):
        result = api.dsh_client_rpc({"request_id": str(uuid.uuid4()), "channel": "/api", "endpoint": endpoint,
                                    "payload": {"args": args}})
        assert result["ok"], result
        return result["value"]

    try:
        rpc("session/create", {"request": {"sessionId": session_id}})
        follow = str(uuid.uuid4())
        api.dsh_client_stream_open({"stream_id": follow, "channel": "/api", "endpoint": "session/follow",
            "payload": {"args": {"request": {"address": {"kind": "session", "sessionId": session_id},
                                                "assistantStream": True}}}, "has_uplink": False})
        cursor = api.dsh_client_stream_read({"stream_id": follow, "cursor": 0})["cursor"]
        rpc("session/prompt", {"request": {"requestId": str(uuid.uuid4()), "sessionId": session_id,
            "mode": "queue", "content": [{"type": "text", "text": "请修改名称、计算并检查真实候选。"}]}})
        events = []
        while True:
            batch = api.dsh_client_stream_read({"stream_id": follow, "cursor": cursor})
            assert "error" not in batch
            cursor = batch["cursor"]
            events.extend(item["event"] for item in batch["items"] if item.get("type") == "event")
            if any(event["type"] == "turn/end" for event in events):
                break
        assert any(event["type"] == "assistant/message" for event in events)
        rows = list(scope.app_call_ledger.read()["entries"].values())
        assert len(rows) == 4
        assert all(row["state"] == "settled_success" for row in rows), rows
        assert all(row["identity"]["turn_id"] == "dsh-turn-1" for row in rows)
        assert binding.candidate_session.revision == 1
        assert binding.candidate_session.candidate["manual_template"]["meta"]["name"] == "DSH public scoped candidate"
        assert original["manual_template"]["meta"]["name"] != "DSH public scoped candidate"
        calculated = binding.candidate_session.calculated
        assert calculated is not None
        assert calculated["result"]["summary"]["total_damage"] == pytest.approx(45)
        checkpoint = json.loads((domain_root / "checkpoint.json").read_text(encoding="utf-8"))
        result_file = domain_root / checkpoint["calculated_file"]
        assert result_file.is_file()
        package = json.loads(Path(result_file).read_text(encoding="utf-8"))
        validate_delivery_calculated(package)
        api.dsh_client_stream_cancel({"stream_id": follow})
        process.client._send("control", "fixture_shutdown", b"{}", correlation=str(uuid.uuid4()))
        assert process._process.wait(timeout=10) == 0
    finally:
        process.close()
        binding.close()
