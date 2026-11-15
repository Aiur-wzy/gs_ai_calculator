"""E1 计算规则库测试：对拍 mechanics/damage-formula.md（用户校订版）数值。"""

import pytest

from genshin_engine.constants import stellar_superconduct_factor
from genshin_engine.formulas import (
    amplify_em_bonus,
    classic_damage,
    crit_expected_multiplier,
    defense_multiplier,
    lunar_em_bonus,
    lunar_stellar_damage,
    reaction_base_value,
    resistance_multiplier,
    transformative_damage,
    transformative_em_bonus,
)


def test_defense_equal_level_is_half():
    assert defense_multiplier(90, 90) == pytest.approx(0.5)


def test_defense_reduction_capped_at_90pct():
    # 减防加算封顶 90%：190 / (190 + 190×0.1)
    assert defense_multiplier(90, 90, def_reduction=1.5) == pytest.approx(190 / 209)


def test_defense_ignore_capped_at_100pct():
    assert defense_multiplier(90, 90, def_ignore=1.5) == pytest.approx(1.0)


def test_resistance_three_segments():
    assert resistance_multiplier(-0.2) == pytest.approx(1.1)      # 负抗减半收益
    assert resistance_multiplier(0.1) == pytest.approx(0.9)       # 常规段
    assert resistance_multiplier(0.75) == pytest.approx(0.25)     # 高抗段起点
    assert resistance_multiplier(0.8) == pytest.approx(1 / 4.2)   # 高抗段


def test_crit_rate_capped():
    assert crit_expected_multiplier(1.5, 2.0) == pytest.approx(3.0)


def test_em_zone_coefficients():
    assert amplify_em_bonus(1400) == pytest.approx(1.39)          # 2.78×1400/2800
    assert transformative_em_bonus(2000) == pytest.approx(8.0)    # 16×2000/4000
    assert lunar_em_bonus(2000) == pytest.approx(3.0)             # 6×2000/4000


def test_reaction_base_value_table():
    assert reaction_base_value(90) == pytest.approx(1446.85)
    assert reaction_base_value(100) == pytest.approx(1674.81)
    with pytest.raises(KeyError):
        reaction_base_value(91)


def test_stellar_superconduct_layers():
    assert stellar_superconduct_factor(0) == pytest.approx(1.0)
    assert stellar_superconduct_factor(5) == pytest.approx(1.65)
    assert stellar_superconduct_factor(12) == pytest.approx(2.0)
    assert stellar_superconduct_factor(13) == pytest.approx(2.0)


def _classic_ctx(**over):
    ctx = {
        "multiplier": 2.0, "scaling": 2000.0, "level": 90, "enemy_level": 90,
        "resistance": 0.1, "crit_rate": 0.4, "crit_dmg": 2.16,
        "dmg_bonus": 0.0, "em": 0.0,
    }
    ctx.update(over)
    return ctx


def test_classic_basic():
    out = classic_damage(_classic_ctx())
    # base=4000；no_crit = 4000×0.9(抗)×0.5(防) = 1800；期望=1800×(1+0.4×2.16)
    assert out["expected"] == pytest.approx(1800 * 1.864)
    assert out["crit"] == pytest.approx(1800 * 3.16)


def test_classic_amplify_vaporize():
    out = classic_damage(_classic_ctx(reaction_type="amplify", amplify_factor=2.0))
    assert out["expected"] == pytest.approx(1800 * 2.0 * 1.864)


def test_classic_aggravate_flat():
    out = classic_damage(_classic_ctx(reaction_type="aggravate", em=0.0))
    # 激化固定增伤 = 1446.85×1.15×(1+0) 加入基础区
    base = 4000 + 1446.85 * 1.15
    assert out["zones"]["base"] == pytest.approx(base)
    assert out["expected"] == pytest.approx(base * 0.45 * 1.864)


def test_classic_feather_and_big_mult():
    out = classic_damage(_classic_ctx(base_add=800.0, base_mult=2.0))
    # base = 2×2000×2 + 800 = 8800
    assert out["zones"]["base"] == pytest.approx(8800.0)


def test_transformative_overload():
    out = transformative_damage({"system_factor": 2.75, "level": 90, "em": 0.0, "resistance": 0.1})
    # 1446.85×2.75×1 × 0.9（抗性），无防御无暴击
    assert out["expected"] == pytest.approx(1446.85 * 2.75 * 0.9)
    assert out["crit"] is None


def test_transformative_em_scaling():
    out = transformative_damage({"system_factor": 2.0, "level": 90, "em": 2000.0, "resistance": 0.0})
    assert out["expected"] == pytest.approx(1446.85 * 2.0 * (1 + 8.0) * 1.0)


def test_lunar_direct_critable_no_defense_no_traditional_bonus():
    ctx = {
        "multiplier": 2.0, "scaling": 2000.0, "system_factor": 3.0,
        "level": 90, "enemy_level": 90, "resistance": 0.1,
        "crit_rate": 1.0, "crit_dmg": 1.0, "em": 0.0,
        "dmg_bonus": 0.5, "def_reduction": 0.0,  # 这两个必须被忽略
    }
    out = lunar_stellar_damage(ctx, "lunar")
    # inner = 2×2000×3 = 12000；no_crit = 12000×0.9；可暴击 crit_e = 2
    assert out["expected"] == pytest.approx(12000 * 0.9 * 2.0)
    assert out["crit"] == pytest.approx(12000 * 0.9 * 2.0)
    assert "defense" not in out["zones"] and "dmg_bonus" not in out["zones"]


def test_lunar_reaction_base_and_flat_outside_em_zone():
    ctx = {
        "reaction_base": True, "system_factor": 3.0, "level": 90,
        "resistance": 0.0, "crit_rate": 0.0, "crit_dmg": 0.0,
        "em": 2000.0, "reaction_bonus": 0.0, "reaction_flat": 500.0,
    }
    out = lunar_stellar_damage(ctx, "lunar")
    # inner = 1446.85×3×(1+3) + 500（羽毛区不吃精通/增伤）
    assert out["expected"] == pytest.approx(1446.85 * 3 * 4 + 500)
