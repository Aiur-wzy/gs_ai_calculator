"""Fresh query integration with two heterogeneous reviewed-data fixtures."""
import sqlite3
from contextlib import closing

import pytest

from genshin_db.equipment_queries import equipment_read_session
from genshin_db.runtime_catalog import RuntimeConfigError
from genshin_db.runtime_queries import query_runtime
from genshin_db.semantic_store import import_bundle, read_record, review_record
from tests.mechanism_source_fixtures import (  # noqa: F401
    fake_repo,
    fixture_catalog,
    mechanism_db,
    mechanism_repo,
)

ADDITION = "skill.current-atk-limited-addition"
# ruff: noqa: F811
SWIRL = "passive.swirl-elemental-bonus"


def query(db, ident=ADDITION):
    return query_runtime(db, {"action": "mechanism_profile", "mechanism_id": ident})


def test_two_real_data_shapes_use_one_compiler(mechanism_db):
    first, second = query(mechanism_db), query(mechanism_db, SWIRL)
    assert first["status"] == second["status"] == "ready", (first["issues"], second["issues"], first["compiler_result"], second["compiler_result"])
    assert first["execution_support"] == second["execution_support"] == "not_evaluated"
    a, b = first["compiler_result"], second["compiler_result"]
    assert a["compiler"] == b["compiler"]
    rule = a["rule_templates"][0]["rule"]
    assert rule["quota"]["capacity"] == 7 and rule["timing"]["duration"] == 15
    assert rule["effects"][0]["value"]["factor"] == pytest.approx(.8218079805374146)
    assert rule["effects"][0]["domain"] == "provider"
    assert len(b["rule_templates"]) == 2
    assert all("quota" not in row["rule"] and row["rule"]["timing"]["duration"] == 8 for row in b["rule_templates"])
    assert all(row["rule"]["effects"][0]["value"]["sample"] == "trigger" for row in b["rule_templates"])


def test_catalog_discovers_from_selected_subject(mechanism_db):
    catalog = query_runtime(mechanism_db, {"action": "mechanism_catalog", "target_type": "character", "target_id": 10000047})
    assert [row["id"] for row in catalog["items"]] == [SWIRL]
    assert catalog["execution_support"] == "not_evaluated"
    assert query_runtime(mechanism_db, {"action": "mechanism_catalog", "page_size": 1})["total"] == 2


def test_batch_and_single_read_transactions_agree(mechanism_db):
    with equipment_read_session(mechanism_db):
        expected = [query(mechanism_db, key) for key in (ADDITION, SWIRL)]
        actual = query_runtime(mechanism_db, {"action": "batch", "items": [
            {"id": str(i), "request": {"action": "mechanism_profile", "mechanism_id": key}}
            for i, key in enumerate((ADDITION, SWIRL))]})
        assert actual["status"] == "complete"
        assert [row["data"] for row in actual["items"]] == expected


def test_revoked_fact_blocks_next_fresh_read_and_reimport(mechanism_db):
    previous = query(mechanism_db)
    ref = previous["descriptor"]["evidence_fact_ids"][0]
    ident, rev = ref.rsplit("@", 1)
    with closing(sqlite3.connect(mechanism_db)) as conn:
        record = read_record(conn, "release", "fact", ident, int(rev))
    seed = fixture_catalog(mechanism_db)
    review_record(mechanism_db, version_line="release", record_kind="fact", record_id=ident, revision=int(rev),
                  status="revoked", reviewer="fixture-revoker", reviewed_at="2026-10-03T01:00:00+00:00",
                  expected_review_fingerprint=record["review_fingerprint"])
    current = query(mechanism_db)
    assert previous["status"] == "ready" and current["status"] == "pending"
    assert current["compiler_result"] is None
    assert current["source_proof"] is None
    assert current.get("authorization_fingerprint") is None
    assert previous["authorization_fingerprint"] is not None
    assert import_bundle(mechanism_db, seed, dry_run=False)["status"] == "conflict_preserved"


def test_read_returns_detached_values(mechanism_db):
    with equipment_read_session(mechanism_db):
        result = query(mechanism_db)
        result["source_proof"]["nodes"][0]["payload"].clear()
        result["facts"][0]["claim"].clear()
        assert query(mechanism_db)["status"] == "ready"


def test_stored_tamper_not_just_materialized_approved(mechanism_db):
    with closing(sqlite3.connect(mechanism_db)) as conn, conn:
        conn.execute("UPDATE mechanic_descriptor SET status='revoked' WHERE record_id=?", (ADDITION,))
    with pytest.raises(RuntimeConfigError) as error:
        query(mechanism_db)
    assert error.value.reason == "semantic_dataset_invalid"


def test_missing_record_does_not_guess_other_head(mechanism_db):
    out = query(mechanism_db, "absent")
    assert out["source_status"] == "missing" and out["compiler_result"] is None


@pytest.mark.parametrize("query_request", [
    {"action": "mechanism_catalog", "target_type": "character"},
    {"action": "mechanism_catalog", "page": True},
    {"action": "mechanism_profile", "mechanism_id": ADDITION, "revision": True},
    {"action": "mechanism_profile", "mechanism_id": "../foreign"},
])
def test_bad_query_before_opening_database(tmp_path, query_request):
    with pytest.raises(ValueError):
        query_runtime(tmp_path / "absent.db", query_request)
