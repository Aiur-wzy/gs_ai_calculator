"""Independent opt-in R3 App per-life source attribution checks."""
import copy
import io
import json
import xml.etree.ElementTree as ET
import zipfile

import pytest

from genshin_app.calculation_review import review_calculation
from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from tests.test_freeze_export_review import workbook_sheets
from tests.test_resource_threshold_lifecycle_app import lifecycle_plan
from tests.test_shared_pool_app_delivery import authorize


def test_pending_native_life_with_prefixed_id_does_not_close_unrelated_life(tmp_path):
    plan = lifecycle_plan()
    config = plan['mechanics_model']['state_runtime']
    third = copy.deepcopy(config['lifecycles'][0])
    third.update(id='field.extra', source_id='binding:life_c', start_order=3, end_order=3, establishment_order=12)
    third['start']['condition'] = 'unresolved-condition'
    config['lifecycles'].append(third)
    authority = copy.deepcopy(next(row for row in plan['mechanics_model']['rules'] if row['id'] == 'life_b'))
    authority.update(id='life_c', source_id='binding:life_c')
    plan['mechanics_model']['rules'].append(authority)
    plan['mechanics_model']['initial_state']['rules'].append({'rule_id': 'life_c', 'cooldown_remaining': 0, 'instances': []})
    source = copy.deepcopy(next(row for row in plan['effect_bindings'][0]['parts'] if row['part_id'] == 'life_b'))
    source.update(part_id='life_c', label='独立 C 来源')
    source['rule']['rule_id'] = 'life_c'
    plan['effect_bindings'][0]['parts'].append(source)
    db = tmp_path / 'absent.db'
    report = compose_equipment_plan(authorize(plan, db), db)
    assert report['state'] == 'pending'
    delivered = report['mechanics_model'].get('state_runtime') or {}
    assert 'field' in {row['id'] for row in delivered.get('lifecycles', [])}, report['state_runtime_source_adoptions']


def test_final_delivery_cannot_inject_unreviewed_initial_target_instance(tmp_path, monkeypatch):
    from genshin_app.equipment import state_runtime_v2

    db = tmp_path / 'absent.db'
    plan = authorize(lifecycle_plan(), db)
    original = state_runtime_v2.verify_positioned_delivery

    def verify(before, resolved, characters):
        from genshin_engine.mechanics import validate_mechanics_model
        target = next(row for row in resolved['initial_state']['rules'] if row['rule_id'] == 'boost')
        target['instances'] = [{'beneficiaries': ['a'], 'remaining': [10]}]
        checked = validate_mechanics_model(resolved)
        assert checked['status'] == 'valid', checked['issues']
        return original(before, resolved, characters)

    monkeypatch.setattr(state_runtime_v2, 'verify_positioned_delivery', verify)
    with pytest.raises(PlanError) as error:
        run_plan_calc(plan, db)
    assert error.value.reason == 'invalid_state_runtime_delivery', str(error.value)


@pytest.mark.parametrize('end,expected', [(False, [45, 90, 45, 90, 45]), (True, [45, 90, 45, 45, 45])])
def test_actual_lifecycle_source_query_and_three_readonly_workbooks(tmp_path, end, expected):
    db = tmp_path / 'absent.db'
    out = run_plan_calc(authorize(lifecycle_plan(end=end), db), db, capture_display=True)
    assert [row['expected'] for row in out['result']['hits']] == expected
    before = copy.deepcopy(out)
    actual = out['result']['resource_threshold_triggers']['trace']
    row = actual[0]
    query = review_calculation(out, {'view': 'resource_threshold_triggers',
                                    'filters': {'event_id': row['source_event_id']}})
    assert query['validation'] == 'full_calculation_package'
    assert query['items'] and query['items'][0]['event_id'] == row['source_event_id']
    assert query['items'][0]['outcome'] == 'lifecycle_started'
    assert review_calculation(out, {'view': 'resource_threshold_triggers',
                                   'filters': {'event_id': row['lifecycle_id']}})['total_rows'] == 0
    support = formula_workbook_support(out)
    assert support['editable']['resource_thresholds_and_sources'] == 'regenerate_required'
    path = tmp_path / 'actual.xlsx'
    export_result(out['result'], 'xlsx', path, receipt=out['result_receipt'])
    payloads = [path.read_bytes(), result_bytes(out['result'], out['result_receipt'], 'xlsx'), build_formula_workbook(out)]
    ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    for raw in payloads:
        sheets = workbook_sheets(raw)
        for title, field in [('阈值收益执行', 'resource_threshold_triggers'), ('生命周期流水', 'state_runtime')]:
            cells = sheets[title]
            chunks = sorted((int(c['value'].split()[-1]), cells['B'+ref[1:]]['value'])
                            for ref, c in cells.items() if ref.startswith('A') and isinstance(c['value'], str)
                            and c['value'].startswith('原文块 '))
            assert json.loads(''.join(text for _, text in chunks)) == out['result'][field]
            assert all(not c['formula'] for c in cells.values())
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                metadata = ET.fromstring(archive.read('xl/workbook.xml')).findall('s:sheets/s:sheet', ns)
                index = next(i+1 for i, s in enumerate(metadata) if s.attrib['name'] == title)
                assert ET.fromstring(archive.read(f'xl/worksheets/sheet{index}.xml')).find('s:sheetProtection', ns) is not None
        values = {c['value'] for c in sheets['阈值收益执行'].values()}
        assert '领域已开始' in values and '收益已激活' not in values
        if end:
            assert '领域已结束' in values
    assert out == before and not db.exists()
