"""raw_store 模块测试：用小型假 JSON 验证表结构、行数与重建行为。"""

import json
import sqlite3

import pytest

from genshin_db import raw_store


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """构造含 2 个白名单文件的假上游目录。"""
    (tmp_path / "ExcelBinOutput").mkdir()
    (tmp_path / "TextMap").mkdir()
    (tmp_path / "ExcelBinOutput" / "AvatarExcelConfigData.json").write_text(
        json.dumps([{"id": 10000002, "nameTextMapHash": 1}, {"id": 10000003, "nameTextMapHash": 2}]),
        encoding="utf-8",
    )
    (tmp_path / "TextMap" / "TextMap_MediumCHS.json").write_text(
        json.dumps({"1": "荧", "2": "安柏"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        raw_store,
        "RAW_SOURCES",
        [
            "ExcelBinOutput/AvatarExcelConfigData.json",
            "TextMap/TextMap_MediumCHS.json",
        ],
    )
    return tmp_path


def test_build_raw_layer_table_and_counts(fake_repo, tmp_path):
    db_path = tmp_path / "build" / "x.db"
    stats = raw_store.build_raw_layer(fake_repo, db_path)
    assert stats.files == 2
    assert stats.rows == 4
    assert stats.table_counts == {"AvatarExcelConfigData": 2, "TextMap_MediumCHS": 2}

    conn = sqlite3.connect(db_path)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(raw_table)")]
        assert cols == ["source_file", "data_json", "imported_at"]
        rows = conn.execute("SELECT source_file, data_json FROM raw_table ORDER BY source_file").fetchall()
        assert len(rows) == 2
        avatar_json = next(r[1] for r in rows if r[0] == "AvatarExcelConfigData")
        assert json.loads(avatar_json)[0]["id"] == 10000002
    finally:
        conn.close()


def test_rebuild_replaces_content(fake_repo, tmp_path):
    db_path = tmp_path / "x.db"
    raw_store.build_raw_layer(fake_repo, db_path)
    (fake_repo / "ExcelBinOutput" / "AvatarExcelConfigData.json").write_text(
        json.dumps([{"id": 99999999}]),
        encoding="utf-8",
    )
    stats = raw_store.build_raw_layer(fake_repo, db_path)
    assert stats.table_counts["AvatarExcelConfigData"] == 1

    conn = sqlite3.connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM raw_table").fetchone()[0]
        assert total == 2
        data = conn.execute(
            "SELECT data_json FROM raw_table WHERE source_file='AvatarExcelConfigData'"
        ).fetchone()[0]
        assert json.loads(data)[0]["id"] == 99999999
    finally:
        conn.close()


def test_missing_file_raises(fake_repo, tmp_path):
    (fake_repo / "TextMap" / "TextMap_MediumCHS.json").unlink()
    with pytest.raises(raw_store.RawStoreError):
        raw_store.build_raw_layer(fake_repo, tmp_path / "x.db")


def test_bad_json_raises(fake_repo, tmp_path):
    (fake_repo / "ExcelBinOutput" / "AvatarExcelConfigData.json").write_text(
        "{not valid json", encoding="utf-8"
    )
    with pytest.raises(raw_store.RawStoreError):
        raw_store.build_raw_layer(fake_repo, tmp_path / "x.db")
