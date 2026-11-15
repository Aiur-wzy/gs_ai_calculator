"""Entity publication evidence stays descriptive and within the reviewed candidate flow."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import zlib

import pytest

from genshin_db.content_change_set import (
    ContentChangeSetError,
    apply_content_change_set,
    create_approval,
    create_change_set,
    project_content_fingerprint,
)
from genshin_db.publication_evidence import (
    ENTITY_TYPES,
    owner_publication,
    publication_by_id,
    publication_fingerprint,
    validate_publication_inventory,
)
from genshin_db.release_hygiene import validate_database_release_hygiene


@pytest.fixture
def databases(tmp_path):
    base, candidate = tmp_path / "base.db", tmp_path / "candidate.db"
    with sqlite3.connect(base) as connection:
        for index, kind in enumerate(ENTITY_TYPES, 1):
            connection.execute(f"CREATE TABLE {kind}(id INTEGER PRIMARY KEY, version_line TEXT)")
            connection.execute(f"INSERT INTO {kind} VALUES (?, 'release')", (index,))
    shutil.copyfile(base, candidate)
    return base, candidate


def _operation(kind, ident, status="released", source_kind="official_reference"):
    key = {"entity_type": kind, "entity_id": ident, "version_line": "release"}
    return {"operation_id": kind, "source_id": "audit", "table": "entity_publication", "action": "add",
            "key": key, "before_sha256": None, "row": {**key, "entity_status": status,
                "checked_on": "2026-11-14", "scope": "fixture", "reason": "fixture",
                "evidence_json": json.dumps([{"source_kind": source_kind,
                    "origin": "https://example.invalid/fixture", "assessment": "fixture"}])}}


def _change_set(base, operations):
    return create_change_set(base_database_release_id="base", target_database_release_id="candidate",
        base_project_content_fingerprint=project_content_fingerprint(base), operations=operations,
        sources=[{"source_id": "audit", "kind": "entity_publication_evidence", "locator": "fixture",
                  "evidence_sha256": "a" * 64, "summary": "fixture"}],
        impact={"components": ["entity_publication"], "summary": "fixture"})


def _apply(databases, operations):
    base, candidate = databases
    change_set = _change_set(base, operations)
    approval = create_approval(change_set, approved_by="test-only", approved_at="2026-11-14T00:00:00Z")
    return apply_content_change_set(base_database=base, candidate_database=candidate, change_set=change_set,
        approval=approval, base_database_release_id="base", target_database_release_id="candidate")


def test_approved_audit_covers_only_entities_and_resolves_selected_owners(databases):
    base, candidate = databases
    before = base.read_bytes()
    receipt = _apply(databases, [_operation("character", 1), _operation("weapon", 2, "unreleased"),
                                 _operation("artifact_set", 3, "unknown", "upstream_raw")])
    assert base.read_bytes() == before
    assert receipt["operation_counts"] == {"add": 3, "update": 0, "delete": 0}
    with sqlite3.connect(candidate) as connection:
        connection.row_factory = sqlite3.Row
        assert validate_publication_inventory(connection) == {"released": 1, "unreleased": 1, "unknown": 1}
        rows = owner_publication(connection, {"character_ids": [1], "weapon_ids": [2, 2]}, "release")
        assert [(row["entity_status"], row["requires_usage_confirmation"]) for row in rows] == [
            ("released", False), ("unreleased", True)]
        assert all("data_revision_status" not in row for row in rows)
        assert publication_by_id(connection, "character", [1], "beta")[1]["evidence_state"] == "no_evidence"
        assert publication_by_id(connection, "artifact_set", [3], "release")[3]["evidence_state"] == "audited"
        before = publication_fingerprint(connection)
        connection.execute("UPDATE entity_publication SET reason='new evidence' WHERE entity_type='character'")
        assert publication_fingerprint(connection) != before


def test_incomplete_inventory_rolls_back_ddl_and_rows(databases):
    _, candidate = databases
    with pytest.raises(ContentChangeSetError, match="publication_inventory_invalid"):
        _apply(databases, [_operation("character", 1)])
    with sqlite3.connect(candidate) as connection:
        assert publication_by_id(connection, "character", [1], "release")[1]["evidence_state"] == "no_evidence"
        assert not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='entity_publication'").fetchone()


@pytest.mark.parametrize("kind,source_kind", [("skill", "official_reference"), ("character", "upstream_raw")])
def test_child_status_and_raw_only_positive_claims_are_rejected(databases, kind, source_kind):
    with pytest.raises(ContentChangeSetError, match="publication_evidence_invalid"):
        _change_set(databases[0], [_operation(kind, 1, source_kind=source_kind)])


def test_hygiene_decodes_and_checks_compressed_source(tmp_path):
    db = tmp_path / "test.db"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE runtime_source(source_size_bytes INTEGER,payload_codec TEXT,payload_blob BLOB,source_sha256 TEXT)")
        raw = b'{"private_path":"C:/Users/Example/secret.txt"}'
        connection.execute("INSERT INTO runtime_source VALUES (?, 'zlib', ?, ?)",
                           (len(raw), zlib.compress(raw), hashlib.sha256(raw).hexdigest()))
    assert raw not in db.read_bytes()
    with pytest.raises(ValueError, match="private_path_or_credential"):
        validate_database_release_hygiene(db, workspace_root=tmp_path)
