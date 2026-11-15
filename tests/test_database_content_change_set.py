from __future__ import annotations

import copy
import hashlib
import shutil
import sqlite3
from pathlib import Path

import pytest

from genshin_db.content_change_set import (
    ContentChangeSetError,
    apply_content_change_set,
    create_approval,
    create_change_set,
    project_content_fingerprint,
    row_fingerprint,
)
from genshin_db.live_mutation_policy import AcceptedDatabaseImmutableError


def _database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE annotation (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_type TEXT NOT NULL,
                target_id INTEGER NOT NULL,
                version_line TEXT,
                category TEXT,
                title TEXT,
                content TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                author TEXT,
                reviewer TEXT,
                reviewed_at TEXT,
                game_version_range TEXT
            );
            CREATE TABLE character_note (
                character_id INTEGER NOT NULL,
                version_line TEXT NOT NULL,
                note TEXT,
                updated_at TEXT,
                PRIMARY KEY(character_id, version_line)
            );
            CREATE TABLE data_component (
                component TEXT NOT NULL,
                version_line TEXT NOT NULL,
                schema_version INTEGER,
                upstream_commit TEXT,
                built_at TEXT,
                validation_json TEXT,
                PRIMARY KEY(component, version_line)
            );
            """
        )
        connection.execute(
            "INSERT INTO annotation VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                7,
                "character",
                10000002,
                "release",
                "mechanism",
                "old title",
                "old content",
                "approved",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
                "maintainer",
                "reviewer",
                "2026-01-01T00:00:00Z",
                "7.1",
            ),
        )
        connection.execute(
            "INSERT INTO character_note VALUES (?,?,?,?)",
            (10000002, "release", "old note", "2026-01-01T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO data_component VALUES (?,?,?,?,?,?)",
            (
                "mechanic_semantics",
                "release",
                1,
                None,
                "2026-01-01T00:00:00Z",
                "{}",
            ),
        )


def _annotation(path: Path) -> dict:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        return dict(connection.execute("SELECT * FROM annotation WHERE id=7").fetchone())


def _bundle(base: Path):
    current = _annotation(base)
    replacement = {
        **current,
        "title": "reviewed title",
        "content": "reviewed content",
        "updated_at": "2026-10-28T00:00:00Z",
    }
    source = {
        "source_id": "appendix:ayaka:2026-10-28",
        "kind": "appendix",
        "locator": "review/ayaka-appendix.json",
        "evidence_sha256": hashlib.sha256(b"reviewed evidence").hexdigest(),
        "summary": "Update one reviewed character appendix row.",
    }
    change_set = create_change_set(
        base_database_release_id="db-7.1.53-r1",
        target_database_release_id="db-7.1.53-r2",
        base_project_content_fingerprint=project_content_fingerprint(base),
        sources=[source],
        operations=[
            {
                "operation_id": "update-annotation-7",
                "source_id": source["source_id"],
                "table": "annotation",
                "action": "update",
                "key": {"id": 7},
                "before_sha256": row_fingerprint(current),
                "row": replacement,
            }
        ],
        impact={"components": ["project_knowledge"], "summary": "One appendix update"},
    )
    approval = create_approval(
        change_set,
        approved_by="user",
        approved_at="2026-10-28T00:05:00Z",
    )
    return change_set, approval


def test_approved_change_applies_only_to_isolated_candidate(tmp_path):
    base = tmp_path / "accepted" / "genshin.db"
    candidate = tmp_path / "candidate" / "genshin.db"
    _database(base)
    candidate.parent.mkdir()
    shutil.copyfile(base, candidate)
    change_set, approval = _bundle(base)

    receipt = apply_content_change_set(
        base_database=base,
        candidate_database=candidate,
        change_set=change_set,
        approval=approval,
        base_database_release_id="db-7.1.53-r1",
        target_database_release_id="db-7.1.53-r2",
        applied_at="2026-10-28T00:06:00Z",
    )

    assert _annotation(base)["content"] == "old content"
    assert _annotation(candidate)["content"] == "reviewed content"
    assert receipt["operation_counts"] == {"add": 0, "update": 1, "delete": 0}
    assert receipt["before_project_content_fingerprint"] == change_set[
        "base_project_content_fingerprint"
    ]
    assert receipt["after_project_content_fingerprint"] == project_content_fingerprint(
        candidate
    )
    assert len(receipt["receipt_fingerprint"]) == 64


def test_unreported_candidate_drift_is_rejected_without_partial_write(tmp_path):
    base = tmp_path / "accepted" / "genshin.db"
    candidate = tmp_path / "candidate" / "genshin.db"
    _database(base)
    candidate.parent.mkdir()
    shutil.copyfile(base, candidate)
    change_set, approval = _bundle(base)
    with sqlite3.connect(candidate) as connection:
        connection.execute(
            "UPDATE character_note SET note='unreported drift' WHERE character_id=10000002"
        )

    with pytest.raises(
        ContentChangeSetError, match="database_content_candidate_base_drift"
    ):
        apply_content_change_set(
            base_database=base,
            candidate_database=candidate,
            change_set=change_set,
            approval=approval,
            base_database_release_id="db-7.1.53-r1",
            target_database_release_id="db-7.1.53-r2",
        )

    assert _annotation(candidate)["content"] == "old content"


@pytest.mark.parametrize("tamper", ["approval", "change_set", "before"])
def test_digest_and_exact_before_preconditions_fail_closed(tmp_path, tamper):
    base = tmp_path / "accepted" / "genshin.db"
    candidate = tmp_path / "candidate" / "genshin.db"
    _database(base)
    candidate.parent.mkdir()
    shutil.copyfile(base, candidate)
    change_set, approval = _bundle(base)
    if tamper == "approval":
        approval = {**approval, "approved_by": "someone else"}
    elif tamper == "change_set":
        change_set = copy.deepcopy(change_set)
        change_set["impact"]["summary"] = "silently changed scope"
    else:
        change_set = copy.deepcopy(change_set)
        change_set["operations"][0]["before_sha256"] = "0" * 64
        # Keep the outer declaration internally valid so the row precondition,
        # rather than the declaration digest, is what rejects the operation.
        unsigned = {key: value for key, value in change_set.items() if key != "change_set_id"}
        change_set["change_set_id"] = hashlib.sha256(
            __import__("json").dumps(
                unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        approval = create_approval(
            change_set, approved_by="user", approved_at="2026-10-28T00:05:00Z"
        )

    with pytest.raises(ContentChangeSetError):
        apply_content_change_set(
            base_database=base,
            candidate_database=candidate,
            change_set=change_set,
            approval=approval,
            base_database_release_id="db-7.1.53-r1",
            target_database_release_id="db-7.1.53-r2",
        )
    assert _annotation(candidate)["content"] == "old content"


def test_accepted_database_directory_cannot_be_used_as_candidate(tmp_path):
    base = tmp_path / "accepted" / "genshin.db"
    candidate = tmp_path / "candidate" / "genshin.db"
    _database(base)
    candidate.parent.mkdir()
    shutil.copyfile(base, candidate)
    (candidate.parent / "database-live-manifest.json").write_text("{}", encoding="utf-8")
    change_set, approval = _bundle(base)

    with pytest.raises(AcceptedDatabaseImmutableError):
        apply_content_change_set(
            base_database=base,
            candidate_database=candidate,
            change_set=change_set,
            approval=approval,
            base_database_release_id="db-7.1.53-r1",
            target_database_release_id="db-7.1.53-r2",
        )
