"""Independent EC16a query boundaries and arithmetic, using a tiny local DB."""

import copy
import hashlib
import json
import shutil
import sqlite3
from contextlib import closing

import pytest

from genshin_db import runtime_catalog as catalog
from genshin_db import runtime_queries as queries
from genshin_db import runtime_store as store
from genshin_db.equipment_queries import equipment_read_session
from tests.test_material_index_review import (  # noqa: F401
    ABILITY,
    MATERIAL,
    ability,
    build,
    energy_review_db,
    energy_review_repo,
    fake_repo,
    material,
    origin,
    rows,
    write,
)

# ruff: noqa: F811


def get(db, suffix="/onAdded/0/configID", **kwargs):
    return queries.query_runtime(db, {
        "action": "energy_generation_profile", "origin_reference_id": origin(db, suffix), **kwargs,
    })


def rebuild(repo, db, *, base=None, ratio=None, item=None):
    raw = ability()
    if base is not None:
        raw[0]["Default"]["onAdded"][0]["baseEnergy"] = base
    if ratio is not None:
        raw[0]["Default"]["onAdded"][0]["ratio"] = ratio
    write(repo, ABILITY, raw)
    if item is not None:
        write(repo, MATERIAL, [item, material(2022, "5")])
    build(db, repo)


def test_review_complete_receipt_reduces_three_facts_without_claiming_dispatch(energy_review_db, energy_review_repo):
    before = energy_review_db.read_bytes()
    first, second = get(energy_review_db), get(energy_review_db, "/onAdded/1/configID")
    assert [r["reference_projection"]["count"] for r in (first, second)] == [3, 4]
    for value in (first, second):
        projection = value["reference_projection"]
        assert projection["unit"]["element"] == "anemo"
        assert projection["particle_type"] == "particle"
        assert value["status"] == value["source_status"] == projection["status"] == "ready"
        assert value["whole_source_status"] == "partial"
        assert value["execution_support"] == value["parent_semantics"]["status"] == "not_evaluated"
        assert value["parent_semantics"]["conditions_not_executed"] is True
        assert projection["evidence_tier"] == "reference_implementation"
        entries = [value["origin"]["parent"], *value["material"]["candidates"]]
        for entry in entries:
            raw = (energy_review_repo / entry["source_path"]).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == entry["source_sha256"]
            parsed = catalog.resolve_pointer(json.loads(raw), entry["json_pointer"])
            assert parsed == entry["payload"]
            assert catalog.fingerprint(parsed) == entry["node_sha256"]
        assert value["content_fingerprint"] == catalog.fingerprint({k: v for k, v in value.items() if k != "content_fingerprint"})
    assert first["source_fingerprint"] != second["source_fingerprint"]
    assert first["origin"]["parent"] == second["origin"]["parent"]
    assert energy_review_db.read_bytes() == before


def test_review_random_parent_retained_and_not_promoted_by_leaf_numeric_success(energy_review_db):
    chosen = get(energy_review_db, "/successActions/0/configID")
    assert chosen["reference_projection"]["count"] == 4
    assert chosen["whole_source_status"] == "partial"
    assert chosen["execution_support"] == "not_evaluated"
    assert chosen["origin"]["parent"]["payload"]["onAdded"][0]["chance"] == .5
    assert chosen["origin"]["owner"]["payload"]["abilityName"] == "ReviewEnergyOwner"
    assert chosen["origin"]["parent"]["definition"]["definition_kind"] == "modifier"


@pytest.mark.parametrize("base,ratio,same,different,count,kind", [
    (9.0, 1, "3", "1", 3, "particle"), (3, 5, "3", "1", 5, "particle"),
    (9, 1, "9", "3", 1, "orb"), (18, 1, "9", "3", 2, "orb"),
    (20, 9, "9", "3", 20, "orb"),
])
def test_review_independent_integer_and_unit_oracles(energy_review_repo, energy_review_db, base, ratio, same, different, count, kind):
    rebuild(energy_review_repo, energy_review_db, base=base, ratio=ratio, item=material(same=same, different=different))
    result = get(energy_review_db)["reference_projection"]
    assert (result["status"], result["count"], result["particle_type"]) == ("ready", count, kind)


@pytest.mark.parametrize("base,ratio", [(True, 1), (9, True), (10**400, 1), (9, 10**400),
                                      (0, 1), (-9, 1), (10, 1), (63, 1), ("Energy", 1)])
def test_review_invalid_or_out_of_scope_numeric_values_stay_unknown(energy_review_repo, energy_review_db, base, ratio):
    rebuild(energy_review_repo, energy_review_db, base=base, ratio=ratio)
    result = get(energy_review_db)
    assert result["source_status"] == "ready"
    assert result["reference_projection"]["status"] == "pending"
    assert result["reference_projection"]["count"] is None
    assert result["reference_projection"]["particle_type"] is None
    assert result["execution_support"] == "not_evaluated"


def test_review_missing_ratio_does_not_copy_simulator_default_one(energy_review_repo, energy_review_db):
    raw = ability()
    del raw[0]["Default"]["onAdded"][0]["ratio"]
    write(energy_review_repo, ABILITY, raw)
    build(energy_review_db, energy_review_repo)
    result = get(energy_review_db)["reference_projection"]
    assert result["field_states"]["ratio"] == "absent"
    assert result["status"] == "pending" and result["count"] is None


@pytest.mark.parametrize("change", ["first_unknown", "tail_unknown", "second_effect", "nonempty_no_op", "cur_avatar", "frozen", "nonstandard"])
def test_review_whole_material_usage_is_required_not_only_first_divisor(energy_review_repo, energy_review_db, change):
    item = material()
    if change == "first_unknown":
        item["itemUse"][0]["futureCondition"] = True
    elif change == "tail_unknown":
        item["itemUse"][1]["futureEffect"] = True
    elif change == "second_effect":
        item["itemUse"].append(copy.deepcopy(item["itemUse"][0]))
    elif change == "nonempty_no_op":
        item["itemUse"][1]["useParam"][0] = "1"
    elif change == "cur_avatar":
        item["useTarget"] = "ITEM_USE_TARGET_CUR_AVATAR"
    elif change == "frozen":
        item["itemUse"][0]["useParam"][0] = "6"
    else:
        item["itemUse"][0]["useParam"][1:3] = ["6", "2"]
    rebuild(energy_review_repo, energy_review_db, item=item)
    value = get(energy_review_db)
    assert value["reference_projection"]["status"] == "pending"
    assert value["reference_projection"]["count"] is None
    assert value["material"]["candidates"][0]["payload"] == item


def test_review_missing_material_is_pending_but_missing_index_relation_is_corruption(energy_review_repo, energy_review_db):
    write(energy_review_repo, MATERIAL, [material(2022, "5")])
    build(energy_review_db, energy_review_repo)
    missing = get(energy_review_db)
    assert missing["material"]["status"] == "missing"
    assert missing["source_status"] == "pending" and missing["reference_projection"]["count"] is None
    with closing(sqlite3.connect(energy_review_db)) as connection, connection:
        connection.execute("DELETE FROM runtime_material_reference WHERE legacy_reference_id=?", (origin(energy_review_db),))
    with pytest.raises(catalog.RuntimeConfigError) as error:
        get(energy_review_db)
    assert error.value.reason == "dataset_invalid"


def test_review_self_consistent_material_index_swap_cannot_override_raw_identity(energy_review_db):
    with closing(sqlite3.connect(energy_review_db)) as connection, connection:
        records = connection.execute("SELECT definition_id,exact_key_json FROM runtime_definition WHERE definition_kind='material_record'").fetchall()
        by_key = {key: ident for ident, key in records}
        for key, replacement in (("2021", "2022"), ("2022", "2021")):
            connection.execute("UPDATE runtime_definition SET exact_key_json=? WHERE definition_id=?", (replacement, by_key[key]))
            connection.execute(
                "UPDATE runtime_material_reference SET resolved_definition_id=?,candidate_ids_json=? WHERE target_key_json=?",
                (by_key[replacement], json.dumps([by_key[replacement]]), key),
            )
    with pytest.raises(catalog.RuntimeConfigError) as error:
        get(energy_review_db)
    assert error.value.reason == "dataset_invalid"


def test_review_same_session_shared_read_does_not_share_mutable_receipts(energy_review_db, monkeypatch):
    real, calls = queries.validate_runtime, []

    def validate(*args, **kwargs):
        calls.append(True)
        return real(*args, **kwargs)

    monkeypatch.setattr(queries, "validate_runtime", validate)
    with equipment_read_session(energy_review_db):
        expected = get(energy_review_db)
        poisoned = get(energy_review_db)
        poisoned["origin"]["parent"]["payload"]["abilityName"] = "untrusted mutation"
        poisoned["snapshot"]["counts"].clear()
        poisoned["material"]["candidates"][0]["payload"]["itemUse"].clear()
        poisoned["reference_projection"]["count"] = 9000
        poisoned["reference_contract"].clear()
        assert get(energy_review_db) == expected
    assert calls == [True]


def test_review_index5_batch_upgrade_is_local_and_existing_gadget_still_works(energy_review_repo, tmp_path, monkeypatch):
    db = tmp_path / "prior.db"
    with monkeypatch.context() as patch:
        patch.setattr(catalog, "INDEXER_VERSION", "5")
        patch.setattr(store, "INDEXER_VERSION", "5")
        build(db, energy_review_repo)
    before = db.read_bytes()
    value = queries.query_runtime(db, {"action": "batch", "items": [
        {"id": "new", "request": {"action": "energy_generation_profile", "origin_reference_id": origin(db)}},
        {"id": "existing", "request": {"action": "gadget_source_profile", "gadget_id": 101}},
    ]})
    assert value["status"] == "partial"
    assert value["items"][0]["error"]["reason"] == "material_index_upgrade_required"
    assert value["items"][1]["status"] == "ok"
    assert value["items"][1]["data"]["status"] == "ready"
    assert db.read_bytes() == before


@pytest.mark.parametrize("location", ["parent", "material", "item_use_count"])
def test_review_public_source_limits_reject_whole_query_without_truncated_ready(energy_review_repo, energy_review_db, location):
    if location == "parent":
        raw = ability()
        raw[0]["Default"]["unknownOriginal"] = "原" * 100000
        write(energy_review_repo, ABILITY, raw)
    else:
        item = material()
        if location == "material":
            item["unknownOriginal"] = "物" * 100000
        else:
            item["itemUse"].extend([{"useParam": ["", ""]} for _ in range(6)])
        write(energy_review_repo, MATERIAL, [item, material(2022, "5")])
    build(energy_review_db, energy_review_repo)
    with pytest.raises(catalog.RuntimeConfigError) as error:
        get(energy_review_db)
    assert error.value.status == 413 and error.value.reason == "response_too_large"
    assert len(catalog.dumps(error.value.details).encode("utf-8")) < 2000


def test_review_scene_gate_does_not_become_implicit_allow_by_ready_numeric_projection(energy_review_repo, energy_review_db):
    raw = ability()
    raw[0]["Default"]["onAdded"][0]["dropType"] = "BigWorldOnly"
    write(energy_review_repo, ABILITY, raw)
    build(energy_review_db, energy_review_repo)
    data = get(energy_review_db)
    assert data["reference_projection"]["count"] == 3
    assert data["origin"]["operation"]["raw"]["dropType"] == "BigWorldOnly"
    assert data["parent_semantics"]["conditions_not_executed"] is True
    assert data["execution_support"] == "not_evaluated"


def test_review_source_identity_ignores_display_metadata_but_rehashes_actual_parent(energy_review_db):
    from genshin_db.runtime_energy_generation import energy_generation_source_payload

    value = get(energy_review_db)
    reference = catalog.fingerprint(energy_generation_source_payload(value))
    assert reference == value["source_fingerprint"]
    display = copy.deepcopy(value)
    display["snapshot"]["built_at"] = "2099-01-01T00:00:00Z"
    display["snapshot"]["counts"] = {"display_only": 999}
    display["issues"] = [{"code": "display-note", "path": "display"}]
    assert catalog.fingerprint(energy_generation_source_payload(display)) == reference
    # Leave all echo hashes unchanged: content identity must use actual raw.
    changed = copy.deepcopy(value)
    changed["origin"]["parent"]["payload"]["unexaminedPredicate"] = {"enabled": True}
    assert catalog.fingerprint(energy_generation_source_payload(changed)) != reference


@pytest.mark.parametrize("bad_id", [None, True, "2021", 0, -1, 2**53])
def test_review_invalid_original_config_id_is_pending_source_not_database_corruption(energy_review_repo, energy_review_db, bad_id):
    raw = ability()
    raw[0]["Default"]["onAdded"][0]["configID"] = bad_id
    write(energy_review_repo, ABILITY, raw)
    build(energy_review_db, energy_review_repo)
    value = get(energy_review_db)
    assert value["source_status"] == "pending"
    assert value["reference_projection"]["status"] == "pending"
    assert value["reference_projection"]["count"] is None
    assert value["origin"]["operation"]["raw"]["configID"] == bad_id
    assert value["execution_support"] == "not_evaluated"


@pytest.mark.parametrize("stage", ["legacy_edges", "material_edges", "material_candidates"])
def test_review_relation_budgets_are_checked_before_full_reconstruction_or_lookup(energy_review_db, monkeypatch, stage):
    from genshin_db import runtime_energy_generation as energy

    ident = origin(energy_review_db)
    parent = next(r["from_definition_id"] for r in rows(energy_review_db, "runtime_reference")
                  if r["reference_id"] == ident)
    legacy_count = len(rows(energy_review_db, "runtime_reference", "WHERE from_definition_id=?", (parent,)))

    def forbidden(*args, **kwargs):
        pytest.fail(f"{stage} fully materialized before its declared budget")

    if stage == "legacy_edges":
        monkeypatch.setitem(energy.ENERGY_LIMITS, "max_edges", legacy_count - 1)
        monkeypatch.setattr(queries, "_references", forbidden)
    elif stage == "material_edges":
        monkeypatch.setitem(energy.ENERGY_LIMITS, "max_edges", legacy_count)
        monkeypatch.setattr(store, "validate_definition_material_references", forbidden)
    else:
        monkeypatch.setitem(energy.ENERGY_LIMITS, "max_candidates", 0)
        monkeypatch.setattr(queries, "_lookup", forbidden)
    with pytest.raises(catalog.RuntimeConfigError) as error:
        get(energy_review_db)
    assert error.value.reason == "response_too_large" and error.value.status == 413


def test_review_exact_combined_edge_and_candidate_boundaries_remain_usable(energy_review_db, monkeypatch):
    from genshin_db import runtime_energy_generation as energy

    expected = get(energy_review_db)
    selected = origin(energy_review_db)
    parent = next(r["from_definition_id"] for r in rows(energy_review_db, "runtime_reference")
                  if r["reference_id"] == selected)
    combined = sum(len(rows(energy_review_db, table, "WHERE from_definition_id=?", (parent,)))
                   for table in ("runtime_reference", "runtime_material_reference"))
    monkeypatch.setitem(energy.ENERGY_LIMITS, "max_edges", combined)
    monkeypatch.setitem(energy.ENERGY_LIMITS, "max_candidates", 1)
    at_boundary = get(energy_review_db)
    assert at_boundary["reference_projection"] == expected["reference_projection"]
    assert at_boundary["source_fingerprint"] == expected["source_fingerprint"]
    monkeypatch.setitem(energy.ENERGY_LIMITS, "max_edges", combined - 1)
    with pytest.raises(catalog.RuntimeConfigError) as error:
        get(energy_review_db)
    assert error.value.reason == "response_too_large"


def _corrupt_unselected_material_candidates(repo, db, count):
    raw = ability()
    raw[0]["Default"]["onAdded"][1]["configID"] = 2022
    write(repo, ABILITY, raw)
    write(repo, MATERIAL, [material(), material(2022, "5"),
                          *(material(3000 + index, "5") for index in range(count - 1))])
    build(db, repo)
    with closing(sqlite3.connect(db)) as connection, connection:
        connection.execute(
            "UPDATE runtime_definition SET exact_key_json='2022' "
            "WHERE definition_kind='material_record' AND CAST(exact_key_json AS INTEGER)>=3000")


def test_review_unselected_same_parent_candidates_are_bounded_before_graph_validation(energy_review_repo, energy_review_db, monkeypatch):
    _corrupt_unselected_material_candidates(energy_review_repo, energy_review_db, count=9)

    def forbidden(*args, **kwargs):
        pytest.fail("unselected sibling candidates reached unbounded graph validation")

    monkeypatch.setattr(store, "validate_definition_material_references", forbidden)
    with pytest.raises(catalog.RuntimeConfigError) as error:
        get(energy_review_db)  # Selects 2021, while the sibling 2022 has 9 candidates.
    assert error.value.reason == "response_too_large" and error.value.status == 413


def test_review_under_budget_ambiguous_index_is_corruption_not_valid_partial_source(energy_review_repo, energy_review_db):
    _corrupt_unselected_material_candidates(energy_review_repo, energy_review_db, count=2)
    with pytest.raises(catalog.RuntimeConfigError) as error:
        get(energy_review_db)
    assert error.value.reason == "dataset_invalid" and error.value.status == 500


@pytest.mark.parametrize("request_data", [{}, {"origin_reference_id": None}, {"origin_reference_id": True},
                                    {"origin_reference_id": "a" * 63}, {"origin_reference_id": "A" * 64},
                                    {"origin_reference_id": "a" * 64, "count": 3}])
def test_review_invalid_input_cannot_open_database(tmp_path, monkeypatch, request_data):
    monkeypatch.setattr(queries, "_read", lambda *args, **kwargs: pytest.fail("invalid input reached DB"))
    with pytest.raises(ValueError):
        queries.query_runtime(tmp_path / "missing.db", {"action": "energy_generation_profile", **request_data})


def test_review_actual_cli_output_file_complete_stdout_retains_model_limits(
    energy_review_db, tmp_path, capsys, monkeypatch
):
    from genshin_app.cli import main

    # The isolated review database is intentionally not a published App/DB
    # pair.  This test owns that fixture boundary so the real startup gate can
    # remain fail-closed.
    monkeypatch.setattr(
        "genshin_app.cli._require_runtime_database", lambda _data_dir: {"can_start": True}
    )

    folder = tmp_path / "cli"
    folder.mkdir()
    db = folder / "genshin.db"
    shutil.copyfile(energy_review_db, db)
    request, output = folder / "request.json", folder / "energy-source.json"
    request.write_text(json.dumps({"action": "energy_generation_profile", "origin_reference_id": origin(db)}), "utf-8")
    assert main(["--data-dir", str(folder), "db", "runtime", "--input", str(request), "--output", str(output), "--summary"]) == 0
    captured = capsys.readouterr()
    assert not captured.err
    assert len(captured.out.encode("utf-8")) < 4000
    assert "ReviewEnergyOwner" not in captured.out
    summary = json.loads(captured.out)["data"]
    assert summary["whole_source_status"] == "partial" and summary["execution_support"] == "not_evaluated"
    complete = json.loads(output.read_text("utf-8"))
    assert complete["origin"]["parent"]["payload"]["abilityName"] == "ReviewEnergyOwner"
    assert complete["reference_projection"]["count"] == 3
