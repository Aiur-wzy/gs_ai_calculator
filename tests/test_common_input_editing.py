"""One non-executing editor; real calculation checks remain independent."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import uuid

import pytest

from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.calculation_qualification import qualify_execution
from genshin_app.equipment import new_plan, run_plan_calc
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_app.input_editing import (
    patch_input,
    prepare_input_edit,
    read_input_snapshot,
)
from genshin_app.whiteboard import make_handoff, new_whiteboard, validate_result_intent
from tests.test_mechanics_execution import fixture
from tests.test_whiteboard import board_fixture


def plan():
    value = new_plan()
    template, rotation, mechanics = fixture()
    template["meta"] = copy.deepcopy(value["manual_template"]["meta"])
    template["characters"][0]["stats"]["energy_recharge"] = 1
    for action in rotation["actions"]:
        action["repeat"] = {"mode": "once"}
    value.update(plan_version=3, manual_template=template, rotation_model=rotation, mechanics_model=mechanics)
    return value


def session(tmp_path):
    return ArtifactCandidateSession(str(uuid.uuid4()), tmp_path / "session", tmp_path / "absent.db", "native", plan())


def edit(session, **args):
    name = "gs_patch_whiteboard" if session._is_board() else "gs_patch_candidate"
    return session.call_tool(name, {"expected_revision": session.revision, "reason": "工程输入修改", **args})


def test_stable_id_patch_has_same_result_as_pointer_and_does_not_mutate_source():
    original = plan()
    stable = patch_input(original, [{"op": "replace", "selector": {"collection": "/rotation_model/actions", "id": "cast"}, "path": "/start", "value": .1}])
    pointer = patch_input(original, [{"op": "replace", "path": "/rotation_model/actions/0/start", "value": .1}])
    assert stable == pointer and original["rotation_model"]["actions"][0]["start"] == 0
    with pytest.raises(PlanError, match="不唯一"):
        duplicate = copy.deepcopy(original)
        duplicate["rotation_model"]["actions"] *= 2
        patch_input(duplicate, [{"op": "replace", "selector": {"collection": "/rotation_model/actions", "id": "cast"}, "path": "/start", "value": .2}])


@pytest.mark.parametrize("pointer", ["/revision", "/resolution", "/plan_id", "/manual_template/meta/version_line"])
def test_system_fields_are_not_game_inputs(pointer):
    with pytest.raises(PlanError) as exc:
        patch_input(plan(), [{"op": "replace", "path": pointer, "value": None}])
    assert exc.value.reason == "agent_patch_forbidden"


def test_incomplete_native_draft_is_editable_saveable_and_cold_recoverable(tmp_path):
    current = session(tmp_path)
    removed = edit(current, operations=[{"op": "remove", "path": "/rotation_model/abilities/0"}])
    assert removed["code"] == 0, removed
    assert current.summary()["saveable"] and current.summary()["input_issues"]
    restored = CandidateSession.load(current.session_id, current.root, current.db_path)
    assert restored.candidate == current.candidate
    assert restored.summary()["saveable"] and not restored.summary()["applicable"]
    rejected = restored.call_tool("gs_calculate", {"expected_revision": restored.revision})
    assert rejected["code"] == 1 and restored.calculated is None
    repaired = edit(restored, operations=[{"op": "add", "path": "/rotation_model/abilities/-", "value": plan()["rotation_model"]["abilities"][0]}])
    assert repaired["code"] == 0, repaired


def test_noop_preserves_revision_and_result_but_change_invalidates(tmp_path):
    current = session(tmp_path)
    current.calculate(0)
    result = fingerprint(current.calculated)
    response = edit(current, operations=[{"op": "replace", "path": "/rotation_model/actions/0/start", "value": 0}])
    assert response["code"] == 0 and current.revision == 0
    assert fingerprint(current.calculated) == result
    response = edit(current, operations=[{"op": "replace", "path": "/manual_template/meta/name", "value": "新标题"}])
    assert response["code"] == 0 and current.calculated is None and current.revision == 1


def test_replace_import_preview_and_independent_conflicts(tmp_path):
    current = session(tmp_path)
    value = copy.deepcopy(current.candidate)
    value["manual_template"]["meta"]["name"] = "文件输入"
    # Unknown future domain data must survive until its execution support is checked.
    value.setdefault("extensions", {})["future_mechanism_notes"] = {"opaque": [1, 2]}
    file = tmp_path / "input.json"
    file.write_text(json.dumps(value), encoding="utf8")
    sha = read_input_snapshot(file)["file_sha"]
    preview = edit(current, mode="import", file_path=str(file), expected_file_sha=sha, preview=True)
    assert preview["code"] == 0 and current.revision == 0
    conflict = edit(current, mode="import", file_path=str(file), expected_file_sha="0" * 64)
    assert conflict["code"] == 1 and current.revision == 0
    response = edit(current, mode="import", file_path=str(file), expected_file_sha=sha)
    assert response["code"] == 0, response
    assert current.candidate["extensions"]["future_mechanism_notes"] == {"opaque": [1, 2]}
    assert current.candidate["plan_id"] == value["plan_id"]
    bad = current.call_tool("gs_patch_candidate", {"expected_revision": 0, "reason": "过期", "operations": [{"op": "replace", "path": "/manual_template/meta/name", "value": "不能覆盖"}]})
    assert bad["code"] == 1 and current.revision == 1
    file.write_text('{"unfinished":', encoding="utf8")
    assert edit(current, mode="import", file_path=str(file))["code"] == 1
    assert current.revision == 1


def test_common_admission_requires_real_execution_but_not_auxiliary_inventory(tmp_path):
    value = plan()
    calculated = run_plan_calc(value, tmp_path / "absent.db", capture_display=True)
    common = qualify_execution(calculated)
    assert common["status"] == "accepted", common
    current = session(tmp_path)
    assert current.call_tool("gs_calculate", {"expected_revision": 0})["code"] == 0
    assert current.calculation_acceptance()["status"] == common["status"]
    invalid = copy.deepcopy(calculated)
    invalid["result"]["mechanics"]["complete_first_cycle"] = False
    invalid["result"]["rotation_check"]["damage_interpretation"]["complete_first_cycle"] = False
    assert qualify_execution(invalid)["status"] == "rejected"


def test_whole_native_input_can_enter_empty_board_without_downgrade(tmp_path):
    board = ArtifactCandidateSession(str(uuid.uuid4()), tmp_path / "board", tmp_path / "absent.db", "whiteboard", new_whiteboard())
    incoming = plan()
    response = board.call_tool("gs_patch_whiteboard", {"expected_revision": 0, "reason": "导入完整原生工作输入", "mode": "replace", "input": incoming})
    assert response["code"] == 0, response
    assert board.candidate["mechanics_model"] == incoming["mechanics_model"]
    assert board.candidate["rotation_model"] == incoming["rotation_model"]
    assert board.document["team"] == []
    assert board.stage_state["representation"] == "native"


def test_atomic_edit_does_not_commit_a_valid_prefix():
    original = plan()
    before = copy.deepcopy(original)
    with pytest.raises(PlanError):
        prepare_input_edit(original, {"operations": [
            {"op": "replace", "path": "/manual_template/meta/name", "value": "不能部分提交"},
            {"op": "replace", "path": "/does/not/exist", "value": 1}]})
    assert original == before


def test_auxiliary_switch_can_move_alone_without_rewriting_user_action(tmp_path):
    board = board_fixture()
    incoming = make_handoff(board)
    incoming["rotation_model"]["abilities"].append({"id": "aux.switch", "character": "actor.a",
        "action_type_id": "action.switch", "cooldown": None, "energy": None,
        "status": "assumed", "source": "工程切人输入"})
    incoming["rotation_model"]["actions"].insert(0, {"id": "aux.in", "ability_id": "aux.switch",
        "start": 0, "occupation_end": 0, "order": 0, "switch_to": "actor.a",
        "repeat": {"mode": "once"}, "status": "assumed", "source": "工程切人输入"})
    current = ArtifactCandidateSession(str(uuid.uuid4()), tmp_path / "board", tmp_path / "absent.db", "whiteboard", board)
    imported = edit(current, mode="replace", input=incoming)
    assert imported["code"] == 0, imported
    response = edit(current, operations=[{"op": "replace", "selector": {
        "collection": "/rotation_model/actions", "id": "aux.in"}, "path": "/start", "value": .1}])
    assert response["code"] == 0, response
    handoff = current.candidate["extensions"]["whiteboard_handoff"]
    assert handoff["auxiliary_action_ids"] == ["aux.in"]
    assert handoff["board"]["actions"] == board["actions"]
    assert handoff["action_map"] == [{"board_action_id": "action.e", "native_action_id": "action.e"}]
    validate_result_intent(handoff["board"], current.candidate)
    assert current.document == board


def test_native_edit_regenerates_projection_without_losing_advanced_inputs(tmp_path):
    board = board_fixture()
    incoming = make_handoff(board)
    incoming["extensions"]["future_mechanism_notes"] = {
        "source_fingerprint": "a" * 64, "profile_content_hash": "b" * 64, "rows": [{"t": 8.5}]}
    current = ArtifactCandidateSession(str(uuid.uuid4()), tmp_path / "board", tmp_path / "absent.db", "whiteboard", board)
    assert edit(current, mode="replace", input=incoming)["code"] == 0
    result = edit(current, operations=[{"op": "replace", "selector": {
        "collection": "/rotation_model/actions", "id": "action.e"}, "path": "/start", "value": 2}])
    assert result["code"] == 0, result
    native = current.candidate
    assert native["extensions"]["whiteboard_handoff"]["board"]["actions"][0]["start"] == 2
    assert native["extensions"]["whiteboard_handoff"]["board"]["revision"] > current.document["revision"]
    assert native["extensions"]["future_mechanism_notes"] == incoming["extensions"]["future_mechanism_notes"]
    assert native["extensions"]["future_mechanism_notes"]["rows"][0]["t"] == 8.5
    assert current.document["actions"][0]["start"] == 1.25
    # The projection must be recomputable, rather than trusting a copied marker.
    tampered = copy.deepcopy(native["extensions"]["whiteboard_handoff"]["board"])
    tampered["actions"][0]["start"] = 3
    with pytest.raises(PlanError):
        validate_result_intent(tampered, native)


def test_parent_replacement_cannot_smuggle_system_authority():
    value = plan()
    extensions = {**value.get("extensions", {}), "result_receipt": {"accepted": True}}
    with pytest.raises(PlanError) as exc:
        patch_input(value, [{"op": "add", "path": "/extensions", "value": extensions}])
    assert exc.value.reason == "agent_patch_forbidden"
    meta = {**value["manual_template"]["meta"], "version_line": "beta"}
    with pytest.raises(PlanError):
        patch_input(value, [{"op": "replace", "path": "/manual_template/meta", "value": meta}])
    imported = prepare_input_edit(value, {"mode": "replace", "input": {**value, "extensions": extensions}})
    assert "result_receipt" not in imported["extensions"]


def test_complete_file_snapshot_rejects_duplicate_keys_and_nonfinite(tmp_path):
    path = tmp_path / "input.json"
    for raw in ('{"kind":1,"kind":2}', '{"value":1e999}', '{"value":NaN}'):
        path.write_text(raw, encoding="utf8")
        with pytest.raises(PlanError):
            read_input_snapshot(path)


def test_native_import_uses_authenticated_captured_bytes_without_opening_python_path(tmp_path, monkeypatch):
    from genshin_app.native_input_snapshot import native_input_snapshot_scope

    current = session(tmp_path)
    incoming = copy.deepcopy(current.candidate)
    incoming["manual_template"]["meta"]["name"] = "来自DSH完整字节"
    raw = json.dumps(incoming, ensure_ascii=False).encode("utf8")
    path = "dsh-backend-only/input.json"  # Deliberately not a Python-readable file.
    snapshot = {"contract": "dsh-game-input-file-snapshot-v1", "requested_path": path,
                "path": "canonical-dsh-input.json", "bytes": len(raw), "file_sha": hashlib.sha256(raw).hexdigest(),
                "content_base64": base64.b64encode(raw).decode("ascii")}
    def forbidden_open(*args, **kwargs):
        raise AssertionError("native import must not reopen paths through Python")
    monkeypatch.setattr("genshin_app.input_editing.read_input_snapshot", forbidden_open)
    with native_input_snapshot_scope(None):
        rejected = edit(current, mode="import", file_path=path)
    assert rejected["code"] == 1 and rejected["reason"] == "input_import_snapshot_required"
    assert current.revision == 0
    with native_input_snapshot_scope(snapshot):
        conflict = edit(current, mode="import", file_path=path, expected_file_sha="0"*64)
    assert conflict["code"] == 1 and conflict["reason"] == "file_conflict" and current.revision == 0
    with native_input_snapshot_scope(snapshot):
        completed = edit(current, mode="import", file_path=path, expected_file_sha=snapshot["file_sha"])
    assert completed["code"] == 0, completed
    assert completed["data"]["source_file"]["file_sha"] == snapshot["file_sha"]
    assert current.candidate["manual_template"]["meta"]["name"] == "来自DSH完整字节"


def test_existing_legal_native_input_without_meta_keeps_its_compiler_default(tmp_path):
    incoming = plan()
    del incoming["manual_template"]["meta"]
    current = ArtifactCandidateSession(str(uuid.uuid4()), tmp_path / "legacy", tmp_path / "absent.db", "native", incoming)
    edited = edit(current, operations=[{"op": "replace", "path": "/rotation_model/actions/0/start", "value": .1}])
    assert edited["code"] == 0, edited
    assert "meta" not in current.candidate["manual_template"]


def test_native_file_cannot_select_another_whiteboard_version_or_drop_document_binding(tmp_path):
    board = board_fixture()
    current = ArtifactCandidateSession(str(uuid.uuid4()), tmp_path / "board", tmp_path / "absent.db", "whiteboard", board)
    incoming = make_handoff(board)
    foreign = copy.deepcopy(incoming)
    foreign["manual_template"]["meta"]["version_line"] = "beta"
    assert edit(current, mode="replace", input=foreign)["code"] == 1
    assert current._is_board()
    assert edit(current, mode="replace", input=incoming)["code"] == 0
    before = fingerprint(current.candidate)
    rejected = edit(current, operations=[{"op": "remove", "path": "/extensions/whiteboard_handoff"}])
    assert rejected["code"] == 1 and fingerprint(current.candidate) == before
