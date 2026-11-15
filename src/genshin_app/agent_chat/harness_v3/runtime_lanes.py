"""Pure App admission planning around DSH-owned live Agent state.

DSH is the only authority for Agent, Session, Turn and activity lifecycle.
This module neither mirrors nor advances that state machine.  It provides:

* immutable identities for an already admitted Turn;
* opaque observations copied from DSH for diagnostics only;
* mutable App resource-holding facts kept separate from Turn admission;
* a pure, non-reserving planning function; and
* protocols which require production to perform evaluate-and-reserve in one
  durable single-writer transaction.

Client or model input never supplies resource leases or DSH binding identity.
The Host resolves both from its trusted operation policy and a separate
durable Session-binding registry before calling admission.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Protocol, runtime_checkable

LANE_PROJECTION_CONTRACT = "native-harness-runtime-lanes-v2"
LANE_ADMISSION_CONTRACT = "native-harness-lane-admission-v2"
LANE_LEASE_PLAN_CONTRACT = "native-harness-lane-lease-plan-v1"
LANE_RESERVATION_RECEIPT_CONTRACT = "native-harness-lane-reservation-v2"
LANE_ADMISSION_OUTCOME_CONTRACT = "native-harness-lane-admission-outcome-v1"

PROVIDER_RESOURCE = "provider"
CANDIDATE_RESOURCE = "candidate"
DOCUMENT_RESOURCE = "document"
FILE_UI_RESOURCE = "file_ui"

DEFAULT_RESOURCE_LIMITS = MappingProxyType({
    PROVIDER_RESOURCE: 1,
    CANDIDATE_RESOURCE: 1,
    DOCUMENT_RESOURCE: 1,
    FILE_UI_RESOURCE: 1,
})

_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class RuntimeLaneContractError(ValueError):
    """A malformed fact or impossible active admission projection."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _identity(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        raise RuntimeLaneContractError(f"lane_{field_name}_invalid")
    return value


def _positive_int(value: object, field_name: str) -> int:
    if type(value) is not int or value < 1:
        raise RuntimeLaneContractError(f"lane_{field_name}_invalid")
    return value


def _nonnegative_int(value: object, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise RuntimeLaneContractError(f"lane_{field_name}_invalid")
    return value


def _opaque_activity(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 256
        or any(ord(character) < 0x20 for character in value)
    ):
        raise RuntimeLaneContractError("lane_dsh_activity_invalid")
    return value


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise RuntimeLaneContractError(f"lane_{field_name}_invalid")
    return value


@dataclass(frozen=True, slots=True)
class LaneIdentity:
    """Stable binding for one generation of a durable DSH Session."""

    conversation_id: str
    dsh_session_id: str
    generation: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "conversation_id",
            _identity(self.conversation_id, "conversation_id"),
        )
        object.__setattr__(
            self,
            "dsh_session_id",
            _identity(self.dsh_session_id, "dsh_session_id"),
        )
        object.__setattr__(
            self,
            "generation",
            _positive_int(self.generation, "generation"),
        )

    def public(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "dsh_session_id": self.dsh_session_id,
            "generation": self.generation,
        }


@dataclass(frozen=True, slots=True)
class SessionBindingSnapshot:
    """One read from the separate durable Session-binding registry."""

    identity: LaneIdentity
    binding_revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.identity, LaneIdentity):
            raise RuntimeLaneContractError("lane_binding_identity_invalid")
        object.__setattr__(
            self,
            "binding_revision",
            _nonnegative_int(self.binding_revision, "binding_revision"),
        )

    def public(self) -> dict[str, Any]:
        return {
            **self.identity.public(),
            "binding_revision": self.binding_revision,
        }


@runtime_checkable
class DurableSessionBindingRegistry(Protocol):
    """Dependency owned outside the lane planner.

    A production admission transaction must re-read and compare this binding
    before reserving a lane.  The generation prevents Session-id reuse from
    attaching to an older live Turn (ABA).
    """

    def read_current_binding(self, conversation_id: str) -> SessionBindingSnapshot:
        """Read the current durable binding for one App conversation."""


@dataclass(frozen=True, slots=True)
class PublicLaneRequest:
    """The complete lane request accepted from GUI/CLI/model-facing code."""

    conversation_id: str
    turn_id: str
    operation: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "conversation_id",
            _identity(self.conversation_id, "conversation_id"),
        )
        object.__setattr__(self, "turn_id", _identity(self.turn_id, "turn_id"))
        object.__setattr__(
            self,
            "operation",
            _identity(self.operation, "operation"),
        )

    def public(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "operation": self.operation,
        }


@dataclass(frozen=True, slots=True)
class ResourceLease:
    """One capacity claim against a named App resource key."""

    resource_kind: str
    resource_id: str
    units: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "resource_kind",
            _identity(self.resource_kind, "resource_kind"),
        )
        object.__setattr__(
            self,
            "resource_id",
            _identity(self.resource_id, "resource_id"),
        )
        object.__setattr__(self, "units", _positive_int(self.units, "lease_units"))

    def public(self) -> dict[str, Any]:
        return {
            "resource_kind": self.resource_kind,
            "resource_id": self.resource_id,
            "units": self.units,
        }


def _leases(value: Iterable[ResourceLease]) -> tuple[ResourceLease, ...]:
    rows = tuple(value)
    if any(not isinstance(row, ResourceLease) for row in rows):
        raise RuntimeLaneContractError("lane_resource_lease_invalid")
    identities = [(row.resource_kind, row.resource_id) for row in rows]
    if len(set(identities)) != len(identities):
        raise RuntimeLaneContractError("lane_resource_lease_duplicate")
    return tuple(sorted(rows, key=lambda row: (row.resource_kind, row.resource_id)))


@dataclass(frozen=True, slots=True)
class HostLeaseResolution:
    """Provenance proving leases came from a Host operation resolver."""

    resolver_id: str
    policy_revision: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "resolver_id",
            _identity(self.resolver_id, "resolver_id"),
        )
        object.__setattr__(
            self,
            "policy_revision",
            _positive_int(self.policy_revision, "policy_revision"),
        )

    def public(self) -> dict[str, Any]:
        return {
            "source": "host_operation_policy",
            "resolver_id": self.resolver_id,
            "policy_revision": self.policy_revision,
        }


@dataclass(frozen=True, slots=True)
class ResolvedLaneRequest:
    """Host-resolved request accepted by planning and atomic admission ports."""

    public_request: PublicLaneRequest
    binding: SessionBindingSnapshot
    required_leases: tuple[ResourceLease, ...]
    resolution: HostLeaseResolution

    def __post_init__(self) -> None:
        if not isinstance(self.public_request, PublicLaneRequest):
            raise RuntimeLaneContractError("lane_public_request_invalid")
        if not isinstance(self.binding, SessionBindingSnapshot):
            raise RuntimeLaneContractError("lane_binding_invalid")
        if self.public_request.conversation_id != self.binding.identity.conversation_id:
            raise RuntimeLaneContractError("lane_binding_conversation_conflict")
        if not isinstance(self.resolution, HostLeaseResolution):
            raise RuntimeLaneContractError("lane_host_resolution_missing")
        object.__setattr__(self, "required_leases", _leases(self.required_leases))

    @property
    def key(self) -> LaneKey:
        return LaneKey(
            identity=self.binding.identity,
            turn_id=self.public_request.turn_id,
            operation=self.public_request.operation,
        )

    def public(self) -> dict[str, Any]:
        return {
            "public_request": self.public_request.public(),
            "binding": self.binding.public(),
            "required_leases": [row.public() for row in self.required_leases],
            "resolution": self.resolution.public(),
        }


def resolve_host_lane_request(
    public_request: PublicLaneRequest,
    *,
    binding: SessionBindingSnapshot,
    required_leases: Iterable[ResourceLease],
    resolver_id: str,
    policy_revision: int,
) -> ResolvedLaneRequest:
    """Host-only seam for a trusted operation-to-lease resolver.

    The function validates provenance shape; it is not an authorization
    boundary by itself.  Production must call it only after reading the
    durable binding and deriving leases from an App-owned operation policy.
    """

    return ResolvedLaneRequest(
        public_request=public_request,
        binding=binding,
        required_leases=_leases(required_leases),
        resolution=HostLeaseResolution(
            resolver_id=resolver_id,
            policy_revision=policy_revision,
        ),
    )


@dataclass(frozen=True, slots=True)
class LaneKey:
    """Immutable identity of one admitted DSH Turn."""

    identity: LaneIdentity
    turn_id: str
    operation: str

    def __post_init__(self) -> None:
        if not isinstance(self.identity, LaneIdentity):
            raise RuntimeLaneContractError("lane_identity_invalid")
        object.__setattr__(self, "turn_id", _identity(self.turn_id, "turn_id"))
        object.__setattr__(
            self,
            "operation",
            _identity(self.operation, "operation"),
        )

    def public(self) -> dict[str, Any]:
        return {
            **self.identity.public(),
            "turn_id": self.turn_id,
            "operation": self.operation,
        }


@dataclass(frozen=True, slots=True)
class RuntimeLane:
    """Immutable App admission fact; it contains no Turn lifecycle state."""

    key: LaneKey
    binding_revision: int
    admission_revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.key, LaneKey):
            raise RuntimeLaneContractError("lane_key_invalid")
        object.__setattr__(
            self,
            "binding_revision",
            _nonnegative_int(self.binding_revision, "binding_revision"),
        )
        object.__setattr__(
            self,
            "admission_revision",
            _positive_int(self.admission_revision, "admission_revision"),
        )

    def public(self) -> dict[str, Any]:
        return {
            **self.key.public(),
            "binding_revision": self.binding_revision,
            "admission_revision": self.admission_revision,
        }


@dataclass(frozen=True, slots=True)
class DshActivityObservation:
    """Opaque DSH activity text, projected without App interpretation."""

    key: LaneKey
    activity: str

    def __post_init__(self) -> None:
        if not isinstance(self.key, LaneKey):
            raise RuntimeLaneContractError("lane_key_invalid")
        object.__setattr__(self, "activity", _opaque_activity(self.activity))


@dataclass(frozen=True, slots=True)
class LeaseHolding:
    """Mutable resource ownership fact, separate from immutable admission."""

    key: LaneKey
    lease: ResourceLease

    def __post_init__(self) -> None:
        if not isinstance(self.key, LaneKey):
            raise RuntimeLaneContractError("lane_key_invalid")
        if not isinstance(self.lease, ResourceLease):
            raise RuntimeLaneContractError("lane_resource_lease_invalid")


@dataclass(frozen=True, slots=True)
class LaneAdmissionPolicy:
    """Provider concurrency plus per-resource-key limits.

    ``max_active_sessions`` is retained as the product setting name, but it is
    exactly the provider capacity, not a limit on durable admitted lanes.  A
    supplied provider limit must match it so there is only one truth.
    """

    max_active_sessions: int = 1
    resource_limits: Mapping[str, int] | None = field(default=None)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "max_active_sessions",
            _positive_int(self.max_active_sessions, "max_active_sessions"),
        )
        raw_limits = self.resource_limits
        if raw_limits is None:
            raw_limits = {
                **dict(DEFAULT_RESOURCE_LIMITS),
                PROVIDER_RESOURCE: self.max_active_sessions,
            }
        if not isinstance(raw_limits, Mapping):
            raise RuntimeLaneContractError("lane_resource_limits_invalid")
        normalized: dict[str, int] = {}
        for raw_kind, raw_limit in raw_limits.items():
            kind = _identity(raw_kind, "resource_kind")
            if kind in normalized:
                raise RuntimeLaneContractError("lane_resource_limit_duplicate")
            normalized[kind] = _positive_int(raw_limit, "resource_limit")
        if normalized.get(PROVIDER_RESOURCE) != self.max_active_sessions:
            raise RuntimeLaneContractError("lane_provider_capacity_conflict")
        object.__setattr__(
            self,
            "resource_limits",
            MappingProxyType(dict(sorted(normalized.items()))),
        )

    def public(self) -> dict[str, Any]:
        return {
            "provider_capacity": self.max_active_sessions,
            "resource_limits": {
                kind: limit
                for kind, limit in self.resource_limits.items()
                if kind != PROVIDER_RESOURCE
            },
        }


@dataclass(frozen=True, slots=True)
class LaneReservationReceipt:
    """Receipt returned only by a durable atomic admission implementation."""

    reservation_id: str
    admission_fingerprint: str
    lane: RuntimeLane
    held_leases: tuple[ResourceLease, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reservation_id",
            _identity(self.reservation_id, "reservation_id"),
        )
        object.__setattr__(
            self,
            "admission_fingerprint",
            _sha256(self.admission_fingerprint, "admission_fingerprint"),
        )
        if not isinstance(self.lane, RuntimeLane):
            raise RuntimeLaneContractError("lane_reservation_lane_invalid")
        object.__setattr__(self, "held_leases", _leases(self.held_leases))

    def public(self) -> dict[str, Any]:
        return {
            "contract": LANE_RESERVATION_RECEIPT_CONTRACT,
            "reservation_id": self.reservation_id,
            "admission_fingerprint": self.admission_fingerprint,
            "lane": self.lane.public(),
            "held_leases": [row.public() for row in self.held_leases],
        }


type LaneAdmissionDisposition = Literal["created", "attached"]


@dataclass(frozen=True, slots=True)
class LaneAdmissionOutcome:
    """Atomic call result, separate from the persistent reservation fact.

    Only a newly created reservation can authorize this invocation to start
    its provider.  Attaching after a lost receipt never grants that authority.
    """

    disposition: LaneAdmissionDisposition
    receipt: LaneReservationReceipt
    provider_start_authorized: bool

    def __post_init__(self) -> None:
        if self.disposition not in {"created", "attached"}:
            raise RuntimeLaneContractError("lane_admission_disposition_invalid")
        if not isinstance(self.receipt, LaneReservationReceipt):
            raise RuntimeLaneContractError("lane_admission_receipt_invalid")
        if type(self.provider_start_authorized) is not bool:
            raise RuntimeLaneContractError(
                "lane_admission_provider_authority_invalid"
            )
        holds_provider = any(
            lease.resource_kind == PROVIDER_RESOURCE
            for lease in self.receipt.held_leases
        )
        expected = self.disposition == "created" and holds_provider
        if self.provider_start_authorized is not expected:
            raise RuntimeLaneContractError(
                "lane_admission_provider_authority_invalid"
            )

    def public(self) -> dict[str, Any]:
        return {
            "contract": LANE_ADMISSION_OUTCOME_CONTRACT,
            "disposition": self.disposition,
            "provider_start_authorized": self.provider_start_authorized,
            "receipt": self.receipt.public(),
        }


@runtime_checkable
class DurableLaneAdmissionPort(Protocol):
    """Required production single-writer boundary.

    Implementations must revalidate the SessionBindingSnapshot and perform
    evaluation plus reservation atomically.  Calling the pure planner and
    writing later is not a conforming implementation.
    """

    def evaluate_and_reserve(
        self,
        request: ResolvedLaneRequest,
        *,
        expected_admission_revision: int,
    ) -> LaneAdmissionOutcome:
        """Atomically compare the binding, evaluate policy and reserve."""

    def replace_lease_holdings(
        self,
        key: LaneKey,
        desired_leases: tuple[ResourceLease, ...],
        *,
        expected_reservation_id: str,
        expected_admission_revision: int,
    ) -> LaneReservationReceipt:
        """Atomically release/reacquire holdings with identity plus CAS."""

    def release_lane(
        self,
        key: LaneKey,
        *,
        expected_reservation_id: str,
        expected_admission_revision: int,
    ) -> None:
        """Release one admission and holdings with identity plus CAS."""


def _normalized_lanes(
    active_lanes: Iterable[RuntimeLane],
    policy: LaneAdmissionPolicy,
) -> tuple[RuntimeLane, ...]:
    if not isinstance(policy, LaneAdmissionPolicy):
        raise RuntimeLaneContractError("lane_policy_invalid")
    lanes = tuple(active_lanes)
    if any(not isinstance(lane, RuntimeLane) for lane in lanes):
        raise RuntimeLaneContractError("lane_active_fact_invalid")
    session_ids = [lane.key.identity.dsh_session_id for lane in lanes]
    if len(set(session_ids)) != len(session_ids):
        raise RuntimeLaneContractError("lane_active_session_duplicate")
    conversation_ids = [lane.key.identity.conversation_id for lane in lanes]
    if len(set(conversation_ids)) != len(conversation_ids):
        raise RuntimeLaneContractError("lane_active_conversation_duplicate")
    return tuple(
        sorted(
            lanes,
            key=lambda lane: (
                lane.key.identity.dsh_session_id,
                lane.key.identity.generation,
                lane.key.turn_id,
            ),
        )
    )


def _lane_index(lanes: Iterable[RuntimeLane]) -> dict[LaneKey, RuntimeLane]:
    return {lane.key: lane for lane in lanes}


def _normalized_holdings(
    holdings: Iterable[LeaseHolding],
    lanes: tuple[RuntimeLane, ...],
    policy: LaneAdmissionPolicy,
) -> tuple[LeaseHolding, ...]:
    rows = tuple(holdings)
    if any(not isinstance(row, LeaseHolding) for row in rows):
        raise RuntimeLaneContractError("lane_lease_holding_invalid")
    keys = _lane_index(lanes)
    identities: set[tuple[LaneKey, str, str]] = set()
    for row in rows:
        if row.key not in keys:
            raise RuntimeLaneContractError("lane_lease_holder_missing")
        lease_identity = (
            row.key,
            row.lease.resource_kind,
            row.lease.resource_id,
        )
        if lease_identity in identities:
            raise RuntimeLaneContractError("lane_lease_holding_duplicate")
        identities.add(lease_identity)
        if row.lease.resource_kind not in policy.resource_limits:
            raise RuntimeLaneContractError("lane_active_resource_policy_missing")
    result = tuple(
        sorted(
            rows,
            key=lambda row: (
                row.lease.resource_kind,
                row.lease.resource_id,
                row.key.identity.dsh_session_id,
                row.key.turn_id,
            ),
        )
    )
    usage = _resource_usage(result)
    for (kind, _resource_id), row in usage.items():
        if kind != PROVIDER_RESOURCE and row["used"] > policy.resource_limits[kind]:
            raise RuntimeLaneContractError("lane_active_resource_capacity_violated")
    provider = _provider_usage(result)
    if provider["used"] > policy.max_active_sessions:
        raise RuntimeLaneContractError("lane_provider_capacity_violated")
    return result


def _normalized_activities(
    activities: Iterable[DshActivityObservation],
    lanes: tuple[RuntimeLane, ...],
) -> tuple[DshActivityObservation, ...]:
    rows = tuple(activities)
    if any(not isinstance(row, DshActivityObservation) for row in rows):
        raise RuntimeLaneContractError("lane_dsh_activity_fact_invalid")
    lane_keys = _lane_index(lanes)
    seen: set[LaneKey] = set()
    for row in rows:
        if row.key not in lane_keys:
            raise RuntimeLaneContractError("lane_dsh_activity_lane_missing")
        if row.key in seen:
            raise RuntimeLaneContractError("lane_dsh_activity_duplicate")
        seen.add(row.key)
    return tuple(
        sorted(rows, key=lambda row: row.key.identity.dsh_session_id)
    )


def _resource_usage(
    holdings: Iterable[LeaseHolding],
) -> dict[tuple[str, str], dict[str, Any]]:
    usage: dict[tuple[str, str], dict[str, Any]] = {}
    for holding in holdings:
        lease = holding.lease
        key = (lease.resource_kind, lease.resource_id)
        row = usage.setdefault(key, {"used": 0, "holders": []})
        row["used"] += lease.units
        row["holders"].append({
            **holding.key.public(),
            "units": lease.units,
        })
    return usage


def _provider_usage(holdings: Iterable[LeaseHolding]) -> dict[str, Any]:
    used = 0
    holders = []
    for holding in holdings:
        if holding.lease.resource_kind != PROVIDER_RESOURCE:
            continue
        used += holding.lease.units
        holders.append({
            **holding.key.public(),
            "resource_id": holding.lease.resource_id,
            "units": holding.lease.units,
        })
    return {"used": used, "holders": holders}


def project_runtime_lanes(
    active_lanes: Iterable[RuntimeLane],
    lease_holdings: Iterable[LeaseHolding],
    policy: LaneAdmissionPolicy,
    *,
    activities: Iterable[DshActivityObservation] = (),
) -> dict[str, Any]:
    """Return one deterministic diagnostic projection without reserving."""

    lanes = _normalized_lanes(active_lanes, policy)
    holdings = _normalized_holdings(lease_holdings, lanes, policy)
    activity_rows = _normalized_activities(activities, lanes)
    activity_by_key = {row.key: row.activity for row in activity_rows}
    holdings_by_key: dict[LaneKey, list[dict[str, Any]]] = {
        lane.key: [] for lane in lanes
    }
    for holding in holdings:
        holdings_by_key[holding.key].append(holding.lease.public())
    usage = _resource_usage(holdings)
    provider = _provider_usage(holdings)
    resources = []
    for (kind, resource_id), row in sorted(usage.items()):
        if kind == PROVIDER_RESOURCE:
            continue
        resources.append({
            "resource_kind": kind,
            "resource_id": resource_id,
            "used": row["used"],
            "limit": policy.resource_limits[kind],
            "remaining": policy.resource_limits[kind] - row["used"],
            "holders": sorted(
                row["holders"],
                key=lambda holder: (
                    holder["dsh_session_id"],
                    holder["generation"],
                    holder["turn_id"],
                ),
            ),
        })
    lane_rows = []
    for lane in lanes:
        lane_rows.append({
            **lane.public(),
            "dsh_activity": activity_by_key.get(lane.key),
            "lease_holdings": holdings_by_key[lane.key],
        })
    return {
        "contract": LANE_PROJECTION_CONTRACT,
        "planning_only": True,
        "atomic_admission_required": True,
        "policy": policy.public(),
        "admitted_lanes": {"count": len(lanes)},
        "provider_capacity": {
            "used": provider["used"],
            "limit": policy.max_active_sessions,
            "remaining": policy.max_active_sessions - provider["used"],
            "holders": sorted(
                provider["holders"],
                key=lambda holder: (
                    holder["dsh_session_id"],
                    holder["generation"],
                    holder["turn_id"],
                ),
            ),
        },
        "lanes": lane_rows,
        "resources": resources,
    }


def evaluate_lane_admission(
    active_lanes: Iterable[RuntimeLane],
    lease_holdings: Iterable[LeaseHolding],
    request: ResolvedLaneRequest,
    policy: LaneAdmissionPolicy,
) -> dict[str, Any]:
    """Plan admission; never call a provider or create a reservation."""

    if not isinstance(request, ResolvedLaneRequest):
        raise RuntimeLaneContractError("lane_request_not_host_resolved")
    lanes = _normalized_lanes(active_lanes, policy)
    holdings = _normalized_holdings(lease_holdings, lanes, policy)
    projection = project_runtime_lanes(lanes, holdings, policy)

    exact = next((lane for lane in lanes if lane.key == request.key), None)
    if exact is not None:
        return _decision(request, projection, "already_active", [], [])

    same_identity_turn = next(
        (
            lane
            for lane in lanes
            if lane.key.identity == request.key.identity
            and lane.key.turn_id == request.key.turn_id
        ),
        None,
    )
    if same_identity_turn is not None:
        return _decision(
            request,
            projection,
            "reject",
            ["lane_request_replay_conflict"],
            [{
                "reason_code": "lane_request_replay_conflict",
                **same_identity_turn.key.public(),
            }],
        )

    reasons: list[str] = []
    conflicts: list[dict[str, Any]] = []
    if not any(
        lease.resource_kind == PROVIDER_RESOURCE
        for lease in request.required_leases
    ):
        reasons.append("lane_provider_lease_required")
    same_session = next(
        (
            lane
            for lane in lanes
            if lane.key.identity.dsh_session_id
            == request.key.identity.dsh_session_id
        ),
        None,
    )
    if same_session is not None:
        if (
            same_session.key.identity.conversation_id
            != request.key.identity.conversation_id
        ):
            reasons.append("lane_session_binding_conflict")
        if same_session.key.identity.generation != request.key.identity.generation:
            reasons.append("lane_generation_conflict")
        reasons.append("lane_session_turn_busy")
        conflicts.append({
            "reason_code": "lane_session_turn_busy",
            **same_session.key.public(),
        })
    same_conversation = next(
        (
            lane
            for lane in lanes
            if lane.key.identity.conversation_id
            == request.key.identity.conversation_id
            and lane.key.identity.dsh_session_id
            != request.key.identity.dsh_session_id
        ),
        None,
    )
    if same_conversation is not None:
        reasons.append("lane_conversation_binding_conflict")
        conflicts.append({
            "reason_code": "lane_conversation_binding_conflict",
            **same_conversation.key.public(),
        })
    _append_resource_conflicts(
        request.required_leases,
        holdings,
        policy,
        reasons,
        conflicts,
    )
    unique_reasons = list(dict.fromkeys(reasons))
    return _decision(
        request,
        projection,
        "admit" if not unique_reasons else "reject",
        unique_reasons,
        conflicts,
    )


def evaluate_lease_replacement(
    active_lanes: Iterable[RuntimeLane],
    lease_holdings: Iterable[LeaseHolding],
    key: LaneKey,
    desired_leases: Iterable[ResourceLease],
    policy: LaneAdmissionPolicy,
) -> dict[str, Any]:
    """Plan release/reacquire for a live lane; never mutate holdings."""

    if not isinstance(key, LaneKey):
        raise RuntimeLaneContractError("lane_key_invalid")
    lanes = _normalized_lanes(active_lanes, policy)
    holdings = _normalized_holdings(lease_holdings, lanes, policy)
    if key not in _lane_index(lanes):
        raise RuntimeLaneContractError("lane_lease_holder_missing")
    desired = _leases(desired_leases)
    other_holdings = tuple(row for row in holdings if row.key != key)
    reasons: list[str] = []
    conflicts: list[dict[str, Any]] = []
    _append_resource_conflicts(
        desired,
        other_holdings,
        policy,
        reasons,
        conflicts,
    )
    unique_reasons = list(dict.fromkeys(reasons))
    return {
        "contract": LANE_LEASE_PLAN_CONTRACT,
        "planning_only": True,
        "atomic_admission_required": True,
        "decision": "replace" if not unique_reasons else "reject",
        "may_update_leases": not unique_reasons,
        "reason_codes": unique_reasons,
        "conflicts": conflicts,
        "key": key.public(),
        "desired_leases": [row.public() for row in desired],
        "current": project_runtime_lanes(lanes, holdings, policy),
    }


def _append_resource_conflicts(
    requested: Iterable[ResourceLease],
    holdings: Iterable[LeaseHolding],
    policy: LaneAdmissionPolicy,
    reasons: list[str],
    conflicts: list[dict[str, Any]],
) -> None:
    usage = _resource_usage(holdings)
    for lease in requested:
        limit = policy.resource_limits.get(lease.resource_kind)
        if limit is None:
            reasons.append("lane_resource_policy_missing")
            conflicts.append({
                "reason_code": "lane_resource_policy_missing",
                "resource_kind": lease.resource_kind,
                "resource_id": lease.resource_id,
            })
            continue
        if lease.resource_kind == PROVIDER_RESOURCE:
            row = _provider_usage(holdings)
        else:
            row = usage.get(
                (lease.resource_kind, lease.resource_id),
                {"used": 0, "holders": []},
            )
        if row["used"] + lease.units > limit:
            reasons.append("lane_resource_capacity_exhausted")
            conflicts.append({
                "reason_code": "lane_resource_capacity_exhausted",
                "resource_kind": lease.resource_kind,
                "resource_id": lease.resource_id,
                "requested_units": lease.units,
                "used": row["used"],
                "limit": limit,
                "holders": sorted(
                    row["holders"],
                    key=lambda holder: (
                        holder["dsh_session_id"],
                        holder["generation"],
                        holder["turn_id"],
                    ),
                ),
            })


def _decision(
    request: ResolvedLaneRequest,
    projection: Mapping[str, Any],
    decision: str,
    reason_codes: list[str],
    conflicts: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "contract": LANE_ADMISSION_CONTRACT,
        "planning_only": True,
        "reservation_created": False,
        "atomic_admission_required": True,
        "decision": decision,
        "may_start_provider": decision == "admit" and any(
            lease.resource_kind == PROVIDER_RESOURCE
            for lease in request.required_leases
        ),
        "may_attach": decision == "already_active",
        "reason_codes": reason_codes,
        "conflicts": conflicts,
        "request": request.public(),
        "current": dict(projection),
    }


__all__ = [
    "CANDIDATE_RESOURCE",
    "DEFAULT_RESOURCE_LIMITS",
    "DOCUMENT_RESOURCE",
    "FILE_UI_RESOURCE",
    "LANE_ADMISSION_CONTRACT",
    "LANE_ADMISSION_OUTCOME_CONTRACT",
    "LANE_LEASE_PLAN_CONTRACT",
    "LANE_PROJECTION_CONTRACT",
    "LANE_RESERVATION_RECEIPT_CONTRACT",
    "PROVIDER_RESOURCE",
    "DshActivityObservation",
    "DurableLaneAdmissionPort",
    "DurableSessionBindingRegistry",
    "HostLeaseResolution",
    "LaneAdmissionDisposition",
    "LaneAdmissionOutcome",
    "LaneAdmissionPolicy",
    "LaneIdentity",
    "LaneKey",
    "LaneReservationReceipt",
    "LeaseHolding",
    "PublicLaneRequest",
    "ResolvedLaneRequest",
    "ResourceLease",
    "RuntimeLane",
    "RuntimeLaneContractError",
    "SessionBindingSnapshot",
    "evaluate_lane_admission",
    "evaluate_lease_replacement",
    "project_runtime_lanes",
    "resolve_host_lane_request",
]
