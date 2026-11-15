from __future__ import annotations

import copy

import pytest

from genshin_app.agent_chat.native_research_bridge import (
    NativeResearchBridgeError,
    NativeResearchTurnBridge,
)
from genshin_app.agent_chat.native_research_ledger import NativeResearchLedger

HASH = "a" * 64


def _pre(bridge, **kwargs):
    return bridge.pre_register(raw_arguments_hash=HASH, **kwargs)


def _ledger(tmp_path):
    counter = iter(range(1, 20))
    return NativeResearchLedger(
        tmp_path,
        conversation_id="conversation-1",
        clock=lambda: "2026-10-23T00:00:00Z",
        id_factory=lambda: f"research_{next(counter):032d}",
    )


def _bridge(tmp_path):
    return NativeResearchTurnBridge(
        ledger=_ledger(tmp_path),
        turn_id="turn-1",
        epoch_id="epoch-1",
        workflow_stage="event_topology",
    )


def _search_terminal(ack, *, call_id="call-1", arguments=None, **changes):
    value = {
        "research_id": ack["research_id"],
        "call_id": call_id,
        "tool": "web_search",
        "arguments": arguments or {"queries": ["元素附着规则"]},
        "raw_arguments_hash": HASH,
        "status": "completed",
        "content_hash": HASH,
        "presentation_meta": {
            "sources": [
                {
                    "url": "https://example.com/mechanic",
                    "title": "Mechanic",
                    "snippet": "A bounded result.",
                }
            ],
            "truncated": False,
            "answer": "A bounded answer.",
        },
        "resource_ids": [],
    }
    value.update(changes)
    return value


def test_pre_registration_is_durable_before_dispatch_and_terminal_is_bound(tmp_path):
    bridge = _bridge(tmp_path)
    arguments = {"queries": ["元素附着规则"]}

    ack = _pre(bridge,
        call_id="call-1", tool="web_search", arguments=arguments,
    )

    assert ack["status"] == "registered"
    assert bridge.public_status() == {
        "research_ledger_revision": 1,
        "registered_count": 1,
        "settled_count": 0,
        "pending_count": 1,
    }
    persisted = bridge._ledger.read()
    assert persisted["entries"][0]["status"] == "pending"
    assert persisted["entries"][0]["arguments"] == arguments

    result = bridge.settle_terminal(_search_terminal(ack))

    assert result["status"] == "completed"
    assert result["content_hash"] == HASH
    assert bridge.public_status()["pending_count"] == 0


def test_lost_registration_ack_replay_is_non_dispatchable_and_keeps_identity(tmp_path):
    bridge = _bridge(tmp_path)
    arguments = {"queries": ["元素附着规则"]}
    first = _pre(bridge,
        call_id="call-1", tool="web_search", arguments=arguments,
    )

    replay = _pre(bridge,
        call_id="call-1", tool="web_search", arguments=arguments,
    )

    assert replay["status"] == "already_registered"
    assert replay["research_id"] == first["research_id"]
    assert bridge._ledger.read()["revision"] == 1


def test_replayed_already_terminal_registration_is_not_downgraded_to_unknown(tmp_path):
    ledger = _ledger(tmp_path)
    first_bridge = NativeResearchTurnBridge(
        ledger=ledger,
        turn_id="turn-1",
        epoch_id="epoch-1",
        workflow_stage="event_topology",
    )
    arguments = {"queries": ["元素附着规则"]}
    first = _pre(first_bridge,
        call_id="call-1", tool="web_search", arguments=arguments,
    )
    first_bridge.settle_terminal(_search_terminal(first))

    recovered_bridge = NativeResearchTurnBridge(
        ledger=ledger,
        turn_id="turn-1",
        epoch_id="epoch-1",
        workflow_stage="event_topology",
    )
    replay = _pre(recovered_bridge,
        call_id="call-1", tool="web_search", arguments=arguments,
    )

    assert replay["status"] == "already_registered"
    assert recovered_bridge.public_status()["pending_count"] == 0
    assert recovered_bridge.settle_unfinished_as_unknown() == ()
    assert ledger.read()["entries"][0]["status"] == "completed"


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"call_id": "other-call"}, "research_terminal_binding_mismatch"),
        ({"tool": "web_fetch"}, "research_terminal_binding_mismatch"),
        (
            {"arguments": {"queries": ["substituted"]}},
            "research_terminal_binding_mismatch",
        ),
        ({"raw_arguments_hash": "b" * 64}, "research_terminal_binding_mismatch"),
    ],
)
def test_terminal_cannot_substitute_registration(tmp_path, change, code):
    bridge = _bridge(tmp_path)
    ack = _pre(bridge,
        call_id="call-1",
        tool="web_search",
        arguments={"queries": ["元素附着规则"]},
    )
    terminal = _search_terminal(ack)
    terminal.update(copy.deepcopy(change))

    with pytest.raises(NativeResearchBridgeError) as error:
        bridge.settle_terminal(terminal)

    assert error.value.code == code
    assert bridge._ledger.read()["entries"][0]["status"] == "pending"


def test_exact_replay_cannot_cross_epoch_boundary(tmp_path):
    ledger = _ledger(tmp_path)
    first = NativeResearchTurnBridge(
        ledger=ledger,
        turn_id="turn-1",
        epoch_id="epoch-1",
        workflow_stage="event_topology",
    )
    _pre(
        first,
        call_id="call-1",
        tool="web_search",
        arguments={"queries": ["元素附着规则"]},
    )
    replacement = NativeResearchTurnBridge(
        ledger=ledger,
        turn_id="turn-1",
        epoch_id="epoch-2",
        workflow_stage="event_topology",
    )

    with pytest.raises(NativeResearchBridgeError) as error:
        _pre(
            replacement,
            call_id="call-1",
            tool="web_search",
            arguments={"queries": ["元素附着规则"]},
        )

    assert error.value.code == "call_id_conflict"


def test_interrupted_callbacks_are_closed_as_unknown_without_touching_other_turn(tmp_path):
    ledger = _ledger(tmp_path)
    bridge = NativeResearchTurnBridge(
        ledger=ledger,
        turn_id="turn-1",
        epoch_id="epoch-1",
        workflow_stage="event_topology",
    )
    first = _pre(bridge,
        call_id="call-1",
        tool="web_fetch",
        arguments={"url": "https://example.com/one"},
    )
    ledger.register(
        expected_revision=1,
        turn_id="turn-2",
        epoch_id="epoch-2",
        workflow_stage="event_topology",
        call_id="call-2",
        tool="web_fetch",
        arguments={"url": "https://example.com/two"},
        raw_arguments_hash=HASH,
    )

    settled = bridge.settle_unfinished_as_unknown()

    assert settled == (first["research_id"],)
    rows = {row["call_id"]: row for row in ledger.read()["entries"]}
    assert rows["call-1"]["status"] == "outcome_unknown"
    assert rows["call-2"]["status"] == "pending"
    assert bridge.settle_unfinished_as_unknown() == ()


def test_failed_terminal_rejects_provider_metadata_and_does_not_persist_body(tmp_path):
    bridge = _bridge(tmp_path)
    ack = _pre(bridge,
        call_id="call-1",
        tool="web_search",
        arguments={"queries": ["元素附着规则"]},
    )
    terminal = _search_terminal(
        ack,
        status="failed",
        presentation_meta=None,
        content_hash=HASH,
    )
    terminal["provider_body"] = "must never be accepted"

    with pytest.raises(NativeResearchBridgeError) as error:
        bridge.settle_terminal(terminal)

    assert error.value.code == "research_terminal_invalid"
    assert "must never be accepted" not in str(bridge._ledger.read())
