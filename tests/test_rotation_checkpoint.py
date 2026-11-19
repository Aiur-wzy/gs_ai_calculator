"""Authoritative scheduler resumes at event boundaries without replaying them."""
import copy

import pytest

from genshin_engine.mechanics import _EXECUTION_SERVICES
from genshin_engine.mechanics_execution_pipeline import _EXECUTION_PASSES, _run_pass
from genshin_engine.mechanics_result_finalization import attach_execution_reports
from genshin_engine.rotation_checkpoint import RotationCheckpoint
from tests.test_action_admission_execution import admission_fixture
from tests.test_attribute_runtime import classify, enable, reader
from tests.test_mechanics_branch_copy import finish, prepared
from tests.test_mechanics_execution import add, rule
from tests.test_mechanics_execution import fixture as basic_fixture
from tests.test_resource_threshold_lifecycle_execution import life_case
from tests.test_shared_pool_execution import setup
from tests.test_stellar_resource_public_operations import fixture


def step(run,resume=None,count=1):
    return _EXECUTION_SERVICES.simulate(run.rotation,run.data,run.expanded,run.windows,
        run.finite_until,run.rotation_context,observer=run.executor,resume=resume,max_events=count)


def complete(run,resume=None,count=1):
    pauses=0
    while True:
        value=step(run,resume,count)
        if not isinstance(value,RotationCheckpoint):break
        pauses+=1
        assert pauses<5000
        resume=value
    run.simulation_result=value
    run.ready.add('simulation_result')
    assert _run_pass(_EXECUTION_PASSES[-1],run,_EXECUTION_SERVICES,None),run.output
    attach_execution_reports(run,_EXECUTION_SERVICES)
    return run.output,pauses


@pytest.mark.parametrize('factory',[fixture,setup,life_case])
@pytest.mark.parametrize('chunk',[1,3,11])
def test_resumed_full_result_equals_uninterrupted_including_derived_events(factory,chunk):
    args=factory()
    expected=finish(prepared(args))
    actual,pauses=complete(prepared(args),count=chunk)
    assert pauses>0
    assert actual==expected


@pytest.mark.parametrize('factory',[fixture,setup,life_case])
def test_fork_mid_execution_rebinds_queue_receipts_and_callbacks(factory):
    original=prepared(factory())
    pause=step(original,count=6)
    assert isinstance(pause,RotationCheckpoint)
    branch,branch_pause=copy.deepcopy((original,pause))
    original_trace=copy.deepcopy(original.executor.trace)
    actual,_=complete(branch,branch_pause)
    assert original.executor.trace==original_trace
    expected,_=complete(original,pause)
    assert actual==expected


def test_resume_rejects_reuse_or_other_execution_graph():
    original=prepared(setup())
    pause=step(original,count=0)
    other=copy.deepcopy(original)
    with pytest.raises(ValueError,match='different execution'):
        step(other,pause)
    next_pause=step(original,pause)
    assert isinstance(next_pause,RotationCheckpoint)
    with pytest.raises(ValueError,match='already been consumed'):
        step(original,pause)


@pytest.mark.parametrize('budget',[-1,True,1.5])
def test_checkpoint_budget_is_explicit_and_validated(budget):
    with pytest.raises(ValueError):step(prepared(setup()),count=budget)


@pytest.mark.parametrize('energy',[0,80])
def test_admission_energy_audit_and_failed_prefix_survive_checkpoints(energy):
    args=admission_fixture(initial_energy=energy)
    expected=finish(prepared(args))
    actual,_=complete(prepared(args))
    assert actual==expected


def test_attribute_frames_and_callbacks_remain_in_the_copied_graph():
    t,r,m=basic_fixture((.2,.4,.6))
    enable(t,m)
    add(m,rule('em','benefit.em.all',100),reader(basis='nonextra'))
    classify(m,'em')
    args=(t,r,m)
    expected=finish(prepared(args))
    original=prepared(args)
    pause=step(original,count=4)
    branch,branch_pause=copy.deepcopy((original,pause))
    assert branch.executor.attribute_runtime.executor is branch.executor
    actual,_=complete(branch,branch_pause)
    assert actual==expected
