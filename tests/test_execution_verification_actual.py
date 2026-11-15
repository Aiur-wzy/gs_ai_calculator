"""Actual one-operation replay counts; source checks and output immutability remain."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import zipfile
from datetime import datetime

import pytest

from genshin_app.calculation_review import review_calculation
from genshin_app.equipment import run_plan_calc, service
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result
from genshin_app.formula_workbook import build_formula_workbook
from genshin_engine import mechanics
from tests.test_attribute_stat_app import adopt, stat_plan
from tests.test_counter_self_frame_app import self_plan
from tests.test_plan_mechanics import native
from tests.test_resource_reaction_app import reaction_plan
from tests.test_shared_pool_app_delivery import authorize


class FrozenDate:
    @staticmethod
    def now(tz):
        return datetime(2026, 10, 6, 1, 2, 3, tzinfo=tz)


def make(kind):
    if kind.startswith('reaction'):
        value = reaction_plan()
    else:
        value = self_plan(captured=kind == 'counter') if kind in ('counter', 'combined') else native()
    if kind in ('snapshot', 'combined', 'reaction-snapshot'):
        model = value['mechanics_model']
        action = value['rotation_model']['actions'][0]['id']
        model['snapshot_captures'] = [{'id': 'measured-capture', 'action_id': action, 'offset': .05,
            'precision': .000001, 'status': 'assumed', 'source': 'Synthetic validation cost probe'}]
        for descriptor in model['hit_events']:
            descriptor['self_snapshot_event'] = 'measured-capture'
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def workbook(content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return {name: hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()}


def exercise(plan, db, kind, tmp_path, monkeypatch):
    calls = []
    actual = mechanics.execute_mechanics
    monkeypatch.setattr(service, 'datetime', FrozenDate)
    def counted(template, rotation_model, mechanics_model, matches=None, **kwargs):
        calls.append(digest({'template': template, 'rotation_model': rotation_model,
                            'mechanics_model': mechanics_model, 'matches': matches}))
        return actual(template, rotation_model, mechanics_model, matches, **kwargs)
    monkeypatch.setattr(mechanics, 'execute_mechanics', counted)
    result = run_plan_calc(plan, db, capture_display=True)
    evidence = {'calculation': len(calls), 'inputs': list(calls), 'delivery': {}}
    before = copy.deepcopy(result)
    cases = {
        'query': lambda: review_calculation(result, {'view': 'hits'}),
        'saved_query': lambda: review_calculation(
            {'result': result['result'], 'result_receipt': result['result_receipt']},
            {'view': 'hits'}),
        'json': lambda: json.loads(result_bytes(result['result'], result['result_receipt'], 'json')),
        'equipment_xlsx': lambda: workbook(result_bytes(result['result'], result['result_receipt'], 'xlsx')),
        'plain_xlsx': lambda: workbook(export_result(result['result'], 'xlsx', tmp_path/'plain.xlsx',
                                          receipt=result['result_receipt']).read_bytes()),
        'formula_xlsx': lambda: workbook(build_formula_workbook(result)),
    }
    delivered = {}
    for name, operation in cases.items():
        calls.clear()
        delivered[name] = operation()
        evidence['delivery'][name] = len(calls)
        evidence['inputs'].extend(calls)
        assert result == before
    assert all(delivered.values())
    assert delivered['query']['validation'] == 'full_calculation_package'
    assert delivered['saved_query']['validation'] == 'saved_result_fingerprint'
    expected_calc = 1 if kind in ('plain', 'attribute') else 2
    assert evidence['calculation'] == expected_calc
    for name, count in evidence['delivery'].items():
        # Full review verifies snapshot context; explicit saved scope retains
        # its prior counter/resource replay guarantee without downgrading full input.
        expected = int(kind not in ('plain', 'attribute') and (name != 'saved_query'
                       or kind in ('counter', 'combined', 'reaction', 'reaction-snapshot')))
        assert count == expected, (name, count, expected)
    assert len(set(evidence['inputs'])) == 1


@pytest.mark.parametrize('kind', ['plain', 'counter', 'snapshot', 'combined', 'attribute', 'reaction', 'reaction-snapshot'])
def test_actual_manual_entries(tmp_path, monkeypatch, kind):
    db = tmp_path/'absent.db'
    plan = adopt(stat_plan(percent=True), db) if kind == 'attribute' else authorize(make(kind), db)
    exercise(plan, db, kind, tmp_path, monkeypatch)
