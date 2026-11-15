"""Pure service obligations; no actual-source approval is implied."""
import copy
import threading

import pytest

import genshin_app.equipment.execution_verification as candidate
from genshin_app.equipment.execution_verification import (
    ExecutionVerificationError,
    ExecutionVerificationSession,
)
from genshin_engine import mechanics


def session_with(**limits):
    return ExecutionVerificationSession(budget={'policy': 'execution-verification-budget-v2', 'limits': limits})


def input_value():
    return {'template': {'test': 1}, 'rotation_model': {}, 'mechanics_model': {}, 'matches': []}


@pytest.fixture
def calls(monkeypatch):
    records = []
    def evaluate(**execution):
        records.append(copy.deepcopy(execution))
        return {'result': {'value': execution['template'].get('test'), 'nested': [7]}, 'status': 'passed'}
    monkeypatch.setattr(mechanics, 'execute_mechanics', evaluate)
    return records


def test_identical_content_shares_real_call_but_isolated_deep_results(calls):
    with ExecutionVerificationSession() as session:
        first = session.replay(input_value())
        first['result']['nested'].append(100)
        second = session.replay(copy.deepcopy(input_value()))
        assert second['result']['nested'] == [7] and len(calls) == 1
        assert session.report()['usage']['cache_hits'] == 1
    with pytest.raises(ExecutionVerificationError): session.replay(input_value())


@pytest.mark.parametrize('field', sorted(candidate.KEYS))
def test_each_execution_member_changes_real_lookup(field, calls):
    session = ExecutionVerificationSession(); original = input_value(); session.replay(original)
    if field == 'matches': original[field] = [{'operation': 'new'}]
    else: original[field]['changed'] = True
    session.replay(original)
    assert len(calls) == 2


def test_numeric_type_and_list_order_are_not_collapsed(calls):
    session = ExecutionVerificationSession(); value = input_value(); session.replay(value)
    value['template']['test'] = 1.0; session.replay(value)
    value['matches'] = [1, 2]; session.replay(value)
    value['matches'] = [2, 1]; session.replay(value)
    assert len(calls) == 4


def test_cross_session_does_not_share_cache(calls):
    ExecutionVerificationSession().replay(input_value())
    ExecutionVerificationSession().replay(input_value())
    assert len(calls) == 2


def test_user_cannot_inject_hash_or_cached_result(calls):
    session = ExecutionVerificationSession(); value = input_value()
    value['cached_result'] = {'status': 'passed'}
    with pytest.raises(ExecutionVerificationError): session.replay(value)
    assert calls == [] and session.report()['usage']['requests'] == 1


def test_engine_input_and_cached_output_detach_from_external_mutation(monkeypatch):
    value = input_value(); shared = {'result': {'nested': [1]}, 'status': 'passed'}
    def evaluate(**execution):
        value['template']['test'] = 99
        assert execution['template']['test'] == 1
        return shared
    monkeypatch.setattr(mechanics, 'execute_mechanics', evaluate)
    session = ExecutionVerificationSession(); session.replay(value)
    shared['result']['nested'].append(22)
    assert session.replay(input_value())['result']['nested'] == [1]


def test_reentry_and_close_inside_engine_rejected_without_corrupting_result(monkeypatch):
    session = ExecutionVerificationSession()
    def evaluate(**execution):
        for action in (lambda: session.replay(input_value()), session.close, session.report):
            with pytest.raises(ExecutionVerificationError): action()
        return {'result': {'value': 5}}
    monkeypatch.setattr(mechanics, 'execute_mechanics', evaluate)
    assert session.replay(input_value())['result']['value'] == 5


def test_foreign_thread_cannot_read_or_use_session(calls):
    session = ExecutionVerificationSession(); errors = []
    def run():
        for action in (lambda: session.replay(input_value()), session.close, session.report):
            try: action()
            except ExecutionVerificationError as error: errors.append(error.code)
    thread = threading.Thread(target=run); thread.start(); thread.join()
    assert len(errors) == 3 and calls == []
    session.replay(input_value()); assert len(calls) == 1


@pytest.mark.parametrize('value', [float('nan'), float('inf'), {1: 'bad'}, '\ud800'])
def test_invalid_json_never_reaches_engine(value, calls):
    execution = input_value(); execution['template']['bad'] = value
    with pytest.raises(ExecutionVerificationError): ExecutionVerificationSession().replay(execution)
    assert calls == []


def test_cycles_and_depth_are_bounded(calls, monkeypatch):
    execution = input_value(); execution['template']['cycle'] = execution
    with pytest.raises(ExecutionVerificationError): ExecutionVerificationSession().replay(execution)
    with pytest.raises(ExecutionVerificationError): session_with(depth=1).replay(input_value())
    assert calls == []


def test_failed_requests_cannot_reset_cumulative_budget(calls, monkeypatch):
    session = session_with(requests=2)
    for _ in range(2):
        with pytest.raises(ExecutionVerificationError): session.replay({})
    with pytest.raises(ExecutionVerificationError): session.replay(input_value())
    assert session.report()['stopped'] and calls == []


def test_failed_engine_calls_consume_execution_attempt_budget(monkeypatch):
    calls = []
    def failed(**execution):
        calls.append(execution); raise RuntimeError('engine failed')
    monkeypatch.setattr(mechanics, 'execute_mechanics', failed)
    session = session_with(executions=2)
    for _ in range(2):
        with pytest.raises(RuntimeError): session.replay(input_value())
    with pytest.raises(ExecutionVerificationError): session.replay(input_value())
    assert len(calls) == 2


def test_pending_output_is_not_changed_to_success(monkeypatch):
    monkeypatch.setattr(mechanics, 'execute_mechanics', lambda **execution: {'status': 'pending', 'result': None})
    session = ExecutionVerificationSession()
    assert session.replay(input_value()) == {'status': 'pending', 'result': None}
    assert session.replay(input_value()) == {'status': 'pending', 'result': None}


def test_work_exhaustion_rejects_before_encoding_children(calls, monkeypatch):
    session = session_with(work=0)
    monkeypatch.setattr(candidate.json, 'dumps', lambda *a, **k: pytest.fail('encoded after work exhaustion'))
    with pytest.raises(ExecutionVerificationError): session.replay(input_value())
    assert calls == [] and session.report()['usage']['work'] == 0


def test_returned_copy_budget_precedes_copy(calls, monkeypatch):
    probe = ExecutionVerificationSession(); probe.replay(input_value())
    session = session_with(returned_bytes=probe.report()['usage']['returned_bytes'])
    probe.close(); calls.clear()
    session.replay(input_value())
    with pytest.raises(ExecutionVerificationError): session.replay(input_value())
    assert len(calls) == 1 and session.report()['stopped']


def test_projected_private_measurements_are_exact_and_no_rescan(calls, monkeypatch):
    session = ExecutionVerificationSession()
    session.replay(input_value())
    before = session.report()['usage']
    result = session.replay(input_value(), _result_fields=('nested',))
    after = session.report()['usage']
    encoded = candidate.json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode('utf8')
    assert after['returned_bytes'] - before['returned_bytes'] == len(encoded)
    assert after['scanned_output_nodes'] == before['scanned_output_nodes']
    result['result']['nested'].append('changed')
    assert session.replay(input_value(), _result_fields=('nested',))['result']['nested'] == [7]


def test_projected_long_colliding_prefix_keys_use_exact_independent_metrics(monkeypatch):
    keys = ['x' * 1100 + suffix for suffix in ('a', 'b')]
    actual = {'result': {keys[0]: '中文' * 6000, keys[1]: ['different'] * 2000}}
    monkeypatch.setattr(mechanics, 'execute_mechanics', lambda **kw: actual)
    session = ExecutionVerificationSession()
    for key in keys:
        before = session.report()['usage']['returned_bytes']
        result = session.replay(input_value(), _result_fields=(key,))
        assert session.report()['usage']['returned_bytes'] - before == len(
            candidate.json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode('utf8'))


@pytest.mark.parametrize('dimension,limit', [('output_bytes', 18), ('output_nodes', 5)])
def test_projected_new_envelope_is_checked_against_single_item_limits(monkeypatch, dimension, limit):
    monkeypatch.setattr(mechanics, 'execute_mechanics', lambda **kw: {'result': {'x': 1}})
    session = session_with(**{dimension: limit})
    with pytest.raises(ExecutionVerificationError) as error:
        session.replay(input_value(), _result_fields=('x',))
    assert error.value.details['issues'][0]['dimension'] == dimension
    assert session.report()['usage']['returned_bytes'] == 0


@pytest.mark.parametrize('budget', ['input_bytes', 'retained_input_bytes'])
def test_input_byte_budget_stops_before_engine(budget, calls, monkeypatch):
    with pytest.raises(ExecutionVerificationError): session_with(**{budget: 1}).replay(input_value())
    assert calls == []


@pytest.mark.parametrize('budget', ['output_bytes', 'retained_output_bytes'])
def test_oversize_engine_result_cannot_be_delivered_as_verified(budget, calls, monkeypatch):
    session = session_with(**{budget: 1})
    with pytest.raises(ExecutionVerificationError): session.replay(input_value())
    assert len(calls) == 1
