"""E3 时间轴求值核心测试：engine.md §9 一期特性项验收 + 汇总输出。"""

import copy

import pytest

from genshin_engine import calculate
from genshin_engine.template import PatchError, TemplateError


def make_template() -> dict:
    """双角色基线：A 攻击 2000，B 攻击 1000；敌人 90 级 10% 抗性。"""
    return {
        "version": 1,
        "form": "resolved",
        "meta": {"name": "特性项测试"},
        "enemy": {"level": 90, "resistance": {"default": 0.1}},
        "characters": [
            {"id": "a", "level": 90, "stats": {"atk": 2000, "crit_rate": 0.0, "crit_dmg": 0.0}},
            {"id": "b", "level": 90, "stats": {"atk": 1000, "crit_rate": 0.0, "crit_dmg": 0.0}},
        ],
        "switches": [{"t": 0, "character": "a"}],
        "buffs": [],
        "hits": [],
        "loop_duration": 20.0,
    }


def hit(t: float, char: str, mult: float = 1.0, **kw) -> dict:
    h = {"t": t, "character": char, "skill": "s", "hit_name": "h",
         "multiplier": mult, "damage_system": "classic", "element": "pyro"}
    h.update(kw)
    return h


def buff(bid: str, effects: list, scope: str = "team", start: float = 0.0,
         end: float = 10.0, target: str | None = None) -> dict:
    b = {"id": bid, "source": "测试", "scope": scope, "start": start, "end": end,
         "effects": effects}
    if target:
        b["target"] = target
    return b


# 无 buff 基线：mult1 × atk × 抗0.9 × 防0.5
BASE_A = 2000 * 0.9 * 0.5
BASE_B = 1000 * 0.9 * 0.5


def dmg(result: dict, i: int = 0) -> float:
    return result["hits"][i]["expected"]


def test_result_weight_scales_one_projected_component_and_summary():
    """Weighted native reaction contributors keep a real per-component row."""
    t = make_template()
    t["hits"] = [hit(1.0, "a", result_weight=0.3)]
    result = calculate(t)
    assert dmg(result) == pytest.approx(BASE_A * 0.3)
    assert result["hits"][0]["result_weight"] == pytest.approx(0.3)
    assert result["summary"]["total_damage"] == pytest.approx(BASE_A * 0.3)


class TestBuffScope:
    def test_team_buff_affects_all(self):
        t = make_template()
        t["buffs"] = [buff("bf", [{"zone": "dmg_bonus", "stat": "all", "value": 0.5}])]
        t["hits"] = [hit(1.0, "a"), hit(2.0, "b")]
        r = calculate(t)
        assert dmg(r, 0) == pytest.approx(BASE_A * 1.5)
        assert dmg(r, 1) == pytest.approx(BASE_B * 1.5)

    def test_frontline_buff_only_active_character(self):
        t = make_template()
        t["switches"] = [{"t": 0, "character": "a"}, {"t": 5, "character": "b"}]
        t["buffs"] = [buff("bf", [{"zone": "dmg_bonus", "stat": "all", "value": 0.5}],
                           scope="frontline")]
        t["hits"] = [hit(1.0, "a"), hit(6.0, "a"), hit(6.0, "b")]
        r = calculate(t)
        assert dmg(r, 0) == pytest.approx(BASE_A * 1.5)  # 前台吃
        assert dmg(r, 1) == pytest.approx(BASE_A)        # 切人后 a 在后台不吃
        assert dmg(r, 2) == pytest.approx(BASE_B * 1.5)  # 新前台 b 吃

    def test_target_restriction(self):
        t = make_template()
        t["buffs"] = [buff("bf", [{"zone": "dmg_bonus", "stat": "all", "value": 0.5}],
                           target="b")]
        t["hits"] = [hit(1.0, "a"), hit(1.0, "b")]
        r = calculate(t)
        assert dmg(r, 0) == pytest.approx(BASE_A)        # 非 target 不吃
        assert dmg(r, 1) == pytest.approx(BASE_B * 1.5)  # target 吃

    def test_dmg_bonus_element_matching(self):
        t = make_template()
        t["buffs"] = [buff("bf", [{"zone": "dmg_bonus", "stat": "cryo", "value": 0.5}])]
        t["hits"] = [hit(1.0, "a", element="pyro"), hit(1.0, "a", element="cryo")]
        r = calculate(t)
        assert dmg(r, 0) == pytest.approx(BASE_A)        # 元素不匹配
        assert dmg(r, 1) == pytest.approx(BASE_A * 1.5)


class TestSnapshot:
    def test_snapshot_locks_buffs_at_cast(self):
        t = make_template()
        t["buffs"] = [buff("bf", [{"zone": "base_add", "stat": "all", "value": 800}],
                           start=0.0, end=2.0)]
        t["hits"] = [
            hit(5.0, "a", snapshot=True, cast_t=1.0),   # Buff 已结束但快照锁定
            hit(5.0, "a"),                               # 非快照对照
        ]
        r = calculate(t)
        assert dmg(r, 0) == pytest.approx((2000 + 800) * 0.9 * 0.5)
        assert dmg(r, 1) == pytest.approx(BASE_A)
        assert r["hits"][0]["snapshot_at"] == 1.0

    def test_snapshot_defaults_cast_t_to_hit_t(self):
        t = make_template()
        t["buffs"] = [buff("bf", [{"zone": "base_add", "stat": "all", "value": 800}])]
        t["hits"] = [hit(1.0, "a", snapshot=True)]
        r = calculate(t)
        assert dmg(r, 0) == pytest.approx((2000 + 800) * 0.9 * 0.5)
        assert r["hits"][0]["snapshot_at"] == 1.0


class TestEnemyDebuffZones:
    def test_enemy_zones_ignore_scope(self):
        """敌方减益不判 scope：frontline 范围的减防对后台角色同样生效。"""
        t = make_template()
        t["switches"] = [{"t": 0, "character": "a"}, {"t": 5, "character": "b"}]
        t["buffs"] = [buff("bf", [{"zone": "def_reduction", "stat": "all", "value": 0.3}],
                           scope="frontline")]
        t["hits"] = [hit(6.0, "a")]  # a 在后台
        r = calculate(t)
        # 防御系数 = 190/(190+190×0.7)
        defense = 190 / (190 + 190 * 0.7)
        assert dmg(r, 0) == pytest.approx(2000 * 0.9 * defense)

    def test_defense_reduction_capped(self):
        t = make_template()
        t["buffs"] = [buff("bf", [{"zone": "def_reduction", "stat": "all", "value": 0.6}])]
        t["enemy"]["defense_reduction"] = 0.6  # 与 buff 加算 1.2 → 封顶 0.9
        t["hits"] = [hit(1.0, "a")]
        r = calculate(t)
        defense = 190 / (190 + 190 * 0.1)
        assert r["hits"][0]["zones"]["defense"] == pytest.approx(defense)

    def test_res_shred_element_matching(self):
        t = make_template()
        t["buffs"] = [buff("bf", [{"zone": "res_shred", "stat": "cryo", "value": 0.2}])]
        t["hits"] = [hit(1.0, "a", element="pyro"), hit(1.0, "a", element="cryo")]
        r = calculate(t)
        assert dmg(r, 0) == pytest.approx(BASE_A)                    # 元素不匹配
        assert dmg(r, 1) == pytest.approx(2000 * 1.05 * 0.5)         # 抗性 0.1-0.2=-0.1 → 1-(-0.1)/2=1.05


class TestDamageSystems:
    def test_four_systems_in_one_template(self):
        t = make_template()
        t["hits"] = [
            hit(1.0, "a", mult=2.0),  # classic: 2×2000×0.9×0.5
            hit(2.0, "a", mult=None, damage_system="transformative",
                reaction={"type": "overload"}),
            hit(3.0, "a", mult=2.0, damage_system="lunar", element="electro",
                reaction={"type": "lunar_electro"}),
            hit(4.0, "a", mult=2.0, damage_system="stellar", element="cryo",
                reaction={"type": "stellar_superconduct", "layers": 5}),
        ]
        t["hits"][1].pop("multiplier")
        r = calculate(t)
        assert dmg(r, 0) == pytest.approx(2 * 2000 * 0.9 * 0.5)
        assert dmg(r, 1) == pytest.approx(1446.85 * 2.75 * 0.9)          # 剧变超载
        assert dmg(r, 2) == pytest.approx(2 * 2000 * 3 * 0.9)            # 月感电×3，无防御
        assert dmg(r, 3) == pytest.approx(2 * 2000 * 1.65 * 0.9)         # 星超导 5 层


class TestAggregation:
    def test_count_dps_coverage_and_by_skill(self):
        t = make_template()
        t["buffs"] = [buff("bf", [{"zone": "dmg_bonus", "stat": "all", "value": 0.5}],
                           start=0.0, end=5.0)]
        t["hits"] = [
            hit(1.0, "a", skill="burst", count=19),
            hit(2.0, "a", skill="skill"),
        ]
        r = calculate(t)
        assert dmg(r, 0) == pytest.approx(BASE_A * 1.5 * 19)
        assert r["summary"]["total_damage"] == pytest.approx(BASE_A * 1.5 * 20)
        assert r["summary"]["dps"] == pytest.approx(BASE_A * 1.5 * 20 / 20.0)
        assert r["summary"]["by_skill"]["burst"] == pytest.approx(BASE_A * 1.5 * 19)
        assert r["buffs_coverage"][0]["uptime"] == pytest.approx(5.0 / 20.0)

    def test_patches_applied_echo_and_effect(self):
        t = make_template()
        t["hits"] = [hit(1.0, "a")]
        patches = [{"op": "replace", "path": "/characters/0/stats/atk", "value": 3000}]
        r = calculate(t, patches=patches)
        assert dmg(r) == pytest.approx(3000 * 0.9 * 0.5)
        assert r["patches_applied"] == patches

    def test_invalid_inputs_raise(self):
        t = make_template()
        t["hits"] = [hit(1.0, "a")]
        with pytest.raises(PatchError):
            calculate(t, patches=[{"op": "remove", "path": "/characters"}])
        bad = copy.deepcopy(t)
        bad["form"] = "unresolved"
        with pytest.raises(TemplateError):
            calculate(bad)


class TestEffectiveStats:
    def test_em_scaling_uses_buff_at_hit_and_snapshot_time(self):
        t = make_template()
        t["characters"][0]["stats"]["em"] = 100
        t["buffs"] = [buff("em", [{"zone": "em", "stat": "all", "value": 100}], end=2)]
        t["hits"] = [
            hit(1, "a", mult=2, scaling_stat="em"),
            hit(5, "a", mult=2, scaling_stat="em", snapshot=True, cast_t=1),
            hit(5, "a", mult=2, scaling_stat="em"),
        ]
        r = calculate(t)
        assert [h["expected"] for h in r["hits"]] == pytest.approx([180, 180, 90])

    def test_em_scaling_and_reaction_bonus_use_the_same_effective_em(self):
        t = make_template()
        t["characters"][0]["stats"]["em"] = 100
        t["buffs"] = [buff("em", [{"zone": "em", "stat": "all", "value": 100}])]
        t["hits"] = [hit(1, "a", mult=2, scaling_stat="em", damage_system="lunar",
                         reaction={"type": "lunar_electro"})]
        expected = 2 * 200 * 3 * (1 + 6 * 200 / 2200) * 0.9
        assert dmg(calculate(t)) == pytest.approx(expected)

    def test_em_buff_respects_frontline_and_target(self):
        t = make_template()
        for char in t["characters"]:
            char["stats"]["em"] = 100
        t["switches"] = [{"t": 0, "character": "a"}, {"t": 2, "character": "b"}]
        t["buffs"] = [buff("em", [{"zone": "em", "stat": "all", "value": 100}],
                           scope="frontline", target="a")]
        t["hits"] = [hit(1, "a", scaling_stat="em"), hit(3, "a", scaling_stat="em"),
                     hit(3, "b", scaling_stat="em")]
        assert [h["expected"] for h in calculate(t)["hits"]] == pytest.approx([90, 45, 45])

    def test_percentages_add_on_base_stat_and_flat_values_add_on_final_stat(self):
        t = make_template()
        t["characters"][0]["stats"]["base_atk"] = 1000
        t["buffs"] = [
            buff("pct1", [{"zone": "stat_pct", "stat": "atk", "value": 0.2}]),
            buff("pct2", [{"zone": "stat_pct", "stat": "atk", "value": 0.3}]),
            buff("flat", [{"zone": "stat_add", "stat": "atk", "value": 800}]),
        ]
        t["hits"] = [hit(1, "a", mult=2)]
        r = calculate(t)
        assert r["hits"][0]["zones"]["base"] == pytest.approx(2 * (2000 + 1000 * 0.5 + 800))
        assert dmg(r) == pytest.approx(2970)

    def test_dynamic_stats_respect_frontline_target_and_snapshot(self):
        t = make_template()
        for char in t["characters"]:
            char["stats"]["base_atk"] = 1000
        t["switches"] = [{"t": 0, "character": "a"}, {"t": 3, "character": "b"}]
        t["buffs"] = [buff("panel", [
            {"zone": "stat_pct", "stat": "atk", "value": 0.2},
            {"zone": "stat_add", "stat": "atk", "value": 800},
        ], scope="frontline", target="a", end=2)]
        t["hits"] = [
            hit(1, "a"), hit(1, "b"),
            hit(5, "a", snapshot=True, cast_t=1), hit(5, "a"), hit(5, "b"),
        ]
        assert [h["expected"] for h in calculate(t)["hits"]] == pytest.approx(
            [1350, 450, 1350, 900, 450])

    @pytest.mark.parametrize("stat,starting,base_value", [("hp", 20000, 10000), ("def", 2000, 1000)])
    def test_hp_def_scaling_only_uses_its_own_stat_buffs(self, stat, starting, base_value):
        t = make_template()
        t["characters"][0]["stats"].update({stat: starting, f"base_{stat}": base_value})
        t["buffs"] = [buff("panel", [
            {"zone": "stat_pct", "stat": "atk", "value": 0.5},
            {"zone": "stat_add", "stat": "atk", "value": 800},
            {"zone": "stat_pct", "stat": stat, "value": 0.2},
            {"zone": "stat_add", "stat": stat, "value": 100},
        ])]
        t["hits"] = [hit(1, "a", scaling_stat=stat)]
        assert dmg(calculate(t)) == pytest.approx((starting + base_value * 0.2 + 100) * 0.45)

    def test_missing_base_is_rejected_only_when_percent_buff_affects_hit(self):
        t = make_template()
        t["buffs"] = [buff("pct", [{"zone": "stat_pct", "stat": "atk", "value": 0.2}],
                           target="b", start=2, end=4)]
        t["hits"] = [hit(3, "a"), hit(1, "b"),
                     hit(3, "b", damage_system="transformative", reaction={"type": "overload"}),
                     hit(3, "b", damage_system="lunar", reaction={"type": "lunar_electro"},
                         reaction_base=True)]
        assert calculate(t)["summary"]["total_damage"] > 0
        t["hits"].append(hit(3, "b"))
        with pytest.raises(TemplateError) as error:
            calculate(t)
        assert "hits[4]" in str(error.value) and "stats.base_atk" in str(error.value)

    def test_flat_stat_buff_does_not_require_base_stat(self):
        t = make_template()
        t["buffs"] = [buff("atk", [{"zone": "stat_add", "stat": "atk", "value": 800}])]
        t["hits"] = [hit(1, "a", mult=2)]
        assert dmg(calculate(t)) == pytest.approx(2 * 2800 * 0.45)

    def test_negative_effective_em_and_negative_damage_are_reported(self):
        t = make_template()
        t["hits"] = [hit(1, "a")]
        t["buffs"] = [buff("em", [{"zone": "em", "stat": "all", "value": -100}])]
        with pytest.raises(TemplateError) as error:
            calculate(t)
        assert "hits[0].resolved.em" in str(error.value)
        t["buffs"] = [buff("negative", [{"zone": "dmg_bonus", "stat": "all", "value": -2}])]
        with pytest.raises(TemplateError, match="负伤害"):
            calculate(t)


class TestUserAssumptions:
    def test_optional_meta_assumptions_append_to_fixed_statements_without_mutation(self):
        t = make_template()
        t["hits"] = [hit(1, "a")]
        defaults = calculate(t)["assumptions"]
        user_notes = ["所有命中均命中单目标", "不计怪物移动"]
        t["meta"]["assumptions"] = user_notes
        result = calculate(t)
        assert result["assumptions"] == defaults + user_notes
        result["assumptions"].append("仅改结果")
        assert t["meta"]["assumptions"] == ["所有命中均命中单目标", "不计怪物移动"]

    def test_patch_can_change_assumptions_and_result_echoes_both(self):
        t = make_template()
        t["hits"] = [hit(1, "a")]
        ops = [{"op": "add", "path": "/meta/assumptions", "value": ["只计算一轮"]}]
        r = calculate(t, ops)
        assert r["assumptions"][-1] == "只计算一轮"
        assert r["patches_applied"] == ops
        assert "assumptions" not in t["meta"]
