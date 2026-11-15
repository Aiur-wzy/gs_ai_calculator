"""Incomplete execution and failed later-cycle validation must remain distinct."""
import pytest

from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from tests.test_formula_workbook import read_sheet
from tests.test_mechanics_execution import ability, action
from tests.test_plan_mechanics import native, review
from tests.test_reaction_em_app import em_plan as em_plan  # noqa: PLC0414
from tests.test_reaction_em_app import prepared


def test_actual_prefix_never_gets_dps_in_any_workbook_route(em_plan, tmp_path):
    plan, db, _, _ = em_plan
    plan['rotation_model']['abilities'].append(ability('offfield_attempt', 'b'))
    plan['rotation_model']['actions'].append(action('later_failure', 'offfield_attempt', .5, .6, 30,
                                                   repeat={'mode': 'once'}))
    out = run_plan_calc(prepared(plan, db), db)
    assert out['result']['summary']['dps'] is None
    assert out['result']['mechanics']['complete_first_cycle'] is False
    assert formula_workbook_support(out)['execution_coverage'] == 'executed_prefix'
    formula = read_sheet(build_formula_workbook(out), 1)
    assert formula['B7'] == {'value': '执行未完成，不提供整轴DPS', 'formula': None}
    assert formula['B6']['value'] == pytest.approx(out['result']['summary']['total_damage'])
    assert formula['A6']['value'] == '已执行前缀期望伤害'
    path = tmp_path / 'prefix.xlsx'
    export_result(out['result'], 'xlsx', path, receipt=out['result_receipt'])
    for raw in (path.read_bytes(), result_bytes(out['result'], out['result_receipt'], 'xlsx')):
        sheet = read_sheet(raw, 1)
        row = next(ref[1:] for ref, cell in sheet.items() if ref.startswith('A') and cell['value'] == '每秒伤害')
        value = sheet.get('B'+row, {'value': None, 'formula': None})
        assert value['formula'] is None and value['value'] in (None, '')


def test_actual_complete_first_window_keeps_dps_when_later_cycle_fails(tmp_path):
    plan, db = native(), tmp_path / 'absent.db'
    rotation = plan['rotation_model']
    rotation['max_cycles'] = 3
    rotation['actions'][0]['repeat'] = {'mode': 'each_cycle'}
    rotation['abilities'][0]['cooldown'] = {'group': 'independent-later-cycle', 'duration': rotation['period'] * 2,
                                          'charges': 1, 'start': 'action_start', 'recovery': 'sequential'}
    rotation['initial_cooldowns'] = [{'character': 'a', 'group': 'independent-later-cycle', 'ready_in': [0]}]
    review(plan, db)
    out = run_plan_calc(plan, db)
    assert out['result']['mechanics']['complete_first_cycle'] is True
    assert out['result']['rotation_check']['status'] == 'failed'
    assert out['result']['summary']['dps'] is not None
    summary = read_sheet(build_formula_workbook(out), 1)
    assert summary['B7']['formula'] is not None
    assert summary['B7']['value'] == pytest.approx(out['result']['summary']['dps'])
    assert 'execution_coverage' not in formula_workbook_support(out)
