"""Deadline leaves preserve both successful output and legacy fault timing."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest
from test_special_reactions import reaction, runtime

from genshin_engine import special_reactions as owner
from genshin_engine.special_reaction_reference import copy_runtime
from genshin_engine.special_state_observation import canonical_stellar_deadline_row


def canonical_trace(rows):
    return [canonical_stellar_deadline_row(row) for row in rows]


def seeded(family):
    value = runtime(family)
    for index in range(1, 7):
        value.replace(reaction(family, index), time=0)
    if family == "stellar_superconduct":
        value.record_stellar_superconduct_application(
            event_id="later.application", target_id="enemy", actor="trigger.actor",
            element="cryo", time=0, application_allowed=True)
    return value


@pytest.mark.parametrize(("family", "fault_index"), [
    ("stellar_superconduct", 1), ("stellar_superconduct", 2),
    ("stellar_superconduct", 3), ("lunar_bloom", 1), ("lunar_bloom", 2),
    ("lunar_bloom", 3),
])
def test_profile_fault_preserves_the_same_standalone_partial_commit(family, fault_index):
    value = seeded(family)
    canonical = copy_runtime(value)
    initial = copy.deepcopy(value.state())
    for target in (value, canonical):
        original = target._profile_audit
        seen = []

        def fault(profile, *, original=original, seen=seen):
            seen.append(profile)
            if len(seen) == fault_index:
                raise ValueError("profile projection fault")
            return original(profile)

        target._profile_audit = fault
        with pytest.raises(ValueError, match="profile projection"):
            target.advance(20_000_000)
    assert value.state() == canonical.state()
    assert canonical_trace(value.trace) == canonical_trace(canonical.trace)
    assert value.state() != initial
    assert value.time == 0  # The final clock assignment was not reached.


@pytest.mark.parametrize("fault", ["factor", "ordinary_bonus"])
def test_settlement_formula_fault_keeps_precise_pre_fault_assignment_order(monkeypatch, fault):
    value = seeded("stellar_superconduct")
    canonical = copy_runtime(value)

    def reject(*_args):
        raise ValueError("settlement formula fault")

    if fault == "factor":
        monkeypatch.setattr(owner, "stellar_superconduct_factor", reject)
        monkeypatch.setitem(canonical._advance_deadlines.__func__.__globals__,
                            "stellar_superconduct_factor", reject)
    else:
        value._ordinary_bonus = reject
        canonical._ordinary_bonus = reject
    for target in (value, canonical):
        with pytest.raises(ValueError, match="settlement formula"):
            target.advance(4_000_000)
    assert value.state() == canonical.state()
    assert canonical_trace(value.trace) == canonical_trace(canonical.trace)
    assert value.stellar_field_by_area["battlefield.primary"]["stellar_superconduct_settled_layers"] == 1


def test_deadline_canonical_is_independent_of_actual_step_handler(monkeypatch):
    value = seeded("stellar_superconduct")
    canonical = copy_runtime(value)
    before, trace = copy.deepcopy(value.state()), copy.deepcopy(value.trace)
    original = owner.plan_stellar_field_deadline_steps

    def stale(*args, **kwargs):
        for step in original(*args, **kwargs):
            yield replace(step, expected_prestate_fingerprint="stale")

    monkeypatch.setattr(owner, "plan_stellar_field_deadline_steps", stale)
    canonical.advance(4_000_000)
    with pytest.raises(ValueError, match="计划与提交"):
        value.advance(4_000_000)
    assert value.state() == before and value.trace == trace
    assert canonical.time == 4_000_000
    assert "plan_stellar_field_deadline_steps" not in canonical._advance_deadlines.__func__.__code__.co_names


def test_bloom_fill_keeps_the_original_queue_list_alias():
    value = seeded("lunar_bloom")
    sources = value.party_state["lunar_bloom_backlog_sources"]
    value.advance(2_500_000)
    assert value.party_state["lunar_bloom_backlog_sources"] is sources
    assert len(sources) == 5


def test_stellar_clock_is_observed_from_the_real_schedule_without_changing_state():
    value = seeded("stellar_superconduct")
    canonical = copy_runtime(value)
    due = value.advance(4_000_000)
    expected_due = canonical.advance(4_000_000)
    settlement, = [row for row in due if row.get("operation") == "settle_four_second_window"]
    field, = value.stellar_field_by_area.values()
    assert settlement["next_settlement_at"] == field["stellar_superconduct_next_settlement"] / 1_000_000 == 8
    assert "next_settlement_at" not in expected_due[0]
    assert value.state() == canonical.state()
    assert canonical_trace(due) == expected_due
    assert canonical_trace(value.trace) == canonical_trace(canonical.trace)
