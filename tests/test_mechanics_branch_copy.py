"""Fork prerequisites: complete owner graphs retain authority and isolation.

These are preparation-boundary checks, not a claim of event-level resumability.
"""
import copy

import pytest

from genshin_engine.mechanics import _EXECUTION_SERVICES
from genshin_engine.mechanics_execution_context import (
    MechanicsExecutionContext,
    MechanicsExecutionRequest,
)
from genshin_engine.mechanics_execution_pipeline import (
    _BASIC_PASS,
    _EXECUTION_PASSES,
    _run_pass,
)
from genshin_engine.mechanics_result_finalization import attach_execution_reports
from tests.test_resource_threshold_lifecycle_execution import life_case
from tests.test_shared_pool_execution import setup
from tests.test_stellar_resource_public_operations import fixture


def prepared(args):
    run=MechanicsExecutionContext(MechanicsExecutionRequest(*copy.deepcopy(args)))
    for stage in (_BASIC_PASS,*_EXECUTION_PASSES):
        if stage.pass_id=='simulation-v1':break
        assert _run_pass(stage,run,_EXECUTION_SERVICES,None),run.output
    return run


def finish(run):
    for stage in _EXECUTION_PASSES:
        if stage.pass_id in ('simulation-v1','result-projection-v1'):
            assert _run_pass(stage,run,_EXECUTION_SERVICES,None),run.output
    attach_execution_reports(run,_EXECUTION_SERVICES)
    return run.output


@pytest.mark.parametrize('factory',[fixture,setup,life_case])
def test_prepared_branch_callbacks_rebind_and_results_match(factory):
    original=prepared(factory())
    branch=copy.deepcopy(original)
    ex=branch.executor
    assert ex.event_emitter._reserve_event.__self__ is ex
    assert ex.elemental_bridge.ports.get_time.__self__ is ex
    assert branch.expanded is not original.expanded
    assert ex.state is not original.executor.state
    before=(original.executor.event_budget_count,copy.deepcopy(original.executor.trace))
    first=finish(branch)
    assert (original.executor.event_budget_count,original.executor.trace)==before
    second=finish(original)
    assert first==second


def test_pool_and_lifecycle_reservations_charge_only_the_copied_owner():
    original=prepared(life_case())
    branch=copy.deepcopy(original)
    ex=branch.executor
    before=original.executor.event_budget_count
    count=ex.event_budget_count
    ex._reserve_resource_events(2)
    ex.state_runtime._reserve_state_events(3)
    assert ex.event_budget_count==count+5
    assert original.executor.event_budget_count==before
    assert ex.state_runtime.ctx is ex
    assert ex.state_runtime.clock._reserve_external.__self__ is ex.state_runtime
    assert ex.state_runtime._authority is not original.executor.state_runtime._authority


def test_copied_router_dispatches_to_copied_owner_not_original():
    from genshin_engine.runtime_event_router import RuntimeEventRouter
    class Owner:
        def __init__(self):
            self.rows=[]
            self.router=RuntimeEventRouter({'record':self.record})
        def record(self,event,cycle,parent):
            self.rows.append((event,cycle,parent))
    original=Owner()
    branch=copy.deepcopy(original)
    assert branch.router.dispatch({'type':'record'},0,{'id':'parent'})
    assert len(branch.rows)==1 and original.rows==[]
    with pytest.raises(TypeError):branch.router._handlers['new']=branch.record
