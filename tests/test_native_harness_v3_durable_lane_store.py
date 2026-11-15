from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from genshin_app.agent_chat.harness_v3.durable_lane_store import (
    DurableLaneAdmissionStore,
    NativeDshLaneBindingRegistry,
)
from genshin_app.agent_chat.harness_v3.runtime_lanes import (
    CANDIDATE_RESOURCE,
    PROVIDER_RESOURCE,
    LaneAdmissionPolicy,
    LaneIdentity,
    PublicLaneRequest,
    ResourceLease,
    SessionBindingSnapshot,
    resolve_host_lane_request,
)
from genshin_app.agent_chat.native_dsh_session_binding import (
    NativeDshSessionBindingStore,
)
from genshin_app.agent_chat.native_session_store import create_native_session
from genshin_app.equipment.schema import PlanError

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
FENCE = "wf_" + "a" * 43


class _FakeBindings:
    def __init__(self, rows: dict[str, SessionBindingSnapshot]) -> None:
        self.rows = rows
        self._lock = threading.RLock()

    @contextmanager
    def locked_current_binding(self, conversation_id: str):
        with self._lock:
            yield self.rows[conversation_id]


def _binding(index: int, *, revision: int = 1) -> SessionBindingSnapshot:
    return SessionBindingSnapshot(
        identity=LaneIdentity(
            conversation_id=f"conversation-{index}",
            dsh_session_id=f"session-{index}",
            generation=1,
        ),
        binding_revision=revision,
    )


def _request(
    index: int,
    binding: SessionBindingSnapshot,
    *,
    candidate: bool = False,
    resolver_id: str = "native-operation-policy",
    policy_revision: int = 1,
):
    leases = [ResourceLease(PROVIDER_RESOURCE, "deepseek")]
    if candidate:
        leases.append(ResourceLease(CANDIDATE_RESOURCE, f"candidate-{index}"))
    return resolve_host_lane_request(
        PublicLaneRequest(
            conversation_id=f"conversation-{index}",
            turn_id=f"turn-{index}",
            operation="conversation.send",
        ),
        binding=binding,
        required_leases=leases,
        resolver_id=resolver_id,
        policy_revision=policy_revision,
    )


def _store(tmp_path, bindings: _FakeBindings, *, capacity: int = 1):
    return DurableLaneAdmissionStore(
        tmp_path,
        policy=LaneAdmissionPolicy(max_active_sessions=capacity),
        binding_registry=bindings,
    )


def test_atomic_reservation_is_durable_and_exact_replay_never_restarts(tmp_path):
    binding = _binding(1)
    store = _store(tmp_path, _FakeBindings({"conversation-1": binding}))
    request = _request(1, binding, candidate=True)

    created = store.evaluate_and_reserve(request, expected_admission_revision=0)
    attached = store.evaluate_and_reserve(request, expected_admission_revision=0)
    first = created.receipt
    reopened = _store(
        tmp_path, _FakeBindings({"conversation-1": binding})
    ).cold_snapshot()

    assert created.disposition == "created"
    assert created.provider_start_authorized is True
    assert attached.disposition == "attached"
    assert attached.provider_start_authorized is False
    assert attached.receipt == first
    assert first.lane.admission_revision == 1
    assert reopened["ledger_revision"] == 1
    assert reopened["reservations"] == [first.public()]
    assert "disposition" not in reopened["reservations"][0]
    assert "provider_start_authorized" not in reopened["reservations"][0]
    assert reopened["projection"]["provider_capacity"]["used"] == 1


def test_waiting_lane_releases_provider_and_resume_reacquires_atomically(tmp_path):
    one = _binding(1)
    two = _binding(2)
    bindings = _FakeBindings({"conversation-1": one, "conversation-2": two})
    store = _store(tmp_path, bindings)
    first = store.evaluate_and_reserve(
        _request(1, one, candidate=True), expected_admission_revision=0
    ).receipt
    candidate_only = (ResourceLease(CANDIDATE_RESOURCE, "candidate-1"),)
    waiting = store.replace_lease_holdings(
        first.lane.key,
        candidate_only,
        expected_reservation_id=first.reservation_id,
        expected_admission_revision=1,
    )

    second = store.evaluate_and_reserve(
        _request(2, two), expected_admission_revision=0
    ).receipt
    with pytest.raises(PlanError) as caught:
        store.replace_lease_holdings(
            waiting.lane.key,
            (
                *candidate_only,
                ResourceLease(PROVIDER_RESOURCE, "deepseek"),
            ),
            expected_reservation_id=waiting.reservation_id,
            expected_admission_revision=2,
        )
    assert caught.value.reason == "lane_resource_capacity_exhausted"

    store.release_lane(
        second.lane.key,
        expected_reservation_id=second.reservation_id,
        expected_admission_revision=second.lane.admission_revision,
    )
    resumed = store.replace_lease_holdings(
        waiting.lane.key,
        (
            *candidate_only,
            ResourceLease(PROVIDER_RESOURCE, "deepseek"),
        ),
        expected_reservation_id=waiting.reservation_id,
        expected_admission_revision=2,
    )
    assert resumed.lane.admission_revision == 5
    assert store.cold_snapshot()["projection"]["provider_capacity"]["used"] == 1


def test_binding_is_reread_and_new_admission_rejects_stale_revision(tmp_path):
    first = _binding(1, revision=1)
    bindings = _FakeBindings({"conversation-1": first})
    store = _store(tmp_path, bindings)
    stale_request = _request(1, first)
    bindings.rows["conversation-1"] = _binding(1, revision=2)

    with pytest.raises(PlanError) as caught:
        store.evaluate_and_reserve(
            stale_request, expected_admission_revision=0
        )
    assert caught.value.reason == "stale_context"
    assert store.cold_snapshot()["reservations"] == []


def test_exact_replay_survives_domain_only_binding_revision_advance(tmp_path):
    first = _binding(1, revision=1)
    bindings = _FakeBindings({"conversation-1": first})
    store = _store(tmp_path, bindings)
    request = _request(1, first)
    receipt = store.evaluate_and_reserve(
        request, expected_admission_revision=0
    ).receipt
    advanced = _binding(1, revision=2)
    bindings.rows["conversation-1"] = advanced

    replay = store.evaluate_and_reserve(
        _request(1, advanced), expected_admission_revision=0
    )
    assert replay.disposition == "attached"
    assert replay.provider_start_authorized is False
    assert replay.receipt == receipt
    assert store.cold_snapshot()["ledger_revision"] == 1


@pytest.mark.parametrize(
    "changed_request",
    [
        {"candidate": True},
        {"resolver_id": "replacement-operation-policy"},
        {"policy_revision": 2},
    ],
)
def test_same_key_replay_requires_identical_immutable_admission_intent(
    tmp_path,
    changed_request,
):
    binding = _binding(1)
    store = _store(tmp_path, _FakeBindings({"conversation-1": binding}))
    receipt = store.evaluate_and_reserve(
        _request(1, binding), expected_admission_revision=0
    ).receipt

    with pytest.raises(PlanError) as caught:
        store.evaluate_and_reserve(
            _request(1, binding, **changed_request),
            expected_admission_revision=0,
        )

    assert caught.value.reason == "lane_request_replay_conflict"
    assert store.cold_snapshot()["reservations"] == [receipt.public()]


def test_recreated_same_key_rejects_late_release_and_replace(tmp_path):
    binding = _binding(1)
    store = _store(tmp_path, _FakeBindings({"conversation-1": binding}))
    request = _request(1, binding)
    old = store.evaluate_and_reserve(
        request, expected_admission_revision=0
    ).receipt
    store.release_lane(
        old.lane.key,
        expected_reservation_id=old.reservation_id,
        expected_admission_revision=old.lane.admission_revision,
    )
    current = store.evaluate_and_reserve(
        request,
        expected_admission_revision=0,
    ).receipt

    assert current.reservation_id != old.reservation_id
    assert current.lane.admission_revision == 3
    with pytest.raises(PlanError) as late_release:
        store.release_lane(
            old.lane.key,
            expected_reservation_id=old.reservation_id,
            expected_admission_revision=old.lane.admission_revision,
        )
    assert late_release.value.reason == "stale_context"
    with pytest.raises(PlanError) as late_replace:
        store.replace_lease_holdings(
            old.lane.key,
            (),
            expected_reservation_id=old.reservation_id,
            expected_admission_revision=old.lane.admission_revision,
        )
    assert late_replace.value.reason == "stale_context"
    assert store.cold_snapshot()["reservations"] == [current.public()]


def test_lease_replacement_preserves_admission_fingerprint(tmp_path):
    binding = _binding(1)
    store = _store(tmp_path, _FakeBindings({"conversation-1": binding}))
    request = _request(1, binding, candidate=True)
    receipt = store.evaluate_and_reserve(
        request, expected_admission_revision=0
    ).receipt

    replaced = store.replace_lease_holdings(
        receipt.lane.key,
        (ResourceLease(CANDIDATE_RESOURCE, "candidate-1"),),
        expected_reservation_id=receipt.reservation_id,
        expected_admission_revision=receipt.lane.admission_revision,
    )

    assert replaced.admission_fingerprint == receipt.admission_fingerprint
    assert replaced.held_leases != receipt.held_leases
    replay = store.evaluate_and_reserve(
        request,
        expected_admission_revision=0,
    )
    assert replay.disposition == "attached"
    assert replay.provider_start_authorized is False
    assert replay.receipt == replaced
    assert store.cold_snapshot()["projection"]["provider_capacity"]["used"] == 0


def test_admission_revision_is_a_per_lane_cas_and_release_is_idempotent(tmp_path):
    binding = _binding(1)
    store = _store(tmp_path, _FakeBindings({"conversation-1": binding}))
    receipt = store.evaluate_and_reserve(
        _request(1, binding), expected_admission_revision=0
    ).receipt
    with pytest.raises(PlanError) as caught:
        store.replace_lease_holdings(
            receipt.lane.key,
            (),
            expected_reservation_id=receipt.reservation_id,
            expected_admission_revision=2,
        )
    assert caught.value.reason == "stale_context"
    with pytest.raises(PlanError):
        store.release_lane(
            receipt.lane.key,
            expected_reservation_id=receipt.reservation_id,
            expected_admission_revision=2,
        )
    store.release_lane(
        receipt.lane.key,
        expected_reservation_id=receipt.reservation_id,
        expected_admission_revision=1,
    )
    store.release_lane(
        receipt.lane.key,
        expected_reservation_id=receipt.reservation_id,
        expected_admission_revision=1,
    )
    assert store.cold_snapshot()["reservations"] == []


def test_two_concurrent_empty_snapshot_requests_cannot_both_reserve(tmp_path):
    one = _binding(1)
    two = _binding(2)
    bindings = _FakeBindings({"conversation-1": one, "conversation-2": two})
    store_one = _store(tmp_path, bindings)
    store_two = _store(tmp_path, bindings)
    barrier = threading.Barrier(2)

    def reserve(store, request):
        barrier.wait(timeout=5)
        try:
            return store.evaluate_and_reserve(
                request, expected_admission_revision=0
            )
        except PlanError as exc:
            return exc.reason

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda pair: reserve(*pair),
            ((store_one, _request(1, one)), (store_two, _request(2, two))),
        ))

    successes = [row for row in results if not isinstance(row, str)]
    failures = [row for row in results if isinstance(row, str)]
    assert len(successes) == 1
    assert failures == ["lane_resource_capacity_exhausted"]
    assert len(store_one.cold_snapshot()["reservations"]) == 1


def test_reservation_count_limit_rejects_growth_without_compaction(
    tmp_path,
    monkeypatch,
):
    one = _binding(1)
    two = _binding(2)
    bindings = _FakeBindings({"conversation-1": one, "conversation-2": two})
    store = _store(tmp_path, bindings, capacity=2)
    monkeypatch.setattr(
        "genshin_app.agent_chat.harness_v3.durable_lane_store.MAX_RESERVATIONS",
        1,
    )
    first = store.evaluate_and_reserve(
        _request(1, one), expected_admission_revision=0
    ).receipt

    with pytest.raises(PlanError) as caught:
        store.evaluate_and_reserve(
            _request(2, two), expected_admission_revision=0
        )

    assert caught.value.reason == "native_lane_store_capacity_exhausted"
    assert store.cold_snapshot()["reservations"] == [first.public()]


def test_failed_publish_leaves_the_previous_complete_ledger(tmp_path, monkeypatch):
    binding = _binding(1)
    bindings = _FakeBindings({"conversation-1": binding})
    store = _store(tmp_path, bindings)
    receipt = store.evaluate_and_reserve(
        _request(1, binding), expected_admission_revision=0
    ).receipt

    def fail_publish(*_args, **_kwargs):
        raise PlanError("injected", "file_conflict")

    monkeypatch.setattr(
        "genshin_app.agent_chat.harness_v3.durable_lane_store.write_owned",
        fail_publish,
    )
    with pytest.raises(PlanError, match="injected"):
        store.replace_lease_holdings(
            receipt.lane.key,
            (),
            expected_reservation_id=receipt.reservation_id,
            expected_admission_revision=1,
        )
    assert store.cold_snapshot()["reservations"] == [receipt.public()]


def test_corrupt_or_oversized_ledger_fails_closed(tmp_path, monkeypatch):
    binding = _binding(1)
    store = _store(tmp_path, _FakeBindings({"conversation-1": binding}))
    store.evaluate_and_reserve(
        _request(1, binding), expected_admission_revision=0
    )
    store.path.write_text(
        '{"contract":"native-harness-durable-lane-store-v2",'
        '"revision":0,"revision":1,"reservations":[]}',
        encoding="utf-8",
    )
    with pytest.raises(PlanError) as duplicate:
        store.cold_snapshot()
    assert duplicate.value.reason == "native_lane_store_invalid"

    monkeypatch.setattr(
        "genshin_app.agent_chat.harness_v3.durable_lane_store.MAX_LEDGER_BYTES",
        128,
    )
    store.path.write_bytes(b"{" + b"x" * 128 + b"}")
    with pytest.raises(PlanError) as oversized:
        store.cold_snapshot()
    assert oversized.value.reason == "agent_size_limit"


def test_initialized_ledger_deletion_never_reopens_as_empty(tmp_path):
    binding = _binding(1)
    store = _store(tmp_path, _FakeBindings({"conversation-1": binding}))
    store.evaluate_and_reserve(
        _request(1, binding), expected_admission_revision=0
    )
    store.path.unlink()

    with pytest.raises(PlanError) as cold:
        store.cold_snapshot()
    assert cold.value.reason == "native_lane_store_ledger_missing"
    with pytest.raises(PlanError) as reserve:
        store.evaluate_and_reserve(
            _request(1, binding), expected_admission_revision=0
        )
    assert reserve.value.reason == "native_lane_store_ledger_missing"


def test_initialized_ledger_without_epoch_marker_fails_closed(tmp_path):
    binding = _binding(1)
    store = _store(tmp_path, _FakeBindings({"conversation-1": binding}))
    store.evaluate_and_reserve(
        _request(1, binding), expected_admission_revision=0
    )
    store.initialization_path.unlink()

    with pytest.raises(PlanError) as caught:
        store.cold_snapshot()
    assert caught.value.reason == "native_lane_store_initialization_missing"


def _native_record(conversation_id: str) -> dict:
    return create_native_session(
        conversation_id=conversation_id,
        connection_kind="native_api",
        runtime_generation="dsh_v2",
        locked_provider="deepseek",
        locked_model="deepseek-chat",
        locked_reasoning_effort="high",
        domain_checkpoint={
            "document_id": "document-1",
            "candidate_revision": 0,
            "stage_revision": 0,
            "database_fingerprint": HASH_A,
            "receipt_fingerprint": HASH_B,
            "writer_fence": FENCE,
        },
        prompt_profile_hash=HASH_C,
        tool_manifest_hash=HASH_D,
    )


def test_native_binding_adapter_uses_unique_session_id_as_aba_fence(tmp_path):
    native = NativeDshSessionBindingStore(
        tmp_path, conversation_id="conversation-1"
    )
    created = native.create_authoritative(_native_record("conversation-1"))
    adapter = NativeDshLaneBindingRegistry(tmp_path)

    first = adapter.read_current_binding("conversation-1")
    assert first.identity.dsh_session_id == created["dsh_session_id"]
    assert first.identity.generation == 1
    assert first.binding_revision == created["revision"]

    native.set_archived(archived=True, expected_revision=created["revision"])
    with pytest.raises(PlanError) as archived:
        adapter.read_current_binding("conversation-1")
    assert archived.value.reason == "stale_context"
