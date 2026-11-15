"""E4 端到端验收：examples/freeze_team.json 跑通 校验→补丁→计算→结果 全链路。

场景要点：班尼特前台开大（team/frontline Buff 生效）→ 切绫华 → 爆发快照
（cast_t=2.0 锁定短促增益，6 秒的绽放段仍享受 3 秒已结束的 Buff）→ 战技实时判定。
"""

import json
from pathlib import Path

import pytest

from genshin_engine import calculate, validate_template

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "freeze_team.json"


def _load() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_example_end_to_end():
    t = _load()
    validate_template(t)
    r = calculate(t)
    s = r["summary"]
    assert s["total_damage"] > 0 and s["dps"] == pytest.approx(s["total_damage"] / 20.0)
    assert set(s["by_character"]) == {"ayaka", "bennett"}
    assert len(r["hits"]) == 4

    # 班尼特 t=1.0 在前台：固定加攻 800 与宗室按基础攻击 600 加算 20%。
    bennett_hit = r["hits"][0]
    assert "bennett_q" in bennett_hit["buffs"]
    assert bennett_hit["zones"]["base"] == pytest.approx(2.33 * (800 + 600 * 0.2 + 800))

    # 快照：绽放 t=6.0 时 short_window（1.5-3.0）已结束，但快照于 cast_t=2.0 仍生效
    bloom = r["hits"][2]
    assert bloom["snapshot_at"] == 2.0
    assert "short_window" in bloom["buffs"]

    # 非快照战技 t=8.0：short_window 已结束，不在生效 Buff 中
    skill_hit = r["hits"][3]
    assert "short_window" not in skill_hit["buffs"]
    assert "bennett_q" in skill_hit["buffs"]  # 13 秒内仍生效

    # 快照 vs 实时差值可复核：切割基础区快照含全部 Buff
    cut = r["hits"][1]
    # 面板百分比按白值 900 加算，两份 20%；固定加攻进入有效攻击，再乘技能倍率。
    ayaka_atk = 2000 + 900 * (0.2 + 0.2) + 800
    assert cut["zones"]["base"] == pytest.approx(2.02 * ayaka_atk)
    expected_bennett = 2.33 * (800 + 600 * 0.2 + 800) * 0.45 * 1.025
    expected_burst = (2.02 * 19 + 3.03) * ayaka_atk * 1.666 * 1.8 * 0.45
    expected_skill = 3.79 * ayaka_atk * 1.466 * 1.8 * 0.45
    assert s["total_damage"] == pytest.approx(expected_bennett + expected_burst + expected_skill)
    assert r["assumptions"][-1] == t["meta"]["assumptions"][-1]

    # 覆盖率：short_window 1.5 秒 / 20 秒轴
    cov = {c["id"]: c["uptime"] for c in r["buffs_coverage"]}
    assert cov["short_window"] == pytest.approx(1.5 / 20.0)


def test_example_with_patch():
    t = _load()
    r = calculate(t, patches=[
        {"op": "replace", "path": "/characters/0/stats/atk", "value": 2200}
    ])
    cut = r["hits"][1]
    assert cut["zones"]["base"] == pytest.approx(2.02 * (2200 + 900 * 0.4 + 800))
    assert r["patches_applied"][0]["path"] == "/characters/0/stats/atk"
