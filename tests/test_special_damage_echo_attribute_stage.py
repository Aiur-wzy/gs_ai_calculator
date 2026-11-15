"""Real multi-contributor echo stages must not reuse a damage projection frame."""
from __future__ import annotations

import copy

import pytest
from test_attribute_graph_runtime import classify
from test_mechanics_execution import ability, action, add, rule, second_character
from test_special_damage_echo import fixture

from genshin_engine.mechanics import execute_mechanics


def setup(contributors=2, ledger=4):
    template, rotation, model = fixture()
    previous = 'a'
    for index, actor in enumerate(['c', 'd'][:contributors - 2]):
        second_character(template, rotation, actor)
        switch_at = .1 + index * .04
        rotation['abilities'] += [ability('to-' + actor, previous, 'action.switch'),
                                  ability(actor + '-e', actor)]
        rotation['actions'] += [
            action('switch-' + actor, 'to-' + actor, switch_at, round(switch_at + .005, 6),
                   order=1, switch_to=actor, repeat={'mode': 'once'}),
            action('cast-' + actor, actor + '-e', round(switch_at + .01, 6), round(switch_at + .02, 6),
                   order=2, repeat={'mode': 'once'})]
        model.setdefault('application_events', []).append({
            'id': 'apply-' + actor, 'action_id': 'cast-' + actor,
            't': round(switch_at + .015, 6), 'order': 3,
            'application': {'profile_id': 'hydro', 'target_id': 'enemy',
                            'area_id': 'battlefield.primary', 'landed': True}})
        previous = actor
    for character in template['characters']:
        character['stats'].update(hp=30000, base_hp=15000, **{
            'def': 1000, 'base_def': 500, 'energy_recharge': 1})
    stats = ('atk', 'hp', 'def', 'em', 'energy_recharge') if ledger == 4 else ('em', 'atk')
    model['attribute_model'] = {
        'contract': f'attribute-ledger-v{ledger}',
        'initial_basis': [
            {'actor': character['id'], **({'stat': stat} if ledger >= 3 else {}),
             'included_components': [],
             **({'nonextra': {'mode': 'panel_total', 'status': 'assumed', 'source': 'anonymous ordinary initial panel'}}
                if ledger != 3 or stat == 'em' else {})}
            for character in template['characters'] for stat in (stats if ledger >= 3 else ('em',))],
        'effect_classifications': []}
    add(model, rule('parent-em-callback', 'benefit.em.all', 25,
        trigger={'event': 'reaction_damage', 'phase': 'post'}, duration=1,
        stacking={'max': 4, 'add': 1, 'policy': 'independent'}))
    classify(model, 'parent-em-callback', 'a', 'included')
    return template, rotation, model


@pytest.mark.parametrize('contributors', [2, 3, 4])
@pytest.mark.parametrize('ledger', [2, 3, 4])
def test_echo_has_independent_ordinary_attribute_stages_without_gameplay_callbacks(contributors, ledger, monkeypatch):
    from genshin_engine.attribute_runtime import AttributeRuntime
    from genshin_engine.mechanics import _Mechanics
    stage, stages = AttributeRuntime.stage, []
    phase = _Mechanics.phase
    def observed_stage(self, event, phase, **kwargs):
        if ':extra:' in event['id']:
            stages.append((event['id'], phase))
        return stage(self, event, phase, **kwargs)
    monkeypatch.setattr(AttributeRuntime, 'stage', observed_stage)
    def observed_phase(self, event, *args, **kwargs):
        assert ':extra:' not in event['id'], 'No gameplay callback stage is allowed for the echo subset'
        return phase(self, event, *args, **kwargs)
    monkeypatch.setattr(_Mechanics, 'phase', observed_phase)
    template, rotation, model = setup(contributors, ledger)
    before = copy.deepcopy((template, rotation, model))
    out = execute_mechanics(template, rotation, model)
    assert out['status'] in ('passed', 'unproven'), out['issues']
    assert (template, rotation, model) == before
    extras = [h for h in out['result']['hits'] if ':extra:' in h['id']]
    assert len(extras) == contributors
    activations = [row for row in out['mechanics_trace']
                   if row.get('type') == 'activation' and row.get('rule_id') == 'parent-em-callback']
    assert len(activations) == contributors
    assert all(':extra:' not in row['event_id'] for row in activations)
    for extra in extras:
        assert [phase for identity, phase in stages if identity == extra['id']] == ['pre', 'damage_projection', 'post']
        assert len(extra['matched_effect_refs']) == contributors
    baselines = {c['id']: c['stats']['em'] for c in template['characters']}
    receipts = [row for row in out['attribute_ledger']['receipts']
                if ':extra:' in row['query']['frame']['event_id']]
    assert len(receipts) == contributors
    assert all(row['value'] == baselines[row['query']['actor']] + 25 * contributors for row in receipts)


def test_v4_echo_current_source_approval_and_independent_saved_readback(tmp_path, monkeypatch):
    import json

    from test_plan_mechanics import review
    from test_special_damage_echo import _fixture_equipment_projection, app_plan

    from genshin_app.calculation_integrity import validate_result_binding
    from genshin_app.equipment import compose_equipment_plan, run_plan_calc
    from genshin_app.equipment.attributes import prepare_attribute_review
    from genshin_app.equipment.files import json_bytes

    _fixture_equipment_projection(monkeypatch)
    plan = app_plan()
    template, rotation, model = setup(2, 4)
    model['rules'][0]['source_id'] = model['special_damage_echoes'][0]['source_id'] = 'binding:part'
    model['rules'][1]['source_id'] = 'binding-em:part'
    model['attribute_model']['effect_classifications'][0]['component']['part_id'] = 'binding-em:part'
    plan.update(manual_template=template, rotation_model=rotation, mechanics_model=model)
    binding = copy.deepcopy(plan['effect_bindings'][0])
    binding['binding_id'] = 'binding-em'
    binding['parts'][0]['rule']['rule_id'] = 'parent-em-callback'
    plan['effect_bindings'].append(binding)
    db = tmp_path / 'absent.db'
    review(plan, db)
    plan = prepare_attribute_review(plan, 'Reviewed exact synthetic EM layers and ordinary baselines.', db)['plan']
    review(plan, db)
    context = compose_equipment_plan(plan, db)['context_fingerprint']
    for item in plan['effect_bindings']:
        item['parts'][0]['dependencies']['context_fingerprint'] = context
    fresh = run_plan_calc(plan, db)
    saved = json.loads(json_bytes(fresh))
    validate_result_binding(saved['result'], saved['result_receipt'])
    assert len(saved['result']['special_damage_echoes']['trace']) == 1
    assert saved['result']['attribute_ledger']['contract'] == 'attribute-ledger-v4'
    assert not db.exists()
