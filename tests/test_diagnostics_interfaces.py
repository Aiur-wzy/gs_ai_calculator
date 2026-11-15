"""Public diagnostics preserve failed status, file protection and candidate identity."""
import copy
import json

from genshin_app.cli import main
from genshin_app.equipment.files import json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_app.gui.api import GuiApi
from tests.test_agent_candidate import native_session as _native_session
from tests.test_calculation_diagnostics import failed

native_session = _native_session


def test_cli_failed_input_pagination_and_protected_files(tmp_path, capsys):
    source, request, target = [tmp_path / name for name in ('error.json', 'request.json', 'page.json')]
    source.write_bytes(json_bytes(failed()))
    request.write_bytes(json_bytes({'view':'issues', 'limit':2}))
    original = source.read_bytes()
    args = ['--data-dir', str(tmp_path), 'equipment', 'diagnose', '--calculated', str(source), '--request', str(request)]
    assert main(args+['--output',str(target)]) == 0
    page = json.loads(capsys.readouterr().out)['data']
    assert page['execution_status'] == 'pending' and len(page['items']) == 2
    request.write_bytes(json_bytes({'view':'issues','cursor':page['next_cursor']}))
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)['data']['items'][0]['index'] == 2
    for protected in (source, request, tmp_path/'genshin.db'):
        assert main(args+['--output', str(protected), '--overwrite']) == 1
        assert json.loads(capsys.readouterr().err)['reason'] == 'file_conflict'
    assert source.read_bytes() == original and not (tmp_path/'genshin.db').exists()


def test_cli_response_limit_checked_before_file_write(tmp_path, capsys, monkeypatch):
    from genshin_engine import diagnostics
    source, target = tmp_path/'error.json', tmp_path/'page.json'
    source.write_bytes(json_bytes(failed()))
    monkeypatch.setattr(diagnostics, 'MAX_RESPONSE_BYTES', 50)
    assert main(['equipment','diagnose','--calculated',str(source),'--output',str(target)]) == 1
    assert json.loads(capsys.readouterr().err)['reason'] == 'diagnostic_response_limit'
    assert not target.exists()


def test_gui_queries_without_database_or_calculation(tmp_path):
    api = GuiApi(tmp_path)
    raw = failed(); before = copy.deepcopy(raw)
    out = api.inspect_calculation_diagnostics(raw)
    assert out['code'] == 0 and out['data']['execution_status'] == 'pending'
    assert out['data']['items'][0]['count'] == 7 and raw == before
    bad = api.inspect_calculation_diagnostics(raw, {'limit': float('nan')})
    assert bad['code'] == 1 and bad['reason'] == 'invalid_diagnostic_input'
    assert list(tmp_path.iterdir()) == []


def fail_engine(*args, **kwargs):
    value = failed()
    raise PlanError('工程失败', value['reason'], value['details'])


def test_agent_failed_calculation_has_readable_original_and_grouped_issues(native_session, monkeypatch):
    from genshin_app.agent_chat import candidate
    monkeypatch.setattr(candidate, 'run_plan_calc', fail_engine)
    session = native_session
    out = session.call_tool('gs_calculate', {'expected_revision':0})
    assert out['code'] == 1 and out['details']['resource'] == 'diagnostic_error'
    assert session.calculated is None and not session.summary()['ready']
    page = session.call_tool('gs_diagnose_result', {'expected_revision':0})
    assert page['code'] == 0 and page['data']['items'][0]['count'] == 7
    raw = session.call_tool('gs_read_context', {'resource':'diagnostic_error', 'pointer':'/details/issues', 'limit':2})
    assert raw['code'] == 0 and raw['data']['total'] == 7
    assert session.report.get('issues') is None  # Original error is not the compose report.


def test_agent_candidate_edit_invalidates_failed_diagnostic(native_session, monkeypatch):
    from genshin_app.agent_chat import candidate
    monkeypatch.setattr(candidate, 'run_plan_calc', fail_engine)
    s = native_session
    assert s.call_tool('gs_calculate', {'expected_revision':0})['code'] == 1
    changed = copy.deepcopy(s.candidate); changed['manual_template']['characters'][0]['stats']['atk'] += 1
    s.replace_candidate(changed, 0, '用户要求改面板')
    assert s.call_tool('gs_diagnose_result', {'expected_revision':0})['reason'] == 'agent_stale_revision'
    assert s.call_tool('gs_diagnose_result', {'expected_revision':1})['reason'] == 'agent_diagnostic_stale'
    assert s._diagnostic_cache['revision'] == 0  # Historical evidence is retained, never current.
    assert s.call_tool('gs_read_context', {'resource': 'diagnostic_error'})['reason'] == 'agent_diagnostic_stale'


def test_agent_recalculation_invalidates_cursor_and_success_uses_receipt(native_session, monkeypatch):
    from genshin_app.agent_chat import candidate
    original = candidate.run_plan_calc
    monkeypatch.setattr(candidate, 'run_plan_calc', fail_engine)
    s = native_session
    assert s.call_tool('gs_calculate', {'expected_revision':0})['code'] == 1
    first = s.call_tool('gs_diagnose_result', {'expected_revision':0, 'request':{'view':'issues','limit':2}})['data']
    monkeypatch.setattr(candidate, 'run_plan_calc', original)
    assert s.call_tool('gs_calculate', {'expected_revision':0})['code'] == 0
    assert s._diagnostic_cache is None
    page = s.call_tool('gs_diagnose_result', {'expected_revision':0})
    assert page['code'] == 0 and page['data']['validation_scope'] == 'full_calculation_package'
    assert s.call_tool('gs_diagnose_result', {'expected_revision':0,'request':{'view':'issues','cursor':first['next_cursor']}})['reason'] == 'stale_diagnostic_cursor'


def test_restart_restores_failed_diagnostic_without_granting_calculation(native_session, monkeypatch):
    from genshin_app.agent_chat import candidate
    monkeypatch.setattr(candidate, 'run_plan_calc', fail_engine)
    s = native_session
    s.call_tool('gs_calculate', {'expected_revision':0})
    s.close()
    restored = candidate.CandidateSession.load(s.session_id, s.root, s.db_path)
    result = restored.call_tool('gs_diagnose_result', {'expected_revision':0})
    assert result['code'] == 0 and result['data']['execution_status'] == 'pending'
    assert result['data']['items'][0]['count'] == 7
    assert restored.calculated is None and not restored.summary()['ready']
    assert restored.summary()['latest_attempt']['attempt_id'] == s.summary()['latest_attempt']['attempt_id']


def test_agent_diagnostics_not_allowed_in_build_workflow():
    from genshin_app.agent_chat.tools import definitions
    assert 'gs_diagnose_result' in {r['name'] for r in definitions('calculate')}
    assert 'gs_diagnose_result' not in {r['name'] for r in definitions('build')}
