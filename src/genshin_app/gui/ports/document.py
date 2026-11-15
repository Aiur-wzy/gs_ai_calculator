"""DocumentGuiPort: concrete domain adapter; no GUI Facade callbacks."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from genshin_app.export import EXPORT_FORMATS, export_result
from genshin_app.output_policy import ordinary_gui_output
from genshin_app.presentation import display_names
from genshin_engine import PatchError, TemplateError, calculate, validate_template

from .responses import GuiResponses


class DocumentGuiPort(GuiResponses):
    def __init__(self, data_dir, plans_dir, database_path, *, display_catalog):
        self.data_dir, self.plans_dir = Path(data_dir), Path(plans_dir)
        self._db_path = Path(database_path)
        self.get_display_catalog = display_catalog

    @ordinary_gui_output
    def _whiteboard_call(self, method, *args, **kwargs):
        from genshin_app import whiteboard
        from genshin_app.equipment.schema import PlanError
        try:
            return self._ok(getattr(whiteboard, method)(*args, **kwargs))
        except PlanError as exc:
            return {"code": 1, "message": str(exc), "reason": exc.reason, "details": exc.details}
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
            return self._err(f"白板操作失败：{exc}")

    def whiteboard_capabilities(self):
        return self._whiteboard_call("whiteboard_capabilities")

    def whiteboard_new(self):
        from genshin_app.whiteboard import new_whiteboard
        return self._whiteboard_call("whiteboard_response", new_whiteboard())

    def whiteboard_validate(self, board):
        return self._whiteboard_call("whiteboard_response", board)

    def whiteboard_load(self, path):
        return self._whiteboard_call("load_whiteboard", path)

    def whiteboard_save(self, path, board, expected_file_sha=None):
        return self._whiteboard_call("save_whiteboard", path, board, expected_file_sha, db_path=self._db_path)

    def whiteboard_handoff(self, board, output_dir):
        return self._whiteboard_call("export_handoff", board, output_dir, self._db_path)

    def whiteboard_read_result(self, board, path, expected_file_sha=None):
        return self._whiteboard_call(
            "read_whiteboard_result", board, path, expected_file_sha
        )

    def open_saved_calculation(self, path, expected_file_sha=None, *, reports=None):
        """Verify a complete calculation file without touching the active document."""
        from genshin_app.calculation_capacity import CALCULATED_ARTIFACT_BYTES
        from genshin_app.equipment.delivery import (
            PACKAGE_KIND,
            PACKAGE_SCHEMA,
            validate_delivery_calculated,
        )
        from genshin_app.equipment.execution_verification import (
            ExecutionVerificationSession,
        )
        from genshin_app.equipment.schema import PlanError
        from genshin_app.whiteboard_store import _read

        try:
            package, file_sha, selected_path = _read(path, CALCULATED_ARTIFACT_BYTES)
            if expected_file_sha is not None and file_sha != expected_file_sha:
                raise PlanError("计算文件在选择后已变化，请重新打开。", "file_conflict")
            if type(package) is not dict or package.get("kind") != PACKAGE_KIND:
                raise PlanError("所选文件不是可识别的完整计算包。", "invalid_calculation_package")
            package_schema = package.get("package_schema")
            if isinstance(package_schema, str) and package_schema != PACKAGE_SCHEMA:
                raise PlanError("这个计算包使用当前 App 不支持的版本。", "saved_calculation_version_incompatible", {
                    "migration_prompt": "请根据当前 App、数据库和拉表说明核对我提供的旧计算文件，"
                    "保留原文件，提取仍可用的队伍、配装和动作，生成一份适用于当前版本的新方案，再重新校验并计算。",
                })
            if package_schema != PACKAGE_SCHEMA or type(package.get("calculated")) is not dict:
                raise PlanError("计算包缺少必要的计算数据或格式标记。", "invalid_calculation_package")
            calculated = package["calculated"]
            with ExecutionVerificationSession() as verification:
                if package.get("integrity") != validate_delivery_calculated(calculated, _session=verification)["integrity"]:
                    raise PlanError("计算包内容与保存的校验记录不一致。", "invalid_calculation_package")
            read_only = bool(calculated["result_receipt"].get("provisional_entity_usage_receipt"))
            view = (reports.summary(calculated, reports._retain_verified_saved(calculated,
                    original_path=selected_path, read_only=read_only), export_allowed=not read_only)
                    if reports else {"result": calculated["result"],
                        "result_receipt": calculated["result_receipt"], "plan": calculated["plan"]})
            return self._ok({**view, "file_sha": file_sha, "path": selected_path,
                             "read_only": True, "validation": "full_calculation_package"})
        except PlanError as error:
            return {"code": 1, "message": str(error), "reason": error.reason, "details": error.details}
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            return {"code": 1, "message": "无法读取或验证这个计算结果文件。原文件和当前文档均已保留。",
                    "reason": "invalid_calculation_package"}

    def whiteboard_apply_equipment(self, board, previous_board=None, previous_result_path=None):
        return self._whiteboard_call("apply_whiteboard_equipment", board, previous_board,
                                    previous_result_path, db_path=self._db_path)

    def inspect_plan_file(self, path: str) -> dict:
        """Identify the file without silently converting legacy templates."""
        from genshin_app.agent_chat.candidate_input_draft import KIND as DRAFT_KIND
        from genshin_app.agent_chat.candidate_input_draft_store import (
            read_input_file_bytes,
        )
        from genshin_app.equipment.schema import PlanError

        try:
            raw = read_input_file_bytes(path)
            value = json.loads(raw.decode("utf-8-sig"))
            kind = value.get("kind") if isinstance(value, dict) else None
            # A kind/SHA probe must not interpret an unknown draft's payload as
            # an old native/legacy template (including its display-name fields).
            manual = value.get("manual_template", value) if isinstance(value, dict) and kind != DRAFT_KIND else {}
            meta = manual.get("meta") if isinstance(manual, dict) else None
            name = value.get("title") if isinstance(value, dict) and value.get("kind") == "genshin-whiteboard" else (
                meta.get("name") if isinstance(meta, dict) else None)
            return self._ok({"kind": kind,
                             "file_sha": hashlib.sha256(raw).hexdigest(),
                             "display_name": name if isinstance(name, str) else None})
        except (OSError, UnicodeError, ValueError, TypeError, RecursionError, PlanError) as e:
            return self._err(f"读取方案失败 {path}: {e}")

    def list_plan_details(self) -> dict:
        r = self.list_plans()
        if r["code"]:
            return r
        items = []
        for name in r["data"]["plans"]:
            check = self.inspect_plan_file(str(self.plans_dir / name))
            items.append({"name": name, "kind": check.get("data", {}).get("kind"),
                          "display_name": check.get("data", {}).get("display_name"),
                          "error": check.get("message")})
        return self._ok({"dir": str(self.plans_dir), "items": items})

    def list_plans(self) -> dict:
        """方案库（R4 文件式）：列出数据目录 plans/ 下的模板文件。"""
        try:
            self.plans_dir.mkdir(parents=True, exist_ok=True)
            plans = sorted(p.name for p in self.plans_dir.glob("*.json"))
        except OSError as e:
            return self._err(f"读取方案目录失败 {self.plans_dir}: {e}")
        return self._ok({"dir": str(self.plans_dir), "plans": plans})

    def new_template(self) -> dict:
        """最小合法模板脚手架（必须过引擎校验）。"""
        template = {
            "version": 1,
            "form": "resolved",
            "meta": {"name": "新方案", "version_line": "release"},
            "enemy": {"level": 90, "resistance": {"default": 0.1}},
            "characters": [
                {"id": "char1", "name": "角色一", "level": 90,
                 "stats": {"atk": 2000, "em": 0, "crit_rate": 0.05, "crit_dmg": 0.5,
                           "dmg_bonus": {}}},
            ],
            "switches": [{"t": 0.0, "character": "char1"}],
            "buffs": [],
            "hits": [
                {"t": 1.0, "character": "char1", "skill": "skill", "hit_name": "一段",
                 "multiplier": 1.0, "damage_system": "classic", "element": "physical"},
            ],
            "loop_duration": 20.0,
        }
        validate_template(template)
        return self._ok({"template": template})

    def load_template(self, path: str) -> dict:
        try:
            p = Path(path)
            if not p.exists():
                return self._err(f"模板文件不存在: {p}")
            raw = p.read_bytes()
            template = json.loads(raw.decode("utf-8-sig"))
        except json.JSONDecodeError as e:
            return self._err(f"JSON 解析失败: {e}")
        except (OSError, UnicodeError, ValueError, TypeError) as e:
            return self._err(f"读取模板失败 {path}: {e}")
        try:
            validate_template(template)
        except TemplateError as e:
            return self._err(f"模板校验失败: {e}")
        return self._ok({"path": str(p), "template": template,
                         "file_sha": hashlib.sha256(raw).hexdigest()})

    @ordinary_gui_output
    def save_template(self, path: str, template: dict, expected_file_sha=None) -> dict:
        from genshin_app.equipment.files import file_sha, publish_files
        from genshin_app.equipment.schema import PlanError
        try:
            validate_template(template)
        except TemplateError as e:
            return self._err(f"模板校验失败，未写入: {e}")
        try:
            p = Path(path)
            observed = file_sha(p) if expected_file_sha is None else expected_file_sha
            published = publish_files([(p, json.dumps(template, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"))],
                                      overwrite=True, expected_shas={p: observed})
        except PlanError as exc:
            return {"code": 1, "message": f"保存模板失败 {path}: {exc}", "reason": exc.reason, "details": exc.details}
        except (OSError, UnicodeError, ValueError, TypeError) as e:
            return self._err(f"保存模板失败 {path}: {e}")
        return self._ok({"path": str(p), "file_sha": published["file_shas"][str(p.resolve())]})

    def run_calc(self, template: dict) -> dict:
        """进程内调引擎（与 CLI engine calc 同一个 calculate，保证口径一致）。"""
        try:
            result = calculate(template)
            from genshin_app.rotation_check import attach_rotation_check
            result = attach_rotation_check(result, template)
        except (TemplateError, PatchError) as e:
            return self._err(f"模板校验失败: {e}")
        except KeyError as e:
            return self._err(f"模板缺少必要字段: {e}")
        except ValueError as e:
            return self._err(f"计算输入无效: {e}")
        return self._ok({"result": result})

    def rotation_capabilities(self) -> dict:
        from genshin_engine.rotation import rotation_capabilities
        return self._ok(rotation_capabilities())

    def check_rotation(self, document: dict) -> dict:
        from genshin_app.rotation_check import check_document
        try:
            return self._ok(check_document(document, self._db_path))
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
            return self._err(f"动作与能量检查失败：{exc}")

    def load_rotation_model(self, path: str) -> dict:
        from genshin_app.rotation_check import read_check_document
        from genshin_engine.rotation import validate_rotation_model
        try:
            document = read_check_document(path)
            if not isinstance(document, dict):
                raise TypeError("模型必须是对象")
            model = document.get("rotation_model", document)
            check = validate_rotation_model(model)
            if check["status"] == "invalid":
                return {"code": 1, "message": "动作模型结构不合法", "details": check["issues"]}
            return self._ok({"rotation_model": model})
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return self._err(f"读取动作模型失败：{exc}")

    @ordinary_gui_output
    def export_result(self, result: dict, fmt: str, name: str = "", selected_path=None) -> dict:
        """导出结果到数据目录 exports/，文件名 <名称>_<时间戳>.<fmt>。"""
        if fmt not in EXPORT_FORMATS:
            return self._err(f"未知导出格式 {fmt!r}（支持 {EXPORT_FORMATS}）")
        if not isinstance(name, str) or not isinstance(result, dict):
            return self._err("导出结果必须为字典，名称必须为字符串")
        safe = re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "result"
        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d_%H%M%S")
        if selected_path is not None and (not isinstance(selected_path, str) or Path(selected_path).suffix.lower() != f".{fmt}"):
            return self._err("所选文件与导出格式不一致")
        path = Path(selected_path) if selected_path else self.data_dir / "exports" / f"{safe}_{stamp}.{fmt}"
        try:
            gui_input = result.get("gui_input")
            source = gui_input.get("template", {}) if isinstance(gui_input, dict) else {}
            if not isinstance(source, dict):
                return self._err("导出结果内的方案输入必须是对象")
            manual = source.get("manual_template", source)
            line = manual.get("meta", {}).get("version_line", "release")
            catalog = self.get_display_catalog(line) if source else {}
            display = display_names(source, catalog.get("data")) if source else None
            export_result(result, fmt, path, display=display)
        except (OSError, UnicodeError, ValueError) as e:
            return self._err(f"导出失败 {path}: {e}")
        return self._ok({"path": str(path)})

