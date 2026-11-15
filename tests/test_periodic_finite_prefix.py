"""Anonymous finite-window periodic lifecycle acceptance and strict contrasts."""
import copy

import pytest

from genshin_app.equipment.periodic_lifecycle import (
    PeriodicLifecycleError,
    project_periodic_lifecycle,
)
from tests.test_periodic_lifecycle import channel, control, establish, lifecycle


def case(**extra):
    return dict(channels=[channel(first_offset_ticks=1, interval_ticks=2, count=10)],
        lifecycle=lifecycle(refresh='reset_duration', duration=20),
        establishments=[establish(at=0), establish('refresh', at=17, order=-1)],
        window_end_ticks=35, **extra)


def test_reset_duration_preserves_phase_and_active_tail_without_extending_window():
    args = case(projection_scope='finite_prefix'); old = copy.deepcopy(args)
    out = project_periodic_lifecycle(**args)
    assert out['occurrences'][-1]['time_ticks'] == 19
    assert all(r['time_ticks'] <= 35 for r in out['occurrences'])
    assert out['generations'][-1]['status'] == 'active_at_boundary'
    assert out['boundary']['remaining_active_ticks'] == 2
    assert out['boundary']['phase'] == 1
    assert out['boundary']['future_delivery_verified'] is False
    assert args == old


def test_legacy_complete_scope_keeps_its_boundary_guard():
    with pytest.raises(PeriodicLifecycleError, match='exceeds the projection window'):
        project_periodic_lifecycle(**case())


def test_paused_prefix_keeps_active_time_and_unissued_finite_candidates():
    args = case(projection_scope='finite_prefix')
    args['establishments'] = [establish()]
    args['lifecycle']['pause_resume_policy'] = 'pause_preserve_phase'
    args['controls'] = [control('pause', 'pause', 4)]
    out = project_periodic_lifecycle(**args)
    assert out['boundary']['status'] == 'paused'
    assert out['boundary']['remaining_active_ticks'] == 16
    assert all(r['time_ticks'] is None for r in out['boundary']['pending_occurrences'])
    assert [r['time_ticks'] for r in out['occurrences']] == [1, 3]


def test_prefix_includes_exact_boundary_without_claiming_future_delivery():
    args = case(projection_scope='finite_prefix')
    args['establishments'] = [establish(at=34)]
    out = project_periodic_lifecycle(**args)
    assert [r['time_ticks'] for r in out['occurrences']] == [35]
    assert out['boundary']['pending_occurrences'][0]['time_ticks'] == 37
    assert out['cancelled'] == []


@pytest.mark.parametrize('change', ['late_control', 'unknown_scope', 'same_position', 'bad_resume'])
def test_prefix_does_not_hide_illegal_axis(change):
    args = case(projection_scope='finite_prefix')
    if change == 'late_control': args['controls'] = [control('cancel', 'cancel', 36)]
    elif change == 'unknown_scope': args['projection_scope'] = 'guess'
    elif change == 'same_position': args['establishments'][1]['order'] = 0
    else: args['controls'] = [control('resume', 'resume', 2)]
    with pytest.raises(PeriodicLifecycleError): project_periodic_lifecycle(**args)


def test_real_adapter_installs_finite_prefix_and_scope_rebuild_rejects_forgery():
    from genshin_app.calculation_completion import calculation_completion
    from genshin_app.equipment.agent_execution_materialization import (
        materialize_agent_execution,
    )
    from genshin_app.equipment.agent_execution_runtime import (
        adapt_agent_execution_runtime,
    )
    from genshin_app.equipment.periodic_execution_scope import periodic_execution_scope
    from tests.test_agent_execution_materialization import (
        _periodic_payload,
        _runtime_base,
        _runtime_document,
        _runtime_topology,
    )
    plan, template, rotation, mechanics = _runtime_base()
    mechanics.pop('elemental_model')
    plan['mechanics_model'].pop('elemental_model')
    topology = _runtime_topology(periodic=True)
    next(n for n in topology['nodes'] if n['node_id'] == 'node.periodic')['schedule']['duration_seconds'] = 12
    document = _runtime_document(plan, topology, periodic=True)
    document['entries'][0]['payload'] = _periodic_payload(duration=12, count=3, interval=.5)
    effective = materialize_agent_execution(plan, topology, document)
    runtime = adapt_agent_execution_runtime(plan, topology, effective, template, rotation, mechanics)
    assert runtime['status'] == 'complete', runtime['receipt']
    scope = periodic_execution_scope(runtime['receipt'])
    assert scope['steady_state_proven'] is False
    result = {'agent_execution_runtime_receipt': runtime['receipt'], 'summary': {'total_damage': 0},
        'mechanics': {'complete_first_cycle': True}, 'mechanics_status': 'passed',
        'rotation_check': {'status': 'passed', 'energy_check': {'cycle_status': 'passed'}}}
    assert calculation_completion(result=result)['task_admission'] == 'accepted'
    assert calculation_completion(result=result)['steady_state'] == 'unproven'
    forged = copy.deepcopy(runtime['receipt'])
    forged['entries'][0]['installed'][0]['boundary']['status'] = 'inactive'
    with pytest.raises(ValueError, match='reconstructed'):
        periodic_execution_scope(forged)
    for change in ('boundary_deleted', 'projection_deleted'):
        forged = copy.deepcopy(runtime['receipt'])
        installed = forged['entries'][0]['installed']
        projection = next(item for item in installed if item['target_identity'].startswith('periodic_lifecycle:'))
        if change == 'boundary_deleted':
            del projection['boundary']
        else:
            installed.remove(projection)
        with pytest.raises(ValueError, match='evidence'):
            periodic_execution_scope(forged, effective, rotation)


@pytest.mark.parametrize('derived', [False, True])
def test_live_periodic_boundary_full_package_roundtrip(tmp_path, derived):
    from genshin_app.equipment import new_plan, run_plan_calc
    from genshin_app.equipment.mechanics import enable_mechanics
    from genshin_app.equipment.service import result_bytes
    from tests.test_agent_execution_materialization import (
        _periodic_payload,
        _runtime_base,
        _runtime_document,
        _runtime_topology,
    )
    _source, template, rotation, mechanics = _runtime_base()
    mechanics.pop('elemental_model')
    plan = enable_mechanics(new_plan())
    plan.update(manual_template=template, rotation_model=rotation, mechanics_model=mechanics)
    topology = _runtime_topology(periodic=True)
    next(n for n in topology['nodes'] if n['node_id'] == 'node.periodic')['schedule']['duration_seconds'] = 12
    if derived:
        next(n for n in topology['nodes'] if n['node_id'] == 'node.periodic')['schedule']['count'] = None
    doc = _runtime_document(plan, topology, periodic=True)
    doc['entries'][0]['payload'] = _periodic_payload(duration=12, count=3, interval=.5)
    if derived:
        doc['entries'][0]['payload']['schedule'].update(count=None, derive_count='while_active_inclusive')
    plan['extensions']['agent_execution_materialization'] = doc
    out = run_plan_calc(plan, tmp_path / 'missing.db')
    assert out['completion']['task_admission'] == 'accepted'
    assert out['completion']['steady_state'] == 'unproven'
    assert result_bytes(out['result'], out['result_receipt'], 'json')
