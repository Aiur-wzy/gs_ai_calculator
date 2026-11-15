import copy

import pytest

from genshin_engine import mechanics
from genshin_engine.mechanics import execute_mechanics
from genshin_engine.resource_definition_index import DefinitionIndex, IndexError
from genshin_engine.resource_lifecycle_index import IndexedQualification, _Compiler
from genshin_engine.resource_lifecycle_runtime import Runtime
from tests.test_resource_lifecycle_actual import values


def incomes(out):
    return [row for row in out['shared_pool_trace'] if row['type'] == 'operation']


@pytest.mark.parametrize('stage', ['prepare', 'admit', 'boundary'])
def test_exhausted_stage_returns_actual_prefix_and_checks_final_graph(monkeypatch, stage):
    captured = []
    if stage == 'boundary':
        original = mechanics._attach_state_runtime_report
        def attach(out, executor, limit):
            runtime = executor.resource_reactions.lifecycle
            audit = runtime.reaction.audit
            audit._work_limit = audit._work + audit._tickets
            captured.append(runtime)
            return original(out, executor, limit)
        monkeypatch.setattr(mechanics, '_attach_state_runtime_report', attach)
    else:
        original = getattr(Runtime, stage)
        calls = []
        def exhaust(self, operation):
            calls.append(True)
            if len(calls) == 2:
                audit = self.reaction.audit
                audit._work_limit = audit._work + audit._tickets
                captured.append(self)
            return original(self, operation)
        monkeypatch.setattr(Runtime, stage, exhaust)
    out = execute_mechanics(*values(delay=0, duration=700000))
    assert out['status'] == 'pending' and out['result'] is None
    assert out['state_runtime']['state'] is None and out['state_runtime']['state_issue']
    assert len(incomes(out)) == (3 if stage == 'boundary' else 1)
    assert out['resource_reactions']['verification_status'] == 'verification_pending'
    assert captured[0].index._closed
    assert captured[0].index._root_visits >= 2
    assert captured[0].index._final_cost > 0


def test_final_protected_work_detects_changed_graph_even_after_operational_exhaustion(monkeypatch):
    original = Runtime.prepare
    captured = []
    def exhaust(self, operation):
        if self.service.usage['attempts'] == 1:
            self.ctx.rules['benefit']['effects'][0]['value'] += 1
            audit = self.reaction.audit
            audit._work_limit = audit._work + audit._tickets
            captured.append(self)
        return original(self, operation)
    monkeypatch.setattr(Runtime, 'prepare', exhaust)
    out = execute_mechanics(*values(delay=0, duration=700000))
    assert out['result'] is None and len(incomes(out)) == 1
    assert captured[0].index._closed
    assert 'edge changed' in out['resource_reactions']['verification_issue']['message']


def test_compiler_storage_is_already_paid_before_any_graph_vector(monkeypatch):
    original = _Compiler.containers
    observed = []
    def observe(self, nodes):
        observed.append(self.budget.storage)
        assert self.budget.storage > 8*1024*1024
        return original(self, nodes)
    monkeypatch.setattr(_Compiler, 'containers', observe)
    out = execute_mechanics(*values(delay=0, duration=700000))
    assert out['result'] is not None and observed


def test_low_compiler_storage_stops_before_materializing_graph_vectors(monkeypatch):
    original = _Compiler.__init__
    called = []
    captured = []
    def bounded(self, budget):
        original(self, budget)
        budget.storage_limit = budget.storage
        captured.append(budget)
    def forbidden(*args, **kwargs):
        called.append(True)
        raise AssertionError('traversal allocated after failed storage reserve')
    monkeypatch.setattr(_Compiler, '__init__', bounded)
    monkeypatch.setattr(_Compiler, 'containers', forbidden)
    out = execute_mechanics(*values(delay=0, duration=700000))
    assert out['result'] is None and out['status'] == 'pending', out['issues']
    assert not called and captured[0].rejected_reservations == 1
    assert captured[0].storage > 0 and captured[0].work > 0


def test_indexed_qualification_maps_need_storage_before_initialization(monkeypatch):
    original_index = DefinitionIndex.__init__
    captured = []
    def index(self, *args, **kwargs):
        original_index(self, *args, **kwargs)
        self.budget.storage_limit = self.budget.storage
    original_service = IndexedQualification.__init__
    def service(self, adapter):
        with pytest.raises(IndexError):
            original_service(self, adapter)
        assert not self.__dict__
        captured.append(adapter.budget)
        raise IndexError('index_limit', 'Recorded preallocation failure')
    monkeypatch.setattr(DefinitionIndex, '__init__', index)
    monkeypatch.setattr(IndexedQualification, '__init__', service)
    out = execute_mechanics(*values(delay=0, duration=700000))
    assert out['result'] is None and out['status'] == 'pending', out['issues']
    assert captured and captured[0].storage > 0


def test_actual_attempt_storage_failure_is_nonrefundable_and_preserves_prefix(monkeypatch):
    original = Runtime.prepare
    captured = []
    def prepare(self, operation):
        if self.service.usage['attempts'] == 1:
            self.budget.storage_limit = self.budget.storage
            captured.append((self, self.budget.storage, self.budget.work))
        return original(self, operation)
    monkeypatch.setattr(Runtime, 'prepare', prepare)
    data = values(delay=0, duration=700000)
    before = copy.deepcopy(data)
    out = execute_mechanics(*data)
    assert out['result'] is None and out['status'] == 'pending', out['issues']
    assert len(incomes(out)) == 1 and data == before
    runtime, storage, work = captured[0]
    assert runtime.budget.storage >= storage and runtime.budget.work >= work
    assert runtime.index._closed
