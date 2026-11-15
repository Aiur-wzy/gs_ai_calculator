"""A real interrupted engine run must never regain complete-axis DPS in Excel."""
import pytest

from genshin_app.equipment import run_plan_calc
from genshin_app.formula_workbook import build_formula_workbook
from tests.test_formula_workbook import read_sheet
from tests.test_mechanics_execution import ability, action
from tests.test_reaction_em_app import em_plan as em_plan  # noqa: PLC0414
from tests.test_reaction_em_app import prepared


def test_actual_failed_later_action_exports_only_prefix_damage_without_dps(em_plan):
    plan, db, _, _ = em_plan
    plan['rotation_model']['abilities'].append(ability('offfield_attempt', 'b'))
    plan['rotation_model']['actions'].append(action('later_failure', 'offfield_attempt', .5, .6, 30,
                                                   repeat={'mode': 'once'}))
    out = run_plan_calc(prepared(plan, db), db)
    assert out['result']['mechanics']['complete_first_cycle'] is False
    assert out['result']['summary']['dps'] is None
    summary = read_sheet(build_formula_workbook(out), 1)
    assert summary['B6']['value'] == pytest.approx(out['result']['summary']['total_damage'])
    assert summary['B7']['value'] == '执行未完成，不提供整轴DPS'
    assert summary['B7']['formula'] is None
    assert summary['A6']['value'] == '已执行前缀期望伤害'
