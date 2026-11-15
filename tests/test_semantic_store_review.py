"""Independent EC20 review/CAS and final-lock mirror tests using tiny SQLite files."""
from __future__ import annotations

import copy
import sqlite3
from contextlib import closing

import pytest

from genshin_db import semantic_store as store


def record(ident="review-fact", revision=1, line="release"):
    return {
        "id": ident, "revision": revision,
        "subject": {"target_type": "character", "target_id": 90000077},
        "payload": {"id": ident, "revision": revision, "kind": "execution_requirement",
                    "claim": {"requirement": "single_target"},
                    "evidence_tier": "experimental_reference", "evidence_refs": ["evidence"]},
        "evidence": [{"id": "evidence", "kind": "primary_test", "locator": "isolated independent fixture",
                      "sha256": "d" * 64}],
        "applicability": {"version_line": line, "upstream_commit": "e" * 40,
                          "normalization_fingerprint": "f" * 64},
        "review": {"status": "approved", "reviewer": "independent-fixture",
                   "reviewed_at": "2026-10-03T03:00:00+00:00"},
    }


def head(ident="review-fact", revision=1, expected=None):
    return {"record_kind": "fact", "record_id": ident, "revision": revision,
            "expected_head_revision": expected}


def bundle(facts=None, heads=None):
    return {"schema_version": 1, "id": "independent-fixture", "facts": facts if facts is not None else [record()],
            "descriptors": [], "heads": heads if heads is not None else [head()]}


@pytest.fixture
def tiny_db(tmp_path):
    path = tmp_path / "isolated-review.db"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("CREATE TABLE manual_note(id TEXT PRIMARY KEY,content TEXT)")
        conn.execute("INSERT INTO manual_note VALUES ('one','必须保留')")
    return path


def read(path, *, line="release", ident="review-fact", revision=None):
    with closing(sqlite3.connect(path)) as conn:
        return store.read_record(conn, line, "fact", ident, revision)


def install(path, data=None, line="release"):
    return store.import_bundle(path, data if data is not None else bundle(), version_line=line, dry_run=False)


def revoke(path, current, *, line="release"):
    return store.review_record(path, version_line=line, record_kind="fact", record_id=current["id"],
                               revision=current["revision"], status="revoked", reviewer="independent-revoker",
                               reviewed_at="2026-10-03T04:00:00+00:00",
                               expected_review_fingerprint=current["review_fingerprint"])


def test_review_missing_and_dryrun_are_physically_read_only(tiny_db):
    before = tiny_db.read_bytes()
    assert read(tiny_db)["reason"] == "component_missing"
    assert not store.import_bundle(tiny_db, bundle())["applied"]
    assert tiny_db.read_bytes() == before
    with closing(sqlite3.connect(tiny_db)) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("manual_note",)]


def test_review_reimport_cannot_resurrect_revoked_or_partially_install_tail(tiny_db):
    assert install(tiny_db)["applied"]
    old = read(tiny_db)
    revoked = revoke(tiny_db, old)
    result = install(tiny_db, bundle([record(), record("later")], [head(), head("later")]))
    assert not result["applied"]
    assert read(tiny_db)["review"]["status"] == "revoked"
    assert read(tiny_db, ident="later", revision=1)["reason"] == "record_missing"
    assert old["payload_fingerprint"] == revoked["payload_fingerprint"]
    assert old["review_fingerprint"] != revoked["review_fingerprint"]
    with pytest.raises(store.SemanticStoreError):
        revoke(tiny_db, old)


def test_review_head_cas_rejects_second_stale_writer_and_preserves_history(tiny_db):
    install(tiny_db)
    assert install(tiny_db, bundle([record(revision=2)], [head(revision=2, expected=1)]))["applied"]
    losing = install(tiny_db, bundle([record(revision=3)], [head(revision=3, expected=1)]))
    assert not losing["applied"]
    assert read(tiny_db)["revision"] == 2
    assert read(tiny_db, revision=3)["reason"] == "record_missing"
    assert install(tiny_db, bundle([], [head(revision=None, expected=2)]))["applied"]
    assert read(tiny_db)["reason"] == "head_missing"
    assert read(tiny_db, revision=1)["revision"] == 1
    assert read(tiny_db, revision=2)["revision"] == 2


@pytest.mark.parametrize("field,value", [("reviewer", None), ("reviewed_at", "not-a-date")])
def test_review_fake_approved_without_review_metadata_cannot_install(tiny_db, field, value):
    data = bundle()
    data["facts"][0]["review"][field] = value
    with pytest.raises(ValueError):
        install(tiny_db, data)
    assert read(tiny_db)["reason"] == "component_missing"


def test_review_future_component_cannot_be_downgraded_by_ensure_or_import(tiny_db):
    install(tiny_db)
    with closing(sqlite3.connect(tiny_db)) as conn, conn:
        conn.execute("UPDATE data_component SET schema_version=999 WHERE component=?", (store.COMPONENT,))
    before = tiny_db.read_bytes()
    with closing(sqlite3.connect(tiny_db)) as conn, pytest.raises(store.SemanticStoreError):
        store.ensure_schema(conn)
    with pytest.raises(store.SemanticStoreError):
        install(tiny_db)
    assert tiny_db.read_bytes() == before


def test_review_latest_revocation_and_head_deletion_mirror_both_lines(tiny_db, tmp_path):
    install(tiny_db)
    install(tiny_db, bundle([record(line="beta")]), line="beta")
    dest = tmp_path / "candidate.db"
    with closing(sqlite3.connect(tiny_db)) as src, closing(sqlite3.connect(dest)) as dst:
        src.backup(dst)
    # These changes simulate writes after candidate creation but before final B lock.
    revoke(tiny_db, read(tiny_db))
    install(tiny_db, bundle([], [head(revision=None, expected=1)]))
    install(tiny_db, bundle([record("late", line="beta")], [head("late")]), line="beta")
    with closing(sqlite3.connect(tiny_db)) as src, closing(sqlite3.connect(dest)) as dst, dst:
        store.synchronize(src, dst)
        assert store.semantic_digest(src) == store.semantic_digest(dst)
        assert dst.execute("SELECT * FROM manual_note").fetchall() == [("one", "必须保留")]
    assert read(dest)["reason"] == "head_missing"
    assert read(dest, revision=1)["review"]["status"] == "revoked"
    assert read(dest, line="beta")["review"]["status"] == "approved"
    assert read(dest, line="beta", ident="late")["revision"] == 1


def test_review_synchronize_does_not_commit_callers_publication_transaction(tiny_db, tmp_path):
    install(tiny_db)
    dest = tmp_path / "candidate.db"
    with closing(sqlite3.connect(tiny_db)) as src, closing(sqlite3.connect(dest)) as dst:
        src.backup(dst)
    before = dest.read_bytes()
    revoke(tiny_db, read(tiny_db))
    with closing(sqlite3.connect(tiny_db)) as src, closing(sqlite3.connect(dest)) as dst:
        dst.execute("BEGIN IMMEDIATE")
        dst.execute("UPDATE manual_note SET content='candidate-only'")
        store.synchronize(src, dst)
        assert store.read_record(dst, "release", "fact", "review-fact")["review"]["status"] == "revoked"
        dst.rollback()
    assert dest.read_bytes() == before


def test_review_corrupt_saved_payload_cannot_be_read_or_mirrored(tiny_db, tmp_path):
    install(tiny_db)
    with closing(sqlite3.connect(tiny_db)) as conn, conn:
        conn.execute("UPDATE mechanic_semantic_fact SET payload_json=replace(payload_json,'single_target','fixed_party')")
    with pytest.raises(store.SemanticStoreError):
        read(tiny_db)
    with closing(sqlite3.connect(tiny_db)) as src, closing(sqlite3.connect(tmp_path / "candidate.db")) as dst:
        with pytest.raises(store.SemanticStoreError):
            store.synchronize(src, dst)
        assert dst.execute("SELECT name FROM sqlite_master").fetchall() == []


def test_review_returned_nested_data_does_not_mutate_saved_review(tiny_db):
    install(tiny_db)
    first = read(tiny_db)
    expected = copy.deepcopy(first)
    first["payload"]["claim"]["requirement"] = "no_hitlag"
    first["review"]["status"] = "revoked"
    assert read(tiny_db) == expected


def test_review_record_applicability_line_must_match_import_partition(tiny_db):
    with pytest.raises(ValueError):
        install(tiny_db, bundle([record(line="beta")]), line="release")
    assert read(tiny_db)["reason"] == "component_missing"
