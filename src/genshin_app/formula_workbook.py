"""Finite, formula-driven XLSX projection of a calculated native plan.

The product writer uses OOXML and the standard library, so the portable App does
not require an installed Excel/Python spreadsheet package. Formula cells retain
independently constructed cached values and request recalculation on opening.
This is separate from :mod:`genshin_app.export`'s immutable result snapshot.
"""
from __future__ import annotations

import copy
import io
import json
import math
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from genshin_app.calculation_integrity import (
    validate_calculated_attribute_budget,
    validate_result_binding,
)
from genshin_app.equipment.action_dispatch_sources import action_dispatch_execution_plan
from genshin_app.equipment.action_event_schema import action_event_receipt_compiler
from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.equipment.limited_hit_benefit_receipt import (
    validate_limited_hit_benefit_receipt,
)
from genshin_app.equipment.mechanism_receipt import validate_mechanism_receipt
from genshin_app.equipment.passive_receipt import validate_passive_receipt
from genshin_app.equipment.reaction_benefit_receipt import (
    validate_reaction_benefit_receipt,
)
from genshin_app.equipment.schema import PlanError
from genshin_app.export import (
    PROVISIONAL_RESULT_FIDELITY,
    PROVISIONAL_RESULT_LABEL,
    action_event_rows,
    action_resource_rows,
    attack_metadata_rows,
    attribute_ledger_rows,
    beneficiary_eligibility_rows,
    damage_reporting_rows,
    elemental_state_rows,
    energy_generation_rows,
    external_damage_rows,
    formal_audit_payload_rows,
    formal_calculation_audit_payload,
    limited_hit_benefit_rows,
    mechanism_rows,
    passive_source_rows,
    periodic_attack_rows,
    provisional_entity_rows,
    provisional_usage_receipt,
    reaction_benefit_rows,
    shared_pool_rows,
    target_condition_rows,
)
from genshin_app.formula_fixed_overrides import (
    fixed_override_projection,
    fixed_override_support,
)
from genshin_app.formula_weighting import weighting_projection
from genshin_app.presentation import display_names
from genshin_app.rotation_check import check_sheets
from genshin_app.verification_scope import verification_scope
from genshin_engine.beneficiary_selection import possible_recipients
from genshin_engine.constants import (
    AGGRAVATE_FACTOR,
    ENEMY_ZONES,
    LUNAR_FACTOR,
    MULTIPLICATIVE_ZONES,
    REACTION_BASE_VALUE,
    STELLAR_FACTOR,
    TRANSFORMATIVE_FACTOR,
    stellar_superconduct_factor,
)
from genshin_engine.core import _consumes_effect
from genshin_engine.execution_control import ExecutionCancelled
from genshin_engine.registry import BENEFIT_TYPES, effect_matches_hit

WORKBOOK_SCHEMA = "genshin.formula-workbook.v1"
_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_STAT_NAMES = {"atk": "攻击力", "hp": "生命值", "def": "防御力", "em": "元素精通",
               "base_atk": "基础攻击力", "base_hp": "基础生命值", "base_def": "基础防御力",
               "crit_rate": "暴击率", "crit_dmg": "暴击伤害", "energy_recharge": "元素充能效率"}
_ELEMENT_NAMES = {"physical": "物理", "pyro": "火", "hydro": "水", "electro": "雷",
                  "cryo": "冰", "anemo": "风", "geo": "岩", "dendro": "草", "all": "全部"}
_ZONE_NAMES = {"stat_add": "面板固定加成", "stat_pct": "基础面板百分比", "base_add": "倍率后加算",
               "base_mult": "基础独立乘区", "dmg_bonus": "伤害加成", "crit_rate": "暴击率",
               "crit_dmg": "暴击伤害", "em": "元素精通", "reaction_bonus": "反应加成",
               "reaction_flat": "反应固定加算", "res_shred": "减抗", "def_reduction": "减防",
               "def_ignore": "无视防御", "lunar_base": "月曜基础增伤", "lunar_mult": "月曜独立乘区",
               "lunar_elevate": "月曜擢升", "stellar_base": "星烁基础增伤",
               "stellar_mult": "星烁独立乘区", "stellar_elevate": "星烁擢升"}


class FormulaWorkbookError(ValueError):
    """The calculation envelope is absent, inconsistent or cannot be projected."""


def _is_execution_prefix(result: dict) -> bool:
    """Use actual coverage, not action/cycle status or a missing duration."""
    window = result.get("damage_report_window")
    if isinstance(window, dict):
        return (window.get("status") != "complete"
                or window.get("required_validation", {}).get("status") != "complete")
    interpretation = (result.get('rotation_check') or {}).get('damage_interpretation')
    return ((result.get('mechanics') or {}).get('complete_first_cycle') is False
            or isinstance(interpretation, dict) and interpretation.get('kind') == 'executed_prefix')


def _rotation_summary_label(calculated):
    result = calculated.get('result', {})
    window = result.get('damage_report_window')
    if isinstance(window, dict) and window.get('contract') == 'damage-report-window-v1':
        from genshin_app.calculation_completion import calculation_completion
        completion = calculation_completion(calculated)
        current = {'complete': '通过', 'failed': '未通过', 'pending': '待验证'}.get(
            completion['required_validation'], '待验证')
        steady = '已证明' if completion['steady_state'] == 'proven' else '未证明'
        return f'本窗{current}；稳态{steady}'
    status = (result.get('rotation_check') or {}).get('status')
    original = {'unproven': '尚未证明', 'passed': '通过', 'valid': '结构有效', 'invalid': '输入无效',
                'failed': '未通过', 'pending': '待补信息', 'not_checked': '未检查'}.get(status, status or '未检查')
    return '导出时状态：' + original


def _column(number: int) -> str:
    result = ""
    while number:
        number, digit = divmod(number - 1, 26)
        result = chr(65 + digit) + result
    return result


@dataclass(frozen=True)
class _Expr:
    text: str
    value: float

    def _binary(self, other, op):
        other = _number(other)
        value = {"+": lambda: self.value + other.value, "-": lambda: self.value - other.value,
                 "*": lambda: self.value * other.value, "/": lambda: self.value / other.value}[op]()
        return _Expr(f"({self.text}{op}{other.text})", value)

    def __add__(self, other): return self._binary(other, "+")
    def __radd__(self, other): return _number(other).__add__(self)
    def __sub__(self, other): return self._binary(other, "-")
    def __rsub__(self, other): return _number(other).__sub__(self)
    def __mul__(self, other): return self._binary(other, "*")
    def __rmul__(self, other): return _number(other).__mul__(self)
    def __truediv__(self, other): return self._binary(other, "/")
    def __rtruediv__(self, other): return _number(other).__truediv__(self)
    def __pow__(self, power): return _Expr(f"({self.text}^{power})", self.value ** power)


def _number(value) -> _Expr:
    if isinstance(value, _Expr):
        return value
    return _Expr(str(float(value)), float(value))


def _bound(kind, value, limit):
    value, limit = _number(value), _number(limit)
    operation = min if kind == "MIN" else max
    return _Expr(f"{kind}({value.text},{limit.text})", operation(value.value, limit.value))


@dataclass
class _Cell:
    value: object
    formula: str | None = None
    style: int = 0


class _Sheet:
    def __init__(self, name, widths=None, *, hidden_columns=()):
        self.name, self.rows = name, []
        self.widths = widths or [24, 30, 24, 24, 36, 60]
        self.hidden_columns = frozenset(hidden_columns)

    def append(self, values, *, style=0):
        cells = []
        for value in values:
            if isinstance(value, _Cell):
                cells.append(value)
            elif isinstance(value, _Expr):
                cells.append(_Cell(value.value, value.text, 3))
            else:
                cells.append(_Cell(value, style=style))
        self.rows.append(cells)
        return len(self.rows)

    def reference(self, row, column):
        value = self.rows[row - 1][column - 1].value
        return _Expr(f"'{self.name}'!${_column(column)}${row}", float(value))


def _validate_attack_metadata_receipt(plan, result, report, receipt):
    records = result.get("attack_metadata_compilations") or []
    if not (records or plan.get("attack_metadata_sources")
            or report.get("attack_metadata_compilations") or receipt.get("attack_metadata")):
        return

    def reject():
        raise FormulaWorkbookError("攻击元信息、原生来源及动作档案确认不一致，请回App重新计算。")

    try:
        execution_plan = action_dispatch_execution_plan(plan, report)
    except (PlanError, ExecutionCancelled):
        raise
    except ValueError:
        reject()
    bindings = execution_plan.get("attack_metadata_sources") or []

    expected_receipt = {
        "compiler": "database-attack-metadata-v1",
        "compilations_fingerprint": fingerprint(records),
        "input_sources_fingerprint": fingerprint(bindings),
    }
    dispatch_materialization = report.get("action_dispatch_materialization") or {}
    dispatch_fingerprint = dispatch_materialization.get("content_fingerprint")
    if dispatch_fingerprint is not None:
        expected_receipt["action_dispatch_materialization_fingerprint"] = dispatch_fingerprint
    if (not records or not bindings or records != report.get("attack_metadata_compilations")
            or receipt.get("input_fingerprint") != fingerprint(input_view(plan))
            or receipt.get("result_fingerprint") != fingerprint(result)
            or report.get("attack_metadata_compilation_fingerprint") != fingerprint(records)
            or receipt.get("attack_metadata") != expected_receipt):
        reject()
    profiles = {p["id"]: p for p in execution_plan.get("action_event_profiles", [])}
    events = {r["profile_id"]: r for r in report.get("action_event_compilations", [])}
    sources = {b["action_event_profile_id"]: b for b in bindings}
    if (len(records) != len(bindings) or len(sources) != len(bindings)
            or {r.get("action_event_profile_id") for r in records} != set(sources)):
        reject()
    for record in records:
        identity = record["action_event_profile_id"]
        binding, profile, event = sources[identity], profiles.get(identity), events.get(identity)
        query = record.get("query") or {}
        candidate = query.get("selected_candidate") or {}
        stored_semantic = record.get("semantic_dependency") or {}
        stored_interpretation = stored_semantic.get("interpretation") or {}
        interpretation = {
            key: binding[key] for key in ("purpose", "status", "source")
        }
        # Packages produced before the explicit application-mode contract used
        # shared_group as their complete declared interpretation.  Their signed
        # source/result fingerprints remain sufficient offline evidence; adding
        # today's canonical pending/legacy fields while validating would rewrite
        # history and reject an otherwise byte-identical package.
        legacy_application_contract = (
            "application_mode" not in binding
            and "application_mode" not in record
            and "application_rule_audit" not in binding
            and "application_adapter" not in binding
            and "application_rule_audit" not in record
            and "application_adapter" not in record
            and "application_mode" not in stored_interpretation
            and "application_rule_audit" not in stored_interpretation
            and "application_adapter" not in stored_interpretation
            and "application_mode" not in (record.get("compiled_fields") or {})
            and "shared_group" in binding
            and "counter_tag" not in binding
            and "verification" not in binding
        )
        if legacy_application_contract:
            if "shared_group" in binding:
                interpretation["shared_group"] = binding["shared_group"]
        else:
            raw_mode = binding.get("application_mode")
            interpretation["application_mode"] = (
                "pending" if raw_mode in (None, "unknown") else raw_mode
            )
            if interpretation["application_mode"] == "sequence":
                interpretation["counter_tag"] = (
                    binding.get("counter_tag") or binding.get("shared_group")
                )
            if "verification" in binding:
                interpretation["verification"] = binding["verification"]
            if "shared_group" in binding:
                interpretation["legacy_counter_tag_field"] = "shared_group"
            if "application_rule_audit" in binding:
                interpretation["application_adapter"] = copy.deepcopy(
                    binding["application_adapter"])
                interpretation["application_rule_audit"] = copy.deepcopy(
                    binding["application_rule_audit"])
                interpretation["database_application_identity"] = copy.deepcopy(
                    record.get("database_application_identity"))
                interpretation["application_identity_divergence"] = record.get(
                    "application_identity_divergence")
        semantic = {"database": query.get("semantic_dependency"), "interpretation": interpretation}
        if (not profile or not event or record.get("status") != "ready"
                or record.get("compiler") != "database-attack-metadata-v1"
                or record.get("whole_source_status") != "partial"
                or record.get("gameplay_interpretation") != "declared_not_verified"
                or record.get("prototype_hit_id") != profile["prototype_hit_id"]
                or record.get("application_profile_id") != profile["application_profile_id"]
                or record.get("candidate_id") != binding["candidate_id"]
                or candidate.get("candidate_id") != binding["candidate_id"]
                or query.get("status") != "ready" or candidate.get("status") != "ready"
                or record.get("projection") != candidate.get("projection")
                or not query.get("semantic_dependency")
                or record.get("source_fingerprint") != query.get("source_fingerprint")
                or query.get("source_fingerprint") != fingerprint(query["semantic_dependency"])
                or record.get("semantic_dependency") != semantic
                or record.get("metadata_dependency_fingerprint") != fingerprint(semantic)
                or event.get("metadata_source_fingerprint") != record.get("source_fingerprint")
                or event.get("metadata_dependency_fingerprint") != fingerprint(semantic)):
            reject()


def _validate_action_event_receipt(plan, result, report, receipt):
    records = result.get("action_event_compilations")
    fields = ("action_event_profiles", "action_event_bindings", "action_event_overrides")
    if not (any(plan.get(key) for key in fields) or records
            or report.get("action_event_compilations") or receipt.get("action_events")):
        return

    def reject():
        raise FormulaWorkbookError("动作档案、派生映射、来源或执行模型与本次回执不一致，请回App重新计算。")

    try:
        execution_plan = action_dispatch_execution_plan(plan, report)
    except (PlanError, ExecutionCancelled):
        raise
    except ValueError:
        reject()

    dispatch_receipt = receipt.get("action_events") or {}
    model = report.get("mechanics_model")
    execution = report.get("action_event_execution_rotation_model")
    base = report.get("mechanics_input_template")
    if (not records or not isinstance(model, dict) or not isinstance(execution, dict) or not isinstance(base, dict)
            or not execution_plan.get("action_event_profiles")
            or not execution_plan.get("action_event_bindings")
            or records != report.get("action_event_compilations")
            or receipt.get("input_fingerprint") != fingerprint(input_view(plan))
            or receipt.get("result_fingerprint") != fingerprint(result)
            or report.get("action_event_compilation_fingerprint") != fingerprint(records)
            or report.get("compiled_mechanics_model_fingerprint") != fingerprint(model)):
        reject()
    source_compilations = {key: report.get(key, []) for key in
        ("hit_parameter_compilations", "elemental_compilations", "reaction_damage_compilations")}
    if report.get("attack_metadata_compilations"):
        source_compilations["attack_metadata_compilations"] = report["attack_metadata_compilations"]
    if report.get("periodic_attack_compilations"):
        source_compilations["periodic_attack_compilations"] = report["periodic_attack_compilations"]
    if report.get("energy_generation_compilations"):
        source_compilations["energy_generation_compilations"] = report["energy_generation_compilations"]
    if report.get("limited_hit_benefit_compilations"):
        source_compilations["limited_hit_benefit_compilations"] = report["limited_hit_benefit_compilations"]
    if report.get("mechanism_compilations"):
        source_compilations["mechanism_compilations"] = report["mechanism_compilations"]
    expected = {
        "compiler": action_event_receipt_compiler(records), "dispatch_mode": "calibrated",
        "skill_coverage": "partial", "resource_coverage": "not_generated",
        "compilations_fingerprint": fingerprint(records),
        "compiled_mechanics_model_fingerprint": fingerprint(model),
        "execution_rotation_model_fingerprint": fingerprint(execution),
        "mechanics_input_template_fingerprint": fingerprint(base),
        "source_compilations_fingerprint": fingerprint(source_compilations),
    }
    if dispatch_receipt != expected:
        reject()
    for report_key, receipt_key, keys in (
        ("hit_parameter_compilations", "hit_parameter_sources", ("hit_id", "snapshot", "source", "compiler", "compilation_fingerprint")),
        ("elemental_compilations", "elemental_sources", ("profile_id", "snapshot", "source", "compiler", "compilation_fingerprint")),
        ("reaction_damage_compilations", "reaction_damage_sources", ("reaction_type", "snapshot", "profile_fingerprint", "compiler", "compilation_fingerprint")),
    ):
        expected_sources = [{key: record.get(key) for key in keys} for record in report.get(report_key, [])]
        if receipt.get(receipt_key, []) != expected_sources:
            reject()
    if (result.get("hit_parameter_sources", []) != report.get("hit_parameter_compilations", [])
            or (result.get("elemental") or {}).get("source_compilations", []) != report.get("elemental_compilations", [])
            or (result.get("elemental") or {}).get("reaction_damage_source_compilations", []) != report.get("reaction_damage_compilations", [])):
        reject()
    profiles = {profile["id"]: profile for profile in execution_plan["action_event_profiles"]}
    if len(records) != len(profiles) or {r.get("profile_id") for r in records} != set(profiles):
        reject()
    patterns = {p["id"]: p for p in model.get("hit_patterns", []) if p["id"].startswith("auto.action.")}
    generated = [row for record in records for row in record.get("generated", [])]
    if (len({row["hit_id"] for row in generated}) != len(generated)
            or len(patterns) != len(generated) or set(patterns) != {row["pattern_id"] for row in generated}):
        reject()
    for record in records:
        if (record.get("status") != "ready" or record.get("dispatch_mode") != "calibrated"
                or record.get("skill_coverage") != "partial" or record.get("resource_coverage") != "not_generated"
                or not record.get("profile_fingerprint")
                or profiles[record["profile_id"]]["accepted_profile_fingerprint"] != record["profile_fingerprint"]):
            reject()
        for row in record.get("generated", []):
            pattern = patterns[row["pattern_id"]]
            if (row["hit_id"] != row["pattern_id"] + ":1" or pattern["action_id"] != row["action_id"]
                    or pattern["prototype_hit_id"] != row["prototype_hit_id"]
                    or pattern["application"]["landed"] is not row["landed"]
                    or pattern["orders"] != [row["order"]] or len(pattern["offsets"]) != 1):
                reject()


def _captured_display_catalog(calculated):
    """Use only the frozen display snapshot; never open a database here."""
    context = calculated.get("display_context")
    receipt = calculated.get("result_receipt") or {}
    if "display_context" not in calculated and "display_source" not in receipt:
        return None
    message = "中文名称目录与本次计算回执不一致，请重新计算。"
    if not isinstance(context, dict):
        raise FormulaWorkbookError(message)
    catalog, warnings, source = context.get("catalog"), context.get("warnings"), context.get("receipt")
    if (not isinstance(catalog, dict) or not isinstance(warnings, list)
            or any(not isinstance(warning, str) for warning in warnings) or not isinstance(source, dict)):
        raise FormulaWorkbookError(message)
    line = calculated["plan"].get("manual_template", {}).get("meta", {}).get("version_line", "release")
    if (source.get("kind") != "calculation_snapshot" or source.get("version_line") != line
            or source.get("catalog_fingerprint") != fingerprint(catalog)
            or source.get("status") != ("template_fallback" if warnings else "ready")
            or source != receipt.get("display_source")
            or "version_line" in catalog and catalog["version_line"] != line):
        raise FormulaWorkbookError(message)
    entries = catalog.get("characters", [])
    if (not isinstance(entries, list) or any(not isinstance(item, dict) or "id" not in item
            or type(item["id"]) not in (int, str) or not str(item["id"]).strip()
            or not isinstance(item.get("name", ""), str)
            or not isinstance(item.get("display_name", ""), str)
            or not (item.get("name") or item.get("display_name"))
            or not isinstance(item.get("aliases", []), list)
            or any(not isinstance(alias, str) for alias in item.get("aliases", [])) for item in entries)):
        raise FormulaWorkbookError(message)
    return catalog


@verification_scope
def _unpack(calculated, *, _check_only=False, _formal_required=False, _session=None):
    if not isinstance(calculated, dict):
        raise FormulaWorkbookError("活公式导出需要原生方案的完整计算回执。")
    plan, result = calculated.get("plan"), calculated.get("result")
    if not isinstance(plan, dict) or not isinstance(result, dict):
        raise FormulaWorkbookError("活公式导出缺少 plan 或 result，结果快照不能替代原始输入。")
    report = calculated.get("report") or {}
    try:
        validate_result_binding(result, calculated.get("result_receipt"), formal_required=_formal_required,
                                _report=report if _formal_required else None, _session=_session)
    except (PlanError, ExecutionCancelled):
        raise
    except ValueError as exc:
        raise FormulaWorkbookError(str(exc)) from exc
    _captured_display_catalog(calculated)
    template = plan.get("resolved_template")
    if not isinstance(template, dict):
        raise FormulaWorkbookError("活公式导出需要已解析的同修订伤害模板。")
    receipt = calculated.get("result_receipt") or {}
    for key in ("plan_id", "revision"):
        if key in receipt and receipt[key] != plan.get(key):
            raise FormulaWorkbookError(f"计算回执的 {key} 与方案不一致，请重新计算。")
    if receipt.get("output_fingerprint") and receipt["output_fingerprint"] != fingerprint(template):
        raise FormulaWorkbookError("计算回执的输出指纹与伤害模板不一致，请重新计算。")
    if ((result.get("elemental") or {}).get("ruleset") == "gauge-freeze-reference-v1"
            and "manual_template" in plan
            and receipt.get("input_fingerprint") != fingerprint(input_view(plan))):
        raise FormulaWorkbookError("实验冻结参考输入与回执指纹不一致；初态、接触或参考确认改变后请重新计算。")
    try:
        validate_calculated_attribute_budget(plan, result, report, receipt)
        from genshin_app.equipment.calculation_overrides.ownership_receipt import (
            validate_ownership_result_proof,
        )
        validate_ownership_result_proof(result, receipt, report)
        from genshin_app.equipment.agent_execution_receipt import (
            validate_agent_execution_result_receipt,
        )
        validate_agent_execution_result_receipt(result, receipt)
        from genshin_app.equipment.attribute_receipt import validate_attribute_receipt
        validate_attribute_receipt(plan, result, report, receipt)
        from genshin_app.equipment.counter_frame_receipt import (
            validate_counter_frame_receipt,
        )
        validate_counter_frame_receipt(plan, result, report, receipt, _session=_session)
        from genshin_app.equipment.resource_reaction_receipt import (
            validate_resource_reaction_receipt,
        )
        validate_resource_reaction_receipt(plan, result, report, receipt, _session=_session)
        from genshin_app.static_inclusion_reporting import has_static_inclusion_signal
        if has_static_inclusion_signal(result, receipt, plan=plan, report=report):
            from genshin_app.equipment.static_inclusion_receipt import (
                validate_static_inclusion_receipt,
            )
            validate_static_inclusion_receipt(plan, result, report, receipt)
        from genshin_app.equipment.external_damage import (
            validate_external_damage_receipt,
        )
        validate_external_damage_receipt(plan, result, report, receipt)
        from genshin_app.equipment.damage_reporting_receipt import (
            validate_empty_damage_receipt,
        )
        validate_empty_damage_receipt(plan, result, report, receipt)
        from genshin_app.equipment.damage_report_window_receipt import (
            validate_window_calculation,
        )
        validate_window_calculation(plan, result, report, receipt, _session=_session)
        from genshin_app.equipment.special_damage_echo_receipt import (
            validate_echo_calculation,
        )
        validate_echo_calculation(plan, result, report, receipt, _session=_session)
        validate_reaction_benefit_receipt(plan, result, report, receipt)
        from genshin_app.equipment.reaction_em_receipt import (
            validate_reaction_em_receipt,
        )
        validate_reaction_em_receipt(plan, result, report, receipt)
        validate_passive_receipt(plan, result, report, receipt)
        from genshin_app.equipment.energy_delivery_receipt import (
            validate_automatic_energy_receipt,
        )
        validate_automatic_energy_receipt(plan, result, report, receipt, _session=_session)
        from genshin_app.equipment.action_capacity_receipt import (
            validate_action_capacity_receipt,
        )
        validate_action_capacity_receipt(plan, result, report, receipt, _session=_session)
        from genshin_app.equipment.action_admission_receipt import (
            validate_action_admission_receipt,
        )
        validate_action_admission_receipt(plan, result, report, receipt, _session=_session)
        from genshin_app.equipment.energy_generation_receipt import (
            validate_energy_generation_receipt,
        )
        validate_energy_generation_receipt(plan, result, report, receipt)
        validate_limited_hit_benefit_receipt(plan, result, report, receipt)
        validate_mechanism_receipt(plan, result, report, receipt, _session=_session)
        from genshin_app.equipment.snapshot_capture_receipt import (
            validate_snapshot_capture_receipt,
        )
        validate_snapshot_capture_receipt(plan, result, report, receipt, _session=_session)
        from genshin_app.equipment.action_automation_audit import (
            validate_action_materialization_result_receipt,
        )
        validate_action_materialization_result_receipt(result, receipt)
        if (plan.get("periodic_attack_sources") or result.get("periodic_attack_compilations")
                or report.get("periodic_attack_compilations") or receipt.get("periodic_attacks")):
            from genshin_app.equipment.periodic_receipt import validate_periodic_receipt
            validate_periodic_receipt(plan, result, report, receipt)
    except (PlanError, ExecutionCancelled):
        raise
    except (TypeError, ValueError) as exc:
        raise FormulaWorkbookError(str(exc)) from exc
    fixed = (report.get("mechanics_model") or {}).get("fixed_effect_sources")
    if fixed or result.get("fixed_effect_sources") or receipt.get("target_conditions"):
        condition_receipt = receipt.get("target_conditions") or {}
        if (not fixed or fixed != result.get("fixed_effect_sources")
                or receipt.get("input_fingerprint") != fingerprint(input_view(plan))
                or receipt.get("result_fingerprint") != fingerprint(result)
                or condition_receipt.get("fixed_sources_fingerprint") != fingerprint(fixed)
                or condition_receipt.get("trace_fingerprint") != fingerprint(result.get("target_condition_trace"))
                or condition_receipt.get("compiled_model_fingerprint") != fingerprint(report.get("mechanics_model"))
                or condition_receipt.get("upstream_compilations_fingerprint") != fingerprint(report.get("upstream_compilations", []))):
            raise FormulaWorkbookError("目标条件来源、逐击流水或编译模型与本次回执不一致，请重新计算。")
        records = report.get("upstream_compilations", [])
        expected_sources = [{k: c.get(k) for k in ("binding_id", "source_identity", "compilation_fingerprint", "provenance",
            "gameplay_semantics_status", "semantic_assumptions", "projection_status", "whole_source_status",
            "coverage_certificate", "branches")} for c in records]
        if receipt.get("upstream_sources", []) != expected_sources:
            raise FormulaWorkbookError("目标条件的上游来源回执与本次完整编译记录不一致，请重新计算。")
    if (plan.get("action_resource_sources") or result.get("action_resource_compilations")
            or report.get("action_resource_compilations") or receipt.get("action_resources")):
        bindings, records = plan.get("action_resource_sources"), result.get("action_resource_compilations")
        resource_receipt = receipt.get("action_resources") or {}
        compiled = report.get("compiled_rotation_model")
        if (not bindings or not records or not isinstance(compiled, dict)
                or receipt.get("input_fingerprint") != fingerprint(input_view(plan))
                or receipt.get("result_fingerprint") != fingerprint(result)):
            raise FormulaWorkbookError("动作资源输入或结果与回执指纹不一致，请重新计算。")
        if (fingerprint(compiled) != report.get("compiled_rotation_model_fingerprint")
                or fingerprint(compiled) != resource_receipt.get("compiled_rotation_model_fingerprint")):
            raise FormulaWorkbookError("动作资源编译模型与回执指纹不一致，请重新计算。")
        if (records != report.get("action_resource_compilations")
                or fingerprint(records) != resource_receipt.get("compilations_fingerprint")
                or resource_receipt.get("identity_coverage") != "explicit_sources_only"
                or {b["ability_id"] for b in bindings} != {r.get("ability_id") for r in records}
                or any(r.get("status") != "ready" or not r.get("compilation_fingerprint") for r in records)):
            raise FormulaWorkbookError("动作资源来源记录缺失、未就绪或与回执不一致，请重新计算。")
    _validate_attack_metadata_receipt(plan, result, report, receipt)
    _validate_action_event_receipt(plan, result, report, receipt)
    if len(template.get("hits", [])) != len(result.get("hits", [])):
        raise FormulaWorkbookError("伤害事件与结果数量不一致，请重新计算。")
    try:
        execution_plan = action_dispatch_execution_plan(plan, report)
    except (PlanError, ExecutionCancelled):
        raise
    except ValueError as exc:
        raise FormulaWorkbookError("动作派发私有物化与本次计算回执不一致，请回App重新计算。") from exc
    if _check_only:
        return None
    projection_plan = copy.deepcopy(plan)
    for field in ("action_event_profiles", "action_event_bindings", "action_event_overrides",
                  "attack_metadata_sources", "periodic_attack_sources"):
        if field in execution_plan:
            projection_plan[field] = copy.deepcopy(execution_plan[field])
    return projection_plan, template, result, receipt


@verification_scope
def validate_formula_calculation(calculated: dict, *, _formal_required=False, _session=None) -> None:
    """Check existing export/source contracts without writing files or reading DB.

    This preserves legacy optional receipt fields. A new complete-package entry
    point must separately require its universal fields and fingerprints.
    """
    _unpack(calculated, _check_only=True, _formal_required=_formal_required, _session=_session)


def _dependency_affects(dependency, stat):
    if dependency["zone"] in ("em", "energy_recharge"):
        return dependency["zone"] == stat
    return dependency["zone"] in ("stat_add", "stat_pct") and dependency["stat"] == stat


@verification_scope
def _mechanics_links(calculated, *, _session=None):
    """Map each projected effect to source parameters and sampled dependencies."""
    return _mechanics_links_from_parts(calculated, _unpack(calculated, _session=_session))


def _mechanics_links_from_parts(calculated, parts):
    """Internal projection of this invocation's already verified material."""
    plan, template, result, _ = parts
    model = (calculated.get("report") or {}).get("mechanics_model") or plan.get("mechanics_model")
    if not model:
        return {}, []
    base = (calculated.get("report") or {}).get("mechanics_input_template")
    trace = calculated.get("mechanics_trace", result.get("mechanics_trace", []))
    samples = calculated.get("sampling_trace", result.get("sampling_trace", []))
    rules = {(r["id"], e["id"]): (ri, ei, r, e) for ri, r in enumerate(model.get("rules", [])) for ei, e in enumerate(r["effects"])}
    fixed = {(s["id"], e["id"]): (si, ei, s, e) for si, s in enumerate(model.get("fixed_effect_sources", [])) for ei, e in enumerate(s["effects"])}
    fixed_applied = {(r.get("event_id"), r.get("projected_effect_id")): r for r in result.get("target_condition_trace", [])
                     if r.get("applied") is True and r.get("cycle", 0) == 0 and r.get("reported_damage") is True}
    manual = {f"manual:{b['id']}:{e['id']}": (bi, ei, e) for bi, b in enumerate((base or {}).get("buffs", [])) for ei, e in enumerate(b["effects"])}
    manual_sources = {(b["id"], e["id"]): (bi, ei, e) for bi, b in enumerate((base or {}).get("buffs", [])) for ei, e in enumerate(b["effects"])}
    manual_ids = {(b["id"], e["id"]) for b in (base or {}).get("buffs", []) for e in b["effects"]}
    energy_windows = {w["id"] for w in (plan.get("rotation_model") or {}).get("energy_recharge_windows", [])}
    characters = {c["id"]: c["stats"] for c in template["characters"]}
    applied = {(r.get("event_id"), r.get("projected_effect_id")): r for r in trace if r.get("type") == "effect_applied" and r.get("cycle", 0) == 0}
    sampling = {(r.get("event_id"), r.get("projected_effect_id")): r for r in samples if r.get("cycle", 0) == 0}
    manual_applied = {(r.get("event_id"), r.get("projected_effect_id")): r for r in trace if r.get("type") == "manual_effect_applied" and r.get("cycle", 0) == 0}
    links, issues = {}, []
    for bi, buff in enumerate(template.get("buffs", [])):
        hid = buff["id"].removeprefix("mechanics:")
        for ei, effect in enumerate(buff["effects"]):
            key, path = (bi, ei), f"buffs[{bi}].effects[{ei}]"
            if effect["id"] in manual:
                links[key] = {"kind": "manual", "source": manual[effect["id"]]}
                continue
            manual_record = manual_applied.get((hid, effect["id"]))
            if manual_record and (manual_record.get("buff_id"), manual_record.get("effect_id")) in manual_sources:
                links[key] = {"kind": "manual", "source": manual_sources[(manual_record["buff_id"], manual_record["effect_id"])]}
                continue
            record = applied.get((hid, effect["id"]))
            target_record = fixed_applied.get((hid, effect["id"]))
            if target_record:
                source = fixed.get((target_record["fixed_source_id"], target_record["effect_id"]))
                if source is not None and source[3]["value"] == effect["value"] == target_record["value"]:
                    links[key] = {"kind": "fixed_effect_source", "source_index": source[0], "effect_index": source[1],
                                  "source": source[2], "effect": source[3], "record": target_record}
                    continue
            if record and (record["rule_id"], record["effect_id"]) in rules:
                ri, source_ei, rule, source_effect = rules[(record["rule_id"], record["effect_id"])]
                sample = sampling.get((hid, effect["id"]))
                if isinstance(source_effect['value'],dict) and source_effect['value'].get('kind')=='counter_view':
                    issues.append({'code':'counter_view_sample_fixed','path':path,
                        'message':'资源收益来自实际累计、单位换算与封顶/阶梯读取。工作簿保留引擎最终层数合成值；修改相关输入须回App重算。'})
                    continue
                if sample and sample.get('attribute_receipt_id'):
                    issues.append({'code':'attribute_ledger_sample_fixed', 'path':path,
                        'message':'该收益读取引擎属性账的总量/非额外量/历史帧。工作簿保留实际取样后的固定值；修改属性或分类须回App重算，不能用基础面板替代。'})
                    continue
                if sample:
                    dependencies_valid = sample.get("stat") in characters.get(sample.get("character"), {})
                    for dep in sample.get("dependencies", []):
                        if not _dependency_affects(dep, sample["stat"]):
                            continue
                        if dep["zone"] == "stat_pct":
                            dependencies_valid &= "base_" + dep["stat"] in characters.get(sample.get("character"), {})
                        if "buff_id" in dep:
                            dependencies_valid &= (dep["buff_id"], dep.get("effect_id")) in manual_ids
                        elif "rule_id" in dep:
                            target = rules.get((dep["rule_id"], dep.get("effect_id")))
                            dependencies_valid &= target is not None and isinstance(target[3]["value"], (int, float)) and "layers" in dep
                        elif "energy_window_id" in dep:
                            dependencies_valid &= dep["energy_window_id"] in energy_windows
                        else:
                            dependencies_valid = False
                    if not dependencies_valid:
                        issues.append({"code": "mechanics_dependency_unavailable", "path": path,
                                       "message": f"派生收益 {effect['id']} 的采样属性、源收益或明确层数缺失。"})
                        continue
                if not isinstance(source_effect["value"], dict) or sample:
                    links[key] = {"kind": "rule", "rule_index": ri, "effect_index": source_ei,
                                  "rule": rule, "effect": source_effect, "record": record, "sample": sample}
                    continue
            issues.append({"code": "mechanics_dependency_unavailable", "path": path,
                           "message": f"派生收益 {effect['id']} 缺少可重建的原始参数或实例采样依赖。"})
    if not base:
        issues.append({"code": "mechanics_input_unavailable", "path": "report.mechanics_input_template",
                       "message": "缺少机制执行前的原始面板与手工增益投影。"})
    return links, issues


def _limited_hit_fixed_paths(calculated, template):
    """Freeze current provider ATK inputs, including potential ordinary writes.

    The workbook can expose their formula dependencies, but does not re-run the
    admission/quota/sampling proof after an edit. Unrelated stats stay editable.
    """
    records = calculated.get("result", {}).get("limited_hit_benefit_compilations") or []
    providers = {row["actor"] for row in records}
    if not providers:
        return []
    paths = []
    for index, character in enumerate(template["characters"]):
        if character["id"] in providers:
            paths.extend(f"characters[{index}].stats.{key}" for key in ("atk", "base_atk")
                         if key in character["stats"] or key == "atk")
    report = calculated.get("report") or {}
    for index, buff in enumerate((report.get("mechanics_input_template") or {}).get("buffs", [])):
        if buff.get("target") is not None and buff["target"] not in providers:
            continue
        for ei, effect in enumerate(buff["effects"]):
            if effect["zone"] in ("stat_add", "stat_pct") and effect["stat"] == "atk":
                paths.append(f"mechanics_input.buffs[{index}].effects[{ei}].value")
    model = report.get("mechanics_model") or calculated["plan"].get("mechanics_model") or {}
    for index, rule in enumerate(model.get("rules", [])):
        possible = set(possible_recipients(rule, providers))
        if not providers.intersection(possible):
            continue
        for ei, effect in enumerate(rule["effects"]):
            info = BENEFIT_TYPES.get(effect["benefit_type_id"], {})
            if info.get("zone") in ("stat_add", "stat_pct") and info.get("stat") == "atk":
                paths.append(f"mechanics_model.rules[{index}].effects[{ei}].value")
    return paths


def _mechanism_fixed_paths(calculated, template):
    """Freeze exactly the stat inputs read by materialized generic expressions."""
    records = calculated.get("result", {}).get("mechanism_compilations") or []
    reads = {(record["actor"], effect["value"]["stat"])
             for record in records for rule in record.get("rules", []) for effect in rule["effects"]
             if isinstance(effect["value"], dict)}
    if not reads:
        return []
    paths = []
    for index, character in enumerate(template["characters"]):
        for actor, stat in sorted(reads):
            if character["id"] == actor:
                for key in (stat, "base_" + stat):
                    if key in character["stats"] or key == stat:
                        paths.append(f"characters[{index}].stats.{key}")
    report = calculated.get("report") or {}
    for bi, buff in enumerate((report.get("mechanics_input_template") or {}).get("buffs", [])):
        stats = {stat for actor, stat in reads if buff.get("target") in (None, actor)}
        for ei, effect in enumerate(buff["effects"]):
            if (effect["zone"] in ("stat_add", "stat_pct") and effect["stat"] in stats
                    or effect["zone"] == "em" and "em" in stats):
                paths.append(f"mechanics_input.buffs[{bi}].effects[{ei}].value")
    for ri, rule in enumerate((report.get("mechanics_model") or {}).get("rules", [])):
        possible = set(possible_recipients(rule, {a for a, _ in reads}))
        stats = {stat for actor, stat in reads if actor in possible}
        for ei, effect in enumerate(rule["effects"]):
            info = BENEFIT_TYPES.get(effect["benefit_type_id"], {})
            if (info.get("zone") in ("stat_add", "stat_pct") and info.get("stat") in stats
                    or info.get("zone") == "em" and "em" in stats):
                paths.append(f"mechanics_model.rules[{ri}].effects[{ei}].value")
    return paths


@verification_scope
def formula_workbook_support(calculated: dict, *, _session=None) -> dict:
    """Return an explicit editing/support contract without writing a workbook."""
    parts = _unpack(calculated, _session=_session)
    links = _mechanics_links_from_parts(calculated, parts)
    return _formula_support_from_parts(calculated, parts, links)


def _fixed_value_projection(result, receipt):
    try:
        return fixed_override_projection(result, receipt)
    except ValueError as exc:
        raise FormulaWorkbookError(str(exc)) from exc


def _formula_support_from_parts(calculated, parts, links, *, fixed_projection=None):
    """No reusable approval token: caller owns one synchronous preparation."""
    plan, template, result, receipt = parts
    declared = bool(plan.get("mechanics_model") or calculated.get("mechanics_trace")
                    or result.get("mechanics_trace"))
    _, limitations = links
    try:
        weighting = weighting_projection(template, result, declared_mechanics=declared)
    except (PlanError, ExecutionCancelled):
        raise
    except ValueError as exc:
        raise FormulaWorkbookError(str(exc)) from exc
    if weighting["has_derived_weights"] or weighting["unknown_provenance"]:
        limitations.append({"code": "special_weighting_fixed_distribution", "path": "result.special_reactions.damage_weighting",
                            "message": "特殊贡献权重对应本次联合分布；相关参数改变后回App重新排名和生成。未知依赖时整个数值基线只读。"})
    characters = {c["id"]: c for c in template["characters"]}
    for hit in template.get("hits", []):
        for bi, ei, effect in _matching_effects(template, hit):
            if effect["zone"] == "stat_pct" and "base_" + effect["stat"] not in characters[hit["character"]]["stats"]:
                limitations.append({"code": "base_stat_missing", "path": f"buffs[{bi}].effects[{ei}].value",
                                    "message": "百分比收益需要基础属性；当前零值可计算，但缺基础属性时不能承诺非零修改联动。"})
    if declared and not (plan.get("mechanics_model") or (calculated.get("report") or {}).get("mechanics_model")):
        limitations.append({"code": "mechanics_dependencies_unavailable", "path": "plan.mechanics_model",
                            "message": "声明式机制尚无工作簿数值依赖映射；可核对冻结事件的公式，修改面板或增益须重新运行引擎。"})
    support = {
        "schema": WORKBOOK_SCHEMA, "status": "partial" if limitations else "supported",
        "systems": ["classic", "transformative", "lunar", "stellar"],
        "plan_id": plan.get("plan_id"), "revision": plan.get("revision"),
        "input_fingerprint": receipt.get("input_fingerprint", fingerprint(plan)),
        "output_fingerprint": fingerprint(template), "limitations": limitations,
        "editable": {
            "character_stats": "regenerate_required" if limitations else "supported",
            "hit_multiplier": "manual_only" if plan.get("hit_parameter_sources") else "supported",
            "numeric_buff_values": "regenerate_required" if limitations else "supported",
            "declared_stat_expression": "regenerate_required" if limitations else "supported_fixed_samples",
            "enemy_numeric_stats": "supported", "energy_recharge": "invalidates_rotation_check",
            "actions_hit_count_timing": "regenerate_required", "rules_match_targets_stacks_quota": "regenerate_required",
            "character_level": "regenerate_required", "equipment": "regenerate_required",
            "app_writeback": "unsupported",
            "upstream_compiled_parameters": "regenerate_required",
            "reaction_benefit_sources": "regenerate_required",
            "passive_sources_progression_and_stat_basis": "regenerate_required",
            "elemental_profiles_initial_aura_icd_reactions": "regenerate_required",
            "database_hit_parameters": "regenerate_required",
            "database_reaction_damage": "regenerate_required",
        },
        "upstream_semantics": result.get("upstream_semantics", {}),
        "scope": "固定事件结构的工作簿局部情景；初始面板已包含套装常驻属性；不自动回写 App。",
    }
    if weighting["has_derived_weights"] or weighting["unknown_provenance"]:
        support["special_weighting"] = {"group_count": len(weighting["groups"]),
            "edit_policy": "readonly_numeric_baseline" if weighting["readonly_all"] else "regenerate_affected_contribution_group",
            "critical_column": "固定期望等价权重投影，不代表一次实际联合暴击。"}
        if not weighting["readonly_all"] and all(row["code"] == "special_weighting_fixed_distribution" for row in limitations):
            for key in ("character_stats", "numeric_buff_values", "enemy_numeric_stats"):
                support["editable"][key] = "supported_except_special_weighting_dependencies"
    if _is_execution_prefix(result):
        support['execution_coverage'] = 'executed_prefix'
        support['scope'] = '仅已执行前缀的局部伤害公式，不提供整轴DPS；补齐或改变动作后回App重新计算。'
    elemental = result.get("elemental") or {}
    if result.get('attribute_ledger'):
        support['editable']['attribute_model_and_basis'] = 'regenerate_required'
        support['attribute_sampling'] = 'readonly_execution_receipts'
        if result['attribute_ledger'].get('contract') in ('attribute-ledger-v3', 'attribute-ledger-v4'):
            support['editable'].update(character_stats='regenerate_required',numeric_buff_values='regenerate_required',
                                       declared_stat_expression='regenerate_required')
            support['attribute_edit_policy']='属性图依赖实际取样和来源审核；面板及属性来源数值固定，修改后回App重新生成。'
    if result.get('attribute_callbacks'):
        support['editable']['attribute_callback_order_and_sources'] = 'regenerate_required'
        support['attribute_callback_execution'] = 'readonly_actual_commit_receipts'
    if result.get('static_inclusion_compilations'):
        support['editable'].update(character_stats='regenerate_required',
                                   static_inclusion_sources_and_panel_policy='regenerate_required')
        support['static_inclusion_policy'] = '静态属性已包含在给定面板；来源页只读且不加数，改面板或配装后回App重算。'
    if result.get('resource_threshold_observers') or result.get('resource_threshold_triggers'):
        support['editable']['resource_thresholds_and_sources'] = 'regenerate_required'
        support['resource_threshold_execution'] = 'readonly_observations_and_actual_dispatch_receipts'
    if result.get('state_runtime'):
        support['editable']['state_runtime_lifecycle_timers'] = 'regenerate_required'
        support['state_runtime_execution'] = 'readonly_execution_receipts'
    if result.get('resource_reactions') or result.get('resource_reaction_source_adoption'):
        support['editable']['resource_reaction_rules_and_sources'] = 'regenerate_required'
        support['resource_reaction_execution'] = 'readonly_actual_reaction_resource_links'
    if result.get('resource_counters'):
        support['editable']['resource_counter_windows_and_sources'] = 'regenerate_required'
        support['resource_counter_execution'] = 'readonly_actual_spend_receipts'
    if result.get('resource_counter_views'):
        support['editable']['resource_counter_views_and_expressions'] = 'regenerate_required'
        support['resource_counter_view_execution'] = 'readonly_committed_counter_reads'
    if result.get("shared_pools"):
        support["editable"]["shared_pools_and_resource_gates"] = "regenerate_required"
        support["shared_pool_execution"] = "readonly_consumption_receipts"
    if result.get("damage_reporting"):
        support["zero_damage_projection"] = "readonly_verified_execution"
        support["editable"]["damage_reporting_contacts_and_outcomes"] = "regenerate_required"
        support["scope"] = "已验证全未命中的完整输入窗口；零伤与命中证明只读，Excel不重新判断命中或重演执行。"
    if result.get("action_capacity_compilations") or result.get("rotation_check", {}).get("action_capacity_consumption"):
        support["editable"]["action_capacity_equipment_actions_and_initial_state"] = "regenerate_required"
        support["action_capacity_execution"] = "readonly_actual_consumption_receipts"
    if result.get("action_admission_compilations") or result.get("rotation_check", {}).get("action_admission_execution"):
        support["editable"]["action_admission_selection_context_and_initial_cooldown"] = "regenerate_required"
        support["action_admission_execution"] = "readonly_actual_gate_and_commit_receipts"
    if result.get("passive_compilations"):
        providers = {r["actor"] for r in result["passive_compilations"]}
        if support["editable"]["character_stats"] == "supported":
            support["editable"]["character_stats"] = "supported_except_passive_provider_em"
        support["editable"]["passive_provider_elemental_mastery"] = "regenerate_required"
        support["fixed_character_stat_paths"] = [f"characters[{i}].stats.em" for i, c in enumerate(template["characters"]) if c["id"] in providers]
    if result.get("limited_hit_benefit_compilations"):
        current = support["editable"]["character_stats"]
        if current == "supported":
            support["editable"]["character_stats"] = "supported_except_limited_hit_provider_atk"
        elif current == "supported_except_passive_provider_em":
            support["editable"]["character_stats"] = "supported_except_source_provider_stats"
        support["editable"].update(limited_hit_benefit_sources="regenerate_required",
                                   limited_hit_provider_attack="regenerate_required",
                                   limited_hit_benefit_provenance_and_ledger="readonly_snapshot")
        paths = _limited_hit_fixed_paths(calculated, template)
        support.setdefault("fixed_character_stat_paths", []).extend(p for p in paths if p.startswith("characters["))
        support["fixed_limited_hit_parameter_paths"] = paths
        support["limited_hit_benefit"] = {"whole_source_status": "partial", "state_machine_replay": False,
                                         "provider_sampling": "current", "quota": "per_beneficiary_reset"}
    if result.get("mechanism_compilations"):
        support["editable"].update(mechanism_sources="regenerate_required",
            mechanism_source_stat_reads="regenerate_required", mechanism_provenance_and_ledger="readonly_snapshot")
        if support["editable"].get("character_stats", "").startswith("supported"):
            support["editable"]["character_stats"] = "supported_except_source_stat_reads"
        paths = _mechanism_fixed_paths(calculated, template)
        support.setdefault("fixed_character_stat_paths", []).extend(p for p in paths if p.startswith("characters["))
        support["fixed_mechanism_parameter_paths"] = paths
        support["mechanisms"] = {"whole_source_status": "partial", "state_machine_replay": False}
    if result.get("fixed_effect_sources"):
        support["editable"].update(target_condition_sources="regenerate_required", target_condition_trace="readonly_snapshot")
    if result.get('snapshot_captures'):
        support['editable'].update(snapshot_capture_timing_and_references='regenerate_required',
                                   snapshot_capture_trace='readonly_snapshot')
        support['snapshot_captures'] = {'state_machine_replay': False,
            'numeric_scope': 'fixed_event_membership_only', 'timing_evidence': 'declared_calibration'}
    if plan.get("action_resource_sources"):
        support["editable"].update(database_action_resources="regenerate_required",
                                   action_resource_provenance="readonly_snapshot")
        if any(binding.get("energy_delivery") == "action_start" for binding in plan["action_resource_sources"]):
            support["editable"].update(automatic_energy_delivery="regenerate_required",
                                       automatic_energy_audit="readonly_snapshot")
            support["automatic_energy_delivery"] = {
                "phase": "action_admission", "state_machine_replay": False,
                "energy_values": "historical_at_commit", "queued_events_generated": False}
    if plan.get("energy_generation_sources"):
        support["editable"].update(energy_generation_sources="regenerate_required",
                                   energy_generation_provenance_and_ledger="readonly_snapshot")
        support["energy_generation"] = {"dispatch_mode": "calibrated_reference", "whole_source_status": "partial",
                                        "state_machine_replay": False, "hitlag_policy": "zero_hitlag_reference"}
    if plan.get("action_event_profiles"):
        support["editable"].update(action_event_profiles_bindings_overrides="regenerate_required",
                                   action_event_provenance="readonly_snapshot")
        support["action_events"] = {"dispatch_mode": "calibrated", "skill_coverage": "partial",
                                    "resource_coverage": "not_generated", "state_machine_replay": False}
    if plan.get("attack_metadata_sources"):
        support["editable"].update(attack_metadata_sources="regenerate_required",
                                   attack_metadata_provenance="readonly_snapshot")
    if plan.get("periodic_attack_sources"):
        support["editable"].update(periodic_attack_sources="regenerate_required",
                                   periodic_attack_provenance="readonly_snapshot")
    if elemental.get("ruleset") == "gauge-freeze-reference-v1":
        support["editable"].update(elemental_initial_state_contact_reference="regenerate_required",
                                   elemental_state_trace="readonly_snapshot")
        support["elemental_reference"] = {"ruleset": elemental["ruleset"],
            "evidence_tier": elemental.get("evidence_tier", "not_provided"),
            "state_integration": "not_replayed_in_workbook",
            "scope": "实验参考模型；固定反应与状态流水，不代表游戏真值。"}
    if result.get("external_damage"):
        support["status"] = "partial"
        support["editable"].update(external_damage_supplements="regenerate_required",
                                   combined_calculation_totals="regenerate_required")
        support["external_damage"] = {"rows": "fixed_external_values", "state_machine_replay": False,
                                      "combined_result_after_edits": "regenerate_required",
                                      "engine_numeric_scope": "existing_local_formula_support"}
        support["scope"] += " 外部补充为固定数值；输入改变后含外部项的合计须回 App / Agent 重新复核并生成。"
    fixed = fixed_projection or _fixed_value_projection(result, receipt)
    if fixed["rows"]:
        support["status"] = "partial"
        support["editable"]["computed_event_fixed_values"] = "regenerate_required"
        support["computed_value_overrides"] = fixed_override_support(fixed)
        support["scope"] += " 单事件固定补正计入有效总量；自动公式仅作基线对照，输入改变后回 App / Agent 重新生成。"
    return support


def _matching_effects(template, hit):
    """Freeze structural membership, never freeze the matched numeric values."""
    timestamp = hit.get("cast_t", hit["t"]) if hit.get("snapshot") else hit["t"]
    frontline = template["characters"][0]["id"]
    for switch in sorted(template.get("switches", []), key=lambda item: item["t"]):
        if switch["t"] <= timestamp:
            frontline = switch["character"]
    for bi, buff in enumerate(template.get("buffs", [])):
        if not buff["start"] <= timestamp < buff["end"]:
            continue
        applies = (buff.get("target") is None or buff["target"] == hit["character"]) and (
            buff.get("scope", "team") == "team" or frontline == hit["character"])
        for ei, effect in enumerate(buff["effects"]):
            if template["version"] == 2 and (not effect_matches_hit(effect, hit) or not _consumes_effect(hit, effect)):
                continue
            zone, stat = effect["zone"], effect["stat"]
            if zone in ENEMY_ZONES:
                if zone == "res_shred" and stat not in ("all", hit["element"]):
                    continue
            elif not applies:
                continue
            elif zone in ("stat_add", "stat_pct"):
                scales = hit["damage_system"] == "classic" or (
                    hit["damage_system"] in ("lunar", "stellar") and not hit.get("reaction_base", False))
                if not scales or stat != hit.get("scaling_stat", "atk"):
                    continue
            elif zone == "dmg_bonus" and stat not in ("all", hit["element"]) or zone == "reaction_bonus" and stat not in ("all", (hit.get("reaction") or {}).get("type", "")):
                continue
            yield bi, ei, effect


class _Projection:
    def __init__(self, calculated, *, _session):
        self.calculated = calculated
        parts = _unpack(calculated, _session=_session)
        self.plan, self.template, self.result, self.receipt = parts
        self.fixed_values = _fixed_value_projection(self.result, self.receipt)
        links = _mechanics_links_from_parts(calculated, parts)
        self.support = _formula_support_from_parts(calculated, parts, links, fixed_projection=self.fixed_values)
        self.weighting = weighting_projection(self.template, self.result,
            declared_mechanics=bool(self.plan.get("mechanics_model") or calculated.get("mechanics_trace") or self.result.get("mechanics_trace")))
        self.summary = _Sheet("汇总", [27, 48, 70, 22, 32, 50])
        self.inputs = _Sheet("参数", [54, 34, 20, 20, 25, 65, 15, 15, 15, 15])
        self.events = _Sheet("逐事件", [23, 17, 25] + [17] * 28, hidden_columns=(30, 31))
        self.sources = _Sheet("来源与范围", [40, 85, 68, 18])
        self.audit_sources = _Sheet("完整来源JSON", [40, 12, 100])
        self.audit_sources.append(["来源键", "分块序号", "紧凑JSON正文（按来源键及序号连接，可核对来源索引SHA256）"], style=1)
        self.sheets = [self.summary, self.inputs, self.events, self.sources]
        self.all_external_items = (self.result.get("external_damage") or {}).get("items", [])
        from genshin_app.equipment.damage_report_scope import (
            report_includes,
            reporting_window,
        )
        window = reporting_window(self.result)
        self.external_items = [item for item in self.all_external_items
            if window is None or report_includes(window, item["t"])]
        self.provisional_receipt = provisional_usage_receipt(
            self.result, self.receipt,
        )
        self.formal_audit = formal_calculation_audit_payload(
            self.result, self.receipt, _session=_session,
        )
        # Dense workbook-local integers preserve full native string identity.
        # They are not hashes, case-folded IDs, or numeric conversions of IDs.
        self.owner_keys = {}
        for char in self.template["characters"]:
            cid = char.get("id")
            if not isinstance(cid, str) or not cid or cid in self.owner_keys:
                raise FormulaWorkbookError(f"角色 ID 无效或重复：{cid!r}。")
            self.owner_keys[cid] = len(self.owner_keys) + 1
        for kind, items in (("引擎事件", self.template["hits"]), ("外部补充", self.external_items)):
            for item in items:
                cid = item.get("character")
                if not isinstance(cid, str) or cid not in self.owner_keys:
                    raise FormulaWorkbookError(f"{kind}的角色 ID 未声明：{cid!r}。")
        self.owner_totals = {}
        self.external_owner_totals = {}
        self.refs, self.input_rows, self.structural_rows, self.er_rows = {}, [], [], []
        self.weighting_guards = []
        self.fixed_value_sheet = None
        if self.fixed_values["rows"]:
            self.fixed_value_sheet = _Sheet("固定事件补正", [24, 24, 24, 24, 24, 18, 45, 50, 45, 65, 65, 18], hidden_columns=(12,))
            self.fixed_value_sheet.append([])
            self.fixed_value_sheet.append(["单事件固定值与自动公式基线"], style=4)
            self.fixed_value_sheet.append(["固定值决定本次伤害；自动公式只作对照，不从固定值反推倍率。输入改变后回App/Agent重新生成。"])
            self.fixed_value_sheet.append(["事件 ID", "补正 ID", "自动公式期望（对照）", "自动暴击（对照）", "本次固定期望",
                                           "精度", "方法", "不确定性", "覆盖键", "来源指纹", "修改要求", "固定值已改"], style=1)
        self.weight_guard_cache = {}
        self.weight_dependencies = None
        self.rows_by_path = {}
        catalog = _captured_display_catalog(calculated)
        display_input = self.plan if "manual_template" in self.plan else self.template
        self.names = display_names(display_input, catalog) if catalog is not None else display_names(self.template)
        self.mechanics_links, _ = links
        self.mechanics_model = (calculated.get("report") or {}).get("mechanics_model") or self.plan.get("mechanics_model")
        self.freeze_reference = (self.result.get("elemental") or {}).get("ruleset") == "gauge-freeze-reference-v1"
        self.automatic_sources = {b["binding_id"] + ":upstream" for b in self.plan.get("effect_bindings", [])
                                  if "upstream_recipe" in b}
        automatic_bindings = {b["binding_id"] for b in self.plan.get("effect_bindings", []) if "upstream_recipe" in b}
        for record in (calculated.get("report") or {}).get("upstream_compilations", []):
            if record.get("status") == "ready" and record.get("binding_id") in automatic_bindings:
                self.automatic_sources.update(record["binding_id"] + ":" + part["part_id"] for part in record.get("parts", []))
        for record in (calculated.get("report") or {}).get("passive_compilations", []):
            if record.get("status") == "ready":
                self.automatic_sources.update(rule["source_id"] for rule in record.get("rules", []))
        for record in (calculated.get("report") or {}).get("limited_hit_benefit_compilations", []):
            if record.get("status") == "ready":
                self.automatic_sources.update(rule["source_id"] for rule in record.get("rules", []))
        for record in (calculated.get("report") or {}).get("mechanism_compilations", []):
            if record.get("status") == "ready":
                self.automatic_sources.update(rule["source_id"] for rule in record.get("rules", []))
        self.limited_hit_fixed_paths = set(_limited_hit_fixed_paths(calculated, self.template))
        self.mechanism_fixed_paths = set(_mechanism_fixed_paths(calculated, self.template))
        self.automatic_hit_parameters = {binding["hit_id"] for binding in self.plan.get("hit_parameter_sources", [])}
        self.action_resource_records = self.result.get("action_resource_compilations") or []
        self.energy_generation_records = self.result.get("energy_generation_compilations") or []
        self.limited_hit_benefit_records = self.result.get("limited_hit_benefit_compilations") or []
        self.mechanism_records = self.result.get("mechanism_compilations") or []
        self.action_event_records = self.result.get("action_event_compilations") or []
        self.attack_metadata_records = self.result.get("attack_metadata_compilations") or []
        self.periodic_attack_records = self.result.get("periodic_attack_compilations") or []
        self.reaction_benefit_records = self.result.get("reaction_benefit_compilations") or []
        self.passive_records = self.result.get("passive_compilations") or []
        self.automatic_hit_parameters.update(row["prototype_hit_id"] for row in self.attack_metadata_records)
        self.automatic_hit_parameters.update(channel["prototype_hit_id"] for record in self.periodic_attack_records
                                             for channel in record.get("channels", []))
        for pattern in (self.mechanics_model or {}).get("hit_patterns", []):
            if pattern["prototype_hit_id"] in self.automatic_hit_parameters:
                self.automatic_hit_parameters.update(f"{pattern['id']}:{i+1}" for i in range(len(pattern["offsets"])))
        self.mechanics_input = (calculated.get("report") or {}).get("mechanics_input_template") or {}
        self.inputs.append([])
        self.inputs.append(["可编辑参数与固定结构"], style=4)
        self.inputs.append(["黄色为可编辑数值；灰色为固定结构。固定结构改变后回 App 重新生成。"])
        self.inputs.append(["字段路径", "中文含义", "当前值", "导出原值", "修改范围", "来源或说明",
                            "数值已改", "输入无效", "结构已改", "充能已改"], style=1)

    def parameter(self, path, label, value, *, editable=True, scope="数值公式重算", note="", minimum=None):
        from genshin_app.formula_sources import local_numeric_edit
        # A complete resolved contribution proof fixes only its actual group
        # dependencies. Reuse the same group owner for editability and guards;
        # other limitations do not gain this numeric-only permission.
        resolved_weighting = (bool(self.weighting["groups"]) and not self.weighting["readonly_all"]
            and all(row["code"] == "special_weighting_fixed_distribution" for row in self.support["limitations"])
            and not self.mechanics_model
            and not any(self.result.get(key) for key in (
                "attribute_ledger", "attribute_callbacks", "static_inclusion_compilations",
                "state_runtime", "shared_pools", "passive_compilations",
                "limited_hit_benefit_compilations", "mechanism_compilations",
                "resource_threshold_observers", "resource_threshold_triggers", "resource_reactions",
                "resource_reaction_source_adoption", "resource_counters", "resource_counter_views")))
        if self.support["limitations"] and not local_numeric_edit(path, resolved_weighting=resolved_weighting):
            editable = False
            scope = "回 App 重新生成"
            note = "当前执行包含固定取样或未映射的状态依赖；该参数没有独立局部改算证明。"
        if self._weighted_parameter(path):
            editable = False
            scope = "回 App 重新生成"
            note = "参数影响特殊贡献组联合分布；旧权重不能重用。权重依赖不完整时仅保留只读数值基线。"
        if self.result.get('static_inclusion_compilations') and path.startswith('characters['):
            editable = False
            scope = '回 App 重新生成'
            note = '静态来源绑定给定面板与配装政策；改变后须回App重新核对，不沿用旧已含证明。'
        if ((self.result.get('attribute_ledger') or {}).get('contract') in ('attribute-ledger-v3', 'attribute-ledger-v4')
                and path.startswith(('characters[','buffs[','mechanics_model.rules[','mechanics_input.buffs['))):
            editable=False
            scope='回 App 重新生成'
            note='属性图取样与来源回执已固定；修改面板、基础攻击、来源数值或表达式后须回App重算，不能沿用旧回执。'
        if path in self.mechanism_fixed_paths:
            editable = False
            scope = "回 App 重新生成"
            note = "该值影响通用来源的属性取样；改变后需回App重算，不沿用历史触发与次数结论。"
        if path in self.limited_hit_fixed_paths:
            editable = False
            scope = "回 App 重新生成"
            note = "该值影响提供者当前攻击力取样；改变后需回App重算，不沿用历史取样和次数结论。"
        computed = isinstance(value, _Expr)
        expression = value.text if computed else None
        value = value.value if computed else value
        if not editable and scope == "数值公式重算":
            scope = "回 App 重新生成"
        if computed:
            scope = "由来源参数公式计算"
        numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
        row = len(self.inputs.rows) + 1
        er = path.endswith(".energy_recharge")
        invalid = f"NOT(ISNUMBER(C{row}))" if numeric else "FALSE"
        if numeric and (".stats." in path and ".dmg_bonus." not in path or path.endswith(".multiplier") or path.startswith("enemy.defense")):
            invalid = f"OR({invalid},C{row}<0)"
        if path == "enemy.level":
            invalid = f"OR({invalid},C{row}<=0,MOD(C{row},1)<>0)"
        if path.endswith(".reaction.factor"):
            invalid = f"OR({invalid},C{row}<=0)"
        if path.endswith(".stats.crit_rate"):
            invalid = f"OR({invalid},C{row}>3)"
        if minimum is not None:
            invalid = f"OR({invalid},C{row}<{minimum})"
        self.inputs.append([path, label, _Cell(value, formula=expression, style=3 if computed else (2 if editable else 5)),
                            _Cell(value, style=5), scope, note,
                            _Cell(0, f"IF(C{row}<>D{row},1,0)"), _Cell(0, f"IF({invalid},1,0)"),
                            _Cell(0, f"IF(C{row}<>D{row},1,0)" if not editable and not computed else "0"),
                            _Cell(0, f"IF(C{row}<>D{row},1,0)" if er else "0")])
        self.input_rows.append(row)
        self.rows_by_path[path] = row
        if not editable and not computed:
            self.structural_rows.append(row)
        if er:
            self.er_rows.append(row)
        if numeric:
            self.refs[path] = self.inputs.reference(row, 3)
            return self.refs[path]
        return value

    def ref(self, path, default=0):
        return self.refs.get(path, _number(default))

    def _weighted_parameter(self, path, members=None):
        if self.weighting["readonly_all"]:
            # Unknown state dependencies keep contributor inputs frozen. A
            # scalar on a separately identified ordinary hit cannot change
            # the joint outcomes: no damage is fed back into the reaction
            # participant, attribute, gauge, timing or callback projection.
            # Unknown weighting provenance cannot establish that separation.
            if not self.weighting["unknown_provenance"]:
                weighted = self.weighting["hit_group"]
                for index, hit in enumerate(self.template["hits"]):
                    if path == f"hits[{index}].multiplier" and hit["id"] not in weighted:
                        return False
            return True
        groups = [g for g in self.weighting["groups"] if members is None or members in g["hit_ids"]]
        for group in groups:
            if path.startswith("enemy."):
                return True
            for i, char in enumerate(self.template["characters"]):
                if char["id"] in group["actors"] and path.startswith(f"characters[{i}]."):
                    return True
            for i, hit in enumerate(self.template["hits"]):
                if hit.get("id") in group["hit_ids"]:
                    if path.startswith(f"hits[{i}]."):
                        return True
                    for bi, ei, _ in _matching_effects(self.template, hit):
                        if path.startswith(f"buffs[{bi}].effects[{ei}]."):
                            return True
        return False

    def _weight_guard(self, hit_id):
        if hit_id not in self.weighting["hit_group"] and not self.weighting["readonly_all"]:
            return None
        identity = (("unknown-dependencies", "all") if self.weighting["readonly_all"]
                    else self.weighting["hit_group"][hit_id])
        if identity in self.weight_guard_cache:
            return self.weight_guard_cache[identity]
        paths = [path for path in self.rows_by_path if self._weighted_parameter(path, hit_id)]
        if self.weight_dependencies is None:
            self.weight_dependencies = _Sheet("贡献权重依赖", [45, 55, 18, 22])
            self.weight_dependencies.append(["贡献组", "参数路径", "输入行", "是否修改"], style=1)
            self.sheets.append(self.weight_dependencies)
        first = len(self.weight_dependencies.rows) + 1
        for path in paths:
            input_row = self.rows_by_path[path]
            self.weight_dependencies.append([str(identity), path, input_row,
                _Cell(0, f"'参数'!G{input_row}", 5)])
        last = len(self.weight_dependencies.rows)
        row = last + 1
        self.weight_dependencies.append([str(identity), "本组任一依赖修改后重新计算", "",
            _Cell(0, f"SUM(D{first}:D{last})" if paths else "0", 5)])
        guard = f"'贡献权重依赖'!D{row}>0"
        self.weight_guard_cache[identity] = guard
        return guard

    def prepare_inputs(self):
        t = self.template
        self.parameter("loop_duration", "循环时长（秒）", t.get("loop_duration") or 0, editable=False)
        self.parameter("enemy.level", "敌人等级", t["enemy"]["level"])
        for key in ("defense_reduction", "defense_ignore"):
            self.parameter(f"enemy.{key}", "敌方基础减防" if key.endswith("reduction") else "敌方基础无视防御", t["enemy"].get(key, 0))
        for element in ["default", *_ELEMENT_NAMES.keys()]:
            if element == "all":
                continue
            value = t["enemy"].get("resistance", {"default": 0.1})
            if element in value:
                self.parameter(f"enemy.resistance.{element}", ("默认" if element == "default" else _ELEMENT_NAMES[element]) + "抗性", value[element])
        for ci, char in enumerate(t["characters"]):
            name = self.names["characters"].get(char["id"], char["id"])
            prefix = f"characters[{ci}]"
            self.parameter(f"{prefix}.level", f"{name}：等级", char["level"], editable=False)
            stats = char["stats"]
            for key in dict.fromkeys([*stats, "atk", "hp", "def", "em", "crit_rate", "crit_dmg"]):
                if key == "dmg_bonus":
                    continue
                value = stats.get(key, 0)
                if isinstance(value, (int, float)):
                    fixed_passive_em = key == "em" and any(r["actor"] == char["id"] for r in self.passive_records)
                    self.parameter(f"{prefix}.stats.{key}", f"{name}：{_STAT_NAMES.get(key, key)}", value,
                                   editable=not fixed_passive_em,
                                   scope="修改后需重新检查循环" if key == "energy_recharge" else "数值公式重算",
                                   note="该固定精通已纳入固有来源采用，变更须回App复核组成并重算。" if fixed_passive_em else "给定面板已包含套装常驻属性；不重复加入两件套。")
            for element in dict.fromkeys([*(stats.get("dmg_bonus") or {}), "all", *{h["element"] for h in t["hits"] if h["character"] == char["id"]}]):
                self.parameter(f"{prefix}.stats.dmg_bonus.{element}", f"{name}：{_ELEMENT_NAMES.get(element, element)}伤害加成", (stats.get("dmg_bonus") or {}).get(element, 0))
        if self.mechanics_model:
            self.prepare_mechanics_inputs()
            if self.mechanics_model.get('snapshot_captures'):
                self.prepare_snapshot_capture_inputs()
        if self.action_resource_records:
            self.prepare_action_resource_inputs()
        if self.energy_generation_records:
            self.prepare_energy_generation_inputs()
        if self.limited_hit_benefit_records:
            self.prepare_limited_hit_benefit_inputs()
        if self.mechanism_records:
            self.prepare_mechanism_source_inputs()
        if self.action_event_records:
            self.prepare_action_event_inputs()
        if self.result.get("damage_reporting"):
            self.prepare_damage_reporting_inputs()
        for bi, buff in enumerate(t.get("buffs", [])):
            name = buff.get("name") or buff.get("source") or buff["id"]
            for key in ("start", "end"):
                self.parameter(f"buffs[{bi}].{key}", f"{name}：{'生效' if key == 'start' else '结束'}时间", buff[key], editable=False)
            for ei, effect in enumerate(buff["effects"]):
                value = self.mechanics_value(bi, ei, effect) if (bi, ei) in self.mechanics_links else effect["value"]
                self.parameter(f"buffs[{bi}].effects[{ei}].value", f"{name}：{_ZONE_NAMES.get(effect['zone'], effect['zone'])}", value,
                               editable=not self.mechanics_model, minimum=0 if effect["zone"] in MULTIPLICATIVE_ZONES else None,
                               note=f"{buff['id']} / {effect.get('id', ei)}；受益范围与匹配事件固定。")
        for hi, hit in enumerate(t["hits"]):
            label = hit.get("hit_name") or hit.get("skill_name") or hit["skill"]
            for key in ("t", "count"):
                self.parameter(f"hits[{hi}].{key}", f"{label}：{'命中时间' if key == 't' else '次数'}", hit.get(key, 1), editable=False)
            automatic = hit["id"] in self.automatic_hit_parameters
            self.parameter(f"hits[{hi}].multiplier", f"{label}：倍率", hit.get("multiplier", 0), editable=not automatic,
                           note="数据库参数来源；更改选择、等级或数值须回App重跑并重新导出，用途为方案显式声明。" if automatic else "")
            reaction = hit.get("reaction") or {}
            if "factor" in reaction:
                self.parameter(f"hits[{hi}].reaction.factor", f"{label}：反应系数", reaction["factor"])

    def prepare_snapshot_capture_inputs(self):
        # Expose structural guards without turning every saved ledger row into
        # an editable numeric input. Fixed-membership formulas remain available.
        values = [('mechanics_model.snapshot_captures', self.mechanics_model['snapshot_captures'])]
        for field in ('hit_events', 'hit_patterns'):
            for index, item in enumerate(self.mechanics_model.get(field, [])):
                if item.get('self_snapshot_event'):
                    values.append((f'mechanics_model.{field}[{index}].self_snapshot_event', item['self_snapshot_event']))
        for path, value in values:
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
            for offset in range(0, max(1, len(encoded)), 15000):
                self.parameter(f'{path}.chunk[{offset // 15000}]', '面板取样固定声明或引用',
                    encoded[offset:offset + 15000], editable=False,
                    note='取样时点、父动作与使用关系改变后回App重算；取样依据不等于游戏实测证明。')

    def prepare_damage_reporting_inputs(self):
        # Keep the proof as exact fixed input, without inventing damage rows or
        # rerunning contact logic. Chunk by code points within Excel cell limits.
        proof = self.result["damage_reporting"]
        for key, value in proof.items():
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
            for index in range(0, len(encoded), 15000):
                self.parameter(f"damage_reporting.{key}.chunk[{index // 15000}]", "固定伤害报告证明", encoded[index:index + 15000],
                               editable=False, note="命中声明、时点或实际执行变更后须回App重算；Excel不重新判定未命中。")

    def prepare_action_event_inputs(self):
        def fixed(path, value):
            if isinstance(value, dict) and value:
                for key, child in value.items():
                    fixed(path + "." + key, child)
            elif isinstance(value, list) and value:
                for index, child in enumerate(value):
                    fixed(f"{path}[{index}]", child)
            else:
                if value is None or isinstance(value, (bool, dict, list)):
                    value = json.dumps(value, ensure_ascii=False, sort_keys=True)
                parts = [value[i:i + 15000] for i in range(0, len(value), 15000)] if isinstance(value, str) and len(value) > 15000 else [value]
                for index, part in enumerate(parts):
                    self.parameter(path + (f".part[{index}]" if len(parts) > 1 else ""), "动作档案与派发固定结构", part,
                                   editable=False, note="校准声明；修改后须回App重算，Excel不重新派发事件。")
        for field in ("action_event_profiles", "action_event_bindings", "action_event_overrides"):
            fixed(field, self.plan.get(field, []))
        if self.plan.get("attack_metadata_sources"):
            fixed("attack_metadata_sources", self.plan["attack_metadata_sources"])
        if self.plan.get("periodic_attack_sources"):
            fixed("periodic_attack_sources", self.plan["periodic_attack_sources"])
        actions = {row["action_id"] for record in self.action_event_records for row in record["generated"]}
        abilities = {row["ability_id"] for record in self.action_event_records for row in record["generated"]}
        model = self.plan.get("rotation_model") or {}
        for category, identities in (("actions", actions), ("abilities", abilities)):
            for index, value in enumerate(model.get(category, [])):
                if value["id"] in identities:
                    fixed(f"rotation_model.{category}[{index}]", value)
        fixed("rotation_model.period", model.get("period"))
        fixed("rotation_model.max_cycles", model.get("max_cycles"))

    def prepare_action_resource_inputs(self):
        def fixed(path, value):
            if isinstance(value, dict):
                for key, child in value.items():
                    fixed(path + "." + key, child)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    fixed(f"{path}[{index}]", child)
            else:
                text = "未提供" if value is None else value
                parts = [text[i:i + 15000] for i in range(0, len(text), 15000)] if isinstance(text, str) and len(text) > 15000 else [text]
                for index, part in enumerate(parts):
                    self.parameter(path + (f".part[{index}]" if len(parts) > 1 else ""), "动作资源固定声明或编译值", part,
                                   editable=False, note="用途由方案声明；来源、身份、冷却或扣能映射改变后回App重算。")
        fixed("action_resource_sources", self.plan["action_resource_sources"])
        for index, record in enumerate(self.action_resource_records):
            fixed(f"action_resource_compilations[{index}].compiled_values", record["compiled_values"])
        # Keep native timing/identities visible without backfilling its missing
        # numbers. Only scope relevant objects are added; ER stays a panel input.
        model = self.plan.get("rotation_model") or {}
        abilities = {r["ability_id"] for r in self.action_resource_records}
        action_ids = {link["action_id"] for r in self.action_resource_records for link in r["action_spend_links"]}
        action_ids.update(link["action_id"] for r in self.action_resource_records
                          for link in (r.get("energy_delivery") or {}).get("links", []))
        event_ids = {link["spend_id"] for r in self.action_resource_records for link in r["action_spend_links"]}
        event_ids.update(m["id"] for r in self.action_resource_records for m in r.get("explicit_modifiers", []))
        for category, identities in (("abilities", abilities), ("actions", action_ids), ("events", event_ids)):
            for index, item in enumerate(model.get(category, [])):
                if item["id"] in identities:
                    fixed(f"rotation_model.{category}[{index}]", item)
        if any(r.get("energy_delivery") for r in self.action_resource_records):
            # These values govern admission and the reported check horizon.
            # Keep them visible and immutable; changing ER remains a panel input
            # that invalidates the historical energy check, as before.
            for key in ("period", "max_cycles", "initial_frontline", "initial_cooldowns"):
                if key in model:
                    fixed("rotation_model." + key, model[key])
            for index, actor in enumerate(model.get("characters", [])):
                for key in ("id", "energy_capacity", "initial_energy", "element"):
                    if key in actor:
                        fixed(f"rotation_model.characters[{index}].{key}", actor[key])

    def prepare_energy_generation_inputs(self):
        def fixed(path, value):
            if isinstance(value, dict) and value:
                for key, child in value.items():
                    fixed(path + "." + key, child)
            elif isinstance(value, list) and value:
                for index, child in enumerate(value):
                    fixed(f"{path}[{index}]", child)
            else:
                text = json.dumps(value, ensure_ascii=False) if value is None or isinstance(value, (bool, dict, list)) else value
                parts = [text[i:i + 15000] for i in range(0, len(text), 15000)] if isinstance(text, str) and len(text) > 15000 else [text]
                for index, part in enumerate(parts):
                    target = path + (f".part[{index}]" if len(parts) > 1 else "")
                    self.parameter(target, "技能产球固定输入或来源值", part, editable=False,
                                   note="改变后回App重算；本工作簿不重演命中、共享状态和收取队列。")
        fixed("energy_generation_sources", self.plan["energy_generation_sources"])
        for index, record in enumerate(self.energy_generation_records):
            for key in ("resource_projection", "trigger_certificate", "parent_links", "migration"):
                fixed(f"energy_generation_compilations[{index}].{key}", record.get(key))

    def prepare_limited_hit_benefit_inputs(self):
        def fixed(path, value):
            if isinstance(value, dict) and value:
                for key, child in value.items():
                    fixed(path + "." + key, child)
            elif isinstance(value, list) and value:
                for index, child in enumerate(value):
                    fixed(f"{path}[{index}]", child)
            else:
                text = json.dumps(value, ensure_ascii=False) if value is None or isinstance(value, (bool, dict, list)) else value
                parts = [text[i:i + 15000] for i in range(0, len(text), 15000)] if isinstance(text, str) and len(text) > 15000 else [text]
                for index, part in enumerate(parts):
                    self.parameter(path + (f".part[{index}]" if len(parts) > 1 else ""), "有限次数收益固定输入或来源", part,
                                   editable=False, note="来源与规则固定；修改后回App重算激活、配额与提供者攻击力取样。")
        fixed("limited_hit_benefit_sources", self.plan["limited_hit_benefit_sources"])
        for index, record in enumerate(self.limited_hit_benefit_records):
            for key in ("benefit_certificate", "parent_links", "initial_rules", "scope"):
                fixed(f"limited_hit_benefit_compilations[{index}].{key}", record.get(key))

    def prepare_mechanism_source_inputs(self):
        def fixed(path, value):
            if isinstance(value, dict) and value:
                for key, child in value.items():
                    fixed(path + "." + key, child)
            elif isinstance(value, list) and value:
                for index, child in enumerate(value):
                    fixed(f"{path}[{index}]", child)
            else:
                text = json.dumps(value, ensure_ascii=False) if value is None or isinstance(value, (bool, dict, list)) else value
                parts = [text[i:i + 15000] for i in range(0, len(text), 15000)] if isinstance(text, str) and len(text) > 15000 else [text]
                for index, part in enumerate(parts):
                    self.parameter(path + (f".part[{index}]" if len(parts) > 1 else ""), "通用机制固定输入或受审资料", part,
                        editable=False, note="修改后回App重新计算；完整来源与实际状态流水见通用机制来源页。")
        fixed("mechanism_sources", self.plan["mechanism_sources"])
        for index, record in enumerate(self.mechanism_records):
            for key in ("source_fingerprint", "authorization_fingerprint", "acceptance_fingerprint", "execution_fingerprint", "parent_graph", "scope", "rules", "initial_rules"):
                fixed(f"mechanism_compilations[{index}].{key}", record.get(key))

    def prepare_mechanics_inputs(self):
        if self.freeze_reference:
            self.prepare_freeze_inputs()
        self.prepare_fixed_inputs()
        if "shared_pools" in self.mechanics_model:
            # Fixed execution context, not an editable balance simulation.
            encoded = json.dumps(self.mechanics_model["shared_pools"], ensure_ascii=False, sort_keys=True)
            for offset in range(0, max(1, len(encoded)), 15000):
                self.parameter(f"mechanics_model.shared_pools.chunk[{offset // 15000}]", "共享资源池与补充/消费规则",
                    encoded[offset:offset + 15000], editable=False, note="改变资源声明、消费门控或来源审核后回App重算。")
        for wi, window in enumerate((self.plan.get("rotation_model") or {}).get("energy_recharge_windows", [])):
            for key in ("start", "end", "delta"):
                self.parameter(f"rotation_model.energy_recharge_windows[{wi}].{key}", "临时充能区间：" + {"start": "开始", "end": "结束", "delta": "加算倍率"}[key],
                               window[key], editable=key == "delta", note=window.get("source", window["id"]))
        for bi, buff in enumerate(self.mechanics_input.get("buffs", [])):
            label = buff.get("name") or buff.get("source") or buff["id"]
            for ei, effect in enumerate(buff["effects"]):
                self.parameter(f"mechanics_input.buffs[{bi}].effects[{ei}].value", label + "：原始增益数值", effect["value"],
                               editable=buff.get("extensions", {}).get("source_id") not in self.automatic_sources,
                               minimum=0 if effect["zone"] in MULTIPLICATIVE_ZONES else None,
                               note=f"{buff['id']} / {effect['id']}；共享给所有已采样命中。")
        for ri, rule in enumerate(self.mechanics_model.get("rules", [])):
            prefix = f"mechanics_model.rules[{ri}]"
            label = rule.get("name_zh") or rule["id"]
            for group in ("timing", "stacking", "quota"):
                for key, value in rule.get(group, {}).items():
                    if isinstance(value, (int, float)):
                        self.parameter(f"{prefix}.{group}.{key}", f"{label}：{group}.{key}", value, editable=False)
            for ei, effect in enumerate(rule["effects"]):
                ep = f"{prefix}.effects[{ei}].value"
                value = effect["value"]
                if isinstance(value, (int, float)):
                    self.parameter(ep, label + "：原始收益数值", value, note=rule.get("source_id", ""),
                                   editable=rule.get("source_id") not in self.automatic_sources,
                                   minimum=0 if BENEFIT_TYPES.get(effect["benefit_type_id"], {}).get("value_semantics") == "multiply" else None)
                else:
                    if value.get('kind')=='counter_view':
                        self.parameter(ep,label+'：资源读数表达式',json.dumps(value,ensure_ascii=False,sort_keys=True),
                                       editable=False,note='实际累计取样与层数合成值已固定；修改后回App重算。')
                        continue
                    for key in ("factor", "add", "min", "max"):
                        if key in value or key == "add":
                            self.parameter(f"{ep}.{key}", label + "：" + {"factor": "属性比例", "add": "固定加算", "min": "数值下限", "max": "数值上限"}[key],
                                           value.get(key, 0), editable=rule.get("source_id") not in self.automatic_sources,
                                           note=f"{rule['id']} / {effect['id']}；采样对象和事件固定。")
                    if "min" in value and "max" in value:
                        lower_row = self.rows_by_path[ep + ".min"]
                        cell = self.inputs.rows[lower_row - 1][7]
                        cell.formula = f"IF(OR({cell.formula}>0,{self.ref(ep + '.min').text}>{self.ref(ep + '.max').text}),1,0)"

    def prepare_fixed_inputs(self):
        def fixed(path, value):
            if isinstance(value, dict) and value:
                for key, child in value.items():
                    fixed(path + "." + key, child)
            elif isinstance(value, list) and value:
                for index, child in enumerate(value):
                    fixed(f"{path}[{index}]", child)
            else:
                if value is None or isinstance(value, (bool, dict, list)):
                    value = json.dumps(value, ensure_ascii=False, sort_keys=True)
                self.parameter(path, "固定目标条件来源", value, editable=False,
                               note="来源常数与条件树固定；逐击目标状态由引擎采样，修改后回App重算。")
        for index, source in enumerate(self.mechanics_model.get("fixed_effect_sources", [])):
            fixed(f"mechanics_model.fixed_effect_sources[{index}]", source)

    def prepare_freeze_inputs(self):
        """Expose fixed state/contact/reference leaves with structural guards."""
        def fixed(path, value):
            if isinstance(value, dict) and value:
                for key, child in value.items():
                    fixed(path + "." + key, child)
            elif isinstance(value, list) and value:
                for index, child in enumerate(value):
                    fixed(f"{path}[{index}]", child)
            else:
                if value is None or isinstance(value, (bool, dict, list)):
                    value = json.dumps(value, ensure_ascii=False, sort_keys=True)
                self.parameter(path, "实验冻结参考：固定输入", value, editable=False,
                               note="实验参考，非游戏真值；改变初态、附着、接触或来源确认必须回App重算，不在Excel重演ODE。")

        fixed("mechanics_model.elemental_model", self.mechanics_model.get("elemental_model", {}))
        for name in ("hit_events", "hit_patterns", "application_events"):
            for index, descriptor in enumerate(self.mechanics_model.get(name, [])):
                if name == "application_events":
                    fixed(f"mechanics_model.{name}[{index}]", descriptor)
                else:
                    for field in ("contact", "application"):
                        if field in descriptor:
                            fixed(f"mechanics_model.{name}[{index}].{field}", descriptor[field])
        for name in ("elemental_sources", "reaction_damage_sources"):
            if name in self.plan:
                fixed(name, self.plan[name])

    def mechanics_value(self, bi, ei, projected):
        link = self.mechanics_links[(bi, ei)]
        if link["kind"] == "manual":
            source_bi, source_ei, _ = link["source"]
            return self.ref(f"mechanics_input.buffs[{source_bi}].effects[{source_ei}].value")
        if link["kind"] == "fixed_effect_source":
            return self.ref(f"mechanics_model.fixed_effect_sources[{link['source_index']}].effects[{link['effect_index']}].value")
        prefix = f"mechanics_model.rules[{link['rule_index']}].effects[{link['effect_index']}].value"
        expression = link["effect"]["value"]
        if not isinstance(expression, dict):
            value = self.ref(prefix)
        else:
            sample = link["sample"]
            ci = next(i for i, c in enumerate(self.template["characters"]) if c["id"] == sample["character"])
            source = self.ref(f"characters[{ci}].stats.{sample['stat']}")
            for dep in sample["dependencies"]:
                if not _dependency_affects(dep, sample["stat"]):
                    continue
                if "buff_id" in dep:
                    dbi, buff = next((i, b) for i, b in enumerate(self.mechanics_input["buffs"]) if b["id"] == dep["buff_id"])
                    dei = next(i for i, e in enumerate(buff["effects"]) if e["id"] == dep["effect_id"])
                    amount = self.ref(f"mechanics_input.buffs[{dbi}].effects[{dei}].value")
                elif "rule_id" in dep:
                    dri, rule = next((i, r) for i, r in enumerate(self.mechanics_model["rules"]) if r["id"] == dep["rule_id"])
                    dei = next(i for i, e in enumerate(rule["effects"]) if e["id"] == dep["effect_id"])
                    if "layers" not in dep:
                        raise FormulaWorkbookError("机制依赖缺少层数，不能根据聚合值反推或冻结提供者属性。")
                    amount = self.ref(f"mechanics_model.rules[{dri}].effects[{dei}].value") * dep["layers"]
                else:
                    wi = next(i for i, window in enumerate(self.plan["rotation_model"]["energy_recharge_windows"]) if window["id"] == dep["energy_window_id"])
                    amount = self.ref(f"rotation_model.energy_recharge_windows[{wi}].delta")
                if dep["zone"] == "stat_pct":
                    amount *= self.ref(f"characters[{ci}].stats.base_{dep['stat']}")
                source += amount
            value = source * self.ref(prefix + ".factor") + self.ref(prefix + ".add")
            if "min" in expression:
                value = _bound("MAX", value, self.ref(prefix + ".min"))
            if "max" in expression:
                value = _bound("MIN", value, self.ref(prefix + ".max"))
        layers = link["record"]["layers"]
        value = value ** layers if projected["zone"] in MULTIPLICATIVE_ZONES else value * layers
        if not math.isclose(value.value, projected["value"], rel_tol=1e-10, abs_tol=1e-9):
            raise FormulaWorkbookError(f"收益 {projected['id']} 的原始参数公式与引擎采样值不一致。")
        return value

    def build_events(self):
        self.events.append([])
        self.events.append(["固定事件结构的可见乘区公式"], style=4)
        self.events.append(["事件顺序与作用关系来自本次引擎执行；黄色参数修改仅在支持范围内重算。"])
        self.events.append(["事件 ID", "角色", "伤害", "时间（秒）", "次数", "倍率", "有效属性", "元素精通",
                            "基础独立乘区", "倍率后加算", "激化固定加算", "基础伤害", "增伤区", "暴击期望区",
                            "抗性值", "抗性区", "防御区", "反应与精通区", "特殊基础乘区", "特殊独立乘区",
                            "擢升区", "期望伤害", "暴击伤害", "本次导出有效值" if self.fixed_value_sheet else "引擎导出基线", "基线差额", "支持状态", "实际收益 ID", "角色 ID", "有效值检查",
                            "角色汇总键", "原始角色 ID", "贡献分配权重", "权重说明"], style=1)
        char_indexes = {char["id"]: i for i, char in enumerate(self.template["characters"])}
        self.event_rows = []
        for hi, (hit, baseline) in enumerate(zip(self.template["hits"], self.result["hits"], strict=True)):
            if hit.get("id") is not None and baseline.get("id") != hit["id"]:
                raise FormulaWorkbookError(f"hits[{hi}] 的事件 ID 与计算结果不一致。")
            ci = char_indexes[hit["character"]]
            p, h = f"characters[{ci}].stats", f"hits[{hi}]"
            acc, names = {}, []
            for bi, ei, effect in _matching_effects(self.template, hit):
                key = effect["zone"]
                if key in ("stat_add", "stat_pct"):
                    key += ":" + effect["stat"]
                value = self.ref(f"buffs[{bi}].effects[{ei}].value")
                acc[key] = acc.get(key, _number(1)) * value if key in MULTIPLICATIVE_ZONES else acc.get(key, _number(0)) + value
                names.append(f"{self.template['buffs'][bi]['id']}/{effect.get('id', ei)}")
            get = lambda key, default=0, acc=acc: acc.get(key, _number(default))
            em = self.ref(p + ".em") + get("em")
            stat = hit.get("scaling_stat", "atk")
            scaling = (em if stat == "em" else self.ref(p + "." + stat)) + self.ref(p + ".base_" + stat) * get("stat_pct:" + stat) + get("stat_add:" + stat)
            multiplier, count = self.ref(h + ".multiplier"), self.ref(h + ".count", 1)
            level = self.ref(f"characters[{ci}].level")
            own_bonus = self.ref(p + ".dmg_bonus." + hit["element"]) + self.ref(p + ".dmg_bonus.all")
            crit_rate = _bound("MAX", self.ref(p + ".crit_rate") + get("crit_rate"), 0)
            crit_dmg = self.ref(p + ".crit_dmg") + get("crit_dmg")
            crit = 1 + _bound("MIN", crit_rate, 1) * crit_dmg
            res_values = self.template["enemy"].get("resistance", {"default": 0.1})
            res_key = hit["element"] if hit["element"] in res_values else "default"
            resistance = self.ref("enemy.resistance." + res_key, 0.1) - get("res_shred")
            r = resistance.text
            resistance_mult = _Expr(f"IF({r}<0,1-{r}/2,IF({r}<0.75,1-{r},1/(1+4*{r})))",
                                   1 - resistance.value / 2 if resistance.value < 0 else (1 - resistance.value if resistance.value < .75 else 1 / (1 + 4 * resistance.value)))
            dr = _bound("MIN", self.ref("enemy.defense_reduction") + get("def_reduction"), .9)
            di = _bound("MIN", self.ref("enemy.defense_ignore") + get("def_ignore"), 1)
            defense = (level + 100) / (level + 100 + (self.ref("enemy.level") + 100) * (1 - dr) * (1 - di))
            reaction = hit.get("reaction") or {}
            rt, system = reaction.get("type"), hit["damage_system"]
            base_mult, base_add = get("base_mult", 1), get("base_add")
            aggravate, bonus, reaction_zone, special_base, big_mult, elevate = [_number(0), _number(1), _number(1), _number(1), _number(1), _number(1)]
            rb = REACTION_BASE_VALUE.get(int(level.value))
            if system == "classic":
                if rt in AGGRAVATE_FACTOR:
                    aggravate = rb * AGGRAVATE_FACTOR[rt] * (1 + 5 * em / (em + 1200) + get("reaction_bonus"))
                base = multiplier * scaling * base_mult + base_add + aggravate
                bonus = 1 + own_bonus + get("dmg_bonus")
                if rt == "amplify":
                    reaction_zone = self.ref(h + ".reaction.factor", reaction["factor"]) * (1 + 2.78 * em / (em + 1400) + get("reaction_bonus"))
            else:
                if "factor" in reaction:
                    factor = self.ref(h + ".reaction.factor")
                elif system == "transformative":
                    factor = _number(TRANSFORMATIVE_FACTOR[rt])
                elif system == "lunar":
                    factor = _number(LUNAR_FACTOR[rt])
                else:
                    factor = _number(stellar_superconduct_factor(reaction.get("layers", 0)) if rt == "stellar_superconduct" else STELLAR_FACTOR[rt])
                defense = _number(1)
                if system == "transformative":
                    base = rb * factor
                    reaction_zone = 1 + 16 * em / (em + 2000) + get("reaction_bonus")
                    crit, base_add = _number(1), get("reaction_flat")
                else:
                    base = _number(rb) if hit.get("reaction_base") else multiplier * scaling
                    special_base = factor * (1 + get(system + "_base"))
                    reaction_zone = 1 + 6 * em / (em + 2000) + get("reaction_bonus")
                    big_mult, elevate = get(system + "_mult", 1), 1 + get(system + "_elevate")
                    base_add = get("reaction_flat")
            row = len(self.events.rows) + 1
            self.events.append([hit.get("id", f"hit-{hi}"), self.names["characters"].get(hit["character"], hit["character"]),
                                hit.get("hit_name") or hit.get("skill_name") or hit["skill"], self.ref(h + ".t"), count,
                                multiplier, scaling, em, base_mult, base_add, aggravate, base, bonus, crit,
                                resistance, resistance_mult, defense, reaction_zone, special_base, big_mult, elevate])
            ref = lambda col, row=row: self.events.reference(row, col)
            if system == "classic":
                no_crit = ref(12) * ref(13) * ref(16) * ref(17) * ref(18)
            else:
                no_crit = (ref(12) * ref(19) * ref(18) * ref(20) + ref(10)) * ref(16) * ref(21)
            weight = _number(hit.get("result_weight", 1))
            expected = no_crit * ref(14) * ref(5) * weight
            fixed = self.fixed_values["rows"].get(hit.get("id"))
            automatic_expected = fixed["automatic_expected"] if fixed else baseline["expected"]
            if not math.isclose(expected.value, automatic_expected, rel_tol=1e-10, abs_tol=1e-7):
                raise FormulaWorkbookError(f"事件 {hit.get('id', hi)} 的公式基线与引擎不一致：{expected.value} / {automatic_expected}。")
            critical = no_crit * (1 + crit_dmg) * ref(5) * weight if system != "transformative" else None
            automatic_crit = fixed["automatic_crit"] if fixed else baseline["crit"]
            if critical is not None and (automatic_crit is None or not math.isclose(critical.value, automatic_crit, rel_tol=1e-10, abs_tol=1e-7)):
                raise FormulaWorkbookError(f"事件 {hit.get('id', hi)} 的暴击公式基线与引擎不一致。")
            guard = self._weight_guard(hit.get("id"))
            if guard:
                self.weighting_guards.append(guard)
            if fixed:
                precision = fixed["precision"]
                self.fixed_value_sheet.append([hit["id"], fixed["override_id"], _Cell(expected.value, expected.text, 3),
                    _Cell(critical.value, critical.text, 3) if critical is not None else "不适用",
                    _Cell(fixed["effective_expected"], style=5), "近似" if precision["kind"] == "approximate" else "精确",
                    precision["method"], precision.get("uncertainty", ""), fixed["coverage_key"], fixed["source_fingerprint"],
                    "固定值不随面板自动重算；修改输入或本值后回App/Agent重新生成。"], style=5)
                expected = self.fixed_value_sheet.reference(len(self.fixed_value_sheet.rows), 5)
                critical = None  # A fixed expected value does not establish a critical-hit value.
                fixed_value_changed = f"OR(NOT(ISNUMBER({expected.text})),{expected.text}<>{fixed['effective_expected']!r})"
                self.fixed_value_sheet.rows[-1].append(_Cell(0, f"IF({fixed_value_changed},1,0)", 5))
                fixed_guard = (f"OR(SUM('参数'!G5:G{len(self.inputs.rows)})>0,"
                               f"'固定事件补正'!L{len(self.fixed_value_sheet.rows)}>0)")
                guard = f"OR({guard},{fixed_guard})" if guard else fixed_guard
            expected_formula = f'IF({guard},"需重新生成",{expected.text})' if guard else expected.text
            critical_formula = f'IF({guard},"需重新生成",{critical.text})' if guard and critical is not None else (critical.text if critical is not None else None)
            owner_key = self.owner_keys[hit["character"]]
            self.events.rows[row - 1].extend([_Cell(expected.value, expected_formula, 3),
                                             _Cell(critical.value, critical_formula, 3) if critical is not None else _Cell("不适用（固定期望）" if fixed else "不适用"),
                                             _Cell(baseline["expected"], style=5),
                                             _Cell(expected.value - baseline["expected"],
                                                   f'IF(ISNUMBER(V{row}),V{row}-X{row},"需重新生成")', 3),
                                             _Cell(("近似固定补正；自动公式仅对照" if fixed["precision"]["kind"] == "approximate" else "固定补正；自动公式仅对照") if fixed else
                                                   ("局部：机制数值依赖未映射" if self.support["limitations"] else "固定结构数值支持")),
                                             _Cell(", ".join(names)), _Cell(hit["character"]),
                                             _Cell(0, f"IF(OR(G{row}<0,H{row}<0,{crit_dmg.text}<0,V{row}<0,"
                                                   f"NOT(ISTEXT(AB{row})),NOT(ISTEXT(AE{row})),NOT(EXACT(AB{row},AE{row})),"
                                                   f"NOT(ISNUMBER(AD{row})),AD{row}<>{owner_key}),1,0)"),
                                             _Cell(owner_key, style=5), _Cell(hit["character"], style=5),
                                             _Cell(weight.value, style=5),
                                             _Cell("固定期望等价权重；参数改变需重新生成" if hit.get("id") in self.weighting["hit_group"] else "固定系数", style=5)])
            self.owner_totals[owner_key] = self.owner_totals.get(owner_key, 0) + expected.value
            self.event_rows.append(row)

    def finish(self):
        self.prepare_inputs()
        self.build_events()
        last_input = len(self.inputs.rows)
        changed = f"SUM('参数'!G5:G{last_input})>0"
        consistency_changed = changed
        invalid = f"SUM('参数'!H5:H{last_input})>0"
        structural = f"SUM('参数'!I5:I{last_input})>0"
        er_changed = f"SUM('参数'!J5:J{last_input})>0"
        sum_formula = f"SUM('逐事件'!V5:V{self.event_rows[-1]})" if self.event_rows else "0"
        total = sum(float(self.events.rows[r - 1][21].value) for r in self.event_rows)
        event_invalid = f"SUM('逐事件'!AC5:AC{self.event_rows[-1]})>0" if self.event_rows else "FALSE"
        weight_changed = "OR(" + ",".join(dict.fromkeys(self.weighting_guards)) + ")" if self.weighting_guards else "FALSE"
        declared_guard = f"OR({structural},{invalid},{event_invalid},{weight_changed})"
        fixed_changed = f"SUM('固定事件补正'!L5:L{len(self.fixed_value_sheet.rows)})>0" if self.fixed_value_sheet else "FALSE"
        if self.fixed_value_sheet:
            declared_guard = f"OR({declared_guard},{changed},{fixed_changed})"
            consistency_changed = f"OR({changed},{fixed_changed})"
        engine_total, engine_sum_formula, engine_guard = total, sum_formula, declared_guard
        if self.external_items:
            external_sheet = _Sheet("外部补充伤害", [30, 32, 24, 24, 18, 22, 30, 36, 40, 18, 28, 18], hidden_columns=(12,))
            for values in external_damage_rows(self.result, self.names, included_only=True):
                external_sheet.append(values, style=5)
            external_sheet.rows[3].extend([_Cell("固定值已改", style=1), _Cell("角色 ID", style=1), _Cell("角色汇总键", style=1)])
            for index, item in enumerate(self.external_items, 5):
                # This is a fixed externally calculated value, never a fabricated
                # engine multiplier. Edits invalidate combined results only.
                actor_literal = item["character"].replace('"', '""')
                owner_key = self.owner_keys[item["character"]]
                external_sheet.rows[index - 1].extend([_Cell(0,
                    f'IF(OR(NOT(ISNUMBER(F{index})),F{index}<>{item["expected"]!r},'
                    f'E{index}<>{item["t"]!r},NOT(ISTEXT(K{index})),NOT(EXACT(K{index},"{actor_literal}")), '
                    f'NOT(ISNUMBER(L{index})),L{index}<>{owner_key}),1,0)', 5),
                    _Cell(item["character"], style=5), _Cell(owner_key, style=5)])
                self.external_owner_totals[owner_key] = self.external_owner_totals.get(owner_key, 0) + item["expected"]
            self.sheets.append(external_sheet)
            last_external = 4 + len(self.external_items)
            external_changed = f"SUM('外部补充伤害'!J5:J{last_external})>0"
            consistency_changed = f"OR({consistency_changed},{external_changed})"
            declared_guard = f"OR({changed},{invalid},{event_invalid},{external_changed})"
            if self.fixed_value_sheet:
                declared_guard = f"OR({declared_guard},{fixed_changed})"
            sum_formula += f"+SUM('外部补充伤害'!F5:F{last_external})"
            total += sum(item["expected"] for item in self.external_items)
        verified_zero = bool(self.result.get("damage_reporting"))
        availability = "已验证零伤；命中结果只读" if verified_zero else ("局部支持" if self.support["limitations"] else "支持范围内可重算")
        if self.external_items:
            availability = "含固定外部补充；修改后需重新复核/生成"
        if self.fixed_value_sheet:
            availability = "含" + ("近似" if self.fixed_values["precision"] == "approximate" else "") + "单事件固定补正；修改后需重新生成"
            if self.external_items:
                availability += "（另含外部补充）"
        stale_message = "需重新复核/生成" if self.external_items else "需重新生成"
        self.summary.append([])
        self.summary.append([self.plan.get("name") or self.template.get("meta", {}).get("name") or "原神拉表活公式工作簿"], style=4)
        prefix = _is_execution_prefix(self.result)
        if prefix:
            availability = "诊断结果；补齐统计或必需验证后回App重算"
        self.summary.append(["结果口径", "仅已执行前缀的局部情景" if prefix else ("引擎计算与固定外部补充" if self.external_items else ("已验证全未命中的完整输入窗口" if verified_zero else "固定事件结构的局部情景")),
                             "上游结构可执行；游戏语义包含适配假设，未完全验证。" if self.automatic_sources else ""], style=0)
        if self.provisional_receipt is not None:
            self.summary.append([
                "结果性质", PROVISIONAL_RESULT_LABEL,
                "使用临时公共资料，仅限当前方案；不是数据库正式事实。",
            ], style=1)
        self.summary.append(["项目", "当前结果", "说明"], style=1)
        self.summary.append(["可用性", _Cell(availability,
                             f'IF({declared_guard},"{"需重新复核/生成" if self.external_items else "需回 App 重新生成"}","{availability}")'),
                             "动作、次数、目标、规则、层数或持续时间改变时，重新执行引擎。"])
        self.summary.append(["已执行前缀期望伤害" if prefix else "总期望伤害", _Cell(total, f'IF({declared_guard},"{stale_message}",{sum_formula})', 3),
                             "引擎计算加固定外部补充；外部值不随面板重算。" if self.external_items else "只统计本方案声明的伤害事件。"])
        total_row = len(self.summary.rows)
        duration = self.template.get("loop_duration")
        self.summary.append(["纸面 DPS", _Cell('执行未完成，不提供整轴DPS', style=5) if prefix else _Cell(total / duration if duration else "未设置循环时长",
                             f'IF({declared_guard},"{stale_message}",' + (f"B{total_row}/'参数'!C5" if duration else '"未设置循环时长"') + ")", 3),
                             "执行已中断；不能用前缀伤害除以整轴时长作为完整结果。" if prefix else "不等同于已证明能量和机制循环成立。"])
        self.summary.append(["与 App 一致性", _Cell("导出时输入一致", f'IF({consistency_changed},"工作簿局部情景；未回写 App","导出时输入一致")')])
        original = _rotation_summary_label(self.calculated)
        self.summary.append(["能量与动作检查", _Cell(original,
                             f'IF(OR({er_changed},{changed}),"输入已改，循环结论失效；回 App 重查","{original}")'),
                             "本工作簿不重演能量与动作状态机；修改数值后原结论失效。"])
        if self.fixed_value_sheet:
            self.summary.append(["固定补正精度", "近似" if self.fixed_values["precision"] == "approximate" else "精确",
                                 "有效值计入总量，自动公式仅作基线对照；来源、方法及不确定性见「固定事件补正」。"])
        if self.result.get("mechanics"):
            self.summary.append(["执行覆盖", "输入窗口完整执行" if self.result["mechanics"].get("complete_first_cycle") else "仅已执行前缀",
                                 "循环中断后的未执行命中不计入本次结果。"])
        else:
            self.summary.append([])
        self.summary.append(["角色", "期望伤害"], style=1)
        for char in self.template["characters"]:
            cid = char["id"]
            owner_key = self.owner_keys[cid]
            formula = (f"SUMIFS('逐事件'!V5:V{self.event_rows[-1]},'逐事件'!AD5:AD{self.event_rows[-1]},{owner_key})"
                       if self.event_rows else "0")
            if self.external_items:
                formula += f"+SUMIFS('外部补充伤害'!F5:F{last_external},'外部补充伤害'!L5:L{last_external},{owner_key})"
            value = self.owner_totals.get(owner_key, 0)
            if self.external_items:
                value += self.external_owner_totals.get(owner_key, 0)
            self.summary.append([self.names["characters"].get(cid, cid), _Cell(value, f'IF({declared_guard},"{stale_message}",{formula})', 3)])
        if self.external_items:
            self.summary.append([])
            self.summary.append(["计算组成", "期望伤害", "使用范围"], style=1)
            self.summary.append(["原生事件（含固定补正）" if self.fixed_value_sheet else "引擎计算（局部公式）", _Cell(engine_total,
                f'IF({engine_guard},"需重新生成",{engine_sum_formula})', 3),
                "仅引擎部分保留原有局部公式能力，不能替代含外部项的整体结论。"])
            self.summary.append(["外部补充（导出固定值）", sum(item["expected"] for item in self.external_items),
                                 "修改外部值或引擎参数后，须重新复核并生成整体结果。"])
        self.sources.append([])
        self.sources.append(["来源、支持范围与原始输入"], style=4)
        self.sources.append(["字段", "内容"], style=1)
        if self.provisional_receipt is not None:
            self.sources.append([
                "结果性质",
                (
                    f"{PROVISIONAL_RESULT_LABEL}；精度标识 {PROVISIONAL_RESULT_FIDELITY}。"
                    "完整数据库核对、Agent证明或用户批准见“临时公共资料”页。"
                ),
            ])
        self.sources.append(["初始面板", "给定面板包含套装常驻属性；选择武器和套装不会在工作簿重复加入这些属性。"])
        editable_count = sum(1 for row in self.inputs.rows if len(row) > 2 and row[2].style == 2)
        self.sources.append(["允许编辑", f"本表有{editable_count}个黄色独立数值；逐项范围与原因见参数页。灰色及公式格不作独立编辑。"])
        self.sources.append(["固定内容", "动作与命中结构、次数、持续时间、层数、匹配和装备选择：回 App 重新执行并生成。"])
        if self.fixed_value_sheet:
            self.sources.append(["单事件固定补正", "本次有效值来自已绑定的单事件补正证明；自动公式不用于推导该固定值。输入改变后须由App/Agent重新计算与生成，不回填倍率。"])
            for override in self.plan.get("calculation_event_overrides", {}).get("items", []):
                for reference in override.get("source_refs", []):
                    self.sources.append(["固定补正来源", reference.get("locator"), reference.get("summary")])
        materialization = self.result.get("action_materialization_receipt")
        if isinstance(materialization, dict):
            status_names = {"complete": "完整", "incomplete": "不完整",
                "unknown": "未知", "not_applicable": "不适用"}
            status = materialization.get("status")
            calculation_class = materialization.get("calculation_class")
            interpretation = ("仅证明独立清单所列关键领域；不代表数据库已覆盖全部游戏机制。"
                if status == "complete" else
                "本次结果属于诊断结果；须按动作缺项补齐资料或物化后回 App 重算。"
                if status in {"incomplete", "unknown"} else
                "本轴没有需要动作物化核验的伤害动作。")
            self.sources.append(["动作物化状态",
                f"{status_names.get(status, '未知')}；"
                f"计算类别：{'诊断结果' if calculation_class == 'diagnostic' else '正常'}。"
                + interpretation])
            summary = materialization.get("summary") or {}
            self.sources.append(["动作物化汇总",
                "动作 {actions}；完整 {complete}；不完整 {incomplete}；未知 {unknown}；"
                "分量 {materialized_components}/{expected_components}；"
                "发生实例 {materialized_occurrences}/{expected_occurrences}。".format(
                    actions=summary.get("actions", "未知"),
                    complete=summary.get("complete_actions", "未知"),
                    incomplete=summary.get("incomplete_actions", "未知"),
                    unknown=summary.get("unknown_actions", "未知"),
                    materialized_components=summary.get("materialized_components", "未知"),
                    expected_components=("未知" if summary.get("expected_components") is None
                                         else summary["expected_components"]),
                    materialized_occurrences=summary.get("materialized_occurrences", "未知"),
                    expected_occurrences=("未知" if summary.get("expected_occurrences") is None
                                          else summary["expected_occurrences"])),
            ])

            def identity_summary(rows, key):
                identities = [row.get(key) for row in rows if isinstance(row, dict) and row.get(key)]
                head = identities[:16]
                text = "、".join(str(value) for value in head) or "无"
                return text + (f"（另有 {len(identities) - len(head)} 项，见原始回执）"
                               if len(identities) > len(head) else "")

            for action in materialization.get("actions", []):
                if not isinstance(action, dict):
                    continue
                action_status = action.get("coverage_status")
                action_id = action.get("action_id", "未知动作")
                ability_id = action.get("ability_id", "未知能力")
                details = [
                    f"覆盖状态：{status_names.get(action_status, '未知')}",
                    f"能力：{ability_id}",
                    "分量：{}/{}".format(
                        action.get("materialized_components", "未知"),
                        "未知" if action.get("expected_components") is None
                        else action["expected_components"]),
                    "发生实例：{}/{}".format(
                        action.get("materialized_occurrences", "未知"),
                        "未知" if action.get("expected_occurrences") is None
                        else action["expected_occurrences"]),
                ]
                unresolved_components = action.get("unresolved_components") or []
                unresolved_occurrences = action.get("unresolved_occurrences") or []
                unexpected_occurrences = action.get("unexpected_occurrences") or []
                if unresolved_components:
                    details.append("未解决分量：" + identity_summary(
                        unresolved_components, "component_id"))
                if unresolved_occurrences:
                    details.append("未解决实例：" + identity_summary(
                        unresolved_occurrences, "occurrence_id"))
                if unexpected_occurrences:
                    details.append("意外实例：" + identity_summary(
                        unexpected_occurrences, "occurrence_id"))
                issue_codes = [row.get("code") for row in action.get("issues", [])
                               if isinstance(row, dict) and row.get("code")]
                if issue_codes:
                    details.append("问题：" + "、".join(issue_codes[:16])
                        + (f"（另有 {len(issue_codes) - 16} 项，见原始回执）"
                           if len(issue_codes) > 16 else ""))
                label = ("动作缺项：" if action_status in {"incomplete", "unknown"}
                         else "动作覆盖：") + str(action_id)
                self.sources.append([label, "；".join(details)])
        if self.result.get('attribute_ledger'):
            attribute_sheet = _Sheet('属性取样账', [38,64,20,18,14,26,16,22,22,18])
            for row in attribute_ledger_rows(self.result):
                attribute_sheet.append(row,style=5)
            self.sheets.append(attribute_sheet)
        if verified_zero:
            self.sources.append(["伤害报告证明", "完整输入窗口内所有已声明接触均实际未命中；无引擎伤害事件行。引擎总伤与DPS为0。普通面板编辑不重新判断命中；源提供者属性锁仍适用。循环是否成立另行检查。" if self.external_items else "完整输入窗口内所有已声明接触均实际未命中；无伤害事件行。总伤与DPS为0。普通面板编辑不重新判断命中；源提供者属性锁仍适用。循环是否成立另行检查。"])
        if self.all_external_items:
            self.sources.append(["外部补充伤害", "独立计算并复核的固定值，不产生引擎命中、乘区、增益或资源事件。任何输入修改后须重新复核并生成；引擎局部公式仍可单独查看。"])
            self.sources.append(["外部补充统计归属", f"窗口内{len(self.external_items)}项计入总伤；窗口外{len(self.all_external_items)-len(self.external_items)}项只保留来源审计。"])
        if self.freeze_reference:
            self.sources.append(["冻结参考证据层级", "experimental_reference：实验参考模型，不是游戏真值。初态、接触或参考契约改变后必须回App重算并重新导出。"])
            self.sources.append(["元素状态流水", "只读引擎快照；C/H/F/x、自然到期与层消费不在Excel中重演ODE。层消费不另计伤害；反应包及范围确认见独立流水页。"])
        if self.automatic_sources:
            self.sources.append(["上游适配语义", "结构可执行；包含显式适配假设，游戏语义未完全验证。自动来源的数值由数据库编译，回 App 更改来源后重新生成。"])
        if self.automatic_hit_parameters:
            from genshin_app.export import hit_parameter_rows
            parameter_rows = hit_parameter_rows(self.result)
            if parameter_rows:
                parameter_sheet = _Sheet('技能参数来源', [30,100,22,24,28,20])
                for row in parameter_rows:
                    parameter_sheet.append(row,style=5)
                self.sheets.append(parameter_sheet)
            self.sources.append(["数据库倍率", "自动倍率为灰色固定来源；参数取自精确等级表，单属性倍率用途由方案声明，未自动验证游戏公式。更改来源后回App重新生成。"])
        if self.action_resource_records:
            self.sources.append(["数据库动作资源", "只读基础值来自本次编译副本；用途为declared_not_verified，仅核对显式身份。Excel不重演CD/能量状态机；修改来源、用途、映射或基础值须回App重新生成。"])
            if any(r.get("energy_delivery") for r in self.action_resource_records):
                self.sources.append(["系统扣能审计", "动作接纳时扣能后留下的只读记录；不占事件order、不二次扣能。before/after为提交瞬间值；修改动作、初态、回能或ER后回App重新检查。"])
        if (self.result.get("action_admission_compilations")
                or self.result.get("rotation_check", {}).get("action_admission_execution")):
            self.sources.append(["动作接纳审计", "来源、固定场景、开场冷却和实际门禁提交均为本次 App 计算的只读历史；Excel 不选择数据库模型，也不重演前台、占用、能量、冷却或次数状态。"])
        if self.action_event_records:
            self.sources.append(["动作档案派发", "一次校准档案按显式动作装配；非上游已证明的原生派发。档案自身不生成产球，独立产球来源另行核验；改校准、绑定或本轴例外须回App重算。"])
        if self.attack_metadata_records:
            self.sources.append(["攻击元信息", "倍率位置、元素、原始GU与衰减配置来自精确候选；来源固定，Excel不重新选择分支或解释单位。"])
        if self.energy_generation_records:
            self.sources.append(["技能产球来源", "球数、元素和实际命中关联由已接受来源自动提供；当前不计受击停顿延寿。改场景、校准或动作需回App重算，Excel不执行产球与收取队列。"])
        if self.limited_hit_benefit_records:
            self.sources.append(["有限次数收益", "来源系数、持续时间和每人次数为固定资料；当前攻击力取样、激活及配额来自实际流水。提供者攻击力及其贡献值改变后须回App重算；Excel不重演此队列。"])
        if self.mechanism_records:
            self.sources.append(["通用机制来源", "由受审事实、参数、资格与本轴父关系装配；来源和所读属性固定，Excel只呈现本次状态与公式，不重演任意状态机。"])
        if self.periodic_attack_records:
            self.sources.append(["周期攻击来源", "创建、挂载与参数的关联仅证明资料关系；按明确首击、次数与生命周期校准展开。来源倍率、附着、间隔及所有校准为固定结构，变化后回App重新生成。"])
        if (self.mechanics_model or {}).get("fixed_effect_sources"):
            self.sources.append(["目标条件收益", "固定来源常数和逐击条件为只读快照。命中前取样，角色面板快照不锁定目标状态；人工微调单独记录，不在Excel重演条件判断。"])
        if self.result.get("shared_pools"):
            self.sources.append(["共享资源", "实际消费回执固定本次命中资格；修改池、补量、门控、时点或来源需回App重算。完整记录见共享资源流水页。"])
        if self.result.get('attribute_ledger'):
            self.sources.append(['属性账取样', '基底、分类、组成和历史帧取样均保留引擎回执；Excel不重演属性账。修改相关输入必须回App重新计算。'])
        if self.result.get('state_runtime'):
            self.sources.append(['生命周期定时', '领域建立、结束、末跳与收益残留来自实际队列。Excel不重演定时、切人或条件判断；改变结构须回App重算。完整记录见生命周期流水页。'])
        self.sources.append(["分域取值", "自身、提供者、敌方的采样事件与依赖集合固定；源面板和支持的数值按公式重新计算。"])
        self.sources.append(["循环结论", "任何输入修改后，旧动作与能量检查仅作历史记录，不能继续视为当前情景结论。"])
        for item in self.support["limitations"]:
            self.sources.append(["局部支持：" + item["path"], item["message"]])
        for key, value in {"workbook_schema": WORKBOOK_SCHEMA, **self.receipt, "support": self.support,
                           **({"fixed_event_override_declaration": self.plan["calculation_event_overrides"]}
                              if self.fixed_value_sheet else {}),
                           **({"external_damage": self.result["external_damage"],
                               "calculation_totals": self.result["calculation_totals"]} if self.all_external_items else {}),
                           **({'result.special_damage_echoes': self.result['special_damage_echoes']}
                              if self.result.get('special_damage_echoes') else {}),
                           **({'special_damage_echo_execution': self.result['special_damage_echo_execution']}
                              if 'special_damage_echo_execution' in self.result else {}),
                           **({"damage_report_window": self.result["damage_report_window"],
                               "damage_report_audit_hits": self.result["damage_report_audit_hits"],
                               "damage_report_execution": self.result["damage_report_execution"]}
                              if self.result.get("damage_report_window") else {}),
                           "equipment_summary": self.calculated.get("equipment_summary", {}),
                           **({'attribute_ledger':self.result['attribute_ledger']} if self.result.get('attribute_ledger') else {}),
                           "mechanics_trace": self.calculated.get("mechanics_trace", self.result.get("mechanics_trace", {})),
                           **({"compiled_mechanics_model": self.mechanics_model,
                               "upstream_compilations": (self.calculated.get("report") or {}).get("upstream_compilations", [])}
                              if self.automatic_sources else {}),
                           **({"elemental_reference": self.result["elemental"]} if self.freeze_reference else {}),
                           **({"compiled_rotation_model": self.calculated["report"]["compiled_rotation_model"],
                               "action_resource_compilations": self.action_resource_records} if self.action_resource_records else {}),
                           **({"action_event_compilations": self.action_event_records,
                               "action_event_execution_rotation_model": self.calculated["report"]["action_event_execution_rotation_model"],
                               "action_event_compiled_mechanics_model": self.mechanics_model} if self.action_event_records else {}),
                           **({"attack_metadata_compilations": self.attack_metadata_records} if self.attack_metadata_records else {}),
                           **({"periodic_attack_compilations": self.periodic_attack_records} if self.periodic_attack_records else {}),
                           **({"energy_generation_compilations": self.energy_generation_records,
                               "energy_generation_summary": self.result.get("energy_generation_summary")} if self.energy_generation_records else {}),
                           **({"limited_hit_benefit_compilations": self.limited_hit_benefit_records,
                               "limited_hit_benefit_summary": self.result.get("limited_hit_benefit_summary")} if self.limited_hit_benefit_records else {}),
                           **({"mechanism_compilations": self.mechanism_records,
                               "mechanism_replay_context": self.result.get("mechanism_replay_context"),
                               "mechanism_summary": self.result.get("mechanism_summary")} if self.mechanism_records else {}),
                           "plan": self.plan}.items():
            from genshin_app.formula_sources import source_chunks
            packed = source_chunks(value)
            start = len(self.audit_sources.rows) + 1
            for index, chunk in enumerate(packed["chunks"], 1):
                self.audit_sources.append([key, index, chunk])
            end = len(self.audit_sources.rows)
            self.sources.append([key, f"完整来源JSON!C{start}:C{end}（{len(packed['chunks'])}块）",
                                 packed["sha256"], packed["bytes"]])
        if verified_zero:
            proof_sheet = _Sheet("伤害报告证明", [32, 65, 35, 24, 18, 18, 18, 28])
            for row in damage_reporting_rows(self.result):
                proof_sheet.append(row, style=5)
            self.sheets.append(proof_sheet)
        if self.freeze_reference:
            state_sheet = _Sheet("元素状态流水", [28, 20, 42, 30, 24, 24, 36, 42] + [18] * 10 + [42, 20, 20, 24, 18, 18, 18, 18, 16, 20, 65])
            for row in elemental_state_rows(self.result):
                state_sheet.append(row, style=1 if row and row[0] == "记录类型" else 5)
            self.sheets.append(state_sheet)
        if self.action_resource_records:
            resource_sheet = _Sheet("动作资源来源", [30, 95, 26, 30, 30, 24])
            for row in action_resource_rows(self.result):
                resource_sheet.append(row, style=1 if row and row[0] == "资料字段" else 5)
            self.sheets.append(resource_sheet)
        if self.action_event_records:
            dispatch_sheet = _Sheet("动作派发来源", [34, 60, 45, 20, 20, 20, 26, 100])
            for row in action_event_rows(self.result):
                dispatch_sheet.append(row, style=1 if row and row[0] == "动作" else 5)
            self.sheets.append(dispatch_sheet)
        if self.energy_generation_records:
            energy_sheet = _Sheet("产球来源", [34, 100, 32, 34, 28, 28])
            for row in energy_generation_rows(self.result):
                energy_sheet.append(row, style=5)
            self.sheets.append(energy_sheet)
            energy_history = _Sheet("能量流水", [12, 18, 28, 18, 22, 18] + [18] * 8 + [60, 60, 100])
            history_rows = dict(check_sheets(self.result["rotation_check"], self.names))["能量流水"]
            energy_history.append(["只读历史流水", "本次 App 计算的资源记录。修改面板、动作、场景或校准后需回 App 重算；本页不会随 Excel 公式重演。"], style=5)
            for row in history_rows:
                energy_history.append(row, style=5)
            self.sheets.append(energy_history)
        if self.attack_metadata_records:
            metadata_sheet = _Sheet("攻击元信息来源", [34, 100, 30, 35, 30, 35])
            for row in attack_metadata_rows(self.result):
                metadata_sheet.append(row, style=5)
            self.sheets.append(metadata_sheet)
        if self.limited_hit_benefit_records:
            limited_sheet = _Sheet("有限次数收益", [30, 100, 30, 100, 28, 28])
            for row in limited_hit_benefit_rows(self.result):
                limited_sheet.append(row, style=5)
            self.sheets.append(limited_sheet)
        qualification_rows = beneficiary_eligibility_rows(self.result, self.names)
        if qualification_rows:
            qualification_sheet = _Sheet("受益者资格", [30, 90, 28, 85])
            qualification_sheet.append(["只读历史记录", "实际选人及残留来自本次 App 计算；修改动作、面板或规则后须回 App 重算，本页不会随 Excel 公式重新选人。"], style=5)
            for row in qualification_rows:
                qualification_sheet.append(row, style=5)
            self.sheets.append(qualification_sheet)
        if self.mechanism_records:
            mechanism_sheet = _Sheet("通用机制来源", [30, 100, 30, 90, 28, 28])
            for row in mechanism_rows(self.result):
                mechanism_sheet.append(row, style=5)
            self.sheets.append(mechanism_sheet)
        if self.result.get('snapshot_captures'):
            from genshin_app.export import snapshot_capture_rows
            capture_sheet = _Sheet('面板取样', [26, 100, 30, 38, 22, 22])
            for row in snapshot_capture_rows(self.result):
                capture_sheet.append(row, style=5)
            self.sheets.append(capture_sheet)
        if self.result.get('state_runtime'):
            from genshin_app.export import state_runtime_rows
            state_sheet = _Sheet('生命周期流水', [14,45,28,28,14,28,25,32,32,35])
            for row in state_runtime_rows(self.result):
                state_sheet.append(row, style=5)
            self.sheets.append(state_sheet)
        if self.result.get("shared_pools"):
            pool_sheet = _Sheet("共享资源流水", [12, 12, 28, 16, 24, 14, 14, 14, 14, 28, 32, 40])
            for row in shared_pool_rows(self.result):
                pool_sheet.append(row, style=5)
            self.sheets.append(pool_sheet)
        if self.result.get('resource_counters'):
            from genshin_app.export import resource_counter_rows
            counter_sheet = _Sheet('资源累计流水', [16,48,24,18,18,18,18,32,32,20,28,32,48])
            for row in resource_counter_rows(self.result):
                counter_sheet.append(row, style=5)
            self.sheets.append(counter_sheet)
        if self.result.get('resource_counter_views'):
            from genshin_app.counter_frame_reporting import is_self_frame_report
            from genshin_app.export import resource_counter_view_rows
            widths = ([16,48,22,35,58,48,40,22,22,22,24,26,16,24,48,30,40]
                      if is_self_frame_report(self.result['resource_counter_views']) else
                      [16,48,22,48,22,22,22,24,16,24,48,30,40])
            view_sheet = _Sheet('资源读数流水', widths)
            for row in resource_counter_view_rows(self.result):
                view_sheet.append(row, style=5)
            self.sheets.append(view_sheet)
        from genshin_app.reaction_resource_reporting import reaction_resource_sheet
        reaction_rows = reaction_resource_sheet(self.result)
        if reaction_rows:
            reaction_sheet = _Sheet('反应资源联动', [14,36,42,16,24,24,42,28,42,24,16,16,16,24,20,20,60])
            for row in reaction_rows:
                reaction_sheet.append(row, style=5)
            self.sheets.append(reaction_sheet)
        from genshin_app.counter_frame_reporting import counter_frame_rows
        frame_rows = counter_frame_rows(self.result.get('resource_counter_views'))
        if frame_rows:
            frame_sheet = _Sheet('累计输出快照', [16,20,36,42,48,20,20,60,60])
            for row in frame_rows:
                frame_sheet.append(row, style=5)
            self.sheets.append(frame_sheet)
        if self.result.get('attribute_callbacks'):
            from genshin_app.export import attribute_callback_rows
            callback_sheet = _Sheet('属性回调执行', [16,20,32,40,28,32,40,20,12,52,40])
            for row in attribute_callback_rows(self.result):
                callback_sheet.append(row, style=5)
            self.sheets.append(callback_sheet)
        from genshin_app.export import resource_threshold_rows
        from genshin_app.resource_threshold_reporting import FIELDS, TITLES
        for field, title in zip(FIELDS, TITLES):
            if self.result.get(field):
                threshold_sheet = _Sheet(title, [16,20,44,36,40,32,44,24,22,22,22,20,50,50])
                for row in resource_threshold_rows(self.result, field):
                    threshold_sheet.append(row, style=5)
                self.sheets.append(threshold_sheet)
        if self.periodic_attack_records:
            periodic_sheet = _Sheet("周期攻击来源", [34, 100, 30, 28, 30, 35])
            for row in periodic_attack_rows(self.result):
                periodic_sheet.append(row, style=5)
            self.sheets.append(periodic_sheet)
        if self.reaction_benefit_records:
            benefit_sheet = _Sheet("反应收益来源", [28, 80, 26, 100, 25, 25])
            for row in reaction_benefit_rows(self.result):
                benefit_sheet.append(row, style=5)
            self.sheets.append(benefit_sheet)
        from genshin_app.reaction_em_reporting import reaction_em_rows
        reaction_em = reaction_em_rows(self.result)
        if reaction_em:
            reaction_em_sheet = _Sheet("反应触发精通", [28, 80, 26, 100, 25, 25])
            for row in reaction_em:
                reaction_em_sheet.append(row, style=5)
            self.sheets.append(reaction_em_sheet)
        from genshin_app.static_inclusion_reporting import static_inclusion_rows
        static_rows = static_inclusion_rows(self.result)
        if static_rows:
            static_sheet = _Sheet('面板已含来源', [22, 34, 16, 26, 18, 24, 30, 48, 48, 18, 70])
            for row in static_rows:
                static_sheet.append(row, style=5)
            self.sheets.append(static_sheet)
        from genshin_app.action_capacity_reporting import capacity_sheets
        for title, rows in capacity_sheets(self.result, self.names):
            capacity_sheet = _Sheet(title, [24, 34, 28, 28, 30, 18, 22, 80, 32, 26, 70, 80, 70, 50, 45])
            for row in rows:
                capacity_sheet.append(row, style=5)
            self.sheets.append(capacity_sheet)
        from genshin_app.action_admission_reporting import action_admission_sheets
        for title, rows in action_admission_sheets(self.result, self.names):
            admission_sheet = _Sheet(
                title,
                [24, 30, 28, 28, 34, 34, 50, 60, 32, 45, 50, 50, 70, 70, 48, 48],
            )
            for row in rows:
                admission_sheet.append(row, style=5)
            self.sheets.append(admission_sheet)
        if self.passive_records:
            passive_sheet = _Sheet("固有天赋来源", [28, 90, 26, 100, 25, 25])
            for row in passive_source_rows(self.result):
                passive_sheet.append(row, style=5)
            self.sheets.append(passive_sheet)
        if self.result.get("fixed_effect_sources"):
            condition_sheet = _Sheet("目标条件收益", [18, 35, 40, 18, 40, 30, 25, 20, 20, 18, 35, 20, 90, 70, 18])
            for row in target_condition_rows(self.result):
                condition_sheet.append(row, style=1 if row and row[0] == "时刻（秒）" else 5)
            self.sheets.append(condition_sheet)
        if self.provisional_receipt is not None:
            provisional_sheet = _Sheet(
                "临时公共资料", [28, 42, 34, 38, 42, 42, 42],
            )
            for row in provisional_entity_rows(self.result, self.receipt):
                provisional_sheet.append(
                    row,
                    style=1 if row and row[0] in {"结果性质", "临时资料类型"} else 5,
                )
            self.sheets.append(provisional_sheet)
        if self.formal_audit is not None:
            audit_sheet = _Sheet("计算审计", [32, 110])
            for row in formal_audit_payload_rows(self.formal_audit):
                audit_sheet.append(
                    row,
                    style=1 if row and row[0] in {"审计类型", "审计合同"} else 5,
                )
            self.sheets.append(audit_sheet)
        if self.fixed_value_sheet:
            self.sheets.append(self.fixed_value_sheet)
        self.summary.append(["计算身份", _Cell("App原计算的导出副本",
            f'IF({consistency_changed},"Excel派生测算，未经App重新接纳","App原计算的导出副本")'),
            "原回执只对应导出原值；允许编辑不会签发新的App结果或循环证明。"])
        self.sheets.append(self.audit_sources)
        return self


def _worksheet_xml(sheet):
    if len(sheet.rows) > 1048576 or any(len(row) > 16384 for row in sheet.rows):
        raise FormulaWorkbookError(f"{sheet.name} 超过 Excel 工作表行列容量，请缩小导出范围。")
    rows = []
    for ri, cells in enumerate(sheet.rows, 1):
        rendered = []
        for ci, cell in enumerate(cells, 1):
            coordinate = f"{_column(ci)}{ri}"
            attrs = f'r="{coordinate}" s="{cell.style}"'
            if cell.formula is not None:
                if len(cell.formula) > 8192:
                    raise FormulaWorkbookError(f"{sheet.name}!{coordinate} 的公式超过 Excel 长度限制，请缩小导出范围或在 App 计算。")
                if isinstance(cell.value, str):
                    attrs += ' t="str"'
                from genshin_app.openxml_text import xml_formula
                rendered.append(f'<c {attrs}><f>{xml_formula(cell.formula, location=coordinate)}</f><v>{xml_formula(str(cell.value), location=coordinate)}</v></c>')
            elif isinstance(cell.value, (int, float)) and not isinstance(cell.value, bool):
                if not math.isfinite(cell.value):
                    raise FormulaWorkbookError(f"{sheet.name}!{coordinate} 数值不是有限值。")
                rendered.append(f'<c {attrs}><v>{cell.value}</v></c>')
            elif cell.value is not None:
                if len(str(cell.value)) > 32767:
                    raise FormulaWorkbookError(f"{sheet.name}!{coordinate} 的文本超过 Excel 单元格长度限制。")
                from genshin_app.openxml_text import spreadsheet_text
                rendered.append(f'<c {attrs} t="inlineStr"><is><t xml:space="preserve">{spreadsheet_text(cell.value, location=coordinate)}</t></is></c>')
        height = 30 if ri == 2 else 23
        rows.append(f'<row r="{ri}" ht="{height}" customHeight="1">{"".join(rendered)}</row>')
    widths = "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"'
                     + (' hidden="1"' if i in sheet.hidden_columns else '') + '/>'
                     for i, w in enumerate(sheet.widths, 1))
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{_NS}">'
            '<sheetViews><sheetView showGridLines="0" workbookViewId="0"><pane ySplit="4" topLeftCell="A5" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
            f'<sheetFormatPr defaultRowHeight="23"/><cols>{widths}</cols><sheetData>{"".join(rows)}</sheetData>'
            '<sheetProtection sheet="1" objects="1" scenarios="1"/>'
            '<pageMargins left="0.3" right="0.3" top="0.4" bottom="0.4" header="0.2" footer="0.2"/>'
            '</worksheet>')


def _xlsx(sheets):
    output = io.BytesIO()
    base = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    package = 'http://schemas.openxmlformats.org/package/2006/relationships'
    types = ['<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
             '<Default Extension="xml" ContentType="application/xml"/>',
             '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
             '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>']
    for i in range(1, len(sheets) + 1):
        types.append(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
    tags = ''.join(f'<sheet name="{escape(s.name)}" sheetId="{i}" r:id="rId{i}"/>' for i, s in enumerate(sheets, 1))
    rels = ''.join(f'<Relationship Id="rId{i}" Type="{base}/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1))
    rels += f'<Relationship Id="rId{len(sheets) + 1}" Type="{base}/styles" Target="styles.xml"/>'
    styles = (f'<styleSheet xmlns="{_NS}"><numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.0000"/></numFmts>'
              '<fonts count="3"><font><sz val="11"/><color rgb="FF243447"/><name val="Arial"/></font>'
              '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Arial"/></font>'
              '<font><b/><sz val="15"/><color rgb="FF243447"/><name val="Arial"/></font></fonts>'
              '<fills count="5"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
              '<fill><patternFill patternType="solid"><fgColor rgb="FF334B66"/><bgColor indexed="64"/></patternFill></fill>'
              '<fill><patternFill patternType="solid"><fgColor rgb="FFFFF0C2"/><bgColor indexed="64"/></patternFill></fill>'
              '<fill><patternFill patternType="solid"><fgColor rgb="FFF0F2F5"/><bgColor indexed="64"/></patternFill></fill></fills>'
              '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="6">'
              '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"><alignment vertical="center"/></xf>'
              '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0"><alignment horizontal="center" vertical="center"/></xf>'
              '<xf numFmtId="164" fontId="0" fillId="3" borderId="0" xfId="0" applyNumberFormat="1"><protection locked="0"/></xf>'
              '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
              '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0"/>'
              '<xf numFmtId="0" fontId="0" fillId="4" borderId="0" xfId="0"/></cellXfs>'
              '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' + ''.join(types) + '</Types>')
        archive.writestr('_rels/.rels', f'<Relationships xmlns="{package}"><Relationship Id="rId1" Type="{base}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr('xl/workbook.xml', f'<workbook xmlns="{_NS}" xmlns:r="{base}"><bookViews><workbookView/></bookViews><sheets>{tags}</sheets><calcPr calcMode="auto" fullCalcOnLoad="1" forceFullCalc="1"/></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', f'<Relationships xmlns="{package}">{rels}</Relationships>')
        archive.writestr('xl/styles.xml', styles)
        for i, sheet in enumerate(sheets, 1):
            archive.writestr(f'xl/worksheets/sheet{i}.xml', _worksheet_xml(sheet))
    return output.getvalue()


@verification_scope
def build_formula_workbook(
    calculated: dict, *, candidate_session=None, _session=None,
) -> bytes:
    """Build a self-contained XLSX; reject stale/mismatched source receipts."""
    from genshin_app.equipment.provisional_entities import (
        validate_current_session_usage_receipt,
    )
    validate_current_session_usage_receipt(
        calculated.get("result", {}), calculated.get("result_receipt", {}),
        candidate_session,
    )
    return _xlsx(_Projection(copy.deepcopy(calculated), _session=_session).finish().sheets)


@verification_scope
def write_formula_workbook(
    calculated: dict, path: str | Path, *, candidate_session=None,
    _session=None,
) -> dict:
    """Write the explicit live-formula mode and return its editing contract."""
    from genshin_app.equipment.files import file_sha
    from genshin_app.output_policy import (
        publish_ordinary_files,
        validate_database_outputs,
    )
    from genshin_app.paths import default_data_dir
    destination = Path(path)
    database = default_data_dir() / "genshin.db"
    validate_database_outputs([destination], database)
    observed = file_sha(destination)
    from genshin_app.equipment.provisional_entities import (
        validate_current_session_usage_receipt,
    )
    validate_current_session_usage_receipt(
        calculated.get("result", {}), calculated.get("result_receipt", {}),
        candidate_session,
    )
    projection = _Projection(copy.deepcopy(calculated), _session=_session).finish()
    content = _xlsx(projection.sheets)
    publish_ordinary_files([(destination, content)], database, overwrite=True, expected_shas={destination: observed})
    return {"path": str(destination.resolve()), "support": projection.support,
            "result_receipt": copy.deepcopy(projection.calculated.get("result_receipt", {}))}
