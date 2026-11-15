"""Actual effect resolver checks; native/runtime behavior is tested separately."""
import copy
import json
from pathlib import Path

import pytest

from genshin_app.equipment import attributes as candidate
from genshin_app.equipment.schema import PlanError
from tests.test_attribute_app_sources import attribute_plan, known_classification


def plan():
    p = attribute_plan()
    model = p['mechanics_model']['attribute_model']
    model['contract'] = 'attribute-ledger-v3'
    model['initial_basis'][0]['stat'] = 'em'
    model['initial_basis'].append({'actor': 'a', 'stat': 'atk', 'included_components': []})
    effects = p['mechanics_model']['rules'][0]['effects']
    effects.extend([
        {'id': 'atk-flat', 'benefit_type_id': 'benefit.stat_add.atk', 'value': 200, 'domain': 'provider'},
        {'id': 'atk-ratio', 'benefit_type_id': 'benefit.stat_pct.atk', 'value': .2, 'domain': 'provider'},
    ])
    return p


def resolve(p, *, execution=None, implementation=candidate):
    issues = []
    output = implementation.prepare_attribute_model(
        p, copy.deepcopy(p['mechanics_model']) if execution is None else execution,
        copy.deepcopy(p['manual_template']), 'fixture-source', lambda *args: issues.append(args))
    return (*output, issues)


def classification(p, effect_id):
    row = known_classification(p)
    row['effect_ref']['effect_id'] = effect_id
    row['component']['effect_id'] = effect_id
    row['nonextra_inclusion'] = 'unknown'
    return row


def test_actual_dimensions_and_unknown_are_separate_from_native():
    p = plan(); before = copy.deepcopy(p)
    model, adoption, issues = resolve(p)
    assert p == before
    assert adoption['contract'] == 'attribute-source-adoption-v2'
    assert adoption['review_status'] == 'pending'
    assert any(issue[0] == 'attribute_scope_unreviewed' for issue in issues)
    rows = {r['component']['effect_id']: r for r in adoption['source_effects']}
    assert len(rows) == 3
    assert rows['atk-flat']['output_stat'] == 'atk'
    assert rows['atk-flat']['value_unit'] == 'flat'
    assert rows['atk-flat']['application'] == {'kind': 'flat'}
    assert rows['atk-ratio']['value_unit'] == 'ratio'
    assert rows['atk-ratio']['application'] == {'kind': 'recipient_base_fraction'}
    assert all(r['nonextra_inclusion'] == 'unknown' and r['status'] == 'pending'
               for r in model['effect_classifications'])
    assert all('output_stat' not in r and 'application' not in r
               for r in model['effect_classifications'])
    assert not p['mechanics_model']['attribute_model']['effect_classifications']


@pytest.mark.parametrize('stat', ['em', 'atk'])
def test_source_on_wrong_initial_stat_is_rejected(stat):
    p = plan(); row = classification(p, 'atk-flat' if stat == 'em' else 'value')
    initial = next(b for b in p['mechanics_model']['attribute_model']['initial_basis'] if b['stat'] == stat)
    initial['included_components'] = [{'component': row['component'], 'value': 50, 'nonextra_inclusion': 'unknown'}]
    with pytest.raises(PlanError, match='属性种类'):
        resolve(p)


def test_included_ratio_source_records_flat_panel_explanation_without_conversion():
    p = plan(); row = classification(p, 'atk-ratio')
    basis = p['mechanics_model']['attribute_model']['initial_basis'][1]
    basis['included_components'] = [{'component': row['component'], 'value': 180, 'nonextra_inclusion': 'unknown'}]
    p['manual_template']['characters'][0]['stats'].update(atk=2000, base_atk=900)
    before = copy.deepcopy(p['manual_template'])
    model, adoption, _ = resolve(p)
    record = next(r for r in adoption['components'] if r['status'] == 'included_in_panel')
    assert record['value_unit'] == 'ratio' and record['included_value_unit'] == 'flat'
    assert model['initial_basis'][1]['included_components'][0]['value'] == 180
    assert p['manual_template'] == before


@pytest.mark.parametrize('where', ['dynamic', 'initial'])
@pytest.mark.parametrize('inclusion', ['included', 'excluded'])
def test_atk_nonextra_not_inferred_or_authorized(where, inclusion):
    p = plan(); row = classification(p, 'atk-flat'); row['nonextra_inclusion'] = inclusion
    config = p['mechanics_model']['attribute_model']
    if where == 'dynamic':
        config['effect_classifications'] = [row]
    else:
        config['initial_basis'][1]['included_components'] = [
            {'component': row['component'], 'value': 200, 'nonextra_inclusion': inclusion}]
    with pytest.raises(PlanError) as raised:
        resolve(p)
    assert raised.value.reason == 'invalid_attribute_source'
    if where == 'initial':
        assert raised.value.details[0]['path'].endswith('nonextra_inclusion')
    else:
        assert 'NONEXTRA' in str(raised.value)


@pytest.mark.parametrize('state', ['pending', 'excluded'])
def test_missing_execution_rule_stays_missing_with_original_identity(state):
    p = plan(); p['effect_bindings'][0]['parts'][0]['state'] = state
    config = p['mechanics_model']['attribute_model']
    config['effect_classifications'] = [classification(p, 'atk-flat')]
    execution = copy.deepcopy(p['mechanics_model']); execution['rules'] = []
    model, adoption, _ = resolve(p, execution=execution)
    assert not model['effect_classifications'] and not adoption['automatic_unknown']
    assert adoption['components'][0]['status'] == state
    assert all(r['active'] is False for r in adoption['source_effects'])


@pytest.mark.parametrize('stat', ['em', 'atk', 'hp'])
def test_basis_uniqueness_and_supported_stat(stat):
    p = plan()
    p['mechanics_model']['attribute_model']['initial_basis'].append(
        {'actor': 'a', 'stat': stat, 'included_components': []})
    with pytest.raises(PlanError) as raised:
        resolve(p)
    assert raised.value.reason == 'invalid_attribute_source'
    if stat == 'hp':
        assert any(issue['path'].endswith('.stat') for issue in raised.value.details)
    else:
        assert '唯一' in str(raised.value)


@pytest.mark.parametrize('zone', ['stat_add', 'stat_pct'])
def test_manual_atk_uses_actual_effect_metadata(zone):
    p = plan()
    p['manual_template']['buffs'].append({
        'id': 'manual-atk', 'target': 'a', 'extensions': {'owner': 'a', 'source_id': 'manual:atk'},
        'effects': [{'id': 'value', 'benefit_type_id':'benefit.'+zone+'.atk', 'zone': zone, 'stat': 'atk', 'value': .2}]})
    _, adoption, _ = resolve(p)
    row = next(r for r in adoption['source_effects'] if r['effect_ref']['kind'] == 'manual_buff')
    assert row['value_unit'] == ('ratio' if zone == 'stat_pct' else 'flat')
    assert row['output_stat'] == 'atk'


def test_impossible_recipient_is_rejected_for_atk():
    p = plan(); p['mechanics_model']['rules'][0]['beneficiaries'] = {'mode': 'self', 'scope': 'locked'}
    other = copy.deepcopy(p['manual_template']['characters'][0]); other['id'] = 'b'
    p['manual_template']['characters'].append(other)
    row = classification(p, 'atk-flat')
    p['mechanics_model']['attribute_model']['initial_basis'].append(
        {'actor': 'b', 'stat': 'atk', 'included_components': [
            {'component': row['component'], 'value': 200, 'nonextra_inclusion': 'unknown'}]})
    with pytest.raises(PlanError, match='受益者'):
        resolve(p)


@pytest.mark.parametrize('contract', ['attribute-ledger-v1', 'attribute-ledger-v2'])
@pytest.mark.parametrize('variant', ['plain', 'classified', 'seed', 'excluded', 'manual'])
def test_legacy_full_helper_output_is_exact(contract, variant):
    p = attribute_plan(); config = p['mechanics_model']['attribute_model']; config['contract'] = contract
    # Pin the historical input, rather than changing the golden when new_plan
    # gains a new policy-bearing field. Production must still hash that field.
    p.pop('direct_energy_policy', None)
    row = known_classification(p)
    if variant in ('classified', 'excluded'):
        config['effect_classifications'] = [row]
    if variant == 'seed':
        config['initial_basis'][0]['included_components'] = [
            {'component': row['component'], 'value': 50, 'nonextra_inclusion': 'included'}]
    if variant == 'excluded':
        p['effect_bindings'][0]['parts'][0]['state'] = 'excluded'
    if variant == 'manual':
        p['manual_template']['buffs'].append({'id': 'manual-em', 'target': 'a',
            'extensions': {'owner': 'a', 'source_id': 'manual:em'},
            'effects': [{'id': 'manual-value', 'zone': 'em', 'stat': 'all', 'value': 50}]})
    execution = copy.deepcopy(p['mechanics_model'])
    if variant == 'excluded':
        execution['rules'] = []
    expected=json.loads((Path(__file__).parent/'fixtures/attribute_source_legacy.json').read_text(encoding='utf8'))
    assert json.loads(json.dumps(resolve(p,execution=execution)))==expected[contract+'/'+variant]
