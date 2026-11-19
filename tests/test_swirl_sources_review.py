"""Independent read-only EC04 source checks against optional project raw files."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from genshin_db.queries import equipment_read_session
from genshin_db.runtime_attenuation import _projection
from genshin_db.runtime_catalog import RuntimeConfigError
from genshin_db.runtime_queries import query_runtime
from genshin_db.runtime_reaction_damage import REACTION_LIMITS

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/genshin.db"
RAW = Path(os.environ.get("GENSHIN_TEST_UPSTREAM_ROOT", ROOT / "data/upstream/repo"))
GROUP = "ElementReaction_Damage_Attenuation_1"


def file_sha(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def node_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode("utf8")).hexdigest()


def pointer(value, location):
    for token in location.removeprefix("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


@pytest.fixture(scope="module")
def real_sources():
    if not DB.is_file() or not RAW.is_dir():
        pytest.skip("optional project database and raw upstream are absent")
    before = file_sha(DB)
    with equipment_read_session(DB):
        data = {element: query_runtime(DB, {"action": "reaction_damage_profile", "element": element})
                for element in ("hydro", "cryo")}
        yield data
    assert file_sha(DB) == before, "the independent audit must not write production data"


@pytest.mark.parametrize("element", ["hydro", "cryo"])
def test_review_all_source_fragments_hash_and_raw_pointers_independently(real_sources, element):
    result = real_sources[element]
    origins = [*result["sources"].values(), *result["raw_nodes"].values(), *result["separate_star_abilities"]]
    files = {}
    for origin in origins:
        path = RAW / origin["source_path"]
        if path not in files:
            files[path] = json.loads(path.read_text("utf8"))
        assert file_sha(path) == origin["source_sha256"]
        raw = pointer(files[path], origin["json_pointer"])
        assert node_sha(raw) == origin["node_sha256"]
        if "raw" in origin:
            assert raw == origin["raw"]
    ordinary = result["raw_nodes"]["ordinary_modifier"]
    assert result["sources"]["origin_attack"]["json_pointer"].startswith(ordinary["json_pointer"] + "/")
    assert "_Alter" not in result["sources"]["origin_attack"]["json_pointer"]


def test_review_channel_split_and_prechange_application_fingerprints(real_sources):
    baseline = json.loads((ROOT / "tests/fixtures/engine-continuation/ec04-research/independent-prechange-application-fingerprints.json").read_text("utf8"))
    # Frozen EC04 receipts supply raw provenance, not current query expectations.
    # EC13a changes the snapshot/definition IDs without changing these source rows.
    historical_paths = {
        "default": "ec04/package/default.json",
        GROUP: "ec04/cli-skill/damage-full.json",
    }
    expected_sequences = {"default": ([1, 0, 0] * 8, 2.5, 2_500_000),
                          GROUP: ([1] * 10, .5, 500_000)}
    for group, expected in baseline["groups"].items():
        historical = json.loads((ROOT / "tests/fixtures/engine-continuation" /
                                 historical_paths[group]).read_text("utf8"))
        old_source = historical["source"]
        old_state = historical["snapshot"]
        assert old_state["snapshot_id"] == baseline["snapshot_id"]
        assert old_state["indexer_version"] == "4"
        raw_path = RAW / old_source["source_path"]
        # An array offset belongs to the historical snapshot. Relocate the
        # current exact group without rewriting or pretending to reuse that
        # historical pointer; unrelated upstream rows can be inserted first.
        current_rows = json.loads(raw_path.read_text("utf8"))
        current_matches = [(index, row) for index, row in enumerate(current_rows) if row.get("group") == group]
        assert len(current_matches) == 1
        current_index, raw = current_matches[0]
        current_pointer = f"/{current_index}"
        assert raw == historical["raw_record"]
        # The frozen receipt identifies the complete file at its historical
        # commit.  A later upstream refresh may change unrelated rows in that
        # file while preserving this exact reaction record.  Keep the old
        # source fingerprint in the historical identity below, but audit the
        # current file separately instead of pretending it is the old blob.
        current_source_sha = file_sha(raw_path)
        assert len(old_source["source_sha256"]) == 64
        assert node_sha(raw) == old_source["node_sha256"]
        sequence, seconds, ticks = expected_sequences[group]
        assert raw["group"] == group
        assert raw["durabilitySequence"] == sequence and raw["resetCycle"] == seconds
        # Independently stated exact-time cases; do not call the product projection.
        projection = {"semantics": "finite-binary-application-sequence-v1",
            "reset_cycle_seconds_raw": seconds, "reset_cycle_ticks": ticks,
            "reset_cycle_seconds_rounded": seconds, "rounding_error_seconds": 0.0,
            "rounding_method": "decimal_roundtrip_half_even_to_microseconds",
            "max_absolute_rounding_error_seconds": .0000005,
            "durability_sequence": sequence, "sequence_exhausted_coefficient": 0}
        old_identity = {"profile_version": 1, "snapshot_id": baseline["snapshot_id"],
            "group": group, "source": {key: old_source[key] for key in
                ("definition_id", "source_path", "json_pointer", "source_sha256", "node_sha256")},
            "projection": projection, "execution_status": "ready"}
        assert node_sha(old_identity) == expected

        default = query_runtime(DB, {"action": "attenuation_profile", "group": group})
        explicit = query_runtime(DB, {"action": "attenuation_profile", "group": group, "channel": "application"})
        assert default == explicit
        assert default["raw_record"] == raw
        assert default["projection"] == projection
        assert default["execution"] == {"status": "ready", "semantics": projection["semantics"],
                                        "issues": [], "attack_binding": "not_evaluated"}
        state, source = default["snapshot"], default["source"]
        source_snapshot = node_sha({"version_line": state["version_line"],
            "upstream_commit": state["upstream_commit"], "schema_version": state["schema_version"],
            "manifest": state["manifest_fingerprint"]})
        assert state["snapshot_id"] == node_sha({"source_snapshot": source_snapshot,
                                                "normalization": state["normalization"]})
        for key in ("source_path", "node_sha256"):
            assert source[key] == old_source[key]
        assert source["json_pointer"] == current_pointer
        assert source["source_sha256"] == current_source_sha
        for key in ("definition_kind", "owner_definition_id", "variant_key", "namespace_key", "exact_key"):
            assert source["definition"][key] == old_source["definition"][key]
        assert source["field_pointers"] == {key: current_pointer + "/" + key
                                            for key in ("group", "resetCycle", "durabilitySequence")}
        old_definition_id = node_sha([baseline["snapshot_id"], old_source["source_path"],
                                     old_source["json_pointer"], "excel_record"])
        new_definition_id = node_sha([state["snapshot_id"], old_source["source_path"],
                                     current_pointer, "excel_record"])
        assert old_source["definition_id"] == old_definition_id
        assert source["definition_id"] == source["definition"]["definition_id"] == new_definition_id
        current_identity = {**old_identity, "snapshot_id": state["snapshot_id"],
                            "source": {**old_identity["source"],
                                       "definition_id": new_definition_id,
                                       "json_pointer": current_pointer,
                                       "source_sha256": current_source_sha}}
        assert default["content_fingerprint"] == node_sha(current_identity)
        if state["snapshot_id"] != baseline["snapshot_id"]:
            assert state["manifest_fingerprint"] != old_state["manifest_fingerprint"]
            assert new_definition_id != old_definition_id
            assert default["content_fingerprint"] != expected
            with pytest.raises(RuntimeConfigError) as error:
                query_runtime(DB, {"action": "attenuation_profile", "group": group,
                                   "snapshot_id": baseline["snapshot_id"]})
            assert error.value.reason == "snapshot_changed"
        else:
            assert default["content_fingerprint"] == expected
        assert query_runtime(DB, {"action": "attenuation_profile", "group": group,
                                  "snapshot_id": state["snapshot_id"]}) == default
    application = query_runtime(DB, {"action": "attenuation_profile", "group": GROUP})
    damage = query_runtime(DB, {"action": "attenuation_profile", "group": GROUP, "channel": "damage"})
    assert application["projection"]["durability_sequence"] == [1] * 10
    assert damage["projection"]["damage_sequence"] == [1, 1] + [0] * 8
    assert "durability_sequence" not in damage["projection"]
    assert damage["content_fingerprint"] != application["content_fingerprint"]
    assert damage["source"]["field_pointers"]["damageSequence"].endswith("/damageSequence")


def test_review_missing_attacker_is_not_defaulted_and_ability_remains_unexecuted(real_sources):
    assert real_sources["hydro"]["projection"]["attacker"] == {"present": True, "value": "Self", "default": "not_inferred"}
    assert real_sources["cryo"]["projection"]["attacker"] == {"present": False, "value": None, "default": "not_inferred"}
    for result in real_sources.values():
        assert result["execution"] == {"sequence_status": "ready", "ability_execution": "pending",
            "owner_resolution": "not_evaluated", "reaction_dispatch": "not_evaluated", "semantics": "source_relation_only"}
        assert result["projection"]["factor"]["raw"] == 0.6000000238418579
        assert result["projection"]["base_reaction_damage"] == {"raw_initial": 0.0, "runtime_injection": "unknown"}
        assert {x["code"] for x in result["issues"]} >= {"alter_unsupported", "star_unsupported", "propagation_unsupported"}


def test_review_true_raw_attack_group_points_to_damage_record(real_sources):
    # Independent raw-token witnesses for the accepted snapshots. The
    # production recipe is deliberately not consulted to define expectations.
    tokens = {
        "224708edc8056f27e9ffc4eacabe797d75274636":
            ("CODEHPJKBAM", "LGHDGGJIJOK", "HGLEEJNPBEB", "IAFHAPFBBOB"),
        "f11cf545a7eee32a29999f960ebfba305f896ed3":
            ("ALJBCOCLPOO", "LONMFMMEPNN", "BJNOBFDFNGG", "EOBMOFPGMLP"),
        "6044f5aae7dd79fbbe15c5748ef99b654f6132b7":
            ("ALJBCOCLPOO", "LONMFMMEPNN", "BJNOBFDFNGG", "EOBMOFPGMLP"),
    }
    for element, result in real_sources.items():
        source = result["sources"]["origin_attack"]
        raw = pointer(json.loads((RAW / source["source_path"]).read_text("utf8")), source["json_pointer"])
        damage_type, attack_info, attenuation_group, attack_tag = tokens[result["snapshot"]["upstream_commit"]]
        assert raw["$type"] == damage_type
        assert raw[attack_info][attenuation_group] == GROUP
        assert raw[attack_info][attack_tag] == "Self_Swirl_" + ("Water" if element == "hydro" else "Ice") + "Damage"
        assert result["sources"]["group_reference"]["raw"] == GROUP
        record = result["raw_nodes"]["attenuation"]["raw"]
        assert record["group"] == GROUP and record["damageSequence"] == [1, 1] + [0] * 8
        assert result["projection"]["reset_cycle_ticks"] == 500000


@pytest.mark.parametrize("element", ["electro", "frozen", "star", None, True])
def test_review_unproven_element_cannot_reuse_ordinary_profile(element):
    with pytest.raises(ValueError):
        query_runtime(DB, {"action": "reaction_damage_profile", "element": element})


@pytest.mark.parametrize("value", [None, [], [True, False], [1, .5], [1] * 65])
def test_review_damage_channel_does_not_fall_back_to_valid_application(value):
    record = {"group": "synthetic", "resetCycle": .5, "durabilitySequence": [1, 1, 1]}
    if value is not None:
        record["damageSequence"] = value
    projection, issues = _projection(record, channel="damage")
    assert issues
    assert projection["semantics"] == "finite-binary-damage-sequence-v1"
    assert "durability_sequence" not in projection


def test_review_stale_snapshot_is_rejected_before_profile_assembly(real_sources):
    with pytest.raises(RuntimeConfigError) as error:
        query_runtime(DB, {"action": "reaction_damage_profile", "element": "cryo", "snapshot_id": "obsolete"})
    assert error.value.reason == "snapshot_changed"


def test_review_caller_mutation_cannot_poison_same_read_session_source_cache(real_sources):
    first = query_runtime(DB, {"action": "reaction_damage_profile", "element": "cryo"})
    baseline = first["content_fingerprint"]
    first["raw_nodes"]["ordinary_modifier"]["raw"]["INDEPENDENT_REVIEW_POISON"] = True
    first["raw_nodes"]["attenuation"]["raw"]["damageSequence"][0] = 99
    first["projection"]["damage_sequence"][0] = 99
    first["snapshot"]["counts"]["runtime_source"] = -1
    second = query_runtime(DB, {"action": "reaction_damage_profile", "element": "cryo"})
    assert second["content_fingerprint"] == baseline
    assert "INDEPENDENT_REVIEW_POISON" not in second["raw_nodes"]["ordinary_modifier"]["raw"]
    assert second["projection"]["damage_sequence"][0] == second["raw_nodes"]["attenuation"]["raw"]["damageSequence"][0] == 1
    assert second["snapshot"]["counts"]["runtime_source"] > 0


@pytest.mark.parametrize("limit,value", [("max_operations", 9), ("max_typed_nodes", 1)])
def test_review_disclosed_operation_and_typed_node_limits_are_effective(real_sources, monkeypatch, limit, value):
    monkeypatch.setitem(REACTION_LIMITS, limit, value)
    with pytest.raises(RuntimeConfigError) as error:
        query_runtime(DB, {"action": "reaction_damage_profile", "element": "hydro"})
    assert error.value.reason == "response_too_large"
