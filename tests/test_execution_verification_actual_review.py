"""Independent actual sharing and prefix checks from small current native plans."""
import copy
import json

import pytest

from genshin_app.calculation_review import review_calculation
from genshin_app.equipment.execution_verification import ExecutionVerificationSession
from genshin_app.equipment.resource_reaction_receipt import (
    ADOPTION,
    validate_resource_reaction_receipt,
)
from genshin_app.equipment.service import result_bytes
from genshin_app.formula_workbook import build_formula_workbook
from tests.resource_reaction_review_helpers import count_engine, live_case, resign


def check(value, session):
    return validate_resource_reaction_receipt(
        *(value[k] for k in ('plan', 'result', 'report', 'result_receipt')), _session=session)


@pytest.mark.parametrize('field', ['source_owner', 'review_mode', 'authority_rule_id'])
def test_cached_engine_does_not_approve_rehashed_source_metadata(tmp_path, monkeypatch, field):
    value = live_case(tmp_path)
    before = copy.deepcopy(value)
    calls = count_engine(monkeypatch)
    with ExecutionVerificationSession() as session:
        check(value, session)
        bad = copy.deepcopy(value)
        bad['result'][ADOPTION]['operations'][0][field] = {
            'source_owner': 'b', 'review_mode': 'source_only', 'authority_rule_id': 'pool-authority'}[field]
        resign(bad)
        with pytest.raises(ValueError):
            check(bad, session)
        check(value, session)
        assert len(calls) == 1
    assert value == before


def test_two_public_exports_get_distinct_owned_sessions(tmp_path, monkeypatch):
    value = live_case(tmp_path)
    calls = count_engine(monkeypatch)
    result_bytes(value['result'], value['result_receipt'], 'json')
    result_bytes(value['result'], value['result_receipt'], 'xlsx')
    assert len(calls) == 2


def test_nested_export_uses_session_but_return_mutation_cannot_poison_it(tmp_path, monkeypatch):
    value = live_case(tmp_path)
    before = copy.deepcopy(value)
    calls = count_engine(monkeypatch)
    with ExecutionVerificationSession() as session:
        check(value, session)
        exported = json.loads(result_bytes(value['result'], value['result_receipt'], 'json', _session=session))
        exported['result']['summary']['total_damage'] = -1
        check(value, session)
        assert len(calls) == 1
    assert value == before


@pytest.mark.parametrize('route', ['json', 'xlsx', 'formula', 'query'])
def test_prefix_window_rehash_rejected_at_each_actual_exit(tmp_path, route):
    db = tmp_path / 'absent.db'
    value = live_case(tmp_path, 'failed-prefix')
    assert value['result']['mechanics']['complete_first_cycle'] is False
    assert value['result']['summary']['dps'] is None
    assert value['result']['resource_reactions']['verified_window'] == 'executed_prefix'
    bad = copy.deepcopy(value)
    bad['result']['resource_reactions']['verified_window'] = 'complete_reported_input'
    resign(bad)
    with pytest.raises(ValueError):
        if route in ('json', 'xlsx'):
            result_bytes(bad['result'], bad['result_receipt'], route)
        elif route == 'formula':
            build_formula_workbook(bad)
        else:
            review_calculation(bad, {'view': 'hits'})
    assert not db.exists()


def test_true_prefix_all_four_exits_share_one_replay_without_mutation(tmp_path, monkeypatch):
    value = live_case(tmp_path, 'failed-prefix')
    before = copy.deepcopy(value)
    calls = count_engine(monkeypatch)
    with ExecutionVerificationSession() as session:
        check(value, session)
        result_bytes(value['result'], value['result_receipt'], 'json', _session=session)
        result_bytes(value['result'], value['result_receipt'], 'xlsx', _session=session)
        build_formula_workbook(value, _session=session)
        review_calculation(value, {'view': 'hits'}, _session=session)
        assert len(calls) == 1
    assert value == before
