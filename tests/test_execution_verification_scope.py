"""Real validators still check their own proof on a shared engine hit."""
import copy
import json

import pytest

from genshin_app.calculation_review import review_calculation
from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.counter_frame_receipt import validate_counter_frame_receipt
from genshin_app.equipment.delivery import build_calculation_delivery
from genshin_app.equipment.execution_verification import ExecutionVerificationSession
from genshin_app.equipment.resource_reaction_receipt import (
    validate_resource_reaction_receipt,
)
from genshin_app.equipment.service import result_bytes
from genshin_app.formula_workbook import FormulaWorkbookError, build_formula_workbook
from genshin_engine import mechanics
from tests.test_execution_verification_actual import (
    make,
)
from tests.test_shared_pool_app_delivery import authorize


def calculated(kind, tmp_path, request):
    db = tmp_path/'absent.db'
    return run_plan_calc(authorize(make(kind), db), db)


def count_engine(monkeypatch):
    calls = []
    actual = mechanics.execute_mechanics
    def counted(*args, **kwargs):
        calls.append(True)
        return actual(*args, **kwargs)
    monkeypatch.setattr(mechanics, 'execute_mechanics', counted)
    return calls


@pytest.mark.parametrize('kind,validator', [('counter', validate_counter_frame_receipt),
    ('reaction', validate_resource_reaction_receipt)])
def test_same_engine_cache_never_approves_changed_native_source(kind, validator, monkeypatch, tmp_path, request):
    value = calculated(kind, tmp_path, request)
    calls = count_engine(monkeypatch)
    with ExecutionVerificationSession() as session:
        args = [value[key] for key in ('plan', 'result', 'report', 'result_receipt')]
        validator(*args, _session=session)
        bad = copy.deepcopy(value)
        bad['plan']['manual_template']['characters'][0]['stats']['atk'] += 1
        with pytest.raises(ValueError):
            validator(*(bad[key] for key in ('plan', 'result', 'report', 'result_receipt')), _session=session)
        validator(*args, _session=session)
        assert len(calls) == 1


def test_complete_delivery_and_packaged_query_own_separate_single_sessions(tmp_path, monkeypatch):
    db = tmp_path/'absent.db'
    value = run_plan_calc(authorize(make('combined'), db), db, capture_display=True)
    before = copy.deepcopy(value)
    calls = count_engine(monkeypatch)
    built = build_calculation_delivery(value, input_file_sha='a'*64)
    assert len(calls) == 1
    package = json.loads(built['artifacts']['calculated'])
    review_calculation(package, {'view': 'hits'})
    assert len(calls) == 2  # A second top-level operation may not reuse a cache.
    assert value == before


def test_formula_prepared_once_fits_three_requests(monkeypatch, tmp_path, request):
    value = calculated('combined', tmp_path, request)
    before = copy.deepcopy(value)
    calls = count_engine(monkeypatch)
    with ExecutionVerificationSession(budget={'policy': 'execution-verification-budget-v2', 'limits': {'requests': 3}}) as session:
        assert build_formula_workbook(value, _session=session).startswith(b'PK')
        usage = session.report()['usage']
        assert usage['requests'] == 2 and usage['executions'] == 1
        assert usage['cache_hits'] == 1 and not session.report()['stopped']
    assert len(calls) == 1 and value == before


def test_formula_cumulative_requests_exhaust_before_another_replay(monkeypatch, tmp_path, request):
    from genshin_app.equipment import execution_verification as ev
    value = calculated('combined', tmp_path, request)
    before = copy.deepcopy(value)
    calls = count_engine(monkeypatch)
    with ExecutionVerificationSession(budget={'policy': 'execution-verification-budget-v2', 'limits': {'requests': 3}}) as session:
        build_formula_workbook(value, _session=session)
        with pytest.raises(FormulaWorkbookError) as caught:
            build_formula_workbook(value, _session=session)
        assert isinstance(caught.value.__cause__, ev.ExecutionVerificationError)
        assert caught.value.__cause__.code == 'verification_budget'
        assert session.report()['stopped']
        assert session.report()['usage']['requests'] == 3
        assert session.report()['usage']['cache_hits'] == 2
        with pytest.raises(ev.ExecutionVerificationError) as stopped:
            build_formula_workbook(value, _session=session)
        assert stopped.value.code == 'verification_stopped'
    assert len(calls) == 1 and value == before


@pytest.mark.parametrize('limit,executions', [(0, 0), (1, 1)])
def test_formula_budget_rejects_before_excess_request(monkeypatch, tmp_path, request, limit, executions):
    from genshin_app.equipment import execution_verification as ev
    value = calculated('combined', tmp_path, request)
    before = copy.deepcopy(value)
    calls = count_engine(monkeypatch)
    with ExecutionVerificationSession(budget={'policy': 'execution-verification-budget-v2', 'limits': {'requests': limit}}) as session:
        with pytest.raises(FormulaWorkbookError) as caught:
            build_formula_workbook(value, _session=session)
        assert isinstance(caught.value.__cause__, ev.ExecutionVerificationError)
        assert caught.value.__cause__.code == 'verification_budget'
        assert session.report()['stopped']
        assert session.report()['usage']['requests'] == limit
        assert session.report()['usage']['executions'] == executions
    assert len(calls) == executions and value == before


@pytest.mark.parametrize('entry', ['formula', 'json', 'query'])
def test_json_cannot_supply_internal_session(entry, tmp_path, request):
    value = calculated('combined', tmp_path, request)
    with pytest.raises(ValueError):
        if entry == 'formula':build_formula_workbook(value, _session={'cache_hits': 99})
        elif entry == 'json':result_bytes(value['result'], value['result_receipt'], 'json', _session={})
        else:review_calculation(value, _session={})


def test_exception_closes_owned_session(monkeypatch):
    closed = []
    close = ExecutionVerificationSession.close
    def record(session):
        closed.append(session)
        return close(session)
    monkeypatch.setattr(ExecutionVerificationSession, 'close', record)
    with pytest.raises(ValueError):
        build_formula_workbook({})
    assert len(closed) == 1 and closed[0].report()['closed']
