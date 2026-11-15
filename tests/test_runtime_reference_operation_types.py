"""Unknown upstream type values remain data instead of crashing extraction."""
import copy
import json

import pytest

from genshin_db.runtime_catalog import extract_definitions, extract_references


@pytest.mark.parametrize('operation', [[], {}, ['AddAbility'], {'type': 'ApplyModifier'}, None, False, 7])
@pytest.mark.parametrize('field', ['abilityName', 'modifierName'])
def test_non_string_operation_is_preserved_with_uninterpreted_reference(operation, field):
    payload = {'Entry': [{'$type': operation, field: 'Target'}]}
    definition = extract_definitions('unknown.json', 'talent', payload, 'fixture-snapshot')[0]
    original = copy.deepcopy(definition)
    refs = extract_references(definition)
    assert definition == original
    assert len(refs) == 1
    assert refs[0]['relation_kind'] == refs[0]['resolution_status'] == 'unsupported_reference_form'
    assert refs[0]['field_name'] == field
    assert json.loads(refs[0]['target_key_json']) == 'Target'


@pytest.mark.parametrize('operation', [[], {}, None, False, 7])
def test_structural_ability_entry_does_not_require_an_operation_type(operation):
    source = {'abilities': [{'$type': operation, 'abilityName': 'Target'}]}
    definition = extract_definitions('BinOutput/Avatar/a.json', 'avatar', source, 'fixture-snapshot')[0]
    refs = extract_references(definition)
    assert len(refs) == 1
    assert refs[0]['relation_kind'] == 'ability_operation'


@pytest.mark.parametrize('operation,field,relation', [
    ('AddAbility','abilityName','ability_operation'),
    ('ModifyAbility','abilityName','ability_operation'),
    ('UnlockTalentParam','abilityName','ability_operation'),
    ('ApplyModifier','modifierName','modifier_operation'),
    ('ExampleMixin','modifierName','modifier_operation'),
    ('Unknown','abilityName','unsupported_reference_form'),
])
def test_string_operations_keep_existing_reference_semantics(operation, field, relation):
    definition = extract_definitions('known.json', 'talent', {'Entry':[{'$type':operation,field:'Target'}]}, 's')[0]
    refs = extract_references(definition)
    assert len(refs) == 1 and refs[0]['relation_kind'] == relation
