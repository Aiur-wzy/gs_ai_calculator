"""CLI keeps unexpected program faults separate from legitimate user errors."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from genshin_app import cli
from genshin_app.app_system_fault import validate_system_failure


@pytest.mark.parametrize("error,component", [
    (ModuleNotFoundError("private file path"), "python_engine_runtime"),
    (ImportError("private dependency"), "python_engine_runtime"),
    (AttributeError("private object"), "app_domain_tools"),
    (KeyError("private key"), "app_domain_tools"),
    (TypeError("private arg"), "app_domain_tools"),
    (RuntimeError("private runtime fault"), "app_domain_tools"),
    (ZeroDivisionError("private arithmetic fault"), "app_domain_tools"),
    (ValueError("private internal value"), "app_domain_tools"),
    (OSError("private device failure"), "app_domain_tools"),
    (FileNotFoundError("private internal resource"), "app_domain_tools"),
])
def test_cli_untyped_program_fault_is_structured(monkeypatch, tmp_path, capsys, error, component):
    def fail(_args):
        raise error
    monkeypatch.setattr(cli, "COMMANDS", [{"path": ("probe",), "description": "test", "args": [], "handler": fail}])
    assert cli.main(["--data-dir", str(tmp_path), "probe"]) == 1
    output = capsys.readouterr()
    assert not output.out
    payload = json.loads(output.err)
    validate_system_failure(payload)
    assert payload["system_fault"]["component"] == component
    assert payload["system_fault"]["operation"] == "cli.probe"
    assert "private" not in output.err


@pytest.mark.parametrize("reason", ["input_file_not_found", "input_file_not_readable", "invalid_query_request"])
def test_cli_typed_user_errors_keep_ordinary_response(monkeypatch, tmp_path, capsys, reason):
    from genshin_app.equipment.schema import PlanError
    error = PlanError("input requires correction", reason, {"path": "/input"})
    def fail(_args):
        raise error
    monkeypatch.setattr(cli, "COMMANDS", [{"path": ("probe",), "description": "test", "args": [], "handler": fail}])
    assert cli.main(["--data-dir", str(tmp_path), "probe"]) == 1
    payload = json.loads(capsys.readouterr().err)
    assert "system_fault" not in payload
    assert payload["message"] == str(error)
    assert payload["reason"] == reason and payload["category"] == "request_error"


def test_cli_typed_typeerror_retains_domain_settlement(monkeypatch, tmp_path, capsys):
    error = TypeError("legitimate typed domain rejection")
    error.reason = "input_contract_invalid"
    error.details = {"path": "request.value"}
    def fail(_args):
        raise error
    monkeypatch.setattr(cli, "COMMANDS", [{"path": ("probe",), "description": "test", "args": [], "handler": fail}])
    assert cli.main(["--data-dir", str(tmp_path), "probe"]) == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload["reason"] == error.reason
    assert payload["details"] == error.details
    assert "system_fault" not in payload


@pytest.mark.parametrize("exception_name", ["ImportError", "TypeError"])
def test_actual_unconfigured_child_stderr_is_one_json_document(tmp_path, exception_name):
    repository = Path(__file__).resolve().parents[1]
    script = r'''
import sys
sys.path.insert(0, sys.argv[1])
from genshin_app import cli
def fail(_args):
 raise getattr(__import__('builtins'), sys.argv[3])('private-fixture-detail')
cli.COMMANDS=[{'path':('probe',),'description':'fixture','args':[],'handler':fail}]
raise SystemExit(cli.main(['--data-dir',sys.argv[2],'probe']))
'''
    child = subprocess.run([sys.executable, "-c", script, str(repository / "src"), str(tmp_path), exception_name],
                           capture_output=True, text=True, encoding="utf-8", check=False)
    assert child.returncode == 1
    assert not child.stdout
    payload = json.loads(child.stderr)
    validate_system_failure(payload)
    assert payload["system_fault"]["operation"] == "cli.probe"
    assert "private-fixture-detail" not in child.stderr
