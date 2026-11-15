"""Synthetic SQLite source integration. No formal DB, network or original approvals."""
import copy
import hashlib
import sqlite3
from contextlib import closing

import pytest

from genshin_db import runtime_action_sources as module
from genshin_db.runtime_capacity_delta import source_fact_claim
from genshin_db.runtime_queries import _read, get_runtime_status, query_runtime
from genshin_db.runtime_talent_level import _recipe
from genshin_db.semantic_store import import_bundle, read_record, review_record
from genshin_runtime.mechanism_semantics import fingerprint, validate_record
from tests.action_resource_fixtures import (
    action_resource_synthetic_db_only,  # noqa: F401
    load_material,
)
from tests.test_pipeline import fake_repo  # noqa: F401
from tests.test_runtime_object_index import build, write

pytestmark = pytest.mark.usefixtures("action_resource_synthetic_db_only")

COMMIT = _recipe()["commit"]
SOURCE = "BinOutput/Talent/AvatarTalents/CapacityFixture.json"
CID, DID, SKILL = 90000001, 9001, 90012


@pytest.fixture
def source_db(tmp_path, fake_repo):  # noqa: F811
    write(fake_repo, "ExcelBinOutput/AvatarSkillDepotExcelConfigData.json", [{
        "id": DID, "skills": [90011, SKILL], "subSkills": [], "energySkill": 90013,
        "talents": list(range(9901, 9907)), "inherentProudSkillOpens": [],
        "specialProudSkillOpens": [], "extraAbilities": [], "leaderTalent": 0,
    }])
    write(fake_repo, "ExcelBinOutput/AvatarSkillExcelConfigData.json", [
        {"id": 90011 + i, "nameTextMapHash": 200, "descTextMapHash": 201,
         "proudSkillGroupId": 91001 + i, "maxChargeNum": 1} for i in range(3)])
    write(fake_repo, "ExcelBinOutput/AvatarTalentExcelConfigData.json", [
        {"talentId": 9900 + i, "openConfig": f"CapacityC{i}", "descTextMapHash": 301,
         "nameTextMapHash": 300, "paramList": []} for i in range(1, 7)])
    write(fake_repo, "ExcelBinOutput/ProudSkillExcelConfigData.json", [
        {"proudSkillId": group * 100 + 10, "proudSkillGroupId": group, "level": 10,
         "paramList": [1], "paramDescList": [201]} for group in range(91001, 91004)])
    ops = {f"CapacityC{i}": [] for i in range(1, 7)}
    ops["CapacityC1"] = [{"$type": "ModifySkillPoint", "skillID": SKILL,
                          "CLGIKOPIKBC": True, "pointDelta": 1}]
    write(fake_repo, SOURCE, ops)
    db = tmp_path / "synthetic-action.db"
    build(db, fake_repo, upstream_commit=COMMIT)
    return db


def fixture_record(db, status="approved"):
    """Rebind only engineering evidence to this synthetic source's actual identity."""
    old = load_material("pending-record-bundle.json")["facts"][0]
    record = copy.deepcopy(old)
    seed = {"payload": {"claim": {"source_claim": {"character_id": CID,
        "skill_depot_id": DID, "target_skill_id": SKILL, "constellation_rank": 1,
        "source_path": SOURCE, "operation_pointer": "/CapacityC1/0"}}}}
    with _read(db, "release") as (conn, state):
        inv, row, declaration = module.fresh_delta_material(conn, state, seed)
    c = record["payload"]["claim"]
    c["source_claim"] = source_fact_claim(inv, row, declaration)
    c["activation"] = {"kind": "constellation_slot", "rank": 1, "talent_id": 9901,
                       "depot_pointer": declaration["depot"]["json_pointer"] + "/talents/0"}
    c["prior_review"].update(original_fact_id="fixture.original", original_fact_sha256="e" * 64,
        source_claim_sha256=fingerprint(c["source_claim"]), decision_sha256="d" * 64, report_sha256="c" * 64,
        assumptions=["Synthetic fixture only, not a real source approval"])
    record["id"] = record["payload"]["id"] = "fixture.action.delta"
    record["subject"]["target_id"] = CID
    record["applicability"] = {"version_line": "release", "upstream_commit": COMMIT,
                               "normalization_fingerprint": fingerprint(inv["snapshot"]["normalization"])}
    evidence = []
    for name, node in [(k, declaration[k]) for k in ("avatar", "depot", "talent", "description")] + [("target", row["target_skill"])]:
        evidence.append({"id": name, "kind": "runtime_node", "locator": node["source_path"] + "#" + node["json_pointer"],
                         "sha256": node["node_sha256"]})
    evidence.append({"id": "operation", "kind": "runtime_node", "locator": SOURCE + "#/CapacityC1/0",
                     "sha256": row["operation_node_sha256"]})
    record["evidence"] = evidence
    record["payload"]["evidence_refs"] = [e["id"] for e in evidence]
    record["review"] = {"status": status, "reviewer": "synthetic-fixture" if status in ("approved", "revoked") else None,
                        "reviewed_at": "2026-10-07T00:00:00+00:00" if status in ("approved", "revoked") else None}
    validate_record(record, "fact")
    return record


def install(db, record, expected=None):
    return import_bundle(db, {"schema_version": 1, "id": "engineering-fixture", "facts": [record], "descriptors": [],
        "heads": [{"record_kind": "fact", "record_id": record["id"], "revision": record["revision"],
                   "expected_head_revision": expected}]}, dry_run=False)


def query(db, **extra):
    return query_runtime(db, {"action": "action_capacity_source_profile", "character_id": CID,
                             "constellation": 1, **extra})


def test_shared_real_sqlite_session_catalog(source_db):
    before = hashlib.sha256(source_db.read_bytes()).hexdigest()
    result = query_runtime(source_db, {"action": "action_activation_catalog", "character_id": CID, "constellation": 1})
    assert result["contract"] == "fresh-action-activation-catalog-v1"
    assert len([r for r in result["root_occurrences"] if r["kind"] == "constellation"]) == 6
    assert result["action_sources"]["capacity"] is None
    assert result["public_root_material"]["execution_authority"] is False
    assert hashlib.sha256(source_db.read_bytes()).hexdigest() == before


def test_complete_fresh_source_with_current_head(source_db):
    record = fixture_record(source_db)
    assert install(source_db, record)["applied"]
    before = hashlib.sha256(source_db.read_bytes()).hexdigest()
    result = query(source_db)
    item = result["sources"][0]
    assert item["status"] == "known_source_delta"
    assert item["projection"]["amount"] == 1
    assert item["projection"]["candidate_projected_capacity"] == 2
    assert result["capacity"] is None and result["execution_authority"] is False
    assert item["review"]["revision"] == 1
    assert item["projection"]["verification_scope"] == "fresh_source_and_current_review_head"
    assert hashlib.sha256(source_db.read_bytes()).hexdigest() == before


def test_same_revision_revoke_blocks_next_query(source_db):
    record = fixture_record(source_db)
    install(source_db, record)
    assert query(source_db)["sources"][0]["projection"]["amount"] == 1
    with closing(sqlite3.connect(source_db)) as conn:
        stored = read_record(conn, "release", "fact", record["id"], 1)
    review_record(source_db, version_line="release", record_kind="fact", record_id=record["id"], revision=1,
                  status="revoked", reviewer="synthetic-revoker", reviewed_at="2026-10-07T01:00:00+00:00",
                  expected_review_fingerprint=stored["review_fingerprint"])
    result = query(source_db)["sources"][0]
    assert result["reason"] == "review_not_approved" and result["projection"] is None


def test_old_approved_revision_cannot_bypass_new_pending_head(source_db):
    old = fixture_record(source_db)
    install(source_db, old)
    new = copy.deepcopy(old)
    new["revision"] = new["payload"]["revision"] = 2
    new["review"] = {"status": "pending", "reviewer": None, "reviewed_at": None}
    install(source_db, new, expected=1)
    result = query(source_db, fact_refs=[{"id": old["id"], "revision": 1}])["sources"][0]
    assert result["reason"] == "head_changed" and result["projection"] is None


def test_batch_and_single_share_actual_connection(source_db, monkeypatch):
    install(source_db, fixture_record(source_db))
    seen = []
    original = module.read_current_reviewed_record

    def observe(conn, *args, **kwargs):
        seen.append(getattr(conn, "connection", conn))
        assert conn.in_transaction
        return original(conn, *args, **kwargs)

    monkeypatch.setattr(module, "read_current_reviewed_record", observe)
    result = query_runtime(source_db, {"action": "batch", "items": [
        {"id": str(i), "request": {"action": "action_capacity_source_profile", "character_id": CID,
                                   "constellation": i}} for i in (0, 1)]})
    assert result["status"] == "complete"
    assert len(seen) == 2 and seen[0] is seen[1]
    assert [x["data"]["sources"][0]["projection"]["amount"] for x in result["items"]] == [0, 1]


@pytest.mark.parametrize("status", ["pending", "revoked"])
def test_unapproved_current_head_does_not_load_source_material(source_db, monkeypatch, status):
    install(source_db, fixture_record(source_db, status))
    monkeypatch.setattr(module, "fresh_delta_material", lambda *_: pytest.fail("unapproved material loaded"))
    item = query(source_db)["sources"][0]
    assert item["reason"] == "review_not_approved" and item["projection"] is None


def test_snapshot_change_does_not_reuse_source_delta(source_db):
    record = fixture_record(source_db)
    c = record["payload"]["claim"]
    c["source_claim"]["snapshot"]["snapshot_id"] = "0" * 64
    c["prior_review"]["source_claim_sha256"] = fingerprint(c["source_claim"])
    install(source_db, record)
    out = query(source_db)["sources"][0]
    assert out["reason"] == "source_snapshot_changed" and out["projection"] is None


def test_inactive_source_still_requires_exact_original_material(source_db, monkeypatch):
    record = fixture_record(source_db)
    install(source_db, record)
    original = module.fresh_delta_material

    def changed(*args):
        inv, row, dec = original(*args)
        inv["snapshot"] = copy.deepcopy(inv["snapshot"])
        inv["snapshot"]["built_at"] = "different-build"
        return inv, row, dec

    monkeypatch.setattr(module, "fresh_delta_material", changed)
    with pytest.raises(module.CapacityError, match="原审核命题"):
        query(source_db, constellation=0)


def test_separate_sqlite_handle_cannot_claim_existing_shared_session(source_db):
    from genshin_db.runtime_action_activation import collect_activation_catalog
    with _read(source_db, "release") as (_, state), closing(sqlite3.connect(source_db)) as other:
        other.execute("BEGIN")
        with pytest.raises(module.CapacityError, match="同一共享"):
            collect_activation_catalog(other, state, db_path=source_db, character_id=CID, constellation=1)


@pytest.mark.parametrize("change", ["activation", "evidence", "target_fingerprint", "parent_fingerprint"])
def test_current_approved_fact_does_not_override_changed_evidence(source_db, change):
    record = fixture_record(source_db)
    c = record["payload"]["claim"]
    if change == "activation":
        c["activation"]["talent_id"] = 9902
    elif change == "evidence":
        record["evidence"][0]["sha256"] = "0" * 64
    else:
        key = "target_skill_fingerprint" if change == "target_fingerprint" else "parent_canonical_sha256"
        c["source_claim"][key] = "0" * 64
        c["prior_review"]["source_claim_sha256"] = fingerprint(c["source_claim"])
    install(source_db, record)
    with pytest.raises(module.CapacityError):
        query(source_db)


@pytest.mark.parametrize("extra", [{"constellation": True}, {"character_id": 1.0},
                                  {"fact_refs": [{"id": "", "revision": 1}]},
                                  {"fact_refs": [{"id": "ok", "revision": True}]}])
def test_invalid_public_selection_rejected_before_database_read(extra, monkeypatch, tmp_path):
    monkeypatch.setattr(module, "_read", lambda *_: pytest.fail("invalid input opened database"))
    with pytest.raises(ValueError):
        module.get_action_source_profile(tmp_path / "must-not-open.db", **{"character_id": CID, "constellation": 1, **extra})


def test_result_is_detached_and_missing_semantics_does_not_install(source_db):
    before = hashlib.sha256(source_db.read_bytes()).hexdigest()
    out = query(source_db)
    assert out["sources"] == [] and out["capacity"] is None
    out["snapshot"].clear()
    assert query(source_db)["snapshot"]["snapshot_id"] == get_runtime_status(source_db)["snapshot_id"]
    assert hashlib.sha256(source_db.read_bytes()).hexdigest() == before
