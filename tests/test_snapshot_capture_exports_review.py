"""Independent export checks for fixed membership and separate sampling domains."""
import copy

import pytest

from genshin_app.equipment import enable_mechanics, new_plan, run_plan_calc
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from tests.test_freeze_export_review import workbook_sheets
from tests.test_mechanics_execution import (
    add,
    condition,
    fixture,
    rule,
    second_character,
)
from tests.test_plan_mechanics import native, review
from tests.test_reaction_sources_review import evaluate_numeric_cell


def domain_plan(db):
    template, rotation, model = fixture((.2, 1.2, 1.5))
    second_character(template, rotation)
    for character in template['characters']:
        character['stats']['energy_recharge'] = 1
    model['snapshot_captures'] = [{'id': 'capture', 'action_id': 'cast', 'offset': .05,
        'precision': .000001, 'status': 'assumed', 'source': 'Independent synthetic sampling fixture'}]
    for hit in model['hit_events']:
        hit['self_snapshot_event'] = 'capture'
    condition(model, 'later', 1)
    later = {'event': 'condition', 'phase': 'pre', 'event_ids': ['later']}
    add(model,
        rule('self', 'benefit.stat_add.atk', 100, trigger=later,
            beneficiaries={'mode': 'characters', 'characters': ['a'], 'scope': 'locked'}),
        rule('provider', 'benefit.stat_add.atk', 200, trigger=later,
            beneficiaries={'mode': 'characters', 'characters': ['b'], 'scope': 'locked'}),
        rule('addition', 'benefit.base_add.all', {'kind': 'stat', 'subject': 'owner',
            'stat': 'atk', 'factor': .1, 'sample': 'current'}, domain='provider', owner='b'),
        rule('enemy', 'benefit.res_shred.cryo', .1, domain='enemy', duration=1.5))
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=model)
    old_binding = native()['effect_bindings'][0]
    old = old_binding['parts'][0]
    bindings = {}
    for item in model['rules']:
        owner = item['owner']
        if owner not in bindings:
            bindings[owner] = copy.deepcopy(old_binding)
            bindings[owner].update(binding_id='binding.' + owner, provider_character_id=owner, parts=[])
        binding = bindings[owner]
        item['source_id'] = binding['binding_id'] + ':' + item['id']
        part = copy.deepcopy(old)
        part.update(part_id=item['id'], rule={'kind': 'mechanics', 'rule_id': item['id']})
        binding['parts'].append(part)
    plan['effect_bindings'] = list(bindings.values())
    review(plan, db)
    return plan


def test_review_formula_keeps_captured_self_but_later_provider_and_enemy_membership(tmp_path):
    db = tmp_path / 'absent.db'
    calc = run_plan_calc(domain_plan(db), db)
    assert not db.exists()
    assert [h['expected'] for h in calc['result']['hits']] == pytest.approx([60, 70, 63])
    cells = workbook_sheets(build_formula_workbook(calc))
    paths = {c['value']: 'C' + a[1:] for a, c in cells['参数'].items() if a.startswith('A')}

    def evaluated(changes):
        edits = {('参数', paths[k]): v for k, v in changes.items()}
        return [evaluate_numeric_cell(cells, '逐事件', f'V{i}', edits) for i in (5, 6, 7)]

    # Independent fixed-membership arithmetic: (self + .1*provider)*DEF*RES.
    assert evaluated({}) == pytest.approx([60, 70, 63])
    assert evaluated({'mechanics_model.rules[0].effects[0].value': 900}) == pytest.approx([60, 70, 63])
    assert evaluated({'mechanics_model.rules[1].effects[0].value': 300}) == pytest.approx([60, 75, 67.5])
    assert evaluated({'characters[0].stats.atk': 200}) == pytest.approx([110, 120, 108])
    assert evaluated({'characters[1].stats.atk': 300}) == pytest.approx([65, 75, 67.5])
    fixed = paths['mechanics_model.snapshot_captures.chunk[0]'][1:]
    assert cells['参数']['E' + fixed]['value'] == '回 App 重新生成'
    assert formula_workbook_support(calc)['snapshot_captures']['state_machine_replay'] is False
