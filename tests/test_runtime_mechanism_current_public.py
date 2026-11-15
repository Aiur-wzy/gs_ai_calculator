"""Independent public regressions built from permanent two-mechanism fixtures."""
import copy
import sqlite3
from contextlib import closing

import pytest

from genshin_db.runtime_queries import query_runtime
from genshin_db.semantic_store import import_bundle, read_record
from tests.mechanism_source_fixtures import (  # noqa: F401
    fake_repo,
    fixture_catalog,
    mechanism_db,
    mechanism_repo,
)

ADDITION = "skill.current-atk-limited-addition"
SWIRL = "passive.swirl-elemental-bonus"


def public(db, mechanism=ADDITION, revision=None):
    request = {"action": "mechanism_profile", "mechanism_id": mechanism}
    if revision is not None:
        request["revision"] = revision
    return query_runtime(db, request)


def advance(db, kind, record, status):
    old_revision = record["revision"]
    bundle = {"schema_version": 1, "id": "independent-current-regression", "facts": [], "descriptors": [],
              "heads": [{"record_kind": kind, "record_id": record["id"],
                         "revision": None if status == "deleted" else old_revision + 1,
                         "expected_head_revision": old_revision}]}
    if status != "deleted":
        next_record = copy.deepcopy(record)
        next_record["revision"] = old_revision + 1
        payload = next_record["payload"] if kind == "fact" else next_record["payload"]["descriptor"]
        payload["revision"] = old_revision + 1
        next_record["review"]["status"] = status
        if status == "pending":
            next_record["review"]["reviewer"] = None
            next_record["review"]["reviewed_at"] = None
        bundle["facts" if kind == "fact" else "descriptors"].append(next_record)
    assert import_bundle(db, bundle, dry_run=False)["applied"]


@pytest.mark.parametrize("kind", ["fact", "descriptor"])
@pytest.mark.parametrize("status", ["approved", "pending", "revoked", "deleted"])
def test_old_selected_revision_cannot_survive_current_head_changes(mechanism_db, kind, status):  # noqa: F811
    initial = public(mechanism_db)
    assert initial["status"] == "ready"
    seed = fixture_catalog(mechanism_db)
    if kind == "descriptor":
        record = next(row for row in seed["descriptors"] if row["id"] == ADDITION)
    else:
        ident, revision = initial["descriptor"]["evidence_fact_ids"][0].rsplit("@", 1)
        record = next(row for row in seed["facts"] if row["id"] == ident and row["revision"] == int(revision))
    advance(mechanism_db, kind, record, status)
    # Selecting an old descriptor is a fresh query, not a request to execute history.
    result = public(mechanism_db, revision=initial["selection"]["revision"])
    assert result["status"] == result["materialization_status"] == "pending"
    compiled = result.get("compiler_result") or {}
    assert not compiled.get("rule_templates")
    assert not compiled.get("initial_rule_templates")
    assert not compiled.get("execution_ports")
    assert not compiled.get("execution_qualification")
    assert result.get("authorization_fingerprint") != initial["authorization_fingerprint"]
    assert result["issues"]
    with closing(sqlite3.connect(mechanism_db)) as conn:
        history = read_record(conn, "release", kind, record["id"], record["revision"])
    assert history["review"]["status"] == "approved"
    assert history["payload"] == record["payload"]
    if kind == "fact":
        assert public(mechanism_db)["status"] == "pending"
    elif status == "approved":
        # New current approved descriptor with still-current dependencies is valid.
        current = public(mechanism_db)
        assert current["status"] == "ready" and current["selection"]["revision"] == record["revision"] + 1


def test_unrelated_current_fact_does_not_invalidate_another_mechanism(mechanism_db):  # noqa: F811
    baseline = public(mechanism_db)
    second = public(mechanism_db, SWIRL)
    assert baseline["status"] == second["status"] == "ready"
    active_refs = set(baseline["descriptor"]["evidence_fact_ids"])
    other_ref = next(ref for ref in second["descriptor"]["evidence_fact_ids"] if ref not in active_refs)
    ident, revision = other_ref.rsplit("@", 1)
    seed = fixture_catalog(mechanism_db)
    record = next(row for row in seed["facts"] if row["id"] == ident and row["revision"] == int(revision))
    advance(mechanism_db, "fact", record, "revoked")
    assert public(mechanism_db) == baseline
    assert public(mechanism_db, SWIRL)["status"] == "pending"


def test_higher_historical_revision_is_not_implicitly_current(mechanism_db):  # noqa: F811
    baseline = public(mechanism_db)
    seed = fixture_catalog(mechanism_db)
    ident, revision = baseline["descriptor"]["evidence_fact_ids"][0].rsplit("@", 1)
    record = copy.deepcopy(next(row for row in seed["facts"] if row["id"] == ident and row["revision"] == int(revision)))
    record["revision"] += 4
    record["payload"]["revision"] += 4
    record["review"]["status"] = "revoked"
    assert import_bundle(mechanism_db, {"schema_version": 1, "id": "independent-history-only", "facts": [record],
                                      "descriptors": [], "heads": [{"record_kind": "fact", "record_id": ident,
                                          "revision": int(revision), "expected_head_revision": int(revision)}]},
                         dry_run=False)["applied"]
    assert public(mechanism_db) == baseline
