"""Generic queue recovery and actionable ownership diagnostics."""
import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.rotation import check_rotation
from tests.test_mechanics_execution import EVIDENCE, action, fixture


def scenario(operation, *, recovery="sequential", amount=1, at=1.2, initial=(15, 30)):
    t, r, _ = fixture(())
    r["abilities"][0]["cooldown"] = {"group": "skill", "duration": 15,
        "charges": 2, "recovery": recovery, "start": "action_start"}
    r["initial_cooldowns"] = [{"character": "a", "group": "skill", "ready_in": list(initial)}]
    r["actions"] = [action(start=5, end=5.1, repeat={"mode": "once"})]
    r["events"] = [{"id": "restore", "type": "cooldown_change", "character": "a",
        "group": "skill", "operation": operation, "amount": amount, "t": at,
        "order": 10, "repeat": {"mode": "once"}, **EVIDENCE}]
    return t, r


@pytest.mark.parametrize(("operation", "recovery", "expected"), [
    ("restore", "sequential", [30]), ("restore_rebase", "sequential", [16.2]),
    ("restore_rebase", "parallel", [30]), ("reduce", "sequential", [14, 29]),
])
def test_recovery_is_explicit_and_preserves_old_semantics(operation, recovery, expected):
    inputs = scenario(operation, recovery=recovery)
    original = copy.deepcopy(inputs)
    result = check_rotation(*inputs)
    row = next(row for row in result["event_log"] if row["id"] == "restore")
    assert row["ready_at"] == pytest.approx(expected), result["issues"]
    assert inputs == original


@pytest.mark.parametrize(("initial", "amount", "at", "expected"), [
    ((), 1, 1.2, []), ((15, 30), 5, 1.2, []),
    ((1.2, 16.2), 1, 1.2, []), ((15, 30), 0, 1.2, [15, 30]),
])
def test_rebase_empty_full_same_tick_and_zero(initial, amount, at, expected):
    result = check_rotation(*scenario("restore_rebase", initial=initial, amount=amount, at=at))
    row = next(row for row in result["event_log"] if row["id"] == "restore")
    assert row["ready_at"] == expected


def test_action_hit_collision_reports_both_input_locations():
    t, r, m = fixture((.2,))
    t["hits"][0]["id"] = "cast"
    m["hit_events"][0]["hit_id"] = "cast"
    result = execute_mechanics(t, r, m)
    issue = next(i for i in result["issues"] if i["code"] == "duplicate_id")
    assert issue["conflict_count"] == 1
    assert issue["conflicts"] == [{"id": "cast", "count": 2, "occurrences": [
        {"path": "rotation_model.actions[0].id", "kind": "action"},
        {"path": "template.hits[0].id", "kind": "hit"}]}]


def test_sequential_rebase_preserves_active_occupation_end_anchor():
    t, r = scenario('restore_rebase', initial=(10,), at=2)
    r['abilities'][0]['cooldown']['start'] = 'occupation_end'
    r['actions'] = [action(start=1, end=5, repeat={'mode': 'once'})]
    result = check_rotation(t, r)
    restore = next(row for row in result['event_log'] if row['id'] == 'restore')
    # The cast is still occupied until 5. Its remaining 15s recovery cannot
    # start at 2 just because another charge was restored.
    assert restore['ready_at'] == [20]


def test_rebase_preserves_remaining_span_after_prior_reduction():
    t, r = scenario('restore_rebase', initial=(15, 30), at=2)
    r['events'].insert(0, {**r['events'][0], 'id': 'reduce', 'operation': 'reduce', 'amount': 3, 't': 1})
    result = check_rotation(t, r)
    restore = next(row for row in result['event_log'] if row['id'] == 'restore')
    assert restore['ready_at'] == [17]
