"""Explicit r1 -> r2 qualification migration, with no approval inheritance."""
import json
import sqlite3
from contextlib import closing

import pytest

from genshin_db.runtime_queries import query_runtime
from genshin_db.semantic_store import (
    import_bundle,
    read_record,
    review_record,
    semantic_digest,
)
from genshin_runtime.mechanism_semantics import validate_record
from tests.mechanism_source_fixtures import (  # noqa: F401
    ROOT,
    fake_repo,
    fixture_qualified_catalog,
    mechanism_db,
    mechanism_repo,
)
from tests.test_semantic_store import semantic_db  # noqa: F401

# ruff: noqa: F811
IDS = ("skill.current-atk-limited-addition", "passive.swirl-elemental-bonus")


def source_seed(version):
    return json.loads((ROOT / f"src/genshin_db/resources/mechanisms/reviewed-mechanisms-v{version}.json").read_text("utf8"))


def record(db, kind, ident, revision=None):
    with closing(sqlite3.connect(db)) as conn:
        return read_record(conn, "release", kind, ident, revision)


def query(db, ident, revision=None):
    request = {"action": "mechanism_profile", "mechanism_id": ident}
    if revision is not None:
        request["revision"] = revision
    return query_runtime(db, request)


def test_packaged_migration_is_pending_and_preserves_all_existing_source_rules():
    old, new = source_seed(1), source_seed(2)
    assert len(new["facts"]) == len(new["descriptors"]) == 2 and len(new["heads"]) == 4
    for kind, records in (("fact", new["facts"]), ("descriptor", new["descriptors"])):
        for row in records:
            assert validate_record(row, kind) == row
            assert row["review"] == {"status": "pending", "reviewer": None, "reviewed_at": None}
    for before, after in zip(old["descriptors"], new["descriptors"], strict=True):
        assert before["id"] == after["id"] and before["revision"] == 1 and after["revision"] == 2
        assert before["payload"]["source_binding"] == after["payload"]["source_binding"]
        left, right = before["payload"]["descriptor"], after["payload"]["descriptor"]
        assert left["rule_templates"] == right["rule_templates"]
        assert right["evidence_fact_ids"] == left["evidence_fact_ids"] + [after["id"] + ".qualification@1"]
    claims = {r["id"]: r["payload"]["claim"] for r in new["facts"]}
    first, second = (claims[i + ".qualification"] for i in IDS)
    assert first["parent"]["parameter_binding"] == {"node_id": "proud", "pointer": "/paramList/1", "param_index": 1}
    assert second["parent"]["parameter_binding"] is None
    assert second["parent"]["selection"] == {"slot": "skill", "constellation": 0}
    assert second["parent"]["required_receipts"] == ["action_event", "attack_metadata"]
    assert second["coverage"] == {"action_filter": None, "application_elements": ["anemo"],
                                  "reject_unknown_application": True}
    assert second["consumer"] is None


def test_pending_migration_never_inherits_old_approval_or_changes_r1(mechanism_db):
    old = {i: record(mechanism_db, "descriptor", i, 1) for i in IDS}
    bundle = fixture_qualified_catalog(mechanism_db, approve=False)
    assert import_bundle(mechanism_db, bundle, dry_run=False)["applied"]
    for ident in IDS:
        assert record(mechanism_db, "descriptor", ident, 1) == old[ident]
        assert record(mechanism_db, "descriptor", ident)["review"]["status"] == "pending"
        current = query(mechanism_db, ident)
        assert current["status"] == "pending" and current["compiler_result"] is None
        previous = query(mechanism_db, ident, 1)
        assert previous["status"] == "pending" and previous["compiler_result"] is None
        assert previous["issues"][0]["reason"] == "head_changed"
        assert record(mechanism_db, "descriptor", ident, 1) == old[ident]


def test_reviewed_fixture_qualification_keeps_same_templates_and_exact_scalar(mechanism_db):
    before = {i: query(mechanism_db, i) for i in IDS}
    assert import_bundle(mechanism_db, fixture_qualified_catalog(mechanism_db), dry_run=False)["applied"]
    for ident in IDS:
        result = query(mechanism_db, ident)
        compiled = result["compiler_result"]
        assert result["status"] == "ready", (result["issues"], compiled)
        assert compiled["qualification_status"] == "consistent"
        assert compiled["execution_status"] == "not_evaluated"
        assert compiled["rule_templates"] == before[ident]["compiler_result"]["rule_templates"]
        assert result["semantic_fingerprint"] != before[ident]["semantic_fingerprint"]
        assert compiled["execution_qualification"]["parent"]["hit"]["damage_type_ids"] == ["damage.elemental_skill"]
    limited = query(mechanism_db, IDS[0])
    proud = next(node for node in limited["source_proof"]["nodes"] if node["id"] == "proud")
    assert proud["payload"]["paramList"][1] == pytest.approx(3.398400068283081)


def test_revoked_qualification_cannot_be_reapproved_by_migration_reimport(mechanism_db):
    data = fixture_qualified_catalog(mechanism_db)
    import_bundle(mechanism_db, data, dry_run=False)
    ident = IDS[0] + ".qualification"
    prior = record(mechanism_db, "fact", ident)
    review_record(mechanism_db, version_line="release", record_kind="fact", record_id=ident, revision=1,
                  status="revoked", reviewer="fixture-revocation", reviewed_at="2026-10-03",
                  expected_review_fingerprint=prior["review_fingerprint"])
    result = query(mechanism_db, IDS[0])
    assert result["status"] == "pending" and result["compiler_result"] is None
    assert import_bundle(mechanism_db, data, dry_run=False)["status"] == "conflict_preserved"
    assert record(mechanism_db, "fact", ident)["review"]["status"] == "revoked"


def test_stale_descriptor_head_prevents_partial_qualification_install(mechanism_db):
    data = fixture_qualified_catalog(mechanism_db)
    import_bundle(mechanism_db, {"schema_version": 1, "id": "test.remove-head", "facts": [], "descriptors": [],
        "heads": [{"record_kind": "descriptor", "record_id": IDS[0], "revision": None, "expected_head_revision": 1}]},
        dry_run=False)
    with closing(sqlite3.connect(mechanism_db)) as conn:
        before = semantic_digest(conn)
    assert import_bundle(mechanism_db, data, dry_run=False)["status"] == "conflict_preserved"
    with closing(sqlite3.connect(mechanism_db)) as conn:
        assert semantic_digest(conn) == before
    assert record(mechanism_db, "fact", IDS[0] + ".qualification", 1)["status"] == "missing"


def test_incremental_seed_requires_existing_r1_facts_without_partial_schema(semantic_db):
    before = semantic_db.read_bytes()
    with pytest.raises(ValueError, match="事实修订"):
        import_bundle(semantic_db, source_seed(2), dry_run=False)
    assert semantic_db.read_bytes() == before
