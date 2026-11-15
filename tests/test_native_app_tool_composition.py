from __future__ import annotations

import copy
import threading
from types import SimpleNamespace

import pytest

from genshin_app.agent_chat.bridge_protocol import raw_json_hash
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
    create_native_app_tool_runtime,
)
from genshin_app.agent_chat.native_prompt_v2 import RUNTIME_GENERATION
from genshin_app.agent_chat.native_research_ledger import NativeResearchLedger
from genshin_app.agent_chat.native_write_ledger import SessionWriteAuthority
from genshin_app.equipment.schema import PlanError
from genshin_app.whiteboard import new_whiteboard
from genshin_db import queries


@pytest.fixture
def production_scope(tmp_path, monkeypatch):
    database = tmp_path / "app.sqlite"
    database.touch()
    metadata = {
        "version_lines": ["release"],
        "datasets": {
            "equipment": {
                "lines": {
                    "release": {
                        "state": "ready",
                        "schema_version": 1,
                        "upstream_commit": "a" * 40,
                        "built_at": "2026-10-23T00:00:00Z",
                    }
                }
            }
        },
    }
    monkeypatch.setattr(queries, "get_meta", lambda _path: copy.deepcopy(metadata))
    root = tmp_path / "conversation"
    root.mkdir()
    conversation_id = "conversation-production-tools"
    provider = CandidateNativeAppBindingProviderV2()
    provider.prepare_create(
        conversation_id=conversation_id,
        workflow="build",
        document_kind="whiteboard",
        document_seed=new_whiteboard(),
        database_path=database,
        delivery_root=tmp_path / "deliveries",
    )
    binding = provider.create(
        conversation_id=conversation_id,
        session_root=root,
        workflow="build",
    )
    binding.install_writer_fence("writer-fence-a")

    action_ledger = NativeAppActionLedger(root, conversation_id=conversation_id)
    events = []

    def action_context():
        checkpoint = binding.domain_checkpoint()
        return {
            "binding": {
                "document_id": checkpoint["document_id"],
                "candidate_revision": checkpoint["candidate_revision"],
                "stage_revision": checkpoint["stage_revision"],
                "receipt_fingerprint": checkpoint["receipt_fingerprint"],
                "applied_receipt_fingerprint": None,
            },
            "workspace": {
                "document_kind": "whiteboard",
                "display_name": "生产工具测试",
                "current_view": "whiteboard",
                "dirty": False,
                "has_path": False,
                "can_save": True,
                "can_open": True,
                "can_export": True,
            },
        }

    action_controller = NativeAppActionController(
        ledger=action_ledger,
        context_reader=action_context,
        callbacks={},
        event_callback=events.append,
    )
    scope = SimpleNamespace(
        conversation_id=conversation_id,
        session_root=root,
        binding=binding,
        candidate_session=binding.candidate_session,
        research_ledger=NativeResearchLedger(root, conversation_id=conversation_id),
        action_controller=action_controller,
        app_call_ledger=NativeAppCallLedger(root, conversation_id=conversation_id),
    )
    return scope, events


def _request(host, *, call_id, tool_name, raw, snapshot=None):
    snapshot = copy.deepcopy(host.snapshot if snapshot is None else snapshot)
    return {
        "conversation_id": host.conversation_id,
        "turn_id": "turn-1",
        "step_id": "step-" + call_id,
        "epoch_id": "epoch-1",
        "call_id": call_id,
        "tool_name": tool_name,
        "arguments_json": raw,
        "raw_arguments_hash": raw_json_hash(raw),
        "document_id": snapshot["document_id"],
        "candidate_revision": snapshot["candidate_revision"],
        "stage_revision": snapshot["stage_revision"],
        "database_fingerprint": snapshot["database_fingerprint"],
        "tool_manifest_hash": snapshot["tool_manifest_hash"],
        "runtime_generation": snapshot["runtime_generation"],
        "writer_fence": snapshot["writer_fence"],
        "catalog_hash": snapshot["catalog_hash"],
    }


def test_production_scope_composes_read_research_and_exact_replay(production_scope):
    scope, _events = production_scope
    host = create_native_app_tool_host(scope)
    live = scope.binding.live_tool_binding(runtime_generation=RUNTIME_GENERATION)
    assert set(live) == {
        "document_id",
        "database_fingerprint",
        "tool_manifest_hash",
        "runtime_generation",
        "writer_fence",
    }
    assert live["writer_fence"] == "writer-fence-a"

    request = _request(
        host,
        call_id="read-1",
        tool_name="gs_read_context",
        raw='{"resource":"whiteboard_schema"}',
    )
    first = host.execute(**request)
    replay = host.execute(**request)
    assert first["terminal_state"] == "settled_success"
    assert first["result"]["code"] == 0
    assert replay["status"] == "replay"
    assert replay["result"] == first["result"]

    research = host.execute(
        **_request(
            host,
            call_id="research-1",
            tool_name="gs_list_research_calls",
            raw="{}",
        )
    )
    assert research["terminal_state"] == "settled_success"
    assert research["result"]["items"] == []


def test_domain_write_uses_authority_and_returns_advanced_catalog(production_scope):
    scope, _events = production_scope
    host = create_native_app_tool_host(scope)
    before = copy.deepcopy(host.snapshot)
    raw = (
        '{"expected_revision":0,"reason":"按用户要求修改标题",'
        '"operations":[{"op":"replace","path":"/title",'
        '"value":"已通过生产Host修改"}]}'
    )
    result = host.execute(
        **_request(
            host,
            call_id="write-1",
            tool_name="gs_patch_whiteboard",
            raw=raw,
        )
    )

    assert result["terminal_state"] == "settled_success"
    assert result["next_snapshot"]["candidate_revision"] == 1
    assert result["next_snapshot"]["catalog_hash"] != before["catalog_hash"]
    assert scope.candidate_session.candidate["title"] == "已通过生产Host修改"
    assert scope.binding.domain_checkpoint()["candidate_revision"] == 1

    with pytest.raises(PlanError) as error:
        scope.binding.read_domain_tool(
            "gs_patch_whiteboard",
            {
                "expected_revision": 1,
                "reason": "不能走只读入口",
                "operations": [],
            },
        )
    assert error.value.reason == "native_app_domain_route_invalid"


def test_fence_rotation_rejects_old_fresh_call_and_old_terminal_replay(
    production_scope,
):
    scope, _events = production_scope
    host = create_native_app_tool_host(scope)
    old_snapshot = copy.deepcopy(host.snapshot)
    old_request = _request(
        host,
        call_id="read-before-transfer",
        tool_name="gs_read_context",
        raw='{"resource":"whiteboard_schema"}',
        snapshot=old_snapshot,
    )
    assert host.execute(**old_request)["terminal_state"] == "settled_success"

    scope.binding.install_writer_fence("writer-fence-b")
    assert scope.binding.current_writer_fence() == "writer-fence-b"

    stale_request = _request(
        host,
        call_id="stale-new-call",
        tool_name="gs_read_context",
        raw='{"resource":"whiteboard_schema"}',
        snapshot=old_snapshot,
    )
    with pytest.raises(PlanError) as error:
        host.execute(**stale_request)
    assert error.value.reason == "stale_tool_snapshot"

    with pytest.raises(PlanError) as error:
        host.execute(**old_request)
    assert error.value.reason == "native_app_terminal_state_mismatch"


def test_action_route_requires_controller_agent_turn_and_updates_checkpoint(
    production_scope,
):
    scope, events = production_scope
    host = create_native_app_tool_host(scope)
    raw = '{"action":"navigate_whiteboard","reason":"返回白板继续编辑"}'

    rejected = host.execute(
        **_request(
            host,
            call_id="action-outside-turn",
            tool_name="gs_app_request_action",
            raw=raw,
        )
    )
    assert rejected["terminal_state"] == "settled_failure"
    assert rejected["result"]["reason"] == "app_action_agent_turn_required"
    assert scope.action_controller.list_cards() == []

    with scope.action_controller.agent_turn():
        accepted = host.execute(
            **_request(
                host,
                call_id="action-inside-turn",
                tool_name="gs_app_request_action",
                raw=raw,
            )
        )
    assert accepted["terminal_state"] == "settled_success"
    assert accepted["result"]["status"] == "queued_for_user"
    assert len(scope.action_controller.list_cards()) == 1
    assert scope.action_controller.checkpoint_projection()["ledger_revision"] == 1
    assert events[-1]["event"] == "card_ledger_changed"


def test_composition_rejects_scope_candidate_or_ledger_mismatch(production_scope, tmp_path):
    scope, _events = production_scope
    wrong_candidate = copy.copy(scope)
    wrong_candidate.candidate_session = object()
    with pytest.raises(PlanError):
        create_native_app_tool_host(wrong_candidate)

    wrong_ledger = copy.copy(scope)
    wrong_ledger.app_call_ledger = NativeAppCallLedger(
        tmp_path / "other",
        conversation_id="conversation-other",
    )
    with pytest.raises(PlanError):
        create_native_app_tool_host(wrong_ledger)


def test_write_authority_is_held_through_app_call_settlement(production_scope):
    scope, _events = production_scope
    host = create_native_app_tool_host(scope)
    checkpoint = scope.binding.domain_checkpoint()
    replacement = SessionWriteAuthority(
        scope.candidate_session,
        writer_fence="writer-fence-a",
        document_id=checkpoint["document_id"],
    )
    settle_entered = threading.Event()
    release_settle = threading.Event()
    transfer_finished = threading.Event()
    original_settle = scope.app_call_ledger.settle

    def delayed_settle(**kwargs):
        settle_entered.set()
        assert release_settle.wait(2)
        return original_settle(**kwargs)

    scope.app_call_ledger.settle = delayed_settle
    raw = (
        '{"expected_revision":0,"reason":"验证原子结算边界",'
        '"operations":[{"op":"replace","path":"/title","value":"边界测试"}]}'
    )
    request = _request(
        host,
        call_id="write-boundary",
        tool_name="gs_patch_whiteboard",
        raw=raw,
    )
    results = []
    writer = threading.Thread(target=lambda: results.append(host.execute(**request)))
    writer.start()
    assert settle_entered.wait(1)

    def transfer():
        replacement.transfer(
            expected_fence="writer-fence-a",
            new_fence="writer-fence-b",
        )
        transfer_finished.set()

    takeover = threading.Thread(target=transfer)
    takeover.start()
    assert not transfer_finished.wait(0.1)
    release_settle.set()
    writer.join(2)
    takeover.join(2)

    assert results[0]["terminal_state"] == "settled_success"
    assert transfer_finished.is_set()


def test_runtime_terminal_source_checkpoints_fresh_and_replayed_terminal(
    production_scope,
):
    scope, _events = production_scope
    runtime = create_native_app_tool_runtime(scope)
    request = _request(
        runtime.host,
        call_id="terminal-source",
        tool_name="gs_read_context",
        raw='{"resource":"whiteboard_schema"}',
    )
    callbacks = []

    def callback(call_id, projection):
        callbacks.append((call_id, copy.deepcopy(dict(projection))))
        return {"status": "checkpointed", "call_id": call_id}

    uninstall = runtime.terminal_source.install_terminal_callback(callback)
    fresh = runtime.host.execute(**request)
    first = runtime.terminal_source.checkpoint("terminal-source", fresh)
    replay = runtime.host.execute(**request)
    second = runtime.terminal_source.checkpoint("terminal-source", replay)

    assert first == second == {
        "status": "checkpointed",
        "call_id": "terminal-source",
    }
    assert replay["status"] == "replay"
    assert len(callbacks) == 2
    assert callbacks[0] == callbacks[1]

    tampered = {**fresh, "after_state_hash": "f" * 64}
    with pytest.raises(PlanError) as error:
        runtime.terminal_source.checkpoint("terminal-source", tampered)
    assert error.value.reason == "settlement_conflict"
    uninstall()
    with pytest.raises(PlanError) as error:
        runtime.terminal_source.checkpoint("terminal-source", fresh)
    assert error.value.reason == "native_app_terminal_callback_missing"
