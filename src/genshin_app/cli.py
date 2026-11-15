"""统一 CLI（app.md CLI 一期定稿）。

单源命令定义 COMMANDS 同时驱动 argparse 与 capabilities 自描述（ADR-0002）。
输出约定：默认 stdout JSON 统一包络 {"code","message","data"}；失败走 stderr + 非零退出码。
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from contextlib import nullcontext
from pathlib import Path

from genshin_app.verification_scope import verification_scope
from genshin_app.version import APP_VERSION

CLI_VERSION = APP_VERSION  # 版本号唯一来源在 version.py（G5）

# Current startup admission can recover or rewrite sidecars even for reads.
# Queue these finite CLI queries across processes until admission and reading
# can share a pinned, read-only release identity.
_SERIAL_DATABASE_QUERIES = frozenset({
    ("db", "context"), ("db", "appendix"), ("db", "catalog"),
    ("db", "panel-basis"), ("db", "runtime"), ("equipment", "panel-reference"),
})


class CLIUsageError(ValueError):
    """参数错误也走 CLI 的 JSON 错误包络。"""

    def __init__(self, message: str, *, reason: str | None = None, details=None):
        super().__init__(message)
        self.reason = reason or "cli_request_invalid"
        self.details = details


class JSONArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CLIUsageError(message)


def _require_runtime_database(data_dir: str | Path) -> dict:
    """Apply the same App/database admission used by the normal GUI.

    Database maintenance commands deliberately do not call this helper: they
    operate on an isolated build/update path whose job is to create the next
    reviewed contract.  Runtime query, calculation and serving commands fail
    closed before importing their engine/service implementation.
    """

    from genshin_app.cli_session import reuse_admission
    reused = reuse_admission(data_dir)
    if reused is not None:
        return reused
    from genshin_app.database_bootstrap import prepare_database_startup
    from genshin_app.database_transaction import profile_writer
    from genshin_app.paths import ensure_data_dir

    with profile_writer(Path(data_dir), timeout=None):
        from genshin_app.product_database_setup import build_database_setup
        if build_database_setup(data_dir) is not None:
            from genshin_app.equipment.schema import PlanError
            raise PlanError("请先安装并校验数据库。", "database_not_installed")
        directory = ensure_data_dir(Path(data_dir))
        return prepare_database_startup(directory)


def _queued_database_read(data_dir):
    from genshin_app.database_transaction import profile_writer

    return profile_writer(Path(data_dir), timeout=None)


# ---------- 命令处理器（返回 data dict；抛异常 → 统一错误包络） ----------

def _reject_deprecated_database_write(operation: str) -> None:
    from genshin_db.live_mutation_policy import REASON, capability, rejection_message

    raise CLIUsageError(
        rejection_message(operation), reason=REASON, details=capability(operation)
    )

def _cmd_capabilities(args: argparse.Namespace) -> dict:
    document = _capabilities_doc()
    if any(getattr(args, name, None) is not None for name in ("command", "section", "pointer")):
        from genshin_app.cli_capability_query import query_capabilities
        return query_capabilities(document, command=args.command, section=args.section, pointer=args.pointer)
    return document


def _cmd_system_diagnostic(args: argparse.Namespace) -> dict:
    from genshin_app.app_fault_store import read_system_diagnostic
    if not args.id:
        raise CLIUsageError("请提供 --id 诊断编号", reason="invalid_diagnostic_id")
    return read_system_diagnostic(args.data_dir, args.id)


def _cmd_system_check_update(args: argparse.Namespace) -> dict:
    from genshin_app.product_database_setup import build_database_setup
    from genshin_app.release_update_contracts import ReleaseUpdateSettings
    from genshin_app.release_update_service import ReleaseUpdateService

    directory = Path(args.data_dir).resolve()
    service = ReleaseUpdateService(
        ReleaseUpdateSettings(directory, directory / "genshin.db", None, False, False, False, False),
        database_setup=build_database_setup(directory))
    return service.check_database_update()


def _cmd_whiteboard(args: argparse.Namespace) -> dict:
    from genshin_app.whiteboard import (
        export_handoff,
        load_whiteboard,
        new_whiteboard,
        read_whiteboard_result,
        save_whiteboard,
        whiteboard_response,
    )
    operation = args._whiteboard
    if operation == "new":
        board = new_whiteboard()
        if args.output:
            _require_runtime_database(args.data_dir)
        return (save_whiteboard(args.output, board, db_path=Path(args.data_dir) / "genshin.db")
                if args.output else whiteboard_response(board))
    if not args.input:
        raise CLIUsageError("白板操作需要 --input 白板JSON")
    loaded = load_whiteboard(args.input)
    if operation == "validate":
        return {key: value for key, value in loaded.items() if key != "board"}
    if operation == "handoff":
        if not args.output_dir:
            raise CLIUsageError("交接需要 --output-dir 新目录")
        _require_runtime_database(args.data_dir)
        return export_handoff(loaded["board"], args.output_dir, Path(args.data_dir) / "genshin.db")
    if not args.result:
        raise CLIUsageError("回流核验需要 --result 完整calculated.json")
    returned = read_whiteboard_result(loaded["board"], args.result)
    calculated = returned.pop("calculated")
    returned["summary"] = calculated["result"].get("calculation_totals", calculated["result"].get("summary"))
    returned["result_receipt"] = calculated["result_receipt"]
    return returned


def _cmd_agent_candidate(args: argparse.Namespace) -> dict:
    from genshin_app.agent_chat.native_cli_link import _decode, call_live_candidate
    from genshin_app.equipment.files import read_input_bytes
    from genshin_app.input_editing import read_input_snapshot

    if not args.connection or not args.session_id:
        raise CLIUsageError("需要当前会话提供的--connection与--session-id；不会另建候选或启动模型")
    request = {}
    if args.request:
        raw = read_input_bytes(args.request, maximum_bytes=64 * 1024 * 1024, field="/request")
        if len(raw) > 64 * 1024 * 1024:
            raise CLIUsageError("请求文件超过64MiB容量")
        request = _decode(raw)
        if not isinstance(request, dict):
            raise CLIUsageError("请求文件必须是JSON对象")
    if args._live_operation != "read" and not args.request:
        raise CLIUsageError("修改、组合或计算需要--request JSON文件")
    if args._live_operation == "edit" and request.get("mode") == "import":
        snapshot = read_input_snapshot(request["file_path"], request.get("expected_file_sha"))
        request = {**request, "mode": "replace", "input": snapshot["input"]}
        request.pop("file_path", None)
        request.pop("expected_file_sha", None)
    return call_live_candidate(args.connection, args._live_operation, args.session_id, request)


def _cmd_agent_gui_workflow(args: argparse.Namespace) -> dict:
    from genshin_app.agent_chat.dsh_gui_workflow_runner import (
        read_prompt_file,
        run_gui_agent_workflow,
    )

    if not args.prompt_file:
        raise CLIUsageError("GUI原生Agent工作流需要 --prompt-file UTF-8任务文本")
    if not args.output_dir:
        raise CLIUsageError("GUI原生Agent工作流需要 --output-dir 新目录")
    _require_runtime_database(args.data_dir)
    api_factory = None
    if args.provider_mode == "deterministic_provider_transport":
        # The reviewed OS acceptance replaces ``genshin_app.gui.api.GuiApi``
        # with a complete production composition.  Pass that resolved class
        # explicitly so the runner can distinguish this seam from an ordinary
        # product invocation; it still validates the App identity below.
        from genshin_app.gui.api import GuiApi

        api_factory = GuiApi
    return run_gui_agent_workflow(
        data_dir=args.data_dir,
        prompt=read_prompt_file(args.prompt_file),
        input_path=args.input,
        output_dir=args.output_dir,
        workflow=args.workflow,
        model=args.model,
        effort=args.effort,
        timeout_seconds=args.timeout_seconds,
        allow_draft=args.allow_draft,
        provider_mode=args.provider_mode,
        api_factory=api_factory,
        resume_session_id=getattr(args, "resume_session_id", None),
    )


def _cmd_agent_gui_webview_smoke(args: argparse.Namespace) -> dict:
    from genshin_app.agent_chat.dsh_gui_workflow_runner import read_prompt_file
    from genshin_app.gui.webview_smoke import (
        read_webview_expectation_file,
        run_gui_webview_smoke,
    )

    if not args.prompt_file:
        raise CLIUsageError("真实WebView smoke需要 --prompt-file UTF-8任务文本")
    if not args.output_dir:
        raise CLIUsageError("真实WebView smoke需要 --output-dir 新目录")
    _require_runtime_database(args.data_dir)
    return run_gui_webview_smoke(
        data_dir=args.data_dir,
        prompt=read_prompt_file(args.prompt_file),
        input_path=args.input,
        output_dir=args.output_dir,
        workflow=args.workflow,
        model=args.model,
        effort=args.effort,
        timeout_seconds=args.timeout_seconds,
        allow_draft=args.allow_draft,
        visible=args.visible,
        expectation=(
            read_webview_expectation_file(args.expectation_file)
            if args.expectation_file
            else None
        ),
    )


def _cmd_db_update(args: argparse.Namespace) -> dict:
    _reject_deprecated_database_write("db update")


def _cmd_db_status(args: argparse.Namespace) -> dict:
    from genshin_db.pipeline import LIVE_DB_NAME
    from genshin_db.queries import get_meta

    db_path = Path(args.data_dir) / LIVE_DB_NAME
    if not db_path.exists():
        raise CLIUsageError("数据库不存在，请选择已有数据库目录或安装已发布的数据库版本。",
                            reason="database_missing", details={"path": "/data_dir"})
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        lines = [
            r[0] for r in conn.execute("SELECT DISTINCT version_line FROM character").fetchall()
        ]
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("character", "skill", "talent", "skill_param", "text_map")
        }
        has_annotation = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='annotation'"
        ).fetchone()[0]
        annotations = {}
        if has_annotation:
            annotations = dict(
                conn.execute(
                    "SELECT status, COUNT(*) FROM annotation GROUP BY status"
                ).fetchall()
            )
        return {
            "db_path": str(db_path),
            "upstream_commit": meta.get("upstream_commit"),
            "game_version": meta.get("game_version"),
            "built_at": meta.get("built_at"),
            "version_lines": lines,
            "counts": counts,
            "annotations": annotations,
            "datasets": get_meta(db_path).get("datasets", {}),
        }
    finally:
        conn.close()


def _cmd_db_activate_bundled(args: argparse.Namespace) -> dict:
    """Activate only the database fixed inside this installed App package."""

    from genshin_app.bundled_database_activation import (
        activate_bundled_database,
        write_activation_report,
    )

    data_dir = Path(args.data_dir)
    try:
        result = activate_bundled_database(
            data_dir,
            consent_source=args.consent_source,
        )
    except Exception as error:
        try:
            write_activation_report(
                data_dir,
                status="failed",
                consent_source=str(args.consent_source or "missing"),
                error=error,
            )
        except OSError:
            # The original activation refusal is authoritative.  A secondary
            # diagnostic-file failure must not replace its reason/exit code.
            logging.getLogger(__name__).exception(
                "无法写入数据库激活失败回执"
            )
        raise
    try:
        report = write_activation_report(
            data_dir,
            status="activated",
            consent_source=args.consent_source,
            result=result,
        )
    except OSError:
        logging.getLogger(__name__).exception(
            "数据库已激活，但无法写入激活成功回执"
        )
        report = None
    return {**result, "report_path": str(report) if report is not None else None}


def _cmd_db_relabel(args: argparse.Namespace) -> dict:
    _reject_deprecated_database_write("db relabel")


def _cmd_db_localize(args: argparse.Namespace) -> dict:
    _reject_deprecated_database_write("db localize")


def _semantic_input(args):
    from genshin_app.equipment.files import read_input_bytes
    from genshin_db.semantic_store import LIMITS
    if not args.input:
        raise CLIUsageError("机制资料操作需要 --input JSON文件")
    raw = read_input_bytes(args.input, maximum_bytes=LIMITS["max_import_bytes"])
    if len(raw) > LIMITS["max_import_bytes"]:
        raise CLIUsageError("机制资料输入超过2MiB")
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise CLIUsageError("机制资料输入需要有效的UTF-8 JSON", reason="input_json_invalid") from exc


def _cmd_semantics_import(args):
    _reject_deprecated_database_write("db semantics import")


def _cmd_semantics_record(args):
    from genshin_db.equipment_queries import (
        borrow_read_connection,
        equipment_read_session,
    )
    from genshin_db.semantic_store import read_record
    request = _semantic_input(args)
    fields = {"record_kind", "record_id", "revision"}
    if type(request) is not dict or request.keys() - fields or not {"record_kind", "record_id"} <= request.keys():
        raise CLIUsageError("记录查询需要record_kind/record_id，可选revision")
    db = Path(args.data_dir) / "genshin.db"
    with equipment_read_session(db):
        return read_record(borrow_read_connection(db), args.version_line, **request)


def _cmd_semantics_review(args):
    _reject_deprecated_database_write("db semantics review")


def _cmd_db_serve(args: argparse.Namespace) -> dict | None:
    _require_runtime_database(args.data_dir)
    from genshin_db.service import serve

    code = serve(Path(args.data_dir), port=args.port)  # 长驻前台，Ctrl+C 退出
    if code:
        raise CLIUsageError("数据服务未启动：已有实例在运行或启动中，请复用 service.json 中的服务",
                            reason="database_service_busy")
    return None


def _cmd_gui(args: argparse.Namespace) -> dict | None:
    from genshin_app.gui.launcher import run_gui

    run_gui(Path(args.data_dir), open_path=Path(args.open).resolve() if args.open else None)
    return None


def _cmd_agent_budget(args: argparse.Namespace) -> dict:
    """Read or mutate the next-turn native Agent budget policy."""
    from genshin_app.agent_chat.native_turn_budget import (
        ADJUSTABLE_BUDGET_FIELDS,
        load_native_turn_budget_policy,
        reset_native_turn_budget_policy,
        update_native_turn_budget_policy,
    )

    operation = args._agent_budget
    data_dir = Path(args.data_dir)
    if operation == "show":
        return load_native_turn_budget_policy(data_dir)
    current = load_native_turn_budget_policy(data_dir)
    expected = (
        args.expected_generation
        if args.expected_generation is not None
        else current["generation"]
    )
    if operation == "reset":
        return reset_native_turn_budget_policy(
            data_dir, expected_generation=expected, source="cli"
        )
    overrides = dict(current["overrides"])
    changed = False
    for field in ADJUSTABLE_BUDGET_FIELDS:
        value = getattr(args, field)
        if value is None:
            continue
        changed = True
        if value == current["defaults"][field]:
            overrides.pop(field, None)
        else:
            overrides[field] = value
    if not changed:
        raise CLIUsageError("agent budget set 至少需要一个预算字段")
    return update_native_turn_budget_policy(
        data_dir,
        overrides,
        expected_generation=expected,
        source="cli",
    )


def _cmd_agent_dsh_settings(args: argparse.Namespace) -> dict:
    """Use official live ConfigEditor; legacy sparse input is migration-only."""
    operation = args._agent_dsh_settings
    from genshin_app.equipment.files import read_input_bytes, read_input_json
    if getattr(args, "connection", None):
        from genshin_app.agent_chat.native_cli_link import _decode, call_live_candidate
        request = {"entry_id": args.entry} if operation == "show" and args.entry else {}
        if operation != "show":
            if not args.input:
                raise CLIUsageError("完整DSH配置修改需要--input：entry_id、expected_fingerprint和operations；先show读取当前schema与指纹")
            raw = read_input_bytes(args.input, maximum_bytes=64 * 1024 * 1024)
            if len(raw) > 64 * 1024 * 1024:
                raise CLIUsageError("配置请求超过64MiB容量")
            request = _decode(raw)
            if not isinstance(request, dict):
                raise CLIUsageError("配置请求必须是JSON对象")
            if operation == "reset" and any(not isinstance(row, dict) or row.get("op") != "unset" for row in request.get("operations", [])):
                raise CLIUsageError("reset只接受unset；set可使用完整ConfigEditor操作合同")
        return call_live_candidate(args.connection, "settings.describe" if operation == "show" else "settings.edit", None, request)
    from genshin_app.agent_chat.native_dsh_settings import (
        default_dsh_profile_root,
        load_native_dsh_settings,
        reset_native_dsh_settings,
        update_native_dsh_settings,
    )

    profile_root = default_dsh_profile_root(args.data_dir)
    if operation == "show":
        return load_native_dsh_settings(profile_root)
    current = load_native_dsh_settings(profile_root)
    expected = (
        args.expected_generation
        if args.expected_generation is not None
        else current["generation"]
    )
    if operation == "reset":
        return reset_native_dsh_settings(
            profile_root, expected_generation=expected, source="cli"
        )
    if not args.input:
        raise CLIUsageError("agent dsh-settings set 需要 --input 稀疏覆盖JSON")
    payload = read_input_json(args.input)
    if not isinstance(payload, dict):
        raise CLIUsageError("DSH稀疏覆盖JSON必须是对象")
    return update_native_dsh_settings(
        profile_root,
        payload,
        expected_generation=expected,
        source="cli",
    )


def _cmd_shortcut_create(args: argparse.Namespace) -> dict:
    from genshin_app.shortcuts import create_shortcut

    if not args.input or not args.output:
        raise CLIUsageError("创建方案快捷方式需要 --input JSON路径 和 --output 快捷方式.lnk")
    return create_shortcut(args.input, args.output, args.data_dir, overwrite=args.overwrite)


def _cmd_db_lookup(args):
    from genshin_app.public_data_query import run_lookup
    from genshin_db.catalog_query import query_catalog
    args._lookup = args._db
    return run_lookup(
        args,
        catalog_function=query_catalog,
        admission_function=_require_runtime_database,
        read_scope=_queued_database_read,
    )


def _cmd_db_context(args):
    from genshin_app.equipment.files import json_bytes, publish_files, read_input_json
    from genshin_app.query_cache import cached_context, protect_inputs

    if not args.input:
        raise CLIUsageError("定向资料查询需要 --input 请求文件")
    database = Path(args.data_dir) / "genshin.db"
    if args.output:
        protect_inputs([args.output], [args.input, database])
    request = read_input_json(args.input)
    with _queued_database_read(args.data_dir):
        _require_runtime_database(args.data_dir)
        data = cached_context(database, request, args.cache_dir, refresh=args.refresh, protected_paths=[args.input])
        if args.related_sources:
            from genshin_app.public_data_query import related_sources
            data = related_sources(database, data)
    if args.notebook_dir:
        from genshin_app.workflow_workspace import export_sources
        receipt = export_sources(data, args.notebook_dir, overwrite=args.overwrite,
                                 protected_paths=[database, args.input])
        data["source_notebooks"] = receipt
    if args.output:
        return {"cache": data["cache"], "snapshot": data["snapshot"],
                **publish_files([(args.output, json_bytes(data))], overwrite=args.overwrite)}
    return data


def _cmd_db_appendix(args):
    from genshin_app.equipment.files import read_input_json
    from genshin_app.public_appendix_query import read_appendix

    if not args.input:
        raise CLIUsageError("附录查询需要 --input 请求文件")
    request = read_input_json(args.input)
    with _queued_database_read(args.data_dir):
        _require_runtime_database(args.data_dir)
        return read_appendix(Path(args.data_dir) / "genshin.db", request)


def _cmd_panel_reference(args):
    from genshin_app.equipment.files import json_bytes, publish_files, read_input_json
    from genshin_app.equipment.panel_reference import reference_panel
    from genshin_app.query_cache import protect_inputs

    if not args.input:
        raise CLIUsageError("面板参考计算需要 --input JSON")
    database = Path(args.data_dir) / "genshin.db"
    if args.output:
        protect_inputs([args.output], [args.input, database])
    request = read_input_json(args.input)
    with _queued_database_read(args.data_dir):
        _require_runtime_database(args.data_dir)
        result = reference_panel(database, request, version_line=args.version_line)
    if args.output:
        files = publish_files([(args.output, json_bytes(result))], overwrite=args.overwrite,
                              input_paths=[args.input, database])
        return {"reference": result, **files}
    return result


def _cmd_formula_workbook(args):
    from genshin_app.equipment.files import publish_files, read_plan
    from genshin_app.equipment.service import run_plan_calc
    from genshin_app.formula_workbook import (
        build_formula_workbook,
        formula_workbook_support,
    )

    if not args.plan or not args.output:
        raise CLIUsageError("活公式导出需要 --plan 和 --output")
    _require_runtime_database(args.data_dir)
    from genshin_app.query_cache import protect_inputs
    protect_inputs([args.output], [args.plan, Path(args.data_dir) / "genshin.db"])
    calculated = run_plan_calc(read_plan(args.plan)["plan"], Path(args.data_dir) / "genshin.db")
    return {"support": formula_workbook_support(calculated), "result_receipt": calculated["result_receipt"],
            **publish_files([(args.output, build_formula_workbook(calculated))], overwrite=args.overwrite)}


def _cmd_enable_mechanics(args):
    from genshin_app.equipment.files import json_bytes, publish_files, read_plan
    from genshin_app.equipment.mechanics import enable_mechanics

    if not args.plan or not args.output:
        raise CLIUsageError("启用机制需要 --plan 和 --output")
    loaded = read_plan(args.plan)
    plan = enable_mechanics(loaded["plan"])
    same = Path(args.output).resolve() == Path(args.plan).resolve()
    if same and not args.overwrite:
        raise CLIUsageError("原地升级需 --overwrite；也可指定新的输出路径")
    from genshin_app.query_cache import protect_inputs
    protect_inputs([args.output], [Path(args.data_dir) / "genshin.db", *([] if same else [args.plan])])
    return {"plan": plan, **publish_files([(args.output, json_bytes(plan))], overwrite=args.overwrite,
                                         expected_shas={args.output: loaded["file_sha"]} if same else None)}


def _cmd_assemble_attack(args):
    from genshin_app.equipment.attack_source_assembly import (
        MAX_REQUEST_BYTES,
        assemble_attack_source,
    )
    return _cmd_assemble_source(args, assemble_attack_source, MAX_REQUEST_BYTES, "assemble-attack")


def _cmd_assemble_periodic(args):
    from genshin_app.equipment.periodic_source_assembly import (
        MAX_REQUEST_BYTES,
        assemble_periodic_source,
    )
    return _cmd_assemble_source(args, assemble_periodic_source, MAX_REQUEST_BYTES, "assemble-periodic")


def _cmd_assemble_source(args, assemble, max_request_bytes, operation):
    from genshin_app.equipment.files import (
        fingerprint,
        input_view,
        json_bytes,
        publish_files,
        read_plan,
    )
    from genshin_app.query_cache import protect_inputs

    if not args.plan or not args.request:
        raise CLIUsageError(f"equipment {operation} 需要 --plan PATH 与 --request PATH")
    if args.overwrite and not args.output:
        raise CLIUsageError("--overwrite 需要同时指定 --output")
    _require_runtime_database(args.data_dir)
    db_path = Path(args.data_dir) / "genshin.db"
    same = bool(args.output) and Path(args.output).resolve() == Path(args.plan).resolve()
    if same and not args.overwrite:
        raise CLIUsageError("原地装配需 --overwrite；也可指定新的输出路径")
    if args.output:
        protected = []
        for value in (db_path, args.request, *([] if same else [args.plan])):
            path = Path(value).resolve()
            protected.extend((path, path.with_name(path.name + ".previous"),
                              path.with_name(f".{path.name}.plan.lock")))
        protected.extend(db_path.with_name(db_path.name + suffix) for suffix in ("-wal", "-shm", "-journal"))
        protected.append(db_path.with_name(f".{db_path.name}.write.lock"))
        protect_inputs([args.output], protected)
    from genshin_app.equipment.files import read_input_bytes
    raw = read_input_bytes(args.request, maximum_bytes=max_request_bytes, field="/request")
    if len(raw) > max_request_bytes:
        raise CLIUsageError(f"动作装配请求超过{max_request_bytes // 1024}KiB")
    try:
        request = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise CLIUsageError("动作装配请求需要有效的UTF-8 JSON", reason="input_json_invalid",
                            details={"path": "/request"}) from exc
    loaded = read_plan(args.plan)
    proposal = assemble(loaded["plan"], request, db_path)
    if not args.output:
        return proposal
    files = publish_files([(args.output, json_bytes(proposal["plan"]))], overwrite=args.overwrite,
                          expected_shas={args.output: loaded["file_sha"]} if same else None)
    report = proposal["report"]
    missing = report.get("missing_inputs", [])
    return {"summary_only": True, "assembly": proposal["assembly"], "state": report["state"],
            "missing_input_count": len(missing), "missing_inputs": missing[:20],
            "input_fingerprint": fingerprint(input_view(proposal["plan"])), "files": files,
            "next_step": "读取compose完整预览后确认动作档案；装配未自动接受来源、修改动作或计算伤害"}


def _cmd_attribute_recovery(args):
    from genshin_app.equipment.attribute_recovery import (
        preview_attribute_recovery,
        read_recovery_input,
    )
    from genshin_app.equipment.files import json_bytes, publish_files

    if not args.plan:
        raise CLIUsageError("属性修复草稿需要 --plan PATH")
    value = preview_attribute_recovery(read_recovery_input(args.plan))
    if not args.output:
        return value
    files = publish_files([(args.output, json_bytes(value))], overwrite=args.overwrite,
                          input_paths=[args.plan])
    return {"contract": value["contract"], "status": value["status"], "executes": False,
            "input_fingerprint": value["input_fingerprint"], "files": files}


def _cmd_hash_input(args):
    from genshin_app.agent_chat.agent_hash_input import hash_input
    from genshin_app.agent_chat.tools import MAX_ARGUMENTS
    from genshin_app.equipment.files import read_input_bytes
    from genshin_app.equipment.schema import PlanError
    if not args.input:
        raise CLIUsageError('技术校验码需要 --input PATH，内容为kind/value请求')
    raw = read_input_bytes(args.input, maximum_bytes=MAX_ARGUMENTS)
    if len(raw) > MAX_ARGUMENTS:
        raise PlanError('校验码输入超过512KiB', 'agent_size_limit')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate key')
            result[key] = value
        return result
    try:
        request = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PlanError('校验码请求必须是有限且无重复键的JSON', 'agent_tool_input') from exc
    if not isinstance(request, dict) or set(request) != {'kind','value'}:
        raise PlanError('校验码请求必须明确且仅含kind/value', 'agent_tool_input')
    return hash_input(request['kind'], request['value'], maximum_bytes=MAX_ARGUMENTS)


def _cmd_workflow(args):
    from genshin_app.equipment.files import (
        file_sha,
        fingerprint,
        input_view,
        json_bytes,
        publish_files,
        read_input_json,
        read_plan,
    )
    from genshin_app.query_cache import protect_inputs
    from genshin_app.workflow import (
        export_audit,
        new_audit,
        saved_audit_binding,
        validate_audit,
    )

    if args._workflow == "workspace":
        from genshin_app.workflow_workspace import workspace
        if not args.audit:
            raise CLIUsageError("工作资料需要 --audit；已有方案时可指定 --plan")
        request = read_input_json(args.request, field="/request") if args.request else None
        if args.task_file and request is None:
            request = {"operation": "append"}
        return workspace(args.plan, args.audit, request, task_file=args.task_file, calculated_path=args.calculated,
                         data_dir=args.data_dir, protected_paths=[args.request] if args.request else [])
    if not args.plan or not args.audit:
        raise CLIUsageError("工作记录需要 --plan 和 --audit")
    plan = read_plan(args.plan)["plan"]
    if Path(args.audit).resolve() == Path(args.plan).resolve():
        raise CLIUsageError("工作记录不能覆盖原生方案")
    if args._workflow == "init":
        protect_inputs([args.audit], [args.plan])
        return publish_files([(args.audit, json_bytes(new_audit(plan)))], overwrite=args.overwrite,
                             input_paths=[args.plan])
    observed = file_sha(args.audit)
    audit = read_input_json(args.audit, field="/audit")
    validate_audit(audit)
    if saved_audit_binding(audit).get("plan_id") not in (None, plan["plan_id"]):
        raise CLIUsageError("工作记录属于另一方案")
    if args._workflow == "bind":
        if audit["schema_version"] == 2:
            from genshin_app.workflow_workspace import workspace
            return workspace(args.plan, args.audit, {"operation": "bind"}, data_dir=args.data_dir)
        protect_inputs([args.audit], [args.plan])
        audit["input_fingerprint"] = fingerprint(input_view(plan))
        return publish_files([(args.audit, json_bytes(audit))], expected_shas={args.audit: observed},
                             input_paths=[args.plan, args.audit])
    if not args.output_dir:
        raise CLIUsageError("导出工作记录需要 --output-dir")
    protect_inputs([Path(args.output_dir) / name for name in
                    ("issues.json", "lookups.jsonl", "web_findings.json", "rotation.md", "task-evidence.json")],
                   [args.plan, args.audit])
    return export_audit(audit, plan, args.output_dir, overwrite=args.overwrite)


def _cmd_engine_calc(args: argparse.Namespace) -> dict:
    from genshin_app.equipment.files import json_bytes, publish_files, read_input_json
    from genshin_app.output_policy import validate_output_paths
    from genshin_engine import calculate

    if args.output:
        validate_output_paths([args.output], input_paths=[args.template, *([args.patch] if args.patch else [])])

    template = read_input_json(args.template, field="/template")
    patches = None
    if args.patch:
        patches = read_input_json(args.patch, field="/patch")
    result = calculate(template, patches)
    from genshin_app.rotation_check import attach_rotation_check
    result = attach_rotation_check(result, template)
    if args.output:
        out = Path(args.output)
        publish_files([(out, json_bytes(result))], overwrite=True,
                      input_paths=[args.template, *([args.patch] if args.patch else [])])
        return {"output": str(out), "summary": result["summary"]}
    return result


def _cmd_engine_check_rotation(args: argparse.Namespace) -> dict:
    import io

    from genshin_app.equipment.files import publish_files
    from genshin_app.export import _write_xlsx
    from genshin_app.rotation_check import (
        check_document,
        check_sheets,
        read_check_document,
    )

    if args.format not in ("json", "xlsx"):
        raise CLIUsageError("动作与能量检查导出支持 json/xlsx")
    if args.format == "xlsx" and not args.output:
        raise CLIUsageError("导出电子表格需要 --output PATH")
    document = read_check_document(args.input)
    database = Path(args.data_dir) / "genshin.db"
    standalone = (isinstance(document, dict) and document.get("kind") != "genshin-plan"
                  and (document.get("kind") == "genshin-rotation-check" or document.get("form") == "resolved"))
    if not args.preflight or not standalone:
        _require_runtime_database(args.data_dir)
    result = check_document(document, database, preflight=args.preflight)
    if not args.output:
        return result
    if Path(args.output).resolve() == Path(args.input).resolve():
        raise CLIUsageError("检查结果不能覆盖输入文件")
    if args.format == "json":
        content = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    else:
        buffer = io.BytesIO()
        _write_xlsx(buffer, check_sheets(result))
        content = buffer.getvalue()
    published = publish_files([(args.output, content)], overwrite=args.overwrite,
                              input_paths=[args.input, database])
    return {"status": result["status"], **({"preflight": result["preflight"]} if "preflight" in result else {}),
            "output": str(Path(args.output).resolve()), **published}


def _cmd_equipment_deliver(args: argparse.Namespace) -> dict:
    from genshin_app.calculation_delivery_files import DIAGNOSTIC_MESSAGE
    from genshin_app.equipment.delivery import deliver_plan_file
    from genshin_app.equipment.schema import PlanError

    if not args.plan or not args.output_dir:
        raise CLIUsageError("equipment deliver 需要 --plan PATH 与 --output-dir DIR")
    _require_runtime_database(args.data_dir)
    delivered = deliver_plan_file(args.plan, Path(args.data_dir) / "genshin.db", args.output_dir,
                                  overwrite=args.overwrite,
                                  roles=[role.strip() for role in args.roles.split(",")] if getattr(args, "roles", None) is not None else None,
                                  energy_intent=getattr(args, "energy_intent", None))
    if delivered["diagnostic"]:
        raise PlanError(DIAGNOSTIC_MESSAGE, "calculation_diagnostic", delivered["data"])
    return delivered["data"]


def _cmd_equipment_edit(args: argparse.Namespace) -> dict:
    from genshin_app.input_edit_cli import command
    return command(args)


def _cmd_equipment_review(args: argparse.Namespace) -> dict:
    from genshin_app.calculation_review import (
        MAX_FILE_BYTES,
        MAX_REQUEST_BYTES,
        MAX_RESPONSE_BYTES,
        read_bounded_json,
        review_calculation,
    )
    from genshin_app.equipment.files import json_bytes, publish_files
    from genshin_app.equipment.schema import PlanError
    from genshin_app.query_cache import protect_inputs

    if not args.calculated:
        raise CLIUsageError("equipment review需要--calculated PATH；读取已有结果，不重新计算")
    if args.output:
        protected = [args.calculated, Path(args.data_dir) / "genshin.db"]
        if args.request:
            protected.append(args.request)
        protect_inputs([args.output], protected)
    request = read_bounded_json(args.request, MAX_REQUEST_BYTES) if args.request else None
    data = review_calculation(read_bounded_json(args.calculated, MAX_FILE_BYTES), request)
    if args.output:
        path = str(Path(args.output).resolve())
        prospective = data | {"files": {"published_paths": [path], "file_shas": {path: "0" * 64}}}
        if len(json_bytes(prospective)) > MAX_RESPONSE_BYTES:
            raise PlanError("审查结果连同文件回执超过响应限额，请缩小limit", "review_size_limit")
        data["files"] = publish_files([(args.output, json_bytes(data))], overwrite=args.overwrite)
    return data


def _cmd_equipment_review_batch(args: argparse.Namespace):
    from genshin_app.calculation_review_batch_cli import command
    return command(args)


def _cmd_equipment_materialization(args: argparse.Namespace):
    from genshin_app.materialization_authoring_cli import command
    return command(args)


def _cmd_attribute_review(args: argparse.Namespace) -> dict:
    from genshin_app.equipment.attributes import prepare_attribute_review
    from genshin_app.equipment.files import json_bytes, publish_files, read_plan
    from genshin_app.query_cache import protect_inputs
    if not args.plan or not args.note:
        raise CLIUsageError('equipment adopt-attributes需要--plan及--note；应先核对属性事实')
    _require_runtime_database(args.data_dir)
    if args.output:
        protect_inputs([args.output], [args.plan, Path(args.data_dir)/'genshin.db'])
    report = prepare_attribute_review(read_plan(args.plan)['plan'], args.note, Path(args.data_dir)/'genshin.db')
    if args.output:
        files = publish_files([(args.output, json_bytes(report['plan']))], overwrite=args.overwrite)
        return {'state':report['state'], 'attribute_source_adoption':report['attribute_source_adoption'],
                'missing_inputs':report['missing_inputs'], 'files':files}
    return report


def _cmd_equipment_diagnose(args: argparse.Namespace) -> dict:
    from genshin_app.calculation_assistance import (
        MAX_REQUEST_BYTES as AUX_REQUEST_BYTES,
    )
    from genshin_app.calculation_assistance import (
        MAX_RESPONSE_BYTES as AUX_RESPONSE_BYTES,
    )
    from genshin_app.calculation_diagnostics import inspect_calculation_diagnostics
    from genshin_app.calculation_review import (
        MAX_FILE_BYTES,
        MAX_REQUEST_BYTES,
        read_bounded_json,
    )
    from genshin_app.equipment.files import json_bytes, publish_files
    from genshin_app.equipment.schema import PlanError
    from genshin_app.query_cache import protect_inputs
    from genshin_engine.diagnostics import MAX_RESPONSE_BYTES

    request = read_bounded_json(args.request, max(MAX_REQUEST_BYTES, AUX_REQUEST_BYTES)) if args.request else None
    auxiliary = isinstance(request, dict) and request.get("view") in {"assistance", "energy_trials", "input_trials"}
    if not args.calculated and not (auxiliary and getattr(args, "plan", None)):
        raise CLIUsageError("equipment diagnose需要--calculated PATH；辅助视图也可提供--plan原生输入")
    if args.output:
        protect_inputs([args.output], [*([args.calculated] if args.calculated else []),
                                       *([args.plan] if getattr(args, "plan", None) else []), Path(args.data_dir) / "genshin.db",
                                       *([args.request] if args.request else [])])
    if auxiliary:
        from genshin_app.calculation_assistance import diagnose_assistance
        value = read_bounded_json(args.calculated, MAX_FILE_BYTES) if args.calculated else {}
        plan = read_bounded_json(args.plan, 8 * 1024 * 1024) if getattr(args, "plan", None) else None
        if request["view"] in {"energy_trials", "input_trials"}:
            if plan is None:
                raise CLIUsageError("试算视图必须提供--plan；计算结果文件不能代替作者输入")
            _require_runtime_database(args.data_dir)
        data = diagnose_assistance(value, request, plan=plan, db_path=Path(args.data_dir) / "genshin.db")
    else:
        data = inspect_calculation_diagnostics(read_bounded_json(args.calculated, MAX_FILE_BYTES), request)
    if args.output:
        path = str(Path(args.output).resolve())
        prospective = data | {"files": {"published_paths": [path], "file_shas": {path: "0" * 64}}}
        if len(json_bytes(prospective)) > (AUX_RESPONSE_BYTES if auxiliary else MAX_RESPONSE_BYTES):
            raise PlanError("诊断页连同文件回执超过限额，请缩小limit", "diagnostic_response_limit")
        data["files"] = publish_files([(args.output, json_bytes(data))], overwrite=args.overwrite)
    return data


@verification_scope
def _cmd_equipment(args: argparse.Namespace, *, _session=None) -> dict:
    from genshin_app.equipment import (
        compose_equipment_plan,
        run_plan_calc,
        validate_plan,
    )
    from genshin_app.equipment.files import publish_files, read_plan
    from genshin_app.equipment.schema import PlanError
    from genshin_app.equipment.service import (
        require_ready,
        require_standalone_resolved,
        result_bytes,
    )
    from genshin_runtime.operation_timing import timed_call

    summary_only = getattr(args, "summary", False)
    if summary_only and not getattr(args, "output", None):
        raise CLIUsageError("--summary 需要同时提供 --output，以保留完整结果文件")
    if not args.plan:
        raise CLIUsageError("equipment 命令需要 --plan PATH")
    if args._equipment != "validate":
        _require_runtime_database(args.data_dir)
    if summary_only:
        from genshin_app.query_cache import protect_inputs
        protect_inputs([args.output], [args.plan, Path(args.data_dir) / "genshin.db"])
    loaded = timed_call("read_plan", read_plan, args.plan)
    plan, source_path = loaded["plan"], Path(args.plan).resolve()
    command = args._equipment
    if command == "validate":
        return validate_plan(plan)
    db_path = Path(args.data_dir) / "genshin.db"
    output = getattr(args, "output", None)
    resolved_path = getattr(args, "export_resolved", None)
    outputs = []
    if command == "compose":
        report = timed_call("compose", compose_equipment_plan, plan, db_path)
        if resolved_path:
            require_standalone_resolved({"plan": plan, "result": {}})
            require_ready(report)
            if plan["plan_version"] == 3:
                calculated = run_plan_calc(plan, db_path)
                from genshin_app.calculation_completion import (
                    require_calculation_complete,
                )
                require_calculation_complete(calculated)
                from genshin_app.calculation_qualification import (
                    qualification_for_claim,
                )
                qualified = qualification_for_claim(calculated, energy_intent=getattr(args, "energy_intent", None))
                if not qualified["can_accept"]:
                    raise PlanError("动作/机制声明未通过共同执行资格，不能导出为完整数值模板",
                                    "calculation_qualification_rejected", {"qualification": qualified})
                require_standalone_resolved(calculated)
                report = calculated["report"]
            outputs.append((resolved_path, json.dumps(report["plan"]["resolved_template"],
                ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")))
        if output:
            outputs.append((output, json.dumps(report["plan"], ensure_ascii=False,
                                               indent=2, allow_nan=False).encode("utf-8")))
        data = report
    else:
        if command == "export" and (not output or args.format not in ("json", "csv", "xlsx")):
            raise CLIUsageError("equipment export 需要 --format json|csv|xlsx 与 --output PATH")
        capture_display = command == "export" and args.format in ("csv", "xlsx")
        try:
            calculated = timed_call("calculate", run_plan_calc, plan, db_path,
                                    capture_display=capture_display, _session=_session)
        except ValueError as exc:
            from genshin_app.calculation_verification_failure import (
                verification_failure_artifact,
                verification_failure_response,
            )
            diagnostic = verification_failure_artifact(
                exc, plan, source_path=source_path, source_file_sha=loaded['file_sha'])
            if diagnostic is None:
                raise
            files = None
            if output and (command != 'export' or args.format == 'json'):
                from genshin_app.equipment.files import json_bytes
                files = publish_files([(output, json_bytes(diagnostic))],
                    overwrite=args.overwrite, input_paths=[source_path, db_path])
            failure = verification_failure_response(exc, diagnostic, files=files)
            raise PlanError(failure['message'], failure['reason'], failure['details']) from exc
        from genshin_app.calculation_completion import calculation_completion
        from genshin_app.calculation_qualification import qualification_for_claim
        completion = calculation_completion(calculated)
        qualification = qualification_for_claim(calculated, energy_intent=getattr(args, "energy_intent", None))
        data = {"result": calculated["result"], "result_receipt": calculated["result_receipt"],
                "equipment_summary": calculated["equipment_summary"], "presentation_issues": calculated["presentation_issues"],
                "qualification": qualification}
        context = calculated.get("display_context")
        if context is not None:
            data["display_warnings"] = context["warnings"]
        if output and (completion['task_admission'] != 'accepted' or not qualification['can_accept']):
            if command != 'export' or args.format == 'json':
                from genshin_app.calculation_review import MAX_FILE_BYTES
                from genshin_app.equipment.files import json_bytes
                diagnostic_bytes = json_bytes({'kind': 'genshin-calculation-diagnostic',
                    'schema_version': 1, 'completion': completion, 'diagnostic': data})
                if len(diagnostic_bytes) > MAX_FILE_BYTES:
                    raise PlanError("诊断超过读取容量，未写出文件", "diagnostic_output_limit",
                                    {"actual_bytes": len(diagnostic_bytes), "maximum_bytes": MAX_FILE_BYTES})
                outputs.append((output, diagnostic_bytes))
        elif output:
            from genshin_app.presentation import display_names
            outputs.append((output, timed_call("serialize_result", result_bytes, calculated["result"], calculated["result_receipt"],
                                                 args.format if command == "export" else "json",
                                                 display_names(calculated["plan"], context["catalog"] if context else None),
                                                 calculated["equipment_summary"], _session=_session)))
    expected = {}
    for path, _ in outputs:
        if Path(path).resolve() == source_path:
            if command != "compose" or resolved_path and Path(resolved_path).resolve() == source_path:
                raise PlanError("计算或 resolved 输出不能覆盖原生输入方案", "file_conflict")
            if not args.overwrite:
                raise PlanError("原地保存需要 --overwrite", "file_conflict")
            expected[path] = loaded["file_sha"]
    if outputs:
        data["files"] = timed_call("publish_result", publish_files, outputs,
                                  overwrite=args.overwrite, expected_shas=expected)
    if command != 'compose':
        from genshin_app.calculation_completion import require_calculation_complete
        data['completion'] = require_calculation_complete(calculated, files=data.get('files'))
        if not qualification["can_accept"]:
            raise PlanError("动作/机制执行声明尚未通过共同计算资格；已保存内容只能作为诊断。",
                "calculation_qualification_rejected", {"qualification": qualification, **({"files": data["files"]} if data.get("files") else {})})
    from genshin_app.agent_chat.skill_context import recommend_focus
    data["work_card_recommendation"] = recommend_focus({"latest_attempt": {
        "operation": "gs_compose" if command == "compose" else "calculate",
        "state": "succeeded", "matches_current_input": True}})
    if summary_only:
        return {**_calculation_output_summary(calculated), "files": data["files"]}
    return data


def _cmd_research(args: argparse.Namespace) -> dict:
    from genshin_app.external_research import (
        ExternalResearchService,
        read_request,
        research_capabilities,
    )
    operation = args._command_path[-1]
    if operation == "schema":
        return research_capabilities()
    if not args.plan or not args.task_id:
        raise CLIUsageError("研究操作需要 --plan 当前原生方案及 --task-id 外部任务ID。",
                            reason="research_external_input")
    service = ExternalResearchService(args.data_dir, args.plan, args.task_id)
    if operation == "list":
        return service.call(operation, offset=args.offset, limit=args.limit)
    if not args.input:
        raise CLIUsageError("该研究操作需要 --input JSON请求。", reason="research_external_input")
    result = service.call(operation, read_request(args.input))
    if result.get("status") in {"incomplete_research_report", "stale_research_report"}:
        from genshin_app.equipment.schema import PlanError
        raise PlanError("研究采用报告尚未闭合，请按缺项或最新revision补全。", "research_report_incomplete", result)
    return result


# ---------- 单源命令定义（argparse 与 capabilities 共用） ----------

def _selected_fields(data, keys):
    """Whitelist bounded metadata; never echo source payloads or event arrays."""
    return {key: data[key] for key in keys if key in data}


def _calculation_output_summary(calculated):
    from genshin_app.agent_chat.skill_context import recommend_focus
    from genshin_app.calculation_completion import calculation_completion
    from genshin_app.default_assistance import compact_default_assistance
    from genshin_app.failure_analysis import calculation_feedback
    result, report = calculated["result"], calculated["report"]
    rotation = result.get("rotation_check", {})
    completion = calculation_completion(calculated)
    output = {
        "summary_only": True,
        "work_card_recommendation": recommend_focus({"latest_attempt": {
            "operation": "calculate", "state": "succeeded", "matches_current_input": True}}),
        'completion': completion,
        "execution_feedback": calculation_feedback({**calculated, "completion": completion}),
        "validation_scope": result.get("validation_scope", {}),
        "state": report["state"],
        "status": result.get("mechanics_status", rotation.get("status", "calculated")),
        "summary": _selected_fields(result["summary"], ("total_damage", "dps")),
        "hit_count": len(result.get("hits", [])),
        "missing_input_count": len(report.get("missing_inputs", [])),
        "presentation_issue_count": len(calculated.get("presentation_issues", [])),
        "mechanics_issue_count": len(result.get("mechanics_issues", [])),
        "rotation_status": rotation.get("status"),
        "mechanics": _selected_fields(result.get("mechanics", {}), ("execution_status", "complete_first_cycle")),
        "result_receipt": _selected_fields(calculated["result_receipt"], (
            "plan_id", "revision", "input_fingerprint", "output_fingerprint", "source_fingerprint",
            "result_fingerprint", "engine_version", "calculated_at")),
    }
    if report.get("default_assistance"):
        output["default_assistance"] = compact_default_assistance(report["default_assistance"])
    if result.get("calculation_totals"):
        output["engine_summary"] = output["summary"]
        output["summary"] = _selected_fields(result["calculation_totals"], ("total_damage", "dps"))
        output["calculation_totals"] = _selected_fields(result["calculation_totals"], (
            "total_damage", "dps", "engine_total_damage", "external_total_damage", "external_count", "evidence_tier"))
    if rotation.get("automatic_energy_delivery"):
        output["automatic_energy_delivery"] = _selected_fields(rotation["automatic_energy_delivery"], (
            "contract", "count_scope", "max_cycles_planned_actions", "unreported_actions",
            "planned_actions", "committed_actions", "not_committed_actions", "not_verified_actions",
            "receipt_count", "receipt_bytes"))
        output["automatic_energy_delivery"]["message"] = "动作接纳时一次扣能，系统审计记录提交瞬间；不自动补施放、产球或证明循环。"
    if result.get("fixed_effect_sources"):
        profiles = sorted({source["target_state_profile"] for source in result["fixed_effect_sources"]})
        partial = [r for r in report.get("upstream_compilations", []) if r.get("whole_source_status") == "partial"]
        rows = result.get("target_condition_trace", [])
        output["target_conditions"] = {
            "profile_ids": profiles, "evidence_tier": "declared_target_profile",
            "source_count": len(result["fixed_effect_sources"]), "check_count": len(rows),
            "override_count": sum(r.get("override") is not None for r in rows), "partial_source_count": len(partial),
            "source_coverage": "partial" if partial else "manual_declared",
            "message": "逐击按声明档案读取命中前目标状态；仅声明的收益参与计算，完整分支与微调见结果文件。"}
    if result.get("action_event_compilations"):
        records = result["action_event_compilations"]
        generated = [hit for record in records for hit in record.get("generated", [])]
        reported_ids = {hit["id"] for hit in result.get("hits", [])}
        output["action_events"] = {
            "dispatch_mode": "calibrated", "skill_coverage": "partial", "resource_coverage": "not_generated",
            "profile_count": len(records), "generated_hit_count": len(generated),
            "reported_hit_count": sum(hit["hit_id"] in reported_ids for hit in generated),
            "declared_missed_count": sum(hit["landed"] is False for hit in generated),
            "message": "按一次校准档案为显式动作生成命中；非上游原生派发。档案自身不生成产球，独立产球来源另行核验。"}
    if result.get("attack_metadata_compilations"):
        output["attack_metadata"] = {
            "source_count": len(result["attack_metadata_compilations"]), "whole_source_status": "partial",
            "message": "精确攻击来源自动提供参数位置、元素、原始附着量与衰减配置；仍按显式校准派发。"}
    if result.get("reaction_benefit_compilations"):
        records = result["reaction_benefit_compilations"]
        output["reaction_benefits"] = {"source_count": len(records),
            "rule_count": sum(len(record.get("rules", [])) for record in records), "whole_source_status": "partial",
            "message": "普通冰水扩散按实际反应自动触发、刷新和计时；原目标范围与原子命中顺序仍由方案声明。"}
    if result.get("passive_compilations"):
        records = result["passive_compilations"]
        output["passive_sources"] = {"source_count": len(records),
            "rule_count": sum(len(record.get("rules", [])) for record in records),
            "qualified_parent_count": sum(len((record.get("scope") or {}).get("provider_parent_events", [])) for record in records),
            "whole_source_status": "partial", "gameplay_interpretation": "declared_not_verified",
            "stat_basis": "fixed_panel_nonextra_em_equals_total_em",
            "detail_pointer": "/result/passive_compilations",
            "message": "普通冰水扩散由已核对的角色战技父链触发固有增伤；精通采用显式固定面板与NONEXTRA相等假设，未支持完整来源或动态转换。"}
        if any(r.get('compiler')=='database-inherent-swirl-attribute-v2' for r in records):
            output['passive_sources'].update(stat_basis='per_source_explicit_attribute_contract',
                message='按各来源显式固定或属性账合同取样；动态来源读取已核验NONEXTRA，实际来源、取样与缺项见完整回执，不代表完整游戏机制已支持。')
    if result.get("limited_hit_benefit_compilations"):
        output["limited_hit_benefit"] = {
            "source_count": len(result["limited_hit_benefit_compilations"]),
            **_selected_fields(result.get("limited_hit_benefit_summary", {}), (
                "activations", "consumed_hits", "exhausted_hits", "applied_benefits", "sampled_hits", "whole_source_status")),
            "message": "施放激活有限次数收益，各角色独立消费配额并读取提供者当前攻击力；仅支持已接受的有限来源与校准范围。"}
    for key in ('resource_counters','resource_counter_views'):
        if result.get(key):
            ledger=result[key]
            output[key]={'contract':ledger.get('contract'),'scope':ledger.get('scope'),
                'trace_count':len(ledger.get('trace',[])),'source_count':len(ledger.get('source_adoptions',[])),
                'review_view':key,'message':'实际执行只读账；含附加检查，不额外计伤。用equipment review按此view分页核对来源、实际消费和取值。'}
    output['calculation_status'] = output['completion']['calculation_status']
    output['task_admission'] = output['completion']['task_admission']
    if output['task_admission'] != 'accepted':
        output['diagnostic_summary'] = output.pop('summary', {})
    return output


def _runtime_output_summary(data, request):
    if request["action"] == "batch":
        items = []
        for item in data["items"]:
            summary = {"id": item["id"], "status": item["status"], "action": item["request"]["action"]}
            if item["status"] == "ok":
                summary["result"] = _runtime_output_summary(item["data"], item["request"])
            else:
                summary["error"] = _selected_fields(item["error"], ("code", "reason", "message", "details_omitted"))
            items.append(summary)
        return {"summary_only": True, "action": "batch", "status": data["status"],
                "snapshot": _selected_fields(data["snapshot"], (
                    "snapshot_id", "upstream_commit", "indexer_version", "version_line")),
                "counts": data["counts"], "content_fingerprint": data["content_fingerprint"], "items": items}
    summary = {"summary_only": True, "action": request["action"],
               **_selected_fields(data, ("status", "state", "index_state", "version_line",
                   "execution_support", "reference_closed", "truncated", "content_fingerprint",
                   "closure_fingerprint", "size_bytes", "group", "channel")),
               "issue_count": len(data.get("issues", [])),
               "missing_input_count": len(data.get("missing_inputs", [])),
               "unresolved_count": len(data.get("unresolved", []))}
    summary["snapshot"] = _selected_fields(data.get("snapshot", data), (
        "snapshot_id", "upstream_commit", "manifest_fingerprint", "indexer_version", "version_line"))
    if "owner_publication" in data:
        summary["owner_publication"] = data["owner_publication"]
    if "selection" in data:
        summary["selection"] = _selected_fields(data["selection"], (
            "character_id", "skill_depot_id", "skill_id", "slot", "constellation",
            "reaction_type", "element", "branch", "scope"))
    if "level" in data:
        summary["level"] = _selected_fields(data["level"], (
            "base_level", "effective_level", "source", "constellation_applied", "constellation_level_support"))
    if request["action"] == "action_level_context":
        summary.update(_selected_fields(data, (
            "contract", "static_level_status", "source_assembly_status", "footprint_status",
            "source_authority", "operation_execution", "whole_skill_coverage")))
        footprints = data.get("footprints", [])
        summary["footprints"] = {"total": len(footprints),
            "target_identified": sum(row.get("status") == "target_identified" for row in footprints),
            "pending": sum(row.get("status") != "target_identified" for row in footprints)}
        summary["target_material_count"] = len(data.get("target_material", []))
        summary["level_issue_count"] = len((data.get("level_profile") or {}).get("issues", []))
        summary["facts_issue_count"] = len((data.get("facts") or {}).get("issues", []))
    if request["action"] == "mechanism_catalog":
        summary.update(**_selected_fields(data, ("component", "page", "page_size", "total")))
        summary["items"] = [_selected_fields(row, (
            "id", "revision", "subject", "review", "payload_fingerprint", "review_fingerprint"))
            for row in data.get("items", [])]
    if request["action"] == "mechanism_profile":
        summary.update(**_selected_fields(data, (
            "source_status", "review_status", "materialization_status", "semantic_fingerprint",
            "authorization_fingerprint", "record_payload_fingerprint")))
        summary["selection"] = _selected_fields(data.get("selection", {}), ("mechanism_id", "revision"))
        compiled = data.get("compiler_result") or {}
        summary["templates"] = {**_selected_fields(compiled, (
            "status", "verification_scope", "execution_status", "whole_source_status", "qualification_status")),
            "rule_count": len(compiled.get("rule_templates", [])),
            "parameter_count": len(data.get("resolved_parameters", [])),
            "required_execution_checks": compiled.get("required_execution_checks", []),
            "issue_count": len(compiled.get("issues", []))}
    if "action_dispatch" in data:
        dispatch = data["action_dispatch"]
        summary["action_dispatch"] = ({"status": dispatch["status"], "reason_count": len(dispatch.get("reasons", []))}
                                      if isinstance(dispatch, dict) else {"status": dispatch})
    if "execution" in data:
        summary["execution"] = {**_selected_fields(data["execution"], (
            "status", "semantics", "attack_binding", "sequence_status", "ability_execution",
            "owner_resolution", "reaction_dispatch")),
                                "issue_count": len(data["execution"].get("issues", []))}
    if request["action"] == "attenuation_profile":
        channel = data.get("channel", request.get("channel", "application"))
        damage = channel == "damage"
        sequence = data.get("raw_record", {}).get("damageSequence" if damage else "durabilitySequence")
        summary["projection_summary"] = {
            **_selected_fields(data.get("projection", {}), ("reset_cycle_seconds_raw", "reset_cycle_seconds_rounded",
                                                            "rounding_error_seconds")),
            "damage_sequence_length" if damage else "durability_sequence_length": len(sequence) if isinstance(sequence, list) else None,
        }
    if request["action"] == "reaction_damage_profile":
        projection = data.get("projection", {})
        sequence = data.get("raw_nodes", {}).get("attenuation", {}).get("raw", {}).get("damageSequence")
        summary["projection_summary"] = {
            **_selected_fields(projection, ("group", "semantics", "sequence_semantics", "reset_cycle_seconds_raw",
                                           "reset_cycle_seconds_rounded", "rounding_error_seconds")),
            "damage_sequence_length": len(sequence) if isinstance(sequence, list) else None,
        }
        summary["source_count"] = len(data.get("sources", {}))
        summary["separate_star_ability_count"] = len(data.get("separate_star_abilities", []))
    if request["action"] == "attack_metadata_profile":
        selected = data.get("selected_candidate") or {}
        projection = selected.get("projection") or {}
        summary.update(candidate_count=len(data.get("candidates", [])),
                       selected_candidate=_selected_fields(selected, ("candidate_id", "status")),
                       whole_source_status=data.get("whole_source_status"),
                       source_fingerprint=data.get("source_fingerprint"))
        summary["projection_summary"] = {key: _selected_fields(projection.get(key, {}),
            ("status", "value", "param_index", "raw_durability", "tax_applied", "group"))
            for key in ("parameter", "element", "gauge", "attenuation")}
    if request["action"] == "passive_source_profile":
        unlock, descriptions = data.get("unlock", {}), data.get("descriptions", {})
        promote = unlock.get("selected_promote_record") or {}
        requirement = unlock.get("requirement") or {}
        summary.update(**_selected_fields(data, ("mode", "source_status", "source_fingerprint", "unlock_fingerprint")),
                       candidate_count=len(data.get("candidates", [])))
        summary["selection"] = _selected_fields(data.get("selection", {}), (
            "character_id", "skill_depot_id", "proud_skill_id", "proud_skill_group_id", "proud_level", "opening_index"))
        summary["candidates"] = [_selected_fields(row, ("proud_skill_id", "proud_level", "relation_status"))
                                  for row in data.get("candidates", [])[:64]]
        summary["unlock"] = {**_selected_fields(unlock, ("status", "supplied", "complete_character_validity")),
            "required_ascension": requirement.get("required_ascension"),
            "current_stage_max_level": promote.get("record", {}).get("unlockMaxLevel"),
            "issue_count": len(unlock.get("issues", []))}
        summary["descriptions"] = {"status": descriptions.get("status"),
            **{key + "_status": descriptions.get(key, {}).get("status") for key in ("name", "description", "unlock_description")}}
    if request["action"] == "gadget_source_profile":
        config = data.get("config") or {}
        mounts = data.get("mounted_abilities") or []
        summary.update(graph_scope=data.get("graph_scope"), selection=_selected_fields(data.get("selection", {}), ("gadget_id",)),
            source_fingerprint=data.get("source_fingerprint"), origin_status=(data.get("origin") or {}).get("status"),
            config_status=config.get("status"), config_candidate_count=len(config.get("candidates", [])),
            mount_list_status=data.get("mount_list_status"), mount_list_field_status=data.get("mount_list_field_status"),
            mount_count=len(mounts), ready_mount_count=sum(r.get("status") == "ready" for r in mounts),
            execution_support=data.get("execution_support"))
    if request["action"] == "energy_generation_profile":
        projection, material = data.get("reference_projection", {}), data.get("material", {})
        summary.update(**_selected_fields(data, ("graph_scope", "source_status", "whole_source_status", "execution_support", "source_fingerprint")))
        summary["selection"] = _selected_fields(data.get("selection", {}), ("origin_reference_id",))
        summary["material"] = {"status": material.get("status"), "candidate_count": len(material.get("candidates", [])),
            "id": projection.get("raw_values", {}).get("config_id")}
        summary["reference_projection"] = {**_selected_fields(projection, ("status", "count", "particle_type", "mapping_status", "evidence_tier")),
            "unit": _selected_fields(projection.get("unit", {}), ("element", "same_element_energy", "different_element_energy"))}
    if request["action"] == "limited_hit_benefit_profile":
        summary.update(**_selected_fields(data, ("graph_scope", "source_status", "projection_status",
            "whole_source_status", "execution_support", "source_fingerprint", "param_index")),
            candidate_count=len(data.get("benefit_candidates", [])),
            parent_bridge=_selected_fields(data.get("parent_bridge", {}), ("status", "kind", "variant")))
        summary["projection_summary"] = {key: _selected_fields(data.get("projection", {}).get(key, {}), ("status", "value"))
            for key in ("coefficient", "duration_seconds", "capacity")}
    if request["action"] == "periodic_attack_profile":
        summary.update(**_selected_fields(data, ("mode", "whole_source_status", "source_relationship_status",
            "numeric_projection_status", "action_creation_association", "dispatch_semantics_status", "source_fingerprint")),
            creation_candidate_count=len(data.get("creation_candidates", [])), candidate_count=len(data.get("candidates", [])),
            selected_count=len(data.get("selected_candidates", [])))
        discovery = data.get("discovery", {})
        summary["discovery"] = {"status": discovery.get("status"), "counts": discovery.get("counts", {}),
                                "blocked_count": len(discovery.get("blocked_paths", []))}
        summary["selected_candidates"] = [{**_selected_fields(c, ("candidate_id", "channel_kind", "status")),
            "projection_summary": {k: _selected_fields(p, ("status", "value", "param_index", "group", "ticks", "seconds", "kind"))
                for k, p in c.get("projection", {}).items() if k in ("parameter", "element", "gauge", "classification", "attenuation", "timer")}}
            for c in data.get("selected_candidates", [])[:8]]
    if request['action'] in ('talent_level_profile','action_parameter_at_level'):
        summary['level']=data.get('level')
        summary['scope']=data.get('scope')
    if request["action"] in ("action_parameter", "action_parameter_at_level"):
        summary["param_index"] = data["param_index"]
        summary["scalar_projection"] = {**_selected_fields(data["scalar_projection"], ("status", "value", "semantics")),
                                        "issue_count": len(data["scalar_projection"].get("issues", []))}
        descriptions = data.get("parameter_descriptions", {})
        matched = descriptions.get("matched", [])
        visible = [row for row in matched[:2]
                   if len(json.dumps(row, ensure_ascii=False).encode("utf-8")) <= 2048]
        summary["descriptions"] = {"status": descriptions.get("status"), "matched_count": len(descriptions.get("matched", [])),
                                   "unresolved_count": len(descriptions.get("unresolved", [])),
                                   "matched": visible,
                                   "more_available": len(matched) > len(visible),
                                   "detail_pointer": "/parameter_descriptions",
                                   "level": data.get("level"),
                                   "source": data.get("source")}
        summary["gameplay_interpretation"] = data["gameplay_interpretation"]
    if request["action"] == "action_resource_profile":
        summary["scope"] = data.get("scope")
        summary["resource_fingerprint"] = data.get("resource_fingerprint")
        summary["projection_summary"] = {
            key: {**_selected_fields(value, ("status", "value", "source_field")),
                  "issue_count": len(value.get("issues", []))}
            for key, value in data.get("projection", {}).items()
            if key in ("base_cooldown_seconds", "base_charge_count", "base_energy_cost", "cost_element")}
        summary["variant_applicability"] = _selected_fields(data.get("variant_applicability", {}), ("status",))
        summary["gameplay_interpretation"] = data.get("gameplay_interpretation")
        descriptions = data.get("descriptions", {})
        summary["descriptions"] = {"status": descriptions.get("status"),
            "name_status": descriptions.get("name", {}).get("status"),
            "description_status": descriptions.get("description", {}).get("status")}
    if request["action"] == "action_admission_profile":
        summary.update(reason=data.get("reason"), source_fingerprint=data.get("source_fingerprint"),
                       current_head_fingerprint=data.get("current_head_fingerprint"),
                       execution_authority=data.get("execution_authority"))
        summary["selection"] = data.get("selection")
        summary["model_ref"] = data.get("model_ref")
        summary["compiled_values"] = data.get("compiled_values")
        summary["declared_not_verified"] = data.get("declared_not_verified", [])
    summary["counts"] = {key: len(data[key]) for key in (
        "roots", "selections", "definitions", "references", "frontier", "abilities", "talents",
        "parameter_bindings", "ability_candidates") if isinstance(data.get(key), list)}
    return summary


def _cmd_db_runtime(args):
    _require_runtime_database(args.data_dir)
    from genshin_app.equipment.files import file_sha, publish_files, read_input_json
    from genshin_app.equipment.schema import PlanError
    from genshin_app.query_cache import protect_inputs
    from genshin_db.runtime_batch import BATCH_LIMITS, validate_batch_request
    from genshin_db.runtime_queries import query_runtime_public

    summary_only = getattr(args, "summary", False)
    output_dir = getattr(args, "output_dir", None)
    if output_dir and args.output:
        raise CLIUsageError("--output 与 --output-dir 不能同时使用")
    if summary_only and not (args.output or output_dir):
        raise CLIUsageError("--summary 需要同时提供 --output 或 --output-dir，以保留完整查询文件")
    request = read_input_json(args.input) if args.input else {"action": "status"}
    batch = isinstance(request, dict) and request.get("action") == "batch"
    if output_dir and not batch:
        raise CLIUsageError("--output-dir 仅用于runtime batch请求")
    db = Path(args.data_dir) / "genshin.db"
    protected = [db, *([args.input] if args.input else [])]
    directory, expected = None, None
    if batch:
        request = validate_batch_request(request)
    if output_dir:
        from genshin_app.runtime_batch_files import prepare_batch_directory
        directory = prepare_batch_directory(output_dir, overwrite=args.overwrite, protected_paths=protected)
    if args.output and (batch or summary_only):
        protect_inputs([args.output], protected)
    if batch and args.output:
        target = Path(args.output)
        if target.exists() and not args.overwrite:
            raise PlanError("目标文件已存在，需要明确允许覆盖", "file_conflict", {"path": str(target.resolve())})
        expected = {target: file_sha(target)}
    # The runtime status query can report a missing directory without creating
    # it. Once a profile exists, hold its update lock during the actual read.
    scope = _queued_database_read(args.data_dir) if db.parent.is_dir() else nullcontext()
    with scope:
        data = query_runtime_public(db, request)
    files = None
    if directory is not None:
        from genshin_app.runtime_batch_files import publish_batch_directory
        publication = publish_batch_directory(directory, data)
        files = _selected_fields(publication, ("path", "file_sha", "generation_path", "published_paths", "file_shas", "bytes"))
    if args.output:
        if args.input and Path(args.input).resolve() == Path(args.output).resolve():
            raise CLIUsageError("查询结果不能覆盖请求文件")
        content = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
        if batch and len(content) > BATCH_LIMITS["delivery_bytes"]:
            raise PlanError("batch实际输出文件超过8MiB", "response_too_large")
        if batch or summary_only:
            protect_inputs([args.output], protected)
        files = publish_files([(args.output, content)], overwrite=args.overwrite, expected_shas=expected)
    if batch and data["status"] == "partial":
        details = {"summary": _runtime_output_summary(data, request)}
        details.update({"files": files} if files else {"batch": data})
        message = "批次含查询错误或未执行项；" + ("完整资料已按指定位置交付" if files else "完整资料见错误回执batch字段")
        raise PlanError(message, "batch_partial", details)
    result = _runtime_output_summary(data, request) if summary_only else data
    return {**result, "files": files} if files else result

GLOBAL_ARGS = [
    {"name": "--data-dir", "kind": "value", "default": None,
     "description": "数据目录（开发态默认 ./data；打包版默认 LOCALAPPDATA 下的 GenshinCalc\\data）"},
    {"name": "--pretty", "kind": "flag", "default": False,
     "description": "人类可读格式化输出（缩进 JSON）"},
]

COMMANDS: list[dict] = [
    {
        "path": ("capabilities",),
        "description": "输出 CLI 能力自描述 JSON（ADR-0002：Skill 能力说明的唯一数据源）",
        "args": [
            {"name": "--command", "kind": "value", "default": None,
             "description": "定向读取完整命令名，例如db runtime；与section互斥"},
            {"name": "--section", "kind": "value", "default": None,
             "description": "定向读取既有能力分区，例如runtime_request_contract或plan_contract"},
            {"name": "--pointer", "kind": "value", "default": None,
             "description": "在选中根内读取JSON Pointer，附带可解析的本地$ref；不截断字段"},
        ],
        "output": "{cli, version, global_args, commands[]}",
        "examples": ["python -m genshin_app capabilities",
                     'python -m genshin_app capabilities --command "db runtime"',
                     "python -m genshin_app capabilities --section runtime_request_contract --pointer /actions/mechanism_catalog"],
        "handler": _cmd_capabilities,
    },
    {"path": ("diagnostics", "show"), "description": "按编号读取安全持久诊断；不启动数据库、模型或GUI",
     "args": [{"name": "--id", "kind": "value", "default": None, "description": "故障回执返回的诊断编号"}],
     "output": "code-only fault diagnostic with operation/context", "handler": _cmd_system_diagnostic,
     "examples": ["python -m genshin_app diagnostics show --id UUID"]},
    {"path": ("system", "check-update"), "description": "查看当前安装和签名更新选项",
     "args": [], "output": "安装状态和可选的应用或数据库更新", "handler": _cmd_system_check_update,
     "examples": ["python -m genshin_app system check-update"]},
    {
        "path": ("db", "update"),
        "description": "[已弃用] 旧活动库更新入口；保留命令名只为向旧脚本返回明确错误，不会修改数据库",
        "lifecycle": "deprecated",
        "args": [
            {"name": "--force", "kind": "flag", "default": False,
             "description": "旧脚本兼容参数；命令仍会拒绝且不写库"},
            {"name": "--offline-repo", "kind": "value", "type": "str", "default": None,
             "description": "旧脚本兼容参数；命令仍会拒绝且不写库"},
            {"name": "--expected-commit", "kind": "value", "type": "str", "default": None,
             "description": "旧脚本兼容参数；命令仍会拒绝且不写库"},
        ],
        "output": "错误包络：accepted_database_is_immutable；数据库保持不变",
        "examples": ["python -m genshin_app db update", "python -m genshin_app db update --force"],
        "handler": _cmd_db_update,
    },
    {
        "path": ("db", "status"),
        "description": "查看当前数据库状态（版本/构建时间/表行数/annotation 统计）",
        "args": [],
        "output": "{db_path, upstream_commit, game_version, built_at, version_lines, counts, annotations, datasets}",
        "examples": ["python -m genshin_app db status"],
        "handler": _cmd_db_status,
    },
    {
        "path": ("db", "activate-bundled"),
        "description": (
            "安装或修复当前桌面应用自带的配套数据库；不接受外部候选路径，"
            "会事务备份并替换旧库"
        ),
        "args": [
            {
                "name": "--consent-source",
                "kind": "value",
                "type": "str",
                "default": None,
                "description": "明确确认来源：installer 或 user_repair（必填）",
            },
        ],
        "output": (
            "{status,release_pair_id,database_release_id,database_sha256,"
            "migration_status,rollback_retained,startup_state,report_path}"
        ),
        "examples": [
            "GenshinCalc.exe db activate-bundled --consent-source user_repair"
        ],
        "handler": _cmd_db_activate_bundled,
    },
    {
        "path": ("db", "relabel"),
        "description": "[已弃用] 旧活动库重标入口；规则变化必须进入新数据库版本候选",
        "lifecycle": "deprecated",
        "args": [
            {"name": "--version-line", "kind": "value", "type": "str", "default": None,
             "description": "旧脚本兼容参数；命令仍会拒绝且不写库"},
        ],
        "output": "错误包络：accepted_database_is_immutable；数据库保持不变",
        "examples": ["python -m genshin_app db relabel",
                     "python -m genshin_app db relabel --version-line release"],
        "handler": _cmd_db_relabel,
    },
    {
        "path": ("db", "localize"),
        "description": "[已弃用] 旧活动库本地化入口；显示索引变化必须进入新数据库版本候选",
        "lifecycle": "deprecated",
        "args": [
            {"name": "--version-line", "kind": "value", "type": "str", "default": "release",
             "description": "旧脚本兼容参数；命令仍会拒绝且不写库"},
        ],
        "output": "错误包络：accepted_database_is_immutable；数据库保持不变",
        "examples": ["python -m genshin_app db localize"],
        "handler": _cmd_db_localize,
    },
    {
        "path": ("db", "serve"),
        "description": "前台启动本地 HTTP 服务（127.0.0.1，ADR-0007 单实例；发现文件 <data-dir>/service.json）",
        "args": [
            {"name": "--port", "kind": "value", "type": "int", "default": 0,
             "description": "端口（默认 0=随机分配）"},
        ],
        "output": "（长驻前台命令：启动信息打印在 stdout，非 JSON 包络；Ctrl+C 退出）",
        "examples": ["python -m genshin_app db serve", "python -m genshin_app db serve --port 8000"],
        "handler": _cmd_db_serve,
    },
    {
        "path": ("gui",),
        "description": "启动桌面 GUI（pywebview 窗口；自动拉起/复用本地数据服务，退出时清理自有实例）",
        "args": [{"name": "--open", "kind": "value", "default": None,
                  "description": "启动后自动载入指定原生方案或数值模板JSON；打开独立窗口，不自动计算"}],
        "output": "（打开桌面窗口；非 JSON 包络）",
        "examples": ["python -m genshin_app gui", "GenshinCalc.exe --data-dir data gui --open 方案.json"],
        "handler": _cmd_gui,
    },
    {
        "path": ("shortcut", "create"),
        "description": "生成与原始JSON关联的Windows快捷方式；双击直接用App打开最新内容",
        "args": [
            {"name": "--input", "kind": "value", "default": None,
             "description": "原生方案或数值模板JSON（不能传计算结果/回执）"},
            {"name": "--output", "kind": "value", "default": None,
             "description": "本机.lnk快捷方式路径；记录程序/JSON/本次data-dir的绝对路径，移动后需重生成"},
            {"name": "--overwrite", "kind": "flag", "default": False,
             "description": "允许覆盖已有快捷方式，并保留.previous备份"},
        ],
        "output": "{path,input,input_kind,input_sha256_at_creation,data_dir,launcher,argv,behavior,portability,published_paths,file_shas}",
        "examples": ['GenshinCalc.exe --data-dir data shortcut create --input "方案.json" --output "打开方案.lnk"'],
        "handler": _cmd_shortcut_create,
    },
    {
        "path": ("engine", "calc"),
        "description": "引擎计算：加载 resolved 模板 JSON（可叠加结构化补丁）输出结果",
        "args": [
            {"name": "template", "kind": "positional",
             "description": "模板 JSON 文件路径（form=resolved）"},
            {"name": "--patch", "kind": "value", "type": "str", "default": None,
             "description": "结构化补丁 JSON 文件（操作序列，可选）"},
            {"name": "--output", "kind": "value", "type": "str", "default": None,
             "description": "完整结果写文件（stdout 只回摘要）；缺省 stdout 输出全量"},
        ],
        "output": "结果 JSON（summary/hits/buffs_coverage/patches_applied/assumptions；模板见捆绑 Skill 的 references/engine-template.md）",
        "examples": [
            "python -m genshin_app engine calc template.json",
            "python -m genshin_app engine calc template.json --patch buff.json --output result.json",
        ],
        "handler": _cmd_engine_calc,
    },
    {
        "path": ("engine", "check-rotation"),
        "description": "检查动作、冷却与元素能量循环；原生v3默认完整执行，--preflight只检查声明的动作和资源",
        "args": [
            {"name": "input", "kind": "positional", "description": "原生方案或 genshin-rotation-check 文件"},
            {"name": "--preflight", "kind": "flag", "default": False,
             "description": "原生v3仅检查已声明时序和资源，不运行机制，不能用来证明完整循环"},
            {"name": "--output", "kind": "value", "default": None, "description": "检查结果写入文件；默认标准输出"},
            {"name": "--format", "kind": "value", "default": "json", "description": "json 或 xlsx"},
            {"name": "--overwrite", "kind": "flag", "default": False, "description": "明确允许覆盖指定结果文件"},
        ],
        "output": "{status,action_check,energy_check,issues,event_log,cycles,gaps,panel_bindings,resolved_rotation_model,unchecked_constraints}",
        "examples": ["python -m genshin_app engine check-rotation examples/rotation-energy-check.json"],
        "handler": _cmd_engine_check_rotation,
    },
]

_AGENT_BUDGET_ARGUMENTS = [
    ("wall-clock-seconds", "单轮墙钟时间秒数"),
    ("provider-steps", "提供方请求步骤总数"),
    ("app-tool-calls", "App工具调用总数"),
    ("web-tool-calls", "联网工具调用总数"),
    ("total-tool-calls", "所有模型工具调用总数"),
    ("web-search-calls", "联网搜索调用数"),
    ("web-search-queries", "联网搜索词总数"),
    ("web-fetch-calls", "网页读取调用数"),
    ("per-request-output-tokens", "单次提供方输出token上限"),
    ("cumulative-output-tokens", "单轮累计输出token上限"),
    ("cumulative-input-soft-tokens", "单轮累计输入软警告token数"),
    ("cumulative-input-hard-tokens", "单轮累计输入硬上限token数"),
    ("cumulative-uncached-input-tokens", "单轮未缓存输入token上限"),
    ("read-tool-calls", "App只读工具调用数"),
    ("mutation-tool-calls", "App变更与计算工具调用数"),
]

COMMANDS += [
    {"path": ("equipment", "panel-reference"), "description": "按主属性和聚合有效词条只读复算参考面板；仅作差异提醒，不修改方案",
     "args": [{"name": "--input", "kind": "value", "default": None, "description": "参考面板构造请求JSON"},
              {"name": "--output", "kind": "value", "default": None, "description": "可选参考结果JSON"},
              {"name": "--version-line", "kind": "value", "default": "release", "choices": ["release", "beta"], "description": "公共资料线"},
              {"name": "--overwrite", "kind": "flag", "default": False, "description": "允许覆盖普通参考结果文件"}],
     "output": "advisory参考面板与差异；有输出时包含发布回执",
     "examples": ["python -m genshin_app equipment panel-reference --input panel.json"],
     "handler": _cmd_panel_reference},
    {
        "path": ("agent", "gui-workflow"),
        "description": (
            "以CLI投影完整GUI原生Agent链路：使用Windows已保存凭据，运行、审核、"
            "应用、保存并由全新GuiApi回流核验；不会读取原生会话私有目录，也不会"
            "替用户批准、回答问题或绕过GUI权限"
        ),
        "args": [
            {"name": "--input", "kind": "value", "default": None,
             "description": "可选白板JSON；新会话省略时新建，恢复会话省略时经公共草稿保存卡导出App保存的原正式基线"},
            {"name": "--resume-session-id", "kind": "value", "default": None,
             "description": "在同一data-dir恢复指定DSH会话和既有候选；新任务文本作为新用户消息，不初始化模型/候选，不自动恢复Goal"},
            {"name": "--prompt-file", "kind": "value", "default": None,
             "description": "UTF-8任务文本（必填；不允许命令行传API Key）"},
            {"name": "--output-dir", "kind": "value", "default": None,
             "description": "新结果目录（必填；不覆盖旧目录）"},
            {"name": "--workflow", "kind": "value", "default": "artifact",
             "description": "artifact：模型自主安排工作重点，工件与计算仍由App验证"},
            {"name": "--model", "kind": "value", "default": None,
             "description": "DeepSeek模型ID；新会话省略时用提供方默认模型，恢复会话省略时保留原选择"},
            {"name": "--effort", "kind": "value", "default": None,
             "description": "模型支持的推理档位；新会话默认high，恢复会话省略时保留原选择"},
            {"name": "--timeout-seconds", "kind": "value", "type": "int", "default": 1800,
             "description": "单次Host状态观察窗口，默认30分钟；不是模型或任务总时限，健康运行不会因此被取消"},
            {"name": "--allow-draft", "kind": "flag", "default": False,
             "description": "诊断用：允许取得阶段草稿；草稿仍不会伪装成计算结果"},
            {"name": "--provider-mode", "kind": "value", "default": "saved_credential",
             "description": "saved_credential；deterministic_provider_transport仅供显式注入完整生产composition的受审测试"},
        ],
        "output": (
            "{contract,status,session_id,revision,receipt_fingerprint,board,"
            "calculated,reload_verified,timeout_semantics}；成功必须完成全新GuiApi回流核验"
        ),
        "examples": [
            "python -m genshin_app agent gui-workflow --prompt-file task.txt --output-dir run-001",
            "GenshinCalc.exe agent gui-workflow --input board.json --prompt-file task.txt --output-dir run-002 --timeout-seconds 1800",
            "python -m genshin_app --data-dir SAME_PROFILE agent gui-workflow --resume-session-id SESSION_ID --prompt-file continue.txt --output-dir run-003",
        ],
        "handler": _cmd_agent_gui_workflow,
    },
    {
        "path": ("agent", "gui-webview-smoke"),
        "description": (
            "启动真实PyWebView与生产JS界面，经过原生Agent会话、候选审核弹窗、"
            "应用、白板保存、Host发布、GUI重载和结果DOM显示；与无WebView的"
            "gui-workflow分开出具证据"
        ),
        "args": [
            {"name": "--input", "kind": "value", "default": None,
             "description": "可选白板JSON；省略时由真实GUI新建白板"},
            {"name": "--prompt-file", "kind": "value", "default": None,
             "description": "UTF-8任务文本（必填；不允许命令行传API Key）"},
            {"name": "--output-dir", "kind": "value", "default": None,
             "description": "新结果目录（必填；不覆盖旧目录）"},
            {"name": "--workflow", "kind": "value", "default": "staged",
             "description": "build、calculate或staged"},
            {"name": "--model", "kind": "value", "default": None,
             "description": "DeepSeek模型ID；省略时使用连接默认模型"},
            {"name": "--effort", "kind": "value", "default": "high",
             "description": "模型支持的推理档位"},
            {"name": "--timeout-seconds", "kind": "value", "type": "int", "default": 1800,
             "description": "真实GUI完整链时限，默认30分钟"},
            {"name": "--allow-draft", "kind": "flag", "default": False,
             "description": "诊断用：允许审核、保存和重载阶段草稿"},
            {"name": "--visible", "kind": "flag", "default": False,
             "description": "显示验收窗口；默认使用隐藏PyWebView窗口"},
            {"name": "--expectation-file", "kind": "value", "default": None,
             "description": "可选UTF-8 JSON题面语义断言；核验角色、动作、修订与正式伤害事件，不接受答案数值"},
        ],
        "output": (
            "{contract,status,session_id,revision,board,calculated,verification_layers,"
            "real_webview_dom_verified,reload_verified,display_verified}"
        ),
        "examples": [
            "python -m genshin_app agent gui-webview-smoke --prompt-file task.txt --output-dir webview-run-001",
            "python -m genshin_app agent gui-webview-smoke --prompt-file task.txt --output-dir webview-run-002 --visible --timeout-seconds 1800",
        ],
        "handler": _cmd_agent_gui_webview_smoke,
    },
    {
        "path": ("agent", "budget", "show"),
        "description": "查看下一轮生效的原生Agent动态预算、默认值、范围和显式覆盖",
        "args": [],
        "output": "{contract,policy_id,policy_version,generation,effective,overrides,defaults,limits,follows_defaults,source,updated_at,apply_scope}",
        "examples": ["python -m genshin_app agent budget show"],
        "defaults": {"_budget": "show"},
        "handler": _cmd_agent_budget,
    },
    {
        "path": ("agent", "budget", "set"),
        "description": "原子更新下一轮预算；未给出的字段保持现状，设为当前默认值会恢复跟随默认",
        "args": [
            {"name": "--expected-generation", "kind": "value", "type": "int", "default": None,
             "description": "可选并发代数；不匹配则拒绝覆盖"},
            *[
                {"name": f"--{name}", "kind": "value", "type": "int", "default": None,
                 "description": description}
                for name, description in _AGENT_BUDGET_ARGUMENTS
            ],
        ],
        "output": "更新后的完整预算策略；只影响下一轮",
        "examples": [
            "python -m genshin_app agent budget set --wall-clock-seconds 14400 --provider-steps 8192",
            "python -m genshin_app agent budget set --expected-generation 3 --web-tool-calls 2048",
        ],
        "defaults": {"_budget": "set"},
        "handler": _cmd_agent_budget,
    },
    {
        "path": ("agent", "budget", "reset"),
        "description": "清空显式覆盖，下一轮开始跟随当前及未来版本的默认预算",
        "args": [
            {"name": "--expected-generation", "kind": "value", "type": "int", "default": None,
             "description": "可选并发代数；不匹配则拒绝覆盖"},
        ],
        "output": "恢复默认后的完整预算策略；只影响下一轮",
        "examples": ["python -m genshin_app agent budget reset"],
        "defaults": {"_budget": "reset"},
        "handler": _cmd_agent_budget,
    },
    {
        "path": ("agent", "dsh-settings", "show"),
        "description": "通过--connection读取官方DSH完整活动profile、schema与指纹；无连接仅供首次迁移前旧配置读取",
        "args": [
            {"name": "--connection", "kind": "value", "default": None, "description": "活动App提供的本地DSH配置连接文件"},
            {"name": "--entry", "kind": "value", "default": None, "description": "可选精确配置条目ID；省略列出完整profile"}],
        "output": "官方ConfigEditor完整profile条目；非JSON表达式保留官方配置文件入口",
        "examples": ["python -m genshin_app agent dsh-settings show"],
        "defaults": {"_agent_dsh_settings": "show"},
        "handler": _cmd_agent_dsh_settings,
    },
    {
        "path": ("agent", "dsh-settings", "set"),
        "description": "通过--connection调用官方ConfigEditor修改任意受支持配置条目；按官方生命周期应用，不自建字段镜像",
        "args": [
            {"name": "--input", "kind": "value", "default": None,
             "description": "活动配置请求entry_id/expected_fingerprint/operations；无连接仅用于首次迁移前旧六段输入"},
            {"name": "--connection", "kind": "value", "default": None, "description": "活动App提供的本地DSH配置连接文件"},
            {"name": "--expected-generation", "kind": "value", "type": "int", "default": None,
             "description": "可选并发代数；不匹配则拒绝覆盖"},
        ],
        "output": "官方配置回执或精确冲突；凭据仍由App的Windows设置管理",
        "examples": ["python -m genshin_app agent dsh-settings set --input dsh-overrides.json"],
        "defaults": {"_agent_dsh_settings": "set"},
        "handler": _cmd_agent_dsh_settings,
    },
    {
        "path": ("agent", "dsh-settings", "reset"),
        "description": "通过官方ConfigEditor移除指定字段；首次迁移前可清空旧六段覆盖",
        "args": [
            {"name": "--connection", "kind": "value", "default": None, "description": "活动App提供的本地DSH配置连接文件"},
            {"name": "--input", "kind": "value", "default": None, "description": "活动配置精确条目、指纹与unset操作；先show核对继承值"},
            {"name": "--expected-generation", "kind": "value", "type": "int", "default": None,
             "description": "可选并发代数；不匹配则拒绝覆盖"},
        ],
        "output": "官方配置回执；生效时机按对应条目生命周期，无连接旧格式仅首次迁移前有效",
        "examples": ["python -m genshin_app agent dsh-settings reset"],
        "defaults": {"_agent_dsh_settings": "reset"},
        "handler": _cmd_agent_dsh_settings,
    },
]

COMMANDS += [
    {"path": ("db", "context"), "description": "定向查询公共资料并复用可失效的内容缓存；不生成队伍答案",
     "args": [{"name": "--input", "kind": "value", "default": None, "description": "上下文请求JSON路径"},
              {"name": "--output", "kind": "value", "default": None, "description": "完整结果文件，可省略"},
              {"name": "--cache-dir", "kind": "value", "default": None, "description": "缓存及按实体拆分的索引目录"},
              {"name": "--related-sources", "kind": "flag", "default": False, "description": "附带角色下级/装备精炼附录导航；不会把目录当已读正文"},
              {"name": "--notebook-dir", "kind": "value", "default": None, "description": "无损导出每实体原文与可编辑解释，既有解释不覆盖"},
              {"name": "--refresh", "kind": "flag", "default": False, "description": "跳过内容缓存重新查询"},
              {"name": "--overwrite", "kind": "flag", "default": False, "description": "允许覆盖指定结果"}],
     "output": "{snapshot,items,cache}|{snapshot,cache,published_paths}",
     "examples": ["python -m genshin_app db context --input request.json --cache-dir .skill-tmp/genshin"],
     "handler": _cmd_db_context},
    {"path": ("db", "appendix"), "description": "按目标与审核状态只读查询附录分类或正文",
     "args": [{"name": "--input", "kind": "value", "default": None, "description": "精确附录请求JSON路径"}],
     "output": "agent-appendix-category-index-v1 | agent-appendix-query-v1",
     "examples": ["python -m genshin_app db appendix --input request.json"],
     "handler": _cmd_db_appendix},
]

for _lookup_name, _lookup_description in [
    ("catalog", "按中文名有界查询公共角色、武器和套装身份，不启动服务、不自动选重名项"),
    ("panel-basis", "只读查询90/100级基础面板与明确武器白值，不推算成品面板"),
]:
    COMMANDS.append({"path": ("db", _lookup_name), "description": _lookup_description,
        "args": [{"name": "--input", "kind": "value", "default": None, "description": "请求JSON，版本线在请求version_line中指定"},
                 {"name": "--output", "kind": "value", "default": None, "description": "完整结果文件，可省略"},
                 {"name": "--overwrite", "kind": "flag", "default": False, "description": "允许覆盖指定结果，不能覆盖请求或数据库"}],
        "output": "有界查询结果或文件发布回执；不启动服务、不创建缺失数据库",
        "examples": [f"python -m genshin_app db {_lookup_name} --input request.json --output result.json"],
        "handler": _cmd_db_lookup})

COMMANDS.append({"path": ("equipment", "edit"), "description": "复用原生候选编辑核心，按稳定ID/补丁/整体/文件编辑输入草稿；不计算或自授批准",
    "args": [{"name": "--plan", "kind": "value", "default": None, "description": "当前白板或原生输入文件（必填）"},
             {"name": "--request", "kind": "value", "default": None, "description": "共用input_edit_contract修改请求JSON（必填）"},
             {"name": "--expected-file-sha", "kind": "value", "default": None, "description": "当前--plan文件SHA；与import请求的源文件SHA分别核对"},
             {"name": "--output", "kind": "value", "default": None, "description": "输出草稿；省略只返回影响预览，不写文件"},
             {"name": "--preview", "kind": "flag", "default": False, "description": "只读影响预览，即使给output也不写入"},
             {"name": "--overwrite", "kind": "flag", "default": False, "description": "允许覆盖目标；原地保存还核对实际读取的SHA"}],
    "output": "修订、输入/文件hash、实际编辑状态和发布回执；游戏内容允许未完成，系统身份和批准不可伪造",
    "examples": ["python -m genshin_app equipment edit --plan plan.json --request edit.json --output next.plan.json"],
    "handler": _cmd_equipment_edit})

COMMANDS.append({"path": ("equipment", "deliver"), "description": "一次真实计算后交付所选App方案/计算包/Excel；默认四产物，可独立选App方案，不自动打开文件",
    "args": [{"name": "--plan", "kind": "value", "default": None, "description": "原生输入方案（必填）"},
             {"name": "--output-dir", "kind": "value", "default": None, "description": "完整交付目录（必填）"},
             {"name": "--roles", "kind": "value", "default": None, "description": "逗号分隔native_plan,calculated,result_workbook,formula_workbook；省略全部，仅App可选native_plan,calculated"},
             {"name": "--energy-intent", "kind": "value", "default": None, "description": "可选single_window_legal/multi_round_legal/sustainable_loop；沿共同执行资格验证，不把两轮当无限循环"},
             {"name": "--overwrite", "kind": "flag", "default": False, "description": "以SHA比较替换同协议索引；保留旧代，不覆盖其他类型index"}],
    "output": "短交付回执：index_committed/index_path/generation_path/所选产物路径与SHA/状态；诊断包可已发布但exit1",
    "examples": ["python -m genshin_app equipment deliver --plan plan.json --output-dir delivery"],
    "handler": _cmd_equipment_deliver})

COMMANDS.append({"path": ("equipment", "review"),
    "description": "只读审查已有计算包或带回执结果，按事件/角色/类别分页核对实际覆盖；不重新计算或读库",
    "args": [{"name": "--calculated", "kind": "value", "default": None, "description": "calculated.json或equipment calc/export的JSON结果（必填）"},
             {"name": "--request", "kind": "value", "default": None, "description": "有界审查请求JSON；省略按角色/技能/类别汇总，schema见calculation_review_contract"},
             {"name": "--output", "kind": "value", "default": None, "description": "另存当前审查页JSON；默认仅stdout"},
             {"name": "--overwrite", "kind": "flag", "default": False, "description": "允许覆盖审查输出，不能覆盖输入/请求/数据库"}],
    "output": "内部一致性范围、指纹、引擎/含外部总量、分页实际事件/消费原因与JSON定位；不报告臆测的游戏应有段数",
    "examples": ["python -m genshin_app equipment review --calculated calculated.json",
                 "python -m genshin_app equipment review --calculated result.json --request review.json --output review-page.json"],
    "handler": _cmd_equipment_review})

COMMANDS.append({"path": ("equipment", "review-batch"),
    "description": "同一私有历史快照完整核验一次后按原顺序投影1至16页；不授予当前候选或来源批准",
    "args": [{"name": "--calculated", "kind": "value", "default": None, "description": "已有计算包或带回执结果JSON（必填）"},
             {"name": "--request", "kind": "value", "default": None, "description": "{requests:[旧单页请求,...]}，1至16页，64KiB（必填）"},
             {"name": "--output", "kind": "value", "default": None, "description": "另存完整批量历史投影JSON"},
             {"name": "--overwrite", "kind": "flag", "default": False, "description": "覆盖审查输出；保护输入/请求/数据库及旁文件"}],
    "output": "历史快照身份、原顺序pages与完整旧单页JSON；含外壳及文件回执至2MiB，任何页失败则整批失败",
    "examples": ["python -m genshin_app equipment review-batch --calculated calculated.json --request review-batch.json"],
    "handler": _cmd_equipment_review_batch})

COMMANDS.append({"path": ("equipment", "diagnose"),
    "description": "只读诊断/软提示；energy_trials/input_trials视图在副本调用真实引擎，不修改原方案或批准",
    "args": [{"name": "--calculated", "kind": "value", "default": None, "description": "已有计算JSON或mechanics_pending错误JSON；普通groups/issues视图必填"},
             {"name": "--plan", "kind": "value", "default": None, "description": "辅助视图的原生作者输入；energy_trials/input_trials必填"},
             {"name": "--request", "kind": "value", "default": None, "description": "groups/issues筛选，或view=assistance/energy_trials/input_trials；精确参数见calculation_diagnostics_contract"},
             {"name": "--output", "kind": "value", "default": None, "description": "另存当前诊断页；默认仅stdout"},
             {"name": "--overwrite", "kind": "flag", "default": False, "description": "覆盖诊断页输出，仍保护输入/请求/数据库"}],
    "output": "执行状态、原问题数量、分组/实例、原JSON位置、未知关联统计和绑定完整输入的分页游标",
    "examples": ["python -m genshin_app equipment diagnose --calculated calculation-error.json",
                 "python -m genshin_app equipment diagnose --calculated calculated.json --request query.json --output diagnostics.json"],
    "handler": _cmd_equipment_diagnose})

COMMANDS.append({"path": ("equipment", "adopt-attributes"),
    "description": "核对事实后显式采用当前属性账声明，自动对齐真实组件指纹和最终上下文；unknown分类保持unknown",
    "args": [{"name":"--plan", "kind":"value", "default":None, "description":"原生v3方案（必填）"},
             {"name":"--note", "kind":"value", "default":None, "description":"已核对基底/分类及来源的说明（必填）"},
             {"name":"--output", "kind":"value", "default":None, "description":"另存已核对候选；省略返回预览，不覆盖原输入"},
             {"name":"--overwrite", "kind":"flag", "default":False, "description":"覆盖输出候选，仍保护原输入与数据库"}],
    "output":"候选与来源确认预览；--output返回文件回执、状态与剩余缺项",
    "examples":["python -m genshin_app equipment adopt-attributes --plan plan.json --note 已核对属性来源 --output reviewed.plan.json"],
    "handler":_cmd_attribute_review})

COMMANDS.append({"path": ("equipment", "attribute-draft"),
    "description": "在正式语义校验前读取旧转换输入，生成无执行权属性账修复patch；不修改原方案、采用来源或计算",
    "args": [{"name":"--plan", "kind":"value", "default":None, "description":"原生方案路径，可为旧转换闭包拒绝的输入（必填）"},
             {"name":"--output", "kind":"value", "default":None, "description":"保存修复草稿；不是可执行原生方案"},
             {"name":"--overwrite", "kind":"flag", "default":False, "description":"明确覆盖草稿输出，仍保护输入和数据库"}],
    "output":"attribute-conversion-recovery-v1：draft_only、原输入指纹、未采用的属性账patch及核对步骤；不是结果或来源确认",
    "examples":["python -m genshin_app equipment attribute-draft --plan plan.json --output attribute-recovery.json"],
    "handler":_cmd_attribute_recovery})

COMMANDS.append({"path": ("equipment", "assemble-attack"),
    "description": "从明确攻击来源和校准建立原生命中档案及引用，fresh预览；不自动接受或改动作",
    "args": [{"name": "--plan", "kind": "value", "default": None, "description": "原生v3方案路径（必填）"},
             {"name": "--request", "kind": "value", "default": None, "description": "装配请求JSON，schema见attack_source_assembly_contract（必填，最多32KiB）"},
             {"name": "--output", "kind": "value", "default": None, "description": "写入原生草稿，stdout只回摘要；省略则只预览完整提案"},
             {"name": "--overwrite", "kind": "flag", "default": False, "description": "明确覆盖输出；原地更新另比较读取SHA"}],
    "output": "{plan,assembly,report}；--output时{summary_only,assembly,state,missing_input_count,missing_inputs,input_fingerprint,files,next_step}",
    "examples": ["python -m genshin_app equipment assemble-attack --plan plan.json --request attack.json --output assembled.plan.json"],
    "handler": _cmd_assemble_attack})

COMMANDS.append({"path": ("equipment", "assemble-periodic"),
    "description": "从明确周期来源与逐通道校准建立原生多通道档案，fresh预览；不自动接受、补次数或改动作",
    "args": [{"name": "--plan", "kind": "value", "default": None, "description": "原生v3方案路径（必填）"},
             {"name": "--request", "kind": "value", "default": None, "description": "多通道装配请求JSON，schema见periodic_source_assembly_contract（必填，最多32KiB）"},
             {"name": "--output", "kind": "value", "default": None, "description": "写入原生草稿，stdout只回摘要；省略则只预览完整提案"},
             {"name": "--overwrite", "kind": "flag", "default": False, "description": "明确覆盖输出；原地更新另比较读取SHA"}],
    "output": "{plan,assembly,report}；--output时{summary_only,assembly,state,missing_input_count,missing_inputs,input_fingerprint,files,next_step}",
    "examples": ["python -m genshin_app equipment assemble-periodic --plan plan.json --request periodic.json --output assembled.plan.json"],
    "handler": _cmd_assemble_periodic})

COMMANDS.append({"path": ("db", "runtime"), "description": "有界查询同快照运行配置、引用链、原始片段或装备装配；不执行游戏脚本",
    "args": [{"name": "--input", "kind": "value", "default": None, "description": "运行资料请求JSON；省略查询状态"},
             {"name": "--output", "kind": "value", "default": None, "description": "查询结果JSON"},
             {"name": "--output-dir", "kind": "value", "default": None, "description": "仅batch：按项写新生成代与最后提交的index.json；与--output互斥"},
             {"name": "--summary", "kind": "flag", "default": False, "description": "需同时--output或--output-dir：文件保留完整查询，标准输出仅状态、选择/派发状态、数量与文件路径/SHA，不回显原文"},
             {"name": "--overwrite", "kind": "flag", "default": False, "description": "允许覆盖指定结果"}],
    "output": "完整查询；--summary时{summary_only,action,status?/state?,snapshot,selection?,level?,action_dispatch?,group?,execution?,projection_summary?,issue_count,missing_input_count,unresolved_count,counts,files}",
    "examples": ["python -m genshin_app db runtime --input runtime-query.json",
                 "python -m genshin_app db runtime --input runtime-query.json --output runtime-result.json --summary"],
    "handler": _cmd_db_runtime})

for _name, _description, _handler in (
    ("import", "[已弃用] 旧机制资料导入／预检入口；新提案验证器尚未发布", _cmd_semantics_import),
    ("record", "只读查询机制事实或描述的精确修订/当前头及审核指纹", _cmd_semantics_record),
    ("review", "[已弃用] 旧活动库审核写入口；审核结果必须进入完整数据库版本候选", _cmd_semantics_review),
):
    COMMANDS.append({"path": ("db", "semantics", _name), "description": _description,
        "args": [{"name": "--input", "kind": "value", "default": None, "description": "结构化资料包或记录请求JSON"},
                 {"name": "--version-line", "kind": "value", "default": "release", "description": "明确资料线release/beta"},
                 *([{"name": "--apply", "kind": "flag", "default": False, "description": "已弃用；指定后明确拒绝，不修改活动数据库"}] if _name == "import" else [])],
        "output": ("错误包络：accepted_database_is_immutable；数据库保持不变"
                   if _name in {"import", "review"}
                   else "完整资料操作回执；不运行本轴、不自动重新批准seed"),
        "examples": [f"python -m genshin_app db semantics {_name} --input request.json"], "handler": _handler})
    if _name in {"import", "review"}:
        COMMANDS[-1]["lifecycle"] = "deprecated"

for _name, _description, _handler in (
    ("workbook", "生成同源活公式工作簿；与旧xlsx结果快照独立，编辑范围见支持矩阵", _cmd_formula_workbook),
    ("enable-mechanics", "显式升级为原生机制方案版本3；保留已有输入，空模型仍需补充", _cmd_enable_mechanics),
):
    COMMANDS.append({"path": ("equipment", _name), "description": _description,
        "args": [{"name": "--plan", "kind": "value", "default": None, "description": "原生输入方案"},
                 {"name": "--output", "kind": "value", "default": None, "description": "输出文件"},
                 {"name": "--overwrite", "kind": "flag", "default": False, "description": "明确覆盖指定输出"}],
        "output": "{published_paths,result_receipt?,support?,plan?}",
        "examples": [f"python -m genshin_app equipment {_name} --plan plan.json --output output." +
                     ("xlsx" if _name == "workbook" else "plan.json")], "handler": _handler})

for _name, _description in (("init", "建立一次事实记录"), ("bind", "复核记录后明确绑定当前输入"),
                            ("export", "由一次记录导出问题/外查/轴说明视图"),
                            ("workspace", "读写可选原题、要求、资料解释及当前证据差异；不影响计算准入")):
    COMMANDS.append({"path": ("workflow", _name), "description": _description,
        "args": [{"name": "--plan", "kind": "value", "default": None, "description": "当前原生方案"},
                 {"name": "--audit", "kind": "value", "default": None, "description": "工作记录文件"},
                 {"name": "--output-dir", "kind": "value", "default": None, "description": "交付视图目录，export必填"},
                 {"name": "--request", "kind": "value", "default": None, "description": "workspace请求JSON，operation=view、append或bind。records为记录列表，source_files映射资料名到JSON路径；只读publication_usage返回拟采用实体的确认问题。"},
                 {"name": "--task-file", "kind": "value", "default": None, "description": "workspace导入原题原文；调用方提供，不赋予批准权"},
                 {"name": "--calculated", "kind": "value", "default": None, "description": "workspace可选已有计算包，按当前输入比对观察，不重算或授予执行权"},
                 {"name": "--overwrite", "kind": "flag", "default": False, "description": "明确覆盖指定输出"}],
        "defaults": {"_workflow": _name}, "output": "{published_paths,review_status?}",
        "examples": [f"python -m genshin_app workflow {_name} --plan plan.json --audit audit.json"],
        "handler": _cmd_workflow})

for _name, _description in (
    ("validate", "仅验证原生方案结构；不查库，不表示可计算"),
    ("compose", "只读组合装备，返回 ready/pending 和当前缺项、来源、贡献及微调预览"),
    ("calc", "重新组合当前输入并计算；pending 不产出结果"),
    ("export", "重新组合并计算，导出带来源回执的 JSON/CSV/xlsx"),
):
    _args = [{"name": "--plan", "kind": "value", "default": None, "description": "原生方案 JSON 路径（必填）"}]
    if _name != "validate":
        _args += [{"name": "--output", "kind": "value", "default": None, "description": "输出路径；默认不覆盖"},
                  {"name": "--overwrite", "kind": "flag", "default": False, "description": "明确允许覆盖指定输出；原地保存比较读取 SHA"}]
    if _name == "compose":
        _args.append({"name": "--export-resolved", "kind": "value", "default": None,
                      "description": "同时输出 v2 数值模板；pending 时所有输出均不写"})
    if _name == "export":
        _args.append({"name": "--format", "kind": "value", "default": None,
                      "description": "json/csv/xlsx（必填）；来源回执包含在输出内"})
    if _name == "calc":
        _args.append({"name": "--summary", "kind": "flag", "default": False,
                      "description": "需同时--output：文件保留完整结果，标准输出仅总伤/DPS、状态、缺项数量、指纹与文件路径/SHA；pending仍失败且不写文件"})
    if _name in {"calc", "export", "compose"}:
        _args.append({"name": "--energy-intent", "kind": "value", "default": None,
                      "description": "可选single_window_legal/multi_round_legal/sustainable_loop；有执行声明时与原生共用资格，普通公式不额外要求动作"})
    COMMANDS.append({"path": ("equipment", _name), "description": _description, "args": _args,
        "output": {"validate": "{valid,plan_id,plan_version,revision}",
                   "compose": "{plan,state,missing_inputs,warnings,contribution_report,effect_requirements,match_previews,data_versions,files?}",
                   "calc": "{result,result_receipt,files?}；--summary时{summary_only,state,status,summary,hit_count,missing_input_count,presentation_issue_count,mechanics_issue_count,rotation_status,mechanics,passive_sources?,automatic_energy_delivery?,result_receipt,files}",
                   "export": "{result,result_receipt,files}"}[_name],
        "examples": [f"python -m genshin_app equipment {_name} --plan plan.json" +
                     (" --format json --output result.json" if _name == "export" else
                      " --output result.json --summary" if _name == "calc" else "")],
        "handler": _cmd_equipment})



for _name, _description in (("new", "建立独立用户排轴白板，未知数据不伪造为可计算"),
                            ("validate", "检查白板结构、动作边界和未知项"),
                            ("handoff", "交付白板、待完善原生方案和Agent说明；保留用户意图"),
                            ("result", "校验完整计算包与当前白板的实际意图绑定")):
    _args = ([{"name": "--output", "kind": "value", "default": None, "description": "新白板文件；不覆盖已有文件"}]
             if _name == "new" else [{"name": "--input", "kind": "value", "default": None, "description": "用户白板JSON"}])
    if _name == "handoff":
        _args.append({"name": "--output-dir", "kind": "value", "default": None, "description": "新交接目录；不覆盖旧交接"})
    if _name == "result":
        _args.append({"name": "--result", "kind": "value", "default": None, "description": "完整calculated.json计算包"})
    COMMANDS.append({"path": ("whiteboard", _name), "description": _description, "args": _args,
        "output": "白板身份/业务指纹、pending或文件回执；result只返回绑定回执及计算摘要",
        "examples": [f"python -m genshin_app whiteboard {_name}" + (" --output board.json" if _name == "new" else " --input board.json")],
        "handler": _cmd_whiteboard})


COMMANDS.append({"path": ("engine", "hash-input"),
    "description": "复用原生Agent的纯技术哈希，不批准事实、变更候选或计算伤害",
    "args": [{"name":"--input", "kind":"value", "default":None,
              "description":"UTF-8 JSON请求文件，字段kind/value；与gs_hash_input相同"}],
    "output":"{kind,algorithm,content_hash,excluded_fields,game_facts_verified,authority}",
    "examples":["python -m genshin_app engine hash-input --input profile-hash-request.json"],
    "handler":_cmd_hash_input})

COMMANDS.append({'path': ('equipment', 'materialization'),
    'description': '只读生成普通原生patch/片段，或生成及校验材料化文档；--request按结构化配方装配，不查库、不计算、不改方案',
    'args': [
        {'name': '--plan', 'kind': 'value', 'default': None, 'description': '当前原生方案；只有材料化族/旧骨架路径要求事件拓扑，普通原生配方不要求'},
        {'name': '--input', 'kind': 'value', 'default': None, 'description': '可选完整materialization文档；省略则生成新空骨架'},
        {'name': '--request', 'kind': 'value', 'default': None, 'description': '可选作者配方请求，kind=catalog发现实际配方/schema；与--input不混用，返回可交现有edit的普通修改'},
        {'name': '--report', 'kind': 'value', 'default': None, 'description': 'binding_refresh使用的当前compose报告或CLI成功包络；不自动查库/确认来源'},
        {'name': '--output', 'kind': 'value', 'default': None, 'description': '可选JSON响应文件，不能覆盖输入'},
        {'name': '--overwrite', 'kind': 'flag', 'default': False, 'description': '允许覆盖既有输出，不授权覆盖输入或数据库'},
    ],
    'output': '{contract,operation,document,validation,plan_modified:false,database_access:false,calculation_executed:false}',
    'examples': ['python -m genshin_app equipment materialization --plan native.plan.json --output scaffold.json'],
    'handler': _cmd_equipment_materialization})

COMMANDS.append({'path': ('equipment', 'topology'),
    'description': '只读生成绑定当前动作的空拓扑，或校验Agent提供的拓扑；共用原生动作投影，不查库或推断角色事件',
    'args': [
        {'name': '--plan', 'kind': 'value', 'default': None, 'description': '当前原生方案与真实动作输入'},
        {'name': '--input', 'kind': 'value', 'default': None, 'description': '可选完整event-topology文档；省略则生成新空骨架'},
        {'name': '--output', 'kind': 'value', 'default': None, 'description': '可选JSON响应文件，不能覆盖输入'},
        {'name': '--overwrite', 'kind': 'flag', 'default': False, 'description': '允许覆盖既有输出，不授权覆盖输入或数据库'},
    ],
    'output': '{contract,operation,document,validation,current_action_checkpoint,action_checkpoint_matches,plan_modified:false}',
    'examples': ['python -m genshin_app equipment topology --plan native.plan.json --output topology-scaffold.json'],
    'handler': _cmd_equipment_materialization})

for _research_name, _research_description in (
    ("schema", "读取外部研究请求schema及使用说明；不会查库或联网"),
    ("append", "登记当前外部任务自行查阅的query/公开来源；明确为external_declared，不伪造DSH执行"),
    ("list", "分页读取当前候选的研究账及旧版本绑定，不改写原生联网事实"),
    ("bind", "当前候选revision前进后明确重绑；保留旧研究原始版本归属"),
    ("report", "为当前候选提交采用/背景/舍弃及实际引用位置，复用原生研究账结算"),
):
    _research_args = [] if _research_name == "schema" else [
        {"name": "--plan", "kind": "value", "default": None, "description": "当前原生方案文件（必填）"},
        {"name": "--task-id", "kind": "value", "default": None, "description": "外部任务稳定ID；不能指定DSH owner/turn或任意账路径"}]
    if _research_name in {"append", "bind", "report"}:
        _research_args.append({"name": "--input", "kind": "value", "default": None,
            "description": "research schema公布的严格JSON请求，含实际candidate_revision"})
    if _research_name == "list":
        _research_args += [{"name": "--offset", "kind": "value", "type": "int", "default": 0, "description": "分页起点"},
                          {"name": "--limit", "kind": "value", "type": "int", "default": 50, "description": "每页1～100条"}]
    COMMANDS.append({"path": ("research", _research_name), "description": _research_description,
        "args": _research_args, "output": "{authority:external_declared,binding,research_ledger_revision,...}；schema返回完整合同",
        "examples": ["python -m genshin_app research " + _research_name +
            ("" if _research_name == "schema" else " --plan plan.json --task-id experiment-1" +
             (" --input research-request.json" if _research_name != "list" else ""))],
        "handler": _cmd_research})


for _live_operation in ("read", "edit", "compose", "calculate"):
    COMMANDS.append({
        "path": ("agent", "candidate", _live_operation),
        "description": "连接正在运行的App，读取、编辑、重新组合或计算同一份候选；不另建候选，不同步正式白板，不代用户批准",
        "args": [
            {"name": "--connection", "kind": "value", "default": None, "description": "当前原生会话提供的本地连接文件路径"},
            {"name": "--session-id", "kind": "value", "default": None, "description": "当前DSH会话ID，不按标题或界面选择猜测"},
            {"name": "--request", "kind": "value", "default": None, "description": "JSON请求；read省略则读输入快照，也接受gs_read_context同形resource/pointer/offset/limit；edit沿共用编辑合同；compose/calculate仅expected_revision"},
        ],
        "defaults": {"_live_operation": _live_operation},
        "output": "实际活动候选/修订或计算回执；断开后结果未知，先读取再决定，不自动重放",
        "examples": [f"python -m genshin_app agent candidate {_live_operation} --connection current-connection.json --session-id SESSION"
                     + ("" if _live_operation == "read" else " --request request.json")],
        "handler": _cmd_agent_candidate,
    })


def _cmd_session(args):
    from genshin_app.cli_session import serve
    serve(args.data_dir, sys.stdin, sys.stdout, admission=_require_runtime_database, log_path=args.log)


COMMANDS.append({"path": ("session",), "description": "显式stdin持久调用，完整准入一次并串行复用领域handler；先读取capabilities --section call_session取得逐行请求/响应合同；EOF退出，无守护进程",
    "args": [{"name": "--log", "kind": "value", "default": None, "description": "可选独占新建JSONL调用日志"}],
    "output": "genshin-cli-session-ready-v1 后每行 genshin-cli-session-result-v1",
    "examples": ["python -m genshin_app capabilities --section call_session",
                 "python -m genshin_app --data-dir data session --log session-new.jsonl"], "handler": _cmd_session})


def _capabilities_doc() -> dict:
    from copy import deepcopy

    from genshin_app.agent_chat.agent_hash_input import TOOL_DESCRIPTION, TOOL_SCHEMA
    from genshin_app.agent_chat.native_special_reaction_contracts import (
        special_reaction_capabilities,
    )
    from genshin_app.calculation_assistance import assistance_contract
    from genshin_app.calculation_diagnostics import calculation_diagnostics_contract
    from genshin_app.calculation_review import review_contract
    from genshin_app.calculation_review_batch import review_batch_contract
    from genshin_app.cli_session import session_capabilities
    from genshin_app.energy_trials import energy_trials_contract
    from genshin_app.equipment import plan_capabilities
    from genshin_app.equipment.runtime_capabilities import app_runtime_capabilities
    from genshin_app.external_research import research_capabilities
    from genshin_app.input_editing import edit_contract
    from genshin_app.presentation import display_contract
    from genshin_app.public_appendix_query import appendix_query_contract
    from genshin_app.public_data_query import panel_basis_capabilities
    from genshin_app.whiteboard import whiteboard_capabilities
    from genshin_app.workflow import workflow_capabilities
    from genshin_db.catalog_query import catalog_capabilities
    from genshin_db.context_query import CONTEXT_CAPABILITIES
    from genshin_db.runtime_request_contracts import runtime_contracts
    return {
        "call_session": session_capabilities(),
        "cli": "genshin_app",
        "version": CLI_VERSION,
        "plan_contract": plan_capabilities(),
        "special_reaction_contract": special_reaction_capabilities(),
        "input_hash_contract": {"description":TOOL_DESCRIPTION, "schema":deepcopy(TOOL_SCHEMA),
                                "cli":"engine hash-input --input REQUEST.json",
                                "authority":"none"},
        "display_contract": display_contract(),
        "workflow_contract": workflow_capabilities(),
        "context_contract": CONTEXT_CAPABILITIES,
        "appendix_query_contract": appendix_query_contract(),
        "catalog_contract": catalog_capabilities(),
        "panel_basis_contract": panel_basis_capabilities(),
        "runtime_contract": app_runtime_capabilities(),
        "runtime_request_contract": runtime_contracts(),
        "calculation_review_contract": review_contract(),
        "calculation_review_batch_contract": review_batch_contract(),
        "calculation_diagnostics_contract": calculation_diagnostics_contract(),
        "calculation_assistance_contract": assistance_contract(),
        "energy_trials_contract": energy_trials_contract(),
        "whiteboard_contract": whiteboard_capabilities(),
        "input_edit_contract": edit_contract(),
        "external_research_contract": research_capabilities(),
        "invocation": "python -m genshin_app [--data-dir DIR] [--pretty] <command>",
        "envelope": {"stdout": '{"code": 0, "message": "ok", "data": ...}',
                     "stderr": '{"code": 非零, "message": 错误信息}', "exit_code": "0 成功 / 1 失败"},
        "global_args": GLOBAL_ARGS,
        "database_call_concurrency": {
            "scope": "same_data_directory",
            "mode": "cross_process_serial_queue",
            "commands": sorted(" ".join(path) for path in _SERIAL_DATABASE_QUERIES),
            "cancellation": "Ctrl+C 可取消等待。若涉及文件输出，先核对文件和回执再重试。",
            "note": "这些查询在同一数据目录自动等待，其他数据目录不相互占用。数据库升级和候选写入各有事务校验。",
        },
        "commands": [
            {
                "name": " ".join(spec["path"]),
                "description": spec["description"],
                "args": spec["args"],
                "output": spec["output"],
                "examples": spec["examples"],
                **(
                    {
                        "lifecycle": spec["lifecycle"],
                        "available": False,
                        "reason": "accepted_database_is_immutable",
                        "replacement": "reviewed_change_proposal_to_complete_database_release",
                    }
                    if spec.get("lifecycle") == "deprecated"
                    else {}
                ),
            }
            for spec in COMMANDS
        ],
    }


# ---------- argparse 装配 ----------

def _add_spec_args(parser: argparse.ArgumentParser, spec: dict) -> None:
    for a in spec["args"]:
        if a["kind"] == "positional":
            parser.add_argument(a["name"], help=a["description"])
        elif a["kind"] == "flag":
            parser.add_argument(a["name"], action="store_true", help=a["description"])
        else:
            arg_type = {"int": int, "str": str}[a.get("type", "str")]
            parser.add_argument(a["name"], type=arg_type, default=a["default"],
                                help=a["description"])


def build_parser() -> argparse.ArgumentParser:
    parser = JSONArgumentParser(prog="genshin_app", description="原神拉表软件统一 CLI")
    for a in GLOBAL_ARGS:
        if a["kind"] == "flag":
            parser.add_argument(a["name"], action="store_true", help=a["description"])
        else:
            parser.add_argument(a["name"], default=a["default"], help=a["description"])
    top = parser.add_subparsers(dest="_top", required=True)
    groups: dict[tuple[str, ...], argparse._SubParsersAction] = {(): top}
    for spec in COMMANDS:
        path = spec["path"]
        if not 1 <= len(path) <= 3:
            raise CLIUsageError("命令定义层数超出支持范围")
        for depth in range(1, len(path)):
            prefix = path[:depth]
            if prefix not in groups:
                group = groups[prefix[:-1]].add_parser(prefix[-1], help=f"{' '.join(prefix)} 命令组")
                groups[prefix] = group.add_subparsers(dest="_" + "_".join(prefix), required=True)
        description = spec["description"]
        if tuple(path) in _SERIAL_DATABASE_QUERIES:
            description += "。同一数据目录的查询自动排队，可用 Ctrl+C 取消等待"
        sp = groups[path[:-1]].add_parser(path[-1], help=description, description=description)
        _add_spec_args(sp, spec)
        # Several public commands intentionally share one implementation. The
        # selected parser, not the first handler registration, owns identity.
        sp.set_defaults(**spec.get("defaults", {}), _handler=spec["handler"], _command_path=tuple(path))
    return parser


# ---------- 输出与入口 ----------

def _emit(data: dict, pretty: bool) -> None:
    payload = {"code": 0, "message": "ok", "data": data}
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, allow_nan=False))


def _emit_error(message: str, pretty: bool, *, reason=None, details=None, operation=None) -> int:
    payload = {"code": 1, "message": message}
    if reason is not None:
        payload["reason"] = reason
        from genshin_app.domain_errors import (
            classify_domain_failure,
            tool_failure_guidance,
        )
        payload.update(classify_domain_failure(reason, operation=operation, details=details))
        payload.update(tool_failure_guidance(reason, operation=operation, details=details))
        if operation is not None:
            payload["operation"] = operation
        payload["details"] = details
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None),
          file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    # UTF-8 also applies to the early packaged selfcheck boundary.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    # Must precede normal parsers/default profile/settings/credential factories.
    early = list(argv) if argv is not None else sys.argv[1:]
    if len(early) >= 2 and early[:2] in (["internal", "verify-frozen"], ["internal", "verify-frozen-readback"]):
        from genshin_app.frozen_selfcheck import main as selfcheck_main
        return selfcheck_main(early[2:], readback=early[1] == "verify-frozen-readback")
    cli_argv = list(argv) if argv is not None else sys.argv[1:]
    try:
        args = build_parser().parse_args(cli_argv)
    except CLIUsageError as e:
        return _emit_error(str(e), "--pretty" in cli_argv, reason=e.reason, details=e.details, operation="cli.parse")
    if args.data_dir is None:  # 开发态 ./data；frozen 态 %LOCALAPPDATA%\GenshinCalc\data
        from genshin_app.paths import default_data_dir
        args.data_dir = str(default_data_dir())
    from genshin_app.cli_dispatch import execute
    payload = execute(args)
    if payload is not None:
        code = 0 if payload.get("code") == 0 else 1
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, allow_nan=False),
              file=sys.stdout if code == 0 else sys.stderr)
        return code
    return 0
