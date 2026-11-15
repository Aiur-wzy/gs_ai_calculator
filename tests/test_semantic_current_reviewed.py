"""Current-head qualification against tiny real SQLite registries, never main DB."""
import copy
import importlib.util
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

import pytest

from genshin_db import semantic_store as store

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def helpers():
    # Reuse existing legal fact/descriptor fixtures and production writing APIs.
    spec = importlib.util.spec_from_file_location("_existing_semantic_fixtures", ROOT / "tests/test_semantic_store.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tiny(tmp_path, helpers):
    path = tmp_path / "tiny-registry.db"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("CREATE TABLE user_note(value TEXT)")
        conn.execute("INSERT INTO user_note VALUES ('unchanged')")
    assert helpers.install(path)["applied"]
    return path


@contextmanager
def transaction(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("BEGIN")
        yield conn


def current(path, expected=None, line="release", kind="fact", ident="one"):
    with transaction(path) as conn:
        before = conn.total_changes
        result = store.read_current_reviewed_record(conn, line, kind, ident, expected)
        assert conn.in_transaction and conn.total_changes == before
        return result


def test_approved_receipt_is_real_registry_fingerprints_and_not_source_authority(tiny, helpers):
    before = tiny.read_bytes()
    result = current(tiny, 1)
    original = helpers.read(tiny)
    assert result["status"] == "ready" and result["record"] == original
    assert result["receipt"]["payload_fingerprint"] == original["payload_fingerprint"]
    assert result["receipt"]["review_fingerprint"] == original["review_fingerprint"]
    assert result["execution_authority"] is False and result["source_status"] == "not_checked"
    assert result["scope"] == "current_semantic_record_review"
    assert tiny.read_bytes() == before


@pytest.mark.parametrize("value", [True, 1.0, "1", 0, -1, 2**31])
def test_expected_revision_is_strict_and_no_implicit_sqlite_coercion(tiny, value):
    with transaction(tiny) as conn, pytest.raises(ValueError):
        store.read_current_reviewed_record(conn, "release", "fact", "one", value)


def test_transaction_required_without_autobegin_or_schema_install(tmp_path):
    with closing(sqlite3.connect(tmp_path / "empty.db")) as conn:
        with pytest.raises(store.SemanticStoreError) as error:
            store.read_current_reviewed_record(conn, "release", "fact", "one")
        assert error.value.reason == "semantic_transaction_required"
        assert not conn.in_transaction and conn.total_changes == 0
        assert conn.execute("SELECT name FROM sqlite_master").fetchall() == []


def test_missing_component_read_does_not_install(tmp_path):
    path = tmp_path / "empty.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE manual(value TEXT)")
    before = path.read_bytes()
    result = current(path)
    assert result["reason"] == "component_missing" and result["record"] is None
    assert path.read_bytes() == before


def test_deleted_head_cannot_fall_back_to_approved_history(tiny, helpers):
    assert helpers.install(tiny, helpers.bundle([], [helpers.head(revision=None, expected=1)]))["applied"]
    assert helpers.read(tiny, revision=1)["review"]["status"] == "approved"
    assert current(tiny, 1)["reason"] == "head_missing"
    assert current(tiny, ident="absent")["reason"] == "head_missing"


def test_new_revoked_head_cannot_be_bypassed_by_old_approved_revision(tiny, helpers):
    assert helpers.install(tiny, helpers.bundle([helpers.fact(revision=2, status="revoked")],
        [helpers.head(revision=2, expected=1)]))["applied"]
    assert helpers.read(tiny, revision=1)["review"]["status"] == "approved"
    old = current(tiny, 1)
    assert old["reason"] == "head_changed" and old["current_revision"] == 2 and old["record"] is None
    latest = current(tiny)
    assert latest["reason"] == "review_not_approved" and latest["review_status"] == "revoked"
    assert latest["receipt"]["revision"] == 2 and latest["record"] is None


def test_same_revision_revocation_changes_review_fingerprint_and_next_call_rejects(tiny, helpers):
    old = current(tiny)
    helpers.review(tiny, helpers.read(tiny))
    revoked = current(tiny, 1)
    assert revoked["review_status"] == "revoked" and revoked["record"] is None
    assert old["receipt"]["revision"] == revoked["receipt"]["revision"] == 1
    assert old["receipt"]["payload_fingerprint"] == revoked["receipt"]["payload_fingerprint"]
    assert old["receipt"]["review_fingerprint"] != revoked["receipt"]["review_fingerprint"]


@pytest.mark.parametrize("status", ["draft", "pending", "revoked"])
def test_nonapproved_status_is_precise_and_withholds_record(tiny, helpers, status):
    assert helpers.install(tiny, helpers.bundle([helpers.fact(revision=2, status=status)],
        [helpers.head(revision=2, expected=1)]))["applied"]
    result = current(tiny)
    assert result["review_status"] == status and result["record"] is None
    assert result["reason"] == "review_not_approved" and result["execution_authority"] is False


@pytest.mark.parametrize("column,value", [
    ("payload_fingerprint", "0" * 64), ("review_fingerprint", "0" * 64),
    ("status", "revoked"), ("payload_json", "{"), ("payload_json", "{}"),
])
def test_corrupt_record_never_returns_approved_and_keeps_domain_error(tiny, column, value):
    with closing(sqlite3.connect(tiny)) as conn, conn:
        conn.execute(f"UPDATE mechanic_semantic_fact SET {column}=?", (value,))
    before = tiny.read_bytes()
    with pytest.raises(store.SemanticStoreError) as error:
        current(tiny)
    assert error.value.reason == "semantic_dataset_invalid"
    assert tiny.read_bytes() == before


def test_future_beta_schema_protects_release_reads_without_masking_reason(tiny, helpers):
    assert helpers.install(tiny, helpers.bundle([helpers.fact(line="beta")]), line="beta")["applied"]
    with closing(sqlite3.connect(tiny)) as conn, conn:
        conn.execute("UPDATE data_component SET schema_version=999 WHERE version_line='beta'")
    with pytest.raises(store.SemanticStoreError) as error:
        current(tiny)
    assert error.value.reason == "semantic_schema_unsupported"


@pytest.mark.parametrize("mutation", ["dangling_head", "missing_table", "bad_head_time"])
def test_invalid_component_and_head_fail_closed(tiny, mutation):
    with closing(sqlite3.connect(tiny)) as conn, conn:
        if mutation == "dangling_head":
            conn.execute("UPDATE mechanic_semantic_head SET revision=999")
        elif mutation == "missing_table":
            conn.execute("DROP TABLE mechanic_descriptor")
        else:
            conn.execute("UPDATE mechanic_semantic_head SET updated_at=''")
    with pytest.raises(store.SemanticStoreError) as error:
        current(tiny)
    assert error.value.reason == "semantic_dataset_invalid"


def test_version_lines_are_independent(tiny, helpers):
    assert helpers.install(tiny, helpers.bundle([helpers.fact(line="beta", status="pending")]), line="beta")["applied"]
    assert current(tiny)["status"] == "ready"
    assert current(tiny, line="beta")["review_status"] == "pending"


def test_descriptor_current_review_does_not_claim_transitive_fact_admission(tiny, helpers):
    path = tiny.with_name("descriptor.db")
    with closing(sqlite3.connect(path)):
        pass
    assert helpers.install(path, helpers.descriptor_bundle())["applied"]
    result = current(path, kind="descriptor", ident="mechanism")
    assert result["status"] == "ready" and result["receipt"]["record_kind"] == "descriptor"
    helpers.review(path, helpers.read(path))
    # This layer qualifies one record; descriptor dependency approval belongs to
    # the caller. Revoking a fact does not silently imply descriptor closure.
    result = current(path, kind="descriptor", ident="mechanism")
    assert result["source_status"] == "not_checked" and result["execution_authority"] is False


def test_sqlite_snapshot_is_consistent_and_next_transaction_observes_revocation(tiny, helpers):
    with closing(sqlite3.connect(tiny)) as conn:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    with transaction(tiny) as reader:
        first = store.read_current_reviewed_record(reader, "release", "fact", "one")
        helpers.review(tiny, helpers.read(tiny))
        second = store.read_current_reviewed_record(reader, "release", "fact", "one")
        assert second == first and second["status"] == "ready"
    assert current(tiny)["review_status"] == "revoked"


def test_returned_material_does_not_mutate_or_cache_registry(tiny):
    result = current(tiny)
    original = copy.deepcopy(result)
    result["record"]["payload"]["claim"]["requirement"] = "tampered"
    result["receipt"]["review_fingerprint"] = "0" * 64
    assert current(tiny) == original
