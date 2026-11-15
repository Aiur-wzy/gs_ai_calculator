"""Exact object identity receipts are bounded and never claim dispatch."""
import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from genshin_db import runtime_catalog as catalog
from genshin_db import runtime_gadget_source as gadget
from genshin_db import runtime_queries as queries
from genshin_db import runtime_store as store
from genshin_db.equipment_queries import equipment_read_session
from tests.test_runtime_object_index import (  # noqa: F401
    ABILITY,
    GADGET,
    build,
    fake_repo,
    object_db,
    object_repo,
    rows,
    write,
)

# ruff: noqa: F811


def get(db, ident=101, **kwargs):
    return queries.query_runtime(db, {"action": "gadget_source_profile", "gadget_id": ident, **kwargs})


def test_exact_full_sources_and_direct_mount_do_not_claim_execution(object_db, object_repo):
    before = object_db.read_bytes()
    data = get(object_db)
    assert data["status"] == "ready" and data["graph_scope"] == "gadget_identity_v1"
    assert data["origin"]["status"] == "not_requested"
    assert data["execution_support"] == data["execution"]["controller"] == "not_evaluated"
    assert data["mount_list_field_status"] == "present"
    assert data["config"]["selected"]["payload"]["opaque"] == {"unknown": True}
    assert data["mounted_abilities"][0]["selected"]["payload"]["abilityName"] == "Child"
    entries = [data["record"], data["config"]["selected"], data["mounted_abilities"][0]["selected"]]
    for entry in entries:
        raw = (object_repo / entry["source_path"]).read_bytes()
        node = catalog.resolve_pointer(json.loads(raw), entry["json_pointer"])
        assert entry["source_sha256"] == hashlib.sha256(raw).hexdigest()
        assert entry["node_sha256"] == catalog.fingerprint(node)
        assert node == entry.get("payload", entry.get("record"))
    assert data["content_fingerprint"] == catalog.fingerprint({k: v for k, v in data.items() if k != "content_fingerprint"})
    assert len(catalog.dumps(data).encode("utf8")) <= 256 * 1024
    assert object_db.read_bytes() == before


def test_creation_leaves_and_modifier_owner_are_individually_auditable(object_db):
    refs = rows(object_db, "runtime_object_reference", "WHERE relation_kind='gadget_id_reference'")
    by_target = {ident: [r for r in refs if json.loads(r["target_key_json"]) == ident] for ident in (101, 102)}
    origins = [get(object_db, origin_reference_id=r["legacy_reference_id"])["origin"] for r in by_target[101]]
    assert len(origins) == 2 and origins[0]["operation"]["json_pointer"] != origins[1]["operation"]["json_pointer"]
    assert origins[0]["parent"] == origins[1]["parent"]
    assert origins[0]["operation"]["raw"] != origins[1]["operation"]["raw"]
    assert all(o["status"] == "verified_relationship" and o["owner_ability"] is None for o in origins)
    modifier = get(object_db, 102, origin_reference_id=by_target[102][0]["legacy_reference_id"])["origin"]
    assert modifier["parent"]["definition"]["definition_kind"] == "modifier"
    assert modifier["owner_ability"]["payload"]["abilityName"] == "Creator"
    assert modifier["operation"]["raw"] == {"$type": "TriggerBullet", "bulletID": 102}
    with pytest.raises(ValueError):
        get(object_db, 101, origin_reference_id=by_target[102][0]["legacy_reference_id"])


def test_generate_particle_external_id_cannot_be_used_as_creation_proof(object_db):
    ref = next(r for r in rows(object_db, "runtime_reference") if r["target_key_json"] == "2022")
    with pytest.raises(catalog.RuntimeConfigError) as exc:
        get(object_db, origin_reference_id=ref["reference_id"])
    assert exc.value.reason == "unsupported_source" and exc.value.status == 409


@pytest.mark.parametrize("value,field,status", [({}, "absent", "pending"),
    ({"abilities": None}, "null", "pending"), ({"abilities": {}}, "invalid_type", "pending"),
    ({"abilities": []}, "empty", "ready"), ({"abilities": [{"abilityName": "Child"}]}, "present", "ready")])
def test_mount_field_presence_is_preserved(object_repo, tmp_path, value, field, status):
    write(object_repo, GADGET, {"Shared": value})
    db = tmp_path / "mount.db"
    build(db, object_repo)
    result = get(db)
    assert result["mount_list_field_status"] == field and result["status"] == status
    assert result["config"]["selected"]["payload"] == value


@pytest.mark.parametrize("ident", [103, 104, 105, 106])
def test_missing_config_never_invents_name_or_mounts(object_db, ident):
    result = get(object_db, ident)
    assert result["status"] == "pending" and result["config"]["selected"] is None
    assert result["mounted_abilities"] == [] and result["mount_list_field_status"] == "not_available"


def test_shared_config_keeps_distinct_record_and_fingerprint(object_db):
    first, second = get(object_db), get(object_db, 102)
    assert first["config"]["selected"] == second["config"]["selected"]
    assert first["source_fingerprint"] != second["source_fingerprint"]
    assert first["record"]["record"]["id"] == 101 and second["record"]["record"]["id"] == 102


@pytest.mark.parametrize("kind", ["config", "ability"])
def test_ambiguous_names_preserve_all_candidates_instead_of_default(object_repo, tmp_path, kind):
    if kind == "config":
        write(object_repo, "BinOutput/Gadget/Collision.json", {"Shared": {"abilities": []}})
    else:
        write(object_repo, "BinOutput/Ability/Temp/Collision.json", [{"Another": {"abilityName": "Child"}}])
    db = tmp_path / "ambiguous.db"
    build(db, object_repo)
    result = get(db)
    target = result["config"] if kind == "config" else result["mounted_abilities"][0]
    assert result["status"] == "pending" and target["status"] == "ambiguous"
    assert target["selected"] is None and len(target["candidates"]) == 2


@pytest.mark.parametrize("query", [
    {"gadget_id": True}, {"gadget_id": 0}, {"gadget_id": 2**53}, {},
    {"gadget_id": 101, "origin_reference_id": "no"}, {"gadget_id": 101, "origin_reference_id": None},
    {"gadget_id": 101, "character_id": 1}, {"gadget_id": 101, "snapshot_id": None},
])
def test_invalid_request_never_reads_db(tmp_path, monkeypatch, query):
    monkeypatch.setattr(queries, "_read", lambda *_: pytest.fail("invalid query read DB"))
    with pytest.raises(ValueError):
        queries.query_runtime(tmp_path / "absent.db", {"action": "gadget_source_profile", **query})


def test_same_session_receipts_are_independent_and_validation_runs_once(object_db, monkeypatch):
    original, calls = queries.validate_runtime, []

    def validate(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(queries, "validate_runtime", validate)
    with equipment_read_session(object_db):
        expected = get(object_db)
        changed = get(object_db)
        changed["snapshot"]["counts"]["runtime_definition"] = -1
        changed["config"]["selected"]["payload"]["opaque"]["unknown"] = False
        assert get(object_db) == expected
    assert calls == [True]


@pytest.mark.parametrize("limit,value", [("max_bytes", 100), ("max_nodes", 1), ("max_edges", 0), ("max_mounts", 0)])
def test_full_source_budgets_reject_without_partial_ready(object_db, monkeypatch, limit, value):
    monkeypatch.setitem(gadget.GADGET_LIMITS, limit, value)
    with pytest.raises(catalog.RuntimeConfigError) as exc:
        get(object_db)
    assert exc.value.status == 413 and exc.value.reason == "response_too_large"
    assert len(catalog.dumps(exc.value.details)) < 200


def test_single_and_batch_same_snapshot_and_missing_item_continue(object_db, monkeypatch):
    expected = get(object_db)
    original, calls = queries.validate_runtime, []

    def validate(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(queries, "validate_runtime", validate)
    batch = queries.query_runtime(object_db, {"action": "batch", "items": [
        {"id": "missing", "request": {"action": "gadget_source_profile", "gadget_id": 999}},
        {"id": "valid", "request": {"action": "gadget_source_profile", "gadget_id": 101}},
    ]})
    assert calls == [True] and batch["status"] == "partial"
    assert batch["items"][0]["error"]["reason"] == "not_found"
    assert batch["items"][1]["data"] == expected


def test_old_index_upgrade_is_item_error_and_later_batch_item_runs(object_repo, tmp_path, monkeypatch):
    db = tmp_path / "old.db"
    with monkeypatch.context() as patch:
        patch.setattr(catalog, "INDEXER_VERSION", "4")
        patch.setattr(store, "INDEXER_VERSION", "4")
        build(db, object_repo)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("DROP TABLE runtime_object_reference")
    before = db.read_bytes()
    with pytest.raises(catalog.RuntimeConfigError) as exc:
        get(db)
    assert exc.value.reason == "gadget_index_upgrade_required" and exc.value.status == 409
    assert exc.value.details == {"stored_indexer_version": "4", "required_indexer_version": "5"}
    batch = queries.query_runtime(db, {"action": "batch", "items": [
        {"id": "upgrade", "request": {"action": "gadget_source_profile", "gadget_id": 101}},
        {"id": "old_ok", "request": {"action": "action_facts", "character_id": 90000001, "slot": "normal"}},
    ]})
    assert batch["status"] == "partial" and batch["items"][0]["error"]["reason"] == "gadget_index_upgrade_required"
    assert batch["items"][1]["status"] == "ok"
    assert db.read_bytes() == before


def test_capabilities_describe_required_identity_and_bounded_independent_graph():
    caps = queries.runtime_capabilities()
    assert caps["required"]["gadget_source_profile"] == ["gadget_id"]
    assert caps["gadget_source_profile"]["execution_support"] == "not_evaluated"
    assert caps["gadget_source_profile"]["limits"]["max_bytes"] == 256 * 1024
    assert "gadget_source_profile" in caps["batch"]["actions"]
