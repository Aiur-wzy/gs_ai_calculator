# ruff: noqa: F811
"""Independent adversarial expectations over frozen source and synthetic SQLite."""
import copy
import sqlite3
import sys
from contextlib import closing

import pytest

from genshin_db import runtime_action_sources as sources
from genshin_db.runtime_queries import query_runtime
from genshin_db.semantic_store import (
    HEAD_TABLE,
    import_bundle,
    read_record,
    review_record,
)
from genshin_runtime import action_root_semantics as roots
from genshin_runtime.mechanism_semantics import fingerprint
from tests.action_resource_fixtures import (
    action_resource_synthetic_db_only,  # noqa: F401
)
from tests.test_action_resource_related import related_fixture  # noqa: F401
from tests.test_action_resource_sources import (  # noqa: F401
    CID,
    DID,
    SOURCE,
    fixture_record,
    install,
    query,
    source_db,
)
from tests.test_pipeline import fake_repo  # noqa: F401

pytestmark = pytest.mark.usefixtures("action_resource_synthetic_db_only")


def new_revision(record, status):
    result = copy.deepcopy(record)
    result["revision"] = result["payload"]["revision"] = 2
    result["review"] = {"status": status, "reviewer": "independent-synthetic" if status in ("approved", "revoked") else None,
                        "reviewed_at": "2026-10-08T00:00:00+00:00" if status in ("approved", "revoked") else None}
    return result


@pytest.mark.parametrize("status", ["approved", "pending", "revoked"])
def test_explicit_historical_delta_cannot_return_after_head_advance(source_db, status):
    old = fixture_record(source_db)
    install(source_db, old)
    install(source_db, new_revision(old, status), expected=1)
    out = query(source_db, fact_refs=[{"id": old["id"], "revision": 1}])
    assert out["sources"][0]["reason"] == "head_changed"
    assert out["sources"][0]["projection"] is None
    assert out["capacity"] is None and out["execution_authority"] is False
    with closing(sqlite3.connect(source_db)) as conn:
        assert read_record(conn, "release", "fact", old["id"], 1)["review"]["status"] == "approved"


def test_higher_history_without_head_change_preserves_exact_source_query(source_db):
    old = fixture_record(source_db)
    install(source_db, old)
    before = query(source_db)
    history = new_revision(old, "revoked")
    import_bundle(source_db, {"schema_version": 1, "id": "independent.history", "facts": [history], "descriptors": [],
        "heads": [{"record_kind": "fact", "record_id": old["id"], "revision": 1, "expected_head_revision": 1}]}, dry_run=False)
    assert query(source_db) == before


def test_head_deleted_blocks_explicit_old_and_auto_never_reanimates(source_db):
    old = fixture_record(source_db)
    install(source_db, old)
    with closing(sqlite3.connect(source_db)) as conn, conn:
        conn.execute(f"DELETE FROM {HEAD_TABLE} WHERE record_id=?", (old["id"],))
    assert query(source_db)["sources"] == []
    item = query(source_db, fact_refs=[{"id": old["id"], "revision": 1}])["sources"][0]
    assert item["reason"] == "head_missing" and item["projection"] is None


@pytest.mark.parametrize("status", ["approved", "pending", "revoked"])
def test_related_old_explicit_revision_never_inherits_new_head(related_fixture, status):
    db, old = related_fixture
    install(db, old)
    install(db, new_revision(old, status), expected=1)
    result = query_runtime(db, {"action": "action_capacity_source_profile", "character_id": old["subject"]["target_id"],
        "constellation": 6, "fact_refs": [{"id": old["id"], "revision": 1}]})
    assert result["sources"][0]["reason"] == "head_changed"
    assert result["sources"][0]["projection"] is None


def test_current_source_review_and_complete_material_share_wal_snapshot(source_db, monkeypatch):
    old = fixture_record(source_db)
    install(source_db, old)
    with closing(sqlite3.connect(source_db)) as conn:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    original = sources.read_current_reviewed_record
    changed = []

    def read_and_revoke(conn, *args):
        current = original(conn, *args)
        if not changed:
            review_record(source_db, version_line="release", record_kind="fact", record_id=old["id"], revision=1,
                status="revoked", reviewer="independent-synthetic", reviewed_at="2026-10-08T02:00:00+00:00",
                expected_review_fingerprint=current["receipt"]["review_fingerprint"])
            changed.append(True)
        return current

    monkeypatch.setattr(sources, "read_current_reviewed_record", read_and_revoke)
    first = query(source_db)
    assert first["sources"][0]["projection"]["amount"] == 1
    assert query(source_db)["sources"][0]["reason"] == "review_not_approved"


@pytest.mark.parametrize("change", ["parent", "text", "slot", "raw_type", "depot_type"])
def test_approved_source_requires_complete_fresh_raw_identity(source_db, monkeypatch, change):
    old = fixture_record(source_db)
    install(source_db, old)
    original = sources.fresh_delta_material

    def mutate(*args):
        inv, row, declaration = copy.deepcopy(original(*args))
        if change == "parent":
            inv["sources"][SOURCE]["raw"]["UnrelatedParentMember"] = {"opaque": 1}
            inv["sources"][SOURCE]["canonical_sha256"] = fingerprint(inv["sources"][SOURCE]["raw"])
        elif change == "text":
            declaration["description"]["text"] += " independent difference"
            declaration["description"]["node_sha256"] = fingerprint(declaration["description"]["text"])
        elif change == "slot":
            declaration["constellation_rank"] = 2
        elif change == "raw_type":
            row["operation"]["pointDelta"] = True
            row["normalized_operation"]["pointDelta"] = True
            row["operation_node_sha256"] = fingerprint(row["operation"])
            inv["sources"][SOURCE]["raw"]["CapacityC1"][0]["pointDelta"] = True
            inv["sources"][SOURCE]["canonical_sha256"] = fingerprint(inv["sources"][SOURCE]["raw"])
        else:
            declaration["depot"]["record"]["id"] = float(DID)
            declaration["depot"]["node_sha256"] = fingerprint(declaration["depot"]["record"])
            declaration["depot"]["definition"]["node_sha256"] = declaration["depot"]["node_sha256"]
        return inv, row, declaration

    monkeypatch.setattr(sources, "fresh_delta_material", mutate)
    with pytest.raises(ValueError):
        query(source_db)


def test_max_explicit_refs_reject_before_read(monkeypatch, tmp_path):
    monkeypatch.setattr(sources, "_read", lambda *a: pytest.fail("oversized request reached database"))
    with pytest.raises(ValueError):
        sources.get_action_source_profile(tmp_path / "must-not-open.db", character_id=CID, constellation=1,
            fact_refs=[{"id": f"record.{i}", "revision": 1} for i in range(33)])


def test_unreviewed_source_cannot_enter_material_rehydration(source_db, monkeypatch):
    install(source_db, fixture_record(source_db, "pending"))
    monkeypatch.setattr(sources, "fresh_delta_material", lambda *a: pytest.fail("unreviewed material loaded"))
    out = query(source_db)
    assert out["sources"][0]["projection"] is None


def test_weak_root_material_budget_rejects_before_unbounded_pending_stack():
    # Do not allocate an extreme corpus: one existing shallow 200001-element
    # list is already provably beyond the walk's published 200000-node limit.
    value = [None] * 200001
    peak = 0

    def trace(frame, event, arg):
        nonlocal peak
        if frame.f_code is roots.bounded.__code__ and event == "line":
            peak = max(peak, len(frame.f_locals.get("stack", [])))
        return trace

    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        with pytest.raises(roots.BaseBindingError) as error:
            roots.bounded(value)
    finally:
        sys.settrace(previous)
    assert error.value.code == "binding_budget"
    assert peak <= 200000, f"Oversized children were queued before budget refusal: {peak} pending entries"
