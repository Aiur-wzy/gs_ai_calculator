"""Nonaligned periodic end is a floor bound, never a shifted pulse."""
import copy

import pytest

from genshin_app.equipment.periodic_lifecycle import (
    PeriodicLifecycleError,
    project_periodic_lifecycle,
)
from tests.test_periodic_lifecycle import channel, control, establish, lifecycle

POLICY = 'while_active_inclusive'


def project(duration=10, **extra):
    args = {'channels': [channel(first_offset_ticks=2, interval_ticks=3, count=None, derive_count=POLICY)],
        'lifecycle': lifecycle(refresh='reset_duration', duration=duration),
        'establishments': [establish()], 'window_end_ticks': 30}
    args.update(extra)
    return project_periodic_lifecycle(**args)


def test_non_aligned_duration_uses_floor_without_moving_any_pulse():
    out = project()
    assert [r['time_ticks'] for r in out['occurrences']] == [2, 5, 8]


@pytest.mark.parametrize('duration,expected', [(11, [2, 5, 8, 11]), (10, [2, 5, 8]), (2, [2])])
def test_exact_and_nonexact_tails(duration, expected):
    assert [r['time_ticks'] for r in project(duration)['occurrences']] == expected


def test_reset_extends_active_time_without_resetting_phase():
    out = project(establishments=[establish(), establish('refresh', 6)])
    assert [r['time_ticks'] for r in out['occurrences']] == [2, 5, 8, 11, 14]
    assert {r['generation'] for r in out['occurrences']} == {1}


@pytest.mark.parametrize('operation', ['extend_duration', 'replace', 'cancel'])
def test_controls_preserve_declared_semantics(operation):
    life = lifecycle(refresh='replace_previous' if operation == 'replace' else 'extend_duration', duration=10)
    extra = {'duration_ticks': 5} if operation == 'extend_duration' else {}
    out = project(lifecycle=life, controls=[control('change', operation, 6, **extra)])
    expected = {'extend_duration': [2, 5, 8, 11, 14], 'replace': [2, 5, 8, 11, 14], 'cancel': [2, 5]}
    assert [r['time_ticks'] for r in out['occurrences']] == expected[operation]
    assert {r['generation'] for r in out['occurrences']} == ({1, 2} if operation == 'replace' else {1})


@pytest.mark.parametrize('pause', ['pause_preserve_phase', 'pause_reset_phase'])
def test_pause_phase_rules_still_apply(pause):
    out = project(lifecycle=lifecycle(duration=10, pause=pause), controls=[control('p', 'pause', 6), control('r', 'resume', 9)])
    assert [r['time_ticks'] for r in out['occurrences']] == [2, 5, 11]


def test_reset_phase_with_less_remaining_time_than_first_pulse_delivers_none():
    out = project(lifecycle=lifecycle(duration=10, pause='pause_reset_phase'),
        controls=[control('p', 'pause', 9), control('r', 'resume', 12)])
    assert [r['time_ticks'] for r in out['occurrences']] == [2, 5, 8]


def test_window_clips_delivery_not_bounded_future_plan_or_lifetime():
    out = project(window_end_ticks=7, projection_scope='finite_prefix')
    assert [r['time_ticks'] for r in out['occurrences']] == [2, 5]
    assert out['boundary']['pending_occurrences'][0]['time_ticks'] == 8
    assert out['boundary']['remaining_active_ticks'] == 3


@pytest.mark.parametrize('mode', ['strict_non_aligned', 'unknown', 'explicit_plus_policy', 'excessive_future'])
def test_negative_policy_and_capacity_are_not_weakened(mode):
    schedule = channel(first_offset_ticks=2, interval_ticks=3, count=None, derive_count=POLICY)
    if mode == 'strict_non_aligned': schedule['schedule']['derive_count'] = 'through_instance_end_inclusive'
    elif mode == 'unknown': schedule['schedule']['derive_count'] = 'invented'
    elif mode == 'explicit_plus_policy': schedule['schedule']['count'] = 3
    else: schedule['schedule'].update(first_offset_ticks=0, interval_ticks=1)
    with pytest.raises(PeriodicLifecycleError):
        project(channels=[schedule], lifecycle=lifecycle(duration=300 if mode == 'excessive_future' else 10),
            projection_scope='finite_prefix', window_end_ticks=7)


def test_public_exact_payload_schema_and_handler_share_the_policy():
    from genshin_app.equipment.agent_execution_materialization import (
        HANDLER_REGISTRY,
        agent_execution_materialization_capabilities,
    )
    from tests.test_agent_execution_materialization import _periodic_payload
    payload = _periodic_payload(first=1.283333, interval=.983333, duration=20.3, count=None)
    payload['schedule']['derive_count'] = POLICY
    schema = agent_execution_materialization_capabilities()['families']['periodic_channel']['payload_schema']
    from genshin_app.equipment.periodic_contract import (
        EQUAL_SCHEDULE_FIELDS,
        PERIODIC_FIELDS,
    )
    assert set(schema['properties']) == set(schema['required']) == PERIODIC_FIELDS
    assert schema['additionalProperties'] is False
    equal = schema['properties']['schedule']['oneOf'][0]
    assert set(equal['properties']) == set(equal['required']) == EQUAL_SCHEDULE_FIELDS
    assert POLICY in equal['properties']['derive_count']['enum']
    assert HANDLER_REGISTRY['periodic_channel'].validate_payload(payload, 'test') == payload
    bad = copy.deepcopy(payload); bad['schedule']['count'] = 20
    with pytest.raises(ValueError): HANDLER_REGISTRY['periodic_channel'].validate_payload(bad, 'test')
    bad = copy.deepcopy(payload); bad['unexpected'] = True
    with pytest.raises(ValueError): HANDLER_REGISTRY['periodic_channel'].validate_payload(bad, 'test')
