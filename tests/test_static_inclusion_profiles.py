"""Version selection uses synthetic snapshots, never a registration of real index7."""
# ruff: noqa: F811
import copy
import json
from pathlib import Path

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment import static_inclusion as compiler
from genshin_app.equipment.static_inclusion_receipt import (
    validate_static_inclusion_receipt,
)
from genshin_app.equipment.static_inclusion_sources import static_inclusion_capabilities
from genshin_db import equipment_queries, runtime_queries
from genshin_db.runtime_catalog import fingerprint
from tests.static_inclusion_fixtures import (  # noqa: F401
    DB,
    MATERIALS,
    snapshot_session,
)
from tests.test_static_inclusion import material, rehash
from tests.test_static_inclusion_app import static_plan, version  # noqa: F401

HERE = Path(__file__).parent / 'fixtures/static_inclusion_profiles'
SYNTHETIC = json.loads((HERE / 'synthetic-profile.json').read_text(encoding='utf8'))


def selection():
    p = SYNTHETIC['profile']
    return {'profile_id': p['id'], 'profile_version': p['version'], 'profile_fingerprint': SYNTHETIC['fingerprint']}


def register(monkeypatch):
    monkeypatch.setattr(compiler, '_REGISTERED_PROFILES', (
        (compiler.PROFILE, compiler.PROFILE_FINGERPRINT),
        (copy.deepcopy(SYNTHETIC['profile']), SYNTHETIC['fingerprint'])))


def new_snapshot(snapshot):
    snapshot.update(copy.deepcopy(SYNTHETIC['profile']['snapshot_scope']))


def new_material(m):
    for key in ('parent', 'source'):
        new_snapshot(m[key]['snapshot'])
    m['source']['source']['snapshot_id'] = m['source']['snapshot']['snapshot_id']
    m['source']['normalization'] = copy.deepcopy(m['source']['snapshot']['normalization'])
    rehash(m)
    return m


def patch_runtime(monkeypatch, set_id=10007):
    from genshin_app.equipment import static_inclusion_sources
    original_definition = runtime_queries.get_runtime_definition
    original_source = runtime_queries.assemble_runtime_source
    matched = {m['source']['source']['affix_id'] for m in MATERIALS if m['selection']['set_id'] == set_id}
    transactions = []
    original_select = static_inclusion_sources.select_static_inclusion_profile
    def select(snapshot):
        transactions.append(equipment_queries.borrow_read_connection(DB))
        return original_select(snapshot)
    def definition(*args, **kwargs):
        transactions.append(equipment_queries.borrow_read_connection(DB))
        row = original_definition(*args, **kwargs)
        if json.loads(row['payload_json'])['setId'] == set_id:
            new_snapshot(row['snapshot'])
            row['content_fingerprint'] = fingerprint({k: v for k, v in row.items() if k != 'content_fingerprint'})
        return row
    def source(db, affix, line='release', *, snapshot_id=None):
        transactions.append(equipment_queries.borrow_read_connection(DB))
        row = original_source(db, affix, line, snapshot_id=None)
        if affix in matched:
            new_snapshot(row['snapshot'])
            row['normalization'] = copy.deepcopy(row['snapshot']['normalization'])
            row['source']['snapshot_id'] = row['snapshot']['snapshot_id']
            row['content_fingerprint'] = fingerprint({k: v for k, v in row.items() if k != 'content_fingerprint'})
        assert snapshot_id == row['snapshot']['snapshot_id']
        return row
    monkeypatch.setattr(runtime_queries, 'get_runtime_definition', definition)
    monkeypatch.setattr(runtime_queries, 'assemble_runtime_source', source)
    monkeypatch.setattr(static_inclusion_sources, 'select_static_inclusion_profile', select)
    return transactions


def test_legacy_complete_shape_matches_frozen_reference():
    expected = json.loads((HERE / 'legacy-shapes.json').read_text(encoding='utf8'))
    assert [compiler.compile_static_inclusion(m) for m in MATERIALS] == expected
    for m, out in zip(MATERIALS, expected, strict=True):
        assert compiler.compile_static_inclusion(m, **{k: out[k] for k in selection()}) == out


@pytest.mark.parametrize('kwargs', [
    {'profile_id': None}, {'profile_version': None}, {'profile_fingerprint': None},
    {'profile_id': compiler.PROFILE['id']},
    {'profile_id': compiler.PROFILE['id'], 'profile_version': 1},
    {'profile_id': compiler.PROFILE['id'], 'profile_version': True, 'profile_fingerprint': compiler.PROFILE_FINGERPRINT},
    {'profile_id': compiler.PROFILE['id'], 'profile_version': 1.0, 'profile_fingerprint': compiler.PROFILE_FINGERPRINT},
    {'profile_id': 'unknown', 'profile_version': 1, 'profile_fingerprint': compiler.PROFILE_FINGERPRINT},
    {'profile_id': compiler.PROFILE['id'], 'profile_version': 1, 'profile_fingerprint': '0' * 64},
])
def test_explicit_incomplete_or_invalid_selection_has_no_fallback(kwargs):
    out = compiler.compile_static_inclusion(material(), **kwargs)
    assert out['status'] == 'pending' and out['profile_fingerprint'] is None
    assert 'declaration' not in out


def test_unregistered_new_snapshot_remains_pending():
    m = new_material(material())
    assert compiler.compile_static_inclusion(m)['status'] == 'pending'
    assert compiler.compile_static_inclusion(m, **selection())['status'] == 'pending'
    chosen = compiler.select_static_inclusion_profile(m['parent']['snapshot'])
    assert chosen['status'] == 'pending' and chosen['profile_fingerprint'] is None


def test_synthetic_profile_exact_selection_without_extra_numeric_delivery(monkeypatch):
    register(monkeypatch)
    m = new_material(material())
    chosen = compiler.select_static_inclusion_profile(m['parent']['snapshot'])
    assert chosen == {'status': 'selected', **selection()}
    out = compiler.compile_static_inclusion(m, **selection())
    assert out['status'] == 'shape_ready'
    assert out['declaration']['numeric_application'] is False
    assert out['declaration']['properties'] == compiler.compile_static_inclusion(material())['declaration']['properties']
    assert compiler.compile_static_inclusion(material(), **selection())['status'] == 'pending'
    assert compiler.compile_static_inclusion(m)['status'] == 'pending'


@pytest.mark.parametrize('field,value', [
    ('version_line', 'beta'), ('upstream_commit', 'f' * 40), ('schema_version', True),
    ('indexer_version', '6'), ('snapshot_id', 'f' * 64), ('manifest_fingerprint', 'f' * 64),
    ('normalization', {}),
])
def test_v2_scope_mismatch_is_not_relabelled(monkeypatch, field, value):
    register(monkeypatch)
    m = new_material(material())
    for part in ('parent', 'source'):
        m[part]['snapshot'][field] = value
    rehash(m)
    assert compiler.compile_static_inclusion(m, **selection())['status'] == 'pending'


@pytest.mark.parametrize('target', ['legacy', 'registered', 'mapping', 'duplicate', 'missing_id', 'missing_version'])
def test_mutated_or_ambiguous_registry_fails_closed(monkeypatch, target):
    register(monkeypatch)
    if target == 'legacy':
        monkeypatch.setitem(compiler.PROFILE, 'param_slots', 9)
    elif target == 'registered':
        compiler._REGISTERED_PROFILES[1][0]['snapshot_scope']['snapshot_id'] = 'f' * 64
    elif target == 'mapping':
        monkeypatch.setitem(compiler.PROP_MAP, 'FIGHT_PROP_ATTACK_PERCENT', ('hp', 'percent'))
    elif target.startswith('missing_'):
        monkeypatch.delitem(compiler.PROFILE, target.removeprefix('missing_'))
    else:
        monkeypatch.setattr(compiler, '_REGISTERED_PROFILES', compiler._REGISTERED_PROFILES + (compiler._REGISTERED_PROFILES[1],))
    assert compiler.compile_static_inclusion(material())['status'] == 'pending'
    assert compiler.select_static_inclusion_profile(new_material(material())['parent']['snapshot'])['status'] == 'pending'
    assert static_inclusion_capabilities()['supported_profiles'] == []


def test_capability_mutation_cannot_change_registered_profile(monkeypatch):
    register(monkeypatch)
    caps = static_inclusion_capabilities()
    caps['supported_profiles'][1]['profile']['snapshot_scope']['snapshot_id'] = 'f' * 64
    caps['profile']['indexer_version'] = '7'
    assert compiler.compile_static_inclusion(new_material(material()), **selection())['status'] == 'shape_ready'
    assert static_inclusion_capabilities()['profile']['indexer_version'] == '6'


def test_fresh_unregistered_index7_has_no_v1_identity(version, monkeypatch):
    patch_runtime(monkeypatch)
    out = compose_equipment_plan(static_plan(version), DB)
    record = out['static_inclusion_compilations'][0]
    assert record['status'] == 'pending'
    assert record['shape_certificate'] is None and record['declaration'] is None
    assert record['source_component']['profile_fingerprint'] is None
    assert any(i['code'] == 'effect_pending' for i in out['missing_inputs'])


def test_fresh_synthetic_profile_same_transaction_and_damage(version, monkeypatch):
    old = run_plan_calc(static_plan(version), DB)
    register(monkeypatch)
    transactions = patch_runtime(monkeypatch)
    out = run_plan_calc(static_plan(version), DB)
    record = out['result']['static_inclusion_compilations'][0]
    assert record['shape_certificate']['profile_version'] == 2
    assert record['source_component']['profile_fingerprint'] == selection()['profile_fingerprint']
    assert len(transactions) == 3 and transactions[0] is not None
    assert all(t.connection is transactions[0].connection and t.in_transaction for t in transactions)
    assert out['result']['hits'] == old['result']['hits']
    assert out['result']['summary'] == old['result']['summary']
    expected_template = copy.deepcopy(old['plan']['resolved_template'])
    new_source = out['report']['source_fingerprint']
    assert new_source != old['report']['source_fingerprint']
    expected_template['meta']['equipment_resolution']['source_fingerprint'] = new_source
    assert out['plan']['resolved_template'] == expected_template
    validate_static_inclusion_receipt(out['plan'], out['result'], out['report'], out['result_receipt'])


def test_mixed_saved_records_select_each_profile_and_reject_second_wrong_identity(version, monkeypatch):
    register(monkeypatch)
    patch_runtime(monkeypatch)
    plan = static_plan(version)
    other = copy.deepcopy(plan['manual_template']['characters'][0])
    other['id'] = 'char2'
    plan['manual_template']['characters'].append(other)
    plan['equipment_inputs']['char2'] = copy.deepcopy(plan['equipment_inputs']['char1'])
    plan['equipment_inputs']['char2']['artifact_sets'] = [{'set_id': 10001, 'count': 2}]
    out = run_plan_calc(plan, DB)
    records = out['result']['static_inclusion_compilations']
    assert {r['shape_certificate']['profile_version'] for r in records} == {1, 2}
    validate_static_inclusion_receipt(out['plan'], out['result'], out['report'], out['result_receipt'])
    # Rehash the local record to ensure the failure is not merely its outer seal.
    from genshin_app.equipment.static_inclusion_receipt import _records_valid
    wrong = copy.deepcopy(records)
    wrong[1]['shape_certificate'].update(selection())
    wrong[1]['compilation_fingerprint'] = fingerprint({k: v for k, v in wrong[1].items() if k != 'compilation_fingerprint'})
    with pytest.raises(ValueError):
        _records_valid(wrong)


@pytest.mark.parametrize('field', ['profile_id', 'profile_version', 'profile_fingerprint'])
def test_saved_missing_identity_cannot_fallback_to_v1(version, field):
    out = run_plan_calc(static_plan(version), DB)
    record = copy.deepcopy(out['result']['static_inclusion_compilations'][0])
    record['shape_certificate'].pop(field)
    record['compilation_fingerprint'] = fingerprint({k: v for k, v in record.items() if k != 'compilation_fingerprint'})
    from genshin_app.equipment.static_inclusion_receipt import (
        validate_static_inclusion_result_receipt,
    )
    out['result']['static_inclusion_compilations'] = [record]
    with pytest.raises(ValueError):
        validate_static_inclusion_result_receipt(out['result'], out['result_receipt'])


@pytest.mark.parametrize('change', ['unknown', 'open_config', 'negative_zero', 'duplicate', 'parameters', 'wrong_parent', 'threshold'])
def test_v2_does_not_expand_raw_semantics(monkeypatch, change):
    register(monkeypatch)
    m = new_material(material())
    raw = m['source']['affix']['record']
    if change == 'unknown':
        raw['onHit'] = False
    elif change == 'open_config':
        raw['openConfig'] = ''
    elif change == 'negative_zero':
        raw['paramList'][0] = -0.0
    elif change == 'duplicate':
        raw['addProps'][1] = copy.deepcopy(raw['addProps'][0])
    elif change == 'parameters':
        raw['paramList'][0] = 1
    elif change == 'wrong_parent':
        raw['id'] += 1
    else:
        m['selection']['required_count'] = 4
    rehash(m)
    assert compiler.compile_static_inclusion(m, **selection())['status'] == 'pending'


@pytest.mark.parametrize('state', ['pending', 'excluded'])
def test_v2_does_not_override_explicit_user_binding(version, monkeypatch, state):
    register(monkeypatch)
    patch_runtime(monkeypatch)
    plan = static_plan(version)
    req = compose_equipment_plan(plan, DB)['effect_requirements'][0]
    binding = copy.deepcopy(req['proposed_binding'])
    binding['parts'] = [{'part_id': 'explicit', 'label': '保留意图', 'state': state,
        'rule': None, 'delivery': None, 'conditions': [],
        'dependencies': {'mode': 'source_only', 'context_fingerprint': None, 'note': '用户意图'},
        'reason': '不自动替换'}]
    plan['effect_bindings'] = [binding]
    before = copy.deepcopy(plan)
    out = compose_equipment_plan(plan, DB)
    assert 'static_inclusion_compilations' not in out
    assert out['plan']['effect_bindings'] == before['effect_bindings'] and plan == before
