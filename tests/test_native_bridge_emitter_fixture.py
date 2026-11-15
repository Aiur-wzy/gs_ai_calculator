from __future__ import annotations

import json
import re
from pathlib import Path

from genshin_app.agent_chat.bridge_projection import NativeBridgeEventProjector
from genshin_app.agent_chat.bridge_protocol import decode_frame

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "agent-runtime"
    / "bridge-emitter-fixture.json"
)


def test_node_emitter_fixture_decodes_and_projects_complete_tool_lifecycle():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["contract"] == "genshin-native-agent-bridge-emitter-fixture-v1"

    validated = [
        decode_frame(json.dumps(frame, ensure_ascii=False, separators=(",", ":")))
        for frame in fixture["frames"]
    ]
    projector = NativeBridgeEventProjector(
        runtime_incarnation="runtime-fixture",
        conversation_id="conversation-fixture",
        turn_id="turn-fixture",
        epoch_id="epoch-fixture",
        validate_tool_call=lambda _name, arguments: arguments,
    )
    projected = []
    for frame in validated:
        projected.extend(projector.ingest(frame))
    projector.finalize()

    tool_call = next(item for item in projected if item["method"] == "item/tool/call")
    assert re.fullmatch(r"call-[0-9a-f]{64}", tool_call["id"])
    assert tool_call["params"]["tool"] == "gs_query"
    assert tool_call["params"]["arguments"] == {"query": "角色资料"}
    assert tool_call["params"]["canonicalArgsHash"] == next(
        frame["canonical_args_hash"]
        for frame in validated
        if frame["type"] == "tool.arguments.completed"
    )

    tool_result = next(
        item for item in projected if item["method"] == "native/tool/result"
    )
    assert tool_result["params"] == {
        "threadId": "conversation-fixture",
        "turnId": "turn-fixture",
        "stepId": "step-1-1",
        "epochId": "epoch-fixture",
        "callId": tool_call["id"],
        "status": "settled_success",
        "code": 0,
        "resourceIds": [],
    }
    assert projected[-1] == {
        "method": "turn/completed",
        "params": {
            "threadId": "conversation-fixture",
            "turn": {"id": "turn-fixture", "status": "completed"},
        },
    }
