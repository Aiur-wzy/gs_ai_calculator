"""Source-document correctness and discoverability, separate from task eval."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skill"
MAP = json.loads((ROOT / "docs/design/skill/context-architecture-v2-map.json").read_text(encoding="utf-8"))


def _text(relative):
    return (SKILL / relative).read_text(encoding="utf-8")


def _links(path):
    for match in re.finditer(r"(?<!!)\[[^\]]*\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
        target = match.group(1).split("#", 1)[0]
        if target and not re.match(r"\w+://", target):
            yield (path.parent / target).resolve()


def test_exact_migration_targets_no_python_prototype_in_production_bundle():
    actual = {p.relative_to(SKILL).as_posix() for p in SKILL.rglob("*.md")}
    # Preserve the r9 migration targets while allowing maintained new resources.
    current = json.loads((ROOT / "docs/design/skill/current-resources.json").read_text(encoding="utf-8"))
    assert set(MAP["proposed_skill_paths"]) <= actual
    assert actual == {row["resource"] for row in current["resources"]}
    assert current["resource_count"] == len(actual)
    assert not (SKILL / "references/native-agent.md").exists()
    assert (ROOT / "docs/archive/skill/native-agent-python-v1.md").is_file()


def test_report_window_delta_is_readable_through_the_production_skill_tool():
    from genshin_app.agent_chat.tools import _skill

    resource = "references/app-mechanics/review/report-window.md"
    response = _skill(None, {"resource": resource})
    assert response["text"] == _text(resource)
    assert response["next_offset"] is None
    for term in ("required_validation", "optional_audit", "0≤t≤T"):
        assert term in response["text"]


def test_every_current_resource_reachable_without_outside_root_dependencies():
    pending, visited = [(SKILL / "SKILL.md").resolve()], set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        for linked in _links(path):
            assert linked.is_relative_to(SKILL.resolve()), (path, linked)
            assert linked.is_file(), (path, linked)
            if linked.suffix == ".md":
                pending.append(linked)
    assert visited == {p.resolve() for p in SKILL.rglob("*.md")}


@pytest.mark.parametrize("page", ["damage-formula", "reactions", "rotation", "artifacts", "mechanics-glossary"])
def test_game_principles_have_no_executable_field_contracts(page):
    text = _text(f"references/mechanics/{page}.md")
    for technical in ("direct_stellar_damage", "direct_lunar_damage", "gs_read_", "`application_mode", "`icd.", "`schema_version", "data.dsh_context_receipt"):
        assert technical not in text, (page, technical)


def test_corrected_game_math_and_resonance_facts():
    artifacts = _text("references/mechanics/artifacts.md")
    assert "粉碎之冰" in artifacts and "蔓生之草" in artifacts
    assert "冰结之冰" not in artifacts and "繁茂之草" not in artifacts
    assert "体力消耗−15%、移动速度＋10%、技能冷却−5%" in artifacts
    assert "≈ 2.33%" in artifacts and "月笼" in artifacts
    formula = _text("references/mechanics/damage-formula.md")
    assert "min(1,max(0,暴击率))" in formula
    glossary = _text("references/mechanics/mechanics-glossary.md")
    assert "任一重置" not in glossary
    assert "增伤类 buff 全部加算在同一区" not in glossary
    assert "ER 是门槛不是收益" not in glossary


def test_current_input_contract_differences_are_not_erased():
    parameters = _text("references/app-mechanics/sources/hit-parameters.md")
    assert "并无统一C0/10级门" in parameters
    assert "level-bound-parameter-v1" in parameters
    attack = _text("references/app-mechanics/sources/attack-metadata.md")
    assert "二者范围不等价" in attack
    event = _text("references/app-mechanics/actions/event-profiles.md")
    assert "非负微秒偏移" in event and "零偏移" in event
    excel = _text("references/outputs/excel.md")
    assert "support.editable" in excel and "不能只凭没有limitation" in excel


def test_special_profiles_keep_both_evidence_paths_without_catalog_count_claims():
    text = _text("references/app-mechanics/elements/special-reactions.md")
    assert "assumed + authority=explicit_assumption" in text
    assert "confirmed" in text and "完整profile精确命中" in text
    assert "reviewed_profile_count" in text
    assert "reviewed profile数为0" not in text and "profiles: []" not in text
    assert "不能只改 `commit` 标签" in text
    assert "不进入多人反应贡献权重" in text


def test_no_claim_of_read_receipt_or_stopped_annotation_write():
    runtime = _text("references/runtime/native-agent.md")
    assert "不签发已读代际票" in runtime
    assert "当前生产不从启动环境读取DEEPSEEK_API_KEY" in _text("references/runtime/software-entrypoints.md")
    for path in SKILL.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert "POST /v1/annotations" not in text
        assert "data.dsh_context_receipt`记录" not in text


def test_workflow_links_all_current_focus_cards():
    from genshin_app.agent_chat.skill_context import FOCUS_RESOURCES

    workflow = SKILL / "references/workflow/index.md"
    linked = set(_links(workflow))
    assert {(SKILL / resource).resolve() for resource in FOCUS_RESOURCES.values()} <= linked
    entry = _text("SKILL.md")
    assert "角色附录按默认配方读取" in entry
    assert "其余只取当前决定需要的部分" in entry


def test_historical_manager_remains_cli_with_a_current_readable_entry():
    source = (ROOT / "src/genshin_app/agent_chat/manager.py").read_text(encoding="utf-8")
    assert "references/runtime/software-entrypoints.md" in source
    assert "references/native-agent.md" not in source
