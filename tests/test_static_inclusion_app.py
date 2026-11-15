"""Actual read-only DB admission plus isolated final graph/receipt counterexamples."""
import copy

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_db import queries
from tests.static_inclusion_fixtures import DB, snapshot_session  # noqa: F401
from tests.test_equipment_panel import panel_plan


@pytest.fixture(scope='module')
def version():
    row = queries.get_meta(DB)['datasets']['equipment']['lines']['release']
    return {'version_line': 'release', 'upstream_commit': row['upstream_commit'],
            'equipment_schema_version': row['schema_version'], 'built_at': row['built_at']}


def static_plan(version, set_id=10007, count=2):
    plan = panel_plan()
    gear = plan['equipment_inputs']['char1']
    gear.update(data_version=copy.deepcopy(version), artifact_sets=[{'set_id': set_id, 'count': count}])
    plan['manual_template']['characters'][0]['stats']['em'] = 80
    return plan


def test_actual_static_source_admits_without_numeric_delivery(version):
    plan = static_plan(version); before = copy.deepcopy(plan)
    out = run_plan_calc(plan, DB)
    records = out['report']['static_inclusion_compilations']
    assert len(records) == 1
    record = records[0]
    assert record['status'] == 'ready' and record['source_authority'] == 'fresh_database_admission'
    assert record['declaration']['properties'][0]['value'] == 80
    assert record['declaration']['numeric_application'] is False
    assert not out['plan']['effect_bindings'] and not out['plan']['resolved_template'].get('buffs', [])
    assert out['plan']['resolved_template']['characters'] == before['manual_template']['characters']
    assert out['report']['effect_requirements'][0]['resolved_by']['id'] == record['id']
    assert out['result']['static_inclusion_compilations'] == records
    assert out['result_receipt']['static_inclusions']['verification_scope'] == 'saved_static_inclusion_consistency_no_requery'
    assert plan == before


def test_only_exact_two_piece_issue_is_removed(version):
    report = compose_equipment_plan(static_plan(version, count=4), DB)
    assert report['state'] == 'pending'
    pending = [i for i in report['missing_inputs'] if i['code'] == 'effect_pending']
    assert len(pending) == 1
    req = next(r for r in report['effect_requirements'] if r['owner']['required_count'] == 4)
    assert pending[0]['binding_id'] == req['binding_id'] and 'resolved_by' not in req
    assert report['static_inclusion_compilations'][0]['status'] == 'ready'


@pytest.mark.parametrize('state', ['pending', 'excluded', 'resolved'])
def test_explicit_owner_binding_retains_legacy_decision(version, state):
    plan = static_plan(version); req = compose_equipment_plan(plan, DB)['effect_requirements'][0]
    binding = copy.deepcopy(req['proposed_binding'])
    binding['parts'] = [{'part_id': 'explicit', 'label': '明确人工意图', 'state': state,
        'rule': None, 'delivery': None, 'conditions': [],
        'dependencies': {'mode': 'source_only', 'context_fingerprint': None, 'note': '用户明确选择'},
        'reason': '用户明确保留'}]
    if state == 'resolved':
        binding['parts'][0].update(rule={'kind': 'constant', 'value': 80},
            delivery={'kind': 'panel', 'target_character_id': 'char1', 'stat': 'em', 'operation': 'flat'})
    plan['effect_bindings'] = [binding]; before = copy.deepcopy(plan)
    report = compose_equipment_plan(plan, DB)
    assert 'static_inclusion_compilations' not in report
    assert report['plan']['effect_bindings'] == before['effect_bindings']
    assert report['state'] == 'pending' and plan == before


def test_ratio_source_needs_no_base_and_does_not_apply_ratio_twice(version):
    plan = static_plan(version, set_id=10001)
    out = run_plan_calc(plan, DB)
    prop = out['result']['static_inclusion_compilations'][0]['declaration']['properties'][0]
    assert prop['unit'] == 'ratio' and prop['stat'] == 'atk' and prop['value'] == pytest.approx(.18)
    assert out['plan']['resolved_template']['characters'][0]['stats']['atk'] == 1800
    assert 'base_atk' not in out['plan']['resolved_template']['characters'][0]['stats']


def test_provider_isolation_and_same_stat_independent_sources(version):
    plan = static_plan(version, set_id=10001)
    other = copy.deepcopy(plan['manual_template']['characters'][0]); other['id'] = 'char2'
    plan['manual_template']['characters'].append(other)
    plan['equipment_inputs']['char2'] = copy.deepcopy(plan['equipment_inputs']['char1'])
    plan['equipment_inputs']['char1']['artifact_sets'].append({'set_id': 15001, 'count': 2})
    report = compose_equipment_plan(plan, DB)
    records = report['static_inclusion_compilations']
    assert len(records) == 3 and all(r['status'] == 'ready' for r in records), report['missing_inputs']
    assert len({r['id'] for r in records}) == 3


def test_changed_equipment_version_cannot_be_healed_by_static_inclusion(version):
    plan = static_plan(version)
    plan['equipment_inputs']['char1']['data_version']['built_at'] = 'stale'
    report = compose_equipment_plan(plan, DB)
    assert report['state'] == 'pending'
    assert any(i['code'] == 'source_changed' for i in report['missing_inputs'])
    assert report['static_inclusion_compilations'][0]['status'] == 'pending'
    assert all('resolved_by' not in r for r in report['effect_requirements'])


def test_saved_caller_declaration_does_not_replace_fresh_db_read(version, monkeypatch):
    from genshin_db import runtime_queries
    plan = static_plan(version); out = run_plan_calc(plan, DB)
    old = out['plan']; old['resolution']['static_inclusion_compilations'][0]['declaration']['properties'][0]['value'] = 9000
    calls = []
    actual = runtime_queries.assemble_runtime_source
    def read(*args, **kwargs):
        calls.append(args)
        return actual(*args, **kwargs)
    monkeypatch.setattr(runtime_queries, 'assemble_runtime_source', read)
    fresh = run_plan_calc(old, DB)
    assert calls and fresh['result']['static_inclusion_compilations'][0]['declaration']['properties'][0]['value'] == 80


def test_raw_unknown_field_is_pending_even_with_rehashed_source_bundle(version, monkeypatch):
    from genshin_db import runtime_queries
    from genshin_db.runtime_catalog import fingerprint
    actual = runtime_queries.assemble_runtime_source
    def read(*args, **kwargs):
        value = copy.deepcopy(actual(*args, **kwargs))
        value['affix']['record']['unknownCondition'] = False
        value['content_fingerprint'] = fingerprint({k: v for k, v in value.items() if k != 'content_fingerprint'})
        return value
    monkeypatch.setattr(runtime_queries, 'assemble_runtime_source', read)
    report = compose_equipment_plan(static_plan(version), DB)
    assert report['state'] == 'pending'
    assert report['static_inclusion_compilations'][0]['status'] == 'pending'
    assert any(i['code'] == 'effect_pending' for i in report['missing_inputs'])


@pytest.fixture(scope='module')
def calculated(version):
    return run_plan_calc(static_plan(version), DB)


def test_static_receipt_is_sealed_after_late_execution_evidence(version, monkeypatch):
    """Counter/resource receipt builders may publish result evidence late."""
    from genshin_app.equipment import counter_frame_receipt
    from genshin_app.equipment.static_inclusion_receipt import (
        StaticInclusionMismatch,
        make_static_inclusion_receipt,
        validate_static_inclusion_receipt,
    )
    original = counter_frame_receipt.make_counter_frame_receipt
    early = []
    def append_evidence(plan, report, result):
        early.append(make_static_inclusion_receipt(plan, report, result))
        proof = original(plan, report, result)
        result['synthetic_late_execution_evidence'] = {'contract': 'engineering-late-evidence-v1', 'value': 1}
        return proof
    monkeypatch.setattr(counter_frame_receipt, 'make_counter_frame_receipt', append_evidence)
    out = run_plan_calc(static_plan(version), DB)
    validate_static_inclusion_receipt(out['plan'], out['result'], out['report'], out['result_receipt'])
    stale = copy.deepcopy(out['result_receipt'])
    stale['static_inclusions'] = early[0]
    with pytest.raises(StaticInclusionMismatch) as exc:
        validate_static_inclusion_receipt(out['plan'], out['result'], out['report'], stale)
    assert exc.value.details['path'] == '/result_receipt/static_inclusions/calculation_fingerprint'
    assert exc.value.details['phase'] == 'sealed_calculation'


@pytest.mark.parametrize('field', ['atk', 'em'])
def test_receipt_binds_native_panel_even_when_saved_resolved_output_is_unchanged(calculated, field):
    from genshin_app.equipment.static_inclusion_receipt import (
        validate_static_inclusion_receipt,
    )
    out = copy.deepcopy(calculated)
    out['plan']['manual_template']['characters'][0]['stats'][field] += 1
    with pytest.raises(ValueError):
        validate_static_inclusion_receipt(out['plan'], out['result'], out['report'], out['result_receipt'])


@pytest.mark.parametrize('field', ['records', 'adoption', 'requirement', 'source_display', 'graph', 'matches', 'scope'])
def test_full_receipt_rejects_separate_report_or_proof_mutation(calculated, field):
    from genshin_app.equipment.static_inclusion_receipt import (
        validate_static_inclusion_receipt,
    )
    out = copy.deepcopy(calculated)
    if field == 'records': out['report']['static_inclusion_compilations'][0]['declaration']['properties'][0]['value'] = 99
    if field == 'adoption': out['result']['static_inclusion_adoption']['adoptions'] = []
    if field == 'requirement': out['report']['effect_requirements'][0].pop('resolved_by')
    if field == 'source_display': out['result']['static_inclusion_compilations'][0]['source_display']['name'] = '伪改'
    if field == 'graph': out['report']['mechanics_model'] = {'rules': []}
    if field == 'matches': out['report']['mechanics_matches'] = [{'unexpected': True}]
    if field == 'scope': out['result_receipt']['static_inclusions']['verification_scope'] = 'fresh_source_verified_offline'
    with pytest.raises(ValueError):
        validate_static_inclusion_receipt(out['plan'], out['result'], out['report'], out['result_receipt'])


@pytest.mark.parametrize('field', ['records', 'marker', 'proof', 'hits', 'summary'])
def test_result_only_rejects_local_mutation_even_after_outer_result_rehash(calculated, field):
    from genshin_app.equipment.files import fingerprint
    from genshin_app.equipment.static_inclusion_receipt import (
        validate_static_inclusion_result_receipt,
    )
    out = copy.deepcopy(calculated)
    if field == 'records': out['result'].pop('static_inclusion_compilations')
    if field == 'marker': out['result'].pop('static_inclusion_adoption')
    if field == 'proof': out['result_receipt'].pop('static_inclusions')
    if field == 'hits': out['result']['hits'][0]['expected'] += 1
    if field == 'summary': out['result']['summary']['total_damage'] += 1
    out['result_receipt']['result_fingerprint'] = fingerprint(out['result'])
    with pytest.raises(ValueError):
        validate_static_inclusion_result_receipt(out['result'], out['result_receipt'])


def test_saved_pure_material_rehash_has_no_route_into_admission(calculated):
    from genshin_app.equipment.static_inclusion_sources import (
        finalize_static_inclusions,
    )
    with pytest.raises(ValueError):
        finalize_static_inclusions(calculated['result']['static_inclusion_compilations'], {}, {}, None, [], [], '0' * 64,
                                   contribution_report={})


def test_unknown_nonextra_is_not_derived_from_static_em(calculated):
    record = calculated['result']['static_inclusion_compilations'][0]
    assert record['declaration']['nonextra_authority'] == 'not_inferred'
    assert 'attribute_model' not in calculated['plan']
    assert 'attribute_ledger' not in calculated['result']
