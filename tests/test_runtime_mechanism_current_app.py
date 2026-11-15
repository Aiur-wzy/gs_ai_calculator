"""Current-review public/App contracts; fixtures are synthetic and local."""
import copy
import sqlite3
from contextlib import closing

import pytest

from genshin_db import semantic_store as store
from genshin_db.equipment_queries import equipment_read_session
from genshin_db.runtime_queries import query_runtime
from tests.mechanism_source_fixtures import (  # noqa: F401
    fake_repo,
    mechanism_db,
    mechanism_repo,
)
from tests.test_mechanism_sources_integration import (
    MECHANISM,
)


def query(db, revision=None):
    request = {"action": "mechanism_profile", "mechanism_id": MECHANISM}
    if revision is not None:
        request["revision"] = revision
    return query_runtime(db, request)


def read(db, kind, ident, revision=None):
    with closing(sqlite3.connect(db)) as conn:
        return store.read_record(conn, "release", kind, ident, revision)


def mutate(db, kind, change):
    descriptor = read(db, "descriptor", MECHANISM)
    ident = MECHANISM if kind == "descriptor" else descriptor["payload"]["descriptor"]["evidence_fact_ids"][0].rsplit("@", 1)[0]
    original = read(db, kind, ident)
    revision = original["revision"]
    if change == "same_revoked":
        store.review_record(db, version_line="release", record_kind=kind, record_id=ident, revision=revision,
            status="revoked", reviewer="isolated-current-regression", reviewed_at="2026-10-07T00:00:00+00:00",
            expected_review_fingerprint=original["review_fingerprint"])
    else:
        data = {"schema_version": 1, "id": "current.regression", "facts": [], "descriptors": [],
                "heads": [{"record_kind": kind, "record_id": ident, "revision": None if change == "deleted" else revision + 1,
                           "expected_head_revision": revision}]}
        if change != "deleted":
            record = {key: copy.deepcopy(original[key]) for key in store.RECORD_FIELDS}
            record["revision"] = revision + 1
            if kind == "fact":
                record["payload"]["revision"] = revision + 1
            else:
                record["payload"]["descriptor"]["revision"] = revision + 1
            record["review"]["status"] = "revoked" if change == "new_revoked" else "approved"
            data["facts" if kind == "fact" else "descriptors"] = [record]
        assert store.import_bundle(db, data, dry_run=False)["applied"]
    return ident, revision, original


@pytest.mark.parametrize("kind", ["descriptor", "fact"])
@pytest.mark.parametrize("change", ["new_approved", "new_revoked", "deleted", "same_revoked"])
def test_public_current_guard_never_uses_old_approval(mechanism_db, kind, change):  # noqa: F811
    before = query(mechanism_db)
    assert before["status"] == "ready"
    ident, revision, old = mutate(mechanism_db, kind, change)
    out = query(mechanism_db, revision=revision if kind == "descriptor" else None)
    assert out["status"] == "pending"
    assert out["source_proof"] is None and out["compiler_result"] is None
    assert out.get("authorization_fingerprint") is None
    issue = next(item for item in out["issues"] if item.get("record_id") == ident)
    assert issue["record_kind"] == kind and issue["requested_revision"] == revision
    assert issue["reason"] == ("head_changed" if change.startswith("new_") else "head_missing" if change == "deleted" else "review_not_approved")
    assert issue["current_revision"] == (revision + 1 if change.startswith("new_") else None if change == "deleted" else revision)
    if change != "same_revoked":
        assert read(mechanism_db, kind, ident, revision) == old


def test_same_transaction_then_next_transaction_revocation(mechanism_db):  # noqa: F811
    with closing(sqlite3.connect(mechanism_db)) as conn:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    with equipment_read_session(mechanism_db):
        before = query(mechanism_db)
        mutate(mechanism_db, "fact", "new_revoked")
        assert query(mechanism_db) == before
    after = query(mechanism_db)
    assert after["status"] == "pending" and after["compiler_result"] is None


@pytest.mark.parametrize("kind", ["descriptor", "fact"])
@pytest.mark.parametrize("target", ["head", "record"])
def test_duplicate_current_identity_is_rejected(mechanism_db, kind, target):  # noqa: F811
    descriptor = read(mechanism_db, "descriptor", MECHANISM)
    ident = MECHANISM if kind == "descriptor" else descriptor["payload"]["descriptor"]["evidence_fact_ids"][0].rsplit("@", 1)[0]
    table = "mechanic_semantic_head" if target == "head" else store.TABLES[kind]
    with closing(sqlite3.connect(mechanism_db)) as conn, conn:
        conn.execute(f"CREATE TABLE duplicate AS SELECT * FROM {table}")
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE duplicate RENAME TO {table}")
        conn.execute(f"INSERT INTO {table} SELECT * FROM {table} WHERE record_id=?", (ident,))
    with pytest.raises(ValueError) as error:
        query(mechanism_db)
    assert getattr(error.value, "reason", None) == "semantic_dataset_invalid"
    assert error.value.details["record_kind"] == kind
    assert error.value.details["record_id"] == ident
    assert error.value.details["current_revision"] is None
    assert error.value.details["review_status"] is None
