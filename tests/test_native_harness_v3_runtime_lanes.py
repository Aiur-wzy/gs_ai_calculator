from __future__ import annotations

import itertools

import pytest

from genshin_app.agent_chat.harness_v3 import (
    CANDIDATE_RESOURCE,
    DOCUMENT_RESOURCE,
    FILE_UI_RESOURCE,
    PROVIDER_RESOURCE,
    DshActivityObservation,
    DurableLaneAdmissionPort,
    DurableSessionBindingRegistry,
    LaneAdmissionPolicy,
    LaneIdentity,
    LaneKey,
    LeaseHolding,
    PublicLaneRequest,
    ResourceLease,
    RuntimeLane,
    RuntimeLaneContractError,
    SessionBindingSnapshot,
    evaluate_lane_admission,
    evaluate_lease_replacement,
    project_runtime_lanes,
    resolve_host_lane_request,
)


def _identity(index: int, *, generation: int = 1) -> LaneIdentity:
    return LaneIdentity(
        conversation_id=f"conversation-{index}",
        dsh_session_id=f"session-{index}",
        generation=generation,
    )


def _binding(index: int, *, generation: int = 1) -> SessionBindingSnapshot:
    return SessionBindingSnapshot(
        identity=_identity(index, generation=generation),
        binding_revision=generation,
    )


def _lease(kind: str, resource_id: str, units: int = 1) -> ResourceLease:
    return ResourceLease(kind, resource_id, units)


def _key(
    index: int,
    *,
    turn: str | None = None,
    generation: int = 1,
    operation: str = "conversation.send",
) -> LaneKey:
    return LaneKey(
        identity=_identity(index, generation=generation),
        turn_id=turn or f"turn-{index}",
        operation=operation,
    )


def _lane(
    index: int,
    *,
    turn: str | None = None,
    generation: int = 1,
    operation: str = "conversation.send",
) -> RuntimeLane:
    return RuntimeLane(
        key=_key(
            index,
            turn=turn,
            generation=generation,
            operation=operation,
        ),
        binding_revision=generation,
        admission_revision=index,
    )


def _resolved(
    index: int,
    *,
    turn: str | None = None,
    generation: int = 1,
    operation: str = "conversation.send",
    leases: tuple[ResourceLease, ...] | None = None,
):
    public = PublicLaneRequest(
        conversation_id=f"conversation-{index}",
        turn_id=turn or f"turn-{index}",
        operation=operation,
    )
    return resolve_host_lane_request(
        public,
        binding=_binding(index, generation=generation),
        required_leases=(
            (_lease(PROVIDER_RESOURCE, "deepseek"),)
            if leases is None
            else leases
        ),
        resolver_id="native-operation-policy",
        policy_revision=1,
    )


def _holding(lane: RuntimeLane, lease: ResourceLease) -> LeaseHolding:
    return LeaseHolding(key=lane.key, lease=lease)


def test_public_request_has_no_session_identity_or_resource_claims():
    request = PublicLaneRequest("conversation-1", "turn-1", "conversation.send")
    assert request.public() == {
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "operation": "conversation.send",
    }
    with pytest.raises(RuntimeLaneContractError, match="not_host_resolved"):
        evaluate_lane_admission([], [], request, LaneAdmissionPolicy())


def test_host_resolution_binds_registry_generation_and_operation_leases():
    request = _resolved(
        1,
        leases=(
            _lease(PROVIDER_RESOURCE, "deepseek"),
            _lease(DOCUMENT_RESOURCE, "document-1"),
        ),
    )
    assert request.binding.public() == {
        "conversation_id": "conversation-1",
        "dsh_session_id": "session-1",
        "generation": 1,
        "binding_revision": 1,
    }
    assert request.resolution.public()["source"] == "host_operation_policy"
    assert request.key.operation == "conversation.send"


def test_projection_is_deterministic_and_keeps_activity_opaque():
    policy = LaneAdmissionPolicy(
        max_active_sessions=3,
        resource_limits={
            PROVIDER_RESOURCE: 3,
            CANDIDATE_RESOURCE: 1,
            DOCUMENT_RESOURCE: 1,
            FILE_UI_RESOURCE: 1,
        },
    )
    first = _lane(1)
    second = _lane(2)
    holdings = [
        _holding(first, _lease(PROVIDER_RESOURCE, "deepseek")),
        _holding(first, _lease(DOCUMENT_RESOURCE, "document-1")),
        _holding(second, _lease(PROVIDER_RESOURCE, "deepseek")),
    ]
    activities = [
        DshActivityObservation(second.key, "upstream/future-state"),
        DshActivityObservation(first.key, "waiting_user"),
    ]

    left = project_runtime_lanes(
        [second, first], reversed(holdings), policy, activities=activities
    )
    right = project_runtime_lanes(
        [first, second], holdings, policy, activities=reversed(activities)
    )

    assert left == right
    assert left["planning_only"] is True
    assert left["atomic_admission_required"] is True
    assert left["admitted_lanes"] == {"count": 2}
    assert {
        key: left["provider_capacity"][key]
        for key in ("used", "limit", "remaining")
    } == {"used": 2, "limit": 3, "remaining": 1}
    assert [
        row["dsh_session_id"] for row in left["provider_capacity"]["holders"]
    ] == ["session-1", "session-2"]
    assert [row["dsh_activity"] for row in left["lanes"]] == [
        "waiting_user", "upstream/future-state",
    ]


def test_exact_replay_is_attach_only_and_cannot_start_provider_again():
    active = _lane(1)
    decision = evaluate_lane_admission(
        [active],
        [_holding(active, _lease(CANDIDATE_RESOURCE, "candidate-1"))],
        _resolved(
            1,
            leases=(
                _lease(PROVIDER_RESOURCE, "deepseek"),
                _lease(CANDIDATE_RESOURCE, "candidate-1"),
            ),
        ),
        LaneAdmissionPolicy(max_active_sessions=1),
    )
    assert decision["decision"] == "already_active"
    assert decision["may_start_provider"] is False
    assert decision["may_attach"] is True
    assert decision["reservation_created"] is False
    assert "allowed" not in decision


def test_same_session_never_admits_a_second_live_turn():
    active = _lane(1)
    decision = evaluate_lane_admission(
        [active],
        [],
        _resolved(1, turn="turn-next"),
        LaneAdmissionPolicy(max_active_sessions=4),
    )
    assert decision["may_start_provider"] is False
    assert decision["may_attach"] is False
    assert "lane_session_turn_busy" in decision["reason_codes"]


def test_same_turn_with_changed_operation_is_not_an_exact_replay():
    active = _lane(1)
    decision = evaluate_lane_admission(
        [active],
        [],
        _resolved(1, operation="turn.retry"),
        LaneAdmissionPolicy(max_active_sessions=1),
    )
    assert decision["reason_codes"] == ["lane_request_replay_conflict"]


def test_different_sessions_are_bounded_by_actual_provider_holdings():
    active = _lane(1)
    holdings = [_holding(active, _lease(PROVIDER_RESOURCE, "deepseek"))]
    accepted = evaluate_lane_admission(
        [active], holdings, _resolved(2), LaneAdmissionPolicy(max_active_sessions=2)
    )
    rejected = evaluate_lane_admission(
        [active], holdings, _resolved(2), LaneAdmissionPolicy(max_active_sessions=1)
    )
    assert accepted["may_start_provider"] is True
    assert accepted["may_attach"] is False
    assert rejected["reason_codes"] == [
        "lane_resource_capacity_exhausted"
    ]


@pytest.mark.parametrize(
    ("kind", "resource_id"),
    [
        (PROVIDER_RESOURCE, "deepseek"),
        (CANDIDATE_RESOURCE, "candidate-shared"),
        (DOCUMENT_RESOURCE, "document-shared"),
        (FILE_UI_RESOURCE, "desktop-file-ui"),
    ],
)
def test_required_resources_are_checked_from_host_resolution(kind, resource_id):
    active = _lane(1)
    holdings = [_holding(active, _lease(kind, resource_id))]
    decision = evaluate_lane_admission(
        [active],
        holdings,
        _resolved(
            2,
            leases=(
                (_lease(kind, resource_id),)
                if kind == PROVIDER_RESOURCE
                else (
                    _lease(PROVIDER_RESOURCE, "deepseek"),
                    _lease(kind, resource_id),
                )
            ),
        ),
        LaneAdmissionPolicy(max_active_sessions=1),
    )
    assert decision["may_start_provider"] is False
    assert decision["reason_codes"] == ["lane_resource_capacity_exhausted"]


def test_resource_capacity_is_per_key_and_extensible_by_policy():
    policy = LaneAdmissionPolicy(
        max_active_sessions=3,
        resource_limits={
            **dict(LaneAdmissionPolicy(max_active_sessions=3).resource_limits),
            "gpu": 2,
        },
    )
    active = _lane(1)
    holdings = [_holding(active, _lease("gpu", "engine-pool"))]
    accepted = evaluate_lane_admission(
        [active],
        holdings,
        _resolved(2, leases=(
            _lease(PROVIDER_RESOURCE, "deepseek"),
            _lease("gpu", "engine-pool"),
        )),
        policy,
    )
    rejected = evaluate_lane_admission(
        [active],
        holdings,
        _resolved(2, leases=(
            _lease(PROVIDER_RESOURCE, "deepseek"),
            _lease("gpu", "engine-pool", 2),
        )),
        policy,
    )
    assert accepted["may_start_provider"] is True
    assert rejected["reason_codes"] == ["lane_resource_capacity_exhausted"]


def test_waiting_user_can_release_provider_and_resume_must_reacquire_it():
    waiting = _lane(1)
    candidate = _lease(CANDIDATE_RESOURCE, "candidate-1")
    holdings = [_holding(waiting, candidate)]
    projection = project_runtime_lanes(
        [waiting],
        holdings,
        LaneAdmissionPolicy(max_active_sessions=2),
        activities=[DshActivityObservation(waiting.key, "waiting_user")],
    )
    plan = evaluate_lease_replacement(
        [waiting],
        holdings,
        waiting.key,
        (candidate, _lease(PROVIDER_RESOURCE, "deepseek")),
        LaneAdmissionPolicy(max_active_sessions=2),
    )
    assert projection["lanes"][0]["dsh_activity"] == "waiting_user"
    assert projection["lanes"][0]["lease_holdings"] == [candidate.public()]
    assert plan["may_update_leases"] is True
    assert plan["planning_only"] is True


def test_waiting_lane_does_not_consume_capacity_and_other_session_can_run():
    waiting = _lane(1)
    holdings = [
        _holding(waiting, _lease(CANDIDATE_RESOURCE, "candidate-1")),
    ]
    policy = LaneAdmissionPolicy(max_active_sessions=1)

    decision = evaluate_lane_admission(
        [waiting], holdings, _resolved(2), policy
    )

    assert decision["may_start_provider"] is True
    assert decision["current"]["admitted_lanes"] == {"count": 1}
    assert decision["current"]["provider_capacity"]["used"] == 0


def test_provider_capacity_setting_cannot_disagree_with_resource_policy():
    with pytest.raises(RuntimeLaneContractError, match="provider_capacity_conflict"):
        LaneAdmissionPolicy(
            max_active_sessions=2,
            resource_limits={
                PROVIDER_RESOURCE: 1,
                CANDIDATE_RESOURCE: 1,
                DOCUMENT_RESOURCE: 1,
                FILE_UI_RESOURCE: 1,
            },
        )


def test_resume_provider_reacquisition_respects_other_lane_holding():
    waiting = _lane(1)
    running = _lane(2)
    holdings = [
        _holding(waiting, _lease(CANDIDATE_RESOURCE, "candidate-1")),
        _holding(running, _lease(PROVIDER_RESOURCE, "deepseek")),
    ]
    plan = evaluate_lease_replacement(
        [waiting, running],
        holdings,
        waiting.key,
        (
            _lease(CANDIDATE_RESOURCE, "candidate-1"),
            _lease(PROVIDER_RESOURCE, "deepseek"),
        ),
        LaneAdmissionPolicy(max_active_sessions=1),
    )
    assert plan["may_update_leases"] is False
    assert plan["reason_codes"] == ["lane_resource_capacity_exhausted"]


def test_two_pure_plans_from_same_snapshot_do_not_claim_atomic_safety():
    policy = LaneAdmissionPolicy(max_active_sessions=1)
    first = evaluate_lane_admission([], [], _resolved(1), policy)
    second = evaluate_lane_admission([], [], _resolved(2), policy)
    assert first["may_start_provider"] is True
    assert second["may_start_provider"] is True
    assert first["reservation_created"] is False
    assert second["reservation_created"] is False
    assert first["atomic_admission_required"] is True
    assert second["atomic_admission_required"] is True


def test_durable_ports_are_explicit_dependencies_not_local_implementations():
    assert DurableSessionBindingRegistry.__protocol_attrs__ == {
        "read_current_binding"
    }
    assert DurableLaneAdmissionPort.__protocol_attrs__ == {
        "evaluate_and_reserve", "release_lane", "replace_lease_holdings",
    }


def test_generation_prevents_aba_attach_to_an_older_live_turn():
    old = _lane(1, generation=1)
    request = _resolved(1, turn="turn-next", generation=2)
    decision = evaluate_lane_admission(
        [old], [], request, LaneAdmissionPolicy(max_active_sessions=2)
    )
    assert set(decision["reason_codes"]) == {
        "lane_generation_conflict", "lane_session_turn_busy",
    }
    assert decision["may_attach"] is False


def test_new_generation_can_start_only_after_old_admission_is_gone():
    decision = evaluate_lane_admission(
        [],
        [],
        _resolved(1, turn="turn-next", generation=2),
        LaneAdmissionPolicy(max_active_sessions=2),
    )
    assert decision["may_start_provider"] is True


def test_invalid_active_facts_and_holdings_fail_closed():
    first = _lane(1)
    duplicate_session = RuntimeLane(
        key=LaneKey(
            LaneIdentity("conversation-2", "session-1", 1),
            "turn-2",
            "conversation.send",
        ),
        binding_revision=1,
        admission_revision=2,
    )
    cases = [
        (
            [first, duplicate_session],
            [],
            LaneAdmissionPolicy(max_active_sessions=2),
            "lane_active_session_duplicate",
        ),
        (
            [first],
            [LeaseHolding(_key(2), _lease(PROVIDER_RESOURCE, "deepseek"))],
            LaneAdmissionPolicy(max_active_sessions=2),
            "lane_lease_holder_missing",
        ),
    ]
    for lanes, holdings, policy, reason in cases:
        with pytest.raises(RuntimeLaneContractError, match=reason):
            project_runtime_lanes(lanes, holdings, policy)


def test_admission_matrix_never_allows_two_turns_for_one_session():
    policy = LaneAdmissionPolicy(
        max_active_sessions=4,
        resource_limits={
            PROVIDER_RESOURCE: 4,
            CANDIDATE_RESOURCE: 4,
            DOCUMENT_RESOURCE: 4,
            FILE_UI_RESOURCE: 4,
        },
    )
    active = _lane(1)
    for turn_id, generation in itertools.product(
        ("turn-1", "turn-next"),
        (1, 2),
    ):
        decision = evaluate_lane_admission(
            [active],
            [],
            _resolved(1, turn=turn_id, generation=generation),
            policy,
        )
        if turn_id == "turn-1" and generation == 1:
            assert decision["decision"] == "already_active"
            assert decision["may_attach"] is True
            assert decision["may_start_provider"] is False
        else:
            assert decision["may_start_provider"] is False
