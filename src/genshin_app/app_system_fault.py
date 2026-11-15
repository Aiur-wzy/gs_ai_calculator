"""Host-authored program faults, separate from task knowledge or input issues."""
from __future__ import annotations

import builtins
import logging
import re
import sys
import traceback
from collections.abc import Mapping

CONTRACT = "genshin-app-system-failure-v1"
COMPONENTS = frozenset({"python_engine_runtime", "app_domain_tools", "app_domain_binding"})
CAUSES = frozenset({"module_unavailable", "dependency_initialization_failed", "internal_exception"})
_OPERATION = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")
_PROJECT_MODULES = frozenset({"genshin_app", "genshin_db", "genshin_engine"})
_GENERATED_FUNCTIONS = frozenset({"<module>", "<lambda>", "<listcomp>", "<dictcomp>", "<setcomp>", "<genexpr>"})
_LOGGER = logging.getLogger(__name__)
# Library code must not activate logging.lastResort on a machine-readable
# CLI/RPC stderr stream. Configured application/maintenance handlers still
# receive the record through normal propagation; importing this module never
# creates a diagnostic file or changes the root logger.
_LOGGER.addHandler(logging.NullHandler())


class AppComponentInitializationError(RuntimeError):
    """Explicit producer signal; never inferred from exception text."""


def safe_exception_diagnostic(error: Exception) -> dict:
    """Return code identities only, without loading source or inspecting data.

    Unknown exception classes use their nearest genuine built-in base. Frames
    are limited to loaded project/standard-library modules; filenames, source
    lines, exception arguments and caller locals never enter this projection.
    ``walk_tb`` also avoids ``extract_tb``'s implicit linecache/source reads in
    a frozen process with a deliberately restricted filesystem.
    """
    exception_type = "Exception"
    for candidate in type(error).__mro__:
        name = candidate.__name__
        if (candidate.__module__ == "builtins" and getattr(builtins, name, None) is candidate
                and issubclass(candidate, BaseException)):
            exception_type = name
            break
    frames = []
    for frame, line in traceback.walk_tb(error.__traceback__):
        module = frame.f_globals.get("__name__")
        if not isinstance(module, str) or len(module) > 256 or not _MODULE.fullmatch(module):
            continue
        loaded = sys.modules.get(module)
        root = module.partition(".")[0]
        if (loaded is None or vars(loaded) is not frame.f_globals
                or root not in _PROJECT_MODULES | sys.stdlib_module_names):
            continue
        function = frame.f_code.co_name
        if not isinstance(function, str) or not (_IDENTIFIER.fullmatch(function) or function in _GENERATED_FUNCTIONS):
            function = "<unavailable>"
        frames.append({"module": module, "function": function, "line": line})
    return {"exception_type": exception_type, "frames": frames[-16:]}


def validate_safe_diagnostic(value):
    if (not isinstance(value, Mapping) or set(value) != {"exception_type", "frames"}
            or not isinstance(value["exception_type"], str)
            or not isinstance(value["frames"], list) or len(value["frames"]) > 16):
        return False
    cls = getattr(builtins, value["exception_type"], None)
    if not isinstance(cls, type) or not issubclass(cls, BaseException):
        return False
    return all(isinstance(frame, Mapping) and set(frame) == {"module", "function", "line"}
        and isinstance(frame["module"], str) and _MODULE.fullmatch(frame["module"])
        and frame["module"].partition(".")[0] in _PROJECT_MODULES | sys.stdlib_module_names
        and isinstance(frame["function"], str)
        and (frame["function"] == "<unavailable>" or _IDENTIFIER.fullmatch(frame["function"])
             or frame["function"] in _GENERATED_FUNCTIONS)
        and type(frame["line"]) is int and frame["line"] >= 0 for frame in value["frames"])


def system_failure(error: Exception, *, component: str, operation: str) -> dict:
    if component not in COMPONENTS or not isinstance(operation, str) or not _OPERATION.fullmatch(operation):
        raise ValueError("invalid_system_failure_producer")
    cause = ("module_unavailable" if isinstance(error, ImportError) else
             "dependency_initialization_failed" if isinstance(error, AppComponentInitializationError) else
             "internal_exception")
    # No caller values, raw exception messages, paths, locals or credentials in
    # the public response or diagnostic log. The UUID correlates exact code
    # locations/type with the already-owned operation and call settlement.
    diagnostic = safe_exception_diagnostic(error)
    from genshin_app.app_fault_store import persist_system_diagnostic
    try:
        diagnostic_id = persist_system_diagnostic(component=component, operation=operation,
                                                  cause=cause, diagnostic=diagnostic)
        storage = {"state": "persisted"}
    except Exception:  # noqa: BLE001 - diagnostic failure must not replace the original fault
        diagnostic_id = None
        storage = {"state": "failed", "reason": "diagnostic_persistence_failed"}
    try:
        _LOGGER.error("app_system_fault diagnostic_id=%s component=%s operation=%s cause=%s type=%s frames=%s",
                      diagnostic_id, component, operation, cause, diagnostic["exception_type"], diagnostic["frames"])
    except Exception:  # noqa: BLE001,S110 - an optional logging handler is not the durable receiver
        pass
    return {
        "code": 1,
        "reason": "app_internal_error" if cause == "internal_exception" else "app_component_unavailable",
        "message": (("应用内部操作失败，请报告诊断编号。" if cause == "internal_exception" else "本次操作依赖的应用组件不可用。")
                    if diagnostic_id is not None else "应用操作失败，诊断记录也未能保存；原操作是否完成请先核对操作账或文件。"),
        "failure_kind": "system", "retryable": False,
        "diagnostic_storage": storage,
        "system_fault": {"contract": CONTRACT, "component": component,
                         "cause_code": cause, "operation": operation, "diagnostic_id": diagnostic_id},
        "recovery": {"action": "report_system_fault", "retry_same_request": False},
    }


def validate_system_failure(value: Mapping) -> None:
    """Check the closed fault/recovery shapes without modifying settlement."""
    if not isinstance(value, Mapping) or value.get("failure_kind") != "system":
        raise ValueError("invalid_system_failure")
    if value.get("code") != 1 or type(value.get("code")) is not int or value.get("retryable") is not False:
        raise ValueError("invalid_system_failure")
    if "category" in value or "blocking_scope" in value:
        raise ValueError("system_failure_is_not_a_task_issue")
    fault = value.get("system_fault")
    if not isinstance(fault, Mapping) or set(fault) != {"contract", "component", "cause_code", "operation", "diagnostic_id"}:
        raise ValueError("invalid_system_fault_shape")
    if fault["contract"] != CONTRACT or fault["component"] not in COMPONENTS or fault["cause_code"] not in CAUSES:
        raise ValueError("invalid_system_fault_identity")
    if not isinstance(fault["operation"], str) or not _OPERATION.fullmatch(fault["operation"]):
        raise ValueError("invalid_system_fault_operation")
    from genshin_app.app_fault_store import _diagnostic_id
    storage = value.get("diagnostic_storage")
    if storage is not None and storage not in ({"state": "persisted"},
            {"state": "failed", "reason": "diagnostic_persistence_failed"}):
        raise ValueError("invalid_system_fault_storage")
    if storage == {"state": "failed", "reason": "diagnostic_persistence_failed"}:
        if fault["diagnostic_id"] is not None:
            raise ValueError("failed_diagnostic_has_no_public_id")
    else:
        _diagnostic_id(fault["diagnostic_id"])
    expected = "app_internal_error" if fault["cause_code"] == "internal_exception" else "app_component_unavailable"
    if value.get("reason") != expected or not isinstance(value.get("message"), str):
        raise ValueError("invalid_system_fault_reason")
    recovery = value.get("recovery")
    if (not isinstance(recovery, Mapping) or set(recovery) != {"action", "retry_same_request"}
            or recovery["action"] != "report_system_fault" or recovery["retry_same_request"] is not False):
        raise ValueError("invalid_system_fault_recovery")


def domain_exception_reason(error: Exception) -> str | None:
    """One public identity for reason-based and legacy code-only producers."""
    return next((value for key in ("reason", "code")
                 if isinstance(value := getattr(error, key, None), str) and value), None)


def is_domain_exception(error: Exception) -> bool:
    """Typed public exceptions only; never classify by message keywords."""
    return domain_exception_reason(error) is not None
