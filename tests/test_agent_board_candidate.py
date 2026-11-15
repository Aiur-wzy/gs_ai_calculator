"""Whiteboard chat is staged editing; calculation repair remains locked."""
from __future__ import annotations

import copy
import json
import uuid

import pytest

from genshin_app.agent_chat.board_candidate import BoardCandidateSession
from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.agent_chat.sessions import MAX_CHECKPOINT, read_owned
from genshin_app.equipment.files import json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_app.whiteboard import make_handoff, new_whiteboard, validate_whiteboard
from genshin_db import queries
from tests.test_calculation_delivery import delivery_plan


@pytest.fixture
def session(tmp_path, monkeypatch):
    db = tmp_path / "engineering.db"
    db.touch()
    meta = {"version_lines": ["release"], "datasets": {"equipment": {"lines": {"release": {"state": "ready", "schema_version": 1,
            "upstream_commit": "a" * 40, "built_at": "2026-10-05T00:00:00Z"}}}}}
    monkeypatch.setattr(queries, "get_meta", lambda _: copy.deepcopy(meta))

    def detail(_, cid, line, **kwargs):
        if cid not in (1, 2, 3, 4, 5):
            raise queries.NotFoundError("工程角色不存在")
        return {"character": {"id": cid, "name": "工程角色" + str(cid), "weapon_type": "WEAPON_SWORD_ONE_HAND",
                "atk_90": 300, "hp_90": 12000, "def_90": 800,
                "atk_100": 375, "hp_100": 13000, "def_100": 850}}

    monkeypatch.setattr(queries, "get_character_detail", detail)
    monkeypatch.setattr(queries, "get_weapon_detail", lambda *a, **kw: {
        "weapon": {"weapon_type": "WEAPON_SWORD_ONE_HAND", "name": "工程武器"}})
    monkeypatch.setattr(queries, "get_weapon_stats", lambda *a, **kw: {
        "properties": [{"prop_type": "FIGHT_PROP_BASE_ATTACK", "value": 674.33}], "warnings": []})
    return BoardCandidateSession(uuid.uuid4().hex, tmp_path / "session", db, "whiteboard", new_whiteboard())


def patch(session, operations, **overrides):
    return session.call_tool("gs_patch_whiteboard", {"expected_revision": session.revision,
        "reason": "按用户要求构建白板", "operations": operations, **overrides})


def add(session, **overrides):
    return session.call_tool("gs_add_character", {"expected_revision": session.revision,
        "reason": "用户选择角色", "db_character_id": 1, **overrides})


def test_empty_and_partial_are_reviewable_without_calculation(session):
    initial = session.summary()
    assert initial["workflow"] == "build" and not initial["can_accept"]
    assert initial["application_kind"] == "whiteboard_draft" and not initial["has_calculation"]
    assert initial["ready_scope"] == "none" and initial["assessment"] == "draft"
    assert {item["code"] for item in initial["missing_inputs"]} >= {"empty_team", "empty_actions"}
    summary = add(session)
    assert summary["code"] == 0, summary
    assert summary["data"]["can_accept"] and summary["data"]["diff_total"] == 1
    assert summary["data"]["ready_scope"] == "draft_sync"
    assert summary["data"]["assessment"] == "draft" and summary["data"]["has_calculation"] is False
    actor = session.candidate["team"][0]
    assert actor["level"] == 90 and actor["constellation"] == 0 and actor["name"] == "工程角色1"
    assert actor["panel"]["base_atk"] is None
    assert actor["panel"]["base_hp"] == 12000 and actor["panel"]["base_def"] == 800
    assert all(actor["panel"][key] is None for key in ("atk", "hp", "def", "crit_rate", "em"))
    assert not session.document["team"] and not session.candidate["actions"]
    accepted = session.accept("whiteboard", session.document, session.revision, summary["data"]["receipt_fingerprint"])
    assert accepted["board"] == session.candidate and not session.document["team"]
    assert not list(session.root.glob("calculated-*.json"))
    with pytest.raises(PlanError, match="至少选择"):
        make_handoff(accepted["board"])


def test_source_auto_sync_preserves_final_stats_and_explicit_manual_mode(session):
    assert add(session)["code"] == 0
    weapon = {"weapon_id": 11, "name": "工程武器", "level": 90, "ascension": 6, "refinement": 1}
    result = patch(session, [{"op": "replace", "path": "/team/0/weapon", "value": weapon},
                             {"op": "replace", "path": "/team/0/panel/atk", "value": 2200}])
    assert result["code"] == 0, result
    assert session.candidate["team"][0]["panel"]["base_atk"] == pytest.approx(974.33)
    assert patch(session, [{"op": "replace", "path": "/team/0/level", "value": 100}])["code"] == 0
    panel = session.candidate["team"][0]["panel"]
    assert panel["atk"] == 2200 and panel["base_hp"] == 13000 and panel["base_atk"] == pytest.approx(1049.33)
    assert patch(session, [{"op": "replace", "path": "/team/0/panel/base_atk", "value": 1}])["code"] == 0
    assert session.candidate["team"][0]["panel"]["base_atk"] == pytest.approx(1049.33)
    result = patch(session, [{"op": "replace", "path": "/team/0/panel_basis_mode", "value": "manual"},
                             {"op": "replace", "path": "/team/0/panel/base_atk", "value": 800}])
    assert result["code"] == 0 and session.candidate["team"][0]["panel"]["base_atk"] == 800


@pytest.mark.parametrize("source_path,new_value", [("/team/0/level", 100),
    ("/team/0/db_character_id", 2),
    ("/team/0/weapon", {"weapon_id": 11, "name": "工程武器", "level": 90, "ascension": 6, "refinement": 1})])
def test_manual_basis_source_change_clears_inherited_values_but_keeps_new_values(session, source_path, new_value):
    assert add(session)["code"] == 0
    assert patch(session, [{"op": "replace", "path": "/team/0/panel_basis_mode", "value": "manual"},
                          {"op": "replace", "path": "/team/0/panel/base_atk", "value": 900}])["code"] == 0
    result = patch(session, [{"op": "replace", "path": source_path, "value": new_value},
                             {"op": "replace", "path": "/team/0/panel/base_def", "value": 999}])
    assert result["code"] == 0 and result["data"]["can_accept"]
    panel = session.candidate["team"][0]["panel"]
    assert panel["base_atk"] is None and panel["base_hp"] is None and panel["base_def"] == 999
    assert any(item["code"] == "unknown_panel_basis" for item in result["data"]["missing_inputs"])
    # An explicit subsequent confirmation may legitimately reuse the old number.
    assert patch(session, [{"op": "replace", "path": "/team/0/panel/base_atk", "value": 900}])["code"] == 0
    assert session.candidate["team"][0]["panel"]["base_atk"] == 900


def test_manual_basis_is_preserved_without_source_change_and_for_new_actor(session):
    from genshin_app.whiteboard import default_panel
    actor = {"id": "manual", "db_character_id": 1, "name": "工程角色1", "level": 90, "constellation": 0,
             "weapon": None, "artifact_sets": [], "panel": {**default_panel(), "base_atk": 900,
                 "base_hp": 11000, "base_def": 700}, "panel_basis_mode": "manual", "notes": ""}
    assert patch(session, [{"op": "add", "path": "/team/-", "value": actor}])["code"] == 0
    original = copy.deepcopy(session.candidate["team"][0]["panel"])
    assert original["base_atk"] == 900 and original["base_hp"] == 11000 and original["base_def"] == 700
    assert patch(session, [{"op": "replace", "path": "/team/0/notes", "value": "补充说明"}])["code"] == 0
    assert session.candidate["team"][0]["panel"] == original


def test_catalog_identities_cannot_be_forged_by_patch(session, monkeypatch):
    assert add(session)["code"] == 0
    original = copy.deepcopy(session.candidate)
    result = patch(session, [{"op": "replace", "path": "/team/0/db_character_id", "value": 999}])
    assert result["reason"] == "agent_catalog_identity_invalid" and session.candidate == original
    result = patch(session, [{"op": "replace", "path": "/team/0/name", "value": "伪造名字"}])
    assert result["code"] == 0 and session.candidate["team"][0]["name"] == "工程角色1"
    monkeypatch.setattr(queries, "get_weapon_detail", lambda *a, **kw: {
        "weapon": {"name": "工程弓", "weapon_type": "WEAPON_BOW"}})
    weapon = {"weapon_id": 2, "name": "伪造名字", "level": 90, "ascension": 6, "refinement": 1}
    assert patch(session, [{"op": "replace", "path": "/team/0/weapon", "value": weapon}])["reason"] == "agent_weapon_type_mismatch"
    monkeypatch.setattr(queries, "get_artifact_set_detail", lambda *a, **kw: {"artifact_set": {"name": "工程套装"}})
    assert patch(session, [{"op": "add", "path": "/team/0/artifact_sets/-", "value": {"set_id": 1, "name": "错名", "count": 4}}])["code"] == 0
    assert session.candidate["team"][0]["artifact_sets"][0]["name"] == "工程套装"


def test_explicit_database_entity_ref_mismatch_is_not_auto_rewritten(session):
    assert add(session)["code"] == 0
    original = copy.deepcopy(session.candidate)
    result = patch(session, [
        {"op": "replace", "path": "/team/0/db_character_id", "value": 2},
        {"op": "replace", "path": "/team/0/character_ref/database_id", "value": 3},
    ])
    assert result["reason"] == "entity_ref_database_mismatch"
    assert session.candidate == original


@pytest.mark.parametrize("kind,query_function", [("character", "list_characters"), ("weapon", "list_weapons"),
                                                ("artifact_set", "list_artifact_sets")])
def test_chinese_catalog_search_is_bounded_and_same_snapshot(session, monkeypatch, kind, query_function):
    captured = []

    def search(db, line, **kwargs):
        captured.append((db, line, kwargs))
        return {"total": 1, "page": kwargs["page"], "page_size": kwargs["page_size"],
                "items": [{"id": 123, "name": "中文资料", "extra_large_raw": "not returned"}]}

    monkeypatch.setattr(queries, query_function, search)
    result = session.call_tool("gs_search_catalog", {"kind": kind, "query": "中文", "page_size": 30})
    assert result["code"] == 0
    item, = result["data"]["items"]
    assert set(item) == {"id", "name", "publication"}
    assert (item["id"], item["name"]) == (123, "中文资料")
    assert item["publication"]["entity"] == {"type": kind, "id": 123}
    assert item["publication"]["version_line"] == "release"
    assert item["publication"]["entity_status"] == "unknown"
    assert item["publication"]["requires_usage_confirmation"] is True
    assert captured == [(session.db_path, "release", {"name": "中文", "page": 1, "page_size": 30})]
    assert session.call_tool("gs_search_catalog", {"kind": kind, "query": "中文", "page_size": 31})["reason"] == "agent_tool_input"
    assert session.call_tool("gs_read_context", {"resource": "whiteboard_schema", "pointer": "/action"})["code"] == 0


def test_basis_read_is_readonly_and_unknown_level_does_not_reuse_90(session):
    result = session.call_tool("gs_character_panel_basis", {"db_character_id": 1, "level": 100})
    assert result["code"] == 0 and result["data"]["panel_base"]["base_hp"] == 13000
    assert session.revision == 0
    assert add(session, level=80)["code"] == 0
    assert all(session.candidate["team"][0]["panel"][key] is None for key in ("base_atk", "base_hp", "base_def"))


def test_accept_does_not_advance_until_next_document_echo_then_multiturn(session):
    original = copy.deepcopy(session.document)
    summary = add(session)["data"]
    accepted = session.accept("whiteboard", original, 1, summary["receipt_fingerprint"])
    assert session.document == original and session.baseline == original
    assert session.resume_document(original)["can_accept"]
    loaded = CandidateSession.load(session.session_id, session.root, session.db_path)
    assert isinstance(loaded, BoardCandidateSession) and loaded.document == original
    assert not loaded.resume_document(accepted["board"])["can_accept"]
    assert loaded.document == loaded.baseline == accepted["board"] and loaded.changes == []
    assert add(loaded, db_character_id=2)["code"] == 0
    assert len(loaded.document["team"]) == 1 and len(loaded.candidate["team"]) == 2
    reread = CandidateSession.load(loaded.session_id, loaded.root, loaded.db_path)
    assert reread.summary() == loaded.summary()


@pytest.mark.parametrize("field,value", [("title", "其他窗口改名"), ("revision", 999), ("notes", "外部修改")])
def test_stale_current_document_cannot_accept_or_resume(session, field, value):
    summary = add(session)["data"]
    stale = copy.deepcopy(session.document)
    stale[field] = value
    for action in (lambda: session.accept("whiteboard", stale, 1, summary["receipt_fingerprint"]),
                   lambda: session.resume_document(stale)):
        with pytest.raises(PlanError) as exc:
            action()
        assert exc.value.reason == "agent_document_changed"


def test_receipt_and_revision_conflicts_do_not_apply(session):
    first = add(session)["data"]
    assert patch(session, [{"op": "replace", "path": "/title", "value": "第二步"}])["code"] == 0
    for revision, receipt in [(1, first["receipt_fingerprint"]), (2, first["receipt_fingerprint"])]:
        with pytest.raises(PlanError):
            session.accept("whiteboard", session.document, revision, receipt)
    assert patch(session, [{"op": "replace", "path": "/title", "value": "过期"}], expected_revision=0)["reason"] == "agent_stale_revision"


@pytest.mark.parametrize("field,value", [("board_id", str(uuid.uuid4())), ("kind", "native"),
    ("version_line", "beta"), ("schema_version", 2), ("revision", 40)])
def test_identity_is_never_editable(session, field, value):
    result = patch(session, [{"op": "replace", "path": "/" + field, "value": value}])
    assert result["reason"] == "agent_patch_forbidden" and session.revision == 0


def test_bad_team_and_dangling_unsorted_or_outside_actions_are_atomic(session):
    assert add(session)["code"] == 0
    before = copy.deepcopy(session.candidate)
    assert add(session)["code"] == 1
    assert add(session, db_character_id=999)["reason"] == "agent_character_not_found"
    action = {"id": "e", "character": "character-1", "action_type_id": "action.elemental_skill",
              "label": "战技", "start": 1, "variant": "tap", "notes": ""}
    for change in ({"character": "absent"}, {"start": 20}, {"action_type_id": "damage"}):
        assert patch(session, [{"op": "add", "path": "/actions/-", "value": {**action, **change}}])["code"] == 1
        assert session.candidate == before
    assert patch(session, [{"op": "add", "path": "/actions/-", "value": action}])["code"] == 0
    assert patch(session, [{"op": "remove", "path": "/team/0"}])["code"] == 1
    assert patch(session, [{"op": "add", "path": "/actions/-", "value": {**action, "id": "q", "start": 0}}])["code"] == 1
    assert patch(session, [{"op": "remove", "path": "/actions/0"}, {"op": "remove", "path": "/team/0"}])["code"] == 0
    assert validate_whiteboard(session.candidate)["valid"]


def test_tool_scope_enforced_at_runtime_in_both_workflows(session, tmp_path):
    names = {item["name"] for item in session.tool_definitions()}
    assert {"gs_add_character", "gs_patch_whiteboard", "gs_character_panel_basis"} <= names
    for name in ("gs_calculate", "gs_compose", "gs_patch_candidate", "gs_assemble_attack", "gs_refresh_data_refs", "gs_review_result"):
        assert name not in names
        assert session.call_tool(name, {})["reason"] == "agent_tool_forbidden"
    for method in (session.compose, session.calculate):
        with pytest.raises(PlanError):
            method(0)
    native = CandidateSession(uuid.uuid4().hex, tmp_path / "native", tmp_path / "none.db", "native", delivery_plan())
    for name in ("gs_add_character", "gs_patch_whiteboard"):
        assert native.call_tool(name, {})["reason"] == "agent_tool_forbidden"
    assert {"gs_search_catalog", "gs_character_panel_basis"} <= {item["name"] for item in native.tool_definitions()}
    assert session.call_tool("gs_read_context", {"resource": "capabilities"})["code"] == 0
    assert session.call_tool("gs_record_finding", {"record": {"id": "choice", "type": "decision", "description": "按用户指定选择角色"}})["code"] == 0


def test_changed_database_blocks_all_mutations_accept_and_resume(session, monkeypatch):
    summary = add(session)["data"]
    monkeypatch.setattr(queries, "get_meta", lambda _: {"changed": True})
    assert not session.summary()["can_accept"]
    assert patch(session, [{"op": "replace", "path": "/title", "value": "失败"}])["reason"] == "agent_database_changed"
    for operation in (lambda: session.accept("whiteboard", session.document, 1, summary["receipt_fingerprint"]),
                      lambda: session.resume_document(session.candidate)):
        with pytest.raises(PlanError) as exc:
            operation()
        assert exc.value.reason == "agent_database_changed"


@pytest.mark.parametrize("field,value", [("workflow", "other"), ("workflow", "calculate"),
    ("kind", "native"), ("calculated_file", "calculated-" + "a" * 64 + ".json"), ("receipt_fingerprint", "a" * 64)])
def test_corrupt_or_cross_workflow_checkpoint_rejected(session, field, value):
    checkpoint = read_owned(session.root, "checkpoint.json", MAX_CHECKPOINT)
    checkpoint[field] = value
    (session.root / "checkpoint.json").write_bytes(json_bytes(checkpoint))
    with pytest.raises(PlanError):
        CandidateSession.load(session.session_id, session.root, session.db_path)


def test_failed_patch_or_baseline_persistence_rolls_back_memory_and_disk(session, monkeypatch):
    from genshin_app.agent_chat import candidate as module
    summary = add(session)["data"]
    accepted = session.accept("whiteboard", session.document, 1, summary["receipt_fingerprint"])
    original, candidate = copy.deepcopy(session.document), copy.deepcopy(session.candidate)
    previous_write = module.write_owned

    def fail(root, name, value, maximum):
        if name == "checkpoint.json":
            raise OSError("模拟磁盘已满")
        return previous_write(root, name, value, maximum)

    monkeypatch.setattr(module, "write_owned", fail)
    assert patch(session, [{"op": "replace", "path": "/notes", "value": "未落盘"}])["code"] == 1
    assert session.candidate == candidate and session.revision == 1
    with pytest.raises(OSError):
        session.resume_document(accepted["board"])
    assert session.document == original and session.baseline == original
    loaded = CandidateSession.load(session.session_id, session.root, session.db_path)
    assert loaded.document == original and loaded.candidate == candidate


def test_noop_and_reverted_changes_are_not_claimed_as_new_draft(session):
    title = session.candidate["title"]
    assert patch(session, [{"op": "replace", "path": "/title", "value": title}])["data"]["revision"] == 0
    assert patch(session, [{"op": "replace", "path": "/title", "value": "临时"}])["code"] == 0
    reverted = patch(session, [{"op": "replace", "path": "/title", "value": title}])["data"]
    assert not reverted["can_accept"] and reverted["diff_total"] == 0


def test_legacy_checkpoint_without_workflow_stays_calculation(tmp_path):
    session = CandidateSession(uuid.uuid4().hex, tmp_path / "legacy", tmp_path / "none.db", "native", delivery_plan())
    path = session.root / "checkpoint.json"
    checkpoint = json.loads(path.read_text("utf8"))
    checkpoint.pop("workflow")
    path.write_bytes(json_bytes(checkpoint))
    assert CandidateSession.load(session.session_id, session.root, session.db_path).workflow == "calculate"
