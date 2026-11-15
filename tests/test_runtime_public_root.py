"""Raw public-source import on small fixtures; never reads the formal DB."""
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from genshin_db import runtime_catalog as catalog
from genshin_db import runtime_store as store
from genshin_db.equipment_queries import equipment_read_session
from genshin_db.runtime_public_root import get_public_root_catalog
from genshin_db.runtime_queries import query_runtime, runtime_capabilities
from tests.test_pipeline import fake_repo  # noqa: F401
from tests.test_runtime_object_index import build

# ruff: noqa: F811
HERE = Path(__file__).resolve().parents[1]
SOURCE = catalog.GLOBAL_COMBAT_PATH


@pytest.fixture
def public_db(tmp_path, fake_repo):
    path = tmp_path / "small-public.db"
    build(path, fake_repo)
    return path


def test_exact_required_source_and_raw_root_no_semantic_edges(public_db, fake_repo):
    raw_bytes = (fake_repo / SOURCE).read_bytes()
    result = query_runtime(public_db, {"action": "public_root_catalog"})
    assert result["root"]["raw"] == json.loads(raw_bytes)
    assert result["root"]["source_sha256"] == hashlib.sha256(raw_bytes).hexdigest()
    assert result["status"] == "ready" and result["execution_authority"] is False
    assert result["mounted_ability_roots"] is None and result["mount_status"] == "not_evaluated"
    with closing(sqlite3.connect(public_db)) as conn:
        ident = result["root"]["definition"]["definition_id"]
        for table, key in (("runtime_reference", "from_definition_id"), ("runtime_object_reference", "from_definition_id"),
                           ("runtime_material_reference", "from_definition_id"), ("runtime_parameter_binding", "origin_definition_id")):
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {key}=?", (ident,)).fetchone()[0] == 0
        assert store.validate_runtime(conn, "release", deep=True)["indexer_version"] == "7"
    manifest = catalog.enumerate_runtime_sources(fake_repo)
    assert len(manifest["files"]) == 34 and manifest["files"][SOURCE]["family"] == "global_combat"


@pytest.mark.parametrize("name,expected_sha", [
    ("ConfigGlobalCombat.git.json", "058fe28326af26f5aa1480f9a06896d25890cb368eaca6da684b8bdd395e83cd"),
    ("ConfigGlobalCombat.worktree.json", "438cf8c67b27207cea249536eeefe14861e31a6ee03bab9a5572a74224559679"),
])
def test_real_source_bytes_imported_exactly_in_synthetic_fixture(fake_repo, tmp_path, name, expected_sha):
    # Only this exact source is real. Other sources/commit are synthetic; this
    # does not represent a new complete production snapshot or reviewed mount.
    payload = (HERE / "tests/fixtures/runtime_public_root" / name).read_bytes()
    assert hashlib.sha256(payload).hexdigest() == expected_sha
    (fake_repo / SOURCE).write_bytes(payload)
    path = tmp_path / "real-source-small-fixture.db"
    build(path, fake_repo)
    result = get_public_root_catalog(path)
    assert result["root"]["source_sha256"] == expected_sha
    assert result["root"]["raw"] == json.loads(payload)
    assert result["execution_authority"] is False and result["mounted_ability_roots"] is None


@pytest.mark.parametrize("raw", [[], None, 1, "text"])
def test_global_root_requires_exact_object_and_source(raw):
    with pytest.raises(catalog.RuntimeConfigError):
        catalog.extract_definitions(SOURCE, "global_combat", raw, "s")
    with pytest.raises(catalog.RuntimeConfigError):
        catalog.extract_definitions("BinOutput/Common/Other.json", "global_combat", {}, "s")
    with pytest.raises(catalog.RuntimeConfigError):
        catalog.extract_definitions(SOURCE, "global_combat", {}, "s", indexer_version="6")


def test_missing_optional_source_builds_degraded_catalog_without_public_root(fake_repo, tmp_path):
    (fake_repo / SOURCE).unlink()
    path = tmp_path / "absent.db"
    report = build(path, fake_repo)
    assert path.is_file()
    assert report["source_capability_state"] == "degraded"
    assert report["source_capabilities"]["global_combat"] == {
        "requirement": "optional",
        "state": "unavailable",
    }
    result = get_public_root_catalog(path)
    assert result["status"] == "missing"
    assert result["reason"] == "public_source_unavailable"
    assert result["root"] is None and result["execution_authority"] is False


def test_manifest_changes_are_not_called_same_snapshot(fake_repo, tmp_path):
    path = tmp_path / "line-endings.db"
    target = fake_repo / SOURCE
    value = json.dumps(json.loads(target.read_bytes()), indent=2).encode()
    target.write_bytes(value)
    first = build(path, fake_repo)
    target.write_bytes(value.replace(b"\n", b"\r\n"))
    second = build(path, fake_repo)
    assert first["snapshot_id"] != second["snapshot_id"]
    assert first["manifest_fingerprint"] != second["manifest_fingerprint"]


def test_source_change_during_build_rolls_back_existing_small_database(public_db, fake_repo, monkeypatch):
    before = public_db.read_bytes()
    parse = store.parse_source
    changed = False

    def alter(payload):
        nonlocal changed
        value = parse(payload)
        if type(value) is dict and "looks_executable" in value and not changed:
            changed = True
            target = fake_repo / SOURCE
            target.write_bytes(target.read_bytes() + b" ")
        return value

    monkeypatch.setattr(store, "parse_source", alter)
    with pytest.raises(catalog.RuntimeConfigError) as error:
        build(public_db, fake_repo)
    assert changed and error.value.reason == "source_changed"
    assert public_db.read_bytes() == before


@pytest.mark.parametrize("limit", [True, 1.0, 0, 2097153, None])
def test_invalid_budget_types_rejected(public_db, limit):
    with pytest.raises(ValueError):
        get_public_root_catalog(public_db, max_bytes=limit)


def test_source_budget_rejects_before_decompression(public_db, monkeypatch):
    from genshin_db.runtime_queries import _Nodes

    def forbidden(*args):
        raise AssertionError("source was read before size preflight")

    monkeypatch.setattr(_Nodes, "node", forbidden)
    with pytest.raises(catalog.RuntimeConfigError) as error:
        get_public_root_catalog(public_db, max_bytes=1)
    assert error.value.reason == "response_too_large"


def test_final_response_budget_includes_metadata(public_db, fake_repo):
    budget = len((fake_repo / SOURCE).read_bytes()) + 1
    with pytest.raises(catalog.RuntimeConfigError) as error:
        get_public_root_catalog(public_db, max_bytes=budget)
    assert error.value.reason == "response_too_large"


def test_old_index6_stays_readable_without_new_source_or_migration(fake_repo, tmp_path, monkeypatch):
    path = tmp_path / "old6.db"
    with monkeypatch.context() as patch:
        patch.setattr(catalog, "INDEXER_VERSION", "6")
        patch.setattr(store, "INDEXER_VERSION", "6")
        old = build(path, fake_repo)
    before = path.read_bytes()
    result = get_public_root_catalog(path)
    assert result["status"] == "missing" and result["reason"] == "public_source_not_indexed"
    assert result["snapshot"]["snapshot_id"] == old["snapshot_id"]
    with closing(sqlite3.connect(path)) as conn:
        assert store.validate_runtime(conn, "release", deep=True)["indexer_version"] == "6"
    assert path.read_bytes() == before
    with pytest.raises(catalog.RuntimeConfigError) as error:
        get_public_root_catalog(path, max_bytes=1)
    assert error.value.reason == "response_too_large"


def test_old_snapshot_request_and_unknown_fields_fail_closed(public_db):
    with pytest.raises(catalog.RuntimeConfigError) as error:
        query_runtime(public_db, {"action": "public_root_catalog", "snapshot_id": "0" * 64})
    assert error.value.reason == "snapshot_changed"
    with pytest.raises(ValueError):
        query_runtime(public_db, {"action": "public_root_catalog", "approve": True})
    assert runtime_capabilities()["public_root_catalog"]["execution_authority"] is False


def test_raw_return_is_isolated_inside_same_read_session(public_db):
    with equipment_read_session(public_db):
        first = get_public_root_catalog(public_db)
        first["root"]["raw"]["opaque"] = ["changed"]
        second = get_public_root_catalog(public_db)
        assert second["root"]["raw"]["opaque"] == [False, 0, None, "Fixture_Ability"]


@pytest.mark.parametrize("mutation", ["source_blob", "root_hash", "root_identity"])
def test_tampered_source_or_root_identity_is_not_raw_ready(public_db, mutation):
    with closing(sqlite3.connect(public_db)) as conn, conn:
        if mutation == "source_blob":
            conn.execute("UPDATE runtime_source SET payload_blob=? WHERE source_path=?", (b"invalid-zlib", SOURCE))
        else:
            field = "node_sha256" if mutation == "root_hash" else "definition_id"
            conn.execute(f"UPDATE runtime_definition SET {field}=? WHERE definition_kind='global_combat_config'", ("0" * 64,))
    with pytest.raises(catalog.RuntimeConfigError):
        get_public_root_catalog(public_db)


def test_forged_public_reference_cannot_become_mounted_edge(public_db):
    with closing(sqlite3.connect(public_db)) as conn, conn:
        conn.row_factory = sqlite3.Row
        ident = conn.execute("SELECT definition_id FROM runtime_definition WHERE definition_kind='global_combat_config'").fetchone()[0]
        reference = dict(conn.execute("SELECT * FROM runtime_reference LIMIT 1").fetchone())
        reference.update(reference_id="f" * 64, from_definition_id=ident, source_path=SOURCE)
        conn.execute("INSERT INTO runtime_reference VALUES (" + ",".join("?" for _ in reference) + ")", tuple(reference.values()))
        report = json.loads(conn.execute("SELECT validation_json FROM data_component WHERE component='runtime_config' AND version_line='release'").fetchone()[0])
        report["table_counts"]["runtime_reference"] += 1
        conn.execute("UPDATE data_component SET validation_json=? WHERE component='runtime_config' AND version_line='release'", (json.dumps(report),))
    with pytest.raises(catalog.RuntimeConfigError):
        get_public_root_catalog(public_db)
