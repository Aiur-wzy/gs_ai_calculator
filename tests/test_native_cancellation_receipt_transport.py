"""Explicit cancellation survives the real App/DSH failure channel, never result acceptance."""
from __future__ import annotations

import copy
import json
import subprocess
import threading
import uuid
from pathlib import Path

import pytest

from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.agent_chat.native_app_tool_bridge import (
    build_native_app_tool_settlement,
    validate_native_app_tool_settlement,
)
from genshin_app.agent_chat.native_tool_receipt import sanitize_native_response
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_app.execution_cancellation import cancellation_response
from genshin_engine.execution_control import ExecutionCancelled, execution_control_scope
from tests.dsh_fixture_environment import isolated_runtime_environment
from tests.test_native_app_tool_host import call, host
from tests.test_plan_mechanics import native

ROOT = Path(__file__).resolve().parents[1]


def cancelled():
    return cancellation_response(ExecutionCancelled(), operation="gs_compose")


def test_actual_host_cancellation_settles_without_changes_and_replays(tmp_path):
    session = ArtifactCandidateSession(str(uuid.uuid4()), tmp_path / "session", tmp_path / "absent.db", "native", native())
    current = host(tmp_path / "host", session, lambda s, n, a: s.call_tool(n, a))
    before = current.current_snapshot()
    source = fingerprint(session.candidate)
    signal = threading.Event()
    with execution_control_scope(signal.is_set):
        signal.set()
        terminal = call(current, tool="gs_compose", raw='{"expected_revision":0}')
    assert terminal["terminal_state"] == "settled_failure"
    assert terminal["state_changes"] == []
    response = terminal["result"]
    assert response == cancelled()
    assert "system_fault" not in response and "category" not in response
    assert fingerprint(session.candidate) == source and session.revision == 0
    assert session.calculated is None and session.summary()["can_accept"] is False
    payload = build_native_app_tool_settlement(terminal, tool_name="gs_compose", current_snapshot=before)
    assert validate_native_app_tool_settlement(payload, current_snapshot=before) == payload
    replay = call(current, tool="gs_compose", raw='{"expected_revision":0}')
    assert replay["status"] == "replay" and replay["result"] == response
    build_native_app_tool_settlement(replay, tool_name="gs_compose", current_snapshot=before)
    entry = next(iter(current.ledger.read()["entries"].values()))
    assert entry["state"] == "settled_failure" and entry["terminal_reason"] == "calculation_cancelled"


@pytest.mark.parametrize("changed", ["author", "derived"])
def test_cancelled_receipt_cannot_bless_changed_state(tmp_path, changed):
    session = ArtifactCandidateSession(str(uuid.uuid4()), tmp_path / "session", tmp_path / "absent.db", "native", native())

    def mutated(current, _name, _args):
        if changed == "author":
            current.candidate["manual_template"]["characters"][0]["stats"]["atk"] += 1
        else:
            current.report = {"unexpected": True}
        return cancelled()

    current = host(tmp_path / "host", session, mutated)
    before = current.current_snapshot()
    terminal = call(current, tool="gs_compose", raw='{"expected_revision":0}')
    assert terminal["terminal_state"] == "unknown_after_interruption"
    assert terminal["terminal_reason"] == ("effect_mask_violation" if changed == "author" else "failed_tool_changed_state")
    assert "result" not in terminal and session.calculated is None
    build_native_app_tool_settlement(terminal, tool_name="gs_compose", current_snapshot=before)


def malformed_cancellations():
    changes = [
        {"code": True}, {"code": 0}, {"reason": "other_cancel"}, {"failure_kind": "unknown"},
        {"retryable": False}, {"message": ""}, {"operation": "invalid operation"}, {"operation": None},
        {"system_fault": {}}, {"diagnostic_storage": {"state": "persisted"}},
        {"category": "cancelled"}, {"blocking_scope": "none"}, {"extra": True},
        {"recovery": {"action": "recalculate_fresh", "retry_same_request": True}},
        {"details": {"code": "calculation_cancelled", "message": cancelled()["message"], "path": "execution", "severity": "error"}},
        {"details": {"code": "calculation_cancelled", "message": "different", "path": "execution", "severity": "cancelled"}},
    ]
    return [{**copy.deepcopy(cancelled()), **change} for change in changes]


@pytest.mark.parametrize("value", malformed_cancellations())
def test_malformed_or_mixed_cancel_receipts_are_rejected(value):
    with pytest.raises(PlanError) as caught:
        sanitize_native_response(value, tool="gs_compose")
    assert caught.value.reason == "native_tool_receipt_invalid"


def test_current_node_validator_accepts_only_closed_cancellation_as_non_system(tmp_path):
    module = (ROOT / "agent-runtime/src/native-app-system-failure.mjs").as_uri()
    script = f"""import assert from 'node:assert/strict';
import {{validateAppSystemFailure}} from {json.dumps(module)};
const valid = {json.dumps(cancelled(), ensure_ascii=True)};
assert.equal(validateAppSystemFailure(valid), false);
for (const value of {json.dumps(malformed_cancellations(), ensure_ascii=True)})
  assert.throws(() => validateAppSystemFailure(value), /invalid_app_system_failure/);
console.log('closed cancellation: non-system, never success');
"""
    completed = subprocess.run([str(ROOT / "agent-runtime/node.exe"), "--input-type=module", "-e", script],
        cwd=ROOT, env=isolated_runtime_environment(tmp_path / "profile"), capture_output=True,
        text=True, encoding="utf-8", check=False, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert completed.returncode == 0, completed.stderr


def test_actual_dsh_classifies_closed_cancellation_without_system_fault(tmp_path):
    """Reuse the real public DSH loop probe, changing only its fixed transport fixture."""
    source = (ROOT / "agent-runtime/src/native-dsh-domain-tools-probe.mjs").read_text("utf-8")
    source = source.replace("from './", "from '" + (ROOT / "agent-runtime/src").as_uri() + "/")
    anchor = "    if (modes.get(request.session_id) === 'system_failure') {"
    assert source.count(anchor) == 1
    source = source.replace(anchor, "    if (modes.get(request.session_id) === 'cancelled') {\n"
        "      value.status = 'settled_failure'; value.result = " + json.dumps(cancelled(), ensure_ascii=True) + ";\n"
        "    }\n" + anchor)
    anchor = "  evidence.systemFaultUsesDshErrorChannelWithOriginalRecovery = true"
    assert source.count(anchor) == 1
    source = source.replace(anchor, anchor + """
  const previousEvents = (await host.context.sessionQuery.readSession('scope-one')).events.length
  modes.set('scope-one', 'cancelled')
  await prompt('scope-one')
  const cancelledResults = (await host.context.sessionQuery.readSession('scope-one')).events.slice(previousEvents)
    .filter(event => event.type === 'tool/result')
  assert.ok(cancelledResults.length > 0)
  for (const event of cancelledResults) {
    const text = JSON.stringify(event.data)
    assert.ok(text.includes('calculation_cancelled') && text.includes('recalculate_fresh'))
    assert.ok(text.includes('"isError":true'))
    assert.equal(text.includes('invalid_app_system_failure'), false)
    assert.equal(text.includes('report_system_fault'), false)
    assert.equal(text.includes('system_fault'), false)
  }
  evidence.closedCancellationRemainsDshFailureWithoutProgramFault = true
""")
    (tmp_path / ".codex-tmp").mkdir()
    script = tmp_path / "cancellation-domain-probe.mjs"
    script.write_text(source, encoding="utf-8")
    completed = subprocess.run([str(ROOT / "agent-runtime/node.exe"), "--expose-internals", str(script)],
        cwd=tmp_path, env=isolated_runtime_environment(tmp_path / "profile"), capture_output=True,
        text=True, encoding="utf-8", check=False, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert completed.returncode == 0, completed.stderr[-6000:]
    evidence = json.loads(completed.stdout)
    assert evidence["closedCancellationRemainsDshFailureWithoutProgramFault"] is True
    (tmp_path / "dsh-cancellation-classification.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
