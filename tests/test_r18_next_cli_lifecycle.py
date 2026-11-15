import errno
import io
import json
import sys

import pytest

from genshin_app import cli_session
from genshin_app.cli_client import CliSessionClient
from genshin_app.equipment.schema import PlanError


def test_continue_waiting_for_same_request_with_utf8(tmp_path):
    child = tmp_path / "子进程.py"
    child.write_text(
        """import json, sys, time
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps({"contract": "genshin-cli-session-ready-v1"}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    sys.stderr.buffer.write(b"x" * 180000)
    sys.stderr.flush()
    time.sleep(0.15)
    print(json.dumps({"contract": "genshin-cli-session-result-v1",
                      "id": request["id"], "result": request["argv"]}, ensure_ascii=False), flush=True)
""", encoding="utf-8",
    )
    with CliSessionClient(tmp_path, command=[sys.executable, str(child)]) as client:
        pending = client.call(["中文路径"], timeout=0)
        assert pending["transport_state"] == "in_flight"
        with pytest.raises(RuntimeError, match="receive"):
            client.call(["重复请求"])
        complete = client.receive(timeout=8)
        assert complete["id"] == pending["id"]
        assert complete["result"] == ["中文路径"]


def test_dependency_batch_does_not_run_against_older_result():
    client = CliSessionClient.__new__(CliSessionClient)
    called = []

    def call(argv, *, request_id):
        called.append(argv[0])
        return {"id": request_id, "exit_code": int(argv[0] == "calc"),
                "result": {"data": {"name": argv[0]}}}

    client.call = call
    result = client.batch([["lookup"], ["compose"], ["calc"], ["deliver"]], stop_on_failure=True)
    assert called == ["lookup", "compose", "calc"]
    assert result["completed"] == 3
    assert result["skipped"][0]["argv"] == ["deliver"]
    assert result["skipped"][0]["depends_on"] == result["results"][2]["id"]


def test_successful_unresolved_compose_stops_dependent_batch():
    client = CliSessionClient.__new__(CliSessionClient)
    called = []

    def call(argv, *, request_id):
        called.append(argv)
        result = {"code": 0, "data": {"missing_inputs": [{"reason": "source_missing"}]}}
        return {"id": request_id, "exit_code": 0, **cli_session._operation_outcome(result)}

    client.call = call
    result = client.batch([["compose"], ["calc"]], stop_on_failure=True)
    assert called == [["compose"]]
    assert result["results"][0]["operation_state"] == "diagnostic"
    assert result["skipped"][0]["argv"] == ["calc"]


@pytest.mark.parametrize("finish", [False, True])
def test_cancel_preserves_already_received_success_only(tmp_path, finish):
    child = tmp_path / "writer.py"
    child.write_text(
        """import json, sys, time
print(json.dumps({"contract": "genshin-cli-session-ready-v1"}), flush=True)
request = json.loads(sys.stdin.readline())
if request["argv"] == ["finish"]:
    print(json.dumps({"contract": "genshin-cli-session-result-v1", "id": request["id"],
                      "write_outcome": "written"}), flush=True)
time.sleep(30)
""", encoding="utf-8",
    )
    client = CliSessionClient(tmp_path, command=[sys.executable, str(child)])
    if finish:
        result = client.call(["finish"], timeout=5)
        assert result["write_outcome"] == "written"
        assert client.cancel() == result
    else:
        pending = client.call(["pending"], timeout=0)
        assert pending["transport_state"] == "in_flight"
        stopped = client.cancel()
        assert stopped["id"] == pending["id"]
        assert stopped["write_outcome"] == "unknown"
        assert stopped["automatically_retried"] is False


def test_optional_log_failure_does_not_hide_committed_response(tmp_path, monkeypatch):
    path = tmp_path / "log.jsonl"

    class FullDisk(io.StringIO):
        def write(self, text):
            raise OSError(errno.ENOSPC, "disk full")

    from pathlib import Path
    original = Path.open

    def fake_open(self, *args, **kwargs):
        return FullDisk() if self == path else original(self, *args, **kwargs)

    committed = []

    def invoke(request, _directory):
        committed.append(request["id"])
        return {"id": request["id"], "result": {"code": 0}, "write_outcome": "written"}

    monkeypatch.setattr(Path, "open", fake_open)
    monkeypatch.setattr(cli_session, "invoke", invoke)
    output = io.StringIO()
    cli_session.serve(tmp_path, io.StringIO('{"id":"1"}\n{"id":"2"}\n'), output,
                      admission=lambda _: {}, log_path=path)
    _, first, second = map(json.loads, output.getvalue().splitlines())
    assert committed == ["1", "2"]
    assert first["write_outcome"] == second["write_outcome"] == "written"
    assert first["log_warning"]["reason"] == second["log_warning"]["reason"] == "session_log_write_failed"


@pytest.mark.parametrize("relative_path", ["src/genshin_app/example.py", "skill/references/example.md"])
def test_source_session_invalidated_by_reference_or_python_edit(tmp_path, monkeypatch, relative_path):
    from genshin_app import paths
    monkeypatch.setattr(paths, "resource_dir", lambda: tmp_path)
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True)
    path.write_text("first", encoding="utf-8")
    data_dir = tmp_path / "data"
    with cli_session.verified_session(data_dir, lambda _: {"verified": True}):
        assert cli_session.reuse_admission(data_dir) == {"verified": True}
        path.write_text("second", encoding="utf-8")
        with pytest.raises(PlanError) as failure:
            cli_session.reuse_admission(data_dir)
        assert failure.value.reason == "cli_session_source_changed"
