"""Non-executable repair drafts for legacy constant-only conversion inputs.

This is deliberately before Composer admission: the unsupported input cannot
first enter a valid CandidateSession or a source preview. It neither loads a
database nor adopts a classification, basis, component identity or result.
"""
from __future__ import annotations

import copy
import json

from .files import fingerprint, input_view
from .schema import PlanError, _json_guard, validate_plan

CONTRACT = "attribute-conversion-recovery-v1"
MAX_INPUT_BYTES = 8 * 1024 * 1024


def recovery_capabilities():
    return {"contract": CONTRACT, "authority": "input_draft_only", "executes": False,
            "tool": "gs_preview_attribute_recovery", "cli": "equipment attribute-draft",
            "purpose": "旧常量面板路径不求解转换闭包；先取得无执行权属性账骨架，再预览、核对采用并计算。",
            "classification": "unknown is never promoted to included/confirmed",
            "supported_graph_stats": ["em", "atk", "hp", "def", "energy_recharge"]}


def read_recovery_input(path):
    """A bounded structural reader, intentionally not read_plan()."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("重复JSON字段")
            result[key] = value
        return result

    from .files import read_input_bytes
    raw = read_input_bytes(path, maximum_bytes=MAX_INPUT_BYTES)
    if len(raw) > MAX_INPUT_BYTES:
        raise PlanError("属性修复输入超过8MiB", "agent_size_limit")
    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite")))
        _json_guard(value)
    except (ValueError, UnicodeError, RecursionError, OverflowError) as exc:
        raise PlanError("属性修复输入必须是无重复键的有限JSON", "invalid_plan") from exc
    return value


def preview_attribute_recovery(plan):
    """Return only a patch and limitations, never an execution-ready plan."""
    _json_guard(plan)
    if len(json.dumps(plan, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_INPUT_BYTES:
        raise PlanError("属性修复输入超过8MiB", "agent_size_limit")
    try:
        validate_plan(plan)
    except PlanError as exc:
        issues = exc.details if isinstance(exc.details, list) else []
        dependencies = [row for row in issues if isinstance(row, dict) and row.get("code") == "unsupported_dependency"]
        # Authors may already have named the intended basis while the required
        # graph declaration is still absent. The complete candidate validation
        # below must discharge that issue; unrelated failures remain rejected.
        repairable = {"unsupported_dependency", "attribute_basis_scope"}
        if not dependencies or any(not isinstance(row, dict) or row.get("code") not in repairable for row in issues):
            raise
    else:
        return {**recovery_capabilities(), "status": "not_required", "operations": [],
                "input_fingerprint": fingerprint(input_view(plan)),
                "message": "当前输入未被旧转换闭包检查拒绝；本工具不替代来源审查或计算。"}

    model = plan["mechanics_model"]
    if model.get("attribute_model") is not None:
        raise PlanError("已有显式属性账，请按当前精确依赖修正，不覆盖现有分类", "attribute_recovery_not_applicable", dependencies)
    stats = {row["value"]["stat"] for rule in model["rules"] for row in rule["effects"]
             if isinstance(row["value"], dict) and row["value"].get("kind") == "stat"}
    # v3 owns both EM and ATK. Other operations remain honest capability gaps.
    five_stats = bool(stats & {'hp', 'def', 'base_hp', 'base_def', 'energy_recharge'})
    contract = "attribute-ledger-v4" if five_stats else "attribute-ledger-v3" if "atk" in stats else "attribute-ledger-v2"
    actors = [row["id"] for row in plan["manual_template"]["characters"]]
    bases = ([{"actor": actor, "stat": stat, "included_components": []}
              for actor in actors for stat in (("em", "atk", "hp", "def", "energy_recharge") if five_stats else ("em", "atk"))
              if stat == 'energy_recharge' or any(c['id'] == actor and stat in c['stats'] for c in plan['manual_template']['characters'])]
             if contract in ("attribute-ledger-v3", "attribute-ledger-v4") else
             [{"actor": actor, "included_components": []} for actor in actors])
    model_draft = {"contract": contract, "initial_basis": bases, "effect_classifications": []}
    candidate = copy.deepcopy(plan)
    candidate["mechanics_model"]["attribute_model"] = model_draft
    # This validates the entire submitted input, not just the newly added field.
    # No unknown classification/basis is interpreted as a game fact here.
    validate_plan(candidate)
    return {**recovery_capabilities(), "status": "draft_only", "required_contract": contract,
            "input_fingerprint": fingerprint(input_view(plan)), "adopted": False,
            "operations": [{"op": "add", "path": "/mechanics_model/attribute_model", "value": model_draft}],
            "issues": copy.deepcopy(dependencies),
            "next_steps": ["将骨架与拟议规则改动同批提交；没有修改原方案。", "compose读取实际组件与缺项。",
                           "按来源核对实际消费的base/NONEXTRA分类；未知保持未知。",
                           "明确adopt-attributes/gs_adopt_attributes后重新计算；真实循环仍会拒绝。"]}
