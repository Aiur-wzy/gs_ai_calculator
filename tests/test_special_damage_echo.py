"""Anonymous finite extra-reaction branch, not a character answer fixture."""
from __future__ import annotations

import copy

import pytest
from test_elemental_multiaura_v4 import aura
from test_mechanics_execution import add, rule, second_character
from test_special_reaction_integration import equipment_snapshot
from test_special_reaction_mechanics_projection import _application_fixture, _enable

from genshin_engine.mechanics import execute_mechanics, validate_mechanics_model
from genshin_engine.special_damage_echo import SEMANTICS, capabilities


def fixture():
    template, rotation, model = _application_fixture(
        'electro', initial=[aura('hydro', .8, 'b')])
    second_character(template, rotation, 'b')
    template['characters'][1]['stats'].update(em=350, crit_rate=.6, crit_dmg=1.2)
    _enable(model, 'lunar_electro')
    add(model, rule('extra-source', effects=[]))
    model['special_damage_echoes'] = [entry()]
    return template, rotation, model


def entry():
    return {'id': 'extra-1', 'source_rule_id': 'extra-source', 'owner': 'a',
        'source_id': 'extra-source',
        'parent': {'reaction_type': 'lunar_electro', 'component': 'thundercloud',
                   'area_id': 'battlefield.primary', 'target_id': 'enemy', 't': .45},
        'branch': {'outcome': 'triggered', 'probability': .33},
        'semantics': {key: value['const'] for key, value in SEMANTICS['properties'].items()},
        'precision': {'kind': 'approximate', 'method': 'controlled no-world-side-effects diagnostic',
                      'uncertainty': 'extra-world, recursion and resource callbacks deliberately excluded'},
        'status': 'assumed', 'source': 'anonymous finite branch fixture'}


def test_extra_uses_actual_parent_contributors_and_real_formula_without_world_mutation():
    template, rotation, model = fixture()
    original = copy.deepcopy((template, rotation, model))
    out = execute_mechanics(template, rotation, model)
    assert out['status'] in ('passed', 'unproven'), out['issues']
    natural = [h for h in out['result']['hits'] if h['damage_system'] == 'lunar' and ':extra:' not in h['id']]
    extra = [h for h in out['result']['hits'] if ':extra:' in h['id']]
    assert len(natural) == len(extra) == 2
    assert [(h['character'], h['expected']) for h in extra] == [(h['character'], h['expected']) for h in natural]
    baseline = copy.deepcopy(model)
    del baseline['special_damage_echoes']
    ordinary = execute_mechanics(template, rotation, baseline)
    assert out['special_reaction_state'] == ordinary['special_reaction_state']
    assert [row for row in out['special_reaction_trace'] if ':extra:' not in row.get('event_id', '')] == ordinary['special_reaction_trace']
    assert 'special_damage_echoes' not in ordinary['result']
    assert len(out['special_reactions']['damage_weighting']) == 2
    assert out['special_damage_echoes']['precision'] == 'approximate'
    assert (template, rotation, model) == original


@pytest.mark.parametrize('case', ['different-time', 'different-target', 'different-area'])
def test_missing_actual_parent_is_pending_not_synthetic(case):
    template, rotation, model = fixture()
    parent = model['special_damage_echoes'][0]['parent']
    parent[{'different-time': 't', 'different-target': 'target_id', 'different-area': 'area_id'}[case]] = (
        .46 if case == 'different-time' else 'not-current')
    out = execute_mechanics(template, rotation, model)
    assert out['status'] == 'pending', out['issues']
    assert 'special_echo_parent_missing' in {row['code'] for row in out['issues']}


@pytest.mark.parametrize('change', ['duplicate', 'source', 'owner', 'confirmed', 'world', 'consumers', 'probability', 'precision'])
def test_invalid_contract_is_rejected(change):
    _, _, model = fixture()
    echo = model['special_damage_echoes'][0]
    if change == 'duplicate':
        other = copy.deepcopy(echo)
        other['id'] = 'alias'
        model['special_damage_echoes'].append(other)
    elif change == 'source': echo['source_id'] = 'unapproved'
    elif change == 'owner': echo['owner'] = 'b'
    elif change == 'confirmed': echo['status'] = 'confirmed'
    elif change == 'world': echo['semantics']['reaction_world_effects'] = 'consume'
    elif change == 'consumers': echo['semantics']['damage_consumers'] = 'none'
    elif change == 'probability': echo['branch']['probability'] = 1.2
    elif change == 'precision': echo['precision']['uncertainty'] = ' '
    assert validate_mechanics_model(model)


def test_source_must_be_active_at_parent_not_only_linked():
    template, rotation, model = fixture()
    model['rules'][0]['timing']['duration'] = .3
    out = execute_mechanics(template, rotation, model)
    assert out['status'] == 'pending', out['issues']
    assert 'special_echo_source_inactive' in {row['code'] for row in out['issues']}


def test_capability_contains_complete_closed_input_schema():
    assert capabilities()['schema']['items']['additionalProperties'] is False
    assert capabilities()['schema']['items']['properties']['semantics'] == SEMANTICS


def test_extra_keeps_normal_damage_quota_consumption_and_does_not_recurse():
    template, rotation, model = fixture()
    add(model, rule('crit-quota', 'benefit.crit_rate.all', .5,
        quota={'capacity': 2, 'scope': 'team', 'refresh': 'reset',
               'consume': {'event': 'reaction_damage', 'phase': 'pre'}}, duration=1))
    out = execute_mechanics(template, rotation, model)
    assert out['status'] in ('passed', 'unproven'), out['issues']
    consumed = [row for row in out['mechanics_trace'] if row['type'] == 'quota'
                and row['rule_id'] == 'crit-quota' and row['cycle'] == 0]
    assert [row['after'] for row in consumed] == [1, 0, 0, 0]
    assert len(out['special_damage_echoes']['trace']) == 1
    assert len([h for h in out['result']['hits'] if ':extra:' in h['id']]) == 2


def app_plan():
    from test_plan_mechanics import native
    plan = native()
    template, rotation, model = fixture()
    for character in template['characters']:
        character['stats']['energy_recharge'] = 1
    plan.update(manual_template=template, rotation_model=rotation, mechanics_model=model)
    model['rules'][0]['source_id'] = 'binding:part'
    model['special_damage_echoes'][0]['source_id'] = 'binding:part'
    part = plan['effect_bindings'][0]['parts'][0]
    part['rule']['rule_id'] = 'extra-source'
    part['dependencies'] = {'mode': 'manual_context', 'context_fingerprint': None,
                            'note': 'Review exact extra branch and excluded world/callback effects'}
    return plan


def authorize(plan, db):
    from test_plan_mechanics import review

    from genshin_app.equipment import compose_equipment_plan
    review(plan, db)
    context = compose_equipment_plan(plan, db)['context_fingerprint']
    plan['effect_bindings'][0]['parts'][0]['dependencies']['context_fingerprint'] = context
    return plan


def _fixture_equipment_projection(monkeypatch):
    # Supply the isolated equipment snapshot to the real qualification
    # projector; all source, event, formula and replay checks still execute.
    import genshin_engine.special_reaction_capabilities as producer
    catalog = producer.load_reviewed_special_reaction_capabilities()
    entry = next(row for row in catalog['entries']
                 if 'native_reaction_qualification.lunar_electro' in row['capability_tags'])
    equipment = equipment_snapshot(entry['character_id'], skill_depot_id=entry['required_skill_depot_id'])
    project = producer.project_special_reaction_party_capabilities_detailed

    def with_equipment_snapshot(inputs, *, actor_ids=None):
        if actor_ids is None:
            return project(inputs)
        assert 'a' in actor_ids and 'a' not in inputs
        return project({**inputs, 'a': equipment}, actor_ids=actor_ids)

    monkeypatch.setattr(producer, 'project_special_reaction_party_capabilities_detailed', with_equipment_snapshot)


def test_public_app_replay_result_export_and_saved_delivery_are_bound(tmp_path, monkeypatch):
    import json

    from genshin_app.calculation_integrity import result_transport_parts
    from genshin_app.equipment import run_plan_calc
    from genshin_app.equipment.delivery import validate_delivery_calculated
    from genshin_app.equipment.files import json_bytes
    from genshin_app.equipment.service import result_bytes
    from genshin_app.formula_workbook import validate_formula_calculation
    _fixture_equipment_projection(monkeypatch)
    db = tmp_path / 'absent.db'
    plan = authorize(app_plan(), db)
    fresh = run_plan_calc(plan, db, capture_display=True)
    assert fresh['result']['special_damage_echoes']['precision'] == 'approximate'
    assert any('近似分支' in row for row in fresh['result_receipt']['assumptions'])
    path = tmp_path / 'echo.calculated.json'
    path.write_bytes(json_bytes(fresh))
    saved = json.loads(path.read_text(encoding='utf8'))
    validate_delivery_calculated(saved)
    validate_formula_calculation(saved)
    exported = json.loads(result_bytes(saved['result'], saved['result_receipt'], 'json'))
    result_transport_parts(exported)
    from genshin_app.formula_workbook import _Projection
    projection = _Projection(saved, _session=None).finish()
    for key, expected in (('result.special_damage_echoes', saved['result']['special_damage_echoes']),
                          ('special_damage_echo_execution', saved['result']['special_damage_echo_execution'])):
        chunks = [(row[1].value, row[2].value) for row in projection.audit_sources.rows[1:]
                  if row[0].value == key]
        assert json.loads(''.join(text for _, text in sorted(chunks))) == expected
    assert not db.exists()


def test_unreviewed_or_revised_extra_scope_is_pending(tmp_path, monkeypatch):
    from genshin_app.equipment import compose_equipment_plan
    _fixture_equipment_projection(monkeypatch)
    db = tmp_path / 'absent.db'
    plan = app_plan()
    assert compose_equipment_plan(plan, db)['state'] == 'pending'
    authorize(plan, db)
    plan['mechanics_model']['special_damage_echoes'][0]['precision']['uncertainty'] += ' revised'
    report = compose_equipment_plan(plan, db)
    assert report['state'] == 'pending'
    assert {'dependency_stale', 'special_echo_source_scope_unreviewed'} & {row['code'] for row in report['missing_inputs']}


@pytest.mark.parametrize('field', ['numbers', 'summary', 'parent', 'precision', 'remove_proof', 'remove_report',
                                  'extra_member', 'skill', 'crit'])
def test_saved_self_resigned_tampering_is_rejected_by_real_replay(tmp_path, monkeypatch, field):
    from genshin_app.calculation_integrity import validate_result_binding
    from genshin_app.equipment import run_plan_calc
    from genshin_app.equipment.files import fingerprint
    _fixture_equipment_projection(monkeypatch)
    db = tmp_path / 'absent.db'
    fresh = run_plan_calc(authorize(app_plan(), db), db)
    result, receipt = fresh['result'], fresh['result_receipt']
    if field == 'numbers':
        next(h for h in result['hits'] if ':extra:' in h['id'])['expected'] += 100
    elif field == 'summary': result['summary']['total_damage'] += 100
    elif field == 'parent': result['special_damage_echoes']['trace'][0]['parent_event_id'] = 'fake'
    elif field == 'precision': result['special_damage_echoes']['precision'] = 'exact'
    elif field == 'remove_proof': del receipt['special_damage_echoes']
    elif field == 'remove_report': del result['special_damage_echoes']
    elif field == 'extra_member':
        extra = copy.deepcopy(result['hits'][-1])
        extra['id'] = 'injected'
        result['hits'].append(extra)
        summary = result['summary']
        summary['total_damage'] += extra['expected']
        summary['dps'] = summary['total_damage'] / 10
        summary['by_character'][extra['character']] += extra['expected']
        summary['by_skill'][extra['skill']] += extra['expected']
    elif field == 'skill': result['hits'][-1]['skill'] = 'false attribution'
    elif field == 'crit': result['hits'][-1]['crit'] += 1
    receipt['result_fingerprint'] = fingerprint(result)
    if field in ('parent', 'precision'):
        receipt['special_damage_echoes']['report_fingerprint'] = fingerprint(result['special_damage_echoes'])
    with pytest.raises(ValueError):
        validate_result_binding(result, receipt)


def test_extra_samples_after_parent_callbacks_and_does_not_trigger_new_callbacks():
    template, rotation, model = fixture()
    template['characters'][1]['stats']['crit_rate'] = 0
    add(model, rule('parent-callback', 'benefit.crit_rate.all', .2,
        trigger={'event': 'reaction_damage', 'phase': 'post'}, duration=1,
        stacking={'max': 4, 'add': 1, 'policy': 'independent'}))
    out = execute_mechanics(template, rotation, model)
    assert out['status'] in ('passed', 'unproven'), out['issues']
    hits = [h for h in out['result']['hits'] if h['damage_system'] == 'lunar']
    assert len(hits) == 4
    assert [h['zones']['crit_expected'] for h in hits] == pytest.approx([1, 1.1, 1.48, 1.2])
    activations = [row for row in out['mechanics_trace']
                   if row.get('type') == 'activation' and row.get('rule_id') == 'parent-callback']
    assert len(activations) == 2


def test_public_cli_discovers_and_executes_same_native_extra_contract(tmp_path, monkeypatch, capsys):
    import json

    from genshin_app import cli
    from genshin_app.calculation_integrity import result_transport_parts
    from genshin_app.cli import main
    from genshin_app.equipment.files import json_bytes
    _fixture_equipment_projection(monkeypatch)
    # This test covers public discovery/authoring/calc/readback, not bundle
    # installation. Isolate that independently tested startup seam instead of
    # copying a 700-MiB production DB for an anonymous no-DB mechanics fixture.
    monkeypatch.setattr(cli, '_require_runtime_database', lambda _: {})
    assert main(['capabilities']) == 0
    caps = json.loads(capsys.readouterr().out)['data']['special_reaction_contract']
    assert caps['extra_damage_contract'] == capabilities()
    source, target = tmp_path / 'input.plan.json', tmp_path / 'result.json'
    source.write_bytes(json_bytes(authorize(app_plan(), tmp_path / 'genshin.db')))
    assert main(['--data-dir', str(tmp_path), 'equipment', 'calc', '--plan', str(source),
                 '--output', str(target), '--summary']) == 0
    receipt = json.loads(capsys.readouterr().out)['data']
    assert receipt['completion']['task_admission'] == 'accepted'
    result, _ = result_transport_parts(json.loads(target.read_text(encoding='utf8')))
    assert len(result['special_damage_echoes']['trace']) == 1
