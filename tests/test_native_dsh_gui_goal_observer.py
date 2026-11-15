"""Production observer follows only official DSH same-Session goal rounds."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from genshin_app.agent_chat.credential_generation import CredentialGenerationController
from genshin_app.agent_chat.dsh_gui_workflow_runner import run_gui_agent_workflow
from genshin_app.agent_chat.native_dsh_application import NativeDshApplication
from genshin_app.agent_chat.native_dsh_host_requests import NativeDshHostRequests
from genshin_app.agent_chat.native_dsh_mux_process import NativeDshMuxProcess
from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry
from genshin_app.gui.api import GuiApi
from tests.dsh_fixture_environment import isolated_runtime_environment
from tests.test_native_dsh_application import Store
from tests.test_queries import db_path as db_path  # noqa: PLC0414

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "nt" or os.environ.get("GENSHIN_NATIVE_DSH_MUX_OS_SMOKE") != "1",
    reason="explicit offline full DSH goal observer/private-pipe qualification, no GUI")


@pytest.mark.parametrize("mode", ["complete", "paused", "round_limit", "no_goal"])
def test_observer_does_not_close_healthy_official_goal_after_first_turn(tmp_path, db_path, mode):
    script = tmp_path / "goal-provider.json"
    script.write_text(json.dumps({"mode": mode}), encoding="utf-8")
    processes = []

    def factory(data_dir):
        registry = NativeDshWorkspaceRegistry(data_dir / "workspaces", database_path=db_path)
        def start(_):
            profile, mirror = data_dir / "profile", data_dir / "media"
            profile.mkdir(exist_ok=True)
            mirror.mkdir(exist_ok=True)
            process = NativeDshMuxProcess(node=Path(os.environ["GENSHIN_NATIVE_NODE"]),
                entry=ROOT / "agent-runtime/src/native-dsh-goal-observer-fixture.mjs", working=ROOT,
                runtime_environment=isolated_runtime_environment(profile),
                entry_args=(str(profile), str(mirror), str(ROOT / "frontend"), str(script)),
                domain_handler=NativeDshHostRequests(registry, data_dir=data_dir),
                diagnostics_root=data_dir / "process-diagnostics")
            processes.append(process)
            credentials = CredentialGenerationController(Store())
            credentials.save("offline-goal-observer-not-a-real-key", expected_generation=0)
            return NativeDshApplication(process, credentials=credentials)
        return GuiApi(data_dir, database_path=db_path, dsh_shell_factory=start, dsh_workspace_registry=registry)
    factory.dsh_projection_provider_mode = "deterministic_provider_transport"
    try:
        result = run_gui_agent_workflow(data_dir=tmp_path, prompt="Please complete this long-running offline fixture.",
            output_dir=tmp_path / "output", allow_draft=True, timeout_seconds=5,
            provider_mode="deterministic_provider_transport", api_factory=factory)
        assert result["receipt_publication"]["status"] == "published", result
        assert result["dsh_turn_end"]["reason"]["kind"] == "completed", result
        if mode == "complete":
            assert result["status"] == "draft_verified", result
            assert result["dsh_goal"]["phase"] == "complete"
            assert result["dsh_goal"]["roundsStarted"] == 1
            assert len(result["dsh_turn_ends"]) == 2
            assert result["assistant_reply"] == "Goal observer fixture closing reply 6"
            saved = json.loads(Path(result["board"]["path"]).read_text(encoding="utf-8"))
            assert saved["title"] == "Goal completed second turn"
            assert result["reload_verified"] is True
        elif mode == "round_limit":
            assert result["status"] == "blocked", result
            assert result["dsh_goal"]["phase"] == "blocked"
            assert result["dsh_goal"]["blockedReason"]["code"] == "round-limit"
            assert len(result["dsh_turn_ends"]) == 2
        elif mode == "paused":
            assert result["status"] == "paused", result
            assert result["dsh_goal"]["phase"] == "paused"
            assert len(result["dsh_turn_ends"]) == 1
        else:
            assert result["status"] == "draft_verified", result
            assert "dsh_goal" not in result
            assert len(result["dsh_turn_ends"]) == 1
        assert len(processes) == 1
    finally:
        for process in processes:
            process.close()


def test_public_cold_resume_preserves_candidate_history_goal_and_rejects_wrong_baseline(tmp_path, db_path):
    from genshin_app.whiteboard import new_whiteboard, save_whiteboard

    script = tmp_path / "resume-provider.json"
    script.write_text(json.dumps({"mode": "fail_for_resume"}), encoding="utf-8")
    processes, observed_apis = [], []

    def factory(data_dir):
        registry = NativeDshWorkspaceRegistry(data_dir / "workspaces", database_path=db_path)
        def start(_):
            profile, mirror = data_dir / "profile", data_dir / "media"
            profile.mkdir(exist_ok=True)
            mirror.mkdir(exist_ok=True)
            process = NativeDshMuxProcess(node=Path(os.environ["GENSHIN_NATIVE_NODE"]),
                entry=ROOT / "agent-runtime/src/native-dsh-goal-observer-fixture.mjs", working=ROOT,
                runtime_environment=isolated_runtime_environment(profile),
                entry_args=(str(profile), str(mirror), str(ROOT / "frontend"), str(script)),
                domain_handler=NativeDshHostRequests(registry, data_dir=data_dir),
                diagnostics_root=data_dir / "process-diagnostics")
            processes.append(process)
            credentials = CredentialGenerationController(Store())
            credentials.save("offline-cold-resume-not-a-real-key", expected_generation=0)
            return NativeDshApplication(process, credentials=credentials)
        api = GuiApi(data_dir, database_path=db_path, dsh_shell_factory=start, dsh_workspace_registry=registry)
        actual_rpc = api.dsh_client_rpc
        api.test_rpc_endpoints = []
        def observed_rpc(request):
            api.test_rpc_endpoints.append(request["endpoint"])
            return actual_rpc(request)
        api.dsh_client_rpc = observed_rpc
        observed_apis.append(api)
        return api
    factory.dsh_projection_provider_mode = "deterministic_provider_transport"
    options = {"data_dir": tmp_path, "api_factory": factory, "provider_mode": "deterministic_provider_transport",
               "timeout_seconds": 5, "allow_draft": True}
    try:
        failed = run_gui_agent_workflow(**options, prompt="Complete the offline long objective.", output_dir=tmp_path / "failed")
        assert failed["status"] == "failed", failed
        assert failed["dsh_turn_end"]["reason"]["error"]["code"] == "MALFORMED_RESPONSE"
        assert failed["dsh_goal"]["phase"] == "active" and failed["dsh_goal"]["activation"] == "disarmed"
        original_failed_bytes = (tmp_path / "failed/gui-workflow-receipt.json").read_bytes()
        script.write_text(json.dumps({"mode": "resume_goal"}), encoding="utf-8")
        resumed = run_gui_agent_workflow(**options, prompt="Continue this existing goal and finish it.",
            resume_session_id=failed["session_id"], output_dir=tmp_path / "resumed")
        assert resumed["status"] == "draft_verified", resumed
        assert resumed["session_id"] == failed["session_id"] and resumed["conversation_id"] == failed["conversation_id"]
        assert resumed["dsh_turn_end"]["turn"] == 2
        assert resumed["dsh_goal"]["id"] == failed["dsh_goal"]["id"] and resumed["dsh_goal"]["phase"] == "complete"
        recovery = resumed["resume"]
        assert recovery["candidate_revision_before"] == 1
        assert recovery["history_event_count_before"] > 0 and recovery["opening_history_records"] > 0
        assert recovery["goal_before"]["goal"]["id"] == failed["dsh_goal"]["id"]
        assert recovery["model_selection_reinitialized"] is False and recovery["candidate_reinitialized"] is False
        assert recovery["goal_resumed_by_observer"] is False
        user = resumed["submitted_user_message"]
        assert user["event_seq"] >= recovery["history_event_count_before"]
        assert user["source"]["kind"] == "user" and user["source"]["rpcId"] == resumed["prompt_request_id"]
        source = json.loads(Path(recovery["baseline_path"]).read_text(encoding="utf-8"))
        snapshot = json.loads(Path(recovery["source_snapshot_path"]).read_text(encoding="utf-8"))
        assert source == snapshot["input"]["source_document"]["payload"]
        assert source["title"] != snapshot["input"]["candidate"]["payload"]["title"]
        assert json.loads(Path(resumed["board"]["path"]).read_text(encoding="utf-8"))["title"] == "Cold resumed same candidate"
        resume_api = observed_apis[1]
        assert "session/create" not in resume_api.test_rpc_endpoints and "session/selectModel" not in resume_api.test_rpc_endpoints
        assert (tmp_path / "failed/gui-workflow-receipt.json").read_bytes() == original_failed_bytes
        wrong = tmp_path / "wrong.whiteboard.json"
        save_whiteboard(wrong, new_whiteboard())
        rejected = run_gui_agent_workflow(**options, prompt="Must not reach the model.", input_path=wrong,
            resume_session_id=failed["session_id"], output_dir=tmp_path / "wrong-baseline")
        assert rejected["status"] == "failed" and rejected["reason"] == "gui_workflow_resume_baseline_mismatch", rejected
        assert "session/prompt" not in observed_apis[-1].test_rpc_endpoints
        assert len(processes) == 3  # Separate cold processes, no fixture state migration.
    finally:
        for process in processes:
            process.close()
