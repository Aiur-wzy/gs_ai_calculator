"""Small SQLite fixtures only; no upstream or formal database reads."""

import copy
import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from genshin_db import semantic_store as store


def fact(ident="one", revision=1, line="release", status="approved"):
    return {"id": ident, "revision": revision,
            "subject": {"target_type": "character", "target_id": 123},
            "payload": {"id": ident, "revision": revision, "kind": "execution_requirement",
                        "claim": {"requirement": "single_target"},
                        "evidence_tier": "experimental_reference", "evidence_refs": ["source"]},
            "evidence": [{"id": "source", "kind": "primary_test", "locator": "fixture:source",
                          "sha256": "a" * 64}],
            "applicability": {"version_line": line, "upstream_commit": "b" * 40,
                              "normalization_fingerprint": "c" * 64},
            "review": {"status": status, "reviewer": "fixture-reviewer" if status in ("approved", "revoked") else None,
                       "reviewed_at": "2026-10-03T00:00:00+00:00" if status in ("approved", "revoked") else None}}


def head(ident="one", revision=1, expected=None, kind="fact"):
    return {"record_kind": kind, "record_id": ident, "revision": revision,
            "expected_head_revision": expected}


def bundle(records=None, heads=None, descriptors=None):
    return {"schema_version": 1, "id": "test.bundle", "facts": records if records is not None else [fact()],
            "descriptors": descriptors or [], "heads": heads if heads is not None else [head()]}


def descriptor_bundle():
    template = {"key": "effect", "rule": {
        "name_zh": "存储测试规则（不代表游戏来源）",
        "trigger": {"event": "action_start", "phase": "pre"},
        "beneficiaries": {"mode": "team", "scope": "locked"},
        "timing": {"delay": 0, "duration": 5, "cooldown": 0, "cooldown_anchor": "success"},
        "stacking": {"max": 1, "add": 1, "policy": "replace"},
        "effects": [{"id": "addition", "benefit_type_id": "benefit.base_add.all",
                     "domain": "provider", "value": 0.5}]}, "value_bindings": [],
        "execution_ports": [{"target": "owner", "role": "provider"},
                            {"target": "trigger.action_ids", "role": "qualifying_actions"}],
        "requirements": ["single_target", "qualified_action_origin"]}
    fact_record = fact()
    fact_record["payload"].update(kind="rule_template", claim=template)
    record = fact("mechanism")
    record["evidence"] = [{"id": "source", "kind": "runtime_node", "locator": "fixture/config.json#/0",
                           "sha256": "a" * 64}]
    record["payload"] = {"descriptor": {
        "schema_version": 1, "mechanism_id": "mechanism", "revision": 1,
        "evidence_fact_ids": ["one@1"], "rule_templates": [copy.deepcopy(template)]},
        "source_binding": {"schema_version": 1, "upstream_commit": "b" * 40,
                           "normalization_fingerprint": "c" * 64,
                           "subject": copy.deepcopy(record["subject"]), "selection": {"character_id": 123},
                           "nodes": [{"id": "source", "definition_kind": "ability",
                                      "source_path": "fixture/config.json", "json_pointer": "/0",
                                      "node_sha256": "a" * 64, "source_sha256": "d" * 64}],
                           "parameters": [], "evidence": copy.deepcopy(record["evidence"]),
                           "coverage": {"whole_source_status": "partial", "excluded_branches": ["synthetic data only"],
                                        "unknown_branches": []}}}
    return bundle([fact_record], [head(), head("mechanism", kind="descriptor")], [record])


@pytest.fixture
def semantic_db(tmp_path):
    path = tmp_path / "fixture.db"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("CREATE TABLE raw_fixture(id INTEGER PRIMARY KEY,payload TEXT)")
        conn.execute("INSERT INTO raw_fixture VALUES (1,'unchanged raw')")
        conn.execute("CREATE TABLE annotation(id INTEGER PRIMARY KEY,content TEXT)")
        conn.execute("INSERT INTO annotation VALUES (1,'人工资料')")
    return path


def read(path, ident="one", revision=None, line="release", kind="fact"):
    with closing(sqlite3.connect(path)) as conn:
        return store.read_record(conn, line, kind, ident, revision)


def digest(path):
    with closing(sqlite3.connect(path)) as conn:
        return store.semantic_digest(conn)


def install(path, data=None, line="release"):
    return store.import_bundle(path, data or bundle(), version_line=line, dry_run=False)


def review(path, current, status="revoked"):
    return store.review_record(path, version_line="release", record_kind="fact", record_id=current["id"],
                               revision=current["revision"], status=status, reviewer="new-reviewer",
                               reviewed_at="2026-10-03T01:00:00+00:00",
                               expected_review_fingerprint=current["review_fingerprint"])


def test_dry_run_and_old_reads_never_install_component(semantic_db):
    before = semantic_db.read_bytes()
    with closing(sqlite3.connect(semantic_db)) as conn:
        assert store.read_catalog(conn)["reason"] == "component_missing"
        assert store.read_record(conn, "release", "fact", "one")["reason"] == "component_missing"
    result = store.import_bundle(semantic_db, bundle())
    assert result["dry_run"] and not result["applied"]
    assert semantic_db.read_bytes() == before


def test_apply_and_repeat_preserve_original_payload_and_raw(semantic_db):
    assert install(semantic_db)["applied"]
    first = read(semantic_db)
    before = semantic_db.read_bytes()
    repeated = install(semantic_db)
    assert repeated["records"][0]["action"] == "unchanged"
    assert semantic_db.read_bytes() == before
    assert read(semantic_db) == first
    with closing(sqlite3.connect(semantic_db)) as conn:
        assert conn.execute("SELECT payload FROM raw_fixture").fetchone() == ("unchanged raw",)
        assert conn.execute("SELECT content FROM annotation").fetchone() == ("人工资料",)


def test_conflicting_one_row_withdraws_entire_import_and_heads(semantic_db):
    install(semantic_db)
    changed = fact(); changed["payload"]["claim"]["requirement"] = "fixed_party"
    before = digest(semantic_db)
    result = install(semantic_db, bundle([changed, fact("two")], [head("two")]))
    assert not result["applied"] and result["status"] == "conflict_preserved"
    assert digest(semantic_db) == before
    assert read(semantic_db, "two", 1)["reason"] == "record_missing"


def test_review_cas_and_seed_cannot_resurrect_revoked(semantic_db):
    install(semantic_db)
    old = read(semantic_db); revoked = review(semantic_db, old)
    assert revoked["review"]["status"] == "revoked"
    assert revoked["payload_fingerprint"] == old["payload_fingerprint"]
    assert revoked["review_fingerprint"] != old["review_fingerprint"]
    assert install(semantic_db)["status"] == "conflict_preserved"
    assert read(semantic_db)["review"]["status"] == "revoked"
    with pytest.raises(store.SemanticStoreError, match="审核状态已变化"):
        review(semantic_db, old)


def test_new_revision_head_cas_delete_and_explicit_history(semantic_db):
    install(semantic_db)
    second = fact(revision=2)
    bad = install(semantic_db, bundle([second], [head(revision=2, expected=None)]))
    assert bad["status"] == "conflict_preserved"
    assert read(semantic_db, revision=2)["reason"] == "record_missing"
    assert install(semantic_db, bundle([second], [head(revision=2, expected=1)]))["applied"]
    assert read(semantic_db)["revision"] == 2
    install(semantic_db, bundle([], [head(revision=None, expected=2)]))
    assert read(semantic_db)["reason"] == "head_missing"
    assert read(semantic_db, revision=1)["revision"] == 1
    assert read(semantic_db, revision=2)["revision"] == 2


@pytest.mark.parametrize("change", ["future", "bool_revision", "unknown", "wrong_line", "duplicate", "bad_head", "too_many"])
def test_invalid_import_is_atomic(semantic_db, change):
    data = bundle()
    if change == "future": data["schema_version"] = 2
    if change == "bool_revision": data["facts"][0]["revision"] = True
    if change == "unknown": data["facts"][0]["surprise"] = 1
    if change == "wrong_line": data["facts"][0]["applicability"]["version_line"] = "beta"
    if change == "duplicate": data["facts"] *= 2
    if change == "bad_head": data["heads"][0]["record_id"] = "missing"
    if change == "too_many": data["heads"] = [head(str(i)) for i in range(101)]
    before = semantic_db.read_bytes()
    with pytest.raises(ValueError): install(semantic_db, data)
    assert semantic_db.read_bytes() == before


def test_future_component_rejects_reads_and_writes_before_changes(semantic_db):
    install(semantic_db)
    with closing(sqlite3.connect(semantic_db)) as conn, conn:
        conn.execute("UPDATE data_component SET schema_version=2 WHERE component=?", (store.COMPONENT,))
    before = semantic_db.read_bytes()
    with pytest.raises(store.SemanticStoreError, match="版本"): read(semantic_db)
    with pytest.raises(store.SemanticStoreError, match="版本"): install(semantic_db)
    assert semantic_db.read_bytes() == before


def test_tampered_payload_is_not_accepted_by_stored_fingerprint(semantic_db):
    install(semantic_db)
    with closing(sqlite3.connect(semantic_db)) as conn, conn:
        conn.execute("UPDATE mechanic_semantic_fact SET payload_json=replace(payload_json,'single_target','fixed_party')")
    with pytest.raises(store.SemanticStoreError, match="指纹"): read(semantic_db)


def test_b_sync_exactly_mirrors_both_lines_revocation_and_deleted_heads(semantic_db, tmp_path):
    install(semantic_db)
    install(semantic_db, bundle([fact("beta.fact", line="beta")], [head("beta.fact")]), "beta")
    destination = tmp_path / "candidate.db"
    with closing(sqlite3.connect(semantic_db)) as source, closing(sqlite3.connect(destination)) as target:
        source.backup(target)
    review(semantic_db, read(semantic_db))
    install(semantic_db, bundle([], [head(revision=None, expected=1)]))
    with closing(sqlite3.connect(semantic_db)) as source, closing(sqlite3.connect(destination)) as target:
        source.execute("BEGIN"); target.execute("BEGIN IMMEDIATE")
        receipt = store.synchronize(source, target)
        assert receipt == store.semantic_digest(source) == store.semantic_digest(target)
        target.commit()
    assert read(destination)["reason"] == "head_missing"
    assert read(destination, revision=1)["review"]["status"] == "revoked"
    assert read(destination, "beta.fact", line="beta")["review"]["status"] == "approved"


def test_sync_error_rolls_back_only_own_savepoint(semantic_db, tmp_path):
    install(semantic_db)
    other = tmp_path / "other.db"
    with closing(sqlite3.connect(other)) as target, target:
        store.ensure_schema(target)
        target.execute("CREATE TRIGGER fail_semantic BEFORE INSERT ON mechanic_semantic_fact BEGIN SELECT RAISE(ABORT,'fault'); END")
    with closing(sqlite3.connect(semantic_db)) as source, closing(sqlite3.connect(other)) as target:
        source.execute("BEGIN"); target.execute("BEGIN")
        before = store.semantic_digest(target)
        with pytest.raises(sqlite3.IntegrityError, match="fault"):
            store.synchronize(source, target)
        assert target.in_transaction
        assert store.semantic_digest(target) == before


def test_source_missing_keeps_new_empty_schema_without_seed(semantic_db, tmp_path):
    with closing(sqlite3.connect(semantic_db)) as source, closing(sqlite3.connect(tmp_path / "new.db")) as target:
        store.ensure_schema(target)
        before = store.semantic_digest(target)
        assert store.synchronize(source, target)["status"] == "source_missing"
        assert store.semantic_digest(target) == before


def test_return_values_are_isolated(semantic_db):
    install(semantic_db)
    original = read(semantic_db)
    changed = copy.deepcopy(original)
    changed["payload"]["claim"]["requirement"] = "fixed_party"
    assert read(semantic_db) == original


def test_import_failure_does_not_partially_install_tables(semantic_db):
    with closing(sqlite3.connect(semantic_db)) as conn, conn:
        store.ensure_schema(conn)
        conn.execute("CREATE TRIGGER fail_semantic BEFORE INSERT ON mechanic_semantic_fact BEGIN SELECT RAISE(ABORT,'fault'); END")
    before = hashlib.sha256(semantic_db.read_bytes()).hexdigest()
    with pytest.raises(sqlite3.IntegrityError, match="fault"):
        install(semantic_db)
    assert hashlib.sha256(semantic_db.read_bytes()).hexdigest() == before


def test_descriptor_catalog_filters_and_reads_exact_head(semantic_db):
    assert install(semantic_db, descriptor_bundle())["applied"]
    record = read(semantic_db, "mechanism", kind="descriptor")
    assert record["payload"]["descriptor"]["evidence_fact_ids"] == ["one@1"]
    with closing(sqlite3.connect(semantic_db)) as conn:
        catalog = store.read_catalog(conn, target_type="character", target_id=123)
        assert catalog["total"] == 1 and catalog["items"][0]["id"] == "mechanism"
        assert "payload" not in catalog["items"][0]
        assert store.read_catalog(conn, target_type="character", target_id=124)["items"] == []
        assert store.read_catalog(conn, page=2, page_size=1)["items"] == []
        with pytest.raises(ValueError): store.read_catalog(conn, target_type="character")
        with pytest.raises(ValueError): store.read_catalog(conn, page_size=51)


def test_descriptor_missing_fact_fails_before_component_install(semantic_db):
    data = descriptor_bundle(); data["facts"] = []; data["heads"] = data["heads"][1:]
    before = semantic_db.read_bytes()
    with pytest.raises(ValueError, match="事实修订"):
        install(semantic_db, data)
    assert semantic_db.read_bytes() == before


def test_normalization_and_source_binding_changes_are_payload_conflicts(semantic_db):
    data = descriptor_bundle(); install(semantic_db, data)
    old = read(semantic_db, "mechanism", kind="descriptor")
    data["descriptors"][0]["applicability"]["normalization_fingerprint"] = "e" * 64
    data["descriptors"][0]["payload"]["source_binding"]["normalization_fingerprint"] = "e" * 64
    assert install(semantic_db, data)["status"] == "conflict_preserved"
    assert read(semantic_db, "mechanism", kind="descriptor") == old


def test_import_canonical_byte_limit_is_checked_before_writes(semantic_db, monkeypatch):
    data = bundle()
    size = len(json.dumps(data, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    before = semantic_db.read_bytes()
    monkeypatch.setitem(store.LIMITS, "max_import_bytes", size - 1)
    with pytest.raises(ValueError, match="2MiB"):
        install(semantic_db, data)
    monkeypatch.setitem(store.LIMITS, "max_import_bytes", size)
    assert store.import_bundle(semantic_db, data)["status"] == "ready"
    assert semantic_db.read_bytes() == before


def test_new_revision_requires_head_cas_even_when_not_activating_it(semantic_db):
    install(semantic_db)
    before = digest(semantic_db)
    with pytest.raises(ValueError, match="head CAS"):
        install(semantic_db, bundle([fact(revision=2)], []))
    stale = install(semantic_db, bundle([fact(revision=2)], [head(revision=1, expected=None)]))
    assert stale["status"] == "conflict_preserved"
    assert digest(semantic_db) == before


def test_dangling_head_is_corruption_not_empty_catalog(semantic_db):
    install(semantic_db)
    with closing(sqlite3.connect(semantic_db)) as conn, conn:
        conn.execute("UPDATE mechanic_semantic_head SET revision=99")
    with pytest.raises(store.SemanticStoreError, match="不存在的修订"):
        read(semantic_db)


def test_datasets_missing_and_ready_are_read_only_with_revision_counts(semantic_db):
    before = semantic_db.read_bytes()
    with closing(sqlite3.connect(semantic_db)) as conn:
        states = store.semantic_datasets(conn)[store.COMPONENT]
        assert states["schema_version"] == 1
        assert all(row["state"] == "missing" and row["counts"] == {"facts": 0, "descriptors": 0, "heads": 0}
                   for row in states["lines"].values())
    assert semantic_db.read_bytes() == before
    install(semantic_db, descriptor_bundle())
    before = semantic_db.read_bytes()
    with closing(sqlite3.connect(semantic_db)) as conn:
        states = store.semantic_datasets(conn)[store.COMPONENT]["lines"]
        assert states["release"]["state"] == "ready"
        assert states["release"]["counts"] == {"facts": 1, "descriptors": 1, "heads": 2}
        assert states["release"]["execution_support"] == "not_evaluated"
        assert states["beta"]["state"] == "missing"
    assert semantic_db.read_bytes() == before


def test_datasets_future_and_corrupt_component_keep_diagnostic_without_writes(semantic_db):
    install(semantic_db)
    with closing(sqlite3.connect(semantic_db)) as conn, conn:
        conn.execute("UPDATE data_component SET schema_version=2 WHERE component=?", (store.COMPONENT,))
    before = semantic_db.read_bytes()
    with closing(sqlite3.connect(semantic_db)) as conn:
        row = store.semantic_datasets(conn)[store.COMPONENT]["lines"]["release"]
        assert row["state"] == "incompatible" and row["schema_version"] == 2
        assert row["diagnostic"]["reason"] == "semantic_schema_unsupported"
    assert semantic_db.read_bytes() == before
    with closing(sqlite3.connect(semantic_db)) as conn, conn:
        conn.execute("UPDATE data_component SET schema_version=1 WHERE component=?", (store.COMPONENT,))
        conn.execute("UPDATE mechanic_semantic_head SET revision=99")
    before = semantic_db.read_bytes()
    with closing(sqlite3.connect(semantic_db)) as conn:
        row = store.semantic_datasets(conn)[store.COMPONENT]["lines"]["release"]
        assert row["state"] == "invalid" and row["counts"] is None
        assert row["diagnostic"]["reason"] == "semantic_dataset_invalid"
        assert "不存在的修订" in row["diagnostic"]["message"]
    assert semantic_db.read_bytes() == before
