"""Public v2 App source closure and actual timer/counter threshold delivery."""
from __future__ import annotations

import copy
import json

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.schema import PlanError
from tests.test_mechanics_execution import add, rule
from tests.test_plan_mechanics import native
from tests.test_resource_counter_execution import case
from tests.test_shared_pool_app_delivery import authorize


def threshold_counter_plan():
    plan = native()
    template, rotation, model = case(times=(.15, .25, .35))
    template['characters'][0]['stats']['energy_recharge'] = 1
    plan.update(manual_template=template, rotation_model=rotation, mechanics_model=model)
    model['rules'] = []
    model['initial_state']['rules'] = []
    parts = plan['effect_bindings'][0]['parts']
    prototype = copy.deepcopy(parts[0])
    parts.clear()
    for name in ('pool', 'life', 'timer', 'counter', 'observer', 'target'):
        authority = rule(name, source_id='binding:' + name)
        authority['effects'] = []
        if name == 'target':
            authority = rule(name, source_id='binding:target', domain='provider', duration=.25,
                             trigger={'kind': 'resource_threshold', 'binding_id': 'spent-window'})
        add(model, authority)
        part = copy.deepcopy(prototype)
        part.update(part_id=name, label='工程来源 ' + name)
        part['rule']['rule_id'] = name
        parts.append(part)
    for row in model['shared_pools']['pools']: row['source_id'] = 'binding:pool'
    for row in model['shared_pools']['operations']: row['source_id'] = 'binding:timer'
    model['state_runtime']['lifecycles'][0]['source_id'] = 'binding:life'
    model['state_runtime']['timers'][0]['source_id'] = 'binding:timer'
    model['resource_counters']['counters'][0]['source_id'] = 'binding:counter'
    model['resource_threshold_observers'] = {'schema_version': 2, 'observers': [{
        'id': 'spent', 'name_zh': '工程实际消费跨线', 'owner': 'a', 'source_id': 'binding:observer',
        'input': {'kind': 'counter_value_key', 'counter_id': 'count', 'key': 'total'},
        'comparison': 'enter_gte', 'threshold_atoms': 16,
        'causes': {'operation_ids': ['refill'], 'consumer_ids': []},
        'status': 'assumed', 'source': '工程情景，不代表游戏客户端事实'}]}
    model['resource_threshold_triggers'] = {'schema_version': 2, 'bindings': [{
        'id': 'spent-window', 'name_zh': '累计过线短时增伤', 'owner': 'a', 'source_id': 'binding:target',
        'observer_id': 'spent', 'order': 1, 'payload': {'kind': 'activate_rule', 'ref_id': 'target'},
        'status': 'assumed', 'source': '工程情景，不代表游戏客户端事实'}]}
    return plan


def part(plan, ident):
    return next(p for p in plan['effect_bindings'][0]['parts'] if p['part_id'] == ident)


def test_full_app_actual_timer_counter_threshold_uses_no_synthetic_condition(tmp_path):
    db = tmp_path / 'absent.db'
    plan = authorize(threshold_counter_plan(), db)
    before = copy.deepcopy(plan)
    out = run_plan_calc(plan, db)
    assert [h['expected'] for h in out['result']['hits']] == [45, 90, 90]
    assert not plan['mechanics_model']['condition_events']
    assert out['result']['resource_counters']['state']['values']['count']['total'] == 19
    observer, = out['report']['resource_threshold_observer_adoptions']
    binding, = out['report']['resource_threshold_trigger_adoptions']
    assert any(d['kind'] == 'counters' and d['id'] == 'count' for d in observer['dependencies'])
    assert any(d['kind'] == 'timers' for d in binding['dependencies'])
    assert any(d['kind'] == 'lifecycles' for d in binding['dependencies'])
    assert plan == before


@pytest.mark.parametrize('excluded', ['observer', 'target', 'counter'])
def test_excluding_threshold_dependency_preserves_independent_real_spend(tmp_path, excluded):
    db = tmp_path / 'absent.db'
    plan = threshold_counter_plan()
    part(plan, excluded).update(state='excluded', reason='Explicit synthetic exclusion')
    out = run_plan_calc(authorize(plan, db), db)
    assert [h['expected'] for h in out['result']['hits']] == [45, 45, 45]
    assert [r['accepted'] for r in out['result']['shared_pools']['trace'] if r['operation'] == 'spend'] == [8, 8, 3]
    assert 'resource_threshold_triggers' not in out['result']
    if excluded != 'counter':
        assert out['result']['resource_counters']['state']['values']['count']['total'] == 19


def test_excluding_timer_closes_actual_clock_and_dependent_threshold(tmp_path):
    db = tmp_path / 'absent.db'
    plan = threshold_counter_plan()
    part(plan, 'timer').update(state='excluded', reason='Timer absent')
    out = run_plan_calc(authorize(plan, db), db)
    assert [h['expected'] for h in out['result']['hits']] == [45, 45, 45]
    assert 'resource_threshold_triggers' not in out['result']
    assert 'state_runtime' not in out['report']['mechanics_model']


@pytest.mark.parametrize('reverse', [False, True])
def test_pending_timer_survives_excluded_target_and_order(tmp_path, reverse):
    db = tmp_path / 'absent.db'
    plan = threshold_counter_plan()
    part(plan, 'target').update(state='excluded', reason='Target absent')
    model = plan['mechanics_model']
    model['state_runtime']['timers'][0]['status'] = 'pending'
    if reverse: model['rules'].reverse()
    report = compose_equipment_plan(authorize(plan, db), db)
    assert report['state'] == 'pending'
    assert report['resource_threshold_trigger_adoptions'][0]['effective_status'] == 'dependency_pending'


@pytest.mark.parametrize('change', ['counter', 'interval', 'threshold'])
def test_counter_timer_and_threshold_edits_invalidate_review(tmp_path, change):
    db = tmp_path / 'absent.db'
    plan = authorize(threshold_counter_plan(), db)
    model = plan['mechanics_model']
    if change == 'counter': model['resource_counters']['counters'][0]['reset_on'] = 'never'
    elif change == 'interval': model['state_runtime']['timers'][0]['interval_us'] = 200000
    else: model['resource_threshold_observers']['observers'][0]['threshold_atoms'] = 17
    assert compose_equipment_plan(plan, db)['state'] == 'pending'
    out = run_plan_calc(authorize(plan, db), db)
    assert out['result']['resource_threshold_observers']['contract'].endswith('v2')


def test_excluded_target_cannot_hide_terminal_timer_origin(tmp_path):
    plan = threshold_counter_plan()
    plan['mechanics_model']['state_runtime']['timers'][0].update(end_tick='before_expiry', terminal_order=1)
    part(plan, 'target').update(state='excluded', reason='Target absent')
    with pytest.raises(PlanError):
        compose_equipment_plan(plan, tmp_path / 'absent.db')


def test_counter_and_timer_provenance_survives_query_and_three_exports(tmp_path):
    from genshin_app.calculation_review import review_calculation
    from genshin_app.equipment.service import result_bytes
    from genshin_app.export import export_result
    from genshin_app.formula_workbook import (
        build_formula_workbook,
        formula_workbook_support,
    )
    from tests.test_freeze_export_review import workbook_sheets

    db = tmp_path / 'absent.db'
    out = run_plan_calc(authorize(threshold_counter_plan(), db), db, capture_display=True)
    field = 'resource_threshold_triggers'
    page = review_calculation(out, {'view': field})
    assert page['validation'] == 'full_calculation_package'
    row, = page['items']
    assert row['source_origin']['timer_id'] == 'refresh' and row['source_origin']['ordinal'] == 2
    assert row['counter_observation']['counter_id'] == 'count'
    assert row['before'] == 8 and row['after'] == 16
    observer, = review_calculation(out, {'view': 'resource_threshold_observers', 'filters': {'start': .2, 'end': .3}})['items']
    assert observer['counter_before_cursor']['ledger_sequence'] == 1
    assert observer['counter_after_cursor']['ledger_sequence'] == 2
    assert observer['resource_receipt_ids']
    assert observer['input_kind'] == 'counter_value_key'
    assert formula_workbook_support(out)['editable']['resource_thresholds_and_sources'] == 'regenerate_required'
    target = tmp_path / 'counter-threshold.xlsx'
    export_result(out['result'], 'xlsx', target, receipt=out['result_receipt'])
    for raw in (target.read_bytes(), result_bytes(out['result'], out['result_receipt'], 'xlsx'), build_formula_workbook(out)):
        sheets = workbook_sheets(raw)
        for title, field in [('资源阈值观察', 'resource_threshold_observers'), ('阈值收益执行', 'resource_threshold_triggers')]:
            cells = sheets[title]
            chunks = sorted((int(c['value'].split()[-1]), cells['B' + ref[1:]]['value'])
                for ref, c in cells.items() if ref.startswith('A') and isinstance(c['value'], str) and c['value'].startswith('原文块 '))
            assert json.loads(''.join(text for _, text in chunks)) == out['result'][field]
            assert any('消费累计' in str(c['value']) for ref, c in cells.items() if ref.startswith('H'))
            assert all(not c['formula'] for c in cells.values())
