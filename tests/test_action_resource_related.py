"""Fresh weak-root admission through real SQLite, using a small reconstructed corpus.

The source rows are copied from archived materials; the reduced corpus is a new
synthetic snapshot and receives only synthetic approvals inside temporary DBs.
"""
import copy
import hashlib
import sqlite3
from contextlib import closing

import pytest

from genshin_db import runtime_action_sources as module
from genshin_db.runtime_queries import (
    _definition_summary,
    _lookup,
    _Nodes,
    _read,
    query_runtime,
)
from genshin_db.semantic_store import read_record, review_record
from genshin_runtime.action_root_semantics import (
    BaseBindingError,
    binding_for,
    refs_for,
)
from genshin_runtime.mechanism_semantics import fingerprint, validate_record
from tests.action_resource_fixtures import (
    action_resource_synthetic_db_only,  # noqa: F401
    load_material,
)
from tests.test_action_resource_sources import COMMIT, install
from tests.test_pipeline import fake_repo  # noqa: F401
from tests.test_runtime_object_index import build, write

pytestmark = pytest.mark.usefixtures("action_resource_synthetic_db_only")


@pytest.fixture
def related_fixture(tmp_path, fake_repo):  # noqa: F811
    old = load_material("action-base-binding__pending-proposals.json")["proposals"][0]
    original = old["material"]["nodes"]
    files = {}
    for node in original:
        path, kind, raw = node["source_path"], node["definition"]["definition_kind"], node["raw"]
        if kind == "excel_record":
            files.setdefault(path, []).append(raw)
        elif kind == "talent_config":
            files.setdefault(path, {})[node["definition"]["exact_key"]] = raw
        else:
            assert kind == "avatar_config"
            files[path] = raw
    for path, value in files.items():
        write(fake_repo, path, value)
    db = tmp_path / "synthetic-related.db"
    build(db, fake_repo, upstream_commit=COMMIT)
    record = copy.deepcopy(load_material("pending-record-bundle.json")["facts"][11])
    claim = record["payload"]["claim"]
    with _read(db, "release") as (conn, state):
        nodes, fresh = _Nodes(conn, "release"), []
        for previous in original:
            d = previous["definition"]
            rows = _lookup(conn, "release", d["definition_kind"], d["exact_key"], previous["source_path"])
            assert len(rows) == 1
            row = rows[0]
            raw, origin = nodes.node(row)
            fresh.append({"id": previous["id"], "definition": _definition_summary(row), **origin, "raw": raw})
        claim["snapshot"] = state
    by_id, old_by_id = {n["id"]: n for n in fresh}, {n["id"]: n for n in original}
    claim["nodes"] = [binding_for(n) for n in fresh]
    claim["mounts"] = refs_for(by_id[claim["roles"]["config"]], state)
    for anchor in claim["anchors"]:
        for field, role in (("proud_reference", "proud"), ("talent_reference", "talent"), ("mount_reference", None)):
            ident = anchor[role] if role else claim["roles"]["config"]
            previous = anchor[field]
            suffix = previous["json_pointer"][len(old_by_id[ident]["json_pointer"]):]
            matches = [r for r in refs_for(by_id[ident], state)
                       if r["json_pointer"] == by_id[ident]["json_pointer"] + suffix
                       and r["target_key"] == previous["target_key"]]
            assert len(matches) == 1
            anchor[field] = matches[0]
    record["id"] = record["payload"]["id"] = "fixture.related.root"
    record["review"] = {"status": "approved", "reviewer": "synthetic-fixture",
                        "reviewed_at": "2026-10-08T00:00:00+00:00"}
    record["applicability"] = {"version_line": "release", "upstream_commit": COMMIT,
                                "normalization_fingerprint": fingerprint(state["normalization"])}
    record["evidence"] = [{"id": n["id"], "kind": "runtime_node",
        "locator": n["source_path"] + "#" + n["json_pointer"], "sha256": fingerprint(n["raw"])} for n in fresh]
    validate_record(record, "fact")
    return db, record


def query(db, record):
    return query_runtime(db, {"action": "action_capacity_source_profile",
        "character_id": record["subject"]["target_id"], "constellation": 0})


def test_fresh_related_root_never_becomes_default_mount(related_fixture):
    db, record = related_fixture
    assert install(db, record)["applied"]
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    out = query(db, record)
    item = out["sources"][0]
    assert item["status"] == "corroborated_root"
    projected = item["projection"]
    assert projected["verification_scope"] == "fresh_source_and_current_review_head"
    assert projected["requires_fresh_registry_admission"] is False
    assert len(projected["material"]["nodes"]) == 14
    assert projected["related_config"] is not None
    assert projected["default_mount"] == "unproven"
    assert projected["execution_authority"] is False and out["capacity"] is None
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("status", ["pending", "revoked"])
def test_related_unapproved_current_head_never_loads_material(related_fixture, monkeypatch, status):
    db, record = related_fixture
    record["review"] = {"status": status, "reviewer": "fixture" if status == "revoked" else None,
                        "reviewed_at": "2026-10-08T00:00:00+00:00" if status == "revoked" else None}
    install(db, record)
    monkeypatch.setattr(module, "_related", lambda *_: pytest.fail("unapproved relation loaded"))
    item = query(db, record)["sources"][0]
    assert item["reason"] == "review_not_approved" and item["projection"] is None


def test_related_same_revision_revocation_is_fresh(related_fixture):
    db, record = related_fixture
    install(db, record)
    assert query(db, record)["sources"][0]["projection"]["related_config"]
    with closing(sqlite3.connect(db)) as conn:
        saved = read_record(conn, "release", "fact", record["id"], 1)
    review_record(db, version_line="release", record_kind="fact", record_id=record["id"], revision=1,
                  status="revoked", reviewer="fixture", reviewed_at="2026-10-08T01:00:00+00:00",
                  expected_review_fingerprint=saved["review_fingerprint"])
    assert query(db, record)["sources"][0]["projection"] is None


def test_related_stale_complete_parent_rejected_even_with_approved_head(related_fixture, monkeypatch):
    db, record = related_fixture
    install(db, record)
    original = module._Nodes.node

    def changed(self, row):
        raw, origin = original(self, row)
        if row["definition_kind"] == "avatar_config":
            raw = copy.deepcopy(raw)
            raw["unreviewed_extra"] = True
        return raw, origin

    monkeypatch.setattr(module._Nodes, "node", changed)
    with pytest.raises(BaseBindingError, match="complete bound node"):
        query(db, record)
