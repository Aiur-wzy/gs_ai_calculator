"""Actual Candidate→tool Host capacity failure is inspectable and not unknown."""
import copy
import hashlib
import json

import pytest

from genshin_app.agent_chat import candidate, tools
from genshin_app.agent_chat.native_app_tool_bridge import (
    NativeAppToolBridgeError,
    build_native_app_tool_settlement,
    validate_native_app_tool_settlement,
)
from genshin_app.agent_chat.native_app_tool_effects import allows_settled_failure_commit
from genshin_app.equipment.execution_verification import ExecutionVerificationSession
from genshin_app.equipment.files import fingerprint
from tests.test_calculation_scope_diagnostics import completed  # noqa: F401
from tests.test_native_app_tool_host import call, host
from tests.test_queries import db_path  # noqa: F401

# ruff: noqa: F811 - imported pytest fixtures are injected by name.


def low_capacity_call(session, monkeypatch):
    original = candidate.run_plan_calc
    def actual_failure(*args, **kwargs):
        with ExecutionVerificationSession(budget={'policy': 'execution-verification-budget-v2',
                'limits': {'requests': 0}}) as owner:
            return original(*args, **kwargs, _session=owner)
    monkeypatch.setattr(candidate, 'run_plan_calc', actual_failure)


def test_actual_native_capacity_failure_settles_exact_derived_effect_and_remains_queryable(completed, tmp_path, monkeypatch):
    low_capacity_call(completed, monkeypatch)
    before = copy.deepcopy(completed.candidate)
    revision = completed.revision
    current = host(tmp_path / 'host', completed, tools.call_tool)
    result = call(current, tool='gs_calculate', raw=json.dumps({'expected_revision': revision}))
    assert result['terminal_state'] == 'settled_failure', result
    assert result['terminal_reason'] == 'calculation_verification_incomplete'
    assert completed.candidate == before and completed.revision == revision
    assert completed.calculated is None and completed.summary()['ready'] is False
    assert set(result['state_changes']) <= {'derived_hash', 'durable_checkpoint_hash', 'stage_state_hash'}
    response = result['result']
    assert response['category'] == 'engine_capability_missing'
    assert response['recovery']['action'] == 'report_capacity_boundary'
    artifact = completed._diagnostic_cache['value']
    assert artifact['sealed'] is artifact['accepted_result'] is False
    assert not {'result', 'calculated', 'summary', 'result_receipt'} & artifact.keys()
    assert allows_settled_failure_commit('gs_calculate', 'derived_calculation', response,
        diagnostic_cache=completed._diagnostic_cache, require_cached_verification=True,
        candidate_state={'revision': completed.revision, 'fingerprint': fingerprint(completed.candidate)})
    payload = build_native_app_tool_settlement(result, tool_name='gs_calculate', current_snapshot=current.snapshot)
    assert payload['terminal_state'] == 'settled_failure'
    assert json.loads(payload['result_json'])['reason'] == 'calculation_verification_incomplete'
    altered = copy.deepcopy(payload)
    body = json.loads(altered['result_json'])
    body['details']['diagnostic']['sealed'] = True
    altered['result_json'] = json.dumps(body, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':'))
    altered['result_hash'] = hashlib.sha256(altered['result_json'].encode('utf8')).hexdigest()
    with pytest.raises(NativeAppToolBridgeError, match='verification_evidence'):
        validate_native_app_tool_settlement(altered, current_snapshot=current.snapshot)
    replay = call(current, tool='gs_calculate', raw=json.dumps({'expected_revision': revision}))
    assert replay['status'] == 'replay' and replay['result'] == response
    diagnosed = call(current, call_id='diagnose-1', tool='gs_diagnose_result',
        raw=json.dumps({'expected_revision': revision, 'request': {'view': 'issues'}}))
    assert diagnosed['terminal_state'] == 'settled_success', diagnosed
    assert diagnosed['result']['data']['validation_scope'] == 'unverified_saved_verification_failure'


@pytest.mark.parametrize('mutation', ['reason_only', 'missing_cache', 'changed_attempt', 'promoted', 'wrong_effect', 'stale_candidate', 'candidate_edit'])
def test_capacity_failure_reason_alone_never_grants_effect_authority(completed, tmp_path, monkeypatch, mutation):
    low_capacity_call(completed, monkeypatch)
    response = completed.call_tool('gs_calculate', {'expected_revision': completed.revision})
    cache = copy.deepcopy(completed._diagnostic_cache)
    if mutation == 'reason_only':
        response.pop('details')
    elif mutation == 'missing_cache':
        cache = None
    elif mutation == 'changed_attempt':
        cache['attempt_id'] = 'f' * 32
    elif mutation == 'promoted':
        response['details']['diagnostic']['sealed'] = True
    if mutation != 'candidate_edit':
        assert not allows_settled_failure_commit('gs_calculate',
            'candidate' if mutation == 'wrong_effect' else 'derived_calculation', response,
            diagnostic_cache=cache, require_cached_verification=True,
            candidate_state={'revision': completed.revision,
                'fingerprint': 'f' * 64 if mutation == 'stale_candidate' else fingerprint(completed.candidate)})
    else:
        def forbidden(current, _name, _args):
            current.candidate['revision'] += 1
            return response
        current = host(tmp_path / 'violating', completed, forbidden)
        result = call(current, tool='gs_calculate', raw=json.dumps({'expected_revision': completed.revision}))
        assert result['terminal_state'] == 'unknown_after_interruption'
        assert result['terminal_reason'] == 'effect_mask_violation'


def test_fresh_host_cannot_commit_old_wire_evidence_without_live_attempt_cache(completed, tmp_path, monkeypatch):
    low_capacity_call(completed, monkeypatch)
    response = completed.call_tool('gs_calculate', {'expected_revision': completed.revision})
    completed._diagnostic_cache = None
    def no_current_attempt(session, _name, _args):
        session.report = {'missing_inputs': [{'code': 'new-unverified-change'}]}
        return response
    current = host(tmp_path / 'no-cache', completed, no_current_attempt)
    result = call(current, tool='gs_calculate', raw=json.dumps({'expected_revision': completed.revision}))
    assert result['terminal_state'] == 'unknown_after_interruption'
    assert result['terminal_reason'] == 'failed_tool_changed_state'
