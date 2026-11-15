"""Input imports cross the actual Host, durable ledger and bridge, not only dispatch."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys

import pytest

from genshin_app.agent_chat.bridge_protocol import raw_json_hash
from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.agent_chat.native_app_call_ledger import NativeAppCallLedger
from genshin_app.agent_chat.native_app_tool_bridge import (
    NativeAppToolBridgeError,
    build_native_app_tool_settlement,
    validate_native_app_tool_settlement,
)
from genshin_app.agent_chat.native_app_tool_effects import (
    ALLOWED_CHANGES,
    artifact_representation_state_matches,
    stage_transition_proof,
)
from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry
from genshin_app.equipment.files import file_sha, input_view, json_bytes
from genshin_app.equipment.schema import PlanError, new_plan
from genshin_app.whiteboard import new_whiteboard
from tests.test_queries import db_path as db_path  # noqa: PLC0414


@pytest.fixture
def workspace(tmp_path, db_path):
    registry = NativeDshWorkspaceRegistry(tmp_path / "domain", database_path=db_path)
    board = new_whiteboard()
    registry.prepare_document("same-session", "whiteboard", board)
    scope = registry.activate("same-session")
    source = new_plan()
    source["manual_template"]["meta"]["name"] = "通用未闭合作者输入"
    path = tmp_path / "author.plan.json"
    path.write_bytes(json_bytes(source))
    try:
        yield registry, scope, board, source, path
    finally:
        registry.close()


def _call(scope, tool, args, *, call_id="import-1"):
    host = scope.tool_host
    snapshot = host.current_snapshot()
    raw = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
    request = {"conversation_id": scope.conversation_id, "turn_id": "dsh-turn-1", "step_id": "dsh-step-1",
        "epoch_id": snapshot["writer_fence"], "call_id": call_id, "tool_name": tool,
        "arguments_json": raw, "raw_arguments_hash": raw_json_hash(raw),
        **{key: snapshot[key] for key in ("document_id", "candidate_revision", "stage_revision",
            "database_fingerprint", "tool_manifest_hash", "runtime_generation", "writer_fence", "catalog_hash")}}
    return snapshot, request, host.execute(**request)


def _import(scope, path):
    return _call(scope, "gs_patch_whiteboard", {"mode": "import", "expected_revision": 0,
        "reason": "导入用户指定的作者输入；不转移结果或权限", "file_path": str(path), "expected_file_sha": file_sha(path)})


def test_real_import_settles_host_ledger_bridge_and_exact_lost_ack_replay(workspace):
    registry, scope, board, source, path = workspace
    before, request, terminal = _import(scope, path)
    session = scope.candidate_session
    assert terminal["terminal_state"] == "settled_success"
    assert terminal["disallowed_state_changes"] == []
    assert terminal["stage_transition"]["kind"] == "artifact_representation"
    assert terminal["state_changes"] == ["candidate_hash", "candidate_revision", "durable_checkpoint_hash",
                                        "stage_revision", "stage_state_hash", "tool_manifest_hash"]
    assert session.revision == session.stage_state["stage_revision"] == 1
    assert session.stage_state["phase"] == "loadout_draft"
    assert session.stage_state["focus_revision"] == 0
    assert session.stage_state["energy_intent"] is None
    assert session.document == board
    assert session.candidate["kind"] == "genshin-plan"
    assert session.candidate["manual_template"] == source["manual_template"]
    assert session.calculated is None
    assert session.summary()["ready"] is False
    payload = build_native_app_tool_settlement(terminal, tool_name="gs_patch_whiteboard", current_snapshot=before)
    assert validate_native_app_tool_settlement(payload, current_snapshot=before) == payload
    replay = scope.tool_host.execute(**request)
    assert replay["status"] == "replay"
    assert replay["result"] == terminal["result"]
    assert session.revision == 1
    build_native_app_tool_settlement(replay, tool_name="gs_patch_whiteboard", current_snapshot=before)
    cold_ledger = NativeAppCallLedger(scope.app_call_ledger.root, conversation_id=scope.conversation_id)
    assert cold_ledger.read()["entries"]["import-1"]["stage_transition"] == terminal["stage_transition"]
    restored = CandidateSession.load(session.session_id, session.root, session.db_path)
    assert restored.revision == 1
    assert restored.stage_state["representation"] == "native"
    assert restored.document == board
    assert input_view(restored.candidate) == input_view(session.candidate)
    # A subsequent ordinary edit has no new representation/generation grant.
    _, _, edited = _call(scope, "gs_patch_candidate", {"expected_revision": 1, "reason": "继续补全",
        "operations": [{"op": "replace", "path": "/manual_template/meta/name", "value": "后续编辑"}]}, call_id="edit-2")
    assert edited["terminal_state"] == "settled_success"
    assert edited["stage_transition"] is None
    assert session.stage_state["stage_revision"] == 1
    assert len(registry._workspaces) == 1


@pytest.mark.parametrize("mutation", ["focus", "focus_revision", "focus_bool", "focus_float", "history", "energy_intent", "generation",
                                     "document", "baseline", "candidate_revision", "future_field"])
def test_import_does_not_bless_unrelated_internal_state_changes(workspace, mutation):
    _, scope, board, _, path = workspace
    host = scope.tool_host
    execute = host._domain_write_executor

    def unexpected(current, name, arguments):
        result = execute(current, name, arguments)
        if mutation == "focus":
            current.stage_state["phase"] = "calculation_closure"
        elif mutation == "focus_revision":
            current.stage_state["focus_revision"] += 1
        elif mutation == "focus_bool":
            current.stage_state["focus_revision"] = False
        elif mutation == "focus_float":
            current.stage_state["focus_revision"] = 0.0
        elif mutation == "history":
            current.stage_state["history"].append({"unexpected": True})
        elif mutation == "energy_intent":
            current.stage_state["energy_intent"] = "multi_round_legal"
        elif mutation == "generation":
            current.stage_state["stage_revision"] += 1
        elif mutation == "document":
            current.document["title"] = "不能修改正式文档"
        elif mutation == "baseline":
            current.baseline["title"] = "不能改用户基线"
        elif mutation == "candidate_revision":
            current.revision += 1
        else:
            current.stage_state["future_field"] = "不能忽略未知副作用"
        current.save()
        return result

    host._domain_write_executor = unexpected
    before, request, terminal = _import(scope, path)
    assert terminal["terminal_state"] == "unknown_after_interruption"
    assert terminal["terminal_reason"] == "effect_mask_violation"
    assert terminal["stage_transition"] is None
    assert "result" not in terminal
    assert {"stage_revision", "tool_manifest_hash"} <= set(terminal["disallowed_state_changes"])
    # Private rejection must travel as UNKNOWN, rather than being replaced by
    # the bridge's weaker public-catalog-only representation inference.
    payload = build_native_app_tool_settlement(terminal, tool_name="gs_patch_whiteboard", current_snapshot=before)
    assert payload["terminal_state"] == "unknown_after_interruption"
    assert payload["stage_transition"] is None
    validate_native_app_tool_settlement(payload, current_snapshot=before)
    replay = host.execute(**request)
    assert replay["status"] == "replay"
    assert replay["terminal_state"] == "unknown_after_interruption"
    if mutation in {"generation", "candidate_revision"}:
        with pytest.raises(NativeAppToolBridgeError, match="native_app_settlement_stage_transition_mismatch"):
            build_native_app_tool_settlement(replay, tool_name="gs_patch_whiteboard", current_snapshot=before)
    else:
        replay_payload = build_native_app_tool_settlement(replay, tool_name="gs_patch_whiteboard", current_snapshot=before)
        assert replay_payload["stage_transition"] is None
        assert replay_payload["result_json"] is None
    # The immutable document writer also rejects this deliberate corruption.
    # Restore only that test injection so normal fixture shutdown can save.
    if mutation == "document":
        scope.candidate_session.document = copy.deepcopy(board)


def test_real_mutation_followed_by_failed_response_remains_unknown(workspace):
    _, scope, _, _, path = workspace
    execute = scope.tool_host._domain_write_executor

    def failed(current, name, arguments):
        execute(current, name, arguments)
        return {"code": 1, "reason": "invalid_plan", "message": "真实写入后未知失败"}

    scope.tool_host._domain_write_executor = failed
    _, _, terminal = _import(scope, path)
    assert terminal["terminal_state"] == "unknown_after_interruption"
    assert terminal["terminal_reason"] == "failed_tool_changed_state"
    assert "result" not in terminal


def test_invalid_source_fails_without_mutation_or_representation_proof(workspace):
    _, scope, _, _, path = workspace
    path.write_text('{"kind":"genshin-plan","result_weight":0.5}', encoding="utf8")
    _, _, terminal = _import(scope, path)
    assert terminal["terminal_state"] == "settled_failure"
    assert terminal["stage_transition"] is None
    assert terminal["state_changes"] == []
    assert scope.candidate_session.revision == 0
    assert scope.candidate_session.stage_state["representation"] == "whiteboard"


@pytest.mark.parametrize("field,value", [("workflow_stage", "numeric_model"), ("stage_revision", 2),
    ("candidate_revision", 2), ("tool_manifest_hash", "a" * 64)])
def test_peer_cannot_forge_representation_consequence(workspace, field, value):
    _, scope, _, _, path = workspace
    before, _, terminal = _import(scope, path)
    payload = build_native_app_tool_settlement(terminal, tool_name="gs_patch_whiteboard", current_snapshot=before)
    payload["next_snapshot"][field] = value
    from genshin_app.equipment.files import fingerprint
    payload["next_snapshot"]["catalog_hash"] = fingerprint({k: v for k, v in payload["next_snapshot"].items() if k != "catalog_hash"})
    with pytest.raises(NativeAppToolBridgeError):
        validate_native_app_tool_settlement(payload, current_snapshot=before)


def test_generic_candidate_mask_stays_strict_and_wrong_tools_cannot_claim_conversion(workspace):
    _, scope, _, _, path = workspace
    before, _, terminal = _import(scope, path)
    assert "stage_revision" not in ALLOWED_CHANGES["candidate"]
    assert "tool_manifest_hash" not in ALLOWED_CHANGES["candidate"]
    assert stage_transition_proof("candidate", before, terminal["next_snapshot"], tool_name="gs_add_character") is None
    forged = copy.deepcopy(terminal["next_snapshot"])
    forged["tools"] = copy.deepcopy(before["tools"])
    assert stage_transition_proof("candidate", before, forged, tool_name="gs_patch_whiteboard") is None
    entry = scope.app_call_ledger.read()["entries"]["import-1"]
    entry["identity"]["tool_name"] = "gs_patch_candidate"
    from genshin_app.agent_chat.native_app_call_ledger import _validate_entry
    with pytest.raises(PlanError):
        _validate_entry("import-1", entry, scope.conversation_id)


def test_bridge_does_not_manufacture_missing_host_import_proof(workspace):
    _, scope, _, _, path = workspace
    before, _, terminal = _import(scope, path)
    terminal["stage_transition"] = None
    with pytest.raises(NativeAppToolBridgeError, match="native_app_terminal_stage_transition_mismatch"):
        build_native_app_tool_settlement(terminal, tool_name="gs_patch_whiteboard", current_snapshot=before)


@pytest.mark.parametrize("value", [False, 0.0, float("nan"), object()])
def test_representation_state_comparison_is_json_type_sensitive_in_nested_fields(value):
    before = {"contract": "artifact-candidate-workflow-v1", "representation": "whiteboard", "stage_revision": 0,
              "calculation_basis": None, "history": [{"revision": 0}]}
    after = {**copy.deepcopy(before), "representation": "native", "stage_revision": 1}
    assert artifact_representation_state_matches(before, after)
    after["history"][0]["revision"] = value
    assert artifact_representation_state_matches(before, after) is False


def test_real_cli_import_and_native_host_edit_share_the_same_candidate(workspace, tmp_path):
    registry, scope, board, source, path = workspace
    registry.enable_cli_link()
    connection = registry.cli_connection("same-session")
    request = tmp_path / "cli-request.json"
    request.write_bytes(json_bytes({"expected_revision": 0, "reason": "CLI导入同份作者输入", "mode": "import",
                                   "file_path": str(path), "expected_file_sha": file_sha(path)}))
    result = subprocess.run([sys.executable, "-m", "genshin_app", "agent", "candidate", "edit", "--connection", connection,
        "--session-id", "same-session", "--request", str(request)], capture_output=True, text=True, encoding="utf8",
        timeout=30, env={**os.environ, "PYTHONUTF8": "1"}, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["data"]["revision"] == 1
    assert scope.candidate_session.candidate["manual_template"] == source["manual_template"]
    assert scope.candidate_session.document == board
    assert scope.candidate_session.stage_state["stage_revision"] == 1
    _, _, terminal = _call(scope, "gs_patch_candidate", {"expected_revision": 1, "reason": "原生工具继续同一输入",
        "operations": [{"op": "replace", "path": "/manual_template/meta/name", "value": "CLI后原生编辑"}]})
    assert terminal["terminal_state"] == "settled_success"
    assert scope.candidate_session.revision == 2
    assert scope.candidate_session.stage_state["stage_revision"] == 1
    assert len(registry._workspaces) == 1
