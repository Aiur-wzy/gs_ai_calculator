"""Portable draft round trips never manufacture calculation or control rights."""
from __future__ import annotations

import copy
import uuid

import pytest

from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.agent_chat.candidate_input_draft import (
    build_draft,
    decode_draft,
    portable_document,
    preview_draft,
    validate_draft,
)
from genshin_app.agent_chat.candidate_input_draft_store import (
    CandidateInputDraftStore,
    load_candidate_input_draft,
)
from genshin_app.agent_chat.native_app_action_controller import (
    NativeAppActionControllerError,
)
from genshin_app.agent_chat.native_app_action_tools import REQUEST_ACTION_TOOL
from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry
from genshin_app.agent_chat.native_dsh_workspace_compatibility import (
    current_workspace_identity,
)
from genshin_app.equipment.files import fingerprint, json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_app.gui.api import GuiApi
from genshin_app.whiteboard import make_handoff, new_whiteboard
from tests.test_native_dsh_file_actions import workspace
from tests.test_queries import db_path as db_path  # noqa: PLC0414
from tests.test_whiteboard import board_fixture


@pytest.fixture
def session(tmp_path):
    return ArtifactCandidateSession(str(uuid.uuid4()), tmp_path / "session", tmp_path / "absent.db",
                                    "whiteboard", new_whiteboard())


def draft(session):
    return build_draft(session, workspace_identity=current_workspace_identity(),
                       database_release_id="fixture-db", producer={"app_version": "test", "app_release_id": "fixture"})


def _rehash(value):
    inputs, evidence = value["input"], value["evidence"]
    value["fingerprints"] = {"source_document_input": fingerprint(inputs["source_document"]),
        "candidate_input": fingerprint(inputs["candidate"]), "evidence": fingerprint(evidence),
        "input_snapshot": fingerprint({"input": inputs, "evidence": evidence})}


def test_empty_input_round_trip_does_not_need_actor_action_or_calculation(session):
    value = draft(session)
    assert decode_draft(json_bytes(value)) == value
    assert value["input"]["candidate"]["payload"]["team"] == []
    assert not {"calculated", "receipt_fingerprint", "session_id"} & value.keys()
    preview = preview_draft(value, workspace_identity=current_workspace_identity(),
                            database_fingerprint=fingerprint(session.database_snapshot))
    assert preview["compatible"] and not preview["has_calculation"] and not preview["can_apply"]


def test_full_whiteboard_input_and_native_partial_input_are_both_preserved(session):
    board = board_fixture()
    board["board_id"] = session.document["board_id"]
    board["notes"] = "用户原始意图不应该由原生摘要替代"
    session.document = copy.deepcopy(board)
    session.candidate = make_handoff(board)
    session.stage_state["representation"] = "native"
    session.candidate["extensions"]["event_topology"] = {"pending_note": "保留完整输入扩展；不授执行权"}
    value = draft(session)
    assert value["input"]["source_document"]["payload"] == board
    restored = value["input"]["candidate"]["payload"]
    assert restored["mechanics_model"] is None
    assert restored["extensions"]["whiteboard_handoff"]["board"] == board
    assert restored["extensions"]["event_topology"] == session.candidate["extensions"]["event_topology"]
    assert len(restored["rotation_model"]["actions"]) == 1
    assert decode_draft(json_bytes(value)) == value


@pytest.mark.parametrize("field", ["writer_fence", "raw_reasoning", "api_key", "dsh_session_id"])
def test_control_or_dsh_data_cannot_be_laundered_through_notes_or_resources(session, field):
    session.resources["query-1"] = {field: "not-portable"}
    with pytest.raises(PlanError, match="控制权限"):
        draft(session)


def test_self_consistent_hash_does_not_authorize_injected_public_approval(session):
    from tests.test_whiteboard import provisional_board_fixture

    board = provisional_board_fixture()
    board["provisional_entities"][0]["approval"] = {"fake": "old user approval"}
    portable = portable_document(board)
    assert portable["payload"]["provisional_entities"][0]["approval"] is None
    assert portable["payload"]["provisional_entities"][0]["values"] == board["provisional_entities"][0]["values"]


def test_duplicate_json_keys_and_tampered_fingerprints_are_rejected(session):
    value = draft(session)
    value["input"]["candidate"]["payload"]["title"] = "tamper"
    with pytest.raises(PlanError, match="完整性"):
        validate_draft(value)
    with pytest.raises(PlanError, match="重复"):
        decode_draft(b'{"kind":"first","kind":"second"}')


def test_resource_body_descriptors_and_findings_survive_without_authority(session):
    session.resources["query-1"] = {"fact": {"duration": 20, "source": "fixture"}}
    session.findings = [{"id": "missing.a", "type": "issue", "description": "仍需补数值"}]
    value = draft(session)
    assert value["evidence"]["resources"][0]["data"] == session.resources["query-1"]
    assert value["evidence"]["findings"][0]["record"] == session.findings[0]
    value["evidence"]["resources"][0]["resource_id"] = "../key"
    _rehash(value)
    with pytest.raises(PlanError, match="逻辑名"):
        validate_draft(value)


def test_store_cas_and_finding_only_changes_invalidate_exact_snapshot(session, tmp_path):
    store = CandidateInputDraftStore(session, current_workspace_identity)
    target = store.capture(0)
    path = tmp_path / "draft.gencdraft.json"
    saved = store.publish(target, path)
    assert saved["saved"] and saved["file_sha"] == load_candidate_input_draft(path)["file_sha"]
    with pytest.raises(PlanError, match="外部修改"):
        store.publish(target, path, expected_file_sha="f"*64, overwrite=True)
    session.findings = [{"id": "missing.a", "type": "issue", "description": "只是资料改变"}]
    assert session.revision == 0
    with pytest.raises(PlanError, match="候选已更新"):
        store.read_target(target)


def test_file_card_exports_captured_input_even_after_agent_continues_editing(tmp_path, db_path):
    registry = NativeDshWorkspaceRegistry(tmp_path / "workspaces", database_path=db_path)
    registry.prepare_document("sid", "whiteboard", new_whiteboard())
    scope = registry.activate("sid")
    document_id = scope.binding.domain_checkpoint()["document_id"]
    registry.update_workspace_state(scope.conversation_id, current_document_id=document_id, workspace=workspace())
    response = registry.request_candidate_input_draft(scope.conversation_id, expected_revision=0)
    card = response["card"]
    target = card["target"]
    original = scope.action_controller.candidate_draft_store.read_target(target, check_current=False)
    patched = scope.candidate_session.call_tool("gs_patch_whiteboard", {"expected_revision": 0,
        "reason": "导出快照后继续编辑", "operations": [{"op": "replace", "path": "/title", "value": "已经继续编辑"}]})
    assert patched["code"] == 0
    presented = registry.app_action(scope.conversation_id, "present", {"action_id": card["action_id"]})["card"]
    registry.app_action(scope.conversation_id, "claim", {"action_id": card["action_id"], "ui_receipt": presented["ui_receipt"]})
    output = tmp_path / "captured.gencdraft.json"
    saved = registry.publish_candidate_input_draft(scope.conversation_id, action_id=card["action_id"],
        ui_receipt=presented["ui_receipt"], output_path=str(output), expected_file_sha=None, overwrite=False)
    assert saved["saved"]
    assert load_candidate_input_draft(output)["draft"] == original
    assert scope.candidate_session.candidate["title"] == "已经继续编辑"
    registry.close()


def test_store_cannot_overwrite_a_formal_document_even_with_known_sha(session, tmp_path):
    path = tmp_path / "formal.gencdraft.json"
    path.write_bytes(json_bytes(new_whiteboard()))
    store = CandidateInputDraftStore(session, current_workspace_identity)
    before = path.read_bytes()
    with pytest.raises(PlanError):
        store.publish(store.capture(), path, expected_file_sha=fingerprint(new_whiteboard()), overwrite=True)
    assert path.read_bytes() == before


def test_overwrite_never_waives_file_sha_cas(session, tmp_path):
    store = CandidateInputDraftStore(session, current_workspace_identity)
    target = store.capture()
    path = tmp_path / "existing.gencdraft.json"
    first = store.publish(target, path)
    original = path.read_bytes()
    with pytest.raises(PlanError, match="外部修改"):
        store.publish(target, path, overwrite=True)
    assert path.read_bytes() == original
    modified = load_candidate_input_draft(path)["draft"]
    modified["producer"]["app_release_id"] = "another-actual-user-edit"
    path.write_bytes(json_bytes(modified))
    changed = path.read_bytes()
    with pytest.raises(PlanError, match="外部修改"):
        store.publish(target, path, expected_file_sha=first["file_sha"], overwrite=True)
    assert path.read_bytes() == changed


def test_envelope_does_not_reduce_existing_native_depth_budget(tmp_path):
    from genshin_app.equipment.schema import new_plan, validate_plan

    plan = new_plan()
    plan["extensions"] = {"portable": {}}
    cursor = plan["extensions"]["portable"]
    for _ in range(95):
        cursor["x"] = {}
        cursor = cursor["x"]
    validate_plan(plan)
    source = ArtifactCandidateSession(str(uuid.uuid4()), tmp_path / "deep", tmp_path / "missing.db", "native", plan)
    value = draft(source)
    assert decode_draft(json_bytes(value)) == value


def test_native_handoff_conflict_is_preserved_pending_and_cold_recoverable(tmp_path, db_path):
    source = NativeDshWorkspaceRegistry(tmp_path / "before", database_path=db_path)
    source.prepare_document("s", "whiteboard", board_fixture())
    original = source.activate("s").candidate_session
    original.transition_phase("event_topology", 0, 0, "准备原生输入", prepare_calculation=True)
    value = draft(original)
    current = value["input"]["candidate"]["payload"]
    current["rotation_model"]["actions"][0]["start"] = 2.25
    _rehash(value)
    preview = preview_draft(value, workspace_identity=current_workspace_identity(),
                             database_fingerprint=fingerprint(original.database_snapshot))
    assert preview["input_issues"][0]["code"] == "intent_alignment_pending"
    fresh = NativeDshWorkspaceRegistry(tmp_path / "after", database_path=db_path)
    fresh.prepare_candidate_input_draft("new", value)
    restored = fresh.activate("new").candidate_session
    assert restored.candidate["rotation_model"]["actions"][0]["start"] == 2.25
    assert restored.candidate["extensions"]["whiteboard_handoff"]["board"]["actions"][0]["start"] == 2.25
    assert restored.summary()["input_issues"] == []
    restored.compose(restored.revision)
    root, identity = restored.root, restored.session_id
    fresh.close()
    assert CandidateSession.load(identity, root, db_path).summary()["input_issues"] == []
    source.close()


def test_complete_gui_projection_exports_candidate_not_empty_formal_board_and_cold_reopens(tmp_path, db_path):
    registry = NativeDshWorkspaceRegistry(tmp_path / "workspaces", database_path=db_path)
    registry.prepare_document("source", None, None)
    scope = registry.activate("source")
    # Engineering domain input differs from the still-empty formal document.
    board = board_fixture()
    actor, action = board["team"][0], board["actions"][0]
    board["team"] = [{**copy.deepcopy(actor), "id": f"actor.{index}", "db_character_id": 90000001+index,
                      "name": f"工程角色{index}"} for index in range(4)]
    board["actions"] = [{**copy.deepcopy(action), "id": f"action.{index}", "character": f"actor.{index % 4}", "start": index*0.5} for index in range(30)]
    board["board_id"] = scope.candidate_session.document["board_id"]
    scope.candidate_session.candidate = board
    scope.candidate_session.revision = 1
    scope.candidate_session.save()
    registry.update_workspace_state(scope.conversation_id, current_document_id=None, workspace=workspace())
    with scope.action_controller.agent_call():
        queued = scope.action_controller.call_model_tool(REQUEST_ACTION_TOOL, {
            "action": "export_candidate_input_draft", "reason": "保留未计算输入", "expected_revision": 1})
    card = registry.app_action(scope.conversation_id, "present", {"action_id": queued["card"]["action_id"]})["card"]
    identity = {"action_id": card["action_id"], "ui_receipt": card["ui_receipt"]}
    claimed = registry.app_action(scope.conversation_id, "claim", identity)
    assert claimed["request"]["target"]["kind"] == "candidate_input_draft"
    path = tmp_path / "user-choice.gencdraft.json"
    api = GuiApi(tmp_path / "gui", database_path=db_path, dsh_workspace_registry=registry)
    saved = api.dsh_client_publish_candidate_input_draft({"conversation_id": scope.conversation_id,
        **identity, "output_path": str(path), "expected_file_sha": None, "overwrite": False})
    assert saved["code"] == 0 and saved["data"]["saved"]
    result = registry.app_action(scope.conversation_id, "settle", {**identity, "outcome": {
        "status": "completed", "message": "候选输入草稿已保存", "dirty_open_guard_passed": False}})
    assert result["card"]["status"] == "completed"
    loaded = load_candidate_input_draft(path)["draft"]
    assert loaded["input"]["source_document"]["payload"]["team"] == []
    assert len(loaded["input"]["candidate"]["payload"]["team"]) == 4
    assert len(loaded["input"]["candidate"]["payload"]["actions"]) == 30
    assert scope.candidate_session.document["team"] == []
    registry.close()
    target = NativeDshWorkspaceRegistry(tmp_path / "fresh", database_path=db_path)
    imported = target.prepare_candidate_input_draft("new-unstarted", loaded)
    assert imported["imported"]
    fresh = target.activate("new-unstarted")
    assert fresh.candidate_session.candidate == board
    assert fresh.candidate_session.calculated is None
    assert not list((tmp_path / "fresh").rglob("*.jsonl"))
    root, session_id = fresh.candidate_session.root, fresh.candidate_session.session_id
    target.close()
    cold = CandidateSession.load(session_id, root, db_path)
    assert cold.candidate == board and cold.calculated is None


def test_forged_completed_outcome_without_app_file_proof_is_rejected(tmp_path, db_path):
    registry = NativeDshWorkspaceRegistry(tmp_path / "domain", database_path=db_path)
    registry.prepare_document("s", None, None)
    scope = registry.activate("s")
    with scope.action_controller.agent_call():
        result = scope.action_controller.call_model_tool(REQUEST_ACTION_TOOL,
            {"action": "export_candidate_input_draft", "reason": "保存草稿", "expected_revision": 0})
    card = registry.app_action(scope.conversation_id, "present", {"action_id": result["card"]["action_id"]})["card"]
    args = {"action_id": card["action_id"], "ui_receipt": card["ui_receipt"]}
    registry.app_action(scope.conversation_id, "claim", args)
    with pytest.raises(NativeAppActionControllerError, match="publication_unconfirmed"):
        registry.app_action(scope.conversation_id, "settle", {**args, "outcome": {
            "status": "completed", "message": "伪造已写入", "dirty_open_guard_passed": False}})
    registry.close()


def test_healthy_model_turn_does_not_block_frozen_draft_file_processing(tmp_path, db_path):
    running = [True]
    registry = NativeDshWorkspaceRegistry(tmp_path / "domain", database_path=db_path,
        session_status_reader=lambda sid: {"session_id": sid, "agent_running": running[0], "admission": {"state": "idle"}})
    registry.prepare_document("s", None, None)
    scope = registry.activate("s")
    with scope.action_controller.agent_call():
        queued = scope.action_controller.call_model_tool(REQUEST_ACTION_TOOL,
            {"action": "export_candidate_input_draft", "reason": "保存草稿", "expected_revision": 0})
    assert registry.app_action(scope.conversation_id, "present", {"action_id": queued["card"]["action_id"]})["card"]["status"] == "presented"
    assert running[0] is True
    registry.close()


def test_native_partial_input_cold_reloads_and_resources_stay_historical(tmp_path, db_path):
    source = NativeDshWorkspaceRegistry(tmp_path / "source", database_path=db_path)
    board = board_fixture()
    source.prepare_document("s", "whiteboard", board)
    scope = source.activate("s")
    session = scope.candidate_session
    response = session.transition_phase("event_topology", 0, 0, "工程白板转原生", prepare_calculation=True)
    assert response["representation"] == "native"
    session.resources["query-1"] = {"fact": "历史知识仍可读"}
    exported = draft(session)
    fresh = NativeDshWorkspaceRegistry(tmp_path / "fresh-native", database_path=db_path)
    fresh.prepare_candidate_input_draft("new", exported)
    restored = fresh.activate("new").candidate_session
    assert restored.candidate["mechanics_model"] is None
    projection = restored.candidate["extensions"]["whiteboard_handoff"]["board"]
    assert projection["actions"] == board["actions"]
    assert projection["team"][0]["panel"] == board["team"][0]["panel"]
    assert restored.document == board
    assert restored.resources["query-1"] == session.resources["query-1"]
    assert "query-1" not in restored.stage_state["resource_descriptors"]
    identity, root = restored.session_id, restored.root
    fresh.close()
    cold = CandidateSession.load(identity, root, db_path)
    assert cold.candidate == restored.candidate
    assert cold.resources == restored.resources and cold.calculated is None
    source.close()


def test_user_file_picker_imports_without_starting_dsh_and_old_identity_stays_read_only(tmp_path, db_path):
    registry = NativeDshWorkspaceRegistry(tmp_path / "domain", database_path=db_path)
    registry.prepare_document("source", None, None)
    source = registry.activate("source").candidate_session
    value = draft(source)
    path = tmp_path / "input.gencdraft.json"
    path.write_bytes(json_bytes(value))
    api = GuiApi(tmp_path / "gui", database_path=db_path, dsh_workspace_registry=registry,
                 dsh_file_dialog=lambda _: str(path))
    result = api.dsh_client_open_candidate_input_draft({"dsh_session_id": "blank"})
    assert result["code"] == 0 and result["data"]["imported"]
    assert api._dsh_shell is None and api._agent_manager is None
    value["compatibility"]["workspace_identity"]["app_artifact_identity_hash"] = "f"*64
    path.write_bytes(json_bytes(value))
    incompatible = api.dsh_client_open_candidate_input_draft({"dsh_session_id": "readonly"})
    assert incompatible["data"]["preview"]["read_only"]
    assert registry.lookup_binding("readonly") is None
    page = api.dsh_client_candidate_input_draft_preview_read({"preview_id": incompatible["data"]["preview_id"],
        "resource": "candidate", "pointer": "/payload", "offset": 0, "limit": 20})
    assert page["code"] == 0
    registry.close()


def test_legacy_unknown_effect_is_closed_in_v1_without_execution_or_upgrade(tmp_path):
    from genshin_app.agent_chat.native_app_action_controller import (
        NativeAppActionCheckpoint,
        NativeAppActionController,
    )
    from genshin_app.agent_chat.native_app_action_ledger import (
        LEGACY_CONTRACT,
        LEGACY_LEDGER_FILE,
        NativeAppActionLedger,
    )
    from tests.test_native_app_action_controller import _binding, _workspace

    current = {"binding": _binding(), "workspace": _workspace()}
    original = NativeAppActionLedger(tmp_path / "make", conversation_id="conversation-1")
    queued = original.request(expected_revision=0, action="save_as", reason="旧保存操作", suggested_name=None,
                              binding=current["binding"], workspace=current["workspace"])
    card = original.present(action_id=queued["card"]["action_id"], agent_turn_active=False,
                             current_binding=current["binding"], current_workspace=current["workspace"])["card"]
    legacy_value = original.read()
    legacy_value["contract"] = LEGACY_CONTRACT
    for row in legacy_value["cards"]:
        row.pop("target")
    root = tmp_path / "old"
    root.mkdir()
    (root / LEGACY_LEDGER_FILE).write_bytes(json_bytes(legacy_value))
    legacy = NativeAppActionLedger(root, conversation_id="conversation-1", legacy_recovery=True)
    checkpoint = NativeAppActionCheckpoint(root, conversation_id="conversation-1", legacy_recovery=True)
    checkpoint.sync(legacy.read())
    checkpoint.persist_execution_marker(action_id=card["action_id"], ui_receipt=card["ui_receipt"])
    checkpoint.claim_execution(ledger=legacy.read(), action_id=card["action_id"], ui_receipt=card["ui_receipt"])
    old_marker = root / ("native-app-action-execution-" + card["action_id"] + ".json")
    marker_bytes = old_marker.read_bytes()
    controller = NativeAppActionController(ledger=NativeAppActionLedger(root, conversation_id="conversation-1"),
        context_reader=lambda: copy.deepcopy(current), callbacks={"save_as": lambda _: pytest.fail("old effect replayed")})
    assert old_marker.read_bytes() == marker_bytes
    assert legacy.read()["contract"] == LEGACY_CONTRACT
    assert legacy.read()["cards"][0]["status"] == "failed"
    assert "结果未知" in legacy.read()["cards"][0]["result_message"]
    assert controller.ledger.read()["cards"] == []
    assert controller.list_cards()[0]["legacy_read_only"]
    assert not list(root.glob("native-app-action-execution-v2-*.json"))


@pytest.mark.parametrize("failure_window", ["input_write", "activation"])
def test_import_failure_rolls_back_new_binding_and_same_unstarted_entry_can_retry(tmp_path, db_path, monkeypatch, failure_window):
    import genshin_app.agent_chat.native_dsh_workspace as workspace_module

    source = NativeDshWorkspaceRegistry(tmp_path / "source", database_path=db_path)
    source.prepare_document("s", None, None)
    value = draft(source.activate("s").candidate_session)
    target = NativeDshWorkspaceRegistry(tmp_path / "target", database_path=db_path)
    if failure_window == "input_write":
        original = workspace_module.write_owned
        def fail_write(root, name, *args, **kwargs):
            if name == "candidate-input-import.json":
                raise OSError("injected interrupted publication")
            return original(root, name, *args, **kwargs)
        monkeypatch.setattr(workspace_module, "write_owned", fail_write)
    else:
        original_activate = target.activate
        monkeypatch.setattr(target, "activate", lambda _: (_ for _ in ()).throw(OSError("injected activation failure")))
    with pytest.raises(OSError):
        target.prepare_candidate_input_draft("retry", value)
    assert target.lookup_binding("retry") is None
    assert target.resolve_domain_route("retry") is None
    if failure_window == "input_write":
        monkeypatch.setattr(workspace_module, "write_owned", original)
    else:
        monkeypatch.setattr(target, "activate", original_activate)
    assert target.prepare_candidate_input_draft("retry", value)["imported"]
    assert not list((tmp_path / "target").rglob("*.jsonl"))
    target.close()
    source.close()


def test_historical_unresolved_evidence_survives_import_and_another_export(tmp_path, db_path):
    source = NativeDshWorkspaceRegistry(tmp_path / "source", database_path=db_path)
    source.prepare_document("s", None, None)
    original = source.activate("s").candidate_session
    original.resources["query-1"] = {"fact": "工程历史资料"}
    value = draft(original)
    value["evidence"]["unresolved_references"] = [{"owner_pointer": "/input/candidate/payload/notes",
        "reference": None, "reason": "nonportable_control_reference"}]
    _rehash(value)
    target = NativeDshWorkspaceRegistry(tmp_path / "target", database_path=db_path)
    target.prepare_candidate_input_draft("new", value)
    restored = target.activate("new").candidate_session
    again = draft(restored)
    assert again["evidence"] == value["evidence"]
    assert restored.stage_context()["historical_resource_index"][0]["status"] == "pending_verification"
    target.close()
    source.close()


def test_overlong_resource_name_is_rejected_before_any_workspace_is_reserved(session):
    value = draft(session)
    value["evidence"]["resources"] = [{"resource_id": "query-" + "9"*500,
        "descriptor": {"kind": "agent_note", "schema_version": 1, "title": "bad id", "source_refs": [], "used_at": []},
        "data": {}, "fingerprint": fingerprint({})}]
    _rehash(value)
    with pytest.raises(PlanError, match="逻辑名"):
        validate_draft(value)


def test_ordinary_inspection_and_exact_user_import_accept_draft_larger_than_plan_limit(tmp_path, db_path):
    source = NativeDshWorkspaceRegistry(tmp_path / "source", database_path=db_path)
    source.prepare_document("s", None, None)
    original = source.activate("s").candidate_session
    for index in range(1, 4):
        original.resources[f"query-{index}"] = {"body": "x"*(3*1024*1024)}
    value = draft(original)
    path = tmp_path / "user-selected.json"
    path.write_bytes(json_bytes(value))
    assert path.stat().st_size > 8*1024*1024
    target = NativeDshWorkspaceRegistry(tmp_path / "target", database_path=db_path)
    api = GuiApi(tmp_path / "gui", database_path=db_path, dsh_workspace_registry=target)
    inspected = api.inspect_plan_file(str(path))
    assert inspected["data"]["kind"] == "genshin-candidate-input-draft"
    result = api.dsh_client_import_candidate_input_draft({"dsh_session_id": "fresh", "path": str(path),
        "expected_file_sha": inspected["data"]["file_sha"]})
    assert result["code"] == 0 and result["data"]["imported"]
    assert len(target.activate("fresh").candidate_session.resources) == 3
    assert api._dsh_shell is None
    target.close()
    source.close()


def test_compatibility_callback_and_closing_reader_cannot_bypass_turn_guard(tmp_path):
    from genshin_app.agent_chat.native_app_action_controller import (
        NativeAppActionController,
    )
    from genshin_app.agent_chat.native_app_action_ledger import NativeAppActionLedger
    from genshin_app.agent_chat.native_dsh_mux_client import NativeDshMuxError
    from tests.test_native_app_action_controller import _binding, _workspace

    active = [False]
    controller = NativeAppActionController(ledger=NativeAppActionLedger(tmp_path / "gate", conversation_id="c"),
        context_reader=lambda: {"binding": _binding(), "workspace": _workspace()},
        callbacks={"save_as": lambda _: {"status":"completed", "message":"已保存", "dirty_open_guard_passed":False}}, turn_active_reader=lambda: active[0])
    with controller.agent_call():
        result = controller.call_model_tool(REQUEST_ACTION_TOOL, {"action": "save_as", "reason": "工程普通保存"})
    card = controller.present_action(result["card"]["action_id"])["card"]
    active[0] = True
    assert controller.execute_presented(action_id=card["action_id"], ui_receipt=card["ui_receipt"])["card"]["status"] == "completed"
    api = GuiApi(tmp_path / "api")
    api._dsh_shell_closed = True
    with pytest.raises(NativeDshMuxError, match="dsh_shell_closed"):
        api._dsh_existing_session_status("s")
