"""v2 分类与收益注册表；标签不扩展任何伤害公式。

本模块不读取数据库，也不推断机制。应用层可复用 effect_matches_hit
预览常规候选；时间、角色及公式是否消费收益仍由 core 判定。
"""

from __future__ import annotations

import copy

from genshin_engine.constants import (
    AGGRAVATE_FACTOR,
    BUFF_ZONES,
    ELEMENTS,
    LUNAR_FACTOR,
    MULTIPLICATIVE_ZONES,
    STELLAR_FACTOR,
    TRANSFORMATIVE_FACTOR,
)

ID_REGISTRY_VERSION = 1
SUPPORTED_TEMPLATE_VERSIONS = (1, 2)
APPLIES_TO_MODES = ("all", "types", "hits", "none")

DAMAGE_TYPES = {
    f"damage.{name}": {"id": f"damage.{name}", "label": label}
    for name, label in (
        ("normal_attack", "普通攻击伤害"), ("charged_attack", "重击伤害"),
        ("plunging_attack", "下落攻击伤害"), ("elemental_skill", "元素战技伤害"),
        ("elemental_burst", "元素爆发伤害"), ("reaction", "独立反应伤害"),
        ("other", "已明确的其他伤害"),
    )
}

REACTION_TYPES = tuple(dict.fromkeys((
    "amplify", *AGGRAVATE_FACTOR, *TRANSFORMATIVE_FACTOR, *LUNAR_FACTOR,
    *STELLAR_FACTOR, "stellar_superconduct",
)))

_ZONE_LABELS = {
    "base_add": "伤害基础平值加算", "stat_add": "面板平值加算",
    "stat_pct": "按基础面板比例加算", "dmg_bonus": "传统增伤加算",
    "crit_rate": "暴击率加算", "crit_dmg": "暴击伤害加算", "em": "元素精通加算",
    "reaction_bonus": "反应增伤加算", "reaction_flat": "反应平值加算",
    "lunar_base": "月曜基础增伤加算", "lunar_elevate": "月曜擢升加算",
    "stellar_base": "星烁基础增伤加算", "stellar_elevate": "星烁擢升加算",
    "def_reduction": "减防加算", "def_ignore": "无视防御加算", "res_shred": "减抗加算",
    "base_mult": "基础区乘算", "lunar_mult": "月曜大权乘算", "stellar_mult": "星烁大权乘算",
}

_STAT_LABELS = {
    "all": "全部", "atk": "攻击力", "hp": "生命值", "def": "防御力",
    "physical": "物理", "pyro": "火元素", "hydro": "水元素", "electro": "雷元素",
    "cryo": "冰元素", "anemo": "风元素", "geo": "岩元素", "dendro": "草元素",
    "amplify": "增幅反应", "aggravate": "超激化", "spread": "蔓激化",
    "burning": "燃烧", "swirl": "扩散", "superconduct": "超导", "electro_charged": "感电",
    "bloom": "绽放", "overload": "超载", "shattered": "碎冰", "burgeon": "烈绽放",
    "hyperbloom": "超绽放", "lunar_bloom": "月绽放", "lunar_electro": "月感电",
    "lunar_crystallize": "月结晶", "stellar_swirl": "直伤星扩散",
    "stellar_swirl_anemo": "反应星扩散·风", "stellar_swirl_cryo_lv2": "反应星扩散·冰（二级）",
    "stellar_swirl_cryo_lv3": "反应星扩散·冰（三级）", "stellar_superconduct": "星超导",
}


def _zone_stats(zone: str) -> tuple[str, ...]:
    if zone in ("stat_add", "stat_pct"):
        return ("atk", "hp", "def")
    if zone in ("dmg_bonus", "res_shred"):
        return ("all", *ELEMENTS)
    if zone == "reaction_bonus":
        return ("all", *REACTION_TYPES)
    return ("all",)


BENEFIT_TYPES = {}
for _zone in BUFF_ZONES:
    for _stat in _zone_stats(_zone):
        _id = f"benefit.{_zone}.{_stat}"
        _mult = _zone in MULTIPLICATIVE_ZONES
        BENEFIT_TYPES[_id] = {
            "id": _id, "label": f"{_ZONE_LABELS[_zone]}（{_STAT_LABELS[_stat]}）",
            "zone": _zone, "stat": _stat,
            "unit": "multiplier" if _mult else (
                "flat" if _zone in ("base_add", "stat_add", "em", "reaction_flat") else "ratio"
            ),
            "value_semantics": "multiply" if _mult else (
                "add_base_fraction" if _zone == "stat_pct" else "add"
            ),
        }


def get_registry() -> dict:
    """返回独立 JSON 副本，供 capabilities/GUI/Skill 共用；不包含 native 微调状态。"""
    return copy.deepcopy({
        "id_registry_version": ID_REGISTRY_VERSION,
        "supported_template_versions": list(SUPPORTED_TEMPLATE_VERSIONS),
        "damage_types": list(DAMAGE_TYPES.values()),
        "benefit_types": list(BENEFIT_TYPES.values()),
        "applies_to_modes": list(APPLIES_TO_MODES),
        "custom_damage_types_supported": True,
        "custom_benefit_types_supported": False,
    })


def effect_matches_hit(effect: dict, hit: dict) -> bool:
    """已校验的 v2 effect/hit 的纯选择器匹配，不执行时间/角色/公式条件。"""
    selector = effect["applies_to"]
    mode = selector["mode"]
    if mode == "all":
        return True
    if mode == "none":
        return False
    if mode == "hits":
        return hit["id"] in selector["hit_ids"]
    if mode == "types":
        return bool(set(hit["damage_type_ids"]).intersection(selector["damage_type_ids"]))
    raise ValueError(f"未校验的 applies_to.mode: {mode!r}")
