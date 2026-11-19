import pytest

from genshin_engine.timed_resource_window import (
    ResourceWindowPolicy, ResourceWindowState, advance_resource_window,
    trigger_resource_window, grant_resource, consume_resource,
)

P = ResourceWindowPolicy(1_500_000, 6_000_000, 4)


def test_refresh_keeps_cadence_and_window_end_generates_once():
    s, _ = trigger_resource_window(ResourceWindowState(), P, 0, 3_000_000)
    s, _ = trigger_resource_window(s, P, 1_000_000, 3_000_000)
    s, rows = advance_resource_window(s, P, 4_000_000)
    assert [r.time for r in rows] == [1_500_000, 3_000_000]
    assert s.count == 2 and s.expires_at == 9_000_000
    assert s.next_generation is None
    s, rows = advance_resource_window(s, P, 9_000_000)
    assert s.count == 0 and rows[0].operation == 'expired'


def test_advance_in_chunks_matches_one_advance():
    s, _ = trigger_resource_window(ResourceWindowState(), P, 0, 6_000_000)
    expected, rows = advance_resource_window(s, P, 20_000_000)
    collected = []
    for time in range(1_000_000, 20_000_001, 1_000_000):
        s, part = advance_resource_window(s, P, time)
        collected.extend(part)
    assert s == expected and tuple(collected) == rows
    assert [r.time for r in rows if r.operation == 'created'] == [1_500_000, 3_000_000, 4_500_000, 6_000_000]


def test_overflow_separate_from_stored_count_and_all_expiry_refreshed():
    s, _ = grant_resource(ResourceWindowState(), P, 0, 4)
    s, rows = grant_resource(s, P, 1, 3)
    assert s.count == 4 and s.expires_at == 6_000_001
    assert [(r.operation, r.amount) for r in rows] == [('created', 3), ('overflow', 3)]


def test_consumption_does_not_cancel_generation_or_refresh_lifetime():
    s, _ = trigger_resource_window(ResourceWindowState(), P, 0, 3_000_000)
    s, _ = consume_resource(s, P, 1_500_000, 1)
    assert s.count == 0 and s.next_generation == 3_000_000
    s, _ = advance_resource_window(s, P, 3_000_000)
    assert s.count == 1
    original = s
    with pytest.raises(ValueError, match='required=2, available=1'):
        consume_resource(s, P, 3_000_000, 2)
    assert s == original


def test_generation_at_expiry_restarts_resource_without_resurrection():
    policy = ResourceWindowPolicy(10, 10, 4)
    s, _ = trigger_resource_window(ResourceWindowState(), policy, 0, 20)
    s, rows = advance_resource_window(s, policy, 20)
    assert [(r.time, r.operation) for r in rows] == [(10, 'created'), (20, 'expired'), (20, 'created')]
    assert s.count == 1


@pytest.mark.parametrize('value', [True, -1, 0, 1.5])
def test_invalid_policy_rejected(value):
    with pytest.raises(ValueError):
        ResourceWindowPolicy(value, 10, 4)
