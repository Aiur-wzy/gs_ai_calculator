"""Finite source units are reference projections, never native trigger proofs."""
from __future__ import annotations

import copy
import json
import sqlite3
from contextlib import closing

import pytest

from genshin_db import runtime_catalog as catalog
from genshin_db import runtime_energy_generation as energy
from genshin_db import runtime_store as store
from genshin_db.equipment_queries import equipment_read_session
from genshin_db.runtime_queries import query_runtime, runtime_capabilities
from tests.test_pipeline import fake_repo  # noqa: F401
from tests.test_runtime_material_index import ABILITY, material_repo  # noqa: F401
from tests.test_runtime_object_index import build, rows, write

# ruff: noqa: F811


@pytest.fixture
def energy_repo(material_repo):
    materials = json.loads((material_repo / catalog.MATERIAL_EXCEL_PATH).read_text("utf8"))
    materials[1] = {**copy.deepcopy(materials[0]), "id": 2022}
    materials[1]["itemUse"][0]["useParam"][0] = "5"
    write(material_repo, catalog.MATERIAL_EXCEL_PATH, materials)
    return material_repo


@pytest.fixture
def energy_db(tmp_path, energy_repo):
    db = tmp_path / "energy.db"
    build(db, energy_repo)
    return db


def origin(db, suffix="/onAdded/0/configID"):
    return next(r["legacy_reference_id"] for r in rows(db, "runtime_material_reference")
                if r["json_pointer"].endswith(suffix) and "/modifiers/" not in r["json_pointer"])


def query(db, ident=None):
    return query_runtime(db, {"action": "energy_generation_profile", "origin_reference_id": ident or origin(db)})


def change_source(repo, path, change):
    value = json.loads((repo / path).read_text("utf8"))
    change(value)
    write(repo, path, value)


def test_exact_material_reference_projection_preserves_unknown_parent(energy_db):
    data = query(energy_db)
    assert data["status"] == data["source_status"] == data["reference_projection"]["status"] == "ready"
    assert data["reference_projection"]["count"] == 3
    assert data["reference_projection"]["particle_type"] == "particle"
    assert data["reference_projection"]["unit"] == {"element": "anemo", "same_element_energy": 3, "different_element_energy": 1}
    assert data["whole_source_status"] == "partial" and data["execution_support"] == "not_evaluated"
    assert data["parent_semantics"]["conditions_not_executed"]
    assert data["origin"]["owner"] is None
    assert data["material"]["field_states"]["useOnGain"] == "absent"
    assert [r["state"] for r in data["material"]["item_use_entries"]] == ["effective", "empty_placeholder", "empty_placeholder"]
    assert catalog.fingerprint(energy.energy_generation_source_payload(data)) == data["source_fingerprint"]
    assert catalog.fingerprint({k: v for k, v in data.items() if k != "content_fingerprint"}) == data["content_fingerprint"]


def test_two_leaves_do_not_collapse_and_modifier_keeps_owner(energy_db):
    a, b = query(energy_db), query(energy_db, origin(energy_db, "/onAdded/1/configID"))
    assert (a["reference_projection"]["count"], b["reference_projection"]["count"]) == (3, 4)
    assert a["source_fingerprint"] != b["source_fingerprint"]
    assert b["origin"]["operation"]["raw"]["predicates"][0]["$type"] == "UnknownPredicate"
    ident = next(r["legacy_reference_id"] for r in rows(energy_db, "runtime_material_reference") if "/modifiers/" in r["json_pointer"])
    nested = query(energy_db, ident)
    assert nested["reference_projection"]["count"] == 1
    assert nested["origin"]["parent"]["definition"]["definition_kind"] == "modifier"
    assert nested["origin"]["owner"]["payload"]["abilityName"] == "ParticleCreator"


@pytest.mark.parametrize("value,ready", [(9.0, True), (True, False), (0, False), (-1, False),
                                        (10**400, False), (9.1, False), (None, False), ("9", False), (10, False), (63, False)])
def test_numeric_domain_is_bounded_and_typed(energy_repo, tmp_path, value, ready):
    change_source(energy_repo, ABILITY, lambda v: v[0]["Default"]["onAdded"][0].update(baseEnergy=value))
    db = tmp_path / "values.db"
    build(db, energy_repo)
    data = query(db)
    assert (data["reference_projection"]["status"] == "ready") is ready
    assert data["reference_projection"]["count"] == (3 if ready else None)
    assert data["reference_projection"]["raw_values"]["base_energy"] == value


@pytest.mark.parametrize("case", ["no_ratio", "null_use", "empty_use", "first_unknown", "extra_active", "tail_null", "tail_param", "unknown_element", "unknown_unit", "other_target"])
def test_missing_or_unknown_semantics_never_default(energy_repo, tmp_path, case):
    if case == "no_ratio":
        change_source(energy_repo, ABILITY, lambda v: v[0]["Default"]["onAdded"][0].pop("ratio"))
    else:
        def change(v):
            m = v[0]
            if case == "null_use": m["itemUse"] = None
            if case == "empty_use": m["itemUse"] = []
            if case == "first_unknown": m["itemUse"][0]["unknownExecution"] = True
            if case == "extra_active": m["itemUse"].append(copy.deepcopy(m["itemUse"][0]))
            if case == "tail_null": m["itemUse"][1]["useOp"] = None
            if case == "tail_param": m["itemUse"][1]["useParam"][0] = "1"
            if case == "unknown_element": m["itemUse"][0]["useParam"][0] = "6"
            if case == "unknown_unit": m["itemUse"][0]["useParam"][1] = "2"
            if case == "other_target": m["useTarget"] = "ITEM_USE_TARGET_CUR_AVATAR"
        change_source(energy_repo, catalog.MATERIAL_EXCEL_PATH, change)
    db = tmp_path / "pending.db"
    build(db, energy_repo)
    data = query(db)
    assert data["status"] == "pending" and data["reference_projection"]["count"] is None
    assert data["source_status"] == "ready"


def test_explicit_empty_none_and_standard_orb_are_supported(energy_repo, tmp_path):
    def change(v):
        v[0]["itemUse"][0]["useParam"][1:3] = ["9", "3"]
        v[0]["itemUse"][1]["useOp"] = "ITEM_USE_NONE"
    change_source(energy_repo, catalog.MATERIAL_EXCEL_PATH, change)
    db = tmp_path / "orb.db"
    build(db, energy_repo)
    data = query(db)
    assert (data["reference_projection"]["count"], data["reference_projection"]["particle_type"]) == (1, "orb")


def test_target_missing_is_pending_but_missing_material_index_is_invalid(energy_repo, tmp_path):
    change_source(energy_repo, catalog.MATERIAL_EXCEL_PATH, lambda v: v.pop(0))
    db = tmp_path / "missing.db"
    build(db, energy_repo)
    ident = origin(db)
    assert query(db, ident)["source_status"] == "pending"
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("DELETE FROM runtime_material_reference WHERE legacy_reference_id=?", (ident,))
    with pytest.raises(catalog.RuntimeConfigError) as error:
        query(db, ident)
    assert error.value.reason == "dataset_invalid"


def test_caps_batch_single_snapshot_and_returned_state_isolation(energy_db, monkeypatch):
    import genshin_db.runtime_queries as queries
    calls, original = [], queries.validate_runtime
    def tracked(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(queries, "validate_runtime", tracked)
    ident = origin(energy_db)
    request = {"action": "energy_generation_profile", "origin_reference_id": ident}
    with equipment_read_session(energy_db):
        a = query(energy_db)
        result = query_runtime(energy_db, {"action": "batch", "items": [{"id": str(i), "request": request} for i in range(8)]})
        assert all(item["data"] == a for item in result["items"])
        result["items"][0]["data"]["snapshot"]["counts"]["runtime_definition"] = -1
        result["items"][1]["data"]["material"]["candidates"][0]["payload"]["id"] = -1
        assert query(energy_db) == a
    assert len(calls) == 1
    caps = runtime_capabilities()
    assert caps["required"]["energy_generation_profile"] == ["origin_reference_id"]
    assert "energy_generation_profile" in caps["batch"]["actions"]


def test_old_index_upgrade_is_item_error_and_next_query_runs(energy_repo, tmp_path, monkeypatch):
    db = tmp_path / "old.db"
    with monkeypatch.context() as m:
        m.setattr(catalog, "INDEXER_VERSION", "5")
        m.setattr(store, "INDEXER_VERSION", "5")
        build(db, energy_repo)
    legacy = rows(db, "runtime_reference", "WHERE source_path=? ORDER BY json_pointer", (ABILITY,))[0]
    batch = query_runtime(db, {"action": "batch", "items": [
        {"id": "new", "request": {"action": "energy_generation_profile", "origin_reference_id": legacy["reference_id"]}},
        {"id": "old", "request": {"action": "gadget_source_profile", "gadget_id": 2021}}]})
    assert batch["items"][0]["error"]["reason"] == "material_index_upgrade_required"
    assert batch["items"][1]["status"] == "ok"


@pytest.mark.parametrize("field,value", [("max_nodes", 1), ("max_bytes", 32), ("max_edges", 1), ("max_item_use", 1), ("max_issues", 0)])
def test_limits_fail_without_silent_partial_source(energy_db, monkeypatch, field, value):
    monkeypatch.setitem(energy.ENERGY_LIMITS, field, value)
    ident = origin(energy_db)
    if field == "max_issues":
        monkeypatch.setattr(energy, "_number", lambda _: False)
    with pytest.raises(catalog.RuntimeConfigError) as error:
        query(energy_db, ident)
    assert error.value.status == 413


@pytest.mark.parametrize("bad", [None, True, "", " ", "a" * 63, "A" * 64, 123])
def test_invalid_selection_is_rejected_before_read(monkeypatch, bad):
    import genshin_db.runtime_queries as queries
    monkeypatch.setattr(queries, "_read", lambda *a, **k: pytest.fail("must validate first"))
    with pytest.raises(ValueError):
        energy.get_runtime_energy_generation_profile("missing.db", origin_reference_id=bad)


def test_reference_resource_preserves_primary_metadata_and_raw_pairs():
    recipe = energy._recipe()
    for source in recipe["reference_sources"]:
        assert "text" not in source and len(source["sha256"]) == 64 and source["bytes"] > 0
        assert recipe["reference_commit"] in source["url"]
    pairs = recipe["evidence"]["operation_pairs"]
    assert len(pairs) == 7
    for pair in pairs:
        parent = next(p for p in recipe["evidence"]["parents"] if p["source_path"] == pair["source_path"]
                      and pair["new_pointer"].startswith(p["new_pointer"] + "/"))
        for era in ("old", "new"):
            raw = catalog.resolve_pointer(parent[era + "_raw"], pair[era + "_pointer"][len(parent[era + "_pointer"]):])
            assert catalog.fingerprint(raw) == pair[era + "_node_sha256"]
        raw = catalog.resolve_pointer(parent["new_raw"], pair["new_pointer"][len(parent["new_pointer"]):])
        issues = []
        fields, status = energy._fields(raw, recipe["field_mapping"]["upstream_commit"], lambda *a, sink=issues: sink.append(a))
        assert status == "ready" and not issues
        assert fields["baseEnergy"]["raw_value"] == pair["base_energy_raw"]
        assert fields["ratio"]["raw_value"] == pair["ratio_raw"]
    issues = []
    assert energy._fields(raw, "b" * 40, lambda *a: issues.append(a))[1] == "pending"
    assert issues


@pytest.mark.parametrize("value", [None, "", True, 0, -1, 2**53, "2021", 2021.0])
def test_invalid_raw_material_ids_are_auditable_pending(energy_repo, tmp_path, value):
    change_source(energy_repo, ABILITY, lambda v: v[0]["Default"]["onAdded"][0].update(configID=value))
    db = tmp_path / "bad-id.db"
    build(db, energy_repo)
    data = query(db)
    assert data["status"] == data["source_status"] == "pending"
    assert data["material"]["status"] in ("source_empty", "invalid_reference_form")
    assert data["reference_projection"]["count"] is None
    assert catalog.dumps(data["origin"]["operation"]["raw"]["configID"]) == catalog.dumps(value)


def test_complete_obfuscated_parent_in_same_commit_database(energy_repo, tmp_path):
    recipe = energy._recipe()
    parent = next(p for p in recipe["evidence"]["parents"] if p["source_path"].endswith("ConfigAbility_Avatar_Kazuha.json")
                  and p["new_pointer"] == "/3/Default")
    write(energy_repo, ABILITY, [{"Default": parent["new_raw"]}])
    db = tmp_path / "raw-current.db"
    build(db, energy_repo, upstream_commit=recipe["field_mapping"]["upstream_commit"])
    links = rows(db, "runtime_material_reference", "WHERE source_path=? ORDER BY json_pointer", (ABILITY,))
    out = [query(db, link["legacy_reference_id"]) for link in links]
    assert len(out) == 2 and [p["reference_projection"]["count"] for p in out] == [3, 4]
    assert all(p["status"] == "ready" and p["whole_source_status"] == "partial" for p in out)
    assert out[0]["origin"]["owner"]["payload"] == parent["new_raw"]


def test_cli_full_file_and_short_summary_retain_different_scopes(
    energy_db, tmp_path, capsys, monkeypatch
):
    from genshin_app.cli import main
    # This test exercises runtime response truncation against a synthetic
    # indexed database, not installation admission.  Keep the production
    # release gate intact and make the fixture boundary explicit.
    monkeypatch.setattr(
        "genshin_app.cli._require_runtime_database", lambda _data_dir: {"can_start": True}
    )
    # CLI resolves genshin.db in its data directory; use a separate fixture copy.
    db_dir = tmp_path / "cli"
    db_dir.mkdir()
    (db_dir / "genshin.db").write_bytes(energy_db.read_bytes())
    request, output = tmp_path / "query.json", tmp_path / "result.json"
    request.write_text(json.dumps({"action": "energy_generation_profile", "origin_reference_id": origin(energy_db)}), "utf8")
    assert main(["--data-dir", str(db_dir), "db", "runtime", "--input", str(request),
                 "--output", str(output), "--summary"]) == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)["data"]
    assert not captured.err and len(captured.out.encode("utf8")) < 4000
    assert summary["reference_projection"]["count"] == 3
    assert summary["whole_source_status"] == "partial" and summary["execution_support"] == "not_evaluated"
    assert "ParticleCreator" not in captured.out and "UnknownPredicate" not in captured.out
    assert json.loads(output.read_text("utf8")) == query(energy_db)


def test_semantic_fingerprint_ignores_display_state_but_keeps_full_parent(energy_db):
    result = query(energy_db)
    changed = copy.deepcopy(result)
    changed["snapshot"]["built_at"] = "different-build-time"
    changed["snapshot"]["counts"] = {"different": -1}
    changed["issues"] = [{"code": "display_only"}]
    assert catalog.fingerprint(energy.energy_generation_source_payload(changed)) == result["source_fingerprint"]
    changed["origin"]["parent"]["payload"]["onAdded"][1]["predicates"][0]["value"] = True
    assert catalog.fingerprint(energy.energy_generation_source_payload(changed)) != result["source_fingerprint"]


@pytest.mark.parametrize("stage", ["legacy", "material", "candidates"])
def test_relation_budget_precedes_full_helpers(energy_db, monkeypatch, stage):
    import genshin_db.runtime_queries as queries
    ident = origin(energy_db)
    parent = next(r["from_definition_id"] for r in rows(energy_db, "runtime_reference") if r["reference_id"] == ident)
    legacy_count = len(rows(energy_db, "runtime_reference", "WHERE from_definition_id=?", (parent,)))
    def forbidden(*args, **kwargs):
        pytest.fail("budget must stop before reconstructing or loading the full collection")
    if stage == "legacy":
        monkeypatch.setitem(energy.ENERGY_LIMITS, "max_edges", 0)
        monkeypatch.setattr(queries, "_references", forbidden)
    elif stage == "material":
        monkeypatch.setitem(energy.ENERGY_LIMITS, "max_edges", legacy_count)
        monkeypatch.setattr(store, "validate_definition_material_references", forbidden)
    else:
        monkeypatch.setitem(energy.ENERGY_LIMITS, "max_candidates", 0)
        monkeypatch.setattr(store, "validate_definition_material_references", forbidden)
        monkeypatch.setattr(queries, "_lookup", forbidden)
    with pytest.raises(catalog.RuntimeConfigError) as error:
        query(energy_db, ident)
    assert error.value.reason == "response_too_large"


def test_exact_edge_and_candidate_budget_keeps_full_proof(energy_db, monkeypatch):
    baseline, ident = query(energy_db), origin(energy_db)
    parent = baseline["origin"]["parent"]["definition"]["definition_id"]
    count = sum(len(rows(energy_db, table, "WHERE from_definition_id=?", (parent,)))
                for table in ("runtime_reference", "runtime_material_reference"))
    monkeypatch.setitem(energy.ENERGY_LIMITS, "max_edges", count)
    monkeypatch.setitem(energy.ENERGY_LIMITS, "max_candidates", 1)
    data = query(energy_db, ident)
    assert data["source_fingerprint"] == baseline["source_fingerprint"]
    assert data["origin"] == baseline["origin"] and data["status"] == "ready"
