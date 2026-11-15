from __future__ import annotations

import pytest

from genshin_app.agent_chat.native_usage_ledger import NativeProviderUsageLedger
from genshin_app.equipment.schema import PlanError


def _clock():
    return "2026-10-29T12:00:00+00:00"


def test_usage_is_durable_idempotent_and_cumulative_per_attempt(tmp_path):
    ledger = NativeProviderUsageLedger(
        tmp_path,
        conversation_id="conversation-1",
        clock=_clock,
    )
    first = ledger.record(
        turn_id="turn-1",
        epoch_id="epoch-1",
        step_id="step-1",
        usage={"input_tokens": 100, "output_tokens": 5},
    )
    replay = ledger.record(
        turn_id="turn-1",
        epoch_id="epoch-1",
        step_id="step-1",
        usage={"input_tokens": 100, "output_tokens": 5},
    )
    assert replay == first
    assert ledger.read()["revision"] == 1

    later = ledger.record(
        turn_id="turn-1",
        epoch_id="epoch-1",
        step_id="step-1",
        usage={"input_tokens": 100, "output_tokens": 9},
    )
    assert later["observation_count"] == 2
    assert later["usage"]["output_tokens"] == 9
    assert ledger.read()["revision"] == 2

    with pytest.raises(PlanError) as error:
        ledger.record(
            turn_id="turn-1",
            epoch_id="epoch-1",
            step_id="step-1",
            usage={"input_tokens": 99, "output_tokens": 9},
        )
    assert error.value.reason == "provider_usage_regressed"


def test_terminal_without_usage_is_explicitly_unknown_never_zero(tmp_path):
    ledger = NativeProviderUsageLedger(
        tmp_path,
        conversation_id="conversation-1",
        clock=_clock,
    )
    sealed = ledger.seal_turn(
        turn_id="turn-1",
        epoch_id="epoch-1",
        status="error",
    )
    assert sealed["usage_state"] == "unknown"
    assert "usage" not in sealed
    assert ledger.seal_turn(
        turn_id="turn-1",
        epoch_id="epoch-1",
        status="error",
    ) == sealed
    assert ledger.public_turn_usage() == [{
        "turn_id": "turn-1",
        "usage_state": "unknown",
        "cached_input_tokens": None,
        "input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
        "total_tokens": None,
    }]


def test_reported_usage_survives_error_seal(tmp_path):
    ledger = NativeProviderUsageLedger(
        tmp_path,
        conversation_id="conversation-1",
        clock=_clock,
    )
    ledger.record(
        turn_id="turn-1",
        epoch_id="epoch-1",
        step_id="attempt-2",
        usage={"input_tokens": 584_000, "output_tokens": 2_000},
    )
    sealed = ledger.seal_turn(
        turn_id="turn-1",
        epoch_id="epoch-1",
        status="error",
    )
    assert sealed["usage_state"] == "reported"
    assert ledger.read()["attempts"][0]["usage"]["input_tokens"] == 584_000


def test_public_turn_usage_sums_latest_value_of_each_attempt(tmp_path):
    ledger = NativeProviderUsageLedger(
        tmp_path,
        conversation_id="conversation-1",
        clock=_clock,
    )
    ledger.record(
        turn_id="turn-1", epoch_id="epoch-1", step_id="attempt-1",
        usage={"input_tokens": 100, "output_tokens": 5},
    )
    ledger.record(
        turn_id="turn-1", epoch_id="epoch-1", step_id="attempt-1",
        usage={"input_tokens": 120, "output_tokens": 7},
    )
    ledger.record(
        turn_id="turn-1", epoch_id="epoch-1", step_id="attempt-2",
        usage={"input_tokens": 40, "output_tokens": 3},
    )
    assert ledger.public_turn_usage() == [{
        "turn_id": "turn-1",
        "usage_state": "reported",
        "input_tokens": 160,
        "output_tokens": 10,
    }]


def test_physical_provider_attempts_are_distinct_within_one_logical_step(tmp_path):
    ledger = NativeProviderUsageLedger(
        tmp_path,
        conversation_id="conversation-1",
        clock=_clock,
    )
    for attempt_id, purpose, input_tokens, output_tokens in (
        ("agent:request-1", "agent", 100, 5),
        ("compaction:compact-1", "compaction", 90, 7),
        ("overflow-retry:request-1", "overflow-retry", 60, 4),
        ("agent:request-2", "agent", 40, 3),
    ):
        ledger.record(
            turn_id="turn-1",
            epoch_id="epoch-1",
            step_id="step-1",
            provider_attempt_id=attempt_id,
            provider_purpose=purpose,
            usage={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        )
    # Replaying the same cumulative sample is idempotent.
    ledger.record(
        turn_id="turn-1",
        epoch_id="epoch-1",
        step_id="step-1",
        provider_attempt_id="compaction:compact-1",
        provider_purpose="compaction",
        usage={"input_tokens": 90, "output_tokens": 7},
    )

    attempts = ledger.read()["attempts"]
    assert len(attempts) == 4
    assert {row["provider_attempt_id"] for row in attempts} == {
        "agent:request-1",
        "compaction:compact-1",
        "overflow-retry:request-1",
        "agent:request-2",
    }
    assert ledger.public_turn_usage() == [{
        "turn_id": "turn-1",
        "usage_state": "reported",
        "input_tokens": 290,
        "output_tokens": 19,
    }]


def test_turn_with_reported_and_unknown_segments_is_explicitly_partial(tmp_path):
    ledger = NativeProviderUsageLedger(
        tmp_path,
        conversation_id="conversation-1",
        clock=_clock,
    )
    ledger.record(
        turn_id="turn-1", epoch_id="segment-1", step_id="attempt-1",
        usage={"input_tokens": 100, "output_tokens": 5},
    )
    ledger.seal_turn(
        turn_id="turn-1", epoch_id="segment-1", status="completed",
    )
    ledger.seal_turn(
        turn_id="turn-1", epoch_id="segment-2", status="error",
    )

    assert ledger.public_turn_usage() == [{
        "turn_id": "turn-1",
        "usage_state": "partial",
        "input_tokens": 100,
        "output_tokens": 5,
    }]
