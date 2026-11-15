"""Actual raw-node materialization; no official/game-frame or live DB claims."""
import copy
import hashlib
import sqlite3
from contextlib import closing

import pytest

from genshin_db.runtime_queries import query_runtime
from genshin_db.semantic_store import import_bundle, read_record, review_record
from genshin_engine.mechanism_descriptor_compiler import compile_mechanism_descriptor
from genshin_runtime.mechanism_qualification import validate_execution_qualification
from genshin_runtime.mechanism_semantics import (
    descriptor_fingerprint,
    fact_fingerprint,
    fingerprint,
    validate_record,
)
from genshin_runtime.mechanism_source_binding import validate_source_binding
from tests.mechanism_contact_source_fixtures import (  # noqa: F401
    DEEPWOOD,
    IDS,
    IRON,
    ROOT,
    approved_contact_db,
    contact_db,
    contact_repo,
    contact_seed,
    fake_repo,
    fixture_contact_catalog,
)

# ruff: noqa: F811


def query(db, ident=DEEPWOOD):
    return query_runtime(db, {'action': 'mechanism_profile', 'mechanism_id': ident})


def compile_again(profile):
    proof = profile['source_proof']
    proof['descriptor_fingerprint'] = descriptor_fingerprint(profile['descriptor'])
    proof['fact_fingerprints'] = {f['id'] + '@' + str(f['revision']): fact_fingerprint(f) for f in profile['facts']}
    return compile_mechanism_descriptor(profile['descriptor'], source_proof=proof,
                                       facts=profile['facts'], parameters=profile['resolved_parameters'])


def qualification(profile):
    return next(f['claim'] for f in profile['facts'] if f['kind'] == 'execution_qualification')


def test_seed_is_pending_and_old_seed_bytes_preserved():
    seed = contact_seed()
    assert len(seed['descriptors']) == 6 and len(seed['facts']) == 29 and len(seed['heads']) == 35
    for kind, records in (('fact', seed['facts']), ('descriptor', seed['descriptors'])):
        for r in records:
            assert validate_record(r, kind) == r
            assert r['review'] == {'status': 'pending', 'reviewer': None, 'reviewed_at': None}
    for version, expected in [(1, '08a9730882c20ea0fd659412524679b2b437c56fd3817bc2df7ee8f61e31a4a6'),
                              (2, '8b1b250d1c41be2b594669b182ecce78a2ddd7741ff434473eb378fcbc7ba40d'),
                              (3, '869e4de7c71647b23e86fb96e68e551b7e1fd3ea9526007d289396a20b056113')]:
        assert hashlib.sha256((ROOT / f'src/genshin_db/resources/mechanisms/reviewed-mechanisms-v{version}.json').read_bytes()).hexdigest() == expected


def test_pending_import_does_not_approve_or_materialize(contact_db):
    seed = fixture_contact_catalog(contact_db)
    before = contact_db.read_bytes()
    assert not import_bundle(contact_db, seed)['applied']
    assert contact_db.read_bytes() == before
    assert import_bundle(contact_db, seed, dry_run=False)['applied']
    for ident in IDS:
        out = query(contact_db, ident)
        assert out['status'] == 'pending' and out['review_status'] == 'pending'
        assert out['compiler_result'] is None and out['source_proof'] is None
        assert out.get('authorization_fingerprint') is None


@pytest.mark.parametrize('ident', IDS)
def test_six_real_records_materialize_numeric_parameters(approved_contact_db, ident):
    out = query(approved_contact_db, ident)
    assert out['status'] == 'ready', out
    compiled = out['compiler_result']
    assert compiled['compiler'] == 'typed-mechanism-d20-v3' and compiled['compiler_version'] == 3
    assert compiled['execution_status'] == 'not_evaluated' and out['execution_support'] == 'not_evaluated'
    rule = compiled['rule_templates'][0]['rule']
    assert rule['trigger']['event'] == 'hit' and rule['trigger']['phase'] == 'post'
    assert compiled['execution_ports'] == [{'rule_key': 'contact', 'target': 'owner', 'role': 'provider'},
                                          {'rule_key': 'contact', 'target': 'trigger.event_ids', 'role': 'qualifying_hits'}]
    if ident == DEEPWOOD:
        assert rule['timing'] == {'delay': 0, 'duration': 8, 'cooldown': 0, 'cooldown_anchor': 'success'}
        assert rule['effects'][0]['value'] == .30000001192092896
        assert rule['effects'][0]['domain'] == 'enemy' and rule['trigger']['owner_presence'] == 'any'
    else:
        rank = int(ident[-1])
        assert rule['timing'] == {'delay': 0, 'duration': 6, 'cooldown': 1, 'cooldown_anchor': 'success'}
        assert rule['stacking'] == {'max': 2, 'add': 1, 'policy': 'add_and_refresh'}
        expected = [.05999999865889549, .07500000298023224, .09000000357627869, .10499999672174454, .11999999731779099][rank-1]
        assert rule['effects'][0]['value'] == expected and rule['trigger']['owner_presence'] == 'frontline'
        assert qualification(out)['scenario_requirements']['no_provider_transformative_damage'] is True
        assert 'no_provider_transformative_damage' in compiled['required_execution_checks']
        assert out['source_binding']['selection'] == {'refinement': rank}


@pytest.mark.parametrize('change', ['callback', 'mount', 'handler', 'predicate', 'apply', 'affix', 'bool_level', 'subject'])
def test_corrupt_contact_bridge_fails_closed(approved_contact_db, change):
    out = query(approved_contact_db)
    q = qualification(out)
    b = q['parent']['callback_bridge']
    if change == 'callback':
        b['callback_pointer'] = b['handler_pointer'] + '/missing'
    elif change == 'mount':
        b['mount_pointer'] = b['apply_pointers'][0]
    elif change == 'handler':
        b['handler_pointer'] = '/missing'
    elif change == 'predicate':
        b['predicate_pointers'][0] += '/missing'
    elif change == 'apply':
        b['apply_pointers'][0] += '/missing'
    elif change == 'affix':
        q['provider']['affix_id'] += 1
    elif change == 'bool_level':
        q['provider']['source_level'] = True
    else:
        out['source_proof']['subject']['target_id'] += 1
    try:
        result = compile_again(out)
    except ValueError:
        return  # Strict typed fact fingerprint itself rejects the invalid shape.
    assert result['status'] == 'pending' and result['rule_templates'] == []


@pytest.mark.parametrize('subject', ['character', 'artifact_set'])
def test_refinement_cannot_pollute_other_subject_selection(subject):
    binding = copy.deepcopy(contact_seed()['descriptors'][1]['payload']['source_binding'])
    binding['subject']['target_type'] = subject
    with pytest.raises(ValueError, match='精炼'):
        validate_source_binding(binding)


def test_wrong_refinement_fresh_db_selection_is_atomic_pending(contact_db):
    seed = fixture_contact_catalog(contact_db, approve=True)
    seed['descriptors'][1]['payload']['source_binding']['selection']['refinement'] = 2
    assert import_bundle(contact_db, seed, dry_run=False)['applied']
    out = query(contact_db, IRON[0])
    assert out['status'] == 'pending'
    assert out['compiler_result']['rule_templates'] == []
    assert any(i['code'] == 'mechanism_contact_source_selection_mismatch' for i in out['issues'])


def test_callback_filter_must_not_be_replaced_by_dispatch_subset(approved_contact_db):
    out = query(approved_contact_db, IRON[0])
    item = out['descriptor']['rule_templates'][0]
    item['rule']['trigger']['elements'] = ['cryo', 'anemo']
    next(f for f in out['facts'] if f['kind'] == 'rule_template')['claim'] = copy.deepcopy(item)
    assert compile_again(out)['status'] == 'pending'


def test_revoked_qualification_withdraws_every_template(approved_contact_db):
    ident = IRON[0] + '.qualification'
    with closing(sqlite3.connect(approved_contact_db)) as conn:
        saved = read_record(conn, 'release', 'fact', ident)
    review_record(approved_contact_db, version_line='release', record_kind='fact', record_id=ident, revision=1,
                  status='revoked', reviewer='isolated-test', reviewed_at='2026-10-03', expected_review_fingerprint=saved['review_fingerprint'])
    out = query(approved_contact_db, IRON[0])
    assert out['status'] == 'pending' and out['compiler_result'] is None
    assert out['source_proof'] is None and out.get('authorization_fingerprint') is None
    assert any(item['record_id'] == ident and item['review_status'] == 'revoked' for item in out['issues'])


def test_v1_v2_do_not_accept_new_scene_or_hit_qualification():
    from tests.test_mechanism_cast_qualification import cast_case
    from tests.test_mechanism_cast_qualification import qualification as cast_q
    from tests.test_mechanism_qualification import claim
    for q in [claim(), cast_q(cast_case()[1])]:
        q['scenario_requirements']['no_provider_transformative_damage'] = True
        with pytest.raises(ValueError):
            validate_execution_qualification(q)

@pytest.mark.parametrize('mutation', ['subset_predicate', 'other_apply_array', 'quota', 'stat_expression', 'missing_qualification', 'raw_hash', 'weapon_group', 'weapon_refinement', 'extra_selection'])
def test_closed_contact_execution_and_raw_identity(approved_contact_db, mutation):
    out = query(approved_contact_db, IRON[0])
    q = qualification(out)
    item = out['descriptor']['rule_templates'][0]
    if mutation == 'subset_predicate':
        q['parent']['callback_bridge']['predicate_pointers'].pop()
    elif mutation == 'other_apply_array':
        q['parent']['callback_bridge']['apply_pointers'] = q['parent']['callback_bridge']['predicate_pointers'][:]
    elif mutation == 'quota':
        from tests.test_mechanism_descriptor_compiler import case
        item['rule']['quota'] = copy.deepcopy(case()[0]['rule_templates'][0]['rule']['quota'])
    elif mutation == 'stat_expression':
        item['value_bindings'] = [b for b in item['value_bindings'] if b['target'] != 'effects.bonus.value']
        item['rule']['effects'][0]['value'] = {'kind': 'stat', 'subject': 'owner', 'stat': 'atk', 'sample': 'current', 'factor': 1, 'add': 0}
    elif mutation == 'missing_qualification':
        out['facts'] = [f for f in out['facts'] if f['kind'] != 'execution_qualification']
        out['descriptor']['evidence_fact_ids'] = [f['id'] + '@1' for f in out['facts']]
        out['source_proof']['review']['fact_statuses'] = {f['id'] + '@1': 'approved' for f in out['facts']}
    elif mutation == 'raw_hash':
        next(n for n in out['source_proof']['nodes'] if n['id'] == 'ability')['payload']['new_unknown'] = True
    elif mutation == 'weapon_group':
        node = next(n for n in out['source_proof']['nodes'] if n['id'] == 'weapon')
        node['payload']['skillAffix'] = [111407, 111407]
        node['node_sha256'] = fingerprint(node['payload'])
        next(e for e in out['source_proof']['evidence'] if e['id'] == 'weapon')['sha256'] = node['node_sha256']
    elif mutation == 'weapon_refinement':
        q['provider']['refinement'] = 2
    else:
        q['parent']['selection']['variant'] = 'hold'
    for f in out['facts']:
        if f['kind'] == 'rule_template':
            f['claim'] = copy.deepcopy(item)
    try:
        compiled = compile_again(out)
    except ValueError:
        return
    assert compiled['status'] == 'pending' and compiled['rule_templates'] == []


def test_full_source_change_invalidates_previously_approved_binding(approved_contact_db, contact_repo):
    import json

    from tests.test_runtime_object_index import build, write
    path = 'BinOutput/Talent/EquipTalents/ConfigAffix_Sword.json'
    data = json.loads((contact_repo / path).read_text('utf8'))
    name = 'Weapon_Sword_AttackGainFullDamgeUpBuff'
    # A second unknown writer is retained in the full source; old approval is stale.
    data[name].append({'$type': 'UnknownWriter', 'target': name, 'value': 0})
    write(contact_repo, path, data)
    build(approved_contact_db, contact_repo, upstream_commit='342084292ba4c3c6ec2bc34c045de4de72ae8d53')
    out = query(approved_contact_db, IRON[0])
    assert out['status'] == 'pending' and out['source_status'] == 'stale'
    assert out['compiler_result']['rule_templates'] == []


def test_public_projection_and_seed_copies_are_isolated(approved_contact_db):
    one = query(approved_contact_db, IRON[0])
    expected = copy.deepcopy(one)
    one['compiler_result']['rule_templates'][0]['rule']['effects'][0]['value'] = 999
    one['source_proof']['nodes'].clear()
    assert query(approved_contact_db, IRON[0]) == expected
