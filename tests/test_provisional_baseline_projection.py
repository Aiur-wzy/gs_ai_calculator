"""Actual upstream-shaped SQLite queries produce explicit typed comparison evidence."""
# ruff: noqa: F811 - imported pytest database fixtures.
from __future__ import annotations

import copy

import pytest

from genshin_app.equipment.provisional_baseline import FIELD, typed_loadout_baseline
from genshin_app.equipment.provisional_entities import (
    LOADOUT_VALUES_CONTRACT,
    ProvisionalEntityError,
    build_public_data_diff,
)
from genshin_db import queries
from genshin_db.equipment import EquipmentError
from tests.test_equipment import equipment_db, fake_repo, stub_fetcher  # noqa: F401
from tests.test_provisional_authority import _draft


def proposal(kind, values):
    return {**_draft("local.entity", "controlled entity", authorization_kind="public_data_patch", values=values),
            "entity_type": kind}


def lookup(kind, candidate):
    return {"query": {"kind": kind}, "observed_candidates": [candidate]}


def test_character_levels_are_real_columns_with_no_extrapolation(equipment_db):
    detail = queries.get_character_detail(equipment_db, 90000001)
    raw = detail["character"]
    values = {"contract": LOADOUT_VALUES_CONTRACT, "extensions": {}, "weapon_type": raw["weapon_type"],
        "panel_basis": [{"level": 90, "base_atk": (raw["atk_90"] or 0) + 1, "base_hp": raw["hp_90"] or 2, "base_def": raw["def_90"] or 3},
                        {"level": 89, "base_atk": 1, "base_hp": 2, "base_def": 3}]}
    original = copy.deepcopy(detail)
    candidate = typed_loadout_baseline(equipment_db, proposal("character", values), detail)
    assert detail == original and "contract" not in candidate
    diff = build_public_data_diff(lookup("character", candidate), values)
    actual = diff["before"]["panel_basis"][0]["base_atk"]
    assert actual == raw["atk_90"] if raw["atk_90"] is not None else actual["status"] == "source_missing"
    assert diff["before"]["panel_basis"][1]["base_atk"]["status"] == "source_missing"
    assert candidate[FIELD]["field_sources"]["/panel_basis/0/base_atk"]["pointer"] == "/character/atk_90"
    changed = copy.deepcopy(values)
    changed["panel_basis"][0]["level"] = 100
    with pytest.raises(ProvisionalEntityError) as failure:
        build_public_data_diff(lookup("character", candidate), changed)
    assert failure.value.reason == "provisional_entity_patch_baseline_mismatch"


def test_set_effect_thresholds_keep_all_raw_evidence_and_no_automatic_policy(equipment_db):
    detail = queries.get_artifact_set_detail(equipment_db, 15)
    count = detail["effects"][0]["required_count"]
    values = {"contract": LOADOUT_VALUES_CONTRACT, "extensions": {}, "thresholds": [
        {"required_count": count, "treatment": "included_in_panel", "note": "explicit user panel interpretation"},
        {"required_count": 5, "treatment": "no_effect_in_scope", "note": "controlled hypothetical threshold"}]}
    candidate = typed_loadout_baseline(equipment_db, proposal("artifact_set", values), detail)
    before = candidate[FIELD]["values"]["thresholds"]
    assert before[0]["required_count"] == count
    assert before[0]["treatment"]["status"] == "agent_execution_policy_not_database_field"
    assert before[1]["required_count"]["status"] == "source_missing"
    assert candidate[FIELD]["field_sources"]["/thresholds/0"]["observed"] == [detail["effects"][0]]
    assert build_public_data_diff(lookup("artifact_set", candidate), values)["after"] == values


def test_weapon_state_queries_are_exact_and_never_interpreted_as_zero(equipment_db):
    detail = queries.get_weapon_detail(equipment_db, 11401)
    values = {"contract": LOADOUT_VALUES_CONTRACT, "extensions": {}, "weapon_type": detail["weapon"]["weapon_type"],
        "panel_basis": [{"level": 20, "ascension": 1, "base_atk": 146}, {"level": 90, "ascension": 6, "base_atk": 741}],
        "passive_treatment": "included_in_panel", "passive_note": "controlled interpretation"}
    candidate = typed_loadout_baseline(equipment_db, proposal("weapon", values), detail)
    before = candidate[FIELD]["values"]["panel_basis"]
    assert before[0]["base_atk"] == 145
    assert before[1]["base_atk"]["status"] == "source_unavailable"
    assert candidate[FIELD]["field_sources"]["/panel_basis/1/base_atk"]["response"]["reason"] == "invalid_equipment_state"


def test_weapon_dataset_integrity_failure_cannot_become_an_approvable_missing_scalar(equipment_db, monkeypatch):
    detail = queries.get_weapon_detail(equipment_db, 11401)
    values = {"contract": LOADOUT_VALUES_CONTRACT, "extensions": {}, "weapon_type": detail["weapon"]["weapon_type"],
        "panel_basis": [{"level": 20, "ascension": 1, "base_atk": 146}],
        "passive_treatment": "included_in_panel", "passive_note": "controlled interpretation"}
    def invalid(*_args):
        raise EquipmentError("controlled dataset failure", "dataset_invalid")
    monkeypatch.setattr(queries, "get_weapon_stats", invalid)
    with pytest.raises(EquipmentError) as failure:
        typed_loadout_baseline(equipment_db, proposal("weapon", values), detail)
    assert failure.value.reason == "dataset_invalid"
