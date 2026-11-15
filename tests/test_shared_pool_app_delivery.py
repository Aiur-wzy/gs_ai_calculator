"""Reviewed shared-resource plans survive App/Excel delivery and reject stale edits."""
import copy
import io
import json
import zipfile

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.delivery import validate_delivery_calculated
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result, shared_pool_rows
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from genshin_app.gui.api import GuiApi
from genshin_engine.buff_state_timeline import build_buff_state_timeline
from tests.test_plan_mechanics import native, review


def pool_plan():
    plan = native()
    model = plan['mechanics_model']
    model['rules'][0]['resource_gate'] = {'consumer_id': 'consume'}
    evidence = {'status': 'assumed', 'source': 'Synthetic limited damage bonus, not a game source'}
    model['shared_pools'] = {'schema_version': 1,
        'pools': [{'id': 'pool', 'name_zh': '共享增伤次数', 'owner': 'a', 'source_id': 'binding:part',
                   'scope': 'team', 'capacity': 10, 'initial': {'team': 1}, **evidence}],
        'operations': [],
        'consumers': [{'id': 'consume', 'pool_id': 'pool', 'source_id': 'binding:part',
            'filter': {'event': 'hit', 'phase': 'pre'}, 'actor_relation': 'party_member',
            'damage_system': 'classic', 'amount': 1, 'eligibility_rule_id': 'boost', **evidence}]}
    return plan


def authorize(plan, db):
    for binding in plan['effect_bindings']:
        for part in binding['parts']:
            part['dependencies'] = {'mode': 'manual_context', 'context_fingerprint': None,
                                    'note': 'Reviewed the whole synthetic resource graph and its gate'}
    review(plan, db)
    current = compose_equipment_plan(plan, db)['context_fingerprint']
    for binding in plan['effect_bindings']:
        for part in binding['parts']:
            part['dependencies']['context_fingerprint'] = current
    return plan


def test_old_source_only_review_cannot_approve_new_resource_subgraph(tmp_path):
    plan = pool_plan(); db = tmp_path / 'absent.db'
    review(plan, db)
    out = compose_equipment_plan(plan, db)
    assert out['state'] == 'pending'
    assert any(i['code'] == 'pool_source_scope_unreviewed' for i in out['missing_inputs'])
    with pytest.raises(PlanError): run_plan_calc(plan, db)


def test_context_approved_graph_runs_reopens_and_exports_readonly_resource_ledger(tmp_path):
    db = tmp_path / 'absent.db'; plan = authorize(pool_plan(), db); before = copy.deepcopy(plan)
    out = run_plan_calc(plan, db)
    assert plan == before
    assert [h['expected'] for h in out['result']['hits']] == [90, 45, 45]
    assert all(r['status'] == 'ready' for r in out['report']['shared_pool_source_adoptions'])
    assert [r['accepted'] for r in out['result']['shared_pools']['trace'] if r['cycle'] == 0] == [1, 0, 0]
    state, = out['result']['buff_state_timeline']['states']
    assert [row['status'] for row in state['slices']] == ['active', 'exhausted']
    assert [row['resource_balance']['remaining'] for row in state['slices']] == [1, 0]
    gui = GuiApi(tmp_path).run_plan_calc(json.loads(json.dumps(plan)))
    assert gui['code'] == 0, gui
    assert gui['data']['result'] == out['result']
    assert formula_workbook_support(out)['editable']['shared_pools_and_resource_gates'] == 'regenerate_required'
    rows = shared_pool_rows(out['result'])
    assert rows[3][5:9] == [1, 1, 1, 0]
    path = tmp_path / 'result.xlsx'; export_result(
        out['result'], 'xlsx', path, receipt=out['result_receipt'])
    for payload in (path.read_bytes(), result_bytes(out['result'], out['result_receipt'], 'xlsx'), build_formula_workbook(out)):
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            assert '共享资源流水' in archive.read('xl/workbook.xml').decode('utf8')
            assert archive.testzip() is None


def test_legacy_shared_pool_package_without_balance_slices_still_validates(tmp_path):
    db = tmp_path / 'absent.db'; plan = authorize(pool_plan(), db)
    out = run_plan_calc(plan, db, capture_display=True)
    result, report = out['result'], out['report']
    # The current receipt first proves the trace-aware strict-rebuild path.
    validate_delivery_calculated(out)
    # Reproduce the pre-resource-slicing receipt while retaining the authoritative
    # shared-pool ledger that older calculated packages already carried.
    result['buff_state_timeline'] = build_buff_state_timeline(
        report['mechanics_model'], result['mechanics_trace'], result['rotation_check'],
        duration=out['plan']['resolved_template']['loop_duration'],
        execution_status=result['mechanics_status'],
        template=report['mechanics_input_template'])
    assert result['shared_pools']['trace']
    assert all('resource_balance' not in row
               for state in result['buff_state_timeline']['states'] for row in state['slices'])
    out['result_receipt']['result_fingerprint'] = fingerprint(result)
    validate_delivery_calculated(out)

    # Formal 0.44 receipts predate the resource_gate field as well.  Strict
    # delivery validation must rebuild that saved shape rather than upgrading it.
    result['buff_state_timeline'] = build_buff_state_timeline(
        report['mechanics_model'], result['mechanics_trace'], result['rotation_check'],
        duration=out['plan']['resolved_template']['loop_duration'],
        execution_status=result['mechanics_status'],
        template=report['mechanics_input_template'], include_resource_gate=False)
    assert all('resource_gate' not in state
               for state in result['buff_state_timeline']['states'])
    out['result_receipt']['result_fingerprint'] = fingerprint(result)
    validate_delivery_calculated(out)


def test_resource_value_edit_invalidates_previous_context_review(tmp_path):
    db = tmp_path / 'absent.db'; plan = authorize(pool_plan(), db)
    run_plan_calc(plan, db)
    plan['mechanics_model']['shared_pools']['pools'][0]['initial']['team'] = 3
    report = compose_equipment_plan(plan, db)
    assert report['state'] == 'pending'
    assert any(i['code'] in ('dependency_stale', 'pool_source_scope_unreviewed') for i in report['missing_inputs'])
    with pytest.raises(PlanError): run_plan_calc(plan, db)


def test_explicit_source_exclusion_disables_pool_and_gate_in_execution_copy(tmp_path):
    db = tmp_path / 'absent.db'; plan = authorize(pool_plan(), db)
    part = plan['effect_bindings'][0]['parts'][0]
    part.update(state='excluded', reason='Synthetic benefit absent in this scenario')
    original = copy.deepcopy(plan)
    out = run_plan_calc(plan, db)
    assert plan == original
    assert [h['expected'] for h in out['result']['hits']] == [45, 45, 45]
    assert 'shared_pools' not in out['report']['mechanics_model']
    assert out['report']['mechanics_model']['rules'] == []
    assert all(r['status'] == 'excluded' for r in out['report']['shared_pool_source_adoptions'])
