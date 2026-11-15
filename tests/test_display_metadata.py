"""中文展示元数据可持久化；改名不改变机器身份、关联或伤害数值。"""

import copy
import json

import pytest

from genshin_app.equipment import (
    PlanError,
    new_plan,
    run_plan_calc,
    save_plan,
    validate_plan,
)
from genshin_app.equipment.files import input_view, read_plan
from genshin_engine import (
    TemplateError,
    calculate,
    convert_v1_to_v2,
    get_registry,
    validate_template,
)


def legacy_template():
    return {
        "version": 1, "form": "resolved", "enemy": {"level": 90},
        "characters": [{"id": "actor", "level": 90, "stats": {"atk": 1000}}],
        "buffs": [{"id": "buff.attack", "start": 0, "end": 10,
                   "effects": [{"zone": "stat_add", "stat": "atk", "value": 100}]}],
        "hits": [{"t": 1, "character": "actor", "skill": "elemental_skill",
                  "hit_name": "元素战技首段", "multiplier": 2,
                  "damage_system": "classic", "element": "cryo"}],
        "loop_duration": 20,
    }


DISPLAY_VALUES = (
    (("characters", 0), "name", "神里绫华"),
    (("characters", 0), "description", "角色展示说明"),
    (("buffs", 0), "name", "攻击力提高"),
    (("buffs", 0), "description", "测试区间内获得固定攻击力"),
    (("buffs", 0, "effects", 0), "name", "固定攻击力加成"),
    (("buffs", 0, "effects", 0), "description", "增加一百点攻击力"),
    (("hits", 0), "description", "测试用单次冰元素伤害"),
)


def at(value, path):
    for key in path:
        value = value[key]
    return value


def with_display(template):
    result = copy.deepcopy(template)
    for path, key, value in DISPLAY_VALUES:
        at(result, path)[key] = value
    return result


@pytest.mark.parametrize("version", [1, 2])
def test_display_roundtrip_keeps_calculation_and_stable_references(tmp_path, version):
    original = legacy_template()
    if version == 2:
        original = convert_v1_to_v2(original, hit_damage_types=[["damage.elemental_skill"]])
        original["buffs"][0]["effects"][0]["applies_to"] = {
            "mode": "hits", "hit_ids": [original["hits"][0]["id"]],
        }
    decorated = with_display(original)
    path = tmp_path / "中文模板.json"
    path.write_text(json.dumps(decorated, ensure_ascii=False), encoding="utf-8")
    loaded = json.loads(path.read_text("utf-8"))
    validate_template(loaded)
    assert loaded == decorated
    actual, expected = calculate(loaded), calculate(original)
    # Read-only source display evidence may legitimately change when display
    # metadata changes. Numeric output and stable event identities may not.
    assert [row["explanation_ref"] for row in actual["hits"]] == [
        row["explanation_ref"] for row in expected["hits"]]
    assert actual["damage_explanations"]["events"].keys() == expected["damage_explanations"]["events"].keys()
    for value in (actual, expected):
        value.pop("damage_explanations")
        value.pop("damage_explanation_fingerprint")
    assert actual == expected
    assert actual["summary"]["total_damage"] == 990


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("path,key,value", DISPLAY_VALUES)
def test_display_metadata_rejects_non_string_with_field_location(version, path, key, value):
    template = legacy_template()
    if version == 2:
        template = convert_v1_to_v2(template, hit_damage_types=[["damage.elemental_skill"]])
    at(template, path)[key] = {"text": value}
    expected_path = "".join(f"[{p}]" if isinstance(p, int) else ("." if i else "") + p
                            for i, p in enumerate((*path, key)))
    with pytest.raises(TemplateError) as exc:
        validate_template(template)
    assert expected_path in str(exc.value)


def test_native_conversion_save_reload_and_compose_preserve_display(tmp_path):
    source = with_display(legacy_template())
    plan = new_plan(source)
    plan["manual_template"]["hits"][0]["damage_type_ids"] = ["damage.elemental_skill"]
    plan["manual_effect_dependencies"][0]["mode"] = "source_only"
    response = run_plan_calc(plan, tmp_path / "unused.db")
    assert response["result"]["summary"] == calculate(source)["summary"]
    saved = response["plan"]
    for field in ("manual_template", "resolved_template"):
        for path, key, value in DISPLAY_VALUES:
            assert at(saved[field], path)[key] == value
    path = tmp_path / "中文原生方案.json"
    publication = save_plan(path, saved)
    loaded = read_plan(path)["plan"]
    assert loaded == publication["plan"]
    assert input_view(loaded) == input_view(saved)
    assert loaded["resolution"] == {"state": "pending"}
    assert loaded["resolved_template"] is None
    recomputed = run_plan_calc(loaded, tmp_path / "unused.db")
    for display_path, key, value in DISPLAY_VALUES:
        assert at(loaded["manual_template"], display_path)[key] == value
        assert at(recomputed["plan"]["resolved_template"], display_path)[key] == value
    assert recomputed["result"] == response["result"]


@pytest.mark.parametrize("path,key,value", DISPLAY_VALUES)
def test_native_schema_checks_display_types(path, key, value):
    plan = new_plan(with_display(legacy_template()))
    at(plan["manual_template"], path)[key] = [value]
    with pytest.raises(PlanError) as exc:
        validate_plan(plan)
    assert any(item["path"].endswith("." + key) for item in exc.value.details)


def test_legacy_free_text_is_not_rewritten_or_guessed():
    template = legacy_template()
    template["characters"][0].update(name="Custom actor", description="")
    template["buffs"][0].update(name="Unverified custom effect", description="unknown")
    original = copy.deepcopy(template)
    validate_template(template)
    calculate(template)
    assert template == original


def test_registry_labels_are_chinese_while_operations_keep_stable_ids():
    benefits = get_registry()["benefit_types"]
    assert all(not any("a" <= c.lower() <= "z" for c in row["label"]) for row in benefits)
    for row in benefits:
        assert row["id"] == f"benefit.{row['zone']}.{row['stat']}"
    atk = next(row for row in benefits if row["id"] == "benefit.stat_add.atk")
    assert atk["label"] == "面板平值加算（攻击力）"
    assert (atk["unit"], atk["value_semantics"]) == ("flat", "add")
