"""A consistent executed prefix is not a completed declared window."""
import pytest

from genshin_app.equipment import run_plan_calc
from tests.test_mechanics_execution import ability, action, second_character
from tests.test_resource_reaction_app import reaction_plan
from tests.test_resource_reaction_receipt import resign, validate
from tests.test_shared_pool_app_delivery import authorize


def prefix_plan():
    plan = reaction_plan()
    second_character(plan['manual_template'], plan['rotation_model'])
    plan['manual_template']['characters'][-1]['stats']['energy_recharge'] = 1
    plan['rotation_model']['abilities'].append(ability('offfield_attempt', 'b'))
    plan['rotation_model']['actions'].append(action('later_failure', 'offfield_attempt', .5, .6, 30,
                                                   repeat={'mode': 'once'}))
    return plan


def test_later_failure_keeps_verified_prefix_without_complete_claim(tmp_path):
    db = tmp_path/'absent.db'
    value = run_plan_calc(authorize(prefix_plan(), db), db)
    result = value['result']
    assert result['mechanics']['complete_first_cycle'] is False and result['summary']['dps'] is None
    assert len(result['resource_reactions']['operations']) == 3
    assert result['resource_reactions']['verification_status'] == 'verified'
    assert result['resource_reactions']['verified_window'] == 'executed_prefix'
    validate(value)
    validate(value, full=False)


@pytest.mark.parametrize('prefix', [False, True])
def test_self_rehash_cannot_change_actual_execution_window(tmp_path, prefix):
    db = tmp_path/'absent.db'
    value = run_plan_calc(authorize(prefix_plan() if prefix else reaction_plan(), db), db)
    value['result']['resource_reactions']['verified_window'] = 'complete_reported_input' if prefix else 'executed_prefix'
    resign(value)
    with pytest.raises(ValueError):validate(value)
    with pytest.raises(ValueError):validate(value, full=False)
