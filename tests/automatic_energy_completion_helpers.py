"""Real fixture compilation for completed-but-unqualified energy regressions."""
import copy

from genshin_app.equipment import compose_equipment_plan
from genshin_app.rotation_check import describe_rotation_check, resolve_rotation_model
from genshin_engine.mechanics import execute_mechanics
from tests.test_action_resources_app import confirm
from tests.test_automatic_energy_app import automatic_plan
from tests.test_mechanics_execution import add, condition, rule
from tests.test_plan_mechanics import native
from tests.test_plan_mechanics import review as review_manual


def energy_case(db, value=None, *, proof=False):
    plan = automatic_plan()
    plan['rotation_model']['max_cycles'] = 3
    plan['manual_template']['hits'] = plan['manual_template']['hits'][:1]
    plan['mechanics_model']['hit_events'] = plan['mechanics_model']['hit_events'][:1]
    condition(plan['mechanics_model'], 'unknown', .1, 2)
    plan['mechanics_model']['condition_events'][0]['value'] = value
    if proof:
        plan['rotation_model']['max_cycles'] = 20
        plan['rotation_model']['period'] = 20
        plan['manual_template']['loop_duration'] = 20
        plan['rotation_model']['actions'][0]['repeat'] = {'mode': 'each_cycle'}
        plan['rotation_model']['events'] = [{'id': 'refund', 'type': 'energy_direct', 't': 0, 'order': 1,
            'repeat': {'mode': 'each_cycle'}, 'amounts': {'a': 60},
            'status': 'assumed', 'source': 'Independent deterministic engineering refund'}]
    add(plan['mechanics_model'], rule(source_id='binding:part', trigger={'event': 'condition', 'phase': 'pre', 'event_ids': ['unknown']}))
    plan['effect_bindings'] = copy.deepcopy(native()['effect_bindings'])
    confirm(plan, db)
    review_manual(plan, db)
    before = copy.deepcopy(plan)
    report = compose_equipment_plan(plan, db)
    template = report['plan']['resolved_template']
    assert template is not None, report.get('missing_inputs')
    rotation, bindings = resolve_rotation_model(template, report['compiled_rotation_model'])
    output = execute_mechanics(template, rotation, report['mechanics_model'], matches=report['mechanics_matches'])
    output['rotation_check'] = describe_rotation_check(output['rotation_check'], template, rotation, bindings, mechanics_model=report['mechanics_model'], matches=report['mechanics_matches'])
    assert plan == before
    return plan, report, output
