"""Real-node pending migration and fresh query tests, on a tiny isolated DB."""
import copy
import hashlib
import sqlite3
from contextlib import closing

from genshin_db.runtime_queries import query_runtime
from genshin_db.semantic_store import (
    import_bundle,
    read_record,
    review_record,
    semantic_digest,
)
from genshin_runtime.mechanism_semantics import validate_record
from tests.mechanism_cast_source_fixtures import (  # noqa: F401
    IDS,
    ROOT,
    cast_db,
    cast_repo,
    cast_seed,
    fake_repo,
    fixture_cast_catalog,
    full_cast_db,
    mechanism_repo,
)

# ruff: noqa: F811


def record(db, kind, ident, revision=None):
    with closing(sqlite3.connect(db)) as conn:
        return read_record(conn, "release", kind, ident, revision)


def query(db, ident, revision=None):
    request = {"action": "mechanism_profile", "mechanism_id": ident}
    if revision is not None:
        request["revision"] = revision
    return query_runtime(db, request)


def test_increment_is_pending_and_old_seed_bytes_do_not_change():
    seed = cast_seed()
    assert len(seed["facts"]) == 6 and len(seed["descriptors"]) == 2 and len(seed["heads"]) == 8
    for kind, rows in (("fact", seed["facts"]), ("descriptor", seed["descriptors"])):
        for r in rows:
            assert validate_record(r, kind) == r
            assert r["review"] == {"status": "pending", "reviewer": None, "reviewed_at": None}
    expected = ["08a9730882c20ea0fd659412524679b2b437c56fd3817bc2df7ee8f61e31a4a6", "8b1b250d1c41be2b594669b182ecce78a2ddd7741ff434473eb378fcbc7ba40d"]
    for version, sha in enumerate(expected, 1):
        assert hashlib.sha256((ROOT / f"src/genshin_db/resources/mechanisms/reviewed-mechanisms-v{version}.json").read_bytes()).hexdigest() == sha
    gear = seed["facts"][-1]["payload"]["claim"]
    assert gear["scenario_requirements"]["no_source_state_changes"] is True
    assert gear["provider"]["requirement_coverage"] == "complete_selected_effect_reference"


def test_default_dry_run_and_pending_migration_do_not_inherit_approval(cast_db):
    before = cast_db.read_bytes()
    seed = fixture_cast_catalog(cast_db)
    assert import_bundle(cast_db, seed)["applied"] is False
    assert cast_db.read_bytes() == before
    old = record(cast_db, "descriptor", IDS[0], 2)
    assert import_bundle(cast_db, seed, dry_run=False)["applied"]
    assert record(cast_db, "descriptor", IDS[0], 2) == old
    for ident in IDS:
        out = query(cast_db, ident)
        assert out["status"] == "pending"
        assert out["compiler_result"] is None and out["source_proof"] is None
        assert out.get("authorization_fingerprint") is None
    previous = query(cast_db, IDS[0], 2)
    assert previous["status"] == "pending" and previous["compiler_result"] is None
    assert previous["issues"][0]["reason"] == "head_changed"
    assert record(cast_db, "descriptor", IDS[0], 2) == old


def test_two_reviewed_real_sources_freshly_materialize_cast_with_exact_parameters(cast_db):
    assert import_bundle(cast_db, fixture_cast_catalog(cast_db, approve=True), dry_run=False)["applied"]
    first, second = [query(cast_db, ident) for ident in IDS]
    for out in (first, second):
        assert out["status"] == "ready", out
        assert out["compiler_result"]["compiler"] == "typed-mechanism-d20-v2"
        assert out["execution_support"] == "not_evaluated"
    left, right = [out["compiler_result"]["rule_templates"][0]["rule"] for out in (first, second)]
    assert left["timing"]["duration"] == 15 and left["quota"]["capacity"] == 7
    assert left["effects"][0]["value"]["factor"] == .8218079805374146
    assert right["timing"]["duration"] == 12 and right["effects"][0]["value"] == .20000000298023224
    assert "quota" not in right and right["effects"][0]["domain"] == "self"
    assert first["compiler_result"]["execution_qualification"]["parent"]["required_receipts"] == ["cast_identity"]
    assert second["compiler_result"]["execution_qualification"]["target_dependency"] == {"mode": "none"}


def test_stale_head_cas_withdraws_entire_increment(cast_db):
    seed = fixture_cast_catalog(cast_db)
    seed["heads"][-1]["expected_head_revision"] = 2
    with closing(sqlite3.connect(cast_db)) as conn:
        before = semantic_digest(conn)
    assert import_bundle(cast_db, seed, dry_run=False)["status"] == "conflict_preserved"
    with closing(sqlite3.connect(cast_db)) as conn:
        assert semantic_digest(conn) == before


def test_revocation_is_not_repaired_by_seed_reimport(cast_db):
    seed = fixture_cast_catalog(cast_db, approve=True)
    assert import_bundle(cast_db, seed, dry_run=False)["applied"]
    ident = IDS[1] + ".qualification"
    saved = record(cast_db, "fact", ident)
    review_record(cast_db, version_line="release", record_kind="fact", record_id=ident, revision=1,
                  status="revoked", reviewer="isolated-test", reviewed_at="2026-10-03", expected_review_fingerprint=saved["review_fingerprint"])
    assert import_bundle(cast_db, seed, dry_run=False)["status"] == "conflict_preserved"
    assert record(cast_db, "fact", ident)["review"]["status"] == "revoked"
    assert query(cast_db, IDS[1])["status"] == "pending"


def test_reviewed_coverage_and_state_assumption_are_payload_not_display(cast_db):
    seed = fixture_cast_catalog(cast_db, approve=True)
    assert import_bundle(cast_db, seed, dry_run=False)["applied"]
    changed = copy.deepcopy(seed)
    changed["facts"][-1]["payload"]["claim"]["scenario_requirements"].pop("no_source_state_changes")
    assert import_bundle(cast_db, changed, dry_run=False)["status"] == "conflict_preserved"


def test_full_small_db_has_real_character_and_equipment_public_data(full_cast_db):
    from genshin_db.queries import get_artifact_set_detail, get_character_detail
    character = get_character_detail(full_cast_db, 10000063)
    assert character["character"]["id"] == 10000063
    gear = get_artifact_set_detail(full_cast_db, 15007)
    assert gear["artifact_set"]["id"] == 15007
    assert query(full_cast_db, IDS[0])["status"] == "ready"
    assert query(full_cast_db, IDS[1])["status"] == "ready"
