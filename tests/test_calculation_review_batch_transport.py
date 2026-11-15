"""Transport-only use of saved measured pages; no additional pressure replay."""
import gzip
import json
from pathlib import Path

from genshin_app import calculation_review_batch_cli as cli
from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.agent_chat.review_projection import finish
from genshin_app.equipment.files import json_bytes
from genshin_app.review_batch_json import PROJECTION_WORK, Budget
from tests.test_calculation_delivery import delivery_plan

ROOT = Path(__file__).resolve().parents[1]


def pressure():
    data = json.loads(gzip.decompress((ROOT / 'tests/fixtures/calculation_review_batch/pressure-batch.json.gz').read_bytes()).decode('utf-8'))
    measured = json.loads((ROOT / 'tests/fixtures/calculation_review_batch/pressure-measurement.json').read_text(encoding='utf-8'))
    budget = Budget(PROJECTION_WORK, 'projection')
    budget.charge(next(row['work'] for row in measured['budgets'] if row['role'] == 'projection'))
    # The real builder has already prepaid this output's traversal and encoding.
    budget._encoding_input = (data, len(json_bytes(data)))
    return data, budget


def test_cli_transport_uses_remaining_real_pressure_budget(tmp_path, capsys, monkeypatch):
    from argparse import Namespace
    request = tmp_path / 'request.json'
    request.write_text('{"requests":[{}]}')
    output, budget = pressure()
    monkeypatch.setattr(cli, '_review_calculation_file', lambda path, req, *, finish: finish(output, budget))
    cli.command(Namespace(calculated=str(tmp_path / 'synthetic-input.json'), request=str(request),
                          output=str(tmp_path / 'projection.json'), data_dir=str(tmp_path), pretty=False, overwrite=False))
    response = json.loads(capsys.readouterr().out)
    assert len(response['data']['pages']) == 12 and budget.used <= PROJECTION_WORK


def test_agent_transport_uses_remaining_real_pressure_budget(tmp_path):
    import uuid
    session = CandidateSession(uuid.uuid4().hex, tmp_path / 'session', tmp_path / 'missing.db', 'native', delivery_plan())
    session.calculate(0)
    output, budget = pressure()
    # Transport-only fixture binds to this synthetic session; it does not assert
    # that pressure saved-result validation substitutes for Agent full-package checks.
    for key in output['identity']:
        output['identity'][key] = session.calculated['result_receipt'].get(key)
    budget._encoding_input = (output, len(json_bytes(output)))
    response = finish(session, output, budget, maximum_response=256 * 1024,
                      maximum_resources=128, maximum_total=64 * 1024 * 1024)
    assert response['data']['storage'] == 'session_resource'
    assert budget.used <= PROJECTION_WORK
    assert len(session.resources[response['data']['reference']['resource']]['projection']['pages']) == 12


def test_pretty_transport_fails_closed_if_true_formatted_response_too_large(tmp_path, capsys, monkeypatch):
    from argparse import Namespace
    request = tmp_path / 'request.json'
    request.write_text('{"requests":[{}]}')
    output, budget = pressure()
    monkeypatch.setattr(cli, '_review_calculation_file', lambda path, req, *, finish: finish(output, budget))
    path = tmp_path / 'projection.json'
    try:
        cli.command(Namespace(calculated=str(tmp_path / 'synthetic-input.json'), request=str(request),
                              output=str(path), data_dir=str(tmp_path), pretty=True, overwrite=False))
    except ValueError:
        assert not path.exists() and not capsys.readouterr().out
    else:
        assert len(capsys.readouterr().out.encode('utf-8')) <= 2 * 1024 * 1024
