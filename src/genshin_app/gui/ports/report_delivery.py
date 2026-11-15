"""Exact, bounded report exports from calculations validated by this Host."""

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from secrets import token_urlsafe
from threading import Lock

from genshin_app.calculation_capacity import CALCULATED_ARTIFACT_BYTES
from genshin_app.equipment import PlanError
from genshin_app.equipment.calculation_wire import calculated_package_bytes
from genshin_app.equipment.files import file_sha, publish_files
from genshin_app.equipment.service import (
    _encode_validated_result,
    _validate_result_export,
    display_names,
)
from genshin_app.export import EXPORT_FORMATS
from genshin_app.output_policy import validate_database_outputs


@dataclass(frozen=True)
class ViewedReport:
    calculated: dict
    names: dict | None
    audit: dict | None
    original_path: str | None
    session_id: str | None
    export_allowed: bool


_SUMMARY_FIELDS = (
    "summary", "calculation_totals", "mechanics_status", "damage_report_window",
    "damage_reporting", "coverage_interpretation",
)
_EFFECT_FIELDS = ("buff_state_timeline", "buffs_coverage", "coverage_interpretation")
_DIAGNOSTIC_FIELDS = (
    "action_materialization_receipt", "action_automation_audit",
    "presentation_issues", "provisional_entity_usage_receipt", "upstream_semantics", "assumptions",
)
_REPORT_PAGE_SIZE = 100
_MECHANICS_SECTIONS = {
    "mechanics_trace": "完整机制流水",
    "sampling_trace": "属性取值与依赖",
    "target_condition_trace": "逐击目标条件",
    "elemental.trace": "元素附着和反应流水",
    "attribute_ledger.receipts": "属性账与收益来源",
    "attribute_ledger.frame_routes": "属性帧来源",
    "special_reactions.trace": "星月反应流水",
    "special_reactions.damage_weighting": "星月反应伤害权重",
    "rotation_check.event_log": "动作和能量流水",
    "state_runtime.execution_trace": "生命周期与定时执行",
    "resource_counters.trace": "资源累计流水",
    "resource_counter_views.trace": "资源读数流水",
    "resource_threshold_observers.trace": "资源阈值观察",
    "resource_threshold_triggers.trace": "阈值收益执行",
    "attribute_callbacks.trace": "属性回调执行",
    "shared_pools.trace": "共享资源流水",
}
_MECHANICS_FIELDS = (
    "mechanics_status", "mechanics_issues", "fixed_effect_sources",
    "action_admission_compilations", "action_capacity_compilations",
    "static_inclusion_compilations", "static_inclusion_adoption",
    "attribute_execution_budget", "energy_settlement_policy",
)
_ROTATION_FIELDS = (
    "status", "damage_interpretation", "automatic_energy_delivery", "panel_bindings", "issues",
    "unchecked_constraints", "cycles", "gaps", "action_capacity_consumption", "action_admission_execution",
)


def _section_rows(result, section):
    value = result
    for key in section.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value if isinstance(value, list) else []


def _mechanics_preview(result):
    preview = {key: result[key] for key in _MECHANICS_FIELDS if key in result}
    preview["detail_sections"] = [{"key": key, "label": label, "total": len(rows)}
                                  for key, label in _MECHANICS_SECTIONS.items()
                                  if (rows := _section_rows(result, key))]
    rotation = result.get("rotation_check")
    if isinstance(rotation, dict):
        preview["rotation_check"] = {key: rotation[key] for key in _ROTATION_FIELDS if key in rotation}
    elemental = result.get("elemental")
    if isinstance(elemental, dict):
        preview["elemental"] = {key: elemental[key] for key in
                                ("evidence_tier", "reference_contract", "reference_acceptance") if key in elemental}
    return preview


class ViewedReportDeliveries:
    def __init__(self, database_path):
        self.database_path = database_path
        self._guard = Lock()
        self._entries = OrderedDict()

    def retain(self, calculated, *, candidate_session=None, session_id=None, _session=None):
        """Accept a service-owned result and its proof, never a Renderer object."""
        result, receipt = calculated["result"], calculated["result_receipt"]
        formal_audit = _validate_result_export(result, receipt, candidate_session=candidate_session, _session=_session)
        return self._retain(calculated, formal_audit, None, session_id, True)

    def _retain_verified_saved(self, calculated, *, original_path, read_only):
        """Only the full-package reader may retain its verified document here."""
        from genshin_app.equipment.calculation_overrides import (
            validate_execution_result_receipt,
        )
        from genshin_app.export import _formal_audit_from_verified_result

        result, receipt = calculated["result"], calculated["result_receipt"]
        # This standalone export requirement is not implied by the package reader.
        validate_execution_result_receipt(result, receipt)
        formal_audit = None if read_only else _formal_audit_from_verified_result(result, receipt)
        return self._retain(calculated, formal_audit, original_path, None, not read_only)

    def _retain(self, calculated, formal_audit, original_path, session_id, export_allowed):
        context = calculated.get("display_context")
        names = display_names(calculated["plan"], context["catalog"]) if context else None
        handle = token_urlsafe(24)
        with self._guard:
            self._entries[handle] = ViewedReport(calculated, names, formal_audit, original_path, session_id, export_allowed)
            while len(self._entries) > 3:
                self._entries.popitem(last=False)
        return handle

    @staticmethod
    def summary(calculated, handle, *, export_allowed=True):
        result = calculated["result"]
        template = calculated["plan"].get("manual_template", {})
        receipt = calculated["result_receipt"]
        action = result.get("action_materialization_receipt") or (result.get("action_automation_audit") or {}).get("materialization_receipt")
        # Materialization rows remain in diagnostics. A small status copy is sufficient for the report header.
        action_status = {key: action[key] for key in ("contract", "contract_id", "status", "calculation_class")
                         if key in action} if isinstance(action, dict) else None
        return {
            "result": {
                **{key: result[key] for key in _SUMMARY_FIELDS if key in result},
                "reported_hit_count": len(result.get("hits", [])),
                "external_hit_count": len((result.get("external_damage") or {}).get("items", [])),
                "uses_provisional_data": bool(receipt.get("provisional_entity_usage_receipt")),
                "has_presentation_diagnostics": bool(result.get("presentation_issues")
                    or (result.get("upstream_semantics") or {}).get("conditional_semantics")),
                "action_materialization_receipt": action_status,
            },
            "result_receipt": {key: receipt[key] for key in
                               ("result_fingerprint", "calculated_at", "engine_version", "database_fingerprint")
                               if key in receipt},
            "plan": {"manual_template": {key: template[key] for key in ("meta", "characters", "loop_duration")
                                          if key in template}},
            "report_handle": handle,
            "report_export_allowed": export_allowed,
            "report_projection": "summary",
        }

    def lookup(self, handle):
        with self._guard:
            report = self._entries.get(handle) if isinstance(handle, str) else None
            if report is not None:
                self._entries.move_to_end(handle)
        if report is None:
            raise PlanError("这份计算结果已离开当前窗口的可导出范围。请重新打开已保存的完整结果。", "report_delivery_unavailable")
        return report

    def read_section(self, handle, section, offset=0):
        calculated = self.lookup(handle).calculated
        result = calculated["result"]
        if type(offset) is not int or offset < 0:
            raise PlanError("报告页码不正确。", "report_page_invalid")
        if section == "hits" or (isinstance(section, str) and section in _MECHANICS_SECTIONS):
            rows = _section_rows(result, section)
            if offset > len(rows):
                raise PlanError("报告明细页超出当前结果范围。", "report_page_invalid")
            size = _REPORT_PAGE_SIZE
            return {"items": rows[offset:offset+size], "total": len(rows),
                    "next_offset": offset+size if offset+size < len(rows) else None}
        if offset != 0:
            raise PlanError("此报告部分没有分页。", "report_page_invalid")
        if section == "damage":
            return {"external_damage": result.get("external_damage")}
        if section == "effects":
            return {key: result[key] for key in _EFFECT_FIELDS if key in result}
        if section == "mechanics":
            return _mechanics_preview(result)
        if section == "diagnostics":
            return {"result": {key: result[key] for key in _DIAGNOSTIC_FIELDS if key in result},
                    "template": calculated["plan"].get("manual_template"),
                    "equipment_summary": calculated.get("equipment_summary")}
        raise PlanError("报告部分不受支持。", "report_section_invalid")

    def publish(self, report, fmt, path, *, expected_file_sha=None, candidate_session=None):
        from genshin_app.equipment.provisional_entities import (
            validate_current_session_usage_receipt,
        )

        if not report.export_allowed:
            raise PlanError("历史结果中的临时公共资料只能查看。", "report_export_read_only")
        if fmt not in EXPORT_FORMATS or not isinstance(path, str) or Path(path).suffix.lower() != f".{fmt}":
            raise PlanError("所选文件与导出格式不一致。", "report_export_format")
        validate_database_outputs([path], self.database_path)
        result, receipt = report.calculated["result"], report.calculated["result_receipt"]
        validate_current_session_usage_receipt(result, receipt, candidate_session)
        if file_sha(path) != expected_file_sha:
            raise PlanError("目标文件在选定后已变化，请重新选择。", "file_conflict")
        if fmt == "json":
            # Keep the whole accepted snapshot so ordinary Open can read it back.
            payload = calculated_package_bytes(report.calculated)
            if len(payload) > CALCULATED_ARTIFACT_BYTES:
                raise PlanError("完整计算包超过当前版本的文件容量。", "report_export_capacity")
        else:
            payload = _encode_validated_result(result, receipt, fmt, report.names,
                report.calculated.get("equipment_summary"), formal_audit=report.audit)
        sources = [self.database_path] + ([report.original_path] if report.original_path else [])
        published = publish_files([(path, payload)], expected_shas={path: expected_file_sha}, input_paths=sources)
        return {"path": published["published_paths"][0], "result_fingerprint": receipt["result_fingerprint"], "format": fmt}
