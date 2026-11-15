"""Independent actual RQ1 queue probes; output-only, no source authority."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.resource_reaction_runtime import Runtime
from tests.test_reaction_operation_capabilities import setup
from tests.test_reaction_resource_queue import enable


def reject(out):
    assert out['status'] == 'pending' and out['result'] is None, out.get('issues')
    assert out['resource_reactions']['verification_status'] == 'verification_pending'


@pytest.mark.parametrize('reacts', [True, False])
def test_passed_parent_cannot_lose_all_actual_application_evidence(monkeypatch, reacts):
    """No-match produces no ledger row; queue execution must still anchor the app."""
    values = enable(setup())
    values[2]['shared_pools']['operations'][0]['trigger']['reaction_types'] = ['melt']
    if not reacts:
        values[2]['elemental_model']['target']['initial_aura'] = None
    original = Runtime.attach

    def omitted(self, output):
        application = next(r for r in self.context.elemental.trace if r['type'] == 'elemental_application')
        parent_run = application['run_id']
        reaction_run = (application.get('reaction') or {}).get('run_id')
        self.context.elemental.trace[:] = [r for r in self.context.elemental.trace
            if r.get('run_id') != parent_run]
        self.context.trace[:] = [r for r in self.context.trace
            if not (r.get('type') == 'elemental_application' and r.get('run_id') == parent_run)
            and not (r.get('type') == 'elemental_reaction' and r.get('run_id') == reaction_run)]
        if reacts:
            scope = self.audit._occurrences.pop(0)
            self.audit._rows[:] = [r for r in self.audit._rows if r['occurrence_id'] != scope['id']]
        original(self, output)

    monkeypatch.setattr(Runtime, 'attach', omitted)
    reject(execute_mechanics(*values))


def test_zero_reaction_application_still_belongs_to_actual_actor(monkeypatch):
    values = enable(setup())
    values[2]['elemental_model']['target']['initial_aura'] = None
    original = Runtime.attach

    def changed(self, output):
        application = next(r for r in self.context.elemental.trace if r['type'] == 'elemental_application')
        application['character'] = 'b'
        twin = next(r for r in self.context.trace if r['type'] == 'elemental_application'
                    and r['run_id'] == application['run_id'])
        twin['character'] = 'b'
        original(self, output)

    monkeypatch.setattr(Runtime, 'attach', changed)
    reject(execute_mechanics(*values))


@pytest.mark.parametrize('field,value', [('frontline', 'b'), ('source_cycle', 9), ('run_id', 'foreign')])
def test_final_parent_binding_rejects_queue_identity_changes(monkeypatch, field, value):
    original = Runtime.attach

    def changed(self, output):
        row = next(r for r in output['rotation_check']['event_log'] if r['type'] == 'mechanics_hit')
        row[field] = value
        original(self, output)

    monkeypatch.setattr(Runtime, 'attach', changed)
    reject(execute_mechanics(*enable(setup())))


def test_actual_raw_resource_change_is_not_hidden_by_unchanged_runtime_trace(monkeypatch):
    original = Runtime.attach

    def changed(self, output):
        self.audit._bound_ledger._receipts[0]['accepted'] += 1
        original(self, output)

    monkeypatch.setattr(Runtime, 'attach', changed)
    out = execute_mechanics(*enable(setup()))
    reject(out)
    assert any(r['type'] == 'operation' and r['accepted'] == 3 for r in out['shared_pool_trace'])


def test_second_ledger_failure_keeps_first_complete_span(monkeypatch):
    from genshin_engine.shared_pools import SharedPoolError, SharedPoolLedger

    values = enable(setup())
    second = copy.deepcopy(values[2]['shared_pools']['operations'][0])
    second.update(id='later', source_id='binding:later', order=50)
    values[2]['shared_pools']['operations'].append(second)
    values[2]['resource_reaction_contract']['operation_ids'].append('later')
    original = SharedPoolLedger.apply_operation

    def failing(self, operation_id, **kwargs):
        if operation_id == 'later':
            raise SharedPoolError('pool_receipt_limit', 'independent second ledger failure', severity='pending')
        return original(self, operation_id, **kwargs)

    monkeypatch.setattr(SharedPoolLedger, 'apply_operation', failing)
    out = execute_mechanics(*values)
    assert out['status'] == 'pending' and out['result'] is None
    assert [r['state'] for r in out['resource_reactions']['operations']] == ['committed', 'failed']
    assert out['resource_reactions']['verification_status'] == 'verified'
    assert out['resource_reactions']['verified_window'] == 'executed_prefix'
    assert not out['resource_reactions']['occurrences'][0]['callback_complete']
    assert out['shared_pool_state']['balances']['pool']['team'] == 3
    assert [r['operation_id'] for r in out['shared_pool_trace'] if r['type'] == 'operation'] == ['refill']


def test_no_contract_is_zero_added_final_verification_work(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('opt-in Runtime must not exist for a legacy input')
    monkeypatch.setattr(Runtime, '__init__', forbidden)
    out = execute_mechanics(*setup())
    assert out['result'] is not None and 'resource_reactions' not in out


def test_foreign_actual_parent_copy_cannot_bind_application(monkeypatch):
    original = Runtime.begin

    def copied(self, event, hit, reaction_event, parent, application, reaction):
        return original(self, copy.deepcopy(event), hit, reaction_event, parent, application, reaction)

    monkeypatch.setattr(Runtime, 'begin', copied)
    out = execute_mechanics(*enable(setup()))
    assert out['status'] == 'pending' and out['result'] is None
    assert any(r['code'] == 'resource_reaction_actual_identity' for r in out['issues'])
    assert not [r for r in out['shared_pool_trace'] if r['type'] == 'operation']


def test_missed_attempt_is_present_without_income_or_icd_count():
    values = enable(setup())
    values[2]['hit_events'][0]['application']['landed'] = False
    out = execute_mechanics(*values)
    assert out['result'] is not None, out['issues']
    audit = out['resource_reactions']
    assert audit['verification_status'] == 'verified'
    first = next(r for r in out['rotation_check']['event_log'] if r['type'] == 'mechanics_hit')
    assert first['status'] == 'missed'
    attempt = next(r for r in out['elemental_trace'] if r['run_id'] == first['run_id'])
    assert attempt['reason'] == 'missed' and attempt['allowed'] is False and attempt['reaction'] is None
    assert not any(r['parent_run_id'] == first['run_id'] for r in audit['occurrences'])
    assert len(audit['occurrences']) == len(audit['operations']) == 2


def test_final_exhausted_budget_stops_before_encoding_children(monkeypatch):
    from genshin_engine import resource_reaction_program

    original_attach = Runtime.attach
    original_encode = resource_reaction_program._canonical
    final = False
    encodings = []

    def counted(value):
        if final:
            encodings.append(type(value).__name__)
        return original_encode(value)

    def exhausted(self, output):
        nonlocal final
        self.audit._work_limit = self.audit._work + self.audit._tickets
        final = True
        original_attach(self, output)

    monkeypatch.setattr(resource_reaction_program, '_canonical', counted)
    monkeypatch.setattr(Runtime, 'attach', exhausted)
    out = execute_mechanics(*enable(setup()))
    reject(out)
    assert encodings == []
    assert [r['state'] for r in out['resource_reactions']['operations']] == ['committed'] * 3
    assert len([r for r in out['shared_pool_trace'] if r['type'] == 'operation']) == 3
    assert any(r['type'] == 'hit' for r in out['mechanics_trace'])
