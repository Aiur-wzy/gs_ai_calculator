"""pipeline 模块测试：模拟新旧两库验证切换、幂等与 annotation 迁移。"""

import json
import sqlite3
from contextlib import closing

import pytest

from genshin_db import fetcher, pipeline, upstream_version
from genshin_db.annotation_store import (
    Annotation,
    add_annotation,
)
from genshin_db.release_integrity import build_release_core
from tests import fixture_candidate_builder as fixture_builder
from tests.equipment_fixtures import equipment_files
from tests.runtime_fixtures import runtime_files

MINIMAL_FILES = {
    **equipment_files(),
    **runtime_files(),
    "ExcelBinOutput/AvatarExcelConfigData.json": [{
        "id": 90000001, "nameTextMapHash": 100, "descTextMapHash": 101,
        "qualityType": "QUALITY_ORANGE", "weaponType": "WEAPON_POLE",
        "hpBase": 1, "attackBase": 1, "defenseBase": 1,
        "skillDepotId": 9001, "avatarPromoteId": 900,
    }],
    "ExcelBinOutput/AvatarSkillExcelConfigData.json": [
        {"id": 90011, "nameTextMapHash": 200, "descTextMapHash": 201, "proudSkillGroupId": 91001},
        {"id": 90013, "nameTextMapHash": 220, "descTextMapHash": 221,
         "cdTime": 15, "costElemVal": 60, "costElemType": "Rock", "proudSkillGroupId": 91002},
    ],
    "ExcelBinOutput/AvatarTalentExcelConfigData.json": [
        {"talentId": 9001, "nameTextMapHash": 300, "descTextMapHash": 301,
         "paramList": [0.1], "addProps": []},
    ],
    "ExcelBinOutput/AvatarSkillDepotExcelConfigData.json": [{
        "id": 9001, "skills": [90011, 0, 0, 0], "energySkill": 90013, "talents": [9001],
    }],
    "ExcelBinOutput/ProudSkillExcelConfigData.json": [
        {"proudSkillGroupId": 91001, "level": 1, "paramList": [0.5], "paramDescList": [201]},
    ],
    "ExcelBinOutput/FetterInfoExcelConfigData.json": [],
    "ExcelBinOutput/AvatarCurveExcelConfigData.json": [],
    "ExcelBinOutput/AvatarPromoteExcelConfigData.json": [],
    "TextMap/TextMap_MediumCHS.json": {
        "100": "甲", "101": "述", "200": "普攻", "201": "造成{param1:F1P}伤害",
        "220": "爆发", "221": "造成岩元素伤害", "300": "一命", "301": "提升攻击力",
    },
}


@pytest.fixture
def fake_repo(tmp_path):
    repo = tmp_path / "fake_repo"
    for rel, data in MINIMAL_FILES.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return repo


@pytest.fixture
def stub_fetcher(fake_repo, monkeypatch):
    """可控的 ensure_upstream stub：calls 控制 changed/commit 序列。"""
    evidence = {
        "resolver_version": 1,
        "status": "resolved",
        "version_evidence_ready": True,
        "snapshot_commit": "a" * 40,
        "snapshot_updated_at": "2026-10-26T00:00:00+00:00",
        "track": "OSCBWin",
        "version": "7.1.51",
        "raw_version": "OSCBWin7.1.51",
        "evidence_kind": "snapshot_commit_subject",
        "evidence_commit": "a" * 40,
        "review_id": None,
        "configuration_trees": {
            "BinOutput": "1" * 40,
            "ExcelBinOutput": "2" * 40,
        },
        "source_trees": {
            "BinOutput": "1" * 40,
            "ExcelBinOutput": "2" * 40,
            "TextMap": "3" * 40,
        },
        "readme_version": "OSCBWin7.1.51",
        "issues": [],
    }
    evidence["evidence_fingerprint"] = upstream_version.evidence_fingerprint(evidence)
    state = {"commit": "a" * 40, "calls": 0, "version_evidence": evidence}

    def _stub(cache_dir):
        state["calls"] += 1
        current_evidence = {
            **state["version_evidence"],
            "snapshot_commit": state["commit"],
            "evidence_commit": state["commit"],
        }
        current_evidence["evidence_fingerprint"] = upstream_version.evidence_fingerprint(
            current_evidence
        )
        return fetcher.FetchResult(
            repo_dir=fake_repo,
            commit=state["commit"],
            changed=state["calls"] == 1,
            snapshot_updated_at=current_evidence["snapshot_updated_at"],
            version_evidence=current_evidence,
        )

    monkeypatch.setattr(fetcher, "ensure_upstream", _stub)
    monkeypatch.setattr(
        fetcher,
        "verify_pinned_sources",
        lambda _repo, commit, manifest: {
            "mode": "test-pinned-git-blob-v1",
            "commit": commit,
            "manifest_fingerprint": manifest["fingerprint"],
            "checked_files": len(manifest["files"]),
        },
    )
    return state


def test_first_run_creates_live_db(tmp_path, stub_fetcher):
    report = fixture_builder.build_database(tmp_path)
    assert report.updated is True
    assert report.old_version is None
    assert report.new_version == "a" * 12
    live = tmp_path / "genshin.db"
    assert live.exists()
    conn = sqlite3.connect(live)
    n = conn.execute("SELECT COUNT(*) FROM character").fetchone()[0]
    meta = dict(conn.execute("SELECT key,value FROM meta"))
    conn.close()
    assert n == 1
    assert meta["upstream_commit"] == "a" * 40
    assert meta["game_version"] == "7.1.51"
    assert meta["upstream_track"] == "OSCBWin"
    assert meta["upstream_updated_at"] == "2026-10-26T00:00:00+00:00"
    assert meta["indexer_version"] == "7"
    assert meta["normalization_version"] == "unavailable"
    assert json.loads(meta["upstream_version_evidence"])["version_evidence_ready"] is True


def test_pinned_snapshot_build_is_release_core_deterministic(tmp_path, stub_fetcher):
    expected = stub_fetcher["version_evidence"]["snapshot_updated_at"]
    first = tmp_path / "first"
    second = tmp_path / "second"
    fixture_builder.build_database(first)
    fixture_builder.build_database(second)

    for database in (first / "genshin.db", second / "genshin.db"):
        with closing(sqlite3.connect(database)) as conn:
            assert {row[0] for row in conn.execute(
                "SELECT DISTINCT imported_at FROM raw_table"
            )} == {expected}
            assert {row[0] for row in conn.execute(
                "SELECT DISTINCT imported_at FROM source_manifest"
            )} == {expected}
            assert {row[0] for row in conn.execute(
                "SELECT DISTINCT built_at FROM data_component "
                "WHERE component IN ('equipment','runtime_config')"
            )} == {expected}
            assert conn.execute(
                "SELECT value FROM meta WHERE key='built_at:release'"
            ).fetchone()[0] == expected
            assert {row[0] for row in conn.execute(
                "SELECT DISTINCT imported_at FROM runtime_source"
            )} == {expected}

    assert build_release_core(first / "genshin.db")["root_sha256"] == (
        build_release_core(second / "genshin.db")["root_sha256"]
    )


def test_second_run_no_change(tmp_path, stub_fetcher):
    fixture_builder.build_database(tmp_path)
    report = fixture_builder.build_database(tmp_path)
    assert report.updated is False
    assert report.old_version == "a" * 12


def test_display_index_rebuilt_with_same_snapshot_and_manual_alias_preserved(tmp_path, stub_fetcher):
    from genshin_db.display_names import display_complete

    fixture_builder.build_database(tmp_path)
    live = tmp_path / "genshin.db"
    with closing(sqlite3.connect(live)) as conn, conn:
        conn.execute("INSERT INTO entity_alias VALUES (?,?,?,?,?,?,?,?,?)",
                     ("character", 90000001, "release", "zh-CN", "人工称呼", "人工称呼",
                      "manual", '{}', None))
        conn.execute("DELETE FROM meta WHERE key='display_generator:release'")
        assert not display_complete(conn, "release")
    report = fixture_builder.build_database(tmp_path)
    assert report.updated is True
    with closing(sqlite3.connect(live)) as conn, conn:
        assert display_complete(conn, "release")
        assert conn.execute("SELECT alias FROM entity_alias WHERE source_kind='manual'").fetchall() == [("人工称呼",)]


def test_future_display_schema_refuses_update_before_fetch(tmp_path, stub_fetcher):
    fixture_builder.build_database(tmp_path)
    live = tmp_path / "genshin.db"
    with closing(sqlite3.connect(live)) as conn, conn:
        conn.execute("UPDATE meta SET value='2' WHERE key='display_schema_version'")
    calls, before = stub_fetcher["calls"], live.read_bytes()
    with pytest.raises(ValueError, match="不受支持"):
        fixture_builder.build_database(tmp_path)
    assert live.read_bytes() == before and stub_fetcher["calls"] == calls


def test_force_rebuild(tmp_path, stub_fetcher):
    fixture_builder.build_database(tmp_path)
    report = fixture_builder.build_database(tmp_path, force=True)
    assert report.updated is True


def test_description_sources_use_new_commit_not_old_snapshot(tmp_path, stub_fetcher):
    fixture_builder.build_database(tmp_path)
    stub_fetcher["commit"] = "b" * 40
    fixture_builder.build_database(tmp_path, force=True)
    conn = sqlite3.connect(tmp_path / "genshin.db")
    try:
        sources = [json.loads(r[0]) for r in conn.execute("SELECT desc_source FROM skill")]
        assert sources and all(s["upstream_commit"] == "b" * 40 for s in sources)
        meta = dict(conn.execute("SELECT key,value FROM meta"))
        assert meta["raw_upstream_commit"] == "b" * 40
        assert meta["raw_version_line"] == "release"
        assert meta["description_schema_version"] == "1"
    finally:
        conn.close()


def test_legacy_annotation_migration_preserves_review_state(tmp_path, stub_fetcher):
    fixture_builder.build_database(tmp_path)
    live = tmp_path / "genshin.db"
    add_annotation(live, Annotation(target_type="character", target_id=90000001,
                                    content="保留", status="approved"))
    add_annotation(live, Annotation(target_type="skill", target_id=99999999,
                                    content="目标不存在", status="approved"))

    stub_fetcher["commit"] = "b" * 40  # 上游更新
    state = stub_fetcher
    state["calls"] = 0  # 让 changed=True 只在下次触发
    report = fixture_builder.build_database(tmp_path, force=True)

    assert report.updated is True
    assert report.orphan_annotations == 0
    conn = sqlite3.connect(live)
    rows = dict(conn.execute("SELECT content, status FROM annotation").fetchall())
    conn.close()
    assert rows["保留"] == "approved"
    assert rows["目标不存在"] == "approved"


def test_validation_failure_keeps_old_db(tmp_path, stub_fetcher, fake_repo):
    fixture_builder.build_database(tmp_path)
    # 破坏数据源：avatar 变空数组 → character 表为空 → 校验失败
    (fake_repo / "ExcelBinOutput" / "AvatarExcelConfigData.json").write_text("[]", encoding="utf-8")
    with pytest.raises(pipeline.PipelineError):
        fixture_builder.build_database(tmp_path, force=True)
    # 旧库仍在且数据完整
    conn = sqlite3.connect(tmp_path / "genshin.db")
    n = conn.execute("SELECT COUNT(*) FROM character").fetchone()[0]
    conn.close()
    assert n == 1


def test_version_regex():
    """T9：从上游 commit message 解析游戏版本号。"""
    from genshin_db import pipeline
    assert pipeline._VERSION_RE.search("OSCBWin7.0.54_R48007341_S47901782_D48007327").group(1) == "7.0.54"
    assert pipeline._VERSION_RE.search("OSRELWin7.0.0_release").group(1) == "7.0.0"
    assert pipeline._VERSION_RE.search("random message") is None


def test_same_commit_changed_version_evidence_rebuilds(tmp_path, stub_fetcher):
    fixture_builder.build_database(tmp_path)
    stub_fetcher["version_evidence"] = {
        **stub_fetcher["version_evidence"],
        "version": "7.1.52",
        "raw_version": "OSCBWin7.1.52",
    }
    stub_fetcher["version_evidence"]["evidence_fingerprint"] = (
        upstream_version.evidence_fingerprint(stub_fetcher["version_evidence"])
    )
    report = fixture_builder.build_database(tmp_path)
    assert report.updated is True
    with closing(sqlite3.connect(tmp_path / "genshin.db")) as conn:
        assert conn.execute(
            "SELECT value FROM meta WHERE key='game_version'"
        ).fetchone()[0] == "7.1.52"


@pytest.mark.parametrize(
    "key",
    [
        "upstream_track",
        "upstream_track:release",
        "upstream_updated_at",
        "upstream_updated_at:release",
        "upstream_version_evidence",
        "upstream_version_evidence:release",
        "indexer_version",
        "indexer_version:release",
        "normalization_version",
        "normalization_version:release",
    ],
)
def test_same_commit_identity_meta_drift_rebuilds(tmp_path, stub_fetcher, key):
    fixture_builder.build_database(tmp_path)
    live = tmp_path / "genshin.db"
    with closing(sqlite3.connect(live)) as conn, conn:
        expected = conn.execute(
            "SELECT value FROM meta WHERE key=?", (key,)
        ).fetchone()[0]
        conn.execute("UPDATE meta SET value='tampered' WHERE key=?", (key,))

    report = fixture_builder.build_database(tmp_path)

    assert report.updated is True
    with closing(sqlite3.connect(live)) as conn:
        assert conn.execute(
            "SELECT value FROM meta WHERE key=?", (key,)
        ).fetchone()[0] == expected


def test_unresolved_version_evidence_fails_before_database_build(tmp_path, stub_fetcher):
    stub_fetcher["version_evidence"] = {
        **stub_fetcher["version_evidence"],
        "status": "unresolved",
        "version_evidence_ready": False,
        "version": None,
        "track": None,
        "issues": ["primary_version_evidence_missing"],
    }

    with pytest.raises(pipeline.PipelineError, match="上游版本证据不完整"):
        fixture_builder.build_database(tmp_path)

    assert not (tmp_path / "genshin.db").exists()


def test_tampered_version_evidence_fingerprint_fails_before_database_build(
    stub_fetcher,
):
    tampered = {
        **stub_fetcher["version_evidence"],
        "readme_version": "OSCBWin0.0.0",
    }

    with pytest.raises(pipeline.PipelineError, match="上游版本证据不完整"):
        pipeline._require_version_evidence(tampered, "a" * 40)


def test_self_consistent_fake_source_tree_evidence_fails_before_database_build(
    stub_fetcher,
):
    tampered = {
        **stub_fetcher["version_evidence"],
        "source_trees": {
            "BinOutput": "1" * 40,
            "ExcelBinOutput": "2" * 40,
        },
    }
    tampered["evidence_fingerprint"] = upstream_version.evidence_fingerprint(
        tampered
    )

    with pytest.raises(pipeline.PipelineError, match="上游版本证据不完整"):
        pipeline._require_version_evidence(tampered, "a" * 40)


def test_candidate_fixture_preserves_other_line(tmp_path, stub_fetcher, fake_repo):
    from genshin_db.extractor import build_curated_layer

    fixture_builder.build_database(tmp_path)
    db = tmp_path / "genshin.db"
    build_curated_layer(db, "beta-test", "beta")
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("INSERT INTO text_map VALUES ('beta-only','CHS','beta text')")
    before_beta = {t: conn.execute(f"SELECT * FROM {t} WHERE version_line='beta'").fetchall()
                   for t in ("character", "skill", "talent", "skill_param", "extra_info")}
    conn.close()
    fixture_builder.build_database(tmp_path, force=True)
    fixture_builder.build_database(tmp_path)
    (fake_repo / "ExcelBinOutput/AvatarExcelConfigData.json").write_text("[]", encoding="utf8")
    with pytest.raises(pipeline.PipelineError):
        fixture_builder.build_database(tmp_path, force=True)
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM character WHERE version_line='beta'").fetchone()[0] == 1
        assert conn.execute("SELECT value FROM meta WHERE key='game_version:beta'").fetchone()[0] == "beta-test"
        assert conn.execute("SELECT text FROM text_map WHERE hash='beta-only'").fetchone()[0] == "beta text"
        for table, original_rows in before_beta.items():
            assert conn.execute(f"SELECT * FROM {table} WHERE version_line='beta'").fetchall() == original_rows
    finally:
        conn.close()


def test_import_log_redacts_machine_paths_but_keeps_failure_meaning(tmp_path):
    from genshin_db.import_log import record_import, sanitize_import_error

    error = (
        "[WinError 5] access denied: "
        "'D:\\fixture\\data\\build\\candidate.db' -> "
        "'D:\\fixture\\data\\example.db'; "
        'mirror="\\\\builder\\share\\candidate.db"; '
        "source=file:///C:/build/source.json"
    )
    sanitized = sanitize_import_error(error)
    assert sanitized == (
        "[WinError 5] access denied: <local-path> -> <local-path>; "
        "mirror=<local-path>; source=<local-path>"
    )

    db = tmp_path / "genshin.db"
    record_import(db, "a" * 40, "2026-10-24T00:00:00+00:00", "failed", error=error)
    with closing(sqlite3.connect(db)) as conn:
        persisted = conn.execute("SELECT error FROM import_log").fetchone()[0]
    assert persisted == sanitized
    assert "D:" not in persisted and "builder" not in persisted


def test_import_log_bounds_path_free_failure_diagnostics(tmp_path):
    from genshin_db.import_log import record_import

    db = tmp_path / "genshin.db"
    record_import(
        db,
        None,
        "2026-10-24T00:00:00+00:00",
        "failed",
        error="validation failed: " + "x" * 10_000,
    )
    with closing(sqlite3.connect(db)) as conn:
        persisted = conn.execute("SELECT error FROM import_log").fetchone()[0]
    assert len(persisted) == 4096
    assert persisted.endswith("...[truncated]")




def test_changed_fixture_base_is_rejected_without_merging(tmp_path, stub_fetcher, monkeypatch):
    from genshin_db import queries

    fixture_builder.build_database(tmp_path)
    db = tmp_path / "genshin.db"
    validate = pipeline._validate

    def write_while_building(path):
        queries.create_annotation(db, "character", 90000001, "accepted while building")
        validate(path)

    monkeypatch.setattr(pipeline, "_validate", write_while_building)
    with pytest.raises(pipeline.PipelineError, match="base_db"):
        fixture_builder.build_database(tmp_path, force=True)
    assert queries.list_annotations(db, status=None)["items"][0]["content"] == "accepted while building"
