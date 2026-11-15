"""引擎常量：系数与等级基数的唯一集中处。

数值来源：skill/references/mechanics/damage-formula.md（用户校订版）。
"""

# 反应基础值（只与角色等级有关）
REACTION_BASE_VALUE = {70: 765.64, 80: 1077.44, 90: 1446.85, 95: 1561.46, 100: 1674.81}

# 精通区系数
AMPLIFY_EM_COEF = 2.78        # 增幅：2.78*EM/(EM+1400)
AMPLIFY_EM_DIVISOR = 1400.0
TRANSFORMATIVE_EM_COEF = 16.0  # 剧变：16*EM/(EM+2000)
TRANSFORMATIVE_EM_DIVISOR = 2000.0
LUNAR_EM_COEF = 6.0            # 月曜/星烁：6*EM/(EM+2000)
LUNAR_EM_DIVISOR = 2000.0
AGGRAVATE_EM_COEF = 5.0        # 激化固定增伤：5*EM/(EM+1200)
AGGRAVATE_EM_DIVISOR = 1200.0

AGGRAVATE_FACTOR = {"aggravate": 1.15, "spread": 1.25}  # 超激化/蔓激化

# 剧变系数（V5.2 后）
TRANSFORMATIVE_FACTOR = {
    "burning": 0.25,
    "swirl": 0.6,
    "superconduct": 1.5,
    "electro_charged": 2.0,
    "bloom": 2.0,
    "overload": 2.75,
    "shattered": 3.0,
    "burgeon": 3.0,
    "hyperbloom": 3.0,
}

# 月曜基础系数
LUNAR_FACTOR = {"lunar_bloom": 1.0, "lunar_electro": 3.0, "lunar_crystallize": 1.6}

# 星烁基础系数（星超导走 stellar_superconduct_factor 按层数计算）
STELLAR_FACTOR = {
    "stellar_swirl": 1.0,
    "stellar_swirl_anemo": 0.75,
    "stellar_swirl_cryo_lv2": 2.0,
    "stellar_swirl_cryo_lv3": 3.0,
}

DEF_REDUCTION_CAP = 0.9   # 减防加算上限
DEF_IGNORE_CAP = 1.0      # 无视防御加算上限
RES_HIGH_THRESHOLD = 0.75  # 抗性分段点
CRIT_RATE_CAP = 1.0

DAMAGE_SYSTEMS = ("classic", "transformative", "lunar", "stellar")
ELEMENTS = ("physical", "pyro", "hydro", "electro", "cryo", "anemo", "geo", "dendro")
SCALING_STATS = ("atk", "hp", "def", "em")

# Buff effect 的 zone 枚举（engine.md §4）
ADDITIVE_ZONES = (
    "base_add", "stat_add", "stat_pct", "dmg_bonus", "crit_rate", "crit_dmg", "em",
    "reaction_bonus", "reaction_flat",
    "lunar_base", "lunar_elevate", "stellar_base", "stellar_elevate",
    "def_reduction", "def_ignore", "res_shred",
)
MULTIPLICATIVE_ZONES = ("base_mult", "lunar_mult", "stellar_mult")
BUFF_ZONES = ADDITIVE_ZONES + MULTIPLICATIVE_ZONES
# 敌方减益：作用于敌人，不判 scope/target
ENEMY_ZONES = ("def_reduction", "def_ignore", "res_shred")

BUFF_SCOPES = ("team", "frontline")


def stellar_superconduct_factor(layers: int) -> float:
    """直伤星超导基础系数：0 层=1；1-12 层=0.05×层数+1.4；>12 层=2。"""
    if layers <= 0:
        return 1.0
    if layers <= 12:
        return 0.05 * layers + 1.4
    return 2.0
