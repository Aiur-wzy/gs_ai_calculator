"""Internal adapter for task-capability-aware missing-input producers.

Production Composer callbacks accept the task metadata keywords.  A number of
small compiler unit tests intentionally pass legacy ``lambda *args`` sinks;
those sinks still receive the historical positional diagnostic without
weakening the production contract.  Consumer state is selected by the App
producer, never copied from Agent or plan JSON.
"""
from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

_METADATA_KEYS = frozenset({
    "category", "consumer", "required", "consumed", "impact",
    "blocking_scope", "retryable", "repair_hint",
})


def _accepts_metadata(callback: Callable[..., Any]) -> bool:
    try:
        parameters = inspect.signature(callback).parameters.values()
    except (TypeError, ValueError):
        return False
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD
           for parameter in parameters):
        return True
    names = {parameter.name for parameter in parameters}
    return _METADATA_KEYS <= names


def emit_task_missing(
    missing: Callable[..., Any],
    code: str,
    path: str,
    message: str,
    action: str,
    *,
    consumer: str,
    category: str,
    required: bool = True,
    consumed: bool = True,
    impact: str = "high",
    blocking_scope: str = "task_capability",
    retryable: bool = True,
    repair_hint: str = "",
    diagnostics: dict[str, Any] | None = None,
) -> None:
    """Emit one producer-owned issue, preserving legacy test sinks.

    ``required`` and ``consumed`` are deliberately explicit even when true so
    a producer cannot appear migrated while silently inheriting the legacy
    conservative fallback.
    """

    args = (code, path, message, action)
    if not _accepts_metadata(missing):
        missing(*args)
        return
    extra = {}
    if diagnostics is not None:
        parameters = inspect.signature(missing).parameters
        if "diagnostics" in parameters or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
            extra["diagnostics"] = diagnostics
    missing(
        *args,
        category=category,
        consumer=consumer,
        required=required,
        consumed=consumed,
        impact=impact,
        blocking_scope=blocking_scope,
        retryable=retryable,
        repair_hint=repair_hint,
        **extra,
    )


__all__ = ["emit_task_missing"]
