"""Real role headroom, immutable instances and usable unsealed diagnostics."""
import copy
import json

import pytest

from genshin_app.equipment.execution_verification import (
    LIMITS,
    ExecutionVerificationError,
    ExecutionVerificationSession,
    verification_diagnostic,
)
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.verification_budget import (
    POLICY,
    verification_budget_contract,
)
from genshin_app.verification_scope import verification_scope
from genshin_engine import mechanics


def execution():
    return {'template': {}, 'rotation_model': {}, 'mechanics_model': {}, 'matches': []}


def session(**limits):
    return ExecutionVerificationSession(budget={'policy': POLICY, 'limits': limits})


def test_policy_and_request_are_snapshotted_per_owner_not_shared(monkeypatch):
    requested = {'policy': POLICY, 'limits': {'requests': 1}}
    small = ExecutionVerificationSession(budget=requested)
    requested['limits']['requests'] = 0
    normal = ExecutionVerificationSession()
    with pytest.raises(TypeError):
        LIMITS['requests'] = 0
    with pytest.raises(TypeError):
        small.limits['requests'] = 0
    monkeypatch.setattr(mechanics, 'execute_mechanics', lambda **kw: {'ok': True})
    assert small.replay(execution()) == {'ok': True}
    with pytest.raises(ExecutionVerificationError):
        small.replay(execution())
    assert normal.replay(execution()) == {'ok': True}
    assert normal.report()['usage']['requests'] == 1


@pytest.mark.parametrize('budget', [
    {}, {'policy': 'unknown', 'limits': {}}, {'policy': POLICY, 'limits': {'unbounded': 1}},
    {'policy': POLICY, 'limits': {'work': True}}, {'policy': POLICY, 'limits': {'work': -1}},
    {'policy': POLICY, 'limits': {'work': float('inf')}},
    {'policy': POLICY, 'limits': {'work': 10**100}},
    {'policy': POLICY, 'limits': {}, 'owner': 'document_supplied'},
])
def test_budget_is_closed_and_finite_without_document_owner(budget):
    with pytest.raises(ValueError):
        ExecutionVerificationSession(budget=budget)


def test_complete_raw_output_above_old_16MiB_survives_cold_and_warm(monkeypatch):
    output = {'full_audit': [{'id': index, 'source': 'kept', 'ledger': 'x' * 60000} for index in range(300)]}
    expected_bytes = len(json.dumps(output, ensure_ascii=False, separators=(',', ':')).encode('utf8'))
    assert expected_bytes > 16*1024*1024
    calls = []
    def run(**kw):
        calls.append(True)
        return output
    monkeypatch.setattr(mechanics, 'execute_mechanics', run)
    with session() as owner:
        first = owner.replay(execution())
        first['full_audit'][0]['source'] = 'modified returned copy'
        second = owner.replay(execution())
        assert second == output and len(calls) == 1
        usage = owner.report()['usage']
        assert usage['retained_output_bytes'] == expected_bytes
        assert usage['returned_bytes'] == expected_bytes * 2
        assert usage['maximum_output_bytes'] == expected_bytes
        assert usage['peak_retained_output_bytes'] == expected_bytes
    assert owner.report()['usage']['retained_output_bytes'] == 0
    assert owner.report()['usage']['peak_retained_output_bytes'] == expected_bytes


def test_failed_output_has_exact_role_path_and_no_result_cache(monkeypatch):
    monkeypatch.setattr(mechanics, 'execute_mechanics', lambda **kw: {'source': 'untruncated evidence'})
    owner = session(output_bytes=10)
    with pytest.raises(ExecutionVerificationError) as raised:
        owner.replay(execution())
    error = raised.value
    assert error.reason == 'verification_size' and error.path.startswith('/output')
    diagnostic = verification_diagnostic(error)
    issue = diagnostic['issues'][0]
    assert diagnostic['sealed'] is diagnostic['accepted_result'] is False
    assert issue['dimension'] == 'output_bytes' and issue['attempted'] > issue['limit'] == 10
    assert diagnostic['usage']['scanned_output_bytes'] > 10
    assert diagnostic['usage']['work'] == owner.report()['usage']['work'] > 0
    assert owner.report()['usage']['retained_output_bytes'] == 0
    assert owner.report()['usage']['returned_bytes'] == 0 and owner._cache == []
    assert len(json.dumps(diagnostic)) < 8192


def test_cumulative_return_exhaustion_keeps_true_cache_and_never_claims_a_return(monkeypatch):
    monkeypatch.setattr(mechanics, 'execute_mechanics', lambda **kw: {'row': 1})
    owner = session(returned_bytes=0)
    with pytest.raises(ExecutionVerificationError):
        owner.replay(execution())
    usage = owner.report()['usage']
    assert owner.report()['stopped']
    assert usage['retained_output_bytes'] > 0 and usage['returned_bytes'] == 0
    owner.close()
    assert owner.report()['usage']['retained_output_bytes'] == 0


@pytest.mark.parametrize('domain_wrapper', [False, True])
def test_existing_wrappers_preserve_capacity_diagnostic_without_losing_domain_facts(monkeypatch, domain_wrapper):
    monkeypatch.setattr(mechanics, 'execute_mechanics', lambda **kw: pytest.fail('unpaid execution'))
    original = {'unsealed': 'actual domain prefix'}
    @verification_scope
    def wrapped(*, _session=None):
        try:
            _session.replay(execution())
        except ValueError as error:
            if domain_wrapper:
                raise PlanError('domain pending', 'domain_pending', {'actual_result': original}) from error
            raise ValueError('legacy generic wrapper') from error
    owner = session(requests=0)
    with pytest.raises(ValueError) as raised:
        wrapped(_session=owner)
    error = raised.value
    assert error.reason == ('domain_pending' if domain_wrapper else 'verification_budget')
    if domain_wrapper:
        assert error.details['actual_result'] is original
    diagnostic = verification_diagnostic(error)
    assert diagnostic['issues'][0]['dimension'] == 'requests'
    assert diagnostic['usage']['executions'] == 0
    assert diagnostic['sealed'] is False


def test_material_envelopes_still_count_aliases_and_aggregate_roles():
    item = {'proof': ['exact', 1]}
    owner = session(material_bytes=20)
    with pytest.raises(ExecutionVerificationError) as raised:
        owner.scan_receipt_materials(outputs=(item, item))
    assert raised.value.reason == 'verification_material_bytes'
    assert verification_diagnostic(raised.value)['issues'][0]['limit'] == 20
    normal = session()
    assert normal.scan_receipt_materials(outputs=(item, item)) == session().scan_receipt_materials(outputs=(item, copy.deepcopy(item)))


def test_internal_headroom_never_changes_wire_envelope_or_source_authority():
    from genshin_app.calculation_delivery_files import LIMITS as delivery
    assert delivery['calculated_bytes'] == 96*1024*1024
    cap = verification_budget_contract()
    assert cap['defaults']['output_bytes'] == 256*1024*1024
    assert cap['source_authority'] == 'none'
