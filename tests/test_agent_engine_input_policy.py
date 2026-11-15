from __future__ import annotations

import re
from itertools import pairwise
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _follow_skill_links(*relative_paths: str) -> str:
    """Check the actual on-demand path, not a duplicate of every policy card."""
    skill_root = (ROOT / "skill").resolve()
    for current, target in pairwise(relative_paths):
        source_path = (ROOT / current).resolve()
        target_path = (ROOT / target).resolve()
        assert source_path.is_relative_to(skill_root)
        assert target_path.is_relative_to(skill_root)
        links = {
            (source_path.parent / raw.split("#", 1)[0]).resolve()
            for raw in re.findall(r"\[[^\]]+\]\(([^)]+)\)", _read(current))
            if "://" not in raw and raw.split("#", 1)[0]
        }
        assert target_path in links, f"{current} no longer links to {target}"
    return _read(relative_paths[-1])


def test_database_assembly_and_agent_materialization_have_an_explicit_boundary() -> None:
    required_by_document = {
        "docs/decisions/0013-Agent监督自动化与受控场景覆盖.md": (
            "当前SQLite → App查询／Resolver／Composer → `resolved`输入",
            "不在计算过程中查询live SQLite",
            "角色／场景派生机制域必须保留强类型Agent输入或受控覆盖路径",
        ),
        "docs/design/engine.md": (
            "系统计算链会使用数据库",
            "不在计算过程中查询live SQLite",
            "agent-execution-materialization-v1",
            "engine_agent_input_gap",
        ),
        "docs/design/staged-agent-workflow.md": (
            "source_prefill",
            "agent_input=supported | partial | unsupported",
            "当前统一材料化、有限序列、周期v2有界动态生命周期",
        ),
    }

    for relative_path, required_phrases in required_by_document.items():
        text = _read(relative_path)
        for phrase in required_phrases:
            assert phrase in text, f"{relative_path} missing policy phrase: {phrase}"


def test_current_skill_links_preserve_the_input_boundary_without_repeating_long_cards() -> None:
    entry = "skill/references/workflow/index.md"
    assert "workflow=artifact" in _read(entry)
    database = _follow_skill_links(entry, "skill/references/workflow/focus/events.md",
                                   "skill/references/data/database-readme.md")
    for phrase in ("公共身份、角色基础面板", "装备成长／属性和精确等级参数",
                   "版本化、强类型、绑定当前方案的执行输入", "engine_agent_input_gap",
                   "不能成为已支持派生机制域的唯一入口"):
        assert phrase in database

    core = _follow_skill_links(entry, "skill/references/workflow/focus/loadout.md", "skill/references/app-mechanics/loadouts.md",
                               "skill/references/app-mechanics/resolved-template.md")
    for phrase in ("本文只描述resolved core入口", "不在计算过程中查询live SQLite",
                   "由App查询／Resolver／Composer在进入该入口前解析",
                   "原生方案的版本化强类型输入"):
        assert phrase in core

    materialization = _follow_skill_links(entry, "skill/references/workflow/focus/numbers.md",
                                          "skill/references/app-mechanics/execution/materialization.md")
    for phrase in ("自包含强类型输入", "family + handler_version",
                   "没有匹配的已发布材料化族时保持pending／unsupported",
                   "执行阶段不重新查询或解释数据库"):
        assert phrase in materialization


def test_current_skill_focus_is_guidance_while_real_input_boundaries_stay_enforced() -> None:
    entry = "skill/references/workflow/index.md"
    corrections = _follow_skill_links(entry, "skill/references/workflow/focus/closure.md", "skill/references/app-mechanics/review/corrections.md")
    assert "任意focus按需读取和调用" in corrections
    assert "schema、有限数值、引用、唯一所有权、防重复、用户授权和审计不能被覆盖" in corrections

    overrides = _follow_skill_links(entry, "skill/references/workflow/focus/closure.md", "skill/references/app-mechanics/review/overrides.md")
    assert "focus不限制读取或调用" in overrides
    assert "实际输入、授权和执行目标仍须满足合同" in overrides

    core = _follow_skill_links(entry, "skill/references/workflow/focus/loadout.md", "skill/references/app-mechanics/loadouts.md",
                               "skill/references/app-mechanics/resolved-template.md")
    assert "不要求focus回退" in core
    assert "不能直接修改总伤、DPS或显示结果" in core


def test_policy_reports_the_first_slice_without_claiming_full_gameplay_coverage() -> None:
    engine = _read("docs/design/engine.md")
    governance = _read("docs/design/agent-engine-override-governance.md")

    assert "目标不变量，不是对当前完成度的夸大" in engine
    assert "统一`agent-execution-materialization-v1`的首批发布闭环已经建立" in engine
    assert "它仍不是完整游戏机制全集" in engine
    assert "D1注册式机制族已完成从Agent自包含输入" in governance
    assert "周期v2的能力表已覆盖本轮有界生命周期子集" in governance


def test_database_numeric_sources_do_not_gain_derived_topology_authority() -> None:
    panel_basis = _read("docs/design/character-panel-basis.md")
    parameter_sources = _read("docs/design/action-parameter-sources.md")
    resolved_template = _read("skill/references/app-mechanics/resolved-template.md")

    assert "用户固定面板保持" in panel_basis
    assert "已保存的数值快照仍是计算依据" in panel_basis
    assert "本批不创建新动作或命中" in parameter_sources
    assert "不改变时点、次数、元素、GU和共享ICD" in parameter_sources
    assert "参数来源闭合不等于整个技能已经自动执行" in parameter_sources
    assert "本文只描述resolved core入口" in resolved_template
    assert "由App查询／Resolver／Composer在进入该入口前解析" in resolved_template
