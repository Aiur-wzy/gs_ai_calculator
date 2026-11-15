"""Independent bounded object-query tests; no implementation-generated oracles."""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from genshin_db import runtime_catalog as catalog
from genshin_db import runtime_gadget_source as gadget
from genshin_db import runtime_queries, runtime_store
from genshin_db.equipment_queries import equipment_read_session
from tests.test_runtime_object_index_review import (  # noqa: F401
    ABILITY_SOURCE,
    GADGET_SOURCE,
    MAX_ID,
    MOUNT,
    NAME,
    build_index,
    fake_repo,
    object_db,
    object_repo,
    origin_refs,
    sql_rows,
    write_source,
)

# Fixture imports are registered by pytest.
# ruff: noqa: F811


def query(db, ident=101, **fields):
    return runtime_queries.query_runtime(db, {"action": "gadget_source_profile", "gadget_id": ident, **fields})


def rebuild(db, repo):
    return runtime_store.build_runtime_layer(db, repo, version_line="release", upstream_commit="review-fixed-commit")


def test_review_identity_ready_never_claims_mount_internal_closure_or_creation(object_db, object_repo):
    before = hashlib.sha256(object_db.read_bytes()).hexdigest()
    value = query(object_db)
    assert value["status"] == "ready"
    assert value["execution"] == {"status": "not_evaluated", "creation": "not_evaluated", "controller": "not_evaluated"}
    assert value["coverage"]["recursive_ability_execution"] == "not_evaluated"
    assert value["origin"]["status"] == "not_requested"
    assert value["record"]["record"] == {"id": 101, "jsonName": NAME, "campID": 7}
    assert len(value["mounted_abilities"]) == 1
    mount = value["mounted_abilities"][0]["selected"]
    assert mount["payload"]["onAdded"][0]["abilityName"] == "Missing_Inside_Mount"
    assert not any(source["definition"]["exact_key"] == "Review_Sibling_Only" for source in value["sources"])
    for node in [value["record"], value["config"]["selected"], mount]:
        source_bytes = (object_repo / node["source_path"]).read_bytes()
        assert node["source_sha256"] == hashlib.sha256(source_bytes).hexdigest()
        original = catalog.resolve_pointer(json.loads(source_bytes), node["json_pointer"])
        assert original == node.get("record", node.get("payload"))
        assert node["node_sha256"] == catalog.fingerprint(original)
    assert hashlib.sha256(object_db.read_bytes()).hexdigest() == before


def test_review_same_parent_two_predicates_select_distinct_origin_fingerprints(object_db):
    refs = [r for r in origin_refs(object_db) if r["json_pointer"] in (
        "/0/Default/onAdded/0/gadgetID", "/0/Default/onAdded/1/gadgetID")]
    first, second = [query(object_db, origin_reference_id=ref["reference_id"]) for ref in refs]
    assert first["origin"]["parent"] == second["origin"]["parent"]
    assert [v["origin"]["operation"]["raw"]["predicate"] for v in (first, second)] == ["branch-one", "branch-two"]
    assert first["source_fingerprint"] != second["source_fingerprint"]
    assert first["origin"]["object_reference"]["reference_id"] != second["origin"]["object_reference"]["reference_id"]


def test_review_modifier_origin_preserves_full_owner_without_following_other_edges(object_db):
    ref = next(r for r in origin_refs(object_db) if r["json_pointer"].endswith("/bulletID"))
    result = query(object_db, origin_reference_id=ref["reference_id"])
    origin = result["origin"]
    assert origin["status"] == "verified_relationship"
    assert origin["parent"]["definition"]["definition_kind"] == "modifier"
    owner = origin["owner_ability"]
    assert owner["payload"]["unknown_parent"] == {"must_keep": False}
    assert owner["payload"]["modifiers"]["Review_Modifier"] == origin["parent"]["payload"]
    assert origin["operation"]["raw"] == {"$type": "TriggerBullet", "bulletID": 101, "unknown_delay": 9}
    assert origin["parent"]["definition"]["owner_definition_id"] == owner["definition"]["definition_id"]
    assert not any(source["definition"]["exact_key"] == "Review_Sibling_Only" for source in result["sources"])


def test_review_same_json_name_does_not_erase_distinct_excel_identity(object_db):
    first, second = query(object_db, 101), query(object_db, 102)
    assert first["config"]["selected"] == second["config"]["selected"]
    assert first["config"]["reference"]["from_definition_id"] != second["config"]["reference"]["from_definition_id"]
    assert first["record"]["record"]["campID"] == 7
    assert second["record"]["record"]["campID"] == 8
    assert first["source_fingerprint"] != second["source_fingerprint"]


@pytest.mark.parametrize("ident", [True, False, 0, -1, 101.0, "101", [], MAX_ID + 1])
def test_review_invalid_id_rejected_before_any_database_read(tmp_path, monkeypatch, ident):
    monkeypatch.setattr(runtime_queries, "_read", lambda *a, **kw: pytest.fail("invalid selection touched database"))
    with pytest.raises(ValueError):
        query(tmp_path / "does-not-exist.db", ident)


def test_review_maximum_public_id_is_buildable_and_queryable(object_repo, tmp_path, monkeypatch):
    write_source(object_repo, catalog.GADGET_EXCEL_PATH, [{"id": MAX_ID, "jsonName": NAME}])
    db = build_index(object_repo, tmp_path / "large-id.db", monkeypatch)
    assert query(db, MAX_ID)["record"]["record"]["id"] == MAX_ID


def test_review_origin_wrong_target_or_particle_does_not_fabricate_ownership(object_db):
    refs = origin_refs(object_db)
    create = next(r for r in refs if r["json_pointer"].endswith("/gadgetID"))
    with pytest.raises(ValueError):
        query(object_db, 102, origin_reference_id=create["reference_id"])
    particle = next(r for r in refs if r["json_pointer"].endswith("/configID"))
    with pytest.raises(catalog.RuntimeConfigError) as error:
        query(object_db, origin_reference_id=particle["reference_id"])
    assert error.value.reason == "unsupported_source"


@pytest.mark.parametrize("origin", ["", "f" * 63, "G" * 64, [], 1])
def test_review_invalid_origin_identity_precedes_database_read(tmp_path, monkeypatch, origin):
    monkeypatch.setattr(runtime_queries, "_read", lambda *a, **kw: pytest.fail("invalid origin touched database"))
    with pytest.raises(ValueError):
        query(tmp_path / "absent.db", origin_reference_id=origin)


def test_review_two_config_candidates_preserved_without_path_or_case_guess(object_db, object_repo):
    write_source(object_repo, "BinOutput/Gadget/SecondReview.json", {NAME: {"abilities": []}, NAME.upper(): {"abilities": []}})
    rebuild(object_db, object_repo)
    result = query(object_db)
    assert result["status"] == "pending" and result["config"]["status"] == "ambiguous"
    assert result["config"]["selected"] is None
    assert len(result["config"]["candidates"]) == 2
    assert {candidate["definition"]["exact_key"] for candidate in result["config"]["candidates"]} == {NAME}


def test_review_mounted_ability_variants_are_ambiguous_and_not_default_preferred(object_db, object_repo):
    write_source(object_repo, "BinOutput/Ability/Temp/OtherReview.json", [{"Alternate": {"abilityName": MOUNT, "different": True}}])
    rebuild(object_db, object_repo)
    result = query(object_db)
    mount = result["mounted_abilities"][0]
    assert result["status"] == "pending" and mount["status"] == "ambiguous"
    assert mount["selected"] is None and len(mount["candidates"]) == 2


@pytest.mark.parametrize("payload", [{}, {"abilities": None}, {"abilities": {}}, {"abilities": [None]}, {"abilities": [{}]}, {"abilities": [{"abilityName": ""}]}, {"abilities": [{"abilityName": False}]}])
def test_review_incomplete_or_invalid_mounts_remain_pending_with_raw_evidence(object_db, object_repo, payload):
    write_source(object_repo, GADGET_SOURCE, {NAME: payload})
    rebuild(object_db, object_repo)
    result = query(object_db)
    assert result["status"] == "pending" and result["issues"]
    assert result["config"]["selected"]["payload"] == payload
    assert result["execution_support"] == "not_evaluated"


def test_review_explicit_empty_mounts_are_evidence_not_assumed_game_execution(object_db, object_repo):
    write_source(object_repo, GADGET_SOURCE, {NAME: {"abilities": []}})
    rebuild(object_db, object_repo)
    result = query(object_db)
    assert result["status"] == "ready" and result["mounted_abilities"] == []
    assert result["config"]["selected"]["payload"] == {"abilities": []}
    assert result["execution_support"] == "not_evaluated"


@pytest.mark.parametrize("payload,presence,status", [
    ({}, "absent", "pending"), ({"abilities": None}, "null", "pending"),
    ({"abilities": []}, "empty", "ready"),
    ({"abilities": [{"abilityName": MOUNT}]}, "present", "ready"),
    ({"abilities": False}, "invalid_type", "pending"),
])
def test_review_raw_mount_field_states_are_not_coerced_together(object_db, object_repo, payload, presence, status):
    write_source(object_repo, GADGET_SOURCE, {NAME: payload})
    rebuild(object_db, object_repo)
    result = query(object_db)
    assert result["mount_list_field_status"] == presence and result["status"] == status


def test_review_mount_limit_whole_response_fails_instead_of_truncated_ready(object_db, object_repo):
    write_source(object_repo, GADGET_SOURCE, {NAME: {"abilities": [{"abilityName": MOUNT}] * 33}})
    rebuild(object_db, object_repo)
    with pytest.raises(catalog.RuntimeConfigError) as error:
        query(object_db)
    assert error.value.status == 413 and error.value.reason == "response_too_large"


def test_review_complete_response_budget_checks_repeated_full_node_copies(object_db, object_repo, monkeypatch):
    # A selected config appears in both candidates and selected; byte limits must
    # count the actual returned envelope, not only unique raw node storage.
    write_source(object_repo, GADGET_SOURCE, {NAME: {"abilities": [], "raw": "中" * 300}})
    rebuild(object_db, object_repo)
    monkeypatch.setitem(gadget.GADGET_LIMITS, "max_bytes", 6000)
    with pytest.raises(catalog.RuntimeConfigError) as error:
        query(object_db)
    assert error.value.status == 413


@pytest.mark.parametrize("version", ["2", "3", "4"])
def test_review_batch_old_index_upgrade_is_local_error_then_legacy_query_continues(tmp_path, object_repo, monkeypatch, version):
    db = build_index(object_repo, tmp_path / "old-batch.db", monkeypatch, version)
    result = runtime_queries.query_runtime(db, {"action": "batch", "items": [
        {"id": "needs-new-index", "request": {"action": "gadget_source_profile", "gadget_id": 101}},
        {"id": "still-readable", "request": {"action": "attenuation_profile", "group": "default"}},
    ]})
    assert result["status"] == "partial"
    first, second = result["items"]
    assert first["status"] == "error" and first["error"]["reason"] == "gadget_index_upgrade_required"
    assert first["error"]["details"]["stored_indexer_version"] == version
    assert second["status"] == "ok" and second["data"]["raw_record"]["resetCycle"] == 2.5
    assert second["data"]["snapshot"]["snapshot_id"] == result["snapshot"]["snapshot_id"]


def test_review_detached_response_and_one_session_do_not_reuse_a_later_snapshot(object_db, object_repo, monkeypatch):
    validations = []
    actual = runtime_queries.validate_runtime

    def tracked(*args, **kwargs):
        validations.append(1)
        return actual(*args, **kwargs)

    monkeypatch.setattr(runtime_queries, "validate_runtime", tracked)
    with equipment_read_session(object_db):
        first = query(object_db)
        expected = copy.deepcopy(first)
        first["config"]["selected"]["payload"]["unknown_control"]["active"] = True
        first["snapshot"]["snapshot_id"] = "mutated-caller-value"
        first["snapshot"]["counts"]["runtime_source"] = 0
        assert query(object_db) == expected
    assert len(validations) == 1
    write_source(object_repo, GADGET_SOURCE, {NAME: {"abilities": [], "changed": True}})
    rebuild(object_db, object_repo)
    second = query(object_db)
    assert len(validations) == 2
    assert second["source_fingerprint"] != expected["source_fingerprint"]
    with pytest.raises(catalog.RuntimeConfigError) as error:
        query(object_db, snapshot_id=expected["snapshot"]["snapshot_id"])
    assert error.value.reason == "snapshot_changed"


def test_review_forged_edge_is_fatal_to_whole_batch_not_hidden_partial(object_db):
    with closing(sqlite3.connect(object_db)) as conn, conn:
        conn.execute("UPDATE runtime_object_reference SET scope_json='{}' WHERE relation_kind='gadget_config_name'")
    with pytest.raises(catalog.RuntimeConfigError) as error:
        runtime_queries.query_runtime(object_db, {"action": "batch", "items": [
            {"id": "gadget", "request": {"action": "gadget_source_profile", "gadget_id": 101}},
            {"id": "later", "request": {"action": "attenuation_profile", "group": "default"}},
        ]})
    assert error.value.reason == "dataset_invalid" and error.value.status == 500


@pytest.mark.parametrize("kind,first_key,second_key", [
    ("gadget_config_entry", NAME, "Sibling"),
    ("ability", MOUNT, "Review_Sibling_Only"),
])
def test_review_coherent_exact_index_swap_cannot_redirect_preserved_raw_sources(object_db, kind, first_key, second_key):
    # Simulate coherent index corruption: both key rows and the corresponding
    # edges agree with one another, but disagree with the unchanged raw source.
    # Merely re-resolving against the same corrupted index is not proof.
    with closing(sqlite3.connect(object_db)) as conn, conn:
        ids = dict(conn.execute("SELECT exact_key_json,definition_id FROM runtime_definition WHERE definition_kind=?", (kind,)))
        first_id, second_id = ids[catalog.dumps(first_key)], ids[catalog.dumps(second_key)]
        conn.execute("UPDATE runtime_definition SET exact_key_json=? WHERE definition_id=?", (catalog.dumps(second_key), first_id))
        conn.execute("UPDATE runtime_definition SET exact_key_json=? WHERE definition_id=?", (catalog.dumps(first_key), second_id))
        for key, ident in [(first_key, second_id), (second_key, first_id)]:
            conn.execute("UPDATE runtime_object_reference SET resolved_definition_id=?,candidate_ids_json=? WHERE target_kind=? AND target_key_json=?",
                         (ident, catalog.dumps([ident]), kind, catalog.dumps(key)))
        # Old references targeting ability names exist in the entire Gadget
        # root; they are not used by the new query, but keep them coherent too.
        if kind == "ability":
            for key, ident in [(first_key, second_id), (second_key, first_id)]:
                conn.execute("UPDATE runtime_reference SET resolved_definition_id=?,candidate_ids_json=? WHERE target_kind=? AND target_key_json=? AND resolution_status='resolved'",
                             (ident, catalog.dumps([ident]), kind, catalog.dumps(key)))
    with pytest.raises(catalog.RuntimeConfigError) as error:
        query(object_db)
    assert error.value.reason == "dataset_invalid"
