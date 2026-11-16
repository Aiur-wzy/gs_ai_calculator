# ruff: noqa: F811 - pytest fixture injection uses imported names.
import copy

import pytest
from test_capacity_inputs import input_plan
from test_capacity_model import (  # noqa: F401
    action_resource_synthetic_db_only,
    fake_repo,
    ready,
    source_db,
)

from genshin_app.equipment.composer import compose_equipment_plan
from genshin_app.equipment.service import run_plan_calc

pytestmark = pytest.mark.usefixtures("action_resource_synthetic_db_only")


def confirmed(db, constellation=1):
    plan = input_plan()
    plan['rotation_model']['max_cycles'] = 3
    plan['rotation_model']['actions'][0]['repeat'] = {'mode': 'once'}
    plan['equipment_inputs']['a']['progression']['constellation'] = constellation
    from genshin_app.equipment.data_version import equipment_data_version
    from genshin_db.queries import get_meta
    plan["equipment_inputs"]["a"]["data_version"] = equipment_data_version(
        get_meta(db), "release"
    )
    report = compose_equipment_plan(plan, db)
    assert report['action_capacity_compilations'][0]['required_confirmation'], report['missing_inputs']
    plan['action_capacity_sources'][0].update(report['action_capacity_compilations'][0]['required_confirmation'])
    report = compose_equipment_plan(plan, db)
    assert report['state'] == 'ready', report['missing_inputs']
    return report['plan']


def test_actual_complete_app_calc(ready):
    plan = confirmed(ready[0])
    from genshin_app.equipment.schema import PlanError
    try:
        calculated = run_plan_calc(plan, ready[0], capture_display=True)
    except PlanError as exc:
        pytest.fail(str(exc.details))
    assert calculated['result']['rotation_check']['action_capacity_consumption']['committed_actions'] == 1



def calculate_case(ready, c=1, times=(0,), initial=()):
    plan = input_plan()
    plan['equipment_inputs']['a']['progression']['constellation'] = c
    plan['rotation_model']['period'] = plan['manual_template']['loop_duration'] = 20
    plan['rotation_model']['initial_cooldowns'][0]['ready_in'] = list(initial)
    original = plan['rotation_model']['actions'][0]
    plan['rotation_model']['actions'] = [{**copy.deepcopy(original), 'id': 'cast' if i == 0 else f'cast{i}',
        'start': t, 'occupation_end': t + .1, 'order': i, 'repeat': {'mode': 'once'}} for i, t in enumerate(times)]
    from genshin_app.equipment.data_version import equipment_data_version
    from genshin_db.queries import get_meta
    plan['equipment_inputs']['a']['data_version'] = equipment_data_version(
        get_meta(ready[0]), 'release'
    )
    report = compose_equipment_plan(plan, ready[0])
    plan['action_capacity_sources'][0].update(report['action_capacity_compilations'][0]['required_confirmation'])
    return run_plan_calc(plan, ready[0], capture_display=True)


@pytest.mark.parametrize('c,times,initial,committed,rejected', [
    (0, (0, 1), (), 1, 1), (1, (0, 1, 2), (), 2, 1),
    (1, (0, 1, 10), (), 3, 0), (1, (0, 1), (5,), 1, 1)])
def test_complete_app_actual_consumption(ready, c, times, initial, committed, rejected):
    from genshin_app.equipment.delivery import validate_delivery_calculated
    calculated = calculate_case(ready, c, times, initial)
    ledger = calculated['result']['rotation_check']['action_capacity_consumption']
    assert ledger['committed_actions'] == committed and ledger['rejected_actions'] == rejected
    assert [r['after'] for r in ledger['receipts'] if r['status'] == 'committed'][:2] == ([1, 0][:committed] if c == 1 and not initial else [0])
    validate_delivery_calculated(calculated)


@pytest.mark.parametrize('mutation', ['missing_receipt', 'missing_records', 'no_audit', 'zero_unused', 'count',
    'queue', 'source_marker', 'initial', 'charges', 'profile_capacity', 'inventory', 'revoked', 'completion', 'hidden_consumer'])
def test_offline_complete_delivery_rejects_tampering_even_after_result_rehash(ready, mutation):
    from genshin_app.equipment.delivery import validate_delivery_calculated
    from genshin_app.equipment.files import fingerprint
    c = calculate_case(ready)
    result, receipt, report = c['result'], c['result_receipt'], c['report']
    check = result['rotation_check']; ledger = check['action_capacity_consumption']
    if mutation == 'missing_receipt': receipt.pop('action_capacities')
    elif mutation == 'missing_records': result.pop('action_capacity_compilations')
    elif mutation == 'no_audit': ledger['receipts'] = []
    elif mutation == 'zero_unused': ledger.update(receipts=[], committed_actions=0, receipt_count=0, planned_actions=0)
    elif mutation == 'count': ledger['receipts'][0]['after'] += 1
    elif mutation == 'queue': ledger['receipts'][0]['queue_after'] = []
    elif mutation == 'source_marker': check['resolved_rotation_model']['abilities'][0]['cooldown']['capacity_source']['source_fingerprint'] = '0'*64
    elif mutation == 'initial': report['compiled_rotation_model']['initial_cooldowns'][0]['ready_in'] = [5]
    elif mutation == 'charges': report['compiled_rotation_model']['abilities'][0]['cooldown']['charges'] = 3
    elif mutation == 'profile_capacity': result['action_capacity_compilations'][0]['projection']['profile']['capacity'] = 3
    elif mutation == 'inventory': result['action_capacity_compilations'][0]['projection']['profile']['competitor_inventory']['entries'].pop()
    elif mutation == 'revoked': result['action_capacity_compilations'][0]['projection']['profile']['model_record']['review']['status'] = 'revoked'
    elif mutation == 'completion': result['mechanics']['complete_first_cycle'] = False
    else:
        hidden = copy.deepcopy(check['resolved_rotation_model']['abilities'][0]); hidden['id'] = 'hidden'
        check['resolved_rotation_model']['abilities'].append(hidden)
    receipt['result_fingerprint'] = fingerprint(result)
    with pytest.raises(ValueError): validate_delivery_calculated(c)


def test_save_reload_recalculate_and_offline_without_sqlite(ready, tmp_path, monkeypatch):
    import sqlite3

    from genshin_app.equipment.delivery import validate_delivery_calculated
    from genshin_app.equipment.files import read_plan, save_plan
    c = calculate_case(ready)
    path = tmp_path / 'capacity.plan.json'
    save_plan(path, c['plan'])
    again = run_plan_calc(read_plan(path)['plan'], ready[0], capture_display=True)
    assert c['result'] == again['result']
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k: pytest.fail('offline path read DB'))
    validate_delivery_calculated(c)


def test_replay_is_shared_in_one_actual_verification_scope(ready, monkeypatch):
    from genshin_app.equipment.delivery import validate_delivery_calculated
    from genshin_app.equipment.verification_scope import verification_scope
    from genshin_engine import mechanics
    c = calculate_case(ready)
    calls = []
    original = mechanics.execute_mechanics
    def count(*a, **k):
        calls.append(1)
        return original(*a, **k)
    monkeypatch.setattr(mechanics, 'execute_mechanics', count)
    @verification_scope
    def twice(*, _session=None):
        validate_delivery_calculated(c, _session=_session)
        validate_delivery_calculated(c, _session=_session)
        return _session.report()
    report = twice()
    assert len(calls) == 1 and report['usage']['executions'] == 1
