"""Independent actual DB adoption and final writer-graph qualification."""
from __future__ import annotations

import copy

import pytest

from genshin_app.equipment import attributes, compose_equipment_plan, run_plan_calc
from genshin_app.equipment.passive_receipt import validate_passive_result_receipt
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_engine import runtime_passive_attribute_compiler as compiler
from tests.test_mechanics_execution import rule
from tests.test_passive_attribute_app import (
    approve,
)
from tests.test_passive_attribute_app import (
    dynamic_plan as dynamic_case,  # noqa: F401
)
from tests.test_passive_sources_app import (  # noqa: F401
    passive_attack_db,
    passive_plan,
)


def test_single_attribute_adoption_is_stable_without_repeated_confirmation(dynamic_case):  # noqa: F811
    plan, db = dynamic_case
    plan = approve(plan, db)
    first = compose_equipment_plan(plan, db)
    assert first['state'] == 'ready', first['missing_inputs']
    expected = copy.deepcopy(first['attribute_source_adoption'])
    for _ in range(3):
        out = compose_equipment_plan(plan, db)
        assert out['state'] == 'ready'
        assert out['source_fingerprint'] == first['source_fingerprint']
        assert out['attribute_source_adoption'] == expected
    result = run_plan_calc(plan, db)
    assert result['result_receipt']['action_events']['compiler'] == 'calibrated-action-events-v2'


def test_forged_component_hash_is_still_rejected_after_causal_digest_exclusion(dynamic_case):  # noqa: F811
    plan, db = dynamic_case
    plan = approve(plan, db)
    plan['mechanics_model']['attribute_model']['effect_classifications'][0]['component']['source_fingerprint'] = '0'*64
    report = compose_equipment_plan(plan, db)
    assert report['state'] == 'pending'
    assert any(i['code'] == 'attribute_source_stale' for i in report['missing_inputs'])
    with pytest.raises(PlanError):
        run_plan_calc(plan, db)


@pytest.mark.parametrize('key,value', [('provider', 'b'), ('part_id', 'unreviewed')])
def test_component_identity_cannot_be_forged_to_escape_source_resolver(dynamic_case, key, value):  # noqa: F811
    plan, db = dynamic_case
    plan = approve(plan, db)
    plan['mechanics_model']['attribute_model']['effect_classifications'][0]['component'][key] = value
    with pytest.raises(PlanError):
        compose_equipment_plan(plan, db)


def test_actual_writer_value_change_invalidates_existing_source_and_component_confirmation(dynamic_case):  # noqa: F811
    plan, db = dynamic_case
    plan = approve(plan, db)
    plan['mechanics_model']['rules'][0]['effects'][0]['value'] += 1
    report = compose_equipment_plan(plan, db)
    assert report['state'] == 'pending'
    assert any(i['code'] == 'passive_confirmation_required' for i in report['missing_inputs'])
    assert report['attribute_source_adoption']['review_status'] == 'pending'
    assert report['attribute_source_adoption']['context_fingerprint'] != plan['attribute_review']['context_fingerprint']
    with pytest.raises(PlanError):
        run_plan_calc(plan, db)


def test_late_assembled_post_writer_is_rejected_on_final_actual_model(dynamic_case, monkeypatch):  # noqa: F811 - pytest fixture injection
    plan, db = dynamic_case
    plan = approve(plan, db)
    assert compose_equipment_plan(plan, db)['state'] == 'ready'
    prepare = attributes.prepare_attribute_model

    def late(*args, **kwargs):
        attr, adoption = prepare(*args, **kwargs)
        # Simulate a later compiler's actual delivered EM write after the preview.
        args[1]['rules'].append(rule('late', 'benefit.em.all', 50,
            trigger={'event': 'hit', 'phase': 'post'}))
        return attr, adoption

    monkeypatch.setattr(attributes, 'prepare_attribute_model', late)
    report = compose_equipment_plan(plan, db)
    assert report['state'] == 'pending'
    assert any(i['code'] == 'attribute_sampling_causality_pending' for i in report['missing_inputs'])
    final = report['passive_compilations'][0]['final_attribute_sampling_qualification']
    assert any(r['effect_ref'].get('rule_id') == 'late' and r['status'] == 'same_proc_sampling_pending'
               for r in final['writers'])
    with pytest.raises(PlanError):
        run_plan_calc(plan, db)


def test_old_native_acceptance_cannot_override_revoked_packaged_sample(dynamic_case, monkeypatch):  # noqa: F811 - pytest fixture injection
    plan, db = dynamic_case
    plan = approve(plan, db)
    package = copy.deepcopy(compiler._profile())
    fact = next(f for f in package['facts'] if f['kind'] == 'attribute_sample')
    package['reviews'][fact['id']+'@'+str(fact['revision'])]['status'] = 'revoked'
    monkeypatch.setattr(compiler, '_profile', lambda: package)
    report = compose_equipment_plan(plan, db)
    assert report['state'] == 'pending'
    assert report['passive_compilations'][0]['status'] != 'ready'
    with pytest.raises(PlanError):
        run_plan_calc(plan, db)


def test_snapshot_export_recompiles_packaged_proof_after_registry_change(dynamic_case, monkeypatch):  # noqa: F811 - pytest fixture injection
    plan, db = dynamic_case
    out = run_plan_calc(approve(plan, db), db)
    validate_passive_result_receipt(out['result'], out['result_receipt'])
    package = copy.deepcopy(compiler._profile())
    next(iter(package['reviews'].values()))['status'] = 'stale'
    monkeypatch.setattr(compiler, '_profile', lambda: package)
    with pytest.raises(ValueError):
        validate_passive_result_receipt(out['result'], out['result_receipt'])
    with pytest.raises(PlanError):
        result_bytes(out['result'], out['result_receipt'], 'xlsx')
