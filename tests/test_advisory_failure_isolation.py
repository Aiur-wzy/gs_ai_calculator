import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_engine.execution_control import ExecutionCancelled
from tests.test_energy_trials import plan as make_plan


@pytest.mark.parametrize('target', [
    'genshin_app.default_assistance.read_default_assistance',
    'genshin_app.effect_observation.observe_effects',
    'genshin_app.damage_count_observation.observe_damage_counts',
])
def test_observer_fault_does_not_change_numeric_result_or_admission(tmp_path, monkeypatch, target):
    plan = make_plan()
    path = tmp_path / 'absent.db'
    expected = run_plan_calc(plan, path)
    def failed(*args, **kwargs):
        raise RuntimeError('synthetic optional observer fault')
    monkeypatch.setattr(target, failed)
    assert compose_equipment_plan(plan, path)['state'] == 'ready'
    actual = run_plan_calc(plan, path)
    assert actual['result']['summary'] == expected['result']['summary']
    assert actual['result']['hits'] == expected['result']['hits']
    assert actual['result_receipt']['result_fingerprint']
    assert actual['completion'] == expected['completion']
    assert 'observation_unavailable' in str(actual['report']['default_assistance'])


@pytest.mark.parametrize('target', [
    'genshin_app.default_assistance.read_default_assistance',
    'genshin_app.effect_observation.observe_effects',
    'genshin_app.damage_count_observation.observe_damage_counts',
])
def test_optional_observer_never_swallows_user_cancellation(tmp_path, monkeypatch, target):
    def cancelled(*args, **kwargs):
        raise ExecutionCancelled()
    monkeypatch.setattr(target, cancelled)
    with pytest.raises(ExecutionCancelled):
        run_plan_calc(make_plan(), tmp_path / 'absent.db')
