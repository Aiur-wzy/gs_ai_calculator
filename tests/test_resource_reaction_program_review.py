"""Independent RQ1 pure scope tests, without occurrence or source authority."""

import copy
import json

import pytest

import genshin_engine.resource_reaction_program as candidate
from tests.test_reaction_operation_capabilities import setup


def model():
    value = setup()[2]
    value['resource_reaction_contract'] = {'schema_version': 1, 'operation_ids': ['refill']}
    value['shared_pools']['operations'][0]['trigger']['reaction_types'] = ['swirl']
    return value


def test_report_fingerprint_is_included_in_actual_program_byte_limit(monkeypatch):
    value = model()
    full = candidate.Program(value, ['a', 'b']).report()
    cap = len(candidate._canonical(full)) - 5
    monkeypatch.setitem(candidate.LIMITS, 'program_bytes', cap)
    with pytest.raises(candidate.ReactionResourceError) as failure:
        candidate.Program(value, ['a', 'b'])
    assert failure.value.issue['code'] == 'resource_reaction_limit'


@pytest.mark.parametrize('shape', [None, [], {'x': 1}, 1, True])
def test_live_operation_identity_has_controlled_type_boundary(shape):
    value = model()
    program = candidate.Program(value, ['a', 'b'])
    with pytest.raises(candidate.ReactionResourceError):
        program.verify_operation(shape, value['shared_pools']['operations'][0],
                                 value['shared_pools']['pools'][0])


def test_live_valid_copy_is_not_a_ledger_or_occurrence_authorization():
    value = model()
    program = candidate.Program(value, ['a', 'b'])
    op = copy.deepcopy(value['shared_pools']['operations'][0])
    pool = copy.deepcopy(value['shared_pools']['pools'][0])
    assert program.verify_operation('refill', op, pool) == ['team']
    report = program.report()
    assert report['execution'] == 'not_connected'
    assert report['source_authority'] == 'not_evaluated'
    assert 'occurrences' not in report and 'approved' not in report
    op['source_id'] = 'different-source'
    with pytest.raises(candidate.ReactionResourceError):
        program.verify_operation('refill', op, pool)


@pytest.mark.parametrize('value', ['\ud800', {'bad': '\udfff'}])
def test_unencodable_json_is_controlled_before_canonical_hash(value):
    with pytest.raises(candidate.ReactionResourceError):
        candidate._bounded(value)


def test_fingerprint_preserves_operation_event_order_instead_of_sorting_game_events():
    value = model()
    second = copy.deepcopy(value['shared_pools']['operations'][0])
    second['id'] = 'second'
    second['order'] = 10
    value['shared_pools']['operations'].append(second)
    value['resource_reaction_contract']['operation_ids'].append('second')
    program = candidate.Program(value, ['a', 'b'])
    permutation = copy.deepcopy(value)
    permutation['resource_reaction_contract']['operation_ids'].reverse()
    program.verify_model(permutation, ['b', 'a'])
    permutation['shared_pools']['operations'].reverse()
    with pytest.raises(candidate.ReactionResourceError):
        program.verify_model(permutation, ['a', 'b'])
    assert json.loads(candidate._canonical(value)) == value
