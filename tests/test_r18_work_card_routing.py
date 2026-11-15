"""Real candidate calls and native binding envelopes share advisory work cards."""
from __future__ import annotations

import copy
import json

import pytest

from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.agent_chat.native_app_binding_provider import CandidateNativeAppBindingProviderV2
from genshin_app.agent_chat.native_prompt_v2 import build_native_prompt_v2, validate_native_prompt_v2_bundle
from genshin_app.agent_chat.skill_context import current_focus_card, recommend_focus
from genshin_app.task_evidence import record_task_source
from genshin_app.whiteboard import new_whiteboard
from tests.test_agent_artifact_workflow import focus, native_plan
from tests.test_energy_trials import plan as energy_plan
from tests.test_native_app_binding_provider import app_database  # noqa: F401


def binding_for(tmp_path, app_database, document=None):
    database, _ = app_database
    document = document if document is not None else native_plan()
    provider = CandidateNativeAppBindingProviderV2()
    provider.prepare_create(conversation_id="routing", workflow="artifact",
        document_kind="whiteboard" if document.get("kind") == "genshin-whiteboard" else "native",
        document_seed=document, database_path=database)
    root = tmp_path / "conversation"
    root.mkdir()
    return provider.create(conversation_id="routing", session_root=root, workflow="artifact")


def assert_shared(binding, expected_focus, reason):
    context = binding.runtime_context()
    session = binding.candidate_session
    card = current_focus_card(context)  # The actual nested production envelope.
    recommendation = session.stage_context()["work_card_recommendation"]
    assert card["focus"] == expected_focus
    assert card["reason"] == reason
    assert {key: card[key] for key in recommendation} == recommendation
    assert recommend_focus(context) == recommendation
    bundle = build_native_prompt_v2(workflow="artifact", runtime_context=context)
    validate_native_prompt_v2_bundle(bundle.public())
    assert context["stage"]["current_stage_skill"] == card["resource"]
    assert context["stage"]["focus"] == session.workflow_stage
    assert card["focus_is_authorization"] is False
    return card


def test_successful_calculation_routes_real_binding_and_tool_receipt(tmp_path, app_database):
    binding = binding_for(tmp_path, app_database)
    session = binding.candidate_session
    manifest = binding.tool_manifest_hash()
    result = session.call_tool("gs_calculate", {"expected_revision": 0})
    assert result["code"] == 0, result
    card = assert_shared(binding, "calculation_closure", "calculation_completed")
    assert result["data"]["work_card_recommendation"]["focus"] == card["focus"]
    assert session.workflow_stage == "loadout_draft"
    assert binding.tool_manifest_hash() == manifest
    # Re-reading and a real repeat calculation do not change the page/dedup key.
    assert current_focus_card(binding.runtime_context()) == card
    assert session.call_tool("gs_calculate", {"expected_revision": 0})["code"] == 0
    assert current_focus_card(binding.runtime_context()) == card


def test_current_failure_then_real_edit_invalidates_old_failure(tmp_path, app_database):
    binding = binding_for(tmp_path, app_database, energy_plan())
    session = binding.candidate_session
    result = session.call_tool("gs_calculate", {"expected_revision": 0})
    assert result["code"] == 1 and result["reason"] == "agent_calculation_rejected", result
    assert_shared(binding, "action_checkpoint", "current_failure")
    assert focus(session, "event_topology")["code"] == 0
    assert_shared(binding, "action_checkpoint", "current_failure")
    edited = session.call_tool("gs_patch_candidate", {"expected_revision": session.revision,
        "reason": "修改数值输入", "operations": [
            {"op": "replace", "path": "/manual_template/hits/0/multiplier", "value": 2}]})
    assert edited["code"] == 0, edited
    context = binding.runtime_context()
    assert context["candidate"]["latest_attempt"]["matches_current_input"] is False
    assert_shared(binding, "numeric_model", "input_edited")
    assert session.workflow_stage == "event_topology"
    restored = CandidateSession.load(session.session_id, session.root, session.db_path)
    assert restored.stage_context()["work_card_recommendation"] == session.stage_context()["work_card_recommendation"]


def test_only_committed_edits_route_and_failed_save_rolls_back(tmp_path, app_database, monkeypatch):
    binding = binding_for(tmp_path, app_database)
    session = binding.candidate_session
    assert focus(session, "event_topology")["code"] == 0
    request = {"expected_revision": 0, "reason": "预览面板变更", "preview": True,
               "operations": [{"op": "replace", "path": "/manual_template/characters/0/stats/atk", "value": 200}]}
    assert session.call_tool("gs_patch_candidate", request)["data"]["edit_status"] == "preview"
    assert_shared(binding, "event_topology", "manual_focus")
    request.pop("preview")
    with monkeypatch.context() as patch:
        patch.setattr(session, "save", lambda: (_ for _ in ()).throw(OSError("save failed")))
        assert session.call_tool("gs_patch_candidate", request)["code"] == 1
    assert session.changes == [] and session.revision == 0
    assert_shared(binding, "event_topology", "manual_focus")
    assert session.call_tool("gs_patch_candidate", request)["code"] == 0
    card = assert_shared(binding, "loadout_draft", "input_edited")
    changes = copy.deepcopy(session.changes)
    request["expected_revision"] = session.revision
    assert session.call_tool("gs_patch_candidate", request)["data"]["edit_status"] == "no_op"
    assert session.changes == changes
    assert current_focus_card(binding.runtime_context()) == card


def test_explicit_task_difference_routes_but_plain_question_does_not(tmp_path, app_database):
    binding = binding_for(tmp_path, app_database)
    session = binding.candidate_session
    record_task_source(session, "请解释充能效率是什么意思。", "message:1")
    card = assert_shared(binding, "loadout_draft", "manual_focus")
    assert card["next_resources"] == []
    result = session.call_tool("gs_record_finding", {"record": {
        "id": "task.check", "type": "requirement", "description": "核对已指定的两次爆发",
        "disposition": "not_implemented"}})
    assert result["code"] == 0, result
    card = assert_shared(binding, "calculation_closure", "task_differences")
    assert card["next_resources"] == ["task_evidence"]
    assert session.workflow_stage == "loadout_draft"
    assert session.call_tool("gs_calculate", {"expected_revision": 0})["code"] == 0
    assert_shared(binding, "calculation_closure", "task_differences")


def test_whiteboard_action_edit_uses_committed_sections(tmp_path, app_database):
    document = new_whiteboard()
    binding = binding_for(tmp_path, app_database, document)
    session = binding.candidate_session
    result = session.call_tool("gs_patch_whiteboard", {"expected_revision": 0,
        "reason": "调整动作时间窗口", "operations": [
            {"op": "replace", "path": "/duration", "value": document["duration"] + 1}]})
    assert result["code"] == 0, result
    assert_shared(binding, "action_checkpoint", "input_edited")
    rejected = session.call_tool("gs_compose", {"expected_revision": session.revision})
    assert rejected["code"] == 1
    assert_shared(binding, "calculation_closure", "current_failure")


def test_real_workspace_model_metadata_retains_current_card(tmp_path, app_database):
    from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry

    database, _ = app_database
    registry = NativeDshWorkspaceRegistry(tmp_path / "workspace", database_path=database, workflow="artifact")
    try:
        registry.prepare_document("one", "native", native_plan())
        scope = registry.activate("one")
        assert scope.candidate_session.call_tool("gs_calculate", {"expected_revision": 0})["code"] == 0
        metadata = registry._model_metadata(scope)
        card = metadata["prompt_bundle"]["current_focus_card"]
        assert card["focus"] == "calculation_closure"
        assert card["reason"] == "calculation_completed"
        assert card == current_focus_card(metadata["runtime_context"])
        assert registry._model_metadata(scope)["prompt_bundle"] == metadata["prompt_bundle"]
    finally:
        registry.close()


def test_new_compose_supersedes_committed_edit(tmp_path, app_database):
    binding = binding_for(tmp_path, app_database)
    session = binding.candidate_session
    assert session.call_tool("gs_patch_candidate", {"expected_revision": 0, "reason": "调整面板",
        "operations": [{"op": "replace", "path": "/manual_template/characters/0/stats/atk", "value": 200}]})["code"] == 0
    assert_shared(binding, "loadout_draft", "input_edited")
    assert session.call_tool("gs_compose", {"expected_revision": session.revision})["code"] == 0
    assert_shared(binding, "numeric_model", "composition_completed")


@pytest.mark.parametrize("matches", [False, None])
def test_stale_or_unproven_failure_never_overrides_manual_intent(matches):
    assert recommend_focus({"focus": "event_topology", "latest_attempt": {
        "state": "failed", "reason": "energy_insufficient", "matches_current_input": matches}})["focus"] == "event_topology"


@pytest.mark.parametrize("summary", [False, True])
def test_actual_plain_cli_calculation_returns_same_review_card(tmp_path, capsys, monkeypatch, summary):
    from genshin_app.cli import main

    # Test the public command after runtime admission, without changing its gate.
    monkeypatch.setattr("genshin_app.cli._require_runtime_database", lambda _: {"can_start": True})
    source = tmp_path / "plan.json"
    source.write_text(json.dumps(native_plan()), encoding="utf-8")
    argv = ["--data-dir", str(tmp_path), "equipment", "calc", "--plan", str(source)]
    if summary:
        argv.extend(["--summary", "--output", str(tmp_path / "result.json")])
    assert main(argv) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    recommendation = data["work_card_recommendation"]
    assert recommendation == recommend_focus({"latest_attempt": {
        "operation": "gs_calculate", "state": "succeeded", "matches_current_input": True}})
    assert recommendation["focus"] == "calculation_closure"


def test_actual_plain_cli_failure_uses_same_current_failure_router(tmp_path, capsys, monkeypatch):
    from genshin_app.cli import main

    monkeypatch.setattr("genshin_app.cli._require_runtime_database", lambda _: {"can_start": True})
    source = tmp_path / "plan.json"
    source.write_text(json.dumps(energy_plan()), encoding="utf-8")
    assert main(["--data-dir", str(tmp_path), "equipment", "calc", "--plan", str(source)]) == 1
    failure = json.loads(capsys.readouterr().err)
    assert failure["work_card_recommendation"]["reason"] == "current_failure"
    assert failure["work_card_recommendation"]["focus"] == "action_checkpoint"


def test_actual_plain_cli_edit_and_explicit_task_workspace_share_router(tmp_path, capsys):
    from genshin_app.cli import main

    source, request, output = (tmp_path / name for name in ("plan.json", "request.json", "next.json"))
    source.write_text(json.dumps(native_plan()), encoding="utf-8")
    request.write_text(json.dumps({"operations": [
        {"op": "replace", "path": "/manual_template/hits/0/multiplier", "value": 2}]}), encoding="utf-8")
    assert main(["--data-dir", str(tmp_path), "equipment", "edit", "--plan", str(source),
                 "--request", str(request), "--output", str(output)]) == 0
    card = json.loads(capsys.readouterr().out)["data"]["work_card_recommendation"]
    assert card["focus"] == "numeric_model" and card["reason"] == "input_edited"
    request.write_text(json.dumps({"operation": "append", "records": [
        {"id": "r1", "type": "requirement", "description": "核对指定动作", "disposition": "pending"}]}), encoding="utf-8")
    task, audit = tmp_path / "task.txt", tmp_path / "audit.json"
    task.write_text("计算并核对指定动作。", encoding="utf-8")
    argv = ["--data-dir", str(tmp_path), "workflow", "workspace", "--plan", str(output), "--audit", str(audit)]
    assert main([*argv, "--request", str(request), "--task-file", str(task)]) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["work_card_recommendation"] == recommend_focus({"task_review": data["task_review"]})
    assert data["work_card_recommendation"]["reason"] == "task_differences"
    assert main(argv) == 0
    assert json.loads(capsys.readouterr().out)["data"]["work_card_recommendation"] == data["work_card_recommendation"]
