"""The duration receipt survives native delivery and remains auditable offline."""
import copy

import pytest
from test_plan_mechanics import native, review

from genshin_app.calculation_review import review_calculation
from genshin_app.equipment.delivery import validate_delivery_calculated
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import run_plan_calc
from genshin_app.export import coverage_rows


def calculated(tmp_path):
    plan = native()
    db = tmp_path / 'absent.db'
    review(plan, db)
    return run_plan_calc(plan, db, capture_display=True)


def test_state_receipt_survives_calculation_delivery_and_coverage_export(tmp_path):
    value = calculated(tmp_path)
    before = copy.deepcopy(value)
    state = value['result']['buff_state_timeline']
    assert state['contract'] == 'buff-state-timeline-v1'
    assert state['states']
    assert all(row['owner'] == 'a' and row['name_zh'] for row in state['states'])
    validate_delivery_calculated(value)
    rows = coverage_rows(value['result'])
    assert any('持续 Buff 状态' in str(row) for row in rows)
    assert value == before


def test_rehashed_fabricated_duration_rejected_by_delivery(tmp_path):
    value = calculated(tmp_path)
    value['result']['buff_state_timeline']['states'][0]['end'] += 3
    value['result_receipt']['result_fingerprint'] = fingerprint(value['result'])
    with pytest.raises(PlanError, match='回执不一致'):
        validate_delivery_calculated(value)


def test_legacy_package_without_state_receipt_remains_supported(tmp_path):
    value = calculated(tmp_path)
    value['result'].pop('buff_state_timeline')
    value['result_receipt']['result_fingerprint'] = fingerprint(value['result'])
    validate_delivery_calculated(value)


def test_state_review_filters_by_intersecting_interval_and_preserves_slices(tmp_path):
    value = calculated(tmp_path)
    state = value['result']['buff_state_timeline']['states'][0]
    start, end = state['start'], state['end']
    middle = (start + end) / 2
    saved = {key: value[key] for key in ('result', 'result_receipt')}
    out = review_calculation(saved, {'view': 'buff_states', 'limit': 1,
        'filters': {'source_id': state['rule_id'], 'start': middle, 'end': end}})
    assert out['total_rows'] >= 1
    row = out['items'][0]
    assert row['slices'] and row['name_zh'] == state['name_zh']
    assert row['json_pointer'] == '/result/buff_state_timeline/states/0'
    after = review_calculation(saved, {'view': 'buff_states', 'filters': {'start': end}})
    assert after['total_rows'] == 0
