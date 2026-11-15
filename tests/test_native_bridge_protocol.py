from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

import pytest

from genshin_app.agent_chat.bridge_protocol import (
    CONTROL_TYPES,
    EVENT_TYPES,
    JSON_SAFE_INTEGER_MAX,
    MAX_FRAME_BYTES,
    MAX_NESTING_DEPTH,
    BridgeProtocolError,
    TurnFrameValidator,
    canonical_payload_hash,
    decode_frame,
    decode_json_object,
    encode_frame,
    make_frame,
    raw_json_hash,
    validate_event_ack_frame,
)


def _frame(seq=1, frame_type="turn.started", **values):
    payload = values.pop("payload", {"state": "running"})
    defaults = {
        "kind": "event",
        "frame_type": frame_type,
        "message_id": str(uuid.uuid4()),
        "runtime_incarnation": "runtime-1",
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "step_id": "step-1",
        "epoch_id": "epoch-1",
        "event_seq": seq,
        "payload": payload,
    }
    defaults.update(values)
    return make_frame(**defaults)


def _validator():
    return TurnFrameValidator(
        runtime_incarnation="runtime-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        epoch_id="epoch-1",
    )


def _error(code, call):
    with pytest.raises(BridgeProtocolError) as caught:
        call()
    assert caught.value.code == code


def test_payload_hash_is_canonical_and_round_trip_is_strict():
    assert canonical_payload_hash({"b": 2, "a": 1}) == canonical_payload_hash({"a": 1, "b": 2})
    frame = _frame(payload={"text": "中文", "values": [1, True, None]})
    encoded = encode_frame(frame)
    assert decode_frame(encoded) == frame
    assert encoded == encode_frame(decode_frame(encoded))


def test_event_ack_is_an_independent_control_stream_with_exact_payload():
    ack = make_frame(
        kind="control",
        frame_type="events.ack",
        message_id=str(uuid.uuid4()),
        runtime_incarnation="runtime-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        step_id="step-7",
        epoch_id="epoch-1",
        event_seq=3,
        payload={"through_event_seq": 27},
    )
    assert "events.ack" in CONTROL_TYPES
    assert validate_event_ack_frame(ack) == ack
    assert ack["event_seq"] == 3
    assert ack["payload"]["through_event_seq"] == 27


@pytest.mark.parametrize("payload", [
    {},
    {"through_event_seq": 0},
    {"through_event_seq": True},
    {"through_event_seq": 1, "extra": 2},
])
def test_event_ack_payload_fails_closed(payload):
    ack = make_frame(
        kind="control",
        frame_type="events.ack",
        message_id=str(uuid.uuid4()),
        runtime_incarnation="runtime-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        step_id="step-1",
        epoch_id="epoch-1",
        event_seq=1,
        payload=payload,
    )
    expected = "invalid_event_ack_payload" if set(payload) != {"through_event_seq"} else (
        "invalid_event_ack_sequence"
    )
    _error(expected, lambda: validate_event_ack_frame(ack))


def test_node_and_python_hash_contract_share_checked_in_golden_vectors():
    vectors = json.loads(
        (Path(__file__).parents[1] / "agent-runtime" / "bridge-vectors.json")
        .read_text(encoding="utf-8"))
    for vector in vectors["payloads"]:
        assert canonical_payload_hash(vector["value"]) == vector["sha256"]
    for vector in vectors["raw_arguments"]:
        assert raw_json_hash(vector["raw"]) == vector["sha256"]


def test_duplicate_keys_and_non_finite_numbers_are_rejected_before_validation():
    _error("duplicate_json_key", lambda: decode_frame('{"a":1,"a":2}'))
    _error("duplicate_json_key", lambda: decode_frame('{"a":{"x":1,"x":2}}'))
    _error("invalid_json_number", lambda: decode_frame('{"value":NaN}'))
    _error("invalid_json_number", lambda: decode_frame('{"value":Infinity}'))
    _error("invalid_json_number", lambda: decode_frame('{"value":1e999}'))


def test_size_and_nesting_limits_are_enforced_for_raw_and_decoded_frames():
    _error("frame_too_large", lambda: decode_frame(b" " * (MAX_FRAME_BYTES + 1)))
    payload = {}
    cursor = payload
    for _ in range(MAX_NESTING_DEPTH + 1):
        cursor["child"] = {}
        cursor = cursor["child"]
    _error("frame_too_deep", lambda: _frame(payload=payload))


def test_direct_python_values_cannot_bypass_strict_json_shape():
    _error("invalid_json_key", lambda: canonical_payload_hash({1: "numeric key"}))
    _error("invalid_json_value", lambda: canonical_payload_hash({"tuple": (1, 2)}))
    _error("invalid_json_value", lambda: canonical_payload_hash({"text": "\ud800"}))
    _error("invalid_protocol_key", lambda: canonical_payload_hash({"中文": "value"}))
    _error("non_integer_protocol_number", lambda: canonical_payload_hash({"value": 1.0}))


def test_tool_arguments_hash_exact_utf8_and_parse_only_after_completion():
    raw = '{"倍率":1.25,"hits":[1,2,3],"label":"冰伤"}'
    assert raw_json_hash(raw) == raw_json_hash(raw)
    assert raw_json_hash(raw) != raw_json_hash(raw + " ")
    assert decode_json_object(raw) == {
        "倍率": 1.25, "hits": [1, 2, 3], "label": "冰伤"}
    _error("duplicate_json_key", lambda: decode_json_object('{"x":1,"x":2}'))
    _error("raw_json_not_object", lambda: decode_json_object("[]"))
    _error("json_integer_out_of_range", lambda: decode_json_object(
        '{"value":9007199254740992}'))


@pytest.mark.parametrize("value", [
    JSON_SAFE_INTEGER_MAX + 1,
    -JSON_SAFE_INTEGER_MAX - 1,
])
def test_nested_json_integers_must_fit_javascript_safe_range(value):
    _error(
        "json_integer_out_of_range",
        lambda: canonical_payload_hash({"outer": [{"value": value}]}),
    )
    raw = '{"outer":{"value":' + str(value) + "}}"
    _error("json_integer_out_of_range", lambda: decode_frame(raw))


def test_safe_integer_boundaries_are_accepted_in_payloads():
    assert canonical_payload_hash({
        "positive": JSON_SAFE_INTEGER_MAX,
        "negative": -JSON_SAFE_INTEGER_MAX,
    })


@pytest.mark.parametrize("field,value,code", [
    ("message_id", "not-a-uuid", "invalid_message_id"),
    ("runtime_incarnation", "bad identity!", "invalid_identity"),
    ("event_seq", True, "invalid_event_sequence"),
])
def test_identity_and_sequence_fields_are_strict(field, value, code):
    _error(code, lambda: _frame(**{field: value}))


@pytest.mark.parametrize("message_id", [
    "00000000-0000-0000-0000-000000000000",
    "00000000-0000-6000-8000-000000000000",
])
def test_message_id_matches_node_rfc4122_v1_to_v5_contract(message_id):
    _error("invalid_message_id", lambda: _frame(message_id=message_id))


def test_event_sequence_and_revisions_reject_unsafe_integers():
    _error(
        "json_integer_out_of_range",
        lambda: _frame(event_seq=JSON_SAFE_INTEGER_MAX + 1),
    )
    _error(
        "json_integer_out_of_range",
        lambda: _frame(candidate_revision=JSON_SAFE_INTEGER_MAX + 1),
    )
    _error("invalid_event_sequence", lambda: TurnFrameValidator(
        runtime_incarnation="runtime-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        epoch_id="epoch-1",
        first_event_seq=JSON_SAFE_INTEGER_MAX + 1,
    ))


def test_unknown_fields_types_and_payload_hashes_fail_closed():
    frame = _frame()
    frame["unexpected"] = True
    _error("unknown_frame_field", lambda: encode_frame(frame))
    frame = _frame()
    frame["type"] = "assistant.unknown"
    _error("unknown_frame_type", lambda: encode_frame(frame))
    frame = _frame()
    frame["payload"]["state"] = "changed"
    _error("payload_hash_mismatch", lambda: encode_frame(frame))


def test_request_and_tool_frames_require_their_correlation_ids():
    _error("missing_request_id", lambda: make_frame(
        kind="request", frame_type="turn.start", message_id=str(uuid.uuid4()),
        runtime_incarnation="runtime-1", conversation_id="conversation-1",
        turn_id="turn-1", step_id="step-1", epoch_id="epoch-1",
        event_seq=1, payload={},
    ))
    _error("missing_call_id", lambda: _frame(frame_type="tool.call.ready"))
    frame = _frame(frame_type="tool.call.ready", call_id="call-1")
    assert frame["call_id"] == "call-1"
    question = _frame(
        frame_type="user.question.opened",
        call_id="call-question-1",
        payload={"batch_id": "call-question-1", "questions": []},
    )
    assert question["call_id"] == "call-question-1"
    answer = make_frame(
        kind="request",
        frame_type="question.answer",
        message_id=str(uuid.uuid4()),
        runtime_incarnation="runtime-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        step_id="step-1",
        epoch_id="epoch-1",
        event_seq=1,
        payload={"answers": []},
        request_id="request-question-1",
        call_id="call-question-1",
    )
    assert answer["request_id"] == "request-question-1"
    assert answer["call_id"] == "call-question-1"
    resume = make_frame(
        kind="request",
        frame_type="question.resume",
        message_id=str(uuid.uuid4()),
        runtime_incarnation="runtime-1",
        conversation_id="conversation-1",
        turn_id="turn-2",
        step_id="step-start",
        epoch_id="epoch-2",
        event_seq=2,
        payload={"answers": []},
        request_id="request-question-resume-1",
        call_id="call-question-1",
    )
    assert resume["call_id"] == "call-question-1"
    suspended = _frame(
        frame_type="user.question.suspended",
        call_id="call-question-1",
        payload={"session_ref": "a" * 64, "event_count": 4},
    )
    assert suspended["payload"]["event_count"] == 4


def test_full_stream_event_catalog_is_explicit():
    assert {
        "turn.started", "turn.status", "step.started", "step.status", "step.completed",
        "assistant.text.delta", "assistant.text.completed",
        "assistant.reasoning.started", "assistant.reasoning.delta",
        "assistant.reasoning.completed", "tool.arguments.delta",
        "tool.arguments.completed", "tool.call.ready", "tool.result",
        "user.question.opened", "user.question.suspended",
        "context.compaction.started", "context.compaction.completed",
        "context.compaction.failed",
        "usage", "turn.completed", "turn.cancelled", "turn.error",
        "turn.segment_restart_required",
    } == EVENT_TYPES


def test_turn_validator_accepts_ordered_stream_and_one_terminal():
    validator = _validator()
    assert validator.accept(_frame(1)).status == "accepted"
    assert validator.accept(_frame(2, "assistant.text.delta", payload={"text": "完成"})).status == "accepted"
    terminal = validator.accept(_frame(3, "turn.completed", payload={"status": "completed"}))
    assert terminal.status == "accepted"
    assert terminal.terminal_type == "turn.completed"
    assert validator.finalize().status == "complete"


def test_turn_validator_requires_a_terminal_when_stream_is_finalized():
    validator = _validator()
    validator.accept(_frame(1))
    _error("missing_terminal_event", validator.finalize)


@pytest.mark.parametrize("seq", [1, 3])
def test_turn_validator_rejects_duplicate_sequence_or_gap(seq):
    validator = _validator()
    validator.accept(_frame(1))
    _error("event_sequence_mismatch", lambda: validator.accept(
        _frame(seq, "assistant.text.delta", payload={"text": "x"})))


def test_exact_duplicate_message_is_idempotent_but_changed_content_conflicts():
    validator = _validator()
    frame = _frame(1)
    assert validator.accept(frame).status == "accepted"
    assert validator.accept(copy.deepcopy(frame)).status == "duplicate"
    changed = copy.deepcopy(frame)
    changed["payload"] = {"state": "different"}
    changed["payload_hash"] = canonical_payload_hash(changed["payload"])
    _error("message_id_conflict", lambda: validator.accept(changed))
    rebound = copy.deepcopy(frame)
    rebound["turn_id"] = "other-turn"
    _error("message_id_conflict", lambda: validator.accept(rebound))


def test_binding_mismatch_is_rejected_before_a_frame_can_advance_sequence():
    validator = _validator()
    _error("frame_binding_mismatch", lambda: validator.accept(
        _frame(1, turn_id="other-turn")))
    assert validator.accept(_frame(1)).status == "accepted"


def test_turn_validator_only_accepts_event_frames():
    validator = _validator()
    _error("invalid_event_frame", lambda: validator.accept(make_frame(
        kind="request", frame_type="turn.start", message_id=str(uuid.uuid4()),
        runtime_incarnation="runtime-1", conversation_id="conversation-1",
        turn_id="turn-1", step_id="step-1", epoch_id="epoch-1",
        event_seq=1, payload={}, request_id="request-1",
    )))
    assert validator.accept(_frame(1)).status == "accepted"


def test_late_display_delta_is_ignored_but_late_critical_event_fails():
    validator = _validator()
    validator.accept(_frame(1, "turn.completed", payload={"status": "completed"}))
    late = validator.accept(_frame(2, "assistant.text.delta", payload={"text": "late"}))
    assert late.status == "ignored_late"
    assert validator.ignored_late_events == 1
    critical = _frame(3, "usage", payload={"total_tokens": 10})
    _error("late_critical_event", lambda: validator.accept(critical))
    _error("late_critical_event", lambda: validator.accept(critical))


def test_late_noncritical_events_remain_strictly_contiguous():
    validator = _validator()
    validator.accept(_frame(1, "turn.completed", payload={"status": "completed"}))
    gap = _frame(3, "assistant.text.delta", payload={"text": "gap"})
    _error("event_sequence_mismatch", lambda: validator.accept(gap))
    late = _frame(2, "assistant.reasoning.delta", payload={"text": "late"})
    assert validator.accept(late).status == "ignored_late"
    assert validator.next_event_seq == 3
    assert validator.accept(copy.deepcopy(late)).status == "duplicate"
    stale = _frame(2, "turn.status", payload={"state": "late"})
    _error("event_sequence_mismatch", lambda: validator.accept(stale))
    assert validator.accept(
        _frame(3, "assistant.text.delta", payload={"text": "next"}),
    ).status == "ignored_late"
    assert validator.ignored_late_events == 2


def test_terminal_duplicate_remains_idempotent_and_second_terminal_is_rejected():
    validator = _validator()
    terminal = _frame(1, "turn.cancelled", payload={"reason": "user"})
    validator.accept(terminal)
    assert validator.accept(copy.deepcopy(terminal)).status == "duplicate"
    _error("late_critical_event", lambda: validator.accept(
        _frame(2, "turn.error", payload={"code": "late"})))


def test_turn_validator_bounds_unique_frame_count_and_total_bytes():
    by_count = TurnFrameValidator(
        runtime_incarnation="runtime-1", conversation_id="conversation-1",
        turn_id="turn-1", epoch_id="epoch-1", max_frames=1,
    )
    first = _frame(1)
    by_count.accept(first)
    assert by_count.accept(copy.deepcopy(first)).status == "duplicate"
    _error("turn_frame_limit", lambda: by_count.accept(_frame(2)))

    first_size = len(encode_frame(_frame(1)))
    by_bytes = TurnFrameValidator(
        runtime_incarnation="runtime-1", conversation_id="conversation-1",
        turn_id="turn-1", epoch_id="epoch-1", max_bytes=first_size,
    )
    frame = _frame(1)
    # UUID lengths are fixed, so frames with the same fields have equal size.
    by_bytes.accept(frame)
    _error("turn_byte_limit", lambda: by_bytes.accept(_frame(2)))


def test_non_object_or_invalid_utf8_never_reaches_envelope_validation():
    _error("frame_not_object", lambda: decode_frame("[]"))
    _error("invalid_utf8", lambda: decode_frame(b"\xff"))
    _error("invalid_json", lambda: decode_frame("{"))


def test_encoded_frame_never_contains_nan_or_python_only_values():
    _error("invalid_json_number", lambda: canonical_payload_hash({"bad": float("nan")}))
    _error("invalid_json_value", lambda: canonical_payload_hash({"bad": object()}))
    assert json.loads(encode_frame(_frame()).decode("utf-8"))["protocol"] == "genshin-native-bridge"
