from __future__ import annotations

import io
import json
import os
import threading

import pytest

from genshin_app import cli
from genshin_app.cli_session import reuse_admission, serve, verified_session
from genshin_app.equipment.schema import PlanError


def request(ident="a"):
    return {"id": ident, "argv": ["capabilities", "--section", "workflow_contract"]}


def test_served_requests_share_one_real_admission_boundary_and_no_stdout_parsing(tmp_path, monkeypatch):
    calls = []
    def admission(directory):
        calls.append(str(directory))
        return {"status": "ready", "identity": "fixture"}
    # Handler still traverses the same real COMMANDS, parser and exception boundary.
    data = "\n".join(json.dumps(request(str(i))) for i in range(3)) + "\n"
    out = io.StringIO()
    serve(tmp_path, io.StringIO(data), out, admission=admission)
    rows = [json.loads(line) for line in out.getvalue().splitlines()]
    assert len(calls) == 1 and rows[0]["full_admission_count"] == 1
    assert [r["id"] for r in rows[1:]] == ["0", "1", "2"]
    assert all(r["result"]["data"]["data"]["record_types"] for r in rows[1:])
    assert all(r["exit_code"] == 0 and r["stdout"] == "" for r in rows[1:])
    assert reuse_admission(tmp_path) is None


def test_lifetime_fence_prevents_concurrent_upgrade_and_releases_at_eof(tmp_path):
    from genshin_app.database_transaction import profile_writer
    seen = []
    def other_thread():
        try:
            with profile_writer(tmp_path, timeout=0):
                seen.append("acquired")
        except TimeoutError:
            seen.append("blocked")
    with verified_session(tmp_path, lambda _: {"ready": True}):
        worker = threading.Thread(target=other_thread)
        worker.start(); worker.join()
        assert seen == ["blocked"]
    worker = threading.Thread(target=other_thread)
    worker.start(); worker.join()
    assert seen[-1] == "acquired"


def test_identity_change_invalidates_session_not_silently_reverified(tmp_path):
    db = tmp_path / "genshin.db"
    db.write_bytes(b"old")
    with verified_session(tmp_path, lambda _: {"ready": True}):
        assert reuse_admission(tmp_path) == {"ready": True}
        db.write_bytes(b"modified")
        with pytest.raises(PlanError, match="变化"):
            reuse_admission(tmp_path)


@pytest.mark.parametrize("frozen,assets_name", [(False, "frontend"), (True, "agent-client-assets")])
@pytest.mark.parametrize("name", ["dsh-client-accepted-manifest.json", "dsh-client-composition.json"])
def test_actual_client_asset_control_change_invalidates_lifetime(tmp_path, monkeypatch, frozen, assets_name, name):
    from genshin_app import paths
    from genshin_app.cli_session import _identity_files
    resources = tmp_path / "resources"
    assets = resources / assets_name
    assets.mkdir(parents=True)
    control = assets / name
    control.write_bytes(b'{"version":"A"}')
    monkeypatch.setattr(paths, "resource_dir", lambda: resources)
    monkeypatch.setattr(paths, "is_frozen", lambda: frozen)
    directory = tmp_path / "profile"
    identities = _identity_files(directory)
    assert control in identities
    assert resources / "agent-runtime" / name not in identities
    with verified_session(directory, lambda _: {"ready": True}):
        assert reuse_admission(directory) == {"ready": True}
        before = control.stat()
        control.write_bytes(b'{"version":"B"}')
        # Same size and timestamp must not hide control-file content changes.
        os.utime(control, ns=(before.st_atime_ns, before.st_mtime_ns))
        with pytest.raises(PlanError, match="变化"):
            reuse_admission(directory)


def test_full_admission_failure_no_ready_and_no_call(tmp_path):
    def denied(_):
        raise PlanError("bad release", "invalid_release")
    out = io.StringIO()
    with pytest.raises(PlanError):
        serve(tmp_path, io.StringIO(json.dumps(request()) + "\n"), out, admission=denied)
    assert not out.getvalue()


def test_bad_request_does_not_poison_next_call_or_execute_shell(tmp_path):
    out = io.StringIO()
    lines = [{"id": "bad", "argv": ["db", "update"]}, request("good")]
    serve(tmp_path, io.StringIO("\n".join(map(json.dumps, lines))), out, admission=lambda _: {})
    rows = [json.loads(v) for v in out.getvalue().splitlines()]
    assert rows[1]["exit_code"] == 1 and rows[2]["exit_code"] == 0
    assert rows[1]["automatically_retried"] is False


def test_same_handler_domain_error_is_structured_and_not_parsed_from_log(tmp_path, monkeypatch):
    from genshin_app.cli_dispatch import execute
    args = cli.build_parser().parse_args(request()["argv"])
    args.data_dir = str(tmp_path)
    def fail(_):
        raise PlanError("page_size只能为1..10", "appendix_query_schema_invalid", {"path": "page_size"})
    args._handler = fail
    payload = execute(args)
    assert payload["reason"] == "appendix_query_schema_invalid"
    assert payload["details"]["path"] == "page_size"
    assert payload["operation"] == "cli.capabilities"


def test_workspace_command_uses_cas_write_and_protects_original_task(tmp_path, capsys):
    from genshin_app.equipment import new_plan
    from genshin_app.equipment.files import json_bytes
    plan, audit, task = tmp_path / "plan.json", tmp_path / "audit.json", tmp_path / "task.txt"
    plan.write_bytes(json_bytes(new_plan()))
    task.write_text("两轮，输出方案。", encoding="utf-8")
    args = ["--data-dir", str(tmp_path), "workflow", "workspace", "--plan", str(plan), "--audit", str(audit)]
    assert cli.main([*args, "--task-file", str(task)]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["data"]["record_count"] == 1 and audit.exists()
    assert cli.main(args) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["data"]["task_review"]["task_sources"][0]["description"] == "两轮，输出方案。"


def test_log_cannot_alias_release_identity_or_request_input(tmp_path):
    from genshin_app.cli_session import invoke
    protected = tmp_path / "database-live-manifest.json"
    with pytest.raises(PlanError):
        serve(tmp_path, io.StringIO(""), io.StringIO(), admission=lambda _: {}, log_path=protected)
    log = tmp_path / "session.jsonl"
    with verified_session(tmp_path, lambda _: {}, log_path=log):
        response = invoke({"id": "a", "argv": ["db", "context", "--input", str(log)]}, tmp_path)
        assert response["result"]["reason"] == "file_conflict"
        # Directory-based publishers and their sidecar/backup roles must also
        # inherit protection, not just exact command arguments.
        from genshin_app.output_policy import cli_output_scope, validate_output_paths
        args = cli.build_parser().parse_args(["workflow", "export", "--plan", "p.json",
            "--audit", "a.json", "--output-dir", str(tmp_path), "--overwrite"])
        args.data_dir = str(tmp_path)
        with cli_output_scope(args), pytest.raises(PlanError):
            validate_output_paths([log])


def test_disconnect_does_not_retry_committed_operation_and_releases_fence(tmp_path, monkeypatch):
    import genshin_app.cli_session as module
    calls = []
    def committed(request, directory):
        calls.append(request["id"])
        return {"id": request["id"], "result": {"code": 0}}
    class Disconnect(io.StringIO):
        def write(self, text):
            if "ready-v1" not in text:
                raise BrokenPipeError("caller disconnected")
            return super().write(text)
    monkeypatch.setattr(module, "invoke", committed)
    with pytest.raises(BrokenPipeError):
        serve(tmp_path, io.StringIO(json.dumps(request()) + "\n"), Disconnect(), admission=lambda _: {})
    assert calls == ["a"] and reuse_admission(tmp_path) is None
def test_python_batch_preserves_completed_prefix_on_transport_failure():
    from genshin_app.cli_client import CliSessionClient
    client = CliSessionClient.__new__(CliSessionClient)
    calls = []
    def call(argv, *, request_id):
        calls.append(request_id)
        if len(calls) == 2:
            raise RuntimeError("connection ended after possible publication")
        return {"id": request_id, "exit_code": 0, "result": {"code": 0}}
    client.call = call
    result = client.batch([["first"], ["second"], ["third"]])
    assert len(calls) == 2 and result["completed"] == 1
    assert result["results"][0]["id"] == calls[0]
    assert result["interrupted_request_id"] == calls[1]
    assert result["write_outcome"] == "unknown"
    assert result["automatically_retried"] is False

