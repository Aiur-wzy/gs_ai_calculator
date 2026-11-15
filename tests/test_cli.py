"""db 命令行为测试：只读 status、已弃用写入口和 JSON 包络。"""

import json
import sqlite3

import pytest

from genshin_app.cli import main


def test_status_missing_db(tmp_path, capsys):
    rc = main(["--data-dir", str(tmp_path), "db", "status"])
    assert rc == 1
    err = json.loads(capsys.readouterr().err)
    assert err["code"] == 1 and "数据库不存在" in err["message"]


def test_status_on_built_db(tmp_path, capsys):
    db = tmp_path / "genshin.db"
    conn = sqlite3.connect(db)
    with conn:
        conn.executescript("""
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE character (id INTEGER, version_line TEXT);
            CREATE TABLE skill (id INTEGER);
            CREATE TABLE talent (id INTEGER);
            CREATE TABLE skill_param (proud_skill_group_id INTEGER);
            CREATE TABLE text_map (hash TEXT);
        """)
        conn.executemany(
            "INSERT INTO meta VALUES (?,?)",
            [("upstream_commit", "abc123def456"), ("game_version", "6.5"),
             ("built_at", "2026-09-02T00:00:00+00:00")],
        )
        conn.execute("INSERT INTO character VALUES (1, 'release')")
    conn.close()
    rc = main(["--data-dir", str(tmp_path), "db", "status"])
    data = json.loads(capsys.readouterr().out)["data"]
    assert rc == 0
    assert data["upstream_commit"] == "abc123def456"
    assert data["game_version"] == "6.5"
    assert data["version_lines"] == ["release"]


def test_update_is_deprecated_and_never_calls_legacy_mutator(tmp_path, monkeypatch, capsys):
    import genshin_db.pipeline

    monkeypatch.setattr(
        genshin_db.pipeline,
        "run_update",
        lambda *_args, **_kwargs: pytest.fail("旧入口不得调用候选构建器"),
    )
    assert main(["--data-dir", str(tmp_path), "db", "update"]) == 1
    err = json.loads(capsys.readouterr().err)
    assert err["reason"] == "accepted_database_is_immutable"
    assert err["details"]["lifecycle"] == "deprecated"
    assert err["details"]["active_database_modified"] is False
    assert not (tmp_path / "genshin.db").exists()


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "db" in capsys.readouterr().out
