from __future__ import annotations

import sqlite3

import pytest

from genshin_app.agent_chat.tools import _read_context
from genshin_app.equipment.schema import PlanError
from genshin_app.publication_usage import prepare_publication_usage
from genshin_app.workflow_workspace import workspace
from genshin_db.publication_evidence import ENTITY_TYPES, SCHEMA


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "genshin.db"
    with sqlite3.connect(path) as connection:
        connection.execute(SCHEMA)
        for kind in ENTITY_TYPES:
            connection.execute(f'CREATE TABLE "{kind}"(id INTEGER, version_line TEXT, name TEXT)')
        connection.execute("INSERT INTO character VALUES (11, 'release', '已上线角色'), (12, 'release', '预告角色')")
        connection.execute("INSERT INTO weapon VALUES (20, 'release', '未知武器')")
        connection.execute("INSERT INTO artifact_set VALUES (30, 'release', '未使用套装')")
        for kind, ident, status in [("character", 11, "released"), ("character", 12, "unreleased")]:
            connection.execute("INSERT INTO entity_publication VALUES (?, ?, 'release', ?, '2026-11-14', 'test', 'test', '[]')",
                               (kind, ident, status))
    return path


def _request(*entities, purpose="calculation"):
    return {"purpose": purpose, "entities": [{"type": kind, "id": ident} for kind, ident in entities]}


def test_only_entities_selected_for_use_are_named(db):
    request = _request(("weapon", 20), ("character", 12), ("character", 11), ("weapon", 20))
    from types import SimpleNamespace

    native = _read_context(SimpleNamespace(db_path=db, session_id="task"),
                           {"resource": "publication_usage", "request": request})
    assert len(native["entities"]) == 3
    text = native["question"]["question"]
    assert "预告角色（角色，尚未正式上线）" in text
    assert "未知武器（武器，目前无法核实是否已上线）" in text
    assert "未使用套装" not in text and "已上线角色" not in text
    assert native["acceptance_effect"] == "none"
    assert prepare_publication_usage(db, _request(("character", 11), purpose="answer"), task_id="task")["question"] is None
    with pytest.raises(PlanError, match="不存在"):
        prepare_publication_usage(db, _request(("weapon", 11)), task_id="task")
    with pytest.raises(PlanError, match="有效 type"):
        prepare_publication_usage(db, _request(("talent", 11)), task_id="task")


def test_cli_answer_reference_is_explicit_scoped_and_not_verified_by_app(db, tmp_path):
    audit = tmp_path / "task.json"
    task = tmp_path / "request.txt"
    task.write_text("做一套计算", encoding="utf8")
    workspace(None, audit, task_file=task)
    request = {"publication_usage": _request(("character", 12))}
    first = workspace(None, audit, request, data_dir=tmp_path)["publication_usage"]
    record = {"id": "user.decision", "type": "decision", "description": "宿主答复",
              "source_refs": [{"kind": "host_publication_answer", "answer": "approved",
                               "scope": "current-task", "purpose": "calculation",
                               "entities": ["character:12"], "question_id": first["question"]["id"],
                               "reference": "dsh:user-answer-1"}]}
    workspace(None, audit, {"operation": "append", "records": [record]})
    result = workspace(None, audit, {"publication_usage": _request(("character", 12), ("weapon", 20))},
                       data_dir=tmp_path)["publication_usage"]
    assert result["entities"][0]["response"]["status"] == "host_answer_reference"
    assert result["entities"][0]["response"]["verified_by_app"] is False
    assert "未知武器" in result["question"]["question"] and "预告角色" not in result["question"]["question"]
    new_scope = workspace(None, audit, {"publication_usage": _request(("character", 12), purpose="answer")},
                          data_dir=tmp_path)["publication_usage"]
    assert "预告角色" in new_scope["question"]["question"]
    assert result["acceptance_effect"] == "none"
