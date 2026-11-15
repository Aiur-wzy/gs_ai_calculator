"""E2 模板层测试：schema 校验 + 结构化补丁（整批拒绝、不污染原模板）。"""

import copy

import pytest

from genshin_engine.template import (
    PatchError,
    TemplateError,
    apply_patch,
    validate_template,
)


def make_template() -> dict:
    return {
        "version": 1,
        "form": "resolved",
        "meta": {"name": "测试"},
        "enemy": {"level": 90, "resistance": {"default": 0.1}},
        "characters": [
            {"id": "a", "level": 90,
             "stats": {"atk": 2000, "crit_rate": 0.4, "crit_dmg": 2.0,
                       "dmg_bonus": {"cryo": 0.466}, "em": 0}},
            {"id": "b", "level": 90, "stats": {"atk": 1000}},
        ],
        "switches": [{"t": 0, "character": "a"}],
        "buffs": [
            {"id": "bf1", "scope": "team", "start": 0.0, "end": 10.0,
             "effects": [{"zone": "dmg_bonus", "stat": "all", "value": 0.5}]}
        ],
        "hits": [
            {"t": 1.0, "character": "a", "skill": "burst", "hit_name": "斩击",
             "multiplier": 2.0, "damage_system": "classic", "element": "cryo"}
        ],
        "loop_duration": 20.0,
    }


def test_valid_template_passes():
    validate_template(make_template())


def test_unresolved_form_rejected():
    t = make_template()
    t["form"] = "unresolved"
    with pytest.raises(TemplateError, match="resolved"):
        validate_template(t)


@pytest.mark.parametrize("mutate,keyword", [
    (lambda t: t.update({"characters": []}), "characters"),
    (lambda t: t["characters"].append({"id": "a", "level": 90, "stats": {}}), "重复"),
    (lambda t: t["hits"][0].update({"character": "x"}), "未知角色"),
    (lambda t: t["buffs"][0].update({"end": 0.0}), "end > start"),
    (lambda t: t["buffs"][0]["effects"][0].update({"zone": "atk_percent"}), "未知 zone"),
    (lambda t: t["buffs"][0].update({"scope": "global"}), "scope"),
    (lambda t: t["buffs"][0].update({"target": "x"}), "未知 target"),
    (lambda t: t["hits"][0].update({"damage_system": "magic"}), "damage_system"),
    (lambda t: t["hits"][0].update({"reaction": {"type": "amplify"}}), "factor"),
    (lambda t: t["hits"][0].update({"damage_system": "transformative"}), "reaction.type"),
    (lambda t: (t["hits"][0].update({"damage_system": "lunar",
                                     "reaction": {"type": "lunar_electro"}}),
                t["hits"][0].pop("multiplier")), "multiplier"),
    (lambda t: t.update({"loop_duration": -1}), "loop_duration"),
])
def test_invalid_templates_rejected(mutate, keyword):
    t = make_template()
    mutate(t)
    with pytest.raises(TemplateError, match=keyword):
        validate_template(t)


def test_transformative_and_lunar_templates_pass():
    t = make_template()
    t["hits"] = [
        {"t": 1.0, "character": "a", "skill": "e", "hit_name": "超载",
         "damage_system": "transformative", "element": "pyro",
         "reaction": {"type": "overload"}},
        {"t": 2.0, "character": "a", "skill": "q", "hit_name": "月感电",
         "multiplier": 2.0, "damage_system": "lunar", "element": "electro",
         "reaction": {"type": "lunar_electro"}},
    ]
    validate_template(t)


def test_patch_replace_and_audit():
    t = make_template()
    out = apply_patch(t, [{"op": "replace", "path": "/characters/0/stats/atk", "value": 2500}])
    assert out["characters"][0]["stats"]["atk"] == 2500
    assert t["characters"][0]["stats"]["atk"] == 2000  # 原模板不被污染


def test_patch_add_and_remove():
    t = make_template()
    out = apply_patch(t, [
        {"op": "add", "path": "/buffs/-", "value": {
            "id": "bf2", "scope": "frontline", "start": 1.0, "end": 5.0,
            "effects": [{"zone": "crit_rate", "stat": "all", "value": 0.1}]}},
        {"op": "remove", "path": "/loop_duration"},
    ])
    assert len(out["buffs"]) == 2 and "loop_duration" not in out


def test_patch_bad_path_rejected_and_template_untouched():
    t = make_template()
    snapshot = copy.deepcopy(t)
    with pytest.raises(PatchError):
        apply_patch(t, [{"op": "replace", "path": "/characters/9/stats/atk", "value": 1}])
    assert t == snapshot


def test_patch_making_template_invalid_rejected():
    t = make_template()
    with pytest.raises(PatchError, match="校验失败"):
        apply_patch(t, [{"op": "replace", "path": "/enemy/level", "value": -5}])


def test_patch_atomicity_multi_ops():
    t = make_template()
    with pytest.raises(PatchError):
        apply_patch(t, [
            {"op": "replace", "path": "/characters/0/stats/atk", "value": 3000},
            {"op": "remove", "path": "/hits"},  # 第二操作使模板非法 → 整批拒绝
        ])
