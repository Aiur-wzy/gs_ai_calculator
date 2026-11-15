from __future__ import annotations

import copy
import json

import pytest

from genshin_app.equipment import compose_equipment_plan, new_plan, run_plan_calc
from genshin_app.equipment.delivery import validate_delivery_calculated
from genshin_app.equipment.files import context_view, fingerprint, input_view, read_plan
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_engine.mechanics import execute_mechanics
from genshin_engine.resource_reaction_runtime import Runtime as ReactionRuntime
from tests.test_beneficiary_eligibility_core import QUALIFY, scene
from tests.test_mechanics_execution import second_character
from tests.test_plan_mechanics import native, review
from tests.test_resource_lifecycle_actual import values
from tests.test_state_runtime_execution import field_case


def plan_case():
    template, rotation, model = scene()
    for actor in template['characters']:
        actor['stats']['energy_recharge'] = 1
    plan = enable_mechanics(new_plan(template))
    plan['rotation_model'], plan['mechanics_model'] = rotation, model
    model['rules'][0]['source_id'] = 'binding:part'
    skeleton = native()['effect_bindings'][0]
    skeleton['parts'][0]['rule']['rule_id'] = 'qualified'
    plan['effect_bindings'] = [skeleton]
    return plan


def authorize(plan, db):
    part = plan['effect_bindings'][0]['parts'][0]
    part['dependencies'] = {'mode': 'manual_context', 'context_fingerprint': None, 'note': 'Synthetic recipient qualification reviewed, not game-source approval.'}
    review(plan, db)
    part['dependencies']['context_fingerprint'] = fingerprint(context_view(input_view(plan)))
    return plan


def test_old_source_only_does_not_authorize_new_beneficiary_semantics(tmp_path):
    plan, db = plan_case(), tmp_path / 'absent.db'
    review(plan, db)
    report = compose_equipment_plan(plan, db)
    assert report['state'] == 'pending'
    assert any(i['code'] == 'beneficiary_source_scope_unreviewed' for i in report['missing_inputs'])
    assert report['mechanics_model']['rules'] == []


def test_manual_context_roundtrip_and_saved_result_validation(tmp_path):
    db = tmp_path / 'absent.db'
    plan = authorize(plan_case(), db)
    out = run_plan_calc(plan, db, capture_display=True)
    validate_delivery_calculated(out)
    assert [h['expected'] for h in out['result']['hits']] == [45, 90, 90, 90, 90, 45, 90, 45]
    path = tmp_path / 'qualified.plan.json'
    path.write_text(json.dumps(out['plan'], ensure_ascii=False), encoding='utf-8')
    again = run_plan_calc(read_plan(path)['plan'], db, capture_display=True)
    assert again['result'] == out['result']
    assert json.loads(result_bytes(out['result'], out['result_receipt'], 'json'))['result'] == out['result']
    assert not db.exists()


def test_changed_qualification_invalidates_existing_context(tmp_path):
    db = tmp_path / 'absent.db'
    plan = authorize(plan_case(), db)
    plan['mechanics_model']['rules'][0]['beneficiaries']['eligibility']['characters'] = ['c']
    report = compose_equipment_plan(plan, db)
    assert report['state'] == 'pending'
    assert any(i['code'] == 'dependency_stale' for i in report['missing_inputs'])


def test_rehashed_fake_selected_actor_rejected_by_saved_replay(tmp_path):
    db = tmp_path / 'absent.db'
    value = run_plan_calc(authorize(plan_case(), db), db)
    row = next(x for x in value['result']['mechanics_trace'] if x.get('beneficiary_selection') and x['type'] == 'activation')
    row['beneficiary_selection']['recipients'] = ['a']
    value['result_receipt']['result_fingerprint'] = fingerprint(value['result'])
    with pytest.raises(PlanError):
        validate_delivery_calculated(value)


@pytest.mark.parametrize('order,damage', [(5, [45, 45, 45, 45, 45]), (20, [45, 90, 90, 45, 45])])
def test_real_state_tick_empty_is_completed_not_activated(order, damage):
    data = field_case(order)
    data[2]['rules'][0]['beneficiaries']['eligibility'] = copy.deepcopy(QUALIFY)
    out = execute_mechanics(*data)
    assert out['result'] is not None, out['issues']
    assert [h['expected'] for h in out['result']['hits']] == damage
    rows = out['state_runtime_trace']
    assert rows[0]['outcome'] == 'not_activated'
    assert out['state_runtime']['engine_queue_integration'] == 'mechanics_event_queue'
    assert any(x.get('reason') == 'no_eligible_beneficiary' for x in out['mechanics_trace'])


@pytest.mark.parametrize('stage', ['prepare', 'final'])
@pytest.mark.parametrize('mutation', ['owner', 'whitelist', 'equivalent_container'])
def test_rq2_spine_keeps_new_qualification_original_edges(monkeypatch, stage, mutation):
    data = values(delay=0, duration=700000)
    second_character(data[0], data[1])
    target = data[2]['rules'][0]
    target['beneficiaries']['eligibility'] = copy.deepcopy(QUALIFY)
    rid = target['id']
    method = 'begin' if stage == 'prepare' else 'attach'
    original = getattr(ReactionRuntime, method)
    changed = []
    def run(self, *args):
        if not changed:
            scope = self.context.rules[rid]['beneficiaries']
            if mutation == 'owner':
                scope['eligibility']['exclude_owner'] = False
            elif mutation == 'whitelist':
                scope['eligibility']['characters'] = ['b']
            else:
                scope['eligibility'] = dict(scope['eligibility'])
            changed.append(True)
        return original(self, *args)
    monkeypatch.setattr(ReactionRuntime, method, run)
    out = execute_mechanics(*data)
    assert changed
    assert out['result'] is None
    assert out['resource_reactions']['verification_status'] == 'verification_pending'
