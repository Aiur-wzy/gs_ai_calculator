"""Adversarial contracts for the multi-target elemental world coordinator.

These tests intentionally stay below any particular reaction packet consumer.
They prove the world services that Star Swirl, Bloom AoE, Lunar clouds and
other area mechanics need: stable target lookup, explicit spatial admission,
shared clocks/state, and all-or-nothing mutation across target runtimes.
"""
from __future__ import annotations

import copy
from types import MethodType

import pytest

import genshin_engine.elemental_world as elemental_world_module
from genshin_engine.elemental import ElementalPending
from genshin_engine.elemental_bloom_core import BloomCoreState
from genshin_engine.elemental_schema import MULTI_AURA_RULESET
from genshin_engine.elemental_world import ElementalWorldPending, ElementalWorldRuntime

EVIDENCE = {"status": "assumed", "source": "world-runtime adversarial fixture"}


def elemental_model(
    target_id: str,
    *,
    pending_profile: str | None = None,
    freezable: bool = False,
) -> dict:
    return {
        "schema_version": 1,
        "ruleset": MULTI_AURA_RULESET,
        "target": {
            "id": target_id,
            "kind": "ordinary",
            "knowledge": "known",
            "initial_auras": [],
            "freeze": {
                "freezable": freezable,
                "resistance": 0,
                **({} if freezable else {
                    "unfreezable_resolution": "coexist",
                }),
                **EVIDENCE,
            },
            **EVIDENCE,
        },
        "profiles": [
            {
                "id": element,
                "element": element,
                "application": "apply",
                "gauge_units": 1,
                "icd": {"kind": "none"},
                **({"status": "pending"} if element == pending_profile else EVIDENCE),
                "source": "world-runtime adversarial fixture",
            }
            for element in ("pyro", "hydro", "cryo", "electro", "dendro", "anemo", "geo")
        ],
        "swirl_damage": {
            "icd": {"kind": "sequence", "reset_seconds": .5, "sequence": [1, 1, 0]},
            **EVIDENCE,
        },
    }


def apply(runtime, element: str, *, event_id: str, time: int = 0) -> dict:
    return runtime.attempt(
        event_id=event_id,
        stable_event_id=event_id,
        action_id="action.fixture",
        actor="actor.fixture",
        time=time,
        application={
            "profile_id": element,
            "target_id": runtime.target,
            "landed": True,
            "area_id": "area.fixture",
            "atomic_parent_id": "packet.fixture",
        },
    )


def create_core(state: BloomCoreState, index: int, *, target_id: str, area_id: str) -> dict:
    return state.create(
        time=index,
        event_id=f"core.{index}",
        target_id=target_id,
        area_id=area_id,
        owner="actor.fixture",
        parent_reaction_id=f"bloom.{index}",
        contributor_ids=["actor.fixture", "aura.fixture"],
        source="world-runtime adversarial fixture",
    )


def test_target_registration_and_area_query_are_stable_and_fail_closed():
    world = ElementalWorldRuntime()
    runtime_b = world.register_target(
        elemental_model("enemy.b"), area_ids=("area.shared", "area.b"))
    runtime_a = world.register_target(
        elemental_model("enemy.a"), area_ids=("area.shared", "area.a"))

    assert world.get_target("enemy.a") is runtime_a
    assert world.get_target("enemy.b") is runtime_b
    assert world.targets_in_area("area.shared") == ("enemy.a", "enemy.b")
    assert world.targets_in_area("area.a") == ("enemy.a",)
    assert world.targets_in_area("area.b") == ("enemy.b",)

    before = world.state()
    with pytest.raises(ElementalWorldPending) as error:
        world.targets_in_area("area.typo")
    assert error.value.issue["code"] == "world_area_unknown"
    assert error.value.issue["severity"] == "pending"
    assert world.state() == before


def test_duplicate_target_registration_cannot_replace_live_authority():
    world = ElementalWorldRuntime()
    original = world.register_target(elemental_model("enemy"))
    before = world.state()

    with pytest.raises(ValueError, match="目标ID重复"):
        world.register_target(elemental_model("enemy"), area_ids=("other.area",))

    assert world.get_target("enemy") is original
    assert world.state() == before
    assert world.targets_in_area("battlefield.primary") == ("enemy",)
    with pytest.raises(ElementalWorldPending):
        world.targets_in_area("other.area")


def test_successful_atomic_operation_commits_all_targets_as_one_packet():
    world = ElementalWorldRuntime()
    left = world.register_target(elemental_model("enemy.left"), area_ids=("area.fixture",))
    right = world.register_target(elemental_model("enemy.right"), area_ids=("area.fixture",))

    def operation(_world):
        left_receipt = apply(left, "hydro", event_id="packet.fixture:left")
        right_receipt = apply(right, "hydro", event_id="packet.fixture:right")
        world.publish_generated_events([
            {
                "type": "fixture_damage_request",
                "event_id": "packet.fixture:damage:left",
                "target_id": "enemy.left",
                "parent_event_id": "packet.fixture",
            },
            {
                "type": "fixture_damage_request",
                "event_id": "packet.fixture:damage:right",
                "target_id": "enemy.right",
                "parent_event_id": "packet.fixture",
            },
        ])
        return left_receipt, right_receipt

    receipts = world.execute_atomic(operation)
    assert [row["target_id"] for row in receipts] == ["enemy.left", "enemy.right"]
    assert [row["element"] for row in left.state()["auras"]] == ["hydro"]
    assert [row["element"] for row in right.state()["auras"]] == ["hydro"]
    drain = world.drain_generated_events(after_cursor=0)
    assert [row["event_id"] for row in drain["events"]] == [
        "packet.fixture:damage:left", "packet.fixture:damage:right"]


def test_pending_child_rolls_back_every_target_and_the_world_event_journal():
    world = ElementalWorldRuntime()
    left = world.register_target(elemental_model("enemy.left"), area_ids=("area.fixture",))
    right = world.register_target(
        elemental_model("enemy.right", pending_profile="pyro"),
        area_ids=("area.fixture",),
    )
    before = world.state()

    def operation(_world):
        apply(left, "hydro", event_id="packet.pending:left", time=1_000_000)
        world.publish_generated_events([{
            "type": "fixture_damage_request",
            "event_id": "packet.pending:provisional-damage",
            "target_id": "enemy.left",
            "parent_event_id": "packet.pending",
        }])
        apply(right, "pyro", event_id="packet.pending:right", time=1_000_000)

    with pytest.raises(ElementalPending) as error:
        world.execute_atomic(operation)
    assert error.value.issue["code"] == "unknown_application"
    assert world.state() == before
    assert world.drain_generated_events(after_cursor=0) == {"events": [], "cursor": 0}


def test_snapshot_restore_keeps_shared_object_and_runtime_identity():
    shared_cores = BloomCoreState()
    world = ElementalWorldRuntime(bloom_core_state=shared_cores)
    runtime = world.register_target(elemental_model("enemy"), area_ids=("area.fixture",))
    crystallize_identity = world.crystallize_world
    bloom_identity = world.bloom_core_state
    runtime_identity = runtime
    snapshot = world.snapshot()
    before = world.state()

    apply(runtime, "hydro", event_id="mutate.aura", time=500_000)
    create_core(shared_cores, 500_001, target_id="enemy", area_id="area.fixture")
    world.publish_generated_events([{"type": "fixture", "event_id": "mutate.event"}])
    world.restore(snapshot)

    assert world.get_target("enemy") is runtime_identity
    assert world.crystallize_world is crystallize_identity
    assert world.bloom_core_state is bloom_identity is shared_cores
    assert runtime.bloom_core_state is shared_cores
    assert world.state() == before


def test_atomic_rollback_preserves_child_and_reverse_set_identities():
    world = ElementalWorldRuntime()
    runtime = world.register_target(
        elemental_model("enemy", freezable=True),
        area_ids=("area.fixture",),
    )
    # Multi-aura targets normally reach Freeze through multi_state.  Keeping
    # this explicit field populated exercises every documented child root.
    runtime.freeze_state = runtime.multi_state.freeze_state
    identities = {
        "freeze_state": runtime.freeze_state,
        "multi_state": runtime.multi_state,
        "crystallize_state": runtime.crystallize_state,
        "area_set": world._targets_by_area["area.fixture"],
    }
    before = copy.deepcopy(world.state())

    def corrupt(current_world):
        runtime.multi_state.add_or_refresh(
            "hydro", 1, "fixture", "fixture.owner")
        current_world._targets_by_area["area.fixture"].clear()
        raise ElementalWorldPending("fixture_abort", "controlled")

    with pytest.raises(ElementalWorldPending, match="controlled"):
        world.execute_atomic(corrupt)

    assert runtime.freeze_state is identities["freeze_state"]
    assert runtime.multi_state is identities["multi_state"]
    assert runtime.multi_state.freeze_state is identities["freeze_state"]
    assert runtime.crystallize_state is identities["crystallize_state"]
    assert world._targets_by_area["area.fixture"] is identities["area_set"]
    assert world.targets_in_area("area.fixture") == ("enemy",)
    assert world.state() == before


def test_atomic_success_rejects_equal_reverse_set_replacement():
    world = ElementalWorldRuntime()
    world.register_target(elemental_model("enemy"), area_ids=("area.fixture",))
    area_set = world._targets_by_area["area.fixture"]
    before = copy.deepcopy(world.state())

    def replace_equal_set(current_world):
        current_world._targets_by_area["area.fixture"] = set(area_set)

    with pytest.raises(ElementalWorldPending) as error:
        world.execute_atomic(replace_equal_set)

    assert error.value.issue["code"] == "world_transaction_commit_invariant_mismatch"
    assert "area_target_set_identity" in error.value.issue["violations"]
    assert world._targets_by_area["area.fixture"] is area_set
    assert world.state() == before


@pytest.mark.parametrize("shared_name", ["crystallize_world", "bloom_core_state"])
def test_atomic_rollback_bypasses_shared_instance_restore_override(shared_name):
    world = ElementalWorldRuntime()
    world.register_target(elemental_model("enemy"))
    shared = getattr(world, shared_name)
    before = copy.deepcopy(world.state())

    def no_restore(_shared, _snapshot):
        return None

    shared.restore = MethodType(no_restore, shared)

    def mutate_then_abort(_world):
        shared.revision = 777
        raise ElementalWorldPending("fixture_abort", "controlled")

    with pytest.raises(ElementalWorldPending, match="controlled"):
        world.execute_atomic(mutate_then_abort)

    assert getattr(world, shared_name) is shared
    assert shared.revision == 0
    assert shared.restore.__self__ is shared
    assert world.state() == before


def test_atomic_catches_base_exception_and_uses_frozen_rollback_method():
    class ControlledAbort(BaseException):
        pass

    world = ElementalWorldRuntime()
    runtime = world.register_target(elemental_model("enemy"))
    before = copy.deepcopy(world.state())

    def fake_restore(_world, _snapshot, _roots):
        return None

    world._restore_atomic_roots = MethodType(fake_restore, world)

    def abort(_world):
        apply(runtime, "hydro", event_id="base-exception")
        raise ControlledAbort()

    with pytest.raises(ControlledAbort):
        world.execute_atomic(abort)

    assert world.state() == before
    assert world._restore_atomic_roots.__self__ is world


def test_atomic_commit_uses_frozen_guards_and_rejects_hidden_runtime_data():
    world = ElementalWorldRuntime()
    runtime = world.register_target(elemental_model("enemy"))
    before = copy.deepcopy(world.state())

    def accept_everything(_world, _roots, _publications):
        return None

    def report_clock_ok(_world):
        return None

    world._assert_atomic_commit_invariants = MethodType(accept_everything, world)
    world._assert_world_clock_synchronized = MethodType(report_clock_ok, world)

    def corrupt(current_world):
        runtime.forged_hidden_data = {"accepted": True}
        current_world.time = 1

    with pytest.raises(ElementalWorldPending) as error:
        world.execute_atomic(corrupt)

    assert error.value.issue["code"] == "world_transaction_commit_invariant_mismatch"
    assert set(error.value.issue["violations"]) >= {
        "target_instance_schema:enemy", "world_clock"}
    assert not hasattr(runtime, "forged_hidden_data")
    assert world.state() == before


def test_atomic_publication_audit_rejects_direct_journal_double_write():
    world = ElementalWorldRuntime()
    before = copy.deepcopy(world.state())
    event = {"type": "fixture", "event_id": "fixture:published"}

    def forge(current_world):
        current_world.publish_generated_events([event])
        current_world._generated_events.append(copy.deepcopy(event))

    with pytest.raises(ElementalWorldPending) as error:
        world.execute_atomic(forge)

    assert error.value.issue["code"] == "world_transaction_commit_invariant_mismatch"
    assert "generated_event_journal" in error.value.issue["violations"]
    assert not hasattr(world, "_transaction_publications")
    assert world.state() == before
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


@pytest.mark.parametrize(
    ("shared_name", "violation"),
    [
        ("crystallize_world", "crystallize_world_instance_schema"),
        ("bloom_core_state", "bloom_core_instance_schema"),
    ],
)
def test_atomic_success_rejects_shared_root_hidden_data(shared_name, violation):
    world = ElementalWorldRuntime()
    world.register_target(elemental_model("enemy"))
    shared = getattr(world, shared_name)
    before = copy.deepcopy(world.state())

    def add_hidden(_world):
        shared.forged_hidden_data = {"accepted": True}

    with pytest.raises(ElementalWorldPending) as error:
        world.execute_atomic(add_hidden)

    assert error.value.issue["code"] == "world_transaction_commit_invariant_mismatch"
    assert violation in error.value.issue["violations"]
    assert not hasattr(shared, "forged_hidden_data")
    assert world.state() == before


@pytest.mark.parametrize(
    ("shared_name", "violation"),
    [
        (
            "crystallize_world",
            "crystallize_world_instance_schema:callable_overrides",
        ),
        ("bloom_core_state", "bloom_core_instance_schema:callable_overrides"),
    ],
)
def test_atomic_success_rejects_new_shared_method_override(shared_name, violation):
    world = ElementalWorldRuntime()
    world.register_target(elemental_model("enemy"))
    shared = getattr(world, shared_name)

    def forged_restore(_shared, _snapshot):
        return None

    def add_allowed_name(_world):
        shared.restore = MethodType(forged_restore, shared)

    with pytest.raises(ElementalWorldPending) as error:
        world.execute_atomic(add_allowed_name)

    assert violation in error.value.issue["violations"]
    assert "restore" not in shared.__dict__


def test_atomic_snapshot_ignores_instance_registry_fingerprint_override():
    world = ElementalWorldRuntime()
    world.register_target(elemental_model("enemy"), area_ids=("area.fixture",))
    calls = []

    def forged_fingerprint(_world):
        calls.append("called")
        world.time = 777
        return ()

    wrapper = MethodType(forged_fingerprint, world)
    world._registry_fingerprint = wrapper

    world.execute_atomic(lambda _world: None)

    assert calls == []
    assert world.time == 0
    assert world._registry_fingerprint is wrapper


def test_atomic_uses_entry_guard_when_operation_rebinds_module_alias():
    world = ElementalWorldRuntime()
    runtime = world.register_target(elemental_model("enemy"))
    original_guard = elemental_world_module._REFERENCE_WORLD_ASSERT_ATOMIC_COMMIT

    def corrupt(_world):
        elemental_world_module._REFERENCE_WORLD_ASSERT_ATOMIC_COMMIT = (
            lambda *_args, **_kwargs: None
        )
        runtime.forged_hidden_data = {"accepted": True}

    with pytest.raises(ElementalWorldPending) as error:
        world.execute_atomic(corrupt)

    assert "target_instance_schema:enemy" in error.value.issue["violations"]
    assert not hasattr(runtime, "forged_hidden_data")
    assert (
        elemental_world_module._REFERENCE_WORLD_ASSERT_ATOMIC_COMMIT
        is original_guard
    )


def test_atomic_uses_entry_rollback_when_base_exception_rebinds_module_alias():
    class ControlledAbort(BaseException):
        pass

    world = ElementalWorldRuntime()
    runtime = world.register_target(elemental_model("enemy"))
    original_rollback = elemental_world_module._REFERENCE_WORLD_RESTORE_ATOMIC_ROOTS
    before = copy.deepcopy(world.state())

    def abort(_world):
        elemental_world_module._REFERENCE_WORLD_RESTORE_ATOMIC_ROOTS = (
            lambda *_args, **_kwargs: None
        )
        runtime.time = 777
        raise ControlledAbort()

    with pytest.raises(ControlledAbort):
        world.execute_atomic(abort)

    assert world.state() == before
    assert (
        elemental_world_module._REFERENCE_WORLD_RESTORE_ATOMIC_ROOTS
        is original_rollback
    )


@pytest.mark.parametrize("child_name", ["multi_state", "crystallize_state"])
@pytest.mark.parametrize("pollution", ["data", "callable"])
def test_atomic_rejects_and_removes_nested_target_pollution(
    child_name,
    pollution,
):
    world = ElementalWorldRuntime()
    runtime = world.register_target(elemental_model("enemy"))
    child_state = getattr(runtime, child_name)
    before = copy.deepcopy(world.state())

    def forged_callback(_child):
        return None

    def pollute(_world):
        if pollution == "data":
            child_state.forged_hidden_data = {"accepted": True}
        else:
            child_state.forged_callback = MethodType(
                forged_callback, child_state)

    with pytest.raises(ElementalWorldPending) as error:
        world.execute_atomic(pollute)

    assert (
        f"target_child_schema:enemy:{child_name}"
        in error.value.issue["violations"]
    )
    assert not hasattr(child_state, "forged_hidden_data")
    assert not hasattr(child_state, "forged_callback")
    assert getattr(runtime, child_name) is child_state
    assert world.state() == before


@pytest.mark.parametrize("container_kind", ["outer_list", "row_dict", "nested_list"])
def test_publish_rejects_non_plain_json_before_subclass_callbacks(container_kind):
    callbacks = []

    class CallbackList(list):
        def __iter__(self):
            callbacks.append("iter")
            return super().__iter__()

        def __deepcopy__(self, memo):
            callbacks.append("deepcopy")
            return type(self)(self)

        def __eq__(self, other):
            callbacks.append("eq")
            return super().__eq__(other)

    class CallbackDict(dict):
        def items(self):
            callbacks.append("items")
            return super().items()

        def __deepcopy__(self, memo):
            callbacks.append("deepcopy")
            return type(self)(self)

        def __eq__(self, other):
            callbacks.append("eq")
            return super().__eq__(other)

    event = {"type": "fixture", "event_id": "fixture:plain-json"}
    if container_kind == "outer_list":
        events = CallbackList([event])
    elif container_kind == "row_dict":
        events = [CallbackDict(event)]
    else:
        events = [{**event, "payload": CallbackList([1])}]
    world = ElementalWorldRuntime()

    with pytest.raises(TypeError, match="普通JSON"):
        world.publish_generated_events(events)

    assert callbacks == []
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


def test_atomic_rejects_cross_world_execute_and_direct_publish_without_writes():
    world_a = ElementalWorldRuntime()
    world_b = ElementalWorldRuntime()
    called = []

    def nested_execute(_world):
        return world_b.execute_atomic(lambda _other: called.append("ran"))

    with pytest.raises(RuntimeError, match="跨世界嵌套"):
        world_a.execute_atomic(nested_execute)

    assert called == []
    assert world_a.drain_generated_events(0) == {"events": [], "cursor": 0}
    assert world_b.drain_generated_events(0) == {"events": [], "cursor": 0}

    def foreign_publish(_world):
        world_b.publish_generated_events([
            {"type": "fixture", "event_id": "foreign:direct"}
        ])

    with pytest.raises(RuntimeError, match="其他世界"):
        world_a.execute_atomic(foreign_publish)

    assert world_a.drain_generated_events(0) == {"events": [], "cursor": 0}
    assert world_b.drain_generated_events(0) == {"events": [], "cursor": 0}


def test_second_commit_guard_failure_restores_journal_and_reverse_roots(
    monkeypatch,
):
    world = ElementalWorldRuntime()
    world.register_target(elemental_model("enemy"), area_ids=("area.fixture",))
    journal = world._generated_events
    area_set = world._targets_by_area["area.fixture"]
    original_guard = elemental_world_module._REFERENCE_WORLD_ASSERT_ATOMIC_COMMIT
    calls = []

    def fail_second_guard(current_world, roots, publications, **kwargs):
        calls.append(kwargs["journal_committed"])
        if kwargs["journal_committed"]:
            current_world._targets_by_area["area.fixture"].clear()
        return original_guard(
            current_world, roots, publications, **kwargs)

    monkeypatch.setattr(
        elemental_world_module,
        "_REFERENCE_WORLD_ASSERT_ATOMIC_COMMIT",
        fail_second_guard,
    )

    def publish(current_world):
        current_world.publish_generated_events([
            {"type": "fixture", "event_id": "fixture:second-guard"}
        ])

    with pytest.raises(ElementalWorldPending):
        world.execute_atomic(publish)

    assert calls == [False, True]
    assert world._generated_events is journal
    assert world._targets_by_area["area.fixture"] is area_set
    assert world.targets_in_area("area.fixture") == ("enemy",)
    assert world.drain_generated_events(0) == {"events": [], "cursor": 0}


@pytest.mark.parametrize(
    ("root_name", "field", "corrupt_value", "violation"),
    [
        ("target", "trace", {}, "target_structure:enemy:trace"),
        ("target", "profiles", [], "target_structure:enemy:profiles"),
        (
            "target",
            "generated_events",
            {},
            "target_structure:enemy:generated_events",
        ),
        (
            "multi",
            "auras",
            "corrupt",
            "target_child_structure:enemy:multi_state:auras",
        ),
        (
            "multi",
            "time",
            "corrupt",
            "target_child_structure:enemy:multi_state:time",
        ),
        (
            "freeze",
            "aura",
            [],
            "target_child_structure:enemy:multi_state.freeze_state:aura",
        ),
        (
            "target_crystallize",
            "next_reaction_time",
            "corrupt",
            "target_child_structure:enemy:crystallize_state:next_reaction_time",
        ),
        (
            "crystallize_world",
            "shards",
            {},
            "crystallize_world_instance_schema:structure:shards",
        ),
        (
            "crystallize_world",
            "revision",
            "corrupt",
            "crystallize_world_instance_schema:structure:revision",
        ),
        (
            "bloom",
            "cores",
            {},
            "bloom_core_instance_schema:structure:cores",
        ),
        (
            "bloom",
            "time",
            "corrupt",
            "bloom_core_instance_schema:structure:time",
        ),
    ],
)
def test_atomic_rejects_existing_field_structure_drift(
    root_name,
    field,
    corrupt_value,
    violation,
):
    world = ElementalWorldRuntime()
    target = world.register_target(elemental_model("enemy", freezable=True))
    roots = {
        "target": target,
        "multi": target.multi_state,
        "freeze": target.multi_state.freeze_state,
        "target_crystallize": target.crystallize_state,
        "crystallize_world": world.crystallize_world,
        "bloom": world.bloom_core_state,
    }
    root = roots[root_name]
    before = copy.deepcopy(world.state())

    def corrupt(_world):
        setattr(root, field, corrupt_value)

    with pytest.raises(ElementalWorldPending) as error:
        world.execute_atomic(corrupt)

    assert violation in error.value.issue["violations"]
    assert world.state() == before


def test_domain_structure_guard_allows_normal_optional_freeze_transition():
    world = ElementalWorldRuntime()
    target = world.register_target(elemental_model("enemy", freezable=True))
    assert target.multi_state.freeze_state.aura is None

    world.execute_atomic(
        lambda _world: apply(target, "hydro", event_id="legal:hydro")
    )

    assert type(target.multi_state.auras) is dict
    assert type(target.multi_state.freeze_state.aura) is dict


def test_atomic_rejects_world_clock_type_drift_without_participants():
    world = ElementalWorldRuntime()

    with pytest.raises(ElementalWorldPending) as error:
        world.execute_atomic(lambda current_world: setattr(
            current_world, "time", "corrupt"))

    assert "world_structure:time" in error.value.issue["violations"]
    assert world.time == 0


def test_shared_advance_moves_every_target_to_one_clock_boundary():
    world = ElementalWorldRuntime()
    left = world.register_target(elemental_model("enemy.left"))
    right = world.register_target(elemental_model("enemy.right"))

    result = world.advance(2_000_000)

    assert world.time == 2_000_000
    assert left.time == right.time == world.time
    assert result["time_tick"] == 2_000_000


def test_shared_advance_is_atomic_when_one_target_fails(monkeypatch):
    world = ElementalWorldRuntime()
    left = world.register_target(elemental_model("enemy.left"))
    right = world.register_target(elemental_model("enemy.right"))
    before = world.state()
    original_advance = right.advance

    def fail_after_local_advance(time):
        original_advance(time)
        raise RuntimeError("adversarial target advance failure")

    monkeypatch.setattr(right, "advance", fail_after_local_advance)
    with pytest.raises(RuntimeError, match="adversarial target advance failure"):
        world.advance(1_000_000)

    assert left.time == right.time == 0
    assert world.state() == before


def test_bloom_core_limit_is_shared_across_targets_and_preserves_area_provenance():
    shared_cores = BloomCoreState()
    world = ElementalWorldRuntime(bloom_core_state=shared_cores)
    left = world.register_target(
        elemental_model("enemy.left"), area_ids=("area.left",))
    right = world.register_target(
        elemental_model("enemy.right"), area_ids=("area.right",))
    assert left.bloom_core_state is right.bloom_core_state is shared_cores

    for index in range(5):
        create_core(
            shared_cores,
            index,
            target_id=("enemy.left" if index % 2 == 0 else "enemy.right"),
            area_id=("area.left" if index % 2 == 0 else "area.right"),
        )
    sixth = create_core(
        shared_cores, 5, target_id="enemy.right", area_id="area.right")

    overflow = sixth["overflow_detonation"]
    assert overflow["event_id"] == "core.0:detonate"
    assert overflow["target_id"] == "enemy.left"
    assert overflow["area_id"] == "area.left"
    assert overflow["reason"] == "field_limit_oldest"
    assert [row["id"] for row in shared_cores.state()["cores"]] == [
        "core.1", "core.2", "core.3", "core.4", "core.5"]
    assert {row["area_id"] for row in shared_cores.state()["cores"]} == {
        "area.left", "area.right"}


def test_cursor_drain_replays_only_when_caller_reuses_an_old_cursor():
    world = ElementalWorldRuntime()
    original_events = [
        {"type": "fixture_damage_request", "event_id": "stable.damage.1"},
        {"type": "fixture_damage_request", "event_id": "stable.damage.2"},
    ]
    world.publish_generated_events(original_events)

    first = world.drain_generated_events(after_cursor=0)
    exhausted = world.drain_generated_events(after_cursor=first["cursor"])
    replay = world.drain_generated_events(after_cursor=0)

    assert first == replay
    assert exhausted == {"events": [], "cursor": first["cursor"]}
    assert [row["event_id"] for row in first["events"]] == [
        "stable.damage.1", "stable.damage.2"]
    assert original_events == [
        {"type": "fixture_damage_request", "event_id": "stable.damage.1"},
        {"type": "fixture_damage_request", "event_id": "stable.damage.2"},
    ]


def test_duplicate_or_invalid_publish_batch_is_rejected_without_partial_append():
    world = ElementalWorldRuntime()
    world.publish_generated_events([{"type": "fixture", "event_id": "existing"}])
    before = world.drain_generated_events(after_cursor=0)

    with pytest.raises(ValueError, match="事件ID"):
        world.publish_generated_events([
            {"type": "fixture", "event_id": "new"},
            {"type": "fixture", "event_id": "existing"},
        ])
    assert world.drain_generated_events(after_cursor=0) == before

    with pytest.raises(ValueError, match="type与event_id"):
        world.publish_generated_events([
            {"type": "fixture", "event_id": "also-new"},
            {"type": "fixture"},
        ])
    assert world.drain_generated_events(after_cursor=0) == before


def test_rolled_back_event_identity_can_be_retried_once_without_collision():
    world = ElementalWorldRuntime()

    def rejected(current_world):
        current_world.publish_generated_events([
            {"type": "fixture", "event_id": "packet.retry:damage"}])
        raise ElementalWorldPending("fixture_pending", "fixture requests retry")

    with pytest.raises(ElementalWorldPending):
        world.execute_atomic(rejected)
    assert world.drain_generated_events(after_cursor=0) == {"events": [], "cursor": 0}

    cursor = world.publish_generated_events([
        {"type": "fixture", "event_id": "packet.retry:damage"}])
    assert cursor == 1
    with pytest.raises(ValueError, match="事件ID"):
        world.publish_generated_events([
            {"type": "fixture", "event_id": "packet.retry:damage"}])


def test_shared_core_expiry_is_published_once_with_multiple_targets():
    shared_cores = BloomCoreState()
    world = ElementalWorldRuntime(bloom_core_state=shared_cores)
    world.register_target(elemental_model("enemy.left"))
    world.register_target(elemental_model("enemy.right"))
    create_core(
        shared_cores, 0, target_id="enemy.left", area_id="battlefield.primary")

    first_advance = world.advance(6_000_000)
    first = world.drain_generated_events(after_cursor=0)
    second_advance = world.advance(6_000_000)
    exhausted = world.drain_generated_events(after_cursor=first["cursor"])

    assert first_advance["generated_event_count"] == 1
    assert second_advance["generated_event_count"] == 0
    assert [row["event_id"] for row in first["events"]] == ["core.0:detonate"]
    assert exhausted == {"events": [], "cursor": first["cursor"]}


def test_event_id_collision_during_advance_rolls_back_expiry_and_clock():
    shared_cores = BloomCoreState()
    world = ElementalWorldRuntime(bloom_core_state=shared_cores)
    world.register_target(elemental_model("enemy"))
    create_core(shared_cores, 0, target_id="enemy", area_id="battlefield.primary")
    world.publish_generated_events([
        {"type": "fixture_reservation", "event_id": "core.0:detonate"}])
    before = world.state()
    before_events = world.drain_generated_events(after_cursor=0)

    with pytest.raises(ValueError, match="事件ID"):
        world.advance(6_000_000)

    assert world.state() == before
    assert world.drain_generated_events(after_cursor=0) == before_events


def test_published_events_are_isolated_from_caller_and_reader_mutation():
    world = ElementalWorldRuntime()
    supplied = {"type": "fixture", "event_id": "stable.event", "detail": {"value": 1}}
    world.publish_generated_events([supplied])
    supplied["detail"]["value"] = 99
    first = world.drain_generated_events(after_cursor=0)
    first["events"][0]["detail"]["value"] = -1

    replay = world.drain_generated_events(after_cursor=0)
    assert replay["events"] == [
        {"type": "fixture", "event_id": "stable.event", "detail": {"value": 1}}]
