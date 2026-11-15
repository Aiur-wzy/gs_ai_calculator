"""Compact engine-output regression and distinct bounded input/output obligations."""
import copy

import pytest

from genshin_app.equipment import execution_verification as subject
from genshin_engine import mechanics


def session_with(**limits):
    return subject.ExecutionVerificationSession(budget={'policy': 'execution-verification-budget-v2', 'limits': limits})


def execution():
    return {'template': {}, 'rotation_model': {}, 'mechanics_model': {}, 'matches': []}


def test_compact_large_output_remains_complete_on_cold_and_warm_read(monkeypatch):
    # More than the former shared 200k bound, without a large saved fixture.
    output = {'rows': [[i] * 1000 for i in range(242)]}
    calls = []
    def run(**value):
        calls.append(True)
        return output
    monkeypatch.setattr(mechanics, 'execute_mechanics', run)
    session = subject.ExecutionVerificationSession()
    first = session.replay(execution())
    assert first == output
    first['rows'][0][0] = -1
    second = session.replay(execution())
    assert second == output and len(calls) == 1
    assert session.report()['usage']['returned_bytes'] > 0


def test_large_input_still_stops_before_engine(monkeypatch):
    value = execution()
    value['template']['rows'] = [[0] * 1000 for _ in range(201)]
    monkeypatch.setattr(mechanics, 'execute_mechanics', lambda **kw: pytest.fail('input budget bypassed'))
    with pytest.raises(subject.ExecutionVerificationError) as caught:
        session_with(input_nodes=200000).replay(value)
    assert caught.value.code == 'verification_shape'


def test_output_node_limit_does_not_cache_partial_result(monkeypatch):
    monkeypatch.setattr(mechanics, 'execute_mechanics', lambda **kw: {'rows': [0, 1, 2]})
    session = session_with(output_nodes=5)
    with pytest.raises(subject.ExecutionVerificationError) as caught:
        session.replay(execution())
    assert caught.value.code == 'verification_shape'
    assert session._cache == []
    assert session.report()['usage']['executions'] == 1
    assert session.report()['usage']['work'] > 0


def test_input_and_output_node_caps_are_distinct(monkeypatch):
    monkeypatch.setattr(mechanics, 'execute_mechanics', lambda **kw: {'rows': list(range(40))})
    session = session_with(input_nodes=9, output_nodes=100)
    assert len(session.replay(execution())['rows']) == 40
    bad = execution()
    bad['template']['extra'] = 1
    with pytest.raises(subject.ExecutionVerificationError):
        session.replay(bad)
    assert session.report()['usage']['executions'] == 1


@pytest.mark.parametrize('field', ['work', 'returned_bytes'])
def test_warm_large_result_keeps_cumulative_budget(monkeypatch, field):
    monkeypatch.setattr(mechanics, 'execute_mechanics', lambda **kw: {'rows': list(range(250))})
    probe = subject.ExecutionVerificationSession()
    probe.replay(execution())
    session = session_with(**{field: probe.report()['usage'][field]})
    probe.close()
    session.replay(execution())
    with pytest.raises(subject.ExecutionVerificationError) as caught:
        session.replay(execution())
    assert caught.value.code == 'verification_budget'
    assert session.report()['stopped']
    assert session.report()['usage']['executions'] == 1
    before = copy.deepcopy(session.report())
    with pytest.raises(subject.ExecutionVerificationError):
        session.replay(execution())
    assert session.report() == before


def test_output_byte_cap_remains_independent(monkeypatch):
    monkeypatch.setattr(mechanics, 'execute_mechanics', lambda **kw: {'s': 'long'})
    session = session_with(output_bytes=3)
    with pytest.raises(subject.ExecutionVerificationError) as caught:
        session.replay(execution())
    assert caught.value.code == 'verification_size' and session._cache == []
