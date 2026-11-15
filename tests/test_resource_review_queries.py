"""New review projections remain bounded and tied to untouched saved rows."""
from __future__ import annotations

import copy

import pytest

from genshin_app import calculation_review
from genshin_app.calculation_review import review_calculation
from genshin_app.equipment.files import fingerprint, json_bytes
from genshin_app.equipment.schema import PlanError
from tests.test_calculation_review import _saved


def resource_saved():
    saved = _saved()
    saved['result']['resource_counters'] = {'trace': [
        {'counter_id': '累计', 'time_us': 100_000*i, 'requested': 8, 'resource_accepted': 0,
         'accepted': 0, 'before': 0, 'after': 0, 'source_id': '源', 'event_id': f'row{i}'}
        for i in range(7)]}
    saved['result']['resource_counter_views'] = {'trace': [
        {'event_id': 'v', 't': .3, 'effect_source_id': 'effect', 'rule_id': 'reader',
         'reading': {'source_id': 'view', 'counter_source_id': 'counter', 'raw_atoms': 19,
                     'counter_cursor': {'ledger_sequence': 5}}, 'single_value': .8,
         'layer_count': 2, 'combined_value': 1.6}]}
    saved['result_receipt']['result_fingerprint'] = fingerprint(saved['result'])
    return saved


def test_offset_pages_preserve_raw_integer_units_zeroes_and_original_positions():
    saved = resource_saved()
    before = copy.deepcopy(saved)
    request = {'view': 'resource_counters', 'limit': 2, 'filters': {'start': .2, 'end': .6, 'source_id': '源'}}
    rows, offset = [], 0
    while offset is not None:
        page = review_calculation(saved, request | {'offset': offset})
        assert page['receipt']['result_fingerprint'] == saved['result_receipt']['result_fingerprint']
        assert page['total_rows'] == 4
        rows.extend(page['items'])
        offset = page['next_offset']
    assert [r['json_pointer'] for r in rows] == [f'/result/resource_counters/trace/{i}' for i in range(2, 6)]
    assert [r['time_us'] for r in rows] == [200_000, 300_000, 400_000, 500_000]
    assert [r['t'] for r in rows] == [.2, .3, .4, .5]
    assert all(r['requested'] == 8 and r['resource_accepted'] == r['accepted'] == 0 for r in rows)
    assert saved == before


@pytest.mark.parametrize('source', ['effect', 'view', 'counter'])
def test_explicit_reading_source_filters_do_not_guess_missing_actor(source):
    saved = resource_saved()
    page = review_calculation(saved, {'view': 'resource_counter_views', 'filters': {'source_id': source}})
    assert page['total_rows'] == 1 and page['items'][0]['reading']['raw_atoms'] == 19
    assert not review_calculation(saved, {'view': 'resource_counter_views', 'filters': {'character': 'a'}})['items']
    assert not review_calculation(saved, {'view': 'resource_counter_views', 'filters': {'source_id': 'counter-prefix'}})['items']


def test_reading_clip_has_original_pointer_and_final_utf8_budget(monkeypatch):
    saved = resource_saved()
    saved['result']['resource_counter_views']['trace'][0]['reading']['source_id'] = '长'*10_000
    saved['result_receipt']['result_fingerprint'] = fingerprint(saved['result'])
    page = review_calculation(saved, {'view': 'resource_counter_views'})
    assert len(page['items'][0]['reading']['source_id']) < 600
    assert page['items'][0]['json_pointer'] == '/result/resource_counter_views/trace/0'
    size = len(json_bytes(page))
    monkeypatch.setattr(calculation_review, 'MAX_RESPONSE_BYTES', size-1)
    with pytest.raises(PlanError) as exc:
        review_calculation(saved, {'view': 'resource_counter_views'})
    assert exc.value.reason == 'review_size_limit'


def test_modified_saved_reading_refuses_same_old_receipt():
    saved = resource_saved()
    saved['result']['resource_counter_views']['trace'][0]['reading']['raw_atoms'] += 1
    with pytest.raises(PlanError, match='指纹'):
        review_calculation(saved, {'view': 'resource_counter_views'})


def test_huge_integer_time_request_is_controlled_error():
    with pytest.raises(PlanError):
        review_calculation(resource_saved(), {'view': 'resource_counters', 'filters': {'start': 10**400}})


def test_huge_saved_microsecond_time_is_controlled_error():
    saved = resource_saved()
    saved['result']['resource_counters']['trace'][0]['time_us'] = 10**400
    saved['result_receipt']['result_fingerprint'] = fingerprint(saved['result'])
    with pytest.raises(PlanError):
        review_calculation(saved, {'view': 'resource_counters'})
