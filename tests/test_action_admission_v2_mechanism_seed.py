"""Independent Noblesse mechanism identity for successful_action_v2."""
from __future__ import annotations

import copy
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from genshin_db.runtime_queries import query_runtime
from genshin_db.semantic_store import import_bundle, read_record
from genshin_runtime.mechanism_semantics import validate_record
from scripts.build_action_admission_v2_mechanism_bundle import (
    NEW_ID,
    QUALIFICATION_ID,
    TARGET,
    build,
)
from tests.mechanism_cast_source_fixtures import (  # noqa: F401
    cast_repo,
    fake_repo,
    full_cast_db,
    mechanism_repo,
)

# ruff: noqa: F811


OLD_ID = "artifact.burst-team-atk-percent"


def _seed() -> dict:
    return json.loads(Path(TARGET).read_text("utf-8"))


def _profile(db, mechanism_id):
    return query_runtime(db, {"action": "mechanism_profile", "mechanism_id": mechanism_id})


def _approved_fixture_seed(db) -> dict:
    """Bind v5 to the isolated fixture's exact reviewed v1 source material."""
    seed = copy.deepcopy(_seed())
    with closing(sqlite3.connect(db)) as conn:
        previous_descriptor = read_record(conn, "release", "descriptor", OLD_ID)
        previous_qualification = read_record(
            conn, "release", "fact", OLD_ID + ".qualification"
        )
    assert previous_descriptor.get("review", {}).get("status") == "approved"
    assert previous_qualification.get("review", {}).get("status") == "approved"

    qualification = seed["facts"][0]
    descriptor = seed["descriptors"][0]
    qualification["evidence"] = copy.deepcopy(previous_qualification["evidence"])
    qualification["applicability"] = copy.deepcopy(
        previous_qualification["applicability"]
    )
    descriptor["evidence"] = copy.deepcopy(previous_descriptor["evidence"])
    descriptor["applicability"] = copy.deepcopy(previous_descriptor["applicability"])
    descriptor["payload"]["source_binding"] = copy.deepcopy(
        previous_descriptor["payload"]["source_binding"]
    )
    for row in [qualification, descriptor]:
        row["review"] = {
            "status": "approved",
            "reviewer": "action-admission-v2-isolated-test",
            "reviewed_at": "2026-10-15T00:00:00+00:00",
        }
    return seed


def test_seed_is_deterministic_pending_and_independent_from_v1():
    seed = _seed()
    assert seed == build()
    assert seed["id"] == "mechanisms.v5.action-admission-v2"
    assert [row["id"] for row in seed["facts"]] == [QUALIFICATION_ID]
    assert [row["id"] for row in seed["descriptors"]] == [NEW_ID]
    for kind, rows in (("fact", seed["facts"]), ("descriptor", seed["descriptors"])):
        for row in rows:
            assert validate_record(row, kind) == row
            assert row["review"] == {"status": "pending", "reviewer": None, "reviewed_at": None}
    qualification = seed["facts"][0]["payload"]["claim"]["parent"]
    assert qualification["profile_kind"] == "successful_action_v2"
    assert qualification["required_receipts"] == ["cast_identity", "action_admission"]
    descriptor = seed["descriptors"][0]["payload"]["descriptor"]
    assert descriptor["mechanism_id"] == NEW_ID
    assert OLD_ID + ".qualification@1" not in descriptor["evidence_fact_ids"]
    assert QUALIFICATION_ID + "@1" in descriptor["evidence_fact_ids"]


def test_approved_v2_profile_materializes_without_changing_v1_head(full_cast_db):
    before = _profile(full_cast_db, OLD_ID)
    assert before["status"] == "ready"
    old_parent = before["compiler_result"]["execution_qualification"]["parent"]
    assert old_parent["profile_kind"] == "successful_action_v1"
    assert old_parent["required_receipts"] == ["cast_identity"]

    seed = _approved_fixture_seed(full_cast_db)
    applied = import_bundle(full_cast_db, seed, dry_run=False)
    assert applied["applied"] is True

    after = _profile(full_cast_db, OLD_ID)
    current = _profile(full_cast_db, NEW_ID)
    assert after["selection"] == before["selection"]
    assert after["compiler_result"]["execution_qualification"]["parent"]["profile_kind"] == "successful_action_v1"
    assert current["status"] == "ready", json.dumps(current, ensure_ascii=False, indent=2)
    parent = current["compiler_result"]["execution_qualification"]["parent"]
    assert parent["profile_kind"] == "successful_action_v2"
    assert parent["required_receipts"] == ["cast_identity", "action_admission"]
