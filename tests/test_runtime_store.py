import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from genshin_db import pipeline
from genshin_db.runtime_catalog import RuntimeConfigError, enumerate_runtime_sources
from genshin_db.runtime_queries import (
    assemble_runtime_source,
    get_runtime_status,
    query_runtime,
)
from genshin_db.runtime_store import build_runtime_layer, validate_runtime
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401

# Fixture imports are registered by pytest and used as function arguments.
# ruff: noqa: F811


@pytest.fixture
def runtime_db(tmp_path, fake_repo, stub_fetcher):
    path = fake_repo / "ExcelBinOutput/EquipAffixExcelConfigData.json"
    data = json.loads(path.read_text("utf8"))
    data[0]["openConfig"] = "Fixture_Effect"
    path.write_text(json.dumps(data), "utf8")
    fixture_builder.build_database(tmp_path)
    return tmp_path / "genshin.db"


def test_bundle_is_complete_same_snapshot_and_retains_unknowns(runtime_db):
    before = hashlib.sha256(runtime_db.read_bytes()).hexdigest()
    bundle = assemble_runtime_source(runtime_db, 917)
    assert bundle["status"] == "ready" and bundle["reference_closed"]
    assert bundle["param_list"] == [0.2, 0, 2, 0]
    assert bundle["talent"]["operations"][0]["unknown_preserved"] is False
    assert bundle["abilities"][0]["payload"]["modifiers"]["Fixture_Modifier"]["unknown_preserved"] == [0, False, None]
    assert len(bundle["abilities"]) == 1
    assert bundle["source"]["snapshot_id"] == bundle["snapshot"]["snapshot_id"]
    assert hashlib.sha256(runtime_db.read_bytes()).hexdigest() == before


def test_same_commit_missing_runtime_fixture_rebuilds(runtime_db):
    with closing(sqlite3.connect(runtime_db)) as conn, conn:
        conn.execute("DELETE FROM data_component WHERE component='runtime_config'")
    report = fixture_builder.build_database(runtime_db.parent)
    assert report.updated and report.old_version == report.new_version
    assert get_runtime_status(runtime_db)["state"] == "ready"
    assert not fixture_builder.build_database(runtime_db.parent).updated


def test_optional_runtime_sources_degrade_capability_without_blocking_build(
    tmp_path, fake_repo, stub_fetcher
):
    for relative in (
        "BinOutput/Gadget/Fixture.json",
        "BinOutput/AnimatorConfig/Fixture.json",
        "BinOutput/Common/ConfigGlobalCombat.json",
        "BinOutput/AnimPattern/AnimPatternData.json",
    ):
        (fake_repo / relative).unlink()

    report = fixture_builder.build_database(tmp_path)
    status = get_runtime_status(tmp_path / "genshin.db")

    assert report.updated is True
    assert status["state"] == "ready"
    assert status["normalization_state"] == "unavailable"
    assert status["source_capability_state"] == "degraded"
    assert status["source_capabilities"]["global_combat"] == {
        "state": "unavailable",
        "requirement": "optional",
    }
    with closing(sqlite3.connect(tmp_path / "genshin.db")) as connection:
        validate_runtime(connection, "release", deep=True)


def test_same_commit_changed_source_changes_snapshot(runtime_db, fake_repo):
    first = assemble_runtime_source(runtime_db, 917)
    path = fake_repo / "BinOutput/Ability/Temp/EquipAbilities/Fixture.json"
    data = json.loads(path.read_text("utf8"))
    data[0]["Default"]["abilitySpecials"]["Rate"] = 0.1
    path.write_text(json.dumps(data), "utf8")
    assert fixture_builder.build_database(runtime_db.parent).updated
    assert assemble_runtime_source(runtime_db, 917)["source"]["snapshot_id"] != first["source"]["snapshot_id"]
    with pytest.raises(RuntimeConfigError) as exc:
        assemble_runtime_source(runtime_db, 917, snapshot_id=first["source"]["snapshot_id"])
    assert exc.value.reason == "snapshot_changed"


@pytest.mark.parametrize("variant", ["missing", "ambiguous"])
def test_unresolved_reference_remains_pending(runtime_db, fake_repo, variant):
    path = fake_repo / "BinOutput/Ability/Temp/EquipAbilities/Fixture.json"
    data = json.loads(path.read_text("utf8"))
    if variant == "missing":
        data[0]["Default"]["onAdded"][0]["modifierName"] = "Missing"
    else:
        data.append({"Alternate": data[0]["Default"]})
    path.write_text(json.dumps(data), "utf8")
    fixture_builder.build_database(runtime_db.parent)
    bundle = assemble_runtime_source(runtime_db, 917)
    assert bundle["status"] == "pending"
    assert variant in {x["code"] for x in bundle["issues"]}


def test_other_runtime_line_survives_rebuild(runtime_db, fake_repo):
    build_runtime_layer(runtime_db, fake_repo, version_line="beta", upstream_commit="beta", manifest=enumerate_runtime_sources(fake_repo))
    with closing(sqlite3.connect(runtime_db)) as conn:
        before = conn.execute("SELECT source_path,source_sha256,snapshot_id FROM runtime_source WHERE version_line='beta' ORDER BY source_path").fetchall()
    fixture_builder.build_database(runtime_db.parent, force=True)
    with closing(sqlite3.connect(runtime_db)) as conn:
        assert conn.execute("SELECT source_path,source_sha256,snapshot_id FROM runtime_source WHERE version_line='beta' ORDER BY source_path").fetchall() == before


def test_corrupt_blob_and_index_are_rejected(runtime_db):
    with closing(sqlite3.connect(runtime_db)) as conn, conn:
        conn.execute("UPDATE runtime_source SET payload_blob=? WHERE source_family='ability'", (b"bad",))
    with pytest.raises(RuntimeConfigError):
        assemble_runtime_source(runtime_db, 917)
    with closing(sqlite3.connect(runtime_db)) as conn, pytest.raises(RuntimeConfigError):
        validate_runtime(conn, "release", deep=True)


def test_build_failure_keeps_live_snapshot(runtime_db, fake_repo):
    original = assemble_runtime_source(runtime_db, 917)["content_fingerprint"]
    (fake_repo / "BinOutput/Talent/EquipTalents/Fixture.json").write_text('{"a":1,"a":2}', "utf8")
    with pytest.raises(RuntimeConfigError):
        fixture_builder.build_database(runtime_db.parent)
    assert assemble_runtime_source(runtime_db, 917)["content_fingerprint"] == original


def test_unknown_future_schema_refuses_update_before_fetch(runtime_db, monkeypatch):
    with closing(sqlite3.connect(runtime_db)) as conn, conn:
        conn.execute("UPDATE meta SET value='900' WHERE key='runtime_schema_version'")
    monkeypatch.setattr(pipeline.fetcher, "ensure_upstream", lambda _: pytest.fail("must not fetch"))
    with pytest.raises(pipeline.PipelineError, match="运行配置结构版本"):
        fixture_builder.build_database(runtime_db.parent)


def test_queries_are_bounded_and_do_not_create_missing_database(tmp_path, runtime_db):
    missing = tmp_path / "absent" / "db.sqlite"
    assert get_runtime_status(missing)["state"] == "missing"
    assert not missing.parent.exists()
    roots = query_runtime(runtime_db, {"action": "roots", "affix_ids": [917]})
    closure = query_runtime(runtime_db, {"action": "closure", "roots": roots["roots"], "max_nodes": 1})
    assert closure["truncated"] and not closure["reference_closed"]
    with pytest.raises(ValueError):
        query_runtime(runtime_db, {"action": "status", "raw": True})
    bundle = query_runtime(runtime_db, {"action": "assemble", "affix_id": 917})
    ident = bundle["abilities"][0]["definition_id"]
    fragment = query_runtime(runtime_db, {"action": "fragment", "definition_id": ident, "relative_pointer": "/abilitySpecials/Rate"})
    assert json.loads(fragment["payload_json"]) == 0
    with pytest.raises(RuntimeConfigError) as exc:
        query_runtime(runtime_db, {"action": "fragment", "definition_id": ident, "max_bytes": 1})
    assert exc.value.reason == "fragment_too_large"


@pytest.mark.parametrize("payload", [{"action": []}, {"action": "closure", "roots": [{}]},
                                      {"action": "definition", "definition_id": "a", "sections": [{}]}])
def test_malformed_container_inputs_are_validation_errors(runtime_db, payload):
    with pytest.raises(ValueError):
        query_runtime(runtime_db, payload)


@pytest.mark.parametrize("field,value", [("candidate_ids_json", "[]"), ("scope_json", '{}'),
                                        ("resolution_status", "source_empty")])
def test_source_reextraction_rejects_tampered_reference_metadata(runtime_db, field, value):
    with closing(sqlite3.connect(runtime_db)) as conn, conn:
        conn.execute(f"UPDATE runtime_reference SET {field}=? WHERE target_kind='ability'", (value,))
    with pytest.raises(RuntimeConfigError):
        assemble_runtime_source(runtime_db, 917)
    with closing(sqlite3.connect(runtime_db)) as conn, pytest.raises(RuntimeConfigError):
        validate_runtime(conn, "release", deep=True)


def test_normal_b_repairs_same_commit_corrupted_runtime_body(runtime_db):
    with closing(sqlite3.connect(runtime_db)) as conn, conn:
        conn.execute("UPDATE runtime_source SET payload_blob=? WHERE source_family='ability'", (b"bad",))
    assert fixture_builder.build_database(runtime_db.parent).updated
    assert assemble_runtime_source(runtime_db, 917)["status"] == "ready"


def test_runtime_cache_is_shared_only_inside_read_transaction(runtime_db, monkeypatch):
    from genshin_db import runtime_queries
    from genshin_db.equipment_queries import equipment_read_session
    calls = []
    original = runtime_queries.validate_runtime
    def tracked(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(runtime_queries, "validate_runtime", tracked)
    with equipment_read_session(runtime_db):
        first = assemble_runtime_source(runtime_db, 917)
        second = assemble_runtime_source(runtime_db, 917)
    assert first == second and len(calls) == 1
    with closing(sqlite3.connect(runtime_db)) as conn, conn:
        conn.execute("UPDATE runtime_source SET payload_blob=? WHERE source_family='ability'", (b"bad",))
    with pytest.raises(RuntimeConfigError):
        assemble_runtime_source(runtime_db, 917)
    assert len(calls) == 2


def test_deep_validation_checks_original_parameter_binding(runtime_db):
    with closing(sqlite3.connect(runtime_db)) as conn, conn:
        conn.execute("UPDATE runtime_parameter_binding SET raw_expression_json='0'")
    with closing(sqlite3.connect(runtime_db)) as conn, pytest.raises(RuntimeConfigError):
        validate_runtime(conn, "release", deep=True)
