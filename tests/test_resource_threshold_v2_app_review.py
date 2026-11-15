"""Independent public-v2 App closure; prior missing execution is not revived."""
from __future__ import annotations

import pytest

from genshin_app.equipment import compose_equipment_plan
from genshin_app.equipment.schema import PlanError
from tests.test_resource_threshold_v2_app import (
    part,
    threshold_counter_plan,
)
from tests.test_shared_pool_app_delivery import authorize


@pytest.mark.parametrize('missing', ['counter', 'timer'])
def test_ready_earlier_adoption_without_actual_execution_is_not_recreated(tmp_path, monkeypatch, missing):
    db = tmp_path / 'absent.db'
    plan = authorize(threshold_counter_plan(), db)
    if missing == 'counter':
        import genshin_app.equipment.resource_counters as module
        original = module.prepare_resource_counters
        def remove(*args, **kwargs):
            _, records = original(*args, **kwargs)
            assert all(r['status'] == 'ready' for r in records)
            return None, records
        monkeypatch.setattr(module, 'prepare_resource_counters', remove)
    else:
        import genshin_app.equipment.state_runtime as module
        original = module.prepare_state_runtime
        def remove(*args, **kwargs):
            result = list(original(*args, **kwargs))
            assert result[0] is not None
            result[0] = None
            return tuple(result)
        monkeypatch.setattr(module, 'prepare_state_runtime', remove)
    report = compose_equipment_plan(plan, db)
    assert report['state'] == 'pending'
    assert report['resource_threshold_trigger_adoptions'][0]['effective_status'] == 'dependency_pending'
    assert 'resource_threshold_triggers' not in report['mechanics_model']


@pytest.mark.parametrize('reverse', [False, True])
def test_pending_counter_is_not_laundered_by_explicit_target_exclusion(tmp_path, reverse):
    plan = threshold_counter_plan()
    part(plan, 'target').update(state='excluded', reason='Target absent, counter still unresolved')
    plan['mechanics_model']['resource_counters']['counters'][0]['status'] = 'pending'
    if reverse:
        plan['mechanics_model']['rules'].reverse()
    db = tmp_path / 'absent.db'
    report = compose_equipment_plan(authorize(plan, db), db)
    assert report['state'] == 'pending'
    assert report['resource_threshold_trigger_adoptions'][0]['own_source_status'] == 'excluded'
    assert report['resource_threshold_trigger_adoptions'][0]['effective_status'] == 'dependency_pending'


def test_exclusion_cannot_hide_unknown_counter_key_or_timer_source_mismatch(tmp_path):
    db = tmp_path / 'absent.db'
    for mutation in ('key', 'timer_source'):
        plan = threshold_counter_plan()
        for p in plan['effect_bindings'][0]['parts']:
            p.update(state='excluded', reason='Original graph must still be checked')
        if mutation == 'key':
            plan['mechanics_model']['resource_threshold_observers']['observers'][0]['input']['key'] = 'missing'
        else:
            plan['mechanics_model']['state_runtime']['timers'][0]['source_id'] = 'binding:observer'
        with pytest.raises(PlanError):
            compose_equipment_plan(plan, db)


def test_timer_manual_source_only_confirmation_does_not_approve_counter_threshold(tmp_path):
    db = tmp_path / 'absent.db'
    plan = authorize(threshold_counter_plan(), db)
    part(plan, 'timer')['dependencies']['mode'] = 'source_only'
    report = compose_equipment_plan(plan, db)
    assert report['state'] == 'pending'
    assert report['resource_threshold_trigger_adoptions'][0]['effective_status'] in ('pending', 'dependency_pending')
    assert 'resource_threshold_triggers' not in report['mechanics_model']
