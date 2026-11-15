"""Source-derived damage sequences must not masquerade as ability execution."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from genshin_db.equipment_queries import equipment_read_session
from genshin_db.runtime_catalog import RuntimeConfigError, fingerprint, resolve_pointer
from genshin_db.runtime_queries import query_runtime, runtime_capabilities
from genshin_db.runtime_reaction_damage import REACTION_LIMITS
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401

# ruff: noqa: F811
SOURCE = "BinOutput/Ability/Temp/ConfigAbility_Element.json"
ATTENUATION = "ExcelBinOutput/AttackAttenuationExcelConfigData.json"
COMMIT = "224708edc8056f27e9ffc4eacabe797d75274636"
GROUP = "ElementReaction_Damage_Attenuation_1"


@pytest.fixture
def reaction_db(tmp_path, fake_repo, stub_fetcher):
    stub_fetcher["commit"] = COMMIT
    raw = json.loads((Path(__file__).parent / "fixtures/runtime_reaction_swirl.json").read_text("utf8"))
    path = fake_repo / SOURCE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw), "utf8")
    (fake_repo / ATTENUATION).write_text(json.dumps([
        {"group": GROUP, "resetCycle": .5, "durabilitySequence": [1] * 10,
         "damageSequence": [1, 1] + [0] * 8, "enbreakSequence": [1] * 10}]), "utf8")
    fixture_builder.build_database(tmp_path)
    return tmp_path / "genshin.db"


def query(db, element="hydro", **extra):
    return query_runtime(db, {"action": "reaction_damage_profile", "element": element, **extra})


def change(db, repo, path, apply):
    file = repo / path
    raw = json.loads(file.read_text("utf8"))
    apply(raw)
    file.write_text(json.dumps(raw), "utf8")
    fixture_builder.build_database(db.parent, force=True)


@pytest.mark.parametrize("element,present,value", [("hydro", True, "Self"), ("cryo", False, None)])
def test_real_node_projection_preserves_defaults_and_pending_execution(reaction_db, fake_repo, element, present, value):
    before = hashlib.sha256(reaction_db.read_bytes()).hexdigest()
    out = query(reaction_db, element)
    assert out["status"] == out["execution"]["sequence_status"] == "ready"
    assert out["execution"]["ability_execution"] == "pending"
    assert out["execution"]["owner_resolution"] == out["execution"]["reaction_dispatch"] == "not_evaluated"
    assert out["projection"]["damage_sequence"] == [1, 1] + [0] * 8
    assert all(type(x) is int for x in out["projection"]["damage_sequence"])
    assert "durability_sequence" not in out["projection"]
    assert out["projection"]["reset_cycle_seconds_rounded"] == .5
    assert out["projection"]["attacker"] == {"present": present, "value": value, "default": "not_inferred"}
    assert out["projection"]["factor"] == {"raw": 0.6000000238418579, "execution": "not_compiled"}
    assert out["projection"]["base_reaction_damage"]["runtime_injection"] == "unknown"
    for source in out["sources"].values():
        raw = json.loads((fake_repo / source["source_path"]).read_text("utf8"))
        assert fingerprint(resolve_pointer(raw, source["json_pointer"])) == source["node_sha256"]
    assert "definition_id" not in out["sources"]["origin_attack"]
    assert out["sources"]["origin_attack"]["owner_definition_id"] == out["sources"]["ability"]["definition_id"]
    assert out["sources"]["group_reference"]["raw"] == GROUP
    assert out["sources"]["attenuation"]["field_pointers"]["damageSequence"].endswith("/damageSequence")
    assert len(out["separate_star_abilities"]) == 2
    assert "alter_modifier" in out["raw_nodes"]
    assert all(x["execution"] == "not_evaluated" for x in out["separate_star_abilities"])
    assert hashlib.sha256(reaction_db.read_bytes()).hexdigest() == before


def test_channel_defaults_are_identical_but_damage_is_separate(reaction_db):
    request = {"action": "attenuation_profile", "group": GROUP}
    original = query_runtime(reaction_db, request)
    explicit = query_runtime(reaction_db, {**request, "channel": "application"})
    damage = query_runtime(reaction_db, {**request, "channel": "damage"})
    assert original == explicit
    assert original["projection"]["durability_sequence"] == [1] * 10
    assert "damage_sequence" not in original["projection"]
    assert damage["projection"]["damage_sequence"] == [1, 1] + [0] * 8
    assert "durability_sequence" not in damage["projection"]
    assert original["content_fingerprint"] != damage["content_fingerprint"]
    assert damage["execution"]["attack_binding"] == "not_evaluated"


@pytest.mark.parametrize("sequence", [[], [True, False], [1, .5], [1] * 65, None])
def test_damage_invalid_or_missing_never_falls_back_to_durability(reaction_db, fake_repo, sequence):
    def apply(rows):
        if sequence is None:
            rows[0].pop("damageSequence")
        else:
            rows[0]["damageSequence"] = sequence
    change(reaction_db, fake_repo, ATTENUATION, apply)
    out = query(reaction_db)
    assert out["status"] == "ready"
    assert out["execution"]["sequence_status"] == "pending"
    assert out["execution"]["ability_execution"] == "pending"
    app = query_runtime(reaction_db, {"action": "attenuation_profile", "group": GROUP})
    assert app["execution"]["status"] == "ready"


def test_damage_float_normalization_and_rounding(reaction_db, fake_repo):
    change(reaction_db, fake_repo, ATTENUATION,
           lambda rows: rows[0].update(damageSequence=[1.0, 0.0], resetCycle=.10000000149011612))
    out = query(reaction_db)
    assert out["projection"]["damage_sequence"] == [1, 0]
    assert all(type(x) is int for x in out["projection"]["damage_sequence"])
    assert type(out["raw_nodes"]["attenuation"]["raw"]["damageSequence"][0]) is float
    assert out["projection"]["reset_cycle_ticks"] == 100000
    assert out["projection"]["rounding_error_seconds"] == pytest.approx(-1.49011612e-9, abs=1e-18)


@pytest.mark.parametrize("timer", [0, -1, True, 10**9 + 1, .0000005])
def test_damage_bad_timer_pending(reaction_db, fake_repo, timer):
    change(reaction_db, fake_repo, ATTENUATION, lambda rows: rows[0].update(resetCycle=timer))
    assert query(reaction_db)["execution"]["sequence_status"] == "pending"


@pytest.mark.parametrize("payload", [
    {"action": "reaction_damage_profile"},
    {"action": "reaction_damage_profile", "element": "electro"},
    {"action": "reaction_damage_profile", "element": "cryo", "reaction_type": "StarSwirl"},
    {"action": "reaction_damage_profile", "element": "hydro", "group": GROUP},
    {"action": "reaction_damage_profile", "element": None},
    {"action": "attenuation_profile", "group": GROUP, "channel": "unknown"},
])
def test_invalid_selections_rejected(reaction_db, payload):
    with pytest.raises(ValueError):
        query_runtime(reaction_db, payload)


@pytest.mark.parametrize("mutate", [
    lambda node: node.update(unreviewed=True),
    lambda node: node["GPPBBDEICKP"]["Swirl_Ice"]["EMGBJJOIENB"][2].update(GLBBADIPHFL="Self"),
    lambda node: node["GPPBBDEICKP"]["Swirl_Ice"]["EMGBJJOIENB"][2]["LGHDGGJIJOK"].update(HGLEEJNPBEB="default"),
    lambda node: node["LOEHLJGBGAC"].update(Swirl_Ice_Damage_Factor=.7),
    lambda node: node["ECFFDLHMCLK"][0]["IJDGNIIOHLM"][0].update(PDIFFCBMGMO=0),
])
def test_unreviewed_whole_ability_change_fails_closed(reaction_db, fake_repo, mutate):
    change(reaction_db, fake_repo, SOURCE, lambda rows: mutate(rows[1]["Default"]))
    with pytest.raises(RuntimeConfigError) as exc:
        query(reaction_db, "cryo")
    assert exc.value.reason == "unsupported_source"


def test_unknown_commit_requires_a_new_recipe(reaction_db, fake_repo, stub_fetcher):
    from genshin_runtime.normalization import for_commit

    # A valid future plaintext source can be indexed; this extraction recipe
    # must still reject its unreviewed commit rather than assume equivalence.
    path = fake_repo / SOURCE
    raw = json.loads(path.read_text("utf8"))
    path.write_text(json.dumps(for_commit(COMMIT).normalize(raw, kind="ability_source")), "utf8")
    stub_fetcher["commit"] = "b" * 40
    fixture_builder.build_database(reaction_db.parent, force=True)
    with pytest.raises(RuntimeConfigError) as exc:
        query(reaction_db)
    assert exc.value.reason == "unsupported_source"


def test_ambiguous_exact_name_is_rejected(reaction_db, fake_repo):
    change(reaction_db, fake_repo, SOURCE, lambda rows: rows.append(copy.deepcopy(rows[0])))
    with pytest.raises(RuntimeConfigError) as exc:
        query(reaction_db)
    assert exc.value.reason == "ambiguous_reference"


@pytest.mark.parametrize("limit", ["max_nodes", "max_bytes", "max_modifiers", "max_operations", "max_typed_nodes", "max_issues"])
def test_response_budgets_fail_without_truncation(reaction_db, monkeypatch, limit):
    monkeypatch.setitem(REACTION_LIMITS, limit, 1)
    with pytest.raises(RuntimeConfigError) as exc:
        query(reaction_db)
    assert exc.value.reason == "response_too_large"


def test_returned_mutation_does_not_poison_borrowed_session_or_recipe(reaction_db):
    with equipment_read_session(reaction_db):
        first = query(reaction_db)
        original = copy.deepcopy(first)
        first["raw_nodes"]["ordinary_modifier"]["raw"].clear()
        first["mapping_evidence"]["field_evidence"].clear()
        assert query(reaction_db) == original


def test_confirmation_identity_detects_source_changes(reaction_db, fake_repo):
    original = query(reaction_db)
    change(reaction_db, fake_repo, ATTENUATION, lambda rows: rows[0].update(resetCycle=.6))
    changed = query(reaction_db)
    assert changed["content_fingerprint"] != original["content_fingerprint"]
    with pytest.raises(RuntimeConfigError) as exc:
        query(reaction_db, snapshot_id=original["snapshot"]["snapshot_id"])
    assert exc.value.reason == "snapshot_changed"


def test_public_capabilities_are_explicit():
    cap = runtime_capabilities()
    assert cap["required"]["reaction_damage_profile"] == ["element"]
    assert cap["reaction_damage_profile"]["elements"] == ["hydro", "cryo", "pyro"]
    assert "channel" in cap["actions"]["attenuation_profile"]
