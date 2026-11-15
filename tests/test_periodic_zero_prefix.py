"""A bounded periodic input can legitimately deliver no in-window events."""
from __future__ import annotations

import copy

import pytest
from test_agent_execution_materialization import (
    _add_runtime_action_anchor,
    _periodic_payload,
    _runtime_base,
    _runtime_document,
    _runtime_topology,
)

from genshin_app.equipment.agent_execution_materialization import (
    AgentExecutionMaterializationError,
    materialize_agent_execution,
    new_agent_execution_materialization,
)
from genshin_app.equipment.agent_execution_runtime import adapt_agent_execution_runtime


def inputs(*, children=False):
    plan, template, rotation, mechanics = _runtime_base()
    topology = _runtime_topology(periodic=True)
    periodic = next(row for row in topology['nodes'] if row['node_id'] == 'node.periodic')
    periodic['schedule'].update(first_offset_seconds=20, interval_seconds=1,
                                count=3, duration_seconds=22)
    document = _runtime_document(plan, topology, periodic=True)
    document['entries'][0]['payload'] = _periodic_payload(first=20, interval=1, count=3, duration=22)
    if children:
        other_plan = copy.deepcopy(plan)
        other_topology = _runtime_topology()
        other = _runtime_document(other_plan, other_topology)
        for name in ('application', 'resource'):
            node = copy.deepcopy(next(row for row in other_topology['nodes'] if row['kind'] == name))
            node['schedule']['anchor']['ref_id'] = 'node.periodic'
            topology['nodes'].append(node)
            topology['sources'][0]['node_ids'].append(node['node_id'])
            topology['edges'].append({'edge_id': 'periodic-' + name, 'kind': 'contains',
                'from_node_id': 'node.periodic', 'to_node_id': node['node_id'],
                'source_ids': ['source.skill'], 'status': 'bound', 'note': ''})
            document['entries'].append(copy.deepcopy(next(row for row in other['entries'] if row['family'] == name)))
            document['entries'][0]['payload']['dispatches'][0]['emits_node_ids'].append(node['node_id'])
        from genshin_app.equipment.files import fingerprint
        document['topology_binding']['topology_fingerprint'] = fingerprint(topology)
        plan['extensions']['event_topology_bindings']['topology_fingerprint'] = fingerprint(topology)
        document['plan_binding'] = new_agent_execution_materialization(
            plan, topology, document['materialization_id'])['plan_binding']
    return plan, template, rotation, mechanics, topology, document


def adapt(case):
    plan, template, rotation, mechanics, topology, document = case
    effective = materialize_agent_execution(plan, topology, document)
    return adapt_agent_execution_runtime(plan, topology, effective, template, rotation, mechanics)


def test_future_first_pulse_is_complete_finite_scope_without_fabricated_hit():
    case = list(inputs())
    other = copy.deepcopy(case[1]['hits'][0])
    other['id'] = 'another-prototype'
    case[1]['hits'].append(other)
    out = adapt(case)
    assert out['status'] == 'complete', out['receipt']['entries']
    assert out['mechanics_model']['hit_events'] == []
    assert out['mechanics_model']['hit_patterns'] == []
    projection = out['receipt']['entries'][0]['installed'][0]
    assert projection['occurrences'] == []
    assert projection['boundary']['status'] == 'active'
    assert projection['boundary']['remaining_active_ticks'] == 12_000_000
    assert len(projection['boundary']['pending_occurrences']) == 3
    assert projection['boundary']['future_delivery_verified'] is False


def test_zero_occurrence_parent_keeps_application_and_energy_as_unexecuted_children():
    case = list(inputs(children=True))
    other = copy.deepcopy(case[1]['hits'][0])
    other['id'] = 'another-prototype'
    case[1]['hits'].append(other)
    out = adapt(case)
    assert out['status'] == 'complete', out['receipt']['entries']
    assert out['rotation_model']['events'] == []
    assert out['mechanics_model'].get('application_events', []) == []
    assert out['mechanics_model']['hit_events'] == []
    for row in out['receipt']['entries'][1:]:
        assert row['installed'][0]['delivery'] == 'dispatch_descriptor'


@pytest.mark.parametrize('missing', ['prototype', 'application', 'resource'])
def test_zero_occurrences_cannot_launder_missing_execution_targets(missing):
    case = list(inputs(children=True))
    if missing == 'prototype':
        case[1]['hits'] = []
    else:
        case[5]['entries'] = [row for row in case[5]['entries'] if row['family'] != missing]
    try:
        out = adapt(case)
    except AgentExecutionMaterializationError:
        return  # Missing typed children can already fail the authoring proof.
    assert out['status'] != 'complete'


def native_plan(*, foreground=True, automatic=False):
    from genshin_app.equipment import new_plan
    from genshin_app.equipment.mechanics import enable_mechanics
    _, template, rotation, mechanics, topology, document = inputs()
    if foreground:
        hit = copy.deepcopy(template['hits'][0])
        hit.update(id='foreground', t=.2)
        template['hits'].append(hit)
        mechanics['hit_events'].append({'hit_id': 'foreground', 'action_id': 'action.skill', 'order': 8})
        # Standalone foreground input has no elemental simulation in this
        # anonymous numerical fixture; no synthetic application is needed.
        mechanics.pop('elemental_model')
    rotation['damage_report'] = ({'contract': 'damage-report-window-v1', 'mode': 'last_active_damage',
                                'active_hit_ids': ['foreground'] if foreground else []}
                               if automatic else {'contract': 'damage-report-window-v1', 'mode': 'explicit', 'end': 10})
    plan = enable_mechanics(new_plan())
    plan.update(manual_template=template, rotation_model=rotation, mechanics_model=mechanics)
    from genshin_app.equipment.files import fingerprint
    plan.setdefault('extensions', {})['event_topology'] = topology
    from genshin_app.equipment.event_topology_binding import new_event_topology_bindings
    bindings = new_event_topology_bindings(topology)
    bindings['bindings'] = [{'binding_id': 'binding.damage', 'node_id': 'node.damage',
        'target_kind': 'hit', 'pointer': '/manual_template/hits/0',
        'target_fingerprint': fingerprint(template['hits'][0]), 'note': ''}]
    plan['extensions']['event_topology_bindings'] = bindings
    document['plan_binding'] = new_agent_execution_materialization(
        plan, topology, document['materialization_id'])['plan_binding']
    plan['extensions']['agent_execution_materialization'] = document
    return plan


def test_zero_pulse_window_is_real_calculation_and_saved_proof_but_no_child_delivery(tmp_path):
    import json

    from genshin_app.equipment import run_plan_calc
    from genshin_app.equipment.delivery import validate_delivery_calculated
    from genshin_app.equipment.files import json_bytes
    from genshin_app.equipment.service import result_bytes
    plan = native_plan()
    out = run_plan_calc(plan, tmp_path/'absent.db', capture_display=True)
    assert out['completion']['task_admission'] == 'accepted'
    assert out['completion']['steady_state'] == 'unproven'
    assert [hit['id'] for hit in out['result']['hits']] == ['foreground']
    assert out['result']['rotation_check']['resolved_rotation_model']['events'] == []
    saved = json.loads(json_bytes(out))
    validate_delivery_calculated(saved)
    assert result_bytes(saved['result'], saved['result_receipt'], 'json')
    again = run_plan_calc(copy.deepcopy(plan), tmp_path/'absent.db')
    assert again['result'] == out['result']


def test_empty_auto_report_still_requires_a_real_direct_hit_anchor(tmp_path):
    from genshin_app.equipment import run_plan_calc
    from genshin_app.equipment.schema import PlanError
    with pytest.raises(PlanError) as error:
        run_plan_calc(native_plan(foreground=False, automatic=True), tmp_path/'absent.db')
    assert '直接命中锚点' in str(error.value.details)


def test_explicit_cutoff_before_real_foreground_hit_keeps_verified_zero_report(tmp_path):
    from genshin_app.equipment import run_plan_calc
    from genshin_app.equipment.service import result_bytes
    plan = native_plan()
    plan['rotation_model']['damage_report']['end'] = .1
    topology = plan['extensions']['event_topology']
    document = plan['extensions']['agent_execution_materialization']
    document['plan_binding'] = new_agent_execution_materialization(plan, topology,
        document['materialization_id'])['plan_binding']
    out = run_plan_calc(plan, tmp_path/'absent.db')
    assert out['completion']['task_admission'] == 'accepted'
    assert out['result']['summary']['total_damage'] == 0
    assert out['result']['hits'] == []
    assert [hit['id'] for hit in out['result']['damage_report_audit_hits']] == ['foreground']
    assert result_bytes(out['result'], out['result_receipt'], 'json')


def test_zero_periodic_cannot_claim_a_prototype_already_owned_by_a_real_event():
    case = list(inputs())
    case[3]['hit_events'].append({'hit_id': 'hit.fixture', 'action_id': 'action.skill', 'order': 8})
    out = adapt(case)
    assert out['status'] == 'partial'
    assert 'collides' in out['receipt']['entries'][0]['issues'][0]['message']


def test_cancel_before_first_pulse_has_no_actual_contact_or_future_delivery():
    case = list(inputs())
    plan, template, rotation, _mechanics, topology, document = case
    template['hits'].append({**copy.deepcopy(template['hits'][0]), 'id': 'other'})
    _add_runtime_action_anchor(plan, rotation, topology, node_id='node.cancel',
                              action_id='action.cancel', start=1, relation='refreshes')
    document['entries'][0]['payload']['controls'] = [{'control_id': 'stop',
        'operation': 'cancel', 'anchor_node_id': 'node.cancel', 'order': 0,
        'duration_seconds': None}]
    from genshin_app.equipment.files import fingerprint
    document['topology_binding']['topology_fingerprint'] = fingerprint(topology)
    plan['extensions']['event_topology_bindings']['topology_fingerprint'] = fingerprint(topology)
    document['plan_binding'] = new_agent_execution_materialization(plan, topology,
        document['materialization_id'])['plan_binding']
    out = adapt(case)
    assert out['status'] == 'complete', out['receipt']['entries']
    projection = out['receipt']['entries'][0]['installed'][0]
    assert projection['occurrences'] == []
    assert projection['boundary']['status'] == 'inactive'
    assert projection['generations'][0]['status'] == 'cancelled'


@pytest.mark.parametrize('new_report', [True, False])
def test_exact_right_boundary_is_executed_with_true_origin_and_correct_report_policy(tmp_path, new_report):
    from genshin_app.equipment import run_plan_calc
    from genshin_app.equipment.files import fingerprint
    plan = native_plan()
    document = plan['extensions']['agent_execution_materialization']
    topology = plan['extensions']['event_topology']
    owner = next(row for row in topology['nodes'] if row['node_id'] == 'node.periodic')
    owner['schedule'].update(first_offset_seconds=10, interval_seconds=1,
                             count=1, duration_seconds=10)
    document['entries'][0]['payload'] = _periodic_payload(first=10, interval=1, count=1, duration=10)
    if not new_report:
        plan['rotation_model'].pop('damage_report')
    document['topology_binding']['topology_fingerprint'] = fingerprint(topology)
    plan['extensions']['event_topology_bindings']['topology_fingerprint'] = fingerprint(topology)
    document['plan_binding'] = new_agent_execution_materialization(plan, topology,
        document['materialization_id'])['plan_binding']
    out = run_plan_calc(plan, tmp_path/'absent.db')
    assert out['completion']['task_admission'] == 'accepted'
    hits = {hit['id']: hit for hit in out['result']['hits']}
    assert ('hit.fixture' in hits) is new_report
    if new_report:
        assert hits['hit.fixture']['t'] == 10
        assert hits['hit.fixture']['expected'] > 0
    actual = [row for row in out['result']['rotation_check']['event_log']
              if row['id'] == 'hit.fixture' and row.get('source_cycle') == 0]
    assert len(actual) == 1 and actual[0]['t'] == 10 and actual[0]['status'] == 'passed'


@pytest.mark.parametrize('mutation', ['delete_boundary', 'hide_future', 'inactive'])
def test_saved_zero_prefix_boundary_cannot_be_changed_by_resigning_public_hashes(tmp_path, mutation):
    from test_agent_execution_materialization import (
        _resign_visible_agent_execution_fingerprints,
    )

    from genshin_app.equipment import run_plan_calc
    from genshin_app.equipment.agent_execution_receipt import (
        validate_agent_execution_result_receipt,
    )
    from genshin_app.equipment.files import fingerprint
    out = run_plan_calc(native_plan(), tmp_path/'absent.db')
    result, receipt = out['result'], out['result_receipt']
    runtime = result['agent_execution_runtime_receipt']
    projection = runtime['entries'][0]['installed'][0]
    if mutation == 'delete_boundary':
        del projection['boundary']
    elif mutation == 'hide_future':
        projection['boundary']['pending_occurrences'] = []
    else:
        projection['boundary']['status'] = 'inactive'
    _resign_visible_agent_execution_fingerprints(out)
    proof = receipt['agent_execution_proof']
    for entry in proof['entries']:
        entry['installed_fingerprint'] = fingerprint(next(row for row in runtime['entries']
            if row['entry_id'] == entry['entry_id'])['installed'])
    proof['proof_fingerprint'] = fingerprint({key: value for key, value in proof.items()
                                             if key != 'proof_fingerprint'})
    with pytest.raises(ValueError, match='boundary|projection'):
        validate_agent_execution_result_receipt(result, receipt)
