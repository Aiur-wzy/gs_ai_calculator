"""Fail-closed recovery contracts for App-owned runtime lane admissions.

This module only decides what a future recovery coordinator may do.  It never
mutates the durable lane store and never interprets DSH messages or advances a
DSH Session/Turn lifecycle.  Evidence producers must already have established
the same Turn identity and supplied opaque receipts from their own durable
sources.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from .runtime_lanes import LaneKey, RuntimeLaneContractError

LANE_RECOVERY_EVIDENCE_CONTRACT = "native-harness-lane-recovery-evidence-v1"
LANE_RECOVERY_RECEIPT_CONTRACT = "native-harness-lane-recovery-receipt-v1"

DSH_TURN_ACTIVE = "same_turn_active"
DSH_TURN_DURABLY_TERMINAL = "durably_terminal"
EVIDENCE_UNKNOWN = "unknown"
EVIDENCE_UNREACHABLE = "unreachable"

RUNTIME_INCARNATION_ALIVE = "same_incarnation_alive"
RUNTIME_INCARNATION_DEAD = "incarnation_dead"

RECOVERY_PRESERVE = "preserve"
RECOVERY_RELEASE_PROVIDER = "release_provider"
RECOVERY_RELEASE_LANE = "release_lane"
RECOVERY_FREEZE = "freeze"

type DshTurnRecoveryState = Literal[
    "same_turn_active",
    "durably_terminal",
    "unknown",
    "unreachable",
]
type RuntimeIncarnationRecoveryState = Literal[
    "same_incarnation_alive",
    "incarnation_dead",
    "unknown",
    "unreachable",
]
type LaneRecoveryAction = Literal[
    "preserve",
    "release_provider",
    "release_lane",
    "freeze",
]

_DSH_STATES = {
    DSH_TURN_ACTIVE,
    DSH_TURN_DURABLY_TERMINAL,
    EVIDENCE_UNKNOWN,
    EVIDENCE_UNREACHABLE,
}
_RUNTIME_STATES = {
    RUNTIME_INCARNATION_ALIVE,
    RUNTIME_INCARNATION_DEAD,
    EVIDENCE_UNKNOWN,
    EVIDENCE_UNREACHABLE,
}
_RECOVERY_ACTIONS = {
    RECOVERY_PRESERVE,
    RECOVERY_RELEASE_PROVIDER,
    RECOVERY_RELEASE_LANE,
    RECOVERY_FREEZE,
}
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _identity(value: object, field: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        raise RuntimeLaneContractError(f"lane_recovery_{field}_invalid")
    return value


def _positive_int(value: object, field: str) -> int:
    if type(value) is not int or value < 1:
        raise RuntimeLaneContractError(f"lane_recovery_{field}_invalid")
    return value


def _optional_receipt(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _identity(value, field)


def _evidence_fingerprint(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class LaneRecoveryEvidence:
    """Trusted observations for one exact durable reservation.

    ``durably_terminal`` must come from a durable DSH projection, not from a
    vanished process.  ``incarnation_dead`` only proves that provider capacity
    may be released; it does not prove that the DSH Turn ended.
    """

    key: LaneKey
    expected_reservation_id: str
    expected_admission_revision: int
    dsh_turn_state: DshTurnRecoveryState
    runtime_incarnation_state: RuntimeIncarnationRecoveryState
    dsh_projection_receipt_id: str | None = None
    runtime_probe_receipt_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, LaneKey):
            raise RuntimeLaneContractError("lane_recovery_key_invalid")
        object.__setattr__(
            self,
            "expected_reservation_id",
            _identity(self.expected_reservation_id, "reservation_id"),
        )
        object.__setattr__(
            self,
            "expected_admission_revision",
            _positive_int(
                self.expected_admission_revision,
                "admission_revision",
            ),
        )
        if self.dsh_turn_state not in _DSH_STATES:
            raise RuntimeLaneContractError("lane_recovery_dsh_state_invalid")
        if self.runtime_incarnation_state not in _RUNTIME_STATES:
            raise RuntimeLaneContractError("lane_recovery_runtime_state_invalid")
        dsh_receipt = _optional_receipt(
            self.dsh_projection_receipt_id,
            "dsh_projection_receipt_id",
        )
        runtime_receipt = _optional_receipt(
            self.runtime_probe_receipt_id,
            "runtime_probe_receipt_id",
        )
        object.__setattr__(self, "dsh_projection_receipt_id", dsh_receipt)
        object.__setattr__(self, "runtime_probe_receipt_id", runtime_receipt)
        if self.dsh_turn_state in {
            DSH_TURN_ACTIVE,
            DSH_TURN_DURABLY_TERMINAL,
        } and dsh_receipt is None:
            raise RuntimeLaneContractError(
                "lane_recovery_dsh_projection_receipt_missing"
            )
        if self.runtime_incarnation_state in {
            RUNTIME_INCARNATION_ALIVE,
            RUNTIME_INCARNATION_DEAD,
        } and runtime_receipt is None:
            raise RuntimeLaneContractError(
                "lane_recovery_runtime_probe_receipt_missing"
            )

    def public(self) -> dict[str, Any]:
        return {
            "contract": LANE_RECOVERY_EVIDENCE_CONTRACT,
            "key": self.key.public(),
            "expected_reservation_id": self.expected_reservation_id,
            "expected_admission_revision": self.expected_admission_revision,
            "dsh_turn_state": self.dsh_turn_state,
            "runtime_incarnation_state": self.runtime_incarnation_state,
            "dsh_projection_receipt_id": self.dsh_projection_receipt_id,
            "runtime_probe_receipt_id": self.runtime_probe_receipt_id,
        }


@dataclass(frozen=True, slots=True)
class LaneRecoveryReceipt:
    """Planning-only recovery decision; applying it requires both CAS values."""

    key: LaneKey
    expected_reservation_id: str
    expected_admission_revision: int
    evidence_fingerprint: str
    action: LaneRecoveryAction
    reason_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.key, LaneKey):
            raise RuntimeLaneContractError("lane_recovery_key_invalid")
        object.__setattr__(
            self,
            "expected_reservation_id",
            _identity(self.expected_reservation_id, "reservation_id"),
        )
        object.__setattr__(
            self,
            "expected_admission_revision",
            _positive_int(
                self.expected_admission_revision,
                "admission_revision",
            ),
        )
        if (
            not isinstance(self.evidence_fingerprint, str)
            or _SHA256.fullmatch(self.evidence_fingerprint) is None
        ):
            raise RuntimeLaneContractError(
                "lane_recovery_evidence_fingerprint_invalid"
            )
        if self.action not in _RECOVERY_ACTIONS:
            raise RuntimeLaneContractError("lane_recovery_action_invalid")
        object.__setattr__(
            self,
            "reason_code",
            _identity(self.reason_code, "reason_code"),
        )

    def public(self) -> dict[str, Any]:
        return {
            "contract": LANE_RECOVERY_RECEIPT_CONTRACT,
            "planning_only": True,
            "mutation_applied": False,
            "key": self.key.public(),
            "expected_reservation_id": self.expected_reservation_id,
            "expected_admission_revision": self.expected_admission_revision,
            "evidence_fingerprint": self.evidence_fingerprint,
            "action": self.action,
            "reason_code": self.reason_code,
        }


def evaluate_lane_recovery(
    evidence: LaneRecoveryEvidence,
) -> LaneRecoveryReceipt:
    """Return the narrowest safe action; ambiguous observations freeze."""

    if not isinstance(evidence, LaneRecoveryEvidence):
        raise RuntimeLaneContractError("lane_recovery_evidence_invalid")
    if evidence.dsh_turn_state == DSH_TURN_DURABLY_TERMINAL:
        action: LaneRecoveryAction = RECOVERY_RELEASE_LANE
        reason = "lane_recovery_dsh_turn_durably_terminal"
    elif evidence.dsh_turn_state in {EVIDENCE_UNKNOWN, EVIDENCE_UNREACHABLE}:
        action = RECOVERY_FREEZE
        reason = "lane_recovery_dsh_evidence_inconclusive"
    elif evidence.runtime_incarnation_state == RUNTIME_INCARNATION_DEAD:
        action = RECOVERY_RELEASE_PROVIDER
        reason = "lane_recovery_runtime_incarnation_dead"
    elif evidence.runtime_incarnation_state == RUNTIME_INCARNATION_ALIVE:
        action = RECOVERY_PRESERVE
        reason = "lane_recovery_same_turn_active"
    else:
        action = RECOVERY_FREEZE
        reason = "lane_recovery_runtime_evidence_inconclusive"
    return LaneRecoveryReceipt(
        key=evidence.key,
        expected_reservation_id=evidence.expected_reservation_id,
        expected_admission_revision=evidence.expected_admission_revision,
        evidence_fingerprint=_evidence_fingerprint(evidence.public()),
        action=action,
        reason_code=reason,
    )


@runtime_checkable
class LaneRecoveryDecisionPort(Protocol):
    """Future coordinator seam; decisions remain planning-only here."""

    def evaluate_recovery(
        self,
        evidence: LaneRecoveryEvidence,
    ) -> LaneRecoveryReceipt:
        """Evaluate evidence without mutating DSH or App admission facts."""


__all__ = [
    "DSH_TURN_ACTIVE",
    "DSH_TURN_DURABLY_TERMINAL",
    "EVIDENCE_UNKNOWN",
    "EVIDENCE_UNREACHABLE",
    "LANE_RECOVERY_EVIDENCE_CONTRACT",
    "LANE_RECOVERY_RECEIPT_CONTRACT",
    "RECOVERY_FREEZE",
    "RECOVERY_PRESERVE",
    "RECOVERY_RELEASE_LANE",
    "RECOVERY_RELEASE_PROVIDER",
    "RUNTIME_INCARNATION_ALIVE",
    "RUNTIME_INCARNATION_DEAD",
    "DshTurnRecoveryState",
    "LaneRecoveryAction",
    "LaneRecoveryDecisionPort",
    "LaneRecoveryEvidence",
    "LaneRecoveryReceipt",
    "RuntimeIncarnationRecoveryState",
    "evaluate_lane_recovery",
]
