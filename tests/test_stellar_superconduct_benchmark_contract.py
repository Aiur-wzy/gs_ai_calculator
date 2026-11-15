from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "benchmarks" / "followup" / "stellar-superconduct-01"
HISTORICAL_RUN = BENCHMARK / "runs" / "sol56-high-two-round-r1"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_revision_2_preserves_the_clarified_build_and_energy_contract() -> None:
    contract = _read_json(BENCHMARK / "benchmark-contract-v2.json")
    artifacts = contract["artifact_contract"]

    assert contract["contract_revision"] == 2
    assert contract["status"] == "authoritative_for_next_run"
    assert artifacts["aroha_main_stats"] == [
        "energy_recharge",
        "electro_dmg_bonus",
        "crit_rate",
    ]
    assert artifacts["aroha_energy_recharge_sands_value"] == 0.518
    assert artifacts["sucrose_main_stats"] == [
        "elemental_mastery",
        "elemental_mastery",
        "elemental_mastery",
    ]
    assert artifacts["main_stats_by_character"] == {
        "桑多涅": ["atk_pct", "atk_pct", "crit_dmg"],
        "奥黛塔": ["atk_pct", "atk_pct", "crit_dmg"],
        "砂糖": [
            "elemental_mastery",
            "elemental_mastery",
            "elemental_mastery",
        ],
        "阿罗夏": ["energy_recharge", "electro_dmg_bonus", "crit_rate"],
    }
    assert "other_main_stats" not in artifacts
    assert artifacts["effective_substat_roll_budgets"] == {
        "桑多涅": 38,
        "奥黛塔": 35,
        "砂糖": 35,
        "阿罗夏": 35,
    }
    assert artifacts["sandrone_constraints"] == {
        "effective_substat_roll_budget": 38,
        "crit_rate_plus_crit_dmg_roll_cap": 30,
        "remaining_rolls": "用于充能效率或其他有效属性；若循环需要充能，先满足循环并等量占用38词预算。",
    }

    allocation = artifacts["allocation_policy"]
    assert allocation["scope"] == "all_characters_required_to_burst_in_round_2"
    assert allocation["required_round_2_burst_characters"] == ["阿罗夏", "桑多涅"]
    assert artifacts["average_roll_values"]["energy_recharge"] == 0.055
    assert "insufficient_energy" in allocation["failure_policy"]
    assert "返回numeric_model" in allocation["failure_policy"]
    assert "有效词条预算内仍无解" in allocation["failure_policy"]


def test_revision_2_has_an_app_native_agent_prompt_with_result_isolation() -> None:
    contract = _read_json(BENCHMARK / "benchmark-contract-v2.json")
    prompt_path = BENCHMARK / contract["native_agent_prompt"]
    prompt = prompt_path.read_text(encoding="utf-8")

    assert prompt_path.is_file()
    assert contract["visibility"] == "host_only_not_agent_input"
    assert contract["subject_safe_input"] == prompt_path.name
    assert contract["native_agent_execution"] == {
        "connection_kind": "native_api",
        "runtime_generation": "dsh_v2",
        "workflow": "staged",
        "energy_intent": "multi_round_legal",
        "workflow_stages": [
            "loadout_draft",
            "action_checkpoint",
            "event_topology",
            "numeric_model",
            "calculation_closure",
        ],
        "app_workspace_only": True,
        "arbitrary_filesystem_required": False,
        "save_and_export_via_user_completed_operation_card": True,
    }

    isolation = contract["research_and_isolation_policy"]
    assert isolation["web_search_and_fetch_allowed"] is True
    assert isolation["public_mechanics_rotations_tables_and_answers_allowed"] is True
    assert isolation["policy"] == "prior_project_run_isolation_not_answer_isolation"
    assert isolation["accidental_project_result_search_hit"] == (
        "reject_without_fetch_or_use"
    )

    for stage in contract["native_agent_execution"]["workflow_stages"]:
        assert f"`{stage}`" in prompt
    assert "`web_search`、`web_fetch`" in prompt
    assert "往期项目结果隔离" in prompt
    assert "不是“答案隔离”" in prompt
    assert "runs/**" in prompt
    assert "不要尝试读取仓库路径" in prompt
    assert "用户完成文件界面" in prompt
    assert "两轮共 18 条" in prompt
    assert "充能／雷伤／暴率" in prompt
    assert "精通／精通／精通" in prompt
    assert "暴击率词条与暴击伤害词条合计不得超过 30" in prompt
    assert "`zero_hitlag_reference`" in prompt
    assert "3 次 `LoopAttack`" in prompt
    assert "`direct_stellar_damage`" in prompt
    assert "本轮禁止使用 `static_layer_bridge`" in prompt
    assert "共 30 个固定语义动作" in prompt
    assert "C1 提供的两次可用次数" in prompt
    assert "不存在影响总量的 `pending`" in prompt
    assert "sol56-high-two-round-r1" not in prompt
    assert "6168524" not in prompt
    assert "`connection_kind=native_api`" in prompt
    assert "`workflow=staged`" in prompt
    assert "`energy_intent=multi_round_legal`" in prompt
    assert "`calculation_acceptance.status=accepted`" in prompt
    assert "留在或回到 `numeric_model`" in prompt
    assert "先回到配装阶段重分配" not in prompt
    assert "此阶段不写执行绑定" in prompt
    assert "此阶段不要求读取技能、天赋、命座机制或 appendix 正文" in prompt
    assert "正式两轮动作/能量合法性只在下一阶段核验" in prompt
    assert "两轮 `multi_round_legal` 接纳" in prompt
    assert "只有当前工作区提供相应动作时才请求保存/导出操作卡" in prompt

    assert contract["special_runtime_policy"] == {
        "stellar_superconduct_direct_damage_event": "direct_stellar_damage",
        "ability_binding_version": 2,
        "caller_may_submit_layers_or_system_factor": False,
        "static_layer_bridge_allowed_for_new_run": False,
        "reviewed_profile_absence_status": (
            "explicit_assumption_or_pending_not_confirmed"
        ),
        "reaction_stellar_swirl_runtime": "existing_stellar_swirl_state_machine",
    }


def test_historical_run_remains_frozen_and_is_explicitly_superseded() -> None:
    contract = _read_json(BENCHMARK / "benchmark-contract-v2.json")
    panels = _read_json(HISTORICAL_RUN / "public" / "fixed-panels.json")
    revision_2_errata = _read_json(BENCHMARK / "revision-2-errata.json")
    validation = _read_json(HISTORICAL_RUN / "host" / "validation-final.json")

    # The old run stays reproducible instead of being silently rewritten.
    assert panels["characters"]["aroha"]["main_stats"][0] == "攻击力"
    assert panels["characters"]["sucrose"]["main_stats"] == [
        "元素精通",
        "元素精通",
        "元素精通",
    ]
    assert panels["characters"]["sandrone"]["substat_rolls"] == {
        "暴击率": 18,
        "暴击伤害": 17,
    }
    assert revision_2_errata["historical_run_remains_immutable"] is True
    assert {
        item["json_pointer"] for item in revision_2_errata["corrections"]
    } >= {
        "/characters/aroha/main_stats/0",
        "/characters/sucrose/main_stats",
        "/characters/sandrone/substat_rolls",
        "/fixed_inputs/rule",
    }

    frozen_files = {
        "public.case.json": HISTORICAL_RUN / "public" / "case.json",
        "public.fixed_panels.json": HISTORICAL_RUN / "public" / "fixed-panels.json",
        "host.input_errata.json": HISTORICAL_RUN / "host" / "input-errata.json",
    }
    checks = {check["id"]: check for check in validation["checks"]}
    for check_id, path in frozen_files.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == (
            checks[check_id]["evidence"]["sha256"]
        )

    invalid = contract["historical_run_policy"]["invalid_for_revision_2"]
    assert "桑多涅38词与双暴不超过30词" in invalid
    assert "基于旧面板的伤害与DPS" in invalid
