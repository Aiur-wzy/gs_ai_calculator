"""Independent S1 shape consistency; no fresh DB or source-authority approval."""
import copy
import json

import pytest

from genshin_app.equipment.static_inclusion import compile_static_inclusion
from tests.test_static_inclusion import MATERIALS, material, parent, put_parent, rehash


@pytest.mark.parametrize('field', ['count', 'set_id', 'required_count'])
@pytest.mark.parametrize('value', [True, 2.0])
def test_selection_identity_never_accepts_boolean_or_float(field, value):
    m = material()
    m['selection'][field] = value
    assert compile_static_inclusion(m)['status'] == 'pending'


def test_returned_certificate_and_input_are_deeply_isolated():
    m = material()
    before = copy.deepcopy(m)
    out = compile_static_inclusion(m)
    assert out['status'] == 'shape_ready'
    pristine = copy.deepcopy(out)
    out['declaration']['properties'][0]['value'] = 999
    out['declaration']['provenance']['parent']['source_sha256'] = '1'*64
    assert m == before
    assert compile_static_inclusion(m) == pristine


def test_same_material_different_provider_has_distinct_consistency_identity_only():
    m = material()
    a = compile_static_inclusion(m)
    m['selection']['provider'] = '冰之角色'
    b = compile_static_inclusion(m)
    assert a['certificate_fingerprint'] != b['certificate_fingerprint']
    assert a['material_fingerprint'] != b['material_fingerprint']
    assert b['status'] == 'shape_ready' and b['source_authority'] == 'not_granted'


def test_ratio_remains_raw_without_base_or_numeric_application():
    m = next(copy.deepcopy(row) for row in MATERIALS
             if row['source']['affix']['record']['addProps'][0].get('propType') == 'FIGHT_PROP_ATTACK_PERCENT')
    out = compile_static_inclusion(m)
    prop = out['declaration']['properties'][0]
    assert prop['unit'] == 'ratio' and prop['application'] == 'base_fraction_already_in_panel'
    assert prop['value'] == m['source']['affix']['record']['addProps'][0]['value']
    assert 'base_atk' not in out['declaration'] and 'flat' not in prop
    assert out['declaration']['numeric_application'] is False


def test_duplicate_parent_json_key_is_rejected_even_when_last_value_matches():
    m = material()
    raw = parent(m)
    encoded = json.dumps(raw, ensure_ascii=False)
    m['parent']['payload_json'] = '{"setId":999999,'+encoded[1:]
    rehash(m)
    out = compile_static_inclusion(m)
    assert out['status'] == 'pending' and out['issues'][0]['code'] == 'duplicate_json_key'


def test_raw_unknown_execution_field_cannot_be_hidden_in_rehashed_material():
    m = material()
    raw = parent(m)
    raw['attackEvent'] = {'enabled': False}
    put_parent(m, raw)
    out = compile_static_inclusion(m)
    assert out['status'] == 'pending' and out['issues'][0]['code'] == 'raw_parent_shape'


def test_affix_slot_identity_preserves_empty_placeholders():
    m = material()
    raw = m['source']['affix']['record']
    moved = [copy.deepcopy(raw['addProps'][1]), copy.deepcopy(raw['addProps'][2]), copy.deepcopy(raw['addProps'][0])]
    raw['addProps'] = moved
    m['normalized']['add_props'] = copy.deepcopy(moved)
    rehash(m)
    out = compile_static_inclusion(m)
    assert out['status'] == 'shape_ready'
    assert out['declaration']['properties'][0]['source_index'] == 2
    assert out['source_authority'] == 'not_granted'


def test_certificate_never_claims_real_origin_after_complete_local_rehash():
    m = material()
    raw = m['source']['affix']['record']
    raw['addProps'][0]['value'] *= 2
    m['normalized']['add_props'] = copy.deepcopy(raw['addProps'])
    rehash(m)
    out = compile_static_inclusion(m)
    assert out['status'] == 'shape_ready'
    assert out['verification_scope'] == 'materialized_consistency_no_source_authority'
    assert out['source_authority'] == 'not_granted' and out['requires_fresh_db_admission'] is True
    assert 'execution_fingerprint' not in out and 'source_approval' not in out


def test_circular_material_is_bounded_pending_without_raw_exception():
    m = material()
    m['unused'] = m
    out = compile_static_inclusion(m)
    assert out['status'] == 'pending' and len(out['issues']) == 1
