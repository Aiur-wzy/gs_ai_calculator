"""Independent adversarial checks for Lunar-Charged endpoint expiry.

The world coordinator owns the two-stage protocol: a tick scheduled exactly
at the thundercloud endpoint observes the still-live cloud, and only the same
atomic batch may then clear that cloud.  These tests deliberately corrupt each
stage and require a byte-equivalent rollback of every public state surface.
"""

from __future__ import annotations

import copy
from collections.abc import Callable

import pytest
from test_elemental_world_lunar_electro_adversarial import (
    FIRST_TICK,
    create_world,
    lunar_reaction,
    special_runtime,
)

from genshin_engine.elemental import ElementalRuntime
from genshin_engine.elemental_world import ElementalWorldPending
from genshin_engine.special_reactions import (
    SpecialReactionPending,
    SpecialReactionRuntime,
)

REFRESH_AT = FIRST_TICK
RECENCY_REFRESH_AT = 1_250_000
ENDPOINT = 6_250_000
AREA = "area.primary"


def _prepare_endpoint_world(
    specs: list[dict],
    *,
    weak_hydro_targets: frozenset[str] = frozenset(),
):
    """Create a reachable tick==expiry state with fresh contributor ledgers."""
    bindings = [(spec["target_id"], AREA) for spec in specs]
    world = create_world(specs, bindings=bindings)

    # Commit the first attack, then refresh the six-second cloud without
    # resetting either target's independent attack phase.  This appends the
    # phase's 6.25-second tick exactly at the refreshed cloud endpoint.
    world.advance(FIRST_TICK)
    world.special_runtime.replace(
        lunar_reaction(specs[0]["target_id"], AREA, 900),
        time=REFRESH_AT,
    )
    world.advance(RECENCY_REFRESH_AT)

    # Use the reviewed same-element refresh operation rather than rewriting a
    # ledger timestamp.  A weak Hydro refresh preserves the insufficient-aura
    # case while renewing its independently reviewed five-second recency fact.
    for spec in specs:
        target_id = spec["target_id"]
        runtime = world.get_target(target_id)
        hydro_raw_gu = 0.1 if target_id in weak_hydro_targets else 5
        runtime.multi_state.add_or_refresh(
            "hydro",
            hydro_raw_gu,
            f"{target_id}.hydro.endpoint-refresh",
            "qualification.provider",
        )
        runtime.multi_state.add_or_refresh(
            "electro",
            5,
            f"{target_id}.electro.endpoint-refresh",
            "trigger.actor",
        )
    return world


def _endpoint_rows(world, cursor: int) -> list[dict]:
    return [
        row
        for row in world.drain_generated_events(cursor)["events"]
        if row.get("time_tick") == ENDPOINT
        and row.get("reaction_type") == "lunar_electro"
    ]


def _capture_atomic_state(world) -> dict:
    target_ids = tuple(world.state()["target_ids"])
    return {
        "state": copy.deepcopy(world.state()),
        "events": copy.deepcopy(world.drain_generated_events(0)),
        "special_trace": copy.deepcopy(world.special_runtime.trace),
        "target_traces": {
            target_id: copy.deepcopy(world.get_target(target_id).trace)
            for target_id in target_ids
        },
        "identities": {
            "special": world.special_runtime,
            "crystallize": world.crystallize_world,
            "bloom": world.bloom_core_state,
            "targets": {
                target_id: world.get_target(target_id) for target_id in target_ids
            },
        },
    }


def _assert_atomic_state(world, before: dict) -> None:
    assert world.state() == before["state"]
    assert world.drain_generated_events(0) == before["events"]
    assert world.special_runtime.trace == before["special_trace"]
    assert world.special_runtime is before["identities"]["special"]
    assert world.crystallize_world is before["identities"]["crystallize"]
    assert world.bloom_core_state is before["identities"]["bloom"]
    for target_id, identity in before["identities"]["targets"].items():
        assert world.get_target(target_id) is identity
        assert world.get_target(target_id).trace == before["target_traces"][target_id]


def _advance_must_rollback(world) -> None:
    before = _capture_atomic_state(world)
    with pytest.raises((ElementalWorldPending, SpecialReactionPending, RuntimeError)):
        world.advance(ENDPOINT)
    _assert_atomic_state(world, before)


def test_endpoint_tick_is_admitted_before_damage_and_expiry():
    world = _prepare_endpoint_world(
        [
            {"target_id": "enemy.a", "hydro": 4.0, "electro": 4.0},
        ]
    )
    cursor = world.drain_generated_events(0)["cursor"]

    world.advance(ENDPOINT)

    rows = _endpoint_rows(world, cursor)
    assert [row["type"] for row in rows] == [
        "special_reaction_tick_request",
        "special_reaction_tick_admission",
        "special_reaction_damage_request",
        "special_reaction_state",
    ]
    assert rows[1]["allowed"] is True
    assert rows[-1]["operation"] == "expire_area_thundercloud"
    assert world.special_runtime.lunar_cloud_by_area[AREA] == {
        "lunar_electro_active": False,
        "lunar_electro_expires_at": None,
        "lunar_electro_target_ids": [],
        "lunar_electro_last_attack_by_target": {},
        "lunar_electro_phase_by_target": {},
    }


def test_endpoint_insufficient_aura_publishes_empty_admission_then_expiry():
    world = _prepare_endpoint_world(
        [{"target_id": "enemy.a", "hydro": 0.3, "electro": 4.0}],
        weak_hydro_targets=frozenset({"enemy.a"}),
    )
    cursor = world.drain_generated_events(0)["cursor"]

    world.advance(ENDPOINT)

    rows = _endpoint_rows(world, cursor)
    assert [row["type"] for row in rows] == [
        "special_reaction_tick_request",
        "special_reaction_tick_admission",
        "special_reaction_state",
    ]
    assert rows[1]["allowed"] is False
    assert rows[1]["reason"] == "hydro_electro_coexistence_or_gauge_insufficient"
    assert rows[1]["consumed_gu"] == {"hydro": 0, "electro": 0}
    assert rows[-1]["operation"] == "expire_area_thundercloud"


def test_endpoint_multi_target_batch_expires_only_after_every_target_receipt():
    world = _prepare_endpoint_world(
        [
            {"target_id": "enemy.b", "hydro": 4.0, "electro": 4.0},
            {"target_id": "enemy.a", "hydro": 4.0, "electro": 4.0},
        ]
    )
    cursor = world.drain_generated_events(0)["cursor"]

    world.advance(ENDPOINT)

    rows = _endpoint_rows(world, cursor)
    assert [(row["target_id"], row["type"]) for row in rows] == [
        ("enemy.a", "special_reaction_tick_request"),
        ("enemy.a", "special_reaction_tick_admission"),
        ("enemy.a", "special_reaction_damage_request"),
        ("enemy.b", "special_reaction_tick_request"),
        ("enemy.b", "special_reaction_tick_admission"),
        ("enemy.b", "special_reaction_damage_request"),
        (None, "special_reaction_state"),
    ]
    assert rows[-1]["previous_target_ids"] == ["enemy.b", "enemy.a"]


@pytest.mark.parametrize("mode", ["missing", "extra", "forged", "wrong_poststate"])
def test_expiry_finalize_output_and_poststate_are_authoritative_and_atomic(
    monkeypatch,
    mode: str,
):
    world = _prepare_endpoint_world(
        [
            {"target_id": "enemy.a", "hydro": 4.0, "electro": 4.0},
        ]
    )
    original = SpecialReactionRuntime.finalize_world_lunar_electro_expiry

    def tamper(self, time):
        rows = original(self, time)
        if time != ENDPOINT:
            return rows
        if mode == "missing":
            return []
        if mode == "extra":
            forged = copy.deepcopy(rows[0])
            forged["event_id"] += ":extra"
            return [*rows, forged]
        if mode == "forged":
            rows[0]["previous_target_ids"] = ["enemy.forged"]
            return rows
        self.lunar_cloud_by_area[AREA]["lunar_electro_active"] = True
        return rows

    monkeypatch.setattr(
        SpecialReactionRuntime,
        "finalize_world_lunar_electro_expiry",
        tamper,
    )
    _advance_must_rollback(world)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("lunar_bloom_dew", 999),
        ("lunar_bloom_backlog", 999),
    ],
)
def test_expiry_finalize_cannot_pollute_unrelated_special_state(
    monkeypatch,
    field: str,
    value: int,
):
    world = _prepare_endpoint_world(
        [
            {"target_id": "enemy.a", "hydro": 4.0, "electro": 4.0},
        ]
    )
    original = SpecialReactionRuntime.finalize_world_lunar_electro_expiry

    def pollute_unrelated_state(self, time):
        rows = original(self, time)
        if time == ENDPOINT:
            self.party_state[field] = value
        return rows

    monkeypatch.setattr(
        SpecialReactionRuntime,
        "finalize_world_lunar_electro_expiry",
        pollute_unrelated_state,
    )

    before = _capture_atomic_state(world)
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(ENDPOINT)

    assert error.value.issue["code"] == "world_lunar_electro_tick_invalid"
    _assert_atomic_state(world, before)


def test_expiry_finalize_cannot_add_hidden_special_runtime_state(monkeypatch):
    world = _prepare_endpoint_world(
        [
            {"target_id": "enemy.a", "hydro": 4.0, "electro": 4.0},
        ]
    )
    original = SpecialReactionRuntime.finalize_world_lunar_electro_expiry

    def add_hidden_state(runtime, time):
        rows = original(runtime, time)
        if time == ENDPOINT:
            runtime.forged_finalize_attr = "committed"
        return rows

    monkeypatch.setattr(
        SpecialReactionRuntime,
        "finalize_world_lunar_electro_expiry",
        add_hidden_state,
    )
    before = _capture_atomic_state(world)
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(ENDPOINT)

    assert error.value.issue["code"] == "world_special_runtime_schema_mismatch"
    _assert_atomic_state(world, before)
    assert not hasattr(world.special_runtime, "forged_finalize_attr")


@pytest.mark.parametrize(
    "mode",
    ["consumed_gu", "aura_after", "damage", "other_cloud"],
)
def test_candidate_admission_tampering_rolls_back_whole_world(
    monkeypatch,
    mode: str,
):
    world = _prepare_endpoint_world(
        [
            {"target_id": "enemy.a", "hydro": 4.0, "electro": 4.0},
        ]
    )
    original = SpecialReactionRuntime.admit_lunar_electro_tick

    def tamper(self, tick, aura_state):
        admitted = original(self, tick, aura_state)
        if tick.get("time_tick") != ENDPOINT:
            return admitted
        if mode == "consumed_gu":
            admitted["admission"]["consumed_gu"]["hydro"] = 0.3
        elif mode == "aura_after":
            admitted["aura_after"]["components"]["hydro"]["remaining_gu"] += 0.1
        elif mode == "damage":
            admitted["damage_event"]["component"] = "forged_component"
        else:
            self._lunar_cloud_state("area.forged")["lunar_electro_active"] = True
        return admitted

    monkeypatch.setattr(
        SpecialReactionRuntime,
        "admit_lunar_electro_tick",
        tamper,
    )
    _advance_must_rollback(world)


@pytest.mark.parametrize("mode", ["drop_damage", "extra_row", "reverse"])
def test_target_commit_rows_cannot_drop_add_or_reorder_receipt(
    monkeypatch,
    mode: str,
):
    world = _prepare_endpoint_world(
        [
            {"target_id": "enemy.a", "hydro": 4.0, "electro": 4.0},
        ]
    )
    original = ElementalRuntime._commit_lunar_electro_tick_receipt

    def tamper(self, tick, admitted):
        rows = original(self, tick, admitted)
        if tick.get("time_tick") != ENDPOINT:
            return rows
        if mode == "drop_damage":
            return rows[:-1]
        if mode == "extra_row":
            forged = copy.deepcopy(rows[-1])
            forged["event_id"] = tick["event_id"] + ":forged"
            return [*rows, forged]
        return list(reversed(rows))

    monkeypatch.setattr(
        ElementalRuntime,
        "_commit_lunar_electro_tick_receipt",
        tamper,
    )
    _advance_must_rollback(world)


def test_missing_middle_phase_tick_is_detected_before_later_tick_can_commit():
    world = _prepare_endpoint_world(
        [
            {"target_id": "enemy.a", "hydro": 4.0, "electro": 4.0},
        ]
    )
    scheduled = world.special_runtime.scheduled
    middle = next(
        row
        for row in scheduled
        if row["target_id"] == "enemy.a" and row["time_tick"] == 4_250_000
    )
    scheduled.remove(middle)
    _advance_must_rollback(world)


def test_second_target_admission_failure_rolls_back_first_candidate_and_world(
    monkeypatch,
):
    world = _prepare_endpoint_world(
        [
            {"target_id": "enemy.a", "hydro": 4.0, "electro": 4.0},
            {"target_id": "enemy.b", "hydro": 4.0, "electro": 4.0},
        ]
    )
    original = SpecialReactionRuntime.admit_lunar_electro_tick_for_world

    def fail_second(self, tick, aura_state):
        admitted = original(self, tick, aura_state)
        if tick.get("time_tick") == ENDPOINT and tick.get("target_id") == "enemy.b":
            raise SpecialReactionPending(
                "controlled_endpoint_admission_failure",
                "controlled endpoint admission failure",
                tick["event_id"],
            )
        return admitted

    monkeypatch.setattr(
        SpecialReactionRuntime,
        "admit_lunar_electro_tick_for_world",
        fail_second,
    )
    _advance_must_rollback(world)


def test_second_target_commit_failure_rolls_back_first_target_and_candidate(
    monkeypatch,
):
    world = _prepare_endpoint_world(
        [
            {"target_id": "enemy.a", "hydro": 4.0, "electro": 4.0},
            {"target_id": "enemy.b", "hydro": 4.0, "electro": 4.0},
        ]
    )
    original = ElementalRuntime._commit_lunar_electro_tick_receipt

    def fail_second(self, tick, admitted):
        rows = original(self, tick, admitted)
        if tick.get("time_tick") == ENDPOINT and self.target == "enemy.b":
            raise RuntimeError("controlled endpoint target commit failure")
        return rows

    monkeypatch.setattr(
        ElementalRuntime,
        "_commit_lunar_electro_tick_receipt",
        fail_second,
    )
    _advance_must_rollback(world)


def test_finalize_failure_after_clear_rolls_back_clock_cloud_targets_trace_cursor(
    monkeypatch,
):
    world = _prepare_endpoint_world(
        [
            {"target_id": "enemy.a", "hydro": 4.0, "electro": 4.0},
        ]
    )
    original = SpecialReactionRuntime.finalize_world_lunar_electro_expiry

    def fail_after_clear(self, time):
        rows = original(self, time)
        if time == ENDPOINT:
            raise RuntimeError("controlled endpoint finalize failure")
        return rows

    monkeypatch.setattr(
        SpecialReactionRuntime,
        "finalize_world_lunar_electro_expiry",
        fail_after_clear,
    )
    _advance_must_rollback(world)


@pytest.mark.parametrize(
    "unfinished_call",
    [
        lambda runtime: runtime.advance(6_000_000),
        lambda runtime: runtime.advance_world_pre_expiry(6_000_000),
    ],
    ids=["ordinary-advance", "second-pre-expiry"],
)
def test_unfinalized_pre_expiry_batch_fails_closed_without_further_mutation(
    unfinished_call: Callable[[SpecialReactionRuntime], object],
):
    runtime = special_runtime("lunar_electro")
    runtime.replace(lunar_reaction("enemy.a", AREA, 1), time=0)
    runtime.advance_world_pre_expiry(6_000_000)
    before_state = copy.deepcopy(runtime.state())
    before_trace = copy.deepcopy(runtime.trace)
    before_clouds = copy.deepcopy(runtime.lunar_cloud_by_area)

    with pytest.raises(RuntimeError):
        unfinished_call(runtime)

    assert runtime.state() == before_state
    assert runtime.trace == before_trace
    assert runtime.lunar_cloud_by_area == before_clouds


def test_non_endpoint_world_tick_path_stays_single_stage_and_cloud_remains_live():
    world = create_world(
        [{"target_id": "enemy.a", "hydro": 4.0, "electro": 4.0}],
        bindings=[("enemy.a", AREA)],
    )

    world.advance(FIRST_TICK)

    rows = world.drain_generated_events(0)["events"]
    assert [row["type"] for row in rows] == [
        "special_reaction_tick_request",
        "special_reaction_tick_admission",
        "special_reaction_damage_request",
    ]
    assert (
        world.special_runtime.lunar_cloud_by_area[AREA]["lunar_electro_active"] is True
    )
    assert (
        world.special_runtime.lunar_cloud_by_area[AREA]["lunar_electro_expires_at"]
        == 6_000_000
    )


def test_ordinary_special_runtime_advance_keeps_legacy_single_stage_expiry():
    runtime = special_runtime("lunar_electro")
    runtime.replace(lunar_reaction("enemy.a", AREA, 1), time=0)
    runtime.advance(FIRST_TICK)
    runtime.replace(lunar_reaction("enemy.a", AREA, 2), time=FIRST_TICK)

    due = runtime.advance(ENDPOINT)

    endpoint = [row for row in due if row.get("time_tick") == ENDPOINT]
    assert {row["type"] for row in endpoint} == {
        "special_reaction_tick_request",
        "special_reaction_state",
    }
    assert any(row.get("operation") == "expire_area_thundercloud" for row in endpoint)
    assert runtime.lunar_cloud_by_area[AREA] == {
        "lunar_electro_active": False,
        "lunar_electro_expires_at": None,
        "lunar_electro_target_ids": [],
        "lunar_electro_last_attack_by_target": {},
        "lunar_electro_phase_by_target": {},
    }
    assert runtime._pending_world_lunar_electro_expiry is None
    assert runtime.advance(ENDPOINT) == []
