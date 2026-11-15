"""Independent source confirmation, recomputation and complete delivery checks."""
import io
import json
import xml.etree.ElementTree as ET
import zipfile

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result
from genshin_app.formula_workbook import build_formula_workbook
from genshin_db.runtime_queries import query_runtime
from tests.test_formula_workbook import read_sheet
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_runtime_talent_level import level_db, mutate  # noqa: F401
from tests.test_talent_level_app import level_plan

# ruff: noqa: F811


def test_parameter_node_hash_cannot_impersonate_full_level_source_confirmation(level_db):
    plan = level_plan(level_db)
    row = plan['hit_parameter_sources'][0]
    row['expected_level_fingerprint'] = row['expected_node_sha256']
    report = compose_equipment_plan(plan, level_db)
    assert report['state'] == 'pending'
    assert any(r['code'] == 'source_changed' for r in report['missing_inputs'])
    assert report['hit_parameter_compilations'][0]['status'] == 'pending'
    with pytest.raises(PlanError):
        run_plan_calc(plan, level_db)


def test_changed_level_cannot_reuse_old_confirmation_even_with_new_table_node(level_db, fake_repo):
    plan = level_plan(level_db)
    mutate(level_db, fake_repo, 'extraLevel', 2)
    row = plan['hit_parameter_sources'][0]
    source = query_runtime(level_db, {'action': 'action_parameter_at_level', **row['selection'], 'param_index': 0})
    row.update(expected_snapshot_id=source['snapshot']['snapshot_id'], expected_node_sha256=source['source']['node_sha256'])
    report = compose_equipment_plan(plan, level_db)
    assert report['state'] == 'pending'
    assert report['hit_parameter_compilations'][0]['level']['effective_level'] == 12
    with pytest.raises(PlanError):
        run_plan_calc(plan, level_db)
    row['expected_level_fingerprint'] = source['level_profile']['content_fingerprint']
    calculated = run_plan_calc(plan, level_db)
    assert calculated['result']['hits'][0]['expected'] == pytest.approx(144)
    assert calculated['result']['hit_parameter_sources'][0]['multiplier'] == 3.2


def test_confirmed_source_does_not_promote_pending_interpretation(level_db):
    plan = level_plan(level_db)
    plan['hit_parameter_sources'][0]['status'] = 'pending'
    report = compose_equipment_plan(plan, level_db)
    assert report['state'] == 'pending'
    assert any(row['code'] == 'parameter_interpretation_pending' for row in report['missing_inputs'])
    with pytest.raises(PlanError):
        run_plan_calc(plan, level_db)


def test_complete_level_source_json_is_recoverable_from_all_three_workbooks(level_db, tmp_path):
    calculated = run_plan_calc(level_plan(level_db), level_db)
    expected = calculated['result']['hit_parameter_sources']
    path = tmp_path / 'level-independent.xlsx'
    export_result(calculated['result'], 'xlsx', path, receipt=calculated['result_receipt'])
    ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    for payload in (path.read_bytes(), result_bytes(calculated['result'], calculated['result_receipt'], 'xlsx'),
                    build_formula_workbook(calculated)):
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            sheets = ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet', ns)
            index = next(i + 1 for i, sheet in enumerate(sheets) if sheet.attrib['name'] == '技能参数来源')
        cells = read_sheet(payload, index)
        chunks = sorted((int(c['value'].split()[-1]), cells['B' + address[1:]]['value'])
                        for address, c in cells.items() if address.startswith('A')
                        and isinstance(c['value'], str) and c['value'].startswith('原文块 '))
        assert chunks and all(len(value) <= 480 for _, value in chunks)
        assert [number for number, _ in chunks] == list(range(1, len(chunks) + 1))
        assert json.loads(''.join(value for _, value in chunks)) == expected
        assert all(c['formula'] is None for c in cells.values())
