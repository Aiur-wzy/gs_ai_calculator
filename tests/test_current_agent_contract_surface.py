"""Actual model catalog/resources and CLI parser share current feature descriptions."""
from __future__ import annotations

import json
import uuid

from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.agent_chat.tools import definitions_for_session
from genshin_app.cli import build_parser, main
from tests.test_energy_trials import plan


def test_artifact_publishes_current_edit_and_auxiliary_contracts_at_any_focus(tmp_path):
    session = ArtifactCandidateSession(uuid.uuid4().hex, tmp_path / "session", tmp_path / "absent.db", "native", plan())
    definitions = {row["name"]: row for row in definitions_for_session(session)}
    patch = definitions["gs_patch_candidate"]
    assert patch["inputSchema"]["properties"]["mode"]["enum"] == ["patch", "replace", "import"]
    assert patch["inputSchema"]["properties"]["preview"] == {"type": "boolean"}
    assert "energy_trials" in definitions["gs_diagnose_result"]["description"]
    for resource, pointer in [("input_edit_contract", ""), ("diagnostics_capabilities", "/auxiliary_views"),
                              ("runtime_contracts", "/actions/mechanism_catalog/input_schema/properties/page_size"),
                              ("tool_contracts", "/tools/gs_patch_candidate")]:
        value = session.call_tool("gs_read_context", {"resource": resource, "pointer": pointer})
        assert value["code"] == 0, value
        assert value["data"]["resource"] == resource
    changed = session.call_tool("gs_transition_phase", {"target_phase": "calculation_closure", "reason": "change reading focus only",
        "expected_revision": 0, "expected_stage_revision": 0})
    assert changed["code"] == 0
    after = {row["name"]: row for row in definitions_for_session(session)}
    assert definitions == after


def test_live_cli_parser_applies_each_selected_command_defaults_without_execution():
    parser = build_parser()
    for operation in ("read", "edit", "calculate"):
        args = parser.parse_args(["agent", "candidate", operation, "--connection", "live.json", "--session-id", "actual"])
        assert args._live_operation == operation
    for operation in ("show", "set", "reset"):
        args = parser.parse_args(["agent", "dsh-settings", operation, "--connection", "live.json"])
        assert args._agent_dsh_settings == operation
    for operation in ("show", "set", "reset"):
        args = parser.parse_args(["agent", "budget", operation])
        assert args._budget == operation


def test_real_cli_capabilities_expose_current_parameters_not_a_parallel_description(capsys):
    for command, fields in [("equipment edit", {"--preview", "--expected-file-sha", "--request"}),
        ("equipment diagnose", {"--plan", "--calculated", "--request"}),
        ("equipment deliver", {"--roles", "--energy-intent"}),
        ("agent candidate edit", {"--connection", "--session-id", "--request"}),
        ("agent dsh-settings show", {"--connection", "--entry"})]:
        assert main(["capabilities", "--command", command, "--pointer", "/args"]) == 0
        value = json.loads(capsys.readouterr().out)["data"]
        assert fields <= {row["name"] for row in value["data"]}
    assert main(["capabilities", "--section", "runtime_contract", "--pointer", "/batch/actions"]) == 0
    actions = json.loads(capsys.readouterr().out)["data"]["data"]
    assert {"definition", "fragment"} <= set(actions)
