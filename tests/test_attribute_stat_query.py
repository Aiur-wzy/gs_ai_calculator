"""Read-only inspection of actual and explicitly synthetic saved receipts."""
import copy
import json
from pathlib import Path

import pytest

from genshin_app import calculation_review as candidate
from genshin_app.equipment.attribute_receipt import (
    attach_attribute_execution_binding,
    make_attribute_receipt,
)
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from tests.test_calculation_review import _saved


def resign_synthetic(value):
    """Seal an explicitly synthetic saved-consistency fixture, not game truth."""
    result, receipt = value['result'], value['result_receipt']
    declarations = {'contract':'attribute-ledger-v3'}
    result['attribute_ledger']['declarations'] = declarations
    result.setdefault('sampling_trace', [])
    report = {'mechanics_input_template': {}, 'mechanics_matches': [],
              'mechanics_model': {'attribute_model':declarations},
              **{key:'0'*64 for key in ('input_fingerprint','output_fingerprint','source_fingerprint')}}
    execution = {'template':{}, 'mechanics_model':report['mechanics_model'], 'rotation_model':{}, 'matches':[]}
    result['rotation_check'] = {'resolved_rotation_model':{}, 'input_fingerprint':fingerprint(execution)}
    attach_attribute_execution_binding(report, result)
    receipt.update({key:report[key] for key in ('input_fingerprint','output_fingerprint','source_fingerprint')})
    receipt['attribute_ledger'] = make_attribute_receipt({}, report, result)
    receipt['result_fingerprint'] = fingerprint(result)


def saved():
    value = _saved()
    rows = []
    for i, stat in enumerate(('em', 'atk', 'em', 'atk')):
        rows.append({'receipt_id': f'receipt-{i}', 'contract': 'attribute-ledger-v3',
            'query': {'actor': 'actor-a', 'stat': stat, 'basis': 'total', 'purpose': 'damage',
                'frame': {'id': f'frame-{i}', 'event_id': 'cast.0.1', 'origin': 0,
                          'run_id': f'run-{i}', 'phase': 'damage_projection', 't_us': i*100000}},
            'status': 'known', 'baseline': 100, 'value': 120,
            'contributions': [{'stat': stat, 'raw_evaluated_value': .2, 'application_base_value': 100,
                               'application_base_receipt_id': 'base-receipt', 'evaluated_value': 20}],
            'issues': []})
    value['result']['attribute_ledger'] = {'contract': 'attribute-ledger-v3', 'receipts': rows}
    resign_synthetic(value)
    return value


def test_stat_filter_pagination_preserves_actual_values_and_units():
    value = saved(); before = copy.deepcopy(value)
    filters = {'stat': 'atk', 'basis': 'total', 'character': 'actor-a', 'action_id': 'cast.0', 'start': .1}
    page = candidate.review_calculation(value, {'view': 'attributes', 'filters': filters, 'limit': 1})
    row = page['items'][0]
    assert page['total_rows'] == 2 and page['next_offset'] == 1
    assert row['id'] == 'receipt-1' and row['stat'] == 'atk' and row['basis'] == 'total'
    assert row['event_id'] == 'cast.0.1' and row['t'] == .1
    assert row['baseline'] == 100 and row['value'] == 120
    assert row['contributions'][0]['raw_evaluated_value'] == .2
    assert row['contributions'][0]['application_base_value'] == 100
    assert row['json_pointer'] == '/result/attribute_ledger/receipts/1'
    second = candidate.review_calculation(value, {'view': 'attributes', 'filters': filters, 'offset': 1})
    assert [r['id'] for r in second['items']] == ['receipt-3']
    assert value == before and page['engine_summary'] == value['result']['summary']


@pytest.mark.parametrize('filters,ids', [
    ({'receipt_id': 'receipt-2'}, ['receipt-2']), ({'frame_id': 'frame-3'}, ['receipt-3']),
    ({'start': .1, 'end': .3}, ['receipt-1', 'receipt-2']), ({'stat': 'def'}, []),
    ({'source_id': 'not-a-provenance-inference'}, []), ({'event_id': 'receipt-1'}, []),
])
def test_exact_filters(filters, ids):
    assert [r['id'] for r in candidate.review_calculation(saved(), {'view': 'attributes', 'filters': filters})['items']] == ids


@pytest.mark.parametrize('time', [True, -1, 10**400, 1.2, '100'])
def test_invalid_time_is_controlled(time):
    value = saved(); value['result']['attribute_ledger']['receipts'][0]['query']['frame']['t_us'] = time
    resign_synthetic(value)
    with pytest.raises(PlanError) as error:
        candidate.review_calculation(value, {'view': 'attributes'})
    assert error.value.reason == 'invalid_review_input'


def test_missing_time_and_unknown_value_remain_missing():
    value = saved(); row = value['result']['attribute_ledger']['receipts'][0]
    del row['query']['frame']['t_us']; row['status'] = 'pending'; row['value'] = None
    resign_synthetic(value)
    result = candidate.review_calculation(value, {'view': 'attributes', 'limit': 1})['items'][0]
    assert 't' not in result and result['value'] is None and result['status'] == 'pending'


def test_attribute_only_filters_do_not_silently_change_other_views():
    with pytest.raises(PlanError):
        candidate.review_calculation(saved(), {'view': 'hits', 'filters': {'stat': 'atk'}})
    value = saved()
    for view in ('summary', 'hits', 'sampling', 'mechanics', 'target_conditions', 'actions', 'external'):
        expected=json.loads((Path(__file__).parent/'fixtures/attribute_query_legacy.json').read_text(encoding='utf8'))
        # The projection oracle is unchanged; its new dedicated fixture proof
        # intentionally has a new saved identity and explicit synthetic refs.
        expected[view]['receipt'].update({key:value['result_receipt'][key] for key in
            ('input_fingerprint','source_fingerprint','result_fingerprint')})
        expected[view]['validation_scope'] = {
            'summary': '旧结果未保存统一验证范围；计算接纳不代表游戏机制完整。',
            'game_mechanism_completeness': 'not_independently_certified'}
        assert candidate.review_calculation(value, {'view': view})==expected[view]


def test_actual_app_receipts_are_queryable_without_recalculation(tmp_path):
    from genshin_app.equipment import run_plan_calc
    from tests.test_attribute_graph_app import adopt, graph_plan
    db=tmp_path/'absent.db'
    value=run_plan_calc(adopt(graph_plan(),db),db)
    rows=value['result']['attribute_ledger']['receipts']
    page=candidate.review_calculation({key:value[key] for key in ('result','result_receipt')},
                                      {'view':'attributes','limit':200})
    assert page['total_rows']==len(rows) and page['items']
    for item in page['items']:
        original=rows[int(item['json_pointer'].rsplit('/',1)[1])]
        assert item['receipt_id']==original['receipt_id'] and item['value']==original.get('value')
        assert item['stat']==original['query']['stat']
