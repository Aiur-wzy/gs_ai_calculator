"""Historical 224708ed rebase invariants, independent of the live checkout."""

import hashlib
import json
from pathlib import Path

import pytest

from tests.historical_runtime_sources import (
    HISTORICAL_REVIEWED_COMMIT,
    read_source_bytes,
)

ROOT = Path(__file__).resolve().parents[1]
RESOURCE_ROOT = ROOT / "src/genshin_runtime/resources"
FIXTURE_ROOT = ROOT / "tests/fixtures"
REBASED_COMMIT = "224708edc8056f27e9ffc4eacabe797d75274636"
PREVIOUS_COMMIT = "342084292ba4c3c6ec2bc34c045de4de72ae8d53"
REBASED_SNAPSHOT = "ef0cac22b5cc4ee887fa64d134b563ffc745ae365b0c6cdb03930f895ab413df"


def load_resource(name: str) -> dict:
    return json.loads((RESOURCE_ROOT / name).read_text(encoding="utf8"))


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf8"))


def source_sha256(source_path: str) -> str:
    return hashlib.sha256(read_source_bytes(source_path)).hexdigest()


def test_all_eleven_profiles_are_bound_to_the_reviewed_rebase_revision():
    assert REBASED_COMMIT == HISTORICAL_REVIEWED_COMMIT
    attack = load_resource("attack_metadata_profiles.json")
    cast = load_resource("cast_sequence_profiles.json")
    generation = load_resource("energy_generation_profiles.json")
    trigger = load_resource("energy_trigger_profiles.json")
    limited = load_resource("limited_hit_benefit_profiles.json")
    benefit = load_resource("reaction_benefit_profiles.json")
    reaction = load_resource("reaction_damage_profiles.json")
    pyro = load_resource("reaction_damage_pyro_profile.json")
    em = load_resource("reaction_em_profiles.json")
    talent = load_resource("talent_level_profiles.json")
    target = load_resource("target_state_profiles.json")

    assert attack["recipe_version"] == 2
    assert attack["commit"] == attack["evidence"]["current_commit"] == REBASED_COMMIT
    assert cast["profile_version"] == 2
    assert {profile["upstream_commit"] for profile in cast["profiles"]} == {REBASED_COMMIT}
    assert {profile["normalization_profile_id"] for profile in cast["profiles"]} == {
        "upstream-224708ed-reviewed-schema-v1"
    }
    assert generation["profile_version"] == 2
    assert generation["field_mapping"]["upstream_commit"] == REBASED_COMMIT
    assert trigger["profile_version"] == 2 and trigger["commit"] == REBASED_COMMIT
    assert limited["profile_version"] == 2 and limited["commit"] == REBASED_COMMIT
    assert limited["normalization"] == {
        "normalization_version": 2,
        "profile_id": "upstream-224708ed-reviewed-schema-v1",
        "mapping_fingerprint": "506862bc6c0bf033d8272b75f193cd2a620fc513320b3223d07afcc6f04356e2",
        "upstream_commit": REBASED_COMMIT,
    }
    assert benefit["profile_version"] == 2 and benefit["commit"] == REBASED_COMMIT
    assert reaction["recipe_version"] == 2 and reaction["commit"] == REBASED_COMMIT
    assert pyro["recipe_version"] == 2 and pyro["commit"] == REBASED_COMMIT
    assert em["profile_version"] == 2 and em["commit"] == REBASED_COMMIT
    assert talent["recipe_version"] == 2
    assert talent["commit"] == talent["evidence"]["current_commit"] == REBASED_COMMIT
    assert target["profile_version"] == 2 and target["commit"] == REBASED_COMMIT


def test_linked_energy_and_reaction_damage_families_move_atomically():
    generation = load_resource("energy_generation_profiles.json")
    trigger = load_resource("energy_trigger_profiles.json")
    reaction = load_resource("reaction_damage_profiles.json")
    pyro = load_resource("reaction_damage_pyro_profile.json")

    assert generation["field_mapping"]["upstream_commit"] == trigger["commit"]
    assert generation["profile_version"] == trigger["profile_version"] == 2
    assert pyro["base_recipe_id"] == reaction["recipe_id"]
    assert pyro["commit"] == reaction["commit"] == REBASED_COMMIT
    assert pyro["recipe_version"] == reaction["recipe_version"] == 2


def test_reviewed_source_file_receipts_match_the_fixed_snapshot():
    cast = load_resource("cast_sequence_profiles.json")
    attack = load_resource("attack_metadata_profiles.json")
    trigger = load_resource("energy_trigger_profiles.json")
    limited = load_resource("limited_hit_benefit_profiles.json")
    benefit = load_resource("reaction_benefit_profiles.json")
    target = load_resource("target_state_profiles.json")

    expected = {}
    for profile in cast["profiles"]:
        for key in ("skill_source", "ability_source"):
            source = profile[key]
            expected[source["source_path"]] = source["source_sha256"]
    for source in attack["evidence"]["files"]:
        expected[source["source_path"]] = source["workspace_source_sha256"]
    for profile in (trigger, limited):
        for source in profile["entry_sources"]:
            expected[source["source_path"]] = source["worktree_sha256"]
    for source in benefit["files"]:
        expected[source["path"]] = source["workspace_source_sha256"]
    for source in target["files"]:
        expected[source["source_path"]] = source["workspace_source_sha256"]

    assert expected
    for source_path, expected_hash in expected.items():
        assert source_sha256(source_path) == expected_hash, source_path


def test_talent_rebased_witnesses_preserve_both_snapshot_identities():
    talent = load_resource("talent_level_profiles.json")
    pairs = talent["evidence"]["non_level_pairs"]
    assert pairs and talent["evidence"]["current_commit"] == REBASED_COMMIT
    for pair in pairs:
        current, previous = pair["current"], pair["old"]
        assert current["operations_normalized"] == previous["operations_normalized"]
        assert current["execution_support"] == previous["execution_support"] == "not_evaluated"
        assert source_sha256(current["source_path"]) == current["source_sha256"]
        parameter = current["parameter_record_origin"]
        assert source_sha256(parameter["source_path"]) == parameter["source_sha256"]
        for reference in current["references"]:
            assert reference["snapshot_id"] == REBASED_SNAPSHOT
        for reference in previous["references"]:
            assert reference["snapshot_id"] != REBASED_SNAPSHOT


def test_rebased_and_previous_fixtures_remain_frozen():
    pairs = [
        (
            "runtime_limited_hit_benefit_current.json",
            "runtime_limited_hit_benefit.json",
            ("snapshot", "upstream_commit"),
        ),
        (
            "runtime_reaction_benefit_vv_current.json",
            "runtime_reaction_benefit_vv.json",
            ("source", "upstream_commit"),
        ),
        (
            "runtime-reaction-em-source-current.json",
            "runtime-reaction-em-source.json",
            ("snapshot", "upstream_commit"),
        ),
        (
            "runtime_target_state_blizzard_current.json",
            "runtime_target_state_blizzard.json",
            ("source", "upstream_commit"),
        ),
    ]
    for current_name, historical_name, path in pairs:
        current = load_fixture(current_name)
        historical = load_fixture(historical_name)
        for key in path:
            current = current[key]
            historical = historical[key]
        assert current == REBASED_COMMIT
        assert historical == PREVIOUS_COMMIT


def test_historical_receipt_audit_never_reads_the_live_checkout(monkeypatch):
    canonical = (ROOT / "data" / "upstream" / "repo").resolve()
    original_open = Path.open

    def isolated_open(path, *args, **kwargs):
        if path.resolve().is_relative_to(canonical):
            pytest.fail("historical receipt audit attempted to read the live checkout")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", isolated_open)
    test_reviewed_source_file_receipts_match_the_fixed_snapshot()
    test_talent_rebased_witnesses_preserve_both_snapshot_identities()
