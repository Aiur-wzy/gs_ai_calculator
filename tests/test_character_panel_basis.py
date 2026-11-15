"""Database white values never become guessed or double-counted final panels."""
from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from genshin_app.equipment import character_panel_basis
from genshin_app.equipment import panel_basis as module
from genshin_app.equipment.schema import PlanError
from genshin_app.gui.api import GuiApi
from genshin_db.equipment import EquipmentError

VERSION = {"version_line": "release", "upstream_commit": "a" * 40,
           "equipment_schema_version": 1, "built_at": "2026-10-05T00:00:00Z"}


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "genshin.db"
    path.touch()
    character = {"id": 1, "name": "测试角色", "weapon_type": "WEAPON_SWORD_ONE_HAND",
                 "atk_90": 300.25, "hp_90": 12000.5, "def_90": 800.25,
                 "atk_100": 375.5, "hp_100": 13000.75, "def_100": 850.75,
                 "crit_rate": .05, "crit_dmg": .5, "ascension_value": .384}
    weapon = {"weapon": {"weapon_type": "WEAPON_SWORD_ONE_HAND"}}
    stats = {"properties": [{"prop_type": "FIGHT_PROP_BASE_ATTACK", "value": 674.33,
                             "source_refs": [{"table": "weapon", "upstream_commit": "a" * 40}],
                             "calculation": {"init_value": 47.5, "promotion_add": 186.7}},
                            {"prop_type": "FIGHT_PROP_CRITICAL_HURT", "value": .44}],
             "data_version": VERSION, "warnings": []}
    scope = []

    @contextmanager
    def session(db):
        assert Path(db) == path
        scope.append("active")
        try:
            yield
        finally:
            scope.pop()

    def detail(db, cid, line, **kwargs):
        assert scope == ["active"]
        assert kwargs == {"sections": ["character"]}
        return {"character": copy.deepcopy(character)}

    def weapon_stats(db, wid, level, ascension, line):
        assert scope == ["active"]
        if (level, ascension) != (90, 6):
            raise EquipmentError("武器等级与突破阶段不匹配", "invalid_equipment_state")
        return copy.deepcopy(stats)

    monkeypatch.setattr(module.queries, "equipment_read_session", session)
    monkeypatch.setattr(module.queries, "get_character_detail", detail)
    monkeypatch.setattr(module.queries, "get_meta", lambda _: {
        "upstream_commit:release": "a" * 40, "built_at:release": VERSION["built_at"],
        "datasets": {"equipment": {"lines": {"release": {"state": "ready", "schema_version": 1,
                        "upstream_commit": "a" * 40, "built_at": VERSION["built_at"]}}}}})
    monkeypatch.setattr(module.queries, "get_weapon_detail", lambda *a, **kw: copy.deepcopy(weapon))
    monkeypatch.setattr(module.queries, "get_weapon_stats", weapon_stats)
    return path, character, weapon, stats


def selected_weapon(**changes):
    return {"weapon_id": 11509, "level": 90, "ascension": 6, "refinement": 1,
            "name": "测试武器", **changes}


def test_90_and_100_read_distinct_actual_columns_without_final_panel(database):
    path, character, _, _ = database
    weapon = selected_weapon()
    original = copy.deepcopy(weapon)
    before = path.read_bytes()
    for level in (90, 100):
        result = character_panel_basis(path, 1, level, weapon=weapon)
        assert result["state"] == "ready"
        assert result["panel_base"] == {"base_atk": character[f"atk_{level}"] + 674.33,
                                        "base_hp": character[f"hp_{level}"], "base_def": character[f"def_{level}"]}
        assert result["character_base_atk"] == character[f"atk_{level}"]
        assert result["source_refs"][0]["fields"] == [f"atk_{level}", f"hp_{level}", f"def_{level}"]
        assert result["data_version"] == VERSION
        assert not {"atk", "hp", "def", "crit_rate", "crit_dmg", "em", "dmg_bonus"} & result.keys()
        assert result["supported_levels"] == [90, 100]
        assert not result["missing_inputs"]
    assert weapon == original and path.read_bytes() == before


def test_no_weapon_is_unknown_total_attack_not_character_only(database):
    result = character_panel_basis(database[0], 1)
    assert result["state"] == "partial"
    assert result["character_base_atk"] == 300.25
    assert result["panel_base"] == {"base_atk": None, "base_hp": 12000.5, "base_def": 800.25}
    assert result["missing_inputs"][0]["code"] == "weapon_selection_required"


@pytest.mark.parametrize("key", ["weapon_id", "level", "ascension"])
def test_incomplete_weapon_keeps_known_hp_def(database, key):
    weapon = selected_weapon()
    del weapon[key]
    result = character_panel_basis(database[0], 1, weapon=weapon)
    assert result["state"] == "partial" and result["panel_base"]["base_atk"] is None
    assert any(item["path"] == "weapon." + key for item in result["missing_inputs"])


def test_incompatible_weapon_never_produces_base_attack(database):
    database[2]["weapon"]["weapon_type"] = "WEAPON_BOW"
    result = character_panel_basis(database[0], 1, weapon=selected_weapon())
    assert result["state"] == "partial" and result["panel_base"]["base_atk"] is None
    assert result["missing_inputs"][0]["code"] == "weapon_type_mismatch"


def test_weapon_level_does_not_follow_character_level_or_fallback(database):
    result = character_panel_basis(database[0], 1, 100, weapon=selected_weapon(level=100))
    assert result["character_base_atk"] == 375.5
    assert result["panel_base"]["base_atk"] is None
    assert result["missing_inputs"][0]["code"] == "weapon_invalid_equipment_state"


@pytest.mark.parametrize("level", [1, 80, 89, 91, 95, 99])
def test_other_levels_never_use_ninety_or_extrapolate(database, level):
    result = character_panel_basis(database[0], 1, level, weapon=selected_weapon())
    assert result["state"] == "unavailable"
    assert all(v is None for v in result["panel_base"].values())
    assert result["missing_inputs"][0]["code"] == "unsupported_character_level"


@pytest.mark.parametrize("value", [None, -1, float("nan"), float("inf"), "300", True])
def test_missing_or_invalid_level_value_never_replaced_with_zero(database, value):
    database[1]["atk_100"] = value
    result = character_panel_basis(database[0], 1, 100, weapon=selected_weapon())
    assert result["state"] == "partial"
    assert result["character_base_atk"] is None and result["panel_base"]["base_atk"] is None
    assert result["panel_base"]["base_hp"] == 13000.75
    assert result["missing_inputs"][0]["code"] == "character_basis_missing"
    json.dumps(result, allow_nan=False)


def test_duplicate_or_missing_weapon_white_value_stays_unknown(database):
    database[3]["properties"].append(copy.deepcopy(database[3]["properties"][0]))
    result = character_panel_basis(database[0], 1, weapon=selected_weapon())
    assert result["panel_base"]["base_atk"] is None
    assert result["missing_inputs"][0]["code"] == "weapon_basis_missing"


def test_database_absent_does_not_create_file(tmp_path):
    path = tmp_path / "missing.db"
    result = character_panel_basis(path, 1)
    assert result["state"] == "unavailable" and not path.exists()
    assert result["missing_inputs"][0]["code"] == "database_unavailable"


def test_character_not_in_selected_version_is_unavailable(database, monkeypatch):
    def not_found(*args, **kwargs):
        raise module.queries.NotFoundError("missing")
    monkeypatch.setattr(module.queries, "get_character_detail", not_found)
    result = character_panel_basis(database[0], 1, version_line="beta")
    assert result["state"] == "unavailable"
    assert result["missing_inputs"][0]["code"] == "character_unavailable"


@pytest.mark.parametrize("level", [True, 90.0, "90", 0, 101])
def test_invalid_input_is_not_silently_coerced(database, level):
    with pytest.raises(PlanError):
        character_panel_basis(database[0], 1, level)


def test_gui_bridge_exposes_service_and_unknown_level_diagnostics(database):
    api = GuiApi(database[0].parent)
    response = api.character_panel_basis(1, 100, "release", selected_weapon())
    assert response["code"] == 0
    assert response["data"]["panel_base"]["base_atk"] == 375.5 + 674.33
    unknown = api.character_panel_basis(1, 80)
    assert unknown["code"] == 0 and unknown["data"]["state"] == "unavailable"
    assert api.character_panel_basis(1, True)["code"] == 1


def test_invalid_weapon_number_cannot_leak_non_json_values(database):
    result = character_panel_basis(database[0], 1, weapon=selected_weapon(level=float("nan")))
    assert result["weapon_contribution"]["level"] is None
    assert result["panel_base"]["base_atk"] is None
    json.dumps(result, allow_nan=False)
