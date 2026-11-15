from __future__ import annotations

import copy
import json
import multiprocessing
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from genshin_app.agent_chat.native_app_action_controller import (
    CHECKPOINT_FILE,
    NativeAppActionController,
    NativeAppActionControllerError,
)
from genshin_app.agent_chat.native_app_action_ledger import (
    NativeAppActionError,
    NativeAppActionLedger,
)
from genshin_app.agent_chat.native_app_action_tools import REQUEST_ACTION_TOOL

HASH = "a" * 64


def _without_validated_opaque_operation_ids(value):
    """Keep every content field; exclude only three strictly typed identities."""
    import re
    import uuid

    if isinstance(value, dict):
        projected = {}
        for key, item in value.items():
            if key == "action_id":
                assert isinstance(item, str) and re.fullmatch(r"action_[A-Za-z0-9_-]{32}", item)
                projected[key] = "<operation-identifier>"
            elif key == "ui_receipt" and item is not None:
                assert isinstance(item, str) and re.fullmatch(r"ui_[A-Za-z0-9_-]{43}", item)
                projected[key] = "<ui-identifier>"
            elif key == "diagnostic_id" and item is not None:
                assert isinstance(item, str) and str(uuid.UUID(item)) == item
                projected[key] = "<diagnostic-identifier>"
            else:
                projected[key] = _without_validated_opaque_operation_ids(item)
        return projected
    if isinstance(value, list):
        return [_without_validated_opaque_operation_ids(item) for item in value]
    return value


def _binding(**changes):
    value = {
        "document_id": "document-1",
        "candidate_revision": 3,
        "stage_revision": 2,
        "receipt_fingerprint": HASH,
        "applied_receipt_fingerprint": HASH,
    }
    value.update(changes)
    return value


def _workspace(**changes):
    value = {
        "document_kind": "whiteboard",
        "display_name": "测试方案",
        "current_view": "whiteboard",
        "dirty": False,
        "has_path": True,
        "can_save": True,
        "can_open": True,
        "can_export": True,
    }
    value.update(changes)
    return value


def _result(status="completed", message="操作完成。", guard=False):
    return {
        "status": status,
        "message": message,
        "dirty_open_guard_passed": guard,
    }


def _controller(
    tmp_path,
    *,
    callbacks=None,
    context=None,
    events=None,
    expected_session_ref=None,
):
    current = context or {
        "binding": _binding(),
        "workspace": _workspace(),
    }
    ledger = NativeAppActionLedger(
        tmp_path,
        conversation_id="conversation-1",
    )
    controller = NativeAppActionController(
        ledger=ledger,
        context_reader=lambda: copy.deepcopy(current),
        callbacks=callbacks or {},
        event_callback=None if events is None else events.append,
        expected_session_ref=expected_session_ref,
    )
    return controller, ledger, current


def _queue(controller, action="save_as", reason="请用户处理。"):
    with controller.agent_turn():
        return controller.call_model_tool(
            REQUEST_ACTION_TOOL,
            {
                "action": action,
                "reason": reason,
                "suggested_name": "测试方案.json",
            },
        )


def _present(controller, action="save_as"):
    queued = _queue(controller, action=action)
    return controller.present_action(queued["card"]["action_id"])


def _cross_process_present(root, action_id, result_queue):
    """Spawn-safe helper proving the OS admission lock is process-wide."""

    ledger = NativeAppActionLedger(root, conversation_id="conversation-1")
    controller = NativeAppActionController(
        ledger=ledger,
        context_reader=lambda: {
            "binding": _binding(),
            "workspace": _workspace(),
        },
        callbacks={},
    )
    try:
        controller.present_action(action_id)
    except NativeAppActionControllerError as exc:
        result_queue.put(exc.code)
    else:
        result_queue.put("unexpected_success")


def test_model_only_queues_path_free_card_and_ui_callback_is_explicit(tmp_path):
    calls = []
    controller, ledger, _ = _controller(
        tmp_path,
        callbacks={"save_as": lambda request: calls.append(request) or _result()},
    )

    queued = _queue(controller)

    assert queued["status"] == "queued_for_user"
    assert calls == []
    assert ledger.read()["cards"][0]["status"] == "queued"
    assert "path" not in json.dumps(_without_validated_opaque_operation_ids(queued), ensure_ascii=False).lower()
    projected = controller.list_cards()[0]
    assert projected["origin_binding"] == {
        "document_id": "document-1",
        "candidate_revision": 3,
        "stage_revision": 2,
        "receipt_fingerprint": HASH,
    }
    assert projected["origin_workspace"] == _workspace()
    assert "applied_receipt_fingerprint" not in projected["origin_binding"]


def test_present_execute_and_lost_ack_replays_never_repeat_callback(tmp_path):
    calls = []
    events = []
    controller, ledger, _ = _controller(
        tmp_path,
        callbacks={"save_as": lambda request: calls.append(request) or _result()},
        events=events,
    )
    queued = _queue(controller)
    action_id = queued["card"]["action_id"]

    first_present = controller.present_action(action_id)
    replay_present = controller.present_action(action_id)
    assert replay_present["status"] == "already_presented"
    assert (
        replay_present["card"]["ui_receipt"]
        == first_present["card"]["ui_receipt"]
    )

    completed = controller.execute_presented(
        action_id=action_id,
        ui_receipt=first_present["card"]["ui_receipt"],
    )

    assert completed["card"]["status"] == "completed"
    assert len(calls) == 1
    assert "path" not in json.dumps(_without_validated_opaque_operation_ids(calls[0]), ensure_ascii=False).lower()
    replay_execute = controller.execute_presented(
        action_id=action_id,
        ui_receipt=first_present["card"]["ui_receipt"],
    )
    assert replay_execute["status"] == "already_settled"
    assert replay_execute["card"] == completed["card"]
    assert len(calls) == 1
    assert ledger.read()["revision"] == 3
    assert {event["event"] for event in events} >= {
        "card_ledger_changed",
        "card_presented",
        "card_resolved",
    }


def test_same_model_request_after_lost_ack_is_deduplicated(tmp_path):
    controller, ledger, _ = _controller(tmp_path)

    first = _queue(controller)
    replay = _queue(controller, reason="换一种措辞仍是同一意图。")

    assert replay["status"] == "already_queued"
    assert replay["card"]["action_id"] == first["card"]["action_id"]
    assert ledger.read()["revision"] == 1


def test_agent_turn_and_file_callback_share_a_fail_closed_gate(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def callback(_request):
        started.set()
        assert release.wait(5)
        return _result()

    controller, _, _ = _controller(
        tmp_path,
        callbacks={"save_as": callback},
    )
    presented = _present(controller)
    action_id = presented["card"]["action_id"]
    receipt = presented["card"]["ui_receipt"]

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            controller.execute_presented,
            action_id=action_id,
            ui_receipt=receipt,
        )
        assert started.wait(5)
        with pytest.raises(
            NativeAppActionControllerError,
            match="app_action_serial_gate_busy",
        ), controller.agent_turn():
            pass
        release.set()
        assert future.result()["card"]["status"] == "completed"

    queued = _queue(controller)
    with controller.agent_turn(), pytest.raises(
        NativeAppActionControllerError,
        match="app_action_serial_gate_busy",
    ):
        controller.present_action(queued["card"]["action_id"])


def test_read_only_card_polling_does_not_compete_with_agent_operation(tmp_path):
    controller, _, _ = _controller(tmp_path)
    queued = _queue(controller)
    started, release = threading.Event(), threading.Event()
    checkpoint_path = tmp_path / CHECKPOINT_FILE
    before = checkpoint_path.read_bytes()

    def hold_agent_operation():
        with controller.agent_call():
            started.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(hold_agent_operation)
        assert started.wait(5)
        try:
            assert controller.list_cards()[0]["action_id"] == queued["card"]["action_id"]
            assert checkpoint_path.read_bytes() == before
            # Reading may proceed, but an actual user file effect still may not.
            with pytest.raises(NativeAppActionControllerError, match="app_action_serial_gate_busy"):
                controller.present_action(queued["card"]["action_id"])
        finally:
            release.set()
        pending.result()


def test_card_polling_observes_pending_ledger_without_advancing_companion(tmp_path):
    controller, ledger, context = _controller(tmp_path)
    checkpoint_path = tmp_path / CHECKPOINT_FILE
    before = checkpoint_path.read_bytes()
    # This is the real cut between an admitted ledger write and companion sync.
    requested = ledger.request(expected_revision=0, action="save_as", reason="保存白板",
        suggested_name="测试.json", binding=context["binding"], workspace=context["workspace"])
    assert controller.list_cards()[0]["action_id"] == requested["card"]["action_id"]
    assert checkpoint_path.read_bytes() == before
    controller.checkpoint.sync(ledger.read())
    assert checkpoint_path.read_bytes() != before


def test_card_polling_still_rejects_rollback_and_same_revision_fork(tmp_path):
    controller, ledger, _ = _controller(tmp_path)
    old = ledger.path.read_bytes() if ledger.path.exists() else None
    _queue(controller)
    current = ledger.path.read_bytes()
    if old is None:
        ledger.path.unlink()
    else:
        ledger.path.write_bytes(old)
    with pytest.raises(NativeAppActionControllerError, match="app_action_checkpoint_rollback"):
        controller.list_cards()
    ledger.path.write_bytes(current)
    raw = json.loads(current)
    raw["cards"][0]["reason"] = "同版本不同内容"
    ledger.path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(NativeAppActionControllerError, match="app_action_checkpoint_ledger_invalid"):
        controller.list_cards()


def test_durable_claim_blocks_second_controller_and_restart_never_replays(tmp_path):
    calls = []
    controller, ledger, context = _controller(
        tmp_path,
        callbacks={"save_as": lambda request: calls.append(request) or _result()},
    )
    presented = _present(controller)
    action_id = presented["card"]["action_id"]
    receipt = presented["card"]["ui_receipt"]
    controller.checkpoint.claim_execution(
        ledger=ledger.read(),
        action_id=action_id,
        ui_receipt=receipt,
    )

    restarted, _, _ = _controller(
        tmp_path,
        callbacks={"save_as": lambda request: calls.append(request) or _result()},
        context=context,
    )
    with pytest.raises(
        NativeAppActionControllerError,
        match="app_action_execution_already_claimed",
    ):
        restarted.execute_presented(action_id=action_id, ui_receipt=receipt)

    recovered = restarted.recover_interrupted_execution()

    assert recovered["status"] == "recovered_without_replay"
    assert recovered["card"]["status"] == "failed"
    assert "不会自动重试" not in recovered["card"]["result_message"]
    assert "未自动重试" in recovered["card"]["result_message"]
    assert calls == []
    assert restarted.checkpoint_projection()["execution_outcome_unknown"] is False


def test_claim_is_on_disk_before_callback_and_callback_runs_once_across_controllers(
    tmp_path,
):
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def callback(_request):
        checkpoint = json.loads((tmp_path / CHECKPOINT_FILE).read_text("utf-8"))
        assert checkpoint["inflight"] is not None
        markers = list(tmp_path.glob("native-app-action-execution-*.json"))
        assert len(markers) == 1
        assert json.loads(markers[0].read_text("utf-8"))["action_id"] == (
            checkpoint["inflight"]["action_id"]
        )
        calls.append("called")
        entered.set()
        assert release.wait(5)
        return _result()

    first, _, context = _controller(
        tmp_path,
        callbacks={"save_as": callback},
    )
    second, _, _ = _controller(
        tmp_path,
        callbacks={"save_as": callback},
        context=context,
    )
    presented = _present(first)
    action_id = presented["card"]["action_id"]
    receipt = presented["card"]["ui_receipt"]

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            first.execute_presented,
            action_id=action_id,
            ui_receipt=receipt,
        )
        assert entered.wait(5)
        with pytest.raises(
            NativeAppActionControllerError,
            match="app_action_serial_gate_busy",
        ):
            second.execute_presented(
                action_id=action_id,
                ui_receipt=receipt,
            )
        release.set()
        completed = future.result()

    replay = second.execute_presented(action_id=action_id, ui_receipt=receipt)
    assert completed["card"]["status"] == "completed"
    assert replay["status"] == "already_settled"
    assert calls == ["called"]


def test_valid_ledger_and_checkpoint_rollback_cannot_replay_callback(tmp_path):
    calls = []
    controller, ledger, context = _controller(
        tmp_path,
        callbacks={"save_as": lambda request: calls.append(request) or _result()},
    )
    presented = _present(controller)
    action_id = presented["card"]["action_id"]
    receipt = presented["card"]["ui_receipt"]
    presented_ledger = ledger.path.read_bytes()
    presented_checkpoint = (tmp_path / CHECKPOINT_FILE).read_bytes()
    completed = controller.execute_presented(
        action_id=action_id,
        ui_receipt=receipt,
    )
    assert completed["card"]["status"] == "completed"
    assert len(calls) == 1

    ledger.path.write_bytes(presented_ledger)
    (tmp_path / CHECKPOINT_FILE).write_bytes(presented_checkpoint)
    restarted, _, _ = _controller(
        tmp_path,
        callbacks={"save_as": lambda request: calls.append(request) or _result()},
        context=context,
    )
    recovered = restarted.execute_presented(
        action_id=action_id,
        ui_receipt=receipt,
    )

    assert recovered["status"] == "recovered_without_replay"
    assert recovered["card"]["status"] == "failed"
    assert "结果未知" in recovered["card"]["result_message"]
    assert len(calls) == 1


def test_recovery_detects_marker_created_before_checkpoint_claim(tmp_path):
    calls = []
    controller, _, context = _controller(
        tmp_path,
        callbacks={"save_as": lambda request: calls.append(request) or _result()},
    )
    presented = _present(controller)
    action_id = presented["card"]["action_id"]
    receipt = presented["card"]["ui_receipt"]
    assert controller.checkpoint.persist_execution_marker(
        action_id=action_id,
        ui_receipt=receipt,
    )

    restarted, _, _ = _controller(
        tmp_path,
        callbacks={"save_as": lambda request: calls.append(request) or _result()},
        context=context,
    )
    recovered = restarted.recover_interrupted_execution()

    assert recovered["status"] == "recovered_without_replay"
    assert recovered["card"]["status"] == "failed"
    assert "结果未知" in recovered["card"]["result_message"]
    assert calls == []


def test_agent_turn_owns_calling_thread_and_blocks_other_controller(tmp_path):
    first, ledger, context = _controller(tmp_path)
    second, _, _ = _controller(tmp_path, context=context)
    errors = []

    with first.agent_turn():
        thread = threading.Thread(
            target=lambda: _capture_controller_error(
                errors,
                first.call_model_tool,
                REQUEST_ACTION_TOOL,
                {"action": "navigate_result", "reason": "越权线程。"},
            )
        )
        thread.start()
        thread.join(5)
        assert not thread.is_alive()
        with pytest.raises(
            NativeAppActionControllerError,
            match="app_action_serial_gate_busy",
        ):
            second.present_action("action_" + "a" * 32)

    assert errors == ["app_action_agent_turn_required"]
    assert ledger.read()["cards"] == []


def _capture_controller_error(target, callback, *args, **kwargs):
    try:
        callback(*args, **kwargs)
    except NativeAppActionControllerError as exc:
        target.append(exc.code)


def test_agent_turn_and_file_ui_are_serialized_across_processes(tmp_path):
    controller, _, _ = _controller(tmp_path)
    queued = _queue(controller, action="navigate_result")
    ctx = multiprocessing.get_context("spawn")
    result_queue = ctx.Queue()

    with controller.agent_turn():
        process = ctx.Process(
            target=_cross_process_present,
            args=(str(tmp_path), queued["card"]["action_id"], result_queue),
        )
        process.start()
        process.join(15)
        assert process.exitcode == 0

    assert result_queue.get(timeout=2) == "app_action_serial_gate_busy"


@pytest.mark.parametrize("status", ["cancelled", "failed"])
def test_user_cancel_and_execution_failure_are_terminal(tmp_path, status):
    controller, _, _ = _controller(
        tmp_path,
        callbacks={
            "save_as": lambda _request: _result(
                status=status,
                message="用户取消。" if status == "cancelled" else "执行失败。",
            )
        },
    )
    presented = _present(controller)

    result = controller.execute_presented(
        action_id=presented["card"]["action_id"],
        ui_receipt=presented["card"]["ui_receipt"],
    )

    assert result["card"]["status"] == status
    assert controller.checkpoint_projection()["execution_outcome_unknown"] is False


def test_callback_exception_keeps_unknown_claim_without_leaking_exception_text(tmp_path):
    def callback(_request):
        raise RuntimeError("C:\\private\\secret.json")

    controller, _, _ = _controller(
        tmp_path,
        callbacks={"save_as": callback},
    )
    presented = _present(controller)

    from genshin_app.app_fault_store import fault_scope
    with fault_scope(tmp_path), pytest.raises(NativeAppActionControllerError,
            match="app_action_execution_outcome_unknown") as caught:
        controller.execute_presented(action_id=presented["card"]["action_id"],
                                     ui_receipt=presented["card"]["ui_receipt"])
    assert controller.execution_pending()
    assert "private" not in json.dumps(_without_validated_opaque_operation_ids(caught.value.details))
    assert controller.list_cards()[0]["status"] == "claimed_for_external_execution"


def test_callback_message_is_redacted_and_extra_path_field_is_rejected(tmp_path):
    secret_message = (
        "保存到 C:\\private\\secret.json，"
        "api_key=sk-abcdefghijklmnopqrstuvwxyz"
    )
    controller, ledger, _ = _controller(
        tmp_path / "redacted",
        callbacks={
            "save_as": lambda _request: _result(message=secret_message)
        },
    )
    presented = _present(controller)
    result = controller.execute_presented(
        action_id=presented["card"]["action_id"],
        ui_receipt=presented["card"]["ui_receipt"],
    )
    persisted = json.dumps(_without_validated_opaque_operation_ids(ledger.read()), ensure_ascii=False)
    returned = json.dumps(_without_validated_opaque_operation_ids(result), ensure_ascii=False)
    assert "private" not in persisted + returned
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in persisted + returned

    rejected, _, _ = _controller(
        tmp_path / "extra-field",
        callbacks={
            "save_as": lambda _request: {
                **_result(),
                "path": "C:\\private\\secret.json",
            }
        },
    )
    presented = _present(rejected)
    from genshin_app.app_fault_store import fault_scope
    with fault_scope(tmp_path), pytest.raises(NativeAppActionControllerError,
            match="app_action_execution_outcome_unknown") as caught:
        rejected.execute_presented(action_id=presented["card"]["action_id"],
                                   ui_receipt=presented["card"]["ui_receipt"])
    assert rejected.execution_pending()
    assert "private" not in json.dumps(_without_validated_opaque_operation_ids(caught.value.details))


def test_callback_invalid_utf8_scalar_does_not_forge_a_failed_write(tmp_path):
    controller, _, _ = _controller(
        tmp_path,
        callbacks={
            "save_as": lambda _request: _result(message="bad\ud800text")
        },
    )
    presented = _present(controller)

    from genshin_app.app_fault_store import fault_scope
    with fault_scope(tmp_path), pytest.raises(NativeAppActionControllerError,
            match="app_action_execution_outcome_unknown") as caught:
        controller.execute_presented(action_id=presented["card"]["action_id"],
                                     ui_receipt=presented["card"]["ui_receipt"])
    assert "bad" not in json.dumps(_without_validated_opaque_operation_ids(caught.value.details))
    assert controller.checkpoint_projection()["execution_outcome_unknown"] is True


def test_event_callback_failure_cannot_rollback_or_replay_operation(tmp_path):
    calls = []

    def broken_event(_payload):
        raise RuntimeError("renderer failed at C:\\private\\view.json")

    controller, ledger, _ = _controller(
        tmp_path,
        callbacks={"save_as": lambda request: calls.append(request) or _result()},
    )
    controller._event_callback = broken_event
    presented = _present(controller)
    first = controller.execute_presented(
        action_id=presented["card"]["action_id"],
        ui_receipt=presented["card"]["ui_receipt"],
    )
    replay = controller.execute_presented(
        action_id=presented["card"]["action_id"],
        ui_receipt=presented["card"]["ui_receipt"],
    )

    assert first["card"]["status"] == "completed"
    assert replay["status"] == "already_settled"
    assert ledger.read()["cards"][0]["status"] == "completed"
    assert len(calls) == 1


def test_dirty_open_requires_callback_confirmation(tmp_path):
    context = {
        "binding": _binding(
            receipt_fingerprint=None,
            applied_receipt_fingerprint=None,
        ),
        "workspace": _workspace(dirty=True),
    }
    controller, _, _ = _controller(
        tmp_path,
        context=context,
        callbacks={"open_document": lambda _request: _result(guard=False)},
    )
    presented = _present(controller, action="open_document")

    result = controller.execute_presented(
        action_id=presented["card"]["action_id"],
        ui_receipt=presented["card"]["ui_receipt"],
    )

    assert presented["card"]["dirty_open_guard_required"] is True
    assert result["card"]["status"] == "failed"
    assert "脏文档" in result["card"]["result_message"]


def test_open_rechecks_dirty_state_at_execution_time(tmp_path):
    context = {
        "binding": _binding(
            receipt_fingerprint=None,
            applied_receipt_fingerprint=None,
        ),
        "workspace": _workspace(dirty=False),
    }
    requests = []
    controller, _, _ = _controller(
        tmp_path,
        context=context,
        callbacks={
            "open_document": lambda request: requests.append(request)
            or _result(guard=False)
        },
    )
    presented = _present(controller, action="open_document")
    assert presented["card"]["dirty_open_guard_required"] is False
    context["workspace"]["dirty"] = True

    result = controller.execute_presented(
        action_id=presented["card"]["action_id"],
        ui_receipt=presented["card"]["ui_receipt"],
    )

    assert requests[0]["dirty_open_guard_required"] is True
    assert result["card"]["status"] == "failed"
    assert "脏文档" in result["card"]["result_message"]


def test_execute_rechecks_workspace_capability_without_invoking_callback(tmp_path):
    context = {"binding": _binding(), "workspace": _workspace()}
    calls = []
    controller, _, _ = _controller(
        tmp_path,
        context=context,
        callbacks={"save_as": lambda request: calls.append(request) or _result()},
    )
    presented = _present(controller)
    context["workspace"]["can_save"] = False

    result = controller.execute_presented(
        action_id=presented["card"]["action_id"],
        ui_receipt=presented["card"]["ui_receipt"],
    )

    assert result["card"]["status"] == "failed"
    assert calls == []
    assert controller.checkpoint_projection()["execution_outcome_unknown"] is False


def test_completed_open_settles_post_binding_then_expires_old_cards(tmp_path):
    context = {
        "binding": _binding(
            receipt_fingerprint=None,
            applied_receipt_fingerprint=None,
        ),
        "workspace": _workspace(dirty=True),
    }

    def open_callback(_request):
        context["binding"] = _binding(
            document_id="document-2",
            candidate_revision=0,
            stage_revision=0,
            receipt_fingerprint=None,
            applied_receipt_fingerprint=None,
        )
        context["workspace"] = _workspace(
            display_name="新方案",
            dirty=False,
            has_path=True,
        )
        return _result(message="打开完成。", guard=True)

    controller, ledger, _ = _controller(
        tmp_path,
        context=context,
        callbacks={"open_document": open_callback},
    )
    opened = _queue(controller, action="open_document")
    stale = _queue(controller, action="navigate_result")
    presented = controller.present_action(opened["card"]["action_id"])

    result = controller.execute_presented(
        action_id=opened["card"]["action_id"],
        ui_receipt=presented["card"]["ui_receipt"],
    )

    cards = {card["action_id"]: card for card in ledger.read()["cards"]}
    assert cards[opened["card"]["action_id"]]["status"] == "completed"
    assert cards[stale["card"]["action_id"]]["status"] == "expired"
    assert result["expired_action_ids"] == [stale["card"]["action_id"]]


def test_restart_recovery_after_open_changed_document_fails_without_replay(tmp_path):
    context = {
        "binding": _binding(
            receipt_fingerprint=None,
            applied_receipt_fingerprint=None,
        ),
        "workspace": _workspace(dirty=False),
    }
    controller, ledger, _ = _controller(tmp_path, context=context)
    presented = _present(controller, action="open_document")
    controller.checkpoint.claim_execution(
        ledger=ledger.read(),
        action_id=presented["card"]["action_id"],
        ui_receipt=presented["card"]["ui_receipt"],
    )
    context["binding"] = _binding(
        document_id="document-2",
        candidate_revision=0,
        stage_revision=0,
        receipt_fingerprint=None,
        applied_receipt_fingerprint=None,
    )

    restarted, _, _ = _controller(tmp_path, context=context)
    recovered = restarted.recover_interrupted_execution()

    assert recovered["card"]["status"] == "failed"
    assert "结果未知" in recovered["card"]["result_message"]


def test_recovery_finishes_post_open_expiration_after_resolution_crash(tmp_path):
    context = {
        "binding": _binding(
            receipt_fingerprint=None,
            applied_receipt_fingerprint=None,
        ),
        "workspace": _workspace(dirty=False),
    }
    controller, ledger, _ = _controller(tmp_path, context=context)
    opened = _queue(controller, action="open_document")
    stale = _queue(controller, action="navigate_result")
    presented = controller.present_action(opened["card"]["action_id"])
    controller.checkpoint.claim_execution(
        ledger=ledger.read(),
        action_id=opened["card"]["action_id"],
        ui_receipt=presented["card"]["ui_receipt"],
    )
    context["binding"] = _binding(
        document_id="document-2",
        candidate_revision=0,
        stage_revision=0,
        receipt_fingerprint=None,
        applied_receipt_fingerprint=None,
    )
    ledger.resolve(
        action_id=opened["card"]["action_id"],
        status="completed",
        result_message="打开完成。",
        current_binding=context["binding"],
        agent_turn_active=False,
        ui_receipt=presented["card"]["ui_receipt"],
        dirty_open_guard_passed=False,
    )

    restarted, _, _ = _controller(tmp_path, context=context)
    recovered = restarted.recover_interrupted_execution()

    cards = {card["action_id"]: card for card in ledger.read()["cards"]}
    assert recovered["card"]["status"] == "completed"
    assert recovered["expired_action_ids"] == [stale["card"]["action_id"]]
    assert cards[stale["card"]["action_id"]]["status"] == "expired"


def test_checkpoint_detects_valid_old_ledger_rollback(tmp_path):
    controller, ledger, _ = _controller(tmp_path)
    _queue(controller, action="navigate_result")
    old_ledger = ledger.path.read_bytes()
    _queue(controller, action="navigate_whiteboard")
    assert ledger.read()["revision"] == 2
    ledger.path.write_bytes(old_ledger)

    with pytest.raises(
        NativeAppActionControllerError, match="app_action_history_rollback"
    ):
        _controller(tmp_path)


def test_external_session_ref_detects_full_companion_and_ledger_rollback(tmp_path):
    controller, ledger, _ = _controller(tmp_path)
    _queue(controller, action="navigate_result")
    old_ledger = ledger.path.read_bytes()
    old_checkpoint = (tmp_path / CHECKPOINT_FILE).read_bytes()
    _queue(controller, action="navigate_whiteboard")
    latest_ref = controller.checkpoint_projection()["session_ref"]

    ledger.path.write_bytes(old_ledger)
    (tmp_path / CHECKPOINT_FILE).write_bytes(old_checkpoint)

    with pytest.raises(
        NativeAppActionControllerError,
        match="app_action_session_checkpoint_mismatch",
    ):
        _controller(tmp_path, expected_session_ref=latest_ref)


def test_checkpoint_detects_same_revision_ledger_fork(tmp_path):
    controller, ledger, _ = _controller(tmp_path)
    _queue(controller, action="navigate_result")
    raw = json.loads(ledger.path.read_text("utf-8"))
    raw["cards"][0]["reason"] = "同 revision 的另一条合法分支。"
    ledger.path.write_text(
        json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(
        NativeAppActionControllerError, match="app_action_history_diverged"
    ):
        controller.checkpoint_projection()


def test_checkpoint_history_is_hash_chained_and_rejects_splicing(tmp_path):
    controller, _, _ = _controller(tmp_path)
    _queue(controller, action="navigate_result")
    _queue(controller, action="navigate_whiteboard")
    path = tmp_path / CHECKPOINT_FILE
    value = json.loads(path.read_text("utf-8"))
    assert len(value["history"]) >= 3
    value["history"][-1]["prior_session_ref"] = value["history"][0][
        "session_ref"
    ]
    path.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    with pytest.raises(
        NativeAppActionControllerError,
        match="app_action_checkpoint_history_invalid",
    ):
        controller.checkpoint_projection()


def test_expected_anchor_is_checked_once_then_survives_history_trimming(tmp_path):
    first, ledger, _ = _controller(tmp_path)
    anchor = first.checkpoint_projection()["session_ref"]
    restarted, _, _ = _controller(tmp_path, expected_session_ref=anchor)
    synthetic = ledger.read()
    for revision in range(1, 70):
        synthetic = copy.deepcopy(synthetic)
        synthetic["revision"] = revision
        restarted.checkpoint.sync(synthetic)

    checkpoint = restarted.checkpoint.read()
    assert len(checkpoint["history"]) == 64
    assert anchor not in {item["session_ref"] for item in checkpoint["history"]}


def test_checkpoint_projection_binds_revision_digest_and_summary(tmp_path):
    controller, _, _ = _controller(tmp_path)
    _queue(controller, action="navigate_result")

    projection = controller.checkpoint_projection()

    assert projection["ledger_revision"] == 1
    assert len(projection["ledger_hash"]) == 64
    assert projection["summary"]["counts"]["queued"] == 1
    assert projection["session_ref"].startswith("appaction2.1.")


def test_context_is_double_sampled_and_change_fails_closed(tmp_path):
    calls = 0

    def reader():
        nonlocal calls
        calls += 1
        return {
            "binding": _binding(stage_revision=2 if calls == 1 else 3),
            "workspace": _workspace(),
        }

    ledger = NativeAppActionLedger(tmp_path, conversation_id="conversation-1")
    controller = NativeAppActionController(
        ledger=ledger,
        context_reader=reader,
        callbacks={},
    )

    with controller.agent_turn(), pytest.raises(
        NativeAppActionControllerError, match="app_action_context_changed"
    ):
        controller.call_model_tool(
            REQUEST_ACTION_TOOL,
            {"action": "navigate_result", "reason": "导航。"},
        )
    assert ledger.read()["cards"] == []


def test_checkpoint_duplicate_json_key_fails_closed(tmp_path):
    controller, _, _ = _controller(tmp_path)
    path = tmp_path / CHECKPOINT_FILE
    raw = path.read_text(encoding="utf-8")
    path.write_text(
        raw.replace('"sequence":0', '"sequence":0,"sequence":0'),
        encoding="utf-8",
    )

    with pytest.raises(
        NativeAppActionControllerError,
        match="app_action_checkpoint_storage_invalid",
    ):
        controller.checkpoint_projection()


def test_checkpoint_invalid_utf8_fails_closed(tmp_path):
    controller, _, _ = _controller(tmp_path)
    (tmp_path / CHECKPOINT_FILE).write_bytes(b"{\xff}")

    with pytest.raises(
        NativeAppActionControllerError,
        match="app_action_checkpoint_storage_invalid",
    ):
        controller.checkpoint_projection()


def test_checkpoint_and_operation_lock_hardlinks_fail_closed(tmp_path):
    controller, _, _ = _controller(tmp_path)
    queued = _queue(controller, action="navigate_result")
    checkpoint_alias = tmp_path / "checkpoint-hardlink-alias"
    try:
        os.link(tmp_path / CHECKPOINT_FILE, checkpoint_alias)
    except OSError as exc:
        pytest.skip(f"filesystem does not permit hardlinks: {exc}")

    with pytest.raises(
        NativeAppActionControllerError,
        match="app_action_checkpoint_storage_invalid",
    ):
        controller.checkpoint_projection()

    checkpoint_alias.unlink()
    operation_path = tmp_path / ".native-app-action-operation-v1.lock"
    operation_alias = tmp_path / "operation-hardlink-alias"
    os.link(operation_path, operation_alias)
    with pytest.raises(
        NativeAppActionControllerError,
        match="app_action_checkpoint_storage_invalid",
    ):
        controller.present_action(queued["card"]["action_id"])


def test_external_ui_claim_protects_its_side_effect_without_blocking_candidate_work(tmp_path):
    controller, _, _ = _controller(tmp_path)
    presented = _present(controller)
    action_id = presented["card"]["action_id"]
    receipt = presented["card"]["ui_receipt"]

    claimed = controller.claim_presented_action(
        action_id=action_id,
        ui_receipt=receipt,
    )

    assert claimed["status"] == "claimed_for_external_execution"
    assert claimed["request"] == {
        "action_id": action_id,
        "action": "save_as",
        "reason": "请用户处理。",
        "suggested_name": "测试方案.json",
        "document_kind": "whiteboard",
        "display_name": "测试方案",
        "current_view": "whiteboard",
        "dirty_open_guard_required": False,
        "target": {"kind": "formal_document", "document_id": "document-1"},
    }
    assert controller.checkpoint.has_execution_marker(
        action_id=action_id,
        ui_receipt=receipt,
    )
    assert controller.checkpoint_projection()["execution_outcome_unknown"] is True
    with controller.agent_call():
        assert controller.agent_turn_active
    assert controller.execution_pending() is True
    with pytest.raises(
        NativeAppActionControllerError,
        match="app_action_execution_already_claimed",
    ):
        controller.claim_presented_action(
            action_id=action_id,
            ui_receipt=receipt,
        )

    listed = controller.list_cards()
    assert listed[0]["status"] == "claimed_for_external_execution"
    assert listed[0]["execution_outcome_unknown"] is True
    assert "request" not in listed[0]


def test_external_ui_settlement_validates_receipt_and_bounded_outcome(tmp_path):
    controller, _, _ = _controller(tmp_path)
    presented = _present(controller)
    action_id = presented["card"]["action_id"]
    receipt = presented["card"]["ui_receipt"]
    controller.claim_presented_action(
        action_id=action_id,
        ui_receipt=receipt,
    )

    with pytest.raises(
        NativeAppActionControllerError,
        match="app_action_execution_receipt_mismatch",
    ):
        controller.settle_claimed_action(
            action_id=action_id,
            ui_receipt="ui_" + "x" * 43,
            outcome=_result(),
        )
    with pytest.raises(
        NativeAppActionControllerError,
        match="app_action_callback_result_invalid",
    ):
        controller.settle_claimed_action(
            action_id=action_id,
            ui_receipt=receipt,
            outcome={**_result(), "path": "C:\\private\\secret.json"},
        )
    assert controller.checkpoint_projection()["execution_outcome_unknown"] is True

    completed = controller.settle_claimed_action(
        action_id=action_id,
        ui_receipt=receipt,
        outcome=_result(message="Renderer 已完成。"),
    )
    replay = controller.settle_claimed_action(
        action_id=action_id,
        ui_receipt=receipt,
        outcome=_result(message="Renderer 已完成。"),
    )

    assert completed["card"]["status"] == "completed"
    assert replay["status"] == "already_settled"
    assert controller.checkpoint_projection()["execution_outcome_unknown"] is False
    with pytest.raises(
        NativeAppActionError, match="app_action_settlement_conflict"
    ):
        controller.settle_claimed_action(
            action_id=action_id,
            ui_receipt=receipt,
            outcome=_result(status="failed", message="冲突。"),
        )


def test_external_ui_crash_after_claim_recovers_without_side_effect_replay(tmp_path):
    calls = []
    controller, _, context = _controller(
        tmp_path,
        callbacks={"save_as": lambda request: calls.append(request) or _result()},
    )
    presented = _present(controller)
    action_id = presented["card"]["action_id"]
    receipt = presented["card"]["ui_receipt"]
    controller.claim_presented_action(
        action_id=action_id,
        ui_receipt=receipt,
    )

    restarted, _, _ = _controller(
        tmp_path,
        context=context,
        callbacks={"save_as": lambda request: calls.append(request) or _result()},
    )
    recovered = restarted.recover_interrupted_execution()

    assert recovered["status"] == "recovered_without_replay"
    assert recovered["card"]["status"] == "failed"
    assert calls == []
    assert restarted.checkpoint_projection()["execution_outcome_unknown"] is False


def test_renderer_reload_explicitly_recovers_exact_claim_without_replay(tmp_path):
    calls = []
    controller, _, _ = _controller(
        tmp_path,
        callbacks={"save_as": lambda request: calls.append(request) or _result()},
    )
    presented = _present(controller)
    action_id = presented["card"]["action_id"]
    receipt = presented["card"]["ui_receipt"]
    controller.claim_presented_action(action_id=action_id, ui_receipt=receipt)

    with pytest.raises(
        NativeAppActionControllerError,
        match="app_action_execution_receipt_mismatch",
    ):
        controller.recover_claimed_action_without_replay(
            action_id=action_id,
            ui_receipt="ui_" + "x" * 43,
        )
    assert controller.checkpoint_projection()["execution_outcome_unknown"] is True

    recovered = controller.recover_claimed_action_without_replay(
        action_id=action_id,
        ui_receipt=receipt,
    )
    replay = controller.recover_claimed_action_without_replay(
        action_id=action_id,
        ui_receipt=receipt,
    )

    assert recovered["status"] == "recovered_without_replay"
    assert recovered["card"]["status"] == "failed"
    assert recovered["request"] is None
    assert replay["status"] == "already_recovered"
    assert replay["request"] is None
    assert calls == []
    assert controller.checkpoint_projection()["execution_outcome_unknown"] is False
    with controller.agent_turn():
        pass


def test_renderer_reload_recovery_closes_exact_marker_only_window(tmp_path):
    controller, _, _ = _controller(tmp_path)
    presented = _present(controller)
    action_id = presented["card"]["action_id"]
    receipt = presented["card"]["ui_receipt"]
    controller.checkpoint.persist_execution_marker(
        action_id=action_id,
        ui_receipt=receipt,
    )

    recovered = controller.recover_claimed_action_without_replay(
        action_id=action_id,
        ui_receipt=receipt,
    )

    assert recovered["status"] == "recovered_without_replay"
    assert recovered["card"]["status"] == "failed"
    assert recovered["request"] is None
    assert controller.checkpoint_projection()["execution_outcome_unknown"] is False


def test_renderer_reload_unknown_open_expires_old_document_actions(tmp_path):
    controller, ledger, _ = _controller(tmp_path)
    opened = _queue(controller, action="open_document")
    stale = _queue(controller, action="navigate_result")
    presented = controller.present_action(opened["card"]["action_id"])
    receipt = presented["card"]["ui_receipt"]
    controller.claim_presented_action(
        action_id=opened["card"]["action_id"],
        ui_receipt=receipt,
    )

    recovered = controller.recover_claimed_action_without_replay(
        action_id=opened["card"]["action_id"],
        ui_receipt=receipt,
    )

    cards = {card["action_id"]: card for card in ledger.read()["cards"]}
    assert recovered["request"] is None
    assert cards[opened["card"]["action_id"]]["status"] == "failed"
    assert cards[stale["card"]["action_id"]]["status"] == "expired"
    assert recovered["expired_action_ids"] == [stale["card"]["action_id"]]


def test_interrupted_claim_recovery_does_not_require_live_app_context(tmp_path):
    controller, _, _ = _controller(tmp_path)
    presented = _present(controller)
    action_id = presented["card"]["action_id"]
    receipt = presented["card"]["ui_receipt"]
    controller.claim_presented_action(
        action_id=action_id,
        ui_receipt=receipt,
    )

    ledger = NativeAppActionLedger(tmp_path, conversation_id="conversation-1")

    def missing_context():
        raise RuntimeError("router context has not been rebuilt")

    restarted = NativeAppActionController(
        ledger=ledger,
        context_reader=missing_context,
        callbacks={},
    )
    recovered = restarted.recover_interrupted_execution()

    assert recovered["status"] == "recovered_without_replay"
    assert recovered["card"]["status"] == "failed"
    assert restarted.checkpoint_projection()["execution_outcome_unknown"] is False


def test_interrupted_open_recovery_expires_other_cards_without_live_context(tmp_path):
    controller, ledger, _ = _controller(tmp_path)
    opened = _queue(controller, action="open_document")
    stale = _queue(controller, action="navigate_result")
    presented = controller.present_action(opened["card"]["action_id"])
    receipt = presented["card"]["ui_receipt"]
    controller.claim_presented_action(
        action_id=opened["card"]["action_id"],
        ui_receipt=receipt,
    )

    restarted = NativeAppActionController(
        ledger=NativeAppActionLedger(
            tmp_path,
            conversation_id="conversation-1",
        ),
        context_reader=lambda: (_ for _ in ()).throw(
            RuntimeError("router context has not been rebuilt")
        ),
        callbacks={},
    )
    recovered = restarted.recover_interrupted_execution()

    cards = {card["action_id"]: card for card in ledger.read()["cards"]}
    assert recovered["card"]["status"] == "failed"
    assert cards[opened["card"]["action_id"]]["status"] == "failed"
    assert cards[stale["card"]["action_id"]]["status"] == "expired"
    assert recovered["expired_action_ids"] == [stale["card"]["action_id"]]


def test_external_ui_settle_recovers_crash_after_terminal_ledger_write(tmp_path):
    controller, _, context = _controller(tmp_path)
    presented = _present(controller)
    action_id = presented["card"]["action_id"]
    receipt = presented["card"]["ui_receipt"]
    outcome = _result(message="已经写入文件。")
    controller.claim_presented_action(
        action_id=action_id,
        ui_receipt=receipt,
    )
    original_finish = controller.checkpoint.finish_execution

    def crash_before_finish(**_kwargs):
        raise RuntimeError("test crash after ledger terminal")

    controller.checkpoint.finish_execution = crash_before_finish
    with pytest.raises(RuntimeError, match="test crash"):
        controller.settle_claimed_action(
            action_id=action_id,
            ui_receipt=receipt,
            outcome=outcome,
        )
    controller.checkpoint.finish_execution = original_finish

    restarted, _, _ = _controller(tmp_path, context=context)
    settled = restarted.settle_claimed_action(
        action_id=action_id,
        ui_receipt=receipt,
        outcome=outcome,
    )
    assert settled["status"] == "already_settled"
    assert settled["card"]["status"] == "completed"
    assert restarted.checkpoint_projection()["execution_outcome_unknown"] is False


def test_external_ui_claim_is_serial_across_controllers(tmp_path):
    first, _, context = _controller(tmp_path)
    second, _, _ = _controller(tmp_path, context=context)
    presented = _present(first)
    action_id = presented["card"]["action_id"]
    receipt = presented["card"]["ui_receipt"]
    entered = threading.Event()
    release = threading.Event()
    original_claim = first.checkpoint.claim_execution

    def slow_claim(**kwargs):
        result = original_claim(**kwargs)
        entered.set()
        assert release.wait(5)
        return result

    first.checkpoint.claim_execution = slow_claim
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            first.claim_presented_action,
            action_id=action_id,
            ui_receipt=receipt,
        )
        assert entered.wait(5)
        with pytest.raises(
            NativeAppActionControllerError,
            match="app_action_serial_gate_busy",
        ):
            second.claim_presented_action(
                action_id=action_id,
                ui_receipt=receipt,
            )
        release.set()
        assert future.result()["status"] == "claimed_for_external_execution"


def test_marker_only_crash_window_keeps_exact_operation_unknown_without_locking_candidate(tmp_path):
    controller, _, _ = _controller(tmp_path)
    presented = _present(controller)
    action_id = presented["card"]["action_id"]
    receipt = presented["card"]["ui_receipt"]
    assert controller.checkpoint.persist_execution_marker(
        action_id=action_id,
        ui_receipt=receipt,
    )

    with controller.agent_call():
        assert controller.agent_turn_active
    assert controller.execution_pending() is True
    recovered = controller.recover_interrupted_execution()
    assert recovered["card"]["status"] == "failed"


def test_same_model_turn_user_wait_can_present_save_and_continue_candidate(tmp_path):
    """Production DSH still reports active while awaiting a user answer."""
    controller, ledger, context = _controller(tmp_path)
    controller._turn_active_reader = lambda: True
    card = _queue(controller, action="save_current")["card"]
    # No App domain call is running: model thinking/waiting isn't a file lock.
    presented = controller.present_action(card["action_id"])["card"]
    controller.claim_presented_action(action_id=card["action_id"], ui_receipt=presented["ui_receipt"])
    with controller.agent_call():
        context["binding"].update(candidate_revision=8, stage_revision=4,
                                  receipt_fingerprint=None, applied_receipt_fingerprint=None)
    settled = controller.settle_claimed_action(action_id=card["action_id"],
        ui_receipt=presented["ui_receipt"], outcome=_result(message="正式文件已保存。"))
    assert settled["card"]["status"] == "completed"
    assert not controller.execution_pending()
    with controller.agent_call():
        assert controller.agent_turn_active
    assert len(ledger.read()["cards"]) == 1


def test_external_completed_open_uses_post_binding_then_expires_old_cards(tmp_path):
    context = {
        "binding": _binding(
            receipt_fingerprint=None,
            applied_receipt_fingerprint=None,
        ),
        "workspace": _workspace(dirty=True),
    }
    controller, ledger, _ = _controller(tmp_path, context=context)
    opened = _queue(controller, action="open_document")
    stale = _queue(controller, action="navigate_result")
    presented = controller.present_action(opened["card"]["action_id"])
    receipt = presented["card"]["ui_receipt"]
    controller.claim_presented_action(
        action_id=opened["card"]["action_id"],
        ui_receipt=receipt,
    )

    context["binding"] = _binding(
        document_id="document-2",
        candidate_revision=0,
        stage_revision=0,
        receipt_fingerprint=None,
        applied_receipt_fingerprint=None,
    )
    context["workspace"] = _workspace(
        display_name="新方案",
        dirty=False,
        has_path=True,
    )
    result = controller.settle_claimed_action(
        action_id=opened["card"]["action_id"],
        ui_receipt=receipt,
        outcome=_result(message="打开完成。", guard=True),
    )

    cards = {card["action_id"]: card for card in ledger.read()["cards"]}
    assert cards[opened["card"]["action_id"]]["status"] == "completed"
    assert cards[stale["card"]["action_id"]]["status"] == "expired"
    assert result["expired_action_ids"] == [stale["card"]["action_id"]]


def test_external_completed_open_expires_old_cards_without_post_binding_claim(tmp_path):
    controller, ledger, _ = _controller(tmp_path)
    opened = _queue(controller, action="open_document")
    stale = _queue(controller, action="navigate_result")
    presented = controller.present_action(opened["card"]["action_id"])
    receipt = presented["card"]["ui_receipt"]
    controller.claim_presented_action(
        action_id=opened["card"]["action_id"], ui_receipt=receipt
    )

    result = controller.settle_claimed_action(
        action_id=opened["card"]["action_id"],
        ui_receipt=receipt,
        outcome=_result(message="打开完成。", guard=True),
    )

    cards = {card["action_id"]: card for card in ledger.read()["cards"]}
    assert cards[opened["card"]["action_id"]]["status"] == "completed"
    assert cards[stale["card"]["action_id"]]["status"] == "expired"
    assert result["expired_action_ids"] == [stale["card"]["action_id"]]


@pytest.mark.parametrize("status", ["cancelled", "failed"])
def test_external_ui_terminal_outcomes_never_invoke_sync_callback(tmp_path, status):
    calls = []
    controller, _, _ = _controller(
        tmp_path,
        callbacks={"save_as": lambda request: calls.append(request) or _result()},
    )
    presented = _present(controller)
    action_id = presented["card"]["action_id"]
    receipt = presented["card"]["ui_receipt"]
    controller.claim_presented_action(
        action_id=action_id,
        ui_receipt=receipt,
    )

    settled = controller.settle_claimed_action(
        action_id=action_id,
        ui_receipt=receipt,
        outcome=_result(status=status, message=f"Renderer {status}。"),
    )

    assert settled["card"]["status"] == status
    assert calls == []


def test_external_open_persists_dirty_guard_decision_across_post_open_binding(
    tmp_path,
):
    context = {
        "binding": _binding(
            receipt_fingerprint=None,
            applied_receipt_fingerprint=None,
        ),
        "workspace": _workspace(dirty=False),
    }
    controller, _, _ = _controller(tmp_path, context=context)
    presented = _present(controller, action="open_document")
    action_id = presented["card"]["action_id"]
    receipt = presented["card"]["ui_receipt"]
    context["workspace"]["dirty"] = True
    claimed = controller.claim_presented_action(
        action_id=action_id,
        ui_receipt=receipt,
    )
    assert claimed["request"]["dirty_open_guard_required"] is True

    context["binding"] = _binding(
        document_id="document-2",
        candidate_revision=0,
        stage_revision=0,
        receipt_fingerprint=None,
        applied_receipt_fingerprint=None,
    )
    context["workspace"] = _workspace(dirty=False)
    settled = controller.settle_claimed_action(
        action_id=action_id,
        ui_receipt=receipt,
        outcome=_result(message="打开完成但缺少脏文档确认。", guard=False),
    )

    assert settled["card"]["status"] == "failed"
    assert "脏文档" in settled["card"]["result_message"]


@pytest.mark.parametrize("test_name", [
    "test_model_only_queues_path_free_card_and_ui_callback_is_explicit",
    "test_present_execute_and_lost_ack_replays_never_repeat_callback",
    "test_callback_exception_keeps_unknown_claim_without_leaking_exception_text",
    "test_callback_message_is_redacted_and_extra_path_field_is_rejected",
    "test_callback_invalid_utf8_scalar_does_not_forge_a_failed_write",
])
def test_confidentiality_checks_accept_legal_opaque_id_substrings(tmp_path, monkeypatch, test_name):
    import uuid
    from types import SimpleNamespace

    from genshin_app import app_fault_store
    from genshin_app.agent_chat import native_app_action_ledger

    # Replay the exact UUID from the full-suite failure, plus legal action/UI
    # tokens containing every short word under test. No product redaction or
    # state transition is patched.
    identifier = uuid.UUID("ff2e21e9-aead-4ce2-9bad-e1a33c3417f7")
    monkeypatch.setattr(app_fault_store, "uuid", SimpleNamespace(UUID=uuid.UUID, uuid4=lambda: identifier))
    def token(length):
        marker = "badpathprivate"
        return marker + "x" * ((length * 8 + 5) // 6 - len(marker))
    monkeypatch.setattr(native_app_action_ledger, "secrets", SimpleNamespace(token_urlsafe=token))
    globals()[test_name](tmp_path)


def test_confidentiality_projection_preserves_sensitive_content_and_rejects_fake_ids():
    value = {
        "action_id": "action_" + "private" + "x" * 25,
        "message": "C:\\private\\secret.json api_key=sk-private-value",
        "path": "C:\\private\\secret.json",
        "details": {"diagnostic_id": "ff2e21e9-aead-4ce2-9bad-e1a33c3417f7", "message": "bad\ud800text"},
        "arbitrary_id": "C:\\private\\secret.json",
    }
    projected = _without_validated_opaque_operation_ids(value)
    assert projected["message"] == value["message"]
    assert projected["path"] == value["path"]
    assert projected["details"]["message"] == value["details"]["message"]
    assert projected["arbitrary_id"] == value["arbitrary_id"]
    encoded = json.dumps(projected)
    assert "private" in encoded and "path" in encoded and "bad" in encoded
    assert "sk-private-value" in encoded and "\\ud800" in encoded
    for key in ("action_id", "ui_receipt", "diagnostic_id"):
        with pytest.raises((AssertionError, ValueError)):
            _without_validated_opaque_operation_ids({key: "C:\\private\\secret.json"})
