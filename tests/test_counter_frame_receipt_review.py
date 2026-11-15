"""Independent saved-input consistency checks; no database authority claims."""
from __future__ import annotations

import copy

import pytest

from tests.test_counter_frame_receipt_helper import calculated, rehash, validate
from tests.test_counter_self_frame_app import self_plan


@pytest.mark.parametrize('field', ['input_fingerprint', 'compiled_rotation_model'])
def test_full_report_must_describe_the_same_actual_execution(tmp_path, field):
    value = calculated(tmp_path)
    if field == 'input_fingerprint':
        value['report'][field] = '0' * 64
    else:
        model = copy.deepcopy(value['result']['rotation_check']['resolved_rotation_model'])
        model['actions'][0]['start'] += .01
        value['report'][field] = model
    with pytest.raises(ValueError):
        validate(value, full=True)


@pytest.mark.parametrize('field', ['rotation_model', 'matches'])
def test_rehashed_execution_components_still_require_real_replay(tmp_path, field):
    value = calculated(tmp_path)
    execution = value['result']['counter_frame_execution']
    if field == 'rotation_model':
        execution[field]['actions'][0]['start'] += .01
    else:
        execution[field].append({'forged': True})
    rehash(value)
    with pytest.raises(ValueError):
        validate(value)


@pytest.mark.parametrize('field', ['historical_capture', 'trace'])
def test_rehashed_malformed_history_is_controlled_rejection(tmp_path, field):
    value = calculated(tmp_path)
    value['result']['resource_counter_views'][field] = [] if field == 'historical_capture' else {}
    rehash(value)
    with pytest.raises(ValueError):
        validate(value)


def test_closed_schema2_source_report_is_bound_even_without_reading(tmp_path):
    plan = self_plan()
    plan['effect_bindings'][0]['parts'][1].update(state='excluded', reason='independent close')
    value = calculated(tmp_path, plan)
    assert 'resource_counter_views' not in value['result']
    value['report']['resource_counter_view_adoptions'][0]['reason'] = 'forged explanation'
    with pytest.raises(ValueError):
        validate(value, full=True)


def test_repeated_validation_is_read_only_and_does_not_reauthorize_source(tmp_path):
    value = calculated(tmp_path)
    before = copy.deepcopy(value)
    validate(value, full=True)
    validate(value)
    assert value == before
    assert value['result_receipt']['counter_self_frames']['verification_scope'] == (
        'saved_input_replay_not_current_database_approval')
