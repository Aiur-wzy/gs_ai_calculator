"""App entry points share bounded work and retain truthful pending results."""
import copy
from types import SimpleNamespace

import pytest

from genshin_app.equipment import execution_verification as budget
from genshin_app.equipment import resource_reaction_receipt as subject
from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from tests.test_resource_lifecycle_app_gates import gated_plan
from tests.test_shared_pool_app_delivery import authorize


def test_make_rejects_input_before_any_hash_or_copy(monkeypatch):
    session = budget.ExecutionVerificationSession(budget={'policy': 'execution-verification-budget-v2', 'limits': {'input_nodes': 1}})
    monkeypatch.setattr(subject, 'fingerprint', lambda value: pytest.fail('unbounded fingerprint'))
    monkeypatch.setattr(subject, 'copy', SimpleNamespace(deepcopy=lambda value: pytest.fail('unbounded copy')))
    result = {'rotation_check': {'resolved_rotation_model': {}}}
    report = {'mechanics_input_template': {},
              'mechanics_model': {subject.FIELD: {'schema_version': 2}},
              subject.ADOPTION: {}}
    with pytest.raises(PlanError) as caught:
        subject.make_resource_reaction_receipt({}, report, result, _session=session)
    assert caught.value.reason == 'resource_reaction_proof_pending'
    assert caught.value.details['actual_result'] is result
    assert caught.value.details['boundary_code'] == 'verification_input_nodes'
    assert caught.value.details['fresh'] is False


def test_app_make_validate_and_export_use_exact_same_session(tmp_path, monkeypatch):
    db = tmp_path / 'absent.db'
    plan = authorize(gated_plan(), db)
    observed = []
    real = budget.ExecutionVerificationSession.scan_receipt_materials
    def scan(self, **kwargs):
        observed.append(self)
        return real(self, **kwargs)
    monkeypatch.setattr(budget.ExecutionVerificationSession, 'scan_receipt_materials', scan)
    with budget.ExecutionVerificationSession() as session:
        calculated = run_plan_calc(plan, db, _session=session)
        first = session.report()['usage']['work']
        count = len(observed)
        assert count > 4 and all(item is session for item in observed)
        result_bytes(calculated['result'], calculated['result_receipt'], 'json', _session=session)
        assert len(observed) > count and all(item is session for item in observed)
        assert session.report()['usage']['work'] > first
        assert session.report()['usage']['executions'] == 1
    assert not db.exists()


def test_export_budget_error_is_pending_not_false_source_tampering(tmp_path, monkeypatch):
    db = tmp_path / 'absent.db'
    plan = authorize(gated_plan(), db)
    with budget.ExecutionVerificationSession() as probe:
        run_plan_calc(plan, db, _session=probe)
        used = probe.report()['usage']['work']
    with budget.ExecutionVerificationSession(budget={'policy': 'execution-verification-budget-v2', 'limits': {'work': used}}) as session:
        calculated = run_plan_calc(plan, db, _session=session)
        before = copy.deepcopy(calculated)
        with pytest.raises(PlanError) as caught:
            result_bytes(calculated['result'], calculated['result_receipt'], 'json', _session=session)
        assert caught.value.reason == 'resource_reaction_proof_pending'
        assert caught.value.details['boundary_code'] == 'verification_budget'
        assert caught.value.details['actual_result'] is calculated['result']
        assert session.report()['stopped'] and calculated == before


def test_v1_keeps_old_guard_and_does_not_pay_new_scan(tmp_path, monkeypatch):
    from tests.test_resource_reaction_app import reaction_plan
    db = tmp_path / 'absent.db'
    monkeypatch.setattr(budget.ExecutionVerificationSession, 'scan_receipt_materials',
                        lambda *args, **kwargs: pytest.fail('v1 acquired new scan policy'))
    result = run_plan_calc(authorize(reaction_plan(), db), db)
    result_bytes(result['result'], result['result_receipt'], 'json')


def test_shallow_v2_ledger_is_content_exact_and_never_changes_source(tmp_path):
    db = tmp_path / 'absent.db'
    result = run_plan_calc(authorize(gated_plan(), db), db)['result']
    before = copy.deepcopy(result)
    old = subject._ledger(result)
    with budget.ExecutionVerificationSession() as session:
        new = subject._ledger(result, _session=session)
        assert subject.fingerprint(old) == subject.fingerprint(new)
        assert new['mechanics_trace'] is result['mechanics_trace']
    assert result == before
