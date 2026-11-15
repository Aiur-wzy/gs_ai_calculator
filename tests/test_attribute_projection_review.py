"""Independent closed projection provenance and immutable-frame checks."""
import copy

import pytest

from genshin_engine.attribute_graph import AttributeGraphLedger
from genshin_engine.attribute_ledger import AttributeLedgerError
from tests.test_attribute_projection import project, projection_fixture


@pytest.mark.parametrize('field,value', [('layer_id', 'other'), ('nonextra_inclusion', 'included'),
                                         ('visibility', 'damage_context'), ('constraints', ['quota'])])
def test_projection_may_not_reclassify_or_reauthor_a_source_layer(field, value):
    data = projection_fixture()
    data['contributions'][1][field] = value
    result = project(AttributeGraphLedger(data))
    assert result['status'] == 'invalid' and 'value' not in result
    assert result['issues'][0]['code'] == 'attribute_projection_source'


def test_provider_qualification_is_current_while_event_input_is_an_older_observation():
    data = projection_fixture()
    original, projected = data['contributions']
    original['output_domain'] = projected['output_domain'] = 'provider'
    data['frames'][0]['contribution_ids'].clear()
    data['frames'][2]['contribution_ids'].append(original['id'])
    projected['projection_of']['frame_id'] = 'current'
    result = project(AttributeGraphLedger(data))
    assert result['status'] == 'known' and result['value'] == 40
    assert result['contributions'][0]['projection_of']['frame_id'] == 'current'


def test_missing_observation_adoption_retries_without_rewriting_earlier_receipt():
    data = projection_fixture()
    observed = data['frames'].pop(1)
    service = AttributeGraphLedger(data)
    earlier = project(service)
    saved = copy.deepcopy(earlier)
    assert earlier['status'] == 'pending' and 'value' not in earlier
    service.adopt_frame(observed)
    assert project(service)['value'] == 40
    assert earlier == saved


@pytest.mark.parametrize('field,value', [('origin', 1), ('event_id', 'wrong'), ('actual_event_run_id', '0:wrong')])
def test_observation_metadata_cannot_disagree_with_frame_identity(field, value):
    data = projection_fixture()
    data['frames'][1]['observation'][field] = value
    with pytest.raises(AttributeLedgerError) as exc:
        AttributeGraphLedger(data)
    assert exc.value.issue['code'] == 'attribute_observation_identity'


def test_projection_cannot_use_future_current_qualification_even_when_only_self_is_read():
    data = projection_fixture()
    data['frames'][2].update(t_us=4, state_epoch=4)
    result = project(AttributeGraphLedger(data))
    assert result['status'] == 'pending' and 'value' not in result
    assert result['issues'][0]['code'] == 'attribute_forward_frame'


def test_valid_context_still_keeps_active_damage_constraints_pending():
    data = projection_fixture()
    for row in data['contributions']:
        row['visibility'] = 'damage_context'
        row['constraints'] = ['resource_gate']
    result = project(AttributeGraphLedger(data))
    assert result['status'] == 'pending' and 'value' not in result
    assert result['issues'][0]['code'] == 'attribute_context_unsupported'


def test_caller_projection_cannot_alias_another_projection_as_self_qualification():
    data = projection_fixture()
    other = copy.deepcopy(data['frames'][-1])
    other.update(id='later_projection', run_id='0:later_projection', event_id='later_projection',
                 t_us=4, state_epoch=4)
    other['context'].update(actual_event_run_id='0:later_projection', self_frame_id='damage')
    data['frames'].append(other)
    result = AttributeGraphLedger(data).read_stat('a', 'em', 'total', 'later_projection', purpose='damage_projection')
    assert result['status'] == 'invalid' and 'value' not in result
    assert result['issues'][0]['code'] == 'attribute_projection_source'
