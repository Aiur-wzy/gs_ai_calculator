"""The actual DSH model schema discloses bounds enforced before App dispatch."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import threading
from pathlib import Path

from genshin_app.agent_chat.bridge_protocol import raw_json_hash
from genshin_app.agent_chat.native_app_call_ledger import NativeAppCallLedger
from genshin_app.agent_chat.native_app_tool_host import _derived_view
from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry
from genshin_app.whiteboard import new_whiteboard
from tests.pinned_native_node import pinned_native_node
from tests.test_queries import db_path as db_path  # noqa: PLC0414

ROOT = Path(__file__).resolve().parents[1]


def test_real_dsh_first_request_bound_feedback_and_corrected_host_call(tmp_path, db_path):
    registry = NativeDshWorkspaceRegistry(tmp_path / "workspace", database_path=db_path)
    session_id = "schema-feedback-engineering"
    registry.prepare_document(session_id, "whiteboard", new_whiteboard())
    scope = registry.activate(session_id)
    session = scope.candidate_session
    before = copy.deepcopy({"derived": _derived_view(session), "candidate": session.candidate})
    database_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()
    # Exact run05 rejected JSON arguments, with no historical session authority.
    invalid = json.loads((ROOT / "tests/fixtures/native_schema_feedback_run05.json").read_text(encoding="utf-8"))
    corrected = copy.deepcopy(invalid)
    corrected["record"].pop("adopted_refs")
    corrected["record"].update(source_refs=[{"kind": "local", "resource": "report/effect_requirements"}],
        not_adopted_reason="仅检查资料为空，无需采用执行规则。")
    config = {"snapshot": scope.tool_host.current_snapshot(), "invalid": invalid, "corrected": corrected,
              "profile_root": str(tmp_path / "dsh-profile"), "session_id": session_id}
    process = subprocess.Popen([str(pinned_native_node()), str(ROOT / "tests/native_app_schema_feedback_probe.mjs")],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8")
    watchdog = threading.Timer(90, process.kill)
    watchdog.daemon = True
    watchdog.start()
    observed = None
    invocations = []
    protocol = []
    errors = []
    error_reader = threading.Thread(target=lambda: errors.extend(process.stderr), daemon=True)
    error_reader.start()
    try:
        process.stdin.write(json.dumps(config, ensure_ascii=True, allow_nan=False) + "\n")
        process.stdin.flush()
        for line in process.stdout:
            if not line.startswith("SCHEMA_FEEDBACK "):
                continue
            packet = json.loads(line.removeprefix("SCHEMA_FEEDBACK "))
            protocol.append({key: packet[key] for key in ("type", "name") if key in packet})
            (tmp_path / "probe-progress.json").write_text(json.dumps(protocol), encoding="utf-8")
            if packet["type"] == "invoke":
                body = packet["body"]
                assert json.loads(body["arguments_json"]) == corrected
                ledger_before = NativeAppCallLedger(session.root, conversation_id=scope.conversation_id).read()
                assert ledger_before["entries"] == {}, "malformed note must never reach the App Host"
                with registry._tool_boundary(scope, "gs_record_finding"):
                    terminal = scope.tool_host.execute(conversation_id=scope.conversation_id,
                        turn_id=f"dsh-turn-{body['turn']}", step_id=f"dsh-step-{body['step']}",
                        epoch_id="feedback-epoch", call_id=body["call_id"], tool_name=body["tool"],
                        arguments_json=body["arguments_json"],
                        raw_arguments_hash=raw_json_hash(body["arguments_json"]),
                        **{key: body[key] for key in ("document_id", "candidate_revision", "stage_revision",
                            "database_fingerprint", "tool_manifest_hash", "runtime_generation",
                            "writer_fence", "catalog_hash")})
                    next_snapshot = scope.tool_host.current_snapshot()
                assert terminal["terminal_state"] == "settled_success", terminal
                invocations.append(terminal)
                response = {"session_id": session_id, "call_id": body["call_id"], "call_seq": body["call_seq"],
                            "status": terminal["terminal_state"], "result": terminal["result"],
                            "next_snapshot": next_snapshot}
                process.stdin.write(json.dumps(response, ensure_ascii=True, allow_nan=False) + "\n")
                process.stdin.flush()
            elif packet["type"] == "done":
                observed = packet
        process.wait(timeout=30)
        error_reader.join(timeout=5)
        assert process.returncode == 0, "".join(errors) + json.dumps(protocol)
        assert observed is not None and len(invocations) == 1
        assert observed["invalid_is_error"] is True
        assert observed["corrected_is_error"] is False
        assert observed["source_host_invocations"] == 1
        assert observed["generic_bound_counterexamples"] == 19
        assert observed["no_network"] is True
        feedback = observed["malformed_prehost_feedback"]
        assert feedback["path"] == "arguments.record.adopted_refs"
        assert feedback["constraint"] == "minItems" and feedback["expected"] == 1
        assert feedback["category"] == "request_error"
        assert feedback["app_call_admission"] == "not_recorded"
        assert feedback["execution_state"] == "did_not_execute"
        assert feedback["operation_may_have_completed"] is False
        assert feedback["contract_ref"] == {"resource": "tool_contracts", "pointer": "/tools/gs_record_finding"}
        assert feedback["recovery"]["action"] == "correct_arguments"
        assert feedback["recovery"]["retry_same_request"] is False
        assert not {"system_fault", "failure_kind", "diagnostic_storage"} & feedback.keys()
        assert len(session.findings) == 1
        saved = session.findings[0]
        assert {key: value for key, value in saved.items() if key != "basis"} == corrected["record"]
        from genshin_app.equipment.files import fingerprint, input_view
        from genshin_app.task_evidence import session_evidence_scope
        assert saved["basis"] == {"input_fingerprint": fingerprint(input_view(session.candidate)),
                                  "dependencies": [], "scope": session_evidence_scope(session)}
        assert {"derived": _derived_view(session), "candidate": session.candidate} == before
        assert len(NativeAppCallLedger(session.root, conversation_id=scope.conversation_id).read()["entries"]) == 1
        assert hashlib.sha256(db_path.read_bytes()).hexdigest() == database_hash
        assert not (session.root / "logs/app-system-faults").exists()
        observed["real_python_host_successes"] = 1
        observed["invalid_host_entries"] = 0
        (tmp_path / "schema-feedback-evidence.json").write_text(
            json.dumps(observed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        watchdog.cancel()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
        registry.close()
