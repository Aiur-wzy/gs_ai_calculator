from __future__ import annotations

import copy

import pytest

from genshin_app.agent_chat.native_research_ledger import NativeResearchLedger
from genshin_app.agent_chat.native_research_tools import (
    LIST_TOOL,
    REPORT_TOOL,
    call,
    definitions,
)
from genshin_app.equipment.schema import PlanError


def store(tmp_path):
    return NativeResearchLedger(
        tmp_path,
        conversation_id="conversation-1",
        clock=lambda: "2026-10-23T02:00:00Z",
        id_factory=lambda: "research_00000000000000000000000000000001",
    )


def usage(research_id):
    return {
        "research_id": research_id,
        "disposition": "adopted",
        "claim_summary": "用于核查",
        "applied_to": ["finding-1"],
        "system_gap_class": "verification_only",
        "reason": "交叉核查",
    }


def test_definitions_are_fresh_and_expose_only_two_bookkeeping_tools():
    first = definitions()
    second = definitions()
    assert [row["name"] for row in first] == [LIST_TOOL, REPORT_TOOL]
    assert all(row["type"] == "function" for row in first)
    report_properties = first[1]["inputSchema"]["properties"]["entries"]["items"][
        "properties"
    ]
    assert report_properties["claim_summary"]["anyOf"][-1] == {"type": "null"}
    applied_to = report_properties["applied_to"]
    assert "ASCII ID" in applied_to["items"]["description"]
    assert "空数组" in applied_to["description"]
    first[0]["inputSchema"]["properties"]["limit"]["maximum"] = 999
    assert second[0]["inputSchema"]["properties"]["limit"]["maximum"] == 100
    assert all("path" not in str(row).lower() for row in second)


def test_list_and_report_take_turn_identity_from_host(tmp_path):
    ledger = store(tmp_path)
    registered = ledger.register(
        expected_revision=0,
        turn_id="turn-1",
        epoch_id="epoch-1",
        workflow_stage="event_topology",
        call_id="call-1",
        tool="web_search",
        arguments={"queries": ["机制查询"]},
        raw_arguments_hash="f" * 64,
    )
    ledger.settle(
        research_id=registered["research_id"],
        status="completed",
        content_hash="a" * 64,
        presentation_meta={"sources": [], "truncated": False},
    )
    listed = call(LIST_TOOL, {}, ledger=ledger, turn_id="turn-1")
    assert listed["items"][0]["research_id"] == registered["research_id"]
    report = call(
        REPORT_TOOL,
        {
            "contract": "research-usage-report-v1",
            "research_ledger_revision": 1,
            "entries": [usage(registered["research_id"])],
            "complete_for_turn": True,
        },
        ledger=ledger,
        turn_id="turn-1",
    )
    assert report["status"] == "complete"


@pytest.mark.parametrize(
    "name,args",
    [
        ("unknown", {}),
        (LIST_TOOL, {"turn_id": "attacker-turn"}),
        (REPORT_TOOL, {"contract": "wrong"}),
    ],
)
def test_unknown_extra_or_incomplete_input_fails_closed(name, args, tmp_path):
    ledger = store(tmp_path)
    with pytest.raises(PlanError) as caught:
        call(name, copy.deepcopy(args), ledger=ledger, turn_id="turn-1")
    assert caught.value.reason in {"agent_tool_input", "agent_tool_rejected"}
