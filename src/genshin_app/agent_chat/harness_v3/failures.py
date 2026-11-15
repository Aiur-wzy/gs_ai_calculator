"""Stable failure layering without promoting every failed turn to runtime death."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_CODE = re.compile(r"[a-z][a-z0-9_.:-]{0,255}\Z")

_PROTOCOL_FATAL = frozenset({
    "native_turn_binding_invalid",
    "native_turn_outcome_invalid",
    "native_turn_terminal_invalid",
    "native_runtime_projection_invalid",
    "native_dsh_checkpoint_invalid",
    "native_checkpoint_binding_invalid",
})


def classify_native_failure(
    code: object,
    *,
    runtime: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a renderer-safe layer and recovery scope for one stable code."""

    value = code if isinstance(code, str) and _CODE.fullmatch(code) else "native_unknown_failure"
    runtime = {} if runtime is None else runtime
    runtime_health = runtime.get("runtime_health")
    runtime_state = runtime.get("runtime_state")
    runtime_broken = (
        runtime_health in {"unhealthy", "crashed"}
        or runtime_state in {"failed", "unhealthy", "crashed"}
    )
    cause = value.split(":", 1)[1] if ":" in value else None
    if value in _PROTOCOL_FATAL:
        layer, scope, recoverable = "runtime_protocol", "session_activation", False
    elif runtime_broken:
        layer, scope, recoverable = "runtime", "runtime", True
    elif value.startswith(("pipe_", "native_pipe_")) or cause and cause.startswith("pipe_"):
        layer, scope, recoverable = "transport", "runtime", True
    elif value.startswith(("provider_", "deepseek_")) or cause and cause.startswith(
        ("provider_", "deepseek_")
    ):
        layer, scope, recoverable = "provider", "turn", True
    elif value.startswith(("native_app_action_", "app_action_")):
        layer, scope, recoverable = "app_tool", "exact_action", True
    elif value.startswith(("native_candidate_", "agent_calculation_")):
        layer, scope, recoverable = "candidate", "candidate", True
    elif value.startswith(("native_document_", "document_")):
        layer, scope, recoverable = "document", "document", True
    elif value.startswith(("native_question_", "workbench_question_")):
        layer, scope, recoverable = "user_interruption", "interruption", True
    elif value.startswith(("native_turn_", "runtime_official_turn_failed", "turn_")):
        # ``runtime_official_turn_failed`` is a legacy wrapper.  It is a Turn
        # failure unless a reviewed cause or runtime health proves transport /
        # process failure.
        layer, scope, recoverable = "turn", "turn", True
    elif value.startswith(("native_session_", "workbench_")):
        layer, scope, recoverable = "session", "session", True
    else:
        layer, scope, recoverable = "domain", "operation", True
    return {
        "code": value,
        "layer": layer,
        "scope": scope,
        "recoverable": recoverable,
        "requires_runtime_restart": layer in {"runtime", "transport", "runtime_protocol"},
    }


def failure_requires_runtime_teardown(
    code: object,
    *,
    runtime: Mapping[str, Any] | None = None,
) -> bool:
    return classify_native_failure(code, runtime=runtime)["requires_runtime_restart"]


__all__ = ["classify_native_failure", "failure_requires_runtime_teardown"]
