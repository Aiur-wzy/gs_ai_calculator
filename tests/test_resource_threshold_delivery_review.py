"""Independent saved threshold reporting, cached Excel values and exact joins."""
from __future__ import annotations

import copy

import pytest

from genshin_app.calculation_review import review_calculation
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_app.export import resource_threshold_rows
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from genshin_app.resource_threshold_reporting import (
    FIELDS,
    threshold_outcome,
    threshold_projection,
)
from tests.test_freeze_export_review import workbook_sheets
from tests.test_resource_threshold_delivery import actual, saved_result


@pytest.mark.parametrize('status', ['pending', 'excluded'])
def test_exact_join_does_not_upgrade_nonready_source_or_match_alias(status):
    row = {'binding_id': 'bound'}
    ledger = {'source_adoptions': [{'id': 'bound', 'source_id': 'source:buff', 'status': status},
                                   {'id': 'bound-extra', 'source_id': 'source:alias', 'status': 'ready'}]}
    before = copy.deepcopy((row, ledger))
    assert threshold_projection(row, ledger, FIELDS[1]) == row
    assert (row, ledger) == before


def test_trace_stage_not_outcome_alone_is_activation_authority():
    assert threshold_outcome({'type': 'issued', 'outcome': 'activated'}, FIELDS[1]) == '执行过程未知'
    assert threshold_outcome({'type': 'claimed', 'outcome': 'activated'}, FIELDS[1]) == '执行过程未知'
    assert threshold_outcome({'type': 'threshold_dispatch_failed', 'outcome': 'pending'}, FIELDS[1]) != '收益已激活'
    assert threshold_outcome({'type': 'threshold_dispatch_completed', 'outcome': 'not_activated'}, FIELDS[1]).startswith('未激活')


def test_paging_preserves_exact_parent_ids_and_result_identity(tmp_path):
    out = actual(tmp_path)
    field = FIELDS[1]
    positive = review_calculation(out, {'view': field})
    assert positive['validation'] == 'full_calculation_package'
    assert positive['total_rows'] == 1
    row = out['result'][field]['trace'][0]
    out['result'][field]['trace'] = [dict(row, source_event_id=f'h{i}', dispatch_run_id=f'd{i}') for i in range(5)]
    out['result_receipt']['result_fingerprint'] = fingerprint(out['result'])
    original = copy.deepcopy(out)
    pages = [review_calculation(saved_result(out), {'view': field, 'offset': offset, 'limit': 2}) for offset in (0, 2, 4)]
    assert all(page['validation'] == 'saved_result_fingerprint' for page in pages)
    assert [r['event_id'] for p in pages for r in p['items']] == [f'h{i}' for i in range(5)]
    assert [r['json_pointer'] for p in pages for r in p['items']] == [f'/result/{field}/trace/{i}' for i in range(5)]
    assert review_calculation(saved_result(out), {'view': field, 'filters': {'event_id': 'h'}})['total_rows'] == 0
    assert review_calculation(saved_result(out), {'view': field, 'filters': {'event_id': 'h3'}})['total_rows'] == 1
    assert out == original
    out['result'][field]['trace'][0]['after'] += 1
    with pytest.raises(PlanError):
        review_calculation(saved_result(out), {'view': field})


def test_zero_origin_and_actual_interval_are_retained_in_readonly_projection(tmp_path):
    out = actual(tmp_path, 'post')
    rows = resource_threshold_rows(out['result'], FIELDS[1])
    assert rows[2][0:2] == [.2, 0]
    assert rows[2][3] == '收益已激活'
    assert [h['expected'] for h in out['result']['hits']] == [45, 90]
    state, = out['result']['buff_state_timeline']['states']
    assert (state['start'], state['end']) == (.2, 1.2)


def test_formula_cache_matches_actual_damage_and_threshold_edit_is_regenerate_required(tmp_path):
    out = actual(tmp_path, 'post')
    support = formula_workbook_support(out)
    assert support['editable']['resource_thresholds_and_sources'] == 'regenerate_required'
    assert support['editable']['shared_pools_and_resource_gates'] == 'regenerate_required'
    sheets = workbook_sheets(build_formula_workbook(out))
    assert [sheets['逐事件'][f'V{i}']['value'] for i in (5, 6)] == [45, 90]
    for name in ('资源阈值观察', '阈值收益执行'):
        assert not any(c['formula'] for c in sheets[name].values())


def test_no_trace_never_promotes_issued_or_claimed_to_summary_row(tmp_path):
    out = actual(tmp_path)
    record = out['result'][FIELDS[1]]
    assert record['authority']['issued'] and record['authority']['claimed']
    record['trace'] = []
    rows = resource_threshold_rows(out['result'], FIELDS[1])
    assert not any(row and row[0] == .2 for row in rows)
    assert not any(len(row) > 3 and row[3] == '收益已激活' for row in rows)


def test_saved_huge_time_export_has_controlled_error_instead_of_python_overflow():
    result = {FIELDS[0]: {'trace': [{'t_us': 10 ** 400}]}}
    with pytest.raises((PlanError, ValueError)):
        resource_threshold_rows(result, FIELDS[0])
