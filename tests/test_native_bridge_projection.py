from __future__ import annotations

import hashlib
import json
import uuid

import pytest

from genshin_app.agent_chat.bridge_projection import (
    BridgeProjectionError,
    NativeBridgeEventProjector,
    NativeBridgeProjectionAcker,
)
from genshin_app.agent_chat.bridge_protocol import (
    BridgeProtocolError,
    make_frame,
    validate_event_ack_frame,
)


def _frame(seq, frame_type, *, payload=None, **optional):
    return make_frame(
        kind="event",
        frame_type=frame_type,
        message_id=str(uuid.uuid4()),
        runtime_incarnation=optional.pop("runtime_incarnation", "runtime-1"),
        conversation_id=optional.pop("conversation_id", "conversation-1"),
        turn_id=optional.pop("turn_id", "turn-1"),
        step_id=optional.pop("step_id", "step-1"),
        epoch_id=optional.pop("epoch_id", "epoch-1"),
        event_seq=seq,
        payload={} if payload is None else payload,
        **optional,
    )


def _projector(validator=None):
    return NativeBridgeEventProjector(
        runtime_incarnation="runtime-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        epoch_id="epoch-1",
        validate_tool_call=validator or (lambda _name, arguments: arguments),
    )


def _arguments(value, raw=None):
    raw = raw or json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _error(code, call):
    with pytest.raises((BridgeProjectionError, BridgeProtocolError)) as caught:
        call()
    assert caught.value.code == code


@pytest.mark.parametrize("status", [
    "budget_progress_70",
    "budget_closure_90",
    "budget_exhausted_100",
    "budget_input_soft_warning",
    "budget_context_prune_required",
])
def test_budget_turn_status_crosses_the_existing_bridge_without_counter_data(status):
    projected = _projector().ingest(_frame(
        1,
        "turn.status",
        payload={"status": status},
    ))
    assert projected == [{
        "method": "native/bridge/status",
        "params": {
            "threadId": "conversation-1",
            "turnId": "turn-1",
            "stepId": "step-1",
            "epochId": "epoch-1",
            "event": "turn.status",
            "status": status,
        },
    }]


def test_context_compaction_lifecycle_is_visible_without_crossing_summary_text():
    projector = _projector()
    started = projector.ingest(_frame(
        1,
        "context.compaction.started",
        payload={"compaction_id": "compact-1"},
    ))
    usage = projector.ingest(_frame(
        2,
        "usage",
        payload={
            "input_tokens": 900,
            "output_tokens": 80,
            "total_tokens": 980,
            "cached_input_tokens": 400,
            "reasoning_tokens": 30,
            "provider_attempt_id": "compaction:compact-1",
            "provider_purpose": "compaction",
        },
    ))
    completed = projector.ingest(_frame(
        3,
        "context.compaction.completed",
        payload={
            "compaction_id": "compact-1",
            "route": {
                "provider": "deepseek",
                "model": "deepseek-chat",
                "max_tokens": 4096,
            },
            "usage": {
                "input_tokens": 900,
                "output_tokens": 80,
                "total_tokens": 980,
            },
            "shadowed_token_count": 12_000,
        },
    ))
    terminal = projector.ingest(_frame(
        4, "turn.completed", payload={"status": "completed"},
    ))
    projector.finalize()

    assert started[0]["params"]["status"] == "context_compaction_started"
    assert started[0]["params"]["compaction"] == {"id": "compact-1"}
    assert usage[0]["method"] == "native/usage"
    assert usage[0]["params"]["providerAttemptId"] == "compaction:compact-1"
    assert usage[0]["params"]["providerPurpose"] == "compaction"
    assert completed[0]["params"]["status"] == "context_compaction_completed"
    assert completed[0]["params"]["compaction"] == {
        "id": "compact-1",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "maxTokens": 4096,
        "shadowedTokens": 12_000,
        "usage": {
            "input_tokens": 900,
            "output_tokens": 80,
            "total_tokens": 980,
        },
    }
    assert terminal[0]["method"] == "turn/completed"
    encoded = json.dumps(started + usage + completed, ensure_ascii=False)
    assert "summary" not in encoded
    assert "rawOutput" not in encoded


def test_overflow_retry_usage_is_projected_as_a_distinct_provider_attempt():
    projected = _projector().ingest(_frame(
        1,
        "usage",
        payload={
            "input_tokens": 1_000,
            "output_tokens": 120,
            "total_tokens": 1_120,
            "provider_attempt_id": "overflow-retry:request-1",
            "provider_purpose": "overflow-retry",
        },
    ))

    assert projected == [{
        "method": "native/usage",
        "params": {
            "threadId": "conversation-1",
            "turnId": "turn-1",
            "stepId": "step-1",
            "epochId": "epoch-1",
            "usage": {
                "input_tokens": 1_000,
                "output_tokens": 120,
                "total_tokens": 1_120,
            },
            "providerAttemptId": "overflow-retry:request-1",
            "providerPurpose": "overflow-retry",
        },
    }]


def test_context_compaction_failure_closes_lifecycle_and_allows_terminal():
    projector = _projector()
    projector.ingest(_frame(
        1,
        "context.compaction.started",
        payload={"compaction_id": "compact-1"},
    ))
    failed = projector.ingest(_frame(
        2,
        "context.compaction.failed",
        payload={"compaction_id": "compact-1", "code": "compaction_failed"},
    ))
    terminal = projector.ingest(_frame(
        3, "turn.completed", payload={"status": "completed"},
    ))
    projector.finalize()

    assert failed[0]["params"]["status"] == "context_compaction_failed"
    assert terminal[0]["method"] == "turn/completed"


def test_context_compaction_requires_a_balanced_lifecycle():
    projector = _projector()
    _error("compaction_end_without_start", lambda: projector.ingest(_frame(
        1,
        "context.compaction.completed",
        payload={
            "compaction_id": "compact-1",
            "route": {"provider": "deepseek", "model": "deepseek-chat"},
            "usage": None,
            "shadowed_token_count": 10,
        },
    )))

    projector = _projector()
    projector.ingest(_frame(
        1,
        "context.compaction.started",
        payload={"compaction_id": "compact-1"},
    ))
    _error("compaction_lifecycle_incomplete", lambda: projector.ingest(_frame(
        2, "turn.completed", payload={"status": "completed"},
    )))


def test_complete_fake_stream_projects_reasoning_and_clears_local_copy_at_terminal():
    projector = _projector()
    output = []
    output += projector.ingest(_frame(1, "turn.started"))
    output += projector.ingest(_frame(2, "assistant.reasoning.started"))
    output += projector.ingest(_frame(
        3, "assistant.reasoning.delta", payload={"text": "private chain"},
    ))
    output += projector.ingest(_frame(4, "assistant.reasoning.completed"))
    output += projector.ingest(_frame(
        5, "assistant.text.delta", payload={"text": "完成"},
    ))
    output += projector.ingest(_frame(
        6, "assistant.text.completed", payload={"text": "完成"},
    ))
    output += projector.ingest(_frame(
        7,
        "usage",
        payload={"input_tokens": 20, "output_tokens": 5, "total_tokens": 25},
    ))
    output += projector.ingest(_frame(
        8, "turn.completed", payload={"status": "completed"},
    ))
    projector.finalize()

    assert [row["method"] for row in output] == [
        "native/bridge/status",
        "native/reasoning/status",
        "native/reasoning/status",
        "item/agentMessage/delta",
        "native/reasoning/status",
        "item/completed",
        "item/agentMessage/delta",
        "item/completed",
        "native/usage",
        "turn/completed",
    ]
    encoded = json.dumps(output, ensure_ascii=False)
    assert "private chain" in encoded
    reasoning_delta = output[3]
    reasoning_completed = output[5]
    assert reasoning_delta["params"]["itemId"].startswith("reasoning:")
    assert reasoning_completed["params"]["item"] == {
        "type": "reasoning",
        "id": reasoning_delta["params"]["itemId"],
        "text": "private chain",
    }
    assert output[-1]["params"]["turn"]["status"] == "completed"
    snapshot = projector.diagnostic_snapshot()
    assert snapshot["assistant_text_chars"] == 0
    assert snapshot["completed_text_blocks"] == 0
    assert snapshot["reasoning_chars"] == 0
    assert snapshot["terminal_type"] == "turn.completed"


def test_segment_restart_receipt_precedes_terminal_and_preserves_budget_usage():
    projector = _projector()
    usage = {
        "elapsed_seconds": 2,
        "provider_steps": 3,
        "app_tool_calls": 1,
        "web_tool_calls": 0,
        "total_tool_calls": 1,
        "web_search_calls": 0,
        "web_search_queries": 0,
        "web_fetch_calls": 0,
        "read_tool_calls": 0,
        "mutation_tool_calls": 1,
        "cumulative_output_tokens": 50,
        "cumulative_input_tokens": 100,
        "cumulative_uncached_input_tokens": 80,
    }
    continued = projector.ingest(_frame(
        1,
        "turn.segment_restart_required",
        payload={"reason": "stage_context_changed", "budget_usage": usage},
    ))
    terminal = projector.ingest(_frame(
        2, "turn.completed", payload={"status": "completed"},
    ))
    projector.finalize()

    assert continued[0]["method"] == "turn/continued"
    assert terminal[0]["method"] == "turn/completed"
    assert projector.segment_restart_reason == "stage_context_changed"
    assert projector.segment_budget_usage == usage


@pytest.mark.parametrize("ready_first", [False, True])
def test_tool_dispatch_waits_for_both_completed_arguments_and_ready(ready_first):
    calls = []

    def validate(name, arguments):
        calls.append((name, arguments))
        return arguments

    projector = _projector(validate)
    seq = 1
    arguments = {"query": "角色资料"}
    raw, arguments_hash = _arguments(arguments)
    events = []
    if ready_first:
        events += projector.ingest(_frame(
            seq, "tool.call.ready", payload={"tool": "gs_query"}, call_id="call-1",
            canonical_args_hash=arguments_hash,
        ))
        seq += 1
    events += projector.ingest(_frame(
        seq, "tool.arguments.delta", payload={"delta": '{"query":"角色'}, call_id="call-1",
    ))
    seq += 1
    events += projector.ingest(_frame(
        seq, "tool.arguments.delta", payload={"delta": '资料"}'}, call_id="call-1",
    ))
    seq += 1
    events += projector.ingest(_frame(
        seq,
        "tool.arguments.completed",
        payload={"arguments_json": raw},
        call_id="call-1",
        canonical_args_hash=arguments_hash,
    ))
    seq += 1
    if not ready_first:
        events += projector.ingest(_frame(
            seq, "tool.call.ready", payload={"tool": "gs_query"}, call_id="call-1",
            canonical_args_hash=arguments_hash,
        ))

    executable = [row for row in events if row["method"] == "item/tool/call"]
    assert len(executable) == 1
    assert calls == [("gs_query", arguments)]
    assert executable[0]["id"] == "call-1"
    assert executable[0]["params"]["arguments"] == arguments
    assert executable[0]["params"]["canonicalArgsHash"] == arguments_hash
    assert projector.diagnostic_snapshot()["pending_tool_calls"] == 0


def test_partial_arguments_never_become_executable():
    projector = _projector()
    _, arguments_hash = _arguments({"query": "x"})
    output = projector.ingest(_frame(
        1, "tool.call.ready", payload={"tool": "gs_query"}, call_id="call-1",
        canonical_args_hash=arguments_hash,
    ))
    output += projector.ingest(_frame(
        2, "tool.arguments.delta", payload={"delta": '{"query":'}, call_id="call-1",
    ))
    assert all(row["method"] != "item/tool/call" for row in output)
    projector.request_cancel()
    assert projector.diagnostic_snapshot()["pending_tool_calls"] == 1
    assert projector.diagnostic_snapshot()["reasoning_chars"] == 0


def test_argument_completion_must_match_stream_and_declared_hash():
    projector = _projector()
    projector.ingest(_frame(
        1, "tool.arguments.delta", payload={"delta": '{"value":1}'}, call_id="call-1",
    ))
    _error("tool_arguments_mismatch", lambda: projector.ingest(_frame(
        2,
        "tool.arguments.completed",
        payload={"arguments_json": '{"value":2}'},
        call_id="call-1",
        canonical_args_hash=_arguments({"value": 2})[1],
    )))
    assert projector.diagnostic_snapshot()["invalidated"] is True
    _error("projection_invalidated", lambda: projector.ingest(_frame(
        3, "turn.completed", payload={"status": "completed"},
    )))

    projector = _projector()
    _error("tool_arguments_hash_mismatch", lambda: projector.ingest(_frame(
        1,
        "tool.arguments.completed",
        payload={"arguments_json": '{"value":1}'},
        call_id="call-1",
        canonical_args_hash="0" * 64,
    )))


@pytest.mark.parametrize("event_type,payload", [
    ("tool.arguments.completed", {"arguments_json": '{"value":1}'}),
    ("tool.call.ready", {"tool": "gs_query"}),
])
def test_executable_tool_boundaries_require_exact_raw_utf8_hash(event_type, payload):
    _error("missing_tool_arguments_hash", lambda: _projector().ingest(_frame(
        1, event_type, payload=payload, call_id="call-1",
    )))


def test_raw_utf8_hash_does_not_depend_on_python_node_canonical_json_agreement():
    first = '{"x":1.0,"é":-0}'
    second = '{"é":-0,"x":1.0}'
    first_hash = hashlib.sha256(first.encode("utf-8")).hexdigest()
    second_hash = hashlib.sha256(second.encode("utf-8")).hexdigest()
    assert first_hash != second_hash

    projector = _projector()
    projector.ingest(_frame(
        1,
        "tool.arguments.completed",
        payload={"arguments_json": first},
        call_id="call-1",
        canonical_args_hash=first_hash,
    ))
    result = projector.ingest(_frame(
        2,
        "tool.call.ready",
        payload={"tool": "gs_query"},
        call_id="call-1",
        canonical_args_hash=first_hash,
    ))
    call = next(row for row in result if row["method"] == "item/tool/call")
    assert call["params"]["canonicalArgsHash"] == first_hash
    assert call["params"]["arguments"] == {"x": 1.0, "é": 0}


@pytest.mark.parametrize("raw,code", [
    ('{"value":1,"value":2}', "duplicate_tool_argument_key"),
    ('{"value":NaN}', "invalid_tool_argument_number"),
    ('{"value":1e999}', "invalid_tool_argument_number"),
    ('{"value":9007199254740992}', "tool_argument_integer_out_of_range"),
    ('[1,2,3]', "invalid_tool_arguments"),
])
def test_completed_tool_arguments_use_strict_json_parser(raw, code):
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    _error(code, lambda: _projector().ingest(_frame(
        1,
        "tool.arguments.completed",
        payload={"arguments_json": raw},
        call_id="call-1",
        canonical_args_hash=digest,
    )))


def test_tool_schema_rejection_never_emits_executable_event():
    def reject(_name, _arguments):
        raise ValueError("schema detail must not cross boundary")

    projector = _projector(reject)
    raw, arguments_hash = _arguments({"value": 1})
    projector.ingest(_frame(
        1,
        "tool.arguments.completed",
        payload={"arguments_json": raw},
        call_id="call-1",
        canonical_args_hash=arguments_hash,
    ))
    _error("tool_schema_invalid", lambda: projector.ingest(_frame(
        2, "tool.call.ready", payload={"tool": "gs_query"}, call_id="call-1",
        canonical_args_hash=arguments_hash,
    )))
    assert projector.diagnostic_snapshot()["pending_tool_calls"] == 1


def test_schema_validator_cannot_silently_rewrite_signed_arguments():
    projector = _projector(lambda _name, arguments: {**arguments, "default": True})
    raw, arguments_hash = _arguments({"value": 1})
    projector.ingest(_frame(
        1,
        "tool.arguments.completed",
        payload={"arguments_json": raw},
        call_id="call-1",
        canonical_args_hash=arguments_hash,
    ))
    _error("tool_schema_changed_arguments", lambda: projector.ingest(_frame(
        2, "tool.call.ready", payload={"tool": "gs_query"}, call_id="call-1",
        canonical_args_hash=arguments_hash,
    )))


def test_tool_result_requires_prior_dispatch_and_projects_only_safe_summary():
    projector = _projector()
    _error("tool_result_without_dispatch", lambda: projector.ingest(_frame(
        1,
        "tool.result",
        payload={"status": "settled_success", "code": 0},
        call_id="call-1",
    )))

    projector = _projector()
    raw, arguments_hash = _arguments({"value": 1})
    projector.ingest(_frame(
        1,
        "tool.arguments.completed",
        payload={"arguments_json": raw},
        call_id="call-1",
        canonical_args_hash=arguments_hash,
    ))
    projector.ingest(_frame(
        2, "tool.call.ready", payload={"tool": "gs_query"}, call_id="call-1",
        canonical_args_hash=arguments_hash,
    ))
    settlement = _frame(
        3,
        "tool.result",
        payload={
            "status": "settled_success",
            "code": 0,
            "resource_ids": ["resource-1"],
        },
        call_id="call-1",
    )
    result = projector.ingest(settlement)
    assert result == [{
        "method": "native/tool/result",
        "params": {
            "threadId": "conversation-1",
            "turnId": "turn-1",
            "stepId": "step-1",
            "epochId": "epoch-1",
            "callId": "call-1",
            "status": "settled_success",
            "code": 0,
            "resourceIds": ["resource-1"],
        },
    }]
    assert projector.ingest(settlement) == []
    _error("duplicate_tool_settlement", lambda: projector.ingest(_frame(
        4,
        "tool.result",
        payload={"status": "settled_failure", "code": "failed"},
        call_id="call-1",
    )))


@pytest.mark.parametrize("secret", [
    "sk-secret",
    "token:secret",
    "authorization:secret",
])
def test_tool_result_code_cannot_carry_credentials(secret):
    projector = _projector()
    raw, arguments_hash = _arguments({"value": 1})
    projector.ingest(_frame(
        1,
        "tool.arguments.completed",
        payload={"arguments_json": raw},
        call_id="call-1",
        canonical_args_hash=arguments_hash,
    ))
    projector.ingest(_frame(
        2, "tool.call.ready", payload={"tool": "gs_query"}, call_id="call-1",
        canonical_args_hash=arguments_hash,
    ))
    _error("sensitive_identity", lambda: projector.ingest(_frame(
        3,
        "tool.result",
        payload={"status": "settled_failure", "code": secret},
        call_id="call-1",
    )))
    assert projector.diagnostic_snapshot()["invalidated"] is True


@pytest.mark.parametrize("partial", ["arguments", "dispatched"])
def test_completed_turn_rejects_unsettled_tool_lifecycle(partial):
    projector = _projector()
    raw, arguments_hash = _arguments({"value": 1})
    projector.ingest(_frame(
        1,
        "tool.arguments.completed",
        payload={"arguments_json": raw},
        call_id="call-1",
        canonical_args_hash=arguments_hash,
    ))
    if partial == "dispatched":
        projector.ingest(_frame(
            2,
            "tool.call.ready",
            payload={"tool": "gs_query"},
            call_id="call-1",
            canonical_args_hash=arguments_hash,
        ))
        terminal_seq = 3
    else:
        terminal_seq = 2
    _error("tool_lifecycle_incomplete", lambda: projector.ingest(_frame(
        terminal_seq, "turn.completed", payload={"status": "completed"},
    )))
    assert projector.diagnostic_snapshot()["invalidated"] is True


@pytest.mark.parametrize("binding", [
    {"runtime_incarnation": "runtime-2"},
    {"epoch_id": "epoch-2"},
])
def test_runtime_and_epoch_are_fenced_before_sequence_advances(binding):
    projector = _projector()
    _error("frame_binding_mismatch", lambda: projector.ingest(_frame(
        1, "turn.started", **binding,
    )))
    assert projector.diagnostic_snapshot()["next_event_seq"] == 1


def test_tool_call_cannot_cross_step_identity():
    projector = _projector()
    raw, arguments_hash = _arguments({})
    projector.ingest(_frame(
        1, "tool.arguments.completed", payload={"arguments_json": raw},
        call_id="call-1", step_id="step-1",
        canonical_args_hash=arguments_hash,
    ))
    _error("tool_step_mismatch", lambda: projector.ingest(_frame(
        2, "tool.call.ready", payload={"tool": "gs_query"},
        call_id="call-1", step_id="step-2",
        canonical_args_hash=arguments_hash,
    )))


def test_invalidation_clears_volatile_data_and_rejects_all_late_frames():
    projector = _projector()
    projector.ingest(_frame(1, "assistant.reasoning.started"))
    projector.ingest(_frame(
        2, "assistant.reasoning.delta", payload={"text": "temporary"},
    ))
    assert projector.diagnostic_snapshot()["reasoning_chars"] == len("temporary")
    projector.invalidate()
    assert projector.diagnostic_snapshot()["reasoning_chars"] == 0
    _error("projection_invalidated", lambda: projector.ingest(_frame(
        3, "turn.cancelled", payload={"reason": "runtime_replaced"},
    )))


@pytest.mark.parametrize("clear", ["cancel", "invalidate"])
def test_cancel_and_invalidate_release_accumulated_assistant_text(clear):
    projector = _projector()
    projector.ingest(_frame(
        1, "assistant.text.delta", payload={"text": "partial visible reply"},
    ))
    assert projector.diagnostic_snapshot()["assistant_text_chars"] > 0
    if clear == "cancel":
        projector.request_cancel()
    else:
        projector.invalidate()
    snapshot = projector.diagnostic_snapshot()
    assert snapshot["assistant_text_chars"] == 0
    assert snapshot["completed_text_blocks"] == 0


@pytest.mark.parametrize("field,value", [
    ("runtime_incarnation", "sk-secret"),
    ("conversation_id", "token:secret"),
    ("turn_id", "authorization:secret"),
    ("epoch_id", "bearer:secret"),
])
def test_projector_identity_cannot_be_used_as_a_credential_container(field, value):
    arguments = {
        "runtime_incarnation": "runtime-1",
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "epoch_id": "epoch-1",
        "validate_tool_call": lambda _name, args: args,
    }
    arguments[field] = value
    _error("sensitive_identity", lambda: NativeBridgeEventProjector(**arguments))


def test_cancel_fences_new_content_and_tools_but_accepts_usage_and_terminal():
    executed = []
    projector = _projector(lambda name, args: executed.append((name, args)) or args)
    projector.request_cancel()
    assert projector.ingest(_frame(
        1, "assistant.text.delta", payload={"text": "must stay hidden"},
    )) == []
    assert projector.ingest(_frame(
        2,
        "tool.arguments.completed",
        payload={"arguments_json": '{"value":1}'},
        call_id="call-1",
        canonical_args_hash=_arguments({"value": 1})[1],
    )) == []
    assert projector.ingest(_frame(
        3, "tool.call.ready", payload={"tool": "gs_query"}, call_id="call-1",
        canonical_args_hash=_arguments({"value": 1})[1],
    )) == []
    usage = projector.ingest(_frame(
        4, "usage", payload={"total_tokens": 10},
    ))
    terminal = projector.ingest(_frame(
        5, "turn.cancelled", payload={"reason": "user"},
    ))
    projector.finalize()
    assert executed == []
    assert usage[0]["method"] == "native/usage"
    assert terminal[0]["params"]["turn"]["status"] == "interrupted"


def test_duplicate_frames_are_idempotent_and_do_not_duplicate_visible_text():
    projector = _projector()
    frame = _frame(1, "assistant.text.delta", payload={"text": "one"})
    assert len(projector.ingest(frame)) == 1
    assert projector.ingest(frame) == []
    completed = projector.ingest(_frame(
        2, "assistant.text.completed", payload={"text": "one"},
    ))
    assert completed[0]["params"]["item"]["text"] == "one"


@pytest.mark.parametrize("payload", [
    {},
    {"total_tokens": -1},
    {"total_tokens": True},
    {"unknown_tokens": 1},
])
def test_usage_payload_is_fail_closed(payload):
    _error("invalid_usage", lambda: _projector().ingest(_frame(
        1, "usage", payload=payload,
    )))


def test_terminal_error_does_not_project_raw_provider_text():
    projector = _projector()
    projected = projector.ingest(_frame(
        1, "turn.error", payload={"code": "provider_failure"},
    ))
    assert projected[0]["method"] == "error"
    assert projected[0]["params"]["willRetry"] is False
    assert projected[0]["params"]["error"] == {
        "type": "provider_failure",
        "message": "原生Agent运行失败。",
    }
    assert projector.terminal_reason == "provider_failure"
    assert projector.diagnostic_snapshot()["terminal_reason"] == "provider_failure"


def test_cancel_reason_is_preserved_as_safe_code_with_bounded_fallback():
    projector = _projector()
    projector.ingest(_frame(1, "turn.cancelled", payload={}))
    assert projector.terminal_reason == "interrupted"

    projector = _projector()
    with pytest.raises(BridgeProjectionError) as caught:
        projector.ingest(_frame(
            1,
            "turn.error",
            payload={"code": "authorization:Bearer-secret"},
        ))
    assert caught.value.code == "sensitive_identity"
    assert projector.terminal_reason is None


def test_reasoning_lifecycle_is_strict_and_text_never_appears_in_diagnostics():
    projector = _projector()
    _error("reasoning_not_started", lambda: projector.ingest(_frame(
        1, "assistant.reasoning.delta", payload={"text": "secret"},
    )))

    projector = _projector()
    projector.ingest(_frame(1, "assistant.reasoning.started"))
    projector.ingest(_frame(
        2, "assistant.reasoning.delta", payload={"text": "secret"},
    ))
    snapshot = projector.diagnostic_snapshot()
    assert "secret" not in json.dumps(snapshot)
    projector.ingest(_frame(3, "assistant.reasoning.completed"))
    projector.ingest(_frame(4, "turn.completed", payload={"status": "completed"}))
    snapshot = projector.diagnostic_snapshot()
    assert snapshot["reasoning_chars"] == 0
    assert "secret" not in json.dumps(snapshot)


def test_projection_acker_uses_an_independent_control_sequence_and_replays_exact_ack():
    projector = _projector()
    message_ids = iter([
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
    ])
    acker = NativeBridgeProjectionAcker(
        projector, first_control_seq=7, message_id_factory=lambda: next(message_ids),
    )
    first = _frame(1, "turn.started")
    receipt = acker.ingest(first)
    checked = validate_event_ack_frame(receipt.acknowledgement)
    assert receipt.duplicate is False
    assert checked["event_seq"] == 7
    assert checked["payload"] == {"through_event_seq": 1}
    assert checked["runtime_incarnation"] == first["runtime_incarnation"]
    assert checked["conversation_id"] == first["conversation_id"]
    assert checked["turn_id"] == first["turn_id"]
    assert checked["epoch_id"] == first["epoch_id"]

    duplicate = acker.ingest(first)
    assert duplicate.duplicate is True
    assert duplicate.projected == ()
    assert duplicate.acknowledgement == receipt.acknowledgement
    # A caller cannot mutate the cached retry identity.
    duplicate.acknowledgement["payload"]["through_event_seq"] = 999
    assert acker.ingest(first).acknowledgement == receipt.acknowledgement
    assert acker.diagnostic_snapshot() == {
        "next_control_seq": 8,
        "cached_acknowledgements": 1,
    }

    second = acker.ingest(_frame(
        2, "assistant.text.delta", payload={"text": "ok"},
    ))
    assert second.acknowledgement["event_seq"] == 8
    assert second.acknowledgement["payload"] == {"through_event_seq": 2}


def test_projection_acker_returns_no_ack_and_poisons_on_semantic_failure():
    projector = _projector()
    acker = NativeBridgeProjectionAcker(projector)
    _error("invalid_usage", lambda: acker.ingest(_frame(
        1, "usage", payload={"total_tokens": -1},
    )))
    assert acker.diagnostic_snapshot()["cached_acknowledgements"] == 0
    assert projector.diagnostic_snapshot()["invalidated"] is True


def test_projection_acker_poisoned_if_ack_identity_cannot_be_built():
    projector = _projector()
    acker = NativeBridgeProjectionAcker(
        projector, message_id_factory=lambda: "not-a-uuid",
    )
    _error("event_ack_build_failed", lambda: acker.ingest(_frame(1, "turn.started")))
    assert acker.diagnostic_snapshot()["cached_acknowledgements"] == 0
    assert projector.diagnostic_snapshot()["invalidated"] is True


def test_terminal_projection_clears_reasoning_without_putting_it_in_ack_diagnostics():
    projector = _projector()
    ids = (str(uuid.uuid4()) for _ in range(4))
    acker = NativeBridgeProjectionAcker(projector, message_id_factory=lambda: next(ids))
    acker.ingest(_frame(1, "assistant.reasoning.started"))
    acker.ingest(_frame(
        2, "assistant.reasoning.delta", payload={"text": "temporary secret"},
    ))
    acker.ingest(_frame(3, "assistant.reasoning.completed"))
    terminal = acker.ingest(_frame(
        4, "turn.completed", payload={"status": "completed"},
    ))
    assert terminal.acknowledgement["payload"] == {"through_event_seq": 4}
    assert projector.diagnostic_snapshot()["reasoning_chars"] == 0
    assert "temporary secret" not in json.dumps(
        acker.diagnostic_snapshot(), ensure_ascii=False,
    )
