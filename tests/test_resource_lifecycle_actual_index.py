import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.resource_lifecycle_runtime import Runtime as LifeRuntime
from genshin_engine.resource_reaction_runtime import Runtime as ReactionRuntime
from tests.test_mechanics_execution import action, add, rule
from tests.test_reaction_operation_capabilities import with_pool
from tests.test_resource_lifecycle_actual import values
from tests.test_swirl_dynamic_v3 import setup


def income(out):
    return [row for row in out.get('shared_pool_trace', []) if row['type'] == 'operation']


def extra_rule(data):
    add(data[2], rule('unrelated', value=0, source_id='separate',
        trigger={'event': 'hit', 'phase': 'post', 'event_ids': ['h2']}))


@pytest.mark.parametrize('mutation', ['source', 'owner', 'trigger', 'optional', 'directory'])
def test_actual_spine_catches_unrelated_becoming_related_before_income(monkeypatch, mutation):
    data = values(delay=0, duration=700000)
    extra_rule(data)
    original = ReactionRuntime.begin
    def change(self, *args):
        token = original(self, *args)
        row = self.context.rules['unrelated']
        if mutation == 'source': row['source_id'] = 'binding:benefit'
        elif mutation == 'owner': row['owner'] = 'changed-owner'
        elif mutation == 'trigger': row['trigger']['event_ids'].append('h0')
        elif mutation == 'optional': row['resources'] = []
        else: self.context.rules = dict(self.context.rules)
        return token
    monkeypatch.setattr(ReactionRuntime, 'begin', change)
    out = execute_mechanics(*data)
    assert out['result'] is None and out['status'] == 'pending', out['issues']
    assert not income(out)


def test_unrelated_numeric_mutation_rejects_total_preserves_only_unverified_raw_prefix(monkeypatch):
    data = values(delay=0, duration=700000)
    extra_rule(data)
    original = ReactionRuntime.attach
    def change(self, output):
        self.context.rules['unrelated']['effects'][0]['value'] = 0.125
        return original(self, output)
    monkeypatch.setattr(ReactionRuntime, 'attach', change)
    out = execute_mechanics(*data)
    assert out['result'] is None and out['resolved_template'] is None
    assert out['resource_reactions']['verification_status'] == 'verification_pending'
    assert out['resource_reactions']['verified_window'] == 'executed_prefix'
    assert len(income(out)) == 3


def test_cross_source_consumer_eligibility_numeric_checked_before_commit(monkeypatch):
    data = values(delay=0, duration=700000)
    data[2]['rules'][0]['source_id'] = 'separate-source'
    # The consumer still explicitly targets that exact rule.
    data[2]['shared_pools']['consumers'][0]['source_id'] = 'separate-source'
    original = ReactionRuntime.begin
    def change(self, *args):
        token = original(self, *args)
        self.context.rules['benefit']['effects'][0]['value'] = 101
        return token
    monkeypatch.setattr(ReactionRuntime, 'begin', change)
    out = execute_mechanics(*data)
    assert out['result'] is None and out['status'] == 'pending', out['issues']
    assert not income(out)


def test_empty_complete_does_not_refresh_mutated_lifecycle(monkeypatch):
    original = LifeRuntime.complete_batch
    changed = []
    def complete(self, record, token=None, **kwargs):
        if (not record['steps'] and self.clock._states.get('field', {}).get('phase') == 'active'
                and not changed):
            self.clock._states['field']['end_us'] += 1
            changed.append(True)
        return original(self, record, token, **kwargs)
    monkeypatch.setattr(LifeRuntime, 'complete_batch', complete)
    out = execute_mechanics(*values(delay=0, duration=700000))
    assert changed and out['result'] is None and not income(out), out['issues']


def test_dirty_other_lifecycle_does_not_refresh_mutated_watched_life(monkeypatch):
    data = values(delay=0, duration=700000)
    data[1]['actions'].append(action('cast-b', 'e', .15, .16, 80, repeat={'mode': 'once'}))
    other = copy.deepcopy(data[2]['state_runtime']['lifecycles'][0])
    other.update(id='other', source_id='other-source', start_order=2, establishment_order=11, end_order=2)
    other['start']['action_ids'] = ['cast-b']
    data[2]['state_runtime']['lifecycles'].append(other)
    original = LifeRuntime.complete_batch
    changed = []
    def complete(self, record, token=None, **kwargs):
        if any(step['lifecycle_id'] == 'other' for step in record['steps']) and not changed:
            self.clock._states['field']['end_us'] += 1
            changed.append(True)
        return original(self, record, token, **kwargs)
    monkeypatch.setattr(LifeRuntime, 'complete_batch', complete)
    out = execute_mechanics(*data)
    assert changed and out['result'] is None and not income(out), out['issues']


def test_real_completed_register_empty_batches_have_no_dirty_members(monkeypatch):
    seen = []
    original = LifeRuntime.verify_report
    def finish(self):
        original(self)
        seen.extend(self.completion_updates)
    monkeypatch.setattr(LifeRuntime, 'verify_report', finish)
    out = execute_mechanics(*values(delay=0, duration=700000))
    assert out['result'] is not None, out['issues']
    assert any(row['dirty'] == [] for row in seen)
    assert any(row['dirty'] == ['field'] for row in seen)
    assert len({row['batch_serial'] for row in seen}) == len(seen)


def test_gate_admission_rechecks_after_public_slot_reserve(monkeypatch):
    from genshin_engine.mechanics import _Mechanics
    original = _Mechanics._reserve_events
    def reserve(self, count, kind):
        rq = getattr(self, 'resource_reactions', None)
        if kind == 'resource_reaction_audit' and rq and rq.lifecycle and rq.lifecycle.lease:
            self.rules['benefit']['effects'][0]['value'] += 1
        return original(self, count, kind)
    monkeypatch.setattr(_Mechanics, '_reserve_events', reserve)
    out = execute_mechanics(*values(delay=0, duration=700000))
    assert out['result'] is None and not income(out), out['issues']


def test_failed_actual_attempt_cannot_retry_same_original_reaction(monkeypatch):
    from genshin_engine.resource_reaction_program import ReactionResourceError
    original = LifeRuntime.prepare
    attempted = []
    def retry(self, operation):
        prior = self.ctx.rules
        self.ctx.rules = dict(prior)
        with pytest.raises(ReactionResourceError):
            original(self, operation)
        self.ctx.rules = prior
        with pytest.raises(ReactionResourceError):
            original(self, operation)
        attempted.append(True)
        raise ReactionResourceError('engineering_retry', 'Original attempt is terminal', severity='pending')
    monkeypatch.setattr(LifeRuntime, 'prepare', retry)
    out = execute_mechanics(*values(delay=0, duration=700000))
    assert attempted and out['result'] is None and not income(out)


def large_case():
    data = with_pool(setup(tuple(round(.2 + i*.01, 2) for i in range(26))))
    small = values(delay=0, duration=700000)
    data[2]['state_runtime'] = small[2]['state_runtime']
    data[2]['resource_reaction_contract'] = small[2]['resource_reaction_contract']
    data[2]['shared_pools']['operations'][0]['trigger']['reaction_types'] = ['swirl']
    data[2]['elemental_model']['target']['initial_aura'] = None
    for i in range(0, 26, 2):
        data[0]['hits'][i]['element'] = 'pyro'
        data[2]['hit_events'][i]['application']['profile_id'] = 'pyro'
    for i in range(100):
        add(data[2], rule('unrelated-'+str(i), value=0, source_id='source-unrelated-'+str(i),
            source=('完整原文保留，不缩短字段。'*80),
            trigger={'event': 'hit', 'phase': 'post', 'event_ids': ['h25']}))
    return data


def test_thirteen_actual_reactions_large_original_text_reach_all_real_ledger_commits():
    data = large_case()
    before = copy.deepcopy(data)
    out = execute_mechanics(*data)
    assert out['result'] is not None, out['issues']
    assert len(out['resource_reactions']['occurrences']) == 13
    assert len(income(out)) == 13
    assert all(row['state'] == 'committed' for row in out['resource_reactions']['operations'])
    assert data == before
