"""The Agent bridge preserves existing growth math and snapshot semantics."""
import copy

import pytest

from genshin_app.agent_chat import agent_weapon_stats as service
from genshin_app.equipment.schema import PlanError
from genshin_db.equipment import EquipmentError


@pytest.fixture(autouse=True)
def clear_cache():
    service._CACHE.clear()
    yield
    service._CACHE.clear()


def test_bridge_keeps_units_source_and_calculation_without_panel_mutation(tmp_path, monkeypatch):
    expected = {"weapon_id": 13407, "level": 90, "ascension": 6, "version_line": "release",
        "formula_version": "weapon-growth-v1", "warnings": [],
        "data_version": {"upstream_commit": "a" * 40},
        "properties": [{"prop_type": "FIGHT_PROP_CHARGE_EFFICIENCY", "value": .306268,
                        "unit": "ratio", "value_status": "present", "source_refs": [{"table": "weapon_curve"}],
                        "calculation": {"init_value": .1, "curve_value": 3.06268, "promotion_add": 0}}]}
    calls = []
    def query(*args):
        calls.append(args)
        return copy.deepcopy(expected)
    monkeypatch.setattr(service.equipment_queries, "get_weapon_stats", query)
    result = service.query_weapon_stats(tmp_path / "db", 13407, 90, 6, "release", database_fingerprint="a" * 64)
    assert result == {**expected, "database_fingerprint": "a" * 64}
    result["properties"][0]["value"] = 0
    assert service.query_weapon_stats(tmp_path / "db", 13407, 90, 6, "release",
        database_fingerprint="a" * 64)["properties"][0]["value"] == .306268
    assert len(calls) == 1
    for state in [(90, 6, "b" * 64), (80, 5, "a" * 64), (100, 6, "a" * 64)]:
        service.query_weapon_stats(tmp_path / "db", 13407, state[0], state[1], "release", database_fingerprint=state[2])
    assert len(calls) == 4  # No hard-coded level-90 fallback or cross-snapshot cache.


def test_invalid_state_is_not_converted_to_missing_or_cached(tmp_path, monkeypatch):
    error = EquipmentError("武器等级与突破阶段不匹配", "invalid_equipment_state", 400,
                           {"available_states": [{"ascension": 6, "min_level": 80, "max_level": 90}]})
    def query(*args):
        raise error
    monkeypatch.setattr(service.equipment_queries, "get_weapon_stats", query)
    with pytest.raises(EquipmentError) as caught:
        service.query_weapon_stats(tmp_path / "db", 1, 100, 6, "release", database_fingerprint="a" * 64)
    assert caught.value is error and not service._CACHE


@pytest.mark.parametrize("field,value", [("weapon_id", True), ("level", 0), ("ascension", -1)])
def test_invalid_arguments_do_not_touch_database(tmp_path, monkeypatch, field, value):
    def unexpected(*args):
        pytest.fail("bad arguments reached database")
    monkeypatch.setattr(service.equipment_queries, "get_weapon_stats", unexpected)
    args = {"weapon_id": 1, "level": 90, "ascension": 6, field: value}
    with pytest.raises(PlanError):
        service.query_weapon_stats(tmp_path / "db", **args, version_line="release", database_fingerprint="a" * 64)


def test_tool_schema_has_no_refinement_or_line_override():
    assert set(service.TOOL_SCHEMA["required"]) == {"weapon_id", "level", "ascension"}
    assert set(service.TOOL_SCHEMA["properties"]) == set(service.TOOL_SCHEMA["required"])
    assert service.TOOL_SCHEMA["additionalProperties"] is False


def test_partial_or_unknown_attribute_retains_exact_status(tmp_path, monkeypatch):
    expected = {"weapon_id": 1, "level": 90, "ascension": 6, "version_line": "release",
        "formula_version": "weapon-growth-v1", "data_version": {},
        "properties": [{"prop_type": "FIGHT_PROP_TEST", "value": None, "unit": "unknown",
                        "value_status": "source_missing", "source_refs": [{"table": "weapon_property"}],
                        "calculation": {"init_value": None}}],
        "warnings": [{"code": "source_missing"}, {"code": "unmapped_prop_type"}]}
    monkeypatch.setattr(service.equipment_queries, "get_weapon_stats", lambda *args: copy.deepcopy(expected))
    result = service.query_weapon_stats(tmp_path / "db", 1, 90, 6, "release", database_fingerprint="a" * 64)
    assert result["properties"] == expected["properties"]
    assert result["warnings"] == expected["warnings"]
