"""乘区管线与四伤害体系公式（规格：mechanics/damage-formula.md 用户校订版）。

所有 damage 函数输入 ctx 字典（由 core 装配），输出
{"expected": 期望伤害, "crit": 暴击大数字（不可暴击体系为 None）, "zones": 乘区分解}。
精度约定：浮点直算不追游戏内舍入（engine.md §3）。
"""

from __future__ import annotations

from genshin_engine.constants import (
    AGGRAVATE_EM_COEF,
    AGGRAVATE_EM_DIVISOR,
    AGGRAVATE_FACTOR,
    AMPLIFY_EM_COEF,
    AMPLIFY_EM_DIVISOR,
    CRIT_RATE_CAP,
    DEF_IGNORE_CAP,
    DEF_REDUCTION_CAP,
    LUNAR_EM_COEF,
    LUNAR_EM_DIVISOR,
    REACTION_BASE_VALUE,
    RES_HIGH_THRESHOLD,
    TRANSFORMATIVE_EM_COEF,
    TRANSFORMATIVE_EM_DIVISOR,
)


def defense_multiplier(
    char_level: int, enemy_level: int, def_reduction: float = 0.0, def_ignore: float = 0.0
) -> float:
    """防御系数。减防加算封顶 90%，无视防御加算封顶 100%（超过无收益）。"""
    dr = min(def_reduction, DEF_REDUCTION_CAP)
    di = min(def_ignore, DEF_IGNORE_CAP)
    return (char_level + 100) / ((char_level + 100) + (enemy_level + 100) * (1 - dr) * (1 - di))


def resistance_multiplier(resistance: float) -> float:
    """抗性系数（三段）：<0 → 1-r/2；[0,75%) → 1-r；≥75% → 1/(1+4r)。"""
    r = resistance
    if r < 0:
        return 1 - r / 2
    if r < RES_HIGH_THRESHOLD:
        return 1 - r
    return 1 / (1 + 4 * r)


def crit_expected_multiplier(crit_rate: float, crit_dmg: float) -> float:
    """暴击期望区：1 + min(暴击率, 100%) × 暴击伤害。"""
    return 1 + min(crit_rate, CRIT_RATE_CAP) * crit_dmg


def amplify_em_bonus(em: float) -> float:
    return AMPLIFY_EM_COEF * em / (em + AMPLIFY_EM_DIVISOR)


def transformative_em_bonus(em: float) -> float:
    return TRANSFORMATIVE_EM_COEF * em / (em + TRANSFORMATIVE_EM_DIVISOR)


def lunar_em_bonus(em: float) -> float:
    return LUNAR_EM_COEF * em / (em + LUNAR_EM_DIVISOR)


def reaction_base_value(level: int) -> float:
    return REACTION_BASE_VALUE[level]


def _result(expected: float, crit: float | None, zones: dict) -> dict:
    return {"expected": expected, "crit": crit, "zones": zones}


def classic_damage(ctx: dict, *, explain: bool = False) -> dict:
    """传统直伤：(倍率×属性×大权区 + 羽毛区 + 激化固定增伤) × 暴击 × 增伤 × 抗性 × 防御 × 增幅。"""
    base_mult = ctx.get("base_mult", 1.0)
    scaled_base = ctx["multiplier"] * ctx["scaling"] * base_mult
    base = scaled_base + ctx.get("base_add", 0.0)
    base_after_flat = base
    reaction_type = ctx.get("reaction_type")
    reaction_bonus = ctx.get("reaction_bonus", 0.0)
    aggravate = 0.0
    if reaction_type in AGGRAVATE_FACTOR:
        em = ctx.get("em", 0.0)
        aggravate = (
            reaction_base_value(ctx["level"])
            * AGGRAVATE_FACTOR[reaction_type]
            * (1 + AGGRAVATE_EM_COEF * em / (em + AGGRAVATE_EM_DIVISOR) + reaction_bonus)
        )
        base += aggravate
    dmg_bonus = ctx.get("dmg_bonus", 0.0)
    res = resistance_multiplier(ctx.get("resistance", 0.0))
    defense = defense_multiplier(
        ctx["level"], ctx["enemy_level"], ctx.get("def_reduction", 0.0), ctx.get("def_ignore", 0.0)
    )
    amplify = 1.0
    if reaction_type == "amplify":
        amplify = ctx["amplify_factor"] * (1 + amplify_em_bonus(ctx.get("em", 0.0)) + reaction_bonus)
    no_crit = base * (1 + dmg_bonus) * res * defense * amplify
    crit_e = crit_expected_multiplier(ctx.get("crit_rate", 0.0), ctx.get("crit_dmg", 0.0))
    crit_n = 1 + ctx.get("crit_dmg", 0.0)
    zones = {
        "base": base, "base_mult": base_mult, "aggravate_flat": aggravate,
        "dmg_bonus": 1 + dmg_bonus, "resistance": res, "defense": defense,
        "amplify": amplify, "crit_expected": crit_e,
    }
    result = _result(no_crit * crit_e, no_crit * crit_n, zones)
    if explain:
        result["formula_explanation"] = {
            "kind": "classic", "version": 1,
            "expression": "((multiplier*scaling*base_mult+base_add)+aggravate_flat)*dmg_bonus*resistance*defense*amplify*crit_expected",
            "checkpoints": {"scaled_base": scaled_base, "base_after_flat": base_after_flat,
                "base_after_aggravate": base, "no_crit": no_crit,
                "def_reduction_effective": min(ctx.get("def_reduction", 0.0), DEF_REDUCTION_CAP),
                "def_ignore_effective": min(ctx.get("def_ignore", 0.0), DEF_IGNORE_CAP),
                "expected_per_count": result["expected"], "critical_per_count": result["crit"]},
            "zones": dict(zones),
        }
    return result


def transformative_damage(ctx: dict, *, explain: bool = False) -> dict:
    """剧变：(反应基础值×系数×(1+精通区+剧变增伤) + 剧变羽毛区) × 抗性。无防御/暴击/传统增伤。"""
    coef = ctx["system_factor"]
    em = ctx.get("em", 0.0)
    em_zone = 1 + transformative_em_bonus(em) + ctx.get("reaction_bonus", 0.0)
    base = reaction_base_value(ctx["level"]) * coef
    inner = base * em_zone + ctx.get("reaction_flat", 0.0)
    res = resistance_multiplier(ctx.get("resistance", 0.0))
    zones = {
        "reaction_base": base, "em_zone": em_zone,
        "reaction_flat": ctx.get("reaction_flat", 0.0), "resistance": res,
    }
    result = _result(inner * res, None, zones)
    if explain:
        result["formula_explanation"] = {
            "kind": "transformative", "version": 1,
            "expression": "(reaction_base*em_zone+reaction_flat)*resistance",
            "checkpoints": {"reaction_base": base, "after_flat": inner,
                "expected_per_count": result["expected"]}, "zones": dict(zones),
        }
    return result


def lunar_stellar_damage(ctx: dict, kind: str, *, explain: bool = False) -> dict:
    """月曜/星烁（kind ∈ lunar/stellar）：不吃传统增伤、无视防御、可暴击。

    (属性×倍率 或 反应基础值) × 基础系数 × (1+基础增伤) × (1+精通区+增伤) × 大权区
    + 羽毛区，然后 × 暴击 × 抗性 × (1+擢升)。羽毛区不吃反应伤害加成（在乘区外加算）。
    """
    coef = ctx["system_factor"]
    if ctx.get("reaction_base"):
        base_in = reaction_base_value(ctx["level"])
    else:
        base_in = ctx["multiplier"] * ctx["scaling"]
    base_bonus = 1 + ctx.get(f"{kind}_base", 0.0)
    em = ctx.get("em", 0.0)
    em_zone = 1 + lunar_em_bonus(em) + ctx.get("reaction_bonus", 0.0)
    big_mult = ctx.get(f"{kind}_mult", 1.0)
    flat = ctx.get("reaction_flat", 0.0)
    special_base = base_in * coef * base_bonus * em_zone * big_mult
    inner = special_base + flat
    res = resistance_multiplier(ctx.get("resistance", 0.0))
    elevate = 1 + ctx.get(f"{kind}_elevate", 0.0)
    no_crit = inner * res * elevate
    crit_e = crit_expected_multiplier(ctx.get("crit_rate", 0.0), ctx.get("crit_dmg", 0.0))
    crit_n = 1 + ctx.get("crit_dmg", 0.0)
    zones = {
        "base_in": base_in, "system_factor": coef, "base_bonus": base_bonus,
        "em_zone": em_zone, "big_mult": big_mult, "reaction_flat": flat,
        "resistance": res, "elevate": elevate, "crit_expected": crit_e,
    }
    result = _result(no_crit * crit_e, no_crit * crit_n, zones)
    if explain:
        result["formula_explanation"] = {
            "kind": kind, "version": 1,
            "expression": "(base_in*system_factor*base_bonus*em_zone*big_mult+reaction_flat)*resistance*elevate*crit_expected",
            "checkpoints": {"base_in": base_in, "before_reaction_flat": special_base,
                "after_reaction_flat": inner, "no_crit": no_crit,
                "expected_per_count": result["expected"], "critical_per_count": result["crit"]},
            "zones": dict(zones),
        }
    return result
