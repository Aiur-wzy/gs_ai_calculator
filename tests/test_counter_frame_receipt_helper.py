"""Bounded offline proof; source freshness belongs to the current App composer."""
from __future__ import annotations

import copy
import json

import pytest

from genshin_app.equipment import counter_frame_receipt as proof
from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_engine import mechanics
from tests.test_counter_self_frame_app import self_plan
from tests.test_mechanics_execution import action
from tests.test_resource_counter_view_sources import view_plan
from tests.test_shared_pool_app_delivery import authorize


def attach(value):
    receipt = value['result_receipt']
    item = proof.make_counter_frame_receipt(value['plan'], value['report'], value['result'])
    if item is not None:
        receipt['counter_self_frames'] = item
        receipt['result_fingerprint'] = fingerprint(value['result'])
    return value


def calculated(tmp_path, plan=None):
    db = tmp_path / 'not-created.db'
    native = plan or self_plan()
    value = run_plan_calc(authorize(native, db), db)
    if native['mechanics_model']['resource_counter_views']['schema_version'] == 2:
        assert 'counter_self_frames' in value['result_receipt'], 'producer must attach its own proof'
        assert 'counter_frame_execution' in value['result']
    return value


def validate(value, *, full=False):
    if full:
        return proof.validate_counter_frame_receipt(value['plan'], value['result'], value['report'], value['result_receipt'])
    return proof.validate_counter_frame_result_receipt(value['result'], value['result_receipt'])


def rehash(value):
    result, receipt = value['result'], value['result_receipt']
    receipt['counter_self_frames'] = proof._proof(result, receipt)
    receipt['result_fingerprint'] = fingerprint(result)


def test_historical_saved_input_replays_once_with_complete_source_report(tmp_path, monkeypatch):
    value = calculated(tmp_path)
    before = copy.deepcopy(value)
    original, calls = mechanics.execute_mechanics, []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(mechanics, 'execute_mechanics', counted)
    validate(value, full=True)
    assert len(calls) == 1 and value == before
    assert value['result_receipt']['counter_self_frames']['verification_scope'] == 'saved_input_replay_not_current_database_approval'


@pytest.mark.parametrize('channel', ['current', 'provider', 'excluded'])
def test_schema2_proof_needed_without_actual_historical_members(tmp_path, channel):
    plan = self_plan(captured=False)
    if channel == 'provider':
        plan['mechanics_model']['rules'][1]['effects'][0].update(domain='provider')
        plan['mechanics_model']['rules'][1]['effects'][0]['value']['sample'] = 'current'
    if channel == 'excluded':
        plan['effect_bindings'][0]['parts'][1].update(state='excluded', reason='engineering excluded')
    value = calculated(tmp_path, plan)
    assert 'counter_self_frames' in value['result_receipt']
    validate(value, full=True)


@pytest.mark.parametrize('change', ['history', 'context', 'raw', 'frozen_layers', 'gate', 'expression', 'hits'])
def test_all_ordinary_hashes_recomputed_cannot_authorize_changed_execution(tmp_path, change):
    value = calculated(tmp_path)
    result = value['result']
    row = result['resource_counter_views']['trace'][0]
    if change == 'history': result['resource_counter_views'].pop('historical_capture')
    elif change == 'context': row['capture_context']['event_id'] = 'forged'
    elif change == 'raw': row['reading']['raw_atoms'] += 1
    elif change == 'frozen_layers': row['layer_count'] += 1
    elif change == 'gate': row['gate_qualification']['kind'] = 'forged'
    elif change == 'expression': row['expression']['factor'] = 99
    else: result['hits'][0]['expected'] += 1
    rehash(value)
    with pytest.raises(ValueError): validate(value)


@pytest.mark.parametrize('remaining', ['history', 'sampling', 'execution', 'native', 'report', 'proof'])
def test_partial_stripping_cannot_hide_all_residual_declarations(tmp_path, remaining):
    value = calculated(tmp_path)
    result = value['result']
    old = copy.deepcopy(value)
    result.pop('counter_frame_execution')
    result.pop('counter_frame_source_adoptions')
    result.pop('resource_counter_views')
    result['sampling_trace'] = []
    result['mechanics_trace'] = []
    value['result_receipt'].pop('counter_self_frames')
    if remaining == 'history': result['resource_counter_views'] = {'historical_capture': old['result']['resource_counter_views']['historical_capture']}
    elif remaining == 'sampling': result['sampling_trace'] = old['result']['sampling_trace']
    elif remaining == 'execution': result['counter_frame_execution'] = old['result']['counter_frame_execution']
    elif remaining == 'proof': value['result_receipt']['counter_self_frames'] = old['result_receipt']['counter_self_frames']
    value['result_receipt']['result_fingerprint'] = fingerprint(result)
    with pytest.raises(ValueError): validate(value, full=remaining in ('native', 'report'))


def test_failed_later_action_preserves_actual_prefix_and_unknown_dps(tmp_path):
    plan = self_plan()
    rotation = plan['rotation_model']
    rotation['actions'][0]['repeat'] = {'mode': 'once'}
    rotation['abilities'][0]['cooldown'] = {'duration': 10, 'charges': 1, 'group': 'cd', 'recovery': 'parallel', 'start': 'action_start'}
    rotation['initial_cooldowns'] = [{'character': 'a', 'group': 'cd', 'ready_in': []}]
    rotation['actions'].append(action(aid='late', start=.5, end=.6, repeat={'mode': 'once'}))
    value = calculated(tmp_path, plan)
    assert value['result']['mechanics_status'] == 'failed'
    assert value['result']['summary']['dps'] is None
    assert value['result']['mechanics']['complete_first_cycle'] is False
    validate(value, full=True)


def test_budget_includes_proof_and_fails_before_material_is_published(tmp_path, monkeypatch):
    from genshin_app.equipment.replay_numeric_projection import numeric_budget_result
    value = calculated(tmp_path)
    result = value['result']
    material = [result['counter_frame_execution'], numeric_budget_result({key: result.get(key) for key in proof.SECTIONS}),
                result.get('rotation_check'), result['counter_frame_source_adoptions'], value['result_receipt']['counter_self_frames']]
    limit = len(json.dumps(material, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf8'))
    monkeypatch.setattr(proof, 'MAX_EVIDENCE_BYTES', limit)
    validate(value)
    monkeypatch.setattr(proof, 'MAX_EVIDENCE_BYTES', limit-1)
    raw = copy.deepcopy(value)
    raw['result'].pop('counter_frame_execution')
    before = copy.deepcopy(raw)
    with pytest.raises(PlanError) as caught: attach(raw)
    assert raw == before
    assert caught.value.reason == 'counter_frame_proof_pending'
    assert caught.value.details['fresh'] is False
    assert caught.value.details['actual_result'] is raw['result']
    monkeypatch.setattr(mechanics, 'execute_mechanics', lambda *a, **k: pytest.fail('over-budget replay'))
    with pytest.raises(ValueError, match='8MiB'): validate(value)


def test_old_v1_adds_nothing_and_never_replays(tmp_path, monkeypatch):
    value = calculated(tmp_path, view_plan())
    assert 'counter_frame_execution' not in value['result']
    assert 'counter_self_frames' not in value['result_receipt']
    monkeypatch.setattr(mechanics, 'execute_mechanics', lambda *a, **k: pytest.fail('old v1 replay'))
    validate(value, full=True)


@pytest.mark.parametrize('part', ['native', 'final_model', 'template', 'adoptions'])
def test_full_package_binds_current_native_and_final_source_graph(tmp_path, part):
    value = calculated(tmp_path)
    if part == 'native': value['plan']['mechanics_model']['rules'][1]['effects'][0]['value']['factor'] = 99
    elif part == 'final_model': value['report']['mechanics_model']['rules'][1]['effects'][0]['value']['factor'] = 99
    elif part == 'template': value['report']['mechanics_input_template']['hits'][0]['multiplier'] = 99
    else: value['report']['resource_counter_view_adoptions'][0]['status'] = 'excluded'
    with pytest.raises(ValueError): validate(value, full=True)
