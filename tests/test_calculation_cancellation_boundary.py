"""A real explicit cancellation must not become an input or program defect."""
from __future__ import annotations

import json
import threading
import uuid

from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.cli import main
from genshin_app.equipment.files import fingerprint
from genshin_app.gui.ports.equipment import EquipmentGuiPort
from genshin_engine.execution_control import execution_control_scope
from tests.test_plan_mechanics import native


def check_cancelled(value):
    assert value['code'] == 1 and value['reason'] == 'calculation_cancelled'
    assert value['failure_kind'] == 'cancelled'
    assert 'system_fault' not in value and 'category' not in value
    assert value['recovery'] == {'action':'recalculate_fresh','retry_same_request':False}


def test_cancelled_native_compose_returns_explicit_receipt_and_preserves_candidate(tmp_path):
    session = ArtifactCandidateSession(str(uuid.uuid4()), tmp_path/'session', tmp_path/'absent.db', 'native', native())
    before = fingerprint(session.candidate)
    signal = threading.Event()
    with execution_control_scope(signal.is_set):
        signal.set()
        response = session.call_tool('gs_compose', {'expected_revision':session.revision})
    check_cancelled(response)
    assert session.revision == 0 and fingerprint(session.candidate) == before
    assert session.calculated is None


def test_cli_and_gui_port_project_cancellation_without_fake_system_failure(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr('genshin_app.cli._require_runtime_database', lambda _: {'can_start':True})
    path = tmp_path/'plan.json'
    plan = native()
    path.write_text(json.dumps(plan), encoding='utf-8')
    signal = threading.Event()
    with execution_control_scope(signal.is_set):
        signal.set()
        assert main(['equipment','validate','--plan',str(path)]) == 1
    check_cancelled(json.loads(capsys.readouterr().err))
    port = EquipmentGuiPort(tmp_path, tmp_path/'absent.db', manager_provider=lambda:None)
    signal.clear()
    with execution_control_scope(signal.is_set):
        signal.set()
        response = port._equipment_call('compose_equipment_plan', plan, tmp_path/'absent.db')
    check_cancelled(response)
