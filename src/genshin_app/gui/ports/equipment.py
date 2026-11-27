"""EquipmentGuiPort: concrete domain adapter; no GUI Facade callbacks."""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from genshin_app.export import EXPORT_FORMATS
from genshin_app.output_policy import ordinary_gui_output
from genshin_engine.execution_control import ExecutionCancelled

from .responses import GuiResponses


class EquipmentGuiPort(GuiResponses):
    def __init__(self, data_dir, database_path, *, manager_provider):
        self.data_dir, self._db_path = Path(data_dir), Path(database_path)
        self._get_agent_manager = manager_provider
        from .report_delivery import ViewedReportDeliveries
        self.viewed_reports = ViewedReportDeliveries(self._db_path)

    @ordinary_gui_output
    def _equipment_call(self, method: str, *args, **kwargs) -> dict:
        """Shared native-plan service; never trust a front-end resolved/result cache."""
        from genshin_app import equipment

        try:
            return self._ok(getattr(equipment, method)(*args, **kwargs))
        except ExecutionCancelled as e:
            from genshin_app.execution_cancellation import cancellation_response
            return cancellation_response(e, operation='gui.'+method)
        except equipment.PlanError as e:
            from genshin_app.domain_errors import (
                classify_domain_failure,
                tool_failure_guidance,
            )
            return {"code": 1, "message": str(e), "reason": e.reason, "details": e.details,
                    "operation": "gui."+method,
                    **classify_domain_failure(e.reason, details=e.details),
                    **tool_failure_guidance(e.reason, details=e.details)}
        except (OSError, UnicodeError, ValueError, TypeError, sqlite3.Error) as e:
            return self._err(f"{method} 失败: {e}")

    @contextmanager
    def _candidate_session_boundary(self, session_id, plan):
        """Resolve an optional Renderer identity to an in-process capability.

        No session object is serialized to JavaScript.  Without an explicit
        native session identity, service-level D2 validation remains fail
        closed exactly as it is for CLI and standalone callers.
        """

        if session_id is None:
            yield None
            return
        from genshin_app.equipment.schema import PlanError

        if not isinstance(session_id, str) or not session_id:
            raise PlanError(
                "原生Agent会话身份不合法",
                "native_candidate_session_invalid",
            )
        manager = self._get_agent_manager()
        boundary = getattr(manager, "candidate_session_boundary", None)
        if not callable(boundary):
            raise PlanError(
                "当前Agent提供方不能为计算提供会话权限",
                "native_candidate_session_unavailable",
            )
        with boundary(session_id, plan) as candidate_session:
            yield candidate_session

    def plan_capabilities(self) -> dict:
        return self._equipment_call("plan_capabilities")

    def action_capacity_model_candidates(self, plan: dict, actor_id: str,
                                         ability_ids: list[str], cursor=None,
                                         page_size: int = 16, view: str = "all") -> dict:
        """Discover references from the current plan; never choose or write one."""
        from genshin_app import equipment

        try:
            return self._ok(equipment.action_capacity_model_candidates(
                plan, actor_id, ability_ids, self._db_path,
                cursor=cursor, page_size=page_size, view=view,
            ))
        except equipment.PlanError as exc:
            return {"code": 1, "message": str(exc), "reason": exc.reason,
                    "details": exc.details}
        except (OSError, UnicodeError, ValueError, TypeError, sqlite3.Error) as exc:
            return {"code": 1, "message": str(exc),
                    "reason": getattr(exc, "reason", "capacity_catalog_failed"),
                    "details": getattr(exc, "details", None)}

    def action_admission_model_candidates(self, plan: dict, actor_id: str,
                                          ability_id: str, cursor=None,
                                          page_size: int = 16, view: str = "all") -> dict:
        """Discover reviewed action-gate references; never compile gate values."""
        from genshin_app import equipment

        try:
            return self._ok(equipment.action_admission_model_candidates(
                plan, actor_id, ability_id, self._db_path,
                cursor=cursor, page_size=page_size, view=view,
            ))
        except equipment.PlanError as exc:
            return {"code": 1, "message": str(exc), "reason": exc.reason,
                    "details": exc.details}
        except (OSError, UnicodeError, ValueError, TypeError, sqlite3.Error) as exc:
            return {"code": 1, "message": str(exc),
                    "reason": getattr(exc, "code", getattr(exc, "reason", "action_admission_catalog_failed")),
                    "details": getattr(exc, "details", None)}

    def action_admission_preview(self, plan: dict, request: dict) -> dict:
        """Fresh detached preview; applying the returned proposal stays a UI decision."""
        from genshin_app import equipment

        try:
            return self._ok(equipment.action_admission_preview(plan, request, self._db_path))
        except equipment.PlanError as exc:
            return {"code": 1, "message": str(exc), "reason": exc.reason,
                    "details": exc.details}
        except (OSError, UnicodeError, ValueError, TypeError, sqlite3.Error) as exc:
            return {"code": 1, "message": str(exc),
                    "reason": getattr(exc, "code", getattr(exc, "reason", "action_admission_preview_failed")),
                    "details": getattr(exc, "details", None)}

    def whiteboard_action_admission_model_candidates(self, board: dict, actor_id: str,
                                                     action_id: str, cursor=None,
                                                     page_size: int = 16, view: str = "all") -> dict:
        """Discover reviewed references from a typed whiteboard v3 burst action."""
        from genshin_app import equipment

        try:
            return self._ok(equipment.whiteboard_action_admission_model_candidates(
                board, actor_id, action_id, self._db_path,
                cursor=cursor, page_size=page_size, view=view,
            ))
        except equipment.PlanError as exc:
            return {"code": 1, "message": str(exc), "reason": exc.reason,
                    "details": exc.details}
        except (OSError, UnicodeError, ValueError, TypeError, sqlite3.Error) as exc:
            return {"code": 1, "message": str(exc),
                    "reason": getattr(exc, "code", getattr(exc, "reason", "action_admission_catalog_failed")),
                    "details": getattr(exc, "details", None)}

    def whiteboard_action_capacity_model_candidates(self, board: dict, actor_id: str,
                                                    action_ids: list[str], cursor=None,
                                                    page_size: int = 16, view: str = "all") -> dict:
        """Discover references from explicit whiteboard fields; never mutate the draft."""
        from genshin_app import equipment

        try:
            return self._ok(equipment.whiteboard_action_capacity_model_candidates(
                board, actor_id, action_ids, self._db_path,
                cursor=cursor, page_size=page_size, view=view,
            ))
        except equipment.PlanError as exc:
            return {"code": 1, "message": str(exc), "reason": exc.reason,
                    "details": exc.details}
        except (OSError, UnicodeError, ValueError, TypeError, sqlite3.Error) as exc:
            return {"code": 1, "message": str(exc),
                    "reason": getattr(exc, "reason", "capacity_catalog_failed"),
                    "details": getattr(exc, "details", None)}

    def new_plan(self, template: dict | None = None) -> dict:
        return self._equipment_call("new_plan", template)

    def enable_mechanics(self, plan: dict) -> dict:
        return self._equipment_call("enable_mechanics", plan)

    def assemble_attack_source(self, plan: dict, request: dict) -> dict:
        """Preview native source wiring without accepting facts or saving files."""
        return self._equipment_call("assemble_attack_source", plan, request, self._db_path)

    def assemble_periodic_source(self, plan: dict, request: dict) -> dict:
        """Preview source-selected channels without modifying the action axis."""
        return self._equipment_call("assemble_periodic_source", plan, request, self._db_path)

    def mechanics_capabilities(self) -> dict:
        from genshin_engine.mechanics_schema import mechanics_capabilities
        return self._ok(mechanics_capabilities())

    def validate_mechanics_model(self, model: dict) -> dict:
        from genshin_engine.mechanics import validate_mechanics_model
        try:
            return self._ok(validate_mechanics_model(model))
        except (ValueError, TypeError, KeyError) as exc:
            return self._err(str(exc))

    def query_context(self, request: dict) -> dict:
        from genshin_db.context_query import query_context
        try:
            return self._ok(query_context(self._db_path, request))
        except (ValueError, OSError, sqlite3.Error) as exc:
            return self._err(f"定向查询失败：{exc}")

    def equipment_reference_panel(self, request: dict, version_line: str = "release") -> dict:
        return self._equipment_call("reference_panel", self._db_path, request, version_line=version_line)

    def query_runtime(self, request: dict) -> dict:
        from genshin_db.runtime_queries import query_runtime_public
        try:
            return self._ok(query_runtime_public(self._db_path, request))
        except (ValueError, OSError, sqlite3.Error) as exc:
            return {"code": 1, "message": f"运行资料查询失败：{exc}",
                    "reason": getattr(exc, "reason", "runtime_query_failed"), "details": getattr(exc, "details", None)}

    @ordinary_gui_output
    def export_formula_workbook(
        self,
        plan: dict,
        name: str = "",
        session_id: str | None = None,
    ) -> dict:
        from genshin_app.agent_chat.transport import TransportError
        from genshin_app.equipment.files import publish_files
        from genshin_app.equipment.schema import PlanError
        from genshin_app.equipment.service import run_plan_calc
        from genshin_app.formula_workbook import (
            build_formula_workbook,
            formula_workbook_support,
        )
        try:
            if not isinstance(name, str):
                raise TypeError("工作簿名称需要文本")
            safe = re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "方案"
            stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d_%H%M%S_%f")
            path = self.data_dir / "exports" / f"{safe}_活公式_{stamp}.xlsx"
            with self._candidate_session_boundary(session_id, plan) as candidate_session:
                calculated = run_plan_calc(
                    plan,
                    self._db_path,
                    candidate_session=candidate_session,
                )
                from genshin_app.calculation_completion import (
                    require_calculation_complete,
                )
                require_calculation_complete(calculated)
                publish_files([(
                    path,
                    build_formula_workbook(
                        calculated,
                        candidate_session=candidate_session,
                    ),
                )])
            return self._ok({"path": str(path.resolve()), "support": formula_workbook_support(calculated),
                             "result_receipt": calculated["result_receipt"]})
        except PlanError as exc:
            return {"code": 1, "message": str(exc), "reason": exc.reason, "details": exc.details}
        except TransportError as exc:
            return {"code": 1, "message": exc.message, "reason": exc.code}
        except (ValueError, TypeError, OSError, UnicodeError, sqlite3.Error) as exc:
            return self._err(f"活公式导出失败：{exc}")

    def load_plan(self, path: str) -> dict:
        return self._equipment_call("load_plan", path, self._db_path)

    def save_plan(self, path: str, plan: dict, expected_file_sha: str | None = None) -> dict:
        return self._equipment_call("save_plan", path, plan, expected_file_sha)

    def compose_equipment_plan(self, plan: dict) -> dict:
        return self._equipment_call("compose_equipment_plan", plan, self._db_path)

    def prepare_attribute_review(self, plan: dict, note: str) -> dict:
        return self._equipment_call("prepare_attribute_review", plan, note, self._db_path)

    def preview_equipment_edit(self, plan: dict, previous_plan: dict) -> dict:
        return self._equipment_call("preview_equipment_edit", plan, previous_plan, self._db_path)

    def apply_data_update(self, plan: dict) -> dict:
        return self._equipment_call("apply_data_update", plan, self._db_path)

    def detach_character(self, plan: dict, character_id: str) -> dict:
        return self._equipment_call("detach_character", plan, character_id, self._db_path)

    def prepare_character_input(self, db_character_id: int, level: int = 90,
                                version_line: str = "release") -> dict:
        return self._equipment_call("prepare_character_input", self._db_path,
                                    db_character_id, level, version_line)

    def character_panel_basis(self, db_character_id: int, level: int = 90,
                              version_line: str = "release", weapon: dict | None = None) -> dict:
        return self._equipment_call("character_panel_basis", self._db_path,
                                    db_character_id, level, version_line, weapon)

    def run_plan_calc(
        self,
        plan: dict,
        session_id: str | None = None,
    ) -> dict:
        from genshin_app import equipment
        from genshin_app.agent_chat.transport import TransportError

        try:
            with self._candidate_session_boundary(session_id, plan) as candidate_session:
                from genshin_app.calculation_completion import calculation_completion
                from genshin_app.equipment.execution_verification import (
                    ExecutionVerificationSession,
                )
                with ExecutionVerificationSession() as verification:
                    calculated = equipment.run_plan_calc(plan, self._db_path, capture_display=True,
                        candidate_session=candidate_session, _session=verification)
                    completion = calculation_completion(calculated)
                    if completion['task_admission'] != 'accepted':
                        return {'code': 1, 'message': '正式计算未完成；以下为诊断前缀。', 'reason': 'calculation_incomplete',
                                'details': {'completion': completion, 'diagnostic': calculated}}
                    return self._ok({**calculated, "report_handle": self.viewed_reports.retain(calculated,
                        candidate_session=candidate_session, session_id=session_id, _session=verification)})
        except equipment.PlanError as exc:
            from genshin_app.calculation_verification_failure import (
                verification_failure_artifact,
                verification_failure_response,
            )
            artifact = verification_failure_artifact(exc, plan)
            if artifact is not None:
                return verification_failure_response(exc, artifact)
            message = str(exc)
            if exc.reason == "mechanics_pending" and isinstance(exc.details, dict):
                issues = exc.details.get("issues") or []
                issue = next((row for row in issues if isinstance(row, dict)
                              and (row.get("message") or row.get("code"))), None)
                if issue:
                    explanation = issue.get("message") or issue["code"]
                    location = issue.get("path") or issue.get("event_id")
                    message = f"{message}。{explanation}"
                    if location:
                        message += f"（{location}）"
                    if issue.get("code") and issue["code"] != explanation:
                        message += f" [{issue['code']}]"
            return {"code": 1, "message": message, "reason": exc.reason,
                    "details": exc.details}
        except TransportError as exc:
            return {"code": 1, "message": exc.message, "reason": exc.code}
        except (OSError, UnicodeError, ValueError, TypeError, sqlite3.Error) as exc:
            from genshin_app.calculation_verification_failure import (
                verification_failure_artifact,
                verification_failure_response,
            )
            artifact = verification_failure_artifact(exc, plan)
            if artifact is not None:
                return verification_failure_response(exc, artifact)
            return self._err(f"run_plan_calc 失败: {exc}")

    def inspect_calculation_diagnostics(self, value: dict, request: dict | None = None) -> dict:
        """Inspect already loaded data; no filesystem access or recalculation."""
        from genshin_app.calculation_diagnostics import inspect_calculation_diagnostics
        from genshin_app.equipment.schema import PlanError
        try:
            return self._ok(inspect_calculation_diagnostics(value, request))
        except PlanError as exc:
            return {"code": 1, "message": str(exc), "reason": exc.reason, "details": exc.details}

    @ordinary_gui_output
    def export_viewed_report(self, handle, fmt, path, expected_file_sha=None):
        from genshin_app.equipment import PlanError
        from genshin_app.equipment.provisional_entities import ProvisionalEntityError

        try:
            report = self.viewed_reports.lookup(handle)
            with self._candidate_session_boundary(report.session_id, report.calculated["plan"]) as candidate_session:
                return self._ok(self.viewed_reports.publish(report, fmt, path, expected_file_sha=expected_file_sha,
                    candidate_session=candidate_session))
        except (PlanError, ProvisionalEntityError) as error:
            return {"code": 1, "message": str(error), "reason": error.reason, "details": error.details}
        except (OSError, ValueError, TypeError) as error:
            return self._err(f"写出结果失败：{error}")

    def read_viewed_report(self, handle, section, offset=0):
        from genshin_app.equipment import PlanError

        try:
            return self._ok(self.viewed_reports.read_section(handle, section, offset))
        except PlanError as error:
            return {"code": 1, "message": str(error), "reason": error.reason, "details": error.details}

    def export_plan_result(
        self,
        plan: dict,
        fmt: str,
        name: str = "",
        session_id: str | None = None,
    ) -> dict:
        if fmt not in EXPORT_FORMATS or not isinstance(name, str):
            return self._err("未知导出格式或无效名称")
        safe = re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "plan"
        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d_%H%M%S_%f")
        path = self.data_dir / "exports" / f"{safe}_{stamp}.{fmt}"
        from genshin_app import equipment
        from genshin_app.agent_chat.transport import TransportError

        try:
            with self._candidate_session_boundary(session_id, plan) as candidate_session:
                return self._ok(equipment.export_plan_result(
                    plan,
                    self._db_path,
                    fmt,
                    path,
                    candidate_session=candidate_session,
                ))
        except equipment.PlanError as exc:
            return {"code": 1, "message": str(exc), "reason": exc.reason,
                    "details": exc.details}
        except TransportError as exc:
            return {"code": 1, "message": exc.message, "reason": exc.code}
        except (OSError, UnicodeError, ValueError, TypeError, sqlite3.Error) as exc:
            return self._err(f"export_plan_result 失败: {exc}")

    def export_resolved_plan(self, plan: dict, path: str) -> dict:
        return self._equipment_call("export_resolved_plan", plan, self._db_path, path)

