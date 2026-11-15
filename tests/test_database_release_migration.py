from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

from genshin_db import release_migration as migration
from genshin_db import semantic_store
from genshin_db.annotation_store import create_annotation_tables
from genshin_db.live_mutation_policy import AcceptedDatabaseImmutableError
from genshin_db.release_integrity import build_release_core
from genshin_db.release_profile import (
    MANIFEST_CONTRACT,
    MANIFEST_SCHEMA_VERSION,
    create_release_pair_manifest,
    validate_database_release_manifest,
)
from genshin_db.semantic_store import ensure_schema as ensure_semantic_schema
from genshin_db.semantic_store import semantic_datasets
from genshin_db.upstream_version import evidence_fingerprint

_FIXTURE_APP_RELEASE_ID = "app-0.45.0"
_FIXTURE_APP_VERSION = "0.45.0"


def _fixture_pair_id(manifest: dict) -> str:
    """Return the App/pair fixture identity without polluting DB manifests."""

    return f"pair-0.45.0-7.0.0-r{manifest['release_sequence']}"


def _fixture_database(
    path: Path,
    *,
    upstream_commit: str,
    annotation_prefix: str,
    semantic_marker: str | None,
    padding_bytes: int = 0,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE fixture_release (upstream_commit TEXT NOT NULL);
            CREATE TABLE character (id INTEGER, version_line TEXT);
            CREATE TABLE skill (
                id INTEGER, character_id INTEGER, version_line TEXT,
                proud_skill_group_id INTEGER
            );
            CREATE TABLE talent (id INTEGER, character_id INTEGER, version_line TEXT);
            CREATE TABLE skill_param (proud_skill_group_id INTEGER, version_line TEXT);
            CREATE TABLE extra_info (
                target_type TEXT, target_id INTEGER, version_line TEXT,
                info_type TEXT, content TEXT, source_id INTEGER,
                proud_skill_group_id INTEGER
            );
            INSERT INTO character VALUES (1, 'release');
            INSERT INTO skill VALUES (11, 1, 'release', 101);
            INSERT INTO talent VALUES (21, 1, 'release');
            INSERT INTO skill_param VALUES (101, 'release');
            """
        )
        connection.execute("INSERT INTO fixture_release VALUES (?)", (upstream_commit,))
        if padding_bytes:
            connection.execute("CREATE TABLE legacy_padding (payload BLOB)")
            connection.execute(
                "INSERT INTO legacy_padding VALUES (zeroblob(?))", (padding_bytes,)
            )
    create_annotation_tables(path)
    with closing(sqlite3.connect(path)) as connection, connection:
        now = "2026-10-26T00:00:00+00:00"
        connection.execute(
            """INSERT INTO annotation
               (id,target_type,target_id,version_line,category,title,content,status,
                created_at,updated_at,author)
               VALUES (7,'character',1,'release','mechanic',?,?, 'approved',?,?,?)""",
            (
                f"{annotation_prefix}-title",
                f"{annotation_prefix}-valid",
                now,
                now,
                f"{annotation_prefix}-author",
            ),
        )
        connection.execute(
            """INSERT INTO annotation
               (id,target_type,target_id,version_line,category,title,content,status,
                created_at,updated_at,author)
               VALUES (8,'character',999,'release','mechanic',?,?, 'approved',?,?,?)""",
            (
                f"{annotation_prefix}-orphan-title",
                f"{annotation_prefix}-orphan",
                now,
                now,
                f"{annotation_prefix}-author",
            ),
        )
        connection.execute(
            "INSERT INTO character_note VALUES (1,'release',?,?)",
            (f"{annotation_prefix}-note", now),
        )
        if semantic_marker is not None:
            ensure_semantic_schema(connection, version_line="release")
            connection.execute(
                """UPDATE data_component SET validation_json=?
                   WHERE component='mechanic_semantics' AND version_line='release'""",
                (json.dumps({"fixture": semantic_marker}),),
            )


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _mutable_semantic_bundle():
    record = {
        "id": "release.mutable.fixture",
        "revision": 1,
        "subject": {"target_type": "character", "target_id": 1},
        "payload": {
            "id": "release.mutable.fixture",
            "revision": 1,
            "kind": "execution_requirement",
            "claim": {"requirement": "single_target"},
            "evidence_tier": "experimental_reference",
            "evidence_refs": ["fixture.source"],
        },
        "evidence": [
            {
                "id": "fixture.source",
                "kind": "primary_test",
                "locator": "fixture:mutable-release-identity",
                "sha256": "a" * 64,
            }
        ],
        "applicability": {
            "version_line": "release",
            "upstream_commit": "c" * 40,
            "normalization_fingerprint": "d" * 64,
        },
        "review": {
            "status": "approved",
            "reviewer": "release-identity-fixture",
            "reviewed_at": "2026-10-26T12:00:00+00:00",
        },
    }
    return {
        "schema_version": 1,
        "id": "release.mutable.bundle",
        "facts": [record],
        "descriptors": [],
        "heads": [
            {
                "record_kind": "fact",
                "record_id": record["id"],
                "revision": 1,
                "expected_head_revision": None,
            }
        ],
    }


def _fake_identity(path: Path, *, required_components=None) -> dict:
    path = Path(path)
    with closing(
        sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    ) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        upstream = connection.execute(
            "SELECT upstream_commit FROM fixture_release"
        ).fetchone()[0]
        semantic = semantic_datasets(connection)["mechanic_semantics"]
        annotations = connection.execute(
            "SELECT id,status,content FROM annotation ORDER BY id"
        ).fetchall()
        notes = connection.execute(
            "SELECT character_id,version_line,note FROM character_note ORDER BY 1,2"
        ).fetchall()
    sha256 = _file_hash(path)
    identity = {
        "sha256": sha256,
        "size_bytes": path.stat().st_size,
        "meta_fingerprint": sha256,
        "upstream_commit": upstream,
        "game_version": "7.0.0",
        "upstream_track": "OSRELWin",
        "upstream_updated_at": "2026-10-26T10:00:00+08:00",
        "upstream_version_evidence": {
            "resolver_version": 1,
            "version_evidence_ready": True,
            "status": "resolved",
            "version": "7.0.0",
            "track": "OSRELWin",
            "raw_version": "OSRELWin7.0.0",
            "snapshot_commit": upstream,
            "snapshot_updated_at": "2026-10-26T10:00:00+08:00",
            "evidence_kind": "snapshot_commit_subject",
            "evidence_commit": upstream,
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
            "readme_version": "OSRELWin7.0.0",
            "issues": [],
        },
        "version_lines": ["release"],
        "indexer_version": "7",
        "normalization_version": "2",
        "manual_content_fingerprint": hashlib.sha256(
            repr((annotations, notes)).encode()
        ).hexdigest(),
        "datasets": {
            "equipment": {
                "schema_version": 1,
                "lines": {"release": {"state": "ready", "schema_version": 1}},
            },
            "runtime_config": {
                "schema_version": 1,
                "lines": {"release": {"state": "ready", "schema_version": 1}},
            },
            "mechanic_semantics": semantic,
        },
        "release_core": build_release_core(path),
    }
    evidence = identity["upstream_version_evidence"]
    evidence["evidence_fingerprint"] = evidence_fingerprint(evidence)
    return identity


def test_required_component_gate_is_manifest_scoped():
    identity = {
        "datasets": {
            "equipment": {"lines": {"release": {"state": "ready"}}},
            "runtime_config": {"lines": {"release": {"state": "missing"}}},
            "mechanic_semantics": {"lines": {"release": {"state": "missing"}}},
        }
    }

    migration._required_components_ready(identity, ("equipment",))
    with pytest.raises(migration.DatabaseMigrationError) as failure:
        migration._required_components_ready(
            identity, ("equipment", "runtime_config")
        )
    assert failure.value.reason == "database_migration_components_not_ready"


def _manifest(
    package: Path,
    *,
    logic_profile: str = "logic-v1",
    release_id: str = "db-7.0.0-r1",
    release_sequence: int = 1,
) -> dict:
    identity = _fake_identity(package)
    return validate_database_release_manifest(
        {
            "contract": MANIFEST_CONTRACT,
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "database_release_id": release_id,
            "release_sequence": release_sequence,
            "channel": "stable",
            "published_at": "2026-10-26T10:00:00+08:00",
            "released_at": "2026-10-26T10:00:00+08:00",
            "logic_profile": logic_profile,
            "data_contract_version": 1,
            "database": {
                key: copy.deepcopy(identity[key])
                for key in (
                    "sha256",
                    "size_bytes",
                    "meta_fingerprint",
                    "upstream_commit",
                    "game_version",
                    "upstream_track",
                    "upstream_updated_at",
                    "upstream_version_evidence",
                    "version_lines",
                    "indexer_version",
                    "normalization_version",
                    "datasets",
                    "release_core",
                )
            },
            "download": None,
        }
    )


@pytest.fixture
def coordinator(monkeypatch):
    monkeypatch.setattr(migration, "_validate_database", _fake_identity)
    monkeypatch.setattr(
        migration.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=100 * 1024**3),
    )

    def run(data_dir: Path, package: Path, manifest: dict, **overrides):
        pair_id = _fixture_pair_id(manifest)
        pair = create_release_pair_manifest(
            manifest,
            release_pair_id=pair_id,
            pair_sequence=manifest["release_sequence"],
            pair_policy="exact",
            app_release_id=_FIXTURE_APP_RELEASE_ID,
            app_version=_FIXTURE_APP_VERSION,
            app_artifact_manifest_sha256="f" * 64,
        )
        arguments = {
            "app_release_id": _FIXTURE_APP_RELEASE_ID,
            "release_pair_id": pair_id,
            "required_database_release_id": manifest["database_release_id"],
            "logic_profile": "logic-v1",
            "data_contract_version": 1,
            "user_confirmed": True,
            "production": True,
            "app_version": _FIXTURE_APP_VERSION,
            "app_artifact_manifest_sha256": "f" * 64,
            "pair_manifest": pair,
        }
        arguments.update(overrides)
        return migration.install_database_release(
            data_dir, package, manifest, **arguments
        )

    return run


def _make_pair(tmp_path: Path, *, live_semantic: str | None = "live"):
    data_dir = tmp_path / "data"
    live = data_dir / "genshin.db"
    package = tmp_path / "package.db"
    _fixture_database(
        live,
        upstream_commit="b" * 40,
        annotation_prefix="user",
        semantic_marker=live_semantic,
    )
    _fixture_database(
        package,
        upstream_commit="c" * 40,
        annotation_prefix="bundle",
        semantic_marker="bundle",
    )
    return data_dir, live, package, _manifest(package)


def test_explicit_confirmation_is_required_before_any_data_directory_write(tmp_path):
    package = tmp_path / "package.db"
    _fixture_database(
        package,
        upstream_commit="c" * 40,
        annotation_prefix="bundle",
        semantic_marker="bundle",
    )
    data_dir = tmp_path / "not-created"
    with pytest.raises(migration.DatabaseMigrationError) as failure:
        migration.install_database_release(
            data_dir,
            package,
            _manifest(package),
            app_version="0.45.0",
            logic_profile="logic-v1",
            data_contract_version=1,
            user_confirmed=False,
            production=True,
        )
    assert failure.value.reason == "database_migration_confirmation_required"
    assert not data_dir.exists()


def test_production_install_cannot_downgrade_to_current_only_or_omit_pair(
    coordinator, tmp_path
):
    data_dir, live, package, manifest = _make_pair(tmp_path)
    before = _file_hash(live)
    with pytest.raises(migration.DatabaseMigrationError) as downgraded:
        coordinator(data_dir, package, manifest, pair_policy="current_only")
    assert downgraded.value.reason == "database_migration_production_pair_required"

    with pytest.raises(migration.DatabaseMigrationError) as missing:
        migration.install_database_release(
            data_dir,
            package,
            manifest,
            logic_profile="logic-v1",
            data_contract_version=1,
            user_confirmed=True,
            production=True,
            app_release_id=_FIXTURE_APP_RELEASE_ID,
            app_version=_FIXTURE_APP_VERSION,
            release_pair_id=_fixture_pair_id(manifest),
            required_database_release_id=manifest["database_release_id"],
            pair_policy="exact",
        )
    assert missing.value.reason == "database_migration_production_pair_required"
    assert _file_hash(live) == before


def test_production_install_publishes_candidate_project_content_without_live_merge(
    coordinator, tmp_path
):
    data_dir, live, package, manifest = _make_pair(tmp_path)
    result = coordinator(data_dir, package, manifest)
    assert result["status"] == "installed"
    assert result["rollback_retained"] is False
    with closing(sqlite3.connect(live)) as connection:
        assert connection.execute("SELECT upstream_commit FROM fixture_release").fetchone()[0] == "c" * 40
        assert connection.execute(
            "SELECT id,status,content FROM annotation ORDER BY id"
        ).fetchall() == [
            (7, "approved", "bundle-valid"),
            (8, "approved", "bundle-orphan"),
        ]
        assert connection.execute("SELECT note FROM character_note").fetchone()[0] == "bundle-note"
        assert json.loads(
            connection.execute(
                """SELECT validation_json FROM data_component
                   WHERE component='mechanic_semantics' AND version_line='release'"""
            ).fetchone()[0]
        ) == {"fixture": "bundle"}
    assert list(data_dir.rglob("*.db")) == [live]
    receipt = json.loads(
        (data_dir / migration.LIVE_RECEIPT_FILENAME).read_text(encoding="utf-8")
    )
    accepted_manifest = json.loads(
        (data_dir / migration.LIVE_MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert accepted_manifest == manifest
    assert not (data_dir / "release-pair-live-manifest.json").exists()
    assert result["scope"] == "database_subtransaction"
    assert result["pair_operational"] is False
    assert receipt["database_release_id"] == manifest["database_release_id"]
    assert receipt["receipt_fingerprint"] == result["live_receipt"]["receipt_fingerprint"]
    journal = json.loads((data_dir / migration.JOURNAL_FILE).read_text(encoding="utf-8"))
    assert journal["state"] == "cleanup_complete"
    assert journal["rollback_name"] is None


def test_repeat_install_is_idempotent_without_new_backup(coordinator, tmp_path):
    data_dir, live, package, manifest = _make_pair(tmp_path)
    coordinator(data_dir, package, manifest)
    before = sorted(path.name for path in data_dir.rglob("*.db"))
    result = coordinator(data_dir, package, manifest)
    assert result["status"] == "already_current"
    assert result["recovered_cleanup"] is False
    assert sorted(path.name for path in data_dir.rglob("*.db")) == before
    with closing(sqlite3.connect(live)) as connection:
        assert connection.execute("SELECT content FROM annotation WHERE id=7").fetchone()[0] == "bundle-valid"


def test_post_install_project_content_drift_blocks_repeat_acceptance(
    coordinator, tmp_path
):
    data_dir, live, package, manifest = _make_pair(tmp_path)
    coordinator(data_dir, package, manifest)
    with closing(sqlite3.connect(live)) as connection, connection:
        connection.execute(
            "UPDATE annotation SET content='out-of-protocol edit' WHERE id=7"
        )
    with pytest.raises(migration.DatabaseMigrationError) as failure:
        coordinator(data_dir, package, manifest)
    assert failure.value.reason == "database_migration_recovery_required"


def test_low_level_semantic_writer_rejects_accepted_live_database(
    coordinator, tmp_path
):
    data_dir, live, package, manifest = _make_pair(tmp_path)
    coordinator(data_dir, package, manifest)
    with pytest.raises(AcceptedDatabaseImmutableError):
        semantic_store.import_bundle(live, _mutable_semantic_bundle(), dry_run=False)


def test_package_identity_mismatch_never_touches_live(coordinator, tmp_path):
    data_dir, live, package, manifest = _make_pair(tmp_path)
    before = _file_hash(live)
    with closing(sqlite3.connect(package)) as connection, connection:
        connection.execute("CREATE TABLE tampered (value TEXT)")
    with pytest.raises(migration.DatabaseMigrationError) as failure:
        coordinator(data_dir, package, manifest)
    assert failure.value.reason == "database_migration_package_identity_mismatch"
    assert _file_hash(live) == before


def test_incompatible_logic_contract_is_rejected_before_migration(coordinator, tmp_path):
    data_dir, live, package, manifest = _make_pair(tmp_path)
    before = _file_hash(live)
    with pytest.raises(migration.DatabaseMigrationError) as failure:
        coordinator(data_dir, package, manifest, logic_profile="logic-v2")
    assert failure.value.reason == "database_migration_release_pair_incompatible"
    assert _file_hash(live) == before


def test_post_replace_fault_restores_old_live_before_returning(coordinator, tmp_path):
    data_dir, live, package, manifest = _make_pair(tmp_path)

    def fail_after_replace(phase: str):
        if phase == "after_replace":
            raise RuntimeError("injected post-replace failure")

    with pytest.raises(RuntimeError, match="post-replace"):
        coordinator(data_dir, package, manifest, fault_hook=fail_after_replace)
    with closing(sqlite3.connect(live)) as connection:
        assert connection.execute("SELECT upstream_commit FROM fixture_release").fetchone()[0] == "b" * 40
        assert connection.execute("SELECT content FROM annotation ORDER BY id").fetchall() == [
            ("user-valid",),
            ("user-orphan",),
        ]
    journal = json.loads((data_dir / migration.JOURNAL_FILE).read_text(encoding="utf-8"))
    assert journal["state"] == "rolled_back"
    assert list(data_dir.rglob("*.db")) == [live]
    assert not (data_dir / migration.LIVE_RECEIPT_FILENAME).exists()


def test_first_install_postcheck_failure_removes_unaccepted_live(coordinator, tmp_path):
    data_dir = tmp_path / "data"
    package = tmp_path / "package.db"
    _fixture_database(
        package,
        upstream_commit="c" * 40,
        annotation_prefix="bundle",
        semantic_marker="bundle",
    )

    def fail_after_replace(phase: str):
        if phase == "after_replace":
            raise RuntimeError("injected first-install failure")

    with pytest.raises(RuntimeError, match="first-install"):
        coordinator(
            data_dir,
            package,
            _manifest(package),
            fault_hook=fail_after_replace,
        )
    assert not (data_dir / "genshin.db").exists()
    assert not (data_dir / migration.LIVE_RECEIPT_FILENAME).exists()
    journal = json.loads((data_dir / migration.JOURNAL_FILE).read_text(encoding="utf-8"))
    assert journal["state"] == "rolled_back"


def test_fault_after_fence_reopens_keeps_new_live_and_retry_finishes_cleanup(
    coordinator, tmp_path
):
    data_dir, live, package, manifest = _make_pair(tmp_path)

    def fail_after_commit(phase: str):
        if phase == "after_commit":
            raise RuntimeError("injected cleanup-window failure")

    with pytest.raises(RuntimeError, match="cleanup-window"):
        coordinator(data_dir, package, manifest, fault_hook=fail_after_commit)
    with closing(sqlite3.connect(live)) as connection:
        assert connection.execute("SELECT upstream_commit FROM fixture_release").fetchone()[0] == "c" * 40
    journal = json.loads((data_dir / migration.JOURNAL_FILE).read_text(encoding="utf-8"))
    assert journal["state"] == "postchecked"
    assert len(list((data_dir / "build").glob("database-rollback-*.db"))) == 1

    recovered = coordinator(data_dir, package, manifest)
    assert recovered["status"] == "already_current"
    assert recovered["recovered_cleanup"] is True
    assert list(data_dir.rglob("*.db")) == [live]
    journal = json.loads((data_dir / migration.JOURNAL_FILE).read_text(encoding="utf-8"))
    assert journal["state"] == "cleanup_complete"


def test_failure_after_receipt_write_restores_previous_database_and_receipt(
    coordinator, tmp_path
):
    data_dir, live, package, manifest = _make_pair(tmp_path)
    coordinator(data_dir, package, manifest)
    receipt_path = data_dir / migration.LIVE_RECEIPT_FILENAME
    manifest_path = data_dir / migration.LIVE_MANIFEST_FILENAME
    previous_receipt = receipt_path.read_bytes()
    previous_manifest = manifest_path.read_bytes()

    next_package = tmp_path / "package-next.db"
    _fixture_database(
        next_package,
        upstream_commit="d" * 40,
        annotation_prefix="bundle-next",
        semantic_marker="bundle-next",
    )
    next_manifest = _manifest(
        next_package,
        release_id="db-7.0.0-r2",
        release_sequence=2,
    )

    def fail_after_receipt(phase: str):
        if phase == "after_receipt_write":
            raise RuntimeError("injected receipt failure")

    with pytest.raises(RuntimeError, match="receipt failure"):
        coordinator(
            data_dir,
            next_package,
            next_manifest,
            fault_hook=fail_after_receipt,
        )
    with closing(sqlite3.connect(live)) as connection:
        assert connection.execute("SELECT upstream_commit FROM fixture_release").fetchone()[0] == "c" * 40
    assert receipt_path.read_bytes() == previous_receipt
    assert manifest_path.read_bytes() == previous_manifest
    journal = json.loads((data_dir / migration.JOURNAL_FILE).read_text(encoding="utf-8"))
    assert journal["state"] == "rolled_back"


def test_first_install_failure_after_manifest_write_removes_all_identity_sidecars(
    coordinator, tmp_path
):
    data_dir = tmp_path / "data"
    package = tmp_path / "package.db"
    _fixture_database(
        package,
        upstream_commit="c" * 40,
        annotation_prefix="bundle",
        semantic_marker="bundle",
    )

    def fail_after_manifest(phase: str):
        if phase == "after_manifest_write":
            raise RuntimeError("injected manifest failure")

    with pytest.raises(RuntimeError, match="manifest failure"):
        coordinator(
            data_dir,
            package,
            _manifest(package),
            fault_hook=fail_after_manifest,
        )
    assert not (data_dir / "genshin.db").exists()
    assert not (data_dir / migration.LIVE_MANIFEST_FILENAME).exists()
    assert not (data_dir / migration.LIVE_RECEIPT_FILENAME).exists()


def test_repeat_install_detects_immutable_row_drift_from_actual_database(
    coordinator, tmp_path
):
    data_dir, live, package, manifest = _make_pair(tmp_path)
    coordinator(data_dir, package, manifest)
    with closing(sqlite3.connect(live)) as connection, connection:
        connection.execute("UPDATE fixture_release SET upstream_commit=?", ("d" * 40,))
    with pytest.raises(migration.DatabaseMigrationError) as failure:
        coordinator(data_dir, package, manifest)
    assert failure.value.reason == "database_migration_active_release_core_mismatch"


@pytest.mark.parametrize(
    ("phase", "reason"),
    [
        ("after_candidate_validation", "database_migration_release_core_changed"),
        ("after_replace", "database_migration_active_postcheck_failed"),
    ],
)
def test_migration_rejects_different_bytes_with_same_logical_identity(
    coordinator, tmp_path, phase, reason,
):
    data_dir, live, package, manifest = _make_pair(tmp_path)

    def modify_sqlite_header(point):
        if point != phase:
            return
        journal = json.loads((data_dir / migration.JOURNAL_FILE).read_text(encoding="utf-8"))
        path = live if point == "after_replace" else data_dir / "build" / journal["staging_name"]
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA user_version=1")

    with pytest.raises(migration.DatabaseMigrationError) as failure:
        coordinator(data_dir, package, manifest, fault_hook=modify_sqlite_header)
    assert failure.value.reason == reason
    with closing(sqlite3.connect(live)) as connection:
        assert connection.execute("SELECT upstream_commit FROM fixture_release").fetchone()[0] == "b" * 40
    assert not (data_dir / migration.LIVE_RECEIPT_FILENAME).exists()


def test_migration_rejects_sqlite_sidecar_even_with_expected_main_file(
    coordinator, tmp_path,
):
    data_dir, _live, package, manifest = _make_pair(tmp_path)
    package.with_name(package.name + "-wal").write_bytes(b"unverified")
    with pytest.raises(migration.DatabaseMigrationError) as failure:
        coordinator(data_dir, package, manifest)
    assert failure.value.reason == "database_migration_package_identity_mismatch"
    assert not (data_dir / migration.JOURNAL_FILE).exists()


def test_migration_checks_deep_package_content_once(coordinator, tmp_path, monkeypatch):
    data_dir, _live, package, manifest = _make_pair(tmp_path)
    validated = []
    original = migration._validate_database

    def track(path, **kwargs):
        validated.append(Path(path))
        return original(path, **kwargs)

    monkeypatch.setattr(migration, "_validate_database", track)
    result = coordinator(data_dir, package, manifest)
    assert result["status"] == "installed"
    assert validated == [package.resolve()]


def test_tampered_accepted_manifest_fails_closed(coordinator, tmp_path):
    data_dir, _live, package, manifest = _make_pair(tmp_path)
    coordinator(data_dir, package, manifest)
    manifest_path = data_dir / migration.LIVE_MANIFEST_FILENAME
    tampered = json.loads(manifest_path.read_text(encoding="utf-8"))
    tampered["released_at"] = "2026-10-26T11:00:00+08:00"
    manifest_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(migration.DatabaseMigrationError) as failure:
        coordinator(data_dir, package, manifest)
    assert failure.value.reason == "database_migration_recovery_required"


def test_development_mode_retains_independent_diagnostic_backup(coordinator, tmp_path):
    data_dir, live, package, manifest = _make_pair(tmp_path)
    result = coordinator(data_dir, package, manifest, production=False)
    backups = list((data_dir / "backups").glob("database-rollback-*.db"))
    assert result["rollback_retained"] is True
    assert len(backups) == 1
    with closing(sqlite3.connect(backups[0])) as connection:
        assert connection.execute("SELECT upstream_commit FROM fixture_release").fetchone()[0] == "b" * 40
    assert live.exists()


def test_space_preflight_leaves_live_and_journal_untouched(
    coordinator, monkeypatch, tmp_path
):
    data_dir, live, package, manifest = _make_pair(tmp_path)
    before = _file_hash(live)
    monkeypatch.setattr(
        migration.shutil, "disk_usage", lambda _path: SimpleNamespace(free=0)
    )
    with pytest.raises(migration.DatabaseMigrationError) as failure:
        coordinator(data_dir, package, manifest)
    assert failure.value.reason == "database_migration_space_insufficient"
    assert _file_hash(live) == before
    assert not (data_dir / migration.JOURNAL_FILE).exists()


def test_exact_interrupted_swap_resumes_publication(coordinator, tmp_path):
    data_dir, live, package, manifest = _make_pair(tmp_path)
    old_sha = _file_hash(live)
    rollback = data_dir / "backups" / "database-rollback-dbm_interrupted.db"
    rollback.parent.mkdir(parents=True)
    rollback.write_bytes(live.read_bytes())
    live.write_bytes(package.read_bytes())
    migration._journal(
        data_dir / migration.JOURNAL_FILE,
        operation_id="dbm_interrupted",
        target_release_id=manifest["database_release_id"],
        state="swap_started",
        production=False,
        old_sha256=old_sha,
        target_package_sha256=manifest["database"]["sha256"],
        target_manifest_fingerprint=migration._fingerprint(manifest),
        staging_name="database-release-dbm_interrupted.db",
        rollback_name=rollback.name,
    )

    result = coordinator(
        data_dir,
        package,
        manifest,
        production=False,
    )

    assert result["status"] == "already_current"
    assert result["recovered_interrupted_swap"] is True
    assert result["rollback_retained"] is True
    assert rollback.exists()
    assert (data_dir / migration.LIVE_MANIFEST_FILENAME).exists()
    assert (data_dir / migration.LIVE_RECEIPT_FILENAME).exists()
    journal = json.loads(
        (data_dir / migration.JOURNAL_FILE).read_text(encoding="utf-8")
    )
    assert journal["state"] == "cleanup_complete"


def test_ambiguous_prior_journal_fails_closed(coordinator, tmp_path):
    data_dir, _live, package, manifest = _make_pair(tmp_path)
    migration._journal(
        data_dir / migration.JOURNAL_FILE,
        operation_id="dbm_interrupted",
        target_release_id=manifest["database_release_id"],
        state="swap_started",
        production=True,
        old_sha256="a" * 64,
        target_package_sha256=manifest["database"]["sha256"],
    )
    with pytest.raises(migration.DatabaseMigrationError) as failure:
        coordinator(data_dir, package, manifest)
    assert failure.value.reason == "database_migration_recovery_required"


@pytest.mark.parametrize("lock_kind", ["update", "writer"])
def test_update_and_writer_lock_contention_fail_without_replacing_live(
    coordinator, tmp_path, lock_kind
):
    data_dir, live, package, manifest = _make_pair(tmp_path)
    before = _file_hash(live)
    ready = threading.Event()
    release = threading.Event()

    def hold_lock():
        lock = (
            migration.file_lock(data_dir / migration.UPDATE_LOCK_FILE, timeout=0)
            if lock_kind == "update"
            else migration.database_lock(live, timeout=0)
        )
        with lock:
            ready.set()
            assert release.wait(10)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert ready.wait(10)
    try:
        with pytest.raises(migration.DatabaseMigrationError) as failure:
            coordinator(data_dir, package, manifest)
        assert failure.value.reason == "database_migration_busy"
        assert _file_hash(live) == before
    finally:
        release.set()
        holder.join(10)
    assert not holder.is_alive()


def test_legacy_55_mib_database_without_semantics_can_upgrade(coordinator, tmp_path):
    data_dir = tmp_path / "data"
    live = data_dir / "genshin.db"
    package = tmp_path / "package.db"
    _fixture_database(
        live,
        upstream_commit="b" * 40,
        annotation_prefix="legacy",
        semantic_marker=None,
        padding_bytes=55 * 1024**2,
    )
    assert live.stat().st_size > 55 * 1024**2
    _fixture_database(
        package,
        upstream_commit="c" * 40,
        annotation_prefix="bundle",
        semantic_marker="bundle",
    )
    result = coordinator(data_dir, package, _manifest(package))
    assert result["status"] == "installed"
    with closing(sqlite3.connect(live)) as connection:
        assert connection.execute("SELECT content FROM annotation WHERE id=7").fetchone()[0] == "bundle-valid"
        assert connection.execute(
            """SELECT validation_json FROM data_component
               WHERE component='mechanic_semantics' AND version_line='release'"""
        ).fetchone() is not None
