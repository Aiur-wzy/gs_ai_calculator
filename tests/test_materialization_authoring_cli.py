"""External authoring discovers exactly the native self-contained envelope."""
import copy
import json
import uuid

import pytest

from genshin_app.cli import main
from genshin_app.equipment.agent_execution_materialization import (
    agent_execution_materialization_capabilities,
    lint_agent_execution_materialization,
    new_agent_execution_materialization,
    validate_agent_execution_materialization,
)
from genshin_app.equipment.files import json_bytes
from tests.test_agent_execution_materialization import _runtime_base, _runtime_topology


def source_plan(tmp_path):
    plan = _runtime_base()[0]
    plan['plan_id'] = str(uuid.uuid4())
    plan['extensions']['event_topology'] = _runtime_topology()
    source = tmp_path / 'native.plan.json'
    source.write_bytes(json_bytes(plan))
    return source, plan


def test_public_schema_covers_real_envelope_and_exact_common_fields():
    caps = agent_execution_materialization_capabilities()
    schema = caps['input_schema']
    document = new_agent_execution_materialization(_runtime_base()[0], _runtime_topology(), 'draft')
    assert set(schema['required']) == set(document)
    assert set(schema['properties']['sources']['items']['required']) == {
        'source_id', 'kind', 'reference', 'fingerprint', 'status', 'note'}
    assert set(schema['properties']['entries']['items']['required']) == {
        'entry_id', 'family', 'handler_version', 'node_ids', 'source_ids', 'coverage_keys', 'payload', 'note'}
    assert caps['authoring']['authority'].startswith('technical binding only')
    validate_agent_execution_materialization(document)


def test_cli_capabilities_exposes_existing_aggregate_panel_request(capsys):
    from genshin_app.equipment.panel_reference import panel_reference_contract
    assert main(['capabilities']) == 0
    contract = json.loads(capsys.readouterr().out)['data']['plan_contract']['panel_reference_contract']
    assert contract == panel_reference_contract()
    assert contract['authority'] == 'advisory_only'
    assert {'db_character_id', 'level'} <= set(contract['request_schema']['required'])
    assert {'artifacts', 'substats', 'weapon'} <= set(contract['request_schema']['properties'])


def test_cli_scaffold_then_validation_uses_native_lint_without_database(tmp_path, capsys):
    source, plan = source_plan(tmp_path)
    before = source.read_bytes()
    base = ['--data-dir', str(tmp_path), 'equipment', 'materialization', '--plan', str(source)]
    assert main(base) == 0
    response = json.loads(capsys.readouterr().out)['data']
    document = response['document']
    validate_agent_execution_materialization(document)
    assert response['validation'] == lint_agent_execution_materialization(
        plan, plan['extensions']['event_topology'], document)
    assert response['plan_modified'] is response['database_access'] is response['calculation_executed'] is False
    request, output = tmp_path / 'document.json', tmp_path / 'validation.json'
    request.write_bytes(json_bytes(document))
    assert main(base + ['--input', str(request), '--output', str(output)]) == 0
    validated = json.loads(capsys.readouterr().out)['data']
    assert validated['operation'] == 'validate'
    assert validated['validation'] == response['validation']
    assert source.read_bytes() == before and not (tmp_path / 'genshin.db').exists()


@pytest.mark.parametrize('role', ['plan', 'document', 'db'])
def test_scaffold_publication_cannot_overwrite_inputs_or_database(tmp_path, capsys, role):
    source, plan = source_plan(tmp_path)
    document = tmp_path / 'document.json'
    document.write_bytes(json_bytes(new_agent_execution_materialization(
        plan, plan['extensions']['event_topology'], 'draft')))
    target = {'plan': source, 'document': document, 'db': tmp_path / 'genshin.db'}[role]
    before = source.read_bytes(), document.read_bytes()
    assert main(['--data-dir', str(tmp_path), 'equipment', 'materialization', '--plan', str(source),
        '--input', str(document), '--output', str(target), '--overwrite']) == 1
    assert json.loads(capsys.readouterr().err)['reason'] == 'file_conflict'
    assert before == (source.read_bytes(), document.read_bytes())
    assert not (tmp_path / 'genshin.db').exists()


def test_validation_rejects_stale_bound_input_without_rebinding_it(tmp_path, capsys):
    source, plan = source_plan(tmp_path)
    document = new_agent_execution_materialization(plan, plan['extensions']['event_topology'], 'draft')
    request = tmp_path / 'document.json'
    request.write_bytes(json_bytes(document))
    altered = copy.deepcopy(plan)
    altered['manual_template']['characters'][0]['stats']['atk'] += 1
    source.write_bytes(json_bytes(altered))
    assert main(['equipment', 'materialization', '--plan', str(source), '--input', str(request)]) == 0
    out = json.loads(capsys.readouterr().out)['data']
    assert out['validation']['status'] != 'ready'
    assert 'agent_materialization_plan_fingerprint_stale' in {
        issue['code'] for issue in out['validation']['issues']}
    assert request.read_bytes() == json_bytes(document)


def test_topology_scaffold_has_actual_current_action_checkpoint_and_full_schema(tmp_path, capsys):
    from genshin_app.equipment.service import plan_capabilities
    source, plan = source_plan(tmp_path)
    plan['extensions'] = {}
    source.write_bytes(json_bytes(plan))
    assert main(['equipment', 'topology', '--plan', str(source)]) == 0
    response = json.loads(capsys.readouterr().out)['data']
    assert response['document']['nodes'] == []
    assert response['current_action_checkpoint']['action_ids'] == ['action.skill']
    assert response['action_checkpoint_matches'] is True
    assert response['context_status'] == 'current'
    assert response['document']['authority'] == 'agent_declared_non_executable'
    from genshin_app.equipment.event_topology_binding import new_event_topology_bindings
    assert response['execution_bindings_scaffold'] == new_event_topology_bindings(response['document'])
    contracts = plan_capabilities()['event_topology_contracts']
    assert 'topology_schema' in contracts and 'execution_bindings_schema' in contracts
    assert 'component_contracts' in contracts['component_capabilities']
    assert contracts['binding_capabilities']['contract_id'] == 'event-topology-execution-bindings-v1'
    assert json.loads(source.read_text()) == plan


def test_topology_validate_reports_stale_action_context_and_malformed_document(tmp_path, capsys):
    source, plan = source_plan(tmp_path)
    base = ['equipment', 'topology', '--plan', str(source)]
    assert main(base) == 0
    document = json.loads(capsys.readouterr().out)['data']['document']
    request = tmp_path / 'topology.json'
    request.write_bytes(json_bytes(document))
    plan['rotation_model']['actions'][0]['start'] = .05
    source.write_bytes(json_bytes(plan))
    assert main(base + ['--input', str(request)]) == 0
    response = json.loads(capsys.readouterr().out)['data']
    assert response['action_checkpoint_matches'] is False and response['context_status'] == 'stale'
    request.write_bytes(b'[]')
    assert main(base + ['--input', str(request)]) == 0
    response = json.loads(capsys.readouterr().out)['data']
    assert response['validation']['status'] == 'invalid'
    assert response['action_checkpoint_matches'] is False
