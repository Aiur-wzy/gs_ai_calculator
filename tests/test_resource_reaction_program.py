"""First RQ1 slice: closed declarations, no execution/source authority."""
import copy

import pytest

from genshin_engine.resource_reaction_program import (
    Program,
    ReactionResourceError,
    validate_bindings,
)
from tests.test_reaction_operation_capabilities import setup


def model():
    value = setup()[2]
    value['resource_reaction_contract'] = {'schema_version': 1, 'operation_ids': ['refill']}
    value['shared_pools']['operations'][0]['trigger']['reaction_types'] = ['swirl']
    return value


def test_valid_program_is_only_declared_consistency_and_detached():
    original = model()
    before = copy.deepcopy(original)
    program = Program(original, ['a', 'b'])
    report = program.report()
    assert original == before
    assert report['source_authority'] == 'not_evaluated'
    assert report['execution'] == 'not_connected'
    assert report['operations'][0]['keys'] == ['team']
    assert report['operations'][0]['selector']['owner_presence'] == 'any'
    report['operations'][0]['keys'].append('fake')
    assert program.report()['operations'][0]['keys'] == ['team']
    original['shared_pools']['operations'][0]['amount'] = 99
    assert program.report()['operations'][0]['operation']['amount'] == 3


def test_program_preserves_explicit_unbounded_pool_semantics():
    value = model()
    value['shared_pools']['pools'][0]['capacity'] = 'unbounded'
    report = Program(value, ['a', 'b']).report()
    assert report['operations'][0]['pool']['capacity'] == 'unbounded'


@pytest.mark.parametrize('change', ['bool_version', 'extra_authority', 'empty', 'duplicate', 'dangling', 'wrong_event',
    'pre', 'condition', 'answer_ids', 'missing_types', 'freeze', 'bool_amount', 'set', 'pending', 'missing_model',
    'old_v2', 'application_only'])
def test_closed_scope_rejects_unreviewed_branches(change):
    value = model()
    contract = value['resource_reaction_contract']
    op = value['shared_pools']['operations'][0]
    if change == 'bool_version': contract['schema_version'] = True
    elif change == 'extra_authority': contract['approved'] = True
    elif change == 'empty': contract['operation_ids'] = []
    elif change == 'duplicate': contract['operation_ids'] *= 2
    elif change == 'dangling': contract['operation_ids'] = ['unknown']
    elif change == 'wrong_event': op['trigger']['event'] = 'hit'
    elif change == 'pre': op['trigger']['phase'] = 'pre'
    elif change == 'condition': op['trigger']['condition'] = 'external-answer'
    elif change == 'answer_ids': op['trigger']['event_ids'] = ['h0']
    elif change == 'missing_types': op['trigger'].pop('reaction_types')
    elif change == 'freeze': op['trigger']['reaction_types'] = ['freeze']
    elif change == 'bool_amount': op['amount'] = True
    elif change == 'set': op['operation'] = 'set'
    elif change == 'pending': op['status'] = 'pending'
    elif change == 'missing_model': value.pop('elemental_model')
    elif change == 'old_v2': value['elemental_model']['ruleset'] = 'gauge-swirl-v2'
    elif change == 'application_only': value['application_events'] = [{'id': 'extra'}]
    with pytest.raises(ReactionResourceError):
        Program(value, ['a', 'b'])


@pytest.mark.parametrize('location', ['amount', 'targets', 'owner', 'cooldown', 'unrelated_rule'])
def test_final_graph_fingerprint_detects_same_identity_changed_material(location):
    value = model()
    program = Program(value, ['a', 'b'])
    if location == 'amount': value['shared_pools']['operations'][0]['amount'] += 1
    elif location == 'targets': value['shared_pools']['operations'][0]['targets'] = {'mode': 'characters', 'characters': ['a']}
    elif location == 'owner': value['shared_pools']['pools'][0]['owner'] = 'b'
    elif location == 'cooldown': value['shared_pools']['operations'][0]['cooldown']['seconds'] = 1
    else: value['rules'][0]['effects'][0]['value'] += 1
    with pytest.raises(ReactionResourceError, match='实际执行图'):
        program.verify_model(value, ['a', 'b'])


def test_character_order_is_a_set_but_duplicate_character_is_invalid():
    value = model()
    assert Program(value, ['a', 'b']).fingerprint == Program(value, ['b', 'a']).fingerprint
    with pytest.raises(ReactionResourceError):
        Program(value, ['a', 'a'])


@pytest.mark.parametrize('bad', ['cycle', 'depth', 'nan', 'oversized_string', 'non_string_key'])
def test_input_is_bounded_before_copy_and_hash(bad):
    value = model()
    if bad == 'cycle': value['recursive'] = value
    elif bad == 'depth':
        nested = value
        for _ in range(70):
            nested['nested'] = {}
            nested = nested['nested']
    elif bad == 'nan': value['shared_pools']['operations'][0]['amount'] = float('nan')
    elif bad == 'oversized_string': value['rules'][0]['source'] = '中' * 70000
    else: value[False] = 'bad'
    with pytest.raises(ReactionResourceError):
        Program(value, ['a', 'b'])


def test_absent_contract_is_zero_change_in_static_adapter():
    value = setup()[2]
    before = copy.deepcopy(value)
    assert validate_bindings(value, ['a', 'b']) == []
    assert value == before


def test_live_operation_check_does_not_need_or_read_balances():
    value = model()
    program = Program(value, ['a', 'b'])
    op = value['shared_pools']['operations'][0]
    pool = value['shared_pools']['pools'][0]
    assert program.verify_operation('refill', op, pool) == ['team']
    op['amount'] = 7
    with pytest.raises(ReactionResourceError):
        program.verify_operation('refill', op, pool)


@pytest.mark.parametrize('shape', [None, [], 'v3', 3])
def test_malformed_elemental_container_is_a_controlled_error(shape):
    value = model()
    value['elemental_model'] = shape
    with pytest.raises(ReactionResourceError):
        Program(value, ['a', 'b'])


@pytest.mark.parametrize('where', ['operation', 'pool'])
def test_live_bound_numeric_type_cannot_change_under_python_equality(where):
    value = model()
    program = Program(value, ['a', 'b'])
    op, pool = value['shared_pools']['operations'][0], value['shared_pools']['pools'][0]
    if where == 'operation': op['amount'] = 3.0
    else: pool['capacity'] = 20.0
    with pytest.raises(ReactionResourceError):
        program.verify_operation('refill', op, pool)


def test_program_limit_is_pending_and_does_not_degrade_to_schema(monkeypatch):
    import genshin_engine.resource_reaction_program as candidate
    monkeypatch.setitem(candidate.LIMITS, 'program_bytes', 50)
    with pytest.raises(ReactionResourceError) as captured:
        Program(model(), ['a', 'b'])
    assert captured.value.issue['code'] == 'resource_reaction_limit'
    assert captured.value.issue['severity'] == 'pending'


def test_existing_legal_c2_with_constant_pool_is_not_blanket_rejected():
    from tests.test_attribute_callback_runtime import callback_case
    from tests.test_reaction_operation_capabilities import with_pool
    value = with_pool(callback_case())[2]
    value['resource_reaction_contract'] = {'schema_version': 1, 'operation_ids': ['refill']}
    value['shared_pools']['operations'][0]['trigger']['reaction_types'] = ['swirl']
    assert Program(value, ['a', 'b']).report()['execution'] == 'not_connected'


def test_source_notes_do_not_become_game_authority():
    value = model()
    value['shared_pools']['operations'][0].update(status='confirmed', source='Claimed game confirmation')
    value['shared_pools']['pools'][0]['status'] = 'confirmed'
    assert Program(value, ['a', 'b']).report()['source_authority'] == 'not_evaluated'
