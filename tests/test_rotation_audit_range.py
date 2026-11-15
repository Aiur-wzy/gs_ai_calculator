import pytest

from genshin_engine.rotation import validate_rotation_model
from genshin_engine.rotation_schema import ROTATION_SCHEMA, rotation_capabilities


@pytest.mark.parametrize("value", [2, 21])
def test_cycle_audit_range_reports_actual_bounds_and_meaning(value):
    response = validate_rotation_model({"schema_version": 1, "max_cycles": value,
        "characters": [], "abilities": [], "actions": [], "events": []})
    row = next(issue for issue in response["issues"] if issue["path"] == "rotation_model.max_cycles")
    assert {key: row[key] for key in ("actual", "minimum", "maximum", "default")} == {
        "actual": value, "minimum": 3, "maximum": 20, "default": 3}
    assert "不是题目的游戏轮数" in row["repair_hint"]
    caps = rotation_capabilities()
    assert caps["field_semantics"]["max_cycles"] == ROTATION_SCHEMA["properties"]["max_cycles"]["description"]
