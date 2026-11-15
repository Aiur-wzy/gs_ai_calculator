"""The native Host must retain the App catalog after a saved fractional prefix."""
from __future__ import annotations

import json
from contextlib import closing

from genshin_app.agent_chat.bridge_protocol import raw_json_hash
from genshin_app.agent_chat.native_dsh_host_requests import NativeDshHostRequests
from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry
from tests.test_energy_trials import plan
from tests.test_queries import db_path as db_path  # noqa: PLC0414


def test_saved_diagnostic_fractional_hit_keeps_native_catalog_and_recovery(tmp_path, db_path):
    session_id = "fractional-diagnostic"
    with closing(NativeDshWorkspaceRegistry(tmp_path / "workspaces", database_path=db_path)) as registry:
        registry.prepare_document(session_id, "native", plan())
        host = NativeDshHostRequests(registry, data_dir=tmp_path)

        def bind(request_id):
            return host("dsh_app_bind_request", {"contract": "genshin-native-dsh-domain-bind-v1",
                "request_id": request_id, "session_id": session_id})

        def invoke(snapshot, tool, arguments, step):
            raw = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
            return host("dsh_app_call_request", {"contract": "genshin-native-dsh-domain-call-v1",
                "request_id": f"request-{step}", "session_id": session_id, "turn": 1,
                "step": step, "call_id": f"call-{step}", "call_seq": step, "tool": tool,
                "arguments_json": raw, "raw_arguments_hash": raw_json_hash(raw),
                **{key: snapshot[key] for key in ("catalog_hash", "candidate_revision", "stage_revision",
                    "document_id", "database_fingerprint", "writer_fence", "tool_manifest_hash", "runtime_generation")}})

        initial = bind("before-execution")
        assert initial["binding_state"] == "bound", initial.get("binding_issue")
        rejection = invoke(initial["snapshot"], "gs_calculate", {"expected_revision": 0}, 1)
        assert rejection["status"] == "settled_failure", rejection
        result = rejection["result"]
        assert result["reason"] == "agent_calculation_rejected"
        feedback = result["details"]["execution_feedback"]
        assert feedback["execution"] == "diagnostic" and feedback["last_reported_hit_t"] == .2
        recovery = result["details"]["recovery"]

        # The next model request refreshes both the tools and prompt through this production bind.
        refreshed = bind("after-rejected-execution")
        assert refreshed["binding_state"] == "bound"
        assert refreshed["snapshot"]["candidate_revision"] == initial["snapshot"]["candidate_revision"] == 0
        assert {"gs_read_context", "gs_calculate", "gs_diagnose_result"} <= {
            tool["name"] for tool in refreshed["snapshot"]["tools"]}
        assert refreshed["runtime_context"]["stage"]["current_problem"] == recovery
        assert "metadata_issue" not in rejection
        diagnostic = invoke(refreshed["snapshot"], recovery["tool"], {
            "expected_revision": recovery["expected_revision"], "request": recovery["request"]}, 2)
        assert diagnostic["status"] == "settled_success", diagnostic
        first_failure = diagnostic["result"]["data"]["failure_summary"]["first_rejected_event"]
        assert first_failure["id"] == "q2" and first_failure["deficit"] == 2
