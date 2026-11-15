"""Independent malformed-$type boundaries; read-only production extraction."""
import copy
import json

import pytest

from genshin_db.runtime_catalog import extract_definitions, extract_references


@pytest.mark.parametrize('indexer', ['2', '3', '4', '5', '6'])
@pytest.mark.parametrize('operation', [[], {}])
def test_raw_pointer_and_unknown_payload_survive_every_supported_indexer(indexer, operation):
    payload = {'条/目~': [{'$type': operation, 'abilityName': '能力', 'modifierName': '修饰'}]}
    definition = extract_definitions('unknown.json', 'talent', payload, 'isolated')[0]
    original = copy.deepcopy(definition)
    refs = extract_references(definition, indexer_version=indexer)
    assert definition == original
    assert [(r['json_pointer'], r['relation_kind'], json.loads(r['target_key_json'])) for r in refs] == [
        ('/条~1目~0/0/abilityName', 'unsupported_reference_form', '能力'),
        ('/条~1目~0/0/modifierName', 'unsupported_reference_form', '修饰')]
    assert [r['source_order'] for r in refs] == [0, 1]
    assert all(r['extraction_rule_id'] == indexer for r in refs)


@pytest.mark.parametrize('operation', [[], {}])
def test_skill_excel_structural_reference_still_does_not_need_string_type(operation):
    payload = [{'id': 1, '$type': operation, 'abilityName': 'Target'}]
    definition = extract_definitions('ExcelBinOutput/AvatarSkillExcelConfigData.json', 'excel', payload, 'isolated')[0]
    refs = extract_references(definition)
    assert len(refs) == 1
    assert refs[0]['json_pointer'] == '/0/abilityName'
    assert refs[0]['relation_kind'] == 'ability_operation'


def test_unknown_outer_type_does_not_drop_a_valid_nested_operation():
    definition = extract_definitions('unknown.json', 'talent', {'Entry': [{
        '$type': [], 'abilityName': 'Outer',
        'payload': {'$type': 'ApplyModifier', 'modifierName': 'Inner'}}]}, 'isolated')[0]
    refs = extract_references(definition)
    assert [(r['json_pointer'], r['relation_kind']) for r in refs] == [
        ('/Entry/0/abilityName', 'unsupported_reference_form'),
        ('/Entry/0/payload/modifierName', 'modifier_operation')]


def test_unknown_modifier_steps_do_not_acquire_supported_operation_semantics():
    definition = extract_definitions('unknown.json', 'talent', {'Entry': [{
        '$type': ['AttachModifierToSelfGlobalValueMixin'],
        'modifierName': 'Outer', 'modifierNameSteps': ['A', 'B']}]}, 'isolated')[0]
    refs = extract_references(definition)
    assert len(refs) == 1
    assert refs[0]['relation_kind'] == 'unsupported_reference_form'
    assert refs[0]['field_name'] == 'modifierName'


def test_unknown_type_preserves_empty_and_null_reference_status():
    definition = extract_definitions('unknown.json', 'talent', {'Entry': [{
        '$type': {}, 'abilityName': None, 'modifierName': ''}]}, 'isolated')[0]
    refs = extract_references(definition)
    assert [r['presence_status'] for r in refs] == ['null', 'empty']
    assert [r['resolution_status'] for r in refs] == ['source_empty', 'source_empty']
    assert all(r['relation_kind'] == 'unsupported_reference_form' for r in refs)
