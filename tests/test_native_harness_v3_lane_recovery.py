from __future__ import annotations

import pytest

from genshin_app.agent_chat.harness_v3.lane_recovery import (
    DSH_TURN_ACTIVE,
    DSH_TURN_DURABLY_TERMINAL,
    EVIDENCE_UNKNOWN,
    EVIDENCE_UNREACHABLE,
    RECOVERY_FREEZE,
    RECOVERY_PRESERVE,
    RECOVERY_RELEASE_LANE,
    RECOVERY_RELEASE_PROVIDER,
    RUNTIME_INCARNATION_ALIVE,
    RUNTIME_INCARNATION_DEAD,
    LaneRecoveryEvidence,
    evaluate_lane_recovery,
)
from genshin_app.agent_chat.harness_v3.runtime_lanes import (
    LaneIdentity,
    LaneKey,
    RuntimeLaneContractError,
)


def _key() -> LaneKey:
    return LaneKey(
        identity=LaneIdentity("conversation-1", "session-1", 1),
        turn_id="turn-1",
        operation="conversation.send",
    )


def _evidence(
    *,
    dsh: str,
    runtime: str,
) -> LaneRecoveryEvidence:
    return LaneRecoveryEvidence(
        key=_key(),
        expected_reservation_id="lane-reservation-1",
        expected_admission_revision=7,
        dsh_turn_state=dsh,
        runtime_incarnation_state=runtime,
        dsh_projection_receipt_id=(
            "dsh-projection-1"
            if dsh in {DSH_TURN_ACTIVE, DSH_TURN_DURABLY_TERMINAL}
            else None
        ),
        runtime_probe_receipt_id=(
            "runtime-probe-1"
            if runtime
            in {RUNTIME_INCARNATION_ALIVE, RUNTIME_INCARNATION_DEAD}
            else None
        ),
    )


@pytest.mark.parametrize(
    ("dsh", "runtime", "expected"),
    [
        (DSH_TURN_ACTIVE, RUNTIME_INCARNATION_ALIVE, RECOVERY_PRESERVE),
        (
            DSH_TURN_ACTIVE,
            RUNTIME_INCARNATION_DEAD,
            RECOVERY_RELEASE_PROVIDER,
        ),
        (
            DSH_TURN_DURABLY_TERMINAL,
            RUNTIME_INCARNATION_ALIVE,
            RECOVERY_RELEASE_LANE,
        ),
        (
            DSH_TURN_DURABLY_TERMINAL,
            EVIDENCE_UNREACHABLE,
            RECOVERY_RELEASE_LANE,
        ),
        (EVIDENCE_UNKNOWN, RUNTIME_INCARNATION_DEAD, RECOVERY_FREEZE),
        (EVIDENCE_UNREACHABLE, RUNTIME_INCARNATION_ALIVE, RECOVERY_FREEZE),
        (DSH_TURN_ACTIVE, EVIDENCE_UNKNOWN, RECOVERY_FREEZE),
    ],
)
def test_recovery_decision_is_narrow_and_fail_closed(dsh, runtime, expected):
    receipt = evaluate_lane_recovery(_evidence(dsh=dsh, runtime=runtime))

    assert receipt.action == expected
    assert receipt.public()["planning_only"] is True
    assert receipt.public()["mutation_applied"] is False


def test_recovery_requires_receipts_for_positive_claims():
    with pytest.raises(RuntimeLaneContractError) as dsh_missing:
        LaneRecoveryEvidence(
            key=_key(),
            expected_reservation_id="lane-reservation-1",
            expected_admission_revision=1,
            dsh_turn_state=DSH_TURN_DURABLY_TERMINAL,
            runtime_incarnation_state=EVIDENCE_UNKNOWN,
        )
    assert dsh_missing.value.reason == (
        "lane_recovery_dsh_projection_receipt_missing"
    )

    with pytest.raises(RuntimeLaneContractError) as runtime_missing:
        LaneRecoveryEvidence(
            key=_key(),
            expected_reservation_id="lane-reservation-1",
            expected_admission_revision=1,
            dsh_turn_state=EVIDENCE_UNKNOWN,
            runtime_incarnation_state=RUNTIME_INCARNATION_DEAD,
        )
    assert runtime_missing.value.reason == (
        "lane_recovery_runtime_probe_receipt_missing"
    )
