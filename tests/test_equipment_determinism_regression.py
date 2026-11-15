"""Equipment map order is not gameplay order or a receipt identity input."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from genshin_app.equipment import (
    PlanError,
    compose_equipment_plan,
    new_plan,
    run_plan_calc,
)
from genshin_app.equipment.delivery import build_calculation_delivery
from genshin_app.equipment.files import fingerprint, input_view, json_bytes
from tests.test_equipment_plan import VERSION
from tests.test_equipment_plan import database as database  # noqa: PLC0414


def _plan():
    plan = new_plan()
    plan['plan_id'] = '829be844-a99b-4e4f-8d47-22d0a7a69b20'
    ids = ['角色乙', 'z', 'α']  # Explicit roster order, deliberately not lexical.
    plan['manual_template'].update(
        loop_duration=3,
        characters=[{'id': cid, 'level': 90, 'stats': {
            'atk': 1000 + i * 100, 'crit_rate': .5, 'crit_dmg': 1,
            'base_atk': 400, 'base_hp': 10000, 'base_def': 500,
            'hp': 15000, 'def': 700, 'em': 0, 'energy_recharge': 1,
            'dmg_bonus': {'pyro': .2, 'physical': .1}}} for i, cid in enumerate(ids)],
        hits=[{'id': 'hit.z', 't': 1, 'character': ids[0], 'skill': 'synthetic',
               'damage_type_ids': ['damage.elemental_skill'], 'multiplier': 1,
               'damage_system': 'classic', 'element': 'physical'},
              {'id': 'hit.a', 't': 1, 'character': ids[1], 'skill': 'synthetic',
               'damage_type_ids': ['damage.elemental_skill'], 'multiplier': 2,
               'damage_system': 'classic', 'element': 'physical'}])
    plan['manual_template']['meta']['assumptions'] = ['authored z', 'authored a']
    plan['equipment_inputs'] = {cid: {
        'mode': 'panel', 'panel_policy': 'static_included', 'character_ref': None,
        'weapon': None, 'artifact_sets': [], 'data_version': copy.deepcopy(VERSION),
        'source_refs': [{'kind': 'manual', 'note': 'synthetic supplied panel'}]}
        for cid in reversed(ids)}
    return plan


def _reverse_maps(value):
    if isinstance(value, dict):
        return {k: _reverse_maps(v) for k, v in reversed(list(value.items()))}
    if isinstance(value, list):
        return [_reverse_maps(v) for v in value]
    return value


def _identity(calculated):
    receipt = calculated['result_receipt']
    return {key: receipt[key] for key in (
        'input_fingerprint', 'output_fingerprint', 'source_fingerprint',
        'result_fingerprint', 'assumptions', 'data_versions')}


@pytest.mark.parametrize('permutation', ['equipment', 'all_maps', 'sorted_json'])
def test_equipment_map_permutations_have_one_calculation_identity(database, permutation):
    db, _ = database
    plan = _plan()
    before = copy.deepcopy(plan)
    if permutation == 'equipment':
        permuted = copy.deepcopy(plan)
        permuted['equipment_inputs'] = dict(reversed(list(permuted['equipment_inputs'].items())))
    elif permutation == 'all_maps':
        permuted = _reverse_maps(plan)
    else:
        permuted = json.loads(json_bytes(plan))
    assert fingerprint(input_view(plan)) == fingerprint(input_view(permuted))
    first, second = run_plan_calc(plan, db), run_plan_calc(permuted, db)
    assert _identity(first) == _identity(second)
    assert first['result'] == second['result']
    assert plan == before  # The caller's input, including insertion order, is untouched.
    assert list(plan['equipment_inputs']) == list(before['equipment_inputs'])
    assert first['plan']['resolved_template']['characters'] == plan['manual_template']['characters']
    assert [h['id'] for h in first['plan']['resolved_template']['hits']] == ['hit.z', 'hit.a']
    assert first['result_receipt']['assumptions'][:2] == ['authored z', 'authored a']
    assert [a.split(' ')[0] for a in first['result_receipt']['assumptions'][2:]] == ['角色乙', 'z', 'α']


def test_delivered_native_fresh_recalculation_preserves_identity(database):
    db, _ = database
    calculated = run_plan_calc(_plan(), db, capture_display=True)
    built = build_calculation_delivery(calculated, input_file_sha='a' * 64)
    native = json.loads(built['artifacts']['native_plan'])
    assert native['resolution'] == {'state': 'pending'} and native['resolved_template'] is None
    reopened = run_plan_calc(native, db, capture_display=True)
    assert _identity(reopened) == _identity(calculated)
    assert reopened['result'] == calculated['result']


def test_explicit_roster_and_list_order_remain_semantic(database):
    db, _ = database
    plan = _plan()
    shuffled_roster = copy.deepcopy(plan)
    shuffled_roster['manual_template']['characters'].reverse()
    a, b = compose_equipment_plan(plan, db), compose_equipment_plan(shuffled_roster, db)
    assert [c['id'] for c in b['plan']['resolved_template']['characters']] == ['α', 'z', '角色乙']
    assert a['input_fingerprint'] != b['input_fingerprint']
    assert a['output_fingerprint'] != b['output_fingerprint']
    assert a['assumptions'][:2] == b['assumptions'][:2] == ['authored z', 'authored a']
    assert [x.split(' ')[0] for x in b['assumptions'][2:]] == ['α', 'z', '角色乙']


@pytest.mark.parametrize('key', ['missing', '不存在的角色'])
def test_extra_equipment_key_is_still_rejected_before_composition(database, key):
    db, _ = database
    plan = _plan()
    plan['equipment_inputs'][key] = copy.deepcopy(next(iter(plan['equipment_inputs'].values())))
    with pytest.raises(PlanError, match='未知方案角色'):
        compose_equipment_plan(plan, db)


def test_actor_without_managed_equipment_keeps_authored_panel(database):
    db, _ = database
    plan = _plan()
    del plan['equipment_inputs']['z']
    calculated = run_plan_calc(plan, db)
    assert [c['id'] for c in calculated['plan']['resolved_template']['characters']] == ['角色乙', 'z', 'α']
    assert calculated['plan']['resolved_template']['characters'][1] == plan['manual_template']['characters'][1]
    assert [x.split(' ')[0] for x in calculated['result_receipt']['assumptions'][2:]] == ['角色乙', 'α']


def test_independent_processes_and_hash_seeds_reproduce_receipt(database, tmp_path):
    db, _ = database
    paths = [tmp_path / 'original.json', tmp_path / 'permuted.json']
    for path, plan in zip(paths, [_plan(), _reverse_maps(_plan())], strict=True):
        path.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf8')
    worker = '''
import json,sys
from genshin_app.equipment import run_plan_calc
from genshin_db import queries
queries.get_meta=lambda _: {'datasets':{'equipment':{'lines':{'release':{
 'state':'ready','schema_version':1,'upstream_commit':'a'*40,
 'built_at':'2026-09-13T00:00:00+00:00'}}}}}
p=json.load(open(sys.argv[1],encoding='utf8'))
c=run_plan_calc(p,sys.argv[2]);r=c['result_receipt']
print(json.dumps({k:r[k] for k in ('input_fingerprint','output_fingerprint',
 'source_fingerprint','result_fingerprint','assumptions','data_versions')},ensure_ascii=True))
'''
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')}
    identities = [json.loads(subprocess.check_output(
        [sys.executable, '-c', worker, str(paths[i % 2]), str(db)],
        env={**env, 'PYTHONHASHSEED': str(i)}, text=True)) for i in range(3)]
    assert identities[0] == identities[1] == identities[2]
