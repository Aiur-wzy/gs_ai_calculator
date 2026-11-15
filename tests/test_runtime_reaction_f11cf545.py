"""Exact new-snapshot swirl provenance, never an ability executor."""
from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from genshin_db.runtime_catalog import RuntimeConfigError, fingerprint, resolve_pointer
from genshin_db.runtime_queries import query_runtime
from genshin_db.runtime_reaction_damage import _recipe
from genshin_runtime.normalization import Profile, for_commit
from scripts.normalization_review_alignment import body_signature
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401

# ruff: noqa: F811
ROOT = Path(__file__).resolve().parents[1]
COMMIT = "f11cf545a7eee32a29999f960ebfba305f896ed3"
OLD = "224708edc8056f27e9ffc4eacabe797d75274636"
SOURCE = "BinOutput/Ability/Temp/ConfigAbility_Element.json"
ATTENUATION = "ExcelBinOutput/AttackAttenuationExcelConfigData.json"
GROUP = "ElementReaction_Damage_Attenuation_1"


def source_fixture():
    return json.loads((ROOT / "tests/fixtures/runtime_reaction_f11cf545.json").read_text("utf8"))


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@pytest.fixture
def f11_reaction_db(tmp_path, fake_repo, stub_fetcher):
    stub_fetcher["commit"] = COMMIT
    fixture = source_fixture()
    raw = [{"Default": record["raw"]} for record in (*fixture["records"], *fixture["separate_star_records"])]
    path = fake_repo / SOURCE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw), "utf8")
    (fake_repo / ATTENUATION).write_text(json.dumps([
        {"group": GROUP, "resetCycle": .5, "durabilitySequence": [1] * 10,
         "damageSequence": [1, 1] + [0] * 8, "enbreakSequence": [1] * 10}]), "utf8")
    fixture_builder.build_database(tmp_path)
    return tmp_path / "genshin.db"


def query(db, element="hydro"):
    return query_runtime(db, {"action": "reaction_damage_profile", "element": element})


@pytest.mark.parametrize("element,raw_element,present", [
    ("hydro", "Water", True), ("cryo", "Ice", False), ("pyro", "Fire", False),
])
def test_new_exact_recipe_reads_each_own_ordinary_branch(f11_reaction_db, element, raw_element, present):
    before = sha(f11_reaction_db)
    value = query(f11_reaction_db, element)
    assert value["mapping_evidence"]["commit"] == COMMIT
    assert value["mapping_evidence"]["recipe_id"] == "ordinary-swirl-origin-source-f11cf545-v3"
    assert value["sources"]["ability"]["node_sha256"] == _recipe(commit=COMMIT)["elements"][element]["expected_node_sha256"]
    assert f"/Swirl_{raw_element}/" in value["sources"]["origin_attack"]["json_pointer"]
    assert "_Alter" not in value["sources"]["origin_attack"]["json_pointer"]
    assert value["projection"]["damage_sequence"] == [1, 1] + [0] * 8
    assert value["projection"]["attacker"] == {"present": present, "value": "Self" if present else None,
                                                "default": "not_inferred"}
    assert value["projection"]["factor"]["raw"] == pytest.approx(.6)
    assert value["execution"]["ability_execution"] == "pending"
    assert value["execution"]["owner_resolution"] == value["execution"]["reaction_dispatch"] == "not_evaluated"
    assert len(value["separate_star_abilities"]) == 2
    assert all(row["execution"] == "not_evaluated" for row in value["separate_star_abilities"])
    assert len(json.dumps(value, ensure_ascii=False).encode()) < 131072
    assert sha(f11_reaction_db) == before


def test_old_recipe_identity_and_mapping_are_not_replaced_by_new_commit():
    old = copy.deepcopy(_recipe(commit=OLD))
    new = _recipe(commit=COMMIT)
    assert new["recipe_version"] == 3 and old["recipe_version"] == 2
    assert set(new["fields"].values()) == set(old["fields"].values())
    assert set(new["types"].values()) == set(old["types"].values())
    assert len(new["fields"]) == 21 and len(new["types"]) == 8
    assert _recipe("pyro", commit=OLD)["recipe_id"] == "ordinary-swirl-pyro-origin-source-224708ed-v2"
    assert _recipe(commit=OLD) == old
    assert set(new["elements"]) == {"hydro", "cryo", "pyro"}
    for unreviewed in (COMMIT[:12], "f" * 40, OLD[:-1] + "7"):
        with pytest.raises(RuntimeConfigError) as error:
            _recipe(commit=unreviewed)
        assert error.value.reason == "unsupported_source"


def test_exact_full_raw_nodes_and_independent_known_token_witnesses():
    fixture, recipe, legacy = source_fixture(), _recipe(commit=COMMIT), _recipe(commit=OLD)
    records = {row["ability_name"]: row for row in fixture["records"]}
    assert len(records) == 4
    old_normalization = for_commit(OLD)
    profile = Profile("old-scope-proof", (OLD,), {**old_normalization.fields, **legacy["fields"]},
                      {**old_normalization.types, **legacy["types"]})
    for whole in recipe["evidence_reference"]["whole_ability_evidence"]:
        record = records[whole["ability_name"]]
        assert fingerprint(record["raw"]) == whole["new_node_sha256"]
        assert fingerprint(record["old_raw"]) == whole["old_node_sha256"]
        assert body_signature(record["raw"]) == body_signature(record["old_raw"]) == whole["whole_identifier_erased_body_sha256"]
    for kind, raw_key, semantic_key in (("field_evidence", "raw_key", "old_semantic_key"),
                                        ("type_evidence", "raw_type", "old_semantic_type")):
        for proof in recipe[kind]:
            assert proof[semantic_key] in (legacy["fields"] if kind == "field_evidence" else legacy["types"]).values()
            witnesses = [proof["anchor"], *([proof["holdout_anchor"]] if "holdout_anchor" in proof else [])]
            for witness in witnesses:
                record = records[witness["ability_name"]]
                a = witness["old_pointer"].removeprefix(record["old_pointer"])
                b = witness["new_pointer"].removeprefix(record["new_pointer"])
                old_value, new_value = resolve_pointer(record["old_raw"], a), resolve_pointer(record["raw"], b)
                old_parent = resolve_pointer(record["old_raw"], a.rsplit("/", 1)[0])
                new_parent = resolve_pointer(record["raw"], b.rsplit("/", 1)[0])
                assert fingerprint(old_value) == witness["old_raw_value_sha256"]
                assert fingerprint(new_value) == witness["new_raw_value_sha256"]
                assert fingerprint(old_parent) == witness["old_parent_sha256"]
                assert fingerprint(new_parent) == witness["new_parent_sha256"]
                assert body_signature(old_parent) == body_signature(new_parent)
                if kind == "field_evidence":
                    assert b.rsplit("/", 1)[1] == proof[raw_key]
                    assert profile.fields.get(a.rsplit("/", 1)[1], a.rsplit("/", 1)[1]) == proof[semantic_key]
                    # Each new semantic token has an independent unique value/subtree
                    # anchor. Repeated booleans and list indices cannot prove it.
                    assert Counter(body_signature(v) for k, v in old_parent.items() if k != "$type")[body_signature(old_value)] == 1
                    assert Counter(body_signature(v) for k, v in new_parent.items() if k != "$type")[body_signature(new_value)] == 1
                else:
                    assert b.endswith("/$type") and new_value == proof[raw_key]
                    assert profile.types.get(old_value, old_value) == proof[semantic_key]
                    old_container = resolve_pointer(record["old_raw"], a.rsplit("/", 2)[0])
                    new_container = resolve_pointer(record["raw"], b.rsplit("/", 2)[0])
                    if isinstance(old_container, list):
                        assert isinstance(new_container, list)
                        assert Counter(body_signature(v) for v in old_container)[body_signature(old_parent)] == 1
                        assert Counter(body_signature(v) for v in new_container)[body_signature(new_parent)] == 1


@pytest.mark.parametrize("mutation", ["field", "type", "alter", "order", "unknown"])
def test_whole_ability_drift_cannot_borrow_equal_damage_sequence(f11_reaction_db, fake_repo, mutation):
    path = fake_repo / SOURCE
    rows = json.loads(path.read_text("utf8"))
    row = next(item["Default"] for item in rows if item["Default"].get("MMMADNCKFLA") == "ElementAbility_Swirl_Ice")
    modifier = row["PGMKHCKJIKM"]["Swirl_Ice"]
    if mutation == "field":
        modifier["NDJFACCPAGC"][2]["LONMFMMEPNN"]["BJNOBFDFNGG"] = "default"
    elif mutation == "type":
        modifier["NDJFACCPAGC"][2]["$type"] = "CODLAMLJMMJ"
    elif mutation == "alter":
        row["PGMKHCKJIKM"]["Swirl_Ice_Alter"]["NDJFACCPAGC"][0]["unreviewed"] = True
    elif mutation == "order":
        modifier["NDJFACCPAGC"] = list(reversed(modifier["NDJFACCPAGC"]))
    else:
        row["unreviewed"] = {"$type": "ALJBCOCLPOO"}
    path.write_text(json.dumps(rows), "utf8")
    fixture_builder.build_database(f11_reaction_db.parent, force=True)
    with pytest.raises(RuntimeConfigError) as error:
        query(f11_reaction_db, "cryo")
    assert error.value.reason == "unsupported_source"


def test_query_scope_and_caller_mutation_do_not_expand_recipe(f11_reaction_db):
    first = query(f11_reaction_db, "hydro")
    first["mapping_evidence"]["field_evidence"].clear()
    assert query(f11_reaction_db, "hydro")["mapping_evidence"]["field_evidence"]
    with pytest.raises(ValueError, match="仅支持swirl"):
        query(f11_reaction_db, "electro")


def test_resource_is_included_by_production_spec_glob():
    spec = (ROOT / "genshin_app.spec").read_text("utf8")
    assert '("src/genshin_runtime/resources/*.json", "genshin_runtime/resources")' in spec
    assert (ROOT / "src/genshin_runtime/resources/reaction_damage_f11cf545_profile.json").is_file()
