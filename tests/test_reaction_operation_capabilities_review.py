"""Independent RQ0 execution/actual App boundaries; fixed engineering golden."""
import copy
import json
from pathlib import Path

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.schema import PlanError
from genshin_engine.mechanics import execute_mechanics
from tests.reaction_observation_compat import reaction_observation_view
from tests.test_plan_mechanics import native
from tests.test_shared_pool_app_delivery import authorize

HERE = Path(__file__).resolve().parent
CASES = json.loads((HERE / 'fixtures/reaction_listener_review.json').read_text(encoding='utf8'))
BAD = [('elemental_reaction', 'pre', True), ('elemental_reaction', 'post', False),
       ('reaction_damage', 'pre', True), ('reaction_damage', 'post', False)]
DB = HERE / 'rq0-review-missing.db'


def case(name):
    return copy.deepcopy(CASES[name]['input'])


def app_plan(data):
    plan = native()
    plan['manual_template'] = copy.deepcopy(data['template'])
    for character in plan['manual_template']['characters']:
        character['stats']['energy_recharge'] = 1
    plan['rotation_model'] = copy.deepcopy(data['rotation'])
    plan['mechanics_model'] = copy.deepcopy(data['mechanics'])
    model = plan['mechanics_model']
    model['rules'][0]['source_id'] = 'binding:part'
    plan['effect_bindings'][0]['parts'][0]['rule']['rule_id'] = model['rules'][0]['id']
    for collection in ('pools', 'operations', 'consumers'):
        for row in model.get('shared_pools', {}).get(collection, []):
            row['source_id'] = 'binding:part'
    return plan


@pytest.mark.parametrize('event,phase,has_model', BAD)
def test_invalid_resource_listener_reports_without_execution(event, phase, has_model):
    data = case(f'operation-{event}-{phase}-model{has_model}')
    before = copy.deepcopy(data)
    out = execute_mechanics(data['template'], data['rotation'], data['mechanics'])
    assert data == before
    assert out['result'] is None and out['issues']
    assert out['status'] in ('invalid', 'pending')
    assert not [r for r in out.get('shared_pool_trace', []) if r['type'] == 'operation']


@pytest.mark.parametrize('event,phase,has_model', BAD)
def test_existing_ordinary_rule_diagnostic_is_exact(event, phase, has_model):
    name = f'rule-{event}-{phase}-model{has_model}'
    data = case(name)
    assert execute_mechanics(data['template'], data['rotation'], data['mechanics']) == CASES[name]['output']


@pytest.mark.parametrize('name', ['operation-elemental_reaction-post-modelTrue',
    'operation-reaction_damage-post-modelTrue', 'legal-reaction-damage-pre-effect-filter'])
def test_supported_complete_outputs_remain_exact(name):
    data = case(name)
    output = execute_mechanics(data['template'], data['rotation'], data['mechanics'])
    assert reaction_observation_view(output, data['rotation']) == CASES[name]['output']


@pytest.mark.parametrize('event,phase,has_model', BAD)
def test_bad_listener_cannot_be_approved_through_actual_app(event, phase, has_model):
    plan = app_plan(case(f'operation-{event}-{phase}-model{has_model}'))
    with pytest.raises(PlanError):
        authorize(plan, DB)
        run_plan_calc(plan, DB)
    assert not DB.exists()


@pytest.mark.parametrize('zero_reactions', [False, True])
def test_legitimate_app_post_and_complete_model_zero_reaction(zero_reactions):
    data = case('operation-elemental_reaction-post-modelTrue')
    if zero_reactions:
        data['mechanics']['elemental_model']['target']['initial_aura'] = None
    plan = app_plan(data)
    authorize(plan, DB)
    original = copy.deepcopy(plan)
    output = run_plan_calc(plan, DB)
    assert plan == original
    assert output['report']['state'] == 'ready'
    operations = [r for r in output['result']['shared_pools']['trace'] if r['type'] == 'operation']
    assert bool(operations) != zero_reactions
    assert not DB.exists()
    assert compose_equipment_plan(plan, DB)['state'] == 'ready'
