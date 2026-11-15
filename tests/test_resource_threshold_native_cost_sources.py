"""Two genuine cost parts share a native parent; aliases grant no new cost."""
from __future__ import annotations

import copy
import json

import pytest
from test_mechanics_execution import add, rule
from test_plan_mechanics import native
from test_resource_threshold_lifecycle_execution import two_native_costs_case
from test_shared_pool_app_delivery import authorize

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.delivery import validate_delivery_calculated
from genshin_app.equipment.files import json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.formula_workbook import validate_formula_calculation


def native_cost_plan():
    plan = native()
    template, rotation, model = two_native_costs_case()
    template['characters'][0]['stats']['energy_recharge'] = 1
    benefit = copy.deepcopy(next(row for row in model['rules'] if row['id'] == 'boost'))
    model['rules'], model['initial_state']['rules'] = [], []
    binding = plan['effect_bindings'][0]
    prototype = copy.deepcopy(binding['parts'][0])
    binding['parts'] = []
    # These are actual source parts declared and separately approved in the
    # native plan, not fresh strings attached only to operation aliases.
    sources = {'binding:pool': ('pool', 'Shared meter identity and explicit initial balance'),
               'source:field': ('meter', 'Primary cost A and the explicitly ordered meter reset'),
               'source:effect': ('field-effect', 'Threshold starts/replaces the short-lived buff field'),
               'source:observer': ('observer', 'Observe the two committed cost receipts'),
               'source:followup_cost': ('followup-cost', 'Independent cost B of the same native hit')}
    for part, description in sources.values():
        authority = (copy.deepcopy(benefit) if part == 'field-effect'
                     else rule(part + '-authority', effects=[]))
        authority['source_id'] = 'binding:' + part
        authority['source'] = 'Synthetic declared mechanic: ' + description
        add(model, authority)
        record = copy.deepcopy(prototype)
        record.update(part_id=part, label=description)
        record['rule']['rule_id'] = authority['id']
        binding['parts'].append(record)
    objects = [*model['shared_pools']['pools'], *model['shared_pools']['operations'],
               *model['state_runtime']['lifecycles'], *model['state_runtime']['timers'],
               *model['resource_threshold_observers']['observers'],
               *model['resource_threshold_triggers']['bindings']]
    for declaration in objects:
        declaration['source_id'] = 'binding:' + sources[declaration['source_id']][0]
    plan.update(manual_template=template, rotation_model=rotation, mechanics_model=model)
    return plan


def test_independent_native_cost_parts_survive_current_approval_calc_and_saved_readers(tmp_path):
    db = tmp_path / 'absent.db'
    plan = authorize(native_cost_plan(), db)
    before = copy.deepcopy(plan)
    fresh = run_plan_calc(plan, db, capture_display=True)
    assert plan == before
    rows = fresh['result']['shared_pools']['trace']
    costs = [row for row in rows if row.get('operation') == 'spend']
    assert [(row['source_id'], row['accepted']) for row in costs] == [
        ('binding:meter', 16), ('binding:followup-cost', 16)]
    assert {row['run_id'] for row in costs} == {'0:h0'}
    starts = [row for row in fresh['result']['state_runtime']['issued_trace']
              if row['type'] == 'lifecycle_start' and row['lifecycle_id'] == 'field_b']
    assert len(starts) == len({row['generation_uid'] for row in starts}) == 2
    target = tmp_path / 'two-real-costs.calculated.json'
    target.write_bytes(json_bytes(fresh))
    saved = json.loads(target.read_text(encoding='utf8'))
    validate_delivery_calculated(saved)
    validate_formula_calculation(saved)
    assert json.loads(result_bytes(saved['result'], saved['result_receipt'], 'json'))
    assert not db.exists()


@pytest.mark.parametrize('failure', ['unapproved', 'unregistered', 'alias'])
def test_operation_names_do_not_replace_actual_source_approval(tmp_path, failure):
    db = tmp_path / 'absent.db'
    plan = native_cost_plan()
    if failure == 'unregistered':
        plan['mechanics_model']['shared_pools']['operations'][-1]['source_id'] = 'binding:made-up-cost'
    elif failure == 'alias':
        second = plan['mechanics_model']['shared_pools']['operations'][-1]
        second.update(id='another-free-name', source_id='binding:meter')
        plan['mechanics_model']['resource_threshold_observers']['observers'][0]['causes']['operation_ids'][-1] = second['id']
    plan = authorize(plan, db)
    if failure == 'unapproved':
        record = next(row for row in plan['effect_bindings'][0]['parts'] if row['part_id'] == 'followup-cost')
        record['dependencies']['context_fingerprint'] = 'old-context'
    if failure != 'alias':
        report = compose_equipment_plan(plan, db)
        assert report['state'] == 'pending'
        expected = 'pool_source_pending' if failure == 'unregistered' else 'pool_source_scope_unreviewed'
        assert any(row['code'] == expected and row['path'].endswith('operations.spend-again.source_id')
                   for row in report['missing_inputs'])
    with pytest.raises(PlanError) as caught:
        run_plan_calc(plan, db)
    if failure == 'alias':
        assert caught.value.reason == 'mechanics_pending'
        assert any(row['code'] == 'pool_duplicate_benefit' for row in caught.value.details['issues'])
