"""V2-01：分类与公式分离、effect 范围、明确转换和真实消费回执。"""

import copy
import json
from pathlib import Path

import pytest

from genshin_engine import (
    PatchError,
    TemplateError,
    apply_patch,
    calculate,
    convert_v1_to_v2,
    effect_matches_hit,
    get_registry,
    validate_template,
)
from genshin_engine.constants import BUFF_ZONES
from genshin_engine.registry import BENEFIT_TYPES


def legacy():
    return {
        "version": 1, "form": "resolved", "meta": {"assumptions": ["人工测试"]},
        "enemy": {"level": 90, "resistance": {"default": 0.1}},
        "characters": [
            {"id": "a", "level": 90, "stats": {"atk": 2000, "base_atk": 1000, "hp": 10000,
                                               "em": 100, "crit_rate": 0, "crit_dmg": 0}},
            {"id": "b", "level": 90, "stats": {"atk": 1000}},
        ],
        "switches": [{"t": 0, "character": "a"}, {"t": 3, "character": "b"}],
        "buffs": [],
        "hits": [
            {"t": 1, "character": "a", "skill": "同名", "multiplier": 1,
             "damage_system": "classic", "element": "pyro"},
            {"t": 1, "character": "a", "skill": "同名", "multiplier": 1,
             "damage_system": "classic", "element": "pyro"},
        ],
        "loop_duration": 20,
    }


def template():
    t = convert_v1_to_v2(legacy(), hit_damage_types=[
        ["damage.normal_attack"], ["damage.elemental_burst", "damage.elemental_skill"],
    ])
    for h, ident in zip(t["hits"], ["h.normal", "h.burst"], strict=True):
        h["id"] = ident
    return t


def effect(ident, zone="dmg_bonus", stat="all", value=0.5, selector=None):
    return {"id": ident, "benefit_type_id": f"benefit.{zone}.{stat}", "zone": zone,
            "stat": stat, "value": value,
            "applies_to": {"mode": "all"} if selector is None else selector}


def add_buff(t, effects, **kwargs):
    t["buffs"].append({"id": "bf", "scope": "team", "start": 0, "end": 10,
                       "effects": effects, **kwargs})


def refs(row):
    return [r["effect_id"] for r in row["matched_effect_refs"]]


def test_mixed_effect_scope_and_multiple_labels_count_once():
    t = template()
    add_buff(t, [effect("atk", "stat_add", "atk", 1000), effect("burst", selector={
        "mode": "types", "damage_type_ids": ["damage.elemental_burst", "damage.elemental_skill"]
    })])
    t["hits"][1]["count"] = 2
    out = calculate(t)
    assert [h["expected"] for h in out["hits"]] == pytest.approx([1350, 4050])
    assert refs(out["hits"][0]) == ["atk"]
    assert refs(out["hits"][1]) == ["atk", "burst"]
    assert out["hits"][1]["matched_effect_refs"][1] == {
        "buff_id": "bf", "effect_id": "burst", "benefit_type_id": "benefit.dmg_bonus.all"}
    assert out["hits"][0]["id"] == "h.normal"
    assert out["template_version"] == 2 and out["id_registry_version"] == 1


def test_exact_hits_none_and_reordering_keep_identity():
    t = template()
    add_buff(t, [effect("only", selector={"mode": "hits", "hit_ids": ["h.normal"]}),
                 effect("excluded", value=999, selector={"mode": "none"})])
    first = calculate(t)
    t["hits"].reverse()
    second = calculate(t)
    assert {r["id"]: r["expected"] for r in first["hits"]} == {
        r["id"]: r["expected"] for r in second["hits"]}
    assert [h["expected"] for h in first["hits"]] == [1350, 900]
    assert refs(first["hits"][1]) == []
    assert first["hits"][1]["buffs"] == []


def test_snapshot_uses_cast_time_but_current_hit_id_and_scope():
    t = template()
    t["hits"][0].update(t=5, snapshot=True, cast_t=1)
    t["hits"][1].update(t=5, snapshot=True, cast_t=1)
    add_buff(t, [effect("snap", selector={"mode": "hits", "hit_ids": ["h.burst"]})],
             scope="frontline", target="a", end=2)
    assert [h["expected"] for h in calculate(t)["hits"]] == [900, 1350]
    t["hits"][1]["snapshot"] = False
    assert calculate(t)["hits"][1]["expected"] == 900
    t["hits"][1].update(snapshot=True, character="b")
    assert refs(calculate(t)["hits"][1]) == []


@pytest.mark.parametrize("change", [
    {"target": "b"}, {"scope": "frontline"}, {"end": 1}, {"start": 2},
])
def test_hit_whitelist_does_not_bypass_other_conditions(change):
    t = template()
    t["switches"] = [{"t": 0, "character": "b"}]
    add_buff(t, [effect("e", selector={"mode": "hits", "hit_ids": ["h.burst"]})], **change)
    row = calculate(t)["hits"][1]
    assert row["expected"] == 900 and refs(row) == []


def test_enemy_debuff_ignores_target_but_obeys_element_and_effect_scope():
    t = template()
    add_buff(t, [effect("res", "res_shred", "pyro", 0.2,
                        {"mode": "hits", "hit_ids": ["h.burst"]}),
                 effect("wrong_element", "res_shred", "cryo", 0.8)],
             target="b", scope="frontline")
    out = calculate(t)
    assert [h["expected"] for h in out["hits"]] == [900, 1050]
    assert refs(out["hits"][1]) == ["res"]


@pytest.mark.parametrize("system,reaction,reaction_base,wanted", [
    ("classic", None, False, {"base_add", "base_mult", "dmg_bonus", "crit_rate", "crit_dmg",
                             "def_reduction", "def_ignore", "res_shred", "stat_add"}),
    ("classic", {"type": "amplify", "factor": 2}, False,
     {"base_add", "base_mult", "dmg_bonus", "crit_rate", "crit_dmg", "def_reduction",
      "def_ignore", "res_shred", "em", "reaction_bonus", "stat_add"}),
    ("transformative", {"type": "overload"}, False,
     {"em", "reaction_bonus", "reaction_flat", "res_shred"}),
    ("lunar", {"type": "lunar_electro"}, True,
     {"lunar_base", "lunar_mult", "lunar_elevate", "em", "crit_rate", "crit_dmg",
      "reaction_bonus", "reaction_flat", "res_shred"}),
    ("stellar", {"type": "stellar_superconduct", "layers": 2}, False,
     {"stellar_base", "stellar_mult", "stellar_elevate", "em", "crit_rate", "crit_dmg",
      "reaction_bonus", "reaction_flat", "res_shred", "stat_add"}),
])
def test_receipt_contains_only_formula_consumed_operations(system, reaction, reaction_base, wanted):
    t = template()
    t["hits"][0].update(damage_system=system, reaction=reaction, reaction_base=reaction_base)
    zones = [z for z in BUFF_ZONES if z != "stat_pct"]
    add_buff(t, [effect(z, z, "atk" if z == "stat_add" else "all",
                        1.1 if z.endswith("mult") else 0.1) for z in zones])
    row = calculate(t)["hits"][0]
    assert set(refs(row)) == wanted
    assert row["buffs"] == ["bf"]


def test_classic_em_scaling_consumes_em_but_other_scaling_does_not():
    t = template()
    add_buff(t, [effect("em", "em", value=100), effect("pct", "stat_pct", "atk", 0.2)])
    t["hits"][0]["scaling_stat"] = "em"
    out = calculate(t)
    assert refs(out["hits"][0]) == ["em"]
    assert out["hits"][0]["expected"] == 90
    assert refs(out["hits"][1]) == ["pct"]
    assert out["hits"][1]["expected"] == 990


def test_irrelevant_percent_buff_does_not_require_missing_base_and_reaction_filter_still_applies():
    t = template()
    del t["characters"][0]["stats"]["base_atk"]
    t["hits"][0].update(damage_system="transformative", reaction={"type": "overload"})
    add_buff(t, [effect("pct", "stat_pct", "atk", 0.2,
                        {"mode": "hits", "hit_ids": ["h.normal"]}),
                 effect("wrong_reaction", "reaction_bonus", "swirl", 100)])
    assert refs(calculate(t)["hits"][0]) == []


def test_custom_classification_adds_no_math_and_exports_as_independent_template():
    t = template()
    t["type_definitions"]["damage_types"] = [{
        "id": "custom.damage.extra", "label": "追加", "description": "此轴的人工分类",
        "source_refs": [{"kind": "manual", "note": "明确测试"}],
    }]
    t["hits"][0]["damage_type_ids"] = ["custom.damage.extra"]
    e = effect("custom", selector={"mode": "types", "damage_type_ids": ["custom.damage.extra"]})
    add_buff(t, [e])
    assert effect_matches_hit(e, t["hits"][0])
    assert not effect_matches_hit(e, t["hits"][1])
    saved = json.loads(json.dumps(t))
    assert [h["expected"] for h in calculate(saved)["hits"]] == [1350, 900]
    saved["hits"][0]["damage_system"] = "custom.math"
    with pytest.raises(TemplateError, match="damage_system"):
        calculate(saved)


@pytest.mark.parametrize("mutate,path", [
    (lambda t: t.update(id_registry_version=True), "id_registry_version"),
    (lambda t: t.update(id_registry_version=2), "id_registry_version"),
    (lambda t: t.pop("type_definitions"), "type_definitions"),
    (lambda t: t.update(rotation_tuning={"buff_hit_matches": []}), "rotation_tuning"),
    (lambda t: t["hits"][0].pop("id"), "hits[0].id"),
    (lambda t: t["hits"][0].update(id="h.burst"), "hits[1].id"),
    (lambda t: t["hits"][0].update(damage_type_ids=None), "hits[0].damage_type_ids"),
    (lambda t: t["hits"][0].update(damage_type_ids=["damage.normal_attack"] * 2), "damage_type_ids[1]"),
    (lambda t: t["hits"][0].update(damage_type_ids=["damage.typo"]), "damage_type_ids[0]"),
    (lambda t: t["hits"][0].update(damage_type_ids=["custom.damage.unknown"]), "damage_type_ids[0]"),
    (lambda t: t["hits"][0].update(damage_type_ids=[{}]), "damage_type_ids[0]"),
    (lambda t: t["hits"][0].update(multiplire=2), "hits[0].multiplire"),
    (lambda t: t["characters"][0]["stats"].update(attack=2000), "stats.attack"),
    (lambda t: t["buffs"][0].update(applies_to={"mode": "all"}), "buffs[0].applies_to"),
    (lambda t: t["buffs"][0]["effects"][0].pop("id"), "effects[0].id"),
    (lambda t: t["buffs"][0]["effects"].append(copy.deepcopy(t["buffs"][0]["effects"][0])),
     "effects[1].id"),
    (lambda t: t["buffs"][0]["effects"][0].pop("benefit_type_id"), "benefit_type_id"),
    (lambda t: t["buffs"][0]["effects"][0].update(benefit_type_id="benefit.crit_rate.all"),
     "benefit_type_id"),
    (lambda t: t["buffs"][0]["effects"][0].update(benefit_type_id={}), "benefit_type_id"),
    (lambda t: t["buffs"][0]["effects"][0].update(stat="atk"), "benefit_type_id"),
    (lambda t: t["buffs"][0]["effects"][0].pop("applies_to"), "applies_to"),
    (lambda t: t["buffs"][0]["effects"][0].update(applies_to=None), "applies_to"),
    (lambda t: t["buffs"][0]["effects"][0].update(applies_to={"mode": "hits", "hit_ids": []}),
     "hit_ids"),
    (lambda t: t["buffs"][0]["effects"][0].update(applies_to={"mode": "types", "damage_type_ids": []}),
     "damage_type_ids"),
    (lambda t: t["buffs"][0]["effects"][0].update(applies_to={"mode": "hits", "hit_ids": ["missing"]}),
     "hit_ids[0]"),
    (lambda t: t["buffs"][0]["effects"][0].update(applies_to={"mode": "all", "hit_ids": ["h.normal"]}),
     "applies_to.hit_ids"),
    (lambda t: t["buffs"][0]["effects"][0].update(applies_to={"mode": []}), "applies_to.mode"),
])
def test_strict_v2_errors_are_located_and_leave_input_unchanged(mutate, path):
    t = template()
    add_buff(t, [effect("e")])
    mutate(t)
    before = copy.deepcopy(t)
    with pytest.raises(TemplateError) as exc:
        calculate(t)
    assert path in str(exc.value)
    assert t == before


@pytest.mark.parametrize("row", [
    {}, {"id": "damage.normal_attack", "label": "重写", "description": "禁止", "source_refs": [{}]},
    {"id": "custom.damage.", "label": "空", "description": "空", "source_refs": [{}]},
    {"id": "custom.damage.x", "label": "x", "description": "说明", "source_refs": []},
    {"id": [], "label": "x", "description": "说明", "source_refs": [{"kind": "manual"}]},
])
def test_custom_type_definition_requires_identity_and_evidence(row):
    t = template()
    t["type_definitions"]["damage_types"] = [row]
    with pytest.raises(TemplateError, match="type_definitions.damage_types"):
        validate_template(t)


def test_same_effect_id_in_different_buffs_is_valid():
    t = template()
    add_buff(t, [effect("e")])
    add_buff(t, [effect("e")], id="other")
    assert [r["buff_id"] for r in calculate(t)["hits"][0]["matched_effect_refs"]] == ["bf", "other"]


def test_patch_removing_referenced_event_is_atomic_and_can_repair_scope():
    t = template()
    add_buff(t, [effect("e", selector={"mode": "hits", "hit_ids": ["h.normal"]})])
    original = copy.deepcopy(t)
    with pytest.raises(PatchError, match="hit_ids"):
        apply_patch(t, [{"op": "remove", "path": "/hits/0"}])
    assert t == original
    out = apply_patch(t, [
        {"op": "remove", "path": "/hits/0"},
        {"op": "replace", "path": "/buffs/0/effects/0/applies_to", "value": {"mode": "none"}},
    ])
    assert len(out["hits"]) == 1 and refs(calculate(out)["hits"][0]) == []


def test_explicit_conversion_preserves_real_example_math_without_guessing_categories():
    old = json.loads((Path(__file__).parents[1] / "examples/freeze_team.json").read_text("utf-8"))
    original = copy.deepcopy(old)
    with pytest.raises(TemplateError, match="hit_damage_types"):
        convert_v1_to_v2(old, hit_damage_types=[])
    converted = convert_v1_to_v2(old, hit_damage_types=[["damage.other"] for _ in old["hits"]])
    assert old == original
    assert calculate(old)["summary"] == calculate(converted)["summary"]
    assert all(e["applies_to"] == {"mode": "all"}
               for b in converted["buffs"] for e in b["effects"])
    assert len({h["id"] for h in converted["hits"]}) == len(converted["hits"])
    assert "id" not in calculate(old)["hits"][0]
    assert "matched_effect_refs" not in calculate(old)["hits"][0]
    with pytest.raises(TemplateError, match="仅接受 v1"):
        convert_v1_to_v2(converted, hit_damage_types=[])


def test_all_four_formulas_keep_numeric_results_after_conversion():
    t = legacy()
    sample = t["hits"][0]
    t["hits"] = []
    for system, reaction, reaction_base in (
        ("classic", None, False), ("classic", {"type": "spread"}, False),
        ("classic", {"type": "amplify", "factor": 1.5}, False),
        ("transformative", {"type": "overload"}, False),
        ("lunar", {"type": "lunar_electro"}, True),
        ("stellar", {"type": "stellar_superconduct", "layers": 5}, False),
    ):
        h = copy.deepcopy(sample)
        h.update(damage_system=system, reaction=reaction, reaction_base=reaction_base)
        t["hits"].append(h)
    add_buff(t, [{"zone": z, "stat": "atk" if z in ("stat_add", "stat_pct") else "all",
                  "value": 1.1 if z.endswith("mult") else 0.1} for z in BUFF_ZONES])
    before = calculate(t)
    after = calculate(convert_v1_to_v2(t, hit_damage_types=[["damage.other"] for _ in t["hits"]]))
    assert after["summary"] == before["summary"]
    for old_row, new_row in zip(before["hits"], after["hits"], strict=True):
        assert (old_row["expected"], old_row["crit"], old_row["zones"]) == (
            new_row["expected"], new_row["crit"], new_row["zones"])


def test_v2_finite_input_overflow_is_a_located_domain_error():
    t = template()
    t["characters"][0]["stats"]["atk"] = 1e308
    add_buff(t, [effect("overflow", "stat_add", "atk", 1e308)])
    with pytest.raises(TemplateError) as exc:
        calculate(t)
    assert "hits[0]" in str(exc.value) and "有限" in str(exc.value)


def test_v1_unknown_stat_stays_compatible_but_conversion_needs_confirmation():
    t = legacy()
    add_buff(t, [{"zone": "crit_rate", "stat": "legacy_note", "value": 0.2}])
    assert calculate(t)["summary"]["total_damage"] == 1800
    with pytest.raises(TemplateError, match="旧收益没有对应注册项"):
        convert_v1_to_v2(t, hit_damage_types=[["damage.other"], ["damage.other"]])


@pytest.mark.parametrize("place,field,value", [
    ("top", "rotation_tuning", {"buff_hit_matches": []}),
    ("top", "id_registry_version", 1),
    ("hit", "damage_type_ids", ["damage.normal_attack"]),
    ("buff", "applies_to", {"mode": "none"}),
    ("effect", "applies_to", {"mode": "none"}),
    ("effect", "benefit_type_id", "benefit.dmg_bonus.all"),
])
def test_v1_does_not_silently_ignore_v2_semantics(place, field, value):
    t = legacy()
    add_buff(t, [{"zone": "dmg_bonus", "stat": "all", "value": 0.2}])
    obj = {"top": t, "hit": t["hits"][0], "buff": t["buffs"][0],
           "effect": t["buffs"][0]["effects"][0]}[place]
    obj[field] = value
    with pytest.raises(TemplateError, match="v1 不支持"):
        calculate(t)


def test_registry_covers_all_existing_operations_and_returns_a_copy():
    data = get_registry()
    assert {b["zone"] for b in data["benefit_types"]} == set(BUFF_ZONES)
    assert BENEFIT_TYPES["benefit.stat_pct.atk"]["value_semantics"] == "add_base_fraction"
    assert BENEFIT_TYPES["benefit.base_mult.all"]["unit"] == "multiplier"
    assert data["custom_benefit_types_supported"] is False
    data["benefit_types"][0]["zone"] = "corrupted"
    assert get_registry()["benefit_types"][0]["zone"] != "corrupted"
