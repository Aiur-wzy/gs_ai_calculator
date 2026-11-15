"""Independent v2 context reapproval and readonly export atom presentation."""
import copy

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.schema import PlanError
from genshin_app.export import shared_pool_rows
from tests.test_shared_pool_app_delivery import authorize
from tests.test_shared_pool_v2_app import scaled_plan


def test_new_spend_policy_requires_review_and_then_recomputes_actual_accepted(tmp_path):
    db = tmp_path / 'absent.db'
    plan = authorize(scaled_plan(), db)
    first = run_plan_calc(plan, db)
    plan['mechanics_model']['shared_pools']['operations'][0]['spend_policy'] = 'all_or_nothing'
    assert compose_equipment_plan(plan, db)['state'] == 'pending'
    with pytest.raises(PlanError):
        run_plan_calc(plan, db)
    reviewed = authorize(plan, db)
    original = copy.deepcopy(reviewed)
    second = run_plan_calc(reviewed, db)
    assert reviewed == original
    assert second['result_receipt']['input_fingerprint'] != first['result_receipt']['input_fingerprint']
    rows = [row for row in second['result']['shared_pools']['trace'] if row['cycle'] == 0]
    assert [(row['accepted'], row['after']) for row in rows] == [(8, 11), (8, 3), (0, 3)]
    assert rows[-1]['reason'] == 'insufficient' and rows[-1]['unfulfilled'] == 8


def test_display_scale_edit_changes_units_without_changing_atom_values(tmp_path):
    db = tmp_path / 'absent.db'
    plan = authorize(scaled_plan(), db)
    first = run_plan_calc(plan, db)
    plan['mechanics_model']['shared_pools']['pools'][0]['unit_scale'] = 1000
    second = run_plan_calc(authorize(plan, db), db)
    a, b = first['result']['shared_pools']['trace'], second['result']['shared_pools']['trace']
    assert [(r['accepted'], r['before'], r['after']) for r in a] == [(r['accepted'], r['before'], r['after']) for r in b]
    assert {r['unit_scale'] for r in b} == {1000}
    assert first['result']['summary'] == second['result']['summary']
    table = shared_pool_rows(second['result'])
    operation_rows = [row for row in table if len(row) > 12 and row[3] == '扣除资源']
    assert operation_rows and all(row[12] == 1000 for row in operation_rows)
