"""Separate finite node bounds must preserve failed work and warm-copy costs."""
import copy

import pytest

from genshin_app.equipment import execution_verification as subject
from genshin_engine import mechanics


def session_with(**limits):
    return subject.ExecutionVerificationSession(budget={'policy': 'execution-verification-budget-v2', 'limits': limits})


def execution():
    return {'template': {}, 'rotation_model': {}, 'mechanics_model': {}, 'matches': []}


def test_larger_output_policy_never_accepts_tuple_or_oversized_input(monkeypatch):
    monkeypatch.setattr(mechanics, 'execute_mechanics', lambda **kw: pytest.fail('invalid input reached engine'))
    for bad in ({**execution(), 'matches': ()},
                {**execution(), 'template': {'unapproved_extra': 1}}):
        with pytest.raises(subject.ExecutionVerificationError):
            session_with(input_nodes=9, output_nodes=500).replay(bad)


def test_failed_output_does_not_cache_or_free_work_and_retry_is_complete(monkeypatch):
    values = [{'rows': [1, 2, 3]}, {'ok': 1}]
    calls = []

    def run(**kw):
        calls.append(True)
        return values[len(calls)-1]

    monkeypatch.setattr(mechanics, 'execute_mechanics', run)
    session = session_with(output_nodes=5)
    with pytest.raises(subject.ExecutionVerificationError):
        session.replay(execution())
    failed = session.report()['usage']
    assert session._cache == [] and failed['executions'] == 1 and failed['work'] > 0
    assert session.replay(execution()) == {'ok': 1}
    assert session.report()['usage']['executions'] == 2
    assert session.report()['usage']['work'] > failed['work']


@pytest.mark.parametrize('field', ['returned_bytes', 'work'])
def test_budget_is_shared_across_distinct_and_warm_requests_and_stops_permanently(monkeypatch, field):
    calls = []

    def run(**kw):
        calls.append(True)
        return {'value': kw['matches']}

    monkeypatch.setattr(mechanics, 'execute_mechanics', run)
    changed = {**execution(), 'matches': ['second']}
    probe = subject.ExecutionVerificationSession()
    probe.replay(execution()); probe.replay(changed)
    limits = {field: probe.report()['usage'][field]}
    probe.close(); calls.clear()
    session = session_with(**limits)
    session.replay(execution()); session.replay(changed)
    assert session.report()['usage']['executions'] == len(calls) == 2
    with pytest.raises(subject.ExecutionVerificationError) as caught:
        session.replay(execution())
    assert caught.value.code == 'verification_budget'
    frozen = copy.deepcopy(session.report())
    assert frozen['stopped']
    with pytest.raises(subject.ExecutionVerificationError):
        session.replay(changed)
    assert session.report() == frozen and len(calls) == 2


def test_byte_and_retained_limits_remain_separate_finite_budgets():
    limits = subject.LIMITS
    assert limits['input_nodes'] == 1_000_000 and limits['output_nodes'] == 16_000_000
    assert limits['input_bytes'] == 32*1024*1024 and limits['output_bytes'] == 256*1024*1024
    assert limits['retained_input_bytes'] == 64*1024*1024
    assert limits['retained_output_bytes'] == 512*1024*1024
    assert limits['returned_bytes'] == 2048*1024*1024 and limits['work'] == 256_000_000
