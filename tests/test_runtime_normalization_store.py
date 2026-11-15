"""Store/query integration: canonical indices retain original source evidence."""
import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from genshin_db import runtime_catalog, runtime_store
from genshin_db.runtime_catalog import RuntimeConfigError
from genshin_db.runtime_queries import (
    assemble_runtime_source,
    get_runtime_status,
    query_runtime,
)
from genshin_db.runtime_store import (
    build_runtime_layer,
    decode_source,
    validate_runtime,
)
from genshin_runtime.normalization import Profile
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401

# Imported fixtures intentionally share the existing minimal source dataset.
# ruff: noqa: F811
COMMIT = "a" * 40
FIELDS = {"AAAAAAAAAAA": "abilityName", "BBBBBBBBBBB": "abilitySpecials",
          "CCCCCCCCCCC": "modifiers", "DDDDDDDDDDD": "modifierName",
          "EEEEEEEEEEE": "paramSpecial", "FFFFFFFFFFF": "paramDelta",
          "GGGGGGGGGGG": "duration", "HHHHHHHHHHH": "onAdded"}
TYPES = {"JJJJJJJJJJJ": "AddAbility", "KKKKKKKKKKK": "ModifyAbility",
         "LLLLLLLLLLL": "ApplyModifier"}


def obfuscate(value):
    if isinstance(value, list):
        return [obfuscate(v) for v in value]
    if not isinstance(value, dict):
        return value
    reverse_fields, reverse_types = {v: k for k, v in FIELDS.items()}, {v: k for k, v in TYPES.items()}
    return {reverse_fields.get(k, k): reverse_types.get(v, v) if k == "$type" else obfuscate(v)
            for k, v in value.items()}


@pytest.fixture
def normalized_db(tmp_path, fake_repo, stub_fetcher, monkeypatch):
    profiles = {COMMIT: Profile("store-test-v1", (COMMIT,), FIELDS, TYPES)}

    def verify(receipt, *, commit):
        profile = profiles.get(commit)
        if profile is None or receipt != profile.receipt(commit):
            raise ValueError("test profile receipt mismatch")
        return profile

    monkeypatch.setattr(runtime_store, "for_commit", profiles.get)
    monkeypatch.setattr(runtime_store, "verify_receipt", verify)
    affix = fake_repo / "ExcelBinOutput/EquipAffixExcelConfigData.json"
    value = json.loads(affix.read_text("utf8"))
    value[0]["openConfig"] = "Fixture_Effect"
    affix.write_text(json.dumps(value), "utf8")
    for path in (fake_repo / "BinOutput/Ability/Temp/EquipAbilities/Fixture.json",
                 fake_repo / "BinOutput/Talent/EquipTalents/Fixture.json"):
        path.write_text(json.dumps(obfuscate(json.loads(path.read_text("utf8")))), "utf8")
    fixture_builder.build_database(tmp_path)
    return tmp_path / "genshin.db", profiles, fake_repo


def rewrite_summary(db, mutate):
    with closing(sqlite3.connect(db)) as conn, conn:
        row = conn.execute("SELECT validation_json FROM data_component WHERE component='runtime_config' AND version_line='release'").fetchone()
        summary = json.loads(row[0])
        mutate(summary)
        conn.execute("UPDATE data_component SET validation_json=? WHERE component='runtime_config' AND version_line='release'", (json.dumps(summary),))


def test_normalized_indices_keep_raw_sources_pointers_and_bundle(normalized_db):
    db, profiles, repo = normalized_db
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    bundle = assemble_runtime_source(db, 917)
    assert bundle["status"] == "ready" and bundle["reference_closed"]
    assert bundle["normalization"] == profiles[COMMIT].receipt(COMMIT)
    assert bundle["snapshot"]["normalization"] == bundle["normalization"]
    assert bundle["talent"]["operations"][0]["$type"] == "JJJJJJJJJJJ"
    assert bundle["talent"]["operations"][0]["AAAAAAAAAAA"] == "Fixture_Ability"
    body = bundle["abilities"][0]["payload"]
    assert "abilityName" not in body and body["AAAAAAAAAAA"] == "Fixture_Ability"
    assert body["CCCCCCCCCCC"]["Fixture_Modifier"]["unknown_preserved"] == [0, False, None]
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        assert validate_runtime(conn, "release", deep=True)["state"] == "ready"
        summary = json.loads(conn.execute("SELECT validation_json FROM data_component WHERE component='runtime_config' AND version_line='release'").fetchone()[0])
        assert summary["types_representation"] == "raw" and summary["types"]["JJJJJJJJJJJ"] == 1
        for row in conn.execute("SELECT * FROM runtime_source"):
            assert row["source_sha256"] == hashlib.sha256((repo / row["source_path"]).read_bytes()).hexdigest()
            assert decode_source(row) == json.loads((repo / row["source_path"]).read_bytes())
        modifier = conn.execute("SELECT * FROM runtime_definition WHERE definition_kind='modifier'").fetchone()
        assert modifier["json_pointer"] == "/0/Default/CCCCCCCCCCC/Fixture_Modifier"
        refs = conn.execute("SELECT * FROM runtime_reference WHERE target_kind='ability'").fetchall()
        assert refs and all(r["json_pointer"].endswith("/AAAAAAAAAAA") for r in refs)
        assert all(r["extraction_rule_id"] == runtime_catalog.INDEXER_VERSION for r in refs)
        binding = conn.execute("SELECT * FROM runtime_parameter_binding").fetchone()
        assert binding["operation_type"] == "ModifyAbility" and binding["parameter_name"] == "Rate"
        assert binding["raw_expression_json"] == '"%1"'
    ident = bundle["abilities"][0]["definition_id"]
    fragment = query_runtime(db, {"action": "fragment", "definition_id": ident, "relative_pointer": "/BBBBBBBBBBB/Rate"})
    assert json.loads(fragment["payload_json"]) == 0
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("variant", ["missing", "forged", "null", "legacy"])
def test_receipt_cannot_be_removed_forged_or_attached_to_legacy(normalized_db, variant):
    db, _, _ = normalized_db
    def change(summary):
        if variant == "missing":
            summary.pop("normalization")
        elif variant == "forged":
            summary["normalization"]["forged"] = True
        elif variant == "null":
            summary["normalization"] = None
        else:
            summary["indexer_version"] = "2"
    rewrite_summary(db, change)
    assert get_runtime_status(db)["state"] == "invalid"
    with pytest.raises(RuntimeConfigError):
        assemble_runtime_source(db, 917)
    with closing(sqlite3.connect(db)) as conn, pytest.raises(RuntimeConfigError):
        validate_runtime(conn, "release", deep=True)


def test_mapping_revision_changes_snapshot_and_rejects_old_receipt(normalized_db):
    db, profiles, repo = normalized_db
    first = assemble_runtime_source(db, 917)
    profiles[COMMIT] = Profile("store-test-v2", (COMMIT,), FIELDS, TYPES)
    assert get_runtime_status(db)["state"] == "invalid"
    build_runtime_layer(db, repo, version_line="release", upstream_commit=COMMIT)
    second = assemble_runtime_source(db, 917)
    assert second["source"]["snapshot_id"] != first["source"]["snapshot_id"]
    assert second["content_fingerprint"] != first["content_fingerprint"]
    assert second["abilities"][0]["payload"] == first["abilities"][0]["payload"]
    with pytest.raises(RuntimeConfigError) as exc:
        assemble_runtime_source(db, 917, snapshot_id=first["source"]["snapshot_id"])
    assert exc.value.reason == "snapshot_changed"


def test_unknown_commit_never_inherits_profile_and_failed_build_rolls_back(normalized_db):
    db, _, repo = normalized_db
    first = assemble_runtime_source(db, 917)
    with pytest.raises(RuntimeConfigError, match="Ability定义缺少名称"):
        build_runtime_layer(db, repo, version_line="release", upstream_commit="b" * 40)
    assert assemble_runtime_source(db, 917) == first


def test_indexer_two_plain_database_stays_readable_without_normalization(tmp_path, fake_repo, stub_fetcher, monkeypatch):
    affix = fake_repo / "ExcelBinOutput/EquipAffixExcelConfigData.json"
    value = json.loads(affix.read_text("utf8"))
    value[0]["openConfig"] = "Fixture_Effect"
    affix.write_text(json.dumps(value), "utf8")
    with monkeypatch.context() as patch:
        patch.setattr(runtime_catalog, "INDEXER_VERSION", "2")
        patch.setattr(runtime_store, "INDEXER_VERSION", "2")
        fixture_builder.build_database(tmp_path)
    db = tmp_path / "genshin.db"
    state = get_runtime_status(db)
    assert state["state"] == "ready" and state["indexer_version"] == "2"
    bundle = assemble_runtime_source(db, 917)
    assert bundle["status"] == "ready" and "normalization" not in bundle
    with closing(sqlite3.connect(db)) as conn:
        assert validate_runtime(conn, "release", deep=True)["state"] == "ready"
        assert {r[0] for r in conn.execute("SELECT extraction_rule_id FROM runtime_reference")} == {"2"}
