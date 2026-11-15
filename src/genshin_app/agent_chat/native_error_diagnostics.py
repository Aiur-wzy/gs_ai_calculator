"""Secret-free diagnostics for the App's native Agent manager boundary.

The release GUI and the complete development GUI share this wrapper.  It
records only a stable operation stage, a reviewed error code, the mode and a
timestamp.  Arguments, exception text, provider responses, prompts, reasoning,
URLs, queries and local paths never enter the record.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .native_error_codes import (
    NATIVE_RUNTIME_START_DIAGNOSTIC_CAUSES,
    reviewed_native_error_code,
)
from .sessions import write_owned

CONTRACT = "genshin-native-agent-error-diagnostic-v1"
DIAGNOSTIC_FILE = "native-agent-error-diagnostic.json"
MAX_DIAGNOSTIC_BYTES = 8 * 1024

_MODES = frozenset({"release", "native_agent_development"})
_STAGES = {
    "detect": "detect",
    "connect": "connect",
    "forget_credential": "credential",
    "budget_settings": "budget_settings",
    "update_budget_settings": "budget_settings_update",
    "reset_budget_settings": "budget_settings_reset",
    "start": "start",
    "send": "send",
    "wait_events": "poll",
    "resume": "resume",
    "poll": "poll",
    "stop": "stop",
    "candidate": "candidate",
    "candidate_read": "candidate_read",
    "accept": "candidate_accept",
    "candidate_applied": "candidate_applied",
    "abort_candidate_application": "candidate_abort",
    "recover_candidate_application": "candidate_recover",
    "recover_last_applied_whiteboard_projection": "projection_recover",
    "whiteboard_apply_equipment": "whiteboard_apply",
    "resolve_app_action": "app_action_resolve",
    "present_app_action": "app_action_present",
    "claim_app_action": "app_action_claim",
    "settle_app_action": "app_action_settle",
    "recover_app_action_without_replay": "app_action_recover",
    "sessions": "sessions",
    "forget": "session_forget",
    "shutdown": "shutdown",
}
_STABLE_STAGES = frozenset({*_STAGES.values(), "runtime_initialization", "turn"})


def _stable_code(error: object) -> str:
    """Reduce an arbitrary exception to one path- and payload-free code."""

    code = reviewed_native_error_code(
        error,
        fallback="native_agent_operation_failed",
    )
    if code == "native_runtime_start_failed":
        details = getattr(error, "details", None)
        if (
            isinstance(details, Mapping)
            and set(details) == {"stage", "cause"}
            and details.get("stage") == "runtime_start"
            and isinstance(details.get("cause"), str)
            and details["cause"] in (
                NATIVE_RUNTIME_START_DIAGNOSTIC_CAUSES
                | {"runtime_start_cause_unavailable"}
            )
        ):
            return f"native_runtime_start_failed:{details['cause']}"
    return code


def record_native_error_diagnostic(
    diagnostics_directory: str | Path,
    *,
    mode: str,
    stage: str,
    error: object,
) -> None:
    """Persist one bounded error record without inspecting operation inputs."""

    if mode not in _MODES:
        raise ValueError("unsupported native diagnostic mode")
    if stage not in _STABLE_STAGES:
        stage = "operation"
    value = {
        "contract": CONTRACT,
        "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "mode": mode,
        "stage": stage,
        "error_code": _stable_code(error),
    }
    try:
        directory = Path(diagnostics_directory)
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink() or not directory.is_dir():
            return
        write_owned(
            directory,
            DIAGNOSTIC_FILE,
            value,
            MAX_DIAGNOSTIC_BYTES,
        )
    except Exception:  # noqa: BLE001,S110 - diagnostics never alter operation outcome
        pass


class NativeErrorDiagnosticManager:
    """Proxy one manager and persist only stable failures from public calls."""

    def __init__(
        self,
        manager: object,
        *,
        mode: str,
        diagnostics_directory: str | Path,
    ) -> None:
        if mode not in _MODES:
            raise ValueError("unsupported native diagnostic mode")
        self._manager = manager
        self._mode = mode
        self._diagnostics_directory = Path(diagnostics_directory)

    def _record(self, stage: str, error: BaseException) -> None:
        record_native_error_diagnostic(
            self._diagnostics_directory,
            mode=self._mode,
            stage=stage,
            error=error,
        )

    def _record_projected_error(self, result: object) -> None:
        if not isinstance(result, Mapping):
            return
        events = result.get("events")
        if not isinstance(events, list):
            return
        for event in reversed(events):
            if not isinstance(event, Mapping) or event.get("type") != "error":
                continue
            record_native_error_diagnostic(
                self._diagnostics_directory,
                mode=self._mode,
                stage="turn",
                error=event.get("reason") or event.get("error"),
            )
            return

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        value = getattr(self._manager, name)
        stage = _STAGES.get(name)
        if stage is None or not callable(value):
            return value

        def checked(*args: object, **kwargs: object) -> object:
            try:
                result = value(*args, **kwargs)
            except BaseException as error:
                self._record(stage, error)
                raise
            if name in {"start", "send", "resume", "poll", "wait_events"}:
                self._record_projected_error(result)
            return result

        return checked


def with_native_error_diagnostics(
    manager: object,
    *,
    mode: str,
    diagnostics_directory: str | Path,
) -> NativeErrorDiagnosticManager:
    """Wrap one release/development manager at their shared GUI boundary."""

    if isinstance(manager, NativeErrorDiagnosticManager):
        return manager
    return NativeErrorDiagnosticManager(
        manager,
        mode=mode,
        diagnostics_directory=diagnostics_directory,
    )


__all__ = [
    "CONTRACT",
    "DIAGNOSTIC_FILE",
    "MAX_DIAGNOSTIC_BYTES",
    "NativeErrorDiagnosticManager",
    "record_native_error_diagnostic",
    "with_native_error_diagnostics",
]
