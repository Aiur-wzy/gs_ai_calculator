"""Fresh full source through the App, with no caller-generated EM rules."""
from __future__ import annotations

import copy
import sqlite3

import pytest

from genshin_app.equipment import compose_equipment_plan, new_plan, run_plan_calc
from genshin_app.equipment.attributes import prepare_attribute_review
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_engine.runtime_reaction_em_compiler import default_scenario
from tests.test_runtime_reaction_em_source import bundle, prepared_queue


@pytest.fixture
def em_plan(tmp_path, monkeypatch):
    from genshin_db import queries, runtime_queries
    template, rotation, model = prepared_queue((.2, .3))
    for character in template['characters']:
        character['stats']['energy_recharge'] = 1
    model['rules'] = []
    model['initial_state']['rules'] = []
    model['attribute_model']['effect_classifications'] = []
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=model)
    db = tmp_path / 'em-fixture.db'
    sqlite3.connect(db).close()
    data = bundle()
    version = {'version_line': 'release', 'upstream_commit': data['source']['upstream_commit'],
               'equipment_schema_version': 1, 'built_at': 'isolated-test'}
    monkeypatch.setattr(queries, 'get_meta', lambda *a, **k: {'datasets': {'equipment': {'lines': {'release': {
        'state': 'ready', 'upstream_commit': version['upstream_commit'], 'schema_version': 1, 'built_at': 'isolated-test'}}}}})
    monkeypatch.setattr(queries, 'get_artifact_set_detail', lambda *a, **k: {
        'artifact_set': {'name': '教官'}, 'effects': [{'required_count': 4, 'effect': {
            'affix_id': data['source']['affix_id'], 'name': '反应精通', 'source_refs': [{'kind': 'database'}]}}],
        'annotations': []})
    reads = []
    def assemble(*args):
        reads.append(args)
        return copy.deepcopy(data)
    monkeypatch.setattr(runtime_queries, 'assemble_runtime_source', assemble)
    monkeypatch.setattr(queries, 'get_character_detail', lambda *a, **k: pytest.fail('EM reaction is not a burst trigger'))
    plan['equipment_inputs'] = {'a': {'mode': 'panel', 'panel_policy': 'static_included', 'character_ref': None,
        'weapon': None, 'artifact_sets': [{'set_id': 10007, 'count': 4}],
        'source_refs': [{'kind': 'manual', 'note': '初始给定面板包含两件常驻精通'}], 'data_version': version}}
    requirement = compose_equipment_plan(plan, db)['effect_requirements'][0]
    binding = copy.deepcopy(requirement['proposed_binding'])
    scenario = default_scenario()
    scenario.update(target_id='enemy', status='confirmed', source='固定单人、全队存活且在来源范围内的无卡肉工程场景')
    binding['upstream_recipe'] = {'schema_version': 1, 'scenario': scenario}
    plan['effect_bindings'] = [binding]
    return plan, db, data, reads


def accept_source(plan, db):
    report = compose_equipment_plan(plan, db)
    plan['effect_bindings'][0]['upstream_recipe']['scenario'].update(
        report['upstream_compilations'][0]['required_confirmation'])
    return report


def prepared(plan, db):
    accept_source(plan, db)
    return prepare_attribute_review(plan, '核对初始总精通、NONEXTRA及当前精通来源', db)['plan']


def test_source_is_private_and_final_scope_is_required(em_plan):
    plan, db, _, _ = em_plan
    original = copy.deepcopy(plan)
    first = compose_equipment_plan(plan, db)
    assert first['state'] == 'pending' and first['plan']['resolved_template'] is None
    assert not first['mechanics_model']['rules']
    assert plan == original
    accept_source(plan, db)
    report = compose_equipment_plan(plan, db)
    compiled = report['upstream_compilations'][0]
    assert compiled['app_scope']['status'] == 'ready'
    assert compiled['coverage_certificate']['execution_gate'] == 'final_actual_model_checked'
    assert compiled['execution_status'] == 'ready'
    assert report['state'] == 'pending'  # Initial panel review remains a separate decision.
    assert not plan['mechanics_model']['rules']


def test_actual_app_uses_fresh_source_and_classification_then_parent_join(em_plan):
    plan, db, _, reads = em_plan
    plan = prepared(plan, db)
    before = copy.deepcopy(plan)
    out = run_plan_calc(plan, db)
    assert plan == before
    compiled = out['report']['upstream_compilations'][0]
    assert compiled['rules'][0]['effects'][0]['value'] == 120
    classifications = out['report']['mechanics_model']['attribute_model']['effect_classifications']
    assert len(classifications) == 1 and classifications[0]['nonextra_inclusion'] == 'included'
    assert not out['plan']['mechanics_model']['attribute_model']['effect_classifications']
    assert len(reads) >= 3
    proof = out['result']['reaction_em_execution']
    assert [row['parent_run_id'] for row in proof['activations']] == ['0:h0', '0:h1']
    assert out['result']['reaction_em_compilations'] == [compiled]
    # Independent hand formula: flat 120 EM cannot benefit its parent reaction.
    values = [h['expected'] for h in out['result']['hits']]
    assert values == pytest.approx([45, .6 * 1446.85 * .9,
                                   45, .6 * 1446.85 * (1 + 16 * 120 / 2120) * .9])
    from genshin_app.equipment.reaction_em_receipt import validate_reaction_em_receipt
    validate_reaction_em_receipt(out['plan'], out['result'], out['report'], out['result_receipt'])


@pytest.mark.parametrize('mutation', ['target', 'v3', 'snapshot', 'late_changed_rule'])
def test_final_scope_failure_withdraws_generated_rule_and_classification(em_plan, monkeypatch, mutation):
    plan, db, _, _ = em_plan
    accept_source(plan, db)
    if mutation == 'target':
        plan['effect_bindings'][0]['upstream_recipe']['scenario']['target_id'] = 'other'
        accept_source(plan, db)
    elif mutation == 'v3':
        plan['mechanics_model']['attribute_model']['contract'] = 'attribute-ledger-v3'
        for row in plan['mechanics_model']['attribute_model']['initial_basis']:
            row['stat'] = 'em'
    elif mutation == 'snapshot':
        plan['manual_template']['hits'][0]['snapshot'] = True
    else:
        from genshin_app.equipment import reaction_em_sources
        original = reaction_em_sources.final_scope
        def altered(p, t, b, c):
            p['mechanics_model']['rules'][0]['effects'][0]['value'] = 999
            return original(p, t, b, c)
        monkeypatch.setattr(reaction_em_sources, 'final_scope', altered)
    report = compose_equipment_plan(plan, db)
    assert report['state'] == 'pending' and report['plan']['resolved_template'] is None
    assert not report['mechanics_model']['rules']
    assert not report['mechanics_model']['initial_state']['rules']
    assert not report['mechanics_model']['attribute_model']['effect_classifications']
    record = report['upstream_compilations'][0]
    assert record['status'] == record['execution_status'] == 'pending'
    assert record['rules'] == record['parts'] == record['attribute_writes'] == []


def test_source_change_cannot_restore_saved_calculation(em_plan):
    plan, db, data, _ = em_plan
    plan = prepared(plan, db)
    old = run_plan_calc(plan, db)
    data['param_list'][1] = 999
    report = compose_equipment_plan(old['plan'], db)
    assert report['state'] == 'pending' and report['plan']['resolved_template'] is None
    assert not report['mechanics_model']['rules']


def test_real_later_action_failure_keeps_proven_prefix_without_full_axis_dps(em_plan):
    from genshin_app.equipment.reaction_em_receipt import validate_reaction_em_receipt
    from tests.test_mechanics_execution import ability, action
    plan, db, _, _ = em_plan
    plan['rotation_model']['abilities'].append(ability('offfield_attempt', 'b'))
    plan['rotation_model']['actions'].append(action('later_failure', 'offfield_attempt', .5, .6, 30,
                                                   repeat={'mode': 'once'}))
    out = run_plan_calc(prepared(plan, db), db)
    result = out['result']
    assert result['mechanics']['complete_first_cycle'] is False
    assert result['summary']['dps'] is None
    assert result['rotation_check']['damage_interpretation']['kind'] == 'executed_prefix'
    assert len(result['reaction_em_execution']['activations']) == 2
    validate_reaction_em_receipt(out['plan'], result, out['report'], out['result_receipt'])
