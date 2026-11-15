"""Real capacity failures stay inspectable without acquiring a result receipt."""
import json

import pytest

from genshin_app.equipment.execution_verification import ExecutionVerificationSession
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.verification_budget import POLICY


def capacity_failure():
    with ExecutionVerificationSession(budget={'policy': POLICY, 'limits': {'work': 1}}) as session:
        try:
            session.scan_receipt_materials(inputs=({'many': [1, 2, 3]},))
        except ValueError as exc:
            return exc
    raise AssertionError('fixture must exhaust its private session')


def fail_capacity(*args, **kwargs):
    raise capacity_failure()


def cli_fixture(tmp_path, monkeypatch):
    from genshin_app import equipment
    from tests.test_calculation_delivery import delivery_plan
    source = tmp_path / 'source.json'
    source.write_text(json.dumps(delivery_plan()), encoding='utf8')
    monkeypatch.setattr('genshin_app.cli._require_runtime_database', lambda _: {})
    monkeypatch.setattr(equipment, 'run_plan_calc', fail_capacity)
    return source, ['--data-dir', str(tmp_path), 'equipment', 'calc', '--plan', str(source)]


def test_cli_saves_unsealed_typed_diagnostic_and_preserves_input(tmp_path, monkeypatch, capsys):
    from genshin_app.calculation_diagnostics import inspect_calculation_diagnostics
    from genshin_app.cli import main
    source, args = cli_fixture(tmp_path, monkeypatch)
    before = source.read_bytes()
    target = tmp_path / 'failure.json'
    assert main(args + ['--output', str(target), '--summary']) == 1
    error = json.loads(capsys.readouterr().err)
    saved = json.loads(target.read_text(encoding='utf8'))
    assert error['reason'] == 'verification_budget'
    assert error['details']['files']['published_paths'] == [str(target.resolve())]
    assert saved['source']['input_fingerprint'] == fingerprint(json.loads(before))
    assert saved['sealed'] is False and saved['accepted_result'] is False
    assert 'result_receipt' not in saved
    assert saved['diagnostic']['issues'][0]['dimension'] == 'work'
    assert source.read_bytes() == before
    for value in (saved, error):
        page = inspect_calculation_diagnostics(value)
        assert page['validation_scope'] == 'unverified_saved_verification_failure'
        assert page['execution_status'] == 'verification_incomplete'


@pytest.mark.parametrize('target', ['source.json', 'genshin.db'])
def test_cli_capacity_failure_cannot_overwrite_inputs_or_database(tmp_path, monkeypatch, capsys, target):
    from genshin_app.cli import main
    source, args = cli_fixture(tmp_path, monkeypatch)
    before = source.read_bytes()
    assert main(args + ['--output', str(tmp_path / target), '--overwrite']) == 1
    assert json.loads(capsys.readouterr().err)['reason'] == 'file_conflict'
    assert source.read_bytes() == before
    assert not (tmp_path / 'genshin.db').exists()


def test_gui_capacity_diagnostic_has_same_contract_and_no_hidden_file_writes(tmp_path, monkeypatch):
    from genshin_app import equipment
    from genshin_app.gui.ports.equipment import EquipmentGuiPort
    monkeypatch.setattr(equipment, 'run_plan_calc', fail_capacity)
    port = EquipmentGuiPort(tmp_path, tmp_path/'genshin.db', manager_provider=lambda: None)
    response = port.run_plan_calc({'plan': 'input'})
    assert response['code'] == 1 and response['reason'] == 'verification_budget'
    assert response['details']['diagnostic']['accepted_result'] is False
    assert not list(tmp_path.iterdir())


def test_wrapped_domain_error_keeps_reason_without_leaking_partial_result(tmp_path, monkeypatch):
    from genshin_app import equipment
    from genshin_app.equipment.schema import PlanError
    from genshin_app.gui.ports.equipment import EquipmentGuiPort

    def wrapped(*args, **kwargs):
        try:
            raise capacity_failure()
        except ValueError as exc:
            raise PlanError('资源证明未完成', 'resource_reaction_proof_pending',
                            {'actual_result': {'unsealed': 'large prefix'}}) from exc

    monkeypatch.setattr(equipment, 'run_plan_calc', wrapped)
    port = EquipmentGuiPort(tmp_path, tmp_path/'genshin.db', manager_provider=lambda: None)
    response = port.run_plan_calc({'plan': 'input'})
    assert response['reason'] == 'resource_reaction_proof_pending'
    assert response['details']['diagnostic']['diagnostic']['issues'][0]['code'] == 'verification_budget'
    assert 'actual_result' not in json.dumps(response)


def test_nonverification_errors_keep_existing_gui_contract(tmp_path, monkeypatch):
    from genshin_app import equipment
    from genshin_app.gui.ports.equipment import EquipmentGuiPort

    def unrelated(*args, **kwargs):
        raise ValueError('unrelated failure')

    monkeypatch.setattr(equipment, 'run_plan_calc', unrelated)
    port = EquipmentGuiPort(tmp_path, tmp_path/'genshin.db', manager_provider=lambda: None)
    response = port.run_plan_calc({})
    assert response == {'code': 1, 'message': 'run_plan_calc 失败: unrelated failure'}


@pytest.mark.parametrize('level,key,value', [('artifact', 'sealed', True),
    ('artifact', 'result_receipt', {}), ('diagnostic', 'accepted_result', True)])
def test_saved_capacity_diagnostic_cannot_be_promoted(level, key, value):
    from genshin_app.calculation_diagnostics import inspect_calculation_diagnostics
    from genshin_app.calculation_verification_failure import (
        verification_failure_artifact,
    )
    from genshin_app.equipment.schema import PlanError
    artifact = verification_failure_artifact(capacity_failure(), {})
    (artifact if level == 'artifact' else artifact['diagnostic'])[key] = value
    with pytest.raises(PlanError):
        inspect_calculation_diagnostics(artifact)
