"""V4 public App composition, adoption, calculation and saved result queries."""
from __future__ import annotations

import copy
import json

import pytest

from genshin_app.calculation_review import review_calculation
from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.attribute_receipt import (
    validate_attribute_receipt,
    validate_attribute_result_receipt,
)
from genshin_app.equipment.attributes import prepare_attribute_review
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from tests.test_attribute_multistat_runtime import hp_to_atk, setup
from tests.test_plan_mechanics import native, review


def native_plan():
    plan = native()
    skeleton = copy.deepcopy(plan['effect_bindings'][0])
    t, r, m = setup((.2, .4))
    hp_to_atk(t, r, m)
    plan.update(manual_template=t, rotation_model=r, mechanics_model=m, effect_bindings=[])
    for source in m['rules']:
        bid = 'bind_' + source['id']
        source['source_id'] = bid + ':part'
        binding = copy.deepcopy(skeleton)
        binding.update(binding_id=bid, provider_character_id=source['owner'])
        binding['parts'][0]['rule']['rule_id'] = source['id']
        plan['effect_bindings'].append(binding)
    for row in m['attribute_model']['effect_classifications']:
        row['component']['part_id'] = 'bind_' + row['effect_ref']['rule_id'] + ':part'
    return plan


def adopt(plan, db):
    review(plan, db)
    out = prepare_attribute_review(plan, 'Synthetic five-stat baselines, classifications and HP conversion reviewed.', db)
    assert out['state'] == 'ready', out['missing_inputs']
    return out['plan']


def test_public_app_adoption_actual_calculation_json_reopen_and_hp_query(tmp_path):
    db = tmp_path / 'absent.db'
    plan = adopt(native_plan(), db)
    out = run_plan_calc(plan, db)
    assert [h['attribute_inputs']['scaling'] for h in out['result']['hits']] == [460, 460]
    reopened = json.loads(json.dumps(out))
    validate_attribute_receipt(reopened['plan'], reopened['result'], reopened['report'], reopened['result_receipt'])
    result_only = {'result': reopened['result'], 'result_receipt': reopened['result_receipt']}
    query = review_calculation(result_only, {'view': 'attributes', 'filters': {'stat': 'hp', 'character': 'b'}})
    assert any(row['value'] == 36000 for row in query['items'])
    assert all(row['stat'] == 'hp' for row in query['items'])
    assert out['result']['attribute_ledger']['contract'] == 'attribute-ledger-v4'


@pytest.mark.parametrize('target', ['panel', 'base', 'expression', 'ledger', 'receipt'])
def test_new_contract_proof_rejects_changed_native_or_actual_evidence(tmp_path, target):
    db = tmp_path / 'absent.db'
    out = run_plan_calc(adopt(native_plan(), db), db)
    changed = copy.deepcopy(out)
    if target in ('panel', 'base'):
        changed['plan']['manual_template']['characters'][1]['stats']['hp' if target == 'panel' else 'base_hp'] += 1
    elif target == 'expression':
        changed['report']['mechanics_model']['rules'][1]['effects'][0]['value']['factor'] += .01
    elif target == 'ledger':
        changed['result']['attribute_ledger']['receipts'][0]['value'] += 1
        changed['result_receipt']['result_fingerprint'] = fingerprint(changed['result'])
    else:
        changed['result_receipt'].pop('attribute_ledger')
    with pytest.raises(ValueError):
        validate_attribute_receipt(changed['plan'], changed['result'], changed['report'], changed['result_receipt'])


def test_panel_change_requires_fresh_review_and_recomputes_dynamic_conversion(tmp_path):
    db = tmp_path / 'absent.db'
    plan = adopt(native_plan(), db)
    first = run_plan_calc(plan, db)
    plan['manual_template']['characters'][1]['stats']['hp'] += 10000
    assert compose_equipment_plan(plan, db)['state'] == 'pending'
    with pytest.raises(PlanError):
        run_plan_calc(plan, db)
    out = run_plan_calc(adopt(plan, db), db)
    assert [h['attribute_inputs']['scaling'] for h in out['result']['hits']] == [560, 560]
    assert first['result_receipt']['result_fingerprint'] != out['result_receipt']['result_fingerprint']
    validate_attribute_result_receipt(out['result'], out['result_receipt'])


def test_public_app_multistat_post_callbacks_are_saved_with_real_execution(tmp_path):
    from tests.test_attribute_callback_app import authorize_callbacks
    db = tmp_path / 'absent.db'
    plan = native_plan()
    model = plan['mechanics_model']
    for source in model['rules']:
        source['trigger'] = {'event': 'hit', 'phase': 'post', 'event_ids': ['h0']}
    model['rules'][1]['effects'][0]['value']['sample'] = 'trigger'
    model['attribute_callbacks'] = {'contract': 'mechanics-attribute-callbacks-v2', 'members': ['source_hp', 'convert'],
        'edges': [{'before_rule_id': 'source_hp', 'after_rule_id': 'convert', 'assumption_id': 'order'}],
        'assumptions': [{'id': 'order', 'note': 'Synthetic ordered post callback'}]}
    for binding in plan['effect_bindings']:
        binding['parts'][0]['dependencies'] = {'mode': 'manual_context', 'context_fingerprint': None,
            'note': 'Synthetic explicit full callback context; no source authority inferred.'}
    out = run_plan_calc(authorize_callbacks(plan, db), db)
    assert [h['attribute_inputs']['scaling'] for h in out['result']['hits']] == [100, 460]
    saved = json.loads(json.dumps(out))
    validate_attribute_receipt(saved['plan'], saved['result'], saved['report'], saved['result_receipt'])
    assert saved['result']['attribute_callbacks']['contract'] == 'mechanics-attribute-callbacks-v2'
    assert len([r for r in saved['result']['attribute_callbacks']['trace']
                if r['type'] == 'callback_committed' and r['origin'] == 0]) == 2
