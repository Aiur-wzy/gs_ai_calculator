"""Explicit DSH cancellation is an outcome, never an argument-repair task."""
import json

import pytest

from genshin_app.cli import main
from genshin_app.domain_errors import classify_domain_failure, tool_failure_guidance
from genshin_app.equipment.schema import PlanError


@pytest.mark.parametrize("reason", ["dsh_mux_request_cancelled", "dsh_shell_request_cancelled"])
def test_actual_cli_parser_preserves_cancellation_with_human_guidance(tmp_path, capsys, monkeypatch, reason):
    def cancelled(*_args):
        raise PlanError(reason, reason)
    monkeypatch.setattr("genshin_app.agent_chat.native_cli_link.call_live_candidate", cancelled)
    code = main(["--data-dir", str(tmp_path), "agent", "dsh-settings", "show", "--connection", "controlled.json"])
    output = capsys.readouterr()
    assert code == 1 and not output.out
    value = json.loads(output.err)
    assert value["reason"] == reason and value["category"] == value["failure_kind"] == "cancelled"
    assert "已取消" in value["message"]
    assert value["recovery"] == {"action": "inspect_state_then_resume", "retry_same_request": False}
    assert not list(tmp_path.glob("logs/app-system-faults/*"))


def test_similar_message_does_not_convert_real_request_error_to_cancellation():
    assert classify_domain_failure("agent_tool_input", details={"message": "cancelled"})["category"] == "request_error"
    assert tool_failure_guidance("agent_tool_input")["recovery"]["action"] == "correct_arguments"
