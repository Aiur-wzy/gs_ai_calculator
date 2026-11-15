"""Requested game edits are reviewable; identity, receipt and source checks remain."""
from __future__ import annotations

import copy
import json
import os
import uuid

import pytest

from genshin_app.agent_chat.candidate import CandidateSession, validate_native_intent
from genshin_app.agent_chat.sessions import read_owned, safe_path
from genshin_app.equipment.files import fingerprint, input_view, json_bytes
from genshin_app.equipment.schema import PlanError, validate_plan
from genshin_app.whiteboard import read_whiteboard_result, validate_result_intent
from genshin_db import queries
from tests.test_calculation_delivery import delivery_plan
from tests.test_equipment_plan import database as _legacy_database
from tests.test_equipment_plan import plan_fixture
from tests.test_external_damage import confirm, external_plan, supplement
from tests.test_whiteboard_equipment import add_manual_buff
from tests.test_whiteboard_review import real_package as _real_package

real_package = _real_package
legacy_database = _legacy_database


@pytest.fixture
def native_session(tmp_path):
    return CandidateSession(uuid.uuid4().hex, tmp_path / "session", tmp_path / "absent.db", "native", delivery_plan())


def patch(session, operations, reason="补全计算条件", revision=None):
    return session.call_tool("gs_patch_candidate", {"expected_revision": session.revision if revision is None else revision,
        "reason": reason, "operations": operations})


def test_native_real_calculation_review_and_accept(native_session):
    session = native_session
    assert session.summary()["ready"] is False
    computed = session.call_tool("gs_calculate", {"expected_revision": 0})
    assert computed["code"] == 0, computed
    summary = computed["data"]
    assert summary["ready"] and summary["has_calculation"]
    reviewed = session.call_tool("gs_review_result", {"expected_revision": 0, "request": {"view": "hits"}})
    assert reviewed["code"] == 0, reviewed
    assert session.calculated["result"]["hits"][0]["expected"] == pytest.approx(45)
    accepted = session.accept("native", delivery_plan(), 0, summary["receipt_fingerprint"])
    assert fingerprint(input_view(accepted["plan"])) == fingerprint(input_view(session.candidate))
    assert not session.db_path.exists()


@pytest.mark.parametrize("field,value", [("/manual_template/loop_duration", 3),
    ("/manual_template/characters/0/stats/atk", 200), ("/manual_template/characters/0/level", 80)])
def test_native_user_requested_values_can_change_in_reviewed_candidate(native_session, field, value):
    before = copy.deepcopy(native_session.candidate)
    result = patch(native_session, [{"op": "replace", "path": field, "value": value}])
    assert result["code"] == 0, result
    assert native_session.candidate != before and native_session.revision == 1
    assert native_session.document == before and not native_session.summary()["can_accept"]


def test_native_action_category_can_change_with_valid_references(real_package, tmp_path):
    _, plan, _, db = real_package
    session = CandidateSession(uuid.uuid4().hex, tmp_path / "session", db, "native", plan)
    changed = copy.deepcopy(session.candidate)
    ability = changed["rotation_model"]["abilities"][0]
    ability.update(id="other-ability", action_type_id="action.normal_attack")
    changed["rotation_model"]["actions"][0]["ability_id"] = "other-ability"
    assert session.replace_candidate(changed, 0, "按用户要求将动作改为普攻")["revision"] == 1
    assert session.document["rotation_model"]["abilities"][0]["action_type_id"] == "action.elemental_skill"


@pytest.mark.parametrize("mutation", [
    lambda p: p["rotation_model"]["actions"][0].update(start=0.5),
    lambda p: p["rotation_model"]["actions"][0].update(repeat={"mode": "each_cycle"}),
    lambda p: p["rotation_model"]["abilities"][0].update(variant="hold"),
    lambda p: (
        p["equipment_inputs"]["a"]["character_ref"].update(db_character_id=2),
        p["equipment_inputs"]["a"]["character_entity_ref"].update(database_id=2),
    ),
])
def test_native_requested_action_or_role_change_is_available_to_review(real_package, tmp_path, mutation):
    _, plan, _, db = real_package
    session = CandidateSession(uuid.uuid4().hex, tmp_path / "session", db, "native", plan)
    changed = copy.deepcopy(session.candidate)
    mutation(changed)
    assert session.replace_candidate(changed, 0, "用户要求调整角色或动作")["revision"] == 1


def test_native_patch_atomically_syncs_database_character_entity_ref(
    real_package, tmp_path,
):
    _, plan, _, db = real_package
    session = CandidateSession(
        uuid.uuid4().hex, tmp_path / "session", db, "native", plan
    )
    result = patch(session, [{
        "op": "replace",
        "path": "/equipment_inputs/a/character_ref/db_character_id",
        "value": 2,
    }], reason="用户要求切换数据库角色")
    assert result["code"] == 0, result
    assert session.candidate["equipment_inputs"]["a"][
        "character_entity_ref"
    ]["database_id"] == 2

    before = copy.deepcopy(session.candidate)
    result = patch(session, [
        {"op": "replace", "path":
         "/equipment_inputs/a/character_ref/db_character_id", "value": 3},
        {"op": "replace", "path":
         "/equipment_inputs/a/character_entity_ref/database_id", "value": 4},
    ], reason="提交相互冲突的角色身份")
    assert result["reason"] == "entity_ref_database_mismatch"
    assert session.candidate == before


def test_requested_constellation_change_is_not_an_authorization_code_gate(real_package):
    _, original, _, _ = real_package
    source = {"hit_id": "h", "version_line": "release", "selection": {"character_id": 1, "constellation": 0, "slot": "skill"},
        "param_index": 0, "scaling_stat": "atk", "purpose": "single_stat_damage_multiplier", "status": "assumed",
        "source": "独立测试声明", "expected_snapshot_id": None, "expected_node_sha256": None}
    original["manual_template"]["hits"][0].pop("multiplier")
    original["hit_parameter_sources"] = [source]
    changed = copy.deepcopy(original)
    changed["manual_template"]["hits"][0]["id"] = "new-h"
    changed["mechanics_model"]["hit_events"][0]["hit_id"] = "new-h"
    changed["hit_parameter_sources"][0].update(hit_id="new-h", selection={"character_id": 1, "constellation": 6, "slot": "skill"})
    validate_plan(changed)
    validate_native_intent(original, changed)


@pytest.mark.parametrize("change", [lambda p: p.update(plan_id="different"),
    lambda p: p["manual_template"]["meta"].update(version_line="beta")])
def test_native_document_identity_is_still_fixed(native_session, change):
    value = copy.deepcopy(native_session.candidate)
    change(value)
    with pytest.raises(PlanError):
        native_session.replace_candidate(value, 0, "身份不可改")


def test_calculation_event_patch_invalidates_receipt_and_stale_revision(native_session):
    session = native_session
    session.calculate(0)
    prior_receipt = session.summary()["receipt_fingerprint"]
    result = patch(session, [{"op": "replace", "path": "/manual_template/hits/0/multiplier", "value": 2}])
    assert result["code"] == 0 and result["data"]["revision"] == 1
    assert result["data"]["ready"] is False and session.calculated is None
    assert result["data"]["changes"][-1]["fields"] == ["manual_template"]
    assert result["data"]["diff"] == [{"path": "/manual_template/hits/0/multiplier", "before": 1, "after": 2}]
    assert result["data"]["diff_total"] == 1 and result["data"]["diff_truncated"] is False
    assert session.call_tool("gs_calculate", {"expected_revision": 0})["reason"] == "agent_stale_revision"
    with pytest.raises(PlanError) as exc:
        session.accept("native", delivery_plan(), 1, prior_receipt)
    assert exc.value.reason == "agent_stale_receipt"
    session.calculate(1)
    assert session.calculated["result"]["hits"][0]["expected"] == pytest.approx(90)


def test_accept_pins_reviewed_revision_receipt_and_current_document(native_session):
    session = native_session
    session.calculate(0)
    receipt = session.summary()["receipt_fingerprint"]
    for revision, expected in [(1, receipt), (0, "0" * 64)]:
        with pytest.raises(PlanError):
            session.accept("native", delivery_plan(), revision, expected)
    current = delivery_plan()
    current["manual_template"]["hits"][0]["multiplier"] = 4
    with pytest.raises(PlanError) as exc:
        session.accept("native", current, 0, receipt)
    assert exc.value.reason == "agent_document_changed"


def test_db_manual_annotation_change_invalidates_queries_calculation_and_accept(real_package, tmp_path, monkeypatch):
    _, plan, _, db = real_package
    session = CandidateSession(uuid.uuid4().hex, tmp_path / "session", db, "native", plan)
    session.calculate(0)
    receipt = session.summary()["receipt_fingerprint"]
    previous = queries.get_meta(db)
    monkeypatch.setattr(queries, "get_meta", lambda _: {**previous, "manual_content_fingerprint": "changed"})
    assert session.summary()["ready"] is False
    for tool, args in [("gs_query_context", {"request": {"characters": [{"id": 1}]}}),
                       ("gs_calculate", {"expected_revision": 0}),
                       ("gs_refresh_data_refs", {"expected_revision": 0, "reason": "不能跨快照更新"})]:
        result = session.call_tool(tool, args)
        assert result["code"] == 1 and result["reason"] == "agent_database_changed"
    with pytest.raises(PlanError) as exc:
        session.accept("native", plan, 0, receipt)
    assert exc.value.reason == "agent_database_changed"


def test_whiteboard_calculation_and_independent_delivery_survive_session_move(real_package, tmp_path):
    board, plan, _, db = real_package
    session = CandidateSession(uuid.uuid4().hex, tmp_path / "session", db, "whiteboard", board)
    assert session.summary()["ready"] is False
    plan["plan_id"] = session.candidate["plan_id"]
    session.replace_candidate(plan, 0, "补齐未知面板、约束及实际伤害事件")
    summary = session.calculate(1)
    # Valid numerical receipt can retain explicit unproven game checks.
    assert summary["ready"] and any(i["code"] == "calculation_energy_check" for i in summary["issues"])
    assert summary["application_kind"] == "whiteboard_calculated"
    accepted = session.accept("whiteboard", board, 1, summary["receipt_fingerprint"])
    path = accepted["result_path"]
    assert str(session.root) not in path
    session.root.rename(tmp_path / "archived-session")
    validated = read_whiteboard_result(board, path)
    assert validated["calculated"]["result"]["hits"][0]["expected"] == pytest.approx(82.4625)


def test_whiteboard_requested_role_and_action_edit_returns_matching_board_and_calculation(real_package, tmp_path):
    original_board, plan, _, db = real_package
    session = CandidateSession(uuid.uuid4().hex, tmp_path / "session", db, "whiteboard", original_board)
    plan["plan_id"] = session.candidate["plan_id"]
    session.replace_candidate(plan, 0, "补齐首版计算输入")
    before = copy.deepcopy(session.document)
    result = patch(session, [
        {"op": "replace", "path": "/extensions/whiteboard_handoff/board/team/0/db_character_id", "value": 2},
        {"op": "replace", "path": "/extensions/whiteboard_handoff/board/team/0/level", "value": 100},
        {"op": "replace", "path": "/extensions/whiteboard_handoff/board/team/0/constellation", "value": 1},
        {"op": "replace", "path": "/extensions/whiteboard_handoff/board/actions/0/start", "value": 1.5},
        {"op": "replace", "path": "/manual_template/characters/0/level", "value": 100},
        {"op": "replace", "path": "/equipment_inputs/a/character_ref/db_character_id", "value": 2},
        {"op": "replace", "path": "/rotation_model/actions/0/start", "value": 1.5},
        {"op": "replace", "path": "/rotation_model/actions/0/occupation_end", "value": 1.6},
        {"op": "replace", "path": "/rotation_model/actions/0/switch_allowed_at", "value": 1.6},
        {"op": "replace", "path": "/manual_template/hits/0/t", "value": 1.7}],
        reason="用户明确要求换角色、100级1命并把战技移到1.5秒")
    assert result["code"] == 0, result
    assert session.document == before and session.candidate["extensions"]["whiteboard_handoff"]["constellations"] == {"a": 1}
    edited = session._candidate_board()
    assert edited["revision"] == original_board["revision"] + 1
    assert session.call_tool("gs_read_context", {"resource": "candidate_board", "pointer": "/team/0/level"})["data"]["value"] == 100
    summary = session.calculate(2)
    assert summary["ready"]
    accepted = session.accept("whiteboard", original_board, 2, summary["receipt_fingerprint"])
    assert accepted["board"] == edited and accepted["board"] != original_board
    assert read_whiteboard_result(accepted["board"], accepted["result_path"])["calculated"]["result"]["hits"]
    with pytest.raises(PlanError):
        read_whiteboard_result(original_board, accepted["result_path"])
    stale = copy.deepcopy(original_board)
    stale["notes"] = "另一个窗口编辑"
    with pytest.raises(PlanError) as exc:
        session.accept("whiteboard", stale, 2, summary["receipt_fingerprint"])
    assert exc.value.reason == "agent_document_changed"
    loaded = CandidateSession.load(session.session_id, session.root, db)
    assert loaded.accept("whiteboard", original_board, 2, summary["receipt_fingerprint"])["board"] == edited
    assert loaded.resume_document(edited)["diff_total"] == 0
    assert loaded.document == edited and loaded.changes == []
    assert patch(loaded, [
        {"op": "replace", "path": "/extensions/whiteboard_handoff/board/team/0/panel/atk", "value": 200},
        {"op": "replace", "path": "/manual_template/characters/0/stats/atk", "value": 200}],
        reason="应用后用户继续要求修改面板")["code"] == 0
    second = loaded.calculate(3)
    second_result = loaded.accept("whiteboard", edited, 3, second["receipt_fingerprint"])
    assert second_result["board"]["team"][0]["panel"]["atk"] == 200
    with pytest.raises(PlanError):
        loaded.accept("whiteboard", original_board, 3, second["receipt_fingerprint"])


def test_candidate_board_declared_progression_stays_synchronized(real_package, tmp_path):
    board, _, _, db = real_package
    board = copy.deepcopy(board)
    board["team"][0].update(ascension=6, talent_base_levels={"normal": 8, "skill": 9, "burst": 10})
    session = CandidateSession(uuid.uuid4().hex, tmp_path / "session", db, "whiteboard", board)
    candidate = copy.deepcopy(session.candidate)
    actor = candidate["extensions"]["whiteboard_handoff"]["board"]["team"][0]
    actor.update(ascension=5, talent_base_levels={"normal": 6, "skill": 7, "burst": 8})

    session.replace_candidate(candidate, 0, "用户明确修改突破与天赋等级")

    progression = session.candidate["equipment_inputs"]["a"]["progression"]
    assert progression["ascension"] == 5 and progression["status"] == "confirmed"
    assert progression["talent_base_levels"] == {"normal": 6, "skill": 7, "burst": 8}


def test_candidate_board_removing_optional_progression_restores_unknown(real_package, tmp_path):
    board, _, _, db = real_package
    board = copy.deepcopy(board)
    board["team"][0].update(ascension=6, talent_base_levels={"normal": 8, "skill": 9, "burst": 10})
    session = CandidateSession(uuid.uuid4().hex, tmp_path / "session", db, "whiteboard", board)
    candidate = copy.deepcopy(session.candidate)
    actor = candidate["extensions"]["whiteboard_handoff"]["board"]["team"][0]
    actor.pop("ascension")
    actor.pop("talent_base_levels")

    session.replace_candidate(candidate, 0, "用户明确清除突破与天赋输入，恢复未知")

    progression = session.candidate["equipment_inputs"]["a"]["progression"]
    assert progression["ascension"] is None and progression["status"] == "pending"
    assert "talent_base_levels" not in progression
    validate_result_intent(session._candidate_board(), session.candidate)


def test_candidate_board_keeps_agent_progression_when_field_was_always_unknown(real_package, tmp_path):
    board, _, _, db = real_package
    assert "ascension" not in board["team"][0]
    assert "talent_base_levels" not in board["team"][0]
    session = CandidateSession(uuid.uuid4().hex, tmp_path / "session", db, "whiteboard", board)
    candidate = copy.deepcopy(session.candidate)
    progression = candidate["equipment_inputs"]["a"]["progression"]
    progression.update(
        ascension=6,
        status="assumed",
        talent_base_levels={"normal": 8, "skill": 9, "burst": 10},
    )

    session.replace_candidate(candidate, 0, "白板始终未声明成长字段，Agent 在原生候选中补齐")

    actual = session.candidate["equipment_inputs"]["a"]["progression"]
    assert actual["ascension"] == 6 and actual["status"] == "assumed"
    assert actual["talent_base_levels"] == {"normal": 8, "skill": 9, "burst": 10}
    validate_result_intent(session._candidate_board(), session.candidate)


def test_native_calculation_resume_accepts_visible_input_and_continues(native_session):
    session = native_session
    original = copy.deepcopy(session.document)
    assert patch(session, [{"op": "replace", "path": "/manual_template/characters/0/stats/atk", "value": 200}])["code"] == 0
    assert session.resume_document(original)["diff_total"] == 1
    with pytest.raises(PlanError):
        session.resume_document(session.candidate)
    summary = session.calculate(1)
    accepted = session.accept("native", original, 1, summary["receipt_fingerprint"])
    assert session.document == original
    visible = copy.deepcopy(input_view(accepted["plan"]))
    assert session.resume_document(visible)["diff_total"] == 0
    assert session._document_fingerprint(session.document) == fingerprint(visible)
    loaded = CandidateSession.load(session.session_id, session.root, session.db_path)
    assert loaded.document == session.document and loaded.calculated is not None
    assert patch(loaded, [{"op": "replace", "path": "/manual_template/characters/0/stats/atk", "value": 300}])["code"] == 0
    summary = loaded.calculate(2)
    assert loaded.accept("native", visible, 2, summary["receipt_fingerprint"])["plan"]["manual_template"]["characters"][0]["stats"]["atk"] == 300


def test_document_checkpoint_restore_rebases_once_and_invalidates_calculation(
    native_session,
):
    session = native_session
    session.calculate(0)
    restored = delivery_plan()
    restored["manual_template"]["hits"][0]["multiplier"] = 3
    result = session.restore_document_checkpoint(restored)
    assert result["revision"] == 1
    assert session.document == restored
    assert session.candidate == restored
    assert session.baseline == restored
    assert session.calculated is None
    assert session.changes[-1]["fields"] == ["document_checkpoint_restore"]
    assert session.restore_document_checkpoint(restored)["revision"] == 1


def test_calculation_can_search_chinese_roles_and_read_basis_without_guessing_ids(real_package, tmp_path, monkeypatch):
    from genshin_app.agent_chat import tools as module
    _, plan, _, db = real_package
    session = CandidateSession(uuid.uuid4().hex, tmp_path / "session", db, "native", plan)
    calls = []

    def catalog(db_path, line, **kwargs):
        calls.append((db_path, line, kwargs))
        return {"items": [{"id": 2, "name": "用户指定角色"}], "total": 1, "page": 1, "page_size": 20}

    def basis(db_path, db_character_id, level, version_line, weapon=None):
        calls.append((db_path, db_character_id, level, version_line, weapon))
        return {"state": "partial", "panel_base": {"base_atk": None, "base_hp": 13000, "base_def": 800}}

    monkeypatch.setattr(queries, "list_characters", catalog)
    monkeypatch.setattr(module, "character_panel_basis", basis)
    result = session.call_tool("gs_search_catalog", {"kind": "character", "query": "用户指定角色"})
    assert result["code"] == 0 and result["data"]["items"][0]["id"] == 2
    result = session.call_tool("gs_character_panel_basis", {"db_character_id": 2, "level": 100})
    assert result["code"] == 0 and result["data"]["panel_base"]["base_hp"] == 13000
    assert calls == [(db, "release", {"name": "用户指定角色", "page": 1, "page_size": 20}),
                     (db, 2, 100, "release", None)]
    assert session.revision == 0


def test_native_resume_rejects_unrelated_document_and_rolls_back_failed_save(native_session, monkeypatch):
    session = native_session
    original = copy.deepcopy(session.document)
    assert patch(session, [{"op": "replace", "path": "/manual_template/characters/0/stats/atk", "value": 200}])["code"] == 0
    session.calculate(1)
    unrelated = copy.deepcopy(session.candidate)
    unrelated["manual_template"]["characters"][0]["stats"]["atk"] = 999
    with pytest.raises(PlanError) as exc:
        session.resume_document(unrelated)
    assert exc.value.reason == "agent_document_changed"
    from genshin_app.agent_chat import candidate as module
    previous_write = module.write_owned

    def fail(root, name, value, maximum):
        if name == "checkpoint.json":
            raise OSError("模拟磁盘已满")
        return previous_write(root, name, value, maximum)

    monkeypatch.setattr(module, "write_owned", fail)
    with pytest.raises(OSError):
        session.resume_document(session.calculated["plan"])
    assert session.document == original
    loaded = CandidateSession.load(session.session_id, session.root, session.db_path)
    assert loaded.document == original and loaded.revision == 1


@pytest.mark.parametrize("path", ["/rotation_model/actions/0/start", "/extensions/whiteboard_handoff/board/actions/0/start"])
def test_whiteboard_declared_board_and_actual_calculation_must_match(real_package, tmp_path, path):
    board, _, _, db = real_package
    session = CandidateSession(uuid.uuid4().hex, tmp_path / "session", db, "whiteboard", board)
    result = patch(session, [{"op": "replace", "path": path, "value": 1.5}])
    assert result["code"] == 1 and result["reason"] == "whiteboard_intent_mismatch"
    assert session.revision == 0


def test_load_recovers_exact_candidate_calculation_and_notes(native_session):
    session = native_session
    result = session.call_tool("gs_record_finding", {"record": {"id": "note", "type": "issue", "description": "工程数据不能证明实际游戏机制"}})
    assert result["code"] == 0, result
    session.calculate(0)
    expected = session.summary()
    session.close()
    loaded = CandidateSession.load(session.session_id, session.root, session.db_path)
    assert loaded.summary() == expected
    assert loaded.calculated == session.calculated and loaded.findings == session.findings
    assert loaded.accept("native", delivery_plan(), 0, expected["receipt_fingerprint"])["kind"] == "native"


def test_persistence_failure_keeps_last_memory_and_disk_candidate(native_session, monkeypatch):
    from genshin_app.agent_chat import candidate as module
    session = native_session
    original = copy.deepcopy(session.candidate)
    previous_write = module.write_owned
    def fail_checkpoint(root, name, value, maximum):
        if name == "checkpoint.json":
            raise OSError("独立模拟磁盘已满")
        return previous_write(root, name, value, maximum)
    monkeypatch.setattr(module, "write_owned", fail_checkpoint)
    result = patch(session, [{"op": "replace", "path": "/manual_template/hits/0/multiplier", "value": 2}])
    assert result["code"] == 1
    assert session.candidate == original and session.revision == 0
    loaded = CandidateSession.load(session.session_id, session.root, session.db_path)
    assert loaded.candidate == original and loaded.revision == 0


def test_load_rejects_tampered_candidate_even_if_valid_json(native_session):
    checkpoint = read_owned(native_session.root, "checkpoint.json", 1024 * 1024)
    path = safe_path(native_session.root, checkpoint["candidate_file"])
    value = json.loads(path.read_text("utf8"))
    value["manual_template"]["hits"][0]["multiplier"] = 9
    path.write_bytes(json_bytes(value))
    with pytest.raises(PlanError):
        CandidateSession.load(native_session.session_id, native_session.root, native_session.db_path)


def test_owned_paths_reject_escape_and_hardlink(tmp_path):
    for name in ("../other.json", "child/other.json", "c:other", "something.", ".."):
        with pytest.raises(PlanError):
            safe_path(tmp_path, name)
    source = tmp_path / "original.json"
    source.write_text("{}", encoding="utf8")
    try:
        os.link(source, tmp_path / "linked.json")
    except OSError as exc:
        pytest.skip(f"当前测试卷或ACL不允许创建硬链接：{exc}")
    with pytest.raises(PlanError):
        safe_path(tmp_path, "linked.json")


def test_tool_allowlist_arguments_and_paging(native_session):
    session = native_session
    assert all(item["type"] == "function" for item in session.tool_definitions())
    for name in ("run_shell", "query_sql", "add_annotation", "agent_accept", "open_file"):
        assert session.call_tool(name, {})["reason"] == "agent_tool_forbidden"
    invalid = session.call_tool("gs_read_context", {"resource": "candidate", "path": "D:/fixture/data/example.db"})
    assert invalid["reason"] == "agent_tool_input"
    result = session.call_tool("gs_read_context", {"resource": "candidate", "pointer": "/manual_template/characters/0/stats", "limit": 1})
    assert result["code"] == 0 and result["data"]["next_offset"] == 1
    assert result["data"]["items"][0]["key"] == "atk"
    for resource in ("capabilities", "mechanics_capabilities", "context_capabilities", "runtime_capabilities", "plan_schema", "database_snapshot"):
        assert session.call_tool("gs_read_context", {"resource": resource, "limit": 2})["code"] == 0
    for path in ("/manual_template/~2", "/manual_template/characters/00"):
        assert session.call_tool("gs_read_context", {"resource": "candidate", "pointer": path})["code"] == 1
    assert session.call_tool("gs_read_skill", {"resource": "../../pyproject.toml"})["code"] == 1
    skills = session.call_tool("gs_read_skill", {})
    assert skills["code"] == 0 and any(name.endswith("SKILL.md") for name in skills["data"]["resources"])


def test_bounded_tool_payload_and_patch_reserved_fields(native_session):
    session = native_session
    result = patch(session, [{"op": "replace", "path": "/plan_id", "value": "bad"}])
    assert result["reason"] == "agent_patch_forbidden"
    result = patch(session, [{"op": "add", "path": "/extensions/test", "value": "x"}] * 129)
    assert result["reason"] == "agent_size_limit"
    result = patch(session, [{"op": "add", "path": "/extensions/test", "value": "x"}], reason="x" * 5000)
    assert result["reason"] == "agent_size_limit"
    result = session.call_tool("gs_read_context", {"resource": "candidate", "limit": True})
    assert result["code"] == 1


def test_same_snapshot_context_query_retained_and_reused(real_package, tmp_path):
    _, plan, _, db = real_package
    session = CandidateSession(uuid.uuid4().hex, tmp_path / "session", db, "native", plan)
    args = {"request": {"characters": [{"id": 1}], "version_line": "release"}}
    first = session.call_tool("gs_query_context", args)
    assert first["code"] == 0, first
    assert first["data"]["resource"] == "query-1"
    assert session.call_tool("gs_query_context", args)["data"]["resource"] == "query-1"
    assert len(session.resources) == 1
    loaded = CandidateSession.load(session.session_id, session.root, session.db_path)
    assert loaded.resources == session.resources


def test_constructor_never_overwrites_existing_session(native_session):
    with pytest.raises(PlanError) as exc:
        CandidateSession(native_session.session_id, native_session.root, native_session.db_path, "native", delivery_plan())
    assert exc.value.reason == "agent_session_exists"


def test_diff_is_bounded_and_explicit_about_omitted_rows(native_session):
    session = native_session
    old = {str(index): index for index in range(240)}
    session.baseline.setdefault("extensions", {})["review_test"] = old
    session.candidate.setdefault("extensions", {})["review_test"] = {key: value + 1 for key, value in old.items()}
    summary = session.summary()
    assert len(summary["diff"]) == 200 and summary["diff_total"] == 240 and summary["diff_truncated"]
    session.candidate["extensions"]["huge_note"] = "说明" * 10000
    session.report = {"missing_inputs": [{"code": "x", "message": "中文说明" * 1000, "path": "路径" * 1000}] * 200}
    summary = session.summary()
    assert summary["issues_truncated"] and summary["issues_total"] == 200
    assert len(json_bytes(summary)) < 256 * 1024


def test_external_value_confirmation_is_not_silently_refreshed(tmp_path):
    db = tmp_path / "absent.db"
    plan = confirm(external_plan(), db)
    accepted = plan["external_damage_supplements"][0]["accepted_fingerprint"]
    session = CandidateSession(uuid.uuid4().hex, tmp_path / "session", db, "native", plan)
    session.calculate(0)
    result = patch(session, [{"op": "replace", "path": "/external_damage_supplements/0/expected", "value": 24}])
    assert result["code"] == 0
    report = session.call_tool("gs_compose", {"expected_revision": 1})
    assert report["code"] == 0 and report["data"]["ready"] is False
    assert any(i["code"] == "external_damage_confirmation_required" for i in report["data"]["missing_inputs"])
    assert session.candidate["external_damage_supplements"][0]["accepted_fingerprint"] == accepted
    assert session.call_tool("gs_calculate", {"expected_revision": 1})["code"] == 1
    assert session.summary()["ready"] is False


def test_blank_whiteboard_stays_pending(real_package, tmp_path):
    board, _, _, db = real_package
    session = CandidateSession(uuid.uuid4().hex, tmp_path / "session", db, "whiteboard", board)
    computed = session.call_tool("gs_calculate", {"expected_revision": 0})
    assert computed["code"] == 1
    assert not session.summary()["ready"] and session.summary()["missing_inputs"]


def test_larger_diff_value_has_exact_read_reference(native_session):
    session = native_session
    result = patch(session, [{"op": "add", "path": "/extensions", "value": {"note": "说明" * 1000}}])
    assert result["code"] == 0
    reference = result["data"]["diff"][0]["after"]
    assert reference["resource"] == "candidate" and reference["pointer"] == "/extensions"
    read = session.call_tool("gs_read_context", {"resource": reference["resource"], "pointer": reference["pointer"]})
    assert read["data"]["items"][0]["value"] == "说明" * 1000


def test_calculation_publication_failure_does_not_leave_ready_summary(native_session, monkeypatch):
    def fail_save():
        raise OSError("独立模拟计算结果存盘失败")
    monkeypatch.setattr(native_session, "save", fail_save)
    result = native_session.call_tool("gs_calculate", {"expected_revision": 0})
    assert result["code"] == 1
    assert native_session.summary()["has_calculation"] is False
    assert native_session.summary()["ready"] is False


@pytest.mark.parametrize("stale", [None, {"version_line": "release", "upstream_commit": "b" * 40,
    "equipment_schema_version": 1, "built_at": "2025-01-01T00:00:00+00:00"}])
def test_refresh_refs_converges_whiteboard_equipment_version_without_panel_change(real_package, tmp_path, stale):
    board, plan, _, db = real_package
    session = CandidateSession(uuid.uuid4().hex, tmp_path / "session", db, "whiteboard", board)
    plan["plan_id"] = session.candidate["plan_id"]
    expected_version = copy.deepcopy(plan["equipment_inputs"]["a"]["data_version"])
    plan["equipment_inputs"]["a"]["data_version"] = stale
    session.replace_candidate(plan, 0, "补齐计算输入，资料版本引用尚未同步")
    stats = copy.deepcopy(session.candidate["manual_template"]["characters"])
    previous_db = db.read_bytes()
    assert any(issue["code"] == "source_changed" for issue in session.compose(1)["missing_inputs"])
    refreshed = session.call_tool("gs_refresh_data_refs", {"expected_revision": 1, "reason": "同步本次资料快照引用"})
    assert refreshed["code"] == 0, refreshed
    assert session.revision == 2 and session.candidate["equipment_inputs"]["a"]["data_version"] == expected_version
    assert session.candidate["manual_template"]["characters"] == stats
    assert db.read_bytes() == previous_db
    assert not any(issue["code"] == "source_changed" for issue in refreshed["data"]["missing_inputs"])
    assert fingerprint(input_view(session.report["plan"])) == fingerprint(input_view(session.candidate))
    assert session.calculate(2)["ready"]


def test_refresh_refs_never_renews_manual_context_or_external_confirmation(real_package, tmp_path):
    _, plan, _, db = real_package
    plan["equipment_inputs"]["a"]["data_version"] = None
    effect = add_manual_buff(plan, db, mode="manual_context")
    plan["external_damage_supplements"] = [supplement()]
    confirm(plan, db)
    old_context = effect["dependencies"]["context_fingerprint"]
    old_external = plan["external_damage_supplements"][0]["accepted_fingerprint"]
    session = CandidateSession(uuid.uuid4().hex, tmp_path / "session", db, "native", plan)
    refreshed = session.call_tool("gs_refresh_data_refs", {"expected_revision": 0, "reason": "资料引用更新仍须人工复核上下文相关值"})
    assert refreshed["code"] == 0, refreshed
    assert session.candidate["effect_bindings"][0]["parts"][0]["dependencies"]["context_fingerprint"] == old_context
    assert session.candidate["external_damage_supplements"][0]["accepted_fingerprint"] == old_external
    codes = {issue["code"] for issue in refreshed["data"]["missing_inputs"]}
    assert {"dependency_stale", "external_damage_confirmation_required"} <= codes
    assert not refreshed["data"]["ready"]
    assert session.call_tool("gs_calculate", {"expected_revision": 1})["code"] == 1


def test_refresh_legacy_database_basis_becomes_reviewable_change(legacy_database, tmp_path, monkeypatch):
    db, _ = legacy_database
    plan = plan_fixture()
    equipment = plan["equipment_inputs"]["char1"]
    equipment["character_ref"] = {"db_character_id": 1, "version_line": "release"}
    equipment["basis"]["mode"] = "database"
    monkeypatch.setattr(queries, "get_character_detail", lambda *a, **kw: {"character": {
        "id": 1, "name": "工程角色", "weapon_type": "WEAPON_SWORD_ONE_HAND", "atk_90": 400, "hp_90": 10000, "def_90": 500,
        "crit_rate": .05, "crit_dmg": .5, "charge_efficiency": 1}})
    session = CandidateSession(uuid.uuid4().hex, tmp_path / "session", db, "native", plan)
    before = copy.deepcopy(session.candidate)
    refreshed = session.call_tool("gs_refresh_data_refs", {"expected_revision": 0, "reason": "用户要求同步数据库基础属性"})
    assert refreshed["code"] == 0, refreshed
    assert session.candidate != before and session.revision == 1
    assert session.document == before and not session.summary()["has_calculation"]
