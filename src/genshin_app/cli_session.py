"""Opt-in stdin lifetime; one full admission, existing upgrade fence, no daemon."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import sys
import time
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from contextvars import ContextVar
from pathlib import Path

from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_runtime.operation_timing import collect_operation_timings, timed_call

_active = ContextVar("cli_verified_session", default=None)
_request_admitted = ContextVar("cli_request_admitted", default=False)
# Transport contains argv and file references, not large game documents.
from genshin_app.agent_chat.bridge_protocol import MAX_FRAME_BYTES


def session_capabilities():
    return {"contract": "genshin-cli-session-v1", "command": "session",
            "request": {"id": "caller-id", "argv": ["capabilities", "--section", "workflow_contract"]},
            "mode": "serial", "atomic": False, "retry": "never_automatic",
            "parallel": "多个一次性只读CLI继续排队；本会话请求按输入顺序串行，不能绕过数据库升级锁。",
            "admission": "启动完整验证一次；活跃进程持有升级互斥，退出后失效，不跨进程缓存。",
            "development": "源码会话按文件属性检查src和Skill参考资料变化。修改后退出重开。不支持保留文件属性的热替换；冻结工件按发行身份校验。",
            "allowed_roots": ["capabilities", "db", "equipment", "engine", "whiteboard", "workflow", "research", "system"],
            "forbidden": "数据库写入/升级/服务/GUI/另一个session；只读db命令按公开清单允许。",
            "request_bytes": MAX_FRAME_BYTES, "shutdown": "stdin EOF 或 Ctrl+C；未知写入结果不得直接重试",
            "python_client": {"module": "genshin_app.cli_client", "class": "CliSessionClient",
                "methods": ["call(argv, timeout=None)", "receive(timeout=None)",
                            "batch(requests, stop_on_failure=False, accepted=None)", "close()", "cancel()"],
                "batch": "非原子、串行；依赖链启用stop_on_failure。可按操作合同提供accepted判定。断流保留前缀，不自动重试。",
                "waiting": "timeout只结束观察。通过receive继续读取原请求；cancel终止本会话进程树；close等待请求收尾。"},
            "results": "每行返回 id、exit_code、result；stdout/stderr是旁路日志，不解析为结果"}


def _identity_files(directory):
    from genshin_app.paths import is_frozen, resource_dir
    root = resource_dir()
    frozen = is_frozen()
    package = Path(sys.executable).resolve().parent if frozen else root
    # Match native_dsh_production's source/frozen asset selection. These two
    # controls belong to the client assets, not the Node runtime directory.
    assets = root / ("agent-client-assets" if frozen else "frontend")
    return [directory / name for name in ("genshin.db", "database-live-manifest.json",
        "database-live-receipt.json", "release-pair-live-manifest.json")] + [
        package / "PACKAGE-FILES.sha256", root / "bundled" / "release-pair-manifest.json",
        root / "bundled" / "database-release-manifest.json",
        root / "agent-runtime" / "dsh-client-shell-runtime-manifest.json",
        assets / "dsh-client-accepted-manifest.json",
        assets / "dsh-client-composition.json", root / "skill" / "SKILL.md",
        Path(__file__).with_name("version.py")]


def _guard(directory):
    # These cheap guards only invalidate an already verified live process;
    # they never establish trust and are never reused across process lifetimes.
    rows = []
    for path in _identity_files(directory):
        if path.exists():
            stat = path.stat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.name != "genshin.db" else None
            rows.append((str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, digest))
    return rows


def _source_guard():
    from genshin_app.paths import is_frozen, resource_dir
    if is_frozen():
        return ()
    root = resource_dir()
    rows = []
    for folder in (root / "src", root / "skill"):
        if not folder.is_dir():
            continue
        for directory, dirs, files in os.walk(folder):
            dirs[:] = [name for name in dirs if name not in {"__pycache__", ".git", "node_modules"}]
            for name in files:
                path = Path(directory) / name
                if path.suffix not in {".py", ".md", ".json"}:
                    continue
                stat = path.stat()
                rows.append((str(path.relative_to(root)), stat.st_ino, stat.st_size, stat.st_mtime_ns))
    return tuple(sorted(rows))


def reuse_admission(data_dir):
    active = _active.get()
    if active is None or Path(data_dir).resolve() != active["directory"]:
        return None
    if _request_admitted.get():
        return copy.deepcopy(active["receipt"])
    if _guard(active["directory"]) != active["guard"]:
        raise PlanError("本调用会话期间数据库身份文件变化，请结束会话并重新准入。",
                        "cli_session_database_changed")
    if _source_guard() != active["source_guard"]:
        raise PlanError("会话启动后的源码或Skill资料已变化。请退出本会话并重新启动。",
                        "cli_session_source_changed")
    return copy.deepcopy(active["receipt"])


@contextmanager
def verified_session(data_dir, admission, *, log_path=None):
    from genshin_app.database_transaction import profile_writer
    from genshin_app.output_policy import ordinary_output_scope
    directory = Path(data_dir).resolve()
    with profile_writer(directory, timeout=None), ordinary_output_scope(
        protected_files=[log_path] if log_path else []
    ):
        started = time.perf_counter()
        receipt = admission(directory)
        admission_seconds = time.perf_counter() - started
        token = _active.set({"directory": directory, "receipt": receipt, "guard": _guard(directory),
                             "source_guard": _source_guard(), "admission_seconds": admission_seconds,
                             "log_path": Path(log_path).resolve() if log_path else None})
        try:
            yield receipt
        finally:
            _active.reset(token)


def _parse_request(request, data_dir):
    from genshin_app.cli import build_parser
    if not isinstance(request, dict) or set(request) != {"id", "argv"} or not isinstance(request["id"], str):
        raise PlanError("调用需要 id 文本和 argv 字符串列表", "cli_session_request_invalid")
    argv = request["argv"]
    if not isinstance(argv, list) or any(not isinstance(v, str) for v in argv):
        raise PlanError("argv 需要字符串列表，不接受 shell 命令", "cli_session_request_invalid")
    args = build_parser().parse_args(argv)
    args.data_dir = args.data_dir or str(data_dir)
    active = _active.get() or {}
    log = active.get("log_path")
    if log:
        from genshin_app.output_policy import same_file
        for key in ("input", "output", "plan", "template", "patch", "request", "request_file", "audit",
                    "calculated", "task_file", "source", "result", "proposal", "review"):
            value = getattr(args, key, None)
            if value and same_file(log, value):
                raise PlanError("调用日志不能同时作为输入或输出文件", "file_conflict")
    if Path(args.data_dir).resolve() != Path(data_dir).resolve():
        raise PlanError("本调用会话固定数据目录；更换数据库请另开会话", "cli_session_database_mismatch")
    path = args._command_path
    if path == ("system", "check-update"):
        raise PlanError("请在独立进程或GUI检查更新。", "cli_session_operation_unavailable")
    if path[0] not in session_capabilities()["allowed_roots"] or (
        path[0] == "db" and path not in {("db", name) for name in
            ("context", "appendix", "catalog", "panel-basis", "runtime")}
    ):
        raise PlanError("该命令不属于持久调用的有限工具范围，请独立执行", "cli_session_operation_unavailable")
    return args


def invoke(request, data_dir):
    from genshin_app.cli_dispatch import error_payload, exception_payload, execute
    out, err, started = io.StringIO(), io.StringIO(), time.perf_counter()
    timing = {}
    stages = []
    try:
        with redirect_stdout(out), redirect_stderr(err), collect_operation_timings() as stages:
            args = _parse_request(request, data_dir)
            checked_at = time.perf_counter()
            reuse_admission(data_dir)
            timing["identity_check_seconds"] = time.perf_counter() - checked_at
            executed_at = time.perf_counter()
            token = _request_admitted.set(True)
            try:
                result = timed_call("dispatch", execute, args)
            finally:
                _request_admitted.reset(token)
            timing["handler_seconds"] = time.perf_counter() - executed_at
        result = result or {"code": 0, "message": "ok", "data": None}
    except SystemExit as exc:
        result = error_payload("持久调用中不执行交互帮助，请读取 capabilities。",
                               reason="cli_session_help_not_request", details={"exit_code": exc.code})
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - shared public session boundary
        result = exception_payload(exc, data_dir=data_dir, operation="cli.session.invoke")
    timing["stages"] = [{"stage": row["stage"], "offset_seconds": row["started"] - started,
                         "elapsed_seconds": row["elapsed_seconds"]} for row in stages]
    return {"contract": "genshin-cli-session-result-v1", "id": request.get("id") if isinstance(request, dict) else None,
            "exit_code": 0 if result.get("code") == 0 else 1, "result": result,
            "transport_state": "completed", "request_exit_code": 0 if result.get("code") == 0 else 1,
            "process_state": "running", "timing": timing, **_operation_outcome(result),
            "stdout": out.getvalue(), "stderr": err.getvalue(), "elapsed_seconds": time.perf_counter() - started,
            "atomic": False, "automatically_retried": False}


def _operation_outcome(result):
    data = result.get("data") if result.get("code") == 0 else result.get("details")
    data = data if isinstance(data, dict) else {}
    files = data.get("files", data)
    files = files if isinstance(files, dict) else {}
    completion = data.get("completion")
    completion = completion if isinstance(completion, dict) else {}
    admission = completion.get("task_admission")
    published = files.get("published_paths")
    file_shas = files.get("file_shas")
    known_file = files.get("path") and files.get("file_sha")
    if files.get("unknown_paths"):
        write = "unknown"
    elif published or file_shas or known_file:
        write = "partially_written" if files.get("pending_paths") else "written"
    elif "published_paths" in files and "pending_paths" in files:
        write = "not_written"
    else:
        write = "not_reported"
    state = "completed" if result.get("code") == 0 else "rejected"
    if result.get("category") == "cancelled":
        state = "cancelled"
    elif result.get("reason") == "calculation_qualification_rejected":
        state = "diagnostic"
    elif admission in {"accepted", "pending", "failed"}:
        state = {"accepted": "accepted", "pending": "diagnostic", "failed": "rejected"}[admission]
    elif result.get("code") == 0 and isinstance(data.get("missing_inputs"), list) and data["missing_inputs"]:
        # Composing unresolved inputs is a successful inspection, not a ready calculation input.
        state = "diagnostic"
    return {"operation_state": state, "write_outcome": write}


def serve(data_dir, input_stream, output_stream, *, admission, log_path=None):
    """One JSON request per line; each response is flushed before reading the next."""
    log_warning = None
    if log_path:
        from genshin_app.output_policy import (
            ordinary_output_scope,
            validate_output_paths,
        )
        with ordinary_output_scope(data_dir=data_dir, protected_files=_identity_files(Path(data_dir))):
            validate_output_paths([log_path])
        # Exclusive creation avoids accidentally mixing two session journals.
        try:
            log = Path(log_path).open("x", encoding="utf-8")  # noqa: SIM115 - closed in finally
        except FileExistsError:
            raise
        except OSError:
            log = None
            log_warning = {"reason": "session_log_unavailable", "path": str(log_path)}
    else:
        log = None
    try:
        with verified_session(data_dir, admission, log_path=log_path) as receipt:
            ready = {"contract": "genshin-cli-session-ready-v1", "admission": receipt,
                     "mode": "serial", "full_admission_count": 1,
                     "timing": {"admission_seconds": _active.get()["admission_seconds"]}}
            if log_warning:
                ready["log_warning"] = log_warning
            output_stream.write(json.dumps(ready, ensure_ascii=False) + "\n")
            output_stream.flush()
            while True:
                line = input_stream.readline(MAX_FRAME_BYTES + 1)
                if not line:
                    break
                if len(line.encode("utf-8")) > MAX_FRAME_BYTES:
                    raise PlanError("调用控制行过大，请通过文件参数提交内容", "cli_session_frame_too_large")
                try:
                    request = json.loads(line)
                except ValueError:
                    request = None
                response = invoke(request, data_dir)
                if log:
                    try:
                        log.write(json.dumps({"request_hash": fingerprint(request), **response},
                                             ensure_ascii=False, allow_nan=False) + "\n")
                        log.flush()
                    except (OSError, ValueError):
                        try:
                            log.close()
                        except OSError:
                            pass
                        log = None
                        log_warning = {"reason": "session_log_write_failed", "path": str(log_path)}
                if log_warning:
                    response["log_warning"] = log_warning
                encoded = json.dumps(response, ensure_ascii=False, allow_nan=False)
                output_stream.write(encoded + "\n")
                output_stream.flush()
    finally:
        if log:
            try:
                log.close()
            except OSError:
                pass
