"""Public one-shot/session/native feedback parity, without a large database copy."""
from __future__ import annotations

import io
import json
import sqlite3
import uuid
from contextlib import contextmanager, nullcontext

import pytest

from genshin_app import cli
from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.app_fault_store import read_system_diagnostic
from genshin_app.app_system_fault import validate_system_failure
from genshin_app.cli_session import invoke, serve
from genshin_app.equipment.files import input_file_errors, read_input_bytes
from genshin_app.equipment.schema import PlanError
from genshin_app.whiteboard import new_whiteboard
from genshin_db import runtime_action_admission as admission
from genshin_db.runtime_queries import query_runtime
from genshin_engine.execution_control import ExecutionCancelled


def catalog_request(*, line="release"):
    return {"action": "action_admission_model_candidates", "version_line": line,
            "request": {"contract": admission.CATALOG_QUERY_CONTRACT, "version_line": "release",
                        "character_id": 10000002, "observed": {},
                        "unknown_paths": sorted(admission._CATALOG_PATHS), "view": "all",
                        "order": admission.CATALOG_ORDER, "page_size": 16, "cursor": None}}


@pytest.fixture
def public_paths(tmp_path, monkeypatch):
    # These tests target the request/execution boundary after admission, not a
    # substitute release validator. The semantic mismatch precedes SQLite.
    monkeypatch.setattr(cli, "_require_runtime_database", lambda _: {"status": "ready"})
    (tmp_path / "genshin.db").touch()
    session = ArtifactCandidateSession(uuid.uuid4().hex, tmp_path / "native", tmp_path / "absent.db",
                                      "whiteboard", new_whiteboard())
    monkeypatch.setattr(session, "_read_db", nullcontext)
    return session


def call_cli(tmp_path, capsys, argv):
    code = cli.main(["--data-dir", str(tmp_path), *argv])
    output = capsys.readouterr()
    assert code == 1 and not output.out
    return json.loads(output.err)


def call_three(tmp_path, capsys, session, request):
    source = tmp_path / "request.json"
    source.write_text(json.dumps(request), encoding="utf-8")
    argv = ["db", "runtime", "--input", str(source)]
    one = call_cli(tmp_path, capsys, argv)
    output = io.StringIO()
    serve(tmp_path, io.StringIO(json.dumps({"id": "bad", "argv": argv}) + "\n" +
        json.dumps({"id": "after", "argv": ["capabilities", "--section", "call_session"]}) + "\n"),
        output, admission=lambda _: {"status": "ready"})
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert rows[0]["full_admission_count"] == 1
    assert rows[1]["exit_code"] == 1 and rows[1]["automatically_retried"] is False
    assert rows[2]["exit_code"] == 0  # An isolated rejection does not poison lifetime.
    return [one, rows[1]["result"], session.call_tool("gs_query_runtime", {"request": request})]


def test_actual_code_only_semantic_error_survives_all_public_boundaries(tmp_path, capsys, public_paths):
    request = catalog_request(line="beta")
    with pytest.raises(admission.ActionAdmissionError) as caught:
        query_runtime(tmp_path / "genshin.db", request)
    # Deliberately bypasses the preceding schema-error adapter; real code-only type.
    assert caught.value.code == "action_admission_catalog_query"
    assert not hasattr(caught.value, "reason")
    rows = call_three(tmp_path, capsys, public_paths, request)
    for row in rows:
        assert row["code"] == 1 and row["reason"] == "action_admission_catalog_query"
        assert row["category"] == "request_error" and "system_fault" not in row
        assert row["recovery"] == {"action": "correct_arguments", "retry_same_request": False}
        assert row["path"] == "/version_line" and "同一来源" in row["repair_hint"]
        assert row["details"]["actual"] == "beta" and row["details"]["expected"] == "release"
    assert rows[0]["details"] == rows[1]["details"] == rows[2]["details"]
    assert rows[0]["contract_ref"]["command"] == "db runtime"
    assert rows[2]["contract_ref"]["pointer"] == "/tools/gs_query_runtime"


@pytest.mark.parametrize("fault", ["sqlite", "value", "io", "cancel"])
def test_actual_query_internal_fault_or_cancel_is_same_fact_in_all_surfaces(
        tmp_path, capsys, public_paths, monkeypatch, fault):
    @contextmanager
    def broken_read(*_args):
        if fault == "sqlite":
            with sqlite3.connect(":memory:") as connection:
                connection.execute("SELECT * FROM private_runtime_missing_table")
        if fault == "value":
            raise ValueError("private_runtime_invalid_internal_state")
        if fault == "io":
            raise OSError("private_runtime_device_fault")
        raise ExecutionCancelled()
        yield  # pragma: no cover - context manager protocol; no execution after fault
    monkeypatch.setattr(admission, "_read", broken_read)
    rows = call_three(tmp_path, capsys, public_paths, catalog_request())
    ids = set()
    for row in rows:
        assert "private_runtime" not in json.dumps(row)
        if fault == "cancel":
            assert row["reason"] == "calculation_cancelled" and row["failure_kind"] == "cancelled"
            assert row["recovery"]["action"] == "recalculate_fresh" and "system_fault" not in row
        else:
            validate_system_failure(row)
            assert row["diagnostic_storage"]["state"] == "persisted"
            ident = row["system_fault"]["diagnostic_id"]
            record = read_system_diagnostic(tmp_path, ident)
            assert "private_runtime" not in json.dumps(record)
            assert record["diagnostic"]["exception_type"] == {
                # Existing safe diagnostic contract only publishes trusted
                # builtin class names; sqlite retains the safe Exception root.
                "sqlite": "Exception", "value": "ValueError", "io": "OSError"}[fault]
            ids.add(ident)
    assert fault == "cancel" or len(ids) == 3


def test_panel_semantic_range_feedback_shared_with_native(tmp_path, capsys, public_paths):
    request = {"db_character_id": 10000002, "weapon": {"ascension": 7}}
    path = tmp_path / "panel.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    argv = ["db", "panel-basis", "--input", str(path)]
    rows = [call_cli(tmp_path, capsys, argv), invoke({"id": "panel", "argv": argv}, tmp_path)["result"],
            public_paths.call_tool("gs_character_panel_basis", request)]
    for row in rows:
        assert row["reason"] == "invalid_query_request" and row["category"] == "request_error"
        assert row["path"] == "/weapon/ascension"
        assert row["details"]["minimum"] == 0 and row["details"]["maximum"] == 6
    assert rows[0]["details"] == rows[1]["details"] == rows[2]["details"]


@pytest.mark.parametrize("argv", [
    ["db", "context", "--input", "MISSING"], ["db", "appendix", "--input", "MISSING"],
    ["db", "catalog", "--input", "MISSING"], ["db", "panel-basis", "--input", "MISSING"],
    ["db", "runtime", "--input", "MISSING"], ["equipment", "panel-reference", "--input", "MISSING"],
    ["engine", "calc", "MISSING"], ["engine", "check-rotation", "MISSING"],
    ["whiteboard", "validate", "--input", "MISSING"], ["equipment", "validate", "--plan", "MISSING"],
    ["equipment", "edit", "--plan", "MISSING", "--request", "MISSING"],
    ["equipment", "review", "--calculated", "MISSING"],
    ["equipment", "review-batch", "--calculated", "MISSING", "--request", "MISSING"],
    ["equipment", "attribute-draft", "--plan", "MISSING"],
    ["equipment", "assemble-attack", "--plan", "MISSING", "--request", "MISSING"],
    ["workflow", "workspace", "--plan", "MISSING", "--audit", "MISSING"],
    ["research", "append", "--plan", "MISSING", "--task-id", "test", "--input", "MISSING"],
    ["agent", "candidate", "read", "--connection", "unused", "--session-id", "test", "--request", "MISSING"],
    ["shortcut", "create", "--input", "MISSING", "--output", "NEW.lnk"],
])
def test_known_input_file_failures_remain_actionable_across_cli_readers(tmp_path, capsys, public_paths, argv):
    argv = [str(tmp_path / "absent.json") if value == "MISSING" else
            str(tmp_path / "link.lnk") if value == "NEW.lnk" else value for value in argv]
    response = call_cli(tmp_path, capsys, argv)
    assert response["reason"] == "input_file_not_found", response
    assert response["category"] == "request_error" and response["recovery"]["action"] == "select_input_file"
    assert "system_fault" not in response


@pytest.mark.parametrize("argv", [["db", "panel-basis"], ["not-a-command"]])
def test_missing_arguments_are_typed_not_internal(tmp_path, capsys, argv):
    row = call_cli(tmp_path, capsys, argv)
    assert row["category"] == "request_error" and row["recovery"]["action"] == "correct_arguments"


def test_exact_io_typing_does_not_hide_unexpected_internal_value_or_io(tmp_path, monkeypatch):
    with input_file_errors(), pytest.raises(OSError, match="device"):
        raise OSError("device")
    with input_file_errors(), pytest.raises(ValueError, match="internal"):
        raise ValueError("internal")
    with pytest.raises(PlanError) as caught, input_file_errors(field="/plan"):
        raise PermissionError("private")
    assert caught.value.reason == "input_file_not_readable" and caught.value.details["path"] == "/plan"
    assert "private" not in str(caught.value)
    from genshin_app import cli_session
    monkeypatch.setattr(cli_session, "reuse_admission", lambda _: (_ for _ in ()).throw(ValueError("private guard")))
    row = invoke({"id": "guard", "argv": ["capabilities", "--section", "call_session"]}, tmp_path)["result"]
    validate_system_failure(row)
    assert "private guard" not in json.dumps(row)


def test_json_error_is_file_input_but_ordinary_non_json_parser_errors_remain_system(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text('{"bad":', encoding="utf-8")
    response = call_cli(tmp_path, capsys, ["engine", "calc", str(bad)])
    assert response["reason"] == "input_json_invalid" and response["details"]["line"] == 1
    with pytest.raises(PlanError) as caught:
        read_input_bytes("bad\0filename")
    assert caught.value.reason == "input_file_invalid"
