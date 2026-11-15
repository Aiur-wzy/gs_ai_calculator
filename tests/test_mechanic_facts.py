"""Explicit, reviewable annotation imports preserve human edits and B updates."""

# ruff: noqa: F811 - pytest resolves imported fixtures through function arguments.

import copy
import json
import sqlite3
import threading
from importlib.resources import files

import pytest

from genshin_db.annotation_store import import_annotation_bundle, migrate_annotations
from genshin_db.mechanic_facts import validate_bundle
from genshin_db.service import GenshinHTTPServer
from tests.test_equipment_http import request
from tests.test_query_descriptions import description_db, legacy_db  # noqa: F401


def sample_bundle():
    return {"schema_version": 1, "id": "test-mechanics", "items": [{
        "id": "particle-count", "target_type": "skill", "target_id": 10,
        "version_line": "release", "title": "产球条件", "category": "energy",
        "conclusion": "仅首次命中产球。", "conditions": "需要命中目标。", "numeric_pattern": "3个微粒。",
        "exceptions": "后续伤害不再产球。", "unconfirmed": "旅行时间。",
        "sources": [{"title": "机制测试", "url": "https://example.org/evidence", "locator": "Skill Mechanics",
                     "source_version": "1.0", "source_date": "2020-01-01", "retrieved_at": "2026-09-13"}]}]}


@pytest.mark.parametrize("kind", ["user_statement", "database_snapshot"])
def test_local_fact_evidence_is_explicit_not_a_fabricated_web_citation(kind):
    bundle = sample_bundle()
    source = bundle["items"][0]["sources"][0]
    source.update(source_kind=kind, url=None, evidence_sha256="a" * 64)
    _, rows = validate_bundle(bundle)
    assert "https://example.org/evidence" not in rows[0]["content"]
    assert "证据SHA256" in rows[0]["content"]
    source["url"] = "https://example.org/invented"
    with pytest.raises(ValueError, match="无伪造URL"):
        validate_bundle(bundle)


def test_packaged_facts_are_valid_reviewed_and_contain_version_boundaries():
    data = json.loads(files("genshin_db").joinpath("resources/mechanics/stable-facts.json").read_text(encoding="utf-8"))
    _, rows = validate_bundle(data)
    assert len(rows) == 17
    assert all(row["status"] == "approved" and row["reviewer"] for row in rows)
    assert all("未确认项" in row["content"] and "来源版本" in row["content"] for row in rows)
    kazuha = next(r for r in rows if r["fact_id"] == "kazuha-burst-hit-pattern")
    assert "2024-04-03" in kazuha["content"] and "4.5" in kazuha["content"]


def test_character_action_appendices_are_pending_character_owned_facts():
    path = files("genshin_db").joinpath("resources/mechanics/character-action-appendices-v1.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    bundle_id, rows = validate_bundle(data)
    assert bundle_id == "character-action-appendices-v1"
    assert len(rows) == len(data["items"]) == 32
    assert {row["target_type"] for row in rows} == {"skill", "talent", "inherent_skill"}
    assert all(row["status"] == "pending" for row in rows)
    assert all(row["reviewer"] is None and row["reviewed_at"] is None for row in rows)
    assert {(row["target_type"], row["target_id"]) for row in rows} == {
        ("skill", 10019), ("skill", 10632), ("skill", 10635),
        ("skill", 10472), ("skill", 10474), ("skill", 10475),
        ("talent", 472), ("inherent_skill", 4721), ("inherent_skill", 4722),
        ("skill", 10542), ("skill", 10545),
        ("skill", 10160), ("skill", 10161), ("skill", 10165), ("talent", 166),
        ("skill", 11072), ("skill", 11075),
        ("skill", 10322), ("skill", 10323),
        ("skill", 11482), ("skill", 11485), ("talent", 1482),
        ("skill", 11502), ("skill", 11505),
        ("skill", 11331), ("skill", 11332), ("skill", 11335), ("talent", 1331),
        ("skill", 10431), ("skill", 10432), ("talent", 431),
    }
    stable = json.loads(files("genshin_db").joinpath("resources/mechanics/stable-facts.json").read_text(encoding="utf-8"))
    assert {item["id"] for item in data["items"]}.isdisjoint({item["id"] for item in stable["items"]})
    for item in data["items"]:
        assert item["id"].startswith("appendix.") and item["id"].endswith(".v1")
        assert item["unconfirmed"]
        serialized = json.dumps(item, ensure_ascii=False)
        assert all(name not in serialized for name in ("神鹤万心", "卢茜万班", "星超导队"))
        for source in item["sources"]:
            if "github.com/genshinsim/gcsim" in (source.get("url") or ""):
                assert "/blob/8792c73d64439feb74fa139c3b745022952b3dc9/" in source["url"]


def test_import_preview_default_draft_idempotency_and_manual_edit_preservation(description_db):
    bundle = sample_bundle()
    preview = import_annotation_bundle(description_db, bundle)
    assert preview["counts"]["would_insert"] == 1
    result = import_annotation_bundle(description_db, bundle, dry_run=False)
    ann_id = result["items"][0]["annotation_ids"][0]
    assert result["counts"]["inserted"] == 1
    assert import_annotation_bundle(description_db, bundle, dry_run=False)["counts"]["unchanged"] == 1
    with sqlite3.connect(description_db) as conn:
        assert conn.execute("SELECT status FROM annotation WHERE id=?", (ann_id,)).fetchone()[0] == "draft"
        conn.execute("UPDATE annotation SET content='人工保留修订',status='approved' WHERE id=?", (ann_id,))
    assert import_annotation_bundle(description_db, bundle, dry_run=False)["counts"]["conflict_preserved"] == 1
    with sqlite3.connect(description_db) as conn:
        assert conn.execute("SELECT content,status FROM annotation WHERE id=?", (ann_id,)).fetchone() == ("人工保留修订", "approved")


def test_import_reports_missing_target_and_validation_is_atomic(description_db):
    bundle = sample_bundle()
    bundle["items"][0]["target_id"] = 987654
    assert import_annotation_bundle(description_db, bundle, dry_run=False)["counts"]["target_missing"] == 1
    bundle = sample_bundle()
    broken = copy.deepcopy(bundle["items"][0])
    broken.update(id="second", status="approved")  # No review identity/date.
    bundle["items"].append(broken)
    with pytest.raises(ValueError):
        import_annotation_bundle(description_db, bundle, dry_run=False)
    with sqlite3.connect(description_db) as conn:
        assert conn.execute("SELECT count(*) FROM annotation WHERE author LIKE 'mechanics:%'").fetchone()[0] == 0


def test_b_migration_preserves_import_identity_review_and_deletion(description_db, tmp_path):
    bundle = sample_bundle()
    item = bundle["items"][0]
    item.update(status="approved", reviewer="机制审核员", reviewed_at="2026-09-13")
    result = import_annotation_bundle(description_db, bundle, dry_run=False)
    ann_id = result["items"][0]["annotation_ids"][0]
    dest = tmp_path / "migrated.db"
    migrate_annotations(description_db, dest)
    with sqlite3.connect(dest) as conn:
        row = conn.execute("SELECT author,status,reviewer FROM annotation WHERE id=?", (ann_id,)).fetchone()
        assert row == ("mechanics:test-mechanics:particle-count", "approved", "机制审核员")
    with sqlite3.connect(description_db) as conn:
        conn.execute("DELETE FROM annotation WHERE id=?", (ann_id,))
    migrate_annotations(description_db, dest)
    with sqlite3.connect(dest) as conn:
        assert conn.execute("SELECT 1 FROM annotation WHERE id=?", (ann_id,)).fetchone() is None


def test_http_import_requires_token_and_defaults_to_preview(description_db):
    srv = GenshinHTTPServer(description_db.parent, port=0)
    worker = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    worker.start()
    try:
        body = {"bundle": sample_bundle()}
        assert request(srv, "/v1/annotations/import", method="POST", body=body)[0] == 401
        status, result, _ = request(srv, "/v1/annotations/import", method="POST", body=body, token=True)
        assert status == 410 and result["reason"] == "accepted_database_is_immutable"
        status, result, _ = request(srv, "/v1/annotations/import", method="POST", body={**body, "dry_run": False}, token=True)
        assert status == 410 and result["reason"] == "accepted_database_is_immutable"
    finally:
        srv.shutdown()
        worker.join(timeout=3)
        srv.server_close()
