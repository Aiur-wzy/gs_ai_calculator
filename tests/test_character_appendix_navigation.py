"""The appendix navigator discovers knowledge; it never grants execution authority."""
import hashlib
import json
import sqlite3
import uuid
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from genshin_app.agent_chat import tools
from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.agent_chat.character_appendix_index import (
    query_character_appendix_index,
)
from genshin_app.agent_chat.native_tool_receipt import build_native_tool_receipt
from genshin_app.agent_chat.staged_candidate import (
    _descriptor_annotation_scope,
    _valid_resource_descriptor,
)
from genshin_app.cli import main
from genshin_app.whiteboard import new_whiteboard
from genshin_db import queries
from genshin_db.appendix_navigation import annotation_owner_scope


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "navigation.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE character(id INTEGER,name TEXT,skill_depot_id INTEGER,version_line TEXT);
            CREATE TABLE skill(id INTEGER,character_id INTEGER,proud_skill_group_id INTEGER,version_line TEXT);
            CREATE TABLE talent(id INTEGER,character_id INTEGER,version_line TEXT);
            CREATE TABLE skill_param(proud_skill_group_id INTEGER,version_line TEXT);
            CREATE TABLE extra_info(id INTEGER,info_type TEXT,content TEXT,version_line TEXT,
                source_id INTEGER,proud_skill_group_id INTEGER,target_type TEXT,target_id INTEGER);
            CREATE TABLE annotation(id INTEGER,target_type TEXT,target_id INTEGER,version_line TEXT,
                status TEXT,category TEXT,title TEXT,content TEXT);
            INSERT INTO character VALUES(1,'角色一',10,'release'),(2,'角色二',20,'release'),
                (3,'测试线角色',30,'beta');
            INSERT INTO skill VALUES(11,1,111,'release'),(11,3,111,'beta');
            INSERT INTO talent VALUES(12,1,'release');
            INSERT INTO extra_info VALUES
                (1,'sub_skill','附属技能','release',10474,NULL,'character',1),
                (2,'sub_skill','共享附属技能','release',10474,NULL,'character',2),
                (3,'sub_skill','另一版本','beta',10474,NULL,'character',3),
                (4,'inherent_skill','不能用source_id推成附属技能','release',10474,113,'character',2),
                (5,'sub_skill','不是角色归属','release',10474,NULL,'weapon',99),
                (6,'inherent_skill','固有天赋','release',NULL,114,'character',1);
            INSERT INTO annotation VALUES
                (1,'skill',10474,'release','pending','action_dispatch','不应出现的标题','不应出现的正文'),
                (2,'skill',11,NULL,'approved','timing','另一标题','另一正文'),
                (3,'talent',12,'release','rejected','timing','拒绝资料','不得返回'),
                (4,'skill',10474,'beta','pending','other_version','测试线','测试正文'),
                (5,'skill',888,'release','pending','unowned','无归属','不要泄漏');
        """)
    return path


def _query(db, cid=1, line="release"):
    return query_character_appendix_index(db, {
        "db_character_id": cid, "version_line": line, "database_fingerprint": "a" * 64})


def test_sub_skill_owner_is_generic_multiowner_and_versioned(db):
    assert queries.annotation_target_scope(db, "skill", 10474)["character_ids"] == [1, 2]
    assert queries.annotation_target_scope(db, "skill", 10474, "beta")["character_ids"] == [3]
    assert queries.annotation_target_scope(db, "skill", 888)["character_ids"] == []
    assert queries.annotation_target_scope(db, "skill", 11)["character_ids"] == [1]
    assert queries.annotation_target_scope(db, "inherent_skill", 114)["character_ids"] == [1]
    with sqlite3.connect(db) as connection:
        connection.execute("INSERT INTO skill VALUES(10474,4,NULL,'release')")
    assert queries.annotation_target_scope(db, "skill", 10474)["character_ids"] == [1, 2, 4]


def test_index_has_existing_targets_and_equivalent_public_category_counts(db):
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    result = _query(db)
    assert result["contract_id"] == "agent-character-appendix-index-v1"
    assert result["status"] == "complete"
    assert result["body_read"] is False and result["execution_authority"] == "none"
    assert result["coverage"]["total_by_status"] == {"approved": 1, "pending": 1}
    assert result["coverage"]["enumeration_complete"] is True
    assert {row["target_type"] for row in result["targets"]} == {
        "character", "skill", "skill_param", "talent", "inherent_skill"}
    sub = next(row for row in result["targets"] if row["target_id"] == 10474)
    assert sub["owner_scope"]["character_ids"] == [1, 2]
    assert sub["discovery_sources"] == [{"table": "extra_info", "record_id": 1, "field": "source_id"}]
    for row in result["targets"]:
        for status in ("approved", "pending"):
            old = queries.list_annotation_categories(db, row["target_type"], row["target_id"], status)
            assert [{"category": item["category"], "count": item["count"]}
                    for item in row["categories"] if item["status"] == status] == old["categories"]
    serialized = json.dumps(result, ensure_ascii=False)
    assert "不应出现" not in serialized and "不得返回" not in serialized
    assert "other_version" not in serialized and "unowned" not in serialized
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_zero_and_not_found_and_unreadable_are_distinct(db):
    with sqlite3.connect(db) as connection:
        connection.execute("INSERT INTO character VALUES(4,'没有附录',40,'release')")
    empty = _query(db, 4)
    assert empty["status"] == "complete" and empty["nonempty_targets"] == []
    absent = _query(db, 404)
    assert absent["status"] == "not_found"
    assert absent["coverage"]["enumeration_complete"] is False
    with sqlite3.connect(db) as connection:
        connection.execute("INSERT INTO annotation VALUES(6,'character',1,'release','pending',NULL,'','')")
    partial = _query(db)
    assert partial["status"] == "partial"
    assert partial["coverage"]["unreadable_categories"] == 1
    assert any(row["code"] == "appendix_category_not_queryable" for row in partial["issues"])
    assert partial["coverage"]["total_by_status"]["pending"] == 2


def test_legacy_inferred_target_is_not_claimed_as_proven_discovery(db):
    with sqlite3.connect(db) as connection:
        connection.execute("INSERT INTO extra_info VALUES(7,'inherent_skill',"
                           "'参数组 115','release',NULL,NULL,'character',1)")
    result = _query(db)
    assert result["status"] == "partial"
    assert any(row["code"] == "target_discovery_evidence_missing" for row in result["issues"])


def test_missing_sub_skill_source_id_is_partial_instead_of_claiming_no_appendix(db):
    with sqlite3.connect(db) as connection:
        connection.execute("INSERT INTO extra_info VALUES(7,'sub_skill',"
                           "'未暴露身份','release',NULL,NULL,'character',1)")
    result = _query(db)
    assert result["status"] == "partial"
    assert {"code": "sub_skill_source_id_unavailable", "table": "extra_info", "record_id": 7} in result["issues"]


def test_index_reads_one_snapshot_even_when_another_writer_commits(db, monkeypatch):
    import genshin_db.appendix_navigation as navigation

    with sqlite3.connect(db) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    real = navigation._discovery_sources

    def mutate_after_first_read(connection, character_id, version_line):
        with sqlite3.connect(db) as writer:
            writer.execute("UPDATE annotation SET category='changed_after_snapshot' WHERE id=1")
        return real(connection, character_id, version_line)

    monkeypatch.setattr(navigation, "_discovery_sources", mutate_after_first_read)
    result = _query(db)
    assert result["nonempty_targets"][-1]["categories"][0]["category"] == "action_dispatch"
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT category FROM annotation WHERE id=1").fetchone()[0] == "changed_after_snapshot"


@pytest.fixture
def session(db, tmp_path):
    root = tmp_path / "session"
    root.mkdir()
    return SimpleNamespace(
        workflow="artifact", workflow_stage="loadout_draft", kind="whiteboard",
        db_path=db, root=root, candidate={"version_line": "release"}, resources={},
        database_snapshot={"snapshot": "fixture"},
        stage_state={"resource_stages": {}, "resource_descriptors": {}},
        _is_board=lambda: True, _read_db=nullcontext, save=lambda: None)


def test_tool_persists_index_and_pages_existing_resources_without_granting_body_authority(session):
    args = {"db_character_id": 1}
    response = tools.call_tool(session, "gs_get_character_appendix_index", args)
    assert response["code"] == 0, response
    data = response["data"]
    assert data["pointer"] == "/nonempty_targets"
    assert data["total"] == 2 and data["next_offset"] is None
    assert data["status"] == "complete" and data["body_read"] is False
    name = data["resource"]
    assert len(session.resources) == 1 and (session.root / (name + ".json")).is_file()
    descriptor = session.stage_state["resource_descriptors"][name]
    assert descriptor["query_kind"] == "appendix_index"
    assert _valid_resource_descriptor(name, descriptor)
    assert not _descriptor_annotation_scope(descriptor)
    assert tools.call_tool(session, "gs_read_context", {"resource": name, "pointer": "/targets", "limit": 2})["data"]["next_offset"] == 2
    again = tools.call_tool(session, "gs_get_character_appendix_index", args)
    assert again == response and len(session.resources) == 1
    session.workflow_stage = "calculation_closure"
    assert "gs_get_character_appendix_index" in tools.recommended_tools_for_session(session)
    restored = json.loads((session.root / (name + ".json")).read_text(encoding="utf-8"))
    assert restored == session.resources[name]
    assert tools.NATIVE_V2_READ_POLICIES["gs_get_character_appendix_index"] == "session_resource_read"
    assert tools.NATIVE_V2_TOOL_POLICIES["gs_get_character_appendix_index"]["effect"] == "session_resource"
    capabilities = tools.call_tool(session, "gs_read_context", {
        "resource": "appendix_navigation_capabilities"})
    assert capabilities["code"] == 0, capabilities


@pytest.mark.parametrize("args", [{}, {"db_character_id": 0}, {"db_character_id": True},
    {"db_character_id": "1"}, {"db_character_id": 1, "version_line": "beta"},
    {"db_character_id": 1, "database_fingerprint": "forged"},
    {"db_character_id": 2**53}])
def test_tool_closed_input_cannot_override_snapshot(session, args):
    response = tools.call_tool(session, "gs_get_character_appendix_index", args)
    assert response["reason"] == "agent_tool_input", response
    assert session.resources == {}


def test_tool_missing_table_reports_system_failure_instead_of_empty_index(session):
    with sqlite3.connect(session.db_path) as connection:
        connection.execute("DROP TABLE character")
    result = tools.call_tool(session, "gs_get_character_appendix_index", {"db_character_id": 1})
    assert result["code"] != 0 and result.get("failure_kind") == "system"
    assert session.resources == {}


def test_large_navigation_uses_existing_resource_paging_without_omission(session):
    with sqlite3.connect(session.db_path) as connection:
        for index in range(60):
            sid = 1000 + index
            connection.execute("INSERT INTO skill VALUES(?,1,NULL,'release')", (sid,))
            connection.execute("INSERT INTO annotation VALUES(?,'skill',?,'release','pending',"
                               "'timing','标题','正文')", (1000 + index, sid))
    response = tools.call_tool(session, "gs_get_character_appendix_index", {"db_character_id": 1})
    assert response["code"] == 0, response
    data = response["data"]
    assert data["total"] == 62 and data["next_offset"] == 50
    assert data["issue_count"] == 0 and data["coverage"]["total_by_status"] == {"approved": 1, "pending": 61}
    next_page = tools.call_tool(session, "gs_read_context", {
        "resource": data["resource"], "pointer": "/nonempty_targets", "offset": 50, "limit": 200})["data"]
    assert len(next_page["items"]) == 12 and next_page["next_offset"] is None


def test_owner_helper_keeps_other_public_domains(db):
    with sqlite3.connect(db) as connection:
        connection.executescript("CREATE TABLE weapon_effect(affix_id,weapon_id,version_line);"
                                 "INSERT INTO weapon_effect VALUES(19,20,'release');")
        assert annotation_owner_scope(connection, "equip_affix", 19, "release")["weapon_ids"] == [20]


def test_cli_appendix_selects_pending_without_mixing_status_or_version(db, tmp_path, monkeypatch, capsys):
    database = tmp_path / "genshin.db"
    database.write_bytes(db.read_bytes())
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE weapon(id INTEGER,version_line TEXT);
            INSERT INTO weapon VALUES(20,'release'),(21,'release'),(20,'beta');
            INSERT INTO annotation VALUES
                (20,'weapon',20,'release','pending','weapon_passive','first','pending first'),
                (21,'weapon',20,NULL,'pending','weapon_passive','second','pending shared'),
                (22,'weapon',20,'release','approved','weapon_passive','approved','approved body'),
                (23,'weapon',20,'beta','pending','weapon_passive','beta','beta body'),
                (24,'weapon',21,'release','pending','weapon_passive','other','other weapon'),
                (25,'weapon',20,'release','rejected','weapon_passive','rejected','rejected body');
        """)
    before = database.read_bytes()
    admitted = []
    monkeypatch.setattr("genshin_app.cli._require_runtime_database", admitted.append)
    request_file = tmp_path / "request.json"
    selector = {"target_type": "weapon", "target_id": 20, "version_line": "release", "status": "pending"}

    def invoke(request, code=0):
        request_file.write_text(json.dumps(request), encoding="utf-8")
        assert main(["--data-dir", str(tmp_path), "db", "appendix", "--input", str(request_file)]) == code
        streams = capsys.readouterr()
        return json.loads(streams.out if code == 0 else streams.err)

    discovery = invoke(selector)["data"]
    assert discovery == tools._appendix_category_index(database, selector)
    assert discovery["total"] == 2
    category = discovery["categories"][0]["category"]
    query = {**selector, "category": category, "page_size": 1}
    first = invoke(query)["data"]
    second = invoke({**query, "page": 2})["data"]
    assert first == tools._appendix_query(database, query)
    assert [page["items"][0]["id"] for page in (first, second)] == [20, 21]
    assert first["items"][0]["execution_authority"] == "none"
    assert first["owner_scope"]["weapon_ids"] == [20]
    assert invoke({**query, "status": "approved"})["data"]["items"][0]["id"] == 22
    for invalid in ({key: value for key, value in selector.items() if key != "status"},
                    {**selector, "status": "all"}, {**selector, "page": 1}):
        error = invoke(invalid, code=1)
        assert error["reason"] == "appendix_query_schema_invalid"
        assert error["category"] == "request_error"
    assert admitted == [str(tmp_path)] * 7
    assert database.read_bytes() == before


def test_index_resource_crosses_native_receipt_boundary(session):
    session.workflow = "staged"
    response = tools.call_tool(session, "gs_get_character_appendix_index", {"db_character_id": 1})
    assert response["code"] == 0, response
    receipt = build_native_tool_receipt(
        session, tool="gs_get_character_appendix_index", arguments={"db_character_id": 1},
        response=response, policy="session_resource_read", resource_scope={
            "session_scope_token": "b" * 64, "candidate_revision": 0,
            "stage_revision": 0, "tool_manifest_hash": "c" * 64})
    assert receipt.resource_ids == (response["data"]["resource"],)
    assert receipt.issued_references[0]["query_kind"] == "appendix_index"


def test_artifact_index_resource_survives_real_session_restore(db, tmp_path, monkeypatch):
    from genshin_app.agent_chat import character_appendix_index as adapter

    original = adapter.query_character_appendix_index
    monkeypatch.setattr(adapter, "query_character_appendix_index",
                        lambda _path, request: original(db, request))
    session = ArtifactCandidateSession(uuid.uuid4().hex, tmp_path / "artifact-session",
                                       tmp_path / "absent.db", "whiteboard", new_whiteboard())
    before = json.dumps(session.candidate, sort_keys=True)
    response = session.call_tool("gs_get_character_appendix_index", {"db_character_id": 1})
    assert response["code"] == 0, response
    name = response["data"]["resource"]
    restored = CandidateSession.load(session.session_id, session.root, session.db_path)
    assert isinstance(restored, ArtifactCandidateSession)
    assert restored.resources[name] == session.resources[name]
    assert restored.stage_state["resource_descriptors"][name]["query_kind"] == "appendix_index"
    assert json.dumps(restored.candidate, sort_keys=True) == before
    assert restored.call_tool("gs_read_context", {"resource": name, "pointer": "/targets"})["code"] == 0
